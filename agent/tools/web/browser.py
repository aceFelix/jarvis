"""浏览器自动化工具 —— 让模型操作网页。

阶段二「电脑操作能力」的浏览器部分，基于 Playwright（async API）实现。
让模型能打开网页、截图看页面、点击元素、输入文字、读取页面内容。

核心设计:
1. **BrowserManager 单例**: 跨工具共享同一个 browser→context→page 链路。
   首次 Navigate 时懒启动，BrowserClose 或进程退出时清理。
2. **元素定位双模式**:
   - selector 模式: CSS / XPath / playwright 语义定位器（精确，需知道 DOM）
   - 坐标模式: 配合 BrowserScreenshot 截图，模型"看"到按钮在哪直接给坐标
3. **多模态视觉**: BrowserScreenshot 和 ScreenShot 一样，截图作为 ImageContent
   回传给支持视觉的 LLM，模型能真正"看"到网页。
4. **权限**: 只读操作（Screenshot/GetText/Close）自动放行；
   写操作（Navigate/Click/Type）一律 ASK（操作网页属不可逆副作用）。

依赖: pip install playwright && playwright install chromium
未安装时工具注册静默跳过，不影响其他工具。
"""

from __future__ import annotations

import asyncio
import atexit
import base64
import io
from typing import Any

from agent.core.context import ToolContext
from agent.core.message import ImageContent
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, PermissionMatcher, Tool


# ---------------------------------------------------------------------------
# 图片编码（与 screen.py 的 _encode_image 逻辑一致，独立内联避免跨模块耦合）
# ---------------------------------------------------------------------------


def _normalize_file_url(url: str) -> str:
    """规范化 file:// URL：Windows 反斜杠路径 → 正斜杠。

    Playwright 不认反斜杠，模型常拼出 file:///E:\\xx.html 形式，
    统一转正斜杠保证能打开。非 file:// URL 原样返回。
    """
    if url.startswith("file://"):
        return url.replace("\\", "/")
    return url


