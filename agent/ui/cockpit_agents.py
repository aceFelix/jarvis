"""子代理/队友任务卡（C3-lite.2）：每个代理一张独立的琥珀色浮窗。

与工具卡（``cockpit_cards.py``）的分工：
- 工具卡回答“主任务这一步在做什么”（青色、按 ``tool_use_id`` 弹）；
- 代理卡回答“哪个子代理/队友在做什么”（琥珀色、按 ``agent_id`` 弹）。

数据源是 ``AgentActivityRegistry`` 的节流快照，不直接消费 trace：
- ``AgentCardSpec`` / ``AgentCardPlanner``：纯逻辑，决定显示/保留/收起/上限，
  不碰 Tk，可单测；
- ``AgentCardWindow`` / ``AgentCardManager``：只在驾驶舱的 Tk 守护线程里
  创建/渲染/销毁 ``Toplevel``；工具栏位置在主 HUD 左侧第二列。

用户 2026-11-08 拍板：每个代理一张卡、保留终端原始输出、空闲卡保留、
代理卡放主 HUD 左侧第二列。方案见 docs/plans/agent-cards.md。

@author aceFelix
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent.core.diag import diag_info
from agent.ui.cockpit import VisualOptions
from agent.ui.cockpit_cards import CARD_W as TOOL_CARD_W
from agent.ui.hud_theme import (
    AMBER,
    AMBER_DIM,
    BG,
    DIM,
    FONT_CARD_TITLE,
    FONT_LABEL,
    FONT_MONO,
    FONT_SMALL,
    GREEN,
    RED,
    TEXT,
    clip,
    fmt_elapsed,
)

#: 代理卡尺寸（比工具卡高 28px：多一行“任务 + 统计”）
AGENT_CARD_W = 300
AGENT_CARD_H = 140

#: 槽位硬上限（与配置钳制保持一致）
_MAX_SLOTS = 6

#: 代理卡内部状态
ACTIVE = "active"
IDLE = "idle"
DONE = "done"
FAILED = "failed"

#: 台账里未收尾的状态
_ACTIVE_STATUSES = ("starting", "running", "thinking")

#: 状态 → 中文展示
_STATUS_TEXT = {
    "starting": "启动中",
    "running": "运行中",
    "thinking": "思考中",
    "idle": "空闲",
    "done": "完成",
    "failed": "失败",
    "stopped": "已停止",
}

#: 参与“是否有变化”判断的展示字段（更新频繁的 updated_at 不算）
_SIGNATURE_FIELDS = (
    "name",
    "role",
    "mode",
    "team",
    "status",
    "task",
    "current_tool",
    "activity",
    "output",
    "error",
)


def _signature(item: dict[str, Any]) -> tuple[str, ...]:
    """把一条台账快照压成展示签名，用于避免无变化时整卡重绘。"""
    return tuple(str(item.get(key) or "") for key in _SIGNATURE_FIELDS)


@dataclass
class AgentCardSpec:
    """一张代理卡的数据（由台账快照映射而来）。

    Attributes:
        agent_id: 台账唯一 id，卡片槽位的稳定键。
        name / role / mode / team: 展示身份。
        status: 台账原始状态（starting/running/thinking/idle/done/failed）。
        task / current_tool / activity / output / error: 展示内容。
        started_at: 墙钟秒（台账口径，用于卡片耗时）。
        iterations / tool_calls: 轮次与工具调用统计。
        state: 卡片内部状态 active / idle / done / failed。
        due_at: 出现阈值到期时刻（单调时钟）。
        promoted: 是否已跨过出现阈值（tick 据此让卡片补位显示）。
        hide_at: 终态收起时刻（单调时钟）；None = 不收起。
        signature: 上次展示签名（变化检测用）。

    @author aceFelix
    """

    agent_id: str
    name: str
    role: str = "general"
    mode: str = "subagent"
    team: str = ""
    status: str = "starting"
    task: str = ""
    current_tool: str = ""
    activity: str = ""
    output: str = ""
    error: str = ""
    started_at: float = 0.0
    iterations: int = 0
    tool_calls: int = 0
    state: str = ACTIVE
    due_at: float = 0.0
    promoted: bool = False
    hide_at: float | None = None
    signature: tuple[str, ...] = ()

    def elapsed_ms(self, now_wall: float) -> float:
        """卡片耗时：从台账登记时刻到当前墙钟（秒）。"""
        if not self.started_at:
            return 0.0
        return max(0.0, (now_wall - self.started_at) * 1000.0)

    def is_idle(self) -> bool:
        """是否为“空闲但保留”的队友卡。"""
        return self.state == IDLE


class AgentCardPlanner:
    """代理卡的纯逻辑规划器（不创建窗口）。

    规则（计划 §5.3）：
    - 运行中（starting/running/thinking）显示；``agent_card_min_ms`` 为出现阈值；
    - 队友空闲（idle）按 ``agent_card_keep_idle`` 保留暗色卡；
    - ``done`` / ``failed`` 保留 ``agent_card_hold_ms`` 后收起；
    - ``stopped`` 立即收起；
    - 同时最多 ``agent_card_max`` 张，超上限时**先收空闲卡**；
    - 台账清空/换轮时同步移除。

    @author aceFelix
    """

    def __init__(
        self,
        options: VisualOptions,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.options = options
        self._clock = clock
        self._specs: dict[str, AgentCardSpec] = {}

    def reset(self) -> bool:
        """清空所有卡；返回是否有变化（窗口由上层统一销毁）。"""
        changed = bool(self._specs)
        self._specs.clear()
        return changed

    def update(self, items: list[dict[str, Any]]) -> bool:
        """消费一份台账快照；返回卡片是否有变化。

        Args:
            items: ``AgentActivityRegistry.snapshot()`` 的普通字典列表。

        Returns:
            是否需要重绘/同步窗口。
        """
        if not self.options.agent_cards_on():
            return self.reset()
        now = self._clock()
        changed = False
        seen: set[str] = set()

        for item in items or []:
            if not isinstance(item, dict):
                continue
            agent_id = str(item.get("agent_id") or "")
            if not agent_id:
                continue
            seen.add(agent_id)
            status = str(item.get("status") or "starting")
            if status == "stopped":
                # 被停掉的代理没有任何保留价值，立即让出槽位
                if agent_id in self._specs:
                    self._specs.pop(agent_id, None)
                    changed = True
                continue
            if status in ("done", "failed"):
                changed |= self._update_terminal(agent_id, status, item, now)
                continue
            if status == "idle" and not self.options.agent_card_keep_idle:
                if agent_id in self._specs:
                    self._specs.pop(agent_id, None)
                    changed = True
                continue
            changed |= self._update_live(agent_id, status, item, now)

        # 快照里消失的代理（台账 clear / 会话重置）同步移除
        for agent_id in list(self._specs):
            if agent_id not in seen:
                self._specs.pop(agent_id, None)
                changed = True
        return changed

    def _update_live(
        self,
        agent_id: str,
        status: str,
        item: dict[str, Any],
        now: float,
    ) -> bool:
        """新增/更新运行中或空闲的代理卡。"""
        state = IDLE if status == "idle" else ACTIVE
        spec = self._specs.get(agent_id)
        changed = False
        if spec is None:
            spec = AgentCardSpec(
                agent_id=agent_id,
                name=str(item.get("name") or agent_id),
                started_at=_as_float(item.get("started_at")) or time.time(),
                state=state,
                due_at=now + self.options.agent_card_min_ms / 1000.0,
                promoted=self.options.agent_card_min_ms <= 0,
            )
            self._specs[agent_id] = spec
            changed = True
        if spec.state != state:
            spec.state = state
            spec.hide_at = None
            changed = True
        return changed | self._apply_fields(spec, item)

    def _update_terminal(
        self,
        agent_id: str,
        status: str,
        item: dict[str, Any],
        now: float,
    ) -> bool:
        """done/failed 卡：回填终态并设置收起时间；从未显示过的不补弹。"""
        spec = self._specs.get(agent_id)
        if spec is None:
            # 窗口启动前就结束的旧记录，不弹“幽灵卡”
            return False
        if not (spec.promoted or now >= spec.due_at):
            # 没跨过出现阈值的快任务：不补弹，避免闪一下就没
            self._specs.pop(agent_id, None)
            return True
        changed = False
        if spec.state != status:
            spec.state = status
            spec.hide_at = now + self.options.agent_card_hold_ms / 1000.0
            changed = True
        return changed | self._apply_fields(spec, item)

    def _apply_fields(self, spec: AgentCardSpec, item: dict[str, Any]) -> bool:
        """把台账字段写进卡片；无变化返回 False（避免无意义的整卡重绘）。"""
        signature = _signature(item)
        if signature == spec.signature:
            return False
        spec.signature = signature
        spec.name = str(item.get("name") or spec.name)
        spec.role = str(item.get("role") or spec.role)
        spec.mode = str(item.get("mode") or spec.mode)
        spec.team = str(item.get("team") or "")
        spec.status = str(item.get("status") or spec.status)
        spec.task = str(item.get("task") or "")
        spec.current_tool = str(item.get("current_tool") or "")
        spec.activity = str(item.get("activity") or "")
        spec.output = str(item.get("output") or "")
        spec.error = str(item.get("error") or "")
        spec.iterations = int(_as_float(item.get("iterations")) or 0)
        spec.tool_calls = int(_as_float(item.get("tool_calls")) or 0)
        return True

    def tick(self) -> bool:
        """推进时间：到点的活动卡转为可显示；终态卡到点收起。

        Returns:
            是否有变化（主 HUD 据此触发一次同步）。
        """
        now = self._clock()
        changed = False
        for agent_id, spec in list(self._specs.items()):
            if spec.hide_at is not None and now >= spec.hide_at:
                self._specs.pop(agent_id, None)
                changed = True
                continue
            if spec.state == ACTIVE and not spec.promoted and now >= spec.due_at:
                spec.promoted = True
                changed = True
        return changed

    def visible(self) -> list[AgentCardSpec]:
        """当前应显示的卡（运行中优先，空闲最后；最多 ``agent_card_max`` 张）。"""
        now = self._clock()
        candidates = [
            spec for spec in self._specs.values() if self._showable(spec, now)
        ]
        # 超上限时优先收空闲卡：运行/终态卡排前，空闲卡排最后
        candidates.sort(key=lambda spec: (1 if spec.is_idle() else 0, spec.started_at or 0.0))
        return candidates[: self.options.agent_card_max]

    def overflow_count(self) -> int:
        """被上限截断、只能汇总到主 HUD 的代理数量。"""
        now = self._clock()
        candidates = [spec for spec in self._specs.values() if self._showable(spec, now)]
        return max(0, len(candidates) - self.options.agent_card_max)

    def summary(self) -> str:
        """主 HUD 页脚用的代理计数：``AGENTS 2 · +1``；没有卡时返回空串。"""
        visible = self.visible()
        if not visible:
            return ""
        extra = self.overflow_count()
        text = f"AGENTS {len(visible)}"
        return f"{text} · +{extra}" if extra > 0 else text

    @staticmethod
    def _showable(spec: AgentCardSpec, now: float) -> bool:
        """单卡是否应显示（含出现阈值与终态收起判定）。"""
        if spec.state == ACTIVE:
            return spec.promoted or now >= spec.due_at
        if spec.state == IDLE:
            return True
        # done / failed：到了收起时间就不再显示（即使 tick 还没跑到）
        return spec.hide_at is None or now < spec.hide_at


def _as_float(value: Any) -> float:
    """宽容转 float；非法/缺失返回 0.0。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class AgentCardWindow:
    """一张代理卡的 Tk 窗口（只在 Tk 线程使用）。

    形态：300×140、无边框、置顶、不抢焦点；琥珀色边框 + ``AGENT`` 标签，
    与青色工具卡明确区分。空闲卡整体降亮度并显示“空闲 · 等待任务”。

    @author aceFelix
    """

    def __init__(self, root: Any, options: VisualOptions) -> None:
        import tkinter as tk

        self._options = options
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.canvas = tk.Canvas(
            self.top,
            width=AGENT_CARD_W,
            height=AGENT_CARD_H,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self._scan_line: int | None = None
        self._elapsed_id: int | None = None
        # 卡片可拖动（2026-11-08 用户实测要求）：整卡无标题栏，按住卡面即可搬走
        self._drag_offset = (0, 0)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)

    def geometry(self, x: int, y: int) -> None:
        """移动卡片到屏幕坐标（无标题栏）。"""
        try:
            if self.top is not None:
                self.top.geometry(f"{AGENT_CARD_W}x{AGENT_CARD_H}+{x}+{y}")
        except Exception:
            pass

    def show(self) -> None:
        """显示卡片（从隐藏状态恢复）。"""
        try:
            if self.top is not None:
                self.top.deiconify()
                self.top.lift()
        except Exception:
            pass

    def hide(self) -> None:
        """隐藏卡片，不销毁。"""
        try:
            if self.top is not None:
                self.top.withdraw()
        except Exception:
            pass

    def destroy(self) -> None:
        """销毁卡片窗口并清空 Tk 引用（必须在 Tk 线程调用）。

        与工具卡同样的退出安全要求：先置空引用再 destroy，避免主线程 GC
        触碰已释放的 Tcl 解释器（``Tcl_AsyncDelete``）。
        """
        top, canvas = self.top, self.canvas
        self.top = None
        self.canvas = None
        self._scan_line = None
        self._elapsed_id = None
        try:
            if top is not None:
                top.destroy()
        except Exception:
            pass
        del canvas

    def render(self, spec: AgentCardSpec, now_wall: float | None = None) -> None:
        """按当前状态重绘整张卡（只在事件/状态变化时调用）。"""
        if self.canvas is None:
            return
        moment = time.time() if now_wall is None else now_wall
        self._scan_line = None
        self._elapsed_id = None
        self.canvas.delete("all")
        self._draw_frame(spec)
        self._draw_header(spec, moment)
        self._draw_identity(spec)
        self._draw_task(spec)
        self._draw_activity(spec)
        self._draw_footer(spec, moment)

    def tick(self, spec: AgentCardSpec, now_wall: float | None = None) -> None:
        """动画帧：只更新耗时文本与扫描线，避免整卡重绘闪烁。"""
        if self.canvas is None:
            return
        moment = time.time() if now_wall is None else now_wall
        if self._elapsed_id is not None:
            try:
                self.canvas.itemconfigure(
                    self._elapsed_id,
                    text=f"AGENT {fmt_elapsed(spec.elapsed_ms(moment))}",
                )
            except Exception:
                pass
        if self._scan_line is not None and self._options.effects != "off":
            try:
                phase = (moment * 0.9) % 1.0
                y = 30 + phase * 96
                self.canvas.coords(self._scan_line, 6, y, AGENT_CARD_W - 6, y)
            except Exception:
                pass

    # ---- 拖动 ----

    def _on_press(self, event: Any) -> None:
        """记录拖动起点；代理卡无标题栏，整卡可拖（用户实测要求）。"""
        try:
            if self.top is not None:
                self._drag_offset = (
                    event.x_root - self.top.winfo_x(),
                    event.y_root - self.top.winfo_y(),
                )
                self.top.lift()
        except Exception:
            pass

    def _on_drag(self, event: Any) -> None:
        """按拖动位移移动卡片；尺寸保持不变。"""
        try:
            if self.top is not None:
                x = event.x_root - self._drag_offset[0]
                y = event.y_root - self._drag_offset[1]
                self.top.geometry(f"{AGENT_CARD_W}x{AGENT_CARD_H}+{x}+{y}")
        except Exception:
            pass

    # ---- 绘制 ----

    def _accent(self, spec: AgentCardSpec) -> str:
        """卡片主色：运行琥珀、空闲暗色、完成绿、失败红。"""
        if spec.state == IDLE:
            return DIM
        if spec.state == DONE:
            return GREEN
        if spec.state == FAILED:
            return RED
        return AMBER

    def _draw_frame(self, spec: AgentCardSpec) -> None:
        """外框与四角角标（颜色跟随状态）。"""
        accent = self._accent(spec)
        outline = AMBER_DIM if spec.state == ACTIVE else DIM
        self.canvas.create_rectangle(4, 4, AGENT_CARD_W - 4, AGENT_CARD_H - 4, outline=outline)
        self.canvas.create_line(4, 4, 22, 4, fill=accent, width=2)
        self.canvas.create_line(4, 4, 4, 22, fill=accent, width=2)
        self.canvas.create_line(
            AGENT_CARD_W - 4, AGENT_CARD_H - 4, AGENT_CARD_W - 22, AGENT_CARD_H - 4,
            fill=accent, width=2,
        )
        self.canvas.create_line(
            AGENT_CARD_W - 4, AGENT_CARD_H - 4, AGENT_CARD_W - 4, AGENT_CARD_H - 22,
            fill=accent, width=2,
        )

    def _draw_header(self, spec: AgentCardSpec, now: float) -> None:
        """状态标记 + 代理名 + AGENT 标签与耗时。"""
        if spec.state == DONE:
            marker = "✓"
        elif spec.state == FAILED:
            marker = "✗"
        elif spec.state == IDLE:
            marker = "◇"
        else:
            marker = "◆"
        name_color = DIM if spec.state == IDLE else TEXT
        self.canvas.create_text(
            14, 18, text=marker, fill=self._accent(spec),
            font=FONT_CARD_TITLE, anchor="w",
        )
        self.canvas.create_text(
            32, 18, text=clip(spec.name, 24), fill=name_color,
            font=FONT_CARD_TITLE, anchor="w",
        )
        self._elapsed_id = self.canvas.create_text(
            AGENT_CARD_W - 14, 18,
            text=f"AGENT {fmt_elapsed(spec.elapsed_ms(now))}",
            fill=AMBER_DIM if spec.state != IDLE else DIM,
            font=FONT_MONO, anchor="e",
        )
        self.canvas.create_line(10, 32, AGENT_CARD_W - 10, 32, fill=AMBER_DIM if spec.state == ACTIVE else DIM)

    def _draw_identity(self, spec: AgentCardSpec) -> None:
        """身份行：``explorer · 子代理`` 或 ``coder · 队友 · team``。"""
        if spec.mode == "teammate":
            identity = f"{spec.role} · 队友" + (f" · {spec.team}" if spec.team else "")
        else:
            identity = f"{spec.role} · 子代理"
        self.canvas.create_text(
            14, 48, text=clip(identity, 44), fill=DIM, font=FONT_SMALL, anchor="w",
        )
        status = _STATUS_TEXT.get(spec.status, spec.status)
        self.canvas.create_text(
            AGENT_CARD_W - 14, 48, text=status, fill=self._accent(spec),
            font=FONT_SMALL, anchor="e",
        )

    def _draw_task(self, spec: AgentCardSpec) -> None:
        """任务行：主代理交办内容（最长 48 显示单元）。"""
        body = spec.task or spec.output or "—"
        self.canvas.create_text(
            14, 70, text=clip(body, 48),
            fill=DIM if spec.state == IDLE else TEXT, font=FONT_SMALL, anchor="w",
        )

    def _draw_activity(self, spec: AgentCardSpec) -> None:
        """当前动作行：错误 > 当前工具 > 动作描述。"""
        if spec.state == FAILED and spec.error:
            line, color = spec.error, RED
        elif spec.state == IDLE:
            line, color = "空闲 · 等待任务", DIM
        elif spec.current_tool:
            line, color = f"调用 {spec.current_tool}", AMBER
        elif spec.activity:
            line, color = spec.activity, AMBER
        elif spec.output:
            line, color = spec.output, DIM
        else:
            line, color = "准备中", DIM
        self.canvas.create_text(
            14, 92, text=clip(line, 48), fill=color,
            font=FONT_MONO if spec.state == ACTIVE else FONT_SMALL, anchor="w",
        )

    def _draw_footer(self, spec: AgentCardSpec, now: float) -> None:
        """底部统计行；运行中叠加扫描线。"""
        status = _STATUS_TEXT.get(spec.status, spec.status)
        line = f"{spec.iterations} 轮 · {spec.tool_calls} 工具 · {status}"
        self.canvas.create_text(
            14, AGENT_CARD_H - 14, text=clip(line, 44),
            fill=self._accent(spec), font=FONT_LABEL, anchor="w",
        )
        if spec.state == ACTIVE and self._options.effects != "off":
            phase = (now * 0.9) % 1.0
            y = 30 + phase * 96
            self._scan_line = self.canvas.create_line(
                6, y, AGENT_CARD_W - 6, y, fill=AMBER_DIM,
            )


