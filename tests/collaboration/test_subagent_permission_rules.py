"""后台值守步骤继承宿主权限规则：口径必须与前台一致，但绝不隐式 yolo。

盯的是 2026-10-22 实机走查那条"前台已放行、后台仍被拒"：``run_subagent`` 过去
写死空 ``RuleSet``，用户在 ``permissions.yaml`` 里配好的一切对后台步骤无效。
本文件把生产组件串起来验证继承真的生效，同时钉住一条保守边界——**裸工具名
的 allow 规则放宽不了工具自己特判的 ASK**（``FileWrite`` 恰好无条件 ASK），
只有带路径 spec 的规则命中同一目标才放宽；否则一条 "FileWrite: allow" 就等于
给无人值守的后台开了 yolo，这是最坏的一类"方便"。

@author aceFelix
"""
from __future__ import annotations

import textwrap
from types import SimpleNamespace

import pytest

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior
from agent.core.tasks.spawn import _host_rules
from agent.permissions.checker import PermissionChecker
from agent.permissions.modes import PermissionMode
from agent.permissions.rules import RuleSet
from agent.tools.file_ops.file_write import FileWriteTool

_WRITE_ARGS = {"file_path": "weekly.txt", "content": "本周一切正常\n"}


def _ctx(workdir: str) -> ToolContext:
    return ToolContext(workdir=workdir, messages=[], permission_mode="default")


def _behavior(rules: RuleSet, tmp_path) -> str:
    """按任务脊柱的口径建 checker 判定一次：mode 固定 DEFAULT（不放宽模式）。"""
    checker = PermissionChecker(rules=rules, mode=PermissionMode.DEFAULT)
    return checker.check(FileWriteTool(), dict(_WRITE_ARGS), _ctx(str(tmp_path))).behavior


class TestHostRulesLoading:
    """``_host_rules`` 是"继承"这条路的入口，它必须真去读用户配的文件。"""

    def test_loads_from_permissions_file(self, tmp_path):
        f = tmp_path / "permissions.yaml"
        f.write_text(
            textwrap.dedent("""
                rules:
                  - pattern: "FileWrite(**)"
                    value: allow
                    source: project
            """),
            encoding="utf-8",
        )
        rules = _host_rules(SimpleNamespace(permissions_file=str(f)))
        assert rules is not None
        assert len(getattr(rules, "rules", rules)) >= 1

    def test_default_ask_for_filewrite_without_rules(self, tmp_path):
        """没规则时 FileWrite 就是 ASK：这是实机那一步被拒的原点。"""
        assert _behavior(RuleSet(), tmp_path) == PermissionBehavior.ASK


class TestInheritanceFlipsDecision:
    """继承生效后，同一份规则在前后台得出同一个结论。"""

    def test_spec_allow_rule_relaxes_tool_ask(self, tmp_path):
        """带路径 spec 的 allow 命中目标 → 后台步骤可写，且只在这条路径上可写。"""
        f = tmp_path / "permissions.yaml"
        f.write_text(
            textwrap.dedent("""
                rules:
                  - pattern: "FileWrite(**)"
                    value: allow
                    source: project
            """),
            encoding="utf-8",
        )
        rules = _host_rules(SimpleNamespace(permissions_file=str(f)))
        assert _behavior(rules, tmp_path) == PermissionBehavior.ALLOW

    def test_bare_toolname_allow_does_not_imply_yolo(self, tmp_path):
        """裸名 allow 不能放宽工具特判的 ASK：否则等于给无人值守后台开 yolo。"""
        f = tmp_path / "permissions.yaml"
        f.write_text(
            textwrap.dedent("""
                rules:
                  - pattern: "FileWrite"
                    value: allow
                    source: project
            """),
            encoding="utf-8",
        )
        rules = _host_rules(SimpleNamespace(permissions_file=str(f)))
        assert _behavior(rules, tmp_path) == PermissionBehavior.ASK, \
            "裸名 allow 放宽了工具 ASK，就是把后台步骤变成 yolo"

    def test_deny_rule_still_wins(self, tmp_path):
        """deny 永不可放宽：规则说拒绝就是拒绝，后台也一样。"""
        f = tmp_path / "permissions.yaml"
        f.write_text(
            textwrap.dedent("""
                rules:
                  - pattern: "FileWrite(**)"
                    value: deny
                    source: project
            """),
            encoding="utf-8",
        )
        rules = _host_rules(SimpleNamespace(permissions_file=str(f)))
        assert _behavior(rules, tmp_path) == PermissionBehavior.DENY

    def test_subagent_checker_uses_passed_rules(self, monkeypatch, tmp_path):
        """``run_subagent`` 必须拿传入的规则建 checker，而不是自己 new 一个空的。

        直接把构造点打桩出来看参数：这一步是"继承"到底有没有接上的分水岭。
        """
        import agent.collaboration.subagent as sub_mod

        seen: dict = {}

        class _Recorder:
            def __init__(self, rules=None, mode=None) -> None:
                seen["rules"] = rules
                seen["mode"] = mode

        monkeypatch.setattr(sub_mod, "PermissionChecker", _Recorder)
        # 在 checker 建好之后断掉（ToolOrchestrator 是下一句）：本用例只关心构造参数
        monkeypatch.setattr(
            sub_mod, "ToolOrchestrator",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("到此为止")),
        )
        with pytest.raises(RuntimeError):
            import asyncio

            asyncio.run(sub_mod.run_subagent(
                SimpleNamespace(agent_type="t", system_prompt="x",
                                allowed_tools=None, max_iterations=1, model=""),
                "写个文件",
                provider=object(), workdir=str(tmp_path),
                permission_mode=PermissionMode.DEFAULT,
                permission_rules="SENTINEL",
            ))
        assert seen["rules"] == "SENTINEL", "子代理没继承宿主规则，仍是空 RuleSet"
        assert seen["mode"] == PermissionMode.DEFAULT, "继承规则不等于放宽模式"
