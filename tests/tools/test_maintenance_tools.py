"""三个维护工具的契约：权限姿态、参数键、预览与执行的边界。

## 两条最容易写错的地方，这里逐条钉住

1. ``DiskClean`` 的 ``is_irreversible`` 必须是 False。它有隔离区可还原，
   挂上延时窗口等于每次清理都干等 30 秒（PLAN 4.6 的明确要求）。
2. 工具参数里**不许**出现 ``path`` / ``file_path`` 这类键。``file_effects.
   extract_targets`` 命中它们会给目标做写前留底——把 ``%LOCALAPPDATA%\\Temp``
   整个复制一份，比清理本身还占空间。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.core.context import ToolContext
from agent.core.maintenance.rules import parse_rule
from agent.tools.extensions import maintenance as mt


def _ctx(**settings_over) -> ToolContext:
    cfg = dict(
        maintenance_enabled=True,
        maintenance_rules_file="",
        maintenance_quarantine_days=7,
        maintenance_scan_max_dir_gb=50.0,
        maintenance_scan_timeout=30,
        maintenance_report_min_mb=0.0,
        maintenance_auto_clean_max_risk="low",
        maintenance_allow_full_scan=False,
    )
    cfg.update(settings_over)
    return ToolContext(
        workdir=".", messages=[], settings=SimpleNamespace(**cfg)
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 注册与元数据
# ---------------------------------------------------------------------------


def test_register_returns_three():
    from agent.core.tool import ToolRegistry

    reg = ToolRegistry()
    assert mt.register_maintenance_tools(reg) == 3
    for name in ("DiskReport", "DiskClean", "SystemHealth"):
        assert reg.get(name) is not None


def test_register_is_idempotent():
    """多次装配同一 registry 不该抛「已注册」。"""
    from agent.core.tool import ToolRegistry

    reg = ToolRegistry()
    mt.register_maintenance_tools(reg)
    mt.register_maintenance_tools(reg)
    assert len(reg.all()) == 3


def test_disk_clean_is_deferred_others_are_not():
    assert mt.DiskCleanTool.deferred is True
    assert mt.DiskReportTool.deferred is False
    assert mt.SystemHealthTool.deferred is False


def test_no_path_like_argument_keys():
    """防 file_effects 自动留底：参数键不能撞 _PATH_KEYS。"""
    forbidden = {"file_path", "filePath", "path", "target", "destination", "dst", "output_path"}
    for tool in (mt.DiskReportTool(), mt.DiskCleanTool(), mt.SystemHealthTool()):
        props = set((tool.input_schema.get("properties") or {}).keys())
        clash = props & forbidden
        assert not clash, f"{tool.name} 的参数键 {clash} 会触发写前留底"


# ---------------------------------------------------------------------------
# DiskReport
# ---------------------------------------------------------------------------


def test_disk_report_is_read_only_and_allowed():
    tool = mt.DiskReportTool()
    assert tool.is_read_only({}) is True
    assert tool.check_permissions({}, _ctx()).behavior.value == "allow"


def test_disk_report_returns_structured_payload(tmp_path):
    """真实扫一次（无路径的机器也能跑），只校验结构。"""
    out = _run(mt.DiskReportTool().call({"min_size_mb": 0}, _ctx()))
    assert not out.is_error
    data = out.data
    for key in ("total_reclaimable_mb", "drives", "items", "not_applicable",
                "rejected_rules", "elapsed_s", "enabled"):
        assert key in data
    assert isinstance(data["drives"], list)


# ---------------------------------------------------------------------------
# DiskClean
# ---------------------------------------------------------------------------


def test_disk_clean_without_confirm_is_preview_and_allowed():
    tool = mt.DiskCleanTool()
    args = {"ids": ["user_temp"]}
    assert tool.is_read_only(args) is True
    assert tool.is_destructive(args) is False
    assert tool.check_permissions(args, _ctx()).behavior.value == "allow"


def test_disk_clean_with_confirm_asks():
    tool = mt.DiskCleanTool()
    args = {"ids": ["user_temp"], "confirm": True}
    assert tool.is_read_only(args) is False
    assert tool.is_destructive(args) is True
    assert tool.check_permissions(args, _ctx()).behavior.value == "ask"


def test_disk_clean_irreversible_is_false():
    """有隔离区可还原 → 不许冒充不可逆，否则每次清理白等 30 秒。"""
    assert mt.DiskCleanTool().is_irreversible({"ids": ["x"], "confirm": True}) is False


def test_disk_clean_denied_when_disabled():
    res = mt.DiskCleanTool().check_permissions(
        {"ids": ["user_temp"], "confirm": True}, _ctx(maintenance_enabled=False)
    )
    assert res.behavior.value == "deny"
    assert "未启用" in str(res.reason)


def test_disk_clean_denied_on_empty_ids():
    res = mt.DiskCleanTool().check_permissions({"ids": [], "confirm": True}, _ctx())
    assert res.behavior.value == "deny"


def test_disk_clean_call_rejects_empty_ids():
    out = _run(mt.DiskCleanTool().call({"ids": []}, _ctx()))
    assert out.is_error
    assert "全清" in str(out.data)


def test_disk_clean_preview_lists_rules():
    out = _run(mt.DiskCleanTool().call({"ids": ["user_temp", "winsxs_cleanup"]}, _ctx()))
    assert not out.is_error
    data = out.data
    assert data["mode"] == "preview"
    by_id = {i["id"]: i for i in data["items"]}
    assert by_id["user_temp"]["supported"] is True
    # 命令型不可还原，预览里必须标出来
    assert by_id["winsxs_cleanup"]["supported"] is False
    assert by_id["winsxs_cleanup"]["note"]


def test_disk_clean_preview_unknown_id():
    out = _run(mt.DiskCleanTool().call({"ids": ["nope"]}, _ctx()))
    assert out.data["items"][0].get("error")


# ---------------------------------------------------------------------------
# SystemHealth
# ---------------------------------------------------------------------------


def test_health_read_actions_are_read_only():
    tool = mt.SystemHealthTool()
    for action in mt.SystemHealthTool._READ_ACTIONS:
        assert tool.is_read_only({"action": action}) is True
        assert tool.check_permissions({"action": action}, _ctx()).behavior.value == "allow"


def test_health_scan_asks():
    tool = mt.SystemHealthTool()
    assert tool.is_read_only({"action": "scan", "scan_kind": "quick"}) is False
    assert tool.check_permissions({"action": "scan"}, _ctx()).behavior.value == "ask"


def test_health_full_scan_denied_by_default():
    res = mt.SystemHealthTool().check_permissions(
        {"action": "scan", "scan_kind": "full"}, _ctx()
    )
    assert res.behavior.value == "deny"
    assert "allow_full_scan" in str(res.reason)


def test_health_full_scan_allowed_when_enabled():
    res = mt.SystemHealthTool().check_permissions(
        {"action": "scan", "scan_kind": "full"},
        _ctx(maintenance_allow_full_scan=True),
    )
    assert res.behavior.value == "ask"


def test_health_threats_includes_hint(monkeypatch):
    from agent.core.maintenance import defender

    monkeypatch.setattr(defender, "recent_threats", lambda limit=20: ([], "服务未运行"))
    out = _run(mt.SystemHealthTool().call({"action": "threats"}, _ctx()))
    assert not out.is_error
    assert out.data["threats"] == []
    assert out.data["note"]
    assert "不代表没有防护" in out.data["hint"]


def test_health_security_returns_dict(monkeypatch):
    from agent.core.maintenance import defender

    monkeypatch.setattr(
        defender, "security_status",
        lambda: defender.SecurityStatus(checked=True, any_enabled=True, primary="X", note="ok"),
    )
    out = _run(mt.SystemHealthTool().call({"action": "security"}, _ctx()))
    assert not out.is_error
    assert out.data["primary"] == "X"


def test_health_scan_status_empty():
    out = _run(mt.SystemHealthTool().call({"action": "scan_status"}, _ctx()))
    assert not out.is_error
    assert "jobs" in out.data


def test_health_save_writes_markdown(monkeypatch, tmp_path):
    from agent.core.maintenance import health as health_mod
    from agent.core.maintenance import psrun

    monkeypatch.setattr(
        psrun, "run_script",
        lambda *a, **k: psrun.PsResult(ok=False, error="stub", reason="error"),
    )
    monkeypatch.setattr(health_mod, "collect", lambda **k: health_mod.HealthReport(
        sections=[health_mod.HealthSection(key="resource", title="X", ok=False, note="stub")],
        is_admin=False, elapsed_s=0.0,
    ))
    target = tmp_path / "report.md"
    out = _run(mt.SystemHealthTool().call(
        {"action": "save", "out_file": str(target)}, _ctx()
    ))
    assert not out.is_error
    assert target.exists()
    assert "# 系统体检报告" in target.read_text(encoding="utf-8")


def test_settings_missing_does_not_crash():
    """裸上下文（无 settings）也要能跑，走默认值。"""
    ctx = ToolContext(workdir=".", messages=[], settings=None)
    out = _run(mt.DiskReportTool().call({"min_size_mb": 0}, ctx))
    assert not out.is_error
    assert out.data["enabled"] is False


def test_rule_with_missing_desc_is_rejected_early():
    """desc 缺失的规则连进清单的机会都没有。"""
    import pytest

    with pytest.raises(ValueError):
        parse_rule({
            "id": "x", "name": "X", "kind": "paths",
            "paths": ["%LOCALAPPDATA%\\Temp"],
            "category": "temp", "risk": "low", "requires_admin": False, "desc": "",
        })


# ---------------------------------------------------------------------------
# 实测回归（ 2026-10-31 本机真实对话里暴露的两个误导）
# ---------------------------------------------------------------------------


def test_disk_clean_copy_never_points_to_undo():
    """还原命令不能写成 ``/undo``。

    实测里模型把工具描述原样转述给用户（「不可用 /undo 还原」），而 ``/undo``
    走的是 W3 副作用账本，敲下去注定落空。隔离区的唯一还原入口是
    ``/clean --restore <batch_id>``。这里把所有面向用户的文案一次性盯住。
    """
    from agent.core.maintenance.cleaner import RESTORE_COMMAND

    assert RESTORE_COMMAND == "/clean --restore"
    tool = mt.DiskCleanTool()
    copies = [
        tool.description,
        tool.is_irreversible.__doc__ or "",
        tool.check_permissions({"ids": ["user_temp"], "confirm": True}, _ctx()).reason or "",
    ]
    preview = _run(tool.call({"ids": ["user_temp"]}, _ctx()))
    copies.append(preview.data["hint"])
    for text in copies:
        assert "/undo" not in text, f"还原命令写错了：{text}"


def test_disk_report_missing_top_root_says_so():
    """top_root 不存在不能静默返回空数组。

    实测：模型把用户主目录猜成 ``C:\\Users\\acefelix``（本机真实是中文用户名），
    拿到 ``top_dirs: []`` 后只能自己连跑几条 ``ls`` / ``du`` 反复试错。
    空数组看起来像「没有东西占空间」，是这里最贵的误导。
    """
    from pathlib import Path

    missing = Path.home() / "__definitely_not_here__"
    out = _run(mt.DiskReportTool().call(
        {"min_size_mb": 0, "include_top": True, "top_root": str(missing)}, _ctx(),
    ))
    assert not out.is_error
    assert out.data["top_dirs"] == []
    assert out.data["top_error"]
    # 提示里必须直接给出该传什么，否则模型只能接着猜
    assert str(Path.home()) in out.data["top_error"]