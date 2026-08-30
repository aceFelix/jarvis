"""热键模块的单元测试。

覆盖:
- 热键字符串解析
- NativeHotkeyListener 平台可用性

（FastTerminalSpawner 随“无窗口 daemon + 托盘遥控”架构下线，
其测试用例一并移除。作者：aceFelix）

@author aceFelix
"""

from __future__ import annotations

import sys
import unittest

from agent.daemon.hotkey_native import _MOD_ALT, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN, _parse_hotkey


class TestParseHotkey(unittest.TestCase):
    """测试 human-readable 热键字符串解析。"""

    def test_ctrl_shift_j(self):
        mods, vk = _parse_hotkey("ctrl+shift+j")
        self.assertEqual(mods, _MOD_CONTROL | _MOD_SHIFT)
        self.assertEqual(vk, 0x4A)

    def test_alt_f1(self):
        mods, vk = _parse_hotkey("alt+f1")
        self.assertEqual(mods, _MOD_ALT)
        self.assertEqual(vk, 0x70)

    def test_win_space(self):
        mods, vk = _parse_hotkey("win+space")
        self.assertEqual(mods, _MOD_WIN)
        self.assertEqual(vk, 0x20)

    def test_case_insensitive(self):
        mods, vk = _parse_hotkey("CTRL+SHIFT+J")
        self.assertEqual(mods, _MOD_CONTROL | _MOD_SHIFT)
        self.assertEqual(vk, 0x4A)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            _parse_hotkey("")

    def test_no_modifier_raises(self):
        with self.assertRaises(ValueError):
            _parse_hotkey("j")

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            _parse_hotkey("ctrl+unknown")


class TestNativeHotkeyListener(unittest.TestCase):
    """测试 NativeHotkeyListener 基础行为。"""

    def test_available_false_on_non_windows(self):
        """非 Windows 平台 available 应返回 False。"""
        from agent.daemon.hotkey_native import NativeHotkeyListener

        listener = NativeHotkeyListener("ctrl+shift+j", lambda: None)
        if sys.platform != "win32":
            self.assertFalse(listener.available)
        # start() 在不可用时应直接返回当前状态 False
        self.assertFalse(listener.start())


if __name__ == "__main__":
    unittest.main()
