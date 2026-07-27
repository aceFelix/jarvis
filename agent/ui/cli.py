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
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.text import Text

    _HAS_RICH = True
except ImportError:  # pragma: no cover
    _HAS_RICH = False

# prompt_toolkit（可选，用于 Tab 补全和 / 命令列表）
try:
    from prompt_toolkit.completion import Completer, Completion, CompleteEvent
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import FormattedText
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
        """Shift+Enter 换行、Enter 提交。"""
        kb = KeyBindings()

        @kb.add("escape", "enter")
        def _(event):
            """Shift+Enter / Escape+Enter: 在光标处插入换行符。"""
            event.current_buffer.insert_text("\n")

        return kb

    # Claude Code 风格补全样式：黑色背景 + 亮色命令名 + 可读描述 + 选中高亮
    _PT_STYLE = Style.from_dict({
        # 菜单整体：透明黑底，细边框
        "completion-menu":                    "bg:#000000 border:#333333",
        # 普通项：无额外背景（继承终端黑色）
        "completion-menu.completion":         "",
        # 选中项：深蓝灰底 + 白字（类似 Claude Code 的选中效果）
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

    # 无 prompt_toolkit 时提供哑元类，让 _SlashCompleter 能声明
    class Completer:
        pass
    class Completion:  # type: ignore[no-redef]
        pass
    class CompleteEvent:  # type: ignore[no-redef]
        pass


# ---- 斜杠命令注册表 ----

#: 全部可用命令 (name, description) 列表。main.py / daemon.py 的 REPL 循环按此列表补全。
SLASH_COMMANDS = [
    ("/exit",       "退出贾维斯"),
    ("/quit",       "退出贾维斯"),
    ("/help",       "查看所有命令帮助"),
    ("/h",          "查看帮助（/help 简写）"),
    ("/mode <m>",   "切换权限模式 (default/plan/accept_edits/yolo)"),
    ("/model <前缀>","前缀匹配切换模型（支持模糊输入）"),
    ("/models",     "交互式选择或添加模型"),
    ("/reset",      "清空对话历史，重新开始"),
    ("/clear",      "清空对话历史（/reset 别名）"),
    ("/compact",    "手动压缩上下文（摘要旧消息节省 token）"),
    ("/save [name]","保存当前会话"),
    ("/load <前缀>","前缀匹配加载会话（支持模糊输入）"),
    ("/loads",      "列出并交互选择已保存会话"),
    ("/sessions",   "列出已保存会话（/loads 别名）"),
    ("/memory",     "查看长期记忆文件内容"),
    ("/skills",     "列出已加载的技能包"),
    ("/agents",     "查看多 Agent 团队状态与成员"),
    ("/tasks",      "查看共享任务列表进度"),
    ("/plan",       "切换规划模式（进入/退出只读规划）"),
    ("/think",      "开关深度思考模式（/think on|off）"),
    ("/mcp",        "查看 MCP server 连接状态与工具"),
    ("/tools",      "列出可用工具列表"),
    ("/image <path>", "添加本地图片到待发送列表（下条消息附带）"),
    ("/img <path>",   "添加本地图片（/image 别名）"),
    ("/paste",        "添加剪贴板图片到待发送列表（下条消息附带）"),
    ("/p",            "添加剪贴板图片（/paste 别名）"),
    ("/say <text>", "用 TTS 语音朗读一段文字"),
    ("/listen",     "录音并识别成文字（麦克风→STT→文本）"),
    ("/mic",        "录音并识别文字（/listen 别名）"),
    ("/voice",      "进入语音对话模式（连续听→想→说循环）"),
    ("/talk",       "进入实时双工语音对话（全双工，说话即可打断）"),
    ("/connect-phone", "手机扫码连接 JARVIS（共享当前会话）"),
    ("/connect-wechat", "微信扫码连接 JARVIS（通过 ClawBot 在微信中对话）"),
    ("/disconnect-wechat", "断开微信 ClawBot 连接"),
    ("/plugin",                 "列出已安装插件（Plugin 系统）"),
    ("/plugin install <名称>",  "安装 Plugin 系统的插件"),
    ("/plugin uninstall <名称>","卸载 Plugin 系统的插件"),
    ("/plugin search <关键词>", "搜索 Plugin 系统市场"),
    ("/plugin info <名称>",     "查看 Plugin 插件详情"),
    ("/plugin update",          "检查 Plugin 插件更新"),
    ("/plugin enable <名称>",   "启用被禁用的插件（通用）"),
    ("/plugin disable <名称>",  "禁用插件，不卸载（通用）"),
    ("/plugin create <名称>",   "创建新插件脚手架（--type harness|plugin）"),
    ("/plugin validate <路径>", "校验 plugin.json / SKILL.md（通用）"),
    ("/cli_anything",           "列出已安装 CLI-Anything harness"),
    ("/cli_anything list",      "列出已安装 CLI-Anything harness"),
    ("/cli_anything market",    "列出市场可用 harness"),
    ("/cli_anything install <id>",  "安装指定 harness"),
    ("/cli_anything uninstall <id>","卸载指定 harness"),
]

# 工具名 → 语音播报描述（方言/中文，适合 TTS 朗读）
_TOOL_VOICE_DESC: dict[str, str] = {
    "Bash": "正在执行命令",
    "Glob": "正在搜索文件",
    "Grep": "正在搜索代码",
    "Read": "正在读取文件",
    "Edit": "正在编辑文件",
    "Write": "正在写入文件",
    "ScreenShot": "正在截图",
    "CameraShot": "正在拍照",
    "WebFetch": "正在访问网页",
    "WebSearch": "正在搜索网络",
    "TodoWrite": "正在整理计划",
    "Subagent": "正在派生子代理协助",
    "ListCameras": "正在检测摄像头",
    "VisionWatch": "正在开启视觉监控",
}


def _tool_voice_desc(tool_name: str) -> str:
    """工具名的中文语音描述。未知工具返回默认短语。"""
    return _TOOL_VOICE_DESC.get(tool_name, "正在处理")


class _SlashCompleter(Completer):
    """斜杠命令自动补全器。

    输入 '/' 时弹出全部命令及描述，类似 Claude Code 的 / 命令列表。
    输入 '/v' 后只匹配 voice 等前缀命中的项。

    子命令补全:
    - /mode <prefix> → 匹配权限模式 (default/plan/accept_edits/yolo)
    - /model <prefix> → 匹配模型名（内置 + 自定义）
    - /load <prefix> → 匹配已保存会话名
    - /save <prefix> → 匹配已保存会话名

    显示风格（仿 Claude Code）:
      /exit          退出贾维斯  ← 命令名亮青蓝 + 描述浅灰
    选中项：深蓝灰底 + 白/浅蓝字（高对比，一眼可见）
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> list[Completion]:
        text_before_cursor = document.text_before_cursor.lstrip()
        if not text_before_cursor.startswith("/"):
            return []

        text_lower = text_before_cursor.lower()

        # ---- /mode <prefix> 子命令补全 ----
        if text_lower.startswith("/mode ") or text_before_cursor.startswith("/mode "):
            return self._complete_modes(text_before_cursor)

        # ---- /model <prefix> 子命令补全 ----
        if text_lower.startswith("/model ") or text_before_cursor.startswith("/model "):
            return self._complete_models(text_before_cursor)

        # ---- /load <prefix> 子命令补全 ----
        if text_lower.startswith("/load ") or text_before_cursor.startswith("/load "):
            return self._complete_sessions(text_before_cursor, prefix_cmd="/load ")

        # ---- /save <prefix> 子命令补全 ----
        if text_lower.startswith("/save ") or text_before_cursor.startswith("/save "):
            return self._complete_sessions(text_before_cursor, prefix_cmd="/save ")

        # ---- 默认：斜杠命令补全 + 技能补全 ----
        word = text_lower
        results: list[Completion] = []
        for cmd_name, desc in SLASH_COMMANDS:
            if cmd_name.lower().startswith(word):
                display_text = f"{cmd_name:<16}  {desc}"
                results.append(
                    Completion(
                        text=cmd_name,
                        start_position=-len(word),
                        display=FormattedText([
                            ("#5bc8ff", f"{cmd_name:<16}"),
                            ("#aaaaaa", f"  {desc}"),
                        ]),
                    )
                )

        # 技能名补全（每个 skill 也作为 /<skill-name> 使用）
        try:
            from agent.core.extensions.skills import load_skills
            import os
            workdir = os.getcwd()
            for skill in load_skills(workdir):
                skill_cmd = f"/{skill.name}"
                if skill_cmd.lower().startswith(word):
                    desc = skill.description or "技能包"
                    results.append(
                        Completion(
                            text=skill_cmd,
                            start_position=-len(word),
                            display=FormattedText([
                                ("#5bc8ff", f"{skill_cmd:<16}"),
                                ("#aaaaaa", f"  {desc}"),
                            ]),
                        )
                    )
        except Exception:
            pass

        return results

    @staticmethod
    def _complete_modes(text: str) -> list[Completion]:
        """补全 /mode <prefix> 的权限模式名。"""
        # 4 种权限模式及描述
        all_modes: list[tuple[str, str]] = [
            ("default",      "默认：写操作需确认，危险命令拒绝"),
            ("plan",         "规划：只读规划，拒绝所有写操作"),
            ("accept_edits", "接受编辑：文件编辑自动放行，其他需确认"),
            ("yolo",         "全自动：自动放行所有操作（危险命令除外）"),
        ]

        prefix = text[len("/mode "):].lower()
        matches = [(n, d) for n, d in all_modes if n.lower().startswith(prefix)]

        results: list[Completion] = []
        for name, desc in matches:
            start_pos = -len(prefix) if prefix else 0
            results.append(
                Completion(
                    text=name,
                    start_position=start_pos,
                    display=FormattedText([
                        ("#5bc8ff", f"{name:<16}"),
                        ("#888888", f"  {desc}"),
                    ]),
                )
            )
        return results

    @staticmethod
    def _complete_models(text: str) -> list[Completion]:
        """补全 /model <prefix> 的模型名。"""
        import os
        import sys

        # 提取 /model 后的参数部分
        prefix = text[len("/model "):].lower()

        # 加载模型列表（懒加载，避免循环导入）
        all_models: list[tuple[str, str]] = []  # [(name, desc), ...]
        try:
            from agent.config.settings import load_settings
            settings = load_settings()
            for name, desc in settings.models.items():
                all_models.append((name, desc))
            for name, cfg in settings.custom_models.items():
                if isinstance(cfg, dict):
                    mtype = cfg.get("model_type", "multimodal")
                    tag = "纯文本" if mtype == "text" else "多模态"
                    all_models.append((name, f"[{tag}] {cfg.get('name', name)}"))
        except Exception:
            pass

        # 前缀过滤
        matches = [(n, d) for n, d in all_models if n.lower().startswith(prefix)]

        results: list[Completion] = []
        for name, desc in matches:
            start_pos = -len(prefix) if prefix else 0
            results.append(
                Completion(
                    text=name,
                    start_position=start_pos,
                    display=FormattedText([
                        ("#5bc8ff", f"{name:<24}"),
                        ("#888888", f"  {desc}"),
                    ]),
                )
            )
        return results

    @staticmethod
    def _complete_sessions(text: str, prefix_cmd: str) -> list[Completion]:
        """补全 /load <prefix> 或 /save <prefix> 的会话名。"""
        prefix = text[len(prefix_cmd):].lower()

        all_sessions: list[tuple[str, str]] = []  # [(name, desc), ...]
        try:
            from agent.core.memory.store import list_sessions
            for s in list_sessions():
                desc = f"{s.message_count} 条消息 | {s.workdir or '(无)'}"
                all_sessions.append((s.name, desc))
        except Exception:
            pass

        matches = [(n, d) for n, d in all_sessions if n.lower().startswith(prefix)]

        results: list[Completion] = []
        for name, desc in matches:
            start_pos = -len(prefix) if prefix else 0
            results.append(
                Completion(
                    text=name,
                    start_position=start_pos,
                    display=FormattedText([
                        ("#5bc8ff", f"{name:<24}"),
                        ("#888888", f"  {desc}"),
                    ]),
                )
            )
        return results


class RichCLI(UIProtocol):
    """基于 Rich 的命令行 UI。"""

    def __init__(self, *, verbose: bool = False, boot_animation: bool = True) -> None:
        self.verbose = verbose
        self._boot_animation = boot_animation
        if _HAS_RICH:
            self._console = Console()
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

        文本/语音模式统一用 Rich Live + Panel 显示思考过程。
        语音模式下 ThinkingDelta 不触发 on_assistant_text，TTS 不会朗读思考，
        所以画 Panel 不影响语音播报。
        """
        self._thinking_buf += text

        if not self._thinking_started:
            self._thinking_started = True
            if self._console and _HAS_RICH:
                self._thinking_live = Live(
                    self._thinking_panel(),
                    console=self._console,
                    refresh_per_second=12,
                    transient=False,
                    vertical_overflow="visible",
                )
                self._thinking_live.start()
        else:
            # 流式更新：新文本到了，刷新 Live 面板
            if self._thinking_live is not None:
                self._thinking_live.update(self._thinking_panel())

    def _thinking_panel(self) -> Panel:
        """生成当前思考内容的 Rich Panel。"""
        display = self._thinking_buf
        if len(display) > 1200:
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
        """结束思考阶段，停止 Live，面板自然保留。"""
        if not self._thinking_started:
            return
        self._thinking_started = False

        if self._thinking_live is not None:
            # 最终刷新 + 停止 Live
            self._thinking_live.update(self._thinking_panel())
            self._thinking_live.stop()
            self._thinking_live = None

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

    def warn(self, text: str) -> None:
        self._end_assistant_line()
        if self._console:
            self._console.print(f"⚠️  {text}", style="yellow")
        else:
            print(f"[warn] {text}")

    def error(self, text: str) -> None:
        self._end_assistant_line()
        if self._console:
            self._console.print(f"❌ {text}", style="bold red")
        else:
            print(f"[error] {text}", file=sys.stderr)

    def ask_user(self, prompt: str) -> str:
        """阻塞式询问。"""
        self._end_assistant_line()
        if self._console:
            return Prompt.ask("[bold magenta]❓ " + prompt + "[/bold magenta]", console=self._console)
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
        """结束当前助手文本流（补一个换行）。"""
        if self._assistant_buf:
            if self._console:
                self._console.print()  # 补换行
            else:
                print()
            self._assistant_buf = ""
