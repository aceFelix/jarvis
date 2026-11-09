"""代理团队活动台账 —— 子代理 / 队友的实时状态与当前动作。

设计定位（aceFelix，2026-11）：
- **只读观测层**：本模块记录「谁在跑、跑到哪、在调什么工具」，不参与任何
  任务调度、权限或结果判定；停掉/杀掉代理一律走原有控制面（runner / task）。
- **进程内单例**：桌面壳一次只跑一个引擎进程，重启即清空；团队与任务台账
  的持久真源仍是 ``~/.jarvis/teams`` 与 ``~/.jarvis/tasks``，这里不落盘。
- **变更通知节流**：工具调用密集时每秒可能几十次更新，监听器最多每
  300ms 收一次快照；状态跃迁（开始/结束/失败）立即通知，避免面板错过收尾。
- **快照拷贝**：``snapshot()`` 返回普通 dict 列表，调用方（RPC / 事件推送）
  拿到后随便序列化，不会与写入线程共享对象。

@author aceFelix
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

#: 视为「已收尾」的状态：面板不再为这些行画停止按钮。
TERMINAL_STATUSES = ("done", "failed", "stopped")

#: 默认节流窗口（秒）。
_NOTIFY_INTERVAL_S = 0.3

#: 输出摘要上限（字符）。面板只显示最近一段，不保留完整子代理对话。
_OUTPUT_LIMIT = 200

#: 任务摘要上限（字符）。
_TASK_LIMIT = 120


@dataclass
class AgentActivity:
    """一个子代理 / 队友的活动记录。

    Attributes:
        agent_id: 唯一标识（同步子代理 ``sub-<type>-<hex>``，队友 ``name@team``）。
        name: 展示名。
        role: explorer / researcher / coder / general。
        mode: ``subagent``（一次性同步）/ ``teammate``（持久队友）。
        team: 所属团队名（空 = 未入队）。
        status: starting / running / thinking / idle / done / failed / stopped。
        task: 任务摘要（prompt 前 N 字）。
        current_tool: 当前工具名 + 关键参数摘要（空 = 不在调工具）。
        activity: 一行当前动作人话（面板第二行）。
        output: 最近输出摘要。
        started_at / updated_at: 墙钟秒。
        iterations: 已完成轮次。
        tool_calls: 工具调用次数。
        error: 失败原因。
    """

    agent_id: str
    name: str
    role: str = "general"
    mode: str = "subagent"
    team: str = ""
    status: str = "starting"
    task: str = ""
    current_tool: str = ""
    activity: str = ""
    output: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    iterations: int = 0
    tool_calls: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """转成面板/RPC 用的普通字典（字段保持 snake_case）。"""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role,
            "mode": self.mode,
            "team": self.team,
            "status": self.status,
            "task": self.task,
            "current_tool": self.current_tool,
            "activity": self.activity,
            "output": self.output,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "error": self.error,
        }


class AgentActivityRegistry:
    """线程安全的活动台账。

    用法::

        reg = get_activity_registry()
        reg.start_agent("sub-explorer-1", "explorer-1", role="explorer")
        reg.update_agent("sub-explorer-1", status="running", current_tool="Grep src/")
        reg.finish_agent("sub-explorer-1", status="done")
        reg.snapshot()

    @author aceFelix
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._agents: dict[str, AgentActivity] = {}
        #: agent_id → 运行它的 asyncio.Task（同步子代理可被 agents.exec stop 取消）
        self._tasks: dict[str, Any] = {}
        self._listener: Optional[Callable[[list[dict[str, Any]]], None]] = None
        #: 终端驾驶舱等只读观察者；与桌面 listener 并存，不改变 has_listener 语义
        self._observers: list[Callable[[list[dict[str, Any]]], None]] = []
        self._last_notify = 0.0
        self._timer: Optional[threading.Timer] = None

    # ---- 写入 ----

    def start_agent(
        self,
        agent_id: str,
        name: str,
        *,
        role: str = "general",
        mode: str = "subagent",
        team: str = "",
        task: str = "",
    ) -> AgentActivity:
        """登记一个开始执行的代理（立即通知监听器）。"""
        now = time.time()
        rec = AgentActivity(
            agent_id=agent_id,
            name=name,
            role=role,
            mode=mode,
            team=team,
            status="starting",
            task=_clip(task, _TASK_LIMIT),
            started_at=now,
            updated_at=now,
        )
        with self._lock:
            self._agents[agent_id] = rec
        self._notify(force=True)
        return rec

    def update_agent(self, agent_id: str, **fields: Any) -> None:
        """更新若干字段；未知字段忽略，避免埋点写错就崩掉执行链路。"""
        with self._lock:
            rec = self._agents.get(agent_id)
            if rec is None:
                return
            for key, value in fields.items():
                if not hasattr(rec, key):
                    continue
                if key in ("task", "output") and isinstance(value, str):
                    value = _clip(value, _TASK_LIMIT if key == "task" else _OUTPUT_LIMIT)
                if key == "activity" and isinstance(value, str):
                    value = _clip(value, _TASK_LIMIT)
                setattr(rec, key, value)
            rec.updated_at = time.time()
            force = str(fields.get("status") or "") in TERMINAL_STATUSES
        self._notify(force=force)

    def finish_agent(
        self,
        agent_id: str,
        *,
        status: str = "done",
        error: str = "",
        output: str = "",
    ) -> None:
        """标记收尾（立即通知）；记录保留，直到会话重置或下次清理。"""
        with self._lock:
            rec = self._agents.get(agent_id)
            if rec is None:
                return
            rec.status = status
            rec.current_tool = ""
            rec.activity = ""
            rec.error = _clip(error, _TASK_LIMIT)
            if output:
                rec.output = _clip(output, _OUTPUT_LIMIT)
            rec.updated_at = time.time()
        self._notify(force=True)

    def snapshot(self) -> list[dict[str, Any]]:
        """当前全部代理的普通字典列表（按开始时间排序）。"""
        with self._lock:
            items = [rec.to_dict() for rec in self._agents.values()]
        return sorted(items, key=lambda x: x.get("started_at") or 0)

    # ---- 监听与清理 ----

    def bind_task(self, agent_id: str, task: Any) -> None:
        """把一个同步子代理绑定到运行它的 asyncio.Task，供控制面取消。"""
        with self._lock:
            self._tasks[agent_id] = task

    def task_for(self, agent_id: str) -> Any:
        """取运行该代理的 asyncio.Task（没有则 None）。"""
        with self._lock:
            return self._tasks.get(agent_id)

    def forget_task(self, agent_id: str) -> None:
        """代理跑完后摘掉任务引用，避免长期持有已完成的 Task。"""
        with self._lock:
            self._tasks.pop(agent_id, None)

    def set_listener(self, listener: Optional[Callable[[list[dict[str, Any]]], None]]) -> None:
        """注册变更监听器（宿主用它把快照推成 ``agents_state`` 事件）。"""
        with self._lock:
            self._listener = listener

    def has_listener(self) -> bool:
        """是否有宿主在消费台账（桌面右栏）；无消费者时保留子代理原始 UI 输出。

        终端 REPL 不注册监听器，子代理的进度仍应打在终端上；只有桌面 serve
        需要把子代理事件收进右栏、避免污染主聊天。
        @author aceFelix
        """
        with self._lock:
            return self._listener is not None

    def add_observer(self, observer: Callable[[list[dict[str, Any]]], None]) -> None:
        """注册只读观察者（终端子代理卡用）。

        与 ``set_listener`` 不同：observer 不改变 ``has_listener``，因此不会
        把子代理的原始输出从终端收走；observer 异常一律吞掉。
        @author aceFelix
        """
        with self._lock:
            if observer not in self._observers:
                self._observers.append(observer)

    def remove_observer(self, observer: Callable[[list[dict[str, Any]]], None]) -> None:
        """注销观察者（窗口关闭 / 进程退出时调用）。"""
        with self._lock:
            if observer in self._observers:
                self._observers.remove(observer)

    def has_card_observer(self) -> bool:
        """是否有终端卡片在观察台账（决定是否启用 TeeActivityUI）。"""
        with self._lock:
            return bool(self._observers)

    def clear(self, *, keep_listener: bool = True) -> None:
        """清空台账（会话重置 / 引擎停机时用）。"""
        with self._lock:
            self._agents.clear()
            self._tasks.clear()
            if not keep_listener:
                self._listener = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._notify(force=True)

    def _notify(self, *, force: bool = False) -> None:
        """节流通知：立即路径直发，否则最多每 300ms 一次。

        监听器与 observer 都在锁外调用，避免宿主回调里再读台账造成重入死锁；
        任一回调异常只吞掉，不影响其它消费者与执行链。
        @author aceFelix
        """
        listener: Optional[Callable[[list[dict[str, Any]]], None]]
        observers: list[Callable[[list[dict[str, Any]]], None]]
        snapshot: list[dict[str, Any]]
        with self._lock:
            listener = self._listener
            observers = list(self._observers)
            if listener is None and not observers:
                return
            now = time.time()
            if not force and (now - self._last_notify) < _NOTIFY_INTERVAL_S:
                # 拖尾一次，保证最后一次变更不会因为落在节流窗口里被丢掉
                if self._timer is None:
                    delay = max(0.01, _NOTIFY_INTERVAL_S - (now - self._last_notify))
                    self._timer = threading.Timer(delay, self._notify, kwargs={"force": True})
                    self._timer.daemon = True
                    self._timer.start()
                return
            self._last_notify = now
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            snapshot = [rec.to_dict() for rec in self._agents.values()]
        ordered = sorted(snapshot, key=lambda x: x.get("started_at") or 0)
        callbacks = ([listener] if listener is not None else []) + observers
        for callback in callbacks:
            try:
                callback(ordered)
            except Exception:
                # 观测层故障绝不能反噬执行链
                pass

    # ---- 展示辅助 ----

    @staticmethod
    def new_agent_id(role: str) -> str:
        """生成同步子代理 id（``sub-<role>-<8位hex>``）。"""
        return f"sub-{role}-{uuid.uuid4().hex[:8]}"


