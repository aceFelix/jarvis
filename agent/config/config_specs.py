"""自配置层（阶段 12 P1）：配置项的人话元数据、三类分级、校验与外科式落盘。

为什么要有这张表（aceFelix，2026-10-26）：`Settings` 有 190 个字段，而面板能改的
只有 22 个、终端 `/config` 只读、模型侧一个配置工具都没有——装完 jarvis 的第一道坎
仍然是"打开一个 TOML 文件"。要让模型帮着改配置，先得有一张**带人话、带权限分级**
的真源表，否则它既解释不清自己在改什么，也可能顺手把沙箱或打扰预算拆了。

三层口径（每一层都对应一次踩过的坑）：

- **``tier``：谁能改。**
  ``auto`` = 纯表达层（音量、语速、简报时间、感知逐源开关），改坏也只是"小声点/
  少说点"；``confirm`` = 涉及"何时能打断用户"或"干什么活的范围"，模型单方面改
  等于给自己放宽护栏，必须先问一句；``forbidden`` = 权限与凭据本身，一律拒绝，
  只告诉用户正确改法。
- **``apply_mode``：改完怎么生效。** 由表声明，**不让模型自己判断**——
  ``tts`` 每帧现读即生效，``percept`` 逐源开关是装配快照得重装配总线，
  ``reschedule`` 的调度任务是启动快照得重注册。判断错了的后果是"用户以为生效了
  其实没有"，比不做更糟。这一层差点出错的一处：``[policy.notify_budget]`` 的
  四项看着像现读，其实 ``get_budget()`` 是个**懒建单例**（``_budget_singleton``
  = None 才重建）——不显式重建就只落盘不生效，故单独一种 ``policy`` 模式。
- **``section/field/attr`` 三者分开写。** 多数配置项三处同名，但
  ``[deadline] enabled`` → ``deadline_enabled``、``[policy.notify_budget] daily_max``
  → ``notify_daily_max`` 这类并不相同；写死在一处，才不会出现"改错了地方"。

与既有两份白名单的关系：``percept_settings``（面板）与 ``desktop_settings``（桌面协议）
**保持各自对外 API 不变**，本表是第三处独立真源，P2 再让它们从本表派生。
P1 刻意不重写它们——那是一次大改，收益不抵风险。

@author aceFelix
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from agent.config.toml_writer import bool_literal, list_literal, save_section, str_literal

# ---- 三类分级 ----
TIER_AUTO = "auto"            # 模型可直接改（纯表达层）
TIER_CONFIRM = "confirm"      # 模型必须先问一句（涉及打断时机 / 工作范围）
TIER_FORBIDDEN = "forbidden"  # 一律拒绝（权限与凭据本身）

# ---- 哪些面板给我留了位置（视图层元数据，与 tier 正交）----
#
# tier 管的是"模型能不能改"，panels 管的是"哪个界面能改"——两件事必须分开：
# `log_events` 面板能改（人是主人），模型不能改（`forbidden`）；反过来
# `quiet_hours` 模型能改（confirm），面板却不给位置（打扰预算只在协议里调）。
PANEL_DESKTOP = "desktop-settings"   # 桌面「[SET] 设置面板」（settings.get/set）
PANEL_PERCEPT = "percept-panel"      # 「管家服务 → 感知源」（percept.config.set）

# ---- 改完怎么生效 ----
APPLY_LIVE = "live"            # 现读即生效（tts 每次播报现读 settings）
APPLY_POLICY = "policy"        # 打扰预算单例是懒建的进程内快照 → 须重建单例
APPLY_PERCEPT = "percept"      # 逐源开关是装配快照 → 需重装配感知总线
APPLY_RESCHEDULE = "reschedule"  # 调度任务是启动快照 → 需重注册
APPLY_RESTART = "restart"      # 只落盘，下次启动生效（不假装已生效）

#: HH:MM 24 小时制（与 desktop_settings.TIME_RE 同口径）
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
#: quiet_hours 形如 ``23:00-07:00``（端点是 HH:MM，允许跨零点）
QUIET_HOURS_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class ConfigSpec:
    """单个可改配置项的元数据（协议键 = 模型/终端统一使用的名字）。

    Attributes:
        key: 协议键（``ConfigSet`` 的入参名）。
        section: ``settings.toml`` 节名（``policy.notify_budget`` 这类嵌套节直接写全）。
        field: 该节内的字段名。
        attr: ``Settings`` 实例属性名。
        kind: bool / int / float / time / quiet / list。
        label: 中文标题（面板、播报、回执共用，避免三处文案各写一份）。
        hint: 一句人话——"改它会发生什么"，模型据此向用户解释。
        tier: auto / confirm / forbidden。
        apply_mode: live / policy / percept / reschedule / restart。
        panels: 哪些面板能改它（``PANEL_DESKTOP`` / ``PANEL_PERCEPT``）；
            空集合 = 只能通过模型工具或手改 TOML。面板与协议方案都从本表
            派生，所以这一列是"两边可改项"的唯一出处。
        lo / hi: 数值范围（含边界）。
        default: 出厂缺省（读侧属性缺失时回退；None 表示按类型零值）。
    """

    key: str
    section: str
    field: str
    attr: str
    kind: str
    label: str
    hint: str
    tier: str = TIER_AUTO
    apply_mode: str = APPLY_LIVE
    panels: frozenset[str] = frozenset()
    lo: float | None = None
    hi: float | None = None
    default: Any = None


#: 两个面板的可改集（表里每项直接引用，不要另写一份键名列表——那就是第四份漂移）
_DESKTOP = frozenset({PANEL_DESKTOP})
_PERCEPT = frozenset({PANEL_PERCEPT})

#: 可改配置项（顺序即 ``ConfigList`` 的展示顺序：先表达层，再确认层）
CONFIG_SPECS: tuple[ConfigSpec, ...] = (
    # ---- 声音表达层（auto，live）----
    ConfigSpec(
        "tts_volume", "tts", "volume", "tts_volume", "int",
        "播报音量", "系统朗读时有多大声音（0 静音 / 100 最大）",
        panels=_DESKTOP, lo=0, hi=100, default=50,
    ),
    ConfigSpec(
        "tts_speech_rate", "tts", "speech_rate", "tts_speech_rate", "float",
        "语速倍率", "朗读速度，1.0 是常速，越大越快",
        panels=_DESKTOP, lo=0.5, hi=2.0, default=1.0,
    ),
    ConfigSpec(
        "proactive_tts_enabled", "daemon", "proactive_tts_enabled",
        "proactive_tts_enabled", "bool",
        "主动播报是否出声", "关掉后主动提醒只上屏、不朗读（静默但不丢消息）",
        panels=_DESKTOP, default=True,
    ),
    # ---- 简报 / 截止日期（auto，reschedule）----
    ConfigSpec(
        "briefing_enabled", "daemon", "briefing_enabled", "briefing_enabled", "bool",
        "每日简报开关", "每天固定时间播一次今日概览（提醒/节假日/截止/日程）",
        apply_mode=APPLY_RESCHEDULE, panels=_DESKTOP, default=False,
    ),
    ConfigSpec(
        "briefing_time", "daemon", "briefing_time", "briefing_time", "time",
        "简报时间", "每日简报在几点播（HH:MM，24 小时制）",
        apply_mode=APPLY_RESCHEDULE, panels=_DESKTOP, default="08:30",
    ),
    ConfigSpec(
        "deadline_enabled", "deadline", "enabled", "deadline_enabled", "bool",
        "截止日期提醒开关", "是否每天检查截止日期并提醒（7/3/1/0 天分级）",
        apply_mode=APPLY_RESCHEDULE, panels=_DESKTOP, default=False,
    ),
    ConfigSpec(
        "deadline_check_time", "deadline", "check_time", "deadline_check_time", "time",
        "截止日期检查时间", "每天几点检查一遍活跃截止日期（HH:MM）",
        apply_mode=APPLY_RESCHEDULE, panels=_DESKTOP, default="09:00",
    ),
    # ---- 感知层逐源（auto，percept）----
    ConfigSpec(
        "window_enabled", "percept", "window_enabled", "percept_window_enabled", "bool",
        "前台窗口感知", "记「切到哪个应用」；标题脱敏、密码类应用零事件",
        apply_mode=APPLY_PERCEPT, panels=_PERCEPT,
    ),
    ConfigSpec(
        "file_enabled", "percept", "file_enabled", "percept_file_enabled", "bool",
        "目录堆积感知", "盯白名单目录的新增文件（需先配守望目录）",
        apply_mode=APPLY_PERCEPT, panels=_PERCEPT,
    ),
    ConfigSpec(
        "vcs_enabled", "percept", "vcs_enabled", "percept_vcs_enabled", "bool",
        "仓库状况感知", "只跑 git status / stash list 两条只读命令",
        apply_mode=APPLY_PERCEPT, panels=_PERCEPT,
    ),
    ConfigSpec(
        "mail_enabled", "percept", "mail_enabled", "percept_mail_enabled", "bool",
        "重要来信感知", "IMAP 只读取信封头（需先配邮箱）",
        apply_mode=APPLY_PERCEPT, panels=_PERCEPT,
    ),
    ConfigSpec(
        "vision_enabled", "percept", "vision_enabled", "percept_vision_enabled", "bool",
        "摄像头在场感知", "唯一会碰摄像头的源，画面不出本机",
        apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=False,
    ),
    ConfigSpec(
        "calendar_enabled", "percept", "calendar_enabled", "percept_calendar_enabled",
        "bool", "日历事件感知", "日程进入提前窗口时报一次（后端取 [calendar] 段）",
        apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=False,
    ),
    ConfigSpec(
        "system_enabled", "percept", "system_enabled", "percept_system_enabled", "bool",
        "系统资源感知", "CPU/内存/磁盘阈值告警（阈值取 [monitor] 那一组）",
        apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=False,
    ),
    ConfigSpec(
        "window_linger_minutes", "percept", "window_linger_minutes",
        "percept_window_linger_minutes", "int",
        "停留提醒阈值（分钟）", "同一应用停留超过这么久才提一句，0 = 不提",
        apply_mode=APPLY_PERCEPT, panels=_PERCEPT, lo=0, hi=1440, default=0,
    ),
    # ---- 感知层：面板早就能改、但 P1 真源表里没登记的那 6 项（P2 补齐）----
    #
    # 补上来才可能让感知面板从本表派生；同时把它们对模型的口径一次说清：
    # 名单类与摄像头时长改的是“看什么/看多久”，归 confirm；留痕开关归 forbidden。
    ConfigSpec(
        "file_watch_dirs", "percept", "file_watch_dirs", "percept_file_watch_dirs", "list",
        "守望目录", "目录堆积只盯这几个绝对路径；空 = 一个目录也不盯",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=(),
    ),
    ConfigSpec(
        "vcs_repos", "percept", "vcs_repos", "percept_vcs_repos", "list",
        "登记仓库", "仓库状况只查这几个仓（绝对路径）；空 = 一个也不查",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=(),
    ),
    ConfigSpec(
        "mail_watch_senders", "percept", "mail_watch_senders",
        "percept_mail_watch_senders", "list",
        "发件人白名单", "重要来信只报这几个发件人；空 = 不筛发件人",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=(),
    ),
    ConfigSpec(
        "window_exclude_apps", "percept", "window_exclude_apps",
        "percept_window_exclude_apps", "list",
        "排除应用", "列在这里的应用全程不记（密码管理器等）",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=(),
    ),
    ConfigSpec(
        "vision_auto_stop_minutes", "percept", "vision_auto_stop_minutes",
        "percept_vision_auto_stop_minutes", "int",
        "摄像头空闲自动关闭（分钟）", "没人也没动静这么久就松开摄像头，0 = 不自动关闭",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, panels=_PERCEPT,
        lo=0, hi=1440, default=0,
    ),
    ConfigSpec(
        "log_events", "percept", "log_events", "percept_log_events", "bool",
        "感知事件本地留痕", "把采到的事件写进本地 jsonl（面板的事件页读它）",
        tier=TIER_FORBIDDEN, apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=True,
    ),
    # ---- 需要确认的（confirm）：涉及"何时能打断用户"或"干什么活" ----
    ConfigSpec(
        "quiet_hours", "policy.notify_budget", "quiet_hours", "notify_quiet_hours", "quiet",
        "静默时段", "这个时段内不主动吵你（HH:MM-HH:MM，可跨零点）",
        tier=TIER_CONFIRM, apply_mode=APPLY_POLICY, default="",
    ),
    ConfigSpec(
        "daily_max", "policy.notify_budget", "daily_max", "notify_daily_max", "int",
        "每日播报上限", "一天最多主动说几次；超额的进补播队列",
        tier=TIER_CONFIRM, apply_mode=APPLY_POLICY, lo=1, hi=100, default=10,
    ),
    ConfigSpec(
        "min_salience", "policy.notify_budget", "min_salience", "notify_min_salience",
        "float", "播报重要度门槛", "低于这个分数的悄悄留痕、不出声（0~1）",
        tier=TIER_CONFIRM, apply_mode=APPLY_POLICY, lo=0.0, hi=1.0, default=0.3,
    ),
    ConfigSpec(
        "aggregate_window_s", "policy.notify_budget", "aggregate_window_s",
        "notify_aggregate_window_s", "int",
        "同源聚合窗口（秒）", "这段时间内同源的多条合并成一次说，0 = 不聚合",
        tier=TIER_CONFIRM, apply_mode=APPLY_POLICY, lo=0, hi=3600, default=0,
    ),
    ConfigSpec(
        "percept_enabled", "percept", "enabled", "percept_enabled", "bool",
        "感知层总开关", "总开关关掉则所有感知源一起停（逐源开关全部失效）",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, panels=_PERCEPT, default=False,
    ),
    ConfigSpec(
        "tasks_enabled", "tasks", "enabled", "tasks_enabled", "bool",
        "后台任务驱动器开关", "关掉后跨会话目标不再自动推进（已建目标保留）",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, default=False,
    ),
    ConfigSpec(
        "undo_window_s", "actions", "undo_window_s", "actions_undo_window_s", "int",
        "不可逆动作延时窗口（秒）", "发邮件这类收不回的动作先等这么久再真发",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, lo=0, hi=300, default=30,
    ),
)

SPEC_BY_KEY: dict[str, ConfigSpec] = {s.key: s for s in CONFIG_SPECS}

#: 明确禁止模型改动的（键 → 一句话理由）。只登记"名字 + 为什么"，
#: 用途是模型被要求时能给出**正确的改法**，而不是含糊地说"不行"。
#:
#: 每条理由都必须**自己站得住**：``forbidden_reason()`` 一次只返回一条，模型与用户
#: 也是分开看的，写成"同上：…"到了那种上下文就是一句废话——这是写这张表时先踩过
#: 一次的坑（测试里有一条用例专门钉住这一点）。
FORBIDDEN_SPECS: dict[str, str] = {
    "sandbox_enabled": "沙箱是权限护栏本身，模型改它等于给自己提权",
    "sandbox_auto_allow_medium": "决定中等风险命令能否不经人确认就执行，模型改它等于给自己提权",
    "sandbox_excluded_commands": "豁免名单能让指定命令绕过沙箱，模型改它等于给自己开后门",
    "log_events": "本地留痕是感知行为的唯一审计线，模型关它等于让自己的采集不留证据",
    "api_key": "凭据只能由人填（/init 或环境变量），写进配置文件会随备份外洩",
    "email_smtp_password": "邮箱密码是凭据，只能由人填，模型不碰",
    "email_imap_password": "邮箱密码是凭据，只能由人填，模型不碰",
}

#: 键前缀 → 理由（覆盖 api_key_* / sandbox_* 这类成族字段）
FORBIDDEN_PREFIXES: tuple[tuple[str, str], ...] = (
    ("api_key", "凭据只能由人填（/init 或环境变量），模型不碰"),
    ("sandbox_", "沙箱是权限护栏本身，模型改它等于给自己提权"),
)

# ---- 生效钩子：宿主注册，工具按 apply_mode 分派 ----
#: ``{apply_mode: [回调, ...]}``，回调返回 ``(是否成功, 一句说明)``。
#: 为什么不把 hub 直接塞进工具：CLI 场景没有 hub，而"没有宿主"本身要如实回执
#: （"已保存，重启后生效"），而不是假装生效。P1 由 serve 的 hub 注册两条。
_APPLY_HOOKS: dict[str, list[Callable[[], tuple[bool, str]]]] = {}


def register_apply_hook(mode: str, fn: Callable[[], tuple[bool, str]]) -> None:
    """登记某个 apply_mode 的生效回调（宿主装配时调用，同名回调只登记一次）。"""
    hooks = _APPLY_HOOKS.setdefault(mode, [])
    if fn not in hooks:
        hooks.append(fn)


def clear_apply_hooks() -> None:
    """清空全部生效钩子（测试隔离用）。"""
    _APPLY_HOOKS.clear()


def run_apply_hooks(mode: str) -> tuple[bool, list[str]]:
    """跑某个 apply_mode 的生效回调，返回 ``(是否全部成功, 逐条说明)``。

    没有登记回调时返回 ``(False, [])`` —— 调用方据此如实回执"需重启生效"。
    """
    hooks = list(_APPLY_HOOKS.get(mode) or ())
    notes: list[str] = []
    ok = True
    for fn in hooks:
        try:
            good, note = fn()
        except Exception as e:  # noqa: BLE001 - 生效失败不该让配置写入变成失败
            good, note = False, f"生效时出错：{type(e).__name__}: {e}"
        notes.append(note)
        ok = ok and bool(good)
    return (ok and bool(hooks)), notes


# ---- 变更监听器：改完之后谁需要知道 ----
#: ``[回调, ...]``，回调收到 ``{协议键: 新值}``。
#:
#: 为什么与生效钩子分开（2026-10-26 实测）：钩子按 ``apply_mode`` 登记，而 ``live``
#: 类项本来就不需要“生效”动作，却没有钩子可挂；可界面恰恰在这时候最容易说错
#: 话——面板只在连上时拉过一次配置，模型改完音量它仍显示旧值，用户顺手拖一下
#: 滑杆就把旧值写了回去。所以“谁改了、改了什么”要独立广播，与“怎么生效”解耦。
_CHANGE_LISTENERS: list[Callable[[dict[str, Any]], None]] = []


def register_change_listener(fn: Callable[[dict[str, Any]], None]) -> None:
    """登记一个配置变更监听器（宿主装配时调，同一个回调只登记一次）。"""
    if fn not in _CHANGE_LISTENERS:
        _CHANGE_LISTENERS.append(fn)


def clear_change_listeners() -> None:
    """清空全部变更监听器（测试隔离用）。"""
    _CHANGE_LISTENERS.clear()


def notify_changed(changes: Mapping[str, Any]) -> None:
    """广播一次配置变更（已落盘且已改运行时之后调）。

    监听器抛异常一律吞掉：界面刷新失败不该让“配置已改成功”变成失败回执。
    取值一律取**新值**（调用方传入），监听器不需要再回头读文件。
    """
    if not changes:
        return
    payload = {str(k): v for k, v in changes.items()}
    for fn in list(_CHANGE_LISTENERS):
        try:
            fn(dict(payload))
        except Exception:  # noqa: BLE001 - 见 docstring
            continue


# ---- 读写 / 校验 / 落盘 ----


def get_spec(key: str) -> ConfigSpec | None:
    """按键取 spec；不在白名单返回 None。"""
    return SPEC_BY_KEY.get(str(key or "").strip())


def forbidden_reason(key: str) -> str:
    """该键是否被禁止改：返回拒绝理由（空串 = 不是禁止项）。"""
    k = str(key or "").strip()
    if k in FORBIDDEN_SPECS:
        return FORBIDDEN_SPECS[k]
    for prefix, reason in FORBIDDEN_PREFIXES:
        if k.startswith(prefix):
            return reason
    return ""


_KIND_ZERO: dict[str, Any] = {
    "bool": False, "int": 0, "float": 0.0, "time": "", "quiet": "", "list": [],
}

# ---- 列表项护栏（模型与面板共用一份）----
#: 列表最多多少项（防止把 settings.toml 写成几万行的巨型数组）
LIST_MAX_ITEMS = 50
#: 单项最长字符数
LIST_MAX_ITEM_CHARS = 500


def clean_list(value: Any, *, name: str = "列表项") -> list[str]:
    """列表规范化：非列表拒绝，逐项转字符串 + strip + 丢空 + 按序去重 + 上限校验。

    模型侧（``validate_value``）与感知面板侧（``validate_percept_config``）
    共用这一份：同一份配置俩入口各写一套清洗规则，迟早一处放行一处拒。

    Raises:
        ValueError: 非列表 / 含非字符串 / 单项过长 / 项数超限。
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} 必须为字符串数组")
    out: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError(f"{name} 只能包含字符串")
        item = raw.strip()
        if not item or item in out:
            continue
        if len(item) > LIST_MAX_ITEM_CHARS:
            raise ValueError(f"{name} 单项过长（上限 {LIST_MAX_ITEM_CHARS} 字符）")
        out.append(item)
    if len(out) > LIST_MAX_ITEMS:
        raise ValueError(f"{name} 最多 {LIST_MAX_ITEMS} 项")
    return out


