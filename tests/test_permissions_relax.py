"""管线规则放宽与路径作用域匹配测试（W1 策略引擎并入五层管线的回归守卫）。

背景（2026-10-14 降级合并）：core/policy 的策略引擎（policy.yaml 四态规则）
被认定为与 permissions.yaml 平行重复，删除并入管线本身：
1. 带 spec 的 allow 规则命中可放宽工具默认 ASK / 特判 ASK（旧行为
   _stricter 只收紧，allow 永远压不过 ASK，permissions.yaml 形同虚设）；
2. 文件类工具的 spec 升级为路径 glob（~ 展开、workdir 相对化、平台
   大小写归一——旧裸 fnmatch 对 "~/reports/**" 永远失配）；
3. 保守边界不放开：DENY 永不可放宽；裸工具名 allow（无 spec）只能兜底
   放宽管线默认 ASK，不能放宽工具特判 ASK、也不能放宽跨 workdir 软
   提醒；带 spec 且命中目标的 allow 规则（用户显式白名单）可放宽
   跨边界软提醒（呼应 path_guard“除非用户显式加白名单”的设计）。

@author aceFelix
"""

from __future__ import annotations

import os
from pathlib import Path

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior, PermissionResult
from agent.core.tool import PermissionMatcher, Tool, ValidationResult
from agent.permissions.checker import PermissionChecker
from agent.permissions.modes import PermissionMode
from agent.permissions.rules import PermissionRule, RuleSet, RuleSource, RuleValue


class _FakeTool(Tool):
    """测试用假工具：可控只读性 / 路径 / 特判结果 / 匹配器。"""

    description = ""
    input_schema = {}

    def __init__(
        self,
        name: str = "Fake",
        *,
        read_only: bool = False,
        path: str = "",
        perm: PermissionResult | None = None,
        matcher: PermissionMatcher | None = None,
    ) -> None:
        self.name = name
        self._read_only = read_only
        self._path = path
        self._perm = perm
        self._matcher = matcher

    async def call(self, args: dict, ctx: ToolContext):  # type: ignore[override]
        return PermissionResult.allow("not used")

    def is_read_only(self, args: dict) -> bool:
        return self._read_only

    def check_permissions(self, args: dict, ctx: ToolContext) -> PermissionResult:
        if self._perm is not None:
            return self._perm
        return PermissionResult.ask("no tool-specific permission rule")

    def get_path(self, args: dict) -> str:
        return self._path

    def prepare_permission_matcher(self, args: dict) -> PermissionMatcher | None:
        return self._matcher


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workdir=str(tmp_path), messages=[])


def _checker(*rules: PermissionRule) -> PermissionChecker:
    return PermissionChecker(rules=RuleSet(list(rules)), mode=PermissionMode.DEFAULT)


class TestAllowRuleRelaxesAsk:
    """带 spec 的 allow 规则可放宽工具 ASK（并入原策略引擎的核心语义）。"""

    def test_relaxes_tool_default_ask(self, tmp_path: Path) -> None:
        """工具默认 ASK + Bash(git *) allow → ALLOW（旧行为：仍 ASK）。"""
        c = _checker(PermissionRule("Bash(git *)", RuleValue.ALLOW, RuleSource.USER))
        tool = _FakeTool(name="Bash", matcher=PermissionMatcher("Bash", ["git status"]))
        result = c.check(tool, {"command": "git status"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW
        assert "规则命中" in (result.reason or "")

    def test_relaxes_tool_specific_ask(self, tmp_path: Path) -> None:
        """工具特判 ASK（如 FileWrite 类）+ 带路径 spec allow → ALLOW。"""
        scope = str(tmp_path / "reports") + "/**"
        c = _checker(PermissionRule(f"FileWrite({scope})", RuleValue.ALLOW, RuleSource.USER))
        tool = _FakeTool(
            name="FileWrite", path="reports/a.md",
            perm=PermissionResult.ask("文件写入需确认"),
        )
        result = c.check(tool, {"file_path": "reports/a.md"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_bare_name_allow_does_not_relax(self, tmp_path: Path) -> None:
        """裸工具名 allow（无 spec）不放宽——免确认必须有明确范围。"""
        c = _checker(PermissionRule("FileWrite", RuleValue.ALLOW, RuleSource.USER))
        tool = _FakeTool(
            name="FileWrite", perm=PermissionResult.ask("文件写入需确认"),
        )
        result = c.check(tool, {"file_path": "a.md"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ASK

    def test_never_relaxes_deny(self, tmp_path: Path) -> None:
        """管线 DENY（危险命令特判/路径守护）永不被 allow 规则放宽。"""
        c = _checker(PermissionRule("Bash(rm *)", RuleValue.ALLOW, RuleSource.USER))
        tool = _FakeTool(
            name="Bash", matcher=PermissionMatcher("Bash", ["rm x"]),
            perm=PermissionResult.deny("命令匹配危险模式"),
        )
        result = c.check(tool, {"command": "rm x"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY

    def test_bare_name_allow_not_relax_cross_boundary(self, tmp_path: Path) -> None:
        """裸名 allow（无 spec）不放宽跨 workdir 边界的软提醒 ASK。

        口径：带 spec 且命中跨边界目标的 allow 规则 = 用户显式白名单，
        可放宽软提醒（见 test_tilde_glob_matches_absolute_target）；但没有
        明确范围的裸名 allow 不能隐式授予“写任意目录外”的权力。
        """
        c = _checker(PermissionRule("Write", RuleValue.ALLOW, RuleSource.USER))
        tool = _FakeTool(name="Write", path="../outside.txt")
        result = c.check(tool, {"file_path": "../outside.txt"}, _ctx(tmp_path))
        # 跨边界软提醒在规则之前生效，裸名 allow 不能把它转成 ALLOW
        assert result.behavior == PermissionBehavior.ASK

    def test_spec_whitelist_relaxes_cross_boundary(self, tmp_path: Path) -> None:
        """带 spec 且覆盖跨边界目标的 allow 规则，可放宽软提醒（白名单语义）。"""
        outside = str(tmp_path.parent / "shared-reports") + "/**"
        c = _checker(PermissionRule(f"Write({outside})", RuleValue.ALLOW, RuleSource.USER))
        target = str(tmp_path.parent / "shared-reports" / "a.md")
        tool = _FakeTool(name="Write", path=target)
        result = c.check(tool, {"file_path": target}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_ask_deny_rules_still_stricter(self, tmp_path: Path) -> None:
        """ask/deny 规则维持旧的"取更严侧"合并（收紧能力不丢）。"""
        c = _checker(PermissionRule("Bash(git push*)", RuleValue.ASK, RuleSource.USER))
        tool = _FakeTool(
            name="Bash", matcher=PermissionMatcher("Bash", ["git push"]),
            perm=PermissionResult.allow("readonly command"),
        )
        result = c.check(tool, {"command": "git push"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ASK


class TestPathScopedMatching:
    """文件类工具 spec 的路径 glob 匹配（升级自裸 fnmatch）。"""

    def test_tilde_glob_matches_absolute_target(self, tmp_path: Path) -> None:
        """~/ 前缀 spec 对绝对路径目标命中（裸 fnmatch 下永远失配的场景）。"""
        home_reports = os.path.join(os.path.expanduser("~"), "reports")
        c = _checker(PermissionRule("FileWrite(~/reports/**)", RuleValue.ALLOW, RuleSource.USER))
        target = os.path.join(home_reports, "sub", "a.md")
        tool = _FakeTool(
            name="FileWrite", path=target,
            perm=PermissionResult.ask("文件写入需确认"),
        )
        result = c.check(tool, {"file_path": target}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_relative_target_resolved_against_workdir(self, tmp_path: Path) -> None:
        """相对路径入参按 workdir 绝对化后再与 spec 比较。"""
        scope = str(tmp_path) + "/**"
        c = _checker(PermissionRule(f"FileWrite({scope})", RuleValue.ALLOW, RuleSource.USER))
        tool = _FakeTool(
            name="FileWrite", path="nested/a.md",
            perm=PermissionResult.ask("文件写入需确认"),
        )
        result = c.check(tool, {"file_path": "nested/a.md"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_out_of_scope_not_matched(self, tmp_path: Path) -> None:
        """作用域之外的路径不命中，保持 ASK。"""
        scope = str(tmp_path / "reports") + "/**"
        c = _checker(PermissionRule(f"FileWrite({scope})", RuleValue.ALLOW, RuleSource.USER))
        tool = _FakeTool(
            name="FileWrite", path=str(tmp_path / "secret" / "a.md"),
            perm=PermissionResult.ask("文件写入需确认"),
        )
        result = c.check(tool, {"file_path": str(tmp_path / "secret" / "a.md")}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ASK

    def test_mixed_separators_normalized(self, tmp_path: Path) -> None:
        """spec 混用正反斜杠（~/x/** 展开后 C:\\users\\x/y/**）仍命中。

        阶段 8 曾在 policy/patterns.expand() 修过同 bug（Windows 用户配
        ~/reports/** 全失配），并入管线后必须保住该回归。
        """
        weird = (Path.home() / "reports" / "**").as_posix()
        c = _checker(PermissionRule(f"FileWrite({weird})", RuleValue.ALLOW, RuleSource.USER))
        target = os.path.join(str(Path.home()), "reports", "a.md")
        tool = _FakeTool(
            name="FileWrite", path=target,
            perm=PermissionResult.ask("文件写入需确认"),
        )
        result = c.check(tool, {"file_path": target}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW
