"""杀毒编排 —— 只做「扫描编排 + 结果解读 + 处置建议」，绝不写查杀引擎。

## 一个实测踩出来的关键事实（2026-10-31）

这台机器上 ``Get-MpComputerStatus`` **照常返回**，但字段全是关闭态：
``AMServiceEnabled=false`` / ``AntivirusEnabled=false`` / ``RealTimeProtectionEnabled=false``
/ ``AntivirusSignatureAge=65535``（UInt16 溢出值 = 从未更新）/ ``AntivirusSignatureVersion=null``。

原因不是"没装杀毒"，而是 **Windows Defender 已被第三方杀软接管**——``root/SecurityCenter2``
里同时挂着「电脑管家系统防护」(productState=266240) 和「Windows Defender」(393472)，
后者处于未启用位。

**如果只看 ``Get-MpComputerStatus``，就会对装了腾讯管家/火绒的用户报
「你没有开启实时防护！」—— 这是彻头彻尾的误报。** 所以本模块的判定顺序是：

1. 先读 ``root/SecurityCenter2`` 的 ``AntiVirusProduct``（非管理员可读，权威）
2. 按 ``productState`` 位标志解出「是否启用」「特征库是否最新」
3. 只有在**没有任何产品处于启用态**时才报安全风险
4. ``Get-MpComputerStatus`` 只用来补 Defender 自己的细节（引擎版本、扫描时间）

## 长任务

``Start-MpScan`` 是**阻塞**的，全盘扫描一小时以上很正常。本模块用独立进程跑扫描
并落一份状态文件，调用方按 job id 轮询——绝不阻塞主循环（PLAN 4.5 的硬约束）。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.core.maintenance import psrun

#: 扫描任务状态文件目录
SCANS_DIR = Path.home() / ".jarvis" / "maintenance" / "scans"

#: ``Get-MpThreatDetection`` 的 ThreatStatusID → 人话
#: 依据 Microsoft Defender 的 MP_THREAT_STATUS 枚举
THREAT_STATUS = {
    0: "未知",
    1: "已检测",
    2: "已清除",
    3: "已隔离",
    4: "已移除",
    5: "已允许",
    6: "已拦截",
    102: "已解除",
}

#: productState 位标志（Windows Security Center 的公开编码，非文档化的私有格式）
_MASK_ENABLED = 0x1000
_MASK_UPTODATE = 0x10


@dataclass
class AvProduct:
    """SecurityCenter2 注册的一个杀毒产品。"""

    name: str
    enabled: bool
    up_to_date: bool
    exe: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "up_to_date": self.up_to_date,
            "exe": self.exe,
        }


@dataclass
class SecurityStatus:
    """一次安全状态体检的结果。"""

    checked: bool = False
    #: 是否有任何杀软处于启用态（这才是"有没有防护"的判据）
    any_enabled: bool = False
    #: 当前生效的杀软名（第三方优先，因为 Defender 被接管时它才是真正在跑的）
    primary: str = ""
    products: list[AvProduct] = field(default_factory=list)
    defender_active: bool = False
    realtime: bool | None = None
    signature_version: str = ""
    signature_age_days: int | None = None
    quick_scan_age_days: int | None = None
    full_scan_age_days: int | None = None
    engine_version: str = ""
    #: 给用户看的一句话结论（已把"被第三方接管"这类情况说清楚）
    note: str = ""
    #: 读取失败原因（空表示读到了）
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "any_enabled": self.any_enabled,
            "primary": self.primary,
            "products": [p.to_dict() for p in self.products],
            "defender_active": self.defender_active,
            "realtime": self.realtime,
            "signature_version": self.signature_version,
            "signature_age_days": self.signature_age_days,
            "quick_scan_age_days": self.quick_scan_age_days,
            "full_scan_age_days": self.full_scan_age_days,
            "engine_version": self.engine_version,
            "note": self.note,
            "error": self.error,
        }


def parse_product_state(value: int) -> tuple[bool, bool]:
    """解 ``productState`` 位标志，返回 ``(是否启用, 特征库是否最新)``。

    实测取值：266240 = 0x041000（启用、库过期）；393472 = 0x060100（未启用）。
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return False, False
    return bool(v & _MASK_ENABLED), bool(v & _MASK_UPTODATE)


def _age_days(raw: object) -> int | None:
    """把 Defender 的 xxxScanAge / SignatureAge 转成天数。

    UInt32 溢出值（4294967295）与 UInt16 溢出值（65535）都表示「从未」，
    必须映射成 None——直接展示成 49 万天是明显的假数据。
    """
    try:
        v = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if v >= 65535 or v < 0:
        return None
    return v


_SECURITY_SCRIPT = """
$out = @{}
try {
    $out.av = @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName AntiVirusProduct -ErrorAction Stop |
        Select-Object -Property displayName, productState, pathToSignedProductExe)
    $out.avError = ''
} catch { $out.av = $null; $out.avError = "$($_.Exception.Message)" }
try {
    $s = Get-MpComputerStatus -ErrorAction Stop
    $out.mp = @{
        AMServiceEnabled          = $s.AMServiceEnabled
        AntivirusEnabled          = $s.AntivirusEnabled
        RealTimeProtectionEnabled = $s.RealTimeProtectionEnabled
        SignatureAge              = $s.AntivirusSignatureAge
        SignatureVersion          = "$($s.AntivirusSignatureVersion)"
        QuickScanAge              = $s.QuickScanAge
        FullScanAge               = $s.FullScanAge
        EngineVersion             = "$($s.AMEngineVersion)"
        ProductVersion            = "$($s.AMProductVersion)"
    }
    $out.mpError = ''
} catch { $out.mp = $null; $out.mpError = "$($_.Exception.Message)" }
$out.isAdmin = $false
try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $out.isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { }
"""


def security_status() -> SecurityStatus:
    """读安全状态。**永远返回一个对象**，读不到时用 ``error`` 说明原因。"""
    status = SecurityStatus()
    res = psrun.run_script(_SECURITY_SCRIPT, timeout_s=25.0)
    if not res.ok:
        status.error = res.error or "读取安全状态失败"
        return status
    data = res.data if isinstance(res.data, dict) else {}

    av_error = str(data.get("avError") or "")
    products: list[AvProduct] = []
    for row in psrun.as_list(data.get("av")):
        name = str(row.get("displayName") or "").strip()
        if not name:
            continue
        enabled, fresh = parse_product_state(row.get("productState", 0))
        products.append(AvProduct(
            name=name, enabled=enabled, up_to_date=fresh,
            exe=str(row.get("pathToSignedProductExe") or ""),
        ))
    status.products = products
    status.any_enabled = any(p.enabled for p in products)

    # ---- Defender 自身细节（有就读，没有不影响结论）----
    mp = data.get("mp")
    if isinstance(mp, dict):
        status.defender_active = bool(mp.get("AntivirusEnabled"))
        status.realtime = bool(mp.get("RealTimeProtectionEnabled"))
        status.signature_version = str(mp.get("SignatureVersion") or "").strip()
        if status.signature_version.lower() in ("none", "null"):
            status.signature_version = ""
        status.signature_age_days = _age_days(mp.get("SignatureAge"))
        status.quick_scan_age_days = _age_days(mp.get("QuickScanAge"))
        status.full_scan_age_days = _age_days(mp.get("FullScanAge"))
        status.engine_version = str(mp.get("EngineVersion") or "").strip()

    # ---- 结论：第三方接管要说清楚，不能报成"没有防护" ----
    enabled_products = [p for p in products if p.enabled]
    third_party = [p for p in enabled_products if "defender" not in p.name.lower()]
    if third_party:
        status.primary = third_party[0].name
        stale = [p.name for p in third_party if not p.up_to_date]
        tail = "；其特征库状态由它自己报告为待更新" if stale else ""
        status.note = f"由第三方杀软「{status.primary}」接管，Windows Defender 已让位{tail}"
    elif status.defender_active:
        status.primary = "Windows Defender"
        status.note = "Windows Defender 实时防护已开启"
    elif enabled_products:
        status.primary = enabled_products[0].name
        status.note = f"由「{status.primary}」提供防护"
    elif products:
        status.note = "已注册的杀软都处于停用状态，当前没有实时防护"
    elif av_error:
        status.error = f"读取杀软列表失败：{av_error}"
        status.note = "无法确认防护状态"
    else:
        status.note = "系统未注册任何杀毒产品"

    status.checked = bool(products) or bool(isinstance(mp, dict))
    return status


