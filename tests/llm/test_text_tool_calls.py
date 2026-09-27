"""文本态工具调用兜底解析器测试（text_tool_calls.py）。

覆盖 DeepSeek DSML 泄漏与纯 XML 泄漏两种形态的解析，以及流式过滤器的
抑制/回退缓冲行为。DSML 样本用 chr(0xFF5D) 构造，避免源码里出现裸全角竖线。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.llm.text_tool_calls import (
    StreamingLeakFilter,
    has_leaked_toolcall,
    normalize_leaked_markup,
    parse_textual_tool_calls,
)

# DSML 特殊 token 分隔符（全角竖线 U+FF5D）
P = chr(0xFF5D)
# `<｜｜DSML｜｜ ` 与 `</｜｜DSML｜｜ ` 前缀（含尾随空格）
DO = "<" + P * 2 + "DSML" + P * 2 + " "
DC = "</" + P * 2 + "DSML" + P * 2 + " "
# 参数标签用变量拼接，避免源码出现会与工具调用分隔符冲突的字面量
_PT = "para" + "meter"
PO = "<" + _PT            # 开标签
PC = "</" + _PT + ">"     # 闭标签
IN = "<" + "invoke"       # invoke 开标签
INC = "</" + "invoke>"    # invoke 闭标签


class TestNormalize:
    """DSML → XML 归一化。"""

    def test_strip_dsml_namespace(self):
        raw = DO + 'invoke name="Bash">' + DC + "invoke>"
        norm = normalize_leaked_markup(raw)
        assert norm == IN + ' name="Bash">' + INC

    def test_plain_text_unchanged(self):
        assert normalize_leaked_markup("普通文本，无标记") == "普通文本，无标记"


class TestParseDsml:
    """解析真实 DSML 泄漏（复现用户截图那一次抽风）。"""

    @pytest.fixture
    def sample(self) -> str:
        return (
            "我来查一下您所在地的天气。"
            + DO + "calls>"
            + DO + 'invoke name="Location">' + DC + "invoke>"
            + DO + 'invoke name="Bash">'
            + DO + 'parameter name="command" string="true">date x' + DC + "parameter>"
            + DC + "invoke>"
            + DC + "calls>"
        )

    def test_parses_two_calls(self, sample):
        assert has_leaked_toolcall(sample) is True
        calls = parse_textual_tool_calls(sample, {"Location", "Bash"})
        assert [c.name for c in calls] == ["Location", "Bash"]
        assert calls[0].input == {}
        assert calls[1].input == {"command": "date x"}

    def test_valid_names_filters_unknown(self, sample):
        # 只允许 Location，Bash 调用被丢弃
        calls = parse_textual_tool_calls(sample, {"Location"})
        assert [c.name for c in calls] == ["Location"]

    def test_no_valid_filter_keeps_all(self, sample):
        calls = parse_textual_tool_calls(sample, valid_names=None)
        assert len(calls) == 2

    def test_preamble_not_mistaken(self):
        """纯正文（无标记）不解析出任何调用。"""
        assert parse_textual_tool_calls("今天天气不错", {"Bash"}) == []


class TestParseXml:
    """解析直接吐 XML 形态的网关（无 DSML 命名空间）。"""

    def test_xml_invoke(self):
        raw = (
            IN + ' name="WebSearch">'
            + PO + ' name="query" string="true">杭州 天气' + PC
            + INC
        )
        calls = parse_textual_tool_calls(raw, {"WebSearch"})
        assert len(calls) == 1
        assert calls[0].name == "WebSearch"
        assert calls[0].input == {"query": "杭州 天气"}

    def test_string_false_coerced_to_int(self):
        raw = (
            IN + ' name="Bash">'
            + PO + ' name="timeout" string="false">30' + PC
            + INC
        )
        calls = parse_textual_tool_calls(raw, {"Bash"})
        assert calls[0].input["timeout"] == 30

    def test_no_attr_numeric_inferred(self):
        raw = IN + ' name="X">' + PO + ' name="n">10' + PC + INC
        calls = parse_textual_tool_calls(raw, {"X"})
        assert calls[0].input["n"] == 10

    def test_no_attr_string_preserved(self):
        raw = IN + ' name="X">' + PO + ' name="cmd">echo hi' + PC + INC
        calls = parse_textual_tool_calls(raw, {"X"})
        assert calls[0].input["cmd"] == "echo hi"

    def test_empty_invoke_no_params(self):
        calls = parse_textual_tool_calls(IN + ' name="Location">' + INC, {"Location"})
        assert calls[0].input == {}


class TestStreamingLeakFilter:
    """流式过滤器：正常文本放行、命中标记后抑制并缓存。"""

    def test_normal_text_passthrough(self):
        f = StreamingLeakFilter()
        out = f.feed("今天天气") + f.feed("不错") + f.flush()
        assert out == "今天天气不错"
        assert f.suppressed is False

    def test_marker_split_across_chunks(self):
        """泄漏标记被拆成多个 chunk 也能识别，标记前文字照常输出。"""
        f = StreamingLeakFilter()
        emitted = f.feed("前言")            # 无标记，直接放行
        emitted += f.feed("<")              # 尾缓冲回看 `<`，不放行
        emitted += f.feed(P)                # `<｜` 仍可能是前缀
        emitted += f.feed(P + 'DSML' + P + P + ' invoke name="Bash">')  # 命中 <｜｜
        assert emitted == "前言"
        assert f.suppressed is True
        assert f.call_text.startswith("<" + P * 2)

    def test_flush_returns_trailing_safe_text(self):
        """以 `<` 结尾时该字符被扣作尾缓冲，流结束 flush 应释放它。"""
        f = StreamingLeakFilter()
        emitted = f.feed("count 3 <")   # 结尾 `<` 可能是标记前缀，回退缓冲
        assert emitted == "count 3 "      # `<` 未放行
        tail = f.flush()
        assert tail == "<"                # flush 释放为安全文本
        assert f.suppressed is False

    def test_suppressed_stops_emitting(self):
        f = StreamingLeakFilter()
        f.feed(DO + 'invoke name="Bash">')
        after = f.feed("更多内容")
        assert after == ""  # 抑制后不再回显任何正文
