"""多任务浮窗卡（C3-lite）：每个运行中工具一张独立 tkinter Toplevel。

职责拆分：
- ``CardSpec`` / ``CardPlanner``：纯逻辑，决定“什么时候显示、什么时候收起、
  最多几张”；不碰 Tk，可单测；
- ``CardWindow`` / ``CardManager``：只在驾驶舱的 Tk 守护线程里创建/渲染/销毁
  ``Toplevel``，不抢键盘焦点。

2026-11-08 方案见 docs/plans/cockpit-multicards.md。@author aceFelix
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent.core.diag import diag_info
from agent.ui.cockpit import VisualOptions
from agent.ui.hud_theme import (
    BG,
    CYAN,
    CYAN_DIM,
    DIM,
    FONT_CARD_TITLE,
    FONT_MONO,
    FONT_SMALL,
    GREEN,
    RED,
    TEXT,
    clip,
    fmt_elapsed,
)

CARD_W = 300
CARD_H = 112

#: 点击卡片展开后的详情尺寸（用户 2026-11-08 实测要求可展开工具调用细节）
CARD_EXPANDED_W = 440
CARD_EXPANDED_H = 300

# 卡片默认最多 4 张；配置会被 VisualOptions 钳制
_MAX_SLOTS = 6

#: 浮窗卡状态
PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


def summarize_input(tool: str, tool_input: Any) -> str:
    """从工具入参里提取一行可读摘要（用于卡片第二/三行）。

    Args:
        tool: 工具名（保留给未来按工具定制）。
        tool_input: trace 里的 slim input 字典。

    Returns:
        截断后的一行文本；没有可用字段时返回空串。
    """
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "path", "url", "query", "pattern", "content"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return clip(value.replace("\n", " "), 46)
    for value in tool_input.values():
        if isinstance(value, str) and value.strip():
            return clip(value.replace("\n", " "), 46)
    return ""


@dataclass
class CardSpec:
    """一张浮窗卡的数据。

    Attributes:
        tool_use_id: trace 里的工具调用唯一 id。
        tool / target / input_summary: 展示字段。
        input_detail: trace 工具入参字典（点击展开详情用，已脱敏截断）。
        started_at: 启动时刻（单调时钟）。
        state: pending / running / done / failed。
        ok / ms / error: 完成结果。
        due_at: pending 到期时间（auto 阈值）。
        hide_at: 完成态收起时间。

    @author aceFelix
    """

    tool_use_id: str
    tool: str
    target: Any = None
    input_summary: str = ""
    input_detail: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    state: str = PENDING
    ok: bool | None = None
    ms: int | None = None
    error: str = ""
    due_at: float = 0.0
    hide_at: float | None = None

    def elapsed_ms(self, now: float) -> float:
        """运行中用时；完成后用工具上报的 duration_ms。"""
        if self.ms is not None:
            return float(self.ms)
        return max(0.0, (now - self.started_at) * 1000)


class CardPlanner:
    """浮窗卡的纯逻辑规划器（不创建窗口）。

    规则：
    - ``auto``：tool_start 后延迟 ``card_min_ms``，到点仍在运行才显示；
    - ``always``：tool_start 立即显示；
    - ``off``：不产生任何卡；
    - 完成态保留 ``card_hold_ms`` 后收起；
    - 同时最多 ``card_max`` 张（running + 完成保留都占槽）；
    - 未显示的短工具直接丢弃。

    @author aceFelix
    """

    def __init__(
        self,
        options: VisualOptions,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.options = options
        self._clock = clock
        self._cards: dict[str, CardSpec] = {}
        self._order: list[str] = []

    def reset(self) -> None:
        """换轮清空所有卡（窗口由上层统一销毁）。"""
        self._cards.clear()
        self._order.clear()

    def update(self, event: dict[str, Any]) -> bool:
        """消费一条 trace 事件；返回是否有变化。"""
        etype = event.get("type")
        now = self._clock()
        if event.get("agent_id"):
            # 子代理工具由 AgentCardPlanner 统一展示，不再弹工具卡
            return False
        if etype == "run_start":
            self.reset()
            return True
        if not self.options.cards_on():
            return False

        if etype == "tool_start":
            return self._on_start(event, now)
        if etype == "tool_end":
            return self._on_end(event, now)
        return False

    def _on_start(self, event: dict[str, Any], now: float) -> bool:
        """工具开始：登记 pending 卡，always 模式立即转 running。"""
        tool_use_id = str(event.get("tool_use_id") or "")
        if not tool_use_id or tool_use_id in self._cards:
            return False
        spec = CardSpec(
            tool_use_id=tool_use_id,
            tool=str(event.get("tool") or "?"),
            target=event.get("target"),
            input_summary=summarize_input(str(event.get("tool") or ""), event.get("input")),
            input_detail=dict(event.get("input") or {}) if isinstance(event.get("input"), dict) else {},
            started_at=now,
            due_at=now + (self.options.card_min_ms / 1000.0),
        )
        if self.options.tool_cards == "always":
            spec.due_at = now
        self._cards[tool_use_id] = spec
        self._order.append(tool_use_id)
        if spec.due_at <= now:
            self._promote(spec)
        return True

    def _on_end(self, event: dict[str, Any], now: float) -> bool:
        """工具结束：回填结果；未显示过的短工具直接移除。"""
        tool_use_id = str(event.get("tool_use_id") or "")
        spec = self._cards.get(tool_use_id)
        if spec is None:
            return False
        if spec.state == PENDING:
            self._remove(tool_use_id)
            return True
        spec.ok = bool(event.get("ok"))
        spec.ms = _as_int(event.get("duration_ms"))
        spec.error = str(event.get("error") or "")[:80]
        spec.state = DONE if spec.ok else FAILED
        spec.hide_at = now + (self.options.card_hold_ms / 1000.0)
        return True

    def tick(self, now: float | None = None) -> bool:
        """推进 pending → running、完成态 → 收起；返回是否有变化。"""
        moment = self._clock() if now is None else now
        changed = False

        # 完成态到期：先收，腾出槽位给仍在 pending 的长工具
        for tool_use_id in list(self._order):
            spec = self._cards.get(tool_use_id)
            if spec is None or spec.hide_at is None:
                continue
            if spec.state in (DONE, FAILED) and moment >= spec.hide_at:
                self._remove(tool_use_id)
                changed = True

        if self.options.cards_on():
            for tool_use_id in list(self._order):
                spec = self._cards.get(tool_use_id)
                if spec is None or spec.state != PENDING:
                    continue
                if moment >= spec.due_at and self._promote(spec):
                    changed = True
        return changed

    def visible(self) -> list[CardSpec]:
        """当前应显示的卡（按启动顺序）。"""
        return [
            self._cards[tid]
            for tid in self._order
            if tid in self._cards and self._cards[tid].state != PENDING
        ]

    def pending_count(self) -> int:
        """尚未显示、等待阈值的卡数量。"""
        return sum(1 for spec in self._cards.values() if spec.state == PENDING)

    def _visible_count(self) -> int:
        """已占槽位的卡数量（running + 完成保留）。"""
        return sum(1 for spec in self._cards.values() if spec.state != PENDING)

    def _promote(self, spec: CardSpec) -> bool:
        """pending → running；槽位满时保持 pending，等 tick 重试。"""
        if spec.state != PENDING:
            return False
        if self._visible_count() >= self.options.card_max:
            return False
        spec.state = RUNNING
        return True

    def _remove(self, tool_use_id: str) -> None:
        """移除一张卡并保持启动顺序。"""
        self._cards.pop(tool_use_id, None)
        if tool_use_id in self._order:
            self._order.remove(tool_use_id)


def _as_int(value: Any) -> int | None:
    """宽容转 int；非法/缺失返回 None。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CardWindow:
    """一张浮窗卡的 Tk 窗口（只在 Tk 线程使用）。"""

    def __init__(self, root: Any, options: VisualOptions) -> None:
        import tkinter as tk

        self._options = options
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.canvas = tk.Canvas(
            self.top,
            width=CARD_W,
            height=CARD_H,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self._scan_line: int | None = None
        self._elapsed_id: int | None = None
        # 点击卡面展开/收起工具调用详情（用户 2026-11-08 实测要求）
        self._expanded = False
        self._last_spec: CardSpec | None = None
        self.canvas.bind("<Button-1>", self._on_click)

    def _w(self) -> int:
        """当前卡片宽度（展开态更宽）。"""
        return CARD_EXPANDED_W if self._expanded else CARD_W

    def _h(self) -> int:
        """当前卡片高度（展开态更高）。"""
        return CARD_EXPANDED_H if self._expanded else CARD_H

    def geometry(self, x: int, y: int) -> None:
        """移动卡片到屏幕坐标（无标题栏）。"""
        try:
            if self.top is not None:
                self.top.geometry(f"{self._w()}x{self._h()}+{x}+{y}")
        except Exception:
            pass

    def _on_click(self, _event: Any) -> None:
        """点击卡面切换展开态；展开后显示完整入参 JSON。"""
        self._expanded = not self._expanded
        try:
            if self.canvas is not None:
                self.canvas.configure(width=self._w(), height=self._h())
            if self.top is not None:
                self.top.geometry(f"{self._w()}x{self._h()}+{self.top.winfo_x()}+{self.top.winfo_y()}")
            if self._last_spec is not None:
                self.render(self._last_spec)
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
        """销毁卡片窗口，并清掉所有 Tk 引用。

        必须在 Tk 线程调用；先把 ``top`` / ``canvas`` 置空再 destroy，
        避免对象之后在主线程 GC 时触碰已释放的 Tcl 解释器。
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

    def render(self, spec: CardSpec, now: float | None = None) -> None:
        """按当前状态重绘整张卡（只在事件/状态变化时调用）。"""
        if self.canvas is None:
            return
        self._last_spec = spec
        moment = time.monotonic() if now is None else now
        self._scan_line = None
        self._elapsed_id = None
        self.canvas.delete("all")
        self._draw_frame()
        self._draw_header(spec, moment)
        self._draw_body(spec)
        self._draw_footer(spec, moment)

    def tick(self, spec: CardSpec, now: float | None = None) -> None:
        """动画帧：只更新耗时文本与扫描线，避免整卡重绘闪烁。"""
        if self.canvas is None:
            return
        moment = time.monotonic() if now is None else now
        if self._elapsed_id is not None:
            try:
                self.canvas.itemconfigure(
                    self._elapsed_id, text=fmt_elapsed(spec.elapsed_ms(moment))
                )
            except Exception:
                pass
        if self._scan_line is not None and self._options.effects != "off":
            try:
                phase = (moment * 0.9) % 1.0
                y = 76 + phase * 14
                self.canvas.coords(self._scan_line, 6, y, self._w() - 6, y)
            except Exception:
                pass

    def _draw_frame(self) -> None:
        """外框与四角角标。"""
        width, height = self._w(), self._h()
        self.canvas.create_rectangle(4, 4, width - 4, height - 4, outline=CYAN_DIM)
        self.canvas.create_line(4, 4, 20, 4, fill=CYAN, width=2)
        self.canvas.create_line(4, 4, 4, 20, fill=CYAN, width=2)
        self.canvas.create_line(width - 4, height - 4, width - 20, height - 4, fill=CYAN, width=2)
        self.canvas.create_line(width - 4, height - 4, width - 4, height - 20, fill=CYAN, width=2)

    def _draw_header(self, spec: CardSpec, now: float) -> None:
        """状态点 + 工具名 + 耗时。"""
        if spec.state == DONE:
            marker, color = "✓", GREEN
        elif spec.state == FAILED:
            marker, color = "✗", RED
        else:
            marker, color = "●", CYAN
        self.canvas.create_text(
            14, 18, text=marker, fill=color,
            font=FONT_CARD_TITLE, anchor="w",
        )
        self.canvas.create_text(
            32, 18, text=clip(spec.tool, 24), fill=TEXT,
            font=FONT_CARD_TITLE, anchor="w",
        )
        self._elapsed_id = self.canvas.create_text(
            self._w() - 14, 18,
            text=fmt_elapsed(spec.elapsed_ms(now)),
            fill=DIM, font=FONT_MONO, anchor="e",
        )

    def _draw_body(self, spec: CardSpec) -> None:
        """目标 / 入参 / 错误三行；展开态改为完整入参详情。"""
        if self._expanded:
            self._draw_detail(spec)
            return
        target = _target_text(spec.target)
        if target:
            self.canvas.create_text(
                14, 48, text=clip(target, 42), fill=TEXT, font=FONT_SMALL, anchor="w",
            )
        if spec.input_summary:
            self.canvas.create_text(
                14, 68, text=clip(spec.input_summary, 44), fill=DIM,
                font=FONT_MONO, anchor="w",
            )
        if spec.state == FAILED and spec.error:
            self.canvas.create_text(
                14, 88, text=clip(spec.error, 44), fill=RED, font=FONT_SMALL, anchor="w",
            )

    def _draw_detail(self, spec: CardSpec) -> None:
        """展开态：显示目标与完整（已脱敏截断的）工具入参 JSON。"""
        target = _target_text(spec.target)
        if target:
            self.canvas.create_text(
                14, 42, text=clip(target, 66), fill=TEXT, font=FONT_SMALL, anchor="w",
            )
        self.canvas.create_text(
            14, 64, text="入参 // INPUT", fill=CYAN_DIM, font=FONT_SMALL, anchor="w",
        )
        self.canvas.create_text(
            14, 84, text=_detail_text(spec), fill=TEXT, font=FONT_MONO,
            anchor="nw", width=self._w() - 28, justify="left",
        )
        if spec.state == FAILED and spec.error:
            self.canvas.create_text(
                14, self._h() - 34, text=clip(f"错误：{spec.error}", 68),
                fill=RED, font=FONT_SMALL, anchor="w",
            )

    def _draw_footer(self, spec: CardSpec, now: float) -> None:
        """底部状态行 + 展开提示；运行中叠加扫描线。"""
        text = {
            RUNNING: "运行中",
            DONE: "完成",
            FAILED: "失败",
        }.get(spec.state, "启动中")
        color = CYAN if spec.state == RUNNING else (GREEN if spec.state == DONE else RED)
        self.canvas.create_text(
            14, self._h() - 14, text=text, fill=color, font=FONT_SMALL, anchor="w",
        )
        hint = "点击收起" if self._expanded else "点击展开"
        self.canvas.create_text(
            self._w() - 14, self._h() - 14, text=hint, fill=CYAN_DIM,
            font=FONT_SMALL, anchor="e",
        )
        if spec.state == RUNNING and self._options.effects != "off":
            phase = (now * 0.9) % 1.0
            y = 76 + phase * 14
            self._scan_line = self.canvas.create_line(
                6, y, self._w() - 6, y, fill=CYAN_DIM,
            )


def _target_text(target: Any) -> str:
    """把 trace target 转成一行可读文本。"""
    if not isinstance(target, dict):
        return ""
    kind = target.get("kind")
    if kind == "file":
        return str(target.get("path") or "")
    if kind == "window":
        return str(target.get("title") or "")
    return ""


def _detail_text(spec: CardSpec) -> str:
    """展开态显示的工具入参 JSON（限制行数，防止撑出卡片）。"""
    if not spec.input_detail:
        return "(无入参)"
    try:
        body = json.dumps(spec.input_detail, ensure_ascii=False, indent=2)
    except Exception:
        body = str(spec.input_detail)
    lines = body.splitlines()
    if len(lines) > 10:
        body = "\n".join(lines[:10]) + "\n…"
    return body


class CardManager:
    """管理所有浮窗卡的 Tk 窗口（只在 Tk 线程使用）。

    Attributes:
        _windows: tool_use_id → CardWindow。
        _slots: tool_use_id → 槽位号（0..card_max-1）。

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
        self._windows: dict[str, CardWindow] = {}
        self._slots: dict[str, int] = {}
        self._disabled = False

    def sync(self, planner: CardPlanner) -> None:
        """把规划器的可见卡同步到实际窗口（创建/更新/销毁）。"""
        if self._disabled or not self.options.cards_on():
            return
        visible = planner.visible()
        wanted = {spec.tool_use_id for spec in visible}

        for tool_use_id in list(self._windows):
            if tool_use_id not in wanted:
                self._destroy(tool_use_id)

        for spec in visible:
            window = self._windows.get(spec.tool_use_id)
            if window is None:
                slot = self._free_slot()
                if slot is None:
                    continue
                try:
                    window = CardWindow(self.root, self.options)
                except Exception as exc:  # noqa: BLE001 - 卡片故障不影响主 HUD
                    self._disabled = True
                    diag_info("ui", f"浮窗卡创建失败，已禁用: {exc}")
                    self.destroy_all()
                    return
                self._windows[spec.tool_use_id] = window
                self._slots[spec.tool_use_id] = slot
                x, y = self._position(slot)
                window.geometry(x, y)
                window.show()
            window.render(spec, time.monotonic())

    def tick(self, planner: CardPlanner) -> None:
        """动画帧：只刷新耗时文本与扫描线（不重建/不重绘整卡）。"""
        if self._disabled:
            return
        now = time.monotonic()
        for spec in planner.visible():
            window = self._windows.get(spec.tool_use_id)
            if window is not None:
                window.tick(spec, now)

    def hide_all(self) -> None:
        """主 HUD 关闭时隐藏所有卡片（保留窗口对象）。"""
        for window in self._windows.values():
            window.hide()

    def show_all(self, planner: CardPlanner) -> None:
        """主 HUD 重现时同步并显示现有卡片。"""
        self.sync(planner)
        for window in self._windows.values():
            window.show()

    def destroy_all(self) -> None:
        """销毁所有卡片（退出或禁用时）。"""
        for tool_use_id in list(self._windows):
            self._destroy(tool_use_id)
        self._windows.clear()
        self._slots.clear()

    def detach(self) -> None:
        """销毁所有卡片并断开对 Tk 根窗口的引用。

        退出时必须调用：CardManager 被主线程对象（CockpitWindow）持有，
        若还保留 ``root``，解释器退出时会在主线程回收 Tk 对象，触发
        ``Tcl_AsyncDelete: async handler deleted by the wrong thread``。
        """
        self.destroy_all()
        self.root = None

    def _destroy(self, tool_use_id: str) -> None:
        """销毁单张卡并释放槽位。"""
        window = self._windows.pop(tool_use_id, None)
        self._slots.pop(tool_use_id, None)
        if window is not None:
            window.destroy()

    def _free_slot(self) -> int | None:
        """分配最低的空闲槽位；满了返回 None。"""
        used = set(self._slots.values())
        for slot in range(min(self.options.card_max, _MAX_SLOTS)):
            if slot not in used:
                return slot
        return None

    def _position(self, slot: int) -> tuple[int, int]:
        """按槽位计算屏幕坐标；主 HUD 隐藏时锚定屏幕右上角。"""
        hud_x, hud_y, hud_visible = self._anchor_provider()
        if not hud_visible:
            screen_w = self.root.winfo_screenwidth()
            return max(8, screen_w - CARD_W - 24), 48
        x = hud_x - CARD_W - 16 - slot * 24
        y = hud_y + slot * 16
        if x < 8:
            x = 8
            y = hud_y + slot * (CARD_H + 12)
        return max(8, x), max(8, y)


__all__ = [
    "CardSpec",
    "CardPlanner",
    "CardWindow",
    "CardManager",
    "summarize_input",
    "CARD_W",
    "CARD_H",
]
