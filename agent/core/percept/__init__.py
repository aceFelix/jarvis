"""W2 感知层（阶段 9）—— 统一事件总线 + 逐源采集器。

一句话：给判断层（W1 `agent/core/policy/`）供货。W1 管"该不该现在打扰"，
本包管"到底发生了什么"。二者之间的唯一契约是 `PerceptEvent`。

装配约定（与 policy 包同构，便于宿主一次性接线）：
- 宿主启动时调 `init_percept(settings)` 注入配置快照；未注入时按**安全缺省**
  ——总开关关、逐源关，总线不起线程、不采一个字节。
- `get_bus()` 取进程内单例；配置变更后需重新 `init_percept` 才会重建总线
  （节流/去重窗口这类参数改动本就应重启源，热更新只做逐源开关）。
- 播报侧不直接订阅总线：由 serve 的 `ProactiveHub` 适配一层，把事件拼成
  文本后走 W1 预算闸门，保证"所有打扰都过同一个闸"这条不变量不被破坏。

一期范围已定（见 docs/roadmap/PLAN-butler-autonomy-upgrade.md 第 8 节）：
只做**电脑管家**——事件源全部来自本机与网络协议，不接 IoT/Home Assistant。

配置字段全部经 `_cfg` 读取（getattr + 默认值），因此 Settings 尚未登记这些
字段时本包照常工作、取安全缺省；逐源开关的字段名口径见 `SOURCE_ENABLE_KEYS`。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.core.percept.bus import (
    BusConfig,
    PerceptBus,
    PollSource,
    PublishResult,
)
from agent.core.percept.event import (
    ALL_SOURCES,
    LEGACY_SOURCES,
    SOURCE_CALENDAR,
    SOURCE_FILE,
    SOURCE_MAIL,
    SOURCE_SCHEDULE,
    SOURCE_SYSTEM,
    SOURCE_VCS,
    SOURCE_VISION,
    SOURCE_WINDOW,
    PerceptEvent,
)

__all__ = [
    "ALL_SOURCES", "BusConfig", "LEGACY_SOURCES", "PerceptBus", "PerceptEvent",
    "PollSource", "PublishResult", "SOURCE_CALENDAR", "SOURCE_FILE",
    "SOURCE_MAIL", "SOURCE_SCHEDULE", "SOURCE_SYSTEM", "SOURCE_VCS",
    "SOURCE_VISION", "SOURCE_WINDOW",
    "build_sources", "get_bus", "init_percept", "register_sources",
    "shutdown_percept", "source_enabled_map",
]

#: 源名 → Settings 里的逐源开关字段名（桌面面板与 TOML 都按此对齐）。
#: 逐源默认关（决策 2），字段缺省时同样按 False 处理。
SOURCE_ENABLE_KEYS: dict[str, str] = {
    SOURCE_WINDOW: "percept_window_enabled",
    SOURCE_FILE: "percept_file_enabled",
    SOURCE_VCS: "percept_vcs_enabled",
    SOURCE_MAIL: "percept_mail_enabled",
    SOURCE_CALENDAR: "percept_calendar_enabled",
    SOURCE_SYSTEM: "percept_system_enabled",
    SOURCE_SCHEDULE: "percept_schedule_enabled",
    SOURCE_VISION: "percept_vision_enabled",
}

# 模块级配置快照（init_percept 注入；None = 安全缺省=全关）
_settings: Any = None
_bus_singleton: PerceptBus | None = None


def init_percept(settings: Any = None, *, bus: PerceptBus | None = None) -> PerceptBus:
    """宿主启动/热更新时注入 Settings 快照并（按需）重建总线单例。

    Args:
        settings: Settings 实例，读取 ``percept_*`` 字段；None 恢复安全缺省。
        bus: 直接指定总线实例（测试或宿主自装配场景），传入即成为单例。

    Returns:
        生效的总线实例，便于宿主链式调用 ``subscribe``/``start``。

    重建路径会把旧总线的**观测账本**（闸门计数 + 内存历史）接到新实例上：
    不然“改一次配置 = 抹一次证据”，面板在用户最需要对比效果的那一刻空掉
    （2026-10-19 人工走查发现，详见 docs/fixlogs/）。显式传入 `bus` 时**不**
    继承：那是测试与宿主自装配入口，拿陈旧的计数去污染一个新实例更难解释。

    @author aceFelix
    """
    global _settings, _bus_singleton
    _settings = settings
    if bus is not None:
        _bus_singleton = bus
        return bus
    previous = _bus_singleton
    _bus_singleton = None  # 下次 get_bus 按新配置重建
    fresh = get_bus()
    fresh.inherit_stats_from(previous)
    return fresh


def _cfg(field: str, default: Any) -> Any:
    """从注入的 settings 读字段（未注入/缺字段回默认）。

    与 ``agent/core/policy/__init__.py`` 的 `_cfg` 同一口径：感知层的全部
    参数都"不配也能跑"，且默认值一律偏保守（关、少、短）。
    """
    if _settings is None:
        return default
    value = getattr(_settings, field, default)
    return default if value is None else value


def build_config() -> BusConfig:
    """按当前配置快照构造 BusConfig（宿主想自建总线时也走这里）。"""
    return BusConfig(
        dedup_window_s=int(_cfg("percept_dedup_window_s", 300)),
        min_salience=float(_cfg("percept_min_salience", 0.0)),
        history_max=int(_cfg("percept_history_max", 200)),
        auto_disable_after_errors=int(_cfg("percept_auto_disable_after_errors", 3)),
        throttle_s={
            SOURCE_VCS: int(_cfg("percept_vcs_min_interval_s", 60)),
            SOURCE_MAIL: int(_cfg("percept_mail_min_interval_s", 0)),
            SOURCE_FILE: int(_cfg("percept_file_min_interval_s", 0)),
        },
    )


def default_log_path() -> Path | None:
    """事件留痕文件路径；``percept_log_events=False`` 时返回 None=完全不留痕。

    目录单一真源用 `core/memory/store.py` 的 `user_jarvis_dir()`（全仓 ~./.jarvis
    路径都经它），而不是在这里再写一遍字面量。
    """
    if not _cfg("percept_log_events", True):
        return None
    try:
        from agent.core.memory.store import user_jarvis_dir

        root = user_jarvis_dir()
    except Exception:  # pragma: no cover - 只防循环导入/记忆模块意外，不阻断感知
        root = Path.home() / ".jarvis"
    return root / "percept" / "events.jsonl"


def get_bus() -> PerceptBus:
    """总线单例（总开关 ``percept_enabled=False`` 时照样返回实例，只是不 start、
    不注册源——订阅/统计接口要始终可用，桌面面板才能显示"当前未启用"）。"""
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = PerceptBus(build_config(), log_path=default_log_path())
    return _bus_singleton


def source_enabled_map() -> dict[str, bool]:
    """逐源开关快照（面板渲染与"总开关关闭则全关"的推导都用它）。

    总开关 ``percept_enabled=False`` 时返回全 False：用户关掉总开关后，
    面板上任何单个源都不该显示为"正在观察"。
    """
    if not _cfg("percept_enabled", False):
        return {name: False for name in SOURCE_ENABLE_KEYS}
    return {name: bool(_cfg(key, False)) for name, key in SOURCE_ENABLE_KEYS.items()}


def shutdown_percept() -> None:
    """停止并释放总线（宿主退出路径）。单例保留但已停，重启进程自然重建。"""
    bus = _bus_singleton
    if bus is None:
        return
    bus.stop()
    bus.shutdown_sources()


def build_sources(settings: Any) -> list[tuple[Any, bool]]:
    """按 Settings 构造已实现的感知源（转发 factory，避免宿主多处 import）。"""
    from agent.core.percept.factory import build_sources as _build

    return _build(settings)


def register_sources(bus: PerceptBus, settings: Any) -> dict[str, bool]:
    """登记全部源并返回逐源启用状态（宿主唯一接线入口，见 factory 说明）。"""
    from agent.core.percept.factory import register_sources as _register

    return _register(bus, settings)
