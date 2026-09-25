"""桌面壳暴露的设置项：白名单 schema、校验、外科式落盘与运行时生效。

背景（aceFelix）：设置面板协议（settings.get/set）原仅暴露 proactive_tts_enabled；
第一批扩至简报/截止日期/TTS 语速音量等高频组（桌面壳设置面板分组渲染）。
本模块是「桌面可改」白名单的单一真源：
- DESKTOP_SETTING_SPECS 登记 key → TOML 节/字段、Settings 属性、类型与取值范围；
- validate_setting 类型 + 范围 + HH:MM 校验（失败抛 ValueError，调用方回执报错）；
- save_setting 外科式文本落盘（与 model_registry save_* 族同口径：保留注释与
  其他字段，节缺失追加末尾，文件缺失创建最小文件）；
- apply_setting 改运行时 Settings 实例（tts/proactive_tts 每次播报现读立即生效；
  briefing/deadline 另需 ProactiveHub.hot_update_schedule 重注册调度任务）。
密钥（api_key 等）、自由路径与极客参数一律不入 SCHEMA——桌面协议永不触碰。

@author aceFelix
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# HH:MM 24 小时制（briefing_time / deadline_check_time 取值格式）
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class SettingSpec:
    """单个桌面可改设置项的元数据。"""

    key: str  # 协议键（settings.get/set payload 字段名）
    section: str  # settings.toml 节名
    field: str  # settings.toml 字段名
    attr: str  # Settings 实例属性名
    kind: str  # bool / int / float / time
    lo: float | None = None  # 数值下限（含）
    hi: float | None = None  # 数值上限（含）


DESKTOP_SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec("proactive_tts_enabled", "daemon", "proactive_tts_enabled", "proactive_tts_enabled", "bool"),
    SettingSpec("briefing_enabled", "daemon", "briefing_enabled", "briefing_enabled", "bool"),
    SettingSpec("briefing_time", "daemon", "briefing_time", "briefing_time", "time"),
    SettingSpec("deadline_enabled", "deadline", "enabled", "deadline_enabled", "bool"),
    SettingSpec("deadline_check_time", "deadline", "check_time", "deadline_check_time", "time"),
    SettingSpec("tts_volume", "tts", "volume", "tts_volume", "int", 0, 100),
    SettingSpec("tts_speech_rate", "tts", "speech_rate", "tts_speech_rate", "float", 0.5, 2.0),
)

SPEC_BY_KEY: dict[str, SettingSpec] = {s.key: s for s in DESKTOP_SETTING_SPECS}

# 改动后需 ProactiveHub 重注册调度任务的键（调度任务是启动时快照，不重注册不生效）
SCHEDULE_KEYS = frozenset(
    {"briefing_enabled", "briefing_time", "deadline_enabled", "deadline_check_time"}
)


def validate_setting(key: str, value: Any) -> Any:
    """按 schema 校验设置值并返回规范化值；未知键/类型不符/超范围抛 ValueError。"""
    spec = SPEC_BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"不支持的设置项 {key}")
    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须为布尔值")
        return value
    if spec.kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} 必须为整数")
        return _check_range(spec, value)
    if spec.kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} 必须为数值")
        return _check_range(spec, float(value))
    # time：HH:MM 字符串
    if not isinstance(value, str) or not TIME_RE.match(value):
        raise ValueError(f"{key} 必须为 HH:MM 格式（24 小时制）")
    return value


def _check_range(spec: SettingSpec, value: float) -> float:
    """数值范围校验（含边界），超范围抛 ValueError。"""
    if spec.lo is not None and value < spec.lo:
        raise ValueError(f"{spec.key} 不得小于 {spec.lo:g}")
    if spec.hi is not None and value > spec.hi:
        raise ValueError(f"{spec.key} 不得大于 {spec.hi:g}")
    return value


def read_setting(settings: Any, spec: SettingSpec) -> Any:
    """读取设置项运行时值（属性缺失返回 None，桌面壳据此显离线态）。"""
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
    """设置值转 TOML 字面量（float 保留小数点，避免被解析成整数）。"""
    if spec.kind == "bool":
        return "true" if value else "false"
    if spec.kind == "int":
        return str(int(value))
    if spec.kind == "float":
        return repr(float(value))
    return f'"{value}"'


def save_setting(spec: SettingSpec, value: Any) -> bool:
    """外科式落盘单个设置项到 ~/.jarvis/settings.toml（保留注释与其他字段）。

    - 节存在：替换已有字段行，节内没有则插在节头后；
    - 节不存在：文件末尾追加新节；文件不存在则创建最小文件。
    返回 True 表示成功；IO 失败返回 False（调用方据此回执报错，不改运行时）。
    """
    line = f"{spec.field} = {_toml_literal(spec, value)}"
    toml_path = Path.home() / ".jarvis" / "settings.toml"
    try:
        toml_path.parent.mkdir(parents=True, exist_ok=True)
        if not toml_path.exists():
            toml_path.write_text(f"[{spec.section}]\n{line}\n", encoding="utf-8")
            return True

        content = toml_path.read_text(encoding="utf-8")
        # 节头匹配不用 \s*$（\s 会吞换行导致后续切片偏移），只容忍行内空白
        section_match = re.search(
            rf"^\[{re.escape(spec.section)}\][ \t]*$", content, re.MULTILINE
        )
        if section_match:
            head_end = section_match.end() + 1  # 跳过节头后的换行
            rest = content[head_end:]
            nxt = re.search(r"^\[", rest, re.MULTILINE)
            sec_end = head_end + (nxt.start() if nxt else len(rest))
            section = content[head_end:sec_end]
            if re.search(rf"^{re.escape(spec.field)}\s*=", section, re.MULTILINE):
                new_section = re.sub(
                    rf"^{re.escape(spec.field)}\s*=.*$", line, section, flags=re.MULTILINE
                )
            else:
                new_section = line + "\n" + section
            content = content[:head_end] + new_section + content[sec_end:]
        else:
            content = content.rstrip() + f"\n\n[{spec.section}]\n{line}\n"

        toml_path.write_text(content, encoding="utf-8")
        return True
    except OSError:
        return False
