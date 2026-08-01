"""系统托盘图标（基于 pystray + PIL）。

在独立线程运行，提供右键菜单: 语音对话 / 实时聊天 / 文本对话 / 退出。
三个交互模式（语音/实时/文本）为互斥关系，选中一个时自动关闭其余两个。
菜单项点击通过回调通知主进程。

从原 daemon.py 拆分而来。

@author aceFelix
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from agent.daemon.platform_utils import _has_pystray


class TrayIcon:
    """系统托盘图标。"""

    def __init__(
        self,
        on_voice: Callable[[], None],
        on_text: Callable[[], None],
        on_quit: Callable[[], None],
        voice_active_getter: Callable[[], bool],
        voice_toggle: Callable[[], None],
        realtime_enabled_getter: Callable[[], bool],
        realtime_toggle: Callable[[], None],
    ) -> None:
        self._on_voice = on_voice
        self._on_text = on_text
        self._on_quit = on_quit
        self._voice_active_getter = voice_active_getter
        self._voice_toggle = voice_toggle
        self._realtime_enabled_getter = realtime_enabled_getter
        self._realtime_toggle = realtime_toggle
        self._icon: Any = None
        self._thread: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return _has_pystray()

    def _make_image(self):
        """生成一个简单的蓝色圆形托盘图标。"""
        from PIL import Image, ImageDraw
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 外环
        draw.ellipse([4, 4, size - 4, size - 4], outline=(43, 143, 224), width=3)
        # 内环
        draw.ellipse([16, 16, size - 16, size - 16], outline=(91, 200, 255), width=2)
        # 核心
        draw.ellipse([24, 24, size - 24, size - 24], fill=(191, 232, 255))
        return img

    def start(self, *, log_func: Callable[..., None] | None = None) -> bool:
        """启动托盘图标。成功返回 True。

        Args:
            log_func: 可选的日志回调，用于把启动异常写入 daemon.log。
        """
        if not self.available:
            return False
        try:
            import pystray
            from PIL import Image  # noqa: F401

            image = self._make_image()
            menu = pystray.Menu(
                pystray.MenuItem(
                    lambda item: "语音对话" if self._voice_active_getter() else "语音对话：已关闭",
                    self._handle_voice,
                    checked=lambda item: self._voice_active_getter(),
                    radio=True,
                    default=True,
                ),
                pystray.MenuItem(
                    lambda item: "实时聊天" if self._realtime_enabled_getter() else "实时聊天：已关闭",
                    self._handle_realtime_talk,
                    checked=lambda item: self._realtime_enabled_getter(),
                    radio=True,
                ),
                pystray.MenuItem(
                    "文本对话",
                    self._handle_text,
                    checked=lambda item: False,
                    radio=True,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出贾维斯", self._handle_quit),
            )
            self._icon = pystray.Icon("jarvis", image, "J.A.R.V.I.S", menu)
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            if log_func:
                log_func("托盘图标启动失败: %s: %s", type(e).__name__, e)
            return False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def notify(self, title: str, message: str) -> None:
        """弹出系统通知（如果托盘已启动）。"""
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def update_menu(self) -> None:
        """刷新托盘菜单显示（动态文本/勾选状态）。"""
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def _handle_voice(self, item=None) -> None:
        """处理托盘「语音对话」项点击。

        三个模式互斥：开启语音对话前会先关闭实时聊天。
        如果语音对话已经在运行，则关闭它。
        """
        try:
            self._voice_toggle()
            self.update_menu()
            if self._voice_active_getter():
                self.notify("J.A.R.V.I.S", "语音对话已开启")
            else:
                self.notify("J.A.R.V.I.S", "语音对话已关闭")
        except Exception as e:
            try:
                self.notify("J.A.R.V.I.S", f"语音对话操作失败: {e}")
            except Exception:
                pass

    def _handle_realtime_talk(self, item=None) -> None:
        """处理托盘「实时聊天」项点击。

        三个模式互斥：开启实时聊天前会先关闭语音对话。
        如果实时聊天已经在运行，则关闭它。

        @author aceFelix
        """
        try:
            self._realtime_toggle()
            self.update_menu()
            if self._realtime_enabled_getter():
                self.notify("J.A.R.V.I.S", "实时聊天已开启")
            else:
                self.notify("J.A.R.V.I.S", "实时聊天已关闭")
        except Exception as e:
            try:
                self.notify("J.A.R.V.I.S", f"实时聊天操作失败: {e}")
            except Exception:
                pass

    def _handle_text(self, *_args) -> None:
        """处理托盘「文本对话」项点击。

        文本对话为一次性动作：先关闭语音对话和实时聊天，再弹出文本终端。
        """
        try:
            self._on_text()
        except Exception as e:
            # 不再静默吞异常: 出错时托盘通知用户
            try:
                self.notify("J.A.R.V.I.S", f"文本对话失败: {e}")
            except Exception:
                pass

    def _handle_quit(self, *_args) -> None:
        try:
            self._on_quit()
        except Exception:
            pass
