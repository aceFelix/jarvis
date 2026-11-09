"""终端优先置顶策略（``cockpit_zorder``）的纯逻辑测试。

2026-11-09 用户要求：正在用 Git Bash / cmd 时，终端要盖住任务驾驶舱与抓取舱；
切到其它程序再恢复置顶。这里不创建真实 Tk，用假 root/抓取舱记录调用。

@author aceFelix
"""

from __future__ import annotations

from agent.ui import cockpit_zorder as z


class _FakeRoot:
    """假 Tk 根窗口：只记录 -topmost 的值。"""

    def __init__(self) -> None:
        self.topmost = None

    def attributes(self, name: str, value) -> None:
        assert name == "-topmost"
        self.topmost = value


class _FakeCrawl:
    """假抓取舱管理器：记录每次 set_topmost。"""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def set_topmost(self, enabled: bool) -> None:
        self.calls.append(enabled)


class TestIsTerminalProcess:
    def test_common_terminal_hosts(self) -> None:
        for name in ("mintty.exe", "cmd.exe", "conhost.exe", "pwsh.exe", "windowsterminal.exe"):
            assert z.is_terminal_process(name) is True

    def test_case_insensitive(self) -> None:
        assert z.is_terminal_process("MINTTY.EXE") is True

    def test_normal_app_is_not_terminal(self) -> None:
        assert z.is_terminal_process("chrome.exe") is False
        assert z.is_terminal_process("") is False


class TestTerminalAwareTopmost:
    def test_demotes_to_terminal_then_restores(self, monkeypatch) -> None:
        """前台是终端 → 两窗取消置顶；切到其它程序 → 恢复置顶。"""
        root, crawl = _FakeRoot(), _FakeCrawl()
        policy = z.TerminalAwareTopmost(root, crawl, interval=0)

        monkeypatch.setattr(z, "foreground_process_name", lambda: "mintty.exe")
        policy.tick()
        assert root.topmost is False
        assert crawl.calls[-1] is False
        assert policy.wanted is False

        monkeypatch.setattr(z, "foreground_process_name", lambda: "chrome.exe")
        policy.tick()
        assert root.topmost is True
        assert crawl.calls[-1] is True
        assert policy.wanted is True

    def test_state_change_only_applies_once(self, monkeypatch) -> None:
        """连续多次检测结果相同 → 不重复调用 Tk（避免闪烁）。"""
        root, crawl = _FakeRoot(), _FakeCrawl()
        policy = z.TerminalAwareTopmost(root, crawl, interval=0)
        monkeypatch.setattr(z, "foreground_process_name", lambda: "cmd.exe")
        policy.tick()
        policy.tick()
        policy.tick()
        assert crawl.calls == [False]

    def test_disabled_never_topmost(self, monkeypatch) -> None:
        """用户关掉置顶（topmost=False）时，即使前台不是终端也不置顶。"""
        root, crawl = _FakeRoot(), _FakeCrawl()
        policy = z.TerminalAwareTopmost(root, crawl, enabled=False, interval=0)
        monkeypatch.setattr(z, "foreground_process_name", lambda: "chrome.exe")
        policy.tick()
        assert policy.wanted is False
        # 初始状态就是“不置顶”，状态未变化因此不会重复 apply
        assert crawl.calls == []
