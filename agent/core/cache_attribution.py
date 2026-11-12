"""成本与缓存失效的归因（从 `query_loop` 抽出的无状态容器）。

## 为什么单独一个模块

两个原因：

1. `query_loop.py` 已到 900 行门禁；塞进去就超；
2. 它是一块**纯记账**逻辑：不依赖会话状态机，只吃"这一轮的用量"和"事件"。抽出来
   才能直接单测——验证"换模型记一次失效"不该去搭一整套 QueryLoop。

## 为什么需要这两张账

`/cost` 原来只报"当前模型"和"累计金额"，跨模型会话里这两个数放在一起会误导：
2026-11 实测一次 7 轮会话显示 ¥0.2491，而按当前模型（deepseek-flash）算只该 ¥0.065
——差的钱是中途切进 `deepseek-v4-pro`（价目贵 7.5 倍）那几轮烧的。同理命中率 75%
也不是缓存坏了，而是两次换模型各付了一次全量未命中。

所以记两样：

- **按模型**：输入/命中/未命中/输出/金额，让金额能自己解释自己；
- **失效事件**：换模型、工具块变化，各带"当时的上下文规模"，从而能算出结构性
  未命中有多少。

@author aceFelix
"""

from __future__ import annotations

import hashlib
from typing import Any


def _empty_bucket() -> dict[str, float]:
    return {"input": 0.0, "hit": 0.0, "miss": 0.0, "output": 0.0, "cost": 0.0}


def _tool_name(item: Any) -> str:
    """兼容两种形状：``ToolDef`` 对象（dataclass）与原始 dict。"""
    name = getattr(item, "name", None)
    if name is None and isinstance(item, dict):
        name = item.get("name")
    return str(name or "")


class CacheAttribution:
    """一张会话级的账：按模型分桶 + 失效事件列表。"""

    def __init__(self) -> None:
        self._by_model: dict[str, dict[str, float]] = {}
        self._breaks: list[dict[str, Any]] = []
        self._last_tool_fp: str | None = None

    # ---- 记录 ----

    def add_turn(self, model: str, turn: Any, settled: Any) -> None:
        """记一轮用量。金额只在该模型有官方价目时计入。"""
        bucket = self._by_model.setdefault(model or "?", _empty_bucket())
        bucket["input"] += getattr(turn, "input_tokens", 0) or 0
        bucket["hit"] += getattr(turn, "cache_read_tokens", 0) or 0
        bucket["miss"] += getattr(turn, "cache_miss_tokens", 0) or 0
        bucket["output"] += getattr(turn, "output_tokens", 0) or 0
        if getattr(settled, "priced", False):
            bucket["cost"] += getattr(settled, "cost_cny", 0.0) or 0.0

    def note_model_switch(self, old: str, new: str, context_tokens: int) -> None:
        """换模型 = 换一个缓存空间（DeepSeek 等按模型隔离），攒的全废。"""
        if not new or new == old or not context_tokens:
            return
        self._breaks.append(
            {"reason": "model_switch", "from": old, "to": new, "context": context_tokens}
        )

    def note_tool_block(self, tool_defs: Any) -> None:
        """记工具块指纹；变了就记一次失效。

        工具 schema 排在前缀里，名字集合一变，从工具段往后整段失效——这是"没换模型
        但命中率突然掉一半"的常见原因（延迟加载按需取工具时尤其明显）。
        指纹只取名字集合：schema 内容变化同样破缓存，但先抓主要矛盾，免得每次微调
        描述都误报。
        """
        names = sorted(_tool_name(d) for d in (tool_defs or []))
        fp = hashlib.sha1("|".join(names).encode("utf-8")).hexdigest()[:12]
        if self._last_tool_fp is not None and fp != self._last_tool_fp:
            self._breaks.append(
                {"reason": "tool_block", "from": self._last_tool_fp, "to": fp}
            )
        self._last_tool_fp = fp

    # ---- 读 ----

    @property
    def by_model(self) -> dict[str, dict[str, float]]:
        return {k: dict(v) for k, v in self._by_model.items()}

    @property
    def breaks(self) -> list[dict[str, Any]]:
        return [dict(b) for b in self._breaks]

    def structural_miss_tokens(self) -> int:
        """结构性未命中估算：每次换模型付掉一次满上下文。"""
        return structural_miss_tokens(self._breaks)

    def summary(self) -> str:
        """一句话归因，给 /cost 用。"""
        return summarize_breaks(self._breaks)


def structural_miss_tokens(breaks: list[dict[str, Any]]) -> int:
    """结构性未命中估算：每次换模型付掉一次满上下文。"""
    return sum(int(b.get("context") or 0) for b in breaks if b.get("reason") == "model_switch")


def summarize_breaks(breaks: list[dict[str, Any]]) -> str:
    """把失效事件折成一句话（/cost 的一行）。"""
    switches = [b for b in breaks if b.get("reason") == "model_switch"]
    tool_changes = [b for b in breaks if b.get("reason") == "tool_block"]
    if not switches and not tool_changes:
        return "无（前缀一直稳定）"
    parts: list[str] = []
    if switches:
        pairs = "、".join(f"{b.get('from')}→{b.get('to')}" for b in switches[:3])
        parts.append(f"换模型 {len(switches)} 次（{pairs}）")
    if tool_changes:
        parts.append(f"工具块变化 {len(tool_changes)} 次")
    tokens = structural_miss_tokens(breaks)
    if tokens:
        parts.append(f"结构性未命中约 {tokens:,} token")
    return "；".join(parts)
