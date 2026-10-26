"""``TrackGoal`` 工具测试（W3.1 的模型侧入口，阶段 10 收尾切片）。

盯四件事：

1. **校验**：create 缺 goal、非 create 缺 goal_id、坏 action、坏等待条件都要失败；
2. **权限**：写台账放行、**删除要 ASK**（后台子代理不该悄悄抹掉用户的台账）；
3. **如实回报**：返回文本必须带上"驱动线程在不在跑"，否则模型会替用户以为已经在盯；
4. **两种 wait 写法**（dict 与 ``"time:+2h"`` 串）落出同一种台账。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import re
import time
from types import SimpleNamespace

import pytest

from agent.core.tasks.spine import GOAL_CANCELLED, configure_spine_store, parse_iso
from agent.core.tool import build_default_registry
from agent.core.tasks import driver as driver_mod
from agent.tools.track_goal import TrackGoalTool


@pytest.fixture()
def store(tmp_path):
    s = configure_spine_store(tmp_path / "goals")
    yield s
    configure_spine_store(None)


@pytest.fixture()
def tool():
    return TrackGoalTool()


@pytest.fixture()
def ctx(tmp_path):
    """最小 ToolContext：workdir / settings / session_name 三个入口都要读的字段。"""
    return SimpleNamespace(
        workdir=str(tmp_path),
        session_name="s-test",
        settings=SimpleNamespace(tasks_wait_deadline_hours=48),
        extra={},
    )


def _run(tool, args, ctx):
    return asyncio.run(tool.call(args, ctx))


def _id_of(text: str) -> str:
    """从返回文案里抠出目标 id（〔xxxx〕之间的十六进制串）。"""
    return re.search(r"〔([0-9a-f]+)〕", text).group(1)


# ---- 注册与形状 ----

def test_tool_registered_in_default_registry():
    """进默认工具表才算模型侧入口（少这一步，前面全白做）。"""
    assert "TrackGoal" in build_default_registry()


def test_description_says_when_not_to_use(tool):
    """描述里必须写清"本轮能做完的别建目标"，否则模型会拿它当 TodoWrite 用。"""
    assert "TodoWrite" in tool.description or "不要建目标" in tool.description


# ---- 校验 ----

@pytest.mark.parametrize(
    "args,frag",
    [
        ({}, "action 无效"),
        ({"action": "frobnicate"}, "action 无效"),
        ({"action": "create"}, "create 必须给 goal"),
        ({"action": "pause"}, "必须给 goal_id"),
    ],
)
def test_validate_input_rejects_bad_args(tool, ctx, args, frag):
    """校验层先把最典型的四种拼错接住（避免进到 call 之后才回一个模糊失败）。"""
    from agent.core.result import ValidationResult

    check = tool.validate_input(args, ctx)
    assert isinstance(check, ValidationResult)
    assert check.ok is False and frag in (check.message or "")


@pytest.mark.parametrize(
    "args",
    [
        {"action": "create", "goal": "x", "steps": "不是数组"},
        {"action": "create", "goal": "x", "wait": {"kind": "time"}},
    ],
)
def test_call_rejects_malformed_params(tool, ctx, args):
    """过了校验但参数形状不对的，进 call 也要 is_error（不静默当成没等待）。"""
    res = _run(tool, args, ctx)
    assert res.is_error is True


def test_missing_goal_id_rejected(tool, ctx):
    res = _run(tool, {"action": "cancel"}, ctx)
    assert res.is_error is True


# ---- 权限 ----

@pytest.mark.parametrize(
    "action,allow",
    [("create", True), ("pause", True), ("resume", True), ("cancel", True),
     ("advance", True), ("list", True), ("show", True), ("delete", False)],
)
def test_only_delete_needs_confirmation(tool, ctx, action, allow):
    """删除是唯一要人确认的动作：它抹掉的是历史，而其他动作只新增或停下。"""
    from agent.core.result import PermissionBehavior

    result = tool.check_permissions({"action": action}, ctx)
    assert result.behavior is (PermissionBehavior.ALLOW if allow else PermissionBehavior.ASK)


@pytest.mark.parametrize("action,read_only", [("list", True), ("show", True), ("create", False), ("cancel", False)])
def test_read_only_follows_action(tool, action, read_only):
    assert tool.is_read_only({"action": action}) is read_only


def test_never_concurrency_safe(tool):
    """台账是锁内读-改-写，且建完要唤醒驱动器：并行会互相看见半成品。"""
    assert tool.is_concurrency_safe({"action": "create"}) is False


# ---- 正常路径 ----

def test_create_then_list_and_show(tool, ctx, store):
    res = _run(tool, {"action": "create", "goal": "收集纪要; 生成周报"}, ctx)
    assert res.is_error is False
    gid = _id_of(res.data)
    assert store.load(gid).goal == "收集纪要"

    listed = _run(tool, {"action": "list"}, ctx)
    assert "收集纪要" in listed.data and gid[:6] in listed.data

    shown = _run(tool, {"action": "show", "goal_id": gid[:6]}, ctx)
    assert "生成周报" in shown.data and "0/2" in shown.data


def test_return_text_states_driver_absent(tool, ctx, store, monkeypatch):
    """没有驱动器时必须写进返回：模型据此告诉用户"要你先开 [tasks] enabled"。"""
    monkeypatch.setattr(driver_mod, "_driver", None, raising=False)
    res = _run(tool, {"action": "create", "goal": "目标甲"}, ctx)
    assert "驱动线程未在跑" in res.data


def test_return_text_states_driver_running(tool, ctx, store, monkeypatch):
    fake = SimpleNamespace(running=True)
    monkeypatch.setattr(driver_mod, "_driver", fake, raising=False)
    res = _run(tool, {"action": "create", "goal": "目标乙"}, ctx)
    assert "条件满足会自动往下推" in res.data


def test_wait_dict_and_string_give_same_ledger(tool, ctx, store):
    """两种写法都能挂上等待，且落到同一种 spec。"""
    a = _run(tool, {"action": "create", "goal": "目标丙", "wait": {"kind": "file_exists", "path": "C:/t/a.txt"}}, ctx)
    b = _run(tool, {"action": "create", "goal": "目标丁", "wait": "file:C:/t/a.txt"}, ctx)
    assert a.is_error is False and b.is_error is False
    wa = store.load(_id_of(a.data)).steps[0].wait
    wb = store.load(_id_of(b.data)).steps[0].wait
    assert wa.kind == wb.kind == "file_exists"
    assert wa.spec == wb.spec


def test_time_wait_lands_in_the_future(tool, ctx, store):
    gid = _id_of(_run(tool, {"action": "create", "goal": "目标戊", "wait": {"kind": "time", "at": "+2h"}}, ctx).data)
    at = parse_iso(store.load(gid).steps[0].wait.spec["at"])
    assert at > time.time() + 3600 * 1.9


def test_bad_wait_reports_actionable_error(tool, ctx):
    res = _run(tool, {"action": "create", "goal": "目标己", "wait": {"kind": "magic"}}, ctx)
    assert res.is_error is True and "未知等待类型" in res.data


def test_cancel_and_delete_paths(tool, ctx, store):
    """cancel 走放行、返回带结果文案；show 一个不存在 id 要给可读的失败。"""
    gid = _id_of(_run(tool, {"action": "create", "goal": "目标庚"}, ctx).data)
    assert _run(tool, {"action": "cancel", "goal_id": gid}, ctx).is_error is False
    assert store.load(gid).status == GOAL_CANCELLED
    assert _run(tool, {"action": "show", "goal_id": "ff00ff00ff"}, ctx).is_error is True


def test_activity_description(tool):
    assert "目标" in tool.activity_description({"action": "create", "goal": "把周报发出去"})
    assert tool.activity_description({"action": "pause", "goal_id": "abc"}) == "任务脊柱操作：pause abc"
    assert tool.activity_description(None) is None


def test_create_wakes_driver(tool, ctx, store, monkeypatch):
    """建完目标必须敲一次驱动器，否则新目标要等到下一次自然唤醒才能动。"""
    calls: list[str] = []
    monkeypatch.setattr(driver_mod, "wake_driver", lambda reason="": calls.append(reason) or True)
    _run(tool, {"action": "create", "goal": "目标辛"}, ctx)
    assert calls and "目标" in calls[0]
