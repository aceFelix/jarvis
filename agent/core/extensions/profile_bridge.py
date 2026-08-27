"""知识图谱画像桥（Profile Bridge）—— jarvis 与 aceFelix 知识图谱的双向同步。

P2 画像双向同步（升级计划 upgrade-plan.md §4）：
- 图谱 → jarvis：启动时经 MCP `get_profile` 拉取图谱画像，渲染进 system prompt，
  让 Agent 直接掌握结构化用户信息（技能/项目/兴趣等），无需重新聊天
- jarvis → 图谱：本地画像记忆（~/.jarvis/memory/profile.json）经 /memory sync
  交给图谱 `ingest_text` 抽取管线（内置查重与防噪闸），预览确认后回写

数据一致性铁律：图谱是**唯一事实源**，本地 profile.json 是可再生的缓存视图。
两者冲突时以图谱为准（注入 prompt 时明确告知模型）。

设计要点（吸取 profile_refiner 教训）：
- 所有 MCP 调用失败静默降级，绝不影响启动与主流程
- 渲染结果进程内缓存，保证同一会话内 system prompt 逐字节稳定（LLM 前缀缓存）
- 开关 [profile_bridge] enabled 默认关闭，避免未配置时每次启动空连 MCP

@author aceFelix
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.core.memory.compactor import estimate_text_tokens

logger = logging.getLogger("jarvis.memory.profile_bridge")

# 图谱画像段的渲染结果缓存（首次渲染后复用，/memory reload 时清空）
_KG_PROFILE_CACHE: str | None = None


# ─────────────────────────────────────────────────────────────
# 图谱 → jarvis：get_profile 拉取 + 渲染注入
# ─────────────────────────────────────────────────────────────

def render_profile_json(raw: str, token_limit: int) -> str:
    """把 get_profile 返回的 JSON 渲染成 system prompt 画像段（token 硬限额）。

    渲染为一行一条的紧凑陈述（"掌握技能: Python"），方便 LLM 阅读且省 token。
    解析失败或无内容返回空字符串。
    """
    try:
        data = json.loads(raw)
    except Exception:
        return ""
    person = data.get("person")
    if not person:
        return ""

    lines: list[str] = [f"- 姓名: {person.get('name', '')}"]
    props = person.get("properties") or {}
    for k, v in props.items():
        # 过滤图片/头像类 URL 属性：对 LLM 无信息量且浪费 token
        if str(v).startswith(("http://", "https://")):
            continue
        lines.append(f"- {k}: {v}")
    for item in data.get("connections", []) or []:
        rel = item.get("relation", "")
        entity = item.get("entity", "")
        direction = item.get("direction", "->")
        # 方向箭头转为自然语言前缀：-> 表示"本人 → 目标"，<- 表示"目标 → 本人"
        prefix = "" if direction == "->" else "（被）"
        lines.append(f"- {prefix}{rel}: {entity}")

    # token 限额裁剪：逐行累加，超限即停（画像段超长会挤占正文上下文）
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = estimate_text_tokens(line)
        if used + cost > token_limit:
            break
        kept.append(line)
        used += cost
    if len(kept) <= 1:  # 只剩姓名没有实质内容，不值得注入
        return ""
    return (
        "# 关于用户（知识图谱画像）\n"
        + "\n".join(kept)
        + "\n（来自 aceFelix 知识图谱，为唯一事实源；与本地画像记忆冲突时以本段为准）"
    )


async def preload_kg_profile(mcp_client: Any, settings: Any) -> bool:
    """启动时经 MCP 拉取图谱画像并缓存渲染结果。返回是否成功注入。

    由 main.py 在 MCP 连接完成后、build_system_prompt 之前调用
    （build_system_prompt 是同步的，画像拉取必须提前完成）。
    """
    global _KG_PROFILE_CACHE
    if not getattr(settings, "profile_bridge_enabled", False):
        return False
    if mcp_client is None or not getattr(mcp_client, "available", False):
        return False
    server = getattr(settings, "profile_bridge_server", "acefelix-knowledge")
    try:
        raw = await mcp_client.call_tool(server, "get_profile", {"max_items": 30})
        limit = int(getattr(settings, "profile_bridge_token_limit", 400))
        section = render_profile_json(raw, limit)
        if section:
            _KG_PROFILE_CACHE = section
            return True
    except Exception as e:
        logger.warning("知识图谱画像拉取失败（不影响启动）: %s", e)
    return False


def kg_profile_section() -> str:
    """供 system.py 取已缓存的图谱画像段（未加载返回空串）。"""
    return _KG_PROFILE_CACHE or ""


def reload_kg_profile_cache() -> None:
    """清空缓存（/memory reload 等命令后，下次 system prompt 重建时重新渲染）。"""
    global _KG_PROFILE_CACHE
    _KG_PROFILE_CACHE = None


# ─────────────────────────────────────────────────────────────
# jarvis → 图谱：本地画像经 ingest_text 回写
# ─────────────────────────────────────────────────────────────

def build_sync_text(store: Any | None = None) -> str:
    """把本地画像条目汇总成一段待抽取文本（交给图谱抽取管线）。

    图谱为唯一事实源，本地条目是"素材"——管线负责抽取、查重、防噪，
    已在图谱中的条目会被查重闸自动跳过，因此每次全量提交是幂等的。
    @param store: 可选注入（测试用），缺省读 ~/.jarvis/memory/profile.json
    """
    from agent.core.memory.profile_store import ProfileStore

    # 显式判 None：ProfileStore 定义了 __len__，空 store 布尔值为 False，
    # 不能用 `store or ProfileStore()` 写法（会回退到默认路径的实例）
    entries = (store if store is not None else ProfileStore()).entries()
    if not entries:
        return ""
    lines = [f"以下是关于用户 aceFelix 的画像事实（共 {len(entries)} 条）："]
    lines.extend(f"- [{e.category}] {e.content}" for e in entries)
    return "\n".join(lines)


async def sync_to_kg(mcp_client: Any, settings: Any, dry_run: bool, store: Any | None = None) -> dict[str, Any]:
    """把本地画像提交给图谱抽取管线。返回管线结果 dict。

    @param dry_run: True 只拿预览（给用户确认）；False 正式写入
    @param store: 可选注入画像存储（测试用）
    @return {"ok": bool, "result"?: 管线结果, "error"?: 错误信息}
    """
    server = getattr(settings, "profile_bridge_server", "acefelix-knowledge")
    text = build_sync_text(store)
    if not text:
        return {"ok": False, "error": "本地画像为空，无可同步"}
    try:
        raw = await mcp_client.call_tool(
            server, "ingest_text",
            {"text": text, "dry_run": dry_run, "source": "jarvis-profile"},
        )
        result = json.loads(raw)
        if isinstance(result, dict) and result.get("error"):
            return {"ok": False, "error": str(result["error"])}
        return {"ok": True, "result": result}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
