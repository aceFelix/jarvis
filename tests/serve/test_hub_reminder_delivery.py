"""用户自设提醒的送达路径（2026-10-22：这条线以前完全不过 W1 闸门）。

验的是**接线与通道选择**：提醒必须交给预算裁决（源名 `schedule`、去重键带 task.id）、
静默降级时"气泡照投但不朗读"、邮件通道走发送并记副作用账、尚未接入的 `sms`
通道要警告并忽略而不是假装已发。闸门内部的阈值算法在
tests/policy/test_notify_budget.py，这里用桩替掉 `engine._budget`，避免读写
真实 `~/.jarvis/notify_state.json`。

@author aceFelix
"""

from __future__ import annotations

import logging
import queue

import pytest

import agent.tools.extensions.email_tool as email_tool
from agent.config.settings import Settings
from agent.core.actions import STATE_APPLIED, configure_journal, get_journal
from agent.core.daemon.proactive import notify_gate_action
from agent.core.daemon.scheduler import ScheduleTask
from agent.core.policy.budget import NotifyAction
from agent.serve import protocol
from agent.serve.hub import ProactiveHub
from agent.serve.quiet_delivery import (
    REMINDER_NOTIFY_SOURCE,
    REMINDER_URGENCY,
    parse_channels,
    send_quiet_email,
)


class _StubBudget:
    """预算替身：记录 hub 交下来的入参，按脚本返回裁决（含 speak 降级标记）。"""

    def __init__(self, kind: str = "emit", *, speak: bool = True) -> None:
        self.kind = kind
        self.speak = speak
        self.calls: list[dict] = []

    def gate(self, text: str, source: str, *, dedup_key: str = "",
             urgency: float = 0.5, silent_delivery: bool = False) -> NotifyAction:
        self.calls.append({
            "text": text, "source": source, "dedup_key": dedup_key,
            "urgency": urgency, "silent_delivery": silent_delivery,
        })
        return NotifyAction(self.kind, text, "测试桩", speak=self.speak)


def _task() -> ScheduleTask:
    return ScheduleTask(id="t1", content="喝水", trigger_at="2026-10-22T23:25:00")


def _settings(**over) -> Settings:
    base = dict(
        percept_enabled=False, briefing_enabled=False, deadline_enabled=False,
        proactive_tts_enabled=False,
    )
    base.update(over)
    return Settings(**base)


def _hub(stub: _StubBudget, **over):
    """建一个不起线程的 hub（感知/简报/截止日期全关，只留提醒路径）。"""
    eq: queue.Queue = queue.Queue()
    hub = ProactiveHub(_settings(**over), eq)
    hub._engine._budget = stub          # 桩替换 W1 闸门
    spoken: list[str] = []
    hub._spawn_tts = lambda kind, text: spoken.append(kind)   # 只记不真朗读
    return hub, eq, stub, spoken


def _drain(eq: queue.Queue) -> list[dict]:
    items: list[dict] = []
    while not eq.empty():
        items.append(eq.get_nowait())
    return items


def _notify_events(items: list[dict]) -> list[dict]:
    return [i["payload"] for i in items
            if i.get("type") == protocol.EVT_PROACTIVE_NOTIFY]


# ---- 闸门接线 ----

def test_reminder_now_goes_through_the_gate():
    """回归主缺陷：以前 `_on_fire` 直接投事件，静默时段与日额度对提醒全部无效。"""
    hub, eq, stub, spoken = _hub(_StubBudget())

    hub._deliver_reminder(_task())

    assert len(stub.calls) == 1, "提醒没交给闸门 = 老 bug 复发"
    call = stub.calls[0]
    assert call["source"] == REMINDER_NOTIFY_SOURCE
    assert "t1" in call["dedup_key"], "去重键必须带 task.id，否则两条提醒互相合并"
    assert call["urgency"] == REMINDER_URGENCY
    # 默认通道 ["screen"] 已实现 → 允许降级投递
    assert call["silent_delivery"] is True
    events = _notify_events(_drain(eq))
    assert [e["kind"] for e in events] == ["reminder"]
    assert events[0]["silent"] is False, "正常放行不该把系统通知也静掉"
    assert spoken == ["reminder"]


def test_queue_verdict_delivers_nothing():
    """闸门判入库待补播时不投事件（信息在 pending 里，不是丢了）。"""
    hub, eq, _stub, spoken = _hub(_StubBudget("queue"))

    hub._deliver_reminder(_task())

    assert _drain(eq) == []
    assert spoken == []


# ---- 降级投递：气泡要投，只是不出声 ----

def test_degraded_delivery_shows_bubble_without_speaking():
    """speak=False 的降级投递：事件照投（气泡+系统通知），TTS 不朗读。"""
    hub, eq, _stub, spoken = _hub(_StubBudget(speak=False))

    hub._deliver_reminder(_task())

    events = _notify_events(_drain(eq))
    assert len(events) == 1 and events[0]["task_id"] == "t1"
    assert spoken == [], "静默时段还朗读就是没降级"
    # 跨仓契约：Windows toast 自带提示音，不靠这个字段就只关掉了 TTS
    assert events[0]["silent"] is True


