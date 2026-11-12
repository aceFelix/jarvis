"""图片驾驶舱（P0）：把「工具产出的图」显示给用户看。

## 为什么要这个窗口

终端只有图片**入站**通道：``ScreenShot`` / ``CameraShot`` / ``BrowserScreenshot``
把图编码成 base64 送给模型，人却看不见。于是模型说「我看了截图，按钮在右下角」
时，用户无法核对它看的是不是同一张图。

本模块只做「给人看」那一半：消费 trace 的 ``image_show`` 事件（只带路径与元信息），
按路径读盘显示。**它不改变送给模型的图片块，也不参与任何 provider 序列化**。

## 形态与约束

- 480×600 无边框 Tk 窗，默认停靠屏幕左上角，整面可拖、位置记在
  ``ui_state.json`` 的 ``image`` 段（与 HUD 的 ``cockpit`` 段并列）；
- 生命周期照抄 ``CrawlManager``：有图才弹、结束保留、✕ 只隐藏、下轮自动重现、
  主 HUD 隐藏时同步隐藏、终端前台时让位置顶；
- **所有 Tk 调用只在 Tk 线程**（本模块的每个方法都由 ``CockpitWindow._pump`` 调用）；
- ``PhotoImage`` 必须被实例持有（缓存在 ``_photos`` 里），否则被 GC 回收后窗口全白。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.diag import diag_info
from agent.ui import image_cache as geo
from agent.ui.cockpit import VisualOptions
from agent.ui.cockpit_position import load_image_position, save_image_position
from agent.ui.hud_theme import (
    THEME_IMAGE as _C,
    draw_background,
    draw_frame,
    glow_text,
    FONT_CARD_TITLE,
    FONT_LABEL,
    FONT_MONO,
    FONT_SMALL,
    FONT_TEXT,
    FONT_TITLE,
    clip,
)

__all__ = ["ImageManager", "ImageCockpit"]


class ImageCockpit:
    """图片驾驶舱（只在 Tk 线程使用）。

    形态：480×600、无边框、置顶、整面可拖；内容：当前大图 + 缩略条 + 元信息。

    @author aceFelix
    """

    def __init__(
        self,
        root: Any,
        options: VisualOptions,
        on_close: Any,
        *,
        topmost: bool = True,
    ) -> None:
        import tkinter as tk

        self._options = options
        self._on_close = on_close
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", bool(topmost))
        self.canvas = tk.Canvas(
            self.top,
            width=geo.WINDOW_W,
            height=geo.WINDOW_H,
            bg=_C.bg,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self._items: list[dict[str, Any]] = []
        self._index = 0
        # PhotoImage 必须被实例持有：Tk 只留一份弱引用，GC 后窗口变白（最难查的一类）
        self._photos: dict[str, Any] = {}
        self._current_keys: set[str] = set()
        self._press_xy: tuple[int, int] | None = None
        self._drag_offset = (0, 0)
        self._did_drag = False
        self._thumb_hits: list[tuple[float, float, float, float, int]] = []

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.tag_bind("img_close", "<Button-1>", lambda _e: self._on_close())
        self.top.bind("<Escape>", lambda _e: self._on_close())
        self._restore_position()

    # ---- 窗口生命周期 ----

    def _restore_position(self) -> None:
        """恢复位置：默认左上角 (24,24)，有记忆则钳制在屏幕内。"""
        try:
            screen_w = int(self.top.winfo_screenwidth())
            screen_h = int(self.top.winfo_screenheight())
        except Exception:
            screen_w, screen_h = 1920, 1080
        saved = load_image_position() if self._options.position_memory else None
        x, y = geo.window_origin(screen_w, screen_h, saved)
        self.geometry(x, y)

    def geometry(self, x: int, y: int) -> None:
        """移动到屏幕坐标（无标题栏）。"""
        try:
            if self.top is not None:
                self.top.geometry(f"{geo.WINDOW_W}x{geo.WINDOW_H}+{int(x)}+{int(y)}")
        except Exception:
            pass

    def remember_position(self) -> None:
        """保存当前位置（只在 Tk 线程调用）；记忆关闭/失败时静默。"""
        if not self._options.position_memory or self.top is None:
            return
        try:
            save_image_position(self.top.winfo_x(), self.top.winfo_y())
        except Exception:
            pass

    def show(self) -> None:
        """显示/提到最前。"""
        try:
            if self.top is not None:
                self.top.deiconify()
                self.top.lift()
        except Exception:
            pass

    def hide(self) -> None:
        """隐藏但不销毁。"""
        try:
            if self.top is not None:
                self.top.withdraw()
        except Exception:
            pass

    def set_topmost(self, enabled: bool) -> None:
        """切换置顶：终端（Git Bash/cmd）在前台时让位给终端。"""
        try:
            if self.top is not None:
                self.top.attributes("-topmost", bool(enabled))
        except Exception:
            pass

    def reset_interaction(self) -> None:
        """新一轮重置选中与缓存（图片清单本身就随 state 换轮）。"""
        self._index = 0
        self._photos.clear()
        self._current_keys = set()

    def destroy(self) -> None:
        """销毁窗口并断开 Tk 引用（必须在 Tk 线程调用）。

        先置空引用再 destroy：主线程 GC 触碰已释放的 Tcl 解释器会触发
        ``Tcl_AsyncDelete``（与工具卡/代理卡/抓取窗同一约束）。
        """
        top, canvas = self.top, self.canvas
        self.top = None
        self.canvas = None
        self._photos.clear()
        try:
            if top is not None:
                top.destroy()
        except Exception:
            pass
        del canvas

    # ---- 渲染 ----

    def render(self, items: list[dict[str, Any]]) -> None:
        """按图片清单重绘（只保留最近 ``image_max`` 张）。

        Args:
            items: ``CockpitState.images`` 的快照（旧→新，元素是含 path 的字典）。
        """
        try:
            limit = max(1, int(self._options.image_max))
            self._items = list(items)[-limit:]
            self._index = geo.clamp_index(self._index, len(self._items))
            self._draw()
        except Exception as exc:  # noqa: BLE001 - 窗口故障不能影响主流程
            diag_info("ui", f"图片驾驶舱渲染失败: {exc}")

    def _draw(self) -> None:
        """全量重绘（元素少、事件驱动，与主 HUD 同一策略）。"""
        self.canvas.delete("all")
        self._current_keys = set()
        self._thumb_hits = []
        self._draw_grid()
        self._draw_frame()
        self._draw_header()
        self._draw_main()
        self._draw_strip()
        self._draw_meta()
        self._draw_footer()

    def _draw_grid(self) -> None:
        """背景材质：深渊黑 + 扫描线 + 网格 + 颗粒 + 侧刻度（统一在 hud_theme）。"""
        draw_background(self.canvas, _C, geo.WINDOW_W, geo.WINDOW_H)

    def _draw_frame(self) -> None:
        """外框 + 四角 HUD 括号（统一在 hud_theme）。"""
        draw_frame(self.canvas, _C, geo.WINDOW_W, geo.WINDOW_H)

    def _draw_header(self) -> None:
        """标题、副标题与关闭按钮。"""
        glow_text(self.canvas, geo.PAD, 25, "J.A.R.V.I.S", theme=_C, font=FONT_TITLE)
        self._text(geo.PAD, 46, "IMAGE COCKPIT // 图片驾驶舱", fill=_C.dim, font=FONT_SMALL)
        # 文案边界：本舱只给「人看」，不改变模型侧回执，别让用户以为模型也看到了
        self._text(
            geo.WINDOW_W - geo.PAD,
            46,
            "不代表模型已看见",
            fill=_C.accent_dim,
            font=FONT_SMALL,
            anchor="e",
        )
        self.canvas.create_oval(
            geo.WINDOW_W - 40, 12, geo.WINDOW_W - 14, 38,
            outline=_C.rule, width=1, tags=("img_close",),
        )
        self._text(
            geo.WINDOW_W - 27, 25, "✕", fill=_C.dim,
            font=("Microsoft YaHei UI", 10), anchor="center", tags=("img_close",),
        )
        self.canvas.create_line(
            geo.PAD, 60, geo.WINDOW_W - geo.PAD, 60, fill=_C.rule
        )

    def _current(self) -> dict[str, Any] | None:
        """当前选中的图片条目；无图时 None。"""
        if not self._items:
            return None
        return self._items[self._index]

    def _draw_main(self) -> None:
        """大图区：按窗宽适配；文件已清理时给占位，不弹错。"""
        left, top = geo.PAD, geo.MAIN_TOP
        right, bottom = geo.WINDOW_W - geo.PAD, geo.MAIN_BOTTOM
        self.canvas.create_rectangle(left, top, right, bottom, fill=_C.bg_panel, outline=_C.rule)
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2

        ref = self._current()
        if ref is None:
            self._text(center_x, center_y, "— 等待第一张图", fill=_C.dim, font=FONT_SMALL, anchor="center")
            return

        path = str(ref.get("path") or "")
        if not geo.file_alive(path):
            self._text(center_x, center_y - 10, "‹图已过期›", fill=_C.warn, font=FONT_CARD_TITLE, anchor="center")
            self._text(center_x, center_y + 12, "原图已被清理，元信息仍保留", fill=_C.dim, font=FONT_SMALL, anchor="center")
            return

        size = geo.main_size(ref.get("width"), ref.get("height"))
        photo = self._photo(path, size, kind="main")
        if photo is None:
            self._text(center_x, center_y, "‹无法读取图片›", fill=_C.fail, font=FONT_SMALL, anchor="center")
            return
        self.canvas.create_image(center_x, center_y, image=photo, anchor="center")

    def _draw_strip(self) -> None:
        """缩略条：单行横向，点击切换；超出窗口宽度的不画（不引入滚动状态）。"""
        top = geo.STRIP_TOP
        self.canvas.create_rectangle(
            geo.PAD, top, geo.WINDOW_W - geo.PAD, top + geo.STRIP_HEIGHT,
            fill=_C.bg_panel, outline=_C.rule,
        )
        if not self._items:
            self._text(geo.PAD + 10, top + geo.STRIP_HEIGHT / 2, "— 暂无图片", fill=_C.dim, font=FONT_SMALL)
            return

        limit = max(16, int(self._options.image_thumb_px))
        for index, x in enumerate(geo.strip_positions(len(self._items), limit, left=geo.PAD + 6)):
            ref = self._items[index]
            tw, th = geo.thumb_size(ref.get("width"), ref.get("height"), limit)
            if tw <= 0 or th <= 0:
                tw = th = limit
            if x + tw > geo.WINDOW_W - geo.PAD - 6:
                break
            y = top + (geo.STRIP_HEIGHT - th) / 2
            path = str(ref.get("path") or "")
            photo = self._photo(path, (tw, th), kind="thumb") if geo.file_alive(path) else None
            if photo is not None:
                self.canvas.create_image(x, y, image=photo, anchor="nw")
            else:
                self.canvas.create_rectangle(x, y, x + tw, y + th, outline=_C.rule)
                self._text(
                    x + tw / 2, y + th / 2, "✕" if not geo.file_alive(path) else "?",
                    fill=_C.dim, font=FONT_SMALL, anchor="center",
                )
            if index == self._index:
                self.canvas.create_rectangle(
                    x - 2, y - 2, x + tw + 2, y + th + 2, outline=_C.accent, width=2
                )
            self._thumb_hits.append((x, x + tw, top, top + geo.STRIP_HEIGHT, index))

    def _draw_meta(self) -> None:
        """元信息：来源/尺寸/格式/体积、时间、路径（过长分两行）。"""
        y = geo.META_TOP
        ref = self._current()
        if ref is None:
            self._text(geo.PAD, y, "— 元信息", fill=_C.dim, font=FONT_SMALL)
            return
        head, stamp, path = geo.describe_meta(ref)
        self._text(geo.PAD, y, head, fill=_C.text, font=FONT_TEXT)
        if stamp:
            self._text(geo.PAD, y + 18, stamp, fill=_C.dim, font=FONT_MONO)
        shown = clip(path, 62)
        self._text(geo.PAD, y + 36, shown, fill=_C.dim, font=FONT_MONO)
        if shown != path and len(path) > 8:
            self._text(geo.PAD, y + 52, "…" + path[-56:], fill=_C.dim, font=FONT_MONO)

    def _draw_footer(self) -> None:
        """底部操作提示与序号。"""
        self.canvas.create_line(
            geo.PAD, geo.WINDOW_H - 36, geo.WINDOW_W - geo.PAD, geo.WINDOW_H - 36,
            fill=_C.accent_dim,
        )
        self._text(
            geo.PAD, geo.WINDOW_H - 20,
            "点击缩略图切换 · 滚轮翻页 · Esc 隐藏", fill=_C.dim, font=FONT_SMALL,
        )
        if self._items:
            self._text(
                geo.WINDOW_W - geo.PAD, geo.WINDOW_H - 20,
                f"{self._index + 1}/{len(self._items)}",
                fill=_C.accent_dim, font=FONT_LABEL, anchor="e",
            )
        else:
            self._text(
                geo.WINDOW_W - geo.PAD, geo.WINDOW_H - 20, "IMAGE",
                fill=_C.accent_dim, font=FONT_SMALL, anchor="e",
            )

    # ---- 缩略图缓存 ----

    def _photo(self, path: str, size: tuple[int, int], *, kind: str) -> Any:
        """按需解码并缓存 ``PhotoImage``；失败返回 ``None``（不抛）。

        缓存键带 mtime：固定路径反复截图（``screenshot_<ts>.png`` 之外的场景）
        被覆盖后键自动失效，不会一直显示旧图。
        """
        width, height = size
        if width <= 0 or height <= 0 or not path:
            return None
        mtime = geo.file_mtime_ns(path)
        if kind == "main":
            key = f"main|{path}|{mtime}|{width}x{height}"
        else:
            key = geo.cache_key(path, mtime, width)
        self._current_keys.add(key)
        cached = self._photos.get(key)
        if cached is not None:
            return cached
        try:
            from PIL import Image, ImageTk

            img = Image.open(path)
            try:
                # JPEG 解码降采样：2560×1440 直接缩到 88px，全解再缩会慢一个数量级
                img.draft("RGB", (max(1, width * 2), max(1, height * 2)))
            except Exception:
                pass
            img.load()
            img.thumbnail((width, height))
            photo = ImageTk.PhotoImage(img)
        except Exception as exc:  # noqa: BLE001 - 单张图坏了只影响这一张
            diag_info("ui", f"图片解码失败: {path} ({exc})")
            return None
        self._photos[key] = photo
        self._trim_photos()
        return photo

    def _trim_photos(self) -> None:
        """缓存超限时丢弃最旧的、且当前不在画面上的条目。"""
        while len(self._photos) > geo.THUMB_CACHE_MAX:
            for key in list(self._photos):
                if key not in self._current_keys:
                    self._photos.pop(key, None)
                    break
            else:
                return

    # ---- 交互 ----

    def _on_press(self, event: Any) -> None:
        """记录拖动起点（整面可拖）。"""
        self._press_xy = (event.x, event.y)
        self._did_drag = False
        try:
            self._drag_offset = (
                event.x_root - self.top.winfo_x(),
                event.y_root - self.top.winfo_y(),
            )
        except Exception:
            self._drag_offset = (0, 0)

    def _on_drag(self, event: Any) -> None:
        """超过阈值算拖动，移动窗口。"""
        if self._press_xy is not None:
            if (
                abs(event.x - self._press_xy[0]) > geo.DRAG_THRESHOLD
                or abs(event.y - self._press_xy[1]) > geo.DRAG_THRESHOLD
            ):
                self._did_drag = True
        try:
            self.top.geometry(
                f"+{int(event.x_root - self._drag_offset[0])}"
                f"+{int(event.y_root - self._drag_offset[1])}"
            )
        except Exception:
            pass

    def _on_release(self, event: Any) -> None:
        """未拖动则按缩略图命中切换；拖动则记住位置。"""
        if self._did_drag:
            self._did_drag = False
            self.remember_position()
            return
        for x0, x1, y0, y1, index in self._thumb_hits:
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                if index != self._index:
                    self._index = index
                    self._draw()
                return

    def _on_wheel(self, event: Any) -> None:
        """滚轮翻页（向上=前一张）。"""
        if len(self._items) <= 1:
            return
        try:
            delta = int(getattr(event, "delta", 0))
        except (TypeError, ValueError):
            delta = 0
        step = -1 if delta > 0 else 1
        self._index = geo.clamp_index(self._index + step, len(self._items))
        self._draw()

    def _text(
        self,
        x: float,
        y: float,
        content: Any,
        *,
        fill: str = _C.text,
        font: Any = FONT_TEXT,
        anchor: str = "w",
        tags: tuple[str, ...] = (),
    ) -> int:
        """在 Canvas 上放一行文字。"""
        return self.canvas.create_text(
            x, y, text=str(content), fill=fill, font=font, anchor=anchor, tags=tags
        )


class ImageManager:
    """图片驾驶舱的显示策略与生命周期（只在 Tk 线程使用）。

    策略与 ``CrawlManager`` 完全同构（2026-11-11）：
    - 本轮出现第一张工具产出的图才创建并弹出；
    - 任务结束保留（不隐藏）；
    - 用户点 ✕ 只隐藏，本轮不再自动弹；下一轮（run_id 变化）有图再出现；
    - 主 HUD 关闭时一并隐藏，HUD 重现时同步显示；
    - 终端在前台时让出置顶。

    @author aceFelix
    """

    def __init__(self, root: Any, options: VisualOptions) -> None:
        self.root = root
        self.options = options
        self._window: ImageCockpit | None = None
        self._disabled = False
        self._hidden = False
        self._hidden_run_id: str | None = None
        self._run_id = ""
        self._rendered_run_id: str | None = None
        self._topmost_allowed = True
        self._last_items: list[dict[str, Any]] = []

    @staticmethod
    def has_data(state: Any) -> bool:
        """是否已有可展示的图片（决定要不要弹窗）。"""
        return bool(getattr(state, "images", None))

    def sync(self, state: Any, run_id: str) -> None:
        """有新图时创建/刷新窗口；本轮用户关过则不再自动弹。"""
        if self._disabled or not self.options.image_on():
            return
        if not self.has_data(state):
            return
        self._run_id = run_id
        self._last_items = [dict(item) for item in state.images]
        if self._hidden and self._hidden_run_id == run_id:
            return
        self._hidden = False
        window = self._ensure_window()
        if window is None:
            return
        if run_id != self._rendered_run_id:
            window.reset_interaction()
            self._rendered_run_id = run_id
        window.render(self._last_items)
        window.show()

    def hide_all(self) -> None:
        """主 HUD 关闭时隐藏（不销毁、不影响下轮重现）。"""
        if self._window is not None:
            self._window.hide()

    def show_all(self, state: Any) -> None:
        """主 HUD 重现时同步显示（有图才显示）。"""
        if self._disabled or not self.options.image_on():
            return
        if self._window is None or not self.has_data(state):
            return
        self._last_items = [dict(item) for item in state.images]
        self._window.render(self._last_items)
        self._window.show()

    def set_topmost(self, enabled: bool) -> None:
        """切换置顶并记住状态：终端在前台时新弹的图片驾驶舱也应让位。"""
        self._topmost_allowed = bool(enabled)
        if self._window is not None:
            self._window.set_topmost(enabled)

    def detach(self) -> None:
        """销毁窗口并断开 Tk 根引用（退出时防跨线程 GC 崩溃）。"""
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self.root = None

    # ---- 内部 ----

    def _ensure_window(self) -> ImageCockpit | None:
        """懒创建窗口；失败置 disabled，不影响其它可视化。"""
        if self._window is not None:
            return self._window
        try:
            self._window = ImageCockpit(
                self.root, self.options, self._on_close, topmost=self._topmost_allowed
            )
            return self._window
        except Exception as exc:  # noqa: BLE001 - 图片驾驶舱故障不能影响主流程
            diag_info("ui", f"图片驾驶舱创建失败，已禁用: {exc}")
            self._disabled = True
            self._window = None
            return None

    def _on_close(self) -> None:
        """用户点 ✕ / 按 Esc：只隐藏；记录轮次避免本轮再次弹出。"""
        self._hidden = True
        self._hidden_run_id = self._run_id or None
        if self._window is not None:
            self._window.hide()
