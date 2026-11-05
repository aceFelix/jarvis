"""冒烟探针（秒级）—— 静态档说"东西都在"之后，真跑一次看看。

为什么要第二档：`import 成功 ≠ 能力可用`，反过来也成立一半 —— **包在、内核目录也
在，仍然可能起不来**（内核下载不完整、revision 与包版本不匹配、杀毒拦截、缺运行库）。
静态档对这些一律看不见。计划第 7.2 条要求的故障注入验收，最后一步就得靠这一档兜。

三条纪律与 probe.py 一致，但多一条最要紧的：

1. **只在显式需要时跑**（`/health caps --deep`、修复后的复测）。启动路径绝不跑这里，
   否则"启动 <50ms"的预算立刻作废。
2. **每个探针跑在子进程里**。理由不是优雅：playwright 的 sync API 撞上正在运行的
   event loop 会直接报错，而 jarvis 到处都在 async 里；子进程还能被超时硬杀，
   主进程不会跟着挂住。
3. **认不出来的失败一律记 `PROBE_FAILED`，不记 `MISSING`**。只有 stderr 里出现
   明确的"缺件特征"才敢判缺失。把"启动失败"当成"没装"去建议用户安装，就是
   本次 `tasklist` 骗局的翻版——只不过换了个受害者。

@author aceFelix
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

from agent.capabilities.registry import CapabilityMeta, ReqState, RequirementStatus, get_meta

# 单个冒烟探针的默认时间预算。playwright 冷启动 + 打开 about:blank 在慢机上也就
# 十几秒；给到 90s 是因为首次真下载可能顺带触发校验，超过这个数就是挂了。
SMOKE_TIMEOUT_S = 90.0

# 缺件特征：只有出现这些字样才判 MISSING（其余失败 = 没看清）。
# 取自 playwright 真实报错文案，不是猜的：`Executable doesn't exist at ...` 与
# `Please run the following command to download new browsers` 是它的固定措辞。
_MISSING_MARKERS = (
    "executable doesn't exist",
    "please run the following command to download new browsers",
    "browsertype.launch: executable doesn't",
    "no such file or directory",
)


def _is_missing_signal(text: str) -> bool:
    """这段输出是不是"明确缺件"的特征，而不是"跑挂了"。"""
    low = text.lower()
    return any(marker in low for marker in _MISSING_MARKERS)


def _run_isolated(code: str, *, timeout_s: float) -> tuple[int, str, str]:
    """在**当前解释器**的子进程里跑一段脚本。

    只认 `sys.executable`：用别的 python 跑，验的就不是用户手上这个环境（版本、
    内核 revision 都跟着解释器走）。返回 (returncode, stdout, stderr)，超时用
    returncode=-1 表示，由调用方判成 PROBE_FAILED 而不是缺件。
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
            # 刻意不用 check=True：这里要看的是 returncode + stderr 来区分“缺件”与
            # “跑挂了”；抛 CalledProcessError 会把两种情形压成同一个异常，反而分不清。
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, "", f"冒烟探针超时（>{timeout_s:.0f}s）"
    except OSError as exc:
        return -1, "", f"无法启动探针子进程：{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ---------------------------------------------------------------------------
# 具体探针
# ---------------------------------------------------------------------------

_BROWSER_LAUNCH_CODE = """
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("about:blank", wait_until="load")
        title = await page.title()
        await browser.close()
        print("SMOKE_OK", title)

asyncio.run(main())
"""


def smoke_browser_launch(timeout_s: float = SMOKE_TIMEOUT_S) -> RequirementStatus:
    """真起一次 headless Chromium，打开 `about:blank`。

    这一条直接回答"交付自查能不能亲眼看过"：静态档只能证明内核目录存在，而本次
    事故要防的是下一层——目录在、内容不完整，或包与内核版本漂移。
    """
    label = "浏览器内核冒烟（真启动一次）"
    rc, out, err = _run_isolated(_BROWSER_LAUNCH_CODE, timeout_s=timeout_s)
    tail = (err.strip() or out.strip())[-400:]

    if rc == 0 and "SMOKE_OK" in out:
        return RequirementStatus(
            kind="smoke", label=label, state=ReqState.OK,
            evidence="headless 启动并打开 about:blank 成功",
        )
    if rc == -1:
        return RequirementStatus(
            kind="smoke", label=label, state=ReqState.PROBE_FAILED,
            evidence=tail or f"探针超时（>{timeout_s:.0f}s）",
        )
    if _is_missing_signal(err + "\n" + out):
        return RequirementStatus(
            kind="smoke", label=label, state=ReqState.MISSING,
            evidence=f"启动时报缺件：{tail}",
            fix_hint=f'& "{sys.executable}" -m playwright install chromium --only-shell',
        )
    # 认不出来的失败：不判缺件。让能力变"结论不可信"，而不是编一个安装建议。
    return RequirementStatus(
        kind="smoke", label=label, state=ReqState.PROBE_FAILED,
        evidence=f"启动失败（exit={rc}，非缺件特征）：{tail}",
    )