def read_value(settings: Any, spec: ConfigSpec) -> Any:
    """读运行时当前值（属性缺失时回退出厂缺省，而不是一律给零值）。

    ``log_events`` 这类"默认开"的字段如果按零值回 False，用户会看到
    "我明明没关它" —— 与 ``percept_settings.read_percept_config`` 同口径。

    list 类型有两个坑，都在这里钉住：

    - 缺省可能是不可变空元组（dataclass 不容可变缺省），**必须转回 list**，
      否则面板拿到 `()` 而前端 `toStringList` 只认数组；
    - 一律返回**新列表**，不能把 `_KIND_ZERO["list"]` 这个共享对象递出去——
      调用方 append 一下就会污染全局零值。

    读侧刻意不做``clean_list``那套上限校验：手改的 TOML 里写了 60 条名单
    也该如实显示（读不该报错），清洗是写入侧的事。
    """
    value = getattr(settings, spec.attr, None) if settings is not None else None
    if spec.kind == "list":
        if value is None:
            return list(spec.default if spec.default is not None else ())
        return [str(v).strip() for v in value if str(v).strip()]
    if value is None:
        return spec.default if spec.default is not None else _KIND_ZERO[spec.kind]
    if spec.kind == "bool":
        return bool(value)
    if spec.kind == "int":
        return int(value)
    if spec.kind == "float":
        return float(value)
    return str(value)


