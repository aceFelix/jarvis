"""桌面项目工作区最近项目持久化（projects_registry）单测。

覆盖 list_projects / touch_project / forget / get_last_active 与 Windows
路径转义往返。用 monkeypatch 把 Path.home 指向 tmp_path，隔离真实用户目录。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config import projects_registry as reg


@pytest.fixture
def home(tmp_path, monkeypatch):
    """把 Path.home 重定向到临时目录，projects.toml 落在 tmp_path/.jarvis 下。"""
    (tmp_path / ".jarvis").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def test_empty_when_no_file(home):
    """无文件：最近列表空、last_active 空串。"""
    assert reg.list_projects() == []
    assert reg.get_last_active() == ""


def test_touch_creates_record_and_sets_active(home):
    """登记新项目：置顶入列表、设 last_active、name 取目录 basename。"""
    p = str(home / "projA")
    rec = reg.touch_project(p)
    assert rec["name"] == "projA"
    assert rec["path"] == str(Path(p))
    items = reg.list_projects()
    assert [i["path"] for i in items] == [str(Path(p))]
    assert reg.get_last_active() == str(Path(p))


def test_touch_reorders_and_dedups(home):
    """再次登记更靠后的项目：它置顶，列表按 last_opened 倒序、无重复。"""
    a = str(home / "a")
    b = str(home / "b")
    reg.touch_project(a)
    reg.touch_project(b)
    reg.touch_project(a)  # a 重新置顶
    items = reg.list_projects()
    assert [i["path"] for i in items] == [str(Path(a)), str(Path(b))]
    assert reg.get_last_active() == str(Path(a))


def test_touch_trims_to_max(home, monkeypatch):
    """超过上限的最近项目被截断（保持文件轻量）。"""
    monkeypatch.setattr(reg, "_MAX_RECENT", 3)
    for i in range(5):
        reg.touch_project(str(home / f"p{i}"))
    assert len(reg.list_projects()) == 3


def test_forget_removes_and_clears_active(home):
    """移除当前活跃项目：从列表消失且 last_active 清空；不存在返回 False。"""
    a = str(home / "a")
    reg.touch_project(a)
    assert reg.forget(a) is True
    assert reg.list_projects() == []
    assert reg.get_last_active() == ""
    assert reg.forget(a) is False  # 已不存在


def test_forget_keeps_other_active(home):
    """移除的若不是 last_active，则 last_active 保持不变。"""
    a = str(home / "a")
    b = str(home / "b")
    reg.touch_project(a)
    reg.touch_project(b)  # b 成为 last_active
    assert reg.forget(a) is True
    assert reg.get_last_active() == str(Path(b))


def test_windows_like_path_roundtrip(home):
    """含反斜杠的路径经转义往返后仍精确匹配（Windows 场景）。"""
    p = str(home / "nested" / "proj")
    reg.touch_project(p)
    assert reg.get_last_active() == str(Path(p))
    assert any(i["path"] == str(Path(p)) for i in reg.list_projects())


def test_get_last_active_existing(home):
    """last_active 存在且目录仍在 → 返回路径；目录缺失/未登记 → 空串。"""
    d = home / "live"
    d.mkdir()
    reg.touch_project(str(d))
    assert reg.get_last_active_existing() == str(Path(d))
    # 登记时目录不存在（不校验）后手动不建 → 恢复时目录不在 → 空串
    ghost = str(home / "gone")
    reg.touch_project(ghost)
    assert reg.get_last_active_existing() == ""  # last_active=ghost 但目录不存在


def test_corrupt_file_degrades_to_empty(home):
    """projects.toml 损坏（非法 TOML）：读取降级为空，不抛异常。"""
    (home / ".jarvis" / "projects.toml").write_text("= not valid [[[\n", encoding="utf-8")
    assert reg.list_projects() == []
    assert reg.get_last_active() == ""
