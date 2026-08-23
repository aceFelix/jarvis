"""画像记忆提炼（Profile Memory Refiner）。

会话结束后在后台线程运行：截取本会话对话 → 用（可独立配置的便宜）
模型提取"关于用户的持久事实" → 与现有画像做冲突裁决 → 写入 ProfileStore。

铁律（吸取 skill 64k token 教训）:
1. 永远异步后台执行，绝不在对话路径上跑 LLM
2. 宁缺毋滥——只提取稳定事实，一次性闲聊不记
3. 提炼失败静默吞掉（写日志），不影响会话保存与主流程

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from agent.core.message import Message, TextContent
from agent.core.memory.profile_store import (
    PROFILE_CATEGORIES,
    ProfileEntry,
    ProfileStore,
)

logger = logging.getLogger("jarvis.memory.profile")

# 提炼节流：两次提炼之间的最小间隔（秒）。_auto_save 每轮对话都会调用，
# 不能每轮都跑 LLM——间隔内直接跳过，用户中途强关终端最近行为也已提炼过。
REFINE_INTERVAL_SECONDS = 600

# 喂给 LLM 的消息窗口与单条截断（控制提炼成本）
_MAX_MESSAGES = 40
_MAX_CHARS_PER_MSG = 300
# 冲突裁决时展示的现有画像条数上限
_MAX_EXISTING_ENTRIES = 50

# 节流状态（模块级，进程内共享）
_last_refine_ts: float = 0.0
_in_flight = threading.Lock()


@dataclass
class RefineReport:
    """一次提炼的结果摘要。"""

    added: int = 0
    updated: int = 0        # 替换旧条目数
    skipped: bool = False   # True = 未触发（节流/消息不足/mock 等）
    reason: str = ""
    error: str = ""


# ─────────────────────────────────────────────────────────────
# 对话截取
# ─────────────────────────────────────────────────────────────

def _collect_dialog_text(messages: list[Message]) -> str:
    """把会话压成"用户/贾维斯"逐行文本（喂提炼 prompt 用）。

    过滤：命令消息（/ 开头）、工具调用与结果（对画像无价值）。
    只取最近 _MAX_MESSAGES 条，每条截 _MAX_CHARS_PER_MSG 字。
    """
    lines: list[str] = []
    for m in messages:
        role = getattr(m, "role", "")
        if role not in ("user", "assistant"):
            continue
        text = m.get_text() if hasattr(m, "get_text") else ""
        text = text.strip()
        if not text or text.startswith("/"):
            continue
        who = "用户" if role == "user" else "贾维斯"
        lines.append(f"{who}: {text[:_MAX_CHARS_PER_MSG]}")
    return "\n".join(lines[-_MAX_MESSAGES:])


# ─────────────────────────────────────────────────────────────
# Provider 构建
# ─────────────────────────────────────────────────────────────

def _build_refine_provider(settings: Any):
    """构建提炼用 LLM provider。

    优先用 [memory] 段独立配置的便宜模型（profile_refine_*），
    未配置则回退主 LLM。提炼是纯文本任务，model_type="text"。

    主模型为 mock 且无独立配置时返回 None（跳过提炼）。
    """
    from agent.bootstrap import _build_provider

    refine_model = getattr(settings, "profile_refine_model", "")
    if refine_model:
        # 独立模型：复制 settings 覆盖 LLM 四元组后走统一工厂
        s = settings.with_overrides(
            model=refine_model,
            api_format=getattr(settings, "profile_refine_provider", "") or settings.api_format,
            base_url=getattr(settings, "profile_refine_base_url", "") or settings.base_url,
            api_key=getattr(settings, "profile_refine_api_key", "") or settings.api_key,
        )
        return _build_provider(s, model_type="text")

    if (getattr(settings, "api_format", "") or "").lower() == "mock":
        return None  # 开发模拟环境，无真实 LLM 可用
    return _build_provider(settings, model_type="text")


# ─────────────────────────────────────────────────────────────
# 提炼 prompt
# ─────────────────────────────────────────────────────────────

def _build_prompt(dialog_text: str, existing: list[ProfileEntry]) -> str:
    cats = ", ".join(PROFILE_CATEGORIES)
    existing_lines = "\n".join(
        f"- [{e.id}] {e.category}: {e.content}" for e in existing[:_MAX_EXISTING_ENTRIES]
    ) or "（暂无）"

    return f"""你是用户画像提炼器。从下面的对话中提取关于【用户本人】的持久事实，用于让 AI 助手长期记住这个用户。

## 规则
1. 只提取多次出现或明确表达的稳定事实（习惯、偏好、背景、常用工具、作息、项目、联系人）
2. 一次性闲聊、临时性问题、关于贾维斯自身的内容——不记
3. content 用第三人称简体中文陈述（如"习惯深夜工作"），单条不超过 40 字
4. category 从以下选择: {cats}
5. confidence 取 0.3~0.95：明确自述的取高，从行为推断的取低
6. 与"已有画像"矛盾或重复的信息，放到 updates 并给出 replace_id（用新表述替换旧条目）
7. 没有值得记的就返回空数组——宁缺毋滥

## 已有画像
{existing_lines}

## 本次对话
{dialog_text}

