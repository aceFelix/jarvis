"""ToolSearch —— 延迟工具按需发现。

延迟工具按需发现：
核心工具始终携带，MCP/harness/可选工具仅发名字摘要到 system prompt，
模型需要时调用 ToolSearch 搜索 → 返回完整 schema → 标记为"已发现" →
下一轮迭代 _build_tool_defs() 自动包含已发现的工具。

@author aceFelix
"""

from __future__ import annotations

import json
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import ToolResult
from agent.core.tool import Tool, ToolRegistry


def _capability_note(tool_name: str) -> str:
    """取该工具所属能力的状态行；能力表无声明或探测出错时返回空串。

    自检不能反过来弄坏主流程：探测层自己是坏的（缺依赖、未预期异常）也必须
    让 ToolSearch 正常返回，所以这里统一拦住异常、回空串。

    @author aceFelix
    """
    try:
        from agent.capabilities import tool_status_note

        return tool_status_note(tool_name)
    except Exception:  # noqa: BLE001 - 辅佐能力不得拖垮工具发现
        return ""


# 中文功能词 → 英文工具名/描述里的常见片段。
# 实机教训（2026-11-09）：查询「浏览器 打开网页 获取文本 browser」整词匹配不到
# BrowserNavigate / BrowserGetText，反而把 cli_anything__browser 排到前 6，
# 模型白跑一步 ToolSearch。@author aceFelix
_ALIASES: dict[str, tuple[str, ...]] = {
    "浏览器": ("browser", "navigate", "page"),
    "网页": ("browser", "page", "url", "navigate"),
    "打开": ("open", "navigate", "launch"),
    "抓取": ("fetch", "get", "crawl"),
    "获取": ("get", "fetch"),
    "文本": ("text", "gettext"),
    "截图": ("screenshot",),
    "点击": ("click",),
    "输入": ("type", "fill", "input"),
    "搜索": ("search",),
}

# 单个工具 schema 与整次返回体的字符上限：避免 56KB 级返回被截断后
# 模型反而读不到关键信息（实机一次 ToolSearch 返回 56,423 字符）。
_MAX_SCHEMA_CHARS = 1200
_MAX_RESULT_CHARS = 6000


def _expand_keywords(query: str) -> list[str]:
    """把查询拆成可用于打分的词：原词 + 中文二元组 + 英文别名。

    Args:
        query: 已转小写的查询串。

    Returns:
        去重后的关键词列表（保持稳定顺序）。
    """
    raw = query.replace(",", " ").replace("，", " ").split()
    words: list[str] = []

    def _add(word: str) -> None:
        if word and word not in words:
            words.append(word)

    for token in raw:
        _add(token)
        # 中文长词拆二元组：「打开网页」→「打开」「开网」「网页」，避免整词匹配落空
        if len(token) >= 3 and any("\u4e00" <= ch <= "\u9fff" for ch in token):
            for index in range(len(token) - 1):
                _add(token[index:index + 2])
        # 每个中文词（含拆出的二元组）再补英文别名
        for part in list(words):
            for alias in _ALIASES.get(part, ()):
                _add(alias)
    return words