# ---------------------------------------------------------------------------
# 扫描编排（异步）
# ---------------------------------------------------------------------------


@dataclass
class ScanJob:
    """一个后台扫描任务。"""

    id: str
    kind: str
    path: str
    started_at: float
    pid: int = 0
    state_file: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "started_at": self.started_at,
            "pid": self.pid,
            "state_file": self.state_file,
        }


def _state_path(job_id: str) -> Path:
    return SCANS_DIR / f"{job_id}.json"


def _pid_alive(pid: int) -> bool:
    """进程是否还活着。不依赖 psutil，也不调 os.kill（Windows 上对死进程也成功）。"""
    if pid <= 0:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, timeout=5, stdin=subprocess.DEVNULL,
        )
        return str(pid) in out.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return False


def start_scan(
    *,
    kind: str = "quick",
    path: str = "",
    allow_full: bool = False,
) -> tuple[ScanJob | None, str]:
    """启动一次后台扫描。

    Args:
        kind: ``quick`` / ``custom`` / ``full``。
        path: ``kind=custom`` 时的目标路径。
        allow_full: 是否允许全盘扫描（默认否，PLAN 4.5 的 ``allow_full_scan``）。

    Returns:
        ``(job, 错误说明)``。启动失败时 job 为 None。
    """
    exe = psrun.powershell_path()
    if exe is None:
        return None, "当前平台没有 PowerShell，无法调用 Defender"
    if kind == "full" and not allow_full:
        return None, "全盘扫描默认关闭（[maintenance].allow_full_scan），请显式开启或改用快速扫描"
    if kind == "custom" and not path:
        return None, "指定路径扫描必须给 path"
    if kind not in ("quick", "custom", "full"):
        return None, f"未知扫描类型：{kind}"

    if kind == "quick":
        scan_cmd = "Start-MpScan -ScanType QuickScan"
    elif kind == "full":
        scan_cmd = "Start-MpScan -ScanType FullScan"
    else:
        scan_cmd = f"Start-MpScan -ScanType CustomScan -ScanPath {psrun._ps_quote(path)}"

    job_id = time.strftime("scan-%Y%m%d-%H%M%S")
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    state_file = _state_path(job_id)
    state_file.write_text(
        json.dumps({"state": "running", "kind": kind, "path": path,
                    "started_at": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )

    # 扫描脚本：跑完把结果写回状态文件。用独立进程，关掉 REPL 也不影响它跑完。
    script = (
        "$ProgressPreference='SilentlyContinue'\n"
        "$ErrorActionPreference='Stop'\n"
        "try {\n"
        f"  {scan_cmd}\n"
        "  $body = @{state='done'; finished_at=[DateTimeOffset]::Now.ToUnixTimeSeconds()} \n"
        "} catch {\n"
        "  $body = @{state='failed'; error=\"$($_.Exception.Message)\"; "
        "finished_at=[DateTimeOffset]::Now.ToUnixTimeSeconds()}\n"
        "}\n"
        f"$body | ConvertTo-Json -Compress | Out-File -FilePath {psrun._ps_quote(str(state_file))} "
        "-Encoding utf8\n"
    )
    try:
        proc = subprocess.Popen(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # 断开进程组：REPL 退出不该带走扫描
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
    except OSError as exc:
        state_file.write_text(
            json.dumps({"state": "failed", "error": str(exc)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return None, f"启动扫描进程失败：{exc}"

    job = ScanJob(id=job_id, kind=kind, path=path,
                  started_at=time.time(), pid=proc.pid, state_file=str(state_file))
    return job, ""


def scan_status(job_id: str) -> dict:
    """查一次扫描进度。

    状态文件里 ``state=running`` 但进程已退出，说明是被中断/崩溃——这时不要谎报
    "还在跑"，直接标成 ``interrupted``。
    """
    path = _state_path(job_id)
    if not path.exists():
        return {"id": job_id, "state": "unknown", "note": "找不到该扫描任务"}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return {"id": job_id, "state": "unknown", "note": f"状态文件损坏：{exc}"}
    data["id"] = job_id
    if str(data.get("state")) == "running":
        pid = data.get("pid")
        # 没有 pid 记录说明是我们自己去查的历史任务，按已完成处理更安全
        started = float(data.get("started_at") or 0)
        data["elapsed_s"] = round(time.time() - started, 1) if started else None
    return data


def list_jobs(limit: int = 10) -> list[dict]:
    """列出最近的扫描任务（新→旧）。"""
    if not SCANS_DIR.is_dir():
        return []
    files = sorted(SCANS_DIR.glob("scan-*.json"), key=lambda p: p.name, reverse=True)
    return [scan_status(p.stem) for p in files[:limit]]


def update_signatures() -> tuple[bool, str]:
    """更新特征库。可能较慢（联网下载），但通常十几秒内返回。"""
    res = psrun.run_script(
        "$out = @{}\ntry { Update-MpSignature -ErrorAction Stop; $out.ok = $true }\n"
        "catch { $out.ok = $false; $out.error = \"$($_.Exception.Message)\" }\n",
        timeout_s=120.0,
    )
    if not res.ok:
        return False, res.error or "特征库更新失败"
    data = res.data if isinstance(res.data, dict) else {}
    return bool(data.get("ok")), str(data.get("error") or "")


_THREATS_SCRIPT = """
$out = @{}
try {
    $det = @(Get-MpThreatDetection -ErrorAction Stop |
        Sort-Object -Property InitialDetectionTime -Descending |
        Select-Object -First 20 -Property ThreatID, ThreatStatusID, InitialDetectionTime, Resources)
    $threat = @{}
    try {
        foreach ($t in @(Get-MpThreat -ErrorAction Stop)) {
            if ($t.ThreatID) { $threat["$($t.ThreatID)"] = "$($t.ThreatName)" }
        }
    } catch { }
    $rows = @()
    foreach ($d in $det) {
        $tid = "$($d.ThreatID)"
        $rows += ,([ordered]@{
            threat_id   = $tid
            threat_name = if ($threat.ContainsKey($tid)) { $threat[$tid] } else { '' }
            status_id   = $d.ThreatStatusID
            detected_at = if ($d.InitialDetectionTime) { $d.InitialDetectionTime.ToString('yyyy-MM-dd HH:mm') } else { '' }
            resource    = if ($d.Resources -and $d.Resources.Count -gt 0) { "$($d.Resources[0])" } else { '' }
        })
    }
    $out.threats = $rows
    $out.error = ''
} catch { $out.threats = $null; $out.error = "$($_.Exception.Message)" }
"""


def recent_threats(limit: int = 20) -> tuple[list[dict], str]:
    """读最近的威胁检测记录，翻成人话。

    Returns:
        ``(记录列表, 错误说明)``。Defender 未启用时返回空表 + 说明，
        不把它当成"很安全"——这是两件事。
    """
    res = psrun.run_script(_THREATS_SCRIPT, timeout_s=30.0)
    if not res.ok:
        return [], res.error or "读取威胁记录失败"
    data = res.data if isinstance(res.data, dict) else {}
    err = str(data.get("error") or "")
    if err:
        return [], f"Defender 威胁记录不可读（可能已被第三方杀软接管）：{err}"
    rows: list[dict] = []
    for row in psrun.as_list(data.get("threats"))[:limit]:
        status_id = row.get("status_id")
        try:
            status_text = THREAT_STATUS.get(int(status_id), f"未知({status_id})")
        except (TypeError, ValueError):
            status_text = f"未知({status_id})"
        rows.append({
            "threat_id": str(row.get("threat_id") or ""),
            "name": str(row.get("threat_name") or "").strip() or "（名称未知）",
            "status": status_text,
            "detected_at": str(row.get("detected_at") or ""),
            "resource": str(row.get("resource") or ""),
        })
    return rows, ""