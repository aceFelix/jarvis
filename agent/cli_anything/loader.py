"""CLI-Anything harness 加载器。

扫描 ``~/.jarvis/cli_anything/`` 与 ``<workdir>/.jarvis/cli_anything/`` 目录，
解析每个 harness 的 SKILL.md，把 frontmatter + Markdown 正文转换为 ``Harness`` 对象。

解析失败时跳过单个 harness，不影响其他 harness 的加载。

@author aceFelix
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from agent.cli_anything.schema import Harness, HarnessArg

logger = logging.getLogger(__name__)

# 用户级 harness 根目录
_DEFAULT_USER_HARNESS_DIR = Path.home() / ".jarvis" / "cli_anything"

# 兼容旧目录 ~/.my-agent/cli_anything
_FALLBACK_USER_HARNESS_DIR = Path.home() / ".my-agent" / "cli_anything"

# 项目级 harness 子目录名
_PROJECT_HARNESS_SUBDIR = Path(".jarvis") / "cli_anything"

# SKILL.md 文件名
_SKILL_FILE = "SKILL.md"

# frontmatter 分隔线
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _user_harness_root_dirs() -> list[Path]:
    """返回可能的用户级 harness 根目录列表（按优先级）。"""
    roots: list[Path] = []
    if _DEFAULT_USER_HARNESS_DIR.exists():
        roots.append(_DEFAULT_USER_HARNESS_DIR)
    if _FALLBACK_USER_HARNESS_DIR.exists():
        roots.append(_FALLBACK_USER_HARNESS_DIR)
    return roots


def _project_harness_root_dir(workdir: Path | str | None = None) -> Path | None:
    """返回项目级 harness 根目录（如果 workdir 有效）。"""
    if workdir is None:
        return None
    wd = Path(workdir)
    if not wd.is_dir():
        return None
    return wd / _PROJECT_HARNESS_SUBDIR


def discover_harnesses(
    root_dir: Path | str | None = None,
    workdir: Path | str | None = None,
) -> list[Harness]:
    """扫描 harness 目录并解析所有 SKILL.md。

    Args:
        root_dir: 指定扫描根目录。为 None 时扫描默认用户目录。
        workdir: 当前工作目录，用于扫描项目级 harness ``<workdir>/.jarvis/cli_anything/``。

    Returns:
        解析成功的 Harness 列表。用户级与项目级 harness 都会加载，
        后者可覆盖前者（相同 id 时后者生效）。
    """
    roots: list[Path] = []
    if root_dir is not None:
        roots.append(Path(root_dir))
    else:
        roots.extend(_user_harness_root_dirs())
        project_root = _project_harness_root_dir(workdir)
        if project_root is not None and project_root.exists():
            roots.append(project_root)

    results: list[Harness] = []
    seen_ids: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for subdir in sorted(root.iterdir()):
            if not subdir.is_dir():
                continue
            skill_path = subdir / _SKILL_FILE
            if not skill_path.is_file():
                continue
            try:
                harness = parse_skill_md(skill_path)
                harness.dir_path = subdir
                # 项目级 harness 覆盖用户级同名 harness
                if harness.id in seen_ids:
                    results = [h for h in results if h.id != harness.id]
                results.append(harness)
                seen_ids.add(harness.id)
            except Exception as e:
                logger.warning("解析 harness 失败 %s: %s", skill_path, e)
    return results


def parse_skill_md(path: Path) -> Harness:
    """解析单个 SKILL.md 文件。

    Args:
        path: SKILL.md 文件路径。

    Returns:
        解析后的 Harness 对象。

    Raises:
        ValueError: frontmatter 缺失或必要字段不完整。
    """
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("SKILL.md 缺少 frontmatter（--- ... ---）")

    front_text = match.group(1)
    try:
        meta: dict[str, Any] = yaml.safe_load(front_text) or {}
    except Exception as e:
        raise ValueError(f"frontmatter YAML 解析失败: {e}") from e

    required = ("id", "name", "description", "command")
    missing = [k for k in required if not meta.get(k)]
    if missing:
        raise ValueError(f"frontmatter 缺少必要字段: {missing}")

    args: list[HarnessArg] = []
    for raw in meta.get("args", []):
        if not isinstance(raw, dict):
            continue
        args.append(
            HarnessArg(
                name=str(raw.get("name", "")),
                type=str(raw.get("type", "string")),
                description=str(raw.get("description", "")),
                required=bool(raw.get("required", False)),
                enum=raw.get("enum"),
                default=raw.get("default"),
                positional=bool(raw.get("positional", False)),
            )
        )

    return Harness(
        id=str(meta["id"]).strip(),
        name=str(meta["name"]).strip(),
        description=str(meta["description"]).strip(),
        command=str(meta["command"]).strip(),
        args=args,
        when_to_use=str(meta.get("when_to_use", "")),
        trigger_words=_as_str_list(meta.get("trigger_words", [])),
        examples=_as_str_list(meta.get("examples", [])),
        dir_path=path.parent,
    )


def _as_str_list(value: Any) -> list[str]:
    """把任意值转成字符串列表。"""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]
