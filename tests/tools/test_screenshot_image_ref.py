"""``ScreenShot`` 的图片窗口接线测试（不依赖真实屏幕 / 不弹窗）。

为什么需要它：2026-11-11 的 ``ToolResult.ok(trace_meta=...)`` 漏改事故里，全仓没有
一条用例真的调用过 ``call()``，于是「加一行 trace_meta」把三个图片工具全打挂而 CI 全绿。
这里用假 pyautogui 把 ``call()`` 真正跑到尾，钉住三件事：

1. ``ToolResult`` 能带 ``trace_meta["image"]`` 出来（走 ok 工厂，不是直接构造）；
2. ``ImageRef`` 的尺寸取**原图**（不是回传给模型的缩放后尺寸）；
3. 回执文案仍带「附上不等于看见」，不许改成「已为你显示」。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent.tools.system import screen as screen_mod


class _FakePilScreenshot:
    """替身：返回一张固定尺寸的真 PIL 图（让编码与落盘都真跑）。"""

    size = (1600, 900)

    def __init__(self) -> None:
        pass


def _fake_pyautogui():
    from PIL import Image

    class _Fake:
        @staticmethod
        def screenshot(region=None):
            return Image.new("RGB", (1600, 900), (30, 60, 90))

    return _Fake()


def _call_shot(monkeypatch, tmp_path: Path, args: dict | None = None):
    monkeypatch.setattr(screen_mod, "_import_pyautogui", _fake_pyautogui)
    monkeypatch.setattr(screen_mod, "_shots_dir", lambda: tmp_path)
    tool = screen_mod.ScreenShotTool()
    return asyncio.run(tool.call(args or {"format": "jpeg", "max_size": 640}, None))


class TestScreenshotTraceMeta:
    def test_ok_factory_carries_image_ref(self, monkeypatch, tmp_path: Path) -> None:
        """走 ``ToolResult.ok(..., trace_meta=)``：这条路径就是事故路径。"""
        result = _call_shot(monkeypatch, tmp_path)
        assert result.is_error is False
        assert len(result.images) == 1
        image = result.trace_meta["image"]
        assert image["path"].endswith(".png")
        # 尺寸必须取原图（1600×900），不能是回传给模型的 640 最长边
        assert image["width"] == 1600
        assert image["height"] == 900
        assert image["source"] == "ScreenShot"
        assert image["media_type"] == "image/jpeg"
        assert image["bytes"] > 0

    def test_trace_meta_carries_no_base64(self, monkeypatch, tmp_path: Path) -> None:
        """事件只带路径；base64 只该留在 ``images`` 里。"""
        result = _call_shot(monkeypatch, tmp_path)
        assert "data" not in result.trace_meta["image"]

    def test_receipt_keeps_see_clause(self, monkeypatch, tmp_path: Path) -> None:
        """文案边界：附上 ≠ 看见（模型侧能不能看由 provider 决定）。"""
        result = _call_shot(monkeypatch, tmp_path)
        assert "附上不等于看见" in str(result.data)
        assert "已为你显示" not in str(result.data)

    def test_file_is_really_written(self, monkeypatch, tmp_path: Path) -> None:
        result = _call_shot(monkeypatch, tmp_path)
        path = Path(result.trace_meta["image"]["path"])
        assert path.is_file()
        assert path.parent == tmp_path
