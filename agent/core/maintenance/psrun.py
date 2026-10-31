"""PowerShell 执行通道 —— 体检层唯一的外部命令入口。

## 为什么必须有这一层而不是到处 ``subprocess.run``

2026-10-31 实测这台 Windows 机器（PowerShell 5.1.19041）踩到四个坑，全部在这一层
收口，否则每个调用点都要各交一次学费：

1. **编码**：``powershell.exe`` 的 stdout 走系统代码页（中文机是 GBK），管道里
   直接读会得到乱码。改成「让 PowerShell 自己写到临时文件、用无 BOM 的 UTF-8」，
   实测在中文 Windows 上稳定，且不必猜代码页。
2. **``ConvertTo-Json`` 默认深度只有 2**：嵌套对象会被截成 ``System.Object[]``，
   所以统一 ``-Depth 6``。
3. **单引号路径要转义**：Windows 路径里带单引号虽罕见，但用户名、目录名里出现
   撇号并不违法，直接拼进单引号字符串会破语法。统一走 ``_ps_quote``。
4. **非管理员是常态**：实测 ``Get-StorageReliabilityCounter`` / ``vssadmin`` /
   ``dism /AnalyzeComponentStore`` / 开机性能事件日志**全部拒绝访问**，
   而 ``Get-PhysicalDisk``、``Get-CimInstance``、``Get-Counter``、
   ``powercfg /batteryreport`` 不需要提权。本层不做"自动提权"——那会弹 UAC，
   让一个"看看电脑怎么样"的请求变成惊吓。需要提权的数据源一律降级为
   「标出原因、不假装成功」。

## 安全边界

本模块只执行**代码里写死的脚本**。规则库里用户可改的命令（``clean_cmd``）
**不走这里**，由 ``agent/tools/extensions/maintenance.py`` 里单独的白名单通路执行。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: 一次 PowerShell 调用的默认超时（秒）。体检要在用户耐心耗尽前给答案。
DEFAULT_TIMEOUT_S = 25.0

#: 需要管理员但被拒绝时的统一措辞（各区块共用，保证文案一致）
ADMIN_REQUIRED_HINT = "需要以管理员身份运行才能读取"

#: 实测发现的"拒绝访问"关键词（中文系统 + 英文系统两套）
_ACCESS_DENIED_MARKERS = (
    "拒绝访问",
    "未经授权",
    "没有正确的权限",
    "需要提升权限",
    "需要提升",
    "Access is denied",
    "not authorized",
    "requires elevation",
    "elevation required",
)

#: 找不到命令/类
_NOT_FOUND_MARKERS = (
    "无法识别",
    "找不到",
    "not recognized",
    "not found",
    "is not recognized",
)


@dataclass
class PsResult:
    """一次 PowerShell 调用的结果。

    刻意不抛异常：体检是"尽力而为"的聚合，一块读不到不该让整份报告消失。
    调用方据 ``ok`` / ``denied`` / ``error`` 决定怎么展示。
    """

    ok: bool = False
    data: object = None
    raw: str = ""
    error: str = ""
    #: 失败原因归类：``denied``（要提权）/ ``missing``（本机不支持）/ ``timeout`` / ``"""
    reason: str = ""
    elapsed_s: float = 0.0

    def __bool__(self) -> bool:
        return self.ok


def _ps_quote(text: str) -> str:
    """把字符串安全地嵌进 PowerShell 单引号字面量。

    PowerShell 单引号串里转义单引号的方式是**写两个**（不是反斜杠）。
    """
    return "'" + str(text).replace("'", "''") + "'"


def classify_failure(text: str) -> str:
    """把 PowerShell 的报错归类，决定展示口径。"""
    low = (text or "").lower()
    for marker in _ACCESS_DENIED_MARKERS:
        if marker.lower() in low:
            return "denied"
    for marker in _NOT_FOUND_MARKERS:
        if marker.lower() in low:
            return "missing"
    return "error"


