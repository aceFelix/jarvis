"""yolo 按目标域放行的护栏测试。

钉的是一条新语义（2026-11 用户要求）：
- 受保护根（默认 C 盘）之外，yolo 拥有全部权力，连 DENY 一起放行；
- 受保护根内的不可逆动作 → 一次确认（点头后执行，不再挂第二道闸）；
- 硬禁止清单（~/.ssh 等）连确认都不收。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent.core.result import PermissionBehavior, PermissionResult, ToolResult
from agent.core.tool import Tool
from agent.permissions.checker import PermissionChecker
from agent.permissions.modes import PermissionMode
from agent.permissions.yolo_gate import extract_targets, matches_hard_deny, under_any


class _Tool(Tool):
    """最小可用的假工具：只声明"是否不可逆"和"工具特判结论"。"""

    name = "Bash"
    description = "test tool"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, irreversible: bool = False, behavior: str = "ask") -> None:
        self._irr = irreversible
        self._behavior = behavior

    def is_irreversible(self, args: dict[str, Any]) -> bool:
        return self._irr

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: Any) -> PermissionResult:
        if self._behavior == "allow":
            return PermissionResult.allow("工具特判放行")
        return PermissionResult.ask("工具特判询问")

    async def call(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult.ok("noop")


def _ctx(roots=("c:\\",), hard=("~/.ssh/**",)):
    return SimpleNamespace(
        workdir=r"E:\work",
        permission_mode="yolo",
        extra={},
        settings=SimpleNamespace(
            permissions_yolo_guard_roots=list(roots),
            permissions_yolo_hard_deny=list(hard),
        ),
    )


class TestTargetExtraction:
    def test_reads_path_args(self):
        t = extract_targets(_Tool(), {"file_path": r"C:\Windows\x.txt"})
        assert any(x.startswith("c:\\") for x in t)

    def test_scans_command_for_drives(self):
        t = extract_targets(_Tool(), {"command": r"del C:\a\b.txt && copy d:/x c:/y"})
        assert any(x.startswith("c:\\") for x in t)
        assert any(x.startswith("d:\\") for x in t)

    def test_empty_when_nothing_found(self):
        assert extract_targets(_Tool(), {"command": "echo hi"}) == []

    def test_under_any_matches_drive_root(self):
        assert under_any(["c:\\windows\\system32"], ["c:\\"]) == "c:\\"
        assert under_any(["d:\\temp"], ["c:\\"]) is None

    def test_hard_deny_glob(self):
        home = __import__("pathlib").Path.home()
        target = str(home / ".ssh" / "id_rsa").replace("/", "\\").lower()
        assert matches_hard_deny([target], ["~/.ssh/**"]) == "~/.ssh/**"


class TestYoloScope:
    """受保护根之外全放行，根内不可逆要点头。"""

    def _check(self, tool, args, ctx=None, mode=PermissionMode.YOLO):
        return PermissionChecker(mode=mode).check(tool, args, ctx or _ctx())

    def test_outside_guard_allow_even_for_dangerous(self):
        r = self._check(_Tool(irreversible=True), {"command": r"del D:\tmp\*"})
        assert r.behavior == PermissionBehavior.ALLOW

    def test_outside_guard_overrides_deny_rule(self):
        """yolo 判定排规则之后：规则 deny 也要被放行，否则改 yolo 没用。"""
        ctx = _ctx(hard=())  # 排除硬禁止干扰
        r = self._check(_Tool(irreversible=True), {"file_path": r"D:\work\a.txt"}, ctx)
        assert r.behavior == PermissionBehavior.ALLOW

    def test_inside_guard_dangerous_asks(self):
        r = self._check(_Tool(irreversible=True), {"file_path": r"C:\Temp\a.txt"})
        assert r.behavior == PermissionBehavior.ASK
        assert "yolo" in r.reason

    def test_inside_guard_reversible_allows(self):
        r = self._check(_Tool(irreversible=False), {"file_path": r"C:\Temp\a.txt"})
        assert r.behavior == PermissionBehavior.ALLOW

    def test_hard_deny_survives_even_with_approval(self):
        home = __import__("pathlib").Path.home()
        target = str(home / ".ssh" / "id_rsa")
        r = self._check(_Tool(irreversible=True), {"file_path": target},
                        _ctx(hard=["~/.ssh/**"]))
        assert r.behavior == PermissionBehavior.DENY
        assert "硬禁止" in r.reason

    def test_default_mode_still_refuses_to_relax_deny(self):
        """非 yolo 模式不继承这套放宽——那条不变量对其他模式继续成立。"""
        r = self._check(_Tool(irreversible=True), {"file_path": r"C:\Temp\a.txt"},
                        mode=PermissionMode.DEFAULT)
        assert r.behavior != PermissionBehavior.ALLOW

    def test_unreadable_guard_config_falls_back_to_c_drive(self):
        ctx = SimpleNamespace(workdir=r"E:\work", permission_mode="yolo", extra={},
                              settings=SimpleNamespace())
        r = self._check(_Tool(irreversible=True), {"file_path": r"C:\Temp\a.txt"}, ctx)
        assert r.behavior == PermissionBehavior.ASK

    def test_explicit_empty_roots_means_no_guard(self):
        """显式 `[]` ≠ 键缺失：用户写空数组是真的要关掉保护域。

        2026-11 之前这里是 `getattr(...) or DEFAULT`，空列表被静默折回默认 C 盘——
        改了等于没改且不报错，那类失败最难查，所以钉一条。
        """
        ctx = _ctx(roots=())
        r = self._check(_Tool(irreversible=True), {"file_path": r"C:\Temp\a.txt"}, ctx)
        assert r.behavior == PermissionBehavior.ALLOW

    def test_explicit_empty_hard_deny_disables_only_that_list(self):
        """硬禁止清单写空 → 不再 DENY；但 C 盘保护仍在（两条清单互不顶替）。"""
        ctx = _ctx(hard=())
        home = __import__("pathlib").Path.home()
        target = str(home / ".ssh" / "id_rsa")
        r = self._check(_Tool(irreversible=True), {"file_path": target}, ctx)
        assert r.behavior != PermissionBehavior.DENY   # 不再是硬禁止
        assert r.behavior == PermissionBehavior.ASK    # 但仍在 C 盘保护域内


class TestHoldSkip:
    def test_hold_in_yolo_defaults_off(self):
        from agent.config.settings import Settings
        assert Settings().actions_hold_in_yolo is False

    def test_guard_roots_default_is_c_drive(self):
        from agent.config.settings import Settings
        assert Settings().permissions_yolo_guard_roots == ["c:\\"]
