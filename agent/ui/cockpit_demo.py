"""主 HUD 本地预览脚本（``python -m agent.ui.cockpit_demo``）。

从 ``cockpit_window.py`` 拆出（单文件 900 行上限，2026-11-09）。仅用于开发时
肉眼检查 HUD 渲染与点击展开详情，不参与运行时。

@author aceFelix
"""

from __future__ import annotations

import time

from agent.ui.cockpit_window import CockpitWindow


def _demo() -> None:
    """投递一组示例事件，打开主 HUD 预览。"""
    win = CockpitWindow()
    now = time.monotonic()
    samples = [
        {"type": "run_start", "run_id": "demo"},
        {"type": "tool_start", "tool": "FileWrite", "target": {"kind": "file", "path": "E:/demo/alpha.txt"}, "input": {"file_path": "E:/demo/alpha.txt", "content": "hello"}},
        {"type": "tool_end", "tool": "FileWrite", "ok": True, "duration_ms": 37},
        {"type": "file_change", "path": "E:/demo/alpha.txt", "diff": {"added": 1, "removed": 0}},
        {"type": "tool_start", "tool": "WebFetch", "target": {"kind": "window", "title": "https://example.com"}, "input": {"url": "https://example.com/a"}},
        {"type": "tool_end", "tool": "WebFetch", "ok": True, "duration_ms": 320},
        {"type": "web_fetch", "url": "https://example.com/a", "status": "ok", "duration_ms": 320, "bytes": 12288},
        {"type": "tool_start", "tool": "Bash", "target": None, "input": {"command": "false"}},
        {"type": "tool_end", "tool": "Bash", "ok": False, "duration_ms": 2100, "error": "exit 1"},
        {"type": "run_end", "steps": 3, "failed": 1, "duration_ms": int((time.monotonic() - now) * 1000) + 4200},
    ]
    for event in samples:
        win.feed(event)
        time.sleep(0.35)
    time.sleep(6)
    win.shutdown()
    time.sleep(0.3)


if __name__ == "__main__":
    _demo()