def validate_value(spec: ConfigSpec, value: Any) -> Any:
    """校验并规范化一个值；不符类型/范围/格式一律抛 ``ValueError``。

    严格到"字符串 'true' 也拒"：入参是模型给的 JSON，松散放行会让
    ``tts_volume="50"`` 这类脏值落进配置文件。
    """
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{spec.key} 必须是布尔值（true/false）")
        return value
    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{spec.key} 必须是整数")
        return int(_check_range(spec, float(value)))
    if spec.kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{spec.key} 必须是数值")
        return float(_check_range(spec, float(value)))
    if spec.kind == "time":
        if not isinstance(value, str) or not TIME_RE.match(value):
            raise ValueError(f"{spec.key} 必须是 HH:MM（24 小时制）")
        return value
    if spec.kind == "quiet":
        if not isinstance(value, str) or not QUIET_HOURS_RE.match(value):
            raise ValueError(f"{spec.key} 必须是 HH:MM-HH:MM（如 23:00-07:00；留空=不设静默）")
        return value
    if spec.kind == "list":
        return clean_list(value, name=spec.key)
    raise ValueError(f"{spec.key} 的类型 {spec.kind} 尚未支持")


def _check_range(spec: ConfigSpec, value: float) -> float:
    """数值范围校验（含边界）。"""
    if spec.lo is not None and value < spec.lo:
        raise ValueError(f"{spec.key} 不得小于 {spec.lo:g}")
    if spec.hi is not None and value > spec.hi:
        raise ValueError(f"{spec.key} 不得大于 {spec.hi:g}")
    return value


