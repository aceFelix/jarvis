"""热键响应优化模块的单元测试。

覆盖:
- 热键字符串解析
- NativeHotkeyListener 平台可用性
- FastTerminalSpawner 窗口复用逻辑

@author aceFelix
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from agent.daemon.hotkey_native import _MOD_ALT, _MOD_CONTROL, _MOD_SHIFT, _MOD_WIN, _parse_hotkey
from agent.daemon.terminal_spawner import FastTerminalSpawner


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


@dataclass
class _FakeSettings:
    """用于测试的最小 Settings 替身。"""

    workdir: str = "/tmp"


class TestFastTerminalSpawner(unittest.TestCase):
    """测试 FastTerminalSpawner 唤起策略。"""

    def test_bring_up_reuses_running_proc(self):
        """已有运行中进程时，bring_up 不应 spawn 新进程。"""
        settings = _FakeSettings(workdir="/tmp")
        spawner = FastTerminalSpawner(settings)

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.pid = 12345
        spawner._text_terminal_proc = fake_proc

        with patch("agent.daemon.terminal_spawner._set_foreground_window", return_value=True) as mock_fg:
            spawner.bring_up()
            mock_fg.assert_called_once_with(12345)

        # 没有 spawn 新进程
        self.assertIs(spawner._text_terminal_proc, fake_proc)

    def test_bring_up_spawns_when_no_running_proc(self):
        """无运行中进程时，bring_up 应调用 _spawn_quick。"""
        settings = _FakeSettings(workdir="/tmp")
        spawner = FastTerminalSpawner(settings)

        with patch.object(spawner, "_spawn_quick") as mock_spawn:
            spawner.bring_up()
            mock_spawn.assert_called_once()

    def test_bring_up_uses_warm_proc(self):
        """warm 进程可用时，应提升为当前终端并启动下一个 warm。"""
        settings = _FakeSettings(workdir="/tmp")
        spawner = FastTerminalSpawner(settings)

        warm_proc = MagicMock()
        warm_proc.poll.return_value = None
        warm_proc.pid = 54321
        spawner._warm_proc = warm_proc

        with patch("agent.daemon.terminal_spawner._set_foreground_window", return_value=True):
            with patch.object(spawner, "start_warm") as mock_start_warm:
                spawner.bring_up()
                # warm 进程被提升为当前终端
                self.assertIs(spawner._text_terminal_proc, warm_proc)
                # 启动下一个 warm 进程
                mock_start_warm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