def test_email_channel_is_used_on_degraded_delivery(monkeypatch):
    """配了 email 通道 → 降级投递时另发一封信（不发真邮件，桩掉发送）。"""
    hub, _eq, _stub, _spoken = _hub(
        _StubBudget(speak=False),
        notify_reminder_channels=["screen", "email"],
    )
    sent: list[str] = []
    monkeypatch.setattr(
        hub, "_send_quiet_email", lambda task: sent.append(task.id),
    )

    hub._deliver_reminder(_task())

    assert sent == ["t1"]


def test_unimplemented_sms_channel_is_ignored_with_warning(caplog):
    """`sms` 是留位：必须警告并忽略，且不能假装降级（退回入库待补播）。"""
    hub, eq, stub, _spoken = _hub(
        _StubBudget("queue"), notify_reminder_channels=["sms"],
    )

    with caplog.at_level(logging.WARNING, logger="jarvis.serve.hub"):
        hub._deliver_reminder(_task())

    assert "sms" in caplog.text
    assert stub.calls[0]["silent_delivery"] is False, "未实现的通道不该开启降级"
    assert _drain(eq) == []


def test_channel_warning_is_not_repeated_per_reminder(caplog):
    """警告只刷一次：提醒到期是高频回调，每条都警告就成了新的打扰。"""
    hub, _eq, _stub, _spoken = _hub(
        _StubBudget("queue"), notify_reminder_channels=["sms"],
    )

    with caplog.at_level(logging.WARNING, logger="jarvis.serve.hub"):
        hub._deliver_reminder(_task())
        hub._deliver_reminder(_task())

    assert caplog.text.count("尚未接入") == 1


# ---- 邮件送达本体（模块函数，不起线程、不碰 SMTP）----

@pytest.fixture
def journal(tmp_path):
    """账本指向 tmp：不污染用户真实的 ~/.jarvis/actions.jsonl。"""
    configure_journal(enabled=True, path=tmp_path / "actions.jsonl",
                      clear_window=True)
    yield get_journal()
    configure_journal(enabled=True, path=None, clear_window=True)


def test_quiet_email_sends_and_records_effect(journal, monkeypatch):
    """发信成功要记一条不可逆副作用账（/undo 据此如实说撤不回，不给无效按钮）。"""
    seen: dict[str, str] = {}

    def _fake_send(settings, args):
        seen.update(args)
        return "✓ 邮件已发送\n  收件人: me@x.com"

    monkeypatch.setattr(email_tool, "send_email_now", _fake_send)

    ok, detail = send_quiet_email(
        _settings(notify_quiet_email_to="me@x.com"), _task(),
    )

    assert ok is True and "已发送" in detail
    assert seen["to"] == "me@x.com"
    assert seen["subject"].startswith("贾维斯静默提醒")
    rec = journal.last(STATE_APPLIED)
    assert rec is not None and rec.tool == "SendEmail"
    assert rec.reversible is False and rec.undo_spec["kind"] == "none"


def test_quiet_email_failure_is_reported_not_raised(journal, monkeypatch):
    """发信失败只回原因：气泡已经上屏，不该重投也不该拖垮调度线程。"""
    def _boom(settings, args):
        raise ValueError("邮件功能未启用")

    monkeypatch.setattr(email_tool, "send_email_now", _boom)

    ok, reason = send_quiet_email(_settings(), _task())

    assert ok is False and "未启用" in reason
    assert journal.last(STATE_APPLIED) is None, "没发出去就不该入账"


def test_parse_channels_is_lenient_on_case_and_reports_unknown():
    """解析层只认已实现通道，其余全部交给调用方报错（不在这里刷日志）。"""
    out, unknown = parse_channels([" Screen ", "email", "sms", "pager", ""])

    assert out == {"screen", "email"}
    assert unknown == ["sms", "pager"]


# ---- away 与降级投递的优先级（模块层闸门）----

def _away_state(monkeypatch, away: bool) -> None:
    """把在场状态桩掉：notify_gate_action 内部现取 get_policy_state。"""
    import agent.core.policy as policy_mod

    class _State:
        def is_away(self):
            return away, {}

    monkeypatch.setattr(policy_mod, "get_policy_state", lambda: _State())


def test_away_still_queues_a_would_be_spoken_notice(monkeypatch):
    """会出声的播报在 away 时仍暂缓入库（TTS 不对空房间说话）。"""
    _away_state(monkeypatch, True)
    action = notify_gate_action(
        "briefing", "简报", budget=_StubBudget(speak=True),
    )
    assert action.kind == "queue"


def test_away_does_not_block_silent_delivery(monkeypatch):
    """降级投递本就不发声：away 时气泡照投，不该再被压回队列。"""
    _away_state(monkeypatch, True)
    action = notify_gate_action(
        REMINDER_NOTIFY_SOURCE, "喝水",
        budget=_StubBudget(speak=False), silent_delivery=True,
    )
    assert action.kind == "emit" and action.speak is False