def _encode_image(img: Any, fmt: str = "jpeg", max_size: int = 1280) -> ImageContent:
    """把 PIL Image 缩放并编码为 base64 图片块。
    Args:
        img: PIL.Image.Image（playwright screenshot 返回 bytes，需先 PIL.open）。
        fmt: "jpeg" 或 "png"。
        max_size: 最长边像素上限，0 表示不缩放。
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


# ---------------------------------------------------------------------------
# BrowserManager —— 浏览器生命周期管理（模块级单例）
# ---------------------------------------------------------------------------


class _BrowserManager:
    """管理 playwright → browser → context → page 的生命周期。

    单例: 全局只有一个浏览器实例，所有浏览器工具共享同一个 page。
    懒启动: 第一次 get_page() 时才启动 playwright 和浏览器。
    清理: BrowserClose 工具显式调用，或进程退出时 atexit 兜底。
    """

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._lock = asyncio.Lock()
        self._headless = True
        self._closed = False

    async def get_page(self, *, headless: bool = True) -> Any:
        """获取当前 page。若未启动则懒启动浏览器。

        headless 参数仅在首次启动时生效（后续调用沿用已有实例）。
        """
        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page
            if self._closed:
                raise RuntimeError("浏览器已关闭，需重新启动（先 BrowserClose 再 Navigate）")

            self._headless = headless
            try:
                from playwright.async_api import async_playwright
            except ImportError as e:
                raise ImportError(
                    "playwright 未安装，请运行: pip install playwright && playwright install chromium"
                ) from e

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=headless)
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
            )
            self._page = await self._context.new_page()
            return self._page

    @property
    def is_active(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    @property
    def headless(self) -> bool:
        return self._headless

    async def close(self) -> None:
        """清理所有资源。幂等。"""
        async with self._lock:
            self._closed = True
            for closer in (
                self._page,
                self._context,
                self._browser,
                self._playwright,
            ):
                if closer is None:
                    continue
                try:
                    if hasattr(closer, "close"):
                        await closer.close()
                    elif hasattr(closer, "stop"):
                        await closer.stop()
                except Exception:
                    pass  # 清理阶段不抛
            self._page = self._context = self._browser = self._playwright = None
            # 允许重新启动
            self._closed = False


# 模块级单例
_manager = _BrowserManager()
atexit.register(lambda: asyncio.run(_manager.close()) if _manager.is_active else None)


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------


class BrowserNavigateTool(Tool):
    name = "BrowserNavigate"
    description = (
        "打开指定 URL 的网页。首次调用会启动浏览器（默认无头模式，不打扰用户）。"
        "打开后可用 BrowserScreenshot 看页面、BrowserClick/Type 交互。"
        "支持 file:// 打开本地 HTML 文件（静态页面自查，免确认）；"
        "http(s) 属网络操作，默认询问用户确认。"
        "可选参数 headless（默认 true 无头；设 false 可看到浏览器窗口，调试用）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "目标网址（含协议，如 https://example.com），"
                    "也支持 file:// 打开本地 HTML 文件（交付自查静态页面用）"
                ),
            },
            "headless": {
                "type": "boolean",
                "description": "是否无头模式（默认 true）。仅首次启动生效。",
            },
        },
        "required": ["url"],
    }
    max_result_chars = 1_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        # file:// 是本地只读查看，不算网络操作；http(s) 才算网络访问需确认。
        return str(args.get("url", "")).startswith("file://")

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        if str(args.get("url", "")).startswith("file://"):
            return PermissionResult.allow("本地文件自查")  # 本地文件自查免确认，降低自查摩擦
        return PermissionResult.ask(f"打开网页 {args.get('url')}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        url = args.get("url", "")
        if not url:
            return ValidationResult.fail("url 不能为空")
        # 支持 http(s) 与 file://（本地静态页面自查）三种协议前缀。
        # 裸 Windows 路径（E:\...）不自动转换，由提示词引导模型自行拼 file:// 前缀。
        if url.startswith(("http://", "https://")):
            return ValidationResult.pass_()
        if url.startswith("file://"):
            return ValidationResult.pass_()
        return ValidationResult.fail(
            f"url 必须以 http://、https:// 或 file:// 开头: {url}"
        )

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(tool_name="BrowserNavigate", targets=[args.get("url", "")])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = args["url"]
        headless = args.get("headless", True)

        # file:// 本地路径容错：Windows 反斜杠 → 正斜杠（Playwright 不认反斜杠）。
        # 不自动把裸路径转 file://（validate_input 已拦截），只做已有 file:// 的规范化。
        url = _normalize_file_url(url)

        try:
            page = await _manager.get_page(headless=headless)
        except ImportError as e:
            return ToolResult.error(str(e))
        except Exception as e:
            return ToolResult.error(f"启动浏览器失败: {type(e).__name__}: {e}")

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            return ToolResult.error(f"打开 {url} 失败: {type(e).__name__}: {e}")

        status = response.status if response else ("file://" if url.startswith("file://") else "?")
        title = await page.title()
        return ToolResult.ok(
            f"已打开: {url}\n"
            f"HTTP 状态: {status}\n"
            f"页面标题: {title}\n"
            f"模式: {'无头' if _manager.headless else '有头'}"
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return f"打开网页 {args.get('url')}" if args else None


class BrowserScreenshotTool(Tool):
    name = "BrowserScreenshot"
    description = (
        "截取当前浏览器页面的图。截图会作为图片直接回传给你（多模态），"
        "你能真正'看到'网页内容——按钮、文字、表单、布局一目了然。"
        "操作网页前务必先截图看清当前页面。只读，自动放行。"
        "可选参数 full_page（默认 false 只截视口，true 截整页）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "full_page": {
                "type": "boolean",
                "description": "是否截整个页面（含需滚动部分），默认 false 只截视口",
            },
            "format": {
                "type": "string",
                "enum": ["jpeg", "png"],
                "description": "图片格式，默认 jpeg 省 token",
            },
            "max_size": {
                "type": "integer",
                "description": "回传图片最长边像素（默认 1280，0=不缩放）",
            },
        },
    }
    max_result_chars = 800

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读操作")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not _manager.is_active:
            return ValidationResult.fail("浏览器未打开任何页面，请先 BrowserNavigate")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        page = await _manager.get_page()
        full_page = args.get("full_page", False)
        fmt = args.get("format", "jpeg")
        max_size = args.get("max_size", 1280)

        try:
            img_bytes = await page.screenshot(full_page=full_page, type="png")
        except Exception as e:
            return ToolResult.error(f"截图失败: {type(e).__name__}: {e}")

        try:
            from PIL import Image
        except ImportError as e:
            return ToolResult.error(f"Pillow 未安装: {e}")

        try:
            img = Image.open(io.BytesIO(img_bytes))
            image_block = _encode_image(img, fmt=fmt, max_size=max_size)
        except Exception as e:
            return ToolResult.error(f"图片编码失败: {type(e).__name__}: {e}")

        w, h = img.size
        scope = "整页" if full_page else "视口"
        return ToolResult.ok(
            f"已截取浏览器{scope}截图\n"
            f"原始尺寸: {w} x {h}\n"
            f"回传格式: {image_block.media_type}, 最长边<={max_size or '原图'}\n"
            f"图片已回传: 是",
            images=[image_block],
        )

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "浏览器截图" + ("（整页）" if args and args.get("full_page") else "（视口）")


class BrowserClickTool(Tool):
    name = "BrowserClick"
    description = (
        "点击网页上的元素。两种定位方式二选一:\n"
        "1. selector: CSS/XPath 定位器（如 '#submit-btn' '//button[text()=\"登录\"]'）\n"
        "2. x,y: 屏幕坐标（配合 BrowserScreenshot 看到按钮位置后直接给坐标）\n"
        "建议先用 BrowserScreenshot 看页面再决定点哪。默认询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS 或 XPath 定位器（XPath 以 // 开头）",
            },
            "x": {"type": "integer", "description": "点击的 x 坐标（页面像素）", "minimum": 0},
            "y": {"type": "integer", "description": "点击的 y 坐标（页面像素）", "minimum": 0},
            "button": {
                "type": "string",
                "enum": ["left", "right", "middle"],
                "description": "鼠标按键，默认 left",
            },
            "clicks": {
                "type": "integer",
                "description": "点击次数，1=单击(默认) 2=双击",
                "minimum": 1,
                "maximum": 3,
            },
        },
    }
    max_result_chars = 800

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        sel = args.get("selector")
        if sel:
            return PermissionResult.ask(f"点击元素 {sel}")
        return PermissionResult.ask(f"点击坐标 ({args.get('x')},{args.get('y')})")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not _manager.is_active:
            return ValidationResult.fail("浏览器未打开任何页面，请先 BrowserNavigate")
        has_selector = bool(args.get("selector"))
        has_xy = args.get("x") is not None and args.get("y") is not None
        if not has_selector and not has_xy:
            return ValidationResult.fail("必须提供 selector 或 (x,y) 之一")
        if has_selector and has_xy:
            return ValidationResult.fail("selector 和 (x,y) 只能选一个")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        sel = args.get("selector")
        target = sel if sel else f"{args.get('x')},{args.get('y')}"
        return PermissionMatcher(tool_name="BrowserClick", targets=[target])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        page = await _manager.get_page()
        selector = args.get("selector")
        button = args.get("button", "left")
        clicks = int(args.get("clicks", 1))

        try:
            if selector:
                # playwright 的 click 默认会等待元素可见可点击
                if selector.startswith("//"):
                    element = page.locator(selector).first
                else:
                    element = page.locator(selector).first
                await element.click(button=button, click_count=clicks, timeout=10000)
                desc = f"元素 {selector}"
            else:
                x = int(args["x"])
                y = int(args["y"])
                await page.mouse.click(x, y, button=button, click_count=clicks)
                desc = f"坐标 ({x},{y})"
        except Exception as e:
            return ToolResult.error(f"点击失败: {type(e).__name__}: {e}")

        return ToolResult.ok(f"已点击 {desc}（{button}键 {clicks}次）")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if not args:
            return None
        sel = args.get("selector")
        return f"点击 {sel}" if sel else f"点击 ({args.get('x')},{args.get('y')})"


class BrowserTypeTool(Tool):
    name = "BrowserType"
    description = (
        "在网页输入框中输入文字。用 selector 定位输入框，然后输入 text。"
        "默认会先清空输入框（clear=false 则不清空，追加输入）。"
        "建议先 BrowserScreenshot 看清表单布局。默认询问用户确认。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "输入框的 CSS 或 XPath 定位器",
            },
            "text": {"type": "string", "description": "要输入的文字"},
            "clear": {
                "type": "boolean",
                "description": "是否先清空输入框（默认 true）",
            },
            "press_enter": {
                "type": "boolean",
                "description": "输入后是否按回车（默认 false）",
            },
        },
        "required": ["selector", "text"],
    }
    max_result_chars = 800

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        text_preview = (args.get("text", "") or "")[:50]
        return PermissionResult.ask(f"在 {args.get('selector')} 输入: {text_preview}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not _manager.is_active:
            return ValidationResult.fail("浏览器未打开任何页面，请先 BrowserNavigate")
        if not args.get("selector"):
            return ValidationResult.fail("selector 不能为空")
        if not args.get("text"):
            return ValidationResult.fail("text 不能为空")
        return ValidationResult.pass_()

    def prepare_permission_matcher(self, args: dict[str, Any]) -> PermissionMatcher | None:
        return PermissionMatcher(tool_name="BrowserType", targets=[args.get("selector", "")])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        page = await _manager.get_page()
        selector = args["selector"]
        text = args["text"]
        clear = args.get("clear", True)
        press_enter = args.get("press_enter", False)

        try:
            element = page.locator(selector).first
            if clear:
                await element.fill("")
            await element.type(text, delay=30)
            if press_enter:
                await element.press("Enter")
        except Exception as e:
            return ToolResult.error(f"输入失败: {type(e).__name__}: {e}")

        action = "输入并回车" if press_enter else "输入"
        return ToolResult.ok(f"已在 {selector} {action}: {text[:80]}")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if not args:
            return None
        return f"在 {args.get('selector')} 输入文字"


class BrowserGetTextTool(Tool):
    name = "BrowserGetText"
    description = (
        "获取网页文本内容。不传 selector 返回整个页面的可见文本（可能很长，已自动截断）。"
        "传 selector 只返回该元素的文本。只读，自动放行。"
        "用于让模型了解页面内容（配合 BrowserScreenshot 用，截图看布局+文本读细节）。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "只取该元素的文本（CSS/XPath）。不填=整页可见文本",
            },
        },
    }
    max_result_chars = 8_000  # 页面文本可能较长，但仍需截断

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读操作")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        if not _manager.is_active:
            return ValidationResult.fail("浏览器未打开任何页面，请先 BrowserNavigate")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        page = await _manager.get_page()
        selector = args.get("selector")

        try:
            if selector:
                element = page.locator(selector).first
                text = await element.inner_text(timeout=10000)
                scope = f"元素 {selector}"
            else:
                text = await page.inner_text("body")
                scope = "整页"
        except Exception as e:
            return ToolResult.error(f"获取文本失败: {type(e).__name__}: {e}")

        # 截断超长文本（orchestrator 也会截，但这里先截防止传太大）
        char_count = len(text)
        if char_count > self.max_result_chars:
            preview = self.max_result_chars // 2
            text = (
                text[:preview]
                + f"\n\n... [文本超长，已截断。完整 {char_count} 字符] ...\n\n"
                + text[-preview:]
            )

        return ToolResult.ok(f"=== {scope}文本（{char_count} 字符）===\n{text}")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "读取网页文本" + (f"({args.get('selector')})" if args and args.get("selector") else "（整页）")


class BrowserCloseTool(Tool):
    name = "BrowserClose"
    description = (
        "关闭浏览器，释放资源。使用完浏览器后建议调用。"
        "关闭后需重新 BrowserNavigate 才能再次使用。只读（资源清理），自动放行。"
    )
    input_schema: JSONSchema = {"type": "object", "properties": {}}
    max_result_chars = 500

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True  # 资源清理，非业务写操作

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False  # 会影响其他浏览器工具

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("资源清理")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not _manager.is_active:
            return ToolResult.ok("浏览器未运行，无需关闭")
        await _manager.close()
        return ToolResult.ok("浏览器已关闭，资源已释放")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "关闭浏览器"
