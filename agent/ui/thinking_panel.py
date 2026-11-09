"""思考过程面板：缓冲、Rich Live 启停与渲染。

从 ``agent/ui/cli.py`` 抽出（2026-11-08），原因：
1. ``cli.py`` 已逼近项目单文件 900 行上限，C1 终端驾驶舱即将接线；
2. 思考面板与驾驶舱是两个**互斥**的 Rich Live，集中管理思考面板的
   生命周期，便于驾驶舱在启动前协调收口；
3. 职责单一：本模块只管思考缓冲与面板，工具卡片/正文/输入循环仍留在
   ``RichCLI``。

渲染模式（2026-11-08 更新）：
- 真交互终端且**非 legacy Windows 控制台**：Rich Live 原地刷新；
- stdout 重定向 / 管道 / 非 TTY / **cmd 这类 legacy 控制台**：静态模式，
  思考结束时一次性打印完整面板，避免 Live 帧画不出来或错乱。

兼容性：既有调用方（单测、``agent/voice/repl_adapter.py``）会直接读写
``RichCLI._thinking_buf/_thinking_started/_thinking_live/_live_handles``，
``RichCLI`` 保留同名属性代理到本模块的 ``ThinkingPanel`` 实例。

@author aceFelix
"""

from __future__ import annotations

import threading
from typing import Any, Callable

try:
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text

    _HAS_RICH = True
except ImportError:  # pragma: no cover - rich 为运行期可选依赖
    _HAS_RICH = False


