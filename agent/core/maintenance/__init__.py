"""系统维护层（清理 / 查毒 / 性能诊断）包门面。

只做再导出，不含逻辑——各子模块职责：

| 模块 | 职责 |
|---|---|
| ``rules``     | ``cleaners.toml`` 的解析、路径展开与白名单校验 |
| ``scanner``   | 目录体积统计（超时/体积上限/不跟随符号链接）与磁盘趋势 |
| ``cleaner``   | 隔离区、批次清单、可撤销删除与还原 |
| ``auto_clean``| 无人值守自动清理：只动规则里 auto_ok=true 的那几条 |
| ``psrun``     | PowerShell 执行通道（编码、JSON 深度、提权降级全在这里收口） |
| ``defender``  | 杀软编排：SecurityCenter2 权威判定 + Defender 扫描/特征库/威胁记录 |
| ``health``    | 体检聚合（资源/磁盘/进程/启动项/电源/温度）与 Markdown 报告 |

设计底线见 BLUEPRINT 6.1/6.2/6.4 与 PLAN-system-maintenance 第七条：
不自研查杀引擎、不自动杀进程、不碰白名单之外的任何文件。

@author aceFelix
"""

from __future__ import annotations

from agent.core.maintenance.cleaner import (
    QUARANTINE_ROOT,
    CleanResult,
    RestoreResult,
    clean,
    list_batches,
    purge_expired,
    restore,
)
from agent.core.maintenance.defender import (
    AvProduct,
    ScanJob,
    SecurityStatus,
    recent_threats,
    scan_status,
    security_status,
    start_scan,
    update_signatures,
)
from agent.core.maintenance.health import HealthReport, HealthSection, collect, render_markdown
from agent.core.maintenance.rules import (
    CleanerRule,
    RuleBook,
    load_rulebook,
)
from agent.core.maintenance.scanner import (
    DirEntrySize,
    DriveInfo,
    ScanItem,
    ScanResult,
    describe_commands,
    disk_trend_7d_gb,
    drive_infos,
    human_size,
    scan_all,
    top_dirs,
)
# 放在 cleaner/scanner 之后：auto_clean 内部要 ``from agent.core.maintenance import cleaner``，
# 顺序反了会拿到半初始化的包对象
from agent.core.maintenance.auto_clean import (
    AutoCleanResult,
    auto_rule_ids,
    explain_rules,
    is_due,
    run_auto_clean,
)

__all__ = [
    # rules
    "CleanerRule",
    "RuleBook",
    "load_rulebook",
    # scanner
    "DirEntrySize",
    "DriveInfo",
    "ScanItem",
    "ScanResult",
    "describe_commands",
    "disk_trend_7d_gb",
    "drive_infos",
    "human_size",
    "scan_all",
    "top_dirs",
    # cleaner
    "QUARANTINE_ROOT",
    "CleanResult",
    "RestoreResult",
    "clean",
    "list_batches",
    "purge_expired",
    "restore",
    # auto_clean
    "AutoCleanResult",
    "auto_rule_ids",
    "explain_rules",
    "is_due",
    "run_auto_clean",
    # defender
    "AvProduct",
    "ScanJob",
    "SecurityStatus",
    "recent_threats",
    "scan_status",
    "security_status",
    "start_scan",
    "update_signatures",
    # health
    "HealthReport",
    "HealthSection",
    "collect",
    "render_markdown",
]