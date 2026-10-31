"""系统体检 —— 把散在各处的观测聚成一份「电脑现在怎么样」的报告。

## 设计立场（与「加速球」的根本区别）

本模块**只发现、只解释、只建议**，一个处置入口都没有：没有 kill 进程参数、
没有禁用启动项参数、没有注册表"优化"。

为什么这么克制：被当成"无用进程"杀掉的，可能是跑了两小时的编译、导入到一半的
模型、正在保存的文档。Win10/11 的进程调度和内存管理已经够好，所谓"加速"提供的
是心理满足。BLUEPRINT 6.1 因此把它改名「性能诊断」——执行权永远在用户手里。

## 分区降级（2026-10-31 实测驱动的设计）

非管理员下这几块**必然读不到**，实测原文：
``Get-StorageReliabilityCounter`` → 无法从客户端访问 CIM 资源；
``MSAcpi_ThermalZoneTemperature`` → 拒绝访问；
开机性能事件日志 → 尝试执行未经授权的操作。

所以每个区块独立 try/catch，读不到的标 ``admin=True`` + 具体原因，
**不假装成功、不编默认值**。用户看到「需要管理员」比看到「温度 0℃」有用得多。

## 区块

| key | 内容 | 需要管理员 |
|---|---|---|
| resource | CPU/内存/磁盘容量 + 7 天趋势 + 磁盘队列 | 否 |
| disk | 物理盘型号/介质/健康状态 + SMART 磨损 | SMART 需 |
| security | 杀软与实时防护（见 defender.py 的接管判定） | 否 |
| processes | 内存占用 Top N + 启动时间 | 否 |
| startup | 启动项 / 自动服务数 / 登录触发计划任务数 | 否 |
| power | 电池电量与供电状态 / 上次开机与运行时长 | 否 |
| thermal | CPU 温度 | 是（且多数主板不支持） |

@author aceFelix
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agent.core.maintenance import defender, psrun, scanner

#: 进程热点展示条数
TOP_PROCESSES = 10

#: 启动项超过这个数量就值得提醒
STARTUP_WARN_THRESHOLD = 20

#: 内存可用低于此值（GB）值得提醒
MEM_LOW_GB = 1.0


@dataclass
class HealthSection:
    """体检的一个区块。"""

    key: str
    title: str
    ok: bool = False
    data: dict = field(default_factory=dict)
    note: str = ""
    #: 该区块因权限不足而降级（内容可能为部分数据）
    need_admin: bool = False

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "ok": self.ok,
            "data": self.data,
            "note": self.note,
            "need_admin": self.need_admin,
        }


@dataclass
class HealthReport:
    """一份完整体检报告。"""

    sections: list[HealthSection] = field(default_factory=list)
    #: 条件化建议（基于数据推出，不是通用套话）
    advice: list[str] = field(default_factory=list)
    is_admin: bool = False
    elapsed_s: float = 0.0
    #: 本次会话正在使用的目录（条件化建议用，由工具层传入）
    in_use_dirs: list[str] = field(default_factory=list)

    def section(self, key: str) -> HealthSection | None:
        for s in self.sections:
            if s.key == key:
                return s
        return None

    def to_dict(self) -> dict:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "advice": self.advice,
            "is_admin": self.is_admin,
            "elapsed_s": self.elapsed_s,
            "in_use_dirs": self.in_use_dirs,
        }


# ---------------------------------------------------------------------------
# 一次性取数脚本（分区块 try/catch，一块失败不影响其他）
# ---------------------------------------------------------------------------

_HEALTH_SCRIPT = """
$out = @{}

# ---- 管理员判定 ----
$out.isAdmin = $false
try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $out.isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
} catch { }

