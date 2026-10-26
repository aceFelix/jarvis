"""自配置层真源表测试（阶段 12 P1）。

这张表一旦写错，后果都是**静默**的，所以每条都盯一个静默失败：

1. **``attr`` 拼错**：``setattr`` 会在 Settings 上凭空造一个新属性，落盘也"成功"，
   运行时却读的还是旧字段——用户改了音量没反应，日志里一句错都没有。第一条用例
   就是把 spec 的每个 ``attr`` 拿去和 ``dataclasses.fields(Settings)`` 对表。
2. **``apply_mode`` 标错**：``[policy.notify_budget]`` 那四项看着像现读，其实
   ``get_budget()`` 是懒建单例——标成 ``live`` 就是"只落盘不生效"。
3. **``kind`` 太松**：入参是模型给的 JSON，``tts_volume="50"`` 这种脏值必须拒。
4. **禁改项漏登记**：``api_key`` / ``sandbox_*`` 被漏掉就等于模型能给自己提权。

@author aceFelix
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from agent.config.config_specs import (
    APPLY_LIVE,
    APPLY_PERCEPT,
    APPLY_POLICY,
    APPLY_RESCHEDULE,
    APPLY_RESTART,
    CONFIG_SPECS,
    FORBIDDEN_SPECS,
    SPEC_BY_KEY,
    TIER_AUTO,
    TIER_CONFIRM,
    TIER_FORBIDDEN,
    clear_apply_hooks,
    clear_change_listeners,
    forbidden_reason,
    get_spec,
    human_value,
    list_specs,
    notify_changed,
    read_value,
    register_apply_hook,
    register_change_listener,
    run_apply_hooks,
    save_value,
    validate_value,
)
from agent.config.settings import Settings

_SETTINGS_FIELDS = {f.name for f in dataclasses.fields(Settings)}


# ---- 真源表自身的形状 ----


def test_every_attr_exists_on_settings():
    """每个 spec 的 ``attr`` 必须是 Settings 的真实字段（防静默造出假字段）。"""
    bad = [(s.key, s.attr) for s in CONFIG_SPECS if s.attr not in _SETTINGS_FIELDS]
    assert bad == [], f"这些 attr 在 Settings 上不存在（拼错了？）: {bad}"


def test_section_field_matches_the_real_toml_mapping():
    """``(section, field)`` 必须与 ``settings_domains`` 的真实映射对得上。

    写错段落同样是**没有任何报错**的失败：值落进 settings.toml 里一个没人读的地方，
    重启后静默无效——用户说"改了没用"，日志却一路全绿。所以这条把 spec 表和
    ``settings_domains`` 这两份真源直接对账（含 ``[policy.notify_budget]`` 那种
    两级嵌套的表）。
    """
    from agent.config.settings_domains import FLAT_DOMAINS, SUB_TABLES

    real: dict[tuple[str, str], str] = {}
    for entry in FLAT_DOMAINS:
        if len(entry) == 2:                       # (section, ((field, attr), ...))
            section, fields = entry
        else:                                     # (top, sub, ((field, attr), ...))
            top, sub, fields = entry
            section = f"{top}.{sub}"
        for field_name, attr in fields:
            real[(section, field_name)] = attr
    for top, sub, fields in SUB_TABLES:
        for field_name, attr in fields:
            real[(f"{top}.{sub}", field_name)] = attr

    bad = [
        (
            s.key,
            f"[{s.section}] {s.field}",
            f"spec={s.attr} 实际={real.get((s.section, s.field))}",
        )
        for s in CONFIG_SPECS
        if real.get((s.section, s.field)) != s.attr
    ]
    assert bad == [], f"段落/字段名与真实映射对不上（值会被写进没人读的地方）: {bad}"


def test_keys_are_unique():
    """协议键唯一：重名会让 ``SPEC_BY_KEY`` 悄悄丢掉前一条。"""
    keys = [s.key for s in CONFIG_SPECS]
    assert len(keys) == len(set(keys))
    assert len(SPEC_BY_KEY) == len(CONFIG_SPECS)


def test_only_two_tiers_in_the_writable_table():
    """可改表里只允许 auto / confirm；forbidden 只登记名字与理由。"""
    assert {s.tier for s in CONFIG_SPECS} == {TIER_AUTO, TIER_CONFIRM}


@pytest.mark.parametrize("spec", CONFIG_SPECS, ids=lambda s: s.key)
def test_spec_shape_is_complete(spec):
    """每一项都要能解释清自己：有中文名、有人话说明、kind 在支持集里。"""
    assert spec.label and spec.hint
    assert spec.kind in {"bool", "int", "float", "time", "quiet", "list"}
    assert spec.apply_mode in {
        APPLY_LIVE, APPLY_POLICY, APPLY_PERCEPT, APPLY_RESCHEDULE, APPLY_RESTART,
    }
    assert spec.section and spec.field


def test_notify_budget_items_are_policy_apply_mode():
    """打扰预算四项必须走 ``policy``：``get_budget()`` 是懒建单例，标 live 就白改。"""
    budget = [s for s in CONFIG_SPECS if s.section == "policy.notify_budget"]
    assert {s.key for s in budget} == {
        "quiet_hours", "daily_max", "min_salience", "aggregate_window_s",
    }
    assert all(s.apply_mode == APPLY_POLICY for s in budget)


def test_expression_layer_stays_auto():
    """纯表达层（音量/语速/简报/感知逐源）必须留在 auto，别被顺手提成 confirm。

    这一档的意义是"用户随口一说就改对"，全塞 confirm 等于每次都要点一次确认框。
    """
    for key in ("tts_volume", "tts_speech_rate", "briefing_time", "window_enabled"):
        assert get_spec(key).tier == TIER_AUTO


def test_interrupt_and_scope_items_require_confirm():
    """会改"何时能打断用户/能干什么活"的项必须是 confirm（模型不能给自己放宽护栏）。"""
    for key in ("quiet_hours", "daily_max", "percept_enabled", "tasks_enabled"):
        assert get_spec(key).tier == TIER_CONFIRM


# ---- 禁改项 ----


def test_forbidden_names_never_appear_in_writable_table():
    """禁改项不许同时出现在可改表里（否则权限层之前就被列出来了）。"""
    for key in FORBIDDEN_SPECS:
        assert get_spec(key) is None


@pytest.mark.parametrize(
    "key",
    ["sandbox_enabled", "sandbox_excluded_commands", "api_key",
     "api_key_secondary", "email_smtp_password", "email_imap_password"],
)
def test_forbidden_reason_covers_credentials_and_sandbox(key):
    """凭据与沙箱成族字段都要有拒绝理由（模型据此告诉用户正确改法）。"""
    reason = forbidden_reason(key)
    assert reason
    assert "提权" in reason or "只能由人填" in reason or "后门" in reason


def test_every_forbidden_reason_stands_on_its_own():
    """理由里不许出现"同上/见上"这类指代。

    ``forbidden_reason()`` 一次只返回一条，模型与用户也是分开看的——写成"同上：…"
    到了那种上下文就是一句废话，用户得不到"为什么不行"。（写这张表时真踩过。）
    """
    for key, reason in FORBIDDEN_SPECS.items():
        assert not reason.startswith(("同上", "见上", "同前")), f"{key} 的理由不自洽：{reason}"
        assert len(reason) >= 10, f"{key} 的理由太短，说不清为什么：{reason}"


def test_forbidden_reason_is_empty_for_writable_keys():
    assert forbidden_reason("tts_volume") == ""
    assert forbidden_reason("") == ""


def test_tier_forbidden_constant_is_not_used_as_spec_tier():
    """``TIER_FORBIDDEN`` 是给权限层判断用的，不该出现在可改表里。"""
    assert TIER_FORBIDDEN not in {s.tier for s in CONFIG_SPECS}


# ---- 校验 ----


@pytest.mark.parametrize(
    "key,raw,frag",
    [
        ("tts_volume", "50", "必须是整数"),        # 字符串数字也要拒
        ("tts_volume", True, "必须是整数"),        # bool 是 int 子类，必须单独挡
        ("tts_volume", 50.5, "必须是整数"),
        ("tts_speech_rate", "1.2", "必须是数值"),
        ("window_enabled", "true", "必须是布尔值"),
        ("briefing_time", "8:30", "HH:MM"),        # 单位数小时不合法
        ("quiet_hours", "23:00~07:00", "HH:MM-HH:MM"),
    ],
)
def test_validate_rejects_dirty_values(key, raw, frag):
    """模型给的 JSON 常见脏写法，一个都不许放过去落盘。"""
    with pytest.raises(ValueError) as ei:
        validate_value(get_spec(key), raw)
    assert frag in str(ei.value)


def test_validate_enforces_range_inclusive():
    """范围含边界：边界值放行、越界（哪怕一点）拒。"""
    spec = get_spec("tts_volume")
    assert validate_value(spec, 0) == 0
    assert validate_value(spec, 100) == 100
    for bad in (-1, 101):
        with pytest.raises(ValueError) as ei:
            validate_value(spec, bad)
        assert "不得" in str(ei.value)


def test_validate_accepts_legal_time_and_quiet_hours():
    assert validate_value(get_spec("briefing_time"), "08:30") == "08:30"
    assert validate_value(get_spec("briefing_time"), "23:59") == "23:59"
    assert validate_value(get_spec("quiet_hours"), "23:00-07:00") == "23:00-07:00"


# ---- 读值 ----


def test_read_value_falls_back_to_default_not_zero():
    """属性缺失时回退出厂缺省，而不是一律零值。

    ``proactive_tts_enabled`` 默认是 True；按零值回 False 会让用户看到
    "我明明没关它" —— 与 ``percept_settings`` 同一口径。
    """
    empty = object()  # 没有该属性的"settings"
    spec = get_spec("proactive_tts_enabled")
    assert read_value(empty, spec) is True
    assert read_value(None, spec) is spec.default


def test_read_value_coerces_by_kind():
    holder = type("S", (), {"tts_volume": "30", "tts_speech_rate": 1.5})()
    assert read_value(holder, get_spec("tts_volume")) == 30
    assert read_value(holder, get_spec("tts_speech_rate")) == 1.5


# ---- 落盘 ----


def test_save_value_keeps_comments_and_other_fields(tmp_path):
    """外科式写入：只动那一行，注释与同节其他字段原样留着。"""
    target = tmp_path / "settings.toml"
    target.write_text(
        "[tts]\n# 用户在 2026-10 手写的备注\nvolume = 50\nvoice = \"longanlang_v3\"\n",
        encoding="utf-8",
    )
    assert save_value(get_spec("tts_volume"), 30, path=target) is True
    text = target.read_text(encoding="utf-8")
    assert "volume = 30" in text
    assert "手写的备注" in text                  # 注释不能丢
    assert 'voice = "longanlang_v3"' in text    # 同节其他字段不能动


def test_save_value_creates_missing_section(tmp_path):
    """节不存在就追加整节（首次配置的常见路径）。"""
    target = tmp_path / "settings.toml"
    target.write_text("[agent]\nname = \"jarvis\"\n", encoding="utf-8")
    assert save_value(get_spec("quiet_hours"), "23:00-07:00", path=target) is True
    text = target.read_text(encoding="utf-8")
    assert "[policy.notify_budget]" in text and 'quiet_hours = "23:00-07:00"' in text


def test_save_value_writes_nested_section_field(tmp_path):
    """嵌套节名（``policy.notify_budget``）要写成 TOML 的点号节头。"""
    target = tmp_path / "settings.toml"
    assert save_value(get_spec("daily_max"), 12, path=target) is True
    text = target.read_text(encoding="utf-8")
    assert "[policy.notify_budget]" in text and "daily_max = 12" in text


def test_save_value_preserves_float_as_float(tmp_path):
    """float 落成 ``1.5`` 而不是 ``1``（否则重启后被解析成整数）。"""
    target = tmp_path / "settings.toml"
    assert save_value(get_spec("tts_speech_rate"), 1.5, path=target) is True
    assert re.search(r"speech_rate = 1\.5", target.read_text(encoding="utf-8"))


# ---- 生效钩子 ----


def test_run_hooks_without_registration_reports_failure():
    """没登记钩子要返回 False——调用方据此如实回执"重启后生效"，不许假装成功。"""
    clear_apply_hooks()
    try:
        ok, notes = run_apply_hooks(APPLY_PERCEPT)
        assert ok is False and notes == []
    finally:
        clear_apply_hooks()


def test_register_hook_is_deduplicated_and_runs():
    clear_apply_hooks()
    calls: list[str] = []

    def hook() -> tuple[bool, str]:
        calls.append("hit")
        return True, "已重装配"

    try:
        register_apply_hook(APPLY_PERCEPT, hook)
        register_apply_hook(APPLY_PERCEPT, hook)   # 重复登记不该跑两次
        ok, notes = run_apply_hooks(APPLY_PERCEPT)
        assert ok is True and notes == ["已重装配"] and calls == ["hit"]
    finally:
        clear_apply_hooks()


def test_broken_hook_does_not_propagate():
    """钩子抛异常不该让"配置已写入"变成"配置失败"——只把说明改成失败。"""
    clear_apply_hooks()

    def boom() -> tuple[bool, str]:
        raise RuntimeError("宿主炸了")

    try:
        register_apply_hook(APPLY_RESCHEDULE, boom)
        ok, notes = run_apply_hooks(APPLY_RESCHEDULE)
        assert ok is False
        assert any("宿主炸了" in n for n in notes)
    finally:
        clear_apply_hooks()


# ---- 变更监听器（与生效钩子分开的那一层） ----


def test_change_listener_receives_new_values_once():
    """变更监听器拿到 `{协议键: 新值}`，且同一个回调不重复登记。"""
    clear_change_listeners()
    seen: list[dict] = []

    def listener(changes: dict) -> None:
        seen.append(changes)

    try:
        register_change_listener(listener)
        register_change_listener(listener)
        notify_changed({"tts_volume": 30})
        assert seen == [{"tts_volume": 30}]
    finally:
        clear_change_listeners()


def test_change_listener_is_not_called_for_empty_changes():
    """空变更不广播（避免无意义的面板重拉）。"""
    clear_change_listeners()
    seen: list[dict] = []
    try:
        register_change_listener(seen.append)
        notify_changed({})
        assert seen == []
    finally:
        clear_change_listeners()


def test_broken_change_listener_does_not_break_the_write():
    """监听器抛异常必须被吞掉：界面刷新失败不该让配置写入变失败。

    这条与 ``test_broken_hook_does_not_propagate`` 不同：钩子失败要如实回执，
    监听器只是旁观者，它坏了不影响"已经改成功"这个事实。
    """
    clear_change_listeners()
    good: list[dict] = []

    def boom(_changes: dict) -> None:
        raise RuntimeError("监听器炸了")

    try:
        register_change_listener(boom)
        register_change_listener(good.append)
        notify_changed({"tts_volume": 40})     # 不抛
        assert good == [{"tts_volume": 40}]   # 后面的监听器照样收到
    finally:
        clear_change_listeners()


# ---- 展示 ----


def test_list_specs_excludes_forbidden_by_default():
    keys = {s.key for s in list_specs()}
    assert "tts_volume" in keys and "api_key" not in keys
    assert {s.key for s in list_specs(tiers=(TIER_CONFIRM,))} <= keys


def test_human_value_renders_bool_and_unset():
    assert human_value(get_spec("proactive_tts_enabled"), True) == "开"
    assert human_value(get_spec("proactive_tts_enabled"), False) == "关"
    assert human_value(get_spec("quiet_hours"), "") == "未设"
    assert human_value(get_spec("tts_speech_rate"), 1.0) == "1"
