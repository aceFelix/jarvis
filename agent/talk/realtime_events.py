"""实时双工语音可观测性 —— /talk 事件时间线与环境音转写。

背景（2026-09，aceFelix）：DashScope smart_turn 模式下，服务端检测到语音
活动、但语义判定为**非有效轮次**（噪声、"嗯""啊"等无语义内容、与 AI 刚说
内容高度重合的回声）时，不触发对话轮，而是把 ASR 结果经
``conversation.item.ambient_audio_transcription.*`` 事件透传，且**不写入
对话上下文**。

客户端此前未订阅该事件 —— 用户说了话、服务端也听到了，屏幕上却一个字都
不显示，表现就是"我一直在说，它不回复"。本模块把这条通道显式暴露出来，
并提供可选的事件时间线日志，用于判定用户的话究竟走了哪条路：

- 走 ``conversation.item.input_audio_transcription.completed`` → 已进入对话轮
- 走 ``conversation.item.ambient_audio_transcription.completed`` → 被语义过滤

设计原则是**零行为风险**：只增加可见性，不改 VAD、不改麦克风增益、不改
session.update 参数；时间线日志默认关闭，关闭时除少量内存计数外不产生
任何输出。

@author aceFelix
"""

from __future__ import annotations

import json

from agent.core import diag

# ---- 事件类型常量（服务端契约，集中在此避免字符串散落） ----
AMBIENT_DELTA = "conversation.item.ambient_audio_transcription.delta"
AMBIENT_COMPLETED = "conversation.item.ambient_audio_transcription.completed"

# 高频事件：只计数不逐条落盘，否则音频帧会瞬间刷爆轮转日志（1 MB 上限）
_AGGREGATED_EVENTS = frozenset({
    "response.audio.delta",
    "response.audio_transcript.delta",
})

# 回溯缓冲容量：保留最近若干条事件类型（连续重复折叠），取消发生时随
# 提示一并输出，用于当场判断“谁在什么时候打断了谁”，不依赖日志开关。
# 取 24 是因为一轮响应自身就会产生多个节点（created → output_item →
# content_part → transcript → audio → done），8 条会被单轮尾部事件占满，
# 看不到更早的“这一轮是怎么开始的”。
_RECENT_LIMIT = 24

# 进入事件链回溯的语义节点白名单。其余事件（content_part.*、audio.*、
# transcript.* 等）每轮都会出现，全部入链等于把关键节点（speech_started、
# 客户端指令）挤出缓冲，反而失去诊断价值。
_CHAIN_EVENTS = frozenset({
    "response.created",
    "response.done",
    "response.output_item.added",
    "response.function_call_arguments.done",
    "conversation.item.created",
    "conversation.item.input_audio_transcription.completed",
    AMBIENT_COMPLETED,
    "input_audio_buffer.speech_started",
    "input_audio_buffer.speech_stopped",
    "session.updated",
    "error",
})


def _chain_label(event_type: str, event: dict) -> str:
    """事件链回溯的节点标签；不在白名单内时返回空串（不入链）。

    `response.done` 带上 status（cancelled / completed 是判断取消成因的第一手
    证据），`response.output_item.added` 带上 item.type（`function_call` 与
    `message` 决定了这一轮是不是工具轮）。

    @author aceFelix
    """
    if event_type == "response.done":
        status = (event.get("response") or {}).get("status") or event.get("status") or ""
        return f"response.done[{status}]" if status else "response.done"
    if event_type == "response.output_item.added":
        item = event.get("item") or {}
        return f"output_item.added({item.get('type', '?')})"
    return event_type if event_type in _CHAIN_EVENTS else ""

# 事件摘要提取的字段白名单（按重要性排序，命中即取，空值跳过）
_BRIEF_KEYS = (
    "name", "call_id", "item_id", "transcript", "text", "reason", "status", "error",
)

# 单条时间线的最大字符数：session.updated 会回带全部工具定义，必须截断
_BRIEF_LIMIT = 160


def _brief(event: dict, limit: int = _BRIEF_LIMIT) -> str:
    """把一条服务端事件压成单行摘要，供时间线日志使用。

    优先取白名单字段（信息密度高、可读）；白名单全缺失时回落截断的 JSON，
    但先去重 type（它已经在日志行首出现）。两者都无内容时返回空串，
    让时间线保持 ``#序号 事件类型`` 的干净形态。
    error 字段是嵌套结构，只取其中的 message。
    """
    parts: list[str] = []
    for key in _BRIEF_KEYS:
        if key not in event:
            continue
        value = event[key]
        if key == "error" and isinstance(value, dict):
            value = value.get("message", "")
        text = str(value).replace("\n", " ").strip()
        if text:
            parts.append(f"{key}={text}")
    if not parts:
        rest = {k: v for k, v in event.items() if k != "type"}
        if not rest:
            return ""
        parts.append(json.dumps(rest, ensure_ascii=False, default=str).replace("\n", " "))
    return " ".join(parts)[:limit]


class TalkEventLog:
    """服务端事件时间线 + 环境音转写缓冲。

    职责分两块，互不耦合：

    1. **计数（始终生效）**：统计各类事件次数，退出时据此判断"用户的话去了
       哪条路"。纯内存操作，无 IO，不产生任何用户可见输出。
    2. **时间线日志（开关控制）**：``enabled=True`` 时把每条事件按序写入
       ``~/.jarvis/logs/diag.log``（component=realtime，info 级不刷终端），
       用于事后复盘"谁在什么时候打断了谁""有没有出现无响应的空窗"。

    @author aceFelix
    """

    def __init__(self, enabled: bool = False, *, component: str = "realtime") -> None:
        self._enabled = enabled
        self._component = component
        self._seq = 0
        # item_id → delta 文本片段（completed 时定稿并清缓冲）
        self._ambient_parts: dict[str, list[str]] = {}
        # 已定稿的环境音转写（被服务端判为非有效轮次的语音）
        self.ambient_turns: list[str] = []
        # 进入对话轮的用户语音段数
        self.user_turns = 0
        # AI 回复条数
        self.ai_turns = 0
        # 服务端判定用户开口次数（含打断与新一轮）
        self.interrupts = 0
        # 被取消（response.done status=cancelled）的响应次数。
        # 连续自打断是“AI 永远不开口”的典型特征：残余回声被服务端
        # 识别为用户语音，触发 speech_started 后本轮响应被取消。
        self.cancelled_responses = 0
        # 被回声保护窗口拦下的误打断次数（AI 说话期间低电平 speech_started）。
        # 非零说明外放回声保护正在生效。
        self.echo_guard_hits = 0
        # 收到的全部事件数 / 其中被聚合成计数的高频事件数
        self.event_total = 0
        self.aggregated_events = 0
        # 最近若干条事件类型（新事件在末尾，连续重复折叠），供取消时刻回溯
        self.recent: list[str] = []

    @property
    def enabled(self) -> bool:
        """时间线日志开关是否打开。"""
        return self._enabled

    def observe(self, event_type: str, event: dict, *, detail: str = "") -> None:
        """记录一条服务端事件：计数 +（开关开启时）写时间线日志。

        Args:
            event_type: 事件类型（event["type"]）。
            event: 完整事件体，用于提取摘要。
            detail: 调用方提供的补充摘要，优先于自动提取。
        """
        self.event_total += 1
        # 事件链回溯：只收语义节点，连续重复折叠（否则音频帧会淹没关键节点）
        label = _chain_label(event_type, event)
        if label and (not self.recent or self.recent[-1] != label):
            self.recent.append(label)
            if len(self.recent) > _RECENT_LIMIT:
                del self.recent[0]
        if event_type in _AGGREGATED_EVENTS:
            self.aggregated_events += 1
            return
        if not self._enabled:
            return
        self._seq += 1
        brief = detail or _brief(event)
        diag.diag_log(self._component, f"#{self._seq} {event_type} {brief}".rstrip())

    def note_client(self, action: str, detail: str = "") -> None:
        """记录一条**客户端发出的指令**，与服务端事件共用同一条时间线。

        时间线此前只记服务端事件，无法回答"这句 cancel/create 究竟是我们发的、
        还是服务端自己做的"——而两种来源的修复方向完全相反。客户端指令统一带
        ``→`` 前缀，与服务端事件的 ``#序号`` 一眼区分。

        @author aceFelix
        """
        # 回溯缓冲与开关无关：取消诊断需在任何配置下都能当场给出事件链。
        # 客户端指令统一带 → 前缀，与服务端事件一眼区分；detail 一并入链
        # （如 mic → "→mic 静音"/"→mic 恢复上传"，取消归因时免猜状态）
        label = f"→{action} {detail}".rstrip()
        if not self.recent or self.recent[-1] != label:
            self.recent.append(label)
            if len(self.recent) > _RECENT_LIMIT:
                del self.recent[0]
        if not self._enabled:
            return
        self._seq += 1
        diag.diag_log(self._component, f"#{self._seq} →{action} {detail}".rstrip())

    def cancel_hint(self) -> str:
        """回溯本次会话最近的事件链（新→旧），用于当场定位取消成因。

        取消发生时，仅靠“是否为回声”已经分不出两类完全相反的成因：

        - 服务端收到新语音后主动取消（真打断）→ 链上会先出现 speech_started
        - 客户端在响应期补发 response.cancel（自打断）→ 链上没有 speech_started

        因此把最近的事件链直接打在终端，无需开启日志开关。

        @author aceFelix
        """
        if not self.recent:
            return ""
        return " ← ".join(reversed(self.recent))

    def dump_cancel_diag(self) -> None:
        """把取消时刻的诊断快照落盘（不依赖 event_log 开关）。

        终端里的事件链会随会话滚走，用户反馈时往往只剩一句“又取消了”。
        落盘一份含事件链与累计次数的快照，事后直接从日志末尾取用即可。

        @author aceFelix
        """
        if not self.recent:
            return
        chain = " ← ".join(reversed(self.recent))
        diag.diag_log(
            self._component,
            f"取消诊断 第{self.cancelled_responses}次 事件链(新→旧): {chain}",
        )

    def feed_ambient(self, event_type: str, event: dict) -> str:
        """喂入环境音转写事件，返回**定稿文本**（delta 阶段返回空串）。

        delta 阶段按 item_id 累积片段；completed 阶段定稿并清缓冲。
        文档未给出 completed 的字段示例，故按 transcript → text → 缓冲累积
        的优先级兜底，避免字段差异导致文本丢失。

        @author aceFelix
        """
        item_id = str(event.get("item_id", "") or "")
        text = str(event.get("text", "") or "").strip()

        if event_type == AMBIENT_DELTA:
            if text:
                self._ambient_parts.setdefault(item_id, []).append(text)
            return ""

        final = str(event.get("transcript", "") or "").strip()
        buffered = "".join(self._ambient_parts.pop(item_id, [])).strip()
        if not final:
            final = text or buffered
        if final:
            self.ambient_turns.append(final)
        return final

    def note_user_turn(self) -> None:
        """记一次"进入了对话轮"的用户语音（与被动过滤的环境音区分开）。"""
        self.user_turns += 1

    def note_ai_turn(self) -> None:
        """记一次 AI 回复。"""
        self.ai_turns += 1

    def note_interrupt(self) -> None:
        """记一次服务端判定用户开口（可能打断、也可能是新一轮）。"""
        self.interrupts += 1

    def note_cancelled(self) -> None:
        """记一次被取消的响应（response.done 的 status=cancelled）。"""
        self.cancelled_responses += 1

    def note_echo_guard(self) -> None:
        """记一次被回声保护拦下的误打断（AI 说话期间低电平 speech_started）。"""
        self.echo_guard_hits += 1

    def report_lines(self) -> list[str]:
        """退出时的会话诊断摘要；一切正常时返回空列表。

        可叠加输出三类信号，正常对话不受打扰：

        1. AI 从未完成过回复且有响应被取消（连续自打断，多为回声残留）
        2. 有语音被判定为非有效轮次（走了 ambient 通道）
        3. 回声保护拦下过误打断（外放保护生效的正面反馈）

        这组摘要直接回答“我一直在说，它不回复”的成因与保护工作状态。
        """
        lines: list[str] = []

        # 自打断诊断：AI 一次也没说完过，但响应被反复取消
        if self.ai_turns == 0 and self.cancelled_responses:
            lines.append(
                f" 本次 {self.cancelled_responses} 次回复被取消，AI 未能完成任何一条回复"
            )
            lines.append(
                "   典型成因：扬声器回声被麦克风重新拾取→服务端当成您在说话→打断并取消回复"
            )
            lines.append(
                "   建议：改用耳机，或确认 [realtime_talk] echo_suppress_with_aec = true"
            )

        # 语义过滤诊断：有语音被判为非有效轮次
        if self.ambient_turns:
            lines.append(
                f"📋 本次有 {len(self.ambient_turns)} 段语音被服务端判为非有效轮次"
                "（已听到、未进入对话轮）"
            )
            if self.user_turns:
                lines.append(f"   另有 {self.user_turns} 段进入对话轮，对话链路本身正常")
            else:
                lines.append(
                    "   没有任何语音进入对话轮 —— 这就是「一直在说、它不回复」的原因"
                )

        # 回声保护工作状态：拦下过误打断属正常，轻提示
        if self.echo_guard_hits:
            lines.append(
                f"🛡️ 已自动忽略 {self.echo_guard_hits} 次回声触发的误打断（外放保护生效）"
            )

        if lines and self._enabled:
            path = diag.get_log_path()
            if path is not None:
                lines.append(f"   事件时间线: {path}")
        return lines

    def log_hint(self) -> str:
        """时间线日志路径提示（开关关闭时返回空串）。"""
        if not self._enabled:
            return ""
        path = diag.get_log_path()
        return str(path) if path is not None else "已开启"
