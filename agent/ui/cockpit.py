"""任务驾驶舱的数据模型（独立窗口版，C1b/C3-lite）。

2026-11-08 方向变更：用户否决了"终端底部内嵌 Rich Live"的 C1 方案，
改为**独立 tkinter HUD 窗口**。C3-lite 在此基础上增加多任务浮窗卡，
每个运行中的工具一张卡，渲染在 ``agent/ui/cockpit_cards.py``。

本模块只做三件事：
1. ``CockpitState``：消费 trace 事件，维护步数/失败/并行工具/数据流/抓取；
2. 文本格式化（毫秒、字节、URL），供主窗与卡片渲染复用；
3. ``VisualOptions``：``[visualization]`` 配置的归一化、钳制与启用判定。

不依赖 Rich、不碰终端、不创建窗口。

@author aceFelix
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from agent.ui.trace_view import describe_target, render_event

# 状态容器容量：全部有界，防止长任务内存增长
FEED_MAX = 8
STEP_MAX = 4
FETCH_MAX = 200
EXTRACT_MAX = 10

_MODE_OFF = "off"
_MODE_WINDOW = "window"

_CARDS_MODES = ("auto", "always", "off")
_EFFECTS_MODES = ("off", "subtle", "full")


def normalize_mode(value: Any) -> str:
    """归一化任务驾驶舱模式：``off`` 关闭，其余一律 ``window``。

    Args:
        value: 配置原值（历史上出现过 auto / terminal / window / off）。

    Returns:
        ``"off"`` 或 ``"window"``。
    """
    if str(value or "").strip().lower() == _MODE_OFF:
        return _MODE_OFF
    return _MODE_WINDOW


def normalize_tool_cards(value: Any) -> str:
    """归一化多任务浮窗模式：auto / always / off；未知值降级为 auto。"""
    text = str(value or "").strip().lower()
    return text if text in _CARDS_MODES else "auto"


def normalize_effects(value: Any) -> str:
    """归一化动效强度：off / subtle；``full`` 暂按 subtle 处理（预留）。"""
    text = str(value or "").strip().lower()
    if text == "off":
        return "off"
    if text == "full":
        return "subtle"
    return "subtle"


def normalize_agent_cards(value: Any) -> str:
    """归一化子代理卡模式：auto / off；未知值降级为 auto。"""
    return "off" if str(value or "").strip().lower() == "off" else "auto"


def normalize_crawl_window(value: Any) -> str:
    """归一化左侧抓取网络窗口模式：``auto`` / ``off``；未知值降级为 auto。

    ``auto`` = 本轮出现抓取才弹出、结束保留；``off`` = 完全不创建该窗口。
    @author aceFelix
    """
    return "off" if str(value or "").strip().lower() == "off" else "auto"


def normalize_crawl_topology(value: Any) -> str:
    """归一化拓扑绘制模式：``auto`` / ``off``；未知值降级为 auto。

    ``off`` 时窗口只保留 URL 清单/抽取表/统计，不画分层拓扑。
    @author aceFelix
    """
    return "off" if str(value or "").strip().lower() == "off" else "auto"


def clamp_int(value: Any, default: int, low: int, high: int) -> int:
    """宽容转 int 并钳制到 [low, high]；非法值回默认值再钳制。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def cockpit_enabled(mode: str, voice_mode: bool) -> bool:
    """兼容旧调用：判断主 HUD 是否启用（仅看 window/off）。

    2026-11-09 用户要求：``/voice`` 等语音模式同样保留操作可视化，
    因此 ``voice_mode`` 不再参与抑制判定；参数保留仅为兼容既有调用方。

    @author aceFelix
    """
    return normalize_mode(mode) != _MODE_OFF


