"""tests/voice 共享 fixture。

实时语音相关测试文件（test_realtime_observability /
test_realtime_response_lifecycle）共用同一套环境准备，集中在此避免重复定义。

@author aceFelix
"""

from __future__ import annotations

import pytest

from agent.voice import realtime_events as ev
from agent.voice import realtime_talk as rt_mod
from tests.voice._fakes import FakeDiag


@pytest.fixture()
def fake_diag(monkeypatch) -> FakeDiag:
    """把 realtime_events 里的 diag 模块换成替身（测试不得落真实日志）。"""
    stub = FakeDiag()
    monkeypatch.setattr(ev, "diag", stub)
    return stub


@pytest.fixture()
def _stub_tools(monkeypatch):
    """构造 RealtimeTalk 时不必真建 ToolRegistry（只验证契约与参数）。"""
    monkeypatch.setattr(rt_mod, "build_all_tools", lambda workdir: ([], {}))
