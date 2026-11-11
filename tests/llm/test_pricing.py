"""计价模块的护栏测试。

这里钉的是**外部事实**（官方价目与峰谷时段），不是内部实现：
价格一变，这些断言就该红——那是提醒去核对官方页，而不是让程序继续按旧价记账。

@author aceFelix
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agent.llm import pricing

# 北京时间高峰时段内的两个采样点（周一 10:00 / 周一 15:00）
MON_10 = datetime(2026, 11, 9, 10, 0, tzinfo=pricing.BEIJING)   # 周一
MON_15 = datetime(2026, 11, 9, 15, 0, tzinfo=pricing.BEIJING)
MON_12 = datetime(2026, 11, 9, 12, 0, tzinfo=pricing.BEIJING)
MON_13 = datetime(2026, 11, 9, 13, 0, tzinfo=pricing.BEIJING)
MON_18 = datetime(2026, 11, 9, 18, 0, tzinfo=pricing.BEIJING)
SAT_10 = datetime(2026, 11, 7, 10, 0, tzinfo=pricing.BEIJING)   # 周六


class TestPriceTable:
    """价目数字与官方中文页逐项对齐（2026-11-11 核对）。"""

    def test_verified_at_is_pinned(self):
        """核对日期写死：改价目必须同时更新它，否则等于用旧价记账。"""
        assert pricing.VERIFIED_AT == "2026-11-11"

    def test_source_is_official_cn_page(self):
        assert "zh-cn" in pricing.SOURCE
        assert "api-docs.deepseek.com" in pricing.SOURCE

    @pytest.mark.parametrize(
        "model,hit,miss,out",
        [
            ("deepseek-flash", 0.04, 2.0, 8.0),
            ("deepseek-v4-pro", 0.30, 9.0, 27.0),
        ],
    )
    def test_peak_prices(self, model, hit, miss, out):
        """高峰价：命中 / 未命中 / 输出。"""
        row = pricing.resolve(model)
        assert row is not None
        assert (row.hit, row.miss, row.output) == (hit, miss, out)

    def test_hit_is_far_cheaper_than_miss(self):
        """命中价必须是未命中的几十分之一——用折扣系数近似会差几十倍。"""
        row = pricing.resolve("deepseek-flash")
        assert row.miss / row.hit >= 30

    @pytest.mark.parametrize(
        "legacy,canonical",
        [
            ("deepseek-chat", "deepseek-flash"),
            ("deepseek-reasoner", "deepseek-flash"),
            ("deepseek-v4-flash", "deepseek-flash"),
            ("deepseek-v4-flash-vision-exp", "deepseek-flash"),
        ],
    )
    def test_legacy_names_map_to_current_price(self, legacy, canonical):
        """旧模型名仍可调用，但按现行模型计费。"""
        assert pricing.resolve(legacy) == pricing.resolve(canonical)

    def test_unknown_model_is_none_not_guess(self):
        """未收录的模型不许套用别家价目。"""
        assert pricing.resolve("qwen3.8-max") is None
        assert pricing.resolve("glm-5.3-flash") is None
        assert pricing.resolve("") is None


class TestPeakWindow:
    """峰谷判定：北京时间周一至周五 9-12、14-18 为高峰。"""

    @pytest.mark.parametrize("at", [MON_10, MON_15])
    def test_inside_window_is_peak(self, at):
        assert pricing.is_peak(at) is True

    @pytest.mark.parametrize("at", [MON_12, MON_13, MON_18])
    def test_boundaries_are_closed_right(self, at):
        """左闭右开：12:00 已出窗、14:00 才进窗、18:00 已出窗。"""
        assert pricing.is_peak(at) is False

    def test_weekend_is_off_peak(self):
        assert pricing.is_peak(SAT_10) is False

    def test_holiday_is_off_peak(self, monkeypatch):
        """法定节假日全天按空闲价——即使落在工作日的 10 点。"""
        monkeypatch.setattr(pricing, "_is_holiday", lambda d: True)
        assert pricing.is_peak(MON_10) is False

    def test_missing_holiday_data_fails_open(self, monkeypatch):
        """节假日表跨年失效时不得让计价崩掉，按非节假日处理。"""
        def boom(d):
            raise RuntimeError("no data for year")
        monkeypatch.setattr(pricing, "_is_holiday", boom)
        assert pricing.is_peak(MON_10) is True


class TestCostMath:
    """金额计算。"""

    def test_peak_one_million_miss(self):
        r = pricing.compute_cost(
            "deepseek-flash", cache_hit_tokens=0, cache_miss_tokens=1_000_000,
            output_tokens=0, at=MON_10,
        )
        assert r.priced is True and r.peak is True
        assert r.cost_cny == pytest.approx(2.0)

    def test_peak_one_million_hit(self):
        r = pricing.compute_cost(
            "deepseek-flash", cache_hit_tokens=1_000_000, cache_miss_tokens=0,
            output_tokens=0, at=MON_10,
        )
        assert r.cost_cny == pytest.approx(0.04)

    def test_off_peak_is_half(self):
        peak = pricing.compute_cost("deepseek-flash", cache_hit_tokens=0,
                                    cache_miss_tokens=1_000_000, output_tokens=0, at=MON_10)
        off = pricing.compute_cost("deepseek-flash", cache_hit_tokens=0,
                                   cache_miss_tokens=1_000_000, output_tokens=0, at=MON_13)
        assert off.cost_cny == pytest.approx(peak.cost_cny / 2)

    def test_three_segments_add_up(self):
        r = pricing.compute_cost(
            "deepseek-v4-pro", cache_hit_tokens=1_000_000, cache_miss_tokens=1_000_000,
            output_tokens=1_000_000, at=MON_10,
        )
        assert r.cost_cny == pytest.approx(0.30 + 9.0 + 27.0)

    def test_unknown_model_reports_unpriced(self):
        r = pricing.compute_cost("qwen3.8-max", cache_hit_tokens=0,
                                 cache_miss_tokens=1_000_000, output_tokens=0, at=MON_10)
        assert r.priced is False
        assert r.cost_cny == 0.0
        # token 数仍如实带出，供调用方显示「无法计价」而不是显示 0 元
        assert r.miss_tokens == 1_000_000


class TestSummarize:
    """会话累计口径：OpenAI 协议 input 含缓存，Anthropic 协议不含。"""

    def test_openai_protocol_splits_hit_out_of_input(self):
        r = pricing.summarize(
            "deepseek-flash", input_tokens=1_000_000, cache_hit_tokens=800_000,
            output_tokens=0, input_includes_cache=True, at=MON_10,
        )
        assert r.hit_tokens == 800_000
        assert r.miss_tokens == 200_000
        assert r.cost_cny == pytest.approx((800_000 * 0.04 + 200_000 * 2.0) / 1_000_000)

    def test_anthropic_protocol_keeps_input_as_miss(self):
        r = pricing.summarize(
            "deepseek-flash", input_tokens=1_000, cache_hit_tokens=800_000,
            output_tokens=0, input_includes_cache=False, at=MON_10,
        )
        assert r.miss_tokens == 1_000
        assert r.hit_tokens == 800_000

    def test_negative_never_propagates(self):
        """端点偶发异常值不得算出负金额。"""
        r = pricing.summarize(
            "deepseek-flash", input_tokens=100, cache_hit_tokens=500,
            output_tokens=-10, input_includes_cache=True, at=MON_10,
        )
        assert r.miss_tokens >= 0 and r.output_tokens == 0
        assert r.cost_cny >= 0