@dataclass(frozen=True)
class VisualOptions:
    """``[visualization]`` 配置的运行时快照（已归一化、已钳制）。

    Attributes:
        enabled: 总开关；False 时关闭所有可视化窗口。
        task_cockpit: ``window`` / ``off``。
        tool_cards: ``auto`` / ``always`` / ``off``。
        card_min_ms: auto 模式下工具持续多久才弹卡（0–5000）。
        card_max: 同时最多几张卡（1–6）。
        card_hold_ms: 完成后保留多久再收（0–3000）。
        effects: ``off`` / ``subtle``（full 暂按 subtle）。
        position_memory: 是否记忆主 HUD 位置。

    @author aceFelix
    """

    enabled: bool = True
    task_cockpit: str = "window"
    tool_cards: str = "auto"
    card_min_ms: int = 300
    card_max: int = 4
    card_hold_ms: int = 600
    effects: str = "subtle"
    position_memory: bool = True
    agent_cards: str = "auto"
    agent_card_max: int = 3
    agent_card_hold_ms: int = 800
    agent_card_keep_idle: bool = True
    agent_card_min_ms: int = 0
    # 左侧抓取网络窗口（C2-lite，2026-11-09）：auto/off
    crawl_window: str = "auto"
    # 左侧窗口的分层拓扑（P3）：auto 画拓扑；off 只保留清单/抽取/统计。
    # crawl_max_nodes 为可见节点总上限（20–200），超出折叠为 +N。
    # @author aceFelix
    crawl_topology: str = "auto"
    crawl_max_nodes: int = 60

    @classmethod
    def from_values(
        cls,
        *,
        enabled: Any = True,
        task_cockpit: Any = "window",
        tool_cards: Any = "auto",
        card_min_ms: Any = 300,
        card_max: Any = 4,
        card_hold_ms: Any = 600,
        effects: Any = "subtle",
        position_memory: Any = True,
        agent_cards: Any = "auto",
        agent_card_max: Any = 3,
        agent_card_hold_ms: Any = 800,
        agent_card_keep_idle: Any = True,
        agent_card_min_ms: Any = 0,
        crawl_window: Any = "auto",
        crawl_topology: Any = "auto",
        crawl_max_nodes: Any = 60,
    ) -> "VisualOptions":
        """从原始配置值构造已归一化的选项。

        这里集中做枚举归一与数值钳制，避免各处散落 if/else。
        """
        return cls(
            enabled=bool(enabled),
            task_cockpit=normalize_mode(task_cockpit),
            tool_cards=normalize_tool_cards(tool_cards),
            card_min_ms=clamp_int(card_min_ms, 300, 0, 5000),
            card_max=clamp_int(card_max, 4, 1, 6),
            card_hold_ms=clamp_int(card_hold_ms, 600, 0, 3000),
            effects=normalize_effects(effects),
            position_memory=bool(position_memory),
            agent_cards=normalize_agent_cards(agent_cards),
            agent_card_max=clamp_int(agent_card_max, 3, 1, 6),
            agent_card_hold_ms=clamp_int(agent_card_hold_ms, 800, 0, 3000),
            agent_card_keep_idle=bool(agent_card_keep_idle),
            agent_card_min_ms=clamp_int(agent_card_min_ms, 0, 0, 5000),
            crawl_window=normalize_crawl_window(crawl_window),
            crawl_topology=normalize_crawl_topology(crawl_topology),
            crawl_max_nodes=clamp_int(crawl_max_nodes, 60, 20, 200),
        )

    @classmethod
    def from_settings(cls, settings: Any) -> "VisualOptions":
        """从 ``Settings`` 读取 ``visual_*`` 字段（鸭子类型，避免循环导入）。"""
        return cls.from_values(
            enabled=getattr(settings, "visual_enabled", True),
            task_cockpit=getattr(settings, "visual_cockpit", "window"),
            tool_cards=getattr(settings, "visual_tool_cards", "auto"),
            card_min_ms=getattr(settings, "visual_tool_card_min_ms", 300),
            card_max=getattr(settings, "visual_tool_card_max", 4),
            card_hold_ms=getattr(settings, "visual_tool_card_hold_ms", 600),
            effects=getattr(settings, "visual_effects", "subtle"),
            position_memory=getattr(settings, "visual_position_memory", True),
            agent_cards=getattr(settings, "visual_agent_cards", "auto"),
            agent_card_max=getattr(settings, "visual_agent_card_max", 3),
            agent_card_hold_ms=getattr(settings, "visual_agent_card_hold_ms", 800),
            agent_card_keep_idle=getattr(settings, "visual_agent_card_keep_idle", True),
            agent_card_min_ms=getattr(settings, "visual_agent_card_min_ms", 0),
            crawl_window=getattr(settings, "visual_crawl_window", "auto"),
            crawl_topology=getattr(settings, "visual_crawl_topology", "auto"),
            crawl_max_nodes=getattr(settings, "visual_crawl_max_nodes", 60),
        )

    def cockpit_on(self) -> bool:
        """主 HUD 是否启用（总开关 + 子开关）。"""
        return self.enabled and self.task_cockpit != _MODE_OFF

    def cards_on(self) -> bool:
        """多任务浮窗是否启用（总开关 + 子开关）。"""
        return self.enabled and self.tool_cards != _MODE_OFF

    def any_enabled(self) -> bool:
        """是否至少有一个可视化窗口需要显示。"""
        return (
            self.cockpit_on()
            or self.cards_on()
            or self.agent_cards_on()
            or self.crawl_on()
        )

    def agent_cards_on(self) -> bool:
        """子代理/队友卡是否启用（总开关 + 子开关）。"""
        return self.enabled and self.agent_cards != _MODE_OFF

    def crawl_on(self) -> bool:
        """左侧抓取网络窗口是否启用（总开关 + 子开关）。"""
        return self.enabled and self.crawl_window != _MODE_OFF

    def crawl_topology_on(self) -> bool:
        """左侧窗口是否绘制分层拓扑（总开关 + 拓扑子开关）。"""
        return self.crawl_on() and self.crawl_topology != _MODE_OFF


