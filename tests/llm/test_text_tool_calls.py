"""文本态工具调用兜底解析器测试（text_tool_calls.py）。

覆盖 DeepSeek DSML 泄漏与纯 XML 泄漏两种形态的解析，以及流式过滤器的
抑制/回退缓冲行为。DSML 样本用 chr(0xFF5C) 构造，避免源码里出现裸全角竖线；
另有 TestRealLeakSample 以 Unicode 转义写死真实存档原文，作为码点锚点。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.llm.text_tool_calls import (
    StreamingLeakFilter,
    TextualToolCallGuard,
    has_leaked_toolcall,
    normalize_leaked_markup,
    parse_textual_tool_calls,
)

# DSML 特殊 token 分隔符：真实泄漏样本为全角竖线 U+FF5C（2026-09-30 按码点核对）
P = chr(0xFF5C)
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


class TestRealLeakSample:
    """真实泄漏样本回归锚点（分隔符码点 U+FF5C）。

    2026-09-30 教训：实现里曾把分隔符误写成 U+FF5D，而测试样本又用同一个错误
    常量构造，结果「实现 + 测试一起绿、线上仍然漏码」。本类把真实存档原文以
    Unicode 转义写死（与 ~/.jarvis/sessions 里的字符逐码点一致），独立于 P 常量，
    防止同类自证错误重演。

    @author aceFelix
    """

    REAL = (
        "我看看这个项目的结构。\n\n"
        "<\uff5c\uff5cDSML\uff5c\uff5c calls>\n"
        "<\uff5c\uff5cDSML\uff5c\uff5c invoke name=\"Bash\">\n"
        "<\uff5c\uff5cDSML\uff5c\uff5c parameter name=\"command\" string=\"true\">ls -la"
        "</\uff5c\uff5cDSML\uff5c\uff5c parameter>\n"
        "</\uff5c\uff5cDSML\uff5c\uff5c invoke>\n"
        "<\uff5c\uff5cDSML\uff5c\uff5c invoke name=\"Glob\">\n"
        "<\uff5c\uff5cDSML\uff5c\uff5c parameter name=\"pattern\" string=\"true\">*.md"
        "</\uff5c\uff5cDSML\uff5c\uff5c parameter>\n"
        "</\uff5c\uff5cDSML\uff5c\uff5c invoke>\n"
        "</\uff5c\uff5cDSML\uff5c\uff5c calls>"
    )

    def test_detected(self):
        assert has_leaked_toolcall(self.REAL) is True

    def test_parsed_two_calls(self):
        calls = parse_textual_tool_calls(self.REAL, {"Bash", "Glob"})
        assert [c.name for c in calls] == ["Bash", "Glob"]
        assert calls[0].input == {"command": "ls -la"}
        assert calls[1].input == {"pattern": "*.md"}

    def test_streaming_filter_suppresses(self):
        """流式路径：前言放行、DSML 起转入缓存并可解析出调用。"""
        head = "我看看这个项目的结构。\n\n"
        f = StreamingLeakFilter()
        emitted = f.feed(head) + f.feed(self.REAL[len(head):]) + f.flush()
        assert emitted == head
        assert f.suppressed is True
        assert parse_textual_tool_calls(f.call_text, {"Bash", "Glob"}) != []

    def test_legacy_ff5d_still_matched(self):
        """兼容 U+FF5D 写法（历史假设/其他端点），不回归。"""
        legacy = (
            "<\uff5d\uff5dDSML\uff5d\uff5d " + 'invoke name="Bash">'
            + "</\uff5d\uff5dDSML\uff5d\uff5d invoke>"
        )
        assert has_leaked_toolcall(legacy) is True
        assert parse_textual_tool_calls(legacy, {"Bash"})[0].name == "Bash"


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


class TestTextualToolCallGuard:
    """组合守卫：过滤 + 解析一条龙（各 Provider 共用的接入点）。

    2026-09-30 补：anthropic 协议路径（deepseek-flash 走 /anthropic 端点）同样会把
    DSML 漏进正文，于是把流程抽成守卫供 OpenAI / Anthropic 两条路径复用。

    @author aceFelix
    """

    def test_normal_text_no_call(self):
        g = TextualToolCallGuard(["Bash"])
        out = g.feed("今天天气") + g.feed("不错") + g.flush()
        assert out == "今天天气不错"
        assert g.suppressed is False
        assert g.drain() == []

    def test_leak_filtered_and_drained(self):
        """前言照常回显、DSML 不回显、结尾解析出真实调用（标记跨多次 feed）。"""
        g = TextualToolCallGuard(["Bash"])
        emitted = g.feed("我来看看。" + DO + "calls>")
        emitted += g.feed(DO + 'invoke name="Bash">')
        emitted += g.feed(
            DO + 'parameter name="command" string="true">ls -l' + DC + "parameter>"
        )
        emitted += g.feed(DC + "invoke>" + DC + "calls>")
        assert emitted == "我来看看。"
        assert g.suppressed is True
        assert g.flush() == ""  # 抑制态下尾缓冲不再回显，避免裸码续写
        calls = g.drain()
        assert [c.name for c in calls] == ["Bash"]
        assert calls[0].input == {"command": "ls -l"}

    def test_unregistered_tool_dropped(self):
        """工具名不在本轮注册集合内 → 不当作调用（防正文尖括号误判）。"""
        g = TextualToolCallGuard(["Location"])
        g.feed(DO + 'invoke name="Bash">' + DC + "invoke>")
        assert g.drain() == []
        # 但不能吞掉信息：解析不出调用时原文要通过 rescue_text 交回调用方
        assert "DSML" in g.rescue_text

    def test_empty_toolset_drops_everything(self):
        """本轮没带任何工具时，泄漏文本不应解析出调用。"""
        g = TextualToolCallGuard()
        g.feed(DO + 'invoke name="Bash">' + DC + "invoke>")
        assert g.drain() == []
        assert "DSML" in g.rescue_text

    def test_rescue_text_empty_when_calls_parsed(self):
        """兜底成功转出调用时无需补救（不重复回显裸码）。"""
        g = TextualToolCallGuard(["Bash"])
        g.feed(DO + 'invoke name="Bash">' + DC + "invoke>")
        assert len(g.drain()) == 1
        assert g.rescue_text == ""

    def test_rescue_text_empty_when_not_suppressed(self):
        """未发生泄漏时无补救文本。"""
        g = TextualToolCallGuard(["Bash"])
        g.feed("正常正文")
        assert g.rescue_text == ""
