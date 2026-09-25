"""ProactiveHub（主动播报中枢）单元测试。

覆盖：
- 装配与幂等：start() 注册每日简报任务，二次 start() 不重复
- 到期路由：用户提醒投 proactive_notify 事件；引擎任务转引擎自处理
- 通知路由：简报/截止日期按内容前缀区分 kind
- 补播窗口判定：错过 ≤ 窗口（settings 可配，默认 2 小时）补播、超窗/未到/关闭/窗口非法不补播
- 提醒确认：acknowledge 落到 Scheduler
- TTS 待机朗读（二期）：开关关闭不朗读、提醒加称呼前缀、忙时探针跳过、
  简报超 200 字截断、markdown 清洗、语音依赖缺失静默降级、停机打断
- server 侧 proactive.ack RPC：hub 缺失/参数缺失报错、正常路径返回

持久化隔离手法与 tests/daemon/test_scheduler.py 一致：monkeypatch
_schedule_file / _deadlines_file 重定向到 tmp_path，不污染真实 ~/.jarvis。

@author aceFelix
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

import agent.core.daemon.deadline as deadline_mod
import agent.core.daemon.scheduler as scheduler_mod
import agent.serve.hub as hub_mod
from agent.serve import protocol
from agent.serve.hub import ProactiveHub
from agent.voice import tts as tts_mod


class _StubSettings:
    """Settings 替身：仅含 ProactiveHub 用到的主动感知与 TTS 字段。"""

    def __init__(
        self,
        *,
        briefing_enabled: bool = True,
        briefing_time: str = "08:30",
        briefing_catchup_window_min: int = 120,
        proactive_tts_enabled: bool = False,
    ):
        self.briefing_enabled = briefing_enabled
        self.briefing_time = briefing_time
        self.briefing_catchup_window_min = briefing_catchup_window_min
        self.deadline_enabled = True
        self.deadline_check_time = "09:00"
        # TTS 朗读通道字段（默认关闭：既有用例不期望起朗读线程）
        self.proactive_tts_enabled = proactive_tts_enabled
        self.tts_model = "cosyvoice-v3-flash"
        self.tts_voice = "longanlang_v3"
        self.tts_volume = 50
        self.tts_speech_rate = 1.0
        self.tts_pitch_rate = 1.0
        self.api_key = "test-key"


class _FakeTTS:
    """CosyVoiceTTS 替身：记录构造参数与朗读文本，不碰真实音频设备。

    block=True 时 speak 进入后阻塞在 release 事件上，供停机打断用例验证。
    """

    instances: list["_FakeTTS"] = []

    def __init__(self, block: bool = False, **kw) -> None:
        self.kw = kw
        self.block = block
        self.text: str | None = None
        self.stopped = False
        self.spoken = threading.Event()
        self.release = threading.Event()
        _FakeTTS.instances.append(self)

    def speak(self, text: str, **kw) -> dict:
        self.text = text
        self.spoken.set()
        if self.block:
            self.release.wait(timeout=3)
        return {"data_chunks": 1}

    def stop(self) -> None:
        self.stopped = True
        self.release.set()


@pytest.fixture
def fake_tts(monkeypatch):
    """桩替换 CosyVoiceTTS（hub 在工作线程内懒导入，替换模块属性即生效）。"""
    _FakeTTS.instances.clear()
    monkeypatch.setattr(tts_mod, "CosyVoiceTTS", _FakeTTS)
    return _FakeTTS


def _wait_instance(fake_tts, timeout: float = 2.0) -> _FakeTTS:
    """轮询等待朗读线程构造出 TTS 实例（避免 sleep 猜时长）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fake_tts.instances:
            return fake_tts.instances[0]
        time.sleep(0.02)
    raise AssertionError("未等到 TTS 朗读实例")


@pytest.fixture(autouse=True)
def isolate_persistence(tmp_path, monkeypatch):
    """把 Scheduler / DeadlineTracker 的持久化文件重定向到 tmp_path。"""
    monkeypatch.setattr(
        scheduler_mod, "_schedule_file", lambda: tmp_path / "schedule.json"
    )
    monkeypatch.setattr(
        deadline_mod, "_deadlines_file", lambda: tmp_path / "deadlines.json"
    )


@pytest.fixture
def hub():
    """构造 Hub（不 start），并保证测试结束时无残留线程/定时器。"""
    eq: queue.Queue = queue.Queue()
    h = ProactiveHub(_StubSettings(), eq)
    yield h, eq
    h.stop()


def _drain(eq: queue.Queue) -> list[dict]:
    """取空事件队列，返回事件列表。"""
    items = []
    while not eq.empty():
        items.append(eq.get_nowait())
    return items