# ---- 资源与性能计数器 ----
try {
    $r = @{}
    $r.cpuPercent = (Get-Counter '\\Processor(_Total)\\% Processor Time' -EA Stop).CounterSamples[0].CookedValue
    $r.diskQueue  = (Get-Counter '\\PhysicalDisk(_Total)\\Current Disk Queue Length' -EA Stop).CounterSamples[0].CookedValue
    $r.diskBusy   = (Get-Counter '\\PhysicalDisk(_Total)\\% Disk Time' -EA Stop).CounterSamples[0].CookedValue
    $r.memAvailMB = (Get-Counter '\\Memory\\Available MBytes' -EA Stop).CounterSamples[0].CookedValue
    $os = Get-CimInstance Win32_OperatingSystem -EA Stop
    $r.memTotalGB  = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    $r.memFreeGB   = [math]::Round($os.FreePhysicalMemory / 1MB, 1)
    $r.uptimeHours = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours, 1)
    $r.lastBoot    = $os.LastBootUpTime.ToString('yyyy-MM-dd HH:mm')
    $r.osCaption   = "$($os.Caption) $($os.Version)"
    $out.resource = $r
    $out.resourceError = ''
} catch { $out.resource = $null; $out.resourceError = "$($_.Exception.Message)" }

# ---- 物理盘 ----
try {
    $out.disks = @(Get-PhysicalDisk -EA Stop | ForEach-Object {
        [ordered]@{
            id       = "$($_.DeviceId)"
            name     = "$($_.FriendlyName)"
            media    = "$($_.MediaType)"
            health   = "$($_.HealthStatus)"
            opStatus = "$($_.OperationalStatus)"
            sizeGB   = [math]::Round($_.Size / 1GB, 1)
        }
    })
    $out.disksError = ''
} catch { $out.disks = $null; $out.disksError = "$($_.Exception.Message)" }

# ---- SMART（要管理员） ----
try {
    $out.smart = @(Get-PhysicalDisk -EA Stop | Get-StorageReliabilityCounter -EA Stop |
        Select-Object -Property DeviceId, Wear, PowerOnHours, ReadErrorsTotal, Temperature)
    $out.smartError = ''
} catch { $out.smart = $null; $out.smartError = "$($_.Exception.Message)" }

# ---- 进程热点 ----
try {
    $top = Get-Process -EA Stop | Sort-Object WorkingSet64 -Descending | Select-Object -First __TOP__
    $rows = @()
    foreach ($p in $top) {
        $st = ''
        try { $st = $p.StartTime.ToString('yyyy-MM-dd HH:mm') } catch { }
        $cpu = $null
        try { $cpu = [math]::Round($p.CPU, 1) } catch { }
        $rows += ,([ordered]@{
            name = "$($p.Name)"; id = $p.Id
            memMB = [math]::Round($p.WorkingSet64 / 1MB, 1)
            cpuS = $cpu; start = $st
        })
    }
    $out.processes = $rows
    $out.processesError = ''
} catch { $out.processes = $null; $out.processesError = "$($_.Exception.Message)" }

# ---- 启动项 / 自动服务 / 登录计划任务 ----
try {
    $out.startup = @(Get-CimInstance -ClassName Win32_StartupCommand -EA Stop |
        Select-Object -Property Name, Command, Location, User)
    $out.startupError = ''
} catch { $out.startup = $null; $out.startupError = "$($_.Exception.Message)" }
try {
    $out.autoServices = @(Get-Service -EA Stop | Where-Object { $_.StartType -eq 'Automatic' }).Count
} catch { $out.autoServices = $null }
try {
    $out.logonTasks = @(Get-ScheduledTask -EA Stop |
        Where-Object {
            $_.State -ne 'Disabled' -and
            ($_.Triggers | Where-Object { "$($_.CimClass.CimClassName)" -match 'LogonTrigger|BootTrigger' })
        }).Count
} catch { $out.logonTasks = $null }

# ---- 电池 ----
try {
    $b = Get-CimInstance Win32_Battery -EA Stop | Select-Object -First 1
    if ($b) {
        $out.battery = [ordered]@{
            percent  = $b.EstimatedChargeRemaining
            statusId = $b.BatteryStatus
        }
    } else { $out.battery = $null }
    $out.batteryError = ''
} catch { $out.battery = $null; $out.batteryError = "$($_.Exception.Message)" }