## 输出（严格 JSON，不要输出其他任何内容）
{{"new": [{{"category": "...", "content": "...", "confidence": 0.8}}], "updates": [{{"replace_id": "ent_xxx", "category": "...", "content": "...", "confidence": 0.9}}]}}"""


# ─────────────────────────────────────────────────────────────
# JSON 容错解析
# ─────────────────────────────────────────────────────────────

def _parse_profile_json(raw: str) -> dict:
    """容错解析 LLM 输出的 JSON。

    依次尝试：直接解析 → 剥 ```json 围栏 → 截取首个 {{ 到最后一个 }}。
    全部失败返回空 dict（视为本次无可提取内容）。
    """
    text = raw.strip()
    if not text:
        return {}
    # 剥 markdown 围栏
    if text.startswith("```"):
        text = text.split("```", 2)
        text = text[1] if len(text) > 1 else ""
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    for candidate in (text,):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    # 截取 {...}
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

async def _call_llm(provider, model: str, prompt: str) -> str:
    """调 provider.stream 收集完整文本（提炼不需要思考，临时关闭）。"""
    msgs = [Message(role="user", content=[TextContent(text=prompt)])]
    old_thinking = provider.is_thinking_enabled()
    out = ""
    try:
        provider.set_thinking_enabled(False)
        events = provider.stream(
            model=model,
            system="你是用户画像提炼器，只输出 JSON。",
            messages=msgs,
            tools=[],
            max_tokens=1500,
            temperature=0.2,
        )
        async for event in events:
            text = getattr(event, "text", "")
            if text:
                out += text
    finally:
        provider.set_thinking_enabled(old_thinking)
    return out


def refine_session(
    messages: list[Message],
    session_id: str,
    settings: Any,
    store: ProfileStore | None = None,
) -> RefineReport:
    """同步提炼一个会话（供后台线程 / /memory refine 手动触发）。"""
    report = RefineReport()
    store = store or ProfileStore()
    provider = None
    try:
        dialog_text = _collect_dialog_text(messages)
        if not dialog_text:
            report.skipped = True
            report.reason = "无可提炼对话内容"
            return report

        provider = _build_refine_provider(settings)
        if provider is None:
            report.skipped = True
            report.reason = "无可用 LLM（mock 且未配置独立提炼模型）"
            return report

        model = getattr(settings, "profile_refine_model", "") or settings.model
        raw = asyncio.run(_call_llm(provider, model, _build_prompt(dialog_text, store.entries())))
        data = _parse_profile_json(raw)

        for item in data.get("new", []) or []:
            if not isinstance(item, dict) or not str(item.get("content", "")).strip():
                continue
            entry = ProfileEntry.new(
                content=str(item["content"]),
                category=str(item.get("category", "other")),
                confidence=float(item.get("confidence", 0.5)),
                source_session=session_id,
            )
            store.upsert(entry)
            report.added += 1

        for item in data.get("updates", []) or []:
            if not isinstance(item, dict):
                continue
            replace_id = str(item.get("replace_id", ""))
            if not replace_id or not store.get(replace_id):
                continue  # LLM 幻觉出不存在的 id → 当新条目处理
            if store.delete(replace_id):
                entry = ProfileEntry.new(
                    content=str(item.get("content", "")),
                    category=str(item.get("category", "other")),
                    confidence=float(item.get("confidence", 0.5)),
                    source_session=session_id,
                )
                store.upsert(entry)
                report.updated += 1

        # 上限淘汰
        max_entries = int(getattr(settings, "profile_max_entries", 200))
        pruned = store.prune_over_limit(max_entries)
        if pruned:
            report.reason = f"淘汰低价值条目 {pruned} 条"

        if report.added or report.updated:
            logger.info(
                "画像提炼完成: session=%s +%d ~%d", session_id, report.added, report.updated
            )
    except Exception as e:  # 提炼失败绝不影响主流程
        report.error = f"{type(e).__name__}: {e}"
        logger.warning("画像提炼失败: %s", report.error)
    finally:
        if provider is not None and not report.skipped:
            try:
                asyncio.run(provider.close())
            except Exception:
                pass
    return report


# ─────────────────────────────────────────────────────────────
# 异步触发（_auto_save 挂载点）
# ─────────────────────────────────────────────────────────────

def maybe_refine_async(messages: list[Message], session_id: str, settings: Any) -> bool:
    """节流后异步触发提炼。返回是否真的启动了后台任务。

    条件：开关开启 + 消息够多 + 距上次提炼超过间隔 + 无进行中的提炼。
    """
    global _last_refine_ts
    if not getattr(settings, "profile_enabled", False):
        return False
    min_msgs = int(getattr(settings, "profile_refine_min_messages", 6))
    if len(messages) < min_msgs:
        return False
    now = time.time()
    if now - _last_refine_ts < REFINE_INTERVAL_SECONDS:
        return False
    if not _in_flight.acquire(blocking=False):
        return False  # 已有线程在提炼

    _last_refine_ts = now

    def _worker() -> None:
        try:
            refine_session(list(messages), session_id, settings)
        finally:
            _in_flight.release()

    threading.Thread(target=_worker, name="profile-refiner", daemon=True).start()
    return True
