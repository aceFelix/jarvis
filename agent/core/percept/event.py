"""感知事件模型 —— W2 事件总线的唯一数据契约（阶段 9）。

背景（aceFelix）：W1 建好的租约/预算/away 闸门只管"播报侧"，而播报的
内容源只有 Scheduler 到点这一类"时钟事件"。W2 要把"工作现场"（前台窗口、
目录变化、仓库构建、来信）也变成可裁的输入，所有源必须说同一个结构，
否则 W1 闸门就得为每个源特判一次——那正是本文档要避免的平行管线。

设计取舍：
- **payload 只放事实，不放文案**：每个源都填 ``payload["summary"]``（一句
  中文事实描述），总线与 W1 用它拼模板文本，**不为单条事件调 LLM**
  （成本控制硬约束：只有过 salience 门槛的事件才可能在最后生成一次话术）。
- **dedup_key 可选**：不填时按 ``source + kind + payload 指纹`` 自动生成，
  这样"同一个构建失败重复上报"天然命中去重，源不必自己记账。
- **salience_hint 是初判不是裁决**：源只给"这类事件通常多重要"的先验，
  最终能不能打扰用户由 W1 ``NotifyBudget.salience()`` 决定（权重×紧迫×
  相关性×历史接受率）。总线层的 floor 只用来丢掉明显无价值的噪声。

@author aceFelix
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# 事件源名：配置键、逐源开关、统计维度、订阅过滤全用这些字面量，
# 避免散落的魔法串（新增一源只需在此登记 + 写一个 source_*.py）。
SOURCE_WINDOW = "window"        # 前台窗口（进程名/标题）
SOURCE_FILE = "file"            # 白名单目录变化
SOURCE_VCS = "vcs"              # 仓库状态 / 构建与 CI 结果
SOURCE_MAIL = "mail"            # 邮件信封头（只读）
SOURCE_CALENDAR = "calendar"    # 既有日历源收编
SOURCE_SYSTEM = "system"        # 既有 SystemMonitor 收编
SOURCE_SCHEDULE = "schedule"    # 既有 Scheduler 降级为事件源
SOURCE_VISION = "vision"        # 既有 VisionWatcher 收编（手势/人脸在场）

#: 全部已知源名（配置与面板按此渲染，未登记的源也可动态注册）。
ALL_SOURCES: tuple[str, ...] = (
    SOURCE_WINDOW, SOURCE_FILE, SOURCE_VCS, SOURCE_MAIL,
    SOURCE_CALENDAR, SOURCE_SYSTEM, SOURCE_SCHEDULE, SOURCE_VISION,
)

#: 收编型源（W2 之前就存在，总线只是给它们一个统一出口）。
LEGACY_SOURCES: frozenset[str] = frozenset(
    {SOURCE_CALENDAR, SOURCE_SYSTEM, SOURCE_SCHEDULE, SOURCE_VISION}
)

# payload 单值长度上限（字符）：窗口标题、构建日志尾行之类都可能很长，
# 总线要常驻内存 + 落盘，必须给硬上限，超限截断而不是拒绝事件。
PAYLOAD_VALUE_LIMIT = 240


def _now_iso() -> str:
    """本地时区 ISO 时间戳（秒级），与 policy 包口径一致。"""
    return datetime.now().isoformat(timespec="seconds")


def _fingerprint(payload: dict[str, Any]) -> str:
    """payload 的稳定短指纹（键排序后 sha256 取前 12 位）。

    用于自动生成 dedup_key：同一件事两次上报 → 指纹相同 → 命中去重。
    排序是关键，否则 ``{"a":1,"b":2}`` 与 ``{"b":2,"a":1}`` 会被当成两件事。
    """
    try:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = str(payload)
    return hashlib.sha256(canonical.encode("utf-8", errors="replace")).hexdigest()[:12]


def clamp_payload(payload: dict[str, Any], limit: int = PAYLOAD_VALUE_LIMIT) -> dict[str, Any]:
    """逐值截断 payload（总线投递前调用，约束常驻内存与落盘体积）。

    只截字符串；数字/布尔/嵌套结构原样保留（嵌套里的字符串由源自己负责，
    总线不递归——保持这一层足够薄，避免变成第二个序列化器）。
    """
    out: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[:limit] + "…"
        else:
            out[key] = value
    return out


@dataclass
class PerceptEvent:
    """一次感知事件。

    Attributes:
        source: 源名（见 ``ALL_SOURCES``），W1 用它查来源权重。
        kind: 源内事件类型，自由串（如 ``focus_changed`` / ``dir_grew`` /
            ``build_failed`` / ``mail_received`` / ``schedule_fired``）。
        payload: 事实载荷，约定含 ``summary`` 一句话中文描述。
        ts: 事件产生时间（本地 ISO）。同一件事反复上报时以首次为准，
            去重窗口内合并的计数不刷新时间戳（由总线记账）。
        salience_hint: 源给出的重要度先验（0~1），最终由 W1 评分覆盖。
        dedup_key: 显式去重键；留空则按 source+kind+payload 指纹自动生成。
        merged: 该事件在去重窗口内合并掉的重复条数（默认 1，总线不改它，
            计数记在 ``stats`` 里，避免同一对象被跨订阅方复用后语义漂移）。

    @author aceFelix
    """

    source: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: str = field(default_factory=_now_iso)
    salience_hint: float = 0.5
    dedup_key: str = ""
    merged: int = 1

    def key(self) -> str:
        """去重键：显式优先，否则 source:kind:指纹。"""
        if self.dedup_key:
            return f"{self.source}:{self.dedup_key}"
        return f"{self.source}:{self.kind}:{_fingerprint(self.payload)}"

    def summary(self) -> str:
        """一句话事实描述（W1 拼模板文本用）；缺失时退化为 kind 本身。"""
        text = str(self.payload.get("summary") or "").strip()
        return text or f"{self.source}.{self.kind}"

    def to_dict(self) -> dict[str, Any]:
        """转 JSON 友好 dict（落盘 jsonl 与桌面导出共用）。"""
        return {
            "ts": self.ts,
            "source": self.source,
            "kind": self.kind,
            "payload": self.payload,
            "salience_hint": round(float(self.salience_hint), 3),
            "dedup_key": self.key(),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PerceptEvent":
        """从 jsonl 行还原事件（导出回读/测试断言用）。

        ``salience_hint`` 落盘是字符串化的浮点，容错解析：解析失败回 0.5
        （先验中位）而不是抛异常——读历史文件不该因为一行坏数据整批失败。
        """
        try:
            hint = float(raw.get("salience_hint", 0.5))
        except (TypeError, ValueError):
            hint = 0.5
        payload = raw.get("payload")
        return cls(
            source=str(raw.get("source", "")),
            kind=str(raw.get("kind", "")),
            payload=payload if isinstance(payload, dict) else {},
            ts=str(raw.get("ts", "")) or _now_iso(),
            salience_hint=hint,
            dedup_key="",  # 落盘存的是算好的 key，还原时留空并按需重算
        )
