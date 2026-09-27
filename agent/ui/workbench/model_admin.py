"""工作台 / 桌面壳的模型配置管理（自 api.py 拆出，聚焦职责并控文件行数）。

覆盖左栏模型面板的三件事：

- ``list_models``：可选模型列表（内置 ``[llm.models]`` + 自定义
  ``[llm.custom_models]``），每项附管理元信息（source / removable / config），
  供桌面壳「双击改配置、右键删除」就地操作；
- ``add_model`` / ``edit_model``：写用户级 models.toml 的
  ``[llm.custom_models."<name>"]`` 并同步内存 ``settings.custom_models``
  （字段与校验口径对齐 REPL /models → 添加其他模型 / 修改配置）；
- ``remove_model``：删除自定义模型（内置模型不可删，对齐 REPL 的
  ``_pick_model_action(allow_delete=<是否自定义>)``）。

与 REPL 的一处刻意差异（桌面壳不回显密钥）：编辑表单不预填 api_key，
``api_key`` 留空表示「保持原 Key 不变」；REPL 表单预填明文，留空即清空。
明文密钥不回传前端（只回 ``has_key`` 布尔），避免密钥在 WS/渲染进程扩散。

@author aceFelix
"""

from __future__ import annotations

from typing import Any, Callable

from agent.config.settings import Settings

# 接口类型 / 模型类型枚举（与 serve `_rpc_models_add` 白名单、REPL 表单选项一致）
VALID_API_FORMATS: tuple[str, ...] = ("openai", "anthropic", "dashscope", "zai")
VALID_MODEL_TYPES: tuple[str, ...] = ("text", "multimodal")


def _infer_model_vendor(name: str, cfg: dict | None) -> str:
    """厂商推断（复用 /models 口径；导入失败降级空串，不影响列表可用）。"""
    try:
        from agent.model_manager import _infer_model_vendor as infer
    except Exception:
        return ""
    try:
        return infer(name, cfg)
    except Exception:
        return ""


def _infer_base_url(vendor: str, api_format: str) -> str:
    """按厂商 + 接口类型推断 base_url（与 REPL 添加流程同一实现）。"""
    try:
        from agent.model_manager import _infer_base_url as infer
    except Exception:
        return ""
    try:
        return infer(vendor, api_format)
    except Exception:
        return ""


def _custom_cfg(settings: Settings, name: str) -> dict:
    """取某模型的自定义配置（无覆盖配置/类型不符时返回空 dict）。"""
    cfg = (getattr(settings, "custom_models", None) or {}).get(name)
    return cfg if isinstance(cfg, dict) else {}


def _model_config(
    name: str,
    *,
    vendor: str,
    api_format: str,
    base_url: str,
    api_key: str,
    model_type: str,
) -> dict:
    """组装写入 models.toml 的模型配置（字段与 REPL 添加/编辑完全一致）。"""
    return {
        "name": name,
        "provider": vendor,  # 模型提供商（vendor），如 deepseek
        "api_format": api_format,  # API 协议，如 openai / anthropic
        "base_url": base_url,
        "api_key": api_key,
        "model_type": model_type,
        # 保留旧字段名以兼容旧代码读取（与 /models 添加口径一致）
        "vendor": vendor,
        "provider_type": api_format,
    }


def _edit_defaults(settings: Settings, name: str, cfg: dict) -> dict[str, str]:
    """编辑表单默认值：自定义覆盖配置 > 内置/全局端点。

    内置模型未加过覆盖配置时，端点取自 ``default_*``（last_model 覆盖前的原始
    值），否则会把自定义模型的端点当成内置默认值填进去。
    """
    vendor = (
        cfg.get("provider")
        or cfg.get("vendor")
        or settings.default_provider
        or settings.provider
        or "deepseek"
    )
    api_format = (
        cfg.get("api_format")
        or cfg.get("provider_type")
        or settings.default_api_format
        or settings.api_format
        or "openai"
    )
    base_url = cfg.get("base_url") or settings.default_base_url or settings.base_url or ""
    # DashScope / 智谱 ZhipuAi SDK 模式 endpoint 由 SDK 自管，留空更贴近实际
    if api_format in ("dashscope", "zai"):
        base_url = ""
    return {
        "vendor": str(vendor),
        "api_format": str(api_format),
        "base_url": str(base_url),
        "model_type": str(cfg.get("model_type") or "multimodal"),
        "has_key": bool(cfg.get("api_key") or settings.api_key),
    }


