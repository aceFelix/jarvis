"""感知层状态快照 —— 桌面「感知源」面板与 `percept.status.get` 的数据源。

为什么单独成模块（aceFelix，2026-10 W2 第 5 步）：面板要的信息横跨三层——
配置（Settings 的 `[percept]`）、总线（计数与逐源账本）、源实例（`state()` 自报
的"为什么没采到"）。拼装逻辑若写在 serve 的 RPC 里，CLI 想打印同一份自查报告就
得再抄一遍；写在总线里又会让 W2 反向依赖配置域。故放本模块，宿主只管传对象。

口径承诺（与决策 2「可见即可导出」一致）：
- 中文标签与"这个源采什么"的说明在这里定义，前端不再硬编码，避免两处漂移；
- 逐源一律列全（已实现的 + 未装配的收编位），未实现的显 `implemented=false`
  并给出"为什么没接"的理由，**不给能点开却采不到的假开关**；
- 事件明细原样返回 payload（用户要能看见到底采了什么），不做二次脱敏——
  脱敏在采集侧已经做过（标题剥凭据 + 截断 + 敏感应用零事件）。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.percept.bus import PerceptBus
from agent.core.percept.event import ALL_SOURCES
from agent.core.percept.factory import IMPLEMENTED_SOURCES, PENDING_SOURCES

#: 源名 → 面板标题（中文，单一真源）
SOURCE_LABELS: dict[str, str] = {
    "window": "前台窗口",
    "file": "目录堆积",
    "vcs": "仓库状况",
    "mail": "重要来信",
    "vision": "摄像头在场",
    "schedule": "定时提醒",
    "calendar": "日历事件",
    "system": "系统资源",
}

#: 源名 → 一句话说明（采什么 / 不采什么），面板折叠前的一行提示
SOURCE_HINTS: dict[str, str] = {
    "window": "只记「切到哪个应用」，标题剥掉凭据片段并截断；密码类应用零事件",
    "file": "只盯白名单目录的新增文件，删除与修改一概不管",
    "vcs": "只跑 git status / stash list 两条只读命令，不动工作区、不推不落",
    "mail": "IMAP 只读，只取信封头（发件人/主题/时间），不拉正文、不标已读",
    "vision": "本地 mediapipe 跑人脸与手势，唯一会碰摄像头的源；画面不出本机",
    "schedule": "用户自设提醒必须准点响，刻意不过打扰闸门，因此不并进总线",
    "calendar": "日历源本体仍是休眠件（serve 侧未装配），开了也采不到",
    "system": "系统监控本体仍是休眠件（同上），简报里已经带系统状态，无需重复",
}

#: 未装配的收编位给面板的固定理由（与 factory.PENDING_SOURCES 同源）
PENDING_REASON = "本期未装配：面板上刻意不给开关，避免「有开关、背后没人采」的假可用"

#: 一次返回的最近事件条数上限（面板列表够看即可，全量自查请读留痕文件）
EVENT_LIMIT = 50


def build_status(
    settings: Any,
    bus: PerceptBus | None = None,
    *,
    hub_enabled: bool | None = None,
    event_limit: int = EVENT_LIMIT,
) -> dict[str, Any]:
    """汇总一份感知状态（`percept.status.get` 的 result）。

    Args:
        settings: 全局 Settings（读 `[percept]` 配置快照）。
        bus: 宿主装配的总线实例；None = 当前没装配（总开关关或 serve 未跑），
            统计与事件段落返回空，但配置段照常可用（面板才不会白屏）。
        hub_enabled: 宿主是否已接线（serve 场景）。None 时按 bus 是否为 None 推断。
        event_limit: 最近事件返回条数（上限 :data:`EVENT_LIMIT`）。

    Returns:
        ``{enabled, assembled, bus_running, counters, dedup_window_s, log_file,
        sources: [...], recent_events: [...], pending: [...]}``，全部为可 JSON
        序列化的基础类型。

    @author aceFelix
    """
    master = bool(getattr(settings, "percept_enabled", False))
    stats = bus.stats() if bus is not None else {}
    source_stats: dict[str, Any] = stats.get("sources") or {}
    registered = set(bus.source_names()) if bus is not None else set()
    limit = max(1, min(int(event_limit or 1), EVENT_LIMIT))

    sources: list[dict[str, Any]] = []
    for name in sorted(ALL_SOURCES, key=_sort_key):
        implemented = name in IMPLEMENTED_SOURCES
        info: dict[str, Any] = {
            "name": name,
            "label": SOURCE_LABELS.get(name, name),
            "hint": SOURCE_HINTS.get(name, ""),
            "enabled": bool(master and getattr(settings, _enable_attr(name), False)),
            "implemented": implemented,
            "registered": name in registered,
            "bus_enabled": bool(bus.is_source_enabled(name)) if bus is not None else False,
            "stats": source_stats.get(name) or {},
            "detail": (bus.source_detail(name) if bus is not None else {}),
        }
        if name in PENDING_SOURCES or not implemented:
            info["pending_reason"] = PENDING_REASON
        sources.append(info)

    return {
        "enabled": master,
        # 宿主接线状态：区分“没接线”和“接了线但全关”，面板据此提示“需 --serve 运行”
        "assembled": bool(hub_enabled) if hub_enabled is not None else bus is not None,
        "bus_running": bool(stats.get("running")) if stats else False,
        "counters": stats.get("counters") or {},
        "dedup_window_s": int(stats.get("dedup_window_s") or 0) if stats else 0,
        "log_file": _log_file(settings, stats),
        "log_events": bool(getattr(settings, "percept_log_events", True)),
        "sources": sources,
        "recent_events": [ev.to_dict() for ev in (bus.recent(limit) if bus else [])],
        "pending": list(PENDING_SOURCES),
    }


def _sort_key(name: str) -> tuple[int, str]:
    """已实现的源排前面（面板主列表），收编位排后面（灰显说明）。"""
    return (0 if name in IMPLEMENTED_SOURCES else 1, name)


def _log_file(settings: Any, stats: dict[str, Any]) -> str:
    """留痕文件路径的三态口径（2026-10 人工走查修正）。

    早先只从总线统计里取，总线没跑就回空串，而前端空值的兜底文案是
    “未开启（关掉「本地留痕事件」后的状态）”——于是“开关是绿的、文案却说已关”，
    把“没跑”误报成“已关”。现在把三种情况分开说：

    - 留痕开关真关 → 空（这是唯一该显示“已关闭”的情形）；
    - 总线在跑 → 用它**实际写入**的路径（宿主可以改 `log_path`，不能拿默认值糊弄）；
    - 没跑但开关开着 → 回默认路径：面板要能回答“事件将来写到哪”。

    @author aceFelix
    """
    if not bool(getattr(settings, "percept_log_events", True)):
        return ""
    path = str(stats.get("log_path") or "") if stats else ""
    if path:
        return path
    # 包内入口函数，顶层导入会与 `__init__` 相互拉取，故在函数内局部导入
    from agent.core.percept import default_log_path

    fallback = default_log_path()
    return str(fallback) if fallback is not None else ""


def _enable_attr(name: str) -> str:
    """源名 → Settings 的逐源开关字段名（真源在 percept.SOURCE_ENABLE_KEYS）。"""
    from agent.core.percept import SOURCE_ENABLE_KEYS

    return SOURCE_ENABLE_KEYS.get(name, f"percept_{name}_enabled")
