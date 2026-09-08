"""autostart CLI 桌面快捷方式入口下线的回归测试。

2026-09 桌面入口由 jarvis-desktop 桌面应用接管，桌面快捷方式子命令
（desktop / desktop-uninstall / desktop-status）与跨平台 .command /
.desktop 生成逻辑一并移除。本用例防止已下线入口"复活"：

- desktop 系命令执行应报未知命令（退出码 1）；
- 相关实现函数不应再存在于模块中（孤儿代码清零）；
- help 文案只保留开机自启命令并明示桌面入口已下线。

@author aceFelix
"""

from agent.daemon import autostart


def test_desktop_command_rejected_as_unknown():
    """desktop 系子命令下线后执行应报未知命令并返回 1。"""
    assert autostart.main(["desktop"]) == 1
    assert autostart.main(["desktop-uninstall"]) == 1
    assert autostart.main(["desktop-status"]) == 1


def test_desktop_helpers_removed():
    """桌面快捷方式实现函数应整体移除，不留孤儿代码。"""
    for name in (
        "install_desktop",
        "uninstall_desktop",
        "status_desktop",
        "desktop_shortcut_path",
        "_install_desktop_macos",
        "_install_desktop_linux",
    ):
        assert not hasattr(autostart, name), f"已下线函数仍存在: {name}"


def test_help_only_lists_autostart_commands(capsys):
    """help 文案只保留开机自启三命令，并明示桌面入口已下线。"""
    assert autostart.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "install" in out
    assert "uninstall" in out
    assert "status" in out
    # 下线子命令不再出现在命令列表里
    assert "desktop-uninstall" not in out
    # 帮助末尾明示桌面入口下线去向
    assert "下线" in out
