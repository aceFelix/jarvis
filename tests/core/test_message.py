"""对话消息类型单元测试。

覆盖 Message 与各 content block（TextContent / ThinkingContent / ToolUseContent /
ToolResultContent / ImageContent）的 type 属性、get_text / get_thinking /
get_tool_uses 等聚合方法。

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


class TestContentBlocks:
    """各 content block 的 type 属性与字段。"""

    def test_text_content_type(self) -> None:
        t = TextContent(text="hello")
        assert t.type == "text"
        assert t.text == "hello"
        # 默认 text 为空串
        assert TextContent().text == ""

    def test_thinking_content_type(self) -> None:
        t = ThinkingContent(text="推理过程")
        assert t.type == "thinking"
        assert t.text == "推理过程"

    def test_tool_use_content_type(self) -> None:
        t = ToolUseContent(id="u1", name="Bash", input={"command": "ls"})
        assert t.type == "tool_use"
        assert t.id == "u1"
        assert t.name == "Bash"
        assert t.input == {"command": "ls"}

    def test_tool_result_content_type(self) -> None:
        t = ToolResultContent(tool_use_id="u1", content="ok")
        assert t.type == "tool_result"
        assert t.tool_use_id == "u1"
        assert t.content == "ok"
        # 默认字段
        assert t.is_error is False
        assert t.images == []

    def test_image_content_type(self) -> None:
        img = ImageContent(data="aGVsbG8=")
        assert img.type == "image"
        assert img.data == "aGVsbG8="
        # 默认 media_type 为 image/png
        assert img.media_type == "image/png"

    def test_image_content_custom_media_type(self) -> None:
        img = ImageContent(data="xxx", media_type="image/jpeg")
        assert img.media_type == "image/jpeg"


class TestMessageFactory:
    """Message 便捷工厂方法。"""

    def test_user_text(self) -> None:
        m = Message.user_text("你好")
        assert m.role == "user"
        assert len(m.content) == 1
        assert isinstance(m.content[0], TextContent)
        assert m.content[0].text == "你好"

    def test_assistant_text(self) -> None:
        m = Message.assistant_text("回复")
        assert m.role == "assistant"
        assert m.content[0].text == "回复"

    def test_system_text(self) -> None:
        m = Message.system_text("system prompt")
        assert m.role == "system"
        assert m.content[0].text == "system prompt"

    def test_default_id_and_timestamp(self) -> None:
        m1 = Message(role="user")
        m2 = Message(role="user")
        assert m1.id
        assert m1.id != m2.id  # 每次生成不同 id
        assert m1.timestamp > 0
        assert m1.timestamp <= m2.timestamp  # 时间不倒退


class TestMessageAggregation:
    """Message 的文本聚合方法。"""

    def _mixed_message(self) -> Message:
        """构造包含 text + thinking + tool_use 的 assistant 消息。"""
        return Message(
            role="assistant",
            content=[
                ThinkingContent(text="先思考"),
                TextContent(text="你好"),
                ToolUseContent(id="u1", name="Bash", input={"command": "ls"}),
                TextContent(text="再见"),
            ],
        )

    def test_get_text_joins_text_blocks_only(self) -> None:
        """get_text 只拼接 TextContent，不含思考内容。"""
        m = self._mixed_message()
        assert m.get_text() == "你好再见"

    def test_get_text_empty(self) -> None:
        assert Message(role="user").get_text() == ""

    def test_get_thinking(self) -> None:
        m = self._mixed_message()
        assert m.get_thinking() == "先思考"

    def test_get_thinking_empty(self) -> None:
        assert Message(role="assistant").get_thinking() == ""

    def test_get_tool_uses(self) -> None:
        m = self._mixed_message()
        uses = m.get_tool_uses()
        assert len(uses) == 1
        assert uses[0].id == "u1"
        assert uses[0].name == "Bash"

    def test_get_tool_uses_empty(self) -> None:
        assert Message.user_text("hi").get_tool_uses() == []