def visual_enabled(options: VisualOptions, voice_mode: bool) -> bool:
    """判断是否启用任一可视化窗口。

    2026-11-09 用户要求：``/voice`` 语音对话期间也要有主 HUD、工具卡与代理卡，
    所以语音模式不再关闭可视化；是否显示只由 ``[visualization]`` 配置决定。
    ``voice_mode`` 参数保留仅为兼容既有调用方。

    @author aceFelix
    """
    return options.any_enabled()


def _fmt_ms(ms: Any) -> str:
    """毫秒 → 人读时长；缺失字段返回空串（绝不输出 None）。"""
    if ms is None:
        return ""
    try:
        value = float(ms)
    except (TypeError, ValueError):
        return ""
    if value >= 1000:
        return f"{value / 1000:.1f}s"
    return f"{int(value)}ms"


def _fmt_bytes(n: Any) -> str:
    """字节数 → 人读大小；缺失字段返回空串。"""
    if n is None:
        return ""
    try:
        value = int(n)
    except (TypeError, ValueError):
        return ""
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f}MB"
    if value >= 1024:
        return f"{int(value / 1024)}KB"
    return f"{value}B"


def _short_url(url: Any, limit: int = 40) -> str:
    """去掉协议前缀并截断过长 URL，保证窗口单行显示。"""
    text = str(url or "")
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _summary_of(event: dict[str, Any]) -> str | None:
    """把事件转成数据流单行摘要（复用 trace_view 的渲染口径）。"""
    line = render_event(event)
    if line:
        return line
    if event.get("type") == "web_fetch":
        return f"🌐 {_short_url(event.get('url'), limit=56)}"
    return None


