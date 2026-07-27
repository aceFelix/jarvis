"""工具错误自愈 —— 错误分类、重试、降级、恢复策略。

P0 升级：工具调用失败后，先由 RecoveryExecutor 尝试自动修复，
而不是直接把错误抛给 LLM。覆盖网络抖动、超时、文件缺失、
API 限流、权限不足等高频可恢复错误。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agent.core.context import ToolContext
from agent.core.result import ToolResult


logger = logging.getLogger(__name__)


@dataclass
class RecoveryIncident:
    """一次工具自愈事件记录。"""

    tool_name: str
    category: ToolErrorCategory
    recoverable: bool
    attempts: int
    resolved: bool
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    message: str = ""


class RecoveryTelemetry:
    """自愈遥测：记录最近失败事件，供 /doctor 展示。

    单例，进程内共享。

    @author aceFelix
    """

    _instance: "RecoveryTelemetry | None" = None

    def __new__(cls) -> "RecoveryTelemetry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._incidents: list[RecoveryIncident] = []
            cls._instance._max_history = 50
        return cls._instance

    def record(
        self,
        tool_name: str,
        category: ToolErrorCategory,
        recoverable: bool,
        attempts: int,
        resolved: bool,
        message: str = "",
    ) -> None:
        """记录一次自愈事件。"""
        incident = RecoveryIncident(
            tool_name=tool_name,
            category=category,
            recoverable=recoverable,
            attempts=attempts,
            resolved=resolved,
            message=message,
        )
        self._incidents.append(incident)
        if len(self._incidents) > self._max_history:
            self._incidents = self._incidents[-self._max_history :]

    def get_summary(self) -> dict[str, Any]:
        """返回自愈统计摘要。"""
        total = len(self._incidents)
        resolved = sum(1 for i in self._incidents if i.resolved)
        by_category: dict[str, int] = {}
        for i in self._incidents:
            key = i.category.value
            by_category[key] = by_category.get(key, 0) + 1
        return {
            "total_incidents": total,
            "resolved": resolved,
            "unresolved": total - resolved,
            "by_category": by_category,
        }

    def get_recent(self, n: int = 10) -> list[RecoveryIncident]:
        """返回最近 n 次事件。"""
        return self._incidents[-n:]

    def top_category(self) -> str | None:
        """返回出现次数最多的错误分类。"""
        by_category: dict[str, int] = {}
        for i in self._incidents:
            key = i.category.value
            by_category[key] = by_category.get(key, 0) + 1
        if not by_category:
            return None
        return max(by_category.items(), key=lambda x: x[1])[0]

    def clear(self) -> None:
        """清空历史。"""
        self._incidents = []



class ToolErrorCategory(enum.Enum):
    """工具错误分类。"""

    OK = "ok"                              # 不是错误
    NETWORK_TRANSIENT = "network_transient"  # 临时网络错误（可重试）
    RATE_LIMIT = "rate_limit"              # API 限流（退避重试）
    TIMEOUT = "timeout"                    # 命令/请求超时（可重试或延长）
    NOT_FOUND = "not_found"                # 文件/目录/资源不存在
    PERMISSION_DENIED = "permission_denied"  # 权限不足
    AUTH_MISSING = "auth_missing"          # 缺少认证信息（API Key 等）
    DEPENDENCY_MISSING = "dependency_missing"  # 缺少外部依赖
    CONFIG_INVALID = "config_invalid"      # 配置错误
    UNKNOWN = "unknown"                    # 未知错误


# 分类到人类可读原因
_CATEGORY_REASONS: dict[ToolErrorCategory, str] = {
    ToolErrorCategory.NETWORK_TRANSIENT: "临时网络错误",
    ToolErrorCategory.RATE_LIMIT: "API 限流",
    ToolErrorCategory.TIMEOUT: "执行超时",
    ToolErrorCategory.NOT_FOUND: "资源不存在",
    ToolErrorCategory.PERMISSION_DENIED: "权限不足",
    ToolErrorCategory.AUTH_MISSING: "缺少认证信息",
    ToolErrorCategory.DEPENDENCY_MISSING: "缺少外部依赖",
    ToolErrorCategory.CONFIG_INVALID: "配置错误",
    ToolErrorCategory.UNKNOWN: "未知错误",
}


@dataclass
class ClassifiedError:
    """分类后的错误信息。"""

    category: ToolErrorCategory
    reason: str
    recoverable: bool
    suggestion: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryPolicy:
    """针对某类错误的自愈策略。"""

    category: ToolErrorCategory
    max_retries: int = 0
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    # 是否尝试修复输入/环境后再重试
    auto_fix: bool = False
    # 修复失败后是否询问用户
    ask_user_on_fail: bool = True
    # 日志级别
    log_level: str = "warning"


# 默认策略表：越可恢复的错误，给越多重试
DEFAULT_POLICIES: dict[ToolErrorCategory, RecoveryPolicy] = {
    ToolErrorCategory.NETWORK_TRANSIENT: RecoveryPolicy(
        category=ToolErrorCategory.NETWORK_TRANSIENT,
        max_retries=3,
        backoff_base_seconds=1.0,
        backoff_max_seconds=10.0,
        auto_fix=False,
        ask_user_on_fail=True,
    ),
    ToolErrorCategory.RATE_LIMIT: RecoveryPolicy(
        category=ToolErrorCategory.RATE_LIMIT,
        max_retries=3,
        backoff_base_seconds=2.0,
        backoff_max_seconds=30.0,
        auto_fix=False,
        ask_user_on_fail=True,
    ),
    ToolErrorCategory.TIMEOUT: RecoveryPolicy(
        category=ToolErrorCategory.TIMEOUT,
        max_retries=2,
        backoff_base_seconds=1.0,
        backoff_max_seconds=10.0,
        auto_fix=True,
        ask_user_on_fail=True,
    ),
    ToolErrorCategory.NOT_FOUND: RecoveryPolicy(
        category=ToolErrorCategory.NOT_FOUND,
        max_retries=1,
        backoff_base_seconds=0.5,
        backoff_max_seconds=2.0,
        auto_fix=True,
        ask_user_on_fail=True,
    ),
    ToolErrorCategory.PERMISSION_DENIED: RecoveryPolicy(
        category=ToolErrorCategory.PERMISSION_DENIED,
        max_retries=0,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        auto_fix=False,
        ask_user_on_fail=True,
    ),
    ToolErrorCategory.AUTH_MISSING: RecoveryPolicy(
        category=ToolErrorCategory.AUTH_MISSING,
        max_retries=0,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        auto_fix=False,
        ask_user_on_fail=True,
    ),
    ToolErrorCategory.DEPENDENCY_MISSING: RecoveryPolicy(
        category=ToolErrorCategory.DEPENDENCY_MISSING,
        max_retries=1,
        backoff_base_seconds=1.0,
        backoff_max_seconds=5.0,
        auto_fix=True,
        ask_user_on_fail=True,
    ),
    ToolErrorCategory.CONFIG_INVALID: RecoveryPolicy(
        category=ToolErrorCategory.CONFIG_INVALID,
        max_retries=1,
        backoff_base_seconds=0.5,
        backoff_max_seconds=2.0,
        auto_fix=True,
        ask_user_on_fail=True,
    ),
    ToolErrorCategory.UNKNOWN: RecoveryPolicy(
        category=ToolErrorCategory.UNKNOWN,
        max_retries=1,
        backoff_base_seconds=1.0,
        backoff_max_seconds=5.0,
        auto_fix=False,
        ask_user_on_fail=True,
    ),
}


class ToolErrorClassifier:
    """工具错误分类器。

    根据 ToolResult.data（字符串）、异常类型、工具名进行模式匹配，
    输出错误分类和修复建议。

    @author aceFelix
    """

    # 网络相关关键字（不区分大小写）
    _NETWORK_PATTERNS = (
        r"timeout",
        r"timed out",
        r"connection",
        r"connect",
        r"reset by peer",
        r"broken pipe",
        r"dns",
        r"name resolution",
        r"unreachable",
        r"refused",
        r"ssl",
        r"certificate",
        r"network",
        r"urlopen error",
        r"temporary failure",
        r"could not connect",
    )

    # 限流相关
    _RATE_LIMIT_PATTERNS = (
        r"rate limit",
        r"too many requests",
        r"429",
        r"throttl",
        r"quota exceeded",
        r"limit exceeded",
    )

    # 资源不存在
    _NOT_FOUND_PATTERNS = (
        r"no such file or directory",
        r"file not found",
        r"does not exist",
        r"cannot find",
        r"找不到",
        r"不存在",
        r"not a valid",
        r"repository not found",
        r"package not found",
    )

    # 权限不足
    _PERMISSION_PATTERNS = (
        r"permission denied",
        r"access denied",
        r"forbidden",
        r"403",
        r"无权访问",
        r"拒绝访问",
    )

    # 认证缺失
    _AUTH_PATTERNS = (
        r"api key",
        r"apikey",
        r"api_key",
        r"authentication",
        r"unauthorized",
        r"401",
        r"缺少.*key",
        r"未配置.*key",
        r"未设置.*key",
        r"未授权",
    )

    # 依赖缺失
    _DEPENDENCY_PATTERNS = (
        r"command not found",
        r"not recognized",
        r"not installed",
        r"no module named",
        r"cannot find module",
        r"missing dependency",
        r"依赖缺失",
        r"未安装",
    )

    # 配置错误
    _CONFIG_PATTERNS = (
        r"invalid config",
        r"configuration error",
        r"配置错误",
        r"配置无效",
        r"bad request",
        r"400",
    )

    def classify(
        self,
        tool_name: str,
        result: ToolResult | None,
        exception: BaseException | None = None,
    ) -> ClassifiedError:
        """对工具执行失败进行分类。

        Args:
            tool_name: 工具名称
            result: 工具返回结果（可能为 None）
            exception: 执行时抛出的异常（可选）

        Returns:
            ClassifiedError，包含 category / reason / recoverable / suggestion
        """
        text = ""
        if exception is not None:
            text += f" {type(exception).__name__}: {exception}"
        if result is not None and isinstance(result.data, str):
            text += f" {result.data}"
        text_lower = text.lower()

        # 1. 超时（网络超时放在这里，命令超时也在这里）
        if isinstance(exception, subprocess.TimeoutExpired) or isinstance(exception, asyncio.TimeoutError):
            return ClassifiedError(
                category=ToolErrorCategory.TIMEOUT,
                reason="执行超时",
                recoverable=True,
                suggestion="可尝试延长超时时间或重试。",
            )

        # 1.5 文本里的 timeout 也优先判为超时（高于网络错误）
        if self._match_any(text_lower, (r"\btimeout\b", r"\btimed out\b")):
            return ClassifiedError(
                category=ToolErrorCategory.TIMEOUT,
                reason="执行超时",
                recoverable=True,
                suggestion="可尝试延长超时时间或重试。",
            )

        # 2. 限流（优先级高于一般网络错误）
        if self._match_any(text_lower, self._RATE_LIMIT_PATTERNS):
            return ClassifiedError(
                category=ToolErrorCategory.RATE_LIMIT,
                reason="API 限流",
                recoverable=True,
                suggestion="稍后重试，或降低请求频率。",
            )

        # 3. 认证缺失
        if self._match_any(text_lower, self._AUTH_PATTERNS):
            return ClassifiedError(
                category=ToolErrorCategory.AUTH_MISSING,
                reason="缺少认证信息",
                recoverable=False,
                suggestion="请检查 API Key / Token 是否配置正确。",
            )

        # 4. 权限不足
        if self._match_any(text_lower, self._PERMISSION_PATTERNS):
            return ClassifiedError(
                category=ToolErrorCategory.PERMISSION_DENIED,
                reason="权限不足",
                recoverable=False,
                suggestion="请检查文件/接口权限，或以管理员权限运行。",
            )

        # 5. 资源不存在
        if self._match_any(text_lower, self._NOT_FOUND_PATTERNS):
            return ClassifiedError(
                category=ToolErrorCategory.NOT_FOUND,
                reason="资源不存在",
                recoverable=True,
                suggestion="检查路径是否正确，或尝试创建缺失的目录/文件。",
            )

        # 6. 依赖缺失
        if self._match_any(text_lower, self._DEPENDENCY_PATTERNS):
            return ClassifiedError(
                category=ToolErrorCategory.DEPENDENCY_MISSING,
                reason="缺少外部依赖",
                recoverable=True,
                suggestion="安装缺失的命令或 Python 包。",
            )

        # 7. 配置错误
        if self._match_any(text_lower, self._CONFIG_PATTERNS):
            return ClassifiedError(
                category=ToolErrorCategory.CONFIG_INVALID,
                reason="配置错误",
                recoverable=True,
                suggestion="检查工具入参或配置文件。",
            )

        # 8. 临时网络错误
        if self._match_any(text_lower, self._NETWORK_PATTERNS):
            return ClassifiedError(
                category=ToolErrorCategory.NETWORK_TRANSIENT,
                reason="临时网络错误",
                recoverable=True,
                suggestion="检查网络连接后重试。",
            )

        return ClassifiedError(
            category=ToolErrorCategory.UNKNOWN,
            reason="未知错误",
            recoverable=False,
            suggestion="查看详细错误信息，或手动修复后重试。",
        )

    @staticmethod
    def _match_any(text: str, patterns: tuple[str, ...]) -> bool:
        """判断 text 是否匹配任意正则模式。"""
        for pat in patterns:
            try:
                if re.search(pat, text, re.IGNORECASE):
                    return True
            except re.error:
                continue
        return False


@dataclass
class RecoveryAttempt:
    """一次自愈尝试记录。"""

    attempt: int
    action: str
    success: bool
    message: str = ""
    new_input: dict[str, Any] | None = None


@dataclass
class RecoveryResult:
    """自愈执行结果。"""

    original_error: ClassifiedError
    final_result: ToolResult
    attempts: list[RecoveryAttempt] = field(default_factory=list)
    asked_user: bool = False
    user_answer: str | None = None


class ToolRecoveryExecutor:
    """工具自愈执行器。

    包装 Tool.call()，在失败时按策略自动重试、降级或询问用户。

    @author aceFelix
    """

    def __init__(
        self,
        classifier: ToolErrorClassifier | None = None,
        policies: dict[ToolErrorCategory, RecoveryPolicy] | None = None,
        global_enabled: bool = True,
    ) -> None:
        self._classifier = classifier or ToolErrorClassifier()
        self._policies = policies or dict(DEFAULT_POLICIES)
        self._global_enabled = global_enabled

    def is_enabled(self) -> bool:
        """是否启用自愈。"""
        return self._global_enabled

    async def execute(
        self,
        tool_name: str,
        call_fn: Callable[[dict[str, Any], ToolContext], Any],
        args: dict[str, Any],
        ctx: ToolContext,
        tool_is_read_only: bool = False,
    ) -> RecoveryResult:
        """执行工具调用，失败时尝试自愈。

        Args:
            tool_name: 工具名
            call_fn: 实际的 tool.call 可调用对象
            args: 工具入参
            ctx: ToolContext
            tool_is_read_only: 工具是否为只读（只读错误可更激进重试）

        Returns:
            RecoveryResult，final_result 为最终成功或失败结果。
        """
        effective_args = dict(args)
        attempts: list[RecoveryAttempt] = []

        try:
            result = await call_fn(effective_args, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error = self._classifier.classify(tool_name, None, e)
            result = ToolResult.error(f"工具执行异常: {type(e).__name__}: {e}")
        else:
            if not result.is_error:
                return RecoveryResult(
                    original_error=ClassifiedError(
                        category=ToolErrorCategory.OK,
                        reason="成功",
                        recoverable=False,
                    ),
                    final_result=result,
                    attempts=attempts,
                )
            error = self._classifier.classify(tool_name, result)

        # 未启用自愈，直接返回错误
        if not self._global_enabled:
            RecoveryTelemetry().record(
                tool_name=tool_name,
                category=error.category,
                recoverable=error.recoverable,
                attempts=0,
                resolved=False,
                message="自愈已禁用",
            )
            return RecoveryResult(
                original_error=error,
                final_result=result,
                attempts=attempts,
            )

        policy = self._policies.get(error.category, DEFAULT_POLICIES[ToolErrorCategory.UNKNOWN])

        # 只读工具可额外多一次重试
        max_retries = policy.max_retries + (1 if tool_is_read_only and policy.max_retries > 0 else 0)

        for attempt in range(1, max_retries + 1):
            # 先尝试 auto_fix
            if policy.auto_fix:
                fixed, fixed_args, fix_msg = self._try_auto_fix(
                    tool_name, error, effective_args, ctx
                )
                attempts.append(RecoveryAttempt(
                    attempt=attempt,
                    action="auto_fix",
                    success=fixed,
                    message=fix_msg,
                    new_input=fixed_args if fixed else None,
                ))
                if fixed:
                    effective_args = fixed_args or effective_args

            # 指数退避
            delay = min(
                policy.backoff_base_seconds * (2 ** (attempt - 1)),
                policy.backoff_max_seconds,
            )
            if delay > 0:
                if ctx.ui:
                    ctx.ui.info(
                        f"{tool_name} 遇到 {_CATEGORY_REASONS.get(error.category, error.reason)}，"
                        f"{delay:.1f} 秒后第 {attempt}/{max_retries} 次重试..."
                    )
                await asyncio.sleep(delay)

            try:
                result = await call_fn(effective_args, ctx)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                result = ToolResult.error(f"工具执行异常: {type(e).__name__}: {e}")
                error = self._classifier.classify(tool_name, result, e)
            else:
                if not result.is_error:
                    RecoveryTelemetry().record(
                        tool_name=tool_name,
                        category=error.category,
                        recoverable=error.recoverable,
                        attempts=len(attempts),
                        resolved=True,
                        message="重试后成功",
                    )
                    return RecoveryResult(
                        original_error=error,
                        final_result=result,
                        attempts=attempts,
                    )
                error = self._classifier.classify(tool_name, result)

        # 重试耗尽，尝试询问用户
        asked_user = False
        user_answer = None
        final_resolved = False
        if policy.ask_user_on_fail and ctx.ui and error.category not in (
            ToolErrorCategory.AUTH_MISSING,
            ToolErrorCategory.PERMISSION_DENIED,
        ):
            # 认证/权限类错误在更上层权限系统处理，这里不再问
            asked_user = True
            question = (
                f"工具 {tool_name} 执行失败: {error.reason}\n"
                f"{result.data}\n\n"
                f"建议: {error.suggestion}\n\n"
                "是否重试一次? [y] 重试 / [n] 放弃: "
            )
            user_answer = ctx.ui.ask_user(question)
            if user_answer and user_answer.strip().lower() in ("y", "yes", "好", "确认", "重试"):
                try:
                    result = await call_fn(effective_args, ctx)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    result = ToolResult.error(f"工具执行异常: {type(e).__name__}: {e}")
                else:
                    if not result.is_error:
                        final_resolved = True
                        return RecoveryResult(
                            original_error=error,
                            final_result=result,
                            attempts=attempts,
                            asked_user=True,
                            user_answer=user_answer,
                        )

        RecoveryTelemetry().record(
            tool_name=tool_name,
            category=error.category,
            recoverable=error.recoverable,
            attempts=len(attempts),
            resolved=final_resolved,
            message="询问后成功" if final_resolved else "未能自动恢复",
        )
        return RecoveryResult(
            original_error=error,
            final_result=result,
            attempts=attempts,
            asked_user=asked_user,
            user_answer=user_answer,
        )

    def _try_auto_fix(
        self,
        tool_name: str,
        error: ClassifiedError,
        args: dict[str, Any],
        ctx: ToolContext,
    ) -> tuple[bool, dict[str, Any] | None, str]:
        """针对可自动修复的错误尝试修正输入或环境。

        Returns:
            (是否修复成功, 新参数, 说明信息)
        """
        # 1. 超时：增加 timeout（仅 Bash 等带 timeout 参数的工具）
        if error.category == ToolErrorCategory.TIMEOUT:
            if "timeout" in args:
                old_timeout = int(args.get("timeout", 120))
                new_timeout = min(600, max(old_timeout + 60, old_timeout * 2))
                new_args = dict(args)
                new_args["timeout"] = new_timeout
                return True, new_args, f"超时时间从 {old_timeout}s 增加到 {new_timeout}s"

        # 2. 文件不存在：尝试创建父目录（仅写类工具）
        if error.category == ToolErrorCategory.NOT_FOUND:
            path_keys = ("file_path", "path", "filePath", "dst", "target")
            for key in path_keys:
                raw = args.get(key)
                if not raw:
                    continue
                p = Path(raw)
                if p.parent and not p.parent.exists():
                    try:
                        p.parent.mkdir(parents=True, exist_ok=True)
                        return True, args, f"已创建缺失目录: {p.parent}"
                    except Exception as e:
                        return False, None, f"创建目录失败: {e}"

        # 3. 依赖缺失：给出安装建议（不实际安装，避免误操作）
        if error.category == ToolErrorCategory.DEPENDENCY_MISSING:
            return False, None, error.suggestion

        return False, None, "暂无自动修复策略"
