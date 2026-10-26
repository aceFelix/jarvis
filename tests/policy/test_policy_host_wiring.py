"""W1 判断层的**宿主接线**测试（serve 入口的 init_policy）。

为什么单独测这个：`agent/core/policy` 的配置是「宿主启动时注入 Settings 快照」
的拉式读取（`_cfg` 从模块级 `_settings` 取字段，None 就回硬编码缺省）。
终端走 `main()` 有注入，桌面壳 spawn 的却是 `python -m agent.serve`，
一旦这个入口漏注入，`quiet_hours` / `daily_max` / `aggregate_window_s` /
`min_salience` / `away_idle_minutes` 五个字段在桌面壳里就全是摆设——
用户改了 TOML 也读不到，属于「配置写了不生效」最难查的那类。2026-10-22 实测复现。

@author aceFelix
"""

from __future__ import annotations

import types

import pytest


def _stub_settings(**over) -> types.SimpleNamespace:
    """policy 只按字段名 getattr，用最小桩即可（与真实 Settings 同字段名）。"""
    base = dict(
        away_idle_minutes=0,
        notify_budget_enabled=True,
        notify_daily_max=3,
        notify_quiet_hours="01:00-02:00",
        notify_aggregate_window_s=45,
        notify_min_salience=0.5,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture()
def isolated_budget(tmp_path, monkeypatch):
    """把预算单例与状态文件都关在 tmp 里，并清掉模块级注入态。

    不复用 conftest 的 policy_paths：那个夹具不重置预算路径，而 `get_budget()`
    建实例时读的是 `budget._DEFAULT_PATH`（真实 ~/.jarvis），测试会写用户盘。
    """
    from agent.core.policy import budget as budget_mod

    gate = pytest.importorskip("agent.core.policy")
    monkeypatch.setattr(budget_mod, "_DEFAULT_PATH", tmp_path / "notify_state.json")
    monkeypatch.setattr(gate, "_settings", None, raising=False)
    monkeypatch.setattr(gate, "_budget_singleton", None, raising=False)
    return tmp_path


def test_missing_injection_falls_back_to_hardcoded_defaults(isolated_budget):
    """未注入时预算只能用默认参数——这正是桌面壳曾经的症状，先钉住判据。"""
    from agent.core.policy import get_budget

    stats = get_budget().stats()
    assert stats["quiet_hours"] == "23:00-07:00"
    assert stats["daily_max"] == 10


def test_run_serve_injects_settings_before_serving(isolated_budget, monkeypatch):
    """回归：`python -m agent.serve` 入口必须注入 Settings，用户配置才生效。"""
    from agent.bridge import server as bridge_server
    from agent.core.policy import get_budget
    from agent.serve import app as serve_app

    # 起服务本身不在本例射程内：桩掉原生库预热与事件循环主体，只验接线
    monkeypatch.setattr(serve_app, "_prewarm_native_libs", lambda: None)

    async def _fake_serve(_settings):
        return None

    monkeypatch.setattr(serve_app, "_serve_main", _fake_serve)
    monkeypatch.setattr(bridge_server, "websockets", object(), raising=False)

    assert serve_app.run_serve(_stub_settings()) == 0
    stats = get_budget().stats()
    assert stats["quiet_hours"] == "01:00-02:00", "桌面壳里改静默时段仍不生效"
    assert stats["daily_max"] == 3


def test_run_serve_injects_even_when_websockets_missing(isolated_budget, monkeypatch):
    """缺 websockets 提前返回 3，注入也已发生：配置读取不能依赖能否起服务。"""
    from agent.bridge import server as bridge_server
    from agent.core.policy import get_budget, init_policy
    from agent.serve import app as serve_app

    init_policy(None)   # 先复位，确保不是上一例留下的注入态
    monkeypatch.setattr(bridge_server, "websockets", None, raising=False)

    assert serve_app.run_serve(_stub_settings()) == 3
    assert get_budget().stats()["quiet_hours"] == "01:00-02:00"
