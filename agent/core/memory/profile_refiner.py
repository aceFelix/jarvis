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
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from agent.core.message import Message, TextContent
from agent.core.memory import profile_ledger, profile_resolve
from agent.core.memory.profile_json import parse_json_object
from agent.core.memory.profile_store import (
    ProfileEntry,
    ProfileStore,
)

logger = logging.getLogger("jarvis.memory.profile")

# 提炼失败退避：两次**失败尝试**之间的最小间隔（秒）。成功不节流——
# 水位 + 条数（min_delta）已经限住频率；这里只在 provider 持续报错时
# 防止每轮 _auto_save 都起线程重试。默认值，实际取 settings.profile_refine_interval。
REFINE_INTERVAL_SECONDS = 600

# 全量归并（dedup_all）是 O(n²)：库大后每轮提炼都跑会把后台拖慢。
# 超过此规模就跳过“顺带归并”，依赖写入时的 merge_or_add 兜底；
# 用户仍可随时用 /memory dedup 手动整理。
_MAX_AUTO_DEDUP_ENTRIES = 600

# 喂给 LLM 的消息窗口与单条截断（控制提炼成本）
_MAX_MESSAGES = 40
_MAX_CHARS_PER_MSG = 300

# 节流状态（模块级，进程内共享）。节流时间戳已改存台账（跨进程一致），
# 这里只留并发闸：同一进程同时只能跑一个提炼。
_in_flight = threading.Lock()


