"""Agent 间消息邮箱系统 —— 基于文件的异步消息传递。

核心概念：
1. **文件邮箱**: 每个 agent 在 inboxes/{name}.json 有一个消息队列。
2. **消息类型**: 支持 10+ 种结构化消息（idle/plan/shutdown/permission/assignment）。
3. **消息投递**: 原子追加到收件人邮箱文件。
4. **消息读取**: 收件人轮询读取，标记已读。
5. **文件锁**: 保证并发安全。

消息类型参考 Claude Code：
- idle_notification: 队友空闲通知（每轮自动发送）
- permission_request: 权限请求
- plan_approval_request: 计划审批请求
- shutdown_request / shutdown_approved / shutdown_rejected: 关机协议
- task_assignment: 任务分配通知
- plain: 普通文本消息

路径：~/.jarvis/teams/{team_name}/inboxes/{agent_name}.json
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class TeammateMessage:
    """一条 Agent 间消息。

    Attributes:
        type: 消息类型标识。
        from_name: 发送者名。
        timestamp: ISO 时间戳。
        text: 消息文本（plain/broadcast 类型用）。
        summary: 简短摘要（5-10 词，UI 展示用）。
        read: 是否已读。
        request_id: 请求 ID（shutdown/plan 类型用）。
        approve: 批准标志（shutdown_response 类型用）。
        task_id: 关联任务 ID（task_assignment 类型用）。
        task_subject: 任务标题（task_assignment 类型用）。
        color: 发送者颜色（UI 用）。
        data: 自由扩展数据。
    """
    type: str = "plain"  # plain | broadcast | idle_notification | shutdown_request | ...
    from_name: str = ""
    timestamp: str = ""
    text: str = ""
    summary: str = ""
    read: bool = False
    request_id: str = ""
    approve: Optional[bool] = None
    task_id: str = ""
    task_subject: str = ""
    color: Optional[str] = None
    data: Optional[dict] = None

    def to_dict(self) -> dict:
        d: dict = {
            "type": self.type,
            "from": self.from_name,
            "timestamp": self.timestamp or _now_iso(),
            "text": self.text,
            "read": self.read,
        }
        if self.summary:
            d["summary"] = self.summary
        if self.request_id:
            d["requestId"] = self.request_id
        if self.approve is not None:
            d["approve"] = self.approve
        if self.task_id:
            d["taskId"] = self.task_id
        if self.task_subject:
            d["taskSubject"] = self.task_subject
        if self.color:
            d["color"] = self.color
        if self.data:
            d["data"] = self.data
        return d

    @classmethod
    def from_dict(cls, d: dict) -> TeammateMessage:
        return cls(
            type=d.get("type", "plain"),
            from_name=d.get("from", ""),
            timestamp=d.get("timestamp", ""),
            text=d.get("text", ""),
            summary=d.get("summary", ""),
            read=d.get("read", False),
            request_id=d.get("requestId", ""),
            approve=d.get("approve"),
            task_id=d.get("taskId", ""),
            task_subject=d.get("taskSubject", ""),
            color=d.get("color"),
            data=d.get("data"),
        )


# ---------------------------------------------------------------------------
# 消息工厂
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def make_idle_notification(
    from_name: str,
    summary: str = "",
    completed_task_id: str = "",
    color: Optional[str] = None,
) -> TeammateMessage:
    """构造空闲通知消息。"""
    return TeammateMessage(
        type="idle_notification",
        from_name=from_name,
        timestamp=_now_iso(),
        summary=summary,
        task_id=completed_task_id,
        color=color,
    )


def make_plain_message(
    from_name: str,
    text: str,
    summary: str = "",
    color: Optional[str] = None,
) -> TeammateMessage:
    """构造普通文本消息。"""
    return TeammateMessage(
        type="plain",
        from_name=from_name,
        timestamp=_now_iso(),
        text=text,
        summary=summary or (text[:50] + "..." if len(text) > 50 else text),
        color=color,
    )


def make_broadcast(
    from_name: str,
    text: str,
    summary: str = "",
) -> TeammateMessage:
    """构造广播消息。"""
    return TeammateMessage(
        type="broadcast",
        from_name=from_name,
        timestamp=_now_iso(),
        text=text,
        summary=summary,
    )


def make_shutdown_request(
    from_name: str,
    request_id: str = "",
    reason: str = "",
) -> TeammateMessage:
    """构造关机请求消息。"""
    return TeammateMessage(
        type="shutdown_request",
        from_name=from_name,
        timestamp=_now_iso(),
        text=reason,
        request_id=request_id or _generate_request_id(),
    )


def make_shutdown_response(
    from_name: str,
    request_id: str,
    approve: bool,
    reason: str = "",
) -> TeammateMessage:
    """构造关机响应消息。"""
    return TeammateMessage(
        type="shutdown_response",
        from_name=from_name,
        timestamp=_now_iso(),
        text=reason,
        request_id=request_id,
        approve=approve,
    )


def make_plan_approval_request(
    from_name: str,
    plan_text: str,
    request_id: str = "",
) -> TeammateMessage:
    """构造计划审批请求消息。"""
    return TeammateMessage(
        type="plan_approval_request",
        from_name=from_name,
        timestamp=_now_iso(),
        text=plan_text,
        request_id=request_id or _generate_request_id(),
    )


def make_plan_approval_response(
    from_name: str,
    request_id: str,
    approve: bool,
    feedback: str = "",
) -> TeammateMessage:
    """构造计划审批响应消息。"""
    return TeammateMessage(
        type="plan_approval_response",
        from_name=from_name,
        timestamp=_now_iso(),
        text=feedback,
        request_id=request_id,
        approve=approve,
    )


def make_task_assignment(
    from_name: str,
    task_id: str,
    task_subject: str,
    task_description: str = "",
) -> TeammateMessage:
    """构造任务分配通知消息。"""
    return TeammateMessage(
        type="task_assignment",
        from_name=from_name,
        timestamp=_now_iso(),
        text=task_description,
        task_id=task_id,
        task_subject=task_subject,
    )


def _generate_request_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------


def _mailbox_path(name: str, team_name: str) -> Path:
    """某个 agent 的邮箱文件路径。"""
    from .team import sanitize_name, team_inbox_dir

    inbox_dir = team_inbox_dir(sanitize_name(team_name))
    inbox_dir.mkdir(parents=True, exist_ok=True)
    return inbox_dir / f"{name}.json"


def _mailbox_lock_path(name: str, team_name: str) -> Path:
    """邮箱锁文件路径。"""
    return _mailbox_path(name, team_name).with_suffix(".json.lock")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def write_mailbox(
    recipient: str,
    message: TeammateMessage,
    team_name: str,
) -> None:
    """向指定收件人的邮箱追加一条消息（文件锁 + 原子写入）。

    Args:
        recipient: 收件人名（如 "researcher", "team-lead"）。
        message: 要投递的消息。
        team_name: 团队名。

    线程/协程安全：使用文件锁 + 原子 rename。
    """
    import os as _os

    path = _mailbox_path(recipient, team_name)
    lock_path = _mailbox_lock_path(recipient, team_name)

    path.parent.mkdir(parents=True, exist_ok=True)

    # 自旋文件锁
    max_retries = 30
    for attempt in range(max_retries + 1):
        try:
            fd = _os.open(
                str(lock_path),
                _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY,
                0o666,
            )
            _os.close(fd)
            break
        except FileExistsError:
            if attempt >= max_retries:
                raise TimeoutError(f"邮箱 {recipient} 锁定超时")
            time.sleep(min(0.005 * (2 ** attempt), 0.1))

    try:
        # 读取现有消息
        messages: list[dict] = []
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    messages = json.loads(content)
            except json.JSONDecodeError:
                messages = []

        # 追加
        msg_dict = message.to_dict()
        msg_dict["read"] = False
        messages.append(msg_dict)

        # 原子写入
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)

    finally:
        try:
            _os.unlink(str(lock_path))
        except FileNotFoundError:
            pass


def broadcast_mailbox(
    sender: str,
    message: TeammateMessage,
    team_name: str,
    *,
    exclude: Optional[list[str]] = None,
) -> None:
    """向团队所有成员（除 exclude）广播消息。

    Args:
        sender: 发送者名。
        message: 消息内容。
        team_name: 团队名。
        exclude: 排除的成员名列表（如排除自己）。
    """
    from .team import get_team_manager

    mgr = get_team_manager()
    team = mgr.load(team_name)
    if team is None:
        return

    exclude_set = set(exclude or []) | {sender}
    msg = TeammateMessage(
        type=message.type,
        from_name=sender,
        timestamp=message.timestamp or _now_iso(),
        text=message.text,
        summary=message.summary,
        request_id=message.request_id,
    )

    for member in team.members:
        if member.name in exclude_set:
            continue
        write_mailbox(member.name, msg, team_name)


def read_mailbox(
    name: str,
    team_name: str,
    *,
    mark_read: bool = True,
    unread_only: bool = False,
) -> list[TeammateMessage]:
    """读取指定 agent 的邮箱消息。

    Args:
        name: agent 名。
        team_name: 团队名。
        mark_read: 是否在读后标记已读。
        unread_only: 是否只返回未读消息。

    Returns:
        消息列表（按时间先后）。
    """
    import os as _os

    path = _mailbox_path(name, team_name)
    if not path.exists():
        return []

    lock_path = _mailbox_lock_path(name, team_name)

    # 自旋锁
    max_retries = 10
    for attempt in range(max_retries + 1):
        try:
            fd = _os.open(
                str(lock_path),
                _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY,
                0o666,
            )
            _os.close(fd)
            break
        except FileExistsError:
            if attempt >= max_retries:
                raise TimeoutError(f"邮箱 {name} 读取锁定超时")
            time.sleep(min(0.005 * (2 ** attempt), 0.05))

    try:
        try:
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                return []
            raw_messages: list[dict] = json.loads(content)
        except json.JSONDecodeError:
            return []

        modified = False
        messages: list[TeammateMessage] = []

        for raw in raw_messages:
            msg = TeammateMessage.from_dict(raw)
            if unread_only and msg.read:
                continue
            messages.append(msg)
            # 标记已读
            if mark_read and not msg.read:
                raw["read"] = True
                modified = True

        if modified:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(raw_messages, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)

        return messages

    finally:
        try:
            _os.unlink(str(lock_path))
        except FileNotFoundError:
            pass


def has_unread(name: str, team_name: str) -> bool:
    """检查是否有未读消息。"""
    import os as _os

    path = _mailbox_path(name, team_name)
    if not path.exists():
        return False

    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return False
        raw_messages: list[dict] = json.loads(content)
    except json.JSONDecodeError:
        return False

    return any(not m.get("read", False) for m in raw_messages)


def clear_mailbox(name: str, team_name: str) -> None:
    """清空指定 agent 的邮箱。"""
    path = _mailbox_path(name, team_name)
    path.write_text("[]", encoding="utf-8")
