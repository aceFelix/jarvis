"""图片 / 剪贴板助手 —— 加载、编码、去重待发送图片。

从 main 拆出，供 main（REPL Ctrl+V 贴图）与 media_commands
（/paste /image 命令）共用。

历史：曾在"空回车"与"每次发消息"时自动检测剪贴板图片，但剪贴板内容
长期残留，导致普通提问被反复误贴图，已改为仅 Ctrl+V / /paste 显式添加。
"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.message import ImageContent
from agent.ui.cli import RichCLI

# 「附上」与「看见」是两件事：纯文本模型、以及没确认过视觉能力的模型，收到的
# 是一句「图片已省略」的占位说明（provider 层转换时替换）。这句约束必须出现在
# 每一次图片回执里——只在 system prompt 里说过一次，长会话里它会被忘掉，
# 而工具回执是模型当下唯一在读的东西。
_IMAGE_SEE_CLAUSE = "，附上不等于看见：无视觉能力的模型只收到省略说明，不得声称亲眼看过"


@dataclass(frozen=True)
class ImageRef:
    """一张「给人看」的图片引用：只带路径与元信息，**不含字节**。

    为什么与 ``ImageContent`` 分开（2026-11-11 图片窗口 P0）：

    - ``ImageContent`` 是送给 provider 的 base64 块，能不能被看见由
      ``model_type`` 决定，是**给模型看**的尺寸链（≤1280 / JPEG85）；
    - ``ImageRef`` 只服务终端图片窗口与 trace，是**给人看**的尺寸链（原图 + 屏幕适配），
      永不参与 provider 序列化；
    - 两条链混用会让用户误判「模型看到的分辨率」，也会让窗口去解 1280 的糊图。

    Attributes:
        path: 落盘绝对路径（唯一必填字段；没有路径就没有展示位）。
        source: 产出工具名（ScreenShot / CameraShot / BrowserScreenshot）。
        width: 原图宽（像素，不是回传给模型的缩放后宽度）。
        height: 原图高。
        media_type: 落盘文件的实际 MIME。
        bytes: 落盘文件字节数。
        ts: 产出时刻（ISO，带时区，展示用）。

    @author aceFelix
    """

    path: str
    source: str = ""
    width: int = 0
    height: int = 0
    media_type: str = ""
    bytes: int = 0
    ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转成可塞进 trace 事件/``trace_meta`` 的字典；缺省字段直接省略。"""
        data: dict[str, Any] = {"path": self.path}
        for key, value in (
            ("source", self.source),
            ("width", self.width),
            ("height", self.height),
            ("media_type", self.media_type),
            ("bytes", self.bytes),
            ("ts", self.ts),
        ):
            if value:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, raw: Any) -> "ImageRef | None":
        """从事件/``trace_meta`` 还原；缺路径或类型不对时返回 ``None``。

        宽容而非严格：脏数据只让这一条不展示，绝不抛给工具主流程
        （与 ``trace._as_event_list`` 同口径：错了就不写，不写假值）。
        """
        if not isinstance(raw, dict):
            return None
        path = str(raw.get("path") or "").strip()
        if not path:
            return None
        return cls(
            path=path,
            source=str(raw.get("source") or ""),
            width=_as_int(raw.get("width")),
            height=_as_int(raw.get("height")),
            media_type=str(raw.get("media_type") or ""),
            bytes=_as_int(raw.get("bytes")),
            ts=str(raw.get("ts") or ""),
        )


