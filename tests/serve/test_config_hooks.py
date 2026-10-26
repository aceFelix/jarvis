"""配置层 × 宿主的生效接线（阶段 12）—— ``agent/serve/config_hooks.py``。

这一层只做一件事：把"改配置之后怎么才能真的生效"从工具里解耦出来。
所以要验的也就三件事：

1. **幂等**：``_start_percept`` 会在每次热重装配里被调用，钩子不能越挂越多；
2. **诚实**：没装配总线时（总开关关 / 终端场景）必须返回 False，让回执说
   "重启后生效"，而不是假装改成功了；
3. **真生效**：``apply_mode=percept`` 的钩子跑完，总线实例确实换了新的
   （旧实例留着就等于资源没释放——"关掉视觉就断摄像头"那条承诺靠这个）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.config.config_specs import (
    APPLY_PERCEPT,
    APPLY_RESCHEDULE,
    clear_apply_hooks,
    clear_change_listeners,
    run_apply_hooks,
)
from agent.config.settings import Settings
from agent.serve.config_hooks import register_hub_apply_hooks
from agent.serve.hub import ProactiveHub


def _settings(**over) -> Settings:
    """hub 测试用配置：感知总开关开、逐源全关、不落盘、简报与 TTS 全关。"""
    base = dict(
        percept_enabled=True,
        percept_log_events=False,
        briefing_enabled=False,
        deadline_enabled=False,
        proactive_tts_enabled=False,
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _clean_hooks():
    """每个用例从空钩子表/监听器表开始（全局字典，不隔离会串味）。"""
    clear_apply_hooks()
    clear_change_listeners()
    yield
    clear_apply_hooks()
    clear_change_listeners()


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """配置层回执可能触发落盘，一律指到 tmp_path。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield


@pytest.fixture()
def hub(_isolated_home):
    """装配好的 hub（逐源全关 → 不起轮询线程，不碰真实设备）。"""
    h = ProactiveHub(_settings(), queue.Queue())
    h.start()
    try:
        yield h
    finally:
        h.stop()


def test_register_is_idempotent(hub):
    """反复登记不能累加回调（``_start_percept`` 走的就是这条路）。"""
    register_hub_apply_hooks(hub)
    register_hub_apply_hooks(hub)
    _ok, notes = run_apply_hooks(APPLY_PERCEPT)
    assert len(notes) == 1              # 只有一条说明 = 只跑了一个回调


def test_start_percept_registers_the_hooks(hub):
    """hub.start() 就该把钩子挂上——挂晚了，开机第一句"关掉感知"会得到"需重启"。"""
    ok, notes = run_apply_hooks(APPLY_PERCEPT)
    assert ok is True and notes
    ok2, notes2 = run_apply_hooks(APPLY_RESCHEDULE)
    assert ok2 is True and notes2


def test_percept_hook_reassembles_the_bus(hub):
    """``percept`` 钩子跑完总线是**新实例**：旧实例留着等于资源没释放。"""
    old = hub.percept_bus
    assert old is not None
    ok, notes = run_apply_hooks(APPLY_PERCEPT)
    assert ok is True
    assert hub.percept_bus is not old
    assert "重装配" in notes[0]


def test_percept_hook_reports_failure_when_bus_absent():
    """总开关关着 → 没有总线 → 钩子必须返回 False（让回执如实说原因）。

    @author aceFelix
    """
    h = ProactiveHub(_settings(percept_enabled=False), queue.Queue())
    h.start()
    try:
        register_hub_apply_hooks(h)
        ok, notes = run_apply_hooks(APPLY_PERCEPT)
        assert ok is False
        assert any("总开关关着" in n for n in notes)
    finally:
        h.stop()


def test_reschedule_hook_reads_current_settings(hub):
    """``reschedule`` 钩子是按 hub 当前 Settings 现读重注册的。

    这条守的是"钩子读的 settings 和模型改的是同一个实例"——两个实例的话，
    钩子会把旧值重新注册一遍，用户看到"保存成功"而实际没变。
    """
    hub._settings.briefing_enabled = True
    hub._settings.briefing_time = "07:15"
    ok, _notes = run_apply_hooks(APPLY_RESCHEDULE)
    assert ok is True
    # 重注册后调度器里应能看到 07:15 的每日简报任务（按 note 认领，不靠中文标题）
    from agent.core.daemon.proactive import _BRIEFING_NOTE

    briefs = [t for t in hub.scheduler.list_all() if t.note == _BRIEFING_NOTE]
    assert len(briefs) == 1                    # 旧任务先撤再注册，不许堆两份
    assert "07:15" in briefs[0].trigger_at


