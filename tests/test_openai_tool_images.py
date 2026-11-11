"""OpenAI 兼容路径的工具图片通道测试。

## 背景（2026-11-12 实机）

DeepSeek 的 `deepseek-flash` 是**多模态**模型（官方 Vision 文档：可读截图文字），
但 jarvis 里它一直"看不见"截图。根因不在模型，在本模块：

- OpenAI 兼容协议里 ``role="tool"`` 的 content **只能是字符串**，图片塞不进去；
- DeepSeek 明文规定图片**只允许出现在 user 消息**（system/assistant 里直接 400）。

旧实现只在 tool 消息末尾拼一句 ``[附带 N 张图片]`` 就结束，等于把图丢了。现在改为
另起一条 ``user`` 消息带 ``image_url``（base64 data URL，与用户直接贴图同构）。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.images import image_receipt, vision_hint
from agent.core.message import (
    ImageContent,
    Message,
    ToolResultContent,
    ToolUseContent,
)
from agent.llm.openai_provider import _MAX_TOOL_IMAGES, _messages_to_openai


def _jpeg(data: str = "QUJD") -> ImageContent:
    return ImageContent(data=data, media_type="image/jpeg")


def _history(images: list[ImageContent] | None = None) -> list[Message]:
    """一轮最简工具调用：assistant(tool_use) + user(tool_result[+images])。"""
    assistant = Message(
        role="assistant",
        content=[ToolUseContent(id="call_1", name="ScreenShot", input={"format": "jpeg"})],
    )
    user = Message(
        role="user",
        content=[
            ToolResultContent(
                tool_use_id="call_1",
                content="已全屏截图\n路径: D:/tmp/a.png",
                images=list(images or []),
            )
        ],
    )
    return [assistant, user]


def _image_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    """取一条消息里的 image_url part（``content`` 为字符串时返回空）。"""
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [
        part for part in content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]


class TestToolImagesReachTheModel:
    def test_images_become_user_message(self) -> None:
        """有视觉能力时：tool 之后必须多出一条带 image_url 的 user 消息。"""
        out = _messages_to_openai(_history([_jpeg()]), "sys", skip_images=False)
        assert [m["role"] for m in out] == ["system", "assistant", "tool", "user"]
        parts = _image_parts(out[-1])
        assert len(parts) == 1
        assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_tool_message_points_to_next_message(self) -> None:
        """tool 消息里的说明要说清「图在下一条」，否则模型不知道往下看。"""
        out = _messages_to_openai(_history([_jpeg()]), "sys", skip_images=False)
        tool_message = next(m for m in out if m["role"] == "tool")
        assert "见下一条消息" in tool_message["content"]

    def test_multiple_images_are_batched_into_one_message(self) -> None:
        """一轮多张图合并为一条 user 消息（少一条消息、缓存更稳）。"""
        out = _messages_to_openai(_history([_jpeg(), _jpeg("QUJDRA==")]), "sys")
        users = [m for m in out if m["role"] == "user"]
        assert len(users) == 1
        assert len(_image_parts(users[0])) == 2

    def test_image_count_is_capped(self) -> None:
        """超量只保留最近 N 张，并在说明里写清楚。"""
        many = [_jpeg(f"AAAA{i}") for i in range(_MAX_TOOL_IMAGES + 3)]
        out = _messages_to_openai(_history(many), "sys")
        parts = _image_parts(out[-1])
        assert len(parts) == _MAX_TOOL_IMAGES
        head = out[-1]["content"][0]["text"]
        assert "仅保留最近" in head

    def test_no_image_no_extra_message(self) -> None:
        """没有图就不该凭空多一条消息。"""
        out = _messages_to_openai(_history(), "sys", skip_images=False)
        assert [m["role"] for m in out] == ["system", "assistant", "tool"]


class TestNoVisionPath:
    def test_images_are_omitted_not_sent(self) -> None:
        """无视觉模型：不发 image_url，且说明里要写「已省略」。"""
        out = _messages_to_openai(_history([_jpeg()]), "sys", skip_images=True)
        assert all(not _image_parts(m) for m in out)
        tool_message = next(m for m in out if m["role"] == "tool")
        assert "图片已省略" in tool_message["content"]


class TestReceiptWording:
    """回执三态：不知道才说含糊话，知道就直说。"""

    def test_visible_true(self) -> None:
        line = image_receipt(True, fmt="jpeg", max_size=1280, visible=True)
        assert "你可以看到这张图" in line
        assert "附上不等于看见" not in line

    def test_visible_false(self) -> None:
        line = image_receipt(True, fmt="jpeg", max_size=1280, visible=False)
        assert "无视觉输入" in line
        assert "不要描述画面内容" in line
        assert "附上不等于看见" not in line

    def test_unknown_keeps_conditional_clause(self) -> None:
        assert "附上不等于看见" in image_receipt(True, visible=None)

    def test_not_attached(self) -> None:
        assert image_receipt(False) == "图片附上: 否"


class TestVisionHint:
    """`vision_hint` 只读配置，不猜。"""

    @staticmethod
    def _ctx(model_type: str | None) -> Any:
        class _Settings:
            model = "deepseek-flash"
            custom_models = {
                "deepseek-flash": {"model_type": model_type} if model_type else {}
            }

        class _Ctx:
            settings = _Settings()

        return _Ctx()

    def test_no_settings(self) -> None:
        assert vision_hint(None) is None
        assert vision_hint(object()) is None

    def test_multimodal(self) -> None:
        assert vision_hint(self._ctx("multimodal")) is True

    def test_text(self) -> None:
        assert vision_hint(self._ctx("text")) is False

    def test_unknown_is_not_vision(self) -> None:
        """未确认按无视觉处理（与 model_capability 的唯一判据一致）。"""
        assert vision_hint(self._ctx(None)) is False