def _as_int(value: Any) -> int:
    """宽容转 int：None/非法/负数一律 0（元信息缺失比抛错重要）。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def make_image_ref(
    img: Any,
    path: str | Path,
    *,
    source: str = "",
    fmt: str = "jpeg",
) -> ImageRef:
    """由 PIL 图与落盘路径构造 ``ImageRef``（尺寸取**原图**，与缩放链无关）。

    Args:
        img: PIL.Image.Image（编码前的那张）。
        path: 实际落盘路径。
        source: 产出工具名，供窗口标注。
        fmt: 落盘/回传格式（``jpeg`` / ``png``），只影响 ``media_type``。

    @author aceFelix
    """
    target = Path(path)
    width = height = size_bytes = 0
    try:
        width, height = (int(v) for v in img.size)
    except Exception:
        pass
    try:
        size_bytes = int(target.stat().st_size)
    except OSError:
        pass
    return ImageRef(
        path=str(target),
        source=source,
        width=width,
        height=height,
        media_type="image/png" if fmt == "png" else "image/jpeg",
        bytes=size_bytes,
        ts=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def encode_pil_image(img: Any, fmt: str = "jpeg", max_size: int = 1280) -> ImageContent:
    """把 PIL Image 缩放并编码为 base64 图片块（截图 / 拍照 / 网页截图共用）。

    为什么住在 core 层：这段逻辑此前在 ``screen.py``、``camera.py``、``browser.py``
    各抄了一份（函数体逐字相同），而本模块为了复用其中一份还反向 import 了
    ``ScreenShotTool`` —— 下层依赖上层。收拢到此处后四处均改为引用，反向依赖一并消除；
    顺带修掉一个误报：旧写法用 ``except ImportError`` 兜住对 ScreenShotTool 的导入，
    pyautogui 缺失时会被报成「缺少 Pillow」。

    实现要点：
    - 缩放到最长边 max_size（保持比例）以省 token；长边 <= 1568px 较稳妥，1280 为默认。
    - JPEG 体积远小于 PNG（屏幕内容偏照片式），默认 JPEG quality 85。
    - JPEG 不支持透明通道，RGBA/LA/P 模式自动转 RGB。
    - 不在此 import PIL：入参已是 PIL Image，各调用方自行延迟导入。

    Args:
        img: PIL.Image.Image（playwright 截图返回 bytes 时需先 ``Image.open``）。
        fmt: ``"jpeg"`` 或 ``"png"``。
        max_size: 最长边像素上限，``0`` 表示不缩放。

    @author aceFelix
    """
    work = img.copy()
    if max_size and max(work.size) > max_size:
        work.thumbnail((max_size, max_size))

    if fmt == "png":
        media_type = "image/png"
        pil_format = "PNG"
    else:
        media_type = "image/jpeg"
        pil_format = "JPEG"
        if work.mode in ("RGBA", "LA", "P"):
            work = work.convert("RGB")

    buf = io.BytesIO()
    save_kwargs: dict[str, Any] = {}
    if pil_format == "JPEG":
        save_kwargs["quality"] = 85
    work.save(buf, format=pil_format, **save_kwargs)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return ImageContent(data=data, media_type=media_type)


def _load_image_from_path(path: str) -> ImageContent | None:
    """从文件路径加载图片，缩放并编码为 ImageContent。

    @author aceFelix
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    p = Path(path).expanduser().resolve()
    if not p.exists():
        return None
    try:
        img = Image.open(p)
        img.load()
        return encode_pil_image(img, "jpeg", 1280)
    except Exception:
        return None


def _load_image_from_clipboard() -> ImageContent | None:
    """从系统剪贴板读取图片（Windows/macOS 支持），编码为 ImageContent。

    @author aceFelix
    """
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        return None

    data = ImageGrab.grabclipboard()
    if data is None:
        return None
    if isinstance(data, Image.Image):
        return encode_pil_image(data, "jpeg", 1280)
    # Windows 剪贴板有时是文件路径列表
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                ext = Path(item).suffix.lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                    content = _load_image_from_path(item)
                    if content is not None:
                        return content
    return None


def _pending_images(ctx: ToolContext) -> list[ImageContent]:
    """获取当前待发送的图片列表。

    @author aceFelix
    """
    return ctx.extra.setdefault("pending_images", [])


