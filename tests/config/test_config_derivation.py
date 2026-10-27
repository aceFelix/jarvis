"""面板派生守卫（阶段 12 P2）：两份面板字段表必须真从真源表来。

背景（aceFelix，2026-10-27）：P1 收尾时实测发现同一件事散在四处——
`config_specs.CONFIG_SPECS`（模型侧真源）、`desktop_settings.DESKTOP_SETTING_SPECS`
（桌面协议）、`percept_settings.PERCEPT_CONFIG_FIELDS`（感知面板）、`configuration.md`
的表格。当时跑差集就已经漂了：

```
面板能改、真源表没有（7 项）：enabled、file_watch_dirs、log_events、
    mail_watch_senders、vcs_repos、vision_auto_stop_minutes、window_exclude_apps
真源表有、面板改不了（7 项）：quiet_hours、daily_max、min_salience、
    aggregate_window_s、percept_enabled、tasks_enabled、undo_window_s
```

最刺眼的是 `enabled` 与 `percept_enabled`——同一项两个名字，两张表各维护一份。
P2 把它们改成派生后，这个文件负责**让漂移重新变成不可能**：
每一条用例都是"如果谁绕过真源表加一项，这里就红"。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.config import desktop_settings, percept_settings
from agent.config.config_specs import (
    CONFIG_SPECS,
    PANEL_DESKTOP,
    PANEL_PERCEPT,
    SPEC_BY_KEY,
    panel_specs,
)

# ---------------------------------------------------------------------------
# 一、派生方向：面板字段集 == 真源表里标了对应面板的那批
# ---------------------------------------------------------------------------


def test_desktop_specs_are_exactly_the_derived_set():
    """桌面白名单必须与「真源表里标了 PANEL_DESKTOP」的顺序完全一致。"""
    derived = [s.key for s in panel_specs(PANEL_DESKTOP)]
    actual = [s.key for s in desktop_settings.DESKTOP_SETTING_SPECS]
    assert actual == derived


def test_percept_fields_are_exactly_the_derived_set():
    """感知面板字段必须与「真源表里标了 PANEL_PERCEPT」一一对应（含别名换名）。"""
    derived = [(s.key, s.attr) for s in panel_specs(PANEL_PERCEPT)]
    actual = [(f.source.key if f.source else "", f.attr) for f in percept_settings.PERCEPT_CONFIG_FIELDS]
    assert actual == derived


@pytest.mark.parametrize("panel", [PANEL_DESKTOP, PANEL_PERCEPT])
def test_panel_has_at_least_one_field(panel):
    """面板派生是动态的：有人把标记全删了也不该静默变成"面板什么都没有"。"""
    assert panel_specs(panel)


# ---------------------------------------------------------------------------
# 二、字段视图与真源项不脱钩
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", percept_settings.PERCEPT_CONFIG_FIELDS, ids=lambda f: f.key)
def test_percept_field_view_matches_its_spec(field):
    """面板视图里的 attr/kind/label/范围必须逐项等于真源项（视图不是第二份真源）。"""
    spec = field.source
    assert spec is not None, f"{field.key} 没有对应的真源项"
    assert field.attr == spec.attr
    assert field.kind == spec.kind
    assert field.label == spec.label
    assert field.lo == (int(spec.lo) if spec.lo is not None else None)
    assert field.hi == (int(spec.hi) if spec.hi is not None else None)


@pytest.mark.parametrize("spec", desktop_settings.DESKTOP_SETTING_SPECS, ids=lambda s: s.key)
def test_desktop_view_matches_its_spec(spec):
    """桌面视图同理：section/field/kind/范围都必须是真源那份，不能各自改。"""
    src = spec.source
    assert src is not None, f"{spec.key} 没有对应的真源项"
    assert (spec.section, spec.field, spec.attr, spec.kind) == (
        src.section, src.field, src.attr, src.kind,
    )
    assert spec.lo == src.lo and spec.hi == src.hi


# ---------------------------------------------------------------------------
# 三、命名别名：只有一条，且必须指向真实存在的感知面板项
# ---------------------------------------------------------------------------


def test_only_the_master_switch_has_an_alias():
    """面板侧与协议侧不同名的，只允许感知总开关这一条。

    为什么它特殊：`[percept] enabled` 在面板对象里叫 `enabled` 已经够清楚
    （对象名限定了语境），但模型面对的是一个扁平键列表，叫 `enabled` 会与
    `[deadline] enabled` 撞名，所以协议层叫 `percept_enabled`。两边各自合理，
    于是把差异写成显式表——而不是靠"碰巧一致"，那正是 P1 收尾时踩的坑。
    """
    assert percept_settings.PANEL_KEY_ALIASES == {"enabled": "percept_enabled"}


def test_alias_targets_are_real_percept_panel_specs():
    """别名指向的协议键必须真是感知面板项（指错就是面板拿不到值）。"""
    for panel_key, spec_key in percept_settings.PANEL_KEY_ALIASES.items():
        spec = SPEC_BY_KEY.get(spec_key)
        assert spec is not None, f"别名 {panel_key} 指向了不存在的键 {spec_key}"
        assert PANEL_PERCEPT in spec.panels


def test_every_percept_panel_key_is_either_same_or_aliased():
    """面板键要么与真源键同名，要么在别名表里——不允许出现第三种情况。

    这条是"同一项两个名字"的兜底：若有人悄悄改了面板视图的键名，
    这里立刻红，而不是等到用户说"改了没反应"。
    """
    renamed = set(percept_settings.PANEL_KEY_ALIASES)
    for field in percept_settings.PERCEPT_CONFIG_FIELDS:
        assert field.source is not None
        if field.key != field.source.key:
            assert field.key in renamed, f"{field.key} 与真源键 {field.source.key} 不同名却不在别名表里"


def test_panel_keys_are_unique():
    """面板键必须唯一（重复的话后一个会覆盖 FIELD_BY_KEY 的前一个）。"""
    keys = [f.key for f in percept_settings.PERCEPT_CONFIG_FIELDS]
    assert len(keys) == len(set(keys))
    dkeys = [s.key for s in desktop_settings.DESKTOP_SETTING_SPECS]
    assert len(dkeys) == len(set(dkeys))


# ---------------------------------------------------------------------------
# 四、覆盖面：真源表 ⊇ 面板（不反过来）
# ---------------------------------------------------------------------------


def test_panel_specs_are_all_in_the_truth_table():
    """面板能改的每一项都必须在真源表里（存在性，不是签名的存在）。"""
    for spec in panel_specs(PANEL_DESKTOP) + panel_specs(PANEL_PERCEPT):
        assert spec.key in SPEC_BY_KEY
        assert spec is SPEC_BY_KEY[spec.key]


def test_truth_table_has_items_no_panel_shows():
    """真源表里必然有"只有模型能改、面板不给位置"的项（如打扰预算四项）。

    这不是缺陷而是分工：面板是低频手工调节点，打扰预算主要靠对话调。
    用例只是把这件事钉住——若哪天全被标成面板项，说明有人手滑批量加了标记。
    """
    no_panel = [s.key for s in CONFIG_SPECS if not s.panels]
    assert "quiet_hours" in no_panel
    assert "daily_max" in no_panel


# ---------------------------------------------------------------------------
# 五、落盘/校验真走同一份实现（不是看着像）
# ---------------------------------------------------------------------------


def test_desktop_save_lands_in_the_same_section_as_the_spec():
    """桌面落盘的节/字段取自真源项：改 `[deadline] enabled` 不能写到别处。"""
    spec = desktop_settings.SPEC_BY_KEY["deadline_enabled"]
    assert spec.source is not None
    assert (spec.source.section, spec.source.field) == ("deadline", "enabled")


def test_percept_save_uses_the_toml_field_not_the_panel_key():
    """落盘用的是 TOML 字段名（`enabled`），面板键也好协议键也好都不能直接当字段写。"""
    field = percept_settings.FIELD_BY_KEY["enabled"]
    assert field.source is not None
    assert field.key == "enabled"          # 面板键
    assert field.source.key == "percept_enabled"  # 协议键
    assert field.source.field == "enabled"        # TOML 字段（落盘用这个）


@pytest.mark.parametrize("panel_key", sorted(percept_settings.FIELD_BY_KEY))
def test_percept_validation_is_delegated(panel_key):
    """校验拒绝逻辑必须来自真源项——用一个必错的值换一条错误消息即可确认。"""
    field = percept_settings.FIELD_BY_KEY[panel_key]
    assert field.source is not None
    bad = {"bool": "true", "int": "5", "list": 5}[field.kind]
    with pytest.raises(ValueError):
        percept_settings.validate_percept_config({panel_key: bad})
