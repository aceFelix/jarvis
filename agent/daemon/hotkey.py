"""跨平台全局热键监听器。

平台策略：
- Windows: 优先使用原生 RegisterHotKey（NativeHotkeyListener，见 hotkey_native.py），
  回退到 keyboard 库。
- macOS: 使用 pynput（不需要 root，只需辅助功能权限），
  回退到 keyboard 库（需 root）。
- Linux: 使用 keyboard 库（需 root 或 input 组权限）。

从原 daemon.py 拆分而来。

@author aceFelix
"""

from __future__ import annotations

from typing import Any, Callable

from agent.daemon.platform_utils import _has_keyboard, _has_pynput, _is_macos


class HotkeyListener:
    """全局热键监听器（跨平台）。"""

    def __init__(self, hotkey: str, on_trigger: Callable[[], None]) -> None:
        self._hotkey = hotkey
        self._on_trigger = on_trigger
        self._started = False
        self._backend: str = ""  # "native" / "pynput" / "keyboard"
        self._listener: Any = None  # pynput listener 对象

    @property
    def available(self) -> bool:
        if _is_macos():
            return _has_pynput() or _has_keyboard()
        return _has_keyboard()

    def start(self) -> bool:
        """启动热键监听。成功返回 True。"""
        if not self.available or self._started:
            return self._started

        # macOS: 优先 pynput（不需 root）
        if _is_macos() and _has_pynput():
            if self._start_pynput():
                return True

        # 回退到 keyboard 库
        if _has_keyboard():
            try:
                import keyboard
                keyboard.add_hotkey(self._hotkey, self._on_trigger, suppress=False)
                self._started = True
                self._backend = "keyboard"
                return True
            except Exception:
                pass
        return False

    def _start_pynput(self) -> bool:
        """macOS: 用 pynput 监听全局热键。

        pynput 解析热键字符串（如 "ctrl+shift+j"）并监听按键组合。
        需要辅助功能权限（系统设置 → 隐私与安全性 → 辅助功能）。
        """
        try:
            from pynput import keyboard as pynput_kb

            # 解析热键字符串为 pynput 的 HotKey 格式
            hotkey_str = self._hotkey.lower().replace(" ", "")
            # pynput HotKey.parse 支持 "<ctrl>+<shift>+j" 格式
            # 将 "ctrl+shift+j" 转换为 "<ctrl>+<shift>+j"
            parts = hotkey_str.split("+")
            parsed_parts = []
            for p in parts:
                # 修饰键加尖括号
                if p in ("ctrl", "shift", "alt", "cmd", "command", "super", "win"):
                    parsed_parts.append(f"<{p}>")
                else:
                    parsed_parts.append(p)
            pynput_hotkey_str = "+".join(parsed_parts)

            def _on_activate():
                self._on_trigger()

            hotkey_obj = pynput_kb.HotKey(
                pynput_kb.HotKey.parse(pynput_hotkey_str),
                _on_activate,
            )

            def _on_press(key):
                hotkey_obj.press(self._normalize_key(key))

            def _on_release(key):
                hotkey_obj.release(self._normalize_key(key))

            listener = pynput_kb.Listener(
                on_press=_on_press,
                on_release=_on_release,
            )
            listener.daemon = True
            listener.start()

            self._listener = listener
            self._started = True
            self._backend = "pynput"
            return True
        except Exception:
            return False

    @staticmethod
    def _normalize_key(key):
        """将 pynput 的 Key/KeyCode 规范化为 HotKey 可识别的格式。"""
        from pynput import keyboard as pynput_kb  # noqa: F401
        try:
            # 特殊键（ctrl/shift/alt/cmd）直接返回
            return key
        except Exception:
            return key

    def stop(self) -> None:
        if not self._started:
            return
        if self._backend == "pynput" and self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        elif self._backend == "keyboard":
            try:
                import keyboard
                keyboard.remove_all_hotkeys()
            except Exception:
                pass
        self._started = False
        self._backend = ""
