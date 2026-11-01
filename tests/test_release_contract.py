"""发布 / 打包契约测试 —— 兜住"改了三处漏一处""规则库漏打包""新增字段没进目录"这类交付事故。

为什么要这一层（aceFelix，2026-11-01）：
- **版本号**：`publish.yml` 只校验 pyproject↔npm 两处，`agent/__init__.py` 的 `__version__`
  不在 CI 校验范围内（见该行注释原文"本行不在 CI 校验范围内，发布时必须一并手改"），
  全靠人肉——正是最容易漏的第三处，漏了装出来的 `jarvis --version` 就对不上 PyPI。
- **随包规则库**：`cleaners.toml` 若漏进 package-data，装出来的包运行 `/disk` 才报
  "规则文件不存在"——单测在源码树里跑永远发现不了。
- **配置目录覆盖**：新增 `Settings` 字段忘了进配置目录，"202 项全能问"的承诺就出现空洞，
  用户问了得不到回答。

本文件把这些**发版前必须成立、但当前无人/无测把关**的不变量钉死：纯本地、无网络、无端口、
不依赖可选 extras，因此在 CI 的 Python 3.11–3.14 矩阵上都能稳定跑。

@author aceFelix
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---- 版本号一致性（覆盖 publish.yml 不管的第三处） ----

def _pyproject_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _npm_version() -> str:
    pkg = json.loads((REPO_ROOT / "npm" / "package.json").read_text(encoding="utf-8"))
    return str(pkg["version"])


def test_version_consistent_across_release_files() -> None:
    """三处版本号必须一致：pyproject == npm/package.json == agent.__version__。

    前两处由 publish.yml 校验，本测试专门补上它没管的 `agent.__version__`。
    """
    import agent

    pv, nv, av = _pyproject_version(), _npm_version(), agent.__version__
    assert pv == nv == av, f"版本号漂移: pyproject={pv} npm={nv} agent.__version__={av}"


# ---- 配置目录必须覆盖每个 Settings 字段 ----

def test_catalog_covers_every_settings_field() -> None:
    """Settings 每字段都应在配置目录里有一条（新增字段没写说明就该红）。

    这是"全部配置项都能问答"承诺的机制守卫：字段存在、目录里却没有 = 用户问了答不上。
    只校验"字段 → 目录"方向（新字段必须被覆盖），容忍目录里额外的派生条目。
    """
    from agent.config.catalog import build_catalog
    from agent.config.settings import Settings

    field_names = {f.name for f in dataclasses.fields(Settings)}
    catalog_attrs = {e.attr for e in build_catalog()}
    missing = field_names - catalog_attrs
    assert not missing, f"以下 Settings 字段未进配置目录: {sorted(missing)}"


# ---- 运行时要读的声明式文件必须存在，且被打包规则覆盖 ----

def test_shipped_runtime_files_exist() -> None:
    """随包运行的声明式文件必须真实存在（否则装出来的包运行期才炸）。"""
    from agent.core.maintenance.rules import DEFAULT_RULES_PATH

    assert DEFAULT_RULES_PATH.is_file(), f"内置清理规则库缺失: {DEFAULT_RULES_PATH}"
    for name in ("models.example.toml", "settings.example.toml", "permissions.yaml"):
        assert (REPO_ROOT / "agent" / "configs" / name).is_file(), f"打包模板缺失: configs/{name}"


def test_package_data_ships_runtime_files() -> None:
    """pyproject 的 package-data 必须覆盖运行时必读文件（漏了装出来的包就找不到）。

    路径相对 `agent` 包；用 fnmatch 逐条比对模式，任一模式命中即视为被打包。
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = list(data["tool"]["setuptools"]["package-data"]["agent"])
    needed = [
        "config/cleaners.toml",
        "configs/models.example.toml",
        "configs/settings.example.toml",
        "configs/permissions.yaml",
    ]
    for rel in needed:
        assert any(fnmatch(rel, pat) for pat in patterns), (
            f"{rel} 未被 package-data 任一模式匹配（装出来的包会缺它）: {patterns}"
        )


# ---- 内置规则库不能"某条静默解析失败"或被清空 ----

def test_builtin_rulebook_parses_clean() -> None:
    """内置 cleaners.toml 应能整表加载、无一条被静默拒绝（规则数只设下限不设死值）。

    静默丢规则是最糟的失败模式：用户以为有条规则在跑，其实早被跳过。
    断言 ``rejected`` 为空即可同时守住"规则库被改坏/改空"和"某条写法不合法"。
    """
    from agent.core.maintenance.rules import load_rulebook

    book = load_rulebook()
    assert not book.rejected, f"内置规则库存在被拒绝的规则: {book.rejected}"
    assert len(book.rules) >= 15, f"内置规则库条数异常偏低（疑似被清空）: {len(book.rules)}"
