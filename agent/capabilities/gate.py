"""打包版能力门禁（2026-11-07）。

背景：桌面安装包自带冻结后端 `jarvis-serve.exe`，此前发布流水线只装基础依赖，
导致“开发态能截图/语音，打包态没有”。本模块给冻结包一个可执行的自检门槛：
`jarvis-serve.exe --caps` 打印结构化结果，并在任何一项打包能力缺失时返回非 0，
让 CI 在发布前就红，而不是等用户安装后发现。

两类检查刻意分开：

1. **能力表**（`CAPABILITY_REGISTRY`）检查 Python 包、必需二进制/资产、探针故障；
   用户凭据（secret）不计入打包门禁——API key 属于用户配置，不属于安装包能力。
2. **extras 直查**：能力表没有覆盖的包（`pypinyin`、`aiohttp`、`qrcode`、
   `win32com` 等）逐模块 `find_spec`，避免“能力表没登记就漏打包”。
3. **浏览器双内核**：playwright 包在 ≠ 内核在；分别检查无头壳与完整 chromium
   目录，对应 `headless=True/False` 两种启动形态。

@author aceFelix
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from agent.capabilities.registry import CapState, ReqState

#: 打包版必须达到“可用”的能力（不含密钥类；secret 由用户配置）。
PACKAGED_REQUIRED_CAPABILITIES: tuple[str, ...] = (
    "browser.selfcheck",
    "gui.input",
    "gui.window",
    "gui.screen_capture",
    "camera.capture",
    "voice.duplex",
    "voice.half",
    "mcp.connect",
    "vision.detect",
    "system.monitor",
)

#: 能力表之外的 extras 模块直查（分组 → 模块名）。python 3.12 / Windows 构建机为准。
PACKAGED_MODULES: dict[str, tuple[str, ...]] = {
    "gui": ("pyautogui", "PIL", "pygetwindow", "pywinauto"),
    "camera": ("cv2", "numpy", "PIL"),
    "vision": ("mediapipe", "cv2", "paddleocr", "paddle"),
    "voice": ("dashscope", "pyaudio", "aec_audio_processing", "numpy", "pypinyin"),
    "browser": ("playwright",),
    "mcp": ("mcp",),
    "wechat": ("aiohttp", "qrcode"),
    "daemon": ("psutil", "win32com"),
}

# Windows 专有模块：只在 win32 上要求；其他平台构建跳过。
_WIN_ONLY = {"win32com", "pywinauto"}


def _module_state(name: str) -> str:
    """模块能否 import（不实际执行包初始化，避免副作用）。"""
    if sys.platform != "win32" and name in _WIN_ONLY:
        return "skipped"
    try:
        return "ok" if importlib.util.find_spec(name) is not None else "missing"
    except (ImportError, ValueError):
        return "missing"


def _browser_kernels() -> dict[str, str]:
    """检查 playwright 双内核目录（无头壳 + 完整 chromium）。

    路径口径与 probe 一致：优先 ``PLAYWRIGHT_BROWSERS_PATH``，否则按平台默认。
    serve_entry 在冻结态会把该变量指向随包的 ``ms-playwright``。
    """
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env:
        root = Path(env)
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        root = Path(base) / "ms-playwright"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Caches" / "ms-playwright"
    else:
        root = Path.home() / ".cache" / "ms-playwright"
    headless = full = "missing"
    if root.is_dir():
        for p in root.iterdir():
            if not p.is_dir():
                continue
            if p.name.startswith("chromium_headless_shell-"):
                headless = "ok"
            elif p.name.startswith("chromium-"):
                full = "ok"
    return {"root": str(root), "headless": headless, "full": full}


def evaluate(reports: dict[str, Any]) -> dict[str, Any]:
    """根据能力报告与模块直查，产出打包门禁结论。

    Args:
        reports: ``probe.check_all()`` 的返回值（能力名 → CapabilityReport）。

    Returns:
        ``{"ok": bool, "failures": [...], "modules": {...}, "browsers": {...},
        "capabilities": {name: {state, summary}}}``。

    @author aceFelix
    """
    failures: list[str] = []

    modules = {
        group: {mod: _module_state(mod) for mod in mods}
        for group, mods in PACKAGED_MODULES.items()
    }
    for group, states in modules.items():
        for mod, state in states.items():
            if state == "missing":
                failures.append(f"缺少模块 {mod}（extras 组 {group}）")

    browsers = _browser_kernels()
    if browsers["headless"] != "ok":
        failures.append("缺少 playwright 无头内核（chromium_headless_shell-*）")
    if browsers["full"] != "ok":
        failures.append("缺少 playwright 完整 chromium（有头调试不可用）")

    caps: dict[str, dict[str, Any]] = {}
    for name in PACKAGED_REQUIRED_CAPABILITIES:
        report = reports.get(name)
        if report is None:
            failures.append(f"能力未登记：{name}")
            continue
        caps[name] = {"state": report.state, "summary": report.summary_line()}
        if report.state == CapState.UNKNOWN:
            failures.append(f"{name}: 未登记（按不可靠处理）")
        for req in report.requirements:
            # 用户凭据不属于安装包能力。
            if req.kind == "secret":
                continue
            if req.state == ReqState.PROBE_FAILED:
                failures.append(f"{name}: 探针失败（{req.label}）——打包结论不可信")
                continue
            if req.kind == "python" and req.state != ReqState.OK:
                failures.append(f"{name}: Python 包 {req.label} 未就绪（{req.state}）")
                continue
            if not req.optional and req.state in (ReqState.MISSING, ReqState.UNSUPPORTED):
                failures.append(f"{name}: 必需项 {req.label} {req.state}")

    return {
        "ok": not failures,
        "failures": failures,
        "modules": modules,
        "browsers": browsers,
        "capabilities": caps,
    }


__all__ = [
    "PACKAGED_MODULES",
    "PACKAGED_REQUIRED_CAPABILITIES",
    "evaluate",
]
