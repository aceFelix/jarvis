"""成本与缓存失效归因：金额要能解释自己，命中率要能自证原因。

背景（2026-11 实测 7 轮会话）：/cost 显示 ¥0.2491、命中率 75.3%。数字都对，但：

- 按当时模型（deepseek-flash 空闲价）算只该 ¥0.065 —— 差的钱是中途切进
  deepseek-v4-pro（贵 7.5 倍）那几轮烧的，而面板只显示"当前模型"；
- 75.3% 不是缓存坏了：未命中 56,246 ≈ 1.92 个满上下文，正好等于两次换模型各付
  一次全量未命中。

这两条都靠"记下来"才能自解释，所以有这张账。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.core.cache_attribution import (
    CacheAttribution,
    structural_miss_tokens,
    summarize_breaks,
)


def _turn(inp=0, hit=0, miss=0, out=0):
    return SimpleNamespace(
        input_tokens=inp, cache_read_tokens=hit, cache_miss_tokens=miss, output_tokens=out
    )


def _settled(cost=0.0, priced=True):
    return SimpleNamespace(cost_cny=cost, priced=priced)


def _tools(*names: str):
    return [{"name": n} for n in names]


class TestByModel:
    def test_splits_usage_and_cost_per_model(self):
        a = CacheAttribution()
        a.add_turn("deepseek-flash", _turn(inp=200_000, hit=171_392, miss=28_608, out=1_100), _settled(0.062))
        a.add_turn("deepseek-v4-pro", _turn(inp=27_638, hit=0, miss=27_638, out=103), _settled(0.187))

        by = a.by_model
        assert set(by) == {"deepseek-flash", "deepseek-v4-pro"}
        assert by["deepseek-flash"]["input"] == 200_000
        assert by["deepseek-v4-pro"]["cost"] == 0.187
        # 两桶金额之和 == 会话总金额
        assert abs(sum(b["cost"] for b in by.values()) - 0.249) < 1e-9

    def test_unpriced_turn_adds_tokens_but_no_money(self):
        a = CacheAttribution()
        a.add_turn("mystery-model", _turn(inp=1000, miss=1000), _settled(9.9, priced=False))
        assert a.by_model["mystery-model"]["input"] == 1000
        assert a.by_model["mystery-model"]["cost"] == 0.0

    def test_same_model_accumulates(self):
        a = CacheAttribution()
        a.add_turn("m", _turn(inp=100, hit=80, miss=20, out=5), _settled(0.01))
        a.add_turn("m", _turn(inp=200, hit=180, miss=20, out=6), _settled(0.02))
        assert a.by_model["m"]["input"] == 300
        assert a.by_model["m"]["hit"] == 260
        assert abs(a.by_model["m"]["cost"] - 0.03) < 1e-9


class TestCacheBreaks:
    def test_model_switch_recorded_with_context(self):
        a = CacheAttribution()
        a.note_model_switch("flash", "v4-pro", 24_000)
        breaks = a.breaks
        assert len(breaks) == 1
        assert breaks[0]["reason"] == "model_switch"
        assert breaks[0]["context"] == 24_000
        assert structural_miss_tokens(breaks) == 24_000

    def test_switch_to_same_model_is_not_a_break(self):
        a = CacheAttribution()
        a.note_model_switch("flash", "flash", 24_000)
        a.note_model_switch("flash", "", 24_000)
        a.note_model_switch("flash", "v4", 0)         # 还没有任何上下文，谈不上失效
        assert a.breaks == []

    def test_tool_block_change_detected(self):
        a = CacheAttribution()
        a.note_tool_block(_tools("Bash", "FileRead"))
        a.note_tool_block(_tools("Bash", "FileRead"))     # 不变 → 不记
        assert a.breaks == []
        a.note_tool_block(_tools("Bash", "FileRead", "ToolSearch"))   # 变了 → 记一次
        assert len(a.breaks) == 1
        assert a.breaks[0]["reason"] == "tool_block"

    def test_order_insensitive_within_one_block(self):
        """同一组工具换个顺序不算变化（集合相同即前缀相同）。"""
        a = CacheAttribution()
        a.note_tool_block(_tools("Bash", "Grep"))
        a.note_tool_block(_tools("Grep", "Bash"))
        assert a.breaks == []


class TestSummary:
    def test_summary_explains_switch_and_tools(self):
        breaks = [
            {"reason": "model_switch", "from": "flash", "to": "v4-pro", "context": 24_000},
            {"reason": "tool_block", "from": "a", "to": "b"},
        ]
        text = summarize_breaks(breaks)
        assert "flash→v4-pro" in text
        assert "工具块变化 1 次" in text
        assert "24,000" in text

    def test_summary_says_stable_when_nothing_happened(self):
        assert "无" in summarize_breaks([])
