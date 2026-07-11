"""prompt_toolkit 终端内联选择器 + 文本输入。

类似 Claude Code 的 Ink Select / Ink TextInput 组件，在同一个终端窗口内显示，
不弹独立窗口。

用法:
    # 选择列表
    items = [(value, label, description), ...]
    result = pick_from_list(items, current="qwen3.7-plus")

    # 文本输入
    text = input_text(title="请输入模型名", placeholder="例如 gpt-4o")
"""

from __future__ import annotations

import concurrent.futures
from typing import Any


def pick_from_list(
    items: list[tuple[str, str, str]],
    *,
    title: str = "选择",
    current: str = "",
    space_tags: set[str] | None = None,
) -> str | None:
    """在终端内弹出内联选择列表。

    Args:
        items: [(value, label, description), ...]
        title: 标题文字
        current: 当前值（高亮标注）
        space_tags: 空格键触发特殊操作的值集合（返回 "__SPACE__<value>" 而非 value）

    Returns:
        选中的 value，取消返回 None。
        当 space_tags 非空且空格键命中时返回 "__SPACE__<value>"。
    """
    if not items:
        return None

    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    selected_idx = [0]
    cancelled = [False]

    # 预选当前项
    for i, (val, _, _) in enumerate(items):
        if val == current:
            selected_idx[0] = i
            break

    kb = KeyBindings()

    @kb.add("up")
    def _up(event):
        selected_idx[0] = (selected_idx[0] - 1) % len(items)

    @kb.add("down")
    def _down(event):
        selected_idx[0] = (selected_idx[0] + 1) % len(items)

    @kb.add("enter")
    def _enter(event):
        event.app.exit(result=items[selected_idx[0]][0])

    @kb.add("space")
    def _space(event):
        val = items[selected_idx[0]][0]
        if space_tags and val in space_tags:
            event.app.exit(result=f"__SPACE__{val}")

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event):
        cancelled[0] = True
        event.app.exit(result=None)

    @kb.add("home")
    def _home(event):
        selected_idx[0] = 0

    @kb.add("end")
    def _end(event):
        selected_idx[0] = len(items) - 1

    def _get_text():
        hint = "↑↓ 移动  Enter 确认  Esc 取消"
        if space_tags:
            hint += "  Space 更多操作"
        lines: list[tuple[str, str]] = [
            ("", "\n"),
            ("bold #5bc8ff", f"  ◆ {title}"),
            ("dim", f"  ({hint})"),
            ("", "\n\n"),
        ]
        for i, (val, label, desc) in enumerate(items):
            is_current = val == current
            is_selected = i == selected_idx[0]

            if is_current and is_selected:
                prefix = "  ●"
                style = "bold #5bc8ff"
            elif is_current:
                prefix = "  ○"
                style = "#5bc8ff"
            elif is_selected:
                prefix = "  ›"
                style = "bold #ffffff"
            else:
                prefix = "   "
                style = ""

            lines.append((style, f"{prefix} {label:<24}"))
            lines.append(("dim #888888", f"  {desc}"))
            if is_current:
                lines.append(("italic #5bc8ff", "  当前"))
            lines.append(("", "\n"))

        lines.append(("", "\n"))
        return lines

    content = FormattedTextControl(_get_text)
    root = HSplit([Window(content)])

    style = Style.from_dict({
        "window": "bg:#0a0e14",
    })

    app = Application(
        layout=Layout(root),
        key_bindings=kb,
        style=style,
        full_screen=True,
    )

    # Application.run() 内部调 asyncio.run()，不能在已有事件循环里直接调。
    # 用线程池跑——新线程无事件循环，asyncio.run() 正常工作。
    result: list[Any] = [None]

    def _run() -> None:
        result[0] = app.run()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result()

    if cancelled[0]:
        return None
    return result[0]


