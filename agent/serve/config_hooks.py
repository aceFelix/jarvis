"""把宿主（ProactiveHub）的「改配置后怎么生效」能力注册给配置层（阶段 12）。

为什么单独成文件（aceFelix，2026-10-26）：`hub.py` 已经 780+ 行，贴着单文件 800 行
的硬上限。这三个方法本质上是**配置层与宿主之间的接线**，既不属于"感知总线装配"，
也不属于"通知投递"，单独放一处比塞进 hub 更容易讲清边界。

为什么由宿主注册、而不是让 config 工具直接调 hub：终端 REPL 场景压根没有 hub，
而"没有宿主"本身要如实回执（"已保存，重启后生效"）——工具不该知道自己跑在哪种
宿主里，否则同一段逻辑要在 CLI / serve 各分叉一次。

@author aceFelix
"""

from __future__ import annotations

from typing import Any


def register_hub_apply_hooks(hub: Any) -> None:
    """给配置层登记两条生效回调（幂等：同一个 hub 只登记一次）。

    ``register_apply_hook`` 是按函数对象去重的，而这里用的是闭包（每次新建对象
    彼此不相等），所以幂等靠 hub 上的标记位保证。

    Args:
        hub: ``ProactiveHub`` 实例（鸭子类型：只需 ``hot_update_percept`` /
            ``hot_update_schedule`` 与 ``_bus``）。

    @author aceFelix
    """
    if getattr(hub, "_config_hooks_ready", False):
        return
    setattr(hub, "_config_hooks_ready", True)

    from agent.config.config_specs import (
        APPLY_PERCEPT,
        APPLY_RESCHEDULE,
        register_apply_hook,
        register_change_listener,
    )

    register_apply_hook(APPLY_PERCEPT, lambda: apply_percept(hub))
    register_apply_hook(APPLY_RESCHEDULE, lambda: apply_schedule(hub))
    register_change_listener(lambda changes: _emit_settings_changed(hub, changes))


def _emit_settings_changed(hub: Any, changes: dict[str, Any]) -> None:
    """把一次配置变更广播给桌面壳（``settings_changed`` 事件）。

    没有事件队列（终端 / 无壳场景）就当无事发生，不报错也不假装发了。

    @author aceFelix
    """
    from agent.serve import protocol

    q = getattr(hub, "_event_queue", None)
    if q is None:
        return
    try:
        q.put_nowait(
            {
                "type": protocol.EVT_SETTINGS_CHANGED,
                "payload": {
                    "keys": sorted(str(k) for k in changes),
                    # 区分来源：现在只有模型会走这条路（面板自己有乐观更新，
                    # 不需要回甜）；留着字段是为了将来 /config set 能报对人。
                    "source": "model",
                },
            }
        )
    except Exception:  # noqa: BLE001 - 队列满/已关闭不该让配置写入变成失败
        pass


def apply_percept(hub: Any) -> tuple[bool, str]:
    """``apply_mode=percept`` 的生效：重装配感知总线。

    这里有三条容易说错话的路，每条都要给不同的回执：

    1. **总开关本来就关着**（实测出厂态就是如此：`[percept]` 里只写了
       `window_enabled = true`，`enabled` 缺省就是 false）：根本没有总线可重装配。
       此时千万不能回“启用重装配”或“宿主未接线”——前者是谎，后者让人去查本来
       就没坏的接线；真正该说的是“改动已存，打开总开关时会按这份配置装配”。
    2. **这次改的就是总开关，且打开它**：总线是要**现装**出来的。若照旧在
       `_bus is None` 时就早退，用户把总开关打开却没有任何总线——这是真 bug。
    3. **关掉总开关**：重装配会直接不建总线，回执要说“总线已停”，
       而不是“启用 0 个源”（后者让人以为总线还在、只是没源在采）。

    @author aceFelix
    """
    had_bus = getattr(hub, "_bus", None) is not None
    master_on = bool(getattr(getattr(hub, "_settings", None), "percept_enabled", False))
    if not had_bus and not master_on:
        return False, "感知层总开关关着，当前没有总线可重装配（打开总开关时会按这份配置装配）"

    status = hub.hot_update_percept()
    if getattr(hub, "_bus", None) is None:
        if not master_on:
            return True, "感知层总开关已关闭，总线已停（逐源开关也随之失效）"
        # 总开关开着却没装出来 = 装配失败（_start_percept 内部已记 warning）
        return False, "配置已保存，但总线装配失败（看后端日志）；修好后重启或再改一次即可"
    on = [k for k, v in (status or {}).items() if v]
    if not had_bus:
        return True, f"感知层总开关已打开，总线已装配（启用 {len(on)} 个源）"
    return True, f"已重装配感知总线（启用 {len(on)} 个源）"


def apply_schedule(hub: Any) -> tuple[bool, str]:
    """``apply_mode=reschedule`` 的生效：按新配置重注册定时任务。"""
    hub.hot_update_schedule()
    return True, "已按新配置重注册定时任务"
