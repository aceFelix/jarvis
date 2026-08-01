"""记忆持久化（Memory Persistence）。

让贾维斯"记得"过去——两个层面:

1. **会话存盘/恢复**（短期记忆）
   - 对话历史存成 JSON 文件，下次启动可恢复，继续之前的对话
   - 存储位置: ~/.jarvis/sessions/<name>.json
   - 支持命名保存 / 列出 / 加载 / 删除

2. **长期记忆**（跨会话笔记）
   - ~/.jarvis/MEMORY.md — 用户级，所有项目共享的偏好/习惯
   - <workdir>/.jarvis/MEMORY.md — 项目级，当前工作目录专属笔记
   - 启动时注入 system prompt，让模型知道"之前学到的"
   - 用户可通过 /memory 命令查看/编辑

致敬 WorkBuddy 的三层记忆机制（cloud profile / user-level / workspace），
但大幅精简: 不做自动学习（用户手动维护 MEMORY.md），不做云同步。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)


# ---- 路径约定 ----

def user_jarvis_dir() -> Path:
    """~/.jarvis/ 用户级配置目录。"""
    return Path.home() / ".jarvis"


def sessions_dir() -> Path:
    """会话存盘目录: ~/.jarvis/sessions/"""
    d = user_jarvis_dir() / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_memory_path() -> Path:
    """用户级长期记忆: ~/.jarvis/MEMORY.md"""
    return user_jarvis_dir() / "MEMORY.md"


def project_memory_path(workdir: str) -> Path:
    """项目级长期记忆: <workdir>/.jarvis/MEMORY.md"""
    return Path(workdir) / ".jarvis" / "MEMORY.md"


# ---- 消息序列化 ----

def _block_to_dict(block: Any) -> dict[str, Any]:
    """把 content block 序列化成 dict。图片块的 data 保留（base64 文本）。"""
    d: dict[str, Any] = {"type": block.type}
    if isinstance(block, TextContent):
        d["text"] = block.text
    elif isinstance(block, ToolUseContent):
        d["id"] = block.id
        d["name"] = block.name
        d["input"] = block.input
    elif isinstance(block, ToolResultContent):
        d["tool_use_id"] = block.tool_use_id
        d["content"] = block.content
        d["is_error"] = block.is_error
        # 图片: 存 data + media_type
        if block.images:
            d["images"] = [
                {"data": img.data, "media_type": img.media_type}
                for img in block.images
            ]
    elif isinstance(block, ImageContent):
        d["data"] = block.data
        d["media_type"] = block.media_type
    return d


def _block_from_dict(d: dict[str, Any]) -> Any:
    """从 dict 反序列化 content block。"""
    t = d.get("type", "text")
    if t == "text":
        return TextContent(text=d.get("text", ""))
    if t == "tool_use":
        return ToolUseContent(
            id=d.get("id", ""),
            name=d.get("name", ""),
            input=d.get("input", {}),
        )
    if t == "tool_result":
        images = []
        for img_d in d.get("images", []):
            images.append(ImageContent(
                data=img_d["data"], media_type=img_d.get("media_type", "image/png")
            ))
        return ToolResultContent(
            tool_use_id=d.get("tool_use_id", ""),
            content=d.get("content", ""),
            is_error=d.get("is_error", False),
            images=images,
        )
    if t == "image":
        return ImageContent(
            data=d.get("data", ""),
            media_type=d.get("media_type", "image/png"),
        )
    # 未知类型: 降级为空文本
    return TextContent(text="")


def _message_to_dict(msg: Message) -> dict[str, Any]:
    return {
        "role": msg.role,
        "content": [_block_to_dict(b) for b in msg.content],
        "id": msg.id,
        "timestamp": msg.timestamp,
    }


def _message_from_dict(d: dict[str, Any]) -> Message:
    blocks = [_block_from_dict(b) for b in d.get("content", [])]
    return Message(
        role=d.get("role", "user"),
        content=blocks,
        id=d.get("id", ""),
        timestamp=d.get("timestamp", time.time()),
    )


# ---- 会话存盘 ----

@dataclass
class SessionMeta:
    """会话元信息（存在 JSON 文件头部）。"""
    name: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    message_count: int = 0
    workdir: str = ""
    model: str = ""
    provider: str = ""


@dataclass
class SessionData:
    """完整会话数据。"""
    meta: SessionMeta
    messages: list[Message]


def _session_path(name: str) -> Path:
    """会话文件路径: ~/.jarvis/sessions/<name>.json"""
    # 防止路径穿越
    safe_name = "".join(c for c in name if c.isalnum() or c in "-_") or "default"
    return sessions_dir() / f"{safe_name}.json"


def save_session(
    name: str,
    messages: list[Message],
    *,
    workdir: str = "",
    model: str = "",
    provider: str = "",
) -> Path:
    """保存会话到 JSON 文件。返回文件路径。

    如果同名会话已存在，更新它（updated_at 刷新）。
    """
    path = _session_path(name)
    now = time.time()

    # 若已存在，保留 created_at
    created_at = now
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            created_at = old.get("meta", {}).get("created_at", now)
        except Exception:
            pass

    meta = SessionMeta(
        name=name,
        created_at=created_at,
        updated_at=now,
        message_count=len(messages),
        workdir=workdir,
        model=model,
        provider=provider,
    )

    data = {
        "meta": asdict(meta),
        "messages": [_message_to_dict(m) for m in messages],
    }
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_session(name: str) -> SessionData | None:
    """加载会话。不存在返回 None。"""
    path = _session_path(name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    meta_d = data.get("meta", {})
    meta = SessionMeta(
        name=meta_d.get("name", name),
        created_at=meta_d.get("created_at", 0),
        updated_at=meta_d.get("updated_at", 0),
        message_count=meta_d.get("message_count", 0),
        workdir=meta_d.get("workdir", ""),
        model=meta_d.get("model", ""),
        provider=meta_d.get("provider", ""),
    )
    messages = [_message_from_dict(m) for m in data.get("messages", [])]
    return SessionData(meta=meta, messages=messages)


def list_sessions() -> list[SessionMeta]:
    """列出所有已保存会话，按更新时间倒序。"""
    sessions: list[SessionMeta] = []
    for path in sessions_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            meta_d = data.get("meta", {})
            sessions.append(SessionMeta(
                name=meta_d.get("name", path.stem),
                created_at=meta_d.get("created_at", 0),
                updated_at=meta_d.get("updated_at", 0),
                message_count=meta_d.get("message_count", 0),
                workdir=meta_d.get("workdir", ""),
                model=meta_d.get("model", ""),
                provider=meta_d.get("provider", ""),
            ))
        except Exception:
            continue
    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return sessions


def delete_session(name: str) -> bool:
    """删除会话。返回是否删除成功。"""
    path = _session_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


def latest_session_name() -> str | None:
    """返回最近更新的会话名（用于自动恢复）。无会话返回 None。"""
    sessions = list_sessions()
    return sessions[0].name if sessions else None


# ---- 长期记忆 ----

def load_long_term_memory(workdir: str) -> str:
    """加载长期记忆，拼接成一段文本。

    优先级: 项目级 > 用户级（项目级更具体，放后面覆盖）。
    若都不存在返回空字符串。
    """
    parts: list[str] = []

    user_mem = user_memory_path()
    if user_mem.exists():
        try:
            content = user_mem.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"## 用户级记忆（~/.jarvis/MEMORY.md）\n\n{content}")
        except Exception:
            pass

    proj_mem = project_memory_path(workdir)
    if proj_mem.exists():
        try:
            content = proj_mem.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"## 项目级记忆（{proj_mem}）\n\n{content}")
        except Exception:
            pass

    if not parts:
        return ""

    return "# 长期记忆\n\n以下是你从过往交互中学到的、需要长期记住的信息:\n\n" + \
           "\n\n".join(parts)


def memory_section(workdir: str) -> str:
    """长期记忆段落（注入 system prompt 用）。无记忆返回空字符串。"""
    mem = load_long_term_memory(workdir)
    return mem + "\n" if mem else ""


def get_memory_files(workdir: str) -> dict[str, Path | None]:
    """返回长期记忆文件路径（供 /memory 命令展示）。"""
    return {
        "user": user_memory_path(),
        "project": project_memory_path(workdir),
    }
