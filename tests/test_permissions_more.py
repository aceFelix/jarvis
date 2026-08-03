"""权限系统补充测试。

覆盖 test_permissions.py 未覆盖的路径：
- RuleSet 排序/合并、load_rules YAML 加载（含异常与容错）
- PermissionChecker 五段判定管线（模式覆写 / 规则匹配 / 路径守护 / 默认 ASK）
- shell_classifier 的边界分支（get_command_head / 重定向 / 链式命令 / matches_spec）
- 路径守护的 home 根 / .config 等额外 deny 场景与符号链接逃逸

@author aceFelix
"""

from __future__ import annotations

import os
import pathlib
from pathlib import Path

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior, PermissionResult, ValidationResult
from agent.core.tool import PermissionMatcher, Tool
from agent.permissions.checker import PermissionChecker, classify_tool_for_shell
from agent.permissions.modes import (
    AUTO_ALLOW_EDIT_TOOLS,
    PLAN_ALLOWED_TOOLS,
    PermissionMode,
    parse_mode,
)
from agent.permissions.path_guard import is_dangerous_write_path, is_symlink_escape
from agent.permissions.rules import (
    PermissionRule,
    RuleSet,
    RuleSource,
    RuleValue,
    load_default_rules,
    load_rules,
)
from agent.permissions.shell_classifier import classify, get_command_head, matches_spec


class _FakeTool(Tool):
    """测试用假工具：可控的只读性 / 路径 / 权限结果 / 规则匹配器。

    @author aceFelix
    """

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
        validate_ok: bool = True,
    ) -> None:
        self.name = name
        self._read_only = read_only
        self._path = path
        self._perm = perm
        self._matcher = matcher
        self._validate_ok = validate_ok

    async def call(self, args: dict, ctx: ToolContext) -> PermissionResult:  # type: ignore[override]
        """call 不参与权限测试，仅满足抽象方法要求。"""
        return PermissionResult.allow("not used")

    def is_read_only(self, args: dict) -> bool:
        return self._read_only

    def check_permissions(self, args: dict, ctx: ToolContext) -> PermissionResult:
        if self._perm is not None:
            return self._perm
        # 与真实工具一致的默认行为：ASK（无工具特判规则）
        return PermissionResult.ask("no tool-specific permission rule")

    def get_path(self, args: dict) -> str:
        return self._path

    def validate_input(self, args: dict, ctx: ToolContext) -> ValidationResult:
        if self._validate_ok:
            return ValidationResult.pass_()
        return ValidationResult.fail("参数非法")

    def prepare_permission_matcher(self, args: dict) -> PermissionMatcher | None:
        return self._matcher


def _ctx(tmp_path: Path) -> ToolContext:
    """构造一个以 tmp_path 为工作目录的测试上下文。"""
    return ToolContext(workdir=str(tmp_path), messages=[])


# ─────────────────────────────────────────────────────────────
# RuleSet / load_rules / load_default_rules
# ─────────────────────────────────────────────────────────────


class TestRuleSet:
    """规则集合的排序与合并。"""

    def test_sorted_by_priority_then_deny(self) -> None:
        """优先级降序；同优先级 deny > ask > allow。"""
        rs = RuleSet()
        rs.add(PermissionRule("Bash", RuleValue.ALLOW, RuleSource.SESSION))
        rs.add(PermissionRule("Bash", RuleValue.ASK, RuleSource.SESSION))
        rs.add(PermissionRule("Bash", RuleValue.DENY, RuleSource.SESSION))
        rs.add(PermissionRule("Bash", RuleValue.ASK, RuleSource.USER))
        rs.add(PermissionRule("Bash", RuleValue.DENY, RuleSource.ENTERPRISE))

        sorted_rules = rs.sorted_for_match()
        # enterprise(100) > user(50) > session(10)
        assert sorted_rules[0].source == RuleSource.ENTERPRISE
        assert sorted_rules[1].source == RuleSource.USER
        rest = [r.value for r in sorted_rules[2:]]
        assert rest == [RuleValue.DENY, RuleValue.ASK, RuleValue.ALLOW]

    def test_priority_property(self) -> None:
        """priority 属性按来源返回对应数值。"""
        assert PermissionRule("Bash", RuleValue.ALLOW, RuleSource.ENTERPRISE).priority == 100
        assert PermissionRule("Bash", RuleValue.ALLOW, RuleSource.USER).priority == 50
        assert PermissionRule("Bash", RuleValue.ALLOW, RuleSource.PROJECT).priority == 30
        assert PermissionRule("Bash", RuleValue.ALLOW, RuleSource.SESSION).priority == 10

    def test_merge(self) -> None:
        """merge 拼接两个规则集（多源加载语义）。"""
        a = RuleSet([PermissionRule("A", RuleValue.ALLOW)])
        b = RuleSet([PermissionRule("B", RuleValue.DENY)])
        merged = a.merge(b)
        assert len(merged.rules) == 2
        assert merged.rules[0].pattern == "A"
        assert merged.rules[1].pattern == "B"

    def test_add(self) -> None:
        """add 追加单条规则。"""
        rs = RuleSet()
        rs.add(PermissionRule("Bash", RuleValue.ASK))
        assert len(rs.rules) == 1


class TestLoadRules:
    """load_rules YAML 加载。"""

    def test_load_valid_yaml(self, tmp_path: Path) -> None:
        """合法的 YAML 规则文件应完整加载（含来源解析）。"""
        p = tmp_path / "rules.yaml"
        p.write_text(
            "rules:\n"
            '  - pattern: "Bash(git *)"\n'
            "    value: allow\n"
            "    source: user\n"
            '  - pattern: "Read"\n'
            "    value: deny\n"
            "  - value: ask\n",  # 无 pattern 键的条目应被跳过
            encoding="utf-8",
        )
        rs = load_rules(p)
        assert len(rs.rules) == 2
        assert rs.rules[0].pattern == "Bash(git *)"
        assert rs.rules[0].value == RuleValue.ALLOW
        assert rs.rules[0].source == RuleSource.USER
        # source 缺省 → PROJECT
        assert rs.rules[1].source == RuleSource.PROJECT

    def test_load_invalid_value_raises(self, tmp_path: Path) -> None:
        """非法规则值应抛出 ValueError。"""
        p = tmp_path / "rules.yaml"
        p.write_text(
            'rules:\n  - pattern: "Bash"\n    value: maybe\n', encoding="utf-8"
        )
        with pytest.raises(ValueError):
            load_rules(p)

    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """文件不存在时返回空 RuleSet（容错）。"""
        rs = load_rules(tmp_path / "nope.yaml")
        assert len(rs.rules) == 0

    def test_load_empty_yaml(self, tmp_path: Path) -> None:
        """空 YAML / 无 rules 键 → 空 RuleSet。"""
        p = tmp_path / "rules.yaml"
        p.write_text("# 只有注释\n", encoding="utf-8")
        assert len(load_rules(p).rules) == 0
        p.write_text("rules: []\n", encoding="utf-8")
        assert len(load_rules(p).rules) == 0

    def test_load_invalid_source_defaults_project(self, tmp_path: Path) -> None:
        """非法 source 值回退到 PROJECT。"""
        p = tmp_path / "rules.yaml"
        p.write_text(
            'rules:\n  - pattern: "Bash"\n    value: allow\n    source: nope\n',
            encoding="utf-8",
        )
        rs = load_rules(p)
        assert rs.rules[0].source == RuleSource.PROJECT

    def test_load_real_config(self) -> None:
        """项目自带 permissions.yaml 应可正常加载。"""
        cfg = Path(__file__).resolve().parent.parent / "agent" / "configs" / "permissions.yaml"
        if not cfg.exists():
            return
        rs = load_rules(cfg)
        assert len(rs.rules) > 0
        assert all(r.value in (RuleValue.ALLOW, RuleValue.DENY, RuleValue.ASK) for r in rs.rules)


class TestLoadDefaultRules:
    """硬编码危险规则。"""

    def test_default_rules_are_enterprise_deny(self) -> None:
        """默认规则全部是 enterprise 来源的 deny。"""
        rs = load_default_rules()
        assert len(rs.rules) >= 8
        assert all(r.value == RuleValue.DENY for r in rs.rules)
        assert all(r.source == RuleSource.ENTERPRISE for r in rs.rules)

    def test_default_rules_contain_ssh_and_rm(self) -> None:
        """关键危险规则存在：写 ~/.ssh、rm -rf /。"""
        rs = load_default_rules()
        patterns = {r.pattern for r in rs.rules}
        assert "Write(~/.ssh/**)" in patterns
        assert "Bash(rm -rf /*)" in patterns


# ─────────────────────────────────────────────────────────────
# PermissionChecker 判定管线
# ─────────────────────────────────────────────────────────────


class TestCheckerDefaultMode:
    """default 模式：严格，未命中 allow 一律 ASK。"""

    def test_default_ask(self, tmp_path: Path) -> None:
        """无规则命中且无工具特判 → ASK（fail-closed）。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        result = checker.check(_FakeTool(name="Bash"), {"command": "date"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ASK
        assert "无 allow 规则命中" in (result.reason or "")

    def test_tool_allow_kept(self, tmp_path: Path) -> None:
        """工具特判 ALLOW 应保留。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Read", perm=PermissionResult.allow("readonly tool"))
        result = checker.check(tool, {"file_path": "a.txt"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_input_validation_fail_denies(self, tmp_path: Path) -> None:
        """输入校验失败直接 DENY。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Bash", validate_ok=False)
        result = checker.check(tool, {"command": "x"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY
        assert "输入校验失败" in (result.reason or "")

    def test_rule_allow_matches(self, tmp_path: Path) -> None:
        """allow 规则命中 → ALLOW。

        注意：工具特判与 allow 规则同为 ALLOW 时，_stricter 取先到的工具结果
        （reason 显示工具特判文案），故额外用白盒 _match_rules 确认规则本身命中。
        """
        rules = RuleSet([PermissionRule("Bash(git *)", RuleValue.ALLOW, RuleSource.USER)])
        checker = PermissionChecker(rules=rules, mode=PermissionMode.DEFAULT)
        tool = _FakeTool(
            name="Bash",
            matcher=PermissionMatcher("Bash", ["git status"]),
            perm=PermissionResult.allow("readonly command"),
        )
        result = checker.check(tool, {"command": "git status"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW
        # 白盒：直接验证规则匹配结果
        rule_result = checker._match_rules(tool, {"command": "git status"})
        assert rule_result is not None
        assert rule_result.behavior == PermissionBehavior.ALLOW
        assert "规则命中" in (rule_result.reason or "")

    def test_rule_deny_wins_same_priority(self, tmp_path: Path) -> None:
        """同优先级 deny > allow（更安全者胜出）。"""
        rules = RuleSet(
            [
                PermissionRule("Bash", RuleValue.ALLOW, RuleSource.USER),
                PermissionRule("Bash(rm *)", RuleValue.DENY, RuleSource.USER),
            ]
        )
        checker = PermissionChecker(rules=rules, mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Bash", matcher=PermissionMatcher("Bash", ["rm -rf /tmp"]))
        result = checker.check(tool, {"command": "rm -rf /tmp"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY

    def test_rule_ask_forces_ask(self, tmp_path: Path) -> None:
        """ask 规则强制 ASK（即使工具特判已 ALLOW）。"""
        rules = RuleSet([PermissionRule("Bash(git push*)", RuleValue.ASK, RuleSource.USER)])
        checker = PermissionChecker(rules=rules, mode=PermissionMode.DEFAULT)
        tool = _FakeTool(
            name="Bash",
            matcher=PermissionMatcher("Bash", ["git push origin main"]),
            perm=PermissionResult.allow("readonly command"),
        )
        result = checker.check(tool, {"command": "git push origin main"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ASK
        assert "规则强制询问" in (result.reason or "")

    def test_default_deny_rules_always_active(self, tmp_path: Path) -> None:
        """未传 rules 时，硬编码危险规则（rm -rf /*）依然生效。"""
        checker = PermissionChecker(mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Bash", matcher=PermissionMatcher("Bash", ["rm -rf /*"]))
        result = checker.check(tool, {"command": "rm -rf /*"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY

    def test_fnmatch_fallback_without_matcher(self, tmp_path: Path) -> None:
        """无 matcher 的工具退化为工具名 + file_path 通配匹配。"""
        rules = RuleSet([PermissionRule("Read(src/**)", RuleValue.ALLOW, RuleSource.PROJECT)])
        checker = PermissionChecker(rules=rules, mode=PermissionMode.DEFAULT)
        # 工具特判 ALLOW + 规则命中 → ALLOW（_stricter 同等级取工具结果，reason 为工具文案）
        tool = _FakeTool(name="Read", perm=PermissionResult.allow("readonly tool"))
        result = checker.check(tool, {"file_path": "src/main.py"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW
        # 白盒确认 fnmatch 规则确实命中
        rule_result = checker._match_rules(tool, {"file_path": "src/main.py"})
        assert rule_result is not None
        assert "规则命中" in (rule_result.reason or "")
        # 工具特判 ASK + 规则未命中 → 默认 ASK
        tool2 = _FakeTool(name="Read")
        result2 = checker.check(tool2, {"file_path": "/etc/passwd"}, _ctx(tmp_path))
        assert result2.behavior == PermissionBehavior.ASK

    def test_user_rule_overrides_project_allow(self, tmp_path: Path) -> None:
        """高优先级（user deny）覆盖低优先级（project allow）。"""
        rules = RuleSet(
            [
                PermissionRule("Write(*)", RuleValue.ALLOW, RuleSource.PROJECT),
                PermissionRule("Write(~/.ssh/**)", RuleValue.DENY, RuleSource.USER),
            ]
        )
        checker = PermissionChecker(rules=rules, mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Write", matcher=PermissionMatcher("Write", ["~/.ssh/id_rsa"]))
        result = checker.check(tool, {"file_path": "~/.ssh/id_rsa"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY


class TestCheckerPathGuard:
    """路径守护（写类工具的目标路径检查）。"""

    def test_path_guard_deny_sensitive(self, tmp_path: Path) -> None:
        """写入敏感目录 → DENY。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Write", perm=PermissionResult.allow("x"), path="~/.ssh/id_rsa")
        result = checker.check(tool, {"file_path": "~/.ssh/id_rsa"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY
        assert "路径守护拦截" in (result.reason or "")

    def test_path_guard_ask_outside_workdir(self, tmp_path: Path) -> None:
        """写工作目录之外 → ASK（软提醒，可被 yolo 放宽）。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Write", perm=PermissionResult.allow("x"), path="../outside.txt")
        result = checker.check(tool, {"path": "../outside.txt"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ASK
        assert "路径提醒" in (result.reason or "")

    def test_path_guard_skip_readonly(self, tmp_path: Path) -> None:
        """只读工具不做路径守护。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Read", read_only=True, path="~/.ssh/config")
        result = checker.check(tool, {"file_path": "~/.ssh/config"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ASK  # 未走路径守护，落到默认 ASK

    def test_path_guard_skip_without_path(self, tmp_path: Path) -> None:
        """没有路径信息的写工具跳过路径守护。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Write", perm=PermissionResult.allow("x"))
        result = checker.check(tool, {"command": "x"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_symlink_escape_deny(self, tmp_path: Path, monkeypatch) -> None:
        """符号链接指向工作目录外 → DENY。

        Windows 下 is_symlink_escape 的逐段循环从 Path("/") 起步（无盘符），
        无法与带盘符的 link_path 直接比较，故用路径后缀 + 假 resolve 模拟。
        """
        workdir = str(tmp_path)
        sep = os.sep
        outside = Path(workdir).parent / "outside_target"

        real_resolve = pathlib.Path.resolve
        real_is_symlink = pathlib.Path.is_symlink

        def fake_resolve(self, strict: bool = False):
            if str(self).endswith(sep + "link"):
                return outside
            return real_resolve(self, strict=strict)

        def fake_is_symlink(self):
            if str(self).endswith(sep + "link"):
                return True
            return real_is_symlink(self)

        monkeypatch.setattr(pathlib.Path, "resolve", fake_resolve)
        monkeypatch.setattr(pathlib.Path, "is_symlink", fake_is_symlink)

        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        tool = _FakeTool(name="Write", perm=PermissionResult.allow("x"), path="link/file.txt")
        result = checker.check(tool, {"file_path": "link/file.txt"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY
        assert "符号链接逃逸" in (result.reason or "")

    def test_symlink_escape_no_symlink(self, tmp_path: Path) -> None:
        """无符号链接的正常路径不触发逃逸。"""
        workdir = str(tmp_path)
        esc, reason = is_symlink_escape(workdir, "normal/file.txt")
        assert esc is False
        assert reason == ""


class TestCheckerModes:
    """模式覆写（plan / accept_edits / yolo）。"""

    def test_plan_allows_readonly(self, tmp_path: Path) -> None:
        """plan 模式放行只读工具。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.PLAN)
        tool = _FakeTool(name="Bash", read_only=True)
        result = checker.check(tool, {"command": "ls"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_plan_allows_whitelisted_tool(self, tmp_path: Path) -> None:
        """plan 模式放行白名单工具（即使不自报只读）。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.PLAN)
        tool = _FakeTool(name="FileRead")
        result = checker.check(tool, {"file_path": "a.txt"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_plan_denies_write(self, tmp_path: Path) -> None:
        """plan 模式拒绝写操作。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.PLAN)
        tool = _FakeTool(name="Bash")
        result = checker.check(tool, {"command": "rm x"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY
        assert "plan 模式禁止写操作" in (result.reason or "")

    def test_accept_edits_auto_allow(self, tmp_path: Path) -> None:
        """accept_edits 自动放行文件编辑工具。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.ACCEPT_EDITS)
        tool = _FakeTool(name="FileEdit")
        result = checker.check(tool, {"file_path": "a.txt"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_accept_edits_other_tool_ask(self, tmp_path: Path) -> None:
        """accept_edits 对非编辑工具仍走默认 ASK。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.ACCEPT_EDITS)
        tool = _FakeTool(name="Bash")
        result = checker.check(tool, {"command": "ls"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ASK

    def test_yolo_auto_allow(self, tmp_path: Path) -> None:
        """yolo 模式自动放行非 deny 操作。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.YOLO)
        tool = _FakeTool(name="Bash")
        result = checker.check(tool, {"command": "some command"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.ALLOW

    def test_yolo_does_not_relax_deny(self, tmp_path: Path) -> None:
        """yolo 模式下路径守护 DENY 依然生效。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.YOLO)
        tool = _FakeTool(name="Write", path="~/.ssh/id_rsa")
        result = checker.check(tool, {"file_path": "~/.ssh/id_rsa"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY

    def test_yolo_does_not_relax_deny_rule(self, tmp_path: Path) -> None:
        """yolo 模式下 deny 规则依然生效。"""
        rules = RuleSet([PermissionRule("Bash(rm *)", RuleValue.DENY, RuleSource.USER)])
        checker = PermissionChecker(rules=rules, mode=PermissionMode.YOLO)
        tool = _FakeTool(name="Bash", matcher=PermissionMatcher("Bash", ["rm -rf /tmp"]))
        result = checker.check(tool, {"command": "rm -rf /tmp"}, _ctx(tmp_path))
        assert result.behavior == PermissionBehavior.DENY

    def test_with_mode_returns_new_checker(self, tmp_path: Path) -> None:
        """with_mode 返回换模式的副本，原 checker 不受影响。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.DEFAULT)
        plan_checker = checker.with_mode(PermissionMode.PLAN)
        assert checker.mode == PermissionMode.DEFAULT
        assert plan_checker.mode == PermissionMode.PLAN

    def test_mode_property(self, tmp_path: Path) -> None:
        """mode 属性返回当前模式。"""
        checker = PermissionChecker(rules=RuleSet(), mode=PermissionMode.YOLO)
        assert checker.mode == PermissionMode.YOLO


class TestModeModule:
    """modes.py 补充。"""

    def test_parse_accept_edits(self) -> None:
        assert parse_mode("accept_edits") == PermissionMode.ACCEPT_EDITS

    def test_parse_empty_none_and_case(self) -> None:
        assert parse_mode("") == PermissionMode.DEFAULT
        assert parse_mode(None) == PermissionMode.DEFAULT
        assert parse_mode("YOLO") == PermissionMode.YOLO

    def test_tool_sets(self) -> None:
        assert "FileEdit" in AUTO_ALLOW_EDIT_TOOLS
        assert "FileWrite" in AUTO_ALLOW_EDIT_TOOLS
        assert "FileRead" in PLAN_ALLOWED_TOOLS
        assert "Glob" in PLAN_ALLOWED_TOOLS
        assert "Grep" in PLAN_ALLOWED_TOOLS


# ─────────────────────────────────────────────────────────────
# classify_tool_for_shell 便捷函数
# ─────────────────────────────────────────────────────────────


class TestClassifyToolForShell:
    """checker.classify_tool_for_shell。"""

    def test_bash_dangerous(self) -> None:
        tool = _FakeTool(name="Bash")
        assert classify_tool_for_shell(tool, {"command": "rm -rf /tmp"}) == "dangerous"

    def test_bash_readonly(self) -> None:
        tool = _FakeTool(name="PowerShell")
        assert classify_tool_for_shell(tool, {"command": "ls"}) == "readonly"

    def test_non_shell_tool(self) -> None:
        tool = _FakeTool(name="Read")
        assert classify_tool_for_shell(tool, {"command": "ls"}) == "unknown"


# ─────────────────────────────────────────────────────────────
# path_guard 补充
# ─────────────────────────────────────────────────────────────


class TestPathGuardMore:
    """is_dangerous_write_path 的额外路径。"""

    def test_home_root_deny(self) -> None:
        """直接写 home 根目录 → deny。"""
        workdir = "/home/user/projects"
        level, reason = is_dangerous_write_path(workdir, "~")
        assert level == "deny"
        assert "home 根" in reason

    def test_config_dir_deny(self) -> None:
        """.config 目录也在危险名单。"""
        workdir = "/home/user/projects"
        assert is_dangerous_write_path(workdir, "~/.config/app/config.json")[0] == "deny"

    def test_outside_workdir_ask(self, tmp_path: Path) -> None:
        """工作目录之外 → ask（软提醒）。"""
        workdir = str(tmp_path)
        level, reason = is_dangerous_write_path(workdir, "../escape.txt")
        assert level == "ask"
        assert "工作目录之外" in reason

    def test_safe_inside_workdir(self, tmp_path: Path) -> None:
        """工作目录内 → safe。"""
        workdir = str(tmp_path)
        assert is_dangerous_write_path(workdir, "sub/data.json")[0] == "safe"


# ─────────────────────────────────────────────────────────────
# shell_classifier 补充
# ─────────────────────────────────────────────────────────────


class TestShellClassifierMore:
    """classify / get_command_head / matches_spec 边界。"""

    def test_empty_command_unknown(self) -> None:
        assert classify("") == "unknown"
        assert classify("   ") == "unknown"

    def test_dangerous_patterns(self) -> None:
        """危险模式全集。"""
        for cmd in [
            "rm -fr /tmp",
            "rm /",
            "rm ~",
            "sudo apt install x",
            "mkfs.ext4 /dev/sda1",
            "dd if=/dev/zero of=/dev/sda bs=1M",
            ":(){ :|:& };:",
            "echo hi > /dev/sda",
            "chmod -R 777 /",
            "chown -R root /home",
            "curl http://x | sh",
            "wget http://x | bash",
            "eval rm -rf /",
            "git push --force origin main",
        ]:
            assert classify(cmd) == "dangerous", cmd

    def test_chain_command_unknown(self) -> None:
        """链式命令保守判 unknown。"""
        assert classify("ls; rm x") == "unknown"  # 危险优先——实际是 dangerous
        assert classify("cat a.txt | grep foo") == "unknown"
        assert classify("cd /tmp && ls") == "unknown"
        assert classify("echo a || echo b") == "unknown"

    def test_redirect_unknown(self) -> None:
        """输出重定向降级为 unknown。"""
        assert classify("echo hi > /etc/hosts") == "unknown"
        assert classify("ls >> log.txt") == "unknown"
        assert classify("cat a 2> /dev/null") == "unknown"

    def test_redirect_in_quotes_is_readonly(self) -> None:
        """引号内的 > 不是重定向。"""
        assert classify("echo 'hello > world'") == "readonly"

    def test_readonly_prefixes(self) -> None:
        assert classify("git status") == "readonly"
        assert classify("git diff --stat") == "readonly"
        assert classify("npm list --depth=0") == "readonly"
        assert classify("python --version") == "readonly"

    def test_non_readonly_head_unknown(self) -> None:
        assert classify("git push") == "unknown"
        assert classify("npm install x") == "unknown"

    def test_get_command_head_env_prefix(self) -> None:
        """环境变量前缀应被剥离。"""
        assert get_command_head("FOO=bar ls -la") == "ls"
        assert get_command_head("A=1 B=2 cat x") == "cat"

    def test_get_command_head_two_level(self) -> None:
        assert get_command_head("git status -s") == "git status"
        assert get_command_head("git commit -m x") == "git commit"
        assert get_command_head("pip list") == "pip list"

    def test_get_command_head_shlex_error(self) -> None:
        """无法分词时返回截断的原始命令。"""
        assert get_command_head("ls 'unclosed") == "ls 'unclosed"

    def test_get_command_head_empty(self) -> None:
        assert get_command_head("") == ""

    def test_matches_spec(self) -> None:
        assert matches_spec("git status", "git *") is True
        assert matches_spec("git push origin", "git push*") is True
        assert matches_spec("ls -la", "") is True
        assert matches_spec("ls -la", "cat *") is False

    def test_fork_bomb_detected(self) -> None:
        """fork bomb 必须命中危险模式。"""
        assert classify(":(){ :|:& };:") == "dangerous"
