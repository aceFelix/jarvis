"""独立任务驾驶舱窗口（C1b）：tkinter 无边框 HUD。

2026-11-08 决策：放弃“终端底部内嵌”，改为**独立窗口**；结束后不关闭、保留最终汇总；
点 ✕ 只隐藏，下一轮首个工具自动重现。形态目标：钢铁侠式深色 HUD（青色线条、角标、扫描线）。

线程模型：主线程只往 ``queue.Queue`` 投事件，后台守护线程独占 ``tk.Tk`` 与
``mainloop``，每 40ms 取一批事件并重绘；创建失败静默禁用并记诊断，绝不阻断对话。

@author aceFelix
"""

from __future__ import annotations

import atexit
import queue
import threading
import time
from typing import Any

from agent.core.diag import diag_info
from agent.ui.cockpit import (
    CockpitState,
    VisualOptions,
    _fmt_bytes,
    _fmt_ms,
    _short_url,
    describe_target,
    step_rows,
)
from agent.ui.cockpit_agents import AgentCardManager, AgentCardPlanner
from agent.ui.cockpit_cards import CardManager, CardPlanner
from agent.ui.cockpit_crawl import CrawlManager
from agent.ui.cockpit_detail import draw_tool_detail
from agent.ui.cockpit_position import load_position as _load_hud_position
from agent.ui.cockpit_position import save_position as _save_hud_position
from agent.ui.cockpit_zorder import TerminalAwareTopmost
from agent.ui.hud_theme import (
    AMBER,
    BG,
    BG_PANEL,
    CYAN,
    CYAN_DIM,
    DIM,
    FONT_LABEL,
    FONT_MONO,
    FONT_NUM,
    FONT_SMALL,
    FONT_TEXT,
    FONT_TITLE,
    GREEN,
    GRID,
    RED,
    TEXT,
    clip,
    display_width,
    fmt_elapsed,
)

# ---- 主窗口尺寸（卡片尺寸见 cockpit_cards.py）----
WINDOW_W = 430
WINDOW_H = 640
PAD = 18

# 测试与旧调用点沿用下划线别名；实现统一在 hud_theme.py
_display_width = display_width
_clip = clip
_fmt_elapsed = fmt_elapsed


class CockpitWindow:
    """驾驶舱窗口控制器（对外只暴露 ``feed`` / ``shutdown``）。

    关键状态：``_events`` 事件队列、``_closed`` 会话已关闭、``_disabled`` Tk 不可用。

    @author aceFelix
    """

    def __init__(
        self,
        options: VisualOptions | None = None,
        *,
        topmost: bool = True,
    ) -> None:
        self.options = options or VisualOptions()
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._disabled = False
        self._lock = threading.Lock()
        self._topmost = topmost
        self._root: Any = None
        self._hud: _Hud | None = None
        self._cards: CardManager | None = None
        # 子代理/队友卡（C3-lite.2）：数据源是活动台账 observer；observer 只在 Tk 线程
        # 真正就绪后注册，保证 run_subagent 选择 TeeActivityUI 时 has_card_observer=True。
        self._agent_planner = AgentCardPlanner(self.options)
        self._agent_cards: AgentCardManager | None = None
        # 左侧抓取网络窗（C2-lite）：有抓取才弹；终端优先置顶策略在 Tk 线程内创建
        self._crawl: CrawlManager | None = None
        self._zorder: TerminalAwareTopmost | None = None
        self._observer_bound = False
        self._state = CockpitState()
        self._planner = CardPlanner(self.options)
        self._last_event_at = 0.0
        self._atexit_registered = False
        # 用户点 ✕ 只隐藏窗口，不销毁 Tk、更不退出进程；下一轮首个工具自动重现。
        # @author aceFelix
        self._run_id = ""
        self._hidden = False
        self._hidden_run_id: str | None = None
        self._destroyed = False
        # 闲聊轮次不弹窗：run_start 先缓存，等本轮首个工具事件再补投（2026-11-09）
        self._pending_run_start: dict[str, Any] | None = None
        self._run_has_activity = False

    # ---- 对外接口 ----

    def feed(self, event: dict[str, Any]) -> None:
        """投递一条 trace 事件（可从任意线程调用，绝不抛异常）。

        **只有真正调用了工具的任务才弹窗**（2026-11-09 用户实测：闲聊轮次也会
        弹出一张“0 步 / 0 工具”的驾驶舱）：``run_start`` 只缓存；本轮首个
        工具/抓取事件才建窗并补投 ``run_start``（耗时仍从任务开始算）；没有
        工具调用的 ``run_end`` 直接丢弃，已显示的窗口保持上一轮汇总。

        窗口被用户关闭（隐藏）后仍在后台消费事件，下一轮首个 ``tool_start``
        自动重新显示。

        Args:
            event: ``TraceRecorder`` 的 JSONL 记录。

        @author aceFelix
        """
        try:
            if self._disabled or self._closed.is_set() or not self.options.any_enabled():
                return
            event_type = str(event.get("type") or "")
            run_id = str(event.get("run_id") or "")

            if event_type == "run_start":
                # 闲聊轮次同样会发 run_start：这里只缓存，等首个工具再决定弹窗
                self._run_id = run_id
                self._run_has_activity = False
                self._pending_run_start = event
                return

            if event_type == "run_end":
                if not self._run_has_activity:
                    # 整轮没有工具调用（纯闲聊）：窗口不动，也不刷新成 0 步
                    self._pending_run_start = None
                    return
                self._run_has_activity = False
                self._pending_run_start = None
            else:
                # 工具、抓取、文件变更等都属于“任务活动”
                self._run_has_activity = True

            if self._hidden and event_type == "tool_start" and run_id != self._hidden_run_id:
                # 关闭发生在上一轮：新一轮的工具开始 → 重新拉起窗口
                self._hidden = False
                if self._thread is not None and not self._closed.is_set():
                    self._events.put({"type": "__show__"})
            if self._thread is None:
                self._ensure_started()
            if self._thread is None or self._closed.is_set():
                return
            pending = self._pending_run_start
            if pending is not None:
                # 窗口刚被拉起：先补投 run_start，状态模型才知道本轮开始时刻
                self._pending_run_start = None
                self._events.put(pending)
            self._events.put(event)
        except Exception:
            pass

    def feed_agents(self, items: list[dict[str, Any]]) -> None:
        """活动台账 observer 回调（任意线程）：只投递快照，绝不碰 Tk。

        子代理/队友的台账通知可能来自模型线程或工具线程，这里只把快照塞进
        事件队列，由 Tk 线程统一映射成代理卡。窗口尚未启动时也照样入队，
        等首次任务事件拉起窗口后按最新快照一次性同步。

        Args:
            items: ``AgentActivityRegistry.snapshot()`` 的普通字典列表。

        @author aceFelix
        """
        try:
            if self._disabled or self._closed.is_set():
                return
            if not self.options.agent_cards_on():
                return
            self._events.put({"type": "__agents__", "items": list(items or [])})
        except Exception:
            pass

    def _bind_observer(self) -> None:
        """注册活动台账观察者（只在 Tk 线程就绪后调用，幂等）。"""
        if self._observer_bound or not self.options.agent_cards_on():
            return
        try:
            from agent.collaboration.activity import get_activity_registry

            get_activity_registry().add_observer(self.feed_agents)
            self._observer_bound = True
        except Exception:
            pass

    def _unbind_observer(self) -> None:
        """注销活动台账观察者（退出/窗口销毁时调用，幂等）。"""
        if not self._observer_bound:
            return
        self._observer_bound = False
        try:
            from agent.collaboration.activity import get_activity_registry

            get_activity_registry().remove_observer(self.feed_agents)
        except Exception:
            pass

    def shutdown(self, *, wait: bool = True) -> None:
        """进程退出前收口（幂等）：通知 Tk 线程销毁窗口。

        Args:
            wait: 是否等待 Tk 线程退出。主线程退出前必须等，否则 Python 在
                主线程回收 Tk 对象会触发 ``Tcl_AsyncDelete``（Tk 的 Tcl 解释器
                不允许跨线程销毁）——实机崩溃过，见本模块 2026-11-08 修复。
        """
        thread = self._thread
        # 先摘掉台账观察者：窗口线程退出后不应再有快照入队
        self._unbind_observer()
        if thread is None:
            return
        try:
            self._events.put({"type": "__shutdown__"})
        except Exception:
            return
        if wait and thread.is_alive():
            thread.join(timeout=1.5)

    # ---- 线程管理 ----

    def _ensure_started(self) -> None:
        """懒启动 Tk 线程；最多等待 2 秒确认窗口是否创建成功。"""
        with self._lock:
            if self._closed.is_set() or self._disabled:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="jarvis-cockpit-window",
                daemon=True,
            )
            self._thread.start()
            if not self._atexit_registered:
                atexit.register(self.shutdown)
                self._atexit_registered = True
        self._ready.wait(timeout=2.0)

    def _thread_main(self) -> None:
        """Tk 线程入口：创建无边框窗口并进入 mainloop。"""
        try:
            import tkinter as tk
        except Exception as exc:  # noqa: BLE001 - 无 tkinter 时静默禁用
            self._disabled = True
            diag_info("ui", f"驾驶舱窗口不可用（tkinter 缺失）: {exc}")
            self._ready.set()
            return

        root = None
        try:
            root = tk.Tk()
            root.title("JARVIS · 任务驾驶舱")
            root.overrideredirect(True)
            if self._topmost:
                root.attributes("-topmost", True)
            # 不做半透明：0.96 的 alpha 会让背后的终端文字透出来，HUD 显得脏
            # （2026-11-08 实机截图确认）。保持完全不透明。
            # 右上角常驻：离屏幕右缘 24px，垂直居中偏上；有记忆位置则恢复
            sw = root.winfo_screenwidth()
            sh = root.winfo_screenheight()
            saved = _load_hud_position() if self.options.position_memory else None
            if saved is not None:
                x, y = saved
                x = max(0, min(sw - WINDOW_W, x))
                y = max(0, min(sh - WINDOW_H, y))
            else:
                x = max(0, sw - WINDOW_W - 24)
                y = max(24, (sh - WINDOW_H) // 2 - 60)
            root.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+{y}")
            if not self.options.cockpit_on():
                # 只开浮窗卡：根窗口保持隐藏，仅作为 Toplevel 的父容器
                root.withdraw()

            self._root = root
            if self.options.cockpit_on():
                self._hud = _Hud(root, on_close=self._close_from_ui, options=self.options)
                # 拖动结束保存位置（只在用户真正移动后写盘）；add="+" 保留
                # _Hud 内部用于“点击步骤展开详情”的 release 绑定
                try:
                    self._hud.canvas.bind(
                        "<ButtonRelease-1>",
                        lambda _e: self._remember_position(),
                        add="+",
                    )
                except Exception:
                    pass
            self._cards = CardManager(root, self.options, self._anchor)
            self._agent_cards = AgentCardManager(root, self.options, self._anchor)
            # 左侧抓取窗（懒创建 Toplevel，首次有抓取才真正出现）
            self._crawl = CrawlManager(root, self.options)
            # 终端优先：Git Bash / cmd 在前台时两个窗口让出置顶（2026-11-09）
            self._zorder = TerminalAwareTopmost(root, self._crawl, enabled=self._topmost)
            # 观察者须在 _ready 前注册，Subagent 首次调用才能看到 has_card_observer
            self._bind_observer()
            self._ready.set()
            self._pump()
            root.mainloop()
        except Exception as exc:  # noqa: BLE001 - 窗口故障不能影响主流程
            self._disabled = True
            diag_info("ui", f"驾驶舱窗口初始化失败: {exc}")
            try:
                if root is not None:
                    root.destroy()
                    self._destroyed = True
            except Exception:
                pass
            self._ready.set()
        finally:
            # Tk 对象必须在创建它的线程里释放，否则 Tcl 报 “async handler deleted by
            # the wrong thread”；destroy 之后禁止再调任何 Tk 方法（实机 “alloc: invalid block”）。
            if root is not None and not self._destroyed:
                try:
                    root.destroy()
                    self._destroyed = True
                except Exception:
                    pass
            self._hud = None
            self._cards = None
            self._agent_cards = None
            self._crawl = None
            self._zorder = None
            self._unbind_observer()
            self._root = None
            self._closed.set()

    def _close_from_ui(self) -> None:
        """用户点击 ✕：只隐藏主 HUD 与浮窗卡（不销毁 Tk、不退出进程），下轮首个工具重现。"""
        self._hidden = True
        self._hidden_run_id = self._run_id
        try:
            if self._root is not None and self.options.cockpit_on():
                self._root.withdraw()
            if self._cards is not None:
                self._cards.hide_all()
            if self._agent_cards is not None:
                self._agent_cards.hide_all()
            if self._crawl is not None:
                self._crawl.hide_all()
        except Exception:
            pass
        self._remember_position()

    def _show_window(self) -> None:
        """把隐藏的主 HUD 与浮窗卡重新显示（只在 Tk 线程调用）。"""
        try:
            if self._root is not None and self.options.cockpit_on():
                self._root.deiconify()
                if self._topmost and getattr(self._zorder, "wanted", True):
                    self._root.attributes("-topmost", True)
                self._root.lift()
            if self._cards is not None:
                self._cards.show_all(self._planner)
            if self._agent_cards is not None:
                self._agent_cards.show_all(self._agent_planner)
            if self._crawl is not None:
                self._crawl.show_all(self._state)
        except Exception:
            pass

    def _destroy_root(self) -> None:
        """销毁所有卡片与 Tk 根窗口（只在 Tk 线程调用，且只执行一次）。"""
        if self._destroyed:
            return
        self._destroyed = True
        try:
            if self._crawl is not None:
                self._crawl.detach()
                self._crawl = None
            if self._agent_cards is not None:
                self._agent_cards.detach()
                self._agent_cards = None
            if self._cards is not None:
                self._cards.detach()
            self._unbind_observer()
            if self._root is not None:
                self._root.destroy()
        except Exception:
            pass

    def _anchor(self) -> tuple[int, int, bool]:
        """浮窗卡的锚点：主 HUD 可见时跟随，否则用屏幕右上角。"""
        try:
            if self._root is not None and not self._hidden and self.options.cockpit_on():
                return self._root.winfo_x(), self._root.winfo_y(), True
        except Exception:
            pass
        return 0, 0, False

    def _remember_position(self) -> None:
        """保存当前 HUD 位置（只在 Tk 线程调用）；记忆关闭/失败时静默。"""
        if not self.options.position_memory or self._root is None:
            return
        try:
            _save_hud_position(self._root.winfo_x(), self._root.winfo_y())
        except Exception:
            pass

    def _pump(self) -> None:
        """Tk 线程的 40ms 循环：取事件 → 更新状态/规划器 → 按需重绘。"""
        changed = False
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break
            if event.get("type") == "__shutdown__":
                self._destroy_root()
                return
            if event.get("type") == "__show__":
                self._show_window()
                continue
            if event.get("type") == "__user_close__":
                self._close_from_ui()
                continue
            if event.get("type") == "__agents__":
                # 子代理卡走台账快照，不参与主任务状态机
                if self._agent_planner.update(list(event.get("items") or [])):
                    changed = True
                self._last_event_at = time.monotonic()
                continue
            if self._planner.update(event):
                changed = True
            self._state.update(event)
            # 抓取类事件：驱动左侧“抓取网络”窗口（本轮有抓取才弹、结束保留）
            # @author aceFelix
            if (
                self._crawl is not None
                and not self._hidden
                and event.get("type") in ("web_fetch", "web_extract", "crawl_stats")
            ):
                try:
                    self._crawl.sync(self._state, self._run_id)
                except Exception:
                    pass
            self._last_event_at = time.monotonic()
            changed = True

        # 阈值到期/完成态收起由规划器推进，即使没有新事件也要检查
        if self._planner.tick():
            changed = True
        if self._agent_planner.tick():
            changed = True

        if changed:
            if self._hud is not None:
                try:
                    self._hud.draw(
                        self._state,
                        agents=self._agent_planner.summary()
                        if self.options.agent_cards_on()
                        else "",
                    )
                except Exception:
                    pass
            if self._cards is not None and not self._hidden:
                try:
                    self._cards.sync(self._planner)
                except Exception:
                    pass
            if self._agent_cards is not None and not self._hidden:
                try:
                    self._agent_cards.sync(self._agent_planner)
                except Exception:
                    pass

        # 动画只在运行中/刚有事件时刷新，且 effects=off 时完全静态；
        # 有代理卡时保持刷新，让卡片耗时持续走动（空闲队友卡也受益）。
        # @author aceFelix
        animating = (
            self._state.current is not None
            or (time.monotonic() - self._last_event_at < 1.5)
            or bool(self._agent_planner.visible())
        )
        if animating and self.options.effects != "off":
            if self._hud is not None:
                try:
                    self._hud.tick()
                except Exception:
                    pass
            if self._cards is not None and not self._hidden:
                try:
                    self._cards.tick(self._planner)
                except Exception:
                    pass
            if self._agent_cards is not None and not self._hidden:
                try:
                    self._agent_cards.tick(self._agent_planner)
                except Exception:
                    pass
        if self._zorder is not None:  # 终端优先：Git Bash / cmd 在前台时让位（0.4s 节流）
            self._zorder.tick()
        try:
            self._root.after(40, self._pump)
        except Exception:
            pass


class _Hud:
    """Canvas HUD 渲染器：每次全量重绘（元素少、事件驱动，足够快）。"""

    def __init__(self, root: Any, *, on_close: Any, options: VisualOptions) -> None:
        import tkinter as tk

        self.root = root
        self.options = options
        self.canvas = tk.Canvas(
            root,
            width=WINDOW_W,
            height=WINDOW_H,
            bg=BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self._drag_offset = (0, 0)
        self._press_xy: tuple[int, int] | None = None
        self._did_drag = False
        self._step_hits: list[tuple[float, dict[str, Any]]] = []
        self._expanded_key = ""
        self._last_state: CockpitState | None = None
        self._last_agents = ""
        self._scan_id: int | None = None
        self._dot_id: int | None = None
        self._anim_top = 0.0
        self._anim_bottom = 0.0
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        # 先绑内部点击（展开/拖动判定），外部再追加保存位置
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.tag_bind("close", "<Button-1>", lambda _e: on_close())

    # ---- 基础绘制 ----

    def draw(self, state: CockpitState, agents: str = "") -> None:
        """按当前状态全量重绘 HUD（只在事件到达时调用）。

        Args:
            state: 主任务状态。
            agents: 代理卡摘要（如 ``AGENTS 2 · +1``）；空串表示没有代理卡。

        @author aceFelix
        """
        self._scan_id = None
        self._dot_id = None
        self._last_state = state
        self._last_agents = agents
        self.canvas.delete("all")
        self._draw_grid()
        self._draw_frame()
        self._draw_header(state)
        self._draw_metrics(state)
        self._draw_current(state)
        self._draw_steps(state)
        self._draw_feed(state)
        self._draw_fetches(state)
        self._draw_detail()
        self._draw_footer(agents)

    def tick(self) -> None:
        """动画帧：只移动扫描线 + 闪烁状态点，不重绘整屏。"""
        if self.options.effects == "off":
            return
        try:
            if self._scan_id is not None:
                phase = (time.monotonic() * 0.9) % 1.0
                y = self._anim_top + phase * (self._anim_bottom - self._anim_top)
                self.canvas.coords(self._scan_id, PAD + 1, y, WINDOW_W - PAD - 1, y)
            if self._dot_id is not None:
                color = CYAN if int(time.monotonic() * 2) % 2 == 0 else CYAN_DIM
                self.canvas.itemconfigure(self._dot_id, fill=color)
        except Exception:
            pass

    def _text(
        self,
        x: float,
        y: float,
        content: Any,
        *,
        fill: str = TEXT,
        font: Any = FONT_TEXT,
        anchor: str = "w",
        tag: str | None = None,
    ) -> int:
        """在 Canvas 上放一行文字（tag 用于关闭按钮命中）。"""
        return self.canvas.create_text(
            x,
            y,
            text=str(content),
            fill=fill,
            font=font,
            anchor=anchor,
            tags=(tag,) if tag else (),
        )

    def _line(self, x1: float, y1: float, x2: float, y2: float, fill: str, width: int = 1) -> int:
        """画一条线并返回 canvas item id（扫描线动画需要）。"""
        return self.canvas.create_line(x1, y1, x2, y2, fill=fill, width=width)

    def _rect(self, x1: float, y1: float, x2: float, y2: float, outline: str, width: int = 1) -> None:
        """画一个矩形边框。"""
        self.canvas.create_rectangle(x1, y1, x2, y2, outline=outline, width=width)

    def _draw_grid(self) -> None:
        """背景网格：低亮度，制造 HUD 底噪。"""
        step = 28
        for x in range(0, WINDOW_W, step):
            self._line(x, 0, x, WINDOW_H, GRID)
        for y in range(0, WINDOW_H, step):
            self._line(0, y, WINDOW_W, y, GRID)

    def _draw_frame(self) -> None:
        """外框 + 四角角标。"""
        self._rect(6, 6, WINDOW_W - 6, WINDOW_H - 6, CYAN_DIM)
        corner = 16
        for x, y, dx, dy in (
            (6, 6, 1, 1),
            (WINDOW_W - 6, 6, -1, 1),
            (6, WINDOW_H - 6, 1, -1),
            (WINDOW_W - 6, WINDOW_H - 6, -1, -1),
        ):
            self._line(x, y, x + dx * corner, y, CYAN, 2)
            self._line(x, y, x, y + dy * corner, CYAN, 2)

    def _draw_header(self, state: CockpitState) -> None:
        """标题、状态点、关闭按钮。"""
        self._text(PAD, 26, "J.A.R.V.I.S", fill=CYAN_DIM, font=FONT_TITLE)
        self._text(PAD + 1, 25, "J.A.R.V.I.S", fill=CYAN, font=FONT_TITLE)
        self._text(PAD, 46, "TASK COCKPIT // 任务驾驶舱", fill=DIM, font=FONT_SMALL)

        if state.finished:
            dot = GREEN if state.display_failed() == 0 else RED
        elif state.current is not None:
            dot = CYAN if int(time.monotonic() * 2) % 2 == 0 else CYAN_DIM
        else:
            dot = DIM
        dot_id = self.canvas.create_oval(
            WINDOW_W - 82, 20, WINDOW_W - 74, 28, fill=dot, outline=""
        )
        # 只有执行中才让 tick 闪烁这个点；完成/待命保持静态颜色
        self._dot_id = dot_id if state.current is not None else None
        self._text(
            WINDOW_W - 56, 24,
            "RUN" if state.current else "IDLE",
            fill=DIM, font=FONT_SMALL, anchor="e",
        )

        # 关闭按钮（点击后本会话不再弹出）
        self.canvas.create_oval(
            WINDOW_W - 40, 12, WINDOW_W - 14, 38,
            outline=CYAN_DIM, width=1, tags=("close",),
        )
        self._text(WINDOW_W - 27, 25, "✕", fill=DIM, font=("Microsoft YaHei UI", 10), anchor="center", tag="close")

        self._line(PAD, 60, WINDOW_W - PAD, 60, CYAN_DIM)

    def _draw_metrics(self, state: CockpitState) -> None:
        """三格大数字：步骤 / 失败 / 总耗时。"""
        cells = (
            ("步骤 STEPS", str(state.display_steps()), CYAN),
            ("失败 FAILED", str(state.display_failed()), RED if state.display_failed() else GREEN),
            ("耗时 ELAPSED", _fmt_elapsed(state.elapsed_ms()), CYAN),
        )
        cell_w = (WINDOW_W - 2 * PAD - 16) // 3
        for index, (label, value, color) in enumerate(cells):
            x = PAD + index * (cell_w + 8)
            self._rect(x, 72, x + cell_w, 132, GRID)
            self._text(x + 10, 84, label, fill=DIM, font=FONT_LABEL)
            # 发光：先画一层暗青偏移，再叠亮色
            self._text(x + 11, 110, value, fill=CYAN_DIM, font=FONT_NUM)
            self._text(x + 10, 109, value, fill=color, font=FONT_NUM)

    def _draw_current(self, state: CockpitState) -> None:
        """当前动作带：完成/运行中/待命，运行中叠加扫描线。"""
        top, bottom = 144, 190
        self.canvas.create_rectangle(PAD, top, WINDOW_W - PAD, bottom, fill=BG_PANEL, outline=GRID)
        if state.finished:
            failed = state.display_failed()
            head = "✓ 任务完成" if failed == 0 else f"✗ 任务结束 · {failed} 步失败"
            color = GREEN if failed == 0 else RED
            self._text(PAD + 12, top + 18, head, fill=color, font=("Microsoft YaHei UI", 11, "bold"))
            self._text(
                PAD + 12, top + 38,
                f"共 {state.display_steps()} 步 · {_fmt_elapsed(state.elapsed_ms())} · 窗口保留",
                fill=DIM, font=FONT_SMALL,
            )
        elif state.current is not None:
            active = state.active_tools()
            if len(active) > 1:
                extra = len(active) - self.options.card_max
                head = f"● {len(active)} 个工具并行  " + " · ".join(active)
                if extra > 0:
                    head += f"  +{extra}"
                self._text(
                    PAD + 12, top + 18, _clip(head, 62), fill=CYAN,
                    font=("Microsoft YaHei UI", 10, "bold"),
                )
                self._text(
                    PAD + 12, top + 38,
                    f"并行执行中 · 浮窗最多显示 {self.options.card_max} 张",
                    fill=DIM, font=FONT_SMALL,
                )
            else:
                tool = str(state.current.get("tool") or "?")
                target = describe_target(state.current.get("target"))
                head = f"● {tool}" + (f"  {target}" if target else "")
                self._text(
                    PAD + 12, top + 18, _clip(head, 62), fill=CYAN,
                    font=("Microsoft YaHei UI", 10, "bold"),
                )
                self._text(PAD + 12, top + 38, "执行中 · 等待工具返回", fill=DIM, font=FONT_SMALL)
            # 扫描线：记录 item id，后续 tick 只移动它（不整屏重绘）
            if self.options.effects != "off":
                self._anim_top = top + 6
                self._anim_bottom = bottom - 10
                phase = (time.monotonic() * 0.9) % 1.0
                y = self._anim_top + phase * (self._anim_bottom - self._anim_top)
                self._scan_id = self._line(PAD + 1, y, WINDOW_W - PAD - 1, y, CYAN_DIM, 1)
        elif state.steps == 0:
            self._text(PAD + 12, top + 18, "● 待命", fill=DIM, font=("Microsoft YaHei UI", 10, "bold"))
            self._text(PAD + 12, top + 38, "等待第一次工具调用", fill=DIM, font=FONT_SMALL)
        else:
            self._text(PAD + 12, top + 18, "● 等待下一步", fill=DIM, font=("Microsoft YaHei UI", 10, "bold"))
            self._text(PAD + 12, top + 38, f"已完成 {state.display_steps()} 步", fill=DIM, font=FONT_SMALL)

    def _draw_steps(self, state: CockpitState) -> None:
        """执行序列：最近 4 步（失败步固定置顶，点击某行展开工具详情）。"""
        self._section("执行序列 // EXEC", 202)
        rows = step_rows(list(state.step_history))
        self._step_hits = []
        if not rows:
            self._expanded_key = ""
            self._text(PAD, 232, "— 暂无工具调用", fill=DIM, font=FONT_SMALL)
            return
        # 展开的步骤可能已被 deque 淘汰：及时收起，避免详情悬空
        keys = {self._step_key(entry) for entry in rows}
        if self._expanded_key not in keys:
            self._expanded_key = ""
        y = 228
        for entry in rows:
            key = self._step_key(entry)
            expanded = key == self._expanded_key
            if expanded:
                self.canvas.create_rectangle(
                    PAD - 4, y - 9, WINDOW_W - PAD, y + 9,
                    fill=BG_PANEL, outline="",
                )
            ok = entry.get("ok")
            if ok is True:
                marker, color = "✓", GREEN
            elif ok is False:
                marker, color = "✗", RED
            else:
                marker, color = "●", CYAN
            self._text(PAD, y, marker, fill=color, font=FONT_TEXT)
            name = str(entry.get("tool") or "?")
            target = describe_target(entry.get("target"))
            left = name + (f"  {target}" if target else "")
            self._text(PAD + 18, y, _clip(left, 40), fill=TEXT, font=FONT_TEXT)
            right = _fmt_ms(entry.get("ms")) or ("运行中" if ok is None else "")
            self._text(WINDOW_W - PAD, y, right, fill=DIM, font=FONT_MONO, anchor="e")
            # 展开指示：▸ 收起 / ▾ 展开；点击整行都可切换
            self._text(
                WINDOW_W - PAD - 58,
                y,
                "▾" if expanded else "▸",
                fill=AMBER if expanded else DIM,
                font=FONT_SMALL,
                anchor="e",
            )
            self._step_hits.append((float(y), entry))
            y += 20

    @staticmethod
    def _step_key(entry: dict[str, Any]) -> str:
        """步骤稳定标识：优先 tool_use_id，缺失时用对象 id（同一 deque 内稳定）。"""
        return str(entry.get("tool_use_id") or id(entry))

    def _draw_detail(self) -> None:
        """展开的工具详情：委托 ``cockpit_detail`` 绘制，避免主文件超 900 行。"""
        if not self._expanded_key:
            return
        entry = next(
            (e for _y, e in self._step_hits if self._step_key(e) == self._expanded_key),
            None,
        )
        if entry is None:
            return
        draw_tool_detail(self.canvas, entry, window_w=WINDOW_W, pad=PAD)

    def _draw_feed(self, state: CockpitState) -> None:
        """数据流：最近 8 条事件摘要。"""
        self._section("数据流 // STREAM", 312)
        items = list(state.feed)[-8:]
        if not items:
            self._text(PAD, 340, "— 暂无事件", fill=DIM, font=FONT_SMALL)
            return
        y = 336
        for line in items:
            self._text(PAD, y, _clip(line, 56), fill=DIM, font=FONT_MONO)
            y += 17

    def _draw_fetches(self, state: CockpitState) -> None:
        """抓取：最近 3 条 URL / 状态 / 耗时 / 字节。"""
        self._section("抓取 // FETCH", 486)
        items = list(state.fetches)[-3:]
        if not items:
            self._text(PAD, 512, "— 暂无抓取", fill=DIM, font=FONT_SMALL)
            return
        y = 508
        for item in items:
            ok = item.get("status") == "ok"
            self._text(PAD, y, "✓" if ok else "✗", fill=GREEN if ok else RED, font=FONT_TEXT)
            self._text(PAD + 16, y, _clip(_short_url(item.get("url")), 34), fill=TEXT, font=FONT_MONO)
            meta = " · ".join(
                part for part in (
                    _fmt_ms(item.get("duration_ms")),
                    _fmt_bytes(item.get("bytes")),
                ) if part
            )
            self._text(WINDOW_W - PAD, y, meta, fill=DIM, font=FONT_MONO, anchor="e")
            y += 22

    def _draw_footer(self, agents: str = "") -> None:
        """底部提示；有代理卡时在右侧显示代理总数与溢出数。"""
        self._line(PAD, WINDOW_H - 36, WINDOW_W - PAD, WINDOW_H - 36, CYAN_DIM)
        self._text(PAD, WINDOW_H - 20, "点 ✕ 隐藏 · 下轮首个工具自动重现", fill=DIM, font=FONT_SMALL)
        if agents:
            self._text(WINDOW_W - PAD, WINDOW_H - 20, agents, fill=AMBER, font=FONT_LABEL, anchor="e")
        else:
            self._text(WINDOW_W - PAD, WINDOW_H - 20, "HUD v1", fill=CYAN_DIM, font=FONT_SMALL, anchor="e")

    def _section(self, title: str, y: int) -> None:
        """小节标题 + 右侧延长线。"""
        self._text(PAD, y, title, fill=CYAN_DIM, font=FONT_LABEL)
        self._line(PAD + 108, y, WINDOW_W - PAD, y, GRID)

    # ---- 拖动 ----

    def _on_press(self, event: Any) -> None:
        """记录拖动起点（窗口无标题栏，整面可拖）与点击起点。"""
        self._press_xy = (event.x, event.y)
        self._did_drag = False
        self._drag_offset = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )

    def _on_drag(self, event: Any) -> None:
        """按拖动位移移动窗口；移动超过 4px 标记为拖动，避免误触展开。"""
        if self._press_xy is not None:
            dx = abs(event.x - self._press_xy[0])
            dy = abs(event.y - self._press_xy[1])
            if dx > 4 or dy > 4:
                self._did_drag = True
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        try:
            self.root.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _on_release(self, event: Any) -> None:
        """点击（非拖动）执行序列行：展开/收起该步工具详情。"""
        if self._did_drag:
            self._did_drag = False
            return
        for y, entry in self._step_hits:
            if abs(event.y - y) <= 9:
                key = self._step_key(entry)
                self._expanded_key = "" if self._expanded_key == key else key
                if self._last_state is not None:
                    self.draw(self._last_state, self._last_agents)
                return
