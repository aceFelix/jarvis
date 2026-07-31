"""LLM 错误分类 —— 将原始 API 异常映射为用户可操作提示。

E-01 改进项：解决 LLM 错误信息不友好（如智谱 405 返回原始 HTML）的问题。

设计：
- classify() 根据 HTTP 状态码和错误消息关键词分类
- 返回 (category, user_message) —— category 用于日志分类，user_message 直接给用户看
- user_message 包含 原始错误 + 通俗解释 + 操作建议

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent.utils.mask import mask_error_message


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
    """从异常中提取原始错误消息。"""
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
        return f"HTTP {exc.status_code}: {msg}" if msg else f"HTTP {exc.status_code}"
    return msg


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

    # ── 网络超时 ──
    if any(kw in raw_lower for kw in (
        "timeout", "timed out", "connection", "refused",
        "network", "resolve"
    )) or status in (502, 503, 504):
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
