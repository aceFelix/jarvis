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
# 护栏清单与其查询函数独立成模块（2026-11），这里 re-export 给既有调用方。
from agent.config.config_forbidden import (  # noqa: F401 - re-export
    FORBIDDEN_PREFIXES,
    FORBIDDEN_SPECS,
    forbidden_fix,
    forbidden_reason,
)
# 三种结构化类型的解析与校验拆在 config_kinds（2026-11）：主表是"加一项"的节奏，那边是
# "改语义"的节奏，混在一起 diff 读不出谁动了口径；且拆开后才守得住 900 行门禁。
# 这里 re-export，调用方仍从 config_specs 取，不必知道内部拆过。
from agent.config.config_kinds import (  # noqa: F401 - re-export 给既有调用方
    ALIAS_MAP_MAX_ITEMS,
    CUSTOM_MODEL_FIELDS,
    MODEL_TABLE_OPS,
    parse_text_value,
    validate_alias_map,
    validate_enum,
    validate_model_table,
)

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
APPLY_LLM = "llm"              # provider/QueryLoop 是装配快照 → 需重建 LLM 栈（2026-11）

# ---- 落盘目标：两个文件，不是一处 ----
# 模型域（provider/model/max_tokens/temperature/thinking 等）由 models_config 拆到了
# **同层的 models.toml**，不在 settings.toml 里；用户 settings.toml 根本没有 [llm] 段。
# 所以 spec 必须声明自己写哪个文件，否则模型类字段会被写进错的地方。
# @author aceFelix
STORE_SETTINGS = "settings"    # 默认：settings.toml，走 save_section
STORE_MODELS = "models"        # models.toml，走 models_config 的外科式 upsert
STORES: frozenset[str] = frozenset({STORE_SETTINGS, STORE_MODELS})

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
    #: 落盘目标（``STORE_SETTINGS`` / ``STORE_MODELS``）。默认 settings，现有项零改动。
    store: str = STORE_SETTINGS
    #: ``kind == "enum"`` 时的可选值；其它 kind 忽略。
    #: 声明成 enum 却没给可选值视为**表写错**，由 test_enum_specs_declare_choices 把关。
    choices: tuple[str, ...] = ()


#: 两个面板的可改集（表里每项直接引用，不要另写一份键名列表——那就是第四份漂移）
_DESKTOP = frozenset({PANEL_DESKTOP})
_PERCEPT = frozenset({PANEL_PERCEPT})

#: 可改配置项（顺序即 ``ConfigList`` 的展示顺序：先表达层，再确认层）
_BASE_SPECS: tuple[ConfigSpec, ...] = (
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
    ConfigSpec(
        "file_snapshots", "actions", "file_snapshots", "actions_file_snapshots", "int",
        "文件留底保留条数",
        "改文件前把原内容留一份底供 /undo 恢复；超出条数滚最旧的，0 = 关闭留底",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, lo=0, hi=500, default=50,
    ),
    ConfigSpec(
        "file_snapshot_mb", "actions", "file_snapshot_mb", "actions_file_snapshot_mb", "int",
        "文件留底体积上限（MB）",
        "留底是复制不是硬链接，单次超过这个体积的目录就不留底（避免写满磁盘）",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, lo=1, hi=1024, default=64,
    ),
    # ---- 系统维护层（阶段 12 维护切片）：清理能清多少、什么时候清 ----
    #
    # 一律不标 ``reschedule``：自动清理任务确实是启动时注册的快照，但
    # ``hot_update_schedule`` 目前只重注册简报/截止日期，不含自动清理。标成
    # reschedule 会回一句“已按新配置重注册”——而实际什么也没变，比不标更糟。
    # 所以能现读的标 live，注册期快照标 restart，各自把话说准。
    ConfigSpec(
        "maintenance_enabled", "maintenance", "enabled", "maintenance_enabled", "bool",
        "系统维护总开关",
        "唯一会主动删文件的子系统的总闸；关着时 /disk 与 /health 仍可用，只有 /clean 报未启用",
        tier=TIER_CONFIRM, apply_mode=APPLY_LIVE, default=False,
    ),
    ConfigSpec(
        "auto_clean_enabled", "maintenance", "auto_clean_enabled",
        "maintenance_auto_clean_enabled", "bool",
        "无人值守自动清理",
        "每天自动清一次零感垃圾（崩溃转储/错误上报队列）；这是“允许替你删文件”的许可，不能自己开",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, default=True,
    ),
    ConfigSpec(
        "auto_clean_time", "maintenance", "auto_clean_time",
        "maintenance_auto_clean_time", "time",
        "自动清理时间", "每天几点跑自动清理（HH:MM）；错开画像维护的 03:30 便于定位问题",
        tier=TIER_AUTO, apply_mode=APPLY_RESTART, default="04:30",
    ),
    ConfigSpec(
        "auto_clean_min_mb", "maintenance", "auto_clean_min_mb",
        "maintenance_auto_clean_min_mb", "float",
        "自动清理最小回收量（MB）",
        "低于这个量就不建隔离区批次：零感垃圾常只有几 MB，天天建目录的成本高于收益",
        tier=TIER_AUTO, apply_mode=APPLY_LIVE, lo=1.0, hi=10_000.0, default=20.0,
    ),
    ConfigSpec(
        "auto_clean_notify_mb", "maintenance", "auto_clean_notify_mb",
        "maintenance_auto_clean_notify_mb", "float",
        "自动清理静默阈值（MB）",
        "清出的量不到这个数就一句话也不说（例行的事不该消耗注意力）；超过才提示一句",
        tier=TIER_AUTO, apply_mode=APPLY_LIVE, lo=1.0, hi=10_000.0, default=200.0,
    ),
    ConfigSpec(
        "quarantine_days", "maintenance", "quarantine_days", "maintenance_quarantine_days",
        "int", "隔离区保留天数",
        "清理不是删而是先移到隔离区，这么多天后才真删——调小等于缩短可后悔的窗口",
        tier=TIER_CONFIRM, apply_mode=APPLY_LIVE, lo=1, hi=90, default=7,
    ),
    ConfigSpec(
        "scan_max_dir_gb", "maintenance", "scan_max_dir_gb", "maintenance_scan_max_dir_gb",
        "float", "单条规则扫描体积上限（GB）",
        "一条规则累加超过这个体积就不再往下扫（防止扫缓存的目录把整机卡住）",
        tier=TIER_AUTO, apply_mode=APPLY_LIVE, lo=1.0, hi=1000.0, default=50.0,
    ),
    ConfigSpec(
        "scan_timeout", "maintenance", "scan_timeout", "maintenance_scan_timeout", "int",
        "扫描总超时（秒）", "整次清理扫描的硬超时；到时即使没扫完也返回已得结果",
        tier=TIER_AUTO, apply_mode=APPLY_LIVE, lo=10, hi=3600, default=120,
    ),
    ConfigSpec(
        "report_min_mb", "maintenance", "report_min_mb", "maintenance_report_min_mb", "float",
        "报告展示阈值（MB）", "可回收量低于这个数就不列进 /disk 输出（避免满屏几百字节的小项）",
        tier=TIER_AUTO, apply_mode=APPLY_LIVE, lo=0.0, hi=10_000.0, default=100.0,
    ),
    ConfigSpec(
        "allow_full_scan", "maintenance", "allow_full_scan", "maintenance_allow_full_scan",
        "bool", "允许 Defender 全盘扫描",
        "全盘扫描要跑几十分钟且严重占 CPU；默认只给快速扫描，开它等于允许一次长任务",
        tier=TIER_CONFIRM, apply_mode=APPLY_LIVE, default=False,
    ),
    # ---- 能力自我修复（[repair]）：维护层头一次会改环境 ----
    #
    # 为什么不归进 [maintenance]：那是两个不同的许可。用户关掉清理不等于同意
    # 装东西，反之也一样；同一个 enabled 顶两件事迟早会有一件被误放行。
    # 前三项标 confirm：总闸、体积线、动作白名单都在改「管家能不能自己动手」；
    # 只有播报那一项是纯表达层（改的是它说什么，不是它能做什么）。
    # @author aceFelix
    ConfigSpec(
        "repair_enabled", "repair", "enabled", "repair_enabled",
        "bool", "允许自我修复（下内核 / 装 extras / 改配置）",
        "开了它 jarvis 会自己补依赖；三道门仍在：超预算先问、超硬上限只给命令、"
        "无人值守一律升回待批。想收权限先关这一项，而不是去关清理",
        tier=TIER_CONFIRM, apply_mode=APPLY_LIVE, default=True,
    ),
    ConfigSpec(
        "repair_ask_threshold_mb", "repair", "ask_threshold_mb", "repair_ask_threshold_mb",
        "float", "修复前必询问的体积线（MB）",
        "200 的实据：playwright 无头壳 112.4MB 在线内（平时自查够用，自动做），"
        "无头壳 + 完整内核约 296MB 在线外（先看后问）。调小它等于每次都要确认",
        tier=TIER_CONFIRM, apply_mode=APPLY_LIVE, lo=0.0, hi=2000.0, default=200.0,
    ),
    ConfigSpec(
        "repair_disabled_actions", "repair", "disabled_actions", "repair_disabled_actions",
        "list", "单独关掉哪几类修复动作",
        "只认四个白名单 ID：fetch-browser / fetch-model / pip-extras / config-fix。"
        "写错不报错也不生效，改完用 /repair status 确认它真的被关上了",
        tier=TIER_FORBIDDEN, apply_mode=APPLY_LIVE, default=[],
    ),
    ConfigSpec(
        "repair_announce_voice", "repair", "announce_voice", "repair_announce_voice",
        "bool", "修完是否出声播报",
        "默认开：权限大是用户给的，动了什么、花了多少、怎么退得主动送到耳边。"
        "关掉后仍会发文字回执，播报只是多加一道语音",
        tier=TIER_AUTO, apply_mode=APPLY_LIVE, default=True,
    ),
    # 这一项是收尾时补登记的：[repair] 五个字段先前只进了四个，而本文件对「不收哪些项」
    # 的习惯是写明理由（见下面 [monitor] 那段）——没写理由的就是漏了，不是故意。
    # 漏掉的代价不是“改不了”那么轻：用户问「修复为什么把我的对话卡住了」时，
    # 这项会被 `describe_missing()` 报成「没有这个配置项」，而它确实存在。
    # tier 归 confirm：它管的是“长任务要不要堵着对话”，属于打断时机一类。
    # @author aceFelix
    ConfigSpec(
        "repair_background_over_s", "repair", "background_over_s", "repair_background_over_s",
        "float", "预计超此时长转后台任务（秒）",
        "30 的实据：`playwright install chromium` 一分多钟，卡在前台会被当成死掉而被打断；"
        "调大它等于让长任务堵着对话，调小它等于什么都进后台",
        tier=TIER_CONFIRM, apply_mode=APPLY_LIVE, lo=0.0, hi=600.0, default=30.0,
    ),
    # ---- 系统资源监控（阶段 12 补齐）：阈值存于 [monitor]，但由感知源装配时读取 ----
    #
    # 为什么是 ``percept`` 而不是 ``live``：阈值由 ``percept/factory.py`` 在造
    # ``SystemSource`` 时吃进去，改运行时 Settings 不会让已装的源换阈值。
    #
    # 为什么不收 ``[monitor] enabled`` / ``high_cpu_duration``：前者 2026-10-31 删了
    # （开关在 ``[percept] system_enabled``），后者同日随 ``check_high_cpu_processes``
    # 一起下线（功能已由 ``/health`` 的进程 Top N 取代）。两个字段都已不存在，
    # 收进来只会得到一条指向空处的代改入口。
    ConfigSpec(
        "cpu_threshold", "monitor", "cpu_threshold", "monitor_cpu_threshold", "float",
        "CPU 告警阈值（%）",
        "CPU 使用率高于它且持续一段时间就告警；调低会明显变吵，属于“何时能打断你”那一档",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, lo=1.0, hi=100.0, default=85.0,
    ),
    ConfigSpec(
        "memory_threshold", "monitor", "memory_threshold", "monitor_memory_threshold", "float",
        "内存告警阈值（%）", "内存使用率高于它就告警；同样归入“何时能打断你”",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, lo=1.0, hi=100.0, default=90.0,
    ),
    ConfigSpec(
        "disk_threshold", "monitor", "disk_threshold", "monitor_disk_threshold", "float",
        "磁盘剩余告警阈值（%）", "剩余空间低于它就告警；调高会提前收到“快满了”",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, lo=1.0, hi=100.0, default=10.0,
    ),
    ConfigSpec(
        "alert_cooldown", "monitor", "alert_cooldown", "monitor_alert_cooldown", "int",
        "同类告警冷却（秒）", "同一种告警在这段时间内不重复说；调小只会变吵，不改变何时开始告警",
        tier=TIER_AUTO, apply_mode=APPLY_PERCEPT, lo=60, hi=86_400, default=600,
    ),
    ConfigSpec(
        "disk_trend_days", "monitor", "disk_trend_days", "monitor_disk_trend_days", "int",
        "磁盘趋势预测窗口（天）", "按最近趋势推算几天后会满，预测到就将提前告警",
        tier=TIER_AUTO, apply_mode=APPLY_PERCEPT, lo=1, hi=90, default=7,
    ),
    ConfigSpec(
        "check_interval", "monitor", "check_interval", "monitor_check_interval", "int",
        "采样间隔（秒）",
        "感知源多久拉一次系统快照；调密更跟手也更费 CPU，默认 60 与代码常量同口径",
        tier=TIER_AUTO, apply_mode=APPLY_PERCEPT, lo=10, hi=3600, default=60,
    ),
    ConfigSpec(
        "work_break_interval", "monitor", "work_break_interval",
        "monitor_work_break_interval", "int",
        "久坐提醒间隔（秒）",
        "连续工作多久提醒起来活动一下；久坐提醒靠键鼠活动推算，离开超过 5 分钟才清零计时。"
        "调小会明显变吵，属于“何时能打断你”那一档",
        tier=TIER_CONFIRM, apply_mode=APPLY_PERCEPT, lo=600, hi=28_800, default=7200,
    ),
    # ---- 上下文压缩：在启动（以及重建 engine）时构造，改完需重启 ----
    ConfigSpec(
        "context_compaction", "context", "compaction", "context_compaction", "bool",
        "上下文自动压缩",
        "长会话快装满时自动压缩；关掉后会直接撞模型窗口报错，等于改变“一口气能干多少活”",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, default=True,
    ),
    ConfigSpec(
        "context_window", "context", "context_window", "context_window", "int",
        "上下文窗口大小（token）",
        "上下文窗口能装多少 token；换模型时要同步改，填大了会超出模型真实上限而过不了请求",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, lo=8_000, hi=2_000_000, default=128_000,
    ),
    ConfigSpec(
        "compact_ratio", "context", "compact_ratio", "compact_ratio", "float",
        "触发压缩的比例", "上下文用到窗口的这个比例才压缩；调大拖得更久才压，调小压得更频繁",
        tier=TIER_AUTO, apply_mode=APPLY_RESTART, lo=0.1, hi=1.0, default=0.5,
    ),
    ConfigSpec(
        "compact_max_output_tokens", "context", "compact_max_output_tokens",
        "compact_max_output_tokens", "int",
        "压缩摘要输出上限（token）",
        "给压缩摘要请求的额度；太小会丢细节，代码场景不建议调低",
        tier=TIER_AUTO, apply_mode=APPLY_RESTART, lo=256, hi=32_768, default=2048,
    ),
    # ---- 能力总开关：都在启动时装配，改完需重启 ----
    ConfigSpec(
        "enable_mcp", "mcp", "enable", "enable_mcp", "bool",
        "MCP 外部工具接入",
        "关掉后 mcp.json 里的服务器全部不连；改它等于改变“能干什么活”",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, default=True,
    ),
    ConfigSpec(
        "enable_lsp", "lsp", "enable", "enable_lsp", "bool",
        "代码智能（LSP）",
        "关掉后没有跳转定义/找引用/诊断；依赖外部 language server 装没装",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, default=True,
    ),
    ConfigSpec(
        "deferred_loading", "tools", "deferred_loading", "tools_deferred_loading", "bool",
        "工具延迟加载",
        "可选工具只发名字、按需取回；关掉后每轮全量下发 schema，token 会涨得很明显",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, default=True,
    ),
    ConfigSpec(
        "chat_detection", "tools", "chat_detection", "tools_chat_detection", "bool",
        "纯聊天零工具",
        "短消息且无动作意图时一个工具都不发；关掉只是多花 token，不改变能力",
        tier=TIER_AUTO, apply_mode=APPLY_RESTART, default=True,
    ),
    ConfigSpec(
        "enable_skills", "skills", "enable", "enable_skills", "bool",
        "技能包加载",
        "关掉后技能包一个都不加载；插件最后也是落成技能才生效，所以会连带关掉已装插件",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, default=True,
    ),
    ConfigSpec(
        "enable_plugins", "plugins", "enable", "enable_plugins", "bool",
        "插件系统",
        "只管安装/卸载/启停这几个动作；已装插件是否被加载看 enable_skills",
        tier=TIER_CONFIRM, apply_mode=APPLY_RESTART, default=True,
    ),
    ConfigSpec(
        "briefing_calendar", "calendar", "enabled", "calendar_enabled", "bool",
        "简报带今日日程",
        "开了后每日简报多一段「今日日程」（读 Outlook 或 ICS）；感知源那份开关另有其人",
        tier=TIER_AUTO, apply_mode=APPLY_RESTART, default=False,
    ),
    # 注意：``enable_tool_self_healing`` 不在此表——它不在 ``settings_domains`` 的
    # 任何映射里（``[tools]`` 段只有上面两项），``save_value`` 找不到落盘位置。
    # 它确实在 ``bootstrap.py`` 启动时被读、真生效，所以也不能当死配置标——
    # 属于“生效但无处可配”，只能代码里改默认值。混进来只会让回执说假话。
)