class CockpitState:
    """一轮任务的数据状态（主 HUD 与浮窗卡的唯一数据源）。

    Attributes:
        run_id: 当前轮次 id（``run_start`` 写入）。
        started_at: 本轮开始时刻（注入的单调时钟，用于实时总耗时）。
        finished: 是否已收到 ``run_end``。
        final_steps / final_failed / final_duration_ms: ``run_end`` 的权威汇总。
        steps / failed: 实时计数。
        active: 并行工具集合，key=``tool_use_id``。
        active_order: 工具启动顺序，决定浮窗卡槽位。
        current: 最后启动的工具（兼容主 HUD 的单值展示）。
        step_history: 最近几步。
        feed / fetches / extracts / stats: 数据流与抓取。

    @author aceFelix
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """初始化状态。

        Args:
            clock: 单调时钟，测试可注入假时钟。
        """
        self._clock = clock
        self.run_id = ""
        self.started_at = 0.0
        self.finished = False
        self.final_steps: int | None = None
        self.final_failed: int | None = None
        self.final_duration_ms: float | None = None
        self.steps = 0
        self.failed = 0
        self.active: dict[str, dict[str, Any]] = {}
        self.active_order: list[str] = []
        self.current: dict[str, Any] | None = None
        self.last_ok: bool | None = None
        self.step_history: deque[dict[str, Any]] = deque(maxlen=STEP_MAX)
        self.feed: deque[str] = deque(maxlen=FEED_MAX)
        self.fetches: deque[dict[str, Any]] = deque(maxlen=FETCH_MAX)
        self.extracts: deque[dict[str, Any]] = deque(maxlen=EXTRACT_MAX)
        self.stats: dict[str, Any] = {}

    def reset(self, run_id: str = "") -> None:
        """换轮清空所有状态（``run_start`` 调用）。"""
        self.run_id = run_id
        self.started_at = self._clock()
        self.finished = False
        self.final_steps = None
        self.final_failed = None
        self.final_duration_ms = None
        self.steps = 0
        self.failed = 0
        self.active.clear()
        self.active_order.clear()
        self.current = None
        self.last_ok = None
        self.step_history.clear()
        self.feed.clear()
        self.fetches.clear()
        self.extracts.clear()
        self.stats = {}

    def elapsed_ms(self) -> float:
        """本轮耗时（毫秒）：已结束时用 run_end 的权威值，否则用墙钟。"""
        if self.finished and self.final_duration_ms is not None:
            return max(0.0, float(self.final_duration_ms))
        if not self.started_at:
            return 0.0
        return max(0.0, (self._clock() - self.started_at) * 1000)

    def display_steps(self) -> int:
        """展示用步数：结束后取 run_end 汇总，避免与 trace 口径不一致。"""
        return int(self.final_steps) if self.finished and self.final_steps is not None else self.steps

    def display_failed(self) -> int:
        """展示用失败数：结束后取 run_end 汇总。"""
        return int(self.final_failed) if self.finished and self.final_failed is not None else self.failed

    def active_tools(self) -> list[str]:
        """当前并行工具名列表（按启动顺序）。"""
        return [
            str(self.active[tid].get("tool") or "?")
            for tid in self.active_order
            if tid in self.active
        ]

    def update(self, event: dict[str, Any]) -> None:
        """消费一条 trace 事件并更新状态（不产生副作用）。"""
        etype = event.get("type")
        # 子代理工具事件由代理卡消费；主 HUD 的步数/失败/工具卡必须过滤，
        # 否则一个子代理会把主任务统计刷爆（2026-11-08 拆分）。@author aceFelix
        if event.get("agent_id") and etype in (
            "tool_start", "tool_end", "web_fetch", "web_extract", "crawl_stats"
        ):
            return
        if etype == "run_start":
            self.reset(str(event.get("run_id") or ""))
            return
        if etype == "run_end":
            self.finished = True
            self.active.clear()
            self.active_order.clear()
            self.current = None
            self.final_steps = _as_int(event.get("steps"))
            self.final_failed = _as_int(event.get("failed"))
            self.final_duration_ms = _as_float(event.get("duration_ms"))
            return
        if not self.started_at:
            # 直接喂工具事件（无 run_start）时兜底起算
            self.started_at = self._clock()

        if etype == "tool_start":
            self._on_tool_start(event)
        elif etype == "tool_end":
            self._on_tool_end(event)
        elif etype == "web_fetch":
            # 单页抓取不写 parent/depth/kind（缺数据就不写）；Crawl 派生的事件带上
            # 这三项，供 URL 清单显示 L 层与后续拓扑连线。@author aceFelix
            record: dict[str, Any] = {
                "url": event.get("url"),
                "status": event.get("status"),
                "duration_ms": event.get("duration_ms"),
                "bytes": event.get("bytes"),
            }
            for key in ("parent_url", "depth", "kind"):
                if event.get(key) is not None:
                    record[key] = event[key]
            self.fetches.append(record)
        elif etype == "web_extract":
            # missing 用于驾驶舱显示“缺 N”；没有缺失项就不写这个键
            record = {
                "url": event.get("url"),
                "fields": event.get("fields") or {},
            }
            if event.get("missing"):
                record["missing"] = list(event["missing"])
            self.extracts.append(record)
        elif etype == "crawl_stats":
            self.stats = {
                key: event[key]
                for key in (
                    "queued",
                    "done",
                    "failed",
                    "dedup_hits",
                    "bytes",
                    "elapsed_ms",
                    "max_pages",
                    "max_depth",
                )
                if key in event
            }

        summary = _summary_of(event)
        if summary:
            self.feed.append(summary)

    def _on_tool_start(self, event: dict[str, Any]) -> None:
        """工具开始：步数 +1，登记到 active 集合并保留启动顺序。"""
        self.steps += 1
        tool_use_id = str(event.get("tool_use_id") or "")
        entry: dict[str, Any] = {
            "tool_use_id": tool_use_id,
            "tool": str(event.get("tool") or "?"),
            "target": event.get("target"),
            "input": event.get("input") or {},
            "started_at": self._clock(),
            "ok": None,
            "ms": None,
        }
        self.current = entry
        self.step_history.append(entry)
        if tool_use_id:
            self.active[tool_use_id] = entry
            self.active_order.append(tool_use_id)

    def _on_tool_end(self, event: dict[str, Any]) -> None:
        """工具结束：回填成败与耗时，从 active 移除并更新 current。"""
        ok = bool(event.get("ok"))
        duration = event.get("duration_ms")
        tool_use_id = str(event.get("tool_use_id") or "")
        entry: dict[str, Any] | None = None
        if tool_use_id:
            entry = self.active.pop(tool_use_id, None)
            if tool_use_id in self.active_order:
                self.active_order.remove(tool_use_id)
        if entry is None and self.active_order:
            # 缺 tool_use_id 时按“最后启动”兜底，避免历史条目永远停在运行中
            fallback_id = self.active_order.pop()
            entry = self.active.pop(fallback_id, None)
        if entry is not None:
            entry["ok"] = ok
            entry["ms"] = duration
            # 失败摘要供主 HUD 展开详情展示（2026-11-09）@author aceFelix
            entry["error"] = event.get("error")
        self.last_ok = ok
        if not ok:
            self.failed += 1
        self.current = self.active[self.active_order[-1]] if self.active_order else None


def step_rows(history: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    """执行序列展示行：最近 ``limit`` 步，并把最近一次失败步固定在顶部。

    2026-11-09 实机：8 步任务里唯一失败（WebFetch 403）被最近 4 步挤出列表，
    界面出现“失败 1”但执行序列全是 ✓ 的矛盾。失败步必须始终可见，因此若
    最近窗口里没有失败步，就把窗口外最近的一次失败补到第一行，并压缩一条
    最近步以维持总行数不变（窗口高度固定）。

    Args:
        history: ``CockpitState.step_history`` 的快照（旧→新）。
        limit: 最多显示几行。

    Returns:
        展示用条目列表（失败置顶时顺序为“失败 → 最近步”）。
    """
    if not history:
        return []
    recent = history[-limit:]
    if any(entry.get("ok") is False for entry in recent):
        return recent
    failed = [entry for entry in history[:-limit] if entry.get("ok") is False]
    if not failed:
        return recent
    return [failed[-1]] + recent[-(limit - 1):]


def _as_int(value: Any) -> int | None:
    """宽容转 int；非法/缺失返回 None（不写假值）。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    """宽容转 float；非法/缺失返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CockpitState",
    "FEED_MAX",
    "STEP_MAX",
    "FETCH_MAX",
    "VisualOptions",
    "describe_target",
    "cockpit_enabled",
    "normalize_mode",
    "normalize_tool_cards",
    "normalize_effects",
    "normalize_crawl_window",
    "normalize_crawl_topology",
    "visual_enabled",
    "step_rows",
    "_fmt_ms",
    "_fmt_bytes",
    "_short_url",
    "_summary_of",
]
