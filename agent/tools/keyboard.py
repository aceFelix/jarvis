"""键盘控制工具 —— 模拟键盘输入。

阶段二「电脑操作能力」的另一半。让模型能输入文字、按快捷键，配合鼠标工具
实现"在那个输入框里打 hello""按 Ctrl+S 保存"这类指令。

技术要点:
- pyautogui.write() 只支持 ASCII。中文/非 ASCII 文本走剪贴板粘贴:
  优先用 pyperclip，未安装则在 Windows 上回退到 ctypes 操作剪贴板，
  都不可用则报错（提示装 pyperclip）。注意粘贴会覆盖原剪贴板内容。
- KeyTap 支持单键(press)和组合键(hotkey): 传一个 key 即按单键，
  传多个 key 即按组合键（如 ["ctrl","c"]）。

权限: 所有键盘操作默认 ASK。
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, PermissionMatcher, Tool


def _import_pyautogui():
    import pyautogui  # type: ignore[import-untyped]
    return pyautogui


def _is_ascii(text: str) -> bool:
    """是否纯 ASCII（可被 pyautogui.write 直接输入）。"""
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _set_clipboard(text: str) -> str | None:
    """把文本写入系统剪贴板。返回 None 成功，否则返回错误说明。

    优先 pyperclip，回退 Windows ctypes。都不行返回错误。
    """
    try:
        import pyperclip  # type: ignore[import-untyped]
        pyperclip.copy(text)
        return None
    except ImportError:
        pass

    # Windows ctypes 回退
    import platform
    if platform.system() == "Windows":
        try:
            import ctypes
            import ctypes.wintypes as w

            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            user32.OpenClipboard.argtypes = [w.HWND]
            user32.OpenClipboard.restype = w.BOOL
            user32.EmptyClipboard.restype = w.BOOL
            user32.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
            user32.SetClipboardData.restype = w.HANDLE
            user32.CloseClipboard.restype = w.BOOL
            kernel32.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
            kernel32.GlobalAlloc.restype = w.HANDLE
            kernel32.GlobalLock.argtypes = [w.HANDLE]
            kernel32.GlobalLock.restype = w.LPVOID
            kernel32.GlobalUnlock.argtypes = [w.HANDLE]

            GMEM_MOVEABLE = 0x0002
            data = text + "\0"
            buf = data.encode("utf-16-le")
            h_global = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buf))
            if not h_global:
                return "GlobalAlloc 失败"
            locked = kernel32.GlobalLock(h_global)
            if not locked:
                return "GlobalLock 失败"
            ctypes.memmove(locked, buf, len(buf))
            kernel32.GlobalUnlock(h_global)

            if not user32.OpenClipboard(None):
                return "OpenClipboard 失败"
            try:
                user32.EmptyClipboard()
                user32.SetClipboardData(CF_UNICODETEXT, h_global)
            finally:
                user32.CloseClipboard()
            return None
        except Exception as e:
            return f"ctypes 剪贴板操作失败: {type(e).__name__}: {e}"

    return "无可用的剪贴板后端（建议 pip install pyperclip）"


class TypeTextTool(Tool):
    name = "TypeText"
    description = (
        "在当前焦点输入框里输入文字。纯英文/数字用打字方式输入；"
        "含中文等非 ASCII 字符时通过剪贴板粘贴（会覆盖原剪贴板内容，结果会提示）。"
        "请先用 MouseClick 把目标输入框点成焦点，再调用本工具。默认会询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要输入的文本"},
            "interval": {
                "type": "number",
                "description": "每个字符的打字间隔秒数（仅对 ASCII 生效，默认 0.0）",
                "minimum": 0.0,
                "maximum": 1.0,
            },
        },
        "required": ["text"],
    }
    max_result_chars = 2_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        # 含非 ASCII 时会改剪贴板
        return not _is_ascii(args.get("text", ""))

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        text = args.get("text", "")
        preview = text if len(text) <= 40 else text[:37] + "..."
        return PermissionResult.ask(f"输入文本: {preview!r}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        text = args.get("text")
        if not isinstance(text, str):
            return ValidationResult.fail("text 必须是字符串")
        if text == "":
            return ValidationResult.fail("text 不能为空")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(tool_name="TypeText", targets=[])
        # 无 target 通配，规则 "TypeText" 可命中

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pyautogui = _import_pyautogui()
        except ImportError as e:
            return ToolResult.error(f"pyautogui 未安装: {e}")

        text = args["text"]
        interval = float(args.get("interval", 0.0))

        if _is_ascii(text):
            try:
                pyautogui.write(text, interval=interval)
            except pyautogui.FailSafeException:
                return ToolResult.error("触发 FAILSAFE，已中止")
            except Exception as e:
                return ToolResult.error(f"输入失败: {type(e).__name__}: {e}")
            return ToolResult.ok(f"已输入 {len(text)} 个 ASCII 字符")

        # 非 ASCII: 走剪贴板粘贴
        err = _set_clipboard(text)
        if err:
            return ToolResult.error(f"无法输入非 ASCII 文本: {err}")
        try:
            # Windows/Linux 用 ctrl+v，mac 用 cmd+v
            import platform
            if platform.system() == "Darwin":
                pyautogui.hotkey("command", "v")
            else:
                pyautogui.hotkey("ctrl", "v")
        except pyautogui.FailSafeException:
            return ToolResult.error("触发 FAILSAFE，已中止")
        except Exception as e:
            return ToolResult.error(f"粘贴失败: {type(e).__name__}: {e}")

        return ToolResult.ok(
            f"已通过剪贴板粘贴 {len(text)} 个字符（⚠️ 原剪贴板内容已被覆盖）"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            text = args.get("text", "")
            preview = text if len(text) <= 20 else text[:17] + "..."
            return f"输入 {preview!r}"
        return None


class KeyTapTool(Tool):
    name = "KeyTap"
    description = (
        "按键或组合键。传单个 key 即按单键（如 [\"enter\"]）；"
        "传多个 key 即按组合键（如 [\"ctrl\",\"s\"] 保存、[\"ctrl\",\"shift\",\"esc\"] 任务管理器）。"
        "常用键名: enter/esc/tab/space/backspace/delete/up/down/left/right/"
        "home/end/pageup/pagedown/f1..f12/ctrl/alt/shift/win/capslock。"
        "默认会询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "按键序列。单元素=单键，多元素=组合键（依次按下再松开）",
                "minItems": 1,
                "maxItems": 6,
            },
        },
        "required": ["keys"],
    }
    max_result_chars = 1_000

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        # 快捷键可能触发任意动作（如 alt+f4 关窗口）
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        keys = args.get("keys", [])
        combo = "+".join(keys) if keys else "?"
        return PermissionResult.ask(f"按键: {combo}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        keys = args.get("keys")
        if not isinstance(keys, list) or not keys:
            return ValidationResult.fail("keys 必须是非空数组")
        if not all(isinstance(k, str) and k for k in keys):
            return ValidationResult.fail("keys 中每个元素须为非空字符串")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        keys = args.get("keys", [])
        return PermissionMatcher(tool_name="KeyTap", targets=["+".join(keys)])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            pyautogui = _import_pyautogui()
        except ImportError as e:
            return ToolResult.error(f"pyautogui 未安装: {e}")

        keys: list[str] = args["keys"]

        try:
            if len(keys) == 1:
                pyautogui.press(keys[0])
                action = f"按下 {keys[0]}"
            else:
                pyautogui.hotkey(*keys)
                action = "按下组合键 " + "+".join(keys)
        except pyautogui.FailSafeException:
            return ToolResult.error("触发 FAILSAFE，已中止")
        except Exception as e:
            return ToolResult.error(f"按键失败: {type(e).__name__}: {e}")

        return ToolResult.ok(action)

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args:
            keys = args.get("keys", [])
            return "按 " + "+".join(keys) if keys else None
        return None
