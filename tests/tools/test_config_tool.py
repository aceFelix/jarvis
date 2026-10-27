"""``ConfigList`` / ``ConfigSet`` 两个工具测试（阶段 12 P1）。

这层最容易出的两个错都不是"报错"，而是"看起来做对了"：

1. **分级没接到权限管线**：``tier=confirm`` 若只在自己代码里打印一句"请确认"，
   用户根本没被问过；``forbidden`` 若只靠 ``call()`` 里拦一道，权限层一被绕过就
   等于放开。所以这两条都从 ``check_permissions`` 的返回值上钉。
2. **写了一半就说成功**：多键补丁里第三项校验失败时，前两项已经落盘了——用户问到
   底哪几项生效，谁也答不上来。用例里用"整批不动"钉这一点。

落盘一律把 ``Path.home`` 重定向到 tmp_path（与 ``test_percept_settings`` 同口径），
账本走 ``configure_journal(path=...)``，绝不碰真实 ``~/.jarvis``。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.config.config_specs import (
    APPLY_PERCEPT,
    clear_apply_hooks,
    clear_change_listeners,
    register_apply_hook,
    register_change_listener,
)
from agent.core.actions.journal import (
    STATE_APPLIED,
    UNDO_CONFIG,
    configure_journal,
    get_journal,
    undo_record,
)
from agent.core.result import PermissionBehavior
from agent.core.tool import build_default_registry
from agent.tools.config_tool import ConfigListTool, ConfigSetTool


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """把 ``Path.home()`` 指到 tmp_path：工具默认写用户级 settings.toml。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path):
    """副作用账本换到临时文件，并在用例结束后清掉生效钩子与变更监听器。"""
    configure_journal(path=tmp_path / "actions.jsonl")
    yield
    clear_apply_hooks()
    clear_change_listeners()
    configure_journal(enabled=False)


@pytest.fixture()
def settings():
    """真实的 Settings 实例（用例改的是内存副本，落盘进临时 home）。"""
    from agent.config.settings import Settings

    return Settings()


@pytest.fixture()
def ctx(settings, tmp_path):
    return SimpleNamespace(
        settings=settings,
        workdir=str(tmp_path),
        permission_mode="default",
        session_name="s-config",
        extra={},
    )


def _run(tool, args, ctx):
    return asyncio.run(tool.call(args, ctx))


def _settings_file() -> Path:
    return Path.home() / ".jarvis" / "settings.toml"


def _seed_percept_sources(ctx, *attrs: str) -> None:
    """把感知逐源先置成开，让随后"关掉它"成为一次真变更。

    逐源开关的出厂缺省全是 False，不先置 True 的话"改成 False"会被无变更逻辑
    直接挡回去（那是对的行为，只是这些用例想验的是生效分派）。
    """
    for attr in attrs or ("percept_window_enabled",):
        setattr(ctx.settings, attr, True)


# ---- 注册与形状 ----


def test_both_tools_are_in_the_default_registry():
    """进默认工具表才算模型侧入口（少这一步，后面全白做）。"""
    registry = build_default_registry()
    assert "ConfigList" in registry and "ConfigSet" in registry


def test_list_tool_is_read_only_and_set_tool_is_not():
    assert ConfigListTool().is_read_only({}) is True
    assert ConfigSetTool().is_read_only({}) is False


# ---- 权限分级 ----


def test_forbidden_key_is_denied_by_permission_layer(ctx):
    """沙箱与凭据走 DENY，且理由要能告诉用户正确改法。"""
    res = ConfigSetTool().check_permissions({"patch": {"sandbox_enabled": True}}, ctx)
    assert res.behavior == PermissionBehavior.DENY
    assert "提权" in (res.reason or "")


def test_credential_key_is_denied(ctx):
    res = ConfigSetTool().check_permissions({"patch": {"api_key": "sk-x"}}, ctx)
    assert res.behavior == PermissionBehavior.DENY


