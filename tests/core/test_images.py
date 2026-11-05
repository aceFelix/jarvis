"""图片 / 剪贴板助手单元测试。

覆盖 _hash_image 去重哈希、_load_image_from_path（真实 PIL 编码）、
_load_image_from_clipboard（mock 剪贴板，覆盖 Image / 文件路径列表 / None
三种情况）、_pending_images 与 paste_clipboard_image 的 Ctrl+V 显式贴图逻辑。

另覆盖图片编码底座 encode_pil_image（格式 / 缩放 / 透明通道 / 不改入参），以及
“编码实现只能有一份”的结构约束：三个图片工具不得再内联副本，core 层不得
反向 import tools 层。

需要真实 Pillow 读写图片的用例（TestLoadImageFromPath /
TestLoadImageFromClipboard / TestEncodePilImage）在 Pillow 未安装（CI 仅装 dev 依赖）时跳过；
其余用例不依赖 PIL 照常运行。

@author aceFelix
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import agent.core.images as images
from agent.core.context import ToolContext
from agent.core.message import ImageContent, Message

try:
    from PIL import Image  # noqa: F401  仅用于探测 Pillow 是否可用
    _PIL_AVAILABLE = True
except ImportError:
    # CI 只装 dev 依赖，Pillow 属于可选 gui/daemon extras。
    # 需要真实 PIL 读写图片 / 解析 "PIL.ImageGrab" 路径的用例在此环境下跳过，
    # 其余纯逻辑用例不依赖 PIL 照常运行。
    _PIL_AVAILABLE = False


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


@pytest.mark.skipif(
    not _PIL_AVAILABLE, reason="Pillow 未安装（可选 gui/daemon extras），需真实 PIL 读写图片"
)
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


@pytest.mark.skipif(
    not _PIL_AVAILABLE, reason="Pillow 未安装（可选 gui/daemon extras），需真实 PIL 构造剪贴板图片"
)
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
    """用于验证 ui.info / ui.warn 调用的 UI 桩。"""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, text: str) -> None:
        self.messages.append(text)

    def warn(self, text: str) -> None:
        self.messages.append(text)


class TestPasteClipboardImage:
    """Ctrl+V 显式粘贴剪贴板图片。"""

    def test_no_clipboard_image_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """剪贴板无图：警告且不新增。"""
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        monkeypatch.setattr(images, "_load_image_from_clipboard", lambda: None)
        ui = FakeUI()
        assert images.paste_clipboard_image(ctx, ui) == 0
        assert images._pending_images(ctx) == []
        assert len(ui.messages) == 1

    def test_new_clipboard_image_appended_to_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """剪贴板有图：加入待发送列表并提示。"""
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        img = ImageContent(data="new-img")
        monkeypatch.setattr(images, "_load_image_from_clipboard", lambda: img)
        ui = FakeUI()
        assert images.paste_clipboard_image(ctx, ui) == 1
        assert images._pending_images(ctx) == [img]
        assert "剪贴板图片" in ui.messages[0]

    def test_same_image_pasted_twice_deduplicated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """同一张图连按 Ctrl+V：列表内去重，不重复添加。"""
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        img = ImageContent(data="dup")
        monkeypatch.setattr(images, "_load_image_from_clipboard", lambda: img)
        ui = FakeUI()
        assert images.paste_clipboard_image(ctx, ui) == 1
        assert images.paste_clipboard_image(ctx, ui) == 0
        assert len(images._pending_images(ctx)) == 1

    def test_paste_after_send_accumulates_second_image(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不同图片可多张叠加待发（模拟 /image 已贴一张后再 Ctrl+V）。"""
        ctx = ToolContext(workdir="/w", messages=[Message(role="user")])
        ctx.extra["pending_images"] = [ImageContent(data="first")]
        img = ImageContent(data="second")
        monkeypatch.setattr(images, "_load_image_from_clipboard", lambda: img)
        ui = FakeUI()
        assert images.paste_clipboard_image(ctx, ui) == 1
        assert [i.data for i in images._pending_images(ctx)] == ["first", "second"]
        assert "2 张" in ui.messages[0]