@dataclass
class RefineReport:
    """一次提炼的结果摘要。"""

    added: int = 0
    updated: int = 0        # 替换旧条目数
    merged: int = 0         # 命中存储层查重被合并的条数（深重复拦截）
    deleted: int = 0        # 裁决为 delete 并成功删除的条数
    noop: int = 0           # 裁决为 noop 的条数（"不记"的正式出口）
    extracted: int = 0      # 抽取段产出的候选数
    rejected: int = 0       # 被硬闸拒掉的条数（>40 字/空内容/非法 op）
    skipped: bool = False   # True = 未触发（节流/消息不足/mock 等）
    advanceable: bool = False  # 本轮是否可推进水位（"没内容"可推进，"没 LLM"不可）
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

    api_key 解析链（留空时逐级回退）：
    refine 配置 → 按厂商查环境变量 → 同名自定义模型的 api_key → 主 LLM key。
    环境变量优先：避免配置文件硬编码 key 失效，也避免错拿主 LLM
    的别厂商 key（如 DashScope）导致鉴权失败。

    主模型为 mock 且无独立配置时返回 None（跳过提炼）。
    """
    from agent.bootstrap import _build_provider

    refine_model = getattr(settings, "profile_refine_model", "")
    if refine_model:
        # 同名自定义模型的配置（协议 / base_url / vendor），留空字段时继承，
        # 与 /model 切换自定义模型的取值逻辑保持一致（兼容旧字段名）
        custom = settings.custom_models.get(refine_model) if hasattr(settings, "custom_models") else None
        if not isinstance(custom, dict):
            custom = {}

        api_format = (
            getattr(settings, "profile_refine_provider", "")
            or custom.get("api_format") or custom.get("provider_type", "")
            or settings.api_format
        )
        base_url = (
            getattr(settings, "profile_refine_base_url", "")
            or custom.get("base_url", "")
            or settings.base_url
        )
        vendor = str(custom.get("provider") or custom.get("vendor", "") or api_format)
        # key 解析链：refine 配置 → 环境变量 → 自定义模型配置 → 主 LLM key
        api_key = (
            getattr(settings, "profile_refine_api_key", "")
            or _resolve_api_key_from_env(vendor)
            or str(custom.get("api_key", "") or "")
            or settings.api_key
        )

        # 独立模型：复制 settings 覆盖 LLM 四元组后走统一工厂。
        # dedicated_client=True 强制给提炼建**独占连接池**：提炼跑在后台线程、
        # 与主对话并发，共用同一个连接池时，提炼的流量与关闭都会影响前台可用
        # 连接（2026-10 事故：提炼 close() 直接关死了主对话的连接池，每次重启后
        # 聊到第十句左右必挂）。物理隔开最省心。@author aceFelix
        s = settings.with_overrides(
            model=refine_model,
            api_format=api_format,
            base_url=base_url,
            api_key=api_key,
        )
        return _build_provider(s, model_type="text", dedicated_client=True)

    if (getattr(settings, "api_format", "") or "").lower() == "mock":
        return None  # 开发模拟环境，无真实 LLM 可用
    # 回退主 LLM 也走独占池：即便模型/端点相同，也必须与前台对话的连接池分开
    return _build_provider(settings, model_type="text", dedicated_client=True)


# 厂商 → 专属 API key 环境变量（与 env.py 的厂商专属变量保持一致）
_VENDOR_KEY_ENVS: dict[str, tuple[str, ...]] = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "dashscope": ("DASHSCOPE_API_KEY",),
    "zai": ("ZAI_API_KEY",),
    "zhipu": ("ZAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "kimi": ("KIMI_API_KEY",),
    "moonshot": ("KIMI_API_KEY",),
    "minimax": ("MINIMAX_API_KEY",),
    "mimo": ("MIMO_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
}


def _resolve_api_key_from_env(vendor: str) -> str:
    """按厂商查环境变量取 API key。

    查厂商专属变量（如 deepseek → DEEPSEEK_API_KEY）；
    仅当厂商未知时才走通用变量兜底（OPENAI_API_KEY 等），
    避免把别家的通用 key 错用在本厂商上。找不到返回空串。
    """
    import os

    vendor_names = _VENDOR_KEY_ENVS.get((vendor or "").lower())
    if vendor_names:
        for n in vendor_names:
            v = os.environ.get(n)
            if v:
                return v
        return ""  # 知道厂商但环境变量没配 → 交给链路下一级，不乱拿通用变量
    for n in ("OPENAI_API_KEY", "JARVIS_API_KEY", "MY_AGENT_API_KEY"):
        v = os.environ.get(n)
        if v:
            return v
    return ""


# ─────────────────────────────────────────────────────────────
# 提炼 prompt
# ─────────────────────────────────────────────────────────────

def _build_prompt(dialog_text: str, existing: list[ProfileEntry]) -> str:
    """兼容旧名：现在等于“抽取段” prompt。

    P1 起提炼拆成两段（抽取 / 裁决），**已有画像只在裁决段出现**——
    抽取段刻意不带旧画像，避免候选与裁决互相污染。
    保留本函数是为了不打断既有测试与外部引用。
    """
    del existing  # 抽取段不使用旧画像
    return profile_resolve.build_extract_prompt(dialog_text)


# ─────────────────────────────────────────────────────────────
# JSON 容错解析
# ─────────────────────────────────────────────────────────────

def _parse_profile_json(raw: str) -> dict:
    """兼容旧名：委托给公共解析器（抽取段与裁决段共用）。"""
    return parse_json_object(raw)


# ─────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────

async def _call_llm(
    provider,
    model: str,
    prompt: str,
    *,
    max_tokens: int = 1500,
) -> str:
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
            max_tokens=max_tokens,
            temperature=0.2,
        )
        async for event in events:
            text = getattr(event, "text", "")
            if text:
                out += text
    finally:
        provider.set_thinking_enabled(old_thinking)
    return out


def _call_llm_sync(provider, model: str, prompt: str, *, max_tokens: int = 1500) -> str:
    """同步包装：提炼主体跑在后台线程且无事件循环，两段调用都用它。"""
    return asyncio.run(_call_llm(provider, model, prompt, max_tokens=max_tokens))


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
            # 没内容也算"本轮处理完毕"：不推进的话同一段空增量会被反复重试
            report.advanceable = True
            return report

        provider = _build_refine_provider(settings)
        if provider is None:
            report.skipped = True
            report.reason = "无可用 LLM（mock 且未配置独立提炼模型）"
            report.advanceable = False  # 等配置好再试，不能吞掉这段增量
            return report

        model = getattr(settings, "profile_refine_model", "") or settings.model
        include_aliases = bool(getattr(settings, "profile_aliases_enabled", True))

        # ① 抽取：只读增量对话，**不判重复**（已有画像不进这一段 prompt）
        candidates, rejected = profile_resolve.extract_candidates(
            provider, model, dialog_text, _call_llm_sync
        )
        report.extracted = len(candidates)
        report.rejected = rejected

        if candidates:
            # ② 逐条召回：每条候选各自带 top-K 最像的旧条目
            top_k = int(getattr(settings, "profile_recall_top_k", 5) or 5)
            profile_resolve.attach_recalls(candidates, store, top_k)

            # ③ 四操作裁决：add / update / delete / noop
            decisions, rejected_resolve = profile_resolve.resolve_decisions(
                provider, model, candidates, _call_llm_sync,
                include_aliases=include_aliases,
            )
            report.rejected += rejected_resolve

            # ④ 落盘（存储层查重闸仍然生效）
            profile_resolve.apply_decisions(decisions, store, session_id, report)


        # 本轮写盘后再做一次全量归并：历史积压的深重复条目（修复前写入的）
        # 随任意一次提炼顺带清理；库超过 _MAX_AUTO_DEDUP_ENTRIES 时跳过（O(n²)），
        # 新重复由写入时的 merge_or_add 拦住，历史积压用 /memory dedup 手动清。
        if len(store) <= _MAX_AUTO_DEDUP_ENTRIES:
            retro = store.dedup_all()
            if retro:
                report.merged += retro

        # 容量上限：默认 0 = 不限（不淘汰）；用户显式配了上限才按权重淘汰
        max_entries = int(getattr(settings, "profile_max_entries", 0))
        pruned = store.prune_over_limit(max_entries)
        if pruned:
            report.reason = f"淘汰低价值条目 {pruned} 条"

        if report.added or report.updated or report.merged:
            logger.info(
                "画像提炼完成: session=%s +%d ~%d 合并重复 %d",
                session_id, report.added, report.updated, report.merged,
            )
        # 跑到这里说明整段增量已处理完（包括 0 条提取）：可以推进水位
        report.advanceable = True
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

def maybe_refine_async(
    messages: list[Message],
    session_id: str,
    settings: Any,
    *,
    force: bool = False,
    wait_seconds: float = 0.0,
) -> bool:
    """按**水位增量**异步触发提炼。返回是否真的启动了后台任务。

    三类触发共用本函数（2026-11-17 简化：空闲触发已下线）：

    - 阈值（每轮 `_auto_save`）：`delta ≥ profile_refine_min_delta`（默认 7）；
      **成功不节流**——水位 + 条数已限住频率，每 7 条才一次；
    - 终态（退出 / 切会话 / 新建）：`force=True`，只要求 `delta ≥ 1`；
    - 手动（`/memory refine`）：`force=True`。

    `profile_refine_interval` 只用于**失败退避**：上次提炼报错（`last_error`
    非空）时，间隔内不再重试，避免 provider 持续报错时每轮起线程。

    `wait_seconds > 0` 时阻塞等待提炼线程结束（上限该秒数）——仅退出路径用：
    后台线程是 daemon，进程退出会直接带走它，必须等一等才能真收干。
    超时无妨：水位不推进，尾巴会在下次触发时重提。
    """
    if not getattr(settings, "profile_enabled", False):
        return False

    sid = str(session_id or "default")
    total = len(messages)
    min_msgs = int(getattr(settings, "profile_refine_min_messages", 6))
    if total < min_msgs:
        return False  # 首提炼的绝对门槛，防两句话就开跑

    # 刷新 last_msg_ts；/rewind 或 /compact 让消息变少时顺带下调水位
    state = profile_ledger.touch(sid, total)
    delta = max(0, total - state.refined_until)

    min_delta = max(1, int(getattr(settings, "profile_refine_min_delta", 7)))
    required = 1 if force else min_delta
    if delta < required:
        return False

    interval = int(getattr(settings, "profile_refine_interval", REFINE_INTERVAL_SECONDS))
    now = time.time()
    # 只对“上次失败”退避：成功后下一批 7 条立刻提炼（用户要的“聊 7 条一次”）。
    # last_error 由 advance() 清空，mark_error() 写入。
    if not force and state.last_error and (now - state.last_attempt_ts) < interval:
        return False
    if not _in_flight.acquire(blocking=False):
        return False  # 已有线程在提炼

    profile_ledger.mark_attempt(sid, now=now)

    # 线程启动前就把增量快照下来：调用方随后可能 clear()/截断 messages
    increment = list(messages[state.refined_until:total])

    def _worker() -> None:
        try:
            report = refine_session(increment, sid, settings)
            if report.error:
                profile_ledger.mark_error(sid, report.error)
            elif report.advanceable:
                # 计数与水位同一次写入（P1d 遥测：/memory 能看到上轮提炼做了什么）
                profile_ledger.advance(
                    sid,
                    total,
                    report={
                        "extracted": report.extracted,
                        "added": report.added,
                        "updated": report.updated,
                        "deleted": report.deleted,
                        "merged": report.merged,
                        "noop": report.noop,
                        "rejected": report.rejected,
                    },
                )
            # advanceable=False 且无 error：本轮不推进，下次重试同一段增量
        finally:
            _in_flight.release()

    thread = threading.Thread(target=_worker, name="profile-refiner", daemon=True)
    thread.start()
    if wait_seconds > 0:
        thread.join(timeout=float(wait_seconds))
    return True
