"""安全沙箱子系统（P3-8）。

提供:
- risk_scorer: 操作风险评分（low/medium/high/critical）
- executor: Windows Job Object 沙箱执行器（资源限制 + 网络隔离）
- file_guard: 文件快照/回滚保护
- audit: 沙箱操作审计日志

设计参考 Claude Code sandbox-adapter 架构:
- 命令包装: 高风险命令自动在沙箱内执行
- 文件系统限制: allowWrite/denyWrite 路径白名单
- 网络隔离: 可选阻断网络访问
- 自动放行: 沙箱开启后，中等风险命令可跳过用户确认
- 违规记录: 审计日志追踪所有沙箱操作
"""

from agent.core.sandbox.risk_scorer import RiskLevel, RiskScorer
from agent.core.sandbox.executor import SandboxExecutor, SandboxConfig
from agent.core.sandbox.file_guard import FileGuard
from agent.core.sandbox.audit import SandboxAuditor

__all__ = [
    "RiskLevel",
    "RiskScorer",
    "SandboxExecutor",
    "SandboxConfig",
    "FileGuard",
    "SandboxAuditor",
]