def test_confirm_tier_asks_instead_of_self_approving(ctx):
    """打扰预算这类必须先问一句：模型单方面改等于给自己放宽护栏。"""
    res = ConfigSetTool().check_permissions({"patch": {"daily_max": 50}}, ctx)
    assert res.behavior == PermissionBehavior.ASK
    assert "每日播报上限" in (res.reason or "")


def test_auto_tier_is_allowed(ctx):
    res = ConfigSetTool().check_permissions({"patch": {"tts_volume": 30}}, ctx)
    assert res.behavior == PermissionBehavior.ALLOW


def test_mixed_patch_asks_because_one_item_needs_confirm(ctx):
    """一锅端里混了 confirm 项 → 整批要问（不能只问一半）。"""
    res = ConfigSetTool().check_permissions(
        {"patch": {"tts_volume": 30, "quiet_hours": "23:00-07:00"}}, ctx
    )
    assert res.behavior == PermissionBehavior.ASK


# ---- 校验 ----


@pytest.mark.parametrize(
    "args,frag",
    [
        ({}, "patch 必须是非空对象"),
        ({"patch": {}}, "patch 必须是非空对象"),
        ({"patch": "不是对象"}, "patch 必须是非空对象"),
        ({"patch": {"no_such_key": 1}}, "不认识的配置项"),
    ],
)
def test_validate_input_rejects_bad_patch(ctx, args, frag):
    check = ConfigSetTool().validate_input(args, ctx)
    assert check.ok is False and frag in (check.message or "")


def test_validate_input_accepts_forbidden_key_so_deny_can_explain(ctx):
    """禁改键要过校验、走到权限层被 DENY——在校验层就报"不认识"，用户得不到理由。"""
    check = ConfigSetTool().validate_input({"patch": {"sandbox_enabled": True}}, ctx)
    assert check.ok is True


def test_bad_value_reports_the_key(ctx):
    res = _run(ConfigSetTool(), {"patch": {"tts_volume": 999}}, ctx)
    assert res.is_error is True and "tts_volume" in res.data


def test_whole_batch_is_validated_before_anything_is_written(ctx):
    """第二项非法 → 第一项也不许落盘（否则用户不知道哪几项生效了）。"""
    res = _run(
        ConfigSetTool(),
        {"patch": {"tts_volume": 30, "tts_speech_rate": "快一点"}},
        ctx,
    )
    assert res.is_error is True
    assert not _settings_file().exists()          # 一个字节都没写
    assert ctx.settings.tts_volume != 30           # 运行时也没动


def test_forbidden_key_fails_even_if_permission_layer_were_bypassed(ctx):
    """权限层被绕过（比如后台子代理）时，工具自己还得兜一道。"""
    res = _run(ConfigSetTool(), {"patch": {"sandbox_enabled": True}}, ctx)
    assert res.is_error is True and "提权" in res.data


# ---- 正常写入 ----


def test_set_writes_file_runtime_and_reports_before_after(ctx):
    res = _run(ConfigSetTool(), {"patch": {"tts_volume": 30}, "reason": "用户说太吵"}, ctx)
    assert res.is_error is False
    # 三者都要对：文件、运行时、回执里的"原值 → 新值"
    assert "volume = 30" in _settings_file().read_text(encoding="utf-8")
    assert ctx.settings.tts_volume == 30
    assert "播报音量" in res.data and "50 → 30" in res.data
    assert "/undo" in res.data


def test_multi_key_patch_writes_all_and_lists_each(ctx, settings):
    res = _run(
        ConfigSetTool(),
        {"patch": {"tts_volume": 20, "tts_speech_rate": 1.5}},
        ctx,
    )
    assert res.is_error is False
    text = _settings_file().read_text(encoding="utf-8")
    assert "volume = 20" in text and "speech_rate = 1.5" in text
    assert settings.tts_volume == 20 and settings.tts_speech_rate == 1.5
    # 两项都不一样，回执里必须各占一行
    assert res.data.count("- ") >= 2


def test_set_records_a_config_undo_spec(ctx):
    """账本里要能查到"谁改的、原值多少"，否则 /undo 无从回退。"""
    _run(ConfigSetTool(), {"patch": {"tts_volume": 30}, "reason": "太吵"}, ctx)
    rec = get_journal().last(STATE_APPLIED)
    assert rec is not None and rec.tool == "ConfigSet"
    assert rec.undo_spec["kind"] == UNDO_CONFIG
    assert rec.undo_spec["before"] == {"tts_volume": 50}
    assert rec.undoable is True
    assert "太吵" in (rec.args or {}).get("reason", "")


def test_undo_restores_file_and_runtime(ctx, settings):
    """端到端：改 → 回退 → 文件与运行时都回到原值。"""
    _run(ConfigSetTool(), {"patch": {"tts_volume": 30}}, ctx)
    rec = get_journal().last(STATE_APPLIED)

    ok, note = undo_record(rec, settings=settings, journal=get_journal())
    assert ok is True and "播报音量" in note
    assert settings.tts_volume == 50
    assert "volume = 50" in _settings_file().read_text(encoding="utf-8")


def test_undo_without_before_snapshot_is_refused(settings, tmp_path):
    """账本里没有 before 快照时不许假装回退成功。"""
    from agent.core.actions.journal import EffectRecord

    configure_journal(path=tmp_path / "j2.jsonl")
    journal = get_journal()
    rec = EffectRecord(
        tool="ConfigSet", args={}, preview="x", reversible=True, undo_spec={},
    )
    journal.record(rec)
    ok, note = undo_record(rec, settings=settings, journal=journal)
    assert ok is False and note


# ---- 生效分派 ----


def test_policy_items_rebuild_the_budget_singleton(ctx):
    """打扰预算单例不重建就只落盘不生效——这一条是这层的核心回归锁。"""
    from agent.core.policy import get_budget

    before = get_budget()
    assert before is not None
    res = _run(ConfigSetTool(), {"patch": {"daily_max": 20}}, ctx)
    assert res.is_error is False
    assert "当场重建" in res.data
    assert get_budget() is not before          # 换了实例 = 真的重建了


def test_percept_item_without_host_hook_says_restart_required(ctx):
    """终端场景没宿主钩子：必须如实说"重启后生效"，不许假装已生效。"""
    clear_apply_hooks()
    _seed_percept_sources(ctx)
    res = _run(ConfigSetTool(), {"patch": {"window_enabled": False}}, ctx)
    assert res.is_error is False
    assert "重启" in res.data


def test_failed_hook_reason_is_used_verbatim_not_wrapped(ctx):
    """钩子跑了但报失败时，回执用**它的原话**，不要再拼一句"宿主未接线"。

    两句话放一起会自相矛盾：钩子明明在，拒绝的理由却是内部原因（比如感知总开关
    关着、没有总线可重装配），用户会去查本来就没坏的接线。

    @author aceFelix
    """
    clear_apply_hooks()
    register_apply_hook(
        APPLY_PERCEPT,
        lambda: (False, "感知层总开关关着，当前没有总线可重装配"),
    )
    _seed_percept_sources(ctx)
    res = _run(ConfigSetTool(), {"patch": {"window_enabled": False}}, ctx)
    assert res.is_error is False
    assert "总开关关着" in res.data
    assert "宿主没有接生效回调" not in res.data


def test_percept_item_with_host_hook_reports_live_effect(ctx):
    clear_apply_hooks()
    register_apply_hook(APPLY_PERCEPT, lambda: (True, "已重装配感知总线（启用 4 个源）"))
    _seed_percept_sources(ctx)
    res = _run(ConfigSetTool(), {"patch": {"window_enabled": False}}, ctx)
    assert "已重装配感知总线" in res.data and "重启" not in res.data