def _clip(text: str, limit: int) -> str:
    """裁剪到上限（超长补省略号），并压掉换行避免面板撑高。"""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# UI 代理：把子代理事件转成活动台账，不污染主聊天流
# ---------------------------------------------------------------------------


class AgentActivityUI:
    """子代理 / 队友的 UI 代理。

    背景：``run_subagent`` 与 ``TeammateRunner`` 原本直接复用主 UI
    （``ctx.ui``），于是子代理的流式文本与工具卡片会全部涌进主聊天——
    在桌面壳里表现为「我派了个子代理，结果我的对话里出现一堆别人的工具卡」。
    本代理把 ``tool_use`` / ``tool_result`` / ``assistant_text`` /
    ``assistant_thinking`` 收进活动台账，其余方法（``info`` / ``warn`` /
    ``error`` / ``ask_user`` / ``end_thinking`` …）原样转发给宿主 UI。

    ``ask_user`` 必须转发：子代理需要用户补充信息时不能静默卡住。

    @author aceFelix
    """

    def __init__(self, base_ui: Any, agent_id: str) -> None:
        self._base = base_ui
        self._agent_id = agent_id

    # ---- 收进台账的事件 ----

    def tool_use(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None:
        from .activity import get_activity_registry  # 同包，惰性导入避免循环

        hint = _tool_hint(tool_name, tool_input)
        get_activity_registry().update_agent(
            self._agent_id,
            status="running",
            current_tool=hint,
            activity=f"调用 {tool_name}：{hint}" if hint else f"调用 {tool_name}",
        )

    def tool_result(
        self,
        tool_name: str,
        result: str,
        is_error: bool = False,
        tool_use_id: str = "",
    ) -> None:
        from .activity import get_activity_registry

        get_activity_registry().update_agent(
            self._agent_id,
            current_tool="",
            activity=f"{tool_name} 返回{'错误' if is_error else '结果'}",
            output=_clip(result or "", _OUTPUT_LIMIT),
        )

    def assistant_text(self, text: str) -> None:
        from .activity import get_activity_registry

        get_activity_registry().update_agent(
            self._agent_id,
            status="running",
            activity="整理结论",
            output=_clip(text or "", _OUTPUT_LIMIT),
        )

    def assistant_thinking(self, text: str) -> None:
        from .activity import get_activity_registry

        get_activity_registry().update_agent(self._agent_id, status="thinking", activity="思考中")

    # ---- 其余接口转发给宿主 ----

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


class TeeActivityUI:
    """转发 + 记账的 UI 代理（终端子代理卡专用）。

    与 ``AgentActivityUI`` 的区别：它**不吞掉**子代理的流式文本与工具卡，
    先原样转发给宿主 UI（终端照常打印），再把同一事件写进活动台账，供
    终端浮窗卡渲染。这样“保留终端原始输出”与“每个代理一张卡”可以共存。

    **整段缓冲（2026-11-08 实机修复）**：多个子代理/队友并行时，逐片段
    转发会让 A 的 Markdown 表格与 B 的表格逐列交错，终端看起来完全错位。
    因此正文按一条 assistant 消息整段缓冲，在工具调用/本轮结束时一次性
    交给宿主；思考也按块缓冲，避免多个代理共用同一个思考面板。

    只在没有桌面 listener、但存在终端卡片 observer 时使用；
    两者都没有时仍走旧行为（直接用 parent_ui）。

    @author aceFelix
    """

    def __init__(self, base_ui: Any, agent_id: str, agent_name: str = "") -> None:
        self._base = base_ui
        self._agent_id = agent_id
        self._agent_name = agent_name or agent_id
        self._text_buf = ""
        self._think_buf = ""

    def assistant_text(self, text: str) -> None:
        """累积整段正文并实时更新台账；不在片段级别写终端。"""
        chunk = text or ""
        if not self._text_buf:
            # 新一段正文开始：先把前面积累的思考整块输出，保证「思考→正文」顺序
            self._flush_thinking()
        self._text_buf += chunk
        from .activity import get_activity_registry

        get_activity_registry().update_agent(
            self._agent_id,
            status="running",
            activity="整理结论",
            output=_clip(self._text_buf, _OUTPUT_LIMIT),
        )

    def assistant_thinking(self, text: str) -> None:
        """累积思考块并标记 thinking；不喂共享思考面板（防多代理混流）。"""
        self._think_buf += text or ""
        from .activity import get_activity_registry

        get_activity_registry().update_agent(self._agent_id, status="thinking", activity="思考中")

    def flush(self) -> None:
        """把尚未输出的思考与正文一次性交给宿主 UI（轮次结束/工具边界调用）。"""
        self._flush_thinking()
        self._flush_text()

    def _flush_text(self) -> None:
        """一次性输出整段正文；空缓冲直接跳过。"""
        text = self._text_buf
        self._text_buf = ""
        if not text:
            return
        try:
            # 整段末尾补换行：两个代理先后 flush 时不会把第二段直接粘在第一段句尾
            self._base.assistant_text(text.rstrip("\n") + "\n")
        except Exception:
            pass

    def _flush_thinking(self) -> None:
        """一次性输出整块思考（带代理名前缀），避免多个面板互相覆盖。"""
        body = self._think_buf.strip()
        self._think_buf = ""
        if not body:
            return
        if len(body) > 3000:
            body = body[:1500] + "\n… [思考截断] …\n" + body[-1000:]
        try:
            from agent.ui.trace_view import safe_info

            safe_info(self._base, f"💭 {self._agent_name} 思考：\n{body}")
        except Exception:
            pass

    def tool_use(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> None:
        """先冲刷待输出文本，再转发工具卡片，最后记录“当前在调什么”。"""
        self.flush()
        self._base.tool_use(tool_name, tool_input, tool_use_id)
        from .activity import get_activity_registry

        hint = _tool_hint(tool_name, tool_input)
        get_activity_registry().update_agent(
            self._agent_id,
            status="running",
            current_tool=hint,
            activity=f"调用 {tool_name}：{hint}" if hint else f"调用 {tool_name}",
        )

    def tool_result(
        self,
        tool_name: str,
        result: str,
        is_error: bool = False,
        tool_use_id: str = "",
    ) -> None:
        """先冲刷待输出文本，再转发结果卡片，最后记录返回摘要。"""
        self.flush()
        self._base.tool_result(tool_name, result, is_error, tool_use_id)
        from .activity import get_activity_registry

        get_activity_registry().update_agent(
            self._agent_id,
            current_tool="",
            activity=f"{tool_name} 返回{'错误' if is_error else '结果'}",
            output=_clip(result or "", _OUTPUT_LIMIT),
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def flush_ui(ui: Any) -> None:
    """收尾时把 UI 代理的缓冲冲刷出去（普通 UI 没有 flush 时静默跳过）。"""
    flush = getattr(ui, "flush", None)
    if callable(flush):
        try:
            flush()
        except Exception:
            pass


def _tool_hint(tool_name: str, args: dict[str, Any] | None) -> str:
    """从工具参数里挑一个最能说明「在干什么」的字段。"""
    if not isinstance(args, dict):
        return ""
    for key in ("path", "file_path", "file", "pattern", "command", "cmd", "query", "url", "prompt"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value, 60)
    return ""


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_registry: Optional[AgentActivityRegistry] = None
_registry_lock = threading.Lock()


def get_activity_registry() -> AgentActivityRegistry:
    """获取全局活动台账单例（进程内）。"""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = AgentActivityRegistry()
    return _registry
