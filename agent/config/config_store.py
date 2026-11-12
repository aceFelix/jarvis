"""按 `store` 分派落盘：settings.toml 还是 models.toml。

## 为什么需要这一层

模型域（provider / model / max_tokens / temperature / …）住在**同层的 models.toml**，
由 `models_config` 管；其余配置在 settings.toml，走 `save_section`。`ConfigSpec.store`
声明了归属，落盘必须照它分派——否则模型字段会被写进 settings.toml 的某处，
读的人却在 models.toml，表现为"改了没反应"。

## 已知边界（2026-11）

`custom_models`（表结构）暂未接线：`models_config.upsert_custom_model/remove_custom_model`
没有 path 参数，只会写用户级文件，测试无法注入临时路径。要接这一路，先给那两个函数
加 `path` 形参（S2c）。在那之前，写入会明确失败并说明，不静默写错地方。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.config import models_config
from agent.config.config_specs import (
    STORE_MODELS,
    ConfigSpec,
    save_value,
    toml_literal,
    validate_value,
)


def _models_path(settings_path: Path | str | None) -> Path:
    """models.toml 与 settings.toml 同层；未给路径时用用户级。"""
    if settings_path is None:
        return models_config.user_models_path()
    return models_config.models_path_for(Path(settings_path))


def write_spec_value(
    spec: ConfigSpec, value: Any, *, settings_path: Path | str | None = None
) -> bool:
    """按 spec.store 落盘一个配置项；失败返回 False（调用方据此回执）。"""
    if spec.store != STORE_MODELS:
        return save_value(spec, value, path=settings_path)

    if spec.kind == "model_table":
        return False  # 见模块文档：upsert_custom_model 还没有 path 形参

    path = _models_path(settings_path)
    try:
        content, eol = models_config.read_toml_text(path)
    except FileNotFoundError:
        content, eol = "", "\n"

    if spec.kind == "alias_map":
        body = [f'"{k}" = {_quote(v)}' for k, v in (value or {}).items()]
        new = models_config.upsert_table_block(content, f"[{spec.section}]", body)
    else:
        new = models_config.upsert_top_scalar(content, spec.field, toml_literal(spec, value))

    models_config.write_toml_text(path, new, eol)
    return True


def _quote(text: Any) -> str:
    """TOML 字符串字面量（转义反斜杠与引号）。"""
    raw = str(text or "")
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def inverse_value(spec: ConfigSpec, old: Any, new: Any) -> Any:
    """把一次写入折算成"能撤销它的那次写入"。

    `model_table` 是增删操作，不能靠写回旧值来实现撤销——旧值是整张表的快照。
    所以这里按操作反推：add 的反面是 remove；remove 的反面是 add 回原字段。
    其它 kind 直接返回旧值（写回即撤销）。
    """
    if spec.kind != "model_table":
        return old
    data = new or {}
    op = str(data.get("op") or "")
    name = str(data.get("name") or "")
    if op == "add":
        return {"op": "remove", "name": name, "fields": {}}
    # remove：把被删的那条原样加回去
    prev = (old or {}).get(name) or {}
    return {"op": "add", "name": name, "fields": {str(k): str(v) for k, v in prev.items()}}


def coerce_for_write(spec: ConfigSpec, value: Any) -> Any:
    """落盘前再校验一遍（撤销路径也走这里，避免旧值绕过校验）。"""
    return validate_value(spec, value)