_SCREEN_GRAB_CODE = """
from PIL import ImageGrab
img = ImageGrab.grab()
w, h = img.size
if w <= 0 or h <= 0:
    raise SystemExit("SCREEN_EMPTY")
print("SMOKE_OK", w, h)
"""


def smoke_screen_capture(timeout_s: float = 30.0) -> RequirementStatus:
    """真截一帧屏，确认拿得到像素而不是只装得上库。

    远程会话、无桌面、锁屏都会让这里失败——那些情况判 `PROBE_FAILED`：能力表
    说"看不清"比说"没装 pyautogui"诚实，用户也不会照着去重装一个本来就在的包。
    """
    label = "屏幕截图冒烟（真截一帧）"
    rc, out, err = _run_isolated(_SCREEN_GRAB_CODE, timeout_s=timeout_s)
    tail = (err.strip() or out.strip())[-300:]

    if rc == 0 and "SMOKE_OK" in out:
        size = out.strip().splitlines()[-1]
        return RequirementStatus(
            kind="smoke", label=label, state=ReqState.OK,
            evidence=f"截帧成功 {size.removeprefix('SMOKE_OK').strip()}",
        )
    if "ScreenGrab" in tail or "screen is locked" in tail.lower():
        # 明确的平台性"这里没有画面可截"，不是没装库
        return RequirementStatus(
            kind="smoke", label=label, state=ReqState.PROBE_FAILED,
            evidence=f"当前会话拿不到可截的画面：{tail}",
        )
    return RequirementStatus(
        kind="smoke", label=label, state=ReqState.PROBE_FAILED,
        evidence=f"截帧失败（exit={rc}）：{tail or '无输出'}",
    )


SMOKE_PROBES: dict[str, Callable[..., RequirementStatus]] = {
    "browser-launch": smoke_browser_launch,
    "screen-capture": smoke_screen_capture,
}


def run_smoke_for(meta: CapabilityMeta) -> list[RequirementStatus]:
    """跑某个能力声明的冒烟探针（没声明就返回空表）。

    调用方是 `probe.deep_check`，它负责两件本层管不了的事：静态档已判缺件时
    直接跳过（没必要花 90s 往一个注定失败的浏览器上撞），以及把结果合并进报告。
    """
    if not meta.smoke_probe:
        return []
    probe = SMOKE_PROBES.get(meta.smoke_probe)
    if probe is None:
        # 表里写了一个没实现的探针名：如实报告，绝不静默当成"通过"
        return [RequirementStatus(
            kind="smoke", label=f"冒烟探针 {meta.smoke_probe}", state=ReqState.PROBE_FAILED,
            evidence="能力表声明了该探针，但 SMOKE_PROBES 里没有实现（契约测会抓住这种漂移）",
        )]
    try:
        return [probe()]
    except Exception as exc:  # noqa: BLE001 - 探针炸了不能冒泡到命令层
        return [RequirementStatus(
            kind="smoke", label=f"冒烟探针 {meta.smoke_probe}", state=ReqState.PROBE_FAILED,
            evidence=f"{type(exc).__name__}: {exc}",
        )]


def describe_smoke(name: str) -> str:
    """给报告用的一行说明：这个能力有没有冒烟探针、实现在不在。"""
    meta: CapabilityMeta | None = get_meta(name)
    if meta is None or not meta.smoke_probe:
        return "无冒烟探针"
    return (
        f"冒烟探针 {meta.smoke_probe}"
        f"（{'已实现' if meta.smoke_probe in SMOKE_PROBES else '未实现'}）"
    )


__all__ = [
    "SMOKE_PROBES",
    "SMOKE_TIMEOUT_S",
    "describe_smoke",
    "run_smoke_for",
    "smoke_browser_launch",
    "smoke_screen_capture",
]