def powershell_path() -> str | None:
    """找可用的 PowerShell 可执行文件（优先 Windows PowerShell 5.1）。"""
    if os.name != "nt":
        return None
    for name in ("powershell.exe", "pwsh.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> bool:
    """本机是否具备运行 PowerShell 的条件（非 Windows 直接 False）。"""
    return powershell_path() is not None


def run_script(
    body: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    expect_json: bool = True,
) -> PsResult:
    """执行一段 PowerShell 脚本。

    Args:
        body: 脚本体。用 ``expect_json=True`` 时应把结果放进 ``$out`` 变量
            （本函数负责序列化），而不是自己 ``Write-Output``。
        timeout_s: 超时秒数。超时按失败返回（``reason="timeout"``），不抛。
        expect_json: 是否把 ``$out`` 序列化成 JSON 读回。

    Returns:
        :class:`PsResult`
    """
    started = __import__("time").monotonic()
    exe = powershell_path()
    if exe is None:
        return PsResult(error="当前平台没有 PowerShell（维护体检仅支持 Windows）",
                        reason="missing", elapsed_s=0.0)

    tmpdir = tempfile.mkdtemp(prefix="jarvis-ps-")
    out_path = Path(tmpdir) / "out.json"
    script_path = Path(tmpdir) / "run.ps1"
    try:
        # 写脚本文件而不是 -Command：命令太长时 Windows 有 8191 字符上限，
        # 且文件形式下引号嵌套不用再过两层 shell 解析。
        prelude = (
            "$ProgressPreference = 'SilentlyContinue'\n"
            "$ErrorActionPreference = 'Continue'\n"
            "try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }\n"
        )
        tail = ""
        if expect_json:
            tail = (
                "\n$json = ConvertTo-Json -InputObject $out -Depth 6 -Compress\n"
                f"[IO.File]::WriteAllText({_ps_quote(str(out_path))}, $json, "
                "[Text.UTF8Encoding]::new($false))\n"
            )
        script_path.write_text(prelude + body + tail, encoding="utf-8")

        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script_path)],
            capture_output=True,
            timeout=timeout_s,
            # stdin 必须断开：继承宿主管道会让 PowerShell 等输入而不是退出
            stdin=subprocess.DEVNULL,
        )
        stderr_text = proc.stderr.decode("utf-8", errors="replace").strip()
        if expect_json and out_path.exists():
            raw = out_path.read_text(encoding="utf-8-sig", errors="replace").strip()
            if raw:
                try:
                    return PsResult(
                        ok=True, data=json.loads(raw), raw=raw,
                        elapsed_s=round(__import__("time").monotonic() - started, 2),
                    )
                except ValueError as exc:
                    return PsResult(
                        error=f"PowerShell 输出不是合法 JSON：{exc}", raw=raw[:2000],
                        reason="error",
                        elapsed_s=round(__import__("time").monotonic() - started, 2),
                    )
            # $out 是 $null（脚本里全部 catch 掉了）——这不算失败，是"本机没有该数据"
            return PsResult(
                ok=True, data=None, error=stderr_text[:500],
                elapsed_s=round(__import__("time").monotonic() - started, 2),
            )

        stdout_text = proc.stdout.decode("utf-8", errors="replace").strip()
        combined = "\n".join(x for x in (stdout_text, stderr_text) if x)
        if proc.returncode == 0:
            return PsResult(
                ok=True, data=stdout_text, raw=combined,
                elapsed_s=round(__import__("time").monotonic() - started, 2),
            )
        return PsResult(
            error=combined[:1000] or f"退出码 {proc.returncode}",
            raw=combined, reason=classify_failure(combined),
            elapsed_s=round(__import__("time").monotonic() - started, 2),
        )
    except subprocess.TimeoutExpired:
        return PsResult(
            error=f"命令超过 {timeout_s:.0f} 秒未返回", reason="timeout",
            elapsed_s=round(__import__("time").monotonic() - started, 2),
        )
    except (OSError, ValueError) as exc:
        return PsResult(
            error=f"{type(exc).__name__}: {exc}", reason="error",
            elapsed_s=round(__import__("time").monotonic() - started, 2),
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def as_list(data: object) -> list[dict]:
    """把 ``ConvertTo-Json`` 的单对象/数组统一成 dict 列表。

    这是 PowerShell 互通里最烦人的一处：``@(单个对象)`` 序列化后可能是一个
    dict 而不是长度 1 的数组，直接迭代 dict 会拿到它的 key。
    """
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def is_admin() -> bool:
    """当前进程是否有管理员权限（决定是否尝试需要提权的数据源）。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False