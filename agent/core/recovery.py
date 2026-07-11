"""会话崩溃恢复 —— crossProjectResume 等价物。

对应原项目 utils/messages/crossProjectResume.ts + session 自动保存机制。

设计目标:
1. 每轮对话结束后自动写"恢复点"到 ~/.jarvis/sessions/.recovery.json
2. 启动时检查恢复点：
   - 不存在 → 正常启动
   - 存在且 clean_exit=True → 上次正常退出，删除恢复点
   - 存在且 clean_exit=False → 崩溃/异常退出，提示用户是否恢复
3. /exit 命令正常退出时标记 clean_exit=True
4. 恢复点包含: messages / workdir / model / provider / saved_at / dialog_count

恢复点文件结构:
{
    "clean_exit": false,
    "saved_at": 1783102338.5,
    "workdir": "/path/to/project",
    "model": "qwen-max",
    "provider": "openai",
    "dialog_count": 5,
    "messages": [...]  // Message 列表的 dict 形式
}
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.core.diag import diag_log, diag_warn
from agent.core.memory import sessions_dir, _message_to_dict, _message_from_dict
from agent.core.message import Message


# 恢复点认为"过期"的时长（秒）。超过此时长不再提示恢复。
_RECOVERY_TTL = 7 * 24 * 3600  # 7 天


def _recovery_path() -> Path:
    """恢复点文件路径: ~/.jarvis/sessions/.recovery.json"""
    return sessions_dir() / ".recovery.json"


@dataclass
class RecoveryPoint:
    """恢复点数据。"""
    clean_exit: bool
    saved_at: float
    workdir: str
    model: str
    provider: str
    dialog_count: int
    messages: list[Message]

    @property
    def age_seconds(self) -> float:
        """恢复点距今多少秒。"""
        return max(0.0, time.time() - self.saved_at)

    @property
    def is_expired(self) -> bool:
        """是否过期（超过 TTL）。"""
        return self.age_seconds > _RECOVERY_TTL


def save_recovery_point(
    messages: list[Message],
    *,
    workdir: str,
    model: str,
    provider: str,
    dialog_count: int,
) -> None:
    """写入恢复点。

    每轮对话结束后调用。失败静默（不影响主流程）。
    """
    try:
        data: dict[str, Any] = {
            "clean_exit": False,
            "saved_at": time.time(),
            "workdir": workdir,
            "model": model,
            "provider": provider,
            "dialog_count": dialog_count,
            "messages": [_message_to_dict(m) for m in messages],
        }
        path = _recovery_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        diag_warn("recovery", f"写恢复点失败: {e}")


def mark_clean_exit() -> None:
    """标记为正常退出（更新恢复点的 clean_exit 字段）。

    /exit 命令调用。下次启动时看到 clean_exit=True 就删除恢复点。
    """
    path = _recovery_path()
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["clean_exit"] = True
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        diag_warn("recovery", f"标记正常退出失败: {e}")


def clear_recovery_point() -> None:
    """删除恢复点。"""
    path = _recovery_path()
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        diag_warn("recovery", f"删除恢复点失败: {e}")


def load_recovery_point() -> RecoveryPoint | None:
    """读取恢复点。

    Returns:
        RecoveryPoint: 存在且未正常退出且未过期时返回
        None: 不存在 / 已正常退出 / 已过期 / 解析失败
    """
    path = _recovery_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        diag_warn("recovery", f"读取恢复点失败: {e}")
        return None

    # 正常退出 → 删除并返回 None
    if data.get("clean_exit", False):
        clear_recovery_point()
        return None

    try:
        messages = [_message_from_dict(m) for m in data.get("messages", [])]
        point = RecoveryPoint(
            clean_exit=False,
            saved_at=float(data.get("saved_at", 0)),
            workdir=str(data.get("workdir", "")),
            model=str(data.get("model", "")),
            provider=str(data.get("provider", "")),
            dialog_count=int(data.get("dialog_count", 0)),
            messages=messages,
        )
        if point.is_expired:
            # 过期则清理
            clear_recovery_point()
            diag_log("recovery", f"恢复点已过期（{int(point.age_seconds)}s），已清理")
            return None
        return point
    except Exception as e:
        diag_warn("recovery", f"解析恢复点失败: {e}")
        return None


def format_recovery_summary(point: RecoveryPoint) -> str:
    """生成恢复点的用户可见摘要（用于提示是否恢复）。"""
    from datetime import datetime
    ts = datetime.fromtimestamp(point.saved_at).strftime("%Y-%m-%d %H:%M:%S")
    age_min = int(point.age_seconds // 60)
    if age_min < 60:
        age_str = f"{age_min} 分钟前"
    elif age_min < 1440:
        age_str = f"{age_min // 60} 小时前"
    else:
        age_str = f"{age_min // 1440} 天前"

    # 取首条用户消息作为摘要
    first_user_msg = ""
    for m in point.messages:
        if m.role == "user":
            for b in m.content:
                if hasattr(b, "text") and b.text:
                    first_user_msg = b.text[:60]
                    break
            if first_user_msg:
                break

    return (
        f"上次会话（{ts}，{age_str}）"
        f" | {point.dialog_count} 轮对话"
        f" | {len(point.messages)} 条消息"
        f" | workdir: {point.workdir}"
        + (f"\n首条: {first_user_msg}..." if first_user_msg else "")
    )
