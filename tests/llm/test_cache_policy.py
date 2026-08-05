"""缓存策略管理单元测试。

覆盖 CachePolicy 配置表、标记注入、usage 统计归一化。

@author aceFelix
"""

from types import SimpleNamespace

import pytest
from agent.llm.cache_policy import (
    CACHE_POLICIES,
    CachePolicy,
    apply_cache_markers,
    get_cache_policy,
    parse_cache_usage,
)


class TestCachePolicies:
    """配置表完整性测试。"""

    def test_all_vendors_registered(self) -> None:
        """核心厂商均已注册。"""
        expected = {
            "dashscope", "dashscope_sdk", "deepseek", "zhipu", "zai",
            "zai_sdk", "anthropic", "openai", "moonshot", "minimax",
            "mimo", "siliconflow", "google", "openai_compatible",
        }
        assert expected.issubset(set(CACHE_POLICIES.keys()))

    def test_get_policy(self) -> None:
        assert get_cache_policy("dashscope") is not None
        assert get_cache_policy("nonexistent") is None

    def test_dashscope_explicit(self) -> None:
        """DashScope 是显式缓存，最多 4 标记，最小 1024 token。"""
        p = CACHE_POLICIES["dashscope"]
        assert p.mode == "explicit"
        assert p.max_markers == 4
        assert p.min_cache_tokens == 1024
        assert p.hit_discount == 0.1
        assert p.tools_marker is False  # 文档明确工具定义不支持独立缓存

    def test_deepseek_implicit(self) -> None:
        """DeepSeek 隐式缓存，无需标记。"""
        p = CACHE_POLICIES["deepseek"]
        assert p.mode == "implicit"
        assert p.max_markers == 0

    def test_anthropic_explicit_with_tools_marker(self) -> None:
        """Anthropic 显式缓存，支持 tools 打断点。"""
        p = CACHE_POLICIES["anthropic"]
        assert p.mode == "explicit"
        assert p.tools_marker is True
        assert p.hit_discount == 0.1


class TestApplyCacheMarkers:
    """cache_control 标记注入测试。"""

    def test_implicit_policy_no_injection(self) -> None:
        """隐式模式不注入任何标记。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        apply_cache_markers(msgs, CACHE_POLICIES["deepseek"])
        assert "cache_control" not in str(msgs)

    def test_explicit_injects_system_marker(self) -> None:
        """显式模式给 system 消息注入标记（字符串 → 数组）。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        apply_cache_markers(msgs, CACHE_POLICIES["dashscope"])
        system_content = msgs[0]["content"]
        assert isinstance(system_content, list)
        assert system_content[0]["cache_control"] == {"type": "ephemeral"}
        assert system_content[0]["text"] == "sys"

    def test_explicit_injects_last_user_marker(self) -> None:
        """最后一条 user 消息也注入滚动标记。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"},
        ]
        apply_cache_markers(msgs, CACHE_POLICIES["dashscope"])
        # system 标记
        assert msgs[0]["content"][0].get("cache_control")
        # 最后一条 user 标记
        assert msgs[-1]["content"][0].get("cache_control")
        # 中间的 user 消息不受影响
        assert msgs[1]["content"] == "q1"

    def test_marker_on_content_list(self) -> None:
        """content 已是列表时在最后一个块上打标记（Anthropic 格式）。"""
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ]
        apply_cache_markers(msgs, CACHE_POLICIES["anthropic"])
        assert msgs[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_dashscope_no_tools_marker(self) -> None:
        """DashScope 的 tools 不被打标记。"""
        tool_defs = [{"name": "Bash", "type": "function"}]
        msgs = [{"role": "user", "content": "hi"}]
        apply_cache_markers(msgs, CACHE_POLICIES["dashscope"], tool_defs=tool_defs)
        assert "cache_control" not in tool_defs[0]

    def test_anthropic_tools_marker(self) -> None:
        """Anthropic 的 tools 末尾被打标记。"""
        tool_defs = [{"name": "A"}, {"name": "B"}]
        msgs = [{"role": "user", "content": "hi"}]
        apply_cache_markers(msgs, CACHE_POLICIES["anthropic"], tool_defs=tool_defs)
        assert "cache_control" not in tool_defs[0]
        assert tool_defs[-1]["cache_control"] == {"type": "ephemeral"}

    def test_max_markers_respected(self) -> None:
        """标记数不超过策略上限。"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        apply_cache_markers(msgs, CachePolicy(mode="explicit", max_markers=1))
        count = str(msgs).count("cache_control")
        assert count == 1