# ---- 温度（多数主板不支持，且要管理员） ----
try {
    $t = Get-CimInstance -Namespace 'root/WMI' -ClassName MSAcpi_ThermalZoneTemperature -EA Stop |
        Select-Object -First 1
    # WMI 给的是开尔文 × 10
    $out.thermalC = [math]::Round(($t.CurrentTemperature / 10) - 273.15, 1)
    $out.thermalError = ''
} catch { $out.thermalC = $null; $out.thermalError = "$($_.Exception.Message)" }
"""

#: ``Win32_Battery.BatteryStatus`` → 人话
_BATTERY_STATUS = {
    1: "使用电池供电",
    2: "已接通电源",
    3: "已充满",
    4: "电量低",
    5: "电量严重不足",
    6: "充电中",
    7: "充电中（电量高）",
    8: "充电中（电量低）",
    9: "充电中（电量严重不足）",
    10: "未定义",
    11: "部分充电",
}


def _num(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _uptime_hours(started_text: str) -> float | None:
    """从 ``yyyy-MM-dd HH:mm`` 文本算已运行小时数；解析不了返回 None。"""
    if not started_text:
        return None
    try:
        began = time.mktime(time.strptime(started_text, "%Y-%m-%d %H:%M"))
    except (ValueError, OverflowError):
        return None
    return round((time.time() - began) / 3600, 1)


def collect(
    *,
    timeout_s: float = 60.0,
    in_use_dirs: list[str] | None = None,
    include_security: bool = True,
) -> HealthReport:
    """跑一次完整体检。

    Args:
        timeout_s: PowerShell 取数超时。
        in_use_dirs: 本次会话正在用的目录（用于条件化建议）。
        include_security: 是否带安全区块（``/disk`` 这类轻量路径可关掉省时间）。

    Returns:
        :class:`HealthReport`。**任何区块失败都不会抛异常。**
    """
    started = time.monotonic()
    report = HealthReport(in_use_dirs=list(in_use_dirs or []))
    script = _HEALTH_SCRIPT.replace("__TOP__", str(TOP_PROCESSES))
    res = psrun.run_script(script, timeout_s=timeout_s)
    data: dict = res.data if isinstance(res.data, dict) else {}
    report.is_admin = bool(data.get("isAdmin")) or psrun.is_admin()

    if not res.ok and not data:
        # 整份读不到：给一个说明区块，不要返回空报告让上层误判"一切正常"
        report.sections.append(HealthSection(
            key="resource", title="系统概况", ok=False,
            note=res.error or "PowerShell 取数失败",
        ))
        report.elapsed_s = round(time.monotonic() - started, 2)
        return report

    report.sections.append(_resource_section(data))
    report.sections.append(_disk_section(data))
    if include_security:
        report.sections.append(_security_section())
    report.sections.append(_process_section(data))
    report.sections.append(_startup_section(data))
    report.sections.append(_power_section(data))
    report.sections.append(_thermal_section(data))

    report.advice = _advise(report)
    report.elapsed_s = round(time.monotonic() - started, 2)
    return report


def _resource_section(data: dict) -> HealthSection:
    raw = data.get("resource")
    err = str(data.get("resourceError") or "")
    if not isinstance(raw, dict):
        return HealthSection(
            key="resource", title="资源与运行概况", ok=False,
            note=err or "未能读取性能计数器",
        )
    drives = scanner.drive_infos()
    critical: list[dict] = []
    for d in drives:
        mark = ""
        if d.used_percent >= 95:
            mark = "critical"
        elif d.used_percent >= 90:
            mark = "warning"
        critical.append({**d.to_dict(), "level": mark})
    payload = {
        "cpu_percent": round(_num(raw.get("cpuPercent")), 1),
        "mem_total_gb": _num(raw.get("memTotalGB")),
        "mem_available_gb": round(_num(raw.get("memAvailMB")) / 1024, 2),
        "disk_queue": round(_num(raw.get("diskQueue")), 2),
        "disk_busy_percent": round(_num(raw.get("diskBusy")), 1),
        "uptime_hours": _num(raw.get("uptimeHours")),
        "last_boot": str(raw.get("lastBoot") or ""),
        "os": str(raw.get("osCaption") or ""),
        "drives": critical,
    }
    return HealthSection(key="resource", title="资源与运行概况", ok=True, data=payload)


def _disk_section(data: dict) -> HealthSection:
    disks = psrun.as_list(data.get("disks"))
    err = str(data.get("disksError") or "")
    smart_raw = psrun.as_list(data.get("smart"))
    smart_err = str(data.get("smartError") or "")
    smart_by_id: dict[str, dict] = {}
    for row in smart_raw:
        smart_by_id[str(row.get("DeviceId") or "")] = row

    items: list[dict] = []
    for d in disks:
        did = str(d.get("id") or "")
        sm = smart_by_id.get(did, {})
        items.append({
            "id": did,
            "name": str(d.get("name") or ""),
            "media": str(d.get("media") or ""),
            "health": str(d.get("health") or ""),
            "operational": str(d.get("opStatus") or ""),
            "size_gb": _num(d.get("sizeGB")),
            "wear_percent": sm.get("Wear"),
            "power_on_hours": sm.get("PowerOnHours"),
            "read_errors": sm.get("ReadErrorsTotal"),
            "temperature_c": sm.get("Temperature"),
        })
    note = ""
    if smart_err:
        # 实测原文：无法从客户端中访问 CIM 资源 —— 需要管理员
        note = f"SMART 磨损数据{psrun.ADMIN_REQUIRED_HINT}（{smart_err}）"
    if not items:
        return HealthSection(
            key="disk", title="磁盘健康", ok=False,
            note=err or "未读到物理磁盘信息",
            need_admin=bool(err),
        )
    return HealthSection(
        key="disk", title="磁盘健康", ok=True, data={"disks": items},
        note=note, need_admin=bool(smart_err),
    )


def _security_section() -> HealthSection:
    status = defender.security_status()
    return HealthSection(
        key="security", title="安全防护", ok=status.checked,
        data=status.to_dict(), note=status.note or status.error,
        need_admin=False,
    )


def _process_section(data: dict) -> HealthSection:
    rows = psrun.as_list(data.get("processes"))
    err = str(data.get("processesError") or "")
    if not rows:
        return HealthSection(
            key="processes", title="内存占用热点", ok=False, note=err or "未读到进程列表"
        )
    items: list[dict] = []
    for row in rows:
        started_text = str(row.get("start") or "")
        hours = _uptime_hours(started_text)
        items.append({
            "name": str(row.get("name") or ""),
            "pid": row.get("id"),
            "mem_mb": _num(row.get("memMB")),
            "cpu_seconds": row.get("cpuS"),
            "started": started_text,
            # 运行不足 2 小时 = 本次会话/近期才起来的，给用户一个"这是我刚开的"判据
            "recent": bool(hours is not None and hours <= 2.0),
        })
    return HealthSection(
        key="processes",
        title=f"内存占用热点 Top {len(items)}（只报告，不会结束任何进程）",
        ok=True, data={"processes": items},
    )


def _startup_section(data: dict) -> HealthSection:
    rows = psrun.as_list(data.get("startup"))
    err = str(data.get("startupError") or "")
    items = [
        {
            "name": str(r.get("Name") or ""),
            "command": str(r.get("Command") or ""),
            "location": str(r.get("Location") or ""),
            "user": str(r.get("User") or ""),
        }
        for r in rows
    ]
    payload = {
        "items": items,
        "auto_services": data.get("autoServices"),
        "logon_tasks": data.get("logonTasks"),
    }
    if not items and err:
        return HealthSection(key="startup", title="开机自启", ok=False, note=err)
    return HealthSection(
        key="startup", title="开机自启（只报告，不会禁用任何项）",
        ok=True, data=payload,
        note="" if items else "未读到启动项",
    )


def _power_section(data: dict) -> HealthSection:
    raw = data.get("battery")
    res = data.get("resource") if isinstance(data.get("resource"), dict) else {}
    payload = {
        "uptime_hours": _num(res.get("uptimeHours")),
        "last_boot": str(res.get("lastBoot") or ""),
    }
    note = ""
    if isinstance(raw, dict):
        sid = raw.get("statusId")
        try:
            status_text = _BATTERY_STATUS.get(int(sid), f"未知({sid})")
        except (TypeError, ValueError):
            status_text = "未知"
        payload["battery_percent"] = raw.get("percent")
        payload["battery_status"] = status_text
    else:
        payload["battery_percent"] = None
        payload["battery_status"] = "本机无电池（台式机）"
        if data.get("batteryError"):
            note = str(data["batteryError"])
    return HealthSection(key="power", title="电源与运行时长", ok=True, data=payload, note=note)


def _thermal_section(data: dict) -> HealthSection:
    temp = data.get("thermalC")
    err = str(data.get("thermalError") or "")
    if temp is None:
        return HealthSection(
            key="thermal", title="温度", ok=False,
            note=f"读取不到温度：{err}" if err else "本机不提供温度数据",
            need_admin=True,
        )
    return HealthSection(key="thermal", title="温度", ok=True,
                         data={"cpu_c": _num(temp)})


def _advise(report: HealthReport) -> list[str]:
    """基于数据推条件化建议。

    **不是**通用套话清单：每条都必须由报告里的数字推出来，否则宁可不说。
    """
    out: list[str] = []

    res = report.section("resource")
    if res and res.ok:
        for d in res.data.get("drives", []):
            free = _num(d.get("free_gb"))
            total = _num(d.get("total_gb"))
            trend = d.get("trend_7d_gb")
            mount = str(d.get("mount") or "")
            if total > 0 and free / total < 0.15:
                extra = ""
                if isinstance(trend, (int, float)) and trend > 0.5:
                    # 这是 360 之类答不了的问题：不只是"满了"，而是"在涨、涨得快"
                    extra = f"，且近 7 天又涨了 {trend:.1f} GB"
                out.append(
                    f"{mount} 只剩 {free:.1f} GB（{free / total * 100:.0f}%）{extra}。"
                    f"建议先跑 /disk 看能清什么；白名单之外的大目录用 /disk --top 看谁占着。"
                )
                # 一次性清理只能拓一时：真正把「磁盘慢慢变满」这个趋势管起来的是
                # 系统自带的存储感知，它按月自动跑，不需要人记得去清。
                out.append(
                    "长期方案是打开 Windows 自带的「存储感知」（设置 → 系统 → 存储）："
                    "它会按你定的频率自动清临时文件与回收站，不用等人想起来手动清。"
                )
        avail = _num(res.data.get("mem_available_gb"))
        if avail and avail < MEM_LOW_GB:
            out.append(
                f"可用内存只剩 {avail:.2f} GB。可以不关进程，先看内存占用热点里有没有能自己收掉的。"
            )
        queue = _num(res.data.get("disk_queue"))
        busy = _num(res.data.get("disk_busy_percent"))
        if queue >= 2.0 or busy >= 90.0:
            out.append(
                f"磁盘队列 {queue:.1f}、繁忙度 {busy:.0f}% —— 现在是磁盘在拖慢系统，"
                f"不是 CPU。杀软全盘扫描或备份软件正在跑的话，等它结束就好了。"
            )
        uptime = _num(res.data.get("uptime_hours"))
        if uptime >= 168:
            out.append(
                f"距上次开机已 {uptime / 24:.0f} 天。长时间不重启会累积内存碎片与未释放句柄，"
                "找个空档重启一次，比任何花哨的优化工具都有效。"
            )

    sec = report.section("security")
    if sec and sec.ok:
        data = sec.data
        if not data.get("any_enabled"):
            out.append("当前没有任何杀软处于启用状态。建议开启 Windows 安全中心的实时防护。")
        else:
            stale = [p.get("name") for p in data.get("products", []) if p.get("enabled") and not p.get("up_to_date")]
            if stale:
                out.append(f"「{'、'.join(stale)}」报告其特征库待更新，建议更新一次。")

    startup = report.section("startup")
    if startup and startup.ok:
        count = len(startup.data.get("items") or [])
        tasks = startup.data.get("logon_tasks")
        services = startup.data.get("auto_services")
        parts = [f"启动项 {count} 个"]
        if isinstance(services, int):
            parts.append(f"自动服务 {services} 个")
        if isinstance(tasks, int):
            parts.append(f"登录触发的计划任务 {tasks} 个")
        if count >= STARTUP_WARN_THRESHOLD or (isinstance(tasks, int) and tasks >= 20):
            out.append(
                "开机自启偏多（" + "、".join(parts) + "）。这里只列出不动手——"
                "禁用在「任务管理器 → 启动应用」里做，比任何一键优化都安全。"
            )

    if report.in_use_dirs:
        out.append(
            "本次会话正在使用的目录（清理时建议保留）："
            + "、".join(report.in_use_dirs[:5])
        )
    return out


def render_markdown(report: HealthReport) -> str:
    """把体检报告渲染成 Markdown（可存盘 / 贴给别人看）。"""
    lines: list[str] = ["# 系统体检报告", ""]
    lines.append(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 管理员权限：{'是' if report.is_admin else '否（部分数据需提权）'}")
    lines.append(f"- 耗时：{report.elapsed_s} 秒")
    lines.append("")

    for sec in report.sections:
        flag = "" if sec.ok else "（未取到）"
        admin = " [需管理员]" if sec.need_admin else ""
        lines.append(f"## {sec.title}{flag}{admin}")
        if sec.note:
            lines.append(f"> {sec.note}")
        data = sec.data
        if sec.key == "resource" and sec.ok:
            lines.append(
                f"- CPU {data.get('cpu_percent')}% / 可用内存 {data.get('mem_available_gb')} GB"
                f" / 总内存 {data.get('mem_total_gb')} GB"
            )
            lines.append(f"- 磁盘队列 {data.get('disk_queue')} 繁忙度 {data.get('disk_busy_percent')}%")
            lines.append(f"- 系统 {data.get('os')}，上次开机 {data.get('last_boot')}"
                         f"（已运行 {data.get('uptime_hours')} 小时）")
            for d in data.get("drives", []):
                lines.append(
                    f"  - {d.get('mount')}：{d.get('free_gb')} GB 可用 / {d.get('total_gb')} GB"
                    f"（{d.get('used_percent')}% 已用"
                    + (f"，7 天变化 {d.get('trend_7d_gb')} GB" if d.get('trend_7d_gb') is not None else "")
                    + "）"
                )
        elif sec.key == "disk" and sec.ok:
            for d in data.get("disks", []):
                wear = d.get("wear_percent")
                wear_text = f"，磨损 {wear}%" if wear is not None else ""
                lines.append(
                    f"- {d.get('name')}（{d.get('media')}, {d.get('size_gb')} GB）"
                    f"状态 {d.get('health')}{wear_text}"
                )
        elif sec.key == "security" and sec.ok:
            for p in data.get("products", []):
                flag2 = "启用" if p.get("enabled") else "未启用"
                fresh = "库最新" if p.get("up_to_date") else "库待更新"
                lines.append(f"- {p.get('name')}：{flag2} / {fresh}")
            if data.get("signature_version"):
                lines.append(f"- Defender 特征库 {data.get('signature_version')}"
                             f"，引擎 {data.get('engine_version')}")
        elif sec.key == "processes" and sec.ok:
            lines.append("| 进程 | PID | 内存 | 启动时间 |")
            lines.append("|---|---|---|---|")
            for p in data.get("processes", []):
                mark = "（近期启动）" if p.get("recent") else ""
                lines.append(
                    f"| {p.get('name')} | {p.get('pid')} | {p.get('mem_mb')} MB | "
                    f"{p.get('started') or '-'}{mark} |"
                )
        elif sec.key == "startup" and sec.ok:
            for item in data.get("items", []):
                lines.append(f"- {item.get('name')}：`{item.get('command')}`")
            lines.append(
                f"- 自动服务 {data.get('auto_services')} 个 / "
                f"登录触发计划任务 {data.get('logon_tasks')} 个"
            )
        elif sec.key == "power" and sec.ok:
            if data.get("battery_percent") is not None:
                lines.append(f"- 电池 {data.get('battery_percent')}%（{data.get('battery_status')}）")
            else:
                lines.append(f"- {data.get('battery_status')}")
            lines.append(f"- 上次开机 {data.get('last_boot')}，已运行 {data.get('uptime_hours')} 小时")
        elif sec.key == "thermal" and sec.ok:
            lines.append(f"- CPU 温度 {data.get('cpu_c')} ℃")
        lines.append("")

    if report.advice:
        lines.append("## 建议")
        for a in report.advice:
            lines.append(f"- {a}")
        lines.append("")
    return "\n".join(lines)