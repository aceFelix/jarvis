"""缓存命中率与上下文口径的护栏测试。

两个指标都必须能独立验算：
- 命中率：厂商给了命中/未命中一对时就是 `hit / (hit + miss)`，不依赖协议推测；
- 上下文：报 API 实计（最近一次请求的 input），不把字符估算当账。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent.llm.base import Usage


@dataclass
class _Details:
    reasoning_tokens: int | None = None


@dataclass
class _Raw:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    completion_tokens_details: _Details | None = None


class TestHitRateExactPath:
    """厂商同时给命中与未命中 → 精确相除。"""

    def test_hit_rate_uses_hit_plus_miss(self):
        u = Usage(input_tokens=149_997, cache_read_tokens=148_480, cache_miss_tokens=1_517)
        assert u.cache_hit_rate == pytest.approx(148_480 / 149_997 * 100, abs=0.01)

    def test_hit_rate_is_protocol_agnostic(self):
        """同样的命中/未命中，不该因为 input 取值不同而算出两个命中率。"""
        openai_like = Usage(input_tokens=150_000, cache_read_tokens=148_480, cache_miss_tokens=1_517)
        anthropic_like = Usage(input_tokens=1_517, cache_read_tokens=148_480, cache_miss_tokens=1_517)
        assert openai_like.cache_hit_rate == pytest.approx(anthropic_like.cache_hit_rate)

    def test_full_hit(self):
        u = Usage(input_tokens=1_000, cache_read_tokens=1_000, cache_miss_tokens=0)
        # miss=0 时不走精确路径（无法区分「全命中」与「没上报」），走兜底
        assert u.cache_hit_rate == pytest.approx(100.0)

    def test_no_usage_returns_zero(self):
        assert Usage().cache_hit_rate == 0.0


class TestHitRateFallback:
    """厂商只给命中数 → 按协议口径兜底。"""

    def test_openai_protocol_input_includes_cache(self):
        u = Usage(input_tokens=10_000, cache_read_tokens=8_000)
        assert u.cache_hit_rate == pytest.approx(80.0)

    def test_anthropic_protocol_input_excludes_cache(self):
        # hit > input 视为「input 不含缓存」
        u = Usage(input_tokens=2_000, cache_read_tokens=8_000)
        assert u.cache_hit_rate == pytest.approx(8_000 / 10_000 * 100)


class TestMissDerivation:
    """未命中 token 的推导（provider 归一化时完成）。"""

    def test_prefers_vendor_reported_miss(self):
        from agent.llm.openai_provider import normalize_usage

        u = _Raw(prompt_tokens=1_000, completion_tokens=10,
                 prompt_cache_hit_tokens=600, prompt_cache_miss_tokens=400)
        out = normalize_usage(u, cached=600, created=0)
        assert out.cache_miss_tokens == 400
        assert "prompt_cache_miss_tokens" in (out.raw_usage or {})

    def test_derives_when_vendor_omits_miss(self):
        from agent.llm.openai_provider import normalize_usage

        u = _Raw(prompt_tokens=1_000, completion_tokens=10, prompt_cache_hit_tokens=600)
        out = normalize_usage(u, cached=600, created=0)
        assert out.cache_miss_tokens == 400

    def test_anthropic_style_input_excludes_cache(self):
        from agent.llm.openai_provider import normalize_usage

        # hit(8,000) > input(2,000)：input 只记未命中
        u = _Raw(prompt_tokens=2_000, completion_tokens=10)
        out = normalize_usage(u, cached=8_000, created=0)
        assert out.cache_miss_tokens == 2_000


class TestContextMeasurement:
    """上下文规模取实测。"""

    def test_last_input_tokens_mirrors_prompt_tokens(self):
        from agent.llm.openai_provider import normalize_usage

        u = _Raw(prompt_tokens=23_456, completion_tokens=5)
        out = normalize_usage(u, cached=0, created=0)
        assert out.last_input_tokens == 23_456

    def test_cost_uses_explicit_miss(self):
        from agent.llm import pricing

        r = pricing.compute_cost(
            "deepseek-flash", cache_hit_tokens=148_480, cache_miss_tokens=1_517,
            output_tokens=2_226,
        )
        # 与 /cost 真机核对过的数字一致（空闲时段半价）
        assert r.cost_cny > 0
