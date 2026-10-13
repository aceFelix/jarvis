"""授权租约（lease）—— 有限期、有限范围的免打断授权（W1·阶段 8）。

"减少打断的主要手段"：用户一次性说"接下来 2 小时允许动 ~/reports"，
此后命中租约的调用直接执行，不再逐次弹确认；`expires_at` 到期自动失效，
行为立刻回到管线的 ASK（无需重启，读取即校验）。

落盘 `~/.jarvis/leases.json`：
    [{"id":"…","tools":["FileWrite"],"path_within":["~/work/reports/**"],
      "granted_at":"…","expires_at":"…","granted_by":"user","note":"…"}]

安全边界：租约只能放宽 ASK，永远放宽不了 DENY（路径守护/危险命令/
策略 deny）——合并逻辑统一走 gateway，这里只管签发与匹配。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent.core.policy.patterns import path_matches

_DEFAULT_PATH = Path.home() / ".jarvis" / "leases.json"
_DEFAULT_TTL_MIN = 120  # 缺省 2 小时：租约是"短时间免打断"，不是永久规则


@dataclass
class Lease:
    """一条授权租约。"""

    id: str
    tools: list[str]
    path_within: list[str] = field(default_factory=list)
    granted_at: str = ""
    expires_at: str = ""
    granted_by: str = "user"
    note: str = ""

    def is_active(self, now: datetime | None = None) -> bool:
        """是否在有效期内（读取即校验，过期自动失效）。"""
        now = now or datetime.now()
        try:
            return now < datetime.fromisoformat(self.expires_at)
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "tools": self.tools, "path_within": self.path_within,
            "granted_at": self.granted_at, "expires_at": self.expires_at,
            "granted_by": self.granted_by, "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Lease":
        return cls(
            id=d.get("id", ""), tools=list(d.get("tools", [])),
            path_within=list(d.get("path_within", [])),
            granted_at=d.get("granted_at", ""), expires_at=d.get("expires_at", ""),
            granted_by=d.get("granted_by", "user"), note=d.get("note", ""),
        )


class LeaseStore:
    """租约仓库：签发 / 匹配 / 撤销 / 列表，JSON 落盘共享（跨进程可见）。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_PATH

    # ---- 持久化 ----

    def _load_raw(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, leases: list[Lease]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([l.to_dict() for l in leases], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # ---- 查询 ----

    def list_active(self) -> list[Lease]:
        """全部生效中的租约（过期项顺带清理落盘）。"""
        all_items = [Lease.from_dict(d) for d in self._load_raw()]
        active = [l for l in all_items if l.is_active()]
        if len(active) != len(all_items):
            self._save(active)  # lazy GC：读到过期就清掉
        return active

    def match(self, tool_name: str, target_path: str = "", workdir: str = "") -> Lease | None:
        """查找命中当前调用的生效租约；无则 None。

        匹配条件：工具名在租约 tools 内（支持 fnmatch 模式），且
        （租约未限定路径 或 目标路径命中 path_within）。无路径参数且
        租约限定了路径范围 → 不算命中（宁可多问一次）。
        """
        import fnmatch as _fm

        for lease in self.list_active():
            if not any(tool_name == t or _fm.fnmatchcase(tool_name, t) for t in lease.tools):
                continue
            if lease.path_within:
                if not target_path:
                    continue
                if not path_matches(lease.path_within, target_path, workdir):
                    continue
            return lease
        return None

    # ---- 变更 ----

    def grant(
        self,
        tools: list[str],
        path_within: list[str] | None = None,
        *,
        ttl_minutes: int = _DEFAULT_TTL_MIN,
        note: str = "",
    ) -> Lease:
        """签发一条新租约。返回 Lease（含 id），调用方负责展示给用户。"""
        now = datetime.now()
        lease = Lease(
            id=uuid.uuid4().hex[:8],
            tools=list(tools),
            path_within=list(path_within or []),
            granted_at=now.isoformat(timespec="seconds"),
            expires_at=(now + timedelta(minutes=max(1, int(ttl_minutes)))).isoformat(timespec="seconds"),
            granted_by="user",
            note=note,
        )
        items = self.list_active()
        items.append(lease)
        self._save(items)
        return lease

    def revoke(self, lease_id: str) -> bool:
        """按 id 撤销（一屏查看/一键撤销的入口）。返回是否删掉了。"""
        items = self.list_active()
        remain = [l for l in items if l.id != lease_id]
        if len(remain) == len(items):
            return False
        self._save(remain)
        return True


# ---- 进程内单例 ----

_store_singleton: LeaseStore | None = None


def get_lease_store(path: Path | str | None = None) -> LeaseStore:
    global _store_singleton
    if _store_singleton is None or path is not None:
        _store_singleton = LeaseStore(path)
    return _store_singleton
