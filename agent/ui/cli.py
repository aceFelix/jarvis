"""Rich CLI 界面。

实现 agent.core.context.UIProtocol，用 Rich 渲染:
- 助手文本流式输出（Live 区域）
- 工具调用 / 结果（带颜色和边框）
- 信息 / 警告 / 错误
- 阻塞式用户输入（含权限确认）

输入层: 优先使用 prompt_toolkit 提供 Tab 补全和 / 命令列表。
未装 prompt_toolkit 时回退到 input() 保持简单可靠。
"""

from __future__ import annotations

import sys
from typing import Any

from agent.core.context import UIProtocol

try:
    from rich.console import Console, ConsoleDimensions
    from rich.live import Live
    from rich.markup import escape
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.text import Text

    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False

# 渲染宽度上限（列数）：防止终端从最大化缩回时画面重排错乱（作者：aceFelix）。
# 已输出到 scrollback 的内容无法重绘——若最大化时按 200+ 列宽渲染，
# 缩窗后终端会把超宽行折行重排，启动横幅/面板全乱。封顶后内容恒窄于常见窗口宽，缩窗不折行。
_MAX_RENDER_WIDTH = 120

if _HAS_RICH:

    class _CappedConsole(Console):
        """渲染宽度封顶的 Console。

        动态覆写 size：取 min(实际终端宽, _MAX_RENDER_WIDTH)——
        窗口缩小不折行，窗口在封顶内变大变小仍自适应。
        """

        @property
        def size(self) -> "ConsoleDimensions":
            dims = super().size
            if dims.width > _MAX_RENDER_WIDTH:
                return ConsoleDimensions(_MAX_RENDER_WIDTH, dims.height)
            return dims

# prompt_toolkit（可选，用于 Tab 补全和 / 命令列表）
try:
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.shortcuts import PromptSession
    from prompt_toolkit.styles import Style

    _HAS_PT = True

    # ---- Windows: 补丁 prompt_toolkit 支持 Shift+Enter 换行 ----
    # prompt_toolkit 的 win32 输入处理器能检测 SHIFT_PRESSED 状态，
    # 但 shift 映射表里漏了 Enter (ControlJ)。这里 monkey-patch 补上：
    # Shift+Enter → Escape + Enter，由 key binding 捕获后插入换行符。
    import sys as _sys
    if _sys.platform == "win32":
        try:
            import prompt_toolkit.input.win32 as _w32
            _orig_event_to_key = _w32.ConsoleInputReader._event_to_key_presses

            def _patched_event_to_key(self, ev):
                result = _orig_event_to_key(self, ev)
                if (
                    result
                    and ev.ControlKeyState & self.SHIFT_PRESSED
                    and not (ev.ControlKeyState & (self.LEFT_CTRL_PRESSED | self.RIGHT_CTRL_PRESSED))
                    and result[0].key == Keys.ControlJ
                ):
                    from prompt_toolkit.keys import KeyPress
                    return [KeyPress(Keys.Escape, ""), result[0]]
                return result

            _w32.ConsoleInputReader._event_to_key_presses = _patched_event_to_key
        except Exception:
            pass  # 非 win32 控制台（如 ConPTY），静默跳过

    def _make_pt_bindings() -> KeyBindings:
        """Shift+Enter 换行、Enter 提交、Ctrl+V 粘贴剪贴板图片。"""
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _(event):
            """Shift+Enter / Escape+Enter: 在光标处插入换行符。"""
            event.current_buffer.insert_text("\n")

        @kb.add("c-v")
        def _(event):
            """Ctrl+V：显式添加剪贴板图片到待发送列表（回调由 REPL 注入，
            见 core.images.paste_clipboard_image）。不再自动读剪贴板，
            避免普通回车误带残留图片。@author aceFelix"""
            if _PASTE_IMAGE_CALLBACK is not None:
                try:
                    _PASTE_IMAGE_CALLBACK()
                except Exception:
                    pass  # 剪贴板读取失败不打断输入

        return kb

    # 补全样式：黑色背景 + 亮色命令名 + 可读描述 + 选中高亮
    _PT_STYLE = Style.from_dict({
        # 菜单整体：透明黑底，细边框
        "completion-menu":                    "bg:#000000 border:#333333",
        # 普通项：无额外背景（继承终端黑色）
        "completion-menu.completion":         "",
        # 选中项：深蓝灰底 + 白字
        "completion-menu.completion.current": "bg:#1a2a3a #ffffff",
        # 命令名（completion 的主文本）：青蓝色，清晰可见
        "completion-menu.meta.completion":     "#5bc8ff",   # JARVIS 亮蓝
        # 选中项的描述文字：浅蓝白
        "completion-menu.meta.completion.current": "#c8e0ff",
        # 滚动条：低调暗色
        "scrollbar.background":                "#111111",
        "scrollbar.button":                   "#333333",
    })