def input_text(
    *,
    title: str = "输入",
    placeholder: str = "",
    default: str = "",
    password: bool = False,
    allow_empty: bool = True,
) -> str | None:
    """在终端内弹出内联文本输入框。

    Args:
        title: 提示文字
        placeholder: 占位文字（灰色显示，未输入时）
        default: 默认值（Enter 直接确认时返回）
        password: 密码模式（输入字符显示为 *）
        allow_empty: 是否允许空输入（False 时空输入按 Enter 无效）

    Returns:
        输入的文本，取消返回 None。
    """
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    text_buffer = [default]
    cancelled = [False]

    kb = KeyBindings()

    @kb.add("enter")
    def _enter(event):
        if not allow_empty and not text_buffer[0].strip():
            return  # 不允许空输入时忽略 Enter
        event.app.exit(result=text_buffer[0])

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event):
        cancelled[0] = True
        event.app.exit(result=None)

    @kb.add("backspace")
    def _backspace(event):
        text_buffer[0] = text_buffer[0][:-1]

    # 处理可打印字符
    @kb.add("<any>")
    def _any(event):
        if event.data and len(event.data) == 1 and event.data.isprintable():
            text_buffer[0] += event.data

    def _get_text():
        lines: list[tuple[str, str]] = [
            ("", "\n"),
            ("bold #5bc8ff", f"  ◆ {title}"),
            ("dim", "  (输入后 Enter 确认  Esc 取消)"),
            ("", "\n\n"),
        ]

        current = text_buffer[0]
        if current:
            if password:
                display = "*" * len(current)
            else:
                display = current
            lines.append(("bold #ffffff", f"  {display}"))
        elif placeholder:
            lines.append(("dim #666666", f"  {placeholder}"))
        else:
            lines.append(("dim #666666", "  _"))

        lines.append(("", "\n\n"))
        return lines

    content = FormattedTextControl(_get_text)
    root = HSplit([Window(content)])

    style = Style.from_dict({
        "window": "bg:#0a0e14",
    })

    app = Application(
        layout=Layout(root),
        key_bindings=kb,
        style=style,
        full_screen=True,
    )

    result: list[Any] = [None]

    def _run() -> None:
        result[0] = app.run()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result()

    if cancelled[0]:
        return None
    return result[0]


