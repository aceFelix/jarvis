"""能力自检层：让 jarvis 知道「自己能做到什么、缺什么、怎么补」。

三层结构与各自边界（设计依据见 docs/plans/capability-selfcheck.md）：

- `registry`：声明。能力由哪些前置条件构成、能否降级、允许哪些修复动作。
- `probe` / `smoke`：探测。静态档（包/二进制/数据资产/凭据/平台）启动即跑；
  冒烟档（真起一次浏览器、真截一帧屏）只在 `deep_check` 下跑，不进启动路径。
- `report`：呈现。`/health caps` 的人读表、Markdown 报告与 `--json` 结构。
- `repair`：自我修复（S4）。读与写分开，是为了让只读这条链先被验收，再放开写权限。

核心纪律：**import 成功不等于能力可用**，以及**探针挂了不等于东西没有**。

本包入口只重导出「声明 + 探测」两条链；`report` 与 `smoke` 走子模块入口
（`from agent.capabilities.report import render_caps`），避免把与呈现相关的
名字混进与判定相关的名字里——调用前门读的是判定，命令面读的是呈现。

@author aceFelix
"""

from __future__ import annotations

from agent.capabilities.probe import (
    CACHE_TTL_S,
    aggregate_state,
    build_capability_prompt_block,
    check,
    check_all,
    deep_check,
    deep_check_all,
    platform_name,
    registry_snapshot,
    report_for_tool,
    reset_cache,
    selfcheck_blockers,
    smoke_requirements,
    tool_block_reason,
    tool_status_note,
    usable,
)
from agent.capabilities.registry import (
    CAPABILITY_REGISTRY,
    DEPENDENCY_FREE_TOOLS,
    AssetReq,
    BinaryReq,
    CapabilityMeta,
    CapabilityReport,
    CapState,
    PlatformReq,
    PythonReq,
    ReqState,
    RequirementStatus,
    SecretReq,
    all_declared_tools,
    capabilities_blocking_names,
    capability_for_tool,
    extras_for_group,
    get_meta,
)

# 导出按 RUF022 要求的全局序（常量 → 类 → 函数），不分层写注释，否则会被判未排序。
__all__ = [
    "CACHE_TTL_S",
    "CAPABILITY_REGISTRY",
    "DEPENDENCY_FREE_TOOLS",
    "AssetReq",
    "BinaryReq",
    "CapState",
    "CapabilityMeta",
    "CapabilityReport",
    "PlatformReq",
    "PythonReq",
    "ReqState",
    "RequirementStatus",
    "SecretReq",
    "aggregate_state",
    "all_declared_tools",
    "build_capability_prompt_block",
    "capabilities_blocking_names",
    "capability_for_tool",
    "check",
    "check_all",
    "deep_check",
    "deep_check_all",
    "extras_for_group",
    "get_meta",
    "platform_name",
    "registry_snapshot",
    "report_for_tool",
    "reset_cache",
    "selfcheck_blockers",
    "smoke_requirements",
    "tool_block_reason",
    "tool_status_note",
    "usable",
]
