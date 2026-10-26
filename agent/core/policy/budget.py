"""打扰预算（notify budget）—— "该不该现在打扰你"的确定性闸门（W1·阶段 8）。

理论锚点：Horvitz《Attention-Sensitive Alerting》——打扰决策形式化为
效用与中断代价的比较。本模块用可解释的确定性规则近似：

- daily_max：每日主动播报次数上限（超出后入库待补播）
- quiet_hours：静默时段（如 23:00-07:00）只入库；时段结束后聚合为一条
  （冲刷不占 daily_max——它是补播；且受 `aggregate_window_s` 冷却约束，
  同一窗口内最多冲刷一次）。**静默入库前仍要先过 min_salience**：
  门槛判的是“这件事值不值得打扰”，静默判的是“现在方不方便打扰”，
  前者不成立的事不该在队列里等着半夜过后补播
- aggregate_window_s：同源事件窗口内合并成一条（防刷屏）
- min_salience：重要度门槛，低分事件**直接抑制**（只进感知总线与留痕文件，
  不进播报队列）——旧版“降级入库”配合无冷却冲刷，等于每两条必播一条，
  门槛被自己的冲刷路径旁路；而闸门顺序把静默排在门槛之前，于是低分事件
  照样能在夜里排队、早上绕过门槛播出（2026-10-19 走查发现，见 fixlog）
- channels：播报渠道优先级（桌面通知 > TTS > 手机推送）

重要度评分 = 来源权重 × 时效紧迫度 × 相关性 × 历史接受率。
历史接受率来自用户反馈（record_accept），初始 1.0 —— "越用越懂你"
的闭环入口（习惯学习 P2-7 复用同一数据）。

落盘 `~/.jarvis/notify_state.json`：当日计数、待发队列、源统计。

@author aceFelix
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.policy")

_DEFAULT_PATH = Path.home() / ".jarvis" / "notify_state.json"

#: 时间写法容错用：允许 `H:MM` / `HH:MM`（可选秒）。`time.fromisoformat` 只认
#: 两位小时，用户写 `"23:00-7:00"` 会抛 ValueError，旧实现抓到后直接
#: `return False`——静默时段整个默默失效，是“配置写了不生效”最阴的一种。
_HM_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def parse_clock(raw: str) -> dtime | None:
    """解析一个时刻写法，失败返回 None（不抛）。

    先试标准 ISO（`23:00` / `07:00` / `07:00:30`），不行再走容错分支接
    个位数小时（`7:00`）。用户手写配置时少写一个 0 是常态，不能因此把
    整条静默时段作废。

    Args:
        raw: 形如 ``HH:MM`` 或 ``H:MM`` 的原始串（允许首尾空格）。

    Returns: 解析出的 ``time``；空串/格式非法/超出 24 小时制返回 None。
    """
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return dtime.fromisoformat(s)
    except ValueError:
        pass
    m = _HM_RE.fullmatch(s)
    if not m:
        return None
    hour, minute, second = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        return None
    return dtime(hour, minute, second)


def parse_quiet_range(raw: str) -> tuple[dtime, dtime] | None:
    """解析静默时段区间，返回 None = 不启用或写法不可用。

    形式 ``"HH:MM-HH:MM"``，支持跨午夜（start > end 即视为跨过 00:00）。
    任一端解析失败都返回 None，**由调用方负责报出去**（不能静默当“没启用”）。
    """
    rng = (raw or "").strip()
    if not rng or "-" not in rng:
        return None
    start_s, end_s = rng.split("-", 1)
    start, end = parse_clock(start_s), parse_clock(end_s)
    if start is None or end is None:
        return None
    return start, end


# 内置来源权重表（未登记的源用 default）
_DEFAULT_SOURCE_WEIGHTS = {
    "briefing": 0.9,      # 每日简报：设计好的固定打扰
    "deadline": 0.8,      # 截止日期：有时效
    "calendar": 0.85,     # 日程提醒：临近即高优
    "system": 0.7,        # 系统健康告警
    "schedule": 0.9,      # 用户自己设的提醒
    "default": 0.5,
    # W2 感知源（`percept:<源名>`）：必须显式登记。旧版没有这几项，所有感知
    # 事件落到 default 0.5，乘上源自带的低先验后**全部低于 0.3 门槛**，
    # 再叠加“入队→下一条冲刷”的乒乓，实际效果是“该静默的照样吵你、
    # 该吵的反而被门槛吃掉”（2026-10 人工走查发现，详见
    # docs/fixlogs/notify-budget-percept-relay-fix.md）。取值口径：
    # 用户显式开启且值得打断 > 顺路看到的现场事实。
    "percept:mail": 0.9,       # 白名单来信：最该打断
    "percept:vcs": 0.7,        # 仓库卫生（未提交/未推/落后）：开发者每天用得上
    "percept:file": 0.6,       # 目录堆积：值得说，但不急
    "percept:window": 0.5,     # 切屏：只让“停留超时”过闸，普通切换只留痕
    "percept:vision": 0.4,     # 在场类：主要喂 away 推断，不直接播报
    "percept:calendar": 0.7,   # 以下三个是收编位（本期未装配），先登记免得
    "percept:system": 0.7,     # 将来接上又落回 default 重踩同一个坑
    "percept:schedule": 0.9,
    # W3 任务脊柱（源名固定 `task`，见 `core/tasks/driver.py::NOTIFY_SOURCE`）：
    # 后台目标都是用户明确交代的长线任务，权重与 `schedule` 同级。
    # 驱动器递出来的紧迫度固定给 0.45（后台结果不是“突发紧事”），因此
    # 0.9×0.45=0.405 仍在门槛之上；不登记则落 default 0.5→得分 0.225，
    # 整个脊柱会“永远不吱声”——与 percept 当年那个坑同源，故一并登记
    # （docs/fixlogs/notify-budget-percept-relay-fix.md）。
    "task": 0.9,
}

# 聚合冲刷单条最多列几行（超出只留最新的，并在尾部注明略去数量）：
# 队列上限 50，不封顶时会拼出一条 50 行的巨块气泡，TTS 还会在 200 字处截断
_FLUSH_MAX_LINES = 5


@dataclass
class NotifyAction:
    """一次通知请求的闸门裁决。

    Attributes:
        kind: ``emit``（照播）/ ``queue``（入库暂缓）/ ``suppress``（丢弃）。
        text: 要播出的文本（queue/suppress 时是原文，仅用于展示与排查）。
        reason: 人话解释（落日志与面板提示，用户问“为何不说话”时拿它回答）。
        speak: 仅对 kind=emit 有意义。False = **降级投递**：气泡与系统通知照投，
            但不 TTS 朗读。用于“用户明确要过的提醒”这类不该整条延后、
            但静默时段/额度用尽时确实不该出声的情形。
    """

    kind: str
    text: str
    reason: str
    speak: bool = True

    @property
    def should_emit(self) -> bool:
        return self.kind == "emit"


@dataclass
class BudgetConfig:
    daily_max: int = 10
    quiet_hours: str = "23:00-07:00"   # "HH:MM-HH:MM"，支持跨午夜；空串=不启用
    aggregate_window_s: int = 300
    min_salience: float = 0.3
    channels: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.channels is None:
            self.channels = ["desktop", "tts", "mobile"]


class NotifyBudget:
    """打扰预算闸门。ProactiveEngine 所有 on_notify 出口统一过闸。"""

    def __init__(
        self,
        config: BudgetConfig | None = None,
        *,
        path: Path | str | None = None,
        source_weights: dict[str, float] | None = None,
    ) -> None:
        self.config = config or BudgetConfig()
        self._path = Path(path) if path else _DEFAULT_PATH
        self._weights = dict(_DEFAULT_SOURCE_WEIGHTS)
        if source_weights:
            self._weights.update(source_weights)
        # 静默时段写了但解析不出来 → 必须说一声。以前这种写法会被当成
        # “没启用”，用户改了配置没反应且日志里一个字都没有。
        quiet = (self.config.quiet_hours or "").strip()
        if quiet and parse_quiet_range(quiet) is None:
            logger.warning(
                "quiet_hours 写法不可用（%r），静默时段未生效；"
                '应为 "HH:MM-HH:MM"，如 "23:00-07:00"', quiet,
            )

    # ---- 评分 ----

    def salience(self, source: str, urgency: float = 0.5, relevance: float = 1.0,
                 state: dict | None = None) -> float:
        """重要度 = 来源权重 × 紧迫度 × 相关性 × 历史接受率（0~1）。"""
        weight = self._weights.get(source, self._weights["default"])
        accept = 1.0
        if state:
            rates = state.get("accept_rate", {})
            accept = rates.get(source, 1.0)
        return max(0.0, min(1.0, weight * urgency * relevance * accept))

    def record_accept(self, source: str, accepted: bool) -> None:
        """记录一次用户对提醒的响应（采纳/忽略），滑动更新接受率。"""
        state = self._load()
        stats = state.setdefault("accept_stats", {})
        seen, ok = stats.get(source, [0, 0])
        seen += 1
        ok += 1 if accepted else 0
        stats[source] = [seen, ok]
        state.setdefault("accept_rate", {})[source] = ok / seen
        self._save(state)

    # ---- 闸门主入口 ----

    def gate(
        self,
        text: str,
        source: str,
        *,
        dedup_key: str = "",
        urgency: float = 0.5,
        relevance: float = 1.0,
        now: datetime | None = None,
        silent_delivery: bool = False,
    ) -> NotifyAction:
        """裁决一条通知请求：立即播报 / 入库暂缓 / 静默丢弃。

        顺序：①非静默时段且已到冲刷时机→先聚合冲刷积压；②静默时段入库；
        ③窗口内同源重复合并；④低于 salience 门槛**直接抑制**（不入队等补
        播）；⑤超日预算入库。

        ``silent_delivery=True`` 会把②与⑤的“入库待补播”改成**降级投递**
        （emit + speak=False，气泡照上屏、不朗读、不入队也不占额度）：用户
        明确要过的提醒整条延后是错的行为（而冲刷靠下一条请求触发，夜里没
        动静就可能永不补）。调用方只有桌面 hub 的提醒路径会传 True。

        冲刷与当前请求**合一返回**：旧实现在冲刷分支直接 `return`，把触发
        冲刷的那条事件整条丢掉（既没播也没入队），而冲刷本身又无冷却，
        导致“入队→下一条冲刷”乒乓、门槛被自己的冲刷路径旁路。
        2026-10 人工走查发现，详见 docs/fixlogs/notify-budget-percept-relay-fix.md。

        @author aceFelix
        """
        now = now or datetime.now()
        state = self._load()
        self._rollover_day(state, now)
        key = dedup_key or f"{source}:{text[:40]}"

        # ① 积压冲刷：需同时满足“非静默时段 + 队列非空 + 距上次冲刷够久”。
        #    冷却是必需的：没冷却时⑤入队的事件会被下一条请求立刻冲刷出去。
        digest: NotifyAction | None = None
        if (
            not self._in_quiet(now)
            and state.get("pending")
            and self._flush_due(state, now)
        ):
            digest = self._flush_pending(state, now)

        current = self._gate_current(
            state, text, source, key, urgency, relevance, now,
            silent_delivery=silent_delivery,
        )
        self._save(state)   # 统一落盘：两个 helper 只改 state，不各自写文件
        if digest is None:
            return current
        if not current.should_emit:
            return digest
        # 一次调用只起一条打扰：当前事件附在摘要尾部，不丢信息也不叠两个气泡
        merged = f"{digest.text}\n  • [{source}] {text}"
        return NotifyAction("emit", merged, f"{digest.reason}；并附当前事件")

    def _gate_current(
        self, state: dict, text: str, source: str, key: str,
        urgency: float, relevance: float, now: datetime,
        *, silent_delivery: bool = False,
    ) -> NotifyAction:
        """当前这条请求自身的裁决（④→②→③→⑤），只改 state，不落盘。

        闸门顺序在 2026-10-19 调过一次：门槛提到静默判断**之前**。旧顺序下
        静默时段入库完全不评分，于是 0.15 分的“切到记事本”照样排队，早上
        一出静默就被冲刷绕过门槛念给用户听——和它白天被拦掉是同一件事，
        等于门槛只在白天生效。实测换序不影响任何非感知源：简报/截止/日程
        的权重×紧迫度最低也是 0.5×0.6=0.30，仍在门槛之上。
        """
        # ④ 重要度门槛：低于门槛**直接抑制**，任何时段都不配占用队列。
        #    事件本身仍在感知总线与 events.jsonl 里，只是不出声。
        score = self.salience(source, urgency, relevance, state)
        if score < self.config.min_salience:
            return NotifyAction(
                "suppress", text,
                f"重要度 {score:.2f} 低于门槛 {self.config.min_salience}，仅留痕不播报",
            )

        # ② 静默时段：够重要但当时不能说 → 入库待补播（这类积压是“该说但
        #    当时不能说”，值得补播；不够重要的在上一步已经被拦掉了）
        if self._in_quiet(now):
            if not silent_delivery:
                self._append_pending(state, text, source, key, now)
                return NotifyAction("queue", text, f"静默时段（{self.config.quiet_hours}）暂缓入库")
            # 降级投递：气泡照投、不朗读。不占 daily_max（上屏不算一次打扰），
            # 但仍记 last_emit，否则同一条提醒反复升级会反复上屏。
            dup = self._merge_recent(state, key, now)
            if dup is not None:
                return NotifyAction("suppress", text, dup)
            self._mark_emitted(state, key, now)
            return NotifyAction(
                "emit", text,
                f"静默时段（{self.config.quiet_hours}）降级：只上屏不朗读",
                speak=False,
            )

        # ③ 聚合窗口：同源窗口内已播过 → 合并抑制
        merged_reason = self._merge_recent(state, key, now)
        if merged_reason is not None:
            return NotifyAction("suppress", text, merged_reason)

        # ⑤ 日预算：够重要但额度用尽 → 入库等冲刷（冲刷不占额度，否则永远出不来）
        if state["count"] >= self.config.daily_max:
            if not silent_delivery:
                self._append_pending(state, text, source, key, now)
                return NotifyAction("queue", text, f"已达每日上限 {self.config.daily_max}，入库待补播")
            # 同上：提醒类降级上屏，不出声也不占额度
            self._mark_emitted(state, key, now)
            return NotifyAction(
                "emit", text,
                f"已达每日上限 {self.config.daily_max}，降级：只上屏不朗读",
                speak=False,
            )

        # 放行：计数 + 记 last_emit
        state["count"] += 1
        self._mark_emitted(state, key, now)
        return NotifyAction("emit", text, f"预算放行（今日 {state['count']}/{self.config.daily_max}，重要度 {score:.2f}）")

    def _merge_recent(self, state: dict, key: str, now: datetime) -> str | None:
        """同源事件在聚合窗口内已播过 → 返回合并理由，否则 None（并计数）。

        抽出来是因为②的降级投递与③的正常路径都要查同一条窗口规则，
        写两处就会分叉（一处合并、一处不合并，升级重发就能绕过去）。
        """
        recent = state.get("last_emit", {}).get(key)
        if not recent:
            return None
        try:
            gap = (now - datetime.fromisoformat(recent["ts"])).total_seconds()
        except ValueError:
            return None
        if 0 <= gap < self.config.aggregate_window_s:
            recent["merged"] = recent.get("merged", 1) + 1
            return f"{int(gap)}s 前已播同源事件，窗口内合并"
        return None

    def _mark_emitted(self, state: dict, key: str, now: datetime) -> None:
        """记一次上屏时刻（聚合窗口的依据）；降级投递不计入 daily_max。"""
        state.setdefault("last_emit", {})[key] = {
            "ts": now.isoformat(timespec="seconds"), "merged": 1,
        }

    def pending_count(self) -> int:
        return len(self._load().get("pending", []))

    # ---- 内部 ----

    def _flush_due(self, state: dict, now: datetime) -> bool:
        """冲刷冷却：同一个 `aggregate_window_s` 内最多冲刷一次。

        为什么必须有：没冷却时，⑤（超额度）入队的事件会被下一条请求立刻
        冲刷出去，变成“每两条播一条”的继电器——预算形同虚设。
        """
        last = state.get("last_flush")
        if not last:
            return True
        try:
            gap = (now - datetime.fromisoformat(str(last))).total_seconds()
        except ValueError:
            return True
        return gap >= self.config.aggregate_window_s

    def _flush_pending(self, state: dict, now: datetime) -> NotifyAction:
        """把积压聚合成一条播出。

        三条口径修正（2026-10 走查）：① 冲刷**不占 daily_max**——它是补播
        不是新增打扰，占额度会导致“预算满 → 积压播不出去 → 积压自己咬自己”
        的死循环（旧实现确实把摘要文本重新塞回 pending）；② 单条不套
        “积压 N 条”壳；③ 文案不再写“静默时段”——预算满造成的积压并不在静默
        时段，旧文案在下午五点说“静默时段积压”是谎话。
        """
        # 按时间重排：同 key 合并时会把 ts 刷成最新一次（见 _append_pending），
        # 但条目位置不变，于是队列不再单调，补播摘要里“最近 N 条”会挑错。
        items = sorted(state.get("pending", []), key=lambda i: str(i.get("ts", "")))
        state["pending"] = []
        state["last_flush"] = now.isoformat(timespec="seconds")
        if not items:
            return NotifyAction("queue", "", "无积压")   # 防御：调用方已判过非空
        if len(items) == 1:
            one = items[0]
            return NotifyAction("emit", one["text"], "补播 1 条积压")
        shown = items[-_FLUSH_MAX_LINES:]
        omitted = len(items) - len(shown)
        joined = "\n".join(f"  • [{i['source']}] {i['text']}" for i in shown)
        tail = f"\n  （另有 {omitted} 条更早的已略去）" if omitted else ""
        text = f"📥 补播 {len(items)} 条提醒，一并呈上：\n{joined}{tail}"
        return NotifyAction("emit", text, f"聚合冲刷 {len(items)} 条积压")

    def _append_pending(self, state: dict, text: str, source: str, key: str, now: datetime) -> None:
        """入待发队列（同 key 已入则只合并计数，不重复存文本）。"""
        pending = state.setdefault("pending", [])
        for item in pending:
            if item.get("key") == key:
                # ts 刷成最新一次 = “这件事最后一次发生的时间”，代价是队列不再
                # 按 ts 有序，故冲刷侧统一重排（见 _flush_pending）
                item["merged"] = item.get("merged", 1) + 1
                item["ts"] = now.isoformat(timespec="seconds")
                return
        pending.append({"ts": now.isoformat(timespec="seconds"), "source": source,
                        "key": key, "text": text})
        del pending[:-50]  # 队列上限 50，丢最旧

    def _in_quiet(self, now: datetime) -> bool:
        """当前是否处于静默时段（支持跨午夜区间，如 23:00-07:00）。"""
        rng = parse_quiet_range(self.config.quiet_hours)
        if rng is None:
            return False
        start, end = rng
        cur = now.time()
        if start <= end:
            return start <= cur < end
        return cur >= start or cur < end  # 跨午夜

    def _rollover_day(self, state: dict, now: datetime) -> None:
        """跨天重置当日计数（保留接受率与 last_emit 无意义，一并清）。"""
        today = now.date().isoformat()
        if state.get("day") != today:
            state["day"] = today
            state["count"] = 0
            state["last_emit"] = {}

    def stats(self) -> dict[str, Any]:
        state = self._load()
        return {
            "day": state.get("day", ""), "count": state.get("count", 0),
            "daily_max": self.config.daily_max, "pending": len(state.get("pending", [])),
            "quiet_hours": self.config.quiet_hours,
        }

    # ---- 持久化 ----

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
