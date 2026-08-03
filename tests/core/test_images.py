"""图片 / 剪贴板助手单元测试。

覆盖 _hash_image 去重哈希、_load_image_from_path（真实 PIL 编码）、
_load_image_from_clipboard（mock 剪贴板，覆盖 Image / 文件路径列表 / None
三种情况）、_pending_images 与 _auto_attach_clipboard_image 的自动附加逻辑。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import agent.core.images as images
from agent.core.context import ToolContext
from agent.core.message import ImageContent, Message


class TestHashImage:
    """图片哈希去重。"""

    def test_hash_is_stable(self) -> None:
        img = ImageContent(data="aGVsbG8=", media_type="image/png")
        assert images._hash_image(img) == images._hash_image(img)

    def test_hash_differs_for_different_data(self) -> None:
        a = ImageContent(data="abc", media_type="image/png")
        b = ImageContent(data="def", media_type="image/png")
        assert images._hash_image(a) != images._hash_image(b)

    def test_hash_includes_media_type(self) -> None:
        a = ImageContent(data="abc", media_type="image/png")
        b = ImageContent(data="abc", media_type="image/jpeg")
        assert images._hash_image(a) != images._hash_image(b)

    def test_hash_returns_md5_hex(self) -> None:
        img = ImageContent(data="data", media_type="image/png")
        assert len(images._hash_image(img)) == 32  # md5 hex 长度


class TestLoadImageFromPath:
    """从文件加载图片（依赖真实 PIL）。"""

    def test_loads_png_file(self, tmp_path: Path) -> None:
        from PIL import Image

        p = tmp_path / "pic.png"
        Image.new("RGB", (64, 64), color="red").save(p)
        content = images._load_image_from_path(str(p))
        assert content is not None
        assert content.type == "image"
        assert content.media_type == "image/jpeg"  # 默认转 JPEG

    def test_nonexistent_path_returns_none(self) -> None:
        assert images._load_image_from_path("Z:/no/such/file.png") is None

    def test_directory_path_returns_none(self, tmp_path: Path) -> None:
        assert images._load_image_from_path(str(tmp_path)) is None

    def test_invalid_image_returns_none(self, tmp_path: Path) -> None:
        p = tmp_path / "broken.png"
        p.write_text("not an image", encoding="utf-8")
        assert images._load_image_from_path(str(p)) is None


class TestLoadImageFromClipboard:
    """从剪贴板加载图片（mock PIL.ImageGrab）。"""

    def test_clipboard_none_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("PIL.ImageGrab.grabclipboard", lambda: None)
        assert images._load_image_from_clipboard() is None

    def test_clipboard_image_encoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from PIL import Image

        img = Image.new("RGB", (32, 32), color="blue")
        monkeypatch.setattr("PIL.ImageGrab.grabclipboard", lambda: img)
        content = images._load_image_from_clipboard()
        assert content is not None
        assert content.media_type == "image/jpeg"

    def test_clipboard_file_list_with_image(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Windows 剪贴板返回文件路径列表：命中支持的图片扩展名。"""
        from PIL import Image

        p = tmp_path / "clip.png"
        Image.new("RGB", (16, 16), color="green").save(p)
        monkeypatch.setattr("PIL.ImageGrab.grabclipboard", lambda: [str(p)])
        content = images._load_image_from_clipboard()
        assert content is not None

    def test_clipboard_file_list_unsupported_ext(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """文件列表里只有不支持的扩展名 → 返回 None。"""
        monkeypatch.setattr("PIL.ImageGrab.grabclipboard", lambda: ["C:/x/notes.txt"])
        assert images._load_image_from_clipboard() is None

    def test_clipboard_file_list_missing_file(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """文件列表里的图片文件不存在 → 返回 None。"""
        monkeypatch.setattr("PIL.ImageGrab.grabclipboard", lambda: ["C:/no/such/img.png"])
        assert images._load_image_from_clipboard() is None


class TestPendingImages:
    """待发送图片列表。"""

    def test_pending_initializes_empty(self) -> None:
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        assert images._pending_images(ctx) == []

    def test_pending_uses_existing_list(self) -> None:
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        img = ImageContent(data="abc")
        ctx.extra["pending_images"] = [img]
        assert images._pending_images(ctx) == [img]


class FakeUI:
    """用于验证 ui.info 调用的 UI 桩。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, text: str) -> None:
        self.messages.append(text)


class TestAutoAttachClipboardImage:
    """自动附加剪贴板图片。"""

    def _ctx_with_pending(self) -> ToolContext:
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        ctx.extra["pending_images"] = [ImageContent(data="abc")]
        return ctx

    def test_existing_pending_returns_directly(self) -> None:
        """已有待发送图片时直接返回，不碰剪贴板。"""
        ctx = self._ctx_with_pending()
        ui = FakeUI()
        result = images._auto_attach_clipboard_image(ctx, ui)
        assert result == [ImageContent(data="abc")]
        assert ui.messages == []

    def test_no_clipboard_image_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        monkeypatch.setattr(images, "_load_image_from_clipboard", lambda: None)
        ui = FakeUI()
        assert images._auto_attach_clipboard_image(ctx, ui) == []
        assert ui.messages == []

    def test_new_clipboard_image_attached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        img = ImageContent(data="new-img")
        monkeypatch.setattr(images, "_load_image_from_clipboard", lambda: img)
        ui = FakeUI()
        result = images._auto_attach_clipboard_image(ctx, ui)
        assert result == [img]
        # 记录哈希，避免下次重复附加
        assert ctx.extra["_last_clipboard_image_hash"] == images._hash_image(img)
        assert len(ui.messages) == 1
        assert "剪贴板图片" in ui.messages[0]

    def test_same_clipboard_image_deduplicated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        img = ImageContent(data="dup")
        monkeypatch.setattr(images, "_load_image_from_clipboard", lambda: img)
        ui = FakeUI()
        first = images._auto_attach_clipboard_image(ctx, ui)
        assert len(first) == 1
        # 第二次：剪贴板同一张图 → 去重，不附加
        second = images._auto_attach_clipboard_image(ctx, ui)
        assert second == []
        assert len(ui.messages) == 1

    def test_pending_cleared_after_consume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pending 被 pop 后，再调用会回到剪贴板路径。"""
        ctx = self._ctx_with_pending()
        ui = FakeUI()
        images._auto_attach_clipboard_image(ctx, ui)
        # pending 已清空；剪贴板返回 None → 空列表
        monkeypatch.setattr(images, "_load_image_from_clipboard", lambda: None)
        assert images._auto_attach_clipboard_image(ctx, ui) == []
