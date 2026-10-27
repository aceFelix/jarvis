"""感知层（W2）桌面可改配置：白名单 schema、校验、落盘与运行时生效。

背景（aceFelix，2026-10 阶段 9 W2 第 5 步）：桌面「感知源」面板要能做两件事——
看（`percept.status.get`：总线在采什么、采到了什么）与改（`percept.config.set`：
逐源开关与白名单）。改必须同时落 `~/.jarvis/settings.toml` 的 `[percept]` 表并
作用到运行时 Settings，否则就是「本次生效、重启回退」的口径分裂。

**阶段 12 P2 改造（2026-10-27）**：字段表不再手写，改成从 `config_specs`
的真源表派生（`panels` 里标了 ``PANEL_PERCEPT`` 的那些项）。动机是一次实测：
面板叫 `enabled`、模型侧叫 `percept_enabled`，同一项两个名字，而两张表各自维护——
加一个字段要记得改两处，漏一处就是"面板能改但模型看不见"。

面板侧的名字与真源表协议键的映射写在 `PANEL_KEY_ALIASES` 里，**只有一条**
（总开关：`[percept]` 表里叫 `enabled`，协议层为避免跳节歧义叫 `percept_enabled`）。
其余字段两边同名。测试会钉住"每个面板字段都能在真源表里找到"与"别名没有多余项"。

为什么不塞进 `desktop_settings` 的通用白名单：那边只支持 bool/int/float/time，
感知有**列表项**（守望目录、登记仓库、发件人白名单、排除应用）；且感知改完要
宿主**重装配总线**（逐源 enabled 是启动快照），与 tts 那类「现读即生效」语义不同。

隐私红线（决策 2 的延续）：本白名单**只放开关与白名单**，不放任何"放宽采集范围"
的极客参数（`dedup_window_s`/`min_salience`/`history_max`/`vision_fps` 等都只能
手改 TOML）；三个未装配的收编位（calendar/system 已接入；schedule 未接入）
**不为 schedule 开开关**——面板上给个拨动就采不到任何东西的开关，比没有开关更糟。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.config.config_specs import (
    LIST_MAX_ITEMS,
    LIST_MAX_ITEM_CHARS,
    PANEL_PERCEPT,
    ConfigSpec,
    clean_list,
    panel_specs,
    read_value,
    save_section,
    toml_literal,
    validate_value,
)

#: 感知配置所在的 TOML 节
SECTION = "percept"

#: 面板侧键名 → 真源表协议键（目前只有总开关一条）。
#:
#: 为何不统一：`[percept] enabled` 在面板对象里就叫 `enabled`（对象名已经限定了
#: 语境），而协议层面对一个扁平的键列表，叫 `enabled` 会与 `[deadline] enabled`
#: 撞名。两边各自合理，所以把差异写成一张显式表，而不是靠"碰巧一致"。
PANEL_KEY_ALIASES: dict[str, str] = {"enabled": "percept_enabled"}


@dataclass(frozen=True)
class PerceptField:
    """单个感知配置项的元数据（派生自真源项，不另写一份）。"""

    key: str  # 面板协议键（`percept.config.set` 的字段名）
    attr: str  # Settings 实例属性名（percept_ 前缀）
    kind: str  # bool / int / list
    # 中文标题：给**模型回执与 ConfigList** 用。面板不走它——桌面壳用自己那套
    # i18n（`butler.percept.*`），两个语境文案本来就该各自贴合，没必要强行统一。
    label: str = ""
    lo: int | None = None  # int 下限（含）
    hi: int | None = None  # int 上限（含）
    default: Any = None  # 出厂缺省（None = 按 kind 取零值；log_events 这种“默认开”的必须显写）
    source: ConfigSpec | None = None  # 真源项：校验/落盘/读值全部转发给它


def _derive(spec: ConfigSpec) -> PerceptField:
    """真源项 → 面板视图（面板键走 `PANEL_KEY_ALIASES` 反查）。"""
    panel_key = _PANEL_KEY_BY_SPEC.get(spec.key, spec.key)
    lo = int(spec.lo) if spec.lo is not None else None
    hi = int(spec.hi) if spec.hi is not None else None
    return PerceptField(
        panel_key, spec.attr, spec.kind, spec.label, lo, hi, spec.default, spec
    )


#: 真源项键 → 面板键（只有总开关会不同；其余同名）
_PANEL_KEY_BY_SPEC: dict[str, str] = {spec: panel for panel, spec in PANEL_KEY_ALIASES.items()}

#: 面板可改的感知配置（顺序即表单渲染顺序；顺序即真源表顺序）
PERCEPT_CONFIG_FIELDS: tuple[PerceptField, ...] = tuple(
    _derive(s) for s in panel_specs(PANEL_PERCEPT)
)

FIELD_BY_KEY: dict[str, PerceptField] = {f.key: f for f in PERCEPT_CONFIG_FIELDS}

#: 各类型的零缺省（字段没显写 `default` 时用它）
_KIND_ZERO: dict[str, Any] = {"bool": False, "int": 0, "list": []}


def read_percept_config(settings: Any) -> dict[str, Any]:
    """读运行时 `[percept]` 快照（面板渲染数据源；感知域无密钥，可原样回传）。

    属性缺失时按 **该字段的出厂缺省** 返回（而不是一律 False/0/空）：
    `log_events` 出厂是开的，拿 False 去渲染会把“没配过”误显成“用户已关”。

    列表项顺带 strip + 去重（与写入侧的 `clean_list` 同口径）。
    """
    result: dict[str, Any] = {}
    for f in PERCEPT_CONFIG_FIELDS:
        if f.source is not None:
            result[f.key] = read_value(settings, f.source)
            continue
        # 真源缺失（理论上不会：派生时必带）——保留兼底口径，不让面板白屏
        value = getattr(settings, f.attr, None)
        if value is None:
            result[f.key] = f.default if f.default is not None else _KIND_ZERO[f.kind]
        elif f.kind == "bool":
            result[f.key] = bool(value)
        elif f.kind == "int":
            result[f.key] = int(value)
        else:
            result[f.key] = clean_list(value, name=f.key)
    return result


def validate_percept_config(data: dict[str, Any]) -> dict[str, Any]:
    """校验 `percept.config.set` 入参，返回规范化补丁（只含实际提交的键）。

    规则全部转发真源表（两入口共用一份，不各自实现）：
    - 不在白名单里的键**直接报错**（不是静默忽略）：面板是机器拼的 payload，
      静默忽略会把"打不开的开关"变成"点了没反应"；
    - bool 要真布尔（前端传 "true" 一律拒）；int 要整数且在 [lo, hi] 内
      （bool 在 Python 里是 int 的子类，显式排除）；list 走 `clean_list`。

    @author aceFelix
    """
    patch: dict[str, Any] = {}
    for key, value in data.items():
        field = FIELD_BY_KEY.get(key)
        if field is None:
            raise ValueError(f"不支持的感知配置项 {key}")
        if field.source is not None:
            patch[key] = validate_value(field.source, value)
        elif field.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{key} 必须为布尔值")
            patch[key] = value
        elif field.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} 必须为整数")
            patch[key] = value
        else:
            patch[key] = clean_list(value, name=key)
    return patch


def apply_percept_config(settings: Any, patch: dict[str, Any]) -> None:
    """把补丁写进运行时 Settings（随后宿主重装配总线才真正生效，见 rpc_percept）。"""
    for key, value in patch.items():
        setattr(settings, FIELD_BY_KEY[key].attr, value)


def save_percept_config(patch: dict[str, Any]) -> bool:
    """外科式落盘到 settings.toml 的 `[percept]` 表（保住用户注释与其他字段）。

    一批只写一次文件（不是逐项写）：一次 set 带多个键时，逐项落盘会在中途失败
    时留下"改了一半"，与 ConfigSet 的整批原子口径也不一致。

    Returns:
        True 成功；IO 失败 False——调用方据此回执报错且**不动**运行时。
    """
    lines: dict[str, str] = {}
    for key, value in patch.items():
        field = FIELD_BY_KEY[key]
        if field.source is not None:
            lines[field.source.field] = toml_literal(field.source, value)
        elif field.kind == "bool":
            lines[key] = "true" if value else "false"
        elif field.kind == "int":
            lines[key] = str(int(value))
        else:
            lines[key] = str(value)
    return save_section(SECTION, lines)


def _clean_list(value: Any, *, name: str = "") -> list[str]:
    """保持旧名可用（已下沉到 `config_specs.clean_list`，这里只做转发）。"""
    return clean_list(value, name=name or "列表项")