class AgentCardManager:
    """管理所有代理卡的 Tk 窗口（只在 Tk 线程使用）。

    布局（计划 §5.2）：主 HUD 左侧第二列，工具卡占第一列；
    ``x = hud_x - 2 * CARD_W - 32 - slot * 24``，``y = hud_y + slot * 18``。
    主 HUD 隐藏时由上层统一隐藏，不单独显示。

    @author aceFelix
    """

    def __init__(
        self,
        root: Any,
        options: VisualOptions,
        anchor_provider: Callable[[], tuple[int, int, bool]],
    ) -> None:
        self.root = root
        self.options = options
        self._anchor_provider = anchor_provider
        self._windows: dict[str, AgentCardWindow] = {}
        self._slots: dict[str, int] = {}
        self._disabled = False

    def sync(self, planner: AgentCardPlanner) -> None:
        """把规划器的可见卡同步到实际窗口（创建/更新/销毁）。"""
        if self._disabled or not self.options.agent_cards_on():
            return
        visible = planner.visible()
        wanted = {spec.agent_id for spec in visible}

        for agent_id in list(self._windows):
            if agent_id not in wanted:
                self._destroy(agent_id)

        now_wall = time.time()
        for spec in visible:
            window = self._windows.get(spec.agent_id)
            if window is None:
                slot = self._free_slot()
                if slot is None:
                    continue
                try:
                    window = AgentCardWindow(self.root, self.options)
                except Exception as exc:  # noqa: BLE001 - 卡片故障不影响主 HUD
                    self._disabled = True
                    diag_info("ui", f"代理卡创建失败，已禁用: {exc}")
                    self.destroy_all()
                    return
                self._windows[spec.agent_id] = window
                self._slots[spec.agent_id] = slot
                x, y = self._position(slot)
                window.geometry(x, y)
                window.show()
            window.render(spec, now_wall)

    def tick(self, planner: AgentCardPlanner) -> None:
        """动画帧：只刷新耗时与扫描线（不重建/不重绘整卡）。"""
        if self._disabled:
            return
        now_wall = time.time()
        for spec in planner.visible():
            window = self._windows.get(spec.agent_id)
            if window is not None:
                window.tick(spec, now_wall)

    def hide_all(self) -> None:
        """主 HUD 关闭时隐藏所有代理卡（保留窗口对象）。"""
        for window in self._windows.values():
            window.hide()

    def show_all(self, planner: AgentCardPlanner) -> None:
        """主 HUD 重现时同步并显示现有卡（含上一轮遗留的空闲卡）。"""
        self.sync(planner)
        for window in self._windows.values():
            window.show()

    def destroy_all(self) -> None:
        """销毁所有卡（退出或禁用时）。"""
        for agent_id in list(self._windows):
            self._destroy(agent_id)
        self._windows.clear()
        self._slots.clear()

    def detach(self) -> None:
        """销毁所有卡并断开 Tk 根引用（退出时防跨线程 GC 崩溃）。"""
        self.destroy_all()
        self.root = None

    def _destroy(self, agent_id: str) -> None:
        """销毁单张卡并释放槽位。"""
        window = self._windows.pop(agent_id, None)
        self._slots.pop(agent_id, None)
        if window is not None:
            window.destroy()

    def _free_slot(self) -> int | None:
        """分配最低的空闲槽位；满了返回 None。"""
        used = set(self._slots.values())
        for slot in range(min(self.options.agent_card_max, _MAX_SLOTS)):
            if slot not in used:
                return slot
        return None

    def _position(self, slot: int) -> tuple[int, int]:
        """按槽位计算屏幕坐标（主 HUD 左侧第二列，纵向完全散开）。

        用户 2026-11-08 实测反馈“卡片叠在一起看不清”，因此代理卡不再像
        工具卡那样斜向堆叠，而是保持一个固定间距纵向排列；拖动过的卡片
        由 ``sync`` 保留原位，不会被这里重置。

        主 HUD 不可见（被关闭或只开了卡片）时退回屏幕右上角；
        若工具卡列也开着，则整体左移一列，避免两套卡重叠。
        """
        hud_x, hud_y, hud_visible = self._anchor_provider()
        gap = 10
        if not hud_visible:
            screen_w = self._screen_width()
            if self.options.cards_on():
                x = screen_w - 2 * TOOL_CARD_W - 40
            else:
                x = screen_w - AGENT_CARD_W - 24
            y = 48 + slot * (AGENT_CARD_H + gap)
            return max(8, x), max(8, y)
        x = hud_x - 2 * TOOL_CARD_W - 32
        y = hud_y + slot * (AGENT_CARD_H + gap)
        if x < 8:
            x = 8
        # 屏幕下方放不下时向上夹住；最多 3 张（默认）时不会触发
        y = min(y, self._screen_height() - AGENT_CARD_H - 8)
        return max(8, x), max(8, y)

    def _screen_width(self) -> int:
        """读取屏幕宽度；根窗口已销毁时给一个保守值。"""
        try:
            if self.root is not None:
                return int(self.root.winfo_screenwidth())
        except Exception:
            pass
        return 1920

    def _screen_height(self) -> int:
        """读取屏幕高度；根窗口已销毁时给一个保守值。"""
        try:
            if self.root is not None:
                return int(self.root.winfo_screenheight())
        except Exception:
            pass
        return 1080


__all__ = [
    "ACTIVE",
    "AGENT_CARD_H",
    "AGENT_CARD_W",
    "AgentCardManager",
    "AgentCardPlanner",
    "AgentCardSpec",
    "AgentCardWindow",
    "DONE",
    "FAILED",
    "IDLE",
]
