"""HUD 渲染路径守卫。

## 事故（2026-11-12）

把 `CYAN_DIM` 换成 `RULE` 时漏了 import，`_draw_header` 在画完标题后抛
`NameError`，被 `_pump` 的 `except` 记进 diag 吞掉——**任务驾驶舱只剩背景和标题**，
跑了一轮真实对话才被用户发现。

当时三道检查全是绿的，各有各的盲区：

| 检查 | 为什么没抓到 |
|---|---|
| `py_compile` | 只查语法；`NameError` 是运行时错误 |
| `tests/ui`（257 条） | 用假窗口替换 `CockpitWindow`，从未真正执行 `_Hud.draw()` |
| 人工冒烟（起窗 2.5 秒） | 只验「不崩」，异常被吞后进程照样活着 |

本文件补两道闸：

1. **ruff F821**（CI 有效）：任何模块用了未导入的名字即红——这次 `RULE` 就栽在这里；
2. **真 Tk 渲染**（有显示环境才跑）：`draw()` 之后 canvas 上必须有上百个 item
   且不止个位数文字项，"只画了背景"会立刻红。

@author aceFelix
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 背景材质（扫描线 + 网格 + 颗粒 + 侧刻度 + 外框）本身就有 200 个左右的 item
_MIN_ITEMS = 200
#: HUD 正文至少这么多段文字（标题 2 + 指标 6 + 序列/数据流若干）
_MIN_TEXTS = 10


def test_no_undefined_names_in_agent() -> None:
    """F821：用了没导入的名字就红（这次 RULE 漏 import 的事故根因）。"""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F821", "agent"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if "No module named ruff" in (result.stderr or ""):
        pytest.skip("ruff 未安装")
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def test_hud_draw_paints_real_items() -> None:
    """真渲染：`_Hud.draw` 之后必须有背景 + 正文，不能只有背景。

    CI（无显示）跳过；本地 Windows 必跑——这是"只剩背景"唯一能被自动抓到的地方。
    """
    try:
        import tkinter as tk
    except Exception:  # pragma: no cover - 极少数无 tkinter 的解释器
        pytest.skip("tkinter 不可用")
    try:
        root = tk.Tk()
    except Exception:  # pragma: no cover - 无显示环境
        pytest.skip("无显示环境")

    try:
        root.withdraw()
        from agent.ui.cockpit import CockpitState, VisualOptions
        from agent.ui.cockpit_window import _Hud

        hud = _Hud(root, on_close=lambda: None, options=VisualOptions())
        state = CockpitState()
        state.update({"type": "run_start", "run_id": "r"})
        state.update(
            {"type": "tool_start", "tool": "Bash", "tool_use_id": "t1",
             "target": {"kind": "file", "path": "D:/tmp/a.txt"}}
        )
        state.update(
            {"type": "tool_end", "tool": "Bash", "tool_use_id": "t1",
             "ok": True, "duration_ms": 120}
        )
        hud.draw(state)

        canvas = hud.canvas
        items = len(canvas.find_all())
        texts = sum(1 for i in canvas.find_all() if canvas.type(i) == "text")
        assert items > _MIN_ITEMS, f"只画了 {items} 个 item，正文可能被异常吞掉了"
        assert texts >= _MIN_TEXTS, f"只画了 {texts} 段文字，正文没画出来"
    finally:
        root.destroy()
