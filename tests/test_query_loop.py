"""QueryLoop、上下文压缩、图片淘汰测试。"""

import pytest

from agent.core.message import Message, TextContent, ThinkingContent, ToolResultContent, ToolUseContent, ImageContent
from agent.core.query_loop import _evict_old_images, _collapse_old_tool_results


class TestImageEviction:
    """图片淘汰测试。"""

    def test_single_image_preserved(self):
        """单张图片应被保留。"""
        img = ImageContent(media_type="image/jpeg", data="fakebase64")
        msgs = [
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_1", content="截图结果", images=[img])
            ]),
        ]
        _evict_old_images(msgs)
        # 只有一张图，应保留
        block = msgs[0].content[0]
        assert block.images == [img]

    def test_multiple_images_evict_old(self):
        """多张图片时，只保留最新一张，其余替换为文本。"""
        img1 = ImageContent(media_type="image/jpeg", data="fake1")
        img2 = ImageContent(media_type="image/jpeg", data="fake2")
        msgs = [
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_1", content="第一张", images=[img1])
            ]),
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_2", content="第二张", images=[img2])
            ]),
        ]
        _evict_old_images(msgs)
        # 第二张（最新）应保留
        block_latest = msgs[1].content[0]
        assert block_latest.images == [img2]
        # 第一张应被替换为文本
        block_old = msgs[0].content[0]
        assert block_old.images == []
        assert "截图已处理" in block_old.content

    def test_no_images_noop(self):
        """无图片消息不应被修改。"""
        msgs = [
            Message(role="user", content=[TextContent(text="hello")]),
            Message(role="assistant", content=[TextContent(text="hi")]),
        ]
        original = [msg.content[0].text for msg in msgs]
        _evict_old_images(msgs)
        result = [msg.content[0].text for msg in msgs]
        assert result == original


class TestToolResultCollapse:
    """工具结果折叠测试。"""

    def test_keep_recent_preserved(self):
        """最近 N 个工具结果应保留完整内容。"""
        msgs = [
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_1", content="结果1")
            ]),
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_2", content="结果2")
            ]),
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_3", content="结果3")
            ]),
        ]
        _collapse_old_tool_results(msgs, keep_recent=2)
        # 后2个保留
        assert msgs[1].content[0].content == "结果2"
        assert msgs[2].content[0].content == "结果3"
        # 第1个被折叠
        assert "已完成" in msgs[0].content[0].content

    def test_fewer_than_keep_all_preserved(self):
        """工具结果少于保留数时，全部保留。"""
        msgs = [
            Message(role="user", content=[
                ToolResultContent(tool_use_id="call_1", content="结果1")
            ]),
        ]
        _collapse_old_tool_results(msgs, keep_recent=4)
        assert msgs[0].content[0].content == "结果1"


class TestMessageSerialization:
    """消息序列化测试。"""

    def test_image_skip_in_text_mode(self):
        """纯文本模型应跳过 ImageContent。"""
        from agent.llm.openai_provider import _messages_to_openai
        img = ImageContent(media_type="image/jpeg", data="fakebase64")
        msg = Message(role="user", content=[
            ToolResultContent(tool_use_id="call_1", content="截图", images=[img])
        ])
        # 多模态：有 image_url
        result_mm = _messages_to_openai([msg], "system", skip_images=False)
        content_mm = result_mm[1]["content"]
        has_image = any(
            isinstance(c, dict) and c.get("type") == "image_url"
            for c in content_mm
        )
        assert has_image, "多模态应包含 image_url"

        # 纯文本：无 image_url，有占位
        result_text = _messages_to_openai([msg], "system", skip_images=True)
        content_text = result_text[1]["content"]
        has_image_text = any(
            isinstance(c, dict) and c.get("type") == "image_url"
            for c in content_text
        )
        assert not has_image_text, "纯文本不应包含 image_url"
        assert "纯文本模型" in str(content_text), "纯文本应有图片省略提示"
