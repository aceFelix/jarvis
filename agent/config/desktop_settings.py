"""桌面壳暴露的设置项：白名单 schema、校验、外科式落盘与运行时生效。

背景（aceFelix）：设置面板协议（settings.get/set）原仅暴露 proactive_tts_enabled，
第一批扩至简报/截止日期/TTS 语速音量等高频组（桌面壳设置面板分组渲染）。

**阶段 12 P2 改造（2026-10-27）**：本模块不再自己写一份字段表。原先的
`DESKTOP_SETTING_SPECS`（7 项）与 `config_specs.CONFIG_SPECS`（模型侧真源表）
是同一批字段的两份抄写，任一处漏改就会出现"面板能改"与"模型能改"两张名单
各说各话（实测就抓到过：感知总开关在协议两侧叫两个名字）。现在字段、类型、
范围、TOML 节/字段全部从真源表**派生**：

- `DESKTOP_SETTING_SPECS` = 真源表里标了 ``PANEL_DESKTOP`` 的那几项；
- `validate_setting` / `save_setting` / `_toml_literal` 转发给 config_specs 的
  通用实现（同一份范围检查与字面量渲染，两处口径不可能再分叉）。

对外 API 一个不变（`DESKTOP_SETTING_SPECS` / `SPEC_BY_KEY` / `SCHEDULE_KEYS` /
`validate_setting` / `read_setting` / `apply_setting` / `save_setting`）——server
与既有测试都照旧调用；`read_setting` 刻意仍保留自己的"属性缺失返回 None"
口径（面板据此显离线态），不跟着 config_specs 回落出厂缺省。
密钥（api_key 等）、自由路径与极客参数一律不进真源表的桌面面板集。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.config.config_specs import (
    PANEL_DESKTOP,
    ConfigSpec,
    panel_specs,
    save_value,
    toml_literal,
    validate_value,
)


@dataclass(frozen=True)
class SettingSpec:
    """单个桌面可改设置项的元数据（由真源表派生，不另写一份）。

    Attributes:
        key / section / field / attr / kind / lo / hi: 与 ``ConfigSpec`` 同口径的视图。
        source: 对应的真源项——校验/落盘直接拿它转发，避免二次转换失真。
    """

    key: str  # 协议键（settings.get/set payload 字段名）
    section: str  # settings.toml 节名
    field: str  # settings.toml 字段名
    attr: str  # Settings 实例属性名
    kind: str  # bool / int / float / time
    lo: float | None = None  # 数值下限（含）
    hi: float | None = None  # 数值上限（含）
    source: ConfigSpec | None = None  # 真源项（派生时必填，测试替身可省）


def _derive(spec: ConfigSpec) -> SettingSpec:
    """真源项 → 面板视图（字段一一对应，不额外推断）。"""
    return SettingSpec(
        spec.key, spec.section, spec.field, spec.attr, spec.kind, spec.lo, spec.hi, spec
    )


#: 面板可改项（顺序即真源表顺序；新增一项只改 config_specs 一处）
DESKTOP_SETTING_SPECS: tuple[SettingSpec, ...] = tuple(
    _derive(s) for s in panel_specs(PANEL_DESKTOP)
)

SPEC_BY_KEY: dict[str, SettingSpec] = {s.key: s for s in DESKTOP_SETTING_SPECS}

# 改动后需 ProactiveHub 重注册调度任务的键（调度任务是启动时快照，不重注册不生效）
SCHEDULE_KEYS = frozenset(
    {"briefing_enabled", "briefing_time", "deadline_enabled", "deadline_check_time"}
)


def validate_setting(key: str, value: Any) -> Any:
    """按真源表校验设置值并返回规范化值；未知键/类型不符/超范围抛 ValueError。

    未知键的报错用本模块的措辞（"不支持的设置项"）——面板那边靠它区分
    "打错键"与"值不合法"，转发实现时保留这层语义。
    """
    spec = SPEC_BY_KEY.get(key)
    if spec is None or spec.source is None:
        raise ValueError(f"不支持的设置项 {key}")
    return validate_value(spec.source, value)


def read_setting(settings: Any, spec: SettingSpec) -> Any:
    """读取设置项运行时值（属性缺失返回 None，桌面壳据此显离线态）。

    与 `config_specs.read_value` 的差别是**刻意**的：那边属性缺失回出厂缺省
    （给模型与终端看"实际生效值"），这边回 None（给面板看"没拉到/未连接"）。
    """
    value = getattr(settings, spec.attr, None)
    if value is None:
        return None
    if spec.kind == "bool":
        return bool(value)
    if spec.kind == "int":
        return int(value)
    if spec.kind == "float":
        return float(value)
    return str(value)


def apply_setting(settings: Any, spec: SettingSpec, value: Any) -> None:
    """把值写进运行时 Settings 实例（每次播报/判定现读的项立即生效）。"""
    setattr(settings, spec.attr, value)


def _toml_literal(spec: SettingSpec, value: Any) -> str:
    """设置值转 TOML 字面量（转发真源表的渲染：float 保留小数点）。"""
    if spec.source is None:
        raise ValueError(f"{spec.key} 缺少真源项，无法渲染 TOML 字面量")
    return toml_literal(spec.source, value)


def save_setting(spec: SettingSpec, value: Any) -> bool:
    """外科式落盘单个设置项到 ~/.jarvis/settings.toml（实现委派 config_specs）。

    返回 True 表示成功；IO 失败返回 False（调用方据此回执报错，不改运行时）。

    @author aceFelix
    """
    if spec.source is None:
        return False
    return save_value(spec.source, value)