def _hash_image(img: ImageContent) -> str:
    """为 ImageContent 生成稳定哈希，用于去重。

    @author aceFelix
    """
    return hashlib.md5(f"{img.media_type}:{img.data}".encode()).hexdigest()


def image_receipt(
    attached: bool,
    *,
    fmt: str = "",
    max_size: int | None = None,
    visible: bool | None = None,
) -> str:
    """图片类工具（截图 / 拍照 / 网页截图）回执里「图片」那一行的统一措辞。

    为什么三个工具要共用一个函数：这句是模型判断「我到底看不看得见」的唯一依据，
    各写一份迟早会有一处写成「已回传: 是」——而工具其实只知道图片块挂上了结果，
    能不能被看见由 provider 按 model_type 决定（``agent/llm/model_capability.py``）。
    措辞越权一次，模型就有依据把「我没看到」汇报成「我验证过」。

    2026-11-12 补 ``visible``：光说「附上: 是」在跨协议时是误导——模型读成
    「我收到了图」。已知能力时直说，未知时才退回那句有条件的提醒。

    Args:
        attached: 本次结果是否真带上了图片块（编码失败降级为纯路径时为 False）。
        fmt: 编码格式（如 ``jpeg``），空则不附体积说明。
        max_size: 最长边像素上限，``0`` / ``None`` 表示原图。
        visible: 当前模型是否真能接收图片；``None`` 表示未确认（保留旧措辞）。

    @author aceFelix
    """
    line = "图片附上: 是" if attached else "图片附上: 否"
    if fmt:
        line += f"（{fmt}, 最长边<={max_size or '原图'}）"
    if not attached:
        return line
    if visible is True:
        # 模型确实有视觉：图会随下一次请求进上下文，直说，别让它再自我怀疑
        return line + "，你可以看到这张图：它随下一次请求进入你的上下文"
    if visible is False:
        # 无视觉模型：明确说「你没拿到画面」，否则它会拿「附上: 是」当成看得见
        return line + "，但当前模型无视觉输入：你只拿到路径，不要描述画面内容"
    return line + _IMAGE_SEE_CLAUSE


def vision_hint(ctx: Any) -> bool | None:
    """当前会话的模型是否真能接收图片；拿不到配置时返回 ``None``（未确认）。

    判据只有一处：``model_capability.resolve_model_type`` + ``model_has_vision``。
    这里只负责「从 ToolContext 摸到 settings」这一小步，并把 import 延迟到函数内，
    避免把 llm 层拖成 core/images 的模块级依赖。

    Args:
        ctx: ``ToolContext``（可缺 ``settings``，例如无头测试）。

    @author aceFelix
    """
    settings = getattr(ctx, "settings", None)
    if settings is None:
        return None
    try:
        from agent.llm.model_capability import model_has_vision, resolve_model_type

        return model_has_vision(resolve_model_type(settings))
    except Exception:  # noqa: BLE001 - 判据取不到就按未确认处理，绝不猜"有"
        return None


def paste_clipboard_image(ctx: ToolContext, ui: RichCLI) -> int:
    """Ctrl+V / /paste 显式添加剪贴板图片到待发送列表，返回新增张数。

    与 _pending_images 同一列表（ctx.extra["pending_images"]），随下一条
    用户消息发出；同一张图连按去重（按内容哈希比对已在列表中的图）。
    @author aceFelix
    """
    img = _load_image_from_clipboard()
    if img is None:
        ui.warn("剪贴板中没有图片（或缺少 Pillow）")
        return 0
    pending = _pending_images(ctx)
    h = _hash_image(img)
    if any(_hash_image(p) == h for p in pending):
        ui.info("剪贴板图片已在待发送列表中，未重复添加")
        return 0
    pending.append(img)
    ui.info(f"✅ 已添加剪贴板图片（待发送 {len(pending)} 张），随下一条消息发出")
    return 1