@pytest.mark.skipif(
    not _PIL_AVAILABLE, reason="Pillow 未安装（可选 gui/daemon extras），需真实 PIL 编解码图片"
)
class TestEncodePilImage:
    """PIL 图片编码——截图 / 拍照 / 网页截图 / 贴图的共同底座。

    这些断言锁的是体积与格式行为（缩到多长、什么时候转 RGB、能不能被
    API 侧解开），而不是实现住在哪个文件里——后者由 TestEncoderIsShared 守。
    """

    @staticmethod
    def _decode(content: ImageContent):
        """把编码结果解回 PIL Image，用于验证格式与尺寸。"""
        from PIL import Image

        return Image.open(io.BytesIO(base64.b64decode(content.data)))

    def test_jpeg_is_the_default_and_decodes(self) -> None:
        from PIL import Image

        content = images.encode_pil_image(Image.new("RGB", (16, 16), color="red"))
        assert content.media_type == "image/jpeg"
        assert self._decode(content).format == "JPEG"

    def test_png_keeps_alpha_channel(self) -> None:
        """PNG 不转 RGB：转了就丢透明通道，模型看到的图与工具截到的不一致。"""
        from PIL import Image

        content = images.encode_pil_image(
            Image.new("RGBA", (16, 16), color=(0, 0, 0, 0)), fmt="png"
        )
        assert content.media_type == "image/png"
        assert self._decode(content).mode == "RGBA"

    def test_rgba_is_converted_for_jpeg(self) -> None:
        """JPEG 不支持透明，RGBA/LA/P 必须先转 RGB，否则 PIL 直接报错。"""
        from PIL import Image

        for mode in ("RGBA", "LA", "P"):
            content = images.encode_pil_image(Image.new(mode, (8, 8)), fmt="jpeg")
            assert self._decode(content).mode == "RGB"

    def test_scales_down_keeping_aspect_ratio(self) -> None:
        """超上限时等比缩放（默认 1280）：长边 2000 → 1280，短边跟着变。"""
        from PIL import Image

        content = images.encode_pil_image(Image.new("RGB", (2000, 1000)))
        assert self._decode(content).size == (1280, 640)

    def test_max_size_zero_keeps_original(self) -> None:
        """max_size=0 表示不缩放——不能当成“缩到 0 像素”。"""
        from PIL import Image

        content = images.encode_pil_image(Image.new("RGB", (2000, 100)), max_size=0)
        assert self._decode(content).size == (2000, 100)

    def test_small_image_is_not_upscaled(self) -> None:
        """小图不拉长：拉长只会肥 token 不会变清楚。"""
        from PIL import Image

        content = images.encode_pil_image(Image.new("RGB", (32, 24)), max_size=1280)
        assert self._decode(content).size == (32, 24)

    def test_input_image_is_not_mutated(self) -> None:
        """缩放走副本：原图尺寸不能变，否则连续复用同一张图的调用方会被隐性改坏。"""
        from PIL import Image

        img = Image.new("RGB", (2000, 100))
        images.encode_pil_image(img, max_size=100)
        assert img.size == (2000, 100)


#: 持有过图片编码副本的三个工具模块
_TOOL_MODULES = (
    "agent.tools.system.screen",
    "agent.tools.vision.camera",
    "agent.tools.web.browser",
)


class TestEncoderIsShared:
    """编码实现只能有一份，且它必须在 core 层。

    为什么单独测这个：三份逐字相同的副本能共存这么久，靠的是一个当时写下来的理由
    （browser.py 旧注释：“独立内联避免跨模块耦合”）。光重构不钉桩，下次有人图
    方便又会内联一份。同时钉住另一件事：core/images.py 曾为了复用其中一份而反向
    import ScreenShotTool（下层依赖上层），且 pyautogui 缺失时会被那句
    ``except ImportError`` 误报成「缺少 Pillow」。
    """

    @staticmethod
    def _module_source(module_name: str) -> str:
        import importlib
        import inspect

        return inspect.getsource(importlib.import_module(module_name))

    @pytest.mark.parametrize("module_name", _TOOL_MODULES)
    def test_tool_module_has_no_private_copy(self, module_name: str) -> None:
        src = self._module_source(module_name)
        assert "def _encode_image" not in src, f"{module_name} 又内联了一份图片编码"
        assert "encode_pil_image" in src, f"{module_name} 应该引用 core 层的共享实现"

    @pytest.mark.parametrize("module_name", _TOOL_MODULES)
    def test_tool_module_still_reports_via_shared_wording(self, module_name: str) -> None:
        """回执措辞仍走同一出处（与图片编码同属“三处工具不得各写一份”）。"""
        assert "image_receipt" in self._module_source(module_name)

    def test_core_images_does_not_import_tools(self) -> None:
        """依赖方向：core 层不得反向 import tools 层。"""
        src = self._module_source("agent.core.images")
        bad = [
            line.strip()
            for line in src.splitlines()
            if re.match(r"^\s*(from|import)\s+agent\.tools", line)
        ]
        assert not bad, f"core/images.py 反向依赖 tools 层: {bad}"