class TestParseCacheUsage:
    """usage 统计归一化测试。"""

    def test_openai_style_object(self) -> None:
        """OpenAI SDK 对象形态（嵌套 prompt_tokens_details）。"""
        usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=30),
        )
        cached, created = parse_cache_usage(usage, CACHE_POLICIES["dashscope"])
        assert cached == 30
        assert created == 0

    def test_deepseek_dict_style(self) -> None:
        """DeepSeek dict 形态（顶层 prompt_cache_hit_tokens）。"""
        usage = {"prompt_cache_hit_tokens": 40, "prompt_tokens": 200}
        cached, created = parse_cache_usage(usage, CACHE_POLICIES["deepseek"])
        assert cached == 40

    def test_anthropic_object(self) -> None:
        """Anthropic 对象形态（cache_read_input_tokens / cache_creation_input_tokens）。"""
        usage = SimpleNamespace(
            input_tokens=500,
            output_tokens=100,
            cache_read_input_tokens=300,
            cache_creation_input_tokens=200,
        )
        cached, created = parse_cache_usage(usage, CACHE_POLICIES["anthropic"])
        assert cached == 300
        assert created == 200

    def test_none_usage(self) -> None:
        cached, created = parse_cache_usage(None, CACHE_POLICIES["dashscope"])
        assert (cached, created) == (0, 0)

    def test_no_policy_field_returns_zero(self) -> None:
        usage = SimpleNamespace(prompt_tokens=10)
        cached, created = parse_cache_usage(usage, CACHE_POLICIES["moonshot"])
        assert (cached, created) == (0, 0)

    def test_zai_nested_field(self) -> None:
        """智谱嵌套字段 prompt_tokens_details.cached_tokens。"""
        usage = SimpleNamespace(
            prompt_tokens=100,
            prompt_tokens_details=SimpleNamespace(cached_tokens=55),
        )
        cached, _ = parse_cache_usage(usage, CACHE_POLICIES["zai_sdk"])
        assert cached == 55

    def test_alt_field_fallback(self) -> None:
        """主字段缺失时回退备选字段（DeepSeek Anthropic 兼容端点）。"""
        # Anthropic 格式 usage：无 prompt_cache_hit_tokens，有 cache_read_input_tokens
        usage = SimpleNamespace(
            input_tokens=500,
            cache_read_input_tokens=400,
        )
        cached, _ = parse_cache_usage(usage, CACHE_POLICIES["deepseek"], input_tokens=500)
        assert cached == 400

    def test_alt_field_abnormal_discarded(self) -> None:
        """备选字段异常（远超输入数，疑似累计值）时丢弃。"""
        usage = SimpleNamespace(
            input_tokens=500,
            cache_read_input_tokens=226560,  # 异常累计值
        )
        cached, _ = parse_cache_usage(usage, CACHE_POLICIES["deepseek"], input_tokens=500)
        assert cached == 0

    def test_alt_field_no_input_no_guard(self) -> None:
        """未传 input_tokens 时不拦截备选值（兼容旧调用）。"""
        usage = SimpleNamespace(cache_read_input_tokens=100)
        cached, _ = parse_cache_usage(usage, CACHE_POLICIES["deepseek"])
        assert cached == 100

    def test_main_field_takes_priority(self) -> None:
        """主字段有值时不走备选。"""
        usage = {"prompt_cache_hit_tokens": 60, "cache_read_input_tokens": 9999}
        cached, _ = parse_cache_usage(usage, CACHE_POLICIES["deepseek"], input_tokens=100)
        assert cached == 60