# ---- 装配与幂等 ----

class TestAssembly:
    def test_start_registers_briefing_task(self, hub):
        """start() 后调度器含每日简报任务（引擎 note 标记识别）。"""
        h, _ = hub
        h.start()
        notes = [t.note for t in h.scheduler.list_pending()]
        assert "__proactive_briefing__" in notes

    def test_start_idempotent(self, hub):
        """二次 start() 不重复注册简报任务、不重复起线程。"""
        h, _ = hub
        h.start()
        h.start()
        briefings = [
            t for t in h.scheduler.list_pending()
            if t.note == "__proactive_briefing__"
        ]
        assert len(briefings) == 1

    def test_stop_without_start_is_safe(self, hub):
        """未 start 直接 stop 不抛异常（幂等收尾）。"""
        h, _ = hub
        h.stop()


# ---- 到期路由 ----

class TestFireRouting:
    def test_user_reminder_emits_event(self, hub):
        """用户提醒任务到期 → proactive_notify（kind=reminder、task_id 正确）。"""
        h, eq = hub
        task = h.scheduler.add_task(
            content="开会", trigger_at=datetime.now() - timedelta(seconds=1)
        )
        h._on_fire(task)
        events = _drain(eq)
        assert len(events) == 1
        assert events[0]["type"] == protocol.EVT_PROACTIVE_NOTIFY
        payload = events[0]["payload"]
        assert payload["kind"] == "reminder"
        assert payload["title"] == "贾维斯提醒"
        assert payload["text"] == "开会"
        assert payload["task_id"] == task.id

    def test_proactive_task_routes_to_engine(self, hub, monkeypatch):
        """引擎注册的任务（简报 note）转引擎自处理，不直接投提醒事件。"""
        h, eq = hub
        h.start()
        calls: list[str] = []
        monkeypatch.setattr(
            h._engine, "handle_task_fire", lambda task: calls.append(task.note)
        )
        task = h.scheduler.add_task(
            content="每日简报",
            trigger_at=datetime.now() + timedelta(hours=1),
            note="__proactive_briefing__",
        )
        h._on_fire(task)
        assert calls == ["__proactive_briefing__"]
        # 引擎任务不走提醒事件通道（handle_task_fire 已被桩替换，队列为空）
        assert _drain(eq) == []


# ---- 通知路由 ----

class TestNotifyRouting:
    def test_briefing_notify(self, hub):
        """简报文本 → kind=briefing，全文入 payload。"""
        h, eq = hub
        text = "早上好，先生。以下是今日简报：\n\n📅 今天是工作日"
        h._on_notify(text)
        payload = _drain(eq)[0]["payload"]
        assert payload["kind"] == "briefing"
        assert payload["title"] == "贾维斯主动提醒"
        assert payload["text"] == text
        assert payload["task_id"] == ""

    def test_deadline_notify_by_prefix(self, hub):
        """截止日期提醒（固定前缀）→ kind=deadline。"""
        h, eq = hub
        h._on_notify("📋 截止日期提醒：\n  • 明天：交报告")
        assert _drain(eq)[0]["payload"]["kind"] == "deadline"


# ---- 简报补播 ----

# 固定时钟：补播判定依赖「现在 vs 今日简报时间」的差值，真实时钟下
# now 减去一小时跨午夜时 HH:MM 会被解释成当天未来时间→返回 None 闪挂；
# 冻结 hub 模块时钟为正午消除时间炸弹。@author aceFelix
FIXED_NOW = datetime(2026, 1, 1, 12, 0, 0)


class _FrozenDatetime(datetime):
    """datetime 替身：now() 恒返回固定正午时刻（注入 hub 模块用）。"""

    @classmethod
    def now(cls, tz=None):  # 签名兼容 stdlib（hub 仅无参调用）
        return FIXED_NOW


