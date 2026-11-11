"""三舱视觉预览：一次弹出任务 / 爬虫 / 图片三个驾驶舱。

    python -m agent.ui.hud_preview

不跑模型、不需要 API Key、不需要 pyautogui。用来评审三套主色与共享材质：

- 任务驾驶舱（右）：电光青 ``#00d4ff``
- 爬虫驾驶舱（左）：品红电流 ``#ff2d78``
- 图片驾驶舱（左上）：荧光绿 ``#39ff88``

三舱共用同一份背景材质（深渊黑 + 扫描线 + 网格 + 颗粒 + 侧刻度）与四角 HUD 括号，
区别只在主色——这正是设计哲学里"三色纪律 + 同一件工艺"的落地检验。

@author aceFelix
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from agent.ui.cockpit import VisualOptions
from agent.ui.cockpit_window import CockpitWindow

_RUN = "preview"
_TS = "2026-11-12T00:40:00+08:00"


def _build_images(root: Path) -> list[dict]:
    """造两张测试图并装成 ``image_show`` 事件体。"""
    from PIL import Image

    items: list[dict] = []
    for name, size, color, source in (
        ("shot_wide.png", (1600, 900), (12, 46, 78), "ScreenShot"),
        ("shot_small.png", (900, 600), (78, 24, 32), "CameraShot"),
    ):
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
                "ts": _TS,
            }
        )
    return items


def main() -> None:
    """三舱全开，投一轮假事件，保持到 Ctrl+C。"""
    options = VisualOptions.from_values(
        task_cockpit="window",
        tool_cards="off",      # 浮窗卡会遮住主 HUD 的下半部分，预览时关掉
        agent_cards="off",
        crawl_window="auto",
        image_window="auto",
    )
    window = CockpitWindow(options=options)
    window.feed({"type": "run_start", "run_id": _RUN})

    steps = (
        ("WebFetch", "https://example.com/zh/news", True, 237, 2048),
        ("ScreenShot", "D:/tmp/jarvis-shots/preview.png", True, 184, 0),
        ("WebFetch", "https://example.com/zh/projects", True, 380, 4096),
    )
    for index, (tool, target, ok, duration, size) in enumerate(steps):
        tool_use_id = f"preview-{index}"
        window.feed(
            {
                "type": "tool_start",
                "run_id": _RUN,
                "tool": tool,
                "tool_use_id": tool_use_id,
                "target": {"kind": "file", "path": target},
                "input": {"url": target} if tool == "WebFetch" else {},
            }
        )
        window.feed(
            {
                "type": "tool_end",
                "run_id": _RUN,
                "tool": tool,
                "tool_use_id": tool_use_id,
                "ok": ok,
                "duration_ms": duration,
            }
        )
        if tool == "WebFetch":
            window.feed(
                {
                    "type": "web_fetch",
                    "run_id": _RUN,
                    "url": target,
                    "status": "ok" if ok else "error",
                    "duration_ms": duration,
                    "bytes": size,
                    "depth": index // 2,
                    "parent_url": "https://example.com" if index else None,
                }
            )

    images = _build_images(Path(tempfile.mkdtemp(prefix="jarvis-hud-preview-")))
    for image in images:
        window.feed({"type": "image_show", "run_id": _RUN, **image})

    window.feed(
        {
            "type": "crawl_stats",
            "run_id": _RUN,
            "queued": 3,
            "done": 3,
            "failed": 0,
            "dedup_hits": 1,
            "bytes": 6144,
            "elapsed_ms": 900,
            "max_pages": 10,
            "max_depth": 2,
        }
    )
    window.feed({"type": "run_end", "run_id": _RUN, "steps": 3, "failed": 0,
                 "files_changed": 0, "duration_ms": 900})

    print(
        "三个驾驶舱已弹出：图片(左上, 荧光绿) / 爬虫(左, 品红) / 任务(右, 电光青)。\n"
        "拖一拖窗面看位置记忆，Ctrl+C 退出。"
    )
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n退出。")
    finally:
        window.shutdown()


if __name__ == "__main__":
    main()
