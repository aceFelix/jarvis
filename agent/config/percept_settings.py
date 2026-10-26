"""感知层（W2）桌面可改配置：白名单 schema、校验、落盘与运行时生效。

背景（aceFelix，2026-10 阶段 9 W2 第 5 步）：桌面「感知源」面板要能做两件事——
看（`percept.status.get`：总线在采什么、采到了什么）与改（`percept.config.set`：
逐源开关与白名单）。改必须同时落 `~/.jarvis/settings.toml` 的 `[percept]` 表并
作用到运行时 Settings，否则就是「本次生效、重启回退」的口径分裂。

为什么不塞进 `desktop_settings.py` 的通用白名单：
- 那边只支持 bool/int/float/time，感知面板的**列表项**（白名单目录、登记仓库、
  发件人白名单、排除应用）表达不了；
- 感知的开关改完还要宿主**重装配总线**（逐源 enabled 是启动快照），与 tts 那类
  「现读即生效」的语义不同，混在一张 SPEC 表里会让 `settings.set` 的分支越来越脏。

隐私红线（决策 2 的延续）：本白名单**只放开关与白名单**，不放任何"放宽采集范围"
的极客参数（`dedup_window_s`/`min_salience`/`history_max`/`vision_fps` 等都只能
手改 TOML）；三个未装配的收编位（calendar/system/schedule）**不入白名单**——
面板上给个拨动就采不到任何东西的开关，比没有开关更糟。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.config.toml_writer import bool_literal, list_literal, save_section

#: 感知配置所在的 TOML 节
SECTION = "percept"

#: 列表项的护栏（防面板把 settings.toml 写成几万行的巨型数组）
LIST_MAX_ITEMS = 50
LIST_MAX_ITEM_CHARS = 500
#: int 项的通用范围（分钟类：0 = 关闭该特性，上限一天）
MINUTES_MIN = 0
MINUTES_MAX = 1440


@dataclass(frozen=True)
class PerceptField:
    """单个感知配置项的元数据（协议键 = TOML `[percept]` 字段名）。"""

    key: str  # 协议键，同时是 [percept] 表里的字段名（settings_domains 同口径）
    attr: str  # Settings 实例属性名（percept_ 前缀）
    kind: str  # bool / int / list
    label: str = ""  # 面板标题（中文，避免前端硬编码文案）
    lo: int | None = None  # int 下限（含）
    hi: int | None = None  # int 上限（含）
    default: Any = None  # 出厂缺省（None = 按 kind 取零值；log_events 这种“默认开”的必须显写）


#: 面板可改的感知配置（顺序即表单渲染顺序；总开关在最前）
PERCEPT_CONFIG_FIELDS: tuple[PerceptField, ...] = (
    PerceptField("enabled", "percept_enabled", "bool", "感知层总开关"),
    PerceptField("window_enabled", "percept_window_enabled", "bool", "前台窗口"),
    PerceptField("file_enabled", "percept_file_enabled", "bool", "目录堆积"),
    PerceptField("vcs_enabled", "percept_vcs_enabled", "bool", "仓库状况"),
    PerceptField("mail_enabled", "percept_mail_enabled", "bool", "重要来信"),
    PerceptField("vision_enabled", "percept_vision_enabled", "bool", "摄像头在场"),
    PerceptField("calendar_enabled", "percept_calendar_enabled", "bool", "日历事件"),
    PerceptField("system_enabled", "percept_system_enabled", "bool", "系统资源"),
    PerceptField("log_events", "percept_log_events", "bool", "本地留痕事件", default=True),
    PerceptField(
        "file_watch_dirs", "percept_file_watch_dirs", "list", "守望目录（绝对路径）"
    ),
    PerceptField("vcs_repos", "percept_vcs_repos", "list", "登记仓库（绝对路径）"),
    PerceptField("mail_watch_senders", "percept_mail_watch_senders", "list", "发件人白名单"),
    PerceptField("window_exclude_apps", "percept_window_exclude_apps", "list", "排除应用"),
    PerceptField(
        "window_linger_minutes", "percept_window_linger_minutes", "int",
        "停留提醒阈值（分钟）", MINUTES_MIN, MINUTES_MAX,
    ),
    PerceptField(
        "vision_auto_stop_minutes", "percept_vision_auto_stop_minutes", "int",
        "摄像头空闲自动关闭（分钟）", MINUTES_MIN, MINUTES_MAX,
    ),
)

FIELD_BY_KEY: dict[str, PerceptField] = {f.key: f for f in PERCEPT_CONFIG_FIELDS}

#: 各类型的零缺省（字段没显写 `default` 时用它）
_KIND_ZERO: dict[str, Any] = {"bool": False, "int": 0, "list": []}


def read_percept_config(settings: Any) -> dict[str, Any]:
    """读运行时 `[percept]` 快照（面板渲染数据源；感知域无密钥，可原样回传）。

    属性缺失时按 **该字段的出厂缺省** 返回（而不是一律 False/0/空）：
    `log_events` 出厂是开的，拿 False 去渲染会把“没配过”误显成“用户已关”。
    """
    result: dict[str, Any] = {}
    for f in PERCEPT_CONFIG_FIELDS:
        value = getattr(settings, f.attr, None)
        if value is None:
            result[f.key] = f.default if f.default is not None else _KIND_ZERO[f.kind]
        elif f.kind == "bool":
            result[f.key] = bool(value)
        elif f.kind == "int":
            result[f.key] = int(value)
        else:
            result[f.key] = _clean_list(value)
    return result


def validate_percept_config(data: dict[str, Any]) -> dict[str, Any]:
    """校验 `percept.config.set` 入参，返回规范化补丁（只含实际提交的键）。

    规则：
    - 不在 `PERCEPT_CONFIG_FIELDS` 白名单里的键**直接报错**（不是静默忽略）：
      面板是机器拼的 payload，静默忽略会把"打不开的开关"变成"点了没反应"；
    - bool 必须是真布尔（前端传 "true" 字符串一律拒绝）；
    - int 必须是整数且在 `[lo, hi]` 内（bool 在 Python 里是 int 的子类，显式排除）；
    - list 逐项 strip、丢空项、按序去重，超 `LIST_MAX_ITEMS` 项或单项超
      `LIST_MAX_ITEM_CHARS` 字符报错。

    @author aceFelix
    """
    patch: dict[str, Any] = {}
    for key, value in data.items():
        field = FIELD_BY_KEY.get(key)
        if field is None:
            raise ValueError(f"不支持的感知配置项 {key}")
        if field.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须为布尔值")
            patch[key] = value
        elif field.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} 必须为整数")
            if field.lo is not None and value < field.lo:
                raise ValueError(f"{key} 不得小于 {field.lo}")
            if field.hi is not None and value > field.hi:
                raise ValueError(f"{key} 不得大于 {field.hi}")
            patch[key] = value
        else:
            patch[key] = _clean_list(value, name=key)
    return patch


def apply_percept_config(settings: Any, patch: dict[str, Any]) -> None:
    """把补丁写进运行时 Settings（随后宿主重装配总线才真正生效，见 rpc_percept）。"""
    for key, value in patch.items():
        setattr(settings, FIELD_BY_KEY[key].attr, value)


def save_percept_config(patch: dict[str, Any]) -> bool:
    """外科式落盘到 settings.toml 的 `[percept]` 表（保住用户注释与其他字段）。

    Returns:
        True 成功；IO 失败 False——调用方据此回执报错且**不动**运行时。
    """
    lines: dict[str, str] = {}
    for key, value in patch.items():
        field = FIELD_BY_KEY[key]
        if field.kind == "bool":
            lines[key] = bool_literal(bool(value))
        elif field.kind == "int":
            lines[key] = str(int(value))
        else:
            lines[key] = list_literal(value)
    return save_section(SECTION, lines)


def _clean_list(value: Any, *, name: str = "") -> list[str]:
    """列表规范化：非列表拒绝，逐项转字符串 + strip + 丢空 + 按序去重 + 上限校验。"""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name or '列表项'} 必须为字符串数组")
    out: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError(f"{name or '列表项'} 只能包含字符串")
        item = raw.strip()
        if not item or item in out:
            continue
        if len(item) > LIST_MAX_ITEM_CHARS:
            raise ValueError(f"{name or '列表项'} 单项过长（上限 {LIST_MAX_ITEM_CHARS} 字符）")
        out.append(item)
    if len(out) > LIST_MAX_ITEMS:
        raise ValueError(f"{name or '列表项'} 最多 {LIST_MAX_ITEMS} 项")
    return out
