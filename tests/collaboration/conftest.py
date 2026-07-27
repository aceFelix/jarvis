"""多 Agent 协作测试的公共 fixture。

所有测试共享一个隔离的临时 JARVIS_HOME，避免污染真实 ~/.jarvis 目录。
"""

import pytest


def _patch_jarvis_home(monkeypatch, tmp_path):
    """将 team / task_list / mailbox 使用的 home 目录重定向到 tmp_path。"""
    from agent.collaboration import team, task_list

    def _fake_home():
        return tmp_path / ".jarvis"

    monkeypatch.setattr(team, "_jarvis_home", _fake_home)
    monkeypatch.setattr(task_list, "_jarvis_home", _fake_home)


@pytest.fixture
def jarvis_home(tmp_path, monkeypatch):
    """返回隔离的 JARVIS_HOME 路径并打补丁。"""
    _patch_jarvis_home(monkeypatch, tmp_path)
    return tmp_path / ".jarvis"
