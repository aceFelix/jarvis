"""OpenAIProvider 补充测试 — 覆盖 test_openai_provider.py 未覆盖的路径。

覆盖内容：
- _messages_to_openai：ThinkingContent 过滤、图片内容、纯文本占位符、
  tool_result 多模态 / 纯文本、assistant content=null 规范
- _parse_tool_args：Level 4 分段解析、Level 5 正则兜底（含 timeout 数字）

@author aceFelix
"""

from __future__ import annotations

from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.llm.openai_provider import _messages_to_openai, _parse_tool_args


class TestMessagesToOpenAI:
    """_messages_to_openai 补充场景。"""

    def test_thinking_content_filtered(self) -> None:
        """assistant 的 ThinkingContent 不参与文本拼接。"""
        msgs = [Message(role="assistant", content=[
            ThinkingContent(text="思考过程"),
            TextContent(text="正式回答"),
        ])]
        out = _messages_to_openai(msgs, "sys")
        assert out[1]["role"] == "assistant"
        assert out[1]["content"] == "正式回答"

    def test_assistant_tool_calls_content_null(self) -> None:
        """只有 tool_calls 的 assistant 消息 content 必须为 None（OpenAI 规范）。"""
        msgs = [Message(role="assistant", content=[
            ToolUseContent(id="call_1", name="Bash", input={"cmd": "date"}),
        ])]
        out = _messages_to_openai(msgs, "sys")
        assert out[1]["content"] is None
        assert out[1]["tool_calls"][0]["function"]["name"] == "Bash"

    def test_assistant_text_and_tool_calls(self) -> None:
        """文本 + tool_calls 并存时 content 为文本。"""
        msgs = [Message(role="assistant", content=[
            TextContent(text="先说一下"),
            ToolUseContent(id="call_1", name="Read", input={"file_path": "a.txt"}),
        ])]
        out = _messages_to_openai(msgs, "sys")
        assert out[1]["content"] == "先说一下"
        assert len(out[1]["tool_calls"]) == 1

    def test_image_content_multimodal(self) -> None:
        """多模态下图片转 image_url data URI。"""
        img = ImageContent(data="AAA", media_type="image/png")
        msgs = [Message(role="user", content=[TextContent(text="看图"), img])]
        out = _messages_to_openai(msgs, "sys")
        assert out[1]["content"][1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAA"},
        }

    def test_skip_images_only_image_no_text(self) -> None:
        """纯文本模式下只有图片（无文本）→ 不产出 user 消息（无内容可发）。"""
        img = ImageContent(data="AAA", media_type="image/png")
        msgs = [Message(role="user", content=[img])]
        out = _messages_to_openai(msgs, "sys", skip_images=True)
        assert len(out) == 1  # 只有 system

    def test_skip_images_with_text_placeholder(self) -> None:
        """纯文本模式：图片 + 文本 → 文本后追加占位符。"""
        img = ImageContent(data="AAA", media_type="image/png")
        msgs = [Message(role="user", content=[TextContent(text="看图"), img])]
        out = _messages_to_openai(msgs, "sys", skip_images=True)
        assert "图片已省略" in out[1]["content"][0]["text"]

    def test_tool_result_images_multimodal(self) -> None:
        """多模态下 tool_result 图片以文本描述附加。"""
        img = ImageContent(data="BBB", media_type="image/jpeg")
        msgs = [Message(role="user", content=[
            ToolResultContent(tool_use_id="call_1", content="截图", images=[img]),
        ])]
        out = _messages_to_openai(msgs, "sys")
        assert out[1]["role"] == "tool"
        assert "[附带 1 张图片]" in out[1]["content"]

    def test_tool_result_without_images(self) -> None:
        """无图片的 tool_result 直接透传 content。"""
        msgs = [Message(role="user", content=[
            ToolResultContent(tool_use_id="call_1", content="2026-07-30"),
        ])]
        out = _messages_to_openai(msgs, "sys")
        assert out[1]["content"] == "2026-07-30"

    def test_system_message_in_list_skipped(self) -> None:
        """消息列表里的 system 消息跳过（system 已作为独立参数）。"""
        msgs = [Message.system_text("被跳过"), Message.user_text("hi")]
        out = _messages_to_openai(msgs, "SYS")
        assert out[0] == {"role": "system", "content": "SYS"}
        assert out[1]["role"] == "user"

    def test_user_tool_result_and_text_combined(self) -> None:
        """同一 user 消息里既有文本又有 tool_result → 分开成两条。"""
        msgs = [Message(role="user", content=[
            TextContent(text="处理结果如下"),
            ToolResultContent(tool_use_id="c1", content="done"),
        ])]
        out = _messages_to_openai(msgs, "sys")
        roles = [m["role"] for m in out]
        assert roles == ["system", "user", "tool"]


class TestParseToolArgs:
    """_parse_tool_args 深层 fallback。"""

    def test_level4_partial_decode(self) -> None:
        """Level 4：前段可解析 JSON，后面跟垃圾 → 返回前段对象。"""
        assert _parse_tool_args('{"a": 1} trailing garbage') == {"a": 1}

    def test_level5_regex_fallback(self) -> None:
        """Level 5：正则提取字段。"""
        assert _parse_tool_args('garbage "command": "ls -la"') == {"command": "ls -la"}

    def test_level5_timeout_int(self) -> None:
        """Level 5：timeout 数字字段转 int。"""
        assert _parse_tool_args('noise "timeout": 30') == {"timeout": 30}

    def test_level5_escaped_unescape(self) -> None:
        """Level 5：反转义 \\n 等。"""
        result = _parse_tool_args('junk "content": "line1\\nline2"')
        assert result["content"] == "line1\nline2"

    def test_whitespace_only(self) -> None:
        assert _parse_tool_args("   ") == {}

    def test_nested_json_valid(self) -> None:
        """合法嵌套 JSON 正常解析。"""
        assert _parse_tool_args('{"a": {"b": [1, 2]}}') == {"a": {"b": [1, 2]}}
