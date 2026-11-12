"""三种结构化配置类型的解析与校验（2026-11 自配置第一批）。

从 `config_specs.py` 拆出来，原因有两条：

1. 那是主真源表，加进 176 行后越过项目 900 行门禁（实测 1008 行）；
2. 这两件事的改动节奏不同——表的变动是"加一项"，解析的变动是"改语义"，
   混在一起看 diff 很难判断哪边动了口径。

**判据只有一条**：解析失败必须给出**可照做的写法**，而不是只说"格式错误"。
调用方是模型，它要据此改写；说"错了"它只会换个猜法再试一次。

@author aceFelix
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 只为类型标注；运行时不导入，避免与 config_specs 循环依赖
    from agent.config.config_specs import ConfigSpec


# ---- enum ----

#: enum 的取值别名（只放跨字段通用的口语说法；字段私有的别名写在 choices 里）
_ENUM_ALIASES: dict[str, str] = {
    "最高": "high", "最高档": "high", "高": "high",
    "中": "medium", "中档": "medium", "中等": "medium",
    "低": "low", "最低": "low", "最低档": "low",
}


def validate_enum(spec: "ConfigSpec", value: Any) -> str:
    """enum：大小写不敏感 + 别名；非法值要**列出全部可选项**。"""
    if not isinstance(value, str):
        raise ValueError(f"{spec.key} 必须是字符串取值：{'/'.join(spec.choices)}")
    raw = value.strip()
    canonical = _ENUM_ALIASES.get(raw) or raw.lower()
    if canonical not in spec.choices:
        raise ValueError(f"{spec.key} 只能是 {'/'.join(spec.choices)}（收到 {raw or '空'}）")
    return canonical


# ---- alias_map（别名表：别名 → 展示名）----

#: alias_map 的项数上限（与 list 同口径，防止把 TOML 写成几万行）
ALIAS_MAP_MAX_ITEMS = 50


def validate_alias_map(spec: "ConfigSpec", value: Any) -> dict[str, str]:
    """alias_map：``{别名: 展示名}``；空表合法（表示清空）。"""
    if not isinstance(value, dict):
        raise ValueError(f"{spec.key} 必须是 别名→展示名 的映射")
    if len(value) > ALIAS_MAP_MAX_ITEMS:
        raise ValueError(
            f"{spec.key} 最多 {ALIAS_MAP_MAX_ITEMS} 项，当前 {len(value)} 项"
        )
    out: dict[str, str] = {}
    for k, v in value.items():
        key = str(k or "").strip()
        if not key:
            raise ValueError(f"{spec.key} 的别名不能为空")
        out[key] = str(v or "").strip()
    return out


# ---- model_table（自定义模型：单条增删）----

#: 自定义模型允许写的字段（未知字段一律拒绝，防拼错写出半残配置）
CUSTOM_MODEL_FIELDS: tuple[str, ...] = (
    "name", "provider_type", "model_type", "base_url", "api_key_env", "max_tokens",
)

#: model_table 的操作名
MODEL_TABLE_OPS: tuple[str, ...] = ("add", "remove")


def validate_model_table(spec: "ConfigSpec", value: Any) -> dict[str, Any]:
    """model_table：只接受 ``{op: add|remove, name, fields}`` 三种键。"""
    if not isinstance(value, dict):
        raise ValueError(
            f'{spec.key} 必须写成 {{"op": "add|remove", "name": ..., "fields": {{...}}}}'
        )
    op = str(value.get("op") or "").strip().lower()
    if op not in MODEL_TABLE_OPS:
        raise ValueError(f"{spec.key} 的操作只能是 add / remove（收到 {op or '空'}）")
    name = str(value.get("name") or "").strip()
    if not name:
        raise ValueError(f"{spec.key} 需要模型名")
    fields = value.get("fields") or {}
    if op == "remove":
        if fields:
            raise ValueError(f"{spec.key} 的 remove 不接受 fields（只删不改）")
        return {"op": op, "name": name, "fields": {}}
    if not isinstance(fields, dict):
        raise ValueError(f"{spec.key} 的 fields 必须是 键=值 映射")
    bad = [str(k) for k in fields if str(k) not in CUSTOM_MODEL_FIELDS]
    if bad:
        raise ValueError(
            f"{spec.key} 不认识的字段 {', '.join(bad)}；可用："
            f"{', '.join(CUSTOM_MODEL_FIELDS)}"
        )
    return {
        "op": op,
        "name": name,
        "fields": {str(k): str(v) for k, v in fields.items()},
    }


# ---- 文本 → 结构值（模型/终端给的是字符串，不是 JSON 结构）----


def parse_text_value(
    spec: "ConfigSpec", raw: str, notes: list[str] | None = None
) -> Any:
    """把**文本**解析成结构值并校验；解析与校验一体，失败即抛 ``ValueError``。

    Args:
        notes: 可选，收集"用户该知道但不该算失败"的情形（如重复别名后者覆盖）。
            由调用方拼进回执——静默吞掉这类信息，用户会以为两份都生效了。
    """
    if spec.kind == "enum":
        return validate_enum(spec, raw)
    if spec.kind == "alias_map":
        return validate_alias_map(spec, _parse_alias_map_text(spec, raw, notes))
    if spec.kind == "model_table":
        return validate_model_table(spec, _parse_model_table_text(spec, raw))
    raise ValueError(f"{spec.key} 的 kind={spec.kind} 不走文本解析")


def _parse_alias_map_text(
    spec: "ConfigSpec", raw: str, notes: list[str] | None = None
) -> dict[str, str]:
    """``名=展示名;名=展示名``；空串表示清空整表。"""
    text = (raw or "").strip()
    if not text:
        return {}
    out: dict[str, str] = {}
    for chunk in re.split(r"[;；]", text):
        piece = chunk.strip()
        if not piece:
            continue
        if "=" not in piece and "＝" not in piece:
            raise ValueError(
                f"{spec.key} 的每项要写成 名=展示名"
                f"（如 qwen3.8-max=通义千问 3.8 Max），这一项是 {piece!r}"
            )
        name, label = re.split(r"[=＝]", piece, maxsplit=1)
        name, label = name.strip(), label.strip()
        if not name:
            raise ValueError(f"{spec.key} 的别名不能为空：{piece!r}")
        if name in out and notes is not None:
            notes.append(f"{name} 重复出现，以后一个为准（{out[name]} → {label}）")
        out[name] = label
    return out


def _parse_model_table_text(spec: "ConfigSpec", raw: str) -> dict[str, Any]:
    """``add 名字 键=值 键=值`` 或 ``remove 名字``。"""
    parts = (raw or "").split()
    if not parts:
        raise ValueError(f"{spec.key} 要写成 add 名字 键=值（只加一项）或 remove 名字")
    op = parts[0].lower()
    if op not in MODEL_TABLE_OPS:
        raise ValueError(f"{spec.key} 的操作只能是 add / remove（收到 {parts[0]!r}）")
    if len(parts) < 2:
        raise ValueError(f"{spec.key} 的 {op} 需要模型名")
    fields: dict[str, str] = {}
    for token in parts[2:]:
        if "=" not in token and "＝" not in token:
            raise ValueError(f"{spec.key} 的字段要写成 键=值（这一项是 {token!r}）")
        k, v = re.split(r"[=＝]", token, maxsplit=1)
        fields[k.strip()] = v.strip()
    return {"op": op, "name": parts[1], "fields": fields}
