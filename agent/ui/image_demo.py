"""图片窗本地预览脚本（开发期用，不参与运行时）。

用途：不跑一轮真实对话、不需要模型与 pyautogui，直接看图片窗长什么样。

    python -m agent.ui.image_demo

它会生成三张不同尺寸的测试图（模拟截图 / 拍照 / 网页截图），只开图片窗
（主 HUD 与其它子窗都关掉），Ctrl+C 退出。

@author aceFelix
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from agent.ui.cockpit import VisualOptions
from agent.ui.cockpit_window import CockpitWindow

#: (文件名, 尺寸, 颜色, 来源) —— 尺寸刻意各不相同，方便看等比适配
_SPECS = (
    ("shot_wide.png", (2560, 1440), (12, 46, 78), "ScreenShot"),
    ("shot_small.png", (1280, 720), (78, 24, 32), "CameraShot"),
    ("shot_tall.png", (900, 1600), (20, 76, 54), "BrowserScreenshot"),
)


def _build_items(root: Path) -> list[dict]:
    """生成测试图并装成 ``image_show`` 事件体（只含路径与元信息）。"""
    from PIL import Image

    items: list[dict] = []
    for name, size, color, source in _SPECS:
        path = root / name
        Image.new("RGB", size, color).save(path, format="PNG")
        items.append(
            {
                "path": str(path),
                "source": source,
                "width": size[0],
                "height": size[1],
                "media_type": "image/png",
                "bytes": path.stat().st_size,
                "ts": "2026-11-11T15:00:00+08:00",
            }
        )
    return items


def main() -> None:
    """起一个只含图片窗的驾驶舱，把三张测试图投进去，直到 Ctrl+C。"""
    options = VisualOptions.from_values(
        task_cockpit="off",
        tool_cards="off",
        agent_cards="off",
        crawl_window="off",
        image_window="auto",
    )
    window = CockpitWindow(options=options)
    window.feed({"type": "run_start", "run_id": "demo"})
    for item in _build_items(Path(tempfile.mkdtemp(prefix="jarvis-image-demo-"))):
        window.feed({"type": "image_show", "run_id": "demo", **item})

    print("图片窗已弹出（默认屏幕左上角）。点击缩略图切换、滚轮翻页、Esc 隐藏；Ctrl+C 退出。")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n退出。")
    finally:
        window.shutdown()


if __name__ == "__main__":
    main()
