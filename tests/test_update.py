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


class _RecordingUI:
    """记录 `ui.info` 文本的替身。"""

    def __init__(self) -> None:
        self.msgs: list[str] = []

    def info(self, text: str) -> None:
        self.msgs.append(text)


def _drain(ui: _RecordingUI, start) -> None:
    """启动后台检查线程并等它把提示写进来（守护线程异步，最多 ~0.5s）。"""
    start()
    for _ in range(50):
        if ui.msgs:
            return
        time.sleep(0.01)


def test_current_version_non_empty() -> None:
    """current_version 应始终返回非空字符串（代码内版本或包元数据）。"""
    ver = update.current_version()
    assert isinstance(ver, str) and ver


def test_current_version_prefers_running_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """当前版本必须来自**实际被执行的代码**，而不是可能陈旧的包元数据。

    回归（2026-11-03 实机）：editable 安装下 dist-info 停在 2.2.2、代码已是 2.3.4，
    于是横幅提示「发现新版本 2.3.4（当前 2.2.2）」——那个“当前版本”是假的，还会把
    开发者推向会覆盖源码树的 `pip install -U`。旧实现先读元数据，顺序恰好错。
    """
    import importlib.metadata as md

    import agent

    monkeypatch.setattr(md, "version", lambda name: "1.0.0")
    monkeypatch.setattr(agent, "__version__", "9.9.9")
    assert update.current_version() == "9.9.9"


def test_current_version_falls_back_to_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """拿不到代码内版本时（源码头文件缺失的极端分发）回退到包元数据。"""
    import importlib.metadata as md

    import agent

    monkeypatch.setattr(md, "version", lambda name: "3.3.3")
    monkeypatch.delattr(agent, "__version__", raising=False)
    assert update.current_version() == "3.3.3"


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


def test_format_command_quotes_spaced_paths() -> None:
    """含空格的路径要加引号，否则 Windows venv 下拼出的命令粘回去执行不了。"""
    assert update.format_command(["C:\\Program Files\\py\\python.exe", "-m", "pip"]) == (
        '"C:\\Program Files\\py\\python.exe" -m pip'
    )


def test_start_background_check_editable_does_not_push_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开发模式下的提示不得喊「运行 /update」或「pip install -U」。

    回归：那句提示会把用户导向一个把 editable 安装换成 PyPI 正式版的命令，
    直接覆盖掉源码树里尚未发布的修改。渠道判断系统里本来就有，只是这条
    提示路径以前没去用它。
    """
    ui = _RecordingUI()
    monkeypatch.setattr(update, "check_for_update", lambda force=False: (True, "2.3.4", "2.4.0"))
    monkeypatch.setattr(update, "upgrade_command", lambda: None)
    _drain(ui, lambda: update.start_background_check(ui))

    text = ui.msgs[0]
    assert "2.4.0" in text
    assert "/update" not in text
    assert "pip install" not in text
    assert "git pull" in text


def test_start_background_check_pip_shows_real_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """普通 pip 安装：照旧告知 /update，并把真实可执行命令而非笼统文案给出来。"""
    ui = _RecordingUI()
    monkeypatch.setattr(update, "check_for_update", lambda force=False: (True, "1.0.0", "2.0.0"))
    monkeypatch.setattr(
        update,
        "upgrade_command",
        lambda: [
            "C:\\Program Files\\py\\python.exe", "-m", "pip",
            "install", "-U", "jarvis-agent",
        ],
    )
    _drain(ui, lambda: update.start_background_check(ui))

    text = ui.msgs[0]
    assert "/update" in text
    assert '"C:\\Program Files\\py\\python.exe"' in text  # 带空格路径已加引号


def test_start_background_check_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """检出更新时，后台检查应通过 ui.info 打印含新版本的提示。"""
    ui = _RecordingUI()
    monkeypatch.setattr(update, "check_for_update", lambda force=False: (True, "1.0.0", "2.0.0"))
    # 固定渠道：不指定的话这条用例会跟着测试机的安装方式变（editable 与 pip 文案不同）
    monkeypatch.setattr(
        update, "upgrade_command",
        lambda: [sys.executable, "-m", "pip", "install", "-U", "jarvis-agent"],
    )
    _drain(ui, lambda: update.start_background_check(ui))

    assert ui.msgs
    assert "2.0.0" in ui.msgs[0]
