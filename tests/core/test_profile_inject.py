"""画像注入渲染（P1c）单元测试。

覆盖: 主题优先、两轮填充（短条先占位）、预算口径（pseudo/weighted）、
非法 mode 回退、空输入/零预算，以及 `ProfileStore.render_for_prompt` 的委托接线。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.core.memory.profile_inject import HEADER, render_for_prompt
from agent.core.memory.profile_store import ProfileEntry, ProfileStore


def _entry(
    content: str,
    category: str = "other",
    confidence: float = 0.5,
    updated_at: float = 0.0,
    entry_id: str = "",
) -> ProfileEntry:
    """构造测试条目（id 可省略，仅渲染不落盘时不需要）。"""
    return ProfileEntry(
        id=entry_id or f"ent_{abs(hash((content, category))) % 100000:05d}",
        category=category,
        content=content,
        confidence=confidence,
        updated_at=updated_at,
    )


class TestRenderBasics:
    """基本契约：空输入、标题、排序。"""

    def test_empty_entries_returns_empty(self):
        assert render_for_prompt([]) == ""

    def test_zero_limit_returns_empty(self):
        """预算为 0 时一条都装不下，返回空串而不是只剩标题。"""
        assert render_for_prompt([_entry("习惯熬夜")], token_limit=0) == ""

    def test_header_exact(self):
        """标题逐字节固定——变更会破坏 system prompt 前缀缓存。"""
        text = render_for_prompt([_entry("习惯熬夜")])
        assert text.startswith(HEADER + "\n")
        assert text.splitlines()[0] == "# 关于用户（画像记忆）"

    def test_content_empty_entries_ignored(self):
        assert render_for_prompt([_entry(""), _entry("   ")]) == ""

    def test_default_order_by_confidence_then_recency(self):
        """无主题偏好时＝旧行为：confidence 降序，同分看 updated_at。"""
        low = _entry("低置信条目", confidence=0.3, updated_at=100)
        high_old = _entry("高置信旧条目", confidence=0.9, updated_at=100)
        high_new = _entry("高置信新条目", confidence=0.9, updated_at=200)
        lines = render_for_prompt([low, high_old, high_new]).splitlines()[1:]
        assert lines == ["- 高置信新条目", "- 高置信旧条目", "- 低置信条目"]


class TestPreferTopics:
    """主题优先：命中类别的条目置顶，空列表保持原排序。"""

    def test_prefer_topic_pinned_first(self):
        schedule = _entry("习惯凌晨两点睡", "schedule", 0.9)
        prefer = _entry("喜欢在家打游戏", "preference", 0.5)
        lines = render_for_prompt([schedule, prefer], prefer_topics=["preference"]).splitlines()[1:]
        assert lines[0] == "- 喜欢在家打游戏"
        assert lines[1] == "- 习惯凌晨两点睡"

    def test_preferred_topic_wins_over_confidence(self):
        """低置信的主题命中条目也要排在高置信之前——这就是"主题优先"的意义。"""
        base = [
            _entry("高置信其它事实", "other", 0.95),
            _entry("低置信偏好事实", "preference", 0.4),
        ]
        lines = render_for_prompt(base, prefer_topics=["preference"]).splitlines()[1:]
        assert lines[0] == "- 低置信偏好事实"

    def test_unknown_topic_is_noop(self):
        items = [_entry("甲", "other", 0.9, 100), _entry("乙", "schedule", 0.5, 100)]
        assert render_for_prompt(items, prefer_topics=["不存在的类别"]) == render_for_prompt(items)

    def test_empty_prefer_keeps_original_order(self):
        items = [_entry("甲", "schedule", 0.9, 100), _entry("乙", "preference", 0.5, 100)]
        assert render_for_prompt(items, prefer_topics=[]) == render_for_prompt(items)


class TestTwoRoundFill:
    """两轮填充：短条目先占位，避免一条长事实挤掉多条短事实。"""

    def test_short_entries_rendered_before_long(self):
        long_one = _entry("L" * 40, confidence=0.9)
        shorts = [_entry(f"S{i}xx", confidence=0.5) for i in range(3)]
        lines = render_for_prompt([long_one] + shorts, token_limit=300).splitlines()[1:]
        assert len(lines) == 4
        assert all(line.startswith("- S") for line in lines[:3])
        assert lines[-1] == "- " + "L" * 40

    def test_tight_budget_prefers_short_entries(self):
        """预算只够短条时，长条被跳过——旧的"长条优先"会把三条短事实全挤掉。"""
        long_one = _entry("L" * 20, confidence=0.9)   # 行 22 字 ≈ 5 token
        shorts = [_entry(f"S{i}", confidence=0.5) for i in range(3)]  # 每行 ≈ 1 token
        text = render_for_prompt([long_one] + shorts, token_limit=5)
        lines = text.splitlines()[1:]
        assert lines == ["- S0", "- S1", "- S2"]

    def test_long_entry_still_fits_when_budget_is_ample(self):
        long_one = _entry("L" * 20, confidence=0.9)
        shorts = [_entry(f"S{i}", confidence=0.5) for i in range(3)]
        lines = render_for_prompt([long_one] + shorts, token_limit=300).splitlines()[1:]
        assert "- " + "L" * 20 in lines
        assert len(lines) == 4


class TestBudgetMode:
    """预算口径：pseudo（len/4，默认）/ weighted（中英分权）。"""

    def test_weighted_costs_more_for_cjk(self):
        """同样 12 个中文字：pseudo 估 3 token 放得下，weighted 估 8 token 放不下。"""
        item = _entry("中" * 12)
        assert render_for_prompt([item], token_limit=5, mode="pseudo") != ""
        assert render_for_prompt([item], token_limit=5, mode="weighted") == ""

    def test_pseudo_is_default(self):
        items = [_entry("甲" * 12), _entry("乙" * 12)]
        assert render_for_prompt(items, token_limit=8) == render_for_prompt(
            items, token_limit=8, mode="pseudo"
        )

    def test_invalid_mode_falls_back_to_pseudo(self):
        """白名单外（含大小写不符）一律回退 pseudo，不抛异常、不静默换口径。"""
        items = [_entry("中" * 12)]
        assert render_for_prompt(items, mode="WEIGHTED") == render_for_prompt(items)
        assert render_for_prompt(items, mode="") == render_for_prompt(items)


class TestStoreDelegation:
    """ProfileStore.render_for_prompt 委托注入模块（接线回归）。"""

    def test_store_passes_prefer_topics(self, tmp_path):
        store = ProfileStore(tmp_path / "profile.json")
        store.upsert(ProfileEntry.new("习惯凌晨两点睡", "schedule", 0.9))
        store.upsert(ProfileEntry.new("喜欢在家打游戏", "preference", 0.5))
        text = store.render_for_prompt(token_limit=300, prefer_topics=["preference"])
        assert text.splitlines()[1] == "- 喜欢在家打游戏"

    def test_store_positional_limit_still_works(self, tmp_path):
        """旧调用形态 `render_for_prompt(15)` 保持可用（向后兼容）。"""
        store = ProfileStore(tmp_path / "profile.json")
        store.upsert(ProfileEntry.new("a" * 100, "other", 0.9))  # 行 102 字 ≈ 25 token
        store.upsert(ProfileEntry.new("b" * 20, "other", 0.5))   # 行 22 字 ≈ 5 token
        text = store.render_for_prompt(15)
        assert "b" * 20 in text
        assert "a" * 100 not in text

    def test_store_empty_returns_empty(self, tmp_path):
        assert ProfileStore(tmp_path / "profile.json").render_for_prompt() == ""

    @pytest.mark.parametrize("mode", ["pseudo", "weighted"])
    def test_store_accepts_both_modes(self, tmp_path, mode):
        store = ProfileStore(tmp_path / "profile.json")
        store.upsert(ProfileEntry.new("习惯凌晨两点睡", "schedule", 0.9))
        assert store.render_for_prompt(token_limit=300, mode=mode) != ""


class TestCoreCap:
    """P5 核心预算：常驻只注入前 N 条（按权重/主题排序），其余走按需检索。"""

    def test_cap_limits_lines(self):
        items = [_entry(f"事实{i}", confidence=0.9 - i * 0.1) for i in range(4)]
        text = render_for_prompt(items, token_limit=300, max_entries=2)
        assert len([l for l in text.splitlines() if l.startswith("- ")]) == 2

    def test_zero_means_unlimited(self):
        items = [_entry(f"事实{i}") for i in range(4)]
        text = render_for_prompt(items, token_limit=300, max_entries=0)
        assert len([l for l in text.splitlines() if l.startswith("- ")]) == 4

    def test_cap_respects_priority(self):
        """核心预算截断发生在排序之后：主题命中先占位。"""
        preferred = _entry("偏好事实", "preference", 0.5)
        other = _entry("其它事实", "other", 0.95)
        text = render_for_prompt(
            [other, preferred], token_limit=300, prefer_topics=["preference"], max_entries=1
        )
        assert "偏好事实" in text and "其它事实" not in text


class TestSettingsWiring:
    """配置键必须真能进 Settings（防"进了表没接线"的死配置）。"""

    def test_memory_table_maps_new_keys(self):
        from agent.config.settings_domains import apply_domain_tables

        updates: dict = {}
        apply_domain_tables(
            {
                "memory": {
                    "profile_inject_prefer_topics": ["schedule", "preference"],
                    "profile_inject_token_budget_mode": "weighted",
                }
            },
            updates,
        )
        assert updates["profile_inject_prefer_topics"] == ["schedule", "preference"]
        assert updates["profile_inject_token_budget_mode"] == "weighted"

    def test_prefer_topics_copied_not_shared(self):
        """数组字段要新拷贝：两个 Settings 实例不能共享同一个可变列表。"""
        from agent.config.settings_domains import apply_domain_tables

        raw = ["schedule"]
        updates: dict = {}
        apply_domain_tables({"memory": {"profile_inject_prefer_topics": raw}}, updates)
        updates["profile_inject_prefer_topics"].append("preference")
        assert raw == ["schedule"]

    def test_defaults_keep_old_behavior(self):
        """默认值＝旧行为：不设主题偏好、预算口径仍为 pseudo。"""
        from agent.config.settings import Settings

        s = Settings()
        assert s.profile_inject_prefer_topics == []
        assert s.profile_inject_token_budget_mode == "pseudo"

