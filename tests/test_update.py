"""agent.update（版本自检与升级，Phase 1）的单元测试。

覆盖：版本比较、网络查询降级、节流缓存、安装渠道识别、升级命令生成、
一键升级的开发模式保护，以及启动后台检查的提示行为。

@author aceFelix
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

import pytest

from agent import update


def test_current_version_non_empty() -> None:
    """current_version 应始终返回非空字符串（包元数据或 agent.__version__）。"""
    ver = update.current_version()
    assert isinstance(ver, str) and ver


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("2.1.2", "2.1.1", True),
        ("2.1.1", "2.1.1", False),
        ("2.1.10", "2.1.9", True),   # 数字段按整数比较，不能按字符串
        ("2.2.0", "2.1.9", True),
        ("2.0.0", "2.1.0", False),
    ],
)
def test_is_newer(latest: str, current: str, expected: bool) -> None:
    assert update.is_newer(latest, current) is expected


def test_version_key_ordering() -> None:
    """宽松版本键：数字段按整数比较，保证 2.1.10 > 2.1.9。"""
    assert update._version_key("2.1.10") > update._version_key("2.1.9")
    assert update._version_key("2.1.1") == update._version_key("2.1.1")


def test_latest_version_returns_none_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """网络异常（离线/超时）必须静默返回 None，不抛异常。"""
    def _boom(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert update.latest_version() is None


def test_latest_version_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常响应时解析 info.version。"""
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self) -> bytes:
            return json.dumps({"info": {"version": "9.9.9"}}).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert update.latest_version() == "9.9.9"


def test_check_for_update_no_network(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """离线（无缓存）时返回 (False, 当前, 当前)。"""
    monkeypatch.setattr(update, "_cache_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(update, "latest_version", lambda timeout=1.5: None)
    has_update, cur, latest = update.check_for_update()
    assert has_update is False
    assert latest == cur


def test_check_for_update_detects_newer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update, "_cache_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(update, "latest_version", lambda timeout=1.5: "99.0.0")
    has_update, _cur, latest = update.check_for_update()
    assert has_update is True
    assert latest == "99.0.0"


def test_check_for_update_throttled_uses_cache(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """24 小时内第二次调用不再打网络，直接复用缓存。"""
    cache = tmp_path / "c.json"
    monkeypatch.setattr(update, "_cache_path", lambda: cache)
    calls = {"n": 0}

    def _fake_latest(timeout: float = 1.5):
        calls["n"] += 1
        return "99.0.0"

    monkeypatch.setattr(update, "latest_version", _fake_latest)
    update.check_for_update()
    assert calls["n"] == 1
    update.check_for_update()
    assert calls["n"] == 1  # 命中节流，未再次请求


def test_upgrade_command_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update, "detect_installer", lambda: "pip")
    cmd = update.upgrade_command()
    assert cmd is not None
    assert cmd[:3] == [sys.executable, "-m", "pip"]
    assert "jarvis-agent" in cmd


def test_upgrade_command_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update, "detect_installer", lambda: "uv")
    assert update.upgrade_command() == ["uv", "tool", "upgrade", "jarvis-agent"]


def test_upgrade_command_none_for_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    """开发模式（editable）不支持自动升级。"""
    monkeypatch.setattr(update, "detect_installer", lambda: "editable")
    assert update.upgrade_command() is None


def test_run_upgrade_editable_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update, "detect_installer", lambda: "editable")
    ok, msg = update.run_upgrade()
    assert ok is False
    assert "开发模式" in msg


def test_start_background_check_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """检出更新时，后台检查应通过 ui.info 打印含新版本的提示。"""
    class _UI:
        def __init__(self) -> None:
            self.msgs: list[str] = []

        def info(self, text: str) -> None:
            self.msgs.append(text)

    ui = _UI()
    monkeypatch.setattr(update, "check_for_update", lambda force=False: (True, "1.0.0", "2.0.0"))
    update.start_background_check(ui)
    # 等待守护线程完成（最多 ~0.5s）
    for _ in range(50):
        if ui.msgs:
            break
        time.sleep(0.01)
    assert ui.msgs
    assert "2.0.0" in ui.msgs[0]
