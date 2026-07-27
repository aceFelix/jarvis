"""TeammateRunner 进程内注册表。

用于 SubagentTool 创建后台 teammate 后，TaskStopTool 等能按名字找到对应 runner。
注意：当前为 in-process 实现，leader 进程重启后注册表清空；
teammate 的实际运行状态仍以 TeamFile / mailbox 为准。
"""

from __future__ import annotations

from typing import Any, Optional


class TeammateRegistry:
    """按 (team_name, agent_name) 索引的 TeammateRunner 弱引用注册表。"""

    def __init__(self) -> None:
        self._runners: dict[tuple[str, str], Any] = {}

    def register(self, team_name: str, agent_name: str, runner: Any) -> None:
        """注册一个 teammate runner。"""
        self._runners[(team_name, agent_name)] = runner

    def unregister(self, team_name: str, agent_name: str) -> None:
        """注销 teammate runner。"""
        self._runners.pop((team_name, agent_name), None)

    def get(self, team_name: str, agent_name: str) -> Optional[Any]:
        """按团队名和队友名获取 runner。"""
        return self._runners.get((team_name, agent_name))

    def list_for_team(self, team_name: str) -> list[str]:
        """列出指定团队下所有已注册的队友名。"""
        return [
            name for (t_name, name) in self._runners.keys()
            if t_name == team_name
        ]


# 全局注册表实例
_global_registry: Optional[TeammateRegistry] = None


def get_teammate_registry() -> TeammateRegistry:
    """获取全局 teammate 注册表。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = TeammateRegistry()
    return _global_registry