class ToolSearchTool(Tool):
    """搜索并加载延迟工具。

    当模型需要某个功能但当前工具列表中没有时，用关键词搜索延迟工具池。
    搜索命中后，工具的完整 JSON Schema 会返回给模型，同时标记为"已发现"，
    后续迭代中该工具会自动出现在可用工具列表中。
    """

    name = "ToolSearch"
    description = (
        "搜索并加载延迟工具。当你需要某个功能但当前可用工具列表中没有时，"
        "用关键词搜索。返回匹配工具的完整定义，之后即可直接调用。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（工具名、功能描述、server 名等）",
            },
            "max_results": {
                "type": "integer",
                "description": "最多返回几个结果（默认 5）",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    # 自身是核心工具，始终携带
    deferred = False

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = (args.get("query") or "").strip().lower()
        max_results = int(args.get("max_results", 5))

        if not query:
            return ToolResult.error("请提供搜索关键词（query 参数）")

        # 搜索池 = 延迟工具 + 已常驻的内置工具。
        # 实机（2026-11-09）：模型搜「浏览器」时内置 BrowserNavigate 已经可用，
        # 但旧的只搜延迟池逻辑让它白跑一步、还拿到 56KB 的插件 schema。
        # @author aceFelix
        deferred_tools = self._registry.all_deferred()
        deferred_names = {tool.name for tool in deferred_tools}
        searchable = deferred_tools + [
            tool for tool in self._registry.all_core() if tool.name != self.name
        ]
        keywords = _expand_keywords(query)

        scored: list[tuple[int, Tool]] = []
        for tool in searchable:
            score = self._score(tool, keywords)
            if score > 0:
                scored.append((score, tool))

        # 按得分降序
        scored.sort(key=lambda x: x[0], reverse=True)
        matches = [tool for _, tool in scored[:max_results]]

        if not matches:
            # 列出所有延迟工具名供参考
            all_names = [t.name for t in deferred_tools]
            hint = f"未找到匹配「{query}」的工具。"
            if all_names:
                sample = ", ".join(all_names[:30])
                hint += f"\n可用延迟工具（共 {len(all_names)} 个）: {sample}"
                if len(all_names) > 30:
                    hint += f" ... 等 {len(all_names) - 30} 个"
            # 兜底提示：避免模型换个关键词反复重试同一个不存在的工具空转
            hint += (
                "\n\n提示：若你已换过多个关键词仍搜不到目标工具，"
                "说明该能力在当前环境未注册（部分功能仅在桌面/服务端可用），"
                "请改用已有工具完成，或如实告知先生该功能暂不可用，"
                "不要继续反复搜索同一工具，也不要自行绕道拼凑实现。"
            )
            return ToolResult.ok(hint)

        # 只有延迟工具需要"发现"；已常驻的内置工具直接标注"已可用"
        discovered: set[str] = ctx.extra.setdefault("discovered_tools", set())
        for tool in matches:
            if tool.name in deferred_names:
                discovered.add(tool.name)

        # 构造返回文本：schema 单条与整体都限长，避免返回被截断后关键信息丢失
        lines = [f"已找到 {len(matches)} 个工具（已可用的直接调用，无需再搜）：\n"]
        for index, tool in enumerate(matches):
            schema_str = json.dumps(tool.input_schema, ensure_ascii=False, indent=2)
            if len(schema_str) > _MAX_SCHEMA_CHARS:
                schema_str = schema_str[:_MAX_SCHEMA_CHARS] + "\n…（schema 已截断）"
            lines.append(f"## {tool.name}")
            if tool.name not in deferred_names:
                lines.append("状态: 已可用（常驻工具，直接调用）")
            lines.append(f"描述: {tool.description.split(chr(10), 1)[0]}")
            # 能力前置状态：让模型在**决定调用之前**就知道这个工具现在跑不通。
            # 以前这里只写“已加载到可用列表”，而“可用”其实只意味着包能 import——
            # playwright 内核缺失时就是这句话让模型去撞一个必失败的调用，然后被自动重试。
            # @author aceFelix
            note = _capability_note(tool.name)
            if note:
                lines.append(note)
            lines.append(f"参数:\n```json\n{schema_str}\n```\n")
            # 整体超长：保留已拼内容并明确告知还有多少条没展开
            if sum(len(line) for line in lines) >= _MAX_RESULT_CHARS:
                remaining = len(matches) - index - 1
                if remaining > 0:
                    lines.append(f"…（返回体已限长，另有 {remaining} 个结果未展开，可缩小关键词）")
                break

        return ToolResult.ok("\n".join(lines))

    @staticmethod
    def _score(tool: Tool, keywords: list[str]) -> int:
        """简单关键词匹配打分。"""
        name_lower = tool.name.lower()
        desc_lower = tool.description.lower()
        score = 0
        for kw in keywords:
            if kw in name_lower:
                score += 10  # 名字命中权重高
            if kw in desc_lower:
                score += 3
            # 工具名按 __ 分割后的片段匹配（如 mcp__browser__navigate）
            parts = name_lower.replace("__", " ").replace("_", " ").split()
            if kw in parts:
                score += 5
        return score

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True
