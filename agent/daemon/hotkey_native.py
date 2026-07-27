"""Windows 原生全局热键实现。

基于 Win32 API ``RegisterHotKey`` + 消息循环，比 keyboard 库的全局钩子
响应更快、权限要求更低。独立后台线程运行，按下热键时立即回调。

非 Windows 平台或注册失败时，应由调用方回退到 ``HotkeyListener``。

@author aceFelix
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import threading
import time
from typing import Callable


# Win32 API 常量
_WM_HOTKEY = 0x0312
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_HOTKEY_ID = 0x7A52  # 'zR' = jarvis


# 键名到虚拟键码映射（只包含常用修饰键和字母/数字）
_VK_MAP: dict[str, int] = {
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45, "f": 0x46,
    "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A, "k": 0x4B, "l": 0x4C,
    "m": 0x4D, "n": 0x4E, "o": 0x4F, "p": 0x50, "q": 0x51, "r": 0x52,
    "s": 0x53, "t": 0x54, "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58,
    "y": 0x59, "z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B,
    "space": 0x20, "tab": 0x09, "esc": 0x1B, "enter": 0x0D,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}


def _parse_hotkey(hotkey: str) -> tuple[int, int]:
    """把 human-readable 热键字符串解析为 (modifiers, vk)。

    支持格式如 ``ctrl+shift+j``、``alt+f1``、``win+space``。
    大小写不敏感，顺序不敏感。

    Args:
        hotkey: 热键描述字符串。

    Returns:
        (modifiers, vk) 元组，供 ``RegisterHotKey`` 使用。

    Raises:
        ValueError: 格式无法解析或包含未知按键。
    """
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        raise ValueError("热键字符串为空")

    modifiers = 0
    key_part: str | None = None
    for part in parts:
        if part in ("ctrl", "control"):
            modifiers |= _MOD_CONTROL
        elif part in ("shift",):
            modifiers |= _MOD_SHIFT
        elif part in ("alt",):
            modifiers |= _MOD_ALT
        elif part in ("win", "windows", "cmd", "command"):
            modifiers |= _MOD_WIN
        elif key_part is None and part in _VK_MAP:
            key_part = part
        else:
            raise ValueError(f"无法解析热键片段: {part!r}")

    if key_part is None:
        raise ValueError(f"热键缺少主键: {hotkey!r}")
    if modifiers == 0:
        raise ValueError("原生热键必须包含至少一个修饰键")

    return modifiers, _VK_MAP[key_part]


class _WNDCLASSW(ctypes.Structure):
    """Win32 WNDCLASSW 结构体，兼容不同 Python/ctypes 版本。"""

    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


class NativeHotkeyListener:
    """Windows 原生全局热键监听器。

    在独立后台线程中创建消息-only 窗口并进入 GetMessage 循环，
    收到 ``WM_HOTKEY`` 后立即调用 ``on_trigger``。

    提供 ``available`` 检测：非 Windows 或关键 API 缺失时返回 False。

    Attributes:
        hotkey: 热键字符串，如 ``ctrl+shift+j``。
        on_trigger: 热键按下时的回调（在监听线程中执行，应快速返回）。
        debounce_ms: 去抖毫秒数，防止一次物理按下触发多次回调。
    """

    def __init__(
        self,
        hotkey: str,
        on_trigger: Callable[[], None],
        *,
        debounce_ms: int = 200,
    ) -> None:
        self._hotkey = hotkey
        self._on_trigger = on_trigger
        self._debounce_ms = max(0, debounce_ms)
        self._started = False
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._last_trigger_time = 0.0
        self._stop_event = threading.Event()
        self._register_event = threading.Event()
        self._register_ok = False
        self._hwnd = 0
        self._atom = 0
        self._modifiers = 0
        self._vk = 0

    @property
    def available(self) -> bool:
        """当前平台是否支持原生热键（仅 Windows）。"""
        if sys.platform != "win32":
            return False
        try:
            # 检查关键 Win32 API 是否可用
            ctypes.windll.user32.RegisterHotKey
            ctypes.windll.user32.GetMessageW
            ctypes.windll.user32.TranslateMessage
            ctypes.windll.user32.DispatchMessageW
            return True
        except Exception:
            return False

    def start(self) -> bool:
        """启动热键监听。成功返回 True。"""
        if not self.available or self._started:
            return self._started

        try:
            self._modifiers, self._vk = _parse_hotkey(self._hotkey)
        except Exception:
            return False

        self._stop_event.clear()
        self._register_event.clear()
        self._register_ok = False
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        # 等待注册完成或失败
        self._register_event.wait(timeout=2.0)
        return self._register_ok

    def stop(self) -> None:
        """停止热键监听并反注册热键。"""
        if not self._started:
            return
        self._stop_event.set()
        try:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, 0x0012, 0, 0  # WM_QUIT
            )
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._started = False

    def _message_loop(self) -> None:
        """后台线程：创建消息窗口、注册热键、进入消息循环。"""
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 创建 message-only 窗口（不可见，只收消息）
        wndclass = _WNDCLASSW()
        wndclass.lpfnWndProc = ctypes.cast(
            ctypes.WINFUNCTYPE(
                ctypes.c_longlong,
                ctypes.wintypes.HWND,
                ctypes.c_uint,
                ctypes.wintypes.WPARAM,
                ctypes.wintypes.LPARAM,
            )(self._wnd_proc),
            ctypes.c_void_p,
        )
        wndclass.lpszClassName = "JarvisNativeHotkey"
        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if not atom:
            self._register_event.set()
            return

        hwnd = user32.CreateWindowExW(
            0, atom, "JarvisNativeHotkey", 0, 0, 0, 0, 0,
            -3,  # HWND_MESSAGE
            0, 0, None,
        )
        if not hwnd:
            user32.UnregisterClassW(atom, None)
            self._register_event.set()
            return

        # 注册热键
        ok = user32.RegisterHotKey(hwnd, _HOTKEY_ID, self._modifiers, self._vk)
        if not ok:
            user32.DestroyWindow(hwnd)
            user32.UnregisterClassW(atom, None)
            self._register_event.set()
            return

        self._hwnd = hwnd
        self._atom = atom
        self._started = True
        self._register_ok = True
        self._register_event.set()

        # 消息循环
        msg = ctypes.wintypes.MSG()
        while not self._stop_event.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:  # WM_QUIT
                break
            if ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # 清理
        user32.UnregisterHotKey(hwnd, _HOTKEY_ID)
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(atom, None)
        self._started = False

    def _wnd_proc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """窗口过程：拦截 WM_HOTKEY 并触发回调。"""
        if msg == _WM_HOTKEY and wparam == _HOTKEY_ID:
            now = time.monotonic()
            elapsed_ms = int((now - self._last_trigger_time) * 1000)
            if elapsed_ms >= self._debounce_ms:
                self._last_trigger_time = now
                try:
                    self._on_trigger()
                except Exception:
                    # 回调异常不能中断消息循环
                    pass
            return 0
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