def test_source_toggle_with_master_off_is_honest():
    """总开关关着时改逐源开关：回执要说“没有总线可重装配”，不能说“宿主未接线”。

    实测出厂态就长这样（`[percept]` 里只有 `window_enabled = true`，`enabled`
    缺省为 false）——这是用户第一次手测最可能撞上的路，说错话会让他去查
    本来就没坏的接线。

    @author aceFelix
    """
    h = ProactiveHub(_settings(percept_enabled=False), queue.Queue())
    h.start()
    try:
        register_hub_apply_hooks(h)
        ok, notes = run_apply_hooks(APPLY_PERCEPT)
        assert ok is False
        assert any("总开关关着" in n and "打开总开关时" in n for n in notes)
    finally:
        h.stop()


def test_turning_master_on_assembles_the_bus():
    """把总开关打开这一步必须**现装**出总线（早退就是真 bug）。

    写成“`_bus is None` 就早退”的话，用户把感知总开关打开却什么也没发生——
    而这恰恰是自配置层最该做对的一件事。

    @author aceFelix
    """
    h = ProactiveHub(_settings(percept_enabled=False), queue.Queue())
    h.start()
    try:
        register_hub_apply_hooks(h)
        assert h.percept_bus is None            # 开局：总开关关，没总线
        h._settings.percept_enabled = True      # 等价于 ConfigSet 已改完运行时
        ok, notes = run_apply_hooks(APPLY_PERCEPT)
        assert ok is True
        assert h.percept_bus is not None
        assert any("总开关已打开" in n and "已装配" in n for n in notes)
    finally:
        h.stop()


def test_master_switch_off_says_bus_stopped_not_zero_sources(hub):
    """关掉感知总开关时，回执要说“总线已停”，不能说“启用 0 个源”。

    后者会让用户以为总线还在、只是没有源在采——实际是整条总线不再存在。

    @author aceFelix
    """
    hub._settings.percept_enabled = False
    ok, notes = run_apply_hooks(APPLY_PERCEPT)
    assert ok is True
    assert any("总开关已关闭" in n and "总线已停" in n for n in notes)
    assert hub.percept_bus is None
    assert not any("启用 0 个源" in n for n in notes)


def test_config_set_broadcasts_settings_changed_event(hub, tmp_path):
    """模型改完配置要往事件队列推一条 ``settings_changed``。

    这是“面板不刷旧值”的根：面板只在 init 拉一次 settings.get，后端不推事件，
    它就永远停在旧值；用户再拖一下滑杆还会把旧值写回去。

    @author aceFelix
    """
    from agent.serve import protocol
    from agent.tools.config_tool import ConfigSetTool

    eq: queue.Queue = queue.Queue()
    hub._event_queue = eq                     # 用干净队列看本次投递
    ctx = SimpleNamespace(
        settings=hub._settings,
        workdir=str(tmp_path),
        permission_mode="default",
        session_name="s-hook",
        extra={},
    )
    res = asyncio.run(ConfigSetTool().call({"patch": {"tts_volume": 30}}, ctx))
    assert res.is_error is False

    events = []
    while not eq.empty():
        events.append(eq.get_nowait())
    pushed = [e for e in events if e.get("type") == protocol.EVT_SETTINGS_CHANGED]
    assert len(pushed) == 1
    assert pushed[0]["payload"] == {"keys": ["tts_volume"], "source": "model"}


def test_no_event_when_write_is_a_noop(hub, tmp_path):
    """值没变就不推事件（不制造无意义的面板重拉）。"""
    from agent.serve import protocol
    from agent.tools.config_tool import ConfigSetTool

    eq: queue.Queue = queue.Queue()
    hub._event_queue = eq
    ctx = SimpleNamespace(
        settings=hub._settings,
        workdir=str(tmp_path),
        permission_mode="default",
        session_name="s-hook",
        extra={},
    )
    asyncio.run(
        ConfigSetTool().call({"patch": {"tts_volume": hub._settings.tts_volume}}, ctx)
    )
    events = []
    while not eq.empty():
        events.append(eq.get_nowait())
    assert not [e for e in events if e.get("type") == protocol.EVT_SETTINGS_CHANGED]


def test_config_set_on_hub_settings_takes_effect_without_restart(hub, tmp_path):
    """端到端：模型改一个感知开关 → 钩子当场重装配 → 回执里不出现"重启"。"""
    from agent.tools.config_tool import ConfigSetTool

    ctx = SimpleNamespace(
        settings=hub._settings,
        workdir=str(tmp_path),
        permission_mode="default",
        session_name="s-hook",
        extra={},
    )
    res = asyncio.run(
        ConfigSetTool().call({"patch": {"window_enabled": True}}, ctx)
    )
    assert res.is_error is False
    assert "重装配" in res.data and "重启" not in res.data