class TestBriefingCatchup:
    @pytest.fixture(autouse=True)
    def _freeze_hub_clock(self, monkeypatch):
        """本类用例统一用 FIXED_NOW 推算时间，hub 内部时钟同步冻结。"""
        monkeypatch.setattr(hub_mod, "datetime", _FrozenDatetime)

    def test_missed_within_window_returns_minutes(self):
        """错过 1 小时（≤2 小时窗口）→ 返回错过分钟数。"""
        eq: queue.Queue = queue.Queue()
        late = (FIXED_NOW - timedelta(hours=1)).strftime("%H:%M")
        h = ProactiveHub(_StubSettings(briefing_time=late), eq)
        missed = h.missed_briefing_minutes()
        assert missed is not None
        assert 55 <= missed <= 65

    def test_missed_beyond_window_returns_none(self):
        """错过 3 小时（超窗口）→ 不补播。"""
        late = (FIXED_NOW - timedelta(hours=3)).strftime("%H:%M")
        h = ProactiveHub(_StubSettings(briefing_time=late), queue.Queue())
        assert h.missed_briefing_minutes() is None

    def test_future_time_returns_none(self):
        """今天的简报时间还没到 → 不补播。"""
        future = (FIXED_NOW + timedelta(hours=1)).strftime("%H:%M")
        h = ProactiveHub(_StubSettings(briefing_time=future), queue.Queue())
        assert h.missed_briefing_minutes() is None

    def test_disabled_returns_none(self):
        """briefing_enabled=False → 不补播。"""
        late = (FIXED_NOW - timedelta(hours=1)).strftime("%H:%M")
        h = ProactiveHub(
            _StubSettings(briefing_enabled=False, briefing_time=late), queue.Queue()
        )
        assert h.missed_briefing_minutes() is None

    def test_invalid_time_returns_none(self):
        """briefing_time 格式非法 → 判定为 None（不抛异常）。"""
        h = ProactiveHub(_StubSettings(briefing_time="bad"), queue.Queue())
        assert h.missed_briefing_minutes() is None

    def test_custom_window_narrower_excludes(self):
        """补播窗口取自 settings：窗口设 30 分钟，错过 40 分钟 → 不补播。"""
        late = (FIXED_NOW - timedelta(minutes=40)).strftime("%H:%M")
        h = ProactiveHub(
            _StubSettings(briefing_time=late, briefing_catchup_window_min=30),
            queue.Queue(),
        )
        assert h.missed_briefing_minutes() is None

    def test_custom_window_wider_includes(self):
        """补播窗口取自 settings：同样错过 40 分钟，窗口设 90 → 补播。"""
        late = (FIXED_NOW - timedelta(minutes=40)).strftime("%H:%M")
        h = ProactiveHub(
            _StubSettings(briefing_time=late, briefing_catchup_window_min=90),
            queue.Queue(),
        )
        missed = h.missed_briefing_minutes()
        assert missed is not None
        assert 35 <= missed <= 45

    def test_invalid_window_returns_none(self):
        """补播窗口配置非法（非数值）→ 安全降级为不补播，不抛异常。"""
        late = (FIXED_NOW - timedelta(minutes=40)).strftime("%H:%M")
        h = ProactiveHub(
            _StubSettings(briefing_time=late, briefing_catchup_window_min="abc"),
            queue.Queue(),
        )
        assert h.missed_briefing_minutes() is None

    def test_catchup_fire_emits_briefing(self, hub, monkeypatch):
        """补播触发引擎简报生成（桩替换 _fire_briefing 断言调用）。"""
        h, _ = hub
        fired: list[bool] = []
        monkeypatch.setattr(h._engine, "_fire_briefing", lambda: fired.append(True))
        h._fire_catchup_briefing()
        assert fired == [True]

    def test_catchup_fire_swallows_exception(self, hub, monkeypatch):
        """补播异常静默（不影响 serve 主流程）。"""
        h, _ = hub

        def _boom():
            raise RuntimeError("简报炸了")

        monkeypatch.setattr(h._engine, "_fire_briefing", _boom)
        h._fire_catchup_briefing()  # 不应抛出


# ---- 提醒确认 ----

class TestAcknowledge:
    def test_acknowledge_reaches_scheduler(self, hub):
        """acknowledge 落到 Scheduler（任务 acknowledged 置位）。"""
        h, _ = hub
        task = h.scheduler.add_task(
            content="喝水", trigger_at=datetime.now() + timedelta(minutes=1)
        )
        assert h.acknowledge(task.id) is True
        stored = next(t for t in h.scheduler.list_all() if t.id == task.id)
        assert stored.acknowledged is True

    def test_acknowledge_unknown_id_returns_false(self, hub):
        """未知 task_id → False（不抛异常）。"""
        h, _ = hub
        assert h.acknowledge("nonexistent") is False


# ---- TTS 待机朗读（二期） ----

