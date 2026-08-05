"""LLM 上下文缓存策略管理 —— 配置表驱动的多厂商缓存适配。

统一管理不同厂商的上下文缓存机制：

- **显式缓存**（DashScope / Anthropic）：需要在请求中注入 `cache_control` 标记，
  标记位置即缓存块终点。DashScope 最多 4 个标记、最小 1024 token、命中 10% 价。
- **隐式缓存**（DeepSeek / 智谱 ZAI / OpenAI）：服务端自动识别公共前缀，
  无需任何标记，只要前缀稳定即可命中。

设计（与 thinking.py / provider_registry.py 同源）：
- CachePolicy 数据类描述一个厂商的缓存规则
- CACHE_POLICIES 是唯一的缓存策略信息源，新增厂商只加一行
- apply_cache_markers() 按策略注入 cache_control 标记（显式模式）
- parse_cache_usage() 把各家 usage 字段归一化为统一的 Usage 统计

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CachePolicy:
    """单个厂商的上下文缓存策略。

    @author aceFelix
    """

    # 缓存模式: explicit（需手动标记）/ implicit（自动，无需标记）
    mode: str = "implicit"
    # 显式模式下单个请求允许的最大标记数（DashScope=4，Anthropic 官方无硬性限制）
    max_markers: int = 0
    # 显式缓存最小可缓存 token 数（DashScope=1024，低于此值标记无效）
    min_cache_tokens: int = 0
    # 命中缓存的价格折扣（0.1 = 标准价的 10%），用于 /cost 成本估算
    hit_discount: float = 1.0
    # 缓存 TTL（秒），显式缓存有限期，隐式为 0（系统管理）
    ttl_seconds: int = 0
    # 命中统计字段名（供 parse_cache_usage 归一化）
    #   prompt_cache_hit_tokens       → DeepSeek
    #   prompt_tokens_details.cached_tokens → DashScope/ZAI/OpenAI
    #   cache_read_input_tokens       → Anthropic
    hit_field: str = "cached_tokens"
    # 备选命中字段（主字段读不到时依次尝试）——用于 Anthropic 兼容端点
    # 的第三方实现（如 DeepSeek 返回 cache_read_input_tokens 但语义异常）
    alt_hit_fields: tuple[str, ...] = ()
    # 创建统计字段名
    #   cache_creation_input_tokens   → DashScope/Anthropic
    create_field: str = "cache_creation_input_tokens"
    # 是否支持在 tools 上打标记（Anthropic 支持；DashScope 文档明确不支持）
    tools_marker: bool = False


# ═══════════════════════════════════════════════════════════════
# 缓存策略注册表 —— 新增厂商只加这一行
# key 与 provider_registry 的厂商名保持一致（self.name 解析结果）
# ═══════════════════════════════════════════════════════════════
CACHE_POLICIES: dict[str, CachePolicy] = {
    # ── DashScope（阿里云百炼）：显式缓存，命中 10%，最多 4 标记 ──
    "dashscope": CachePolicy(
        mode="explicit",
        max_markers=4,
        min_cache_tokens=1024,
        hit_discount=0.1,
        ttl_seconds=300,
        hit_field="prompt_tokens_details.cached_tokens",
        create_field="prompt_tokens_details.cache_creation_input_tokens",
        tools_marker=False,  # 文档明确：工具定义不支持独立缓存
    ),
    # DashScope 原生 SDK 路径
    "dashscope_sdk": CachePolicy(
        mode="explicit",
        max_markers=4,
        min_cache_tokens=1024,
        hit_discount=0.1,
        ttl_seconds=300,
        hit_field="prompt_cache_hit_tokens",
        create_field="prompt_cache_creation_tokens",
        tools_marker=False,
    ),
    # ── DeepSeek：隐式硬盘缓存，前缀单元完整匹配，无需标记 ──
    # Anthropic 兼容端点返回 cache_read_input_tokens 但语义异常（累计值），
    # 作为备选字段并带合理性校验（命中数不可能远超输入数）
    "deepseek": CachePolicy(
        mode="implicit",
        hit_discount=0.5,
        hit_field="prompt_cache_hit_tokens",
        alt_hit_fields=("cache_read_input_tokens",),
        create_field="",
    ),
    # ── 智谱 AI（OpenAI 兼容 + 原生 SDK）：隐式，内容相似度自动触发 ──
    "zhipu": CachePolicy(
        mode="implicit",
        hit_discount=0.5,
        hit_field="prompt_tokens_details.cached_tokens",
        create_field="",
    ),
    "zai": CachePolicy(
        mode="implicit",
        hit_discount=0.5,
        hit_field="prompt_tokens_details.cached_tokens",
        create_field="",
    ),
    "zai_sdk": CachePolicy(
        mode="implicit",
        hit_discount=0.5,
        hit_field="prompt_tokens_details.cached_tokens",
        create_field="",
    ),
    # ── Anthropic：显式断点缓存，system+tools 标记，命中 90% off ──
    "anthropic": CachePolicy(
        mode="explicit",
        max_markers=8,
        min_cache_tokens=0,
        hit_discount=0.1,
        ttl_seconds=300,
        hit_field="cache_read_input_tokens",
        create_field="cache_creation_input_tokens",
        tools_marker=True,  # Anthropic 支持在 tools 上打断点
    ),
    # ── OpenAI 官方：自动前缀缓存（无标记、无显式断点）──
    "openai": CachePolicy(
        mode="implicit",
        hit_discount=0.5,
        hit_field="prompt_tokens_details.cached_tokens",
        create_field="",
    ),
    # ── 其他 OpenAI 兼容（Moonshot/MiniMax/MiMo/SiliconFlow 等）──
    # 缓存策略不透明，按隐式处理，只做统计
    "moonshot": CachePolicy(mode="implicit", hit_discount=1.0),
    "minimax": CachePolicy(mode="implicit", hit_discount=1.0),
    "mimo": CachePolicy(mode="implicit", hit_discount=1.0),
    "siliconflow": CachePolicy(mode="implicit", hit_discount=1.0),
    "google": CachePolicy(mode="implicit", hit_discount=1.0),
    "openai_compatible": CachePolicy(mode="implicit", hit_discount=1.0),
}


def get_cache_policy(name: str) -> CachePolicy | None:
    """按厂商名查缓存策略。未注册返回 None（不注入、不统计）。

    @author aceFelix
    """
    return CACHE_POLICIES.get(name)


# ═══════════════════════════════════════════════════════════════
# 标记注入（显式缓存模式）
# ═══════════════════════════════════════════════════════════════

def apply_cache_markers(
    api_msgs: list[dict[str, Any]],
    policy: CachePolicy,
    *,
    tool_defs: list[Any] | None = None,
) -> None:
    """按策略在消息中注入 cache_control 标记（就地修改 api_msgs）。

    注入位置（按稳定性排序）：
    1. system 消息 —— 最稳定，几乎不变
    2. 最后一条 user 消息 —— 滚动标记，命中即续期

    仅显式模式（explicit）生效；隐式模式自动缓存，注入反而可能
    改变消息结构破坏前缀，因此不动。

    Args:
        api_msgs: 已转换为厂商 API 格式的消息列表
        policy: 缓存策略
        tool_defs: 工具定义（Anthropic 支持在此打标记，DashScope 不支持）

    @author aceFelix
    """
    if policy.mode != "explicit":
        return

    markers = 0

    # 1. system 消息末尾标记
    for msg in api_msgs:
        if msg.get("role") == "system":
            _add_marker(msg, policy)
            markers += 1
            break

    # 2. 最后一条 user 消息滚动标记（避免与 system 是同一处时重复）
    if markers < policy.max_markers:
        for msg in reversed(api_msgs):
            if msg.get("role") == "user":
                _add_marker(msg, policy)
                markers += 1
                break

    # 3. tools 标记（仅 Anthropic 支持）
    if tool_defs and policy.tools_marker and markers < policy.max_markers:
        if tool_defs:
            last = tool_defs[-1]
            if isinstance(last, dict):
                last["cache_control"] = {"type": "ephemeral"}


def _add_marker(msg: dict[str, Any], policy: CachePolicy) -> None:
    """给单条消息的 content 添加 cache_control 标记。

    处理两种 content 形态：
    - 字符串：转为 [{"type": "text", "text": ..., "cache_control": ...}]
    - 列表：在最后一个 content 块上添加（Anthropic 格式）
    - dict：OpenAI 单块形态，原地添加

    @author aceFelix
    """
    content = msg.get("content")
    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = {"type": "ephemeral"}
    elif isinstance(content, dict):
        content["cache_control"] = {"type": "ephemeral"}


# ═══════════════════════════════════════════════════════════════
# 命中统计归一化
# ═══════════════════════════════════════════════════════════════

def parse_cache_usage(
    usage_obj: Any,
    policy: CachePolicy,
    input_tokens: int = 0,
    *,
    input_includes_cache: bool = True,
) -> tuple[int, int]:
    """从厂商响应的 usage 中提取缓存命中/创建 token 数。

    支持三种 usage 形态：
    - OpenAI SDK 对象（chunk.usage，属性访问）
    - DashScope SDK dict（resp.usage["prompt_cache_hit_tokens"]）
    - Anthropic SDK 对象（usage.cache_read_input_tokens）

    Args:
        usage_obj: 厂商返回的 usage（对象或 dict）
        policy: 缓存策略（提供字段名）
        input_tokens: 本次请求输入 token 数（用于备选字段合理性校验；
            备选命中数若远超输入数，视为端点实现异常，丢弃该值）
        input_includes_cache: input_tokens 是否已包含缓存命中部分。
            - True（默认，OpenAI/DashScope 兼容协议）：input_tokens 含缓存，
              命中数远超输入数 → 视为累计值异常，丢弃。
            - False（Anthropic 协议）：input_tokens 仅含未命中部分，
              命中数远大于 input_tokens 是正常的（如 system prompt 全命中），
              不做累计值校验。

    Returns:
        (cache_read_tokens, cache_creation_tokens)

    @author aceFelix
    """
    if usage_obj is None:
        return 0, 0

    def _get(d: Any, field: str) -> int:
        """按 a.b.c 路径取值，兼容对象和 dict。"""
        if not field:
            return 0
        cur = d
        for part in field.split("."):
            if cur is None:
                return 0
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = getattr(cur, part, None)
        return cur or 0

    # 主字段
    read = _get(usage_obj, policy.hit_field) if policy.hit_field else 0
    # 主字段读不到 → 依次尝试备选字段（带合理性校验）
    if not read and policy.alt_hit_fields:
        for field in policy.alt_hit_fields:
            v = _get(usage_obj, field)
            if not v:
                continue
            # 合理性校验：命中数不应远超本次输入数。
            # 仅当 input_tokens 含缓存（OpenAI/DashScope 协议）时校验——
            # 此协议下命中数是 input_tokens 的子集，远超则视为累计值异常。
            # Anthropic 协议下 input_tokens 不含缓存，命中数可能远大于
            # input_tokens（如 system prompt 全命中），不应丢弃。
            if input_includes_cache and input_tokens and v > input_tokens * 3:
                continue
            read = v
            break

    created = _get(usage_obj, policy.create_field) if policy.create_field else 0
    return read, created


def merge_tool_results(messages: list[Message]) -> list[Message]:
    """合并连续同角色消息（DashScope 20 个 content 块回溯优化）。

    并行工具调用会产生多条连续 tool 消息，每条占一个 content 块。
    消息过多时可能使 cache_control 标记超出 20 块回溯窗口导致缓存失效。
    将连续 tool 结果合并为单条消息 + 多个 content 块，减少块层级。

    注意：为保持与现有 query_loop 兼容，此函数目前仅作预留，
    不改变默认行为（返回原列表）。

    @author aceFelix
    """
    return messages
