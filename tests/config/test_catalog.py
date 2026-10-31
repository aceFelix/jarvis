"""配置目录（``agent/config/catalog.py``）与 ConfigExplain 的测试。

守的是三条不变量，每条都对应一次真实的产品缺陷：

1. **一个都不漏**：目录要覆盖 ``Settings`` 的全部字段。漏掉的那些，用户问起来
   模型只能编——而"编一个近似的字段名"比说不知道更糟。
2. **一个都不空**：每条都要有说明。说明允许来自不同真源（真源表 hint / 模板注释
   / 人工补丁），但不允许空着交差。
3. **凭据不吐值**：即使调用方是用户本人，``smtp_password`` 这类也只回"已设置 /
   未设置"——值一旦进了模型上下文就收不回来。

另外守住「存在但未纳管」与「根本不存在」两种回答必须分开：混成一句
"不认识的配置项"会让用户以为自己拼错了，然后去两百多行 TOML 里白找。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from agent.config.catalog import (
    DEAD_FIELDS,
    REMOVED_FIELDS,
    build_catalog,
    describe_missing,
    entries_of_section,
    human_value,
    render_entries,
    render_overview,
    search_catalog,
)
from agent.config.config_specs import CONFIG_SPECS, TIER_FORBIDDEN
from agent.config.settings import Settings
from agent.tools.config_tool import ConfigExplainTool


@pytest.fixture()
def settings() -> Settings:
    """一份干净实例：只测目录本身，不读用户真实配置。"""
    return Settings()


# ---------------------------------------------------------------------------
# 覆盖率：一个都不漏、一个都不空
# ---------------------------------------------------------------------------


def test_catalog_covers_every_settings_field():
    """目录必须收录 Settings 的每一个字段（漏一个就有一问必瞎）。"""
    fields = {f.name for f in dataclasses.fields(Settings)}
    recorded = {e.attr for e in build_catalog()}
    assert fields - recorded == set(), "有字段没进配置目录"


def test_every_entry_has_doc():
    """每条都得有说明——宁可短，不能空。"""
    missing = [f"{e.section}.{e.field}" for e in build_catalog() if not e.doc]
    assert missing == [], f"这些项没有说明：{missing}"


def test_editable_set_matches_spec_table():
    """``editable`` 必须与真源表一一对应：表内可代改，表外不可。"""
    spec_attrs = {s.attr for s in CONFIG_SPECS}
    for entry in build_catalog():
        assert entry.editable == (entry.attr in spec_attrs), entry.attr


def test_no_duplicated_location():
    """同一 (段, 字段) 只能出现一次，否则渲染会出现两行同名项。"""
    seen = [(e.section, e.field) for e in build_catalog()]
    assert len(seen) == len(set(seen))


# ---------------------------------------------------------------------------
# 凭据：值不进上下文
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "attr",
    ["api_key", "email_smtp_password", "email_imap_password", "bridge_token"],
)
def test_sensitive_entries_are_marked(attr):
    entry = next(e for e in build_catalog() if e.attr == attr)
    assert entry.sensitive is True


def test_sensitive_value_is_masked():
    """凭据只回状态，不回值——哪怕值就在对象里。"""
    entry = next(e for e in build_catalog() if e.attr == "email_smtp_password")
    fake = Settings()
    setattr(fake, "email_smtp_password", "SUPER-SECRET-CODE")
    rendered = human_value(entry, getattr(fake, "email_smtp_password"))
    assert rendered == "已设置"
    assert "SUPER-SECRET-CODE" not in rendered


def test_sensitive_empty_reads_as_unset():
    entry = next(e for e in build_catalog() if e.attr == "bridge_token")
    assert human_value(entry, "") == "未设置"


# ---------------------------------------------------------------------------
# 检索：用中文功能词能找到东西
# ---------------------------------------------------------------------------


def test_search_by_friendly_chinese_name():
    """用户不会说字段名，会说"CPU 阈值"。"""
    hits = search_catalog("CPU 阈值", limit=5)
    assert any(e.attr == "monitor_cpu_threshold" for e in hits)


def test_search_prefers_the_section_over_a_stray_hint():
    """问"邮件"时该先给 [email] 整段，而不是哪条 hint 里碰巧有"发邮件"。

    实测踩过：``actions.undo_window_s`` 的说明里写着"发邮件这类收不回的动作"，
    于是它抢在邮箱配置前面——用户想问的显然是邮箱怎么填。
    """
    hits = search_catalog("邮件", limit=5)
    assert hits, "至少要有结果"
    assert hits[0].section == "email", f"首条落在 {hits[0].section}"


def test_search_supports_section_name_directly():
    hits = search_catalog("monitor", limit=5)
    assert hits and all(e.section == "monitor" for e in hits[:3])


def test_entries_of_section_returns_whole_block():
    entries = entries_of_section("maintenance")
    assert len(entries) >= 10
    assert {e.section for e in entries} == {"maintenance"}


def test_search_miss_returns_empty():
    assert search_catalog("绝不存在的功能词zzz", limit=5) == []


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_render_groups_by_section(settings):
    """按段分组：段首用途只出现一次，而不是每条重复一遍。

    注：每行末尾的“只能自己改：… [monitor]”也带方括号，所以不能直接数 ``[monitor]``
    的出现次数（那样永远大于一）。数段首那行。
    """
    text = render_entries(search_catalog("monitor", limit=6), settings)
    headers = [ln for ln in text.splitlines() if ln.startswith("[monitor]")]
    assert len(headers) == 1
    assert "系统资源监控" in headers[0]


def test_render_marks_how_to_change(settings):
    """可代改与只能改文件必须给出不同的下一步。"""
    editable = render_entries(search_catalog("auto_clean_time", limit=1), settings)
    assert "我可以代改" in editable and "/config set" in editable
    manual = render_entries(search_catalog("smtp_host", limit=1), settings)
    assert "只能自己改" in manual and "settings.toml" in manual


def test_render_empty_is_actionable():
    text = render_entries([], None)
    assert "没有找到" in text


def test_overview_lists_sections():
    text = render_overview()
    assert "配置" in text
    assert "[monitor]" in text and "[maintenance]" in text


# ---------------------------------------------------------------------------
# 存在但未纳管 vs 根本不存在
# ---------------------------------------------------------------------------


def test_describe_missing_explains_unmanaged_key():
    """"存在但只能改文件"必须与"不存在"分开说。"""
    text = describe_missing("cpu_threshold")
    assert text is not None
    assert "cpu_threshold" in text
    assert "[monitor]" in text
    assert "settings.toml" in text
    # 别再说"不认识"——那是误导
    assert "不认识" not in text


def test_describe_missing_suggests_near_miss():
    """拼得不完全对时，给相近项而不是一句"没有"。"""
    text = describe_missing("cpu_thres")  # 前缀不全
    assert text is not None
    assert "cpu" in text.lower()


def test_describe_missing_returns_none_for_garbage():
    assert describe_missing("zzz_definitely_not_a_knob") is None


# ---------------------------------------------------------------------------
# 死配置：字段存在、面板可见、TOML 能写，但没有任何代码读它
# ---------------------------------------------------------------------------
#
# 这一类比"未纳管"更难被察觉：用户改了它，落盘成功、日志全绿、重启也没变化。
# 目录必须主动说出来，并且给出"那到底该改哪个"——只告诉用户"这条路死了"
# 是半个答案。


def test_dead_fields_all_exist_on_settings():
    """死配置清单的键必须是真实字段：拼错的键会让标注永远不生效。"""
    fields = {f.name for f in dataclasses.fields(Settings)}
    assert set(DEAD_FIELDS) <= fields, f"清单里有不存在的字段: {set(DEAD_FIELDS) - fields}"


def test_dead_fields_are_flagged_and_never_editable():
    """死配置一律不可代改，且渲染时必须明说"未接线"。"""
    entries = {e.attr: e for e in build_catalog() if e.attr in DEAD_FIELDS}
    assert set(entries) == set(DEAD_FIELDS), "有死配置没进目录"
    for entry in entries.values():
        assert entry.dead, f"{entry.attr} 没被标成未接线"
        assert entry.editable is False, f"{entry.attr} 竟被判为可代改"
        assert "未接线" in render_entries([entry], Settings())


def test_every_dead_field_points_to_a_live_alternative():
    """死配置不能只说“死了”，要么给替代改法、要么说清根本没实现。"""
    for attr, reason in DEAD_FIELDS.items():
        assert len(reason) >= 8, f"{attr} 的说明太短：{reason}"
        marks = ("[", "常量", "未接", "默认值", "没做完", "无人调用", "只在测试")
        assert any(m in reason for m in marks), (
            f"{attr} 既没指出替代改法、也没说清根本无处可改：{reason}"
        )


def test_dead_field_table_is_currently_empty():
    """7 项死配置已全部处理完，这张表当前必须为空。

    留着这条断言不是凑数：``DEAD_FIELDS`` 是给“字段在、没人读”兜底的检测网，
    后续有人往目录里加了配置项却没接线时，这里应该重新有东西——那时要么接线、
    要么如实标注，不能默默留着。
    """
    assert DEAD_FIELDS == {}, f"又有配置没人读了：{sorted(DEAD_FIELDS)}"


def test_removed_fields_say_they_are_gone_not_unknown():
    """已删字段要能说出“它没了、去哪了”，而不是绕一圈“没找到”。

    场景很真实：用户升级后旧 ``settings.toml`` 里还留着 ``monitor_enabled = true``
    ，改了半天没反应。（加载器对未知键静默忽略，不会报错。）
    如果这里只回“没有这个键”，就和当初“不认识的配置项”那句错话同一个毛病。
    """
    real = {f.name for f in dataclasses.fields(Settings)}
    for attr, reason in REMOVED_FIELDS.items():
        assert attr not in real, f"{attr} 还在 Settings 里，不该列进已删表"
        text = describe_missing(attr)
        assert text is not None
        assert "已经不存在" in text
        assert reason[:10] in text, "没说清它去哪了"


def test_removed_fields_answer_to_the_toml_side_name_too():
    """用 TOML 里的短名问已删字段，也必须答得上来。

    用户是从**自己的配置文件**里抄名字来问的，那里写的是 ``hotkey = "..."``——
    不是 ``daemon_hotkey``。只认属性名的话，他问“hotkey 这项怎么改”会被回一句
    不认识的配置项，而那个字段明明就躺在他文件第 128 行。
    """
    from agent.config.catalog import _REMOVED_ALIASES

    assert _REMOVED_ALIASES, "别名表空了？那 TOML 短名就没人认了"
    for alias, attr in _REMOVED_ALIASES.items():
        assert attr in REMOVED_FIELDS, f"{alias} 指向的 {attr} 不在已删表里"
        assert alias != attr, "别名和属性名一样就没意义了"
        text = describe_missing(alias)
        assert text is not None, f"问 TOML 短名 {alias} 居然没答上来"
        assert "已经不存在" in text


def test_generic_field_names_are_not_in_removed_aliases():
    """``enabled`` 这类多段通用的名字不能当已删别名。

    ``[monitor] enabled`` 与 ``[email] enabled`` 是两个东西——把 ``enabled``
    收进别名表，用户问“enabled 能不能改”就会被笼统回一句“已删除”，
    而 ``[email] enabled`` 明明还活着。
    """
    from agent.config.catalog import _REMOVED_ALIASES

    for generic in ("enabled", "enable", "model", "ics_path"):
        assert generic not in _REMOVED_ALIASES


def test_hotkey_fields_are_fully_gone():
    """全局热键 2026-11-01 整体下线：字段、包、配置键都不该还在。

    这条盯的是一个容易反复的坑——留一个“只删了一半”的状态（比如字段删了但
    ``settings_domains`` 还映射着），旧配置照样能“看得到”却永远不生效。
    """
    from agent.config.settings_domains import FLAT_DOMAINS

    real = {f.name for f in dataclasses.fields(Settings)}
    stale = {n for n in real if "hotkey" in n}
    assert not stale, f"热键字段复活了：{sorted(stale)}"

    mapped = {attr for _table, pairs in FLAT_DOMAINS for _f, attr in pairs}
    leftover = {a for a in mapped if "hotkey" in a}
    assert not leftover, f"settings_domains 还映射着已删字段：{sorted(leftover)}"

    for attr in ("daemon_hotkey", "daemon_hotkey_native", "daemon_hotkey_debounce_ms"):
        assert attr in REMOVED_FIELDS, f"{attr} 删了但没登记，用户问起来没说法"


def test_describe_missing_points_attr_name_to_the_real_spec_key():
    """用 Settings 属性名问时，应该把可代改的协议键直接给出来。

    这一条是修过一个真问题：``work_break_interval`` 纳管后，用户若按 TOML 里看到的
    完整属性名（``monitor_work_break_interval``）去问，以前会被回“不在可代改范围”，
    明明一句话能改的项却被打发去翻文件。
    """
    text = describe_missing("monitor_work_break_interval")
    assert text is not None
    assert "可以代改" in text
    assert "/config set work_break_interval" in text
    assert "不在「可以让我代改」" not in text


def test_monitor_section_has_no_dead_knobs():
    """``[monitor]`` 段七项全部真生效，死配置清零。

    历史口径：这一段曾有九项——含一个没人读的 ``enabled``、一个已下线的
    ``high_cpu_duration``。2026-10-31 收口：``enabled`` 删了（开关在
    ``[percept] system_enabled``）、``check_interval`` 与 ``work_break_interval``
    接进了感知源、``high_cpu_duration`` 因功能被 ``/health`` 取代而删除。
    """
    monitor = {e.field: e for e in entries_of_section("monitor")}
    assert len(monitor) == 7
    assert not [e for e in monitor.values() if e.dead], "还有没接线的项"
    writable = {name for name, entry in monitor.items() if entry.editable}
    assert writable == {
        "cpu_threshold",
        "memory_threshold",
        "disk_threshold",
        "alert_cooldown",
        "disk_trend_days",
        "check_interval",
        "work_break_interval",
    }


def test_unmanaged_key_does_not_leak_forbidden_name():
    """护栏项的说明可以给（用户自己有权知道），但不能出现在可代改清单里。"""
    writable = [e.spec_key for e in build_catalog() if e.editable]
    assert "auto_clean_max_risk" not in writable
    assert all(not e.key.startswith("sandbox_") for e in CONFIG_SPECS if e.tier != TIER_FORBIDDEN)


# ---------------------------------------------------------------------------
# ConfigExplain 工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def tool() -> ConfigExplainTool:
    return ConfigExplainTool()


class _Ctx:
    """最小 ToolContext 替身：这两个工具只用到 ``settings``。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.workdir = ""
        self.permission_mode = "default"


def _call(tool: ConfigExplainTool, settings: Settings, **args) -> str:
    """跑一次工具并取出纯文本输出（工具是 async 的）。"""
    result = asyncio.run(tool.call(dict(args), _Ctx(settings)))
    return result.data


def test_tool_is_read_only_and_allowed(tool):
    assert tool.is_read_only({}) is True
    assert tool.is_concurrency_safe({}) is True


def test_tool_answers_by_keyword(tool, settings):
    text = _call(tool, settings, query="CPU 阈值")
    assert "cpu_threshold" in text


def test_tool_answers_by_section(tool, settings):
    text = _call(tool, settings, section="email")
    assert "[email]" in text
    assert "smtp_host" in text


def test_tool_falls_back_to_search_for_typo_section(tool, settings):
    """段名拼错时退回关键词检索，而不是直接报“没找到”。"""
    text = _call(tool, settings, section="perceptio")
    assert "percept" in text


def test_tool_overview(tool, settings):
    text = _call(tool, settings, overview=True)
    assert "[monitor]" in text and "[email]" in text


def test_tool_defaults_to_overview(tool, settings):
    """什么都没给时退回总览，比报参数错误有用。"""
    text = _call(tool, settings)
    assert "[monitor]" in text