class TestTtsBroadcast:
    def test_switch_off_no_speak(self, hub, fake_tts):
        """proactive_tts_enabled=False（桩默认）→ 事件照投、不起朗读线程。"""
        h, eq = hub
        h._emit(kind="reminder", title="贾维斯提醒", text="喝水")
        assert len(_drain(eq)) == 1
        time.sleep(0.2)
        assert fake_tts.instances == []

    def test_reminder_speak_with_prefix(self, fake_tts):
        """开关开 + 空闲：reminder 加称呼前缀整段播，事件通道不受影响。"""
        eq: queue.Queue = queue.Queue()
        h = ProactiveHub(_StubSettings(proactive_tts_enabled=True), eq)
        try:
            h._emit(kind="reminder", title="贾维斯提醒", text="喝水")
            inst = _wait_instance(fake_tts)
            assert inst.spoken.wait(2)
            assert inst.text == "先生，提醒您：喝水"
            assert len(_drain(eq)) == 1
            # TTS 构造参数取自 settings 的 tts_* 字段
            assert inst.kw["model"] == "cosyvoice-v3-flash"
            assert inst.kw["voice"] == "longanlang_v3"
        finally:
            h.stop()

    def test_busy_probe_skips_speak(self, fake_tts):
        """构造期注入忙探针=True → 只推事件不朗读。"""
        eq: queue.Queue = queue.Queue()
        h = ProactiveHub(
            _StubSettings(proactive_tts_enabled=True), eq, busy_probe=lambda: True
        )
        try:
            h._emit(kind="reminder", title="贾维斯提醒", text="喝水")
            assert len(_drain(eq)) == 1
            time.sleep(0.2)
            assert fake_tts.instances == []
        finally:
            h.stop()

    def test_set_busy_probe_after_construct(self, fake_tts):
        """事后 set_busy_probe 注入（hub 先于 engine 装配的接线方式）。"""
        eq: queue.Queue = queue.Queue()
        h = ProactiveHub(_StubSettings(proactive_tts_enabled=True), eq)
        try:
            h.set_busy_probe(lambda: True)
            h._emit(kind="briefing", title="t", text="简报")
            time.sleep(0.2)
            assert fake_tts.instances == []
            h.set_busy_probe(lambda: False)
            h._emit(kind="briefing", title="t", text="简报")
            inst = _wait_instance(fake_tts)
            assert inst.spoken.wait(2)
        finally:
            h.stop()

    def test_briefing_truncated(self, fake_tts):
        """简报超 200 字 → 只播前 200 字 + 引导后缀（复刻老 daemon 口径）。"""
        eq: queue.Queue = queue.Queue()
        h = ProactiveHub(_StubSettings(proactive_tts_enabled=True), eq)
        try:
            h._on_notify("简" * 300)
            inst = _wait_instance(fake_tts)
            assert inst.spoken.wait(2)
            assert inst.text == "简" * 200 + "……详细内容请查看桌面壳。"
        finally:
            h.stop()

    def test_markdown_cleaned(self, fake_tts):
        """朗读文本经 clean_for_tts 清洗：markdown/think/工具标签不入口。"""
        eq: queue.Queue = queue.Queue()
        h = ProactiveHub(_StubSettings(proactive_tts_enabled=True), eq)
        try:
            h._on_notify(
                "# 标题\n**提醒**：喝水 <think>秘密</think>\n尾<bash>rm -rf /</bash>。"
            )
            inst = _wait_instance(fake_tts)
            assert inst.spoken.wait(2)
            assert "秘密" not in inst.text
            assert "rm -rf" not in inst.text
            assert "**" not in inst.text and "#" not in inst.text
            assert "标题" in inst.text
            assert "提醒：喝水" in inst.text
            assert inst.text.endswith("尾。")
        finally:
            h.stop()

    def test_voice_dep_missing_silent(self, fake_tts, monkeypatch):
        """语音依赖缺失（ImportError）→ 事件照投、朗读静默降级不抛异常。"""
        monkeypatch.setitem(sys.modules, "agent.voice.tts", None)
        eq: queue.Queue = queue.Queue()
        h = ProactiveHub(_StubSettings(proactive_tts_enabled=True), eq)
        try:
            h._emit(kind="reminder", title="贾维斯提醒", text="喝水")
            assert len(_drain(eq)) == 1
            time.sleep(0.2)
            assert fake_tts.instances == []
        finally:
            h.stop()

    def test_stop_interrupts_inflight(self, fake_tts, monkeypatch):
        """停机打断正在朗读的 TTS（stop() 调到当前实例的 stop）。"""
        monkeypatch.setattr(
            tts_mod, "CosyVoiceTTS", lambda **kw: _FakeTTS(block=True, **kw)
        )
        eq: queue.Queue = queue.Queue()
        h = ProactiveHub(_StubSettings(proactive_tts_enabled=True), eq)
        h.start()
        h._emit(kind="reminder", title="贾维斯提醒", text="喝水")
        inst = _wait_instance(fake_tts)
        assert inst.spoken.wait(2)  # speak 已进入并阻塞在 release 上
        h.stop()
        assert inst.stopped is True
