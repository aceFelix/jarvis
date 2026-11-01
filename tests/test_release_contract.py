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
import os
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# 内置清理规则库承载的路径全是 Windows 语义（%LOCALAPPDATA% / 盘符 / 反斜杠）。POSIX
# 上没有这些环境变量，expand_path 会原样保留 %VAR% 占位符，继而 fail-closed 闸判为
# 「展开后不是绝对路径」把 paths 型规则整条拒掉——那是**正确行为**（维护层本就仅
# Windows），不是缺陷。但「如实拒绝」不等于「随便拒绝」，所以这里不用 skip 把整条测试
# 关掉（那样会把与平台无关的三道校验闸一起丢掉），而是让它在 POSIX 上断言「拒绝恰好
# 等于平台性拒绝的集合」。（aceFelix，2026-11-02）
#
# 分工：Windows 上的「零静默拒绝 + 关键 id 名单」已由
# tests/core/test_cleaner_rules.py::test_load_builtin_rulebook_has_all_rules 把关，本文件
# **不重复**它；这里只补它管不到的那一半——Linux CI 上规则库到底被怎么处理。
_IS_WINDOWS = os.name == "nt"

#: POSIX 上唯一允许出现的拒绝原因（来源 rules.py「展开后不是绝对路径」那道校验）。新增
#: 合法的平台性拒绝原因时在此登记，别让它们被当成「规则库写坏了」而误红。
_PLATFORM_REJECTION_MARKERS = ("不是绝对路径",)


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


# ---- 内置规则库：POSIX 上的拒绝必须严格等于「平台性拒绝」----

_ON_POSIX = pytest.mark.skipif(_IS_WINDOWS, reason="POSIX 上才有平台性拒绝")


def _declared_rules() -> dict[str, str]:
    """读内置 cleaners.toml 的原始声明，返回 ``{规则 id: kind}``。

    校验闸会把写坏的条目拒掉，所以「文件里声明了什么」和「加载出什么」必须逐条对得上，
    才谈得上「没有静默丢规则」。
    """
    from agent.core.maintenance.rules import DEFAULT_RULES_PATH

    data = tomllib.loads(DEFAULT_RULES_PATH.read_text(encoding="utf-8"))
    return {
        str(raw.get("id") or "<无 id>").strip(): str(raw.get("kind") or "paths").strip()
        for raw in data["cleaner"]
    }


def _split_book(book) -> tuple[dict[str, str], dict[str, str]]:
    """把一次加载结果拆成 (已加载 id→kind, 被拒 id→原因)。"""
    loaded = {r.id: r.kind for r in book.rules}
    rejected = dict(book.rejected)
    return loaded, rejected


@_ON_POSIX
def test_builtin_rulebook_rejects_only_platform_paths() -> None:
    """POSIX：paths 型规则因 Windows 语义**如实**被拒，command 型仍全部通过校验。

    这条把 README 平台支持矩阵里「系统维护层仅 Windows（POSIX 如实拒绝）」钉成可执行
    证据，同时保住与平台无关的三道校验闸（见模块头注释）——规则库被删空、字段非法、
    命令不在白名单，在 Linux 上照样红。
    """
    from agent.core.maintenance.rules import load_rulebook

    declared = _declared_rules()
    declared_paths = {rid for rid, kind in declared.items() if kind == "paths"}
    declared_commands = {rid for rid, kind in declared.items() if kind == "command"}
    loaded, rejected = _split_book(load_rulebook())

    # 1. 拒绝集合必须「恰好」是 paths 型：少了说明规则被平台悄悄吃进 POSIX（规则库全是
    #    Windows 语义，任何一条 paths 都不该在 POSIX 上展开成功），多了说明有非法规则混入。
    assert set(rejected) == declared_paths, (
        f"POSIX 上被拒的应恰为全部 paths 型规则：多拒 {sorted(set(rejected) - declared_paths)}，"
        f"漏拒 {sorted(declared_paths - set(rejected))}"
    )
    # 2. 拒绝原因只许是平台性的；出现别的写法问题就是真缺陷，必须红。
    for rid, reason in sorted(rejected.items()):
        assert any(mk in reason for mk in _PLATFORM_REJECTION_MARKERS), (
            f"{rid} 的拒绝原因不是平台性的，属规则库写法缺陷: {reason}"
        )
    # 3. 与平台无关的校验闸必须照旧生效：命令白名单、risk/kind、auto_ok 互斥都在
    #    构建 CleanerRule 之前跑完，command 型一条都不许被拒。
    assert {rid for rid, kind in loaded.items() if kind == "command"} == declared_commands, (
        f"command 型规则在 POSIX 上与平台无关，应全部通过校验；实际 loaded="
        f"{sorted(loaded)} rejected={sorted(rejected)}"
    )
    # 4. 已加载的条不能错挂 kind，也不能有声明外的条目。
    for rid, kind in loaded.items():
        assert declared.get(rid) == kind, f"{rid} 加载后的 kind={kind!r} 与声明不符"
    assert not set(loaded) - set(declared), f"出现声明外的规则 id: {sorted(set(loaded) - set(declared))}"
    # 5. 声明里的每条都必须在 loaded 或 rejected 中出现——静默丢弃即红。
    assert set(loaded) | set(rejected) == set(declared), (
        f"存在被静默丢弃、两边都不在的规则: "
        f"{sorted(set(declared) - set(loaded) - set(rejected))}"
    )