def form_input(
    *,
    title: str = "填写表单",
    fields: list[dict],
) -> dict[str, str | None] | None:
    """在终端内弹出内联表单——多个字段在同一屏输入。

    类似 Claude Code 的 Ink Form，支持三种字段类型:
    - text: 普通文本输入
    - password: 密码模式（输入显示为 *）
    - select: 选择列表（← → 切换，不占额外屏幕）

    Args:
        title: 表单标题
        fields: [{"name": "模型名", "type": "text", "placeholder": "...", "default": ""},
                 {"name": "接口类型", "type": "select", "options": [("openai", "OpenAI 兼容"), ...], "default": "openai"},
                 ...]

    Returns:
        {field_name: value} 字典，取消返回 None。
    """
    try:
        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout, HSplit, Window
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    # ---- 构建字段状态 ----
    field_state: list[dict] = []
    for f in fields:
        ftype = f.get("type", "text")
        fs = {"name": f["name"], "type": ftype}
        if ftype == "select":
            options = f.get("options", [])
            default = f.get("default", options[0][0] if options else "")
            idx = 0
            for i, (v, _) in enumerate(options):
                if v == default:
                    idx = i
                    break
            fs["options"] = options
            fs["index"] = idx
        else:
            fs["buffer"] = f.get("default", "")
            fs["placeholder"] = f.get("placeholder", "")
        field_state.append(fs)

    focused = [0]
    cancelled = [False]

    kb = KeyBindings()

    @kb.add("tab")
    def _tab(event):
        focused[0] = (focused[0] + 1) % len(field_state)

    @kb.add("s-tab")
    def _s_tab(event):
        focused[0] = (focused[0] - 1) % len(field_state)

    @kb.add("down")
    def _down(event):
        focused[0] = (focused[0] + 1) % len(field_state)

    @kb.add("up")
    def _up(event):
        focused[0] = (focused[0] - 1) % len(field_state)

    @kb.add("left")
    def _left(event):
        fs = field_state[focused[0]]
        if fs["type"] == "select" and fs["options"]:
            fs["index"] = (fs["index"] - 1) % len(fs["options"])

    @kb.add("right")
    def _right(event):
        fs = field_state[focused[0]]
        if fs["type"] == "select" and fs["options"]:
            fs["index"] = (fs["index"] + 1) % len(fs["options"])

    @kb.add("backspace")
    def _backspace(event):
        fs = field_state[focused[0]]
        if fs["type"] in ("text", "password"):
            fs["buffer"] = fs["buffer"][:-1]

    @kb.add("<any>")
    def _any(event):
        if event.data and len(event.data) == 1 and event.data.isprintable():
            fs = field_state[focused[0]]
            if fs["type"] in ("text", "password"):
                fs["buffer"] += event.data

    @kb.add("space")
    def _space(event):
        fs = field_state[focused[0]]
        if fs["type"] in ("text", "password"):
            fs["buffer"] += " "

    @kb.add("enter")
    def _enter(event):
        # 最后一个字段：提交。否则跳下一字段（类标准表单行为）
        if focused[0] == len(field_state) - 1:
            event.app.exit(result=True)
        else:
            focused[0] = (focused[0] + 1) % len(field_state)

    @kb.add("escape")
    @kb.add("c-c")
    def _cancel(event):
        cancelled[0] = True
        event.app.exit(result=None)

    def _get_text():
        lines: list[tuple[str, str]] = [
            ("", "\n"),
            ("bold #5bc8ff", f"  ◆ {title}"),
            ("dim", "  (Tab 切换字段  Enter 下一项/提交  Esc 取消)"),
            ("", "\n\n"),
        ]
        for i, fs in enumerate(field_state):
            is_focused = i == focused[0]
            label = fs["name"]
            ftype = fs["type"]

            if is_focused:
                lines.append(("bold #5bc8ff", f"  › {label}"))
            else:
                lines.append(("", f"    {label}"))
            lines.append(("", "\n"))

            if ftype == "select":
                options = fs.get("options", [])
                idx = fs.get("index", 0)
                if options:
                    label_text = options[idx][1] if idx < len(options) else "?"
                    if is_focused:
                        lines.append(("bold #ffffff", f"      ◀ {label_text} ▶"))
                    else:
                        lines.append(("dim #888888", f"      {label_text}"))
                else:
                    lines.append(("dim #444444", "      （无选项）"))
            else:
                buf = fs["buffer"]
                placeholder = fs.get("placeholder", "")
                if buf:
                    if ftype == "password":
                        display = "*" * len(buf)
                    else:
                        display = buf
                    if is_focused:
                        lines.append(("bold #ffffff", f"      {display}│"))
                    else:
                        lines.append(("", f"      {display}"))
                elif placeholder:
                    lines.append(("dim #666666", f"      {placeholder}"))
                else:
                    if is_focused:
                        lines.append(("bold #666666", "      │"))
                    else:
                        lines.append(("dim #444444", "      _"))
            lines.append(("", "\n"))

        lines.append(("", "\n"))
        lines.append(("dim", "  Enter 提交（最后一栏）  Esc 取消"))
        lines.append(("", "\n"))
        return lines

    content = FormattedTextControl(_get_text)
    root = HSplit([Window(content)])

    style = Style.from_dict({
        "window": "bg:#0a0e14",
    })

    app = Application(
        layout=Layout(root),
        key_bindings=kb,
        style=style,
        full_screen=True,
    )

    def _run() -> None:
        app.run()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result()

    if cancelled[0]:
        return None

    result: dict[str, str | None] = {}
    for fs in field_state:
        if fs["type"] == "select":
            options = fs.get("options", [])
            idx = fs.get("index", 0)
            result[fs["name"]] = options[idx][0] if idx < len(options) else None
        else:
            result[fs["name"]] = fs["buffer"].strip() or None

    return result
