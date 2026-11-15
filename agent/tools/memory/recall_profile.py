"""画像按需检索工具（P5，RecallProfile）。

## 为什么需要它

画像库默认只把「活跃且权重高」的一小部分注入 system prompt；库越大覆盖率越低
（实测 51 条库只注入 35 条，相关性召回为 0）。当用户问到某件**具体往事**、
而注入段没覆盖时，模型需要一条按需检索的路。

## 怎么检索

纯本地字面召回（`ProfileStore.recall_similar`）：字符二元组相似度 + 别名命中 +
同类别加权，不发网络、不引向量、不读任何外部服务。休眠条目也会被检索到——
用户主动问起，就该把它捞回来。

只读：不改画像、不写盘、不加锁写。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.memory.profile_store import ProfileStore
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool

#: 单次返回条数的上下限（防御性；默认值来自 settings.profile_recall_tool_top_k）
_MIN_TOP_K = 1
_MAX_TOP_K = 20
_DEFAULT_TOP_K = 8


class RecallProfileTool(Tool):
    """按关键词检索画像记忆（只读，按需加载）。

    @author aceFelix
    """

    name = "RecallProfile"
    description = (
        "按关键词检索用户的画像记忆（长期偏好/习惯/背景/关系/愿望等）。"
        "**何时使用**：用户问到某件具体个人事实，而当前 system prompt 的画像段没有覆盖"
        "（例如“我上次说的那个编辑器是什么”“我以前提过的作息”）。"
        "**不要使用**：写记忆（用 MemoryUpdate）、与用户本人无关的通用知识。"
        "返回按相似度排序的条目（类别 + 内容 + 置信度），只读。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "要检索的关键词或一句话（如“作息”“编辑器”“面试”）",
            },
            "category": {
                "type": "string",
                "description": "可选：限定类别（identity/personality/preference/work_habit/"
                               "schedule/relationship/aspiration/mindset/tool_usage/project/other）",
            },
            "top_k": {
                "type": "integer",
                "description": "返回条数（默认 8，范围 1–20）",
            },
        },
        "required": ["query"],
    }
    max_result_chars = 1_500
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
        query = str(args.get("query", "") or "").strip()
        if not query:
            return ValidationResult.fail("query 不能为空")
        top_k = args.get("top_k", 0)
        if top_k not in (None, "") and not isinstance(top_k, int):
            try:
                int(top_k)
            except (TypeError, ValueError):
                return ValidationResult.fail("top_k 必须是整数")
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

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query", "") or "").strip()
        category = str(args.get("category", "") or "").strip()
        top_k = self._resolve_top_k(args, ctx)

        try:
            store = ProfileStore()
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