class ThinkingPanel:
    """思考过程面板控制器。

    交互终端：Rich ``Live`` 原地刷新（12fps），思考结束/正文到达时收口；
    非交互终端（stdout 重定向、管道、部分 IDE 终端）：不建 Live，只累积，
    结束时刻一次性打印完整面板，避免每次刷新追加整帧导致刷屏
    （fixlog: thinking-panel-flood-fix）。

    Attributes:
        buf: 当前思考文本缓冲。
        started: 是否已进入「可见思考」阶段（纯空白增量不算）。
        live: 当前正在渲染的 Rich Live（静态模式为 None）。
        handles: 历史 Live 句柄清单；stop 抛异常时保留，下次收口重试。

    @author aceFelix
    """

    #: Live 帧面板的最大可视行数：思考流无限长，必须做尾部滚动窗口，
    #: 否则面板随缓冲增长超出终端高度，ellipsis 截断后永远停在开头段
    #: （模型思考块开头几乎总是前导换行 → 空面板刷屏）。
    _MAX_LINES = 12

    def __init__(self, console_provider: Callable[[], Any]) -> None:
        """初始化面板控制器。

        Args:
            console_provider: 返回当前 Rich ``Console`` 的取用函数。
                ``RichCLI._console`` 会被测试/运行期替换，因此不能在建
                实例时捕获，必须每次动态读取。
        """
        self._console_provider = console_provider
        self.buf = ""
        self.started = False
        self.live: Any = None
        self.handles: list[Any] = []
        # 后台线程（窗口观察器）与模型线程都会调用 feed/end，锁住
        # 「状态 + start」这一小段，避免 started=False 但 Live 仍在跑的残留。
        self._lock = threading.RLock()

    def feed(self, text: str) -> None:
        """接收一段思考增量（``UIProtocol.assistant_thinking`` 的真实实现）。

        Args:
            text: 模型流式输出的思考片段。
        """
        live: Any = None
        with self._lock:
            self.buf += text

            if not self.started:
                # 纯空白增量不启动：继续静默累积，等第一段可见文本到达再开面板
                if not self.buf.strip():
                    return
                # 先清掉任何异常残留的 Live，保证同一时刻只有一个面板在渲染
                self._stop_handles_locked()
                self.started = True
                console = self._console_provider()
                # legacy Windows 控制台（cmd 默认）不支持 Rich Live 的原地刷新/
                # 擦除，帧会画不出来或错乱；与"stdout 已重定向"同口径降级为静态
                # 模式，思考结束时一次性打印完整面板（2026-11-08 实机：cmd 看不到
                # 思考、mintty 反而正常，根因就是 legacy_windows=True）。
                use_live = (
                    console
                    and _HAS_RICH
                    and console.is_terminal
                    and not bool(getattr(console, "legacy_windows", False))
                )
                if use_live:
                    live = Live(
                        self.panel(),
                        console=console,
                        refresh_per_second=12,
                        transient=False,
                        # 面板超过终端可视高度时截断为省略号，而不是 "visible"
                        # （visible 会让超高内容滚出屏幕后无法原地擦除，同样刷屏）
                        vertical_overflow="ellipsis",
                    )
                    self.live = live
                    self.handles.append(live)
                    live.start()
                # else: 静态模式——不建 Live，只在 end() 时打印一次
            else:
                # 流式更新：新文本到了，刷新 Live 面板（静态模式下无 Live，仅累积）
                live = self.live

        if live is not None:
            # 刷新放到锁外：渲染可能较慢，不能挡住后台线程的 info()/收尾
            try:
                live.update(self.panel(windowed=True))
            except Exception:
                # 单帧刷新失败不影响后续；收口时仍会按句柄清单强制 stop
                pass

    def panel(self, windowed: bool = False) -> Any:
        """生成当前思考内容的 Rich Panel。

        Args:
            windowed: True=Live 帧专用，额外做尾部滚动窗口（面板高度有界，
                永远显示最新一段思考）；False=静态模式终帧，打印完整思考
                内容（一次性输出，无刷屏风险，供事后回看）。
        """
        # 去掉前导空白：思考块开头几乎总是纯换行，留着会把可见文字顶出
        # 终端可视区，渲染出一框空白（2026-10-14 空面板刷屏的真实根因）
        display = self.buf.lstrip("\n")
        if windowed:
            # 显式尾部窗口：只保留最后 _MAX_LINES 行，面板高度有界，
            # Live 每次刷新都能看到最新文字在原地滚动
            lines = display.split("\n")
            if len(lines) > self._MAX_LINES:
                display = "...（前略）\n" + "\n".join(lines[-self._MAX_LINES:])
        if len(display) > 1200:
            # 行数少但单行超长的兜底，防止一行巨长文本撑爆面板
            display = display[:800] + "\n... [思考继续] ...\n" + display[-250:]
        return Panel(
            Text(display, style="dim #6a7a8a"),
            title="💭 思考过程",
            title_align="left",
            border_style="dim #3a4a5a",
            expand=False,
            padding=(0, 1),
        )

    def plain_panel(self) -> str:
        """legacy Windows 控制台专用的纯文本思考块。

        2026-11-08 实机：cmd（conhost，``legacy_windows=True``）下 Rich Panel
        会被渲染成一列竖排乱码（边框/宽度计算在 legacy 渲染器里不可靠）。
        因此 legacy 模式完全避开 Panel，输出纯文本分隔块，保证可读。

        Returns:
            带标题分隔线、可直接 ``console.print`` 的纯文本字符串。
        """
        display = self.buf.lstrip("\n")
        if len(display) > 1200:
            display = display[:800] + "\n... [思考继续] ...\n" + display[-250:]
        return f"\n---- 思考过程 ----\n{display}\n------------------\n"

    def _stop_handles_locked(self) -> None:
        """停掉所有记录的 Live（调用方必须已持有 ``_lock``）。

        只清状态、逐个 stop；任何异常都吞掉——收口失败不能让对话中断。
        失败的句柄留在列表里，下一次收口或读输入前会再试一次。
        """
        handles, self.handles = self.handles, []
        self.live = None
        for old in handles:
            try:
                old.stop()
            except Exception:
                self.handles.append(old)

    def end(self) -> None:
        """结束思考阶段（幂等）。

        Live 模式：最终刷新 + 停止，面板自然保留。
        静态模式（非交互终端）：此刻才一次性打印面板，避免流式刷屏。

        2026-11-08 修复：以前先判 ``started``，为 False 就整段跳过——一旦
        上一次 stop 抛异常留下"标志已清、Live 仍在跑"的残留，后续每次收口
        都会跳过它，泄漏的 Live 用 12fps 一直重绘，把正文/工具输出撕成
        一字一行。现在无论标志如何，都按句柄清单强制收口。
        """
        with self._lock:
            handles = self.handles
            self.handles = []
            self.live = None
            started = self.started
            self.started = False

        if not started and not handles:
            # 未启动（整段都是空白增量）：兜底清空缓冲，不输出任何面板
            with self._lock:
                self.buf = ""
            return

        if handles and not started:
            # 状态不一致说明前面的 stop 失败过；记一条诊断，便于下次定位
            try:
                from agent.core.diag import diag_warn

                diag_warn("ui", f"思考面板 Live 残留 {len(handles)} 个，已强制收口")
            except Exception:
                pass

        try:
            final_panel = self.panel()
        except Exception:
            final_panel = None

        for live in handles:
            try:
                if final_panel is not None:
                    live.update(final_panel)
            except Exception:
                pass
            try:
                live.stop()
            except Exception:
                with self._lock:
                    self.handles.append(live)

        if not handles and final_panel is not None and self.buf:
            # 静态模式：思考完整了才输出一个面板（全程只打印一次）
            console = self._console_provider()
            if console:
                if bool(getattr(console, "legacy_windows", False)):
                    # legacy 控制台打印 Panel 会竖排乱码，改纯文本块
                    console.print(self.plain_panel())
                else:
                    console.print(final_panel)

        with self._lock:
            self.buf = ""