def list_models(settings: Settings, engine: Any) -> list[dict[str, Any]]:
    """可选模型列表：内置模型（[llm.models]）+ 自定义模型（[llm.custom_models]）。

    当前模型置顶并标 current；厂商经 model_manager._infer_model_vendor 推断，
    内置名 + 自定义覆盖合并去重（同 /models 的 builtin/custom 合并规则）。
    current 取引擎正在跑的模型（热切换即时生效），而非启动配置快照；会话未
    装配时引擎返回待生效的切换目标。

    每项附带管理元信息（桌面壳模型面板的就地改/删依据）：
    - ``source``：builtin（[llm.models] 内置）/ custom（用户添加）；
    - ``editable``：是否可改配置（两类都可，内置模型改的是用户级覆盖配置）；
    - ``removable``：是否可删（仅自定义模型，内置模型不可删）；
    - ``config``：编辑表单默认值（含 has_key 布尔，明文密钥不回传）。

    @author aceFelix
    """
    current = str(getattr(engine, "current_model", "") or "")
    models_map = settings.models or {}
    custom_map = settings.custom_models or {}
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(name: str, desc: str = "") -> None:
        cfg = _custom_cfg(settings, name)
        items.append({
            "name": name,
            "vendor": _infer_model_vendor(name, cfg or None),
            "current": name == current,
            "desc": desc,
            "source": "builtin" if name in models_map else "custom",
            "editable": True,
            "removable": bool(cfg) and name not in models_map,
            "config": _edit_defaults(settings, name, cfg),
        })
        seen.add(name)

    # 当前模型置顶（即使它不在两张表里，也保证可见可标）
    if current:
        desc = models_map.get(current, "")
        _append(current, desc if isinstance(desc, str) else "")

    # 内置模型表（项目级 [llm.models]，含用户级覆盖合并后的结果）
    for name, desc in models_map.items():
        if name in seen:
            continue
        _append(name, desc if isinstance(desc, str) else "")

    # 自定义模型（/models 添加的，非内置的）
    for name, cfg in custom_map.items():
        if name in seen or not isinstance(cfg, dict):
            continue
        _append(name)
    return items


def add_model(
    settings: Settings,
    name: str,
    *,
    vendor: str = "deepseek",
    api_format: str = "openai",
    base_url: str = "",
    api_key: str = "",
    model_type: str = "text",
) -> dict[str, Any]:
    """添加（或覆盖）自定义模型：写用户级 models.toml 并即时更新内存配置。

    字段与持久化口径与 REPL /models → 添加其他模型 完全一致
    （agent/model_manager.py::_add_custom_model_flow）：base_url 留空时按
    厂商 + 接口类型推断；api_key 非空时由 upsert 同步系统 keyring。
    写盘成功后同步 ``settings.custom_models``，让 list_models 立即可见新模型
    （引擎解析与列表读同一份 settings 快照）；写盘失败抛错，由 serve 回
    ok=false（前端 runCommand 统一弹错误，不再静默）。

    @author aceFelix
    """
    from agent.config.model_registry import save_custom_model

    vendor = vendor or "deepseek"
    base_url = base_url or _infer_base_url(vendor, api_format)
    config = _model_config(
        name,
        vendor=vendor,
        api_format=api_format,
        base_url=base_url,
        api_key=api_key,
        model_type=model_type,
    )
    if not save_custom_model(name, config):
        raise RuntimeError("写盘失败：无法写入 ~/.jarvis/models.toml")
    settings.custom_models[name] = config
    return {
        "name": name,
        "vendor": vendor,
        "api_format": api_format,
        "base_url": base_url,
        "model_type": model_type,
    }