def test_apply_mode_is_dispatched_once_per_mode(ctx):
    """一批里三项都走 percept 时只重装配一次（重装配不便宜）。"""
    clear_apply_hooks()
    calls: list[int] = []
    register_apply_hook(APPLY_PERCEPT, lambda: (calls.append(1), (True, "已重装配"))[1])
    _seed_percept_sources(ctx, "percept_window_enabled", "percept_file_enabled",
                         "percept_vcs_enabled")
    _run(
        ConfigSetTool(),
        {"patch": {"window_enabled": False, "file_enabled": False, "vcs_enabled": False}},
        ctx,
    )
    assert calls == [1]


# ---- 值没变：不假装改过 ----


def test_noop_change_is_reported_as_noop(ctx):
    """设成当前值 → 回执说"本来就是这个值"，且**不记账、不写文件、不重装配**。

    不剔掉无变更项的后果：回执写成"开 → 开"，账本里多一条原值等于新值的变更，
    用户看 /undo 列表时会看到一条"撤了等于没撤"。

    @author aceFelix
    """
    before = ctx.settings.tts_volume
    res = _run(ConfigSetTool(), {"patch": {"tts_volume": before}}, ctx)
    assert res.is_error is False
    assert "本来就是这个值" in res.data
    assert "/undo" not in res.data                 # 没记账就不该提回退
    assert get_journal().last(STATE_APPLIED) is None
    assert not _settings_file().exists()          # 连文件都没碰


def test_noop_change_does_not_reapply_percept(ctx):
    """无变更时不该跑生效钩子（重装配总线不便宜）。"""
    clear_apply_hooks()
    calls: list[int] = []
    register_apply_hook(APPLY_PERCEPT, lambda: (calls.append(1), (True, "x"))[1])
    ctx.settings.percept_vcs_enabled = True
    _run(ConfigSetTool(), {"patch": {"vcs_enabled": True}}, ctx)
    assert calls == []


def test_mixed_patch_changes_only_the_different_one(ctx):
    """一批里只改真的变了的，另几项在回执里标注"未动"。"""
    ctx.settings.tts_speech_rate = 1.0
    res = _run(
        ConfigSetTool(),
        {"patch": {"tts_volume": 30, "tts_speech_rate": 1.0}},
        ctx,
    )
    assert res.is_error is False
    assert "已改 1 项" in res.data
    assert "本来就是这个值，未动" in res.data
    rec = get_journal().last(STATE_APPLIED)
    assert rec.undo_spec["before"] == {"tts_volume": 50}   # 只记真改的那项


# ---- 变更广播：界面靠这个才知道值变了 ----


def test_change_is_broadcast_with_new_values(ctx):
    """成功改完要广播 `{协议键: 新值}`——否则桌面面板会一直显示旧值。

    实测缺陷（2026-10-26）：模型把音量 100→50，文件与运行时都对，但面板滑杆
    仍停在 100；用户这时拖一下滑杆就把 100 写回去，静默覆盖掉模型的改动。

    @author aceFelix
    """
    seen: list[dict] = []
    register_change_listener(seen.append)
    _run(ConfigSetTool(), {"patch": {"tts_volume": 30, "tts_speech_rate": 1.5}}, ctx)
    assert len(seen) == 1
    assert seen[0] == {"tts_volume": 30, "tts_speech_rate": 1.5}   # 只有真变了的那项


def test_noop_change_is_not_broadcast(ctx):
    """无变更不广播（面板无需重拉）。"""
    seen: list[dict] = []
    register_change_listener(seen.append)
    _run(ConfigSetTool(), {"patch": {"tts_volume": ctx.settings.tts_volume}}, ctx)
    assert seen == []


