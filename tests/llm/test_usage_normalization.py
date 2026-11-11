"""用量归一口径的护栏测试。

钉的是**厂商字段语义**，不是实现细节：
- 思考 token 是 completion 的子集，相加会重复计费（宁可不相加，也别多收一遍钱）
- 原始 usage 必须原样留存，供 /cost --raw 与官方文档对照
- 中文/英文的 token 换算比例不同，估算不能一律 len/4

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.core.memory.compactor import (
    estimate_text_tokens,
    estimate_text_tokens_weighted,
)
from agent.llm.openai_provider import normalize_usage


@dataclass
class _Details:
    reasoning_tokens: int | None = None


@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int | None = None
    completion_tokens_details: _Details | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class TestReasoningIsASubset:
    """思考 token 与 completion 的关系。"""

    def test_reasoning_is_recorded_but_not_added(self):
        """子集口径：output 保持 completion 原值，reasoning 单独记下。"""
        u = _Usage(
            prompt_tokens=1000,
            completion_tokens=800,
            completion_tokens_details=_Details(reasoning_tokens=600),
        )
        out = normalize_usage(u, cached=0, created=0)
        assert out.output_tokens == 800, "不得把思考再加一遍"
        assert out.reasoning_tokens == 600

    def test_separate_accounting_is_corrected(self):
        """reasoning 大于 completion → 该厂商分开计量，以 reasoning 为准。"""
        u = _Usage(
            prompt_tokens=1000,
            completion_tokens=200,
            completion_tokens_details=_Details(reasoning_tokens=1500),
        )
        out = normalize_usage(u, cached=0, created=0)
        assert out.output_tokens == 1500
        assert out.reasoning_tokens == 1500

    def test_no_details_means_zero(self):
        u = _Usage(prompt_tokens=10, completion_tokens=5)
        out = normalize_usage(u, cached=0, created=0)
        assert out.reasoning_tokens == 0
        assert out.output_tokens == 5

    def test_reasoning_never_exceeds_output_edge_equal(self):
        """reasoning == completion（全部输出都是思考）也不得翻倍。"""
        u = _Usage(prompt_tokens=10, completion_tokens=300,
                   completion_tokens_details=_Details(reasoning_tokens=300))
        out = normalize_usage(u, cached=0, created=0)
        assert out.output_tokens == 300


class TestRawUsage:
    """原始键值留存，供 /cost --raw 核对。"""

    def test_collects_known_fields(self):
        u = _Usage(prompt_tokens=1234, completion_tokens=56, total_tokens=1290,
                   prompt_cache_hit_tokens=1000)
        out = normalize_usage(u, cached=1000, created=0)
        assert out.raw_usage is not None
        assert out.raw_usage["prompt_tokens"] == 1234
        assert out.raw_usage["completion_tokens"] == 56
        assert out.raw_usage["prompt_cache_hit_tokens"] == 1000

    def test_missing_fields_are_absent_not_zero(self):
        """没返回的字段不该被编成 0——那会让人以为厂商报了 0。"""
        u = _Usage(prompt_tokens=10)
        out = normalize_usage(u, cached=0, created=0)
        assert out.raw_usage is not None
        assert "prompt_cache_hit_tokens" not in out.raw_usage

    def test_raw_does_not_affect_counters(self):
        u = _Usage(prompt_tokens=100, completion_tokens=10,
                   completion_tokens_details=_Details(reasoning_tokens=4))
        out = normalize_usage(u, cached=7, created=3)
        assert (out.input_tokens, out.output_tokens) == (100, 10)
        assert (out.cache_read_tokens, out.cache_creation_tokens) == (7, 3)


class TestWeightedEstimator:
    """按官方语种比例估算：中文 0.6 / 其他 0.3 每字符。"""

    def test_pure_chinese_is_higher_than_flat_quarter(self):
        text = "这" * 1000  # 1000 个汉字
        assert estimate_text_tokens_weighted(text) == 600
        assert estimate_text_tokens(text) == 250  # len//4
        assert estimate_text_tokens_weighted(text) > estimate_text_tokens(text)

    def test_pure_ascii_near_official_ratio(self):
        text = "a" * 1000
        assert estimate_text_tokens_weighted(text) == 300

    def test_mixed_text_between_the_two(self):
        text = "这" * 500 + "a" * 500
        assert estimate_text_tokens_weighted(text) == pytest.approx(300 + 150)

    def test_empty_is_zero(self):
        assert estimate_text_tokens_weighted("") == 0

    def test_flat_estimator_unchanged(self):
        """压缩阈值仍在用老口径：本笔不得顺手改掉那条行为边界。"""
        assert estimate_text_tokens("这" * 1000) == 250
