"""默认工作区解析（agent/config/workspace.py）回归测试。

锁定（2026-11-20 实机）：
- 从 ``C:\\Program Files`` 启动 → 项目会话记忆 ``makedirs(workdir/.jarvis)``
  直接 PermissionError 打崩启动；
- 从 ``C:\\Windows\\System32`` 启动 → 检查点 ``git add -A`` 遍历数万文件，
  首轮卡死；
- 从用户主目录根启动 → 把整个 home（桌面/下载/文档）当成一个项目。

新口径三档：显式指定 > 安全 cwd > 默认工作区 ``~/.jarvis/workspace``。

@author aceFelix
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.config import workspace as W


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """把 Path.home() 钉到临时目录，绝不碰真实 ~/.jarvis。"""
    h = tmp_path / "home"
    (h / ".jarvis").mkdir(parents=True)
    monkeypatch.setattr(W.Path, "home", lambda: h)
    W._WORKDIR_NOTE = ""
    yield h
    W._WORKDIR_NOTE = ""


def test_explicit_requested_wins(home, tmp_path):
    """--workdir / 配置 / 桌面最近项目：原样采用，不参与安全判定。"""
    target = tmp_path / "proj"
    target.mkdir()
    assert W.resolve_workdir(str(target)) == target.resolve()
    assert W.consume_workdir_note() == ""


def test_safe_cwd_is_used(home, monkeypatch, tmp_path):
    """普通项目目录仍沿用 cwd（保留 cd project && jarvis 的习惯）。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setattr(W.Path, "cwd", lambda: proj)
    assert W.resolve_workdir() == proj.resolve()
    assert W.consume_workdir_note() == ""


def test_home_root_falls_back(home, monkeypatch):
    """主目录根不当项目：回退到默认工作区并自动创建。"""
    monkeypatch.setattr(W.Path, "cwd", lambda: home)
    resolved = W.resolve_workdir()
    assert resolved == home / ".jarvis" / "workspace"
    assert resolved.is_dir()
    assert "主目录根" in W.consume_workdir_note()


_NT_ONLY = pytest.mark.skipif(
    os.name != "nt", reason="系统目录/盘符判定按 Windows 路径语义"
)


@_NT_ONLY
def test_drive_root_falls_back(home, monkeypatch):
    """盘根不当项目：回退到默认工作区（Windows 路径语义）。"""
    monkeypatch.setattr(W.Path, "cwd", lambda: Path("C:\\"))
    resolved = W.resolve_workdir()
    assert resolved == home / ".jarvis" / "workspace"
    assert "盘根" in W.consume_workdir_note()


@pytest.mark.skipif(os.name == "nt", reason="POSIX 盘根用例")
def test_posix_root_is_unsafe():
    """Linux/macOS 的 `/` 同样是盘根，不能当项目。"""
    assert W.unsafe_reason("/")


@_NT_ONLY
@pytest.mark.parametrize(
    "path",
    [
        r"C:\Windows\System32",
        r"C:\Program Files\Demo",
        r"C:\ProgramData",
    ],
)
def test_system_dirs_are_unsafe(path):
    assert W.unsafe_reason(path)


def test_note_is_consumed_once(home, monkeypatch):
    """提示只取一次：避免每轮或每个宿主重复打印。"""
    monkeypatch.setattr(W.Path, "cwd", lambda: home)
    W.resolve_workdir()
    assert W.consume_workdir_note()
    assert W.consume_workdir_note() == ""


def test_settings_toml_workdir_is_honored(home, tmp_path, monkeypatch):
    """配置里的 workdir 必须生效。

    回归：该键此前漏在 ``_apply_toml`` 的顶层白名单外，写进 settings.toml
    也静默忽略（而 catalog/模板都说可以改）。2026-11-20 修。
    """
    from agent.config.settings import load_settings

    monkeypatch.setenv("JARVIS_WORKDIR", "")  # 避免环境变量抢优先级
    proj = tmp_path / "proj"
    (proj / "configs").mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    (proj / "configs" / "settings.toml").write_text(
        f'workdir = "{target.as_posix()}"\n', encoding="utf-8"
    )

    s = load_settings(workdir=str(proj))
    assert Path(s.workdir) == target.resolve()
