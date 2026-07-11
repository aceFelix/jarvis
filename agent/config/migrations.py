"""配置 migrations —— schema 升级时自动迁移。

v0.1 实现:
1. 每个迁移是一个函数 (data: dict) -> dict，原地修改并返回 TOML dict
2. 在 ~/.jarvis/.migrations 记录已执行的迁移 ID
3. 启动时按顺序跑未执行的迁移
4. 迁移失败不阻塞启动，记录到诊断日志

迁移场景示例:
- v0.1 → v0.2: settings.toml 字段重命名（tts_model → voice_tts_model）
- v0.2 → v0.3: 默认模型从 qwen-max 改为 qwen-max-latest
- 添加新字段并希望给老用户一个非默认值
- 删除废弃字段（保留兼容但有警告）

注意: 现阶段 Jarvis 配置 schema 还在演进，这里先建好框架，初始迁移为空。
当以后改动 schema 时，新增迁移函数 + 注册到 MIGRATIONS 列表即可。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Any

from agent.core.diag import diag_log, diag_warn

# 迁移函数类型: 接收原 TOML dict，返回迁移后的 dict
MigrationFunc = Callable[[dict[str, Any]], dict[str, Any]]

# 迁移记录文件: ~/.jarvis/.migrations
# 每行一个已执行的迁移 ID
def _migrations_record_path() -> Path:
    return Path.home() / ".jarvis" / ".migrations"


def _read_executed() -> set[str]:
    """读取已执行的迁移 ID 集合。"""
    path = _migrations_record_path()
    if not path.exists():
        return set()
    try:
        return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    except Exception:
        return set()


def _mark_executed(migration_id: str) -> None:
    """标记一个迁移已执行。"""
    path = _migrations_record_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(migration_id + "\n")
    except Exception as e:
        diag_warn("migrations", f"无法写入迁移记录: {e}")


# ---- 迁移函数注册表 ----
# 每条迁移: (id, description, func)
# id 命名约定: YYYYMMDD_short_name（如 20260704_rename_tts_fields）
# 新增迁移时追加到列表末尾，保持顺序执行

MIGRATIONS: list[tuple[str, str, MigrationFunc]] = []


def register_migration(migration_id: str, description: str) -> Callable[[MigrationFunc], MigrationFunc]:
    """装饰器：注册一个迁移函数。

    用法::

        @register_migration("20260704_rename_tts_fields", "TTS 字段重命名")
        def rename_tts(data: dict) -> dict:
            if "tts" in data and "model" in data["tts"]:
                data["tts"]["tts_model"] = data["tts"].pop("model")
            return data
    """
    def decorator(func: MigrationFunc) -> MigrationFunc:
        MIGRATIONS.append((migration_id, description, func))
        return func
    return decorator


def run_migrations(config_path: Path | None = None) -> tuple[int, list[str]]:
    """对所有未执行的迁移按顺序执行。

    Args:
        config_path: 要迁移的 settings.toml 路径。
            None 表示仅 dry-run（不写文件），返回待执行的迁移列表。

    Returns:
        (executed_count, failed_ids)
    """
    executed = _read_executed()
    pending = [(mid, desc, func) for mid, desc, func in MIGRATIONS if mid not in executed]

    if not pending:
        return 0, []

    # 读取当前配置
    data: dict[str, Any] = {}
    if config_path and config_path.exists():
        try:
            if sys.version_info >= (3, 11):
                import tomllib
            else:
                import tomli as tomllib
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        except Exception as e:
            diag_warn("migrations", f"读取配置失败，跳过迁移: {config_path}: {e}")
            return 0, [mid for mid, _, _ in pending]

    failed: list[str] = []
    executed_count = 0

    for mid, desc, func in pending:
        try:
            data = func(data) or data
            _mark_executed(mid)
            executed_count += 1
            diag_log("migrations", f"已执行迁移 {mid}: {desc}")
        except Exception as e:
            failed.append(mid)
            diag_warn("migrations", f"迁移 {mid} 失败: {type(e).__name__}: {e}")

    # 写回配置文件
    if config_path and executed_count > 0 and failed.__len__() == 0:
        try:
            _write_toml(config_path, data)
        except Exception as e:
            diag_warn("migrations", f"写回配置失败: {e}")

    return executed_count, failed


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    """把 dict 写回 TOML 文件。

    使用纯文本拼接而非 tomli_w，避免新增依赖。
    仅支持 Jarvis settings.toml 用到的简单结构（顶层标量 + 一层嵌套表）。
    """
    lines: list[str] = []

    def _format_value(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            # 转义双引号和反斜杠
            escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        if isinstance(v, list):
            return "[" + ", ".join(_format_value(x) for x in v) + "]"
        return f'"{v}"'

    def _is_table(v: Any) -> bool:
        return isinstance(v, dict)

    # 先写顶层标量字段
    for k, v in data.items():
        if not _is_table(v):
            lines.append(f"{k} = {_format_value(v)}")

    # 再写嵌套表
    for k, v in data.items():
        if _is_table(v):
            lines.append("")
            lines.append(f"[{k}]")
            for sub_k, sub_v in v.items():
                if not _is_table(sub_v):
                    lines.append(f"{sub_k} = {_format_value(sub_v)}")
            # 二层嵌套（如 [llm.custom_models."xxx]）
            for sub_k, sub_v in v.items():
                if _is_table(sub_v):
                    lines.append("")
                    lines.append(f"[{k}.{sub_k}]")
                    for sub2_k, sub2_v in sub_v.items():
                        if not _is_table(sub2_v):
                            lines.append(f"{sub2_k} = {_format_value(sub2_v)}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def list_pending_migrations() -> list[tuple[str, str]]:
    """列出待执行的迁移（供 /doctor 命令展示）。"""
    executed = _read_executed()
    return [(mid, desc) for mid, desc, _ in MIGRATIONS if mid not in executed]


def list_all_migrations() -> list[tuple[str, str, bool]]:
    """列出所有迁移及其执行状态。返回 (id, desc, executed) 元组列表。"""
    executed = _read_executed()
    return [(mid, desc, mid in executed) for mid, desc, _ in MIGRATIONS]
