"""打扰预算（notify budget）—— "该不该现在打扰你"的确定性闸门（W1·阶段 8）。

理论锚点：Horvitz《Attention-Sensitive Alerting》——打扰决策形式化为
效用与中断代价的比较。本模块用可解释的确定性规则近似：

- daily_max：每日主动播报次数上限（超出后只收录不播报）
- quiet_hours：静默时段（如 23:00-07:00）只入库；时段结束后聚合为一条
- aggregate_window_s：同源事件窗口内合并成一条（防刷屏）
- min_salience：重要度门槛，低分事件降级入库
- channels：播报渠道优先级（桌面通知 > TTS > 手机推送）

重要度评分 = 来源权重 × 时效紧迫度 × 相关性 × 历史接受率。
历史接受率来自用户反馈（record_accept），初始 1.0 —— "越用越懂你"
的闭环入口（习惯学习 P2-7 复用同一数据）。

落盘 `~/.jarvis/notify_state.json`：当日计数、待发队列、源统计。

@author aceFelix
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path.home() / ".jarvis" / "notify_state.json"

# 内置来源权重表（未登记的源用 default）
_DEFAULT_SOURCE_WEIGHTS = {
    "briefing": 0.9,      # 每日简报：设计好的固定打扰
    "deadline": 0.8,      # 截止日期：有时效
    "calendar": 0.85,     # 日程提醒：临近即高优
    "system": 0.7,        # 系统健康告警
    "schedule": 0.9,      # 用户自己设的提醒
    "default": 0.5,
}


@dataclass
class NotifyAction:
    """一次通知请求的闸门裁决。kind: emit（照播）/ queue（入库暂缓）/ suppress（丢弃）。"""

    kind: str
    text: str
    reason: str

    @property
    def should_emit(self) -> bool:
        return self.kind == "emit"


@dataclass
class BudgetConfig:
    daily_max: int = 10
    quiet_hours: str = "23:00-07:00"   # "HH:MM-HH:MM"，支持跨午夜；空串=不启用
    aggregate_window_s: int = 300
    min_salience: float = 0.3
    channels: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.channels is None:
            self.channels = ["desktop", "tts", "mobile"]


class NotifyBudget:
    """打扰预算闸门。ProactiveEngine 所有 on_notify 出口统一过闸。"""

    def __init__(
        self,
        config: BudgetConfig | None = None,
        *,
        path: Path | str | None = None,
        source_weights: dict[str, float] | None = None,
    ) -> None:
        self.config = config or BudgetConfig()
        self._path = Path(path) if path else _DEFAULT_PATH
        self._weights = dict(_DEFAULT_SOURCE_WEIGHTS)
        if source_weights:
            self._weights.update(source_weights)

    # ---- 评分 ----

    def salience(self, source: str, urgency: float = 0.5, relevance: float = 1.0,
                 state: dict | None = None) -> float:
        """重要度 = 来源权重 × 紧迫度 × 相关性 × 历史接受率（0~1）。"""
        weight = self._weights.get(source, self._weights["default"])
        accept = 1.0
        if state:
            rates = state.get("accept_rate", {})
            accept = rates.get(source, 1.0)
        return max(0.0, min(1.0, weight * urgency * relevance * accept))

    def record_accept(self, source: str, accepted: bool) -> None:
        """记录一次用户对提醒的响应（采纳/忽略），滑动更新接受率。"""
        state = self._load()
        stats = state.setdefault("accept_stats", {})
        seen, ok = stats.get(source, [0, 0])
        seen += 1
        ok += 1 if accepted else 0
        stats[source] = [seen, ok]
        state.setdefault("accept_rate", {})[source] = ok / seen
        self._save(state)

    # ---- 闸门主入口 ----

    def gate(
        self,
        text: str,
        source: str,
        *,
        dedup_key: str = "",
        urgency: float = 0.5,
        relevance: float = 1.0,
        now: datetime | None = None,
    ) -> NotifyAction:
        """裁决一条通知请求：立即播报 / 入库暂缓 / 静默丢弃。

        顺序：①非静默时段先冲刷积压（聚合成一条）；②静默时段直接入库；
        ③窗口内同源重复合并；④低于 salience 门槛入库；⑤超日预算 suppress。
        """
        now = now or datetime.now()
        state = self._load()
        self._rollover_day(state, now)
        key = dedup_key or f"{source}:{text[:40]}"

        # ① 非静默时段：先冲刷静默期积压（一次聚合播报）
        if not self._in_quiet(now) and state.get("pending"):
            return self._flush_pending(state, now)

        # ② 静默时段：只入库不打扰
        if self._in_quiet(now):
            self._append_pending(state, text, source, key, now)
            self._save(state)
            return NotifyAction("queue", text, f"静默时段（{self.config.quiet_hours}）暂缓入库")

        # ③ 聚合窗口：同源窗口内已播过 → 合并抑制
        recent = state.get("last_emit", {}).get(key)
        if recent:
            try:
                gap = (now - datetime.fromisoformat(recent["ts"])).total_seconds()
                if 0 <= gap < self.config.aggregate_window_s:
                    recent["merged"] = recent.get("merged", 1) + 1
                    self._save(state)
                    return NotifyAction("suppress", text, f"{int(gap)}s 前已播同源事件，窗口内合并")
            except ValueError:
                pass

        # ④ 重要度门槛
        score = self.salience(source, urgency, relevance, state)
        if score < self.config.min_salience:
            self._append_pending(state, text, source, key, now)
            self._save(state)
            return NotifyAction("queue", text, f"重要度 {score:.2f} 低于门槛 {self.config.min_salience}，入库暂缓")

        # ⑤ 日预算
        if state["count"] >= self.config.daily_max:
            self._append_pending(state, text, source, key, now)
            self._save(state)
            return NotifyAction("suppress", text, f"已达每日播报上限 {self.config.daily_max}")

        # 放行：计数 + 记 last_emit
        state["count"] += 1
        state.setdefault("last_emit", {})[key] = {"ts": now.isoformat(timespec="seconds"), "merged": 1}
        self._save(state)
        return NotifyAction("emit", text, f"预算放行（今日 {state['count']}/{self.config.daily_max}，重要度 {score:.2f}）")

    def pending_count(self) -> int:
        return len(self._load().get("pending", []))

    # ---- 内部 ----

    def _flush_pending(self, state: dict, now: datetime) -> NotifyAction:
        """静默时段结束后，把积压聚合成一条播出。"""
        items = state.get("pending", [])
        state["pending"] = []
        joined = "\n".join(f"  • [{i['source']}] {i['text']}" for i in items)
        text = f"📥 静默时段积压 {len(items)} 条，一并呈上：\n{joined}"
        if state["count"] < self.config.daily_max:
            state["count"] += 1
            self._save(state)
            return NotifyAction("emit", text, f"聚合冲刷 {len(items)} 条积压")
        # 预算也满了：继续压着，等明天
        state["pending"] = [{"ts": now.isoformat(timespec="seconds"), "source": "digest",
                             "key": "digest", "text": text}]
        self._save(state)
        return NotifyAction("queue", text, "积压仍有但今日预算已尽")

    def _append_pending(self, state: dict, text: str, source: str, key: str, now: datetime) -> None:
        pending = state.setdefault("pending", [])
        # 同 key 已入库则合并计数，不重复存文本
        for item in pending:
            if item.get("key") == key:
                item["merged"] = item.get("merged", 1) + 1
                item["ts"] = now.isoformat(timespec="seconds")
                return
        pending.append({"ts": now.isoformat(timespec="seconds"), "source": source,
                        "key": key, "text": text})
        del pending[:-50]  # 队列上限 50，丢最旧

    def _in_quiet(self, now: datetime) -> bool:
        """当前是否处于静默时段（支持跨午夜区间，如 23:00-07:00）。"""
        rng = (self.config.quiet_hours or "").strip()
        if not rng or "-" not in rng:
            return False
        try:
            start_s, end_s = rng.split("-", 1)
            start = dtime.fromisoformat(start_s.strip())
            end = dtime.fromisoformat(end_s.strip())
        except ValueError:
            return False
        cur = now.time()
        if start <= end:
            return start <= cur < end
        return cur >= start or cur < end  # 跨午夜

    def _rollover_day(self, state: dict, now: datetime) -> None:
        """跨天重置当日计数（保留接受率与 last_emit 无意义，一并清）。"""
        today = now.date().isoformat()
        if state.get("day") != today:
            state["day"] = today
            state["count"] = 0
            state["last_emit"] = {}

    def stats(self) -> dict[str, Any]:
        state = self._load()
        return {
            "day": state.get("day", ""), "count": state.get("count", 0),
            "daily_max": self.config.daily_max, "pending": len(state.get("pending", [])),
            "quiet_hours": self.config.quiet_hours,
        }

    # ---- 持久化 ----

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
