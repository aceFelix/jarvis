"""提炼的空闲触发（P0b）：聊一半走开，也要把增量落库。

## 为什么不复用宿主 Scheduler

计划原稿是"挂到宿主已有的 Scheduler 上"，实现时否掉了：`Scheduler.add_task` 会把任务
**持久化到 `~/.jarvis/schedule.json`**，并且 `list_pending()` 会把它列进 `/schedule`——
内部提炼任务会污染用户可见的提醒列表，还每轮写一次盘。

改用**每会话一个一次性 `threading.Timer`**：

- 它不是常驻线程：只在等待期间存在，触发或取消即释放；
- 不需要改 `Scheduler` 的公开语义（不影响提醒/自动清理）；
- 每个会话最多一个（重复调度会取消旧的），不会累积。

## 触发条件（与台账同一套判据）

到点时重新读台账，只有**三条同时成立**才真正提炼：

1. 当前会话仍是调度时的会话（用户切走了就跳过，旧会话在切换时已 force 收干）；
2. `now - last_msg_ts ≥ profile_refine_idle_minutes`；
3. `当前消息数 - refined_until ≥ profile_refine_min_delta`。

然后以 `force=True` 调 `maybe_refine_async`（无视 600 秒节流，但仍只喂增量）。

@author aceFelix
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from agent.core.diag import diag_warn
from agent.core.memory import profile_ledger

_COMPONENT = "memory.profile_idle"

#: 默认空闲阈值（分钟）；实际取 settings.profile_refine_idle_minutes
DEFAULT_IDLE_MINUTES = 60

#: 测试可替换：签名 (delay_seconds, callback) -> Timer 兼容对象
_timer_factory: Callable[[float, Callable[[], None]], Any] = threading.Timer

_lock = threading.Lock()
_timers: dict[str, Any] = {}

_settings: Any = None
_messages_provider: Callable[[], list] | None = None
_session_provider: Callable[[], str] | None = None


def bind(
    settings: Any,
    *,
    messages_provider: Callable[[], list],
    session_provider: Callable[[], str],
) -> None:
    """宿主启动时绑定一次：Settings + 取当前消息/会话的只读回调。

    两个 provider 必须是"读当前值"的闭包（`lambda: engine._messages`），
    不能传快照——空闲触发发生在几十秒后，会话可能已经变了。
    """
    global _settings, _messages_provider, _session_provider
    with _lock:
        _settings = settings
        _messages_provider = messages_provider
        _session_provider = session_provider


def bind_messages_provider(fn: Callable[[], list]) -> None:
    """只换消息来源（测试/宿主热切换用）。"""
    global _messages_provider
    with _lock:
        _messages_provider = fn


def unbind() -> None:
    """解绑并取消全部计时器（停机/测试收尾）。"""
    global _settings, _messages_provider, _session_provider
    with _lock:
        timers = list(_timers.values())
        _timers.clear()
        _settings = None
        _messages_provider = None
        _session_provider = None
    for t in timers:
        _cancel_timer(t)


def is_bound() -> bool:
    """是否已绑定宿主（未绑定时空闲触发静默禁用）。"""
    with _lock:
        return _settings is not None


def _cancel_timer(timer: Any) -> None:
    try:
        timer.cancel()
    except Exception:  # noqa: BLE001 - 取消失败不影响主流程
        pass


def schedule_idle_check(session_id: str, *, last_msg_ts: float | None = None) -> bool:
    """为一会话安排一次空闲检查（会替换同会话的旧计时器）。

    Args:
        session_id: 会话名（台账 key）。
        last_msg_ts: 最近一条消息的时间戳；缺省用当前时间。

    Returns:
        是否真的安排了（未绑定 / 空闲阈值关闭 / 异常 → False）。
    """
    s = _settings
    if s is None:
        return False
    try:
        idle_minutes = int(getattr(s, "profile_refine_idle_minutes", DEFAULT_IDLE_MINUTES) or 0)
    except (TypeError, ValueError):
        idle_minutes = DEFAULT_IDLE_MINUTES
    if idle_minutes <= 0:
        return False

    sid = str(session_id or "default")
    base = float(last_msg_ts if last_msg_ts is not None else time.time())
    delay = max(1.0, base + idle_minutes * 60.0 - time.time())

    cancel_idle_check(sid)
    try:
        timer = _timer_factory(delay, lambda: _fire(sid))
        timer.daemon = True
        timer.name = f"profile-idle:{sid}"
        with _lock:
            _timers[sid] = timer
        timer.start()
        return True
    except Exception as e:  # noqa: BLE001 - 定时器失败不能影响对话
        diag_warn(_COMPONENT, f"空闲检查安排失败: {type(e).__name__}: {e}")
        return False


def cancel_idle_check(session_id: str) -> bool:
    """取消某会话的空闲检查。返回是否取消到一个。"""
    sid = str(session_id or "default")
    with _lock:
        timer = _timers.pop(sid, None)
    if timer is None:
        return False
    _cancel_timer(timer)
    return True


def pending_sessions() -> list[str]:
    """当前挂着计时器的会话（调试/测试用）。"""
    with _lock:
        return sorted(_timers)


def _fire(session_id: str, *, now: float | None = None) -> None:
    """计时器到点：条件复核后以 force 触发提炼。绝不抛给线程。

    `now` 仅给测试注入（生产由计时器无参调用，取当前时间）。
    """
    sid = str(session_id or "default")
    ts = time.time() if now is None else float(now)
    with _lock:
        _timers.pop(sid, None)
        settings = _settings
        messages_provider = _messages_provider
        session_provider = _session_provider

    try:
        if settings is None or not getattr(settings, "profile_enabled", False):
            return
        idle_minutes = int(getattr(settings, "profile_refine_idle_minutes", DEFAULT_IDLE_MINUTES) or 0)
        if idle_minutes <= 0:
            return

        # 用户已切走：旧会话在切换时由 _flush_profile_refine 收干，这里不重复
        if session_provider is not None:
            current = str(session_provider() or "")
            if current and current != sid:
                return

        messages = messages_provider() if messages_provider is not None else None
        if not messages:
            return

        # 空闲触发已受"一次/小时"限流，门槛就是 delta≥1：
        # 若也用 min_delta，"聊两三句就走开"的尾巴永远落不了库（实测踩过）。
        due = profile_ledger.idle_sessions(
            now=ts,
            idle_minutes=idle_minutes,
            min_delta=1,
            counts={sid: len(messages)},
        )
        if sid not in due:
            return

        # 延迟导入：refiner 也依赖本模块的调度入口，模块级互相导入会成环
        from agent.core.memory.profile_refiner import maybe_refine_async

        maybe_refine_async(list(messages), sid, settings, force=True)
    except Exception as e:  # noqa: BLE001 - 后台线程不能因单次失败挂掉
        diag_warn(_COMPONENT, f"空闲触发失败: {type(e).__name__}: {e}")


def reset_for_tests() -> None:
    """测试收尾：解绑 + 清空计时器。"""
    unbind()
