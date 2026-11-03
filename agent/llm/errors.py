"""LLM 错误分类 —— 将原始 API 异常映射为用户可操作提示。

E-01 改进项：解决 LLM 错误信息不友好（如智谱 405 返回原始 HTML）的问题。

设计：
- classify() 根据 HTTP 状态码和错误消息关键词分类
- 返回 (category, user_message) —— category 供下游选恢复动作，user_message 直接给用户看
- user_message 包含 原始错误 + 通俗解释 + 操作建议

category 必须一路带到 ProviderError（用 `to_provider_error()`），否则下游只能靠匹配
文案子串选动作——那种耦合会让“改一句提示语”变成“静默关掉一条恢复路径”。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from agent.utils.mask import mask_error_message

if TYPE_CHECKING:  # 仅为类型注解引入，避开与 base 的循环导入（运行时在函数内 import）
    from agent.llm.base import ProviderError


class ErrorCategory(str, Enum):
    AUTH = "auth"              # 鉴权失败（API Key 无效/过期）
    RATE_LIMIT = "rate_limit"   # 限流（QPS 超限/配额用尽）
    MODEL_NOT_FOUND = "model_not_found"  # 模型不存在/无权访问
    CONTEXT_TOO_LONG = "context_too_long"  # 上下文超长
    NETWORK = "network"         # 网络超时/连接失败
    SERVER_ERROR = "server_error"  # 服务端错误（5xx）
    BAD_REQUEST = "bad_request"  # 参数错误（4xx）
    UNKNOWN = "unknown"         # 未分类


@dataclass
class ClassifiedError:
    """分类后的错误信息。"""
    category: ErrorCategory
    user_message: str    # 给用户看的中文消息（含原始错误 + 解释 + 建议）
    raw_message: str     # 原始错误消息（调试用）


def _extract_raw(exc: Exception) -> str:
    """从异常中提取原始错误消息，**含 __cause__ 链**。

    为什么必须往下挖（2026-10 排查教训）：openai SDK 会把底层 httpx 异常
    包成 ``APIConnectionError("Connection error.")``，而真正的原因——是 DNS
    解析失败、TLS 握手失败、还是服务端在响应前直接断开——**只存在于 cause 链
    里**。只取 ``str(exc)`` 的结果是所有连接类故障长得一模一样，界面上全都
    显示“请检查网络”，而用户那边网络明明是好的。

    @author aceFelix
    """
    msg = str(exc)
    # OpenAI SDK 的错误带有状态码和详情
    if hasattr(exc, "body") and isinstance(exc.body, dict):
        err = exc.body.get("error", {})
        code = err.get("code", "")
        detail = err.get("message", "")
        if code or detail:
            return f"[{code}] {detail}" if code else detail
    # HTTP 状态码
    if hasattr(exc, "status_code"):
        base = f"HTTP {exc.status_code}: {msg}" if msg else f"HTTP {exc.status_code}"
    else:
        base = msg

    # 顺着 cause/context 链把底层原因一起带出来
    chain: list[str] = []
    cur = exc.__cause__ or exc.__context__
    depth = 0
    while cur is not None and depth < 4:
        text = str(cur).strip()
        if text and text not in base:
            chain.append(f"{type(cur).__name__}: {text}")
        cur = cur.__cause__ or cur.__context__
        depth += 1
    if chain:
        base = f"{base} <- " + " <- ".join(chain)
    return base


def classify(exc: Exception) -> ClassifiedError:
    """将 LLM API 异常分类为可操作提示。

    Args:
        exc: 原始异常（openai.APIError / HTTPError 等）

    Returns:
        ClassifiedError，包含分类、用户消息和原始消息（已脱敏）

    @author aceFelix
    """
    raw = mask_error_message(_extract_raw(exc))
    raw_lower = raw.lower()
    status = getattr(exc, "status_code", 0) or 0

    # ── 厂商侧 5xx / 过载（HTTP 状态码是强证据，因此优先于关键词表）──
    # 为什么单独一条、而且放在最前面（2026-11-03 实机）：DeepSeek 过载时返回
    # `503 service_unavailable_error`「Service is too busy」，旧实现把 502/503/504 归进了
    # 下面的 NETWORK 兜底，于是界面叫用户「检查网络连接 / base_url / HTTP_PROXY」，而
    # 用户网络明明正常、请求也确实发出去并拿到了对方响应。专为 5xx 写的 SERVER_ERROR
    # 分支就在十几行之后，却因为被抢先命中而一次都没执行过。
    # 放最前面还避开两处误伤：504 的文案里常带 "timeout"，若让 timeout 分支先命中，
    # 就把厂商网关超时说成用户侧超时；关键词表里还有个 "token"，可能把带 token 字样的
    # 5xx 误判成上下文超长。
    if status in (502, 503, 504) or any(kw in raw_lower for kw in (
        "service_unavailable", "service is too busy", "server busy", "overloaded",
        "bad gateway", "gateway time", "temporarily unavailable", "upstream"
    )):
        return ClassifiedError(
            category=ErrorCategory.SERVER_ERROR,
            raw_message=raw,
            user_message=_build(
                raw, "厂商服务暂不可用",
                "模型服务商自己返回了 5xx/过载——请求已经发出并拿到了对方响应，"
                "所以这不是你的网络、base_url 或代理的问题（查 HTTP_PROXY 没有意义）。\n"
                "可稍后重试，或运行 /model <其他厂商模型> 暂时切走。\n"
                "想让 jarvis 在厂商报错时自动切到备选，请在配置里设置 vendor_fallback。"
            ),
        )

    # ── 鉴权失败 ──
    if status == 401 or any(kw in raw_lower for kw in (
        "invalid_api_key", "unauthorized", "authentication", "incorrect api key",
        "auth", "token", "key not found"
    )):
        return ClassifiedError(
            category=ErrorCategory.AUTH,
            raw_message=raw,
            user_message=_build(
                raw, "鉴权失败",
                "API Key 无效、已过期或未配置。\n"
                "请检查环境变量 DASHSCOPE_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY。\n"
                "或运行 /models 命令确认当前模型的 API Key 配置。"
            ),
        )

    # ── 限流 ──
    if status == 429 or any(kw in raw_lower for kw in (
        "rate_limit", "too many requests", "quota", "throttle",
        "请求过于频繁", "限流", "频率"
    )):
        return ClassifiedError(
            category=ErrorCategory.RATE_LIMIT,
            raw_message=raw,
            user_message=_build(
                raw, "请求限流",
                "API 调用频率超限或配额用尽。\n"
                "请稍后重试，或检查 API 账户的额度余额。\n"
                "可输入 /model <名称> 切换到其他模型继续使用。"
            ),
        )

    # ── 模型不存在 ──
    if status == 404 or any(kw in raw_lower for kw in (
        "model_not_found", "model does not exist", "not found",
        "no such model", "deployment"
    )):
        return ClassifiedError(
            category=ErrorCategory.MODEL_NOT_FOUND,
            raw_message=raw,
            user_message=_build(
                raw, "模型不可用",
                "当前模型不存在或无权访问。\n"
                "请尝试：\n"
                "  /models → 查看可用模型列表\n"
                "  /model <名称> → 切换到其他模型"
            ),
        )

    # ── 上下文超长 ──
    if any(kw in raw_lower for kw in (
        "context_length", "too long", "reduce length",
        "prompt_too_long", "maximum context", "token",
        "上下文", "超出"
    )):
        return ClassifiedError(
            category=ErrorCategory.CONTEXT_TOO_LONG,
            raw_message=raw,
            user_message=_build(
                raw, "上下文过长",
                "对话历史 Token 数超过了模型上下文窗口上限。\n"
                "Jarvis 会自动压缩后重试。如持续报错，可运行 /compact 手动压缩。"
            ),
        )

    # ── 网络：先分清「真超时」与「连接被对端断开」──
    # 两者提示差别很大：超时才可能是本地网络/代理；连接被断多半是上游服务端
    # 或中间网关主动关的，让用户去查 HTTP_PROXY 是纯误导。实机 2026-10：
    # 用户网络实测完全正常，界面上却一直在让他“检查网络连接”。
    # @author aceFelix
    if "timeout" in raw_lower or "timed out" in raw_lower:
        return ClassifiedError(
            category=ErrorCategory.NETWORK,
            raw_message=raw,
            user_message=_build(
                raw, "请求超时",
                "等待 API 响应超时（请求已发出，但模型迟迟未开始返回）。\n"
                "如果用的是推理型模型，思考耗时较长时会更容易触发。\n"
                "可先重试一次；连续发生时切到 /model <非推理模型> 试试。"
            ),
        )
    if any(kw in raw_lower for kw in (
        "disconnected", "remote protocol", "server disconnected",
        "connection reset", "broken pipe", "eof occurred",
        "incomplete", "peer closed", "stream closed"
    )):
        return ClassifiedError(
            category=ErrorCategory.NETWORK,
            raw_message=raw,
            user_message=_build(
                raw, "连接被中断",
                "服务端在响应完成前断开了连接。这通常是**上游临时故障**或网关\n"
                "空闲超时，不是你本地网络的问题（此时查 HTTP_PROXY 没有意义）。\n"
                "重试一次通常可恢复；若每次都在同一位置断开，请把日志反馈给维护者。"
            ),
        )
    if any(kw in raw_lower for kw in (
        "timeout", "connection", "refused",
        "network", "resolve"
    )):
        return ClassifiedError(
            category=ErrorCategory.NETWORK,
            raw_message=raw,
            user_message=_build(
                raw, "网络错误",
                "API 服务连接失败或超时。\n"
                "请检查网络连接，或确认 base_url 配置是否正确。\n"
                "如使用代理，请检查 HTTP_PROXY 环境变量。"
            ),
        )

    # ── 服务端错误 ──
    if 500 <= status < 600:
        return ClassifiedError(
            category=ErrorCategory.SERVER_ERROR,
            raw_message=raw,
            user_message=_build(
                raw, "服务端错误",
                "LLM API 服务暂时异常。通常短暂存在，稍后自动恢复。\n"
                "如持续出现，可运行 /model <备选> 切换到其他厂商。"
            ),
        )

    # ── 参数错误 ──
    if 400 <= status < 500:
        return ClassifiedError(
            category=ErrorCategory.BAD_REQUEST,
            raw_message=raw,
            user_message=_build(
                raw, "请求参数错误",
                "发送给 API 的请求格式不正确。\n"
                "可能是模型不支持当前工具格式，或参数类型不匹配。\n"
                "尝试 /model 切换到兼容的模型。"
            ),
        )

    # ── 未分类 ──
    return ClassifiedError(
        category=ErrorCategory.UNKNOWN,
        raw_message=raw,
        user_message=_build(raw, "调用失败", "LLM API 返回了未识别的错误。"),
    )


def _build(raw: str, title: str, explanation: str) -> str:
    """构造用户可读的错误消息。

    格式：原始错误 + 分类标题 + 通俗解释
    """
    return (
        f"原始错误: {raw}\n"
        f"\n[ {title} ]\n"
        f"{explanation}"
    )


def effective_category(exc: BaseException) -> str:
    """取 ProviderError 的 category；缺失时从用户文案回退推断。

    为什么必须留回退：仓内与测试里都存在 `ProviderError("网络错误: timeout")` 这种裸
    构造（ScriptedProvider 场景），它们没有 category。若只认 category，这些调用点的
    重试行为会静默变化。回退仅用于兜底：有 category 时一律以 category 为准。

    回退表里的文案必须与 classify() 各分支的标题一致（两边改动要同步）。

    @author aceFelix
    """
    cat = str(getattr(exc, "category", "") or "")
    if cat:
        return cat
    text = str(exc).lower()
    if any(kw in text for kw in ("网络错误", "请求超时", "连接被中断")):
        return ErrorCategory.NETWORK.value
    if "请求限流" in text:
        return ErrorCategory.RATE_LIMIT.value
    if any(kw in text for kw in ("厂商服务暂不可用", "服务端错误")):
        return ErrorCategory.SERVER_ERROR.value
    if "鉴权失败" in text:
        return ErrorCategory.AUTH.value
    if "模型不可用" in text:
        return ErrorCategory.MODEL_NOT_FOUND.value
    if "上下文过长" in text:
        return ErrorCategory.CONTEXT_TOO_LONG.value
    return ""


def to_provider_error(exc: Exception) -> ProviderError:
    """把原始异常分类后包成 ProviderError，并**带上 category**。

    四个 Provider 的 except 分支以前各写一遍
    `raise ProviderError(classify(e).user_message)`，分类结果就在这一步被丢成了字符串。
    统一收到这个工厂里：新加 Provider 不会漏传 category，下游（query_loop）也能拿到
    结构化分类，而不是去匹配中文文案。（aceFelix，2026-11-03）

    @author aceFelix
    """
    from agent.llm.base import ProviderError

    classified = classify(exc)
    return ProviderError(classified.user_message, classified.category.value)