except ImportError:
    _HAS_PT = False
    _PT_STYLE = None


# ---- 斜杠命令注册表与补全器（已迁至 agent/ui/slash_completer.py）----
# RichCLI 自身职责是渲染与交互循环，命令清单/候选补全属于另一件事；拆出后
# 此处以别名重新导入，既有调用点、`from agent.ui.cli import SLASH_COMMANDS`
# 与测试/文档引用路径全部不变。
# @author aceFelix
from agent.ui.slash_completer import (  # noqa: F401
    SLASH_COMMANDS,
    SlashCompleter as _SlashCompleter,
    tool_voice_desc as _tool_voice_desc,
)

# Ctrl+V 贴图回调：由 main.repl() 启动时注入（读剪贴板图片加入待发送列表）。
# 放在模块级供 _make_pt_bindings 的按键回调调用，避免 ui 层反向依赖 core 层。
# @author aceFelix
_PASTE_IMAGE_CALLBACK = None


class RichCLI(UIProtocol):
    """基于 Rich 的命令行 UI。"""

    def __init__(self, *, verbose: bool = False, boot_animation: bool = True) -> None:
        self.verbose = verbose
        self._boot_animation = boot_animation
        if _HAS_RICH:
            # 用封顶宽度的 Console：最大化启动后缩窗不会把已输出的宽行折行重排（作者：aceFelix）
            self._console = _CappedConsole()
        else:
            self._console = None  # type: ignore[assignment]
        # 当前正在累积的助手文本（用于流式）
        self._assistant_buf = ""
        # 当前正在累积的思考文本（深度思考流式）
        self._thinking_buf = ""
        self._thinking_started = False
        self._thinking_live: Any = None  # Rich Live 实例（流式思考面板）
        # prompt_toolkit 会话（懒初始化，复用历史记录）
        self._pt_session: PromptSession | None = None
        # 语音模式：隐藏思维链文本，通过 TTS 播报进度（voice_loop 设置）
        self._voice_mode: bool = False
        self._voice_tts_feed: Any = None  # TTS feed 回调
        if _HAS_PT:
            try:
                kw: dict = dict(
                    completer=_SlashCompleter(),
                    history=InMemoryHistory(),
                    complete_while_typing=True,    # 输入 / 时自动弹出菜单
                    multiline=False,
                    key_bindings=_make_pt_bindings(),
                )
                if _PT_STYLE is not None:
                    kw["style"] = _PT_STYLE
                self._pt_session = PromptSession(**kw)
            except Exception:
                self._pt_session = None

    # ---- UIProtocol 实现 ----

    def set_paste_image_handler(self, handler) -> None:
        """注入 Ctrl+V 贴图回调（无参可调用）：由 main.repl() 在命令
        上下文建好后调用，回调内部读剪贴板图片加入待发送列表。
        @author aceFelix
        """
        global _PASTE_IMAGE_CALLBACK
        _PASTE_IMAGE_CALLBACK = handler

    def assistant_text(self, text: str) -> None:
        """流式助手文本。直接打到 stdout，不加换行。"""
        # 先从思考模式切换到正式回复
        self._end_thinking()
        self._assistant_buf += text
        if self._console:
            self._console.print(text, end="", highlight=False, soft_wrap=True)
        else:
            sys.stdout.write(text)
            sys.stdout.flush()

    def assistant_thinking(self, text: str) -> None:
        """流式思考增量（深度思考/思维链）。

        交互终端：用 Rich Live + Panel 原地刷新显示思考过程。
        语音模式下 ThinkingDelta 不触发 on_assistant_text，TTS 不会朗读思考，
        所以画 Panel 不影响语音播报。

        防刷屏关键（fixlog: thinking-panel-flood-fix）：
        Rich Live 的原地擦除依赖光标移动转义序列，仅在真实交互终端可用。
        stdout 被重定向/非交互终端（部分 IDE 终端、mintty、管道）时，
        每次 update 都会把整帧内容追加输出——思考流每秒十几次刷新，
        屏幕瞬间被重复递增的面板刷满。因此非交互终端降级为静态模式：
        只累积缓冲，思考结束时一次性打印面板。

        防空面板（2026-10-14 二次修复）：模型思考块开头常先流式吐纯换行
        增量。首版修复只延迟了面板启动，但 Live 帧显示的是缓冲**开头**——
        一旦可见文字到达，面板从此固定在「前导空白 + 开头 800 字」，后续
        增量只增长不可见的尾部（vertical_overflow=ellipsis 截断），终端最
        低位窗口够不到新内容，12fps 自动刷新仍把近似的空面板刷满屏幕。
        根治在 _thinking_panel：去前导空白 + 无界缓冲改显式尾部窗口（永远
        显示最新一段思考），面板高度有界，Live 原地擦回不再产生空帧。
        """
        self._thinking_buf += text

        if not self._thinking_started:
            # 纯空白增量不启动：继续静默累积，等第一段可见文本到达再开面板
            if not self._thinking_buf.strip():
                return
            self._thinking_started = True
            if self._console and _HAS_RICH and self._console.is_terminal:
                self._thinking_live = Live(
                    self._thinking_panel(),
                    console=self._console,
                    refresh_per_second=12,
                    transient=False,
                    # 面板超过终端可视高度时截断为省略号，而不是 "visible"
                    # （visible 会让超高内容滚出屏幕后无法原地擦除，同样导致刷屏）
                    vertical_overflow="ellipsis",
                )
                self._thinking_live.start()
            # else: 静态模式——不建 Live，只在 _end_thinking 时打印一次
        else:
            # 流式更新：新文本到了，刷新 Live 面板（静态模式下无 Live，仅累积）
            if self._thinking_live is not None:
                self._thinking_live.update(self._thinking_panel(windowed=True))

    # Live 帧面板的最大可视行数：思考流是无限长的，必须做尾部滚动窗口，
    # 否则面板随缓冲增长超出终端高度，ellipsis 截断后终端永远显示开头段
    # （开头段常年是前导换行→空面板刷屏），且 Live 也无法原地擦回。
    _THINKING_MAX_LINES = 12

    def _thinking_panel(self, windowed: bool = False) -> Panel:
        """生成当前思考内容的 Rich Panel。

        Args:
            windowed: True=Live 帧专用，额外做尾部滚动窗口（面板高度有界，
                永远显示最新一段思考）；False=静态模式终帧，打印完整思考
                内容（一次性输出，无刷屏风险，供事后回看）。
        """
        # 去掉前导空白：思考块开头几乎总是纯换行，留着会把可见文字顶出
        # 终端可视区，渲染出一框空白（2026-10-14 空面板刷屏的真实根因）
        display = self._thinking_buf.lstrip("\n")
        if windowed:
            # 显式尾部窗口：只保留最后 _THINKING_MAX_LINES 行，面板高度有界，
            # Live 每次刷新都能看到最新文字在原地滚动
            lines = display.split("\n")
            if len(lines) > self._THINKING_MAX_LINES:
                display = "...（前略）\n" + "\n".join(lines[-self._THINKING_MAX_LINES:])
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

    def _end_thinking(self) -> None:
        """结束思考阶段。

        Live 模式：最终刷新 + 停止，面板自然保留。
        静态模式（非交互终端）：此刻才一次性打印面板，避免流式刷屏。
        """
        if not self._thinking_started:
            # 未启动（整段都是空白增量）：兜底清空缓冲，不输出任何面板
            self._thinking_buf = ""
            return
        self._thinking_started = False

        if self._thinking_live is not None:
            # 最终刷新 + 停止 Live
            self._thinking_live.update(self._thinking_panel())
            self._thinking_live.stop()
            self._thinking_live = None
        elif self._thinking_buf and self._console:
            # 静态模式：思考完整了才输出一个面板（全程只打印一次）
            self._console.print(self._thinking_panel())

        self._thinking_buf = ""

    # 代码截断：超过此行数时截断并提示
    _MAX_CODE_LINES = 30

    def tool_use(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None:
        """工具调用开始。先换行结束助手文本，再画工具框。

        语音模式：不画框，通过 TTS 播报工具用途。
        """
        if self._voice_mode:
            if self._voice_tts_feed:
                desc = _tool_voice_desc(tool_name)
                try:
                    self._voice_tts_feed(f"\n{desc}...\n")
                except Exception:
                    pass
            return

        self._end_assistant_line()
        if not self._console:
            import json
            print(f"\n[工具调用] {tool_name}: {json.dumps(tool_input, ensure_ascii=False, indent=2)}")
            return

        import json as _json
        from rich.console import Group
        from rich.syntax import Syntax
        from rich.text import Text

        # 1. 扫描参数，挑出所有代码类字段
        code_fields: list[tuple[str, str, str]] = []  # (field_name, lang, text)
        meta_input = {}
        for k, v in tool_input.items():
            if isinstance(v, str):
                lang = self._detect_code_language(v)
                if lang and ("\n" in v or len(v) > 200):
                    code_fields.append((k, lang, v))
                    # 在 JSON 里用摘要占位
                    lines = v.splitlines()
                    summary = f"<{lang} 代码, {len(lines)} 行>"
                    meta_input[k] = summary
                    continue
            meta_input[k] = v

        # 2. 元信息渲染成 JSON（代码字段已替换为摘要）
        panels_body: list = []
        if meta_input:
            meta_str = _json.dumps(meta_input, ensure_ascii=False, indent=2)
            panels_body.append(
                Syntax(meta_str, "json", theme="ansi_dark",
                       word_wrap=True, background_color="default")
            )

        # 3. 逐个渲染代码字段（截断过长代码）
        for field_name, lang, code in code_fields:
            lines = code.splitlines()
            total = len(lines)

            if panels_body:
                panels_body.append(Text(""))  # 空行分隔
            panels_body.append(
                Text(f"⤵ {field_name} ({lang}, {total} 行):", style="dim italic")
            )

            if total > self._MAX_CODE_LINES:
                truncated = "\n".join(lines[:self._MAX_CODE_LINES])
                panels_body.append(
                    Syntax(truncated, lang, theme="ansi_dark",
                           word_wrap=True, background_color="default",
                           line_numbers=True)
                )
                panels_body.append(
                    Text(f"  … +{total - self._MAX_CODE_LINES} 行", style="dim")
                )
            else:
                panels_body.append(
                    Syntax(code, lang, theme="ansi_dark",
                           word_wrap=True, background_color="default",
                           line_numbers=total > 10)
                )

        body = panels_body[0] if len(panels_body) == 1 else Group(*panels_body)
        self._console.print(
            Panel(
                body,
                title=f"🔧 {tool_name}",
                title_align="left",
                border_style="blue",
                expand=False,
            )
        )

    @staticmethod
    def _detect_code_language(text: str) -> str | None:
        """判断字符串是否是代码，返回语言名（供 Syntax 用）或 None。

        用于 tool_use: 当参数值是多行代码时，单独提取渲染。
        保守判断，避免把普通长文本误判成代码。
        """
        if not text or len(text.strip()) < 10:
            return None
        stripped = text.strip()
        low = stripped[:500].lower()
        has_newlines = "\n" in text

        # ── HTML ──
        if "<!doctype html" in low or ("<html" in low and "</html>" in text.lower()):
            return "html"
        # XML
        if stripped.startswith("<?xml"):
            return "xml"
        # Markdown（含代码围栏）
        if has_newlines and ("```" in text and any(text.count("```" + ext) for ext in
                ("python", "js", "javascript", "ts", "typescript", "html", "css", "json",
                 "bash", "sh", "sql", "java", "go", "rust", "yaml", "toml", "xml"))):
            return "markdown"

        # ── 结构化数据（需多行才判为代码） ──
        if has_newlines:
            # JSON（多行对象）
            if stripped[0] in "{[" and stripped[-1] in "}]":
                try:
                    import json as _json
                    _json.loads(text)
                    return "json"
                except Exception:
                    pass
            # YAML（以 - 或 key: 开头，多行缩进）
            if stripped[0] in "-#" or (": " in stripped and "  " in text):
                return "yaml"
            # TOML（[section] 或 key = "value"）
            if ("[" in stripped and stripped.startswith("[")) or (
                    " = " in stripped and ("[section]" in low or "title" in low)):
                return "toml"

        # ── 编程语言（关键字匹配） ──
        # Python
        py_kw = ("def ", "import ", "from ", "class ", "    return ",
                 "if __name__", "elif ", "print(", "#!/usr/bin/env python")
        if sum(1 for kw in py_kw if kw in text) >= 1:
            return "python"
        # JavaScript / TypeScript
        js_kw = ("function ", "const ", "let ", "var ", "=>",
                 "console.log", "require(", "module.exports",
                 "interface ", "type ", "export ", "import {")
        if sum(1 for kw in js_kw if kw in text) >= 1:
            # 简单区分 TS vs JS
            if any(kw in text for kw in ("interface ", ": string", ": number", ": boolean", "as ")):
                return "typescript"
            return "javascript"
        # Bash / Shell
        if any(text.startswith(prefix) for prefix in ("#!/bin/bash", "#!/bin/sh", "#!/usr/bin/env bash")):
            return "bash"
        bash_kw = ("echo ", "export ", "source ", "apt-get", "npm ", "pip ", "cd ", "ls ", "grep ")
        if sum(1 for kw in bash_kw if kw in text) >= 2 and has_newlines:
            return "bash"
        # SQL
        sql_kw = ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE",
                  "ALTER TABLE", "DROP ", "FROM ", "WHERE ", "JOIN ")
        if sum(1 for kw in sql_kw if kw.upper() in text.upper()) >= 2:
            return "sql"
        # Java
        java_kw = ("public class ", "public void ", "public static", "private ",
                   "System.out.print", "@Override", "@Autowired", "import java.")
        if sum(1 for kw in java_kw if kw in text) >= 1:
            return "java"
        # Go
        go_kw = ("package main", "func ", "fmt.Println", "import (", ":=", "go func")
        if sum(1 for kw in go_kw if kw in text) >= 1:
            return "go"
        # Rust
        rust_kw = ("fn ", "let mut ", "impl ", "pub fn ", "use std::",
                    "#[derive", "println!", "Vec<")
        if sum(1 for kw in rust_kw if kw in text) >= 1:
            return "rust"

        # ── 标记 / 模板语言 ──
        # CSS（花括号 + 属性:值 模式，不强制要求 @media）
        open_braces = stripped.count("{")
        close_braces = stripped.count("}")
        if open_braces >= 2 and close_braces >= 2 and ":" in text and ";" in text:
            if any(kw in low for kw in ("body{", ".{", "#{", "@media", "@import",
                                          "@keyframes", "display:", "margin:", "padding:")):
                return "css"
        # Vue SFC（含 <template> 和 <script>）
        if "<template" in low and "<script" in low:
            return "html"  # Rich 没有 vue lexer，用 html 最接近
        # JSX / React
        if any(pat in text for pat in ("<div>", "<span>", "<button>", "className=",
                                        "useState", "useEffect", "return (")):
            return "javascript"

        return None

    def tool_result(
        self,
        tool_name: str,
        tool_use_id: str,
        content: str,
        *,
        is_error: bool = False,
    ) -> None:
        """工具结果。"""
        style = "red" if is_error else "green"
        border = "red" if is_error else "green"
        label = f"❌ {tool_name} 结果" if is_error else f"✅ {tool_name} 结果"
        # 截断过长结果（UI 展示用，回传给 LLM 的不截断）
        display = content if len(content) <= 2000 else content[:1000] + "\n... [截断] ...\n" + content[-500:]
        if self._console:
            from rich.syntax import Syntax
            lexer = self._detect_lexer(display)
            body = Syntax(
                display, lexer, theme="ansi_dark",
                word_wrap=True, background_color="default",
            ) if lexer else Text(display, style=style)
            self._console.print(
                Panel(
                    body,
                    title=label,
                    title_align="left",
                    border_style=border,
                    expand=False,
                )
            )
        else:
            print(f"[{label}]\n{display}")

    @staticmethod
    def _detect_lexer(text: str) -> str | None:
        """判断结果文本的语言/格式，供 Syntax 高亮用。

        判断顺序（避免误判）:
        1. JSON: 以 { 或 [ 开头且能解析
        2. HTML: 含 <html 或 <!DOCTYPE
        3. XML: 以 <?xml 或 < 开头成对标签
        4. Python/JS 等代码: 含典型关键字（保守，避免误判普通文本）
        5. 命令行输出 / diff / 其他
        6. 否则返回 None，退回纯文本渲染
        """
        s = text.lstrip()
        if not s:
            return None
        # JSON
        if s[0] in "{[":
            import json as _json
            try:
                _json.loads(text)
                return "json"
            except Exception:
                pass
        low = s[:300].lower()
        # HTML
        if "<!doctype html" in low or "<html" in low:
            return "html"
        # XML
        if s.startswith("<?xml") or (s.startswith("<") and "</" in s):
            return "xml"
        # Python（多行，含 python 关键字）
        if "\n" in text and any(kw in text for kw in
                ("def ", "import ", "class ", "    return ", "Traceback", "File \"")):
            return "python"
        # Bash / Shell（命令输出常见格式）
        if any(kw in text for kw in ("$ ", "Usage: ", "error: ", "warning: ")):
            return "bash"
        # Diff
        if text.startswith("---") or text.startswith("+++"):
            return "diff"
        # Markdown
        if text.startswith("# ") or "```" in text:
            return "markdown"
        return None

    def info(self, text: str) -> None:
        self._end_assistant_line()
        if self._console:
            self._console.print(text, style="dim")
        else:
            print(f"[info] {text}")

    def notify_reminder(self, text: str) -> None:
        """从后台线程（Scheduler 到期回调）安全播报一条定时提醒。

        与 info()/warn() 的关键区别：本方法极可能在 prompt_toolkit 正持有
        屏幕时被调用——直接 print 会把输入行顶掉、文字叠在提示符上。因此
        打印后调用 app.invalidate()，让 prompt_toolkit 把提示符重绘到输出
        下方；无 PT 会话（非交互 / 管道）时退化为普通打印。

        绝不抛异常：调用方是调度器后台线程，那里一次 UI 故障不能拖停后续
        所有提醒。宿主 CliReminderHost 另有兜底。

        @author aceFelix
        """
        try:
            self._end_assistant_line()
            if self._console:
                # 前置换行：确保提醒另起一行，不与正在输入的行共用行底
                self._console.print(f"\n⏰ {escape(text)}", style="bold cyan")
            else:
                print(f"\n[提醒] {text}")
            session = getattr(self, "_pt_session", None)
            app = getattr(session, "app", None) if session is not None else None
            if app is not None:
                app.invalidate()
        except Exception:
            pass

    def warn(self, text: str) -> None:
        """黄字警告。text 是外部动态文本（子命令 stderr、异常消息等），
        必须先 escape 再拼标记——否则 `[=<n>[/<m>]]` 这类内容会被 Rich
        当标记解析直接抛 MarkupError（同 ask_user 的方括号吞字问题，
        /diff 非 git 目录崩溃案，2026-10）。
        @author aceFelix"""
        self._end_assistant_line()
        if self._console:
            self._console.print(f"⚠️  {escape(text)}", style="yellow")
        else:
            print(f"[warn] {text}")

    def error(self, text: str) -> None:
        """红字错误。同 warn：text 含异常/子进程输出等不可控内容，
        先 escape 防止 Rich 标记解析崩溃。@author aceFelix"""
        self._end_assistant_line()
        if self._console:
            self._console.print(f"❌ {escape(text)}", style="bold red")
        else:
            print(f"[error] {text}", file=sys.stderr)

    def ask_user(self, prompt: str) -> str:
        """阻塞式询问。

        prompt 是外部动态文本（工具参数 JSON、权限 reason 等），必须先经
        markup.escape 转义再拼进 Rich 标记——否则 Rich 会把 [y]/[a] 这类
        方括号内容当作样式标签整体吞掉，导致确认提示行的按键名丢失；
        JSON 数组的方括号同样会被误解析。

        双冒号消除：Prompt.ask 渲染时会自动追加 ": " 后缀（且旧版/新版
        rich 的 prompt_suffix 参数签名不兼容，不能显式传），故先去掉
        prompt 自带的结尾 ": "，由默认后缀补回，任意 rich 版本显示一致。
        @author aceFelix
        """
        self._end_assistant_line()
        if self._console:
            text = prompt[:-2] if prompt.endswith(": ") else prompt
            return Prompt.ask(
                "[bold magenta]❓ " + escape(text) + "[/bold magenta]",
                console=self._console,
            )
        return input(prompt + " ")

    # ---- RealtimeTalkUI 扩展实现 ----
    # 当 --talk 未安装 pywebview 时回退到 RichCLI，以下方法把实时对话事件
    # 以终端友好的方式打印出来。

    def on_status(self, status: str) -> None:
        """实时对话状态变化。"""
        labels = {
            "connecting": "🟡 连接中...",
            "standby": "🟢 待命",
            "listening": "🎙️  聆听中...",
            "speaking": "🔊 贾维斯说话中...",
            "error": "🔴 连接异常",
        }
        if self.verbose and status in labels:
            self.info(labels[status])

    def on_volume(self, level: float) -> None:
        """麦克风音量级别（verbose 模式下显示进度条）。"""
        if self.verbose:
            bars = int(level * 20)
            self.info("音量: [" + "█" * bars + "░" * (20 - bars) + f"] {level:.0%}")

    def on_user_speaking(self, speaking: bool) -> None:
        """用户开始/停止说话。"""
        if self.verbose:
            self.info("用户" + ("开始" if speaking else "停止") + "说话")

    def on_ai_speaking(self, speaking: bool) -> None:
        """AI 开始/停止说话。"""
        if self.verbose:
            self.info("贾维斯" + ("开始" if speaking else "停止") + "说话")

    def on_user_transcript(self, text: str) -> None:
        """用户语音转录完成——在终端中显示为对话气泡样式。"""
        self.info(f"\n🧑 你: {text}")

    def on_ai_transcript(self, text: str) -> None:
        """AI 语音转录完成——在终端中显示为对话气泡样式。"""
        self.info(f"\n🤖 贾维斯: {text}")

    def is_running(self) -> bool:
        """终端 UI 始终认为自己在运行。"""
        return True

    # ---- REPL 入口 ----

    def read_user_input(self, prompt_str: str = "> ") -> str:
        """读取用户输入（同步版，用于无事件循环的上下文如 daemon 文本 REPL）。

        当 prompt_toolkit 可用时，提供 Tab 补全和 / 命令列表。
        否则回退到 input()。

        注意: 此方法直接调用 prompt()，要求调用时没有正在运行的事件循环。
        如果在 async 上下文中（如 main.py 的 repl()），请改用
        read_user_input_async()，它使用 prompt_async() 在主线程中运行，
        避免 Windows IME（中文输入法）组合字符（如""）无法输入的问题。

        EOFError / KeyboardInterrupt 不在此层捕获，向上传播到 REPL 循环
        统一处理（break 退出）。
        """
        self._end_assistant_line()
        if self._pt_session is not None:
            return self._pt_session.prompt(prompt_str)
        return input(prompt_str)

    async def read_user_input_async(self, prompt_str: str = "> ") -> str:
        """读取用户输入（异步版，用于 async 上下文如 main.py 的 repl()）。

        使用 prompt_toolkit 的 prompt_async()，在主线程 + 现有事件循环中
        运行，无需额外线程。这样 Windows IME（中文输入法）的组合事件能
        正常传递给 prompt_toolkit，解决""等中文标点无法输入的问题。

        EOFError / KeyboardInterrupt 不在此层捕获，向上传播到 REPL 循环。
        """
        self._end_assistant_line()
        if self._pt_session is not None:
            return await self._pt_session.prompt_async(prompt_str)
        return input(prompt_str)

    def banner(self, provider: str, model: str, workdir: str) -> None:
        """启动横幅。优先播放 JARVIS 方舟反应炉动画，不支持则回退到 Panel。"""
        if not self._console:
            print(f"jarvis | provider={provider} model={model} workdir={workdir}")
            return
        if self._boot_animation:
            try:
                from agent.ui.boot_animation import play_boot_animation

                if play_boot_animation(self._console, provider, model, workdir):
                    return
            except Exception:
                pass  # 动画失败 → 回退到简单 Panel
        # 回退：简单 Panel
        self._console.print(
            Panel(
                f"[bold]jarvis[/bold] v0.1\n"
                f"provider: [cyan]{provider}[/cyan]  model: [cyan]{model}[/cyan]\n"
                f"workdir:  [cyan]{workdir}[/cyan]\n"
                f"输入 [magenta]/help[/magenta] 查看命令，[magenta]/voice[/magenta] 语音对话，"
                f"[magenta]/talk[/magenta] 实时聊天，[magenta]/exit[/magenta] 退出",
                border_style="green",
                expand=False,
            )
        )

    def goodbye(self) -> None:
        if self._console:
            self._console.print("[dim]再见。[/dim]")
        else:
            print("再见。")

    # ---- 内部 ----

    def _end_assistant_line(self) -> None:
        """结束当前助手流式输出。

        先收尾思考阶段（思考→直接工具调用时没有 assistant_text 触发
        _end_thinking，若不在此处收口，Live 面板会残留且缓冲跨轮累积），
        再为正文补一个换行。
        """
        self._end_thinking()
        if self._assistant_buf:
            if self._console:
                self._console.print()  # 补换行
            else:
                print()
            self._assistant_buf = ""