def test_failed_write_is_not_broadcast(ctx):
    """校验失败 / 禁改项不广播：值没变就不该让界面去重拉。"""
    seen: list[dict] = []
    register_change_listener(seen.append)
    _run(ConfigSetTool(), {"patch": {"tts_volume": 999}}, ctx)
    _run(ConfigSetTool(), {"patch": {"sandbox_enabled": False}}, ctx)
    assert seen == []


def test_broken_listener_does_not_fail_the_tool(ctx):
    """监听器炸了不能让"已改成功"变成失败回执。"""
    def boom(_changes: dict) -> None:
        raise RuntimeError("界面刷新崩了")

    register_change_listener(boom)
    res = _run(ConfigSetTool(), {"patch": {"tts_volume": 30}}, ctx)
    assert res.is_error is False and "播报音量" in res.data
    assert ctx.settings.tts_volume == 30


# ---- 列表类配置（P2 新对模型开放的那批）----


def test_list_item_is_broadcast_and_applied(ctx):
    """名单类（守望目录）能改：落盘 + 改运行时 + 广播 + 回执用人话。"""
    seen: list[dict] = []
    register_change_listener(seen.append)
    res = _run(
        ConfigSetTool(),
        {"patch": {"window_exclude_apps": ["1Password", " KeePass "]}},
        ctx,
    )
    assert res.is_error is False
    assert ctx.settings.percept_window_exclude_apps == ["1Password", "KeePass"]  # strip 过
    assert seen[0] == {"window_exclude_apps": ["1Password", "KeePass"]}
    # 回执不能吐 Python 列表的方括号与引号（用户要看的是"排除了哪几个"）
    assert "1Password、KeePass" in res.data
    assert "[" not in res.data.split("：", 1)[-1] or "[]" in res.data


def test_list_item_rejects_non_strings(ctx):
    """名单里混进数字要拒——否则会把 `1` 当目录名写进配置。"""
    res = _run(ConfigSetTool(), {"patch": {"vcs_repos": ["E:/repo", 1]}}, ctx)
    assert res.is_error is True and "字符串" in res.data


def test_empty_list_reads_back_as_empty_not_tuple(ctx):
    """读回来的空名单必须是 []（面板前端只认数组，拿到 () 会渲染成空白单元格）。"""
    from agent.config.config_specs import get_spec, read_value

    spec = get_spec("file_watch_dirs")
    assert isinstance(read_value(ctx.settings, spec), list)


# ---- ConfigList ----


def test_list_renders_keys_labels_and_hints(ctx):
    res = _run(ConfigListTool(), {}, ctx)
    assert res.is_error is False
    assert "tts_volume" in res.data and "播报音量" in res.data
    assert "需确认" in res.data and "可直接改" in res.data


def test_list_never_shows_forbidden_keys(ctx):
    """禁改项连名字都不给：给了模型就会反复试探，得到的只有一句拒绝。"""
    res = _run(ConfigListTool(), {}, ctx)
    assert "sandbox_enabled" not in res.data and "api_key" not in res.data


def test_list_tier_filter(ctx):
    only_confirm = _run(ConfigListTool(), {"tier": "confirm"}, ctx).data
    assert "每日播报上限" in only_confirm and "播报音量" not in only_confirm

    only_auto = _run(ConfigListTool(), {"tier": "auto"}, ctx).data
    assert "播报音量" in only_auto and "每日播报上限" not in only_auto


def test_list_reports_current_value_from_settings(ctx):
    ctx.settings.tts_volume = 12
    assert "12" in _run(ConfigListTool(), {"tier": "auto"}, ctx).data


def test_settings_used_by_tests_has_the_expected_defaults():
    """守住用例依赖的缺省值：真改了出厂默认，这里的断言会先炸而不是静默漂移。"""
    from agent.config.settings import Settings

    f = {x.name: x.default for x in dataclasses.fields(Settings)}
    assert f["tts_volume"] == 50
    assert f["tts_speech_rate"] == pytest.approx(1.0)
    assert f["notify_daily_max"] == 10
    assert f["proactive_tts_enabled"] is True
