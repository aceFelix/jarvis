"""画像按需检索工具（P5，RecallProfile）。

## 为什么需要它

画像库默认只把「活跃且权重高」的一小部分注入 system prompt；库越大覆盖率越低
（实测 51 条库只注入 35 条，相关性召回为 0）。当用户问到某件**具体往事**、
而注入段没覆盖时，模型需要一条按需检索的路。

## 怎么检索

两种模式：
- `search`（默认）：纯本地字面召回（`ProfileStore.recall_similar`）：字符二元组相似度 +
  别名命中 + 同类别加权，不发网络、不引向量。休眠条目也会被检索到——用户主动问起，
  就该把它捞回来。
- `list`：按类别 + 权重分页列出**全部**有效条目（含休眠），用于回答“你了解我多少”。

只读：不改画像、不写盘、不加锁写。

@author aceFelix
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from agent.core.context import ToolContext
from agent.core.memory.profile_store import (
    CATEGORY_LABELS,
    DORMANT_WEIGHT_FLOOR,
    PROFILE_CATEGORIES,
    ProfileStore,
)
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool

#: 单次返回条数的上下限（防御性；默认值来自 settings.profile_recall_tool_top_k）
_MIN_TOP_K = 1
_MAX_TOP_K = 20
_DEFAULT_TOP_K = 8

#: list 模式分页上限（一次最多 50 条，避免把上下文撑爆）
_MIN_LIMIT = 1
_MAX_LIMIT = 50
_DEFAULT_LIMIT = 20


class RecallProfileTool(Tool):
    """按关键词检索画像记忆（只读，按需加载）。

    @author aceFelix
    """

    name = "RecallProfile"
    description = (
        "读取用户的画像记忆（长期偏好/习惯/背景/关系/愿望等）。两种模式："
        "① mode=search（默认）按关键词检索具体事实；"
        "② mode=list 按类别+权重分页列出**全部**画像。"
        "**何时使用**：用户问“你了解我多少 / 你都记得我什么 / 介绍一下你记得的我”时，"
        "必须用 mode=list 分页读完全部条目再总结，**不要只根据注入的人设卡回答**；"
        "用户问到某件具体个人事实而摘录没覆盖时用 query 检索。"
        "**不要使用**：写记忆（用 MemoryUpdate）、与用户本人无关的通用知识。只读。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["search", "list"],
                "description": "search=按关键词检索（默认）；list=分页列出全部画像",
            },
            "query": {
                "type": "string",
                "description": "search 模式必填：要检索的关键词或一句话（如“作息”“编辑器”）",
            },
            "category": {
                "type": "string",
                "description": "可选：限定类别（identity/personality/preference/work_habit/"
                               "schedule/relationship/aspiration/mindset/tool_usage/project/other）",
            },
            "top_k": {
                "type": "integer",
                "description": "search 模式返回条数（默认 8，范围 1–20）",
            },
            "offset": {
                "type": "integer",
                "description": "list 模式起始下标（默认 0）；上一页末尾会给出下一页的 offset",
            },
            "limit": {
                "type": "integer",
                "description": "list 模式每页条数（默认 20，范围 1–50）",
            },
        },
        "required": [],
    }
    max_result_chars = 3_000
    # 延迟加载：通过 ToolSearch 按需发现，不占每轮工具 schema
    deferred = True

    def is_read_only(self, args: dict[str, Any]) -> bool:
        """纯本地查询，不改任何状态。"""
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        """只读，可与其它只读工具并发。"""
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """只读本地文件，默认放行。"""
        return PermissionResult.allow("读取本地画像记忆（只读）")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        mode = str(args.get("mode", "search") or "search").strip().lower()
        if mode not in ("search", "list"):
            return ValidationResult.fail("mode 只能是 search 或 list")
        query = str(args.get("query", "") or "").strip()
        if mode == "search" and not query:
            return ValidationResult.fail("search 模式需要 query；浏览全部请用 mode=list")
        for name in ("top_k", "offset", "limit"):
            raw = args.get(name, 0)
            if raw in (None, ""):
                continue
            if not isinstance(raw, int):
                try:
                    int(raw)
                except (TypeError, ValueError):
                    return ValidationResult.fail(f"{name} 必须是整数")
        return ValidationResult.pass_()

    def _resolve_top_k(self, args: dict[str, Any], ctx: ToolContext) -> int:
        """解析返回条数：显式传参 > settings 默认 > 内置 8，并夹在 1–20。"""
        raw = args.get("top_k")
        if raw in (None, ""):
            settings = getattr(ctx, "settings", None)
            raw = getattr(settings, "profile_recall_tool_top_k", _DEFAULT_TOP_K)
        try:
            value = int(raw or _DEFAULT_TOP_K)
        except (TypeError, ValueError):
            value = _DEFAULT_TOP_K
        return max(_MIN_TOP_K, min(_MAX_TOP_K, value))

    def _resolve_limit(self, args: dict[str, Any]) -> int:
        """list 模式每页条数：默认 20，夹在 1–50。"""
        try:
            value = int(args.get("limit") or _DEFAULT_LIMIT)
        except (TypeError, ValueError):
            value = _DEFAULT_LIMIT
        return max(_MIN_LIMIT, min(_MAX_LIMIT, value))

    def _list(self, store: ProfileStore, category: str, offset: int, limit: int) -> ToolResult:
        """分页列出全部有效条目（含休眠）：按类别 → 权重 → 更新时间排序。"""
        pairs = store.weighted_entries()
        if category:
            pairs = [(w, e) for w, e in pairs if e.category == category]
        total = len(pairs)
        if total == 0:
            suffix = f"类别 {category} 下" if category else ""
            return ToolResult(data=f"画像库{suffix}暂无有效记忆。")
        order = {name: i for i, name in enumerate(PROFILE_CATEGORIES)}
        pairs.sort(
            key=lambda pair: (
                order.get(pair[1].category, 99), -pair[0], -pair[1].updated_at
            )
        )
        offset = max(0, int(offset))
        if offset >= total:
            return ToolResult(data=f"已到末尾：画像库共 {total} 条（offset={offset}）。")
        page = pairs[offset:offset + limit]
        counts = Counter(e.category for _, e in pairs)
        summary = " / ".join(
            f"{CATEGORY_LABELS.get(c, c)} {n}" for c, n in sorted(
                counts.items(), key=lambda kv: order.get(kv[0], 99)
            )
        )
        lines = [
            f"画像全库（共 {total} 条，第 {offset + 1}–{offset + len(page)} 条，按类别+权重排序）",
            f"分类：{summary}",
        ]
        for weight, e in page:
            dormant = "，休眠" if weight < DORMANT_WEIGHT_FLOOR else ""
            lines.append(
                f"- [{CATEGORY_LABELS.get(e.category, e.category)}] {e.content}"
                f"（置信度 {e.confidence:.2f}，提及 {e.ref_count} 次{dormant}）"
            )
        next_offset = offset + len(page)
        if next_offset < total:
            lines.append(f"下一批：mode=\"list\", offset={next_offset}")
        return ToolResult(data="\n".join(lines))

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        mode = str(args.get("mode", "search") or "search").strip().lower()
        query = str(args.get("query", "") or "").strip()
        category = str(args.get("category", "") or "").strip()

        try:
            store = ProfileStore()
            if mode == "list":
                return self._list(
                    store, category,
                    int(args.get("offset") or 0), self._resolve_limit(args),
                )
            top_k = self._resolve_top_k(args, ctx)
            hits = store.recall_similar(query, category=category, top_k=top_k)
        except Exception as e:  # noqa: BLE001 - 画像库坏了也不该炸穿工具层
            return ToolResult(data=f"画像检索失败: {type(e).__name__}: {e}", is_error=True)

        if not hits:
            return ToolResult(
                data=f"画像库里没有找到与「{query}」相关的条目"
                     f"（库内共 {len(store)} 条有效记忆）。"
            )

        lines = [f"画像检索结果（query={query}，共 {len(hits)} 条，按相似度排序）:"]
        for e in hits:
            alias = f" | 别名 {'/'.join(e.aliases[:3])}" if e.aliases else ""
            lines.append(
                f"- [{e.category}] {e.content}"
                f"（置信度 {e.confidence:.2f}，提及 {e.ref_count} 次{alias}）"
            )
        return ToolResult(data="\n".join(lines))
