"""多 Agent 团队系统 —— 管理团队生命周期、成员、状态持久化。

核心概念：
1. **Team**: 一个协作团队，1:1 对应一个 TaskList。包含成员列表、配置、邮件箱。
2. **TeamMember**: 队友身份——agentId(name@teamName)、角色、状态、模型。
3. **TeamFile**: 持久化到 ~/.jarvis/teams/{team_name}/config.json 的团队快照。
4. **Leader**: 团队领导者（"team-lead@teamName"），负责编排；is_teammate()=False。
5. **文件布局**:
   ~/.jarvis/
   ├── teams/{team_name}/
   │   ├── config.json          # TeamFile
   │   └── inboxes/
   │       ├── team-lead.json
   │       └── {member}.json

设计要点：
- 一个会话只能领导一个团队（与 Claude Code 一致）。
- Leader 的 agent_id 是确定性的（team-lead@teamName），不依赖会话 ID。
- 队友可以 in-process（asyncio）执行。
- 团队删除时检查活跃成员，阻止误删。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class TeamMember:
    """团队成员身份。

    Attributes:
        agent_id: 格式 "name@teamName"（确定性的唯一标识）。
        name: 显示名（"researcher", "coder" 等）。
        agent_type: 角色类型（explorer/researcher/coder/general）。
        model: 使用的模型名。None = 继承 leader。
        prompt: 初始任务描述。
        color: UI 颜色名（可选）。
        plan_mode_required: 是否需要 leader 审批计划。
        joined_at: 加入时间戳。
        cwd: 工作目录。
        is_active: 是否活跃。False=空闲/等待中，True/None=活跃。
        permission_mode: 权限模式。
        session_id: 关联的会话 ID（可选）。
    """
    agent_id: str
    name: str
    agent_type: Optional[str] = None
    model: Optional[str] = None
    prompt: str = ""
    color: Optional[str] = None
    plan_mode_required: bool = False
    joined_at: float = 0.0
    cwd: str = ""
    is_active: Optional[bool] = None
    permission_mode: str = "default"
    session_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = {
            "agentId": self.agent_id,
            "name": self.name,
            "joinedAt": self.joined_at,
            "cwd": self.cwd,
        }
        if self.agent_type:
            d["agentType"] = self.agent_type
        if self.model:
            d["model"] = self.model
        if self.prompt:
            d["prompt"] = self.prompt
        if self.color:
            d["color"] = self.color
        if self.plan_mode_required:
            d["planModeRequired"] = self.plan_mode_required
        if self.is_active is not None:
            d["isActive"] = self.is_active
        if self.permission_mode:
            d["mode"] = self.permission_mode
        if self.session_id:
            d["sessionId"] = self.session_id
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TeamMember:
        return cls(
            agent_id=d["agentId"],
            name=d["name"],
            agent_type=d.get("agentType"),
            model=d.get("model"),
            prompt=d.get("prompt", ""),
            color=d.get("color"),
            plan_mode_required=d.get("planModeRequired", False),
            joined_at=d.get("joinedAt", 0.0),
            cwd=d.get("cwd", ""),
            is_active=d.get("isActive"),
            permission_mode=d.get("mode", "default"),
            session_id=d.get("sessionId"),
        )


@dataclass
class TeamFile:
    """持久化到磁盘的团队快照。

    Attributes:
        name: 团队名（唯一标识）。
        description: 团队描述。
        created_at: 创建时间戳。
        lead_agent_id: 领导者的 agent_id（"team-lead@teamName"）。
        lead_session_id: leader 的会话 ID。
        members: 所有成员列表（含 leader）。
    """
    name: str
    created_at: float = 0.0
    lead_agent_id: str = ""
    lead_session_id: Optional[str] = None
    description: str = ""
    members: list[TeamMember] = field(default_factory=list)

    @property
    def non_lead_members(self) -> list[TeamMember]:
        """除 leader 外的所有成员。"""
        return [m for m in self.members if m.agent_id != self.lead_agent_id]

    @property
    def active_non_lead_members(self) -> list[TeamMember]:
        """除 leader 外仍活跃的成员。"""
        return [m for m in self.non_lead_members if m.is_active is not False]

    def get_member(self, name: str) -> Optional[TeamMember]:
        """按名查找成员。"""
        for m in self.members:
            if m.name == name:
                return m
        return None

    def get_member_by_id(self, agent_id: str) -> Optional[TeamMember]:
        """按 agent_id 查找成员。"""
        for m in self.members:
            if m.agent_id == agent_id:
                return m
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "createdAt": self.created_at,
            "leadAgentId": self.lead_agent_id,
            "leadSessionId": self.lead_session_id,
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, d: dict) -> TeamFile:
        members = [TeamMember.from_dict(m) for m in d.get("members", [])]
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            created_at=d.get("createdAt", 0.0),
            lead_agent_id=d.get("leadAgentId", ""),
            lead_session_id=d.get("leadSessionId"),
            members=members,
        )


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TEAM_LEAD_NAME = "team-lead"
"""团队领导者的固定名称。"""

AGENT_ID_SEPARATOR = "@"
"""agent_id 格式分隔符: name@teamName。"""


def format_agent_id(name: str, team_name: str) -> str:
    """构造确定性 agent_id: name@teamName。"""
    return f"{name}{AGENT_ID_SEPARATOR}{team_name}"


def sanitize_name(name: str) -> str:
    """清洗团队名：只保留字母、数字、连字符、下划线。"""
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", name)
    return sanitized.strip("-") or "team"


def is_agent_id(identifier: str) -> bool:
    """判断字符串是否为 agent_id 格式（name@team）。"""
    return AGENT_ID_SEPARATOR in identifier


def parse_agent_id(agent_id: str) -> tuple[str, str]:
    """解析 agent_id 为 (name, team_name)。"""
    parts = agent_id.split(AGENT_ID_SEPARATOR, 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------


def _jarvis_home() -> Path:
    """~/.jarvis 目录。"""
    home = Path(os.path.expanduser("~")) / ".jarvis"
    home.mkdir(parents=True, exist_ok=True)
    return home


def team_dir(team_name: str) -> Path:
    """团队配置目录路径。"""
    return _jarvis_home() / "teams" / sanitize_name(team_name)


def team_config_path(team_name: str) -> Path:
    """团队配置文件路径。"""
    return team_dir(team_name) / "config.json"


def team_inbox_dir(team_name: str) -> Path:
    """团队邮件箱目录路径。"""
    return team_dir(team_name) / "inboxes"


def team_task_dir(team_name: str) -> Path:
    """团队任务列表目录路径。"""
    return _jarvis_home() / "tasks" / sanitize_name(team_name)


# ---------------------------------------------------------------------------
# 团队管理 API
# ---------------------------------------------------------------------------


class TeamManager:
    """团队生命周期管理器。

    用法::

        mgr = TeamManager()
        team = mgr.create("my-project", lead_session_id="abc123")
        mgr.add_member("my-project", member)
        mgr.remove_member("my-project", "researcher")
        mgr.delete("my-project")
    """

    def __init__(self) -> None:
        self._active_team: Optional[str] = None  # 当前会话领导的团队名

    # ---- 创建/删除 ----

    def create(
        self,
        name: str,
        *,
        lead_session_id: Optional[str] = None,
        description: str = "",
        cwd: str = "",
        model: Optional[str] = None,
    ) -> TeamFile:
        """创建一个新团队。

        Args:
            name: 团队名。
            lead_session_id: leader 的会话 ID。
            description: 团队描述。
            cwd: 工作目录。
            model: leader 的模型。

        Returns:
            创建的 TeamFile。

        Raises:
            RuntimeError: 当前已领导另一个团队。
        """
        if self._active_team is not None:
            raise RuntimeError(
                f"当前已在领导团队 '{self._active_team}'，不能同时领导多个团队。"
                f"请先用 TeamDelete 解散当前团队。"
            )

        safe_name = sanitize_name(name)
        team_config = team_config_path(safe_name)

        # 检查是否已存在
        if team_config.exists():
            existing = self.load(safe_name)
            if existing is not None:
                raise RuntimeError(f"团队 '{safe_name}' 已存在")

        # 确保目录
        td = team_dir(safe_name)
        td.mkdir(parents=True, exist_ok=True)
        team_inbox_dir(safe_name).mkdir(parents=True, exist_ok=True)

        now = time.time()
        lead_id = format_agent_id(TEAM_LEAD_NAME, safe_name)

        leader = TeamMember(
            agent_id=lead_id,
            name=TEAM_LEAD_NAME,
            agent_type="general",
            model=model,
            joined_at=now,
            cwd=cwd,
            is_active=True,
            session_id=lead_session_id,
        )

        team = TeamFile(
            name=safe_name,
            created_at=now,
            lead_agent_id=lead_id,
            lead_session_id=lead_session_id,
            description=description,
            members=[leader],
        )

        self._save(team)
        self._active_team = safe_name
        return team

    def load(self, team_name: str) -> Optional[TeamFile]:
        """从磁盘加载团队配置。"""
        path = team_config_path(team_name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TeamFile.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save(self, team: TeamFile) -> None:
        """保存团队配置到磁盘。"""
        self._save(team)

    def _save(self, team: TeamFile) -> None:
        """内部保存（不触发 hook）。"""
        path = team_config_path(team.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 原子写入：先写临时文件再 rename
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(team.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    def delete(self, team_name: str) -> bool:
        """删除团队及其所有数据。

        前置条件：除 leader 外无活跃成员。

        Returns:
            True 删除成功，False 有活跃成员阻止删除。
        """
        team = self.load(team_name)
        if team is None:
            return True  # 不存在即成功

        active = team.active_non_lead_members
        if active:
            names = ", ".join(m.name for m in active)
            raise RuntimeError(
                f"团队 '{team_name}' 还有活跃成员 ({names})，不能删除。"
                f"请先用 SendMessage shutdown_request 让队友关闭。"
            )

        # 清理目录
        import shutil

        td = team_dir(team_name)
        if td.exists():
            shutil.rmtree(td)

        task_d = team_task_dir(team_name)
        if task_d.exists():
            shutil.rmtree(task_d)

        if self._active_team == sanitize_name(team_name):
            self._active_team = None

        return True

    def delete_if_empty(self, team_name: str) -> bool:
        """仅当团队无活跃非 leader 成员时删除。"""
        team = self.load(team_name)
        if team is None:
            return True
        if team.active_non_lead_members:
            return False
        return self.delete(team_name)

    # ---- 成员管理 ----

    def add_member(self, team_name: str, member: TeamMember) -> TeamFile:
        """向团队添加成员（含去重）。"""
        team = self.load(team_name)
        if team is None:
            raise RuntimeError(f"团队不存在: {team_name}")

        # 去重：同名覆盖
        existing = team.get_member(member.name)
        if existing is not None:
            team.members.remove(existing)

        team.members.append(member)
        self._save(team)
        return team

    def remove_member(self, team_name: str, name: str) -> Optional[TeamFile]:
        """从团队移除成员。不能移除 leader。"""
        team = self.load(team_name)
        if team is None:
            return None

        if format_agent_id(name, team_name) == team.lead_agent_id:
            raise RuntimeError("不能移除团队领导者")

        member = team.get_member(name)
        if member is not None:
            team.members.remove(member)
            self._save(team)

        return team

    def mark_member_active(self, team_name: str, name: str, active: bool) -> Optional[TeamFile]:
        """标记成员活跃/空闲状态。"""
        team = self.load(team_name)
        if team is None:
            return None

        member = team.get_member(name)
        if member is not None:
            member.is_active = None if active else False  # None=活跃, False=空闲
            self._save(team)

        return team

    # ---- 查询 ----

    @property
    def active_team(self) -> Optional[str]:
        """当前会话领导的团队名。"""
        return self._active_team

    def set_active_team(self, team_name: Optional[str]) -> None:
        """设置当前会话领导的团队名。"""
        self._active_team = team_name

    def list_teams(self) -> list[str]:
        """列出所有团队。"""
        teams_parent = _jarvis_home() / "teams"
        if not teams_parent.exists():
            return []
        return [
            d.name
            for d in teams_parent.iterdir()
            if d.is_dir() and (d / "config.json").exists()
        ]

    def is_teammate(self, agent_id: Optional[str] = None, name: Optional[str] = None) -> bool:
        """判断当前进程是否是某个团队的队友（非 leader）。

        如果当前没有活跃团队，返回 False。
        如果是 leader（或 agent_id 为空），返回 False。
        """
        if self._active_team is None:
            return False
        if agent_id:
            return agent_id != format_agent_id(TEAM_LEAD_NAME, self._active_team)
        if name:
            return name != TEAM_LEAD_NAME
        return False

    def get_leader_id(self, team_name: str) -> str:
        """获取团队的 leader agent_id。"""
        return format_agent_id(TEAM_LEAD_NAME, sanitize_name(team_name))


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

# 全局团队管理器实例
_team_manager: Optional[TeamManager] = None


def get_team_manager() -> TeamManager:
    """获取全局团队管理器单例。"""
    global _team_manager
    if _team_manager is None:
        _team_manager = TeamManager()
    return _team_manager