def edit_model(
    settings: Settings,
    engine: Any,
    post: Callable[[dict], None] | None,
    name: str,
    *,
    new_name: str = "",
    vendor: str = "",
    api_format: str = "",
    base_url: str = "",
    api_key: str = "",
    model_type: str = "",
) -> dict[str, Any]:
    """修改模型配置（桌面壳左栏双击模型项 → 编辑表单 → models.edit）。

    语义（对齐 REPL「修改配置」）：
    - 内置模型（[llm.models]）名固定不可改，写入的是用户级覆盖配置；
    - 自定义模型可改名 —— 改名 = 删旧段 + 写新段（同 _edit_custom_model）；
    - base_url 留空 → 按厂商 + 接口类型推断（同添加口径）；
    - api_key 留空 → **保持原 Key 不变**（桌面壳不回显密钥，不能把「未填」
      当作「清空」，否则一次改名就会把密钥抹掉）；
    - 改的是当前运行模型时，入队 ``switch_model``（带 force）：引擎强制按新
      配置重建 provider，端点/模型类型的改动立即生效，不必重启。

    Returns:
        ``{name, vendor, api_format, base_url, model_type, hot_switched}``。

    @author aceFelix
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("缺少模型名 name")
    models_map = settings.models or {}
    cfg = _custom_cfg(settings, name)
    builtin = name in models_map
    if not builtin and not cfg:
        raise ValueError(f"模型不存在，无法修改: {name}")

    target = (new_name or name).strip()
    if not target:
        raise ValueError("模型名不能为空")
    if builtin and target != name:
        raise ValueError("内置模型名不可修改（需要新名字请用「添加模型」）")
    if target != name and target in (settings.custom_models or {}):
        raise ValueError(f"模型名已被占用: {target}")

    defaults = _edit_defaults(settings, name, cfg)
    vendor = (vendor or defaults["vendor"]).strip() or defaults["vendor"]
    api_format = (api_format or defaults["api_format"]).strip() or defaults["api_format"]
    if api_format not in VALID_API_FORMATS:
        raise ValueError(f"不支持的接口类型 api_format: {api_format}")
    model_type = (model_type or defaults["model_type"]).strip() or defaults["model_type"]
    if model_type not in VALID_MODEL_TYPES:
        raise ValueError(f"不支持的模型类型 model_type: {model_type}")
    base_url = (base_url or "").strip() or _infer_base_url(vendor, api_format)
    # 留空 = 保持原 Key（不回显密钥 → 空值不能解释为清空）
    api_key = (api_key or "").strip() or str(cfg.get("api_key", "") or "")

    config = _model_config(
        target,
        vendor=vendor,
        api_format=api_format,
        base_url=base_url,
        api_key=api_key,
        model_type=model_type,
    )
    from agent.config.model_registry import save_custom_model

    renamed = target != name
    if renamed:
        # 改名 = 删旧段再写新段（否则旧名会成为「幽灵模型」留在列表里）
        from agent.config.models_config import remove_custom_model

        remove_custom_model(name)
    if not save_custom_model(target, config):
        raise RuntimeError("写盘失败：无法写入 ~/.jarvis/models.toml")
    custom = settings.custom_models
    if renamed:
        custom.pop(name, None)
    custom[target] = config

    hot = _is_current(engine, name) or _is_current(engine, target)
    if hot and post is not None:
        # 强制重建：改的正是运行中的模型，端点/类型可能已变；force 让引擎
        # 跳过「同名即当前」短路，按新配置重新构造 provider。@author aceFelix
        post({"cmd": "switch_model", "name": target, "force": True})
    return {
        "name": target,
        "vendor": vendor,
        "api_format": api_format,
        "base_url": base_url,
        "model_type": model_type,
        "hot_switched": bool(hot),
    }


def remove_model(settings: Settings, engine: Any, name: str) -> dict[str, Any]:
    """删除自定义模型（桌面壳左栏右键模型项 → 删除按钮 → models.remove）。

    内置模型（[llm.models]）不可删：它们来自项目级配置，删掉用户级覆盖段也
    只是回退到内置默认端点，列表里仍会在（对齐 REPL 的 allow_delete 口径）。
    用户级 models.toml 里不存在该段时同样拒绝（配置来自项目级，删了会在
    重启后「复活」，属口径分裂）。删除**不动**运行中的 provider：若删的是
    当前模型，引擎仍按已加载配置继续工作，由调用方提示用户另选模型。

    Returns:
        ``{name, was_current}``：was_current 供前端提示「当前仍在用它」。

    @author aceFelix
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("缺少模型名 name")
    if name in (settings.models or {}):
        raise ValueError(f"内置模型不可删除，仅可修改配置: {name}")
    if name not in (settings.custom_models or {}):
        raise ValueError(f"模型不存在或不是自定义模型: {name}")
    from agent.config.models_config import remove_custom_model

    if not remove_custom_model(name):
        raise RuntimeError(
            f"删除失败：用户级 models.toml 中未找到 {name}（项目级配置的模型或磁盘写入失败）"
        )
    settings.custom_models.pop(name, None)
    return {"name": name, "was_current": _is_current(engine, name)}


def _is_current(engine: Any, name: str) -> bool:
    """引擎当前模型是否就是 name（engine 为 None/未装配时按名义模型判断）。"""
    if engine is None or not name:
        return False
    return str(getattr(engine, "current_model", "") or "") == name