# 第一批「模型与上下文」的 12 项放在独立模块（主表已逼近 900 行门禁）。
# 延迟导入：config_specs_llm 需要本模块的 ConfigSpec / TIER_* / STORE_* / APPLY_*，
# 那些到这里都已定义完，故在模块末尾再做这次导入不会形成循环。
from agent.config.config_specs_llm import LLM_SPECS  # noqa: E402

CONFIG_SPECS: tuple[ConfigSpec, ...] = _BASE_SPECS + LLM_SPECS

SPEC_BY_KEY: dict[str, ConfigSpec] = {s.key: s for s in CONFIG_SPECS}

#: 明确禁止模型改动的（键 → 一句话理由）。只登记"名字 + 为什么"，
#: 用途是模型被要求时能给出**正确的改法**，而不是含糊地说"不行"。
#:
#: 每条理由都必须**自己站得住**：``forbidden_reason()`` 一次只返回一条，模型与用户
#: 也是分开看的，写成"同上：…"到了那种上下文就是一句废话——这是写这张表时先踩过
#: 一次的坑（测试里有一条用例专门钉住这一点）。

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




_KIND_ZERO: dict[str, Any] = {
    "bool": False, "int": 0, "float": 0.0, "time": "", "quiet": "", "list": [],
    # 2026-11 第一批新增的三种 kind 的"读侧缺失回退值"。
    # 缺了会在 _current_value() 里 KeyError——那不是运行时该出现的事。
    "enum": "", "alias_map": {}, "model_table": {},
    # 自由字符串（模型名等）：历史表里没有这一档，字符串靠 time/quiet 兜着；
    # 加它是为了让"就是个名字"的项不必假装成时间格式。
    "str": "",
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
    if spec.kind == "str":
        if not isinstance(value, str):
            raise ValueError(f"{spec.key} 必须是字符串")
        return value.strip()
    if spec.kind == "enum":
        return validate_enum(spec, value)
    if spec.kind == "alias_map":
        return validate_alias_map(spec, value)
    if spec.kind == "model_table":
        return validate_model_table(spec, value)
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
    if spec.kind in ("alias_map", "model_table"):
        # 这两种不是"一行标量"，要写成表块，由 store 分派的写入器处理
        # （models.toml 走 upsert_table_block / upsert_custom_model）。
        # 走到这里说明调用方漏了分派——直接报错，绝不能 str(dict) 写进 TOML。
        raise ValueError(f"{spec.key} 是表结构，必须由 store 分派的写入器落盘")
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
    if spec.kind == "enum":
        return str(value) or "（未设）"
    if spec.kind == "alias_map":
        if not isinstance(value, dict):
            return str(value) or "（空）"
        items = value
        if not items:
            return "（空）"
        return f"{len(items)} 个别名：" + "、".join(str(k) for k in items)
    if spec.kind == "model_table":
        # 渲染层不该假设类型：读侧可能拿到历史字符串、None 或整表快照。
        if not isinstance(value, dict):
            return str(value) or "（空）"
        return f"{value.get('op', '')} {value.get('name', '')}".strip() or "（空）"
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
