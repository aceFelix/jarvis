"""Hooks 钩子系统 —— 事件订阅与分发。

对应原项目 utils/hooks/（17 文件）。原版支持 Agent/HTTP/Prompt 三类钩子、
文件变更监听、frontmatter/skill 钩子、SSRF 防护等复杂场景。

v0.1 实现核心:
1. AsyncHookRegistry —— 异步钩子注册中心
2. 事件类型: session_start / session_end / tool_before / tool_after /
   user_prompt / assistant_response / file_changed / error
3. 钩子可以是同步或异步函数，统一 await
4. 钩子失败不炸主流程（捕获异常并记录到诊断日志）
5. 钩子可返回 HookResult 影响主流程（如 tool_before 拒绝执行）

设计要点:
- 注册时按 event 类型分组，触发时按优先级顺序调用
- 同步钩子自动包装成异步
- 钩子执行有超时保护（默认 10 秒），避免卡死主循环
- 钩子异常隔离：一个钩子失败不影响其他钩子和主流程
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Union

# 钩子函数类型：可以是同步或异步
HookFunc = Union[Callable[[dict[str, Any]], None], Callable[[dict[str, Any]], Awaitable[None]]]


class HookEvent(str, Enum):
    """钩子事件类型。

    命名约定：<对象>_<时机>：
    - session_start / session_end: 会话开始/结束
    - user_prompt: 用户输入提交后、调 LLM 前
    - assistant_response: LLM 回复完成后
    - tool_before: 工具执行前（可拒绝执行）
    - tool_after: 工具执行后（含结果）
    - file_changed: 文件被工具修改后
    - error: 未捕获异常时
    """
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_PROMPT = "user_prompt"
    ASSISTANT_RESPONSE = "assistant_response"
    TOOL_BEFORE = "tool_before"
    TOOL_AFTER = "tool_after"
    FILE_CHANGED = "file_changed"
    ERROR = "error"


@dataclass
class HookResult:
    """钩子返回值，可影响主流程。

    Attributes:
        allow: 是否允许主流程继续（仅 tool_before 有效，False 表示拒绝执行该工具）
        reason: 拒绝原因（allow=False 时给用户/模型看）
        modify_input: 修改后的输入（仅 tool_before/user_prompt 有效，None 表示不修改）
    """
    allow: bool = True
    reason: str = ""
    modify_input: dict[str, Any] | str | None = None

    @classmethod
    def continue_(cls) -> "HookResult":
        return cls(allow=True)

    @classmethod
    def deny(cls, reason: str) -> "HookResult":
        return cls(allow=False, reason=reason)


@dataclass
class HookEntry:
    """已注册的钩子条目。"""
    name: str                       # 钩子名（用于 /hooks 命令展示和卸载）
    event: HookEvent
    func: HookFunc
    priority: int = 0               # 数值小先执行
    timeout: float = 10.0           # 超时秒数


class HookRegistry:
    """钩子注册中心。

    全局单例（通过 get_hooks() 获取）。生命周期与进程一致。

    用法::

        from agent.core.hooks import get_hooks, HookEvent

        @get_hooks().on(HookEvent.TOOL_BEFORE, name="log-bash")
        async def log_bash(payload):
            if payload["tool_name"] == "Bash":
                print(f"执行命令: {payload['tool_input'].get('command')}")

        # 主动触发
        result = await get_hooks().trigger(HookEvent.TOOL_BEFORE, {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        })
        if not result.allow:
            # 拒绝执行
            ...
    """

    def __init__(self) -> None:
        self._entries: dict[HookEvent, list[HookEntry]] = {
            e: [] for e in HookEvent
        }
        self._disabled: set[str] = set()   # 按名称禁用的钩子

    def on(
        self,
        event: HookEvent,
        *,
        name: str | None = None,
        priority: int = 0,
        timeout: float = 10.0,
    ) -> Callable[[HookFunc], HookFunc]:
        """装饰器：注册钩子。

        Args:
            event: 监听的事件类型
            name: 钩子名（不传则用函数名）。重名时后注册的覆盖前者
            priority: 数值小先执行（默认 0）
            timeout: 单个钩子执行超时（秒）

        Returns:
            原函数不变（装饰器只做注册）
        """
        def decorator(func: HookFunc) -> HookFunc:
            entry = HookEntry(
                name=name or func.__name__,
                event=event,
                func=func,
                priority=priority,
                timeout=timeout,
            )
            # 重名时移除旧的
            self._entries[event] = [
                e for e in self._entries[event] if e.name != entry.name
            ]
            self._entries[event].append(entry)
            # 按 priority 排序
            self._entries[event].sort(key=lambda e: e.priority)
            # 同时从禁用集合移除（重注册即启用）
            self._disabled.discard(entry.name)
            return func
        return decorator

    def register(
        self,
        event: HookEvent,
        func: HookFunc,
        *,
        name: str | None = None,
        priority: int = 0,
        timeout: float = 10.0,
    ) -> str:
        """直接注册钩子（非装饰器形式）。

        返回钩子名，可用于后续 unregister。
        """
        hook_name = name or func.__name__
        entry = HookEntry(
            name=hook_name, event=event, func=func,
            priority=priority, timeout=timeout,
        )
        self._entries[event] = [
            e for e in self._entries[event] if e.name != hook_name
        ]
        self._entries[event].append(entry)
        self._entries[event].sort(key=lambda e: e.priority)
        self._disabled.discard(hook_name)
        return hook_name

    def unregister(self, name: str) -> bool:
        """按名称卸载钩子。返回是否成功移除。"""
        removed = False
        for event in HookEvent:
            before = len(self._entries[event])
            self._entries[event] = [
                e for e in self._entries[event] if e.name != name
            ]
            if len(self._entries[event]) < before:
                removed = True
        self._disabled.discard(name)
        return removed

    def disable(self, name: str) -> None:
        """临时禁用钩子（不卸载）。"""
        self._disabled.add(name)

    def enable(self, name: str) -> None:
        """重新启用被禁用的钩子。"""
        self._disabled.discard(name)

    def list_hooks(self) -> list[HookEntry]:
        """列出所有已注册钩子（不含被禁用的）。"""
        out: list[HookEntry] = []
        for event in HookEvent:
            for e in self._entries[event]:
                if e.name not in self._disabled:
                    out.append(e)
        return out

    def list_all(self) -> list[tuple[HookEntry, bool]]:
        """列出所有钩子（含禁用状态）。返回 (entry, enabled) 元组列表。"""
        out: list[tuple[HookEntry, bool]] = []
        for event in HookEvent:
            for e in self._entries[event]:
                out.append((e, e.name not in self._disabled))
        return out

    async def trigger(self, event: HookEvent, payload: dict[str, Any]) -> HookResult:
        """触发事件，按 priority 顺序调用所有钩子。

        - 钩子返回 None: 视为 HookResult.continue_()
        - 钩子返回 HookResult: 影响主流程
        - 钩子返回 bool: True=allow, False=deny（无 reason）
        - 钩子返回 str: 视为 deny(reason)
        - 钩子抛异常: 记录到诊断日志，继续后续钩子
        - 钩子超时: 取消并记录，继续后续钩子

        tool_before 事件中，任一钩子 deny 则整体 deny。
        """
        merged = HookResult.continue_()
        for entry in list(self._entries[event]):
            if entry.name in self._disabled:
                continue
            try:
                ret = await self._call_one(entry, payload)
            except asyncio.TimeoutError:
                await self._log_error(
                    f"钩子 {entry.name} 超时 ({entry.timeout}s)",
                    event=event, payload=payload,
                )
                continue
            except Exception as e:
                await self._log_error(
                    f"钩子 {entry.name} 异常: {type(e).__name__}: {e}",
                    event=event, payload=payload,
                )
                continue

            # 合并返回值
            if ret is None:
                continue
            if isinstance(ret, bool):
                if not ret:
                    merged = HookResult.deny(f"钩子 {entry.name} 拒绝")
                    break  # deny 后不再调后续钩子
            elif isinstance(ret, str):
                merged = HookResult.deny(ret)
                break
            elif isinstance(ret, HookResult):
                if not ret.allow:
                    merged = ret
                    break
                # 合并 modify_input（后者覆盖前者）
                if ret.modify_input is not None:
                    merged.modify_input = ret.modify_input
        return merged

    async def _call_one(self, entry: HookEntry, payload: dict[str, Any]) -> Any:
        """调用单个钩子，处理同步/异步 + 超时。"""
        func = entry.func
        if inspect.iscoroutinefunction(func):
            # 异步钩子：带超时
            return await asyncio.wait_for(func(payload), timeout=entry.timeout)
        else:
            # 同步钩子：直接调用（避免阻塞 event loop 太久，超时只能事后判断）
            start = time.monotonic()
            ret = func(payload)
            elapsed = time.monotonic() - start
            if elapsed > entry.timeout:
                raise asyncio.TimeoutError()
            return ret

    async def _log_error(self, msg: str, *, event: HookEvent, payload: dict[str, Any]) -> None:
        """钩子异常记录到诊断日志（避免 import 循环，惰性导入）。"""
        try:
            from agent.core.diag import diag_log
            diag_log(
                "hooks",
                f"{msg} | event={event.value} payload_keys={list(payload.keys())}",
                level="warn",
            )
        except Exception:
            pass  # 诊断日志本身失败不能再抛


# ---- 全局单例 ----

_global_registry: HookRegistry | None = None


def get_hooks() -> HookRegistry:
    """获取全局钩子注册中心单例。"""
    global _global_registry
    if _global_registry is None:
        _global_registry = HookRegistry()
    return _global_registry


def reset_hooks() -> None:
    """重置全局钩子注册中心（测试用）。"""
    global _global_registry
    _global_registry = None
