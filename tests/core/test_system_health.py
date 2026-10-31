"""体检聚合与杀软编排：productState 解码、提权降级、报告渲染。

## 这组测试保护的核心

2026-10-31 实测：本机 Defender 被「电脑管家系统防护」接管，``Get-MpComputerStatus``
照常返回但字段全是关闭态。**只看它就会对装了第三方杀软的用户报「你没有实时防护」**
—— 那是彻头彻尾的误报。所以这里钉住两条：

1. ``productState`` 位解码对实测值必须判对（266240=启用 / 393472=未启用）；
2. 有第三方接管时，结论必须是「由 X 接管」而不是「没有防护」。

PowerShell 一律不打真命令，全部 monkeypatch。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.core.maintenance import defender, health, psrun


# ---------------------------------------------------------------------------
# productState 位解码（用真机实测到的两个值）
# ---------------------------------------------------------------------------


def test_parse_product_state_enabled():
    """266240 = 0x041000：电脑管家实测值，启用位为 1、库标记为过期。"""
    enabled, uptodate = defender.parse_product_state(266240)
    assert enabled is True
    assert uptodate is False


def test_parse_product_state_disabled():
    """393472 = 0x060100：被接管的 Windows Defender 实测值，启用位为 0。"""
    enabled, _uptodate = defender.parse_product_state(393472)
    assert enabled is False


def test_parse_product_state_uptodate_bit():
    # 0x1010：启用 + 库最新
    enabled, uptodate = defender.parse_product_state(0x1010)
    assert enabled is True and uptodate is True


def test_parse_product_state_garbage():
    assert defender.parse_product_state("abc") == (False, False)


# ---------------------------------------------------------------------------
# Defender 年龄字段（溢出值必须变成 None）
# ---------------------------------------------------------------------------


def test_age_days_maps_overflow_to_none():
    """65535 / 4294967295 都是溢出哨兵值，表示「从未」。展示成 49 万天是假数据。"""
    assert defender._age_days(65535) is None
    assert defender._age_days(4294967295) is None
    assert defender._age_days(None) is None
    assert defender._age_days("x") is None


def test_age_days_normal():
    assert defender._age_days(0) == 0
    assert defender._age_days(3) == 3


# ---------------------------------------------------------------------------
# security_status：第三方接管不得误报
# ---------------------------------------------------------------------------


def _fake_ps(data):
    return psrun.PsResult(ok=True, data=data)


def test_security_reports_third_party_takeover(monkeypatch):
    """核心断言：不能把「被接管」报成「没防护」。"""
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps({
        "av": [
            {"displayName": "电脑管家系统防护", "productState": 266240,
             "pathToSignedProductExe": "D:\\x\\QQPCMgr.exe"},
            {"displayName": "Windows Defender", "productState": 393472,
             "pathToSignedProductExe": "windowsdefender://"},
        ],
        "avError": "",
        "mp": {"AMServiceEnabled": False, "AntivirusEnabled": False,
               "RealTimeProtectionEnabled": False, "SignatureAge": 65535,
               "SignatureVersion": "None", "QuickScanAge": 4294967295,
               "FullScanAge": 4294967295, "EngineVersion": "1.1", "ProductVersion": "4.18"},
        "mpError": "",
        "isAdmin": False,
    }))
    st = defender.security_status()
    assert st.any_enabled is True
    assert st.primary == "电脑管家系统防护"
    assert "接管" in st.note
    assert "没有实时防护" not in st.note
    # Defender 自己的字段仍然如实反映
    assert st.defender_active is False
    assert st.signature_age_days is None
    assert st.signature_version == ""


def test_security_reports_defender_active(monkeypatch):
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps({
        "av": [{"displayName": "Windows Defender", "productState": 0x061100}],
        "avError": "",
        "mp": {"AntivirusEnabled": True, "RealTimeProtectionEnabled": True,
               "SignatureAge": 1, "SignatureVersion": "1.2.3",
               "QuickScanAge": 2, "FullScanAge": 5, "EngineVersion": "1.1"},
        "mpError": "",
    }))
    st = defender.security_status()
    assert st.primary == "Windows Defender"
    assert st.realtime is True
    assert st.signature_version == "1.2.3"
    assert "没有实时防护" not in st.note


def test_security_reports_no_protection_only_when_真没有(monkeypatch):
    """只有所有产品都未启用时才允许说"没有实时防护"。"""
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps({
        "av": [{"displayName": "Windows Defender", "productState": 0x060100}],
        "avError": "",
        "mp": {"AntivirusEnabled": False, "RealTimeProtectionEnabled": False},
        "mpError": "",
    }))
    st = defender.security_status()
    assert st.any_enabled is False
    assert "没有实时防护" in st.note


def test_security_handles_ps_failure(monkeypatch):
    monkeypatch.setattr(
        psrun, "run_script",
        lambda *a, **k: psrun.PsResult(ok=False, error="拒绝访问", reason="denied"),
    )
    st = defender.security_status()
    assert st.checked is False
    assert st.error


# ---------------------------------------------------------------------------
# 威胁记录
# ---------------------------------------------------------------------------


def test_threat_status_mapping(monkeypatch):
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps({
        "threats": [{"threat_id": "1", "threat_name": "Trojan:Win32/Wacatac",
                     "status_id": 3, "detected_at": "2026-10-01 10:00",
                     "resource": "C:\\x\\a.exe"}],
        "error": "",
    }))
    rows, err = defender.recent_threats()
    assert err == ""
    assert rows[0]["status"] == "已隔离"
    assert "Wacatac" in rows[0]["name"]


def test_threats_unreadable_reports_reason(monkeypatch):
    """Defender 被接管时读不到威胁记录 —— 要说清楚，不能当成"很安全"。"""
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps(
        {"threats": None, "error": "服务未运行"}
    ))
    rows, err = defender.recent_threats()
    assert rows == []
    assert "不可读" in err


# ---------------------------------------------------------------------------
# 扫描编排
# ---------------------------------------------------------------------------


def test_scan_rejects_full_by_default(monkeypatch):
    job, err = defender.start_scan(kind="full", allow_full=False)
    assert job is None
    assert "全盘" in err


def test_scan_rejects_custom_without_path():
    job, err = defender.start_scan(kind="custom", path="")
    assert job is None
    assert "path" in err


def test_scan_rejects_unknown_kind():
    job, err = defender.start_scan(kind="turbo")
    assert job is None
    assert "未知" in err


def test_scan_status_unknown_job():
    data = defender.scan_status("scan-19000101-000000")
    assert data["state"] == "unknown"


# ---------------------------------------------------------------------------
# 体检聚合
# ---------------------------------------------------------------------------


def _fake_health(**over):
    data = {
        "isAdmin": False,
        "resource": {"cpuPercent": 21.5, "memTotalGB": 8.0, "memAvailMB": 500.0,
                     "diskQueue": 0.0, "diskBusy": 1.2, "uptimeHours": 100.0,
                     "lastBoot": "2026-10-27 09:00", "osCaption": "Windows 10 Pro"},
        "resourceError": "",
        "disks": [{"id": "0", "name": "WDC SSD", "media": "SSD",
                   "health": "Healthy", "opStatus": "OK", "sizeGB": 476.9}],
        "disksError": "",
        "smart": None,
        "smartError": "无法从客户端中访问 CIM 资源。",
        "processes": [{"name": "big.exe", "id": 1, "memMB": 1200.0, "cpuS": 5.0,
                       "start": ""}],
        "processesError": "",
        "startup": [{"Name": "app", "Command": "app.exe", "Location": "Run", "User": "me"}],
        "startupError": "",
        "autoServices": 85,
        "logonTasks": 23,
        "battery": {"percent": 100, "statusId": 2},
        "batteryError": "",
        "thermalC": None,
        "thermalError": "拒绝访问 ",
    }
    data.update(over)
    return data


def test_collect_builds_all_sections(monkeypatch):
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps(_fake_health()))
    monkeypatch.setattr(defender, "security_status",
                        lambda: defender.SecurityStatus(checked=True, any_enabled=True,
                                                        primary="X", note="ok"))
    rep = health.collect(timeout_s=5)
    keys = [s.key for s in rep.sections]
    assert keys == ["resource", "disk", "security", "processes", "startup", "power", "thermal"]


def test_smart_failure_marks_need_admin(monkeypatch):
    """SMART 要管理员，读不到要标出来而不是假装有数据。"""
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps(_fake_health()))
    monkeypatch.setattr(defender, "security_status",
                        lambda: defender.SecurityStatus(checked=True))
    rep = health.collect(timeout_s=5, include_security=False)
    disk = rep.section("disk")
    assert disk and disk.ok is True
    assert disk.need_admin is True
    assert "管理员" in disk.note


def test_thermal_failure_marks_need_admin(monkeypatch):
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps(_fake_health()))
    rep = health.collect(timeout_s=5, include_security=False)
    thermal = rep.section("thermal")
    assert thermal and thermal.ok is False
    assert thermal.need_admin is True


def test_process_recent_flag(monkeypatch):
    """启动时间在 2 小时内标 recent —— 用户能自己判断"这是我刚开的编译器"。"""
    import time as _t

    now = _t.strftime("%Y-%m-%d %H:%M")
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps(
        _fake_health(processes=[
            {"name": "new.exe", "id": 1, "memMB": 10.0, "cpuS": 0, "start": now},
            {"name": "old.exe", "id": 2, "memMB": 10.0, "cpuS": 0,
             "start": "2026-01-01 00:00"},
        ])
    ))
    rep = health.collect(timeout_s=5, include_security=False)
    procs = rep.section("processes").data["processes"]
    assert procs[0]["recent"] is True
    assert procs[1]["recent"] is False


def test_ps_total_failure_returns_explained_section(monkeypatch):
    """整份读不到时不能返回空报告——那会让上层误判"一切正常"。"""
    monkeypatch.setattr(
        psrun, "run_script",
        lambda *a, **k: psrun.PsResult(ok=False, error="没有 PowerShell", reason="missing"),
    )
    rep = health.collect(timeout_s=5)
    assert len(rep.sections) == 1
    assert rep.sections[0].ok is False
    assert "PowerShell" in rep.sections[0].note


def test_advice_disk_low_space(monkeypatch):
    """建议必须由数据推出，不是通用套话。"""
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps(_fake_health()))
    import agent.core.maintenance.scanner as sc

    monkeypatch.setattr(sc, "drive_infos", lambda: [
        sc.DriveInfo(mount="C:", total_gb=100.0, free_gb=5.0,
                     used_percent=95.0, trend_7d_gb=8.4)
    ])
    rep = health.collect(timeout_s=5, include_security=False)
    joined = "\n".join(rep.advice)
    assert "只剩 5.0 GB" in joined
    assert "7 天又涨了 8.4 GB" in joined


def test_render_markdown_contains_sections(monkeypatch):
    monkeypatch.setattr(psrun, "run_script", lambda *a, **k: _fake_ps(_fake_health()))
    monkeypatch.setattr(defender, "security_status",
                        lambda: defender.SecurityStatus(checked=True, any_enabled=True,
                                                        primary="X", note="ok"))
    md = health.render_markdown(health.collect(timeout_s=5))
    assert md.startswith("# 系统体检报告")
    assert "磁盘健康" in md
    assert "建议" in md


# ---------------------------------------------------------------------------
# psrun 的纯函数
# ---------------------------------------------------------------------------


def test_ps_quote_doubles_single_quote():
    """PowerShell 单引号串转义是「写两个」，不是反斜杠。"""
    assert psrun._ps_quote("C:\\a'b") == "'C:\\a''b'"


def test_classify_failure():
    assert psrun.classify_failure("拒绝访问 ") == "denied"
    assert psrun.classify_failure("Access is denied") == "denied"
    assert psrun.classify_failure("无法识别 cmdlet") == "missing"
    assert psrun.classify_failure("something odd") == "error"


def test_as_list_normalizes():
    """ConvertTo-Json 对单对象不产数组，直接迭代 dict 会拿到它的 key。"""
    assert psrun.as_list(None) == []
    assert psrun.as_list({"a": 1}) == [{"a": 1}]
    assert psrun.as_list([{"a": 1}, "junk", {"b": 2}]) == [{"a": 1}, {"b": 2}]