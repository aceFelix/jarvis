"""策略引擎单测 —— 四态语义 / 合并次序 / 可解释性（阶段 8 验收①）。

覆盖：无文件回落管线、allow 只放宽 ASK 不放宽 DENY、ask 收紧 ALLOW、
deny 压一切、dry_run 映射带标记 ask、多命中取更安全者、destructive /
away / path_within 匹配条件、mtime 热重载。

@author aceFelix
"""

from __future__ import annotations

import os
from datetime import datetime

from agent.core.policy.engine import PolicyEffect, PolicyEngine
from agent.core.result import PermissionBehavior, PermissionResult


def _write(p, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def test_no_file_returns_none(policy_paths):
    """无 policy.yaml → 不产生决策，原样回落管线（fail-closed）。"""
    engine = PolicyEngine(policy_paths["policy_yaml"])
    assert engine.evaluate("FileRead", {}) is None


def test_allow_relaxes_ask_only(policy_paths):
    """allow 放宽管线 ASK；管线的 ALLOW 原样保持。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 只读免打扰\n"
        "    match: {tool: [FileRead]}\n"
        "    effect: allow\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    d = engine.evaluate("FileRead", {})
    assert d is not None and d.effect == PolicyEffect.ALLOW
    relaxed = PolicyEngine.apply_to(PermissionResult.ask("默认"), d)
    assert relaxed.behavior == PermissionBehavior.ALLOW
    assert "只读免打扰" in (relaxed.reason or "")
    kept = PolicyEngine.apply_to(PermissionResult.allow("管线放行"), d)
    assert kept.behavior == PermissionBehavior.ALLOW


def test_allow_never_relaxes_deny(policy_paths):
    """管线 DENY（路径守护/危险规则）不可被策略放宽。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 全放行\n"
        "    match: {tool: [Bash]}\n"
        "    effect: allow\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    d = engine.evaluate("Bash", {"command": "rm -rf /"})
    denied = PolicyEngine.apply_to(PermissionResult.deny("危险命令"), d)
    assert denied.behavior == PermissionBehavior.DENY


def test_ask_tightens_allow(policy_paths):
    """ask 是显式收紧：管线 ALLOW 也升级成 ASK。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 外发必确认\n"
        "    match: {tool: [SendEmail]}\n"
        "    effect: ask\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    d = engine.evaluate("SendEmail", {"to": "x@y.z"})
    tightened = PolicyEngine.apply_to(PermissionResult.allow("模式放行"), d)
    assert tightened.behavior == PermissionBehavior.ASK
    assert "外发必确认" in (tightened.reason or "")


def test_dry_run_maps_to_marked_ask(policy_paths):
    """dry_run → 带 [dry_run 预览] 标记的 ASK（不破坏既有消费方）。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 先看预览\n"
        "    match: {tool: [SendEmail]}\n"
        "    effect: dry_run\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    d = engine.evaluate("SendEmail", {})
    r = PolicyEngine.apply_to(PermissionResult.ask(), d)
    assert r.behavior == PermissionBehavior.ASK
    assert "[dry_run 预览]" in (r.reason or "")


def test_multi_hit_takes_safer(policy_paths):
    """多条命中取更安全者：allow + deny 同时命中 → deny 胜。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 宽放行\n"
        "    match: {tool: [Bash]}\n"
        "    effect: allow\n"
        "  - name: 严拒绝\n"
        "    match: {tool: [Bash]}\n"
        "    effect: deny\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    d = engine.evaluate("Bash", {"command": "dir"})
    assert d.effect == PolicyEffect.DENY
    assert set(d.matched_rules) == {"宽放行", "严拒绝"}


def test_same_rank_first_declared_wins(policy_paths):
    """同级多条（两个 ask）取声明顺序在前者，保证确定性。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 先声明\n"
        "    match: {tool: [Bash]}\n"
        "    effect: ask\n"
        "  - name: 后声明\n"
        "    match: {tool: [Bash]}\n"
        "    effect: ask\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    d = engine.evaluate("Bash", {"command": "dir"})
    assert d.rule_name == "先声明"


def test_destructive_match_requires_dangerous_command(policy_paths):
    """destructive 条件：dangerous 命令命中，普通命令不命中。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 破坏性拦截\n"
        "    match: {tool: [Bash], destructive: true}\n"
        "    effect: deny\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    assert engine.evaluate("Bash", {"command": "rm -rf C:\\\\Windows"}) is not None
    assert engine.evaluate("Bash", {"command": "dir"}) is None
    # 非 shell 工具永不命中 destructive
    assert engine.evaluate("FileWrite", {"command": "rm -rf x"}) is None


def test_away_condition(policy_paths):
    """away: true 的规则仅在无人值守评估时命中。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 无人值守收紧\n"
        "    match: {tool: [Bash], away: true}\n"
        "    effect: deny\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    assert engine.evaluate("Bash", {"command": "dir"}, away=False) is None
    assert engine.evaluate("Bash", {"command": "dir"}, away=True) is not None


def test_path_within_glob(tmp_path, policy_paths):
    """path_within glob：目录内子孙命中，目录外不命中；
    正斜杠模式在 Windows 也能匹配（分隔符归一防回归）。"""
    root = tmp_path / "work" / "reports"
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 报告目录\n"
        "    match: {tool: [FileWrite], path_within: ['~/work/reports/**']}\n"
        "    effect: allow\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    # 归一化会展开 ~，测试用真实展开路径构造输入
    home = os.path.expanduser("~")
    inside = os.path.join(home, "work", "reports", "a.md")
    outside = os.path.join(home, "work", "other", "a.md")
    assert engine.evaluate("FileWrite", {"file_path": inside}) is not None
    assert engine.evaluate("FileWrite", {"file_path": outside}) is None
    assert str(root)  # 目录仅占位，不必真实存在
    # 反斜杠写法同效（用户从地址栏粘贴路径）
    assert engine.evaluate("FileWrite", {"file_path": inside.replace("/", os.sep)}) is not None


def test_mtime_hot_reload(policy_paths):
    """文件内容变更后（mtime 前进）自动重载，无需重启。"""
    p = policy_paths["policy_yaml"]
    _write(p, "rules: []\n")
    engine = PolicyEngine(p)
    assert engine.evaluate("FileRead", {}) is None
    _write(p, (
        "rules:\n"
        "  - name: 新规则\n"
        "    match: {tool: [FileRead]}\n"
        "    effect: deny\n"
    ))
    # 部分文件系统 mtime 粒度较粗，显式前进 5s 保证与缓存 mtime 必然不同
    st = os.stat(p)
    os.utime(p, (st.st_atime, st.st_mtime + 5))
    d = engine.evaluate("FileRead", {})
    assert d is not None and d.rule_name == "新规则"


def test_invalid_rule_skipped(policy_paths):
    """非法规则（缺 effect）跳过并警告，不拖垮主流程。"""
    _write(policy_paths["policy_yaml"], (
        "rules:\n"
        "  - name: 缺effect\n"
        "    match: {tool: [FileRead]}\n"
        "  - name: 合法\n"
        "    match: {tool: [FileRead]}\n"
        "    effect: allow\n"
    ))
    engine = PolicyEngine(policy_paths["policy_yaml"])
    rules = engine.rules
    assert [r.name for r in rules] == ["合法"]