def toml_literal(spec: ConfigSpec, value: Any) -> str:
    """值 → TOML 字面量（float 保留小数点，避免被解析成整数）。"""
    if spec.kind == "bool":
        return bool_literal(bool(value))
    if spec.kind == "int":
        return str(int(value))
    if spec.kind == "float":
        return repr(float(value))
    if spec.kind == "list":
        return list_literal(value)
    return str_literal(str(value))


def save_value(spec: ConfigSpec, value: Any, *, path: Any = None) -> bool:
    """外科式落盘单个配置项（保留用户注释与其他段），失败返回 False。"""
    return save_section(spec.section, {spec.field: toml_literal(spec, value)}, path=path)


def apply_value(settings: Any, spec: ConfigSpec, value: Any) -> None:
    """把值写进运行时 Settings 实例（是否需要重装配由 apply_mode 决定）。"""
    if settings is not None:
        setattr(settings, spec.attr, value)


def human_value(spec: ConfigSpec, value: Any) -> str:
    """值的人话表示（回执与播报用：布尔写"开/关"，空串写"未设"）。

    列表类必须单独处理：`str(['E:/a'])` 会在回执里括出 Python 列表的方括号与引号，
    用户看到的是一条"程序内部的字符串"而不是"守望哪几个目录"。
    """
    if spec.kind == "bool":
        return "开" if value else "关"
    if spec.kind == "quiet":
        return str(value) or "未设"
    if spec.kind == "time":
        return str(value) or "未设"
    if spec.kind == "float":
        return f"{float(value):g}"
    if spec.kind == "list":
        items = [str(v) for v in (value or ())]
        return "、".join(items) if items else "（空）"
    return str(value)


def list_specs(*, tiers: tuple[str, ...] = (TIER_AUTO, TIER_CONFIRM)) -> tuple[ConfigSpec, ...]:
    """按分级列出可展示的 spec（默认不含 forbidden——连名字都不给）。"""
    return tuple(s for s in CONFIG_SPECS if s.tier in tiers)


def panel_specs(panel: str) -> tuple[ConfigSpec, ...]:
    """某个面板能改的全部 spec（顺序即表内顺序）。

    面板与协议白名单都从这里派生：新增一项只改真源表一处，面板自动跟上，
    不必再去另一份键名列表里补一遍。
    """
    return tuple(s for s in CONFIG_SPECS if panel in s.panels)
