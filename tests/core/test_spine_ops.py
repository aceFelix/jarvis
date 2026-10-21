"""``spine_ops`` 的目标写入与管控测试（W3.1 入口层，阶段 10 收尾切片）。

这一层是终端 `/tasks` 子命令、``TrackGoal`` 工具与 ``tasks.exec`` RPC 三个入口的
共用真源，所以测试盯的是**语义**而不是入口形状：

- 等待条件解析：相对时间 / ISO / 文件出现与消失 / 感知事件，以及必须报错的畸形写法
- 建目标：空目标失败、分号拆步骤、无步骤时补单步、等待挂到第一步
- 管控：pause/resume/cancel/advance/delete 的状态流转；**定局目标拒绝改活**
- 前缀查找：唯一命中才返回，歧义与不存在都当没找到（宁可不猜）
- 快照：driver_running 与台账目录一起带回

@author aceFelix
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent.core.tasks import spine_ops as ops
from agent.core.tasks.spine import (
    GOAL_ACTIVE,
    GOAL_CANCELLED,
    GOAL_PAUSED,
    STEP_PENDING,
    WAIT_FILE_EXISTS,
    WAIT_PERCEPT_EVENT,
    WAIT_TIME,
    configure_spine_store,
)


@pytest.fixture()
def store(tmp_path):
    """台账单例指向临时目录（conftest 已做一次，这里再钉一次防用例互串）。"""
    s = configure_spine_store(tmp_path / "goals")
    yield s
    configure_spine_store(None)


# ---- 步骤拆分 ----

def test_split_steps_accepts_both_semicolons():
    """中英文分号都算分隔符（中文输入法下打出的就是全角）。"""
    assert ops.split_steps("收集纪要；生成周报") == ["收集纪要", "生成周报"]
    assert ops.split_steps("a; b ;c") == ["a", "b", "c"]


def test_split_steps_without_semicolon_is_single_step():
    """没有分号就是单步：绝不能返回空列表，否则驱动器无步可推。"""
    assert ops.split_steps("把周报发出去") == ["把周报发出去"]
    assert ops.split_steps("   ") == []


# ---- 等待条件解析 ----

def test_parse_wait_relative_time():
    """``+90s`` 这类相对写法换算成将来的绝对时刻。"""
    now = time.time()
    wait, err = ops.parse_wait("time:+90s", now=now)
    assert err == ""
    assert wait is not None and wait.kind == WAIT_TIME
    # 台账里存 ISO，这里只校验它落在 90 秒后（±2s 容差）
    from agent.core.tasks.spine import parse_iso

    assert pytest.approx(parse_iso(wait.spec["at"]), abs=2) == now + 90
    # time 类自带确定时刻，不再叠加 deadline（避免"到点之前先超时"的怪状态）
    assert wait.deadline_at == 0.0


def test_parse_wait_iso_time():
    """ISO 写法直接采纳。"""
    future = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 3600))
    wait, err = ops.parse_wait(f"time:{future}")
    assert err == "" and wait is not None and wait.kind == WAIT_TIME


def test_parse_wait_rejects_past_time():
    """过去的时刻必须报错：否则建出来就是一个"已经到点"的目标，用户看不出问题。"""
    wait, err = ops.parse_wait(f"time:{time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() - 60))}")
    assert wait is None and "已过期" in err


def test_parse_wait_file_both_modes_have_deadline():
    """file/gone 两类必须带兜底时限，否则目标会永远挂着等一个不来的文件。"""
    wait, err = ops.parse_wait("file:C:/tmp/a.txt", deadline_hours=3)
    assert err == "" and wait.kind == WAIT_FILE_EXISTS
    assert wait.spec == {"path": "C:/tmp/a.txt", "mode": "exists"}
    assert wait.deadline_at > time.time()

    gone, _ = ops.parse_wait("gone:C:/tmp/a.txt", deadline_hours=3)
    assert gone.spec["mode"] == "gone"


def test_parse_wait_deadline_zero_means_no_limit():
    """``deadline_hours<=0`` 是调用方明确要"不设限"，照实尊重。"""
    wait, err = ops.parse_wait("file:x.txt", deadline_hours=0)
    assert err == "" and wait.deadline_at == 0.0


def test_parse_wait_event_splits_source_and_kind():
    """event 写成 ``source/kind``，kind 可省。"""
    wait, err = ops.parse_wait("event:mail/mail_received")
    assert err == "" and wait.kind == WAIT_PERCEPT_EVENT
    assert wait.spec == {"source": "mail", "kind": "mail_received"}
    bare, _ = ops.parse_wait("event:window")
    assert bare.spec == {"source": "window", "kind": ""}


@pytest.mark.parametrize(
    "raw",
    ["", "no-colon", "time:not-a-time", "file:", "event:", "unknown:C:/x"],
)
def test_parse_wait_bad_inputs(raw):
    """畸形写法一律返回错误说明而不是抛异常（命令层要能把原话回显给用户）。"""
    if not raw:
        wait, err = ops.parse_wait(raw)
        assert wait is None and err == ""     # 空串=没有等待条件，不算错误
        return
    wait, err = ops.parse_wait(raw)
    assert wait is None and err


# ---- wait_from_spec（结构化写法，工具与 RPC 用）----

def test_wait_from_spec_matches_parse_wait_shape():
    """两种写法落出同一种 TaskWait，避免两个入口写出两种台账。"""
    spec, err = ops.wait_from_spec({"kind": "time", "at": "+2h"}, deadline_hours=72)
    assert err == "" and spec.kind == WAIT_TIME
    f, err2 = ops.wait_from_spec({"kind": "file_exists", "path": "a.txt", "mode": "gone"})
    assert err2 == "" and f.spec == {"path": "a.txt", "mode": "gone"}
    e, err3 = ops.wait_from_spec({"kind": "percept_event", "source": "mail"})
    assert err3 == "" and e.spec["source"] == "mail"


@pytest.mark.parametrize(
    "spec,frag",
    [
        ({"kind": "time"}, "必须给 at"),
        ({"kind": "time", "at": "昨天"}, "解析不出来"),
        ({"kind": "file_exists"}, "必须给 path"),
        ({"kind": "file_exists", "path": "a", "mode": "weird"}, "mode 只能是"),
        ({"kind": "percept_event"}, "必须给 source"),
        ({"kind": "magic"}, "未知等待类型"),
    ],
)
def test_wait_from_spec_rejects_bad_specs(spec, frag):
    """缺字段/错枚举都给出可执行的改法。"""
    wait, err = ops.wait_from_spec(spec)
    assert wait is None and frag in err


def test_wait_from_spec_empty_is_not_an_error():
    """None / 空 dict / kind=none 都表示"不等待"。"""
    for spec in (None, {}, {"kind": "none"}):
        wait, err = ops.wait_from_spec(spec)
        assert wait is None and err == ""


# ---- 建目标 ----

def test_create_goal_requires_text(store):
    assert ops.create_goal("  ", store=store).ok is False


def test_create_goal_splits_steps_and_uses_first_as_goal_text(store):
    """分号串：目标名取第一段，步骤取全部段。"""
    res = ops.create_goal("收集纪要; 生成周报; 发送", store=store)
    assert res.ok and res.goal is not None
    goal = store.load(res.goal.id)
    assert goal.goal == "收集纪要"
    assert [s.desc for s in goal.steps] == ["收集纪要", "生成周报", "发送"]


def test_create_goal_without_steps_falls_back_to_single_step(store):
    res = ops.create_goal("把周报发出去", store=store)
    goal = store.load(res.goal.id)
    assert len(goal.steps) == 1 and goal.steps[0].desc == "把周报发出去"


def test_create_goal_explicit_steps_win(store):
    res = ops.create_goal("发周报", steps=["起草", "发送"], store=store)
    goal = store.load(res.goal.id)
    assert [s.desc for s in goal.steps] == ["起草", "发送"]


def test_create_goal_attaches_wait_to_first_step_only(store):
    """等待挂第一步：挂到后面的步骤上会"看起来没生效"。"""
    wait, _ = ops.parse_wait("file:C:/tmp/x.txt")
    res = ops.create_goal("a; b", wait=wait, store=store)
    goal = store.load(res.goal.id)
    assert goal.steps[0].wait is not None
    assert goal.steps[1].wait is None
    assert "先等 file_exists" in res.message


def test_create_goal_records_workdir_and_session(store):
    res = ops.create_goal("x", workdir="C:/proj", session_id="s1", store=store)
    goal = store.load(res.goal.id)
    assert goal.workdir == "C:/proj" and goal.session_id == "s1"


def test_create_goal_survives_without_driver(store):
    """没有装配驱动器时唤醒失败也不影响台账写入（_wake 吞异常）。"""
    res = ops.create_goal("没有驱动器也要建得出来", store=store)
    assert res.ok and store.load(res.goal.id) is not None


# ---- 管控 ----

def test_control_goal_pause_resume(store):
    res = ops.create_goal("目标A", store=store)
    gid = res.goal.id
    assert ops.control_goal(gid, "pause", store=store).ok
    assert store.load(gid).status == GOAL_PAUSED
    assert ops.control_goal(gid, "resume", store=store).ok
    assert store.load(gid).status == GOAL_ACTIVE


def test_control_goal_cancel_keeps_wait_for_review(store):
    """取消不清 wait：保留现场才能复盘"当初在等什么"。"""
    wait, _ = ops.parse_wait("file:C:/tmp/y.txt")
    gid = ops.create_goal("目标B", wait=wait, store=store).goal.id
    assert ops.control_goal(gid, "cancel", store=store).ok
    goal = store.load(gid)
    assert goal.status == GOAL_CANCELLED
    assert goal.steps[0].wait is not None


def test_control_goal_terminal_refuses_reactivation(store):
    """定局目标不能被 pause/resume/advance 改活（重跑已做完的事最坏是重复外发）。"""
    gid = ops.create_goal("目标C", store=store).goal.id
    ops.control_goal(gid, "cancel", store=store)
    for action in ("pause", "resume", "advance"):
        res = ops.control_goal(gid, action, store=store)
        assert res.ok is False and "已定局" in res.message
    # delete 仍允许：终态目标也该能被清理掉
    assert ops.control_goal(gid, "delete", store=store).ok
    assert store.load(gid) is None


def test_control_goal_advance_clears_wait_and_notes_skip(store):
    """advance 只跳等待、不改步骤内容，并在 result 里留下"跳过"的痕迹。"""
    wait, _ = ops.parse_wait("file:C:/tmp/z.txt")
    gid = ops.create_goal("目标D", wait=wait, store=store).goal.id
    assert ops.control_goal(gid, "advance", store=store).ok
    step = store.load(gid).steps[0]
    assert step.wait is None
    assert step.state == STEP_PENDING
    assert step.next_run_at == 0.0
    assert "advance" in step.result


def test_control_goal_advance_without_driver_says_no_one_will_run(store, monkeypatch):
    """没人推的时候必须跟着说一句：否则用户敲完 advance 会盯屏幕等一个不来的结果。"""
    from agent.core.tasks import driver as driver_mod

    monkeypatch.setattr(driver_mod, "_driver", None)
    gid = ops.create_goal("目标无驱动", store=store).goal.id
    res = ops.control_goal(gid, "advance", store=store)
    assert res.ok is True and "没有驱动线程" in res.message


def test_control_goal_advance_with_driver_omits_the_warning(store, monkeypatch):
    from agent.core.tasks import driver as driver_mod

    monkeypatch.setattr(driver_mod, "_driver", SimpleNamespace(running=True, wake=lambda reason="": None))
    gid = ops.create_goal("目标有驱动", store=store).goal.id
    res = ops.control_goal(gid, "advance", store=store)
    assert "没有驱动线程" not in res.message


def test_control_goal_unknown_action(store):
    res = ops.control_goal("whatever", "frob", store=store)
    assert res.ok is False and "未知操作" in res.message


def test_control_goal_missing_id(store):
    res = ops.control_goal("deadbeef", "pause", store=store)
    assert res.ok is False and "没有 id 以" in res.message


# ---- 前缀查找 ----

def test_find_goal_by_full_id_and_prefix(store):
    gid = ops.create_goal("目标E", store=store).goal.id
    assert ops.find_goal(store, gid).id == gid
    assert ops.find_goal(store, gid[:6]).id == gid


def test_find_goal_empty_and_absent(store):
    assert ops.find_goal(store, "") is None
    assert ops.find_goal(store, "ffffffffffff") is None


def test_find_goal_ambiguous_prefix_returns_none(store):
    """两个目标共享前缀时宁可不猜：挑错一个去 cancel/delete 的代价比"没找到"高。"""
    from agent.core.tasks.spine import TaskGoal

    store.save(TaskGoal(goal="甲", id="aaaa1111"))
    store.save(TaskGoal(goal="乙", id="aaaa2222"))
    assert ops.find_goal(store, "aaaa") is None
    assert ops.find_goal(store, "aaaa1111").goal == "甲"


# ---- 只读视图 ----

def test_goal_detail_shape(store):
    wait, _ = ops.parse_wait("event:mail")
    gid = ops.create_goal("目标F; 第二步", wait=wait, store=store).goal.id
    detail = ops.goal_detail(store.load(gid))
    assert detail["id"] == gid
    assert detail["progress"] == "0/2"
    assert detail["current_step"]["wait"].startswith("percept_event")
    assert len(detail["steps"]) == 2


def test_snapshot_reports_driver_absent_and_base_dir(store):
    ops.create_goal("目标G", store=store)
    data = ops.snapshot(store=store)
    assert data["count"] == 1
    assert data["driver_running"] is False      # 没有装配驱动器就是没在跑
    assert data["base_dir"].endswith("goals")
    assert data["goals"][0]["summary"].startswith("目标G")


def test_wait_readable_covers_three_kinds():
    w, _ = ops.parse_wait("time:+1h")
    assert "time" in ops.wait_readable(w)
    f, _ = ops.parse_wait("gone:C:/tmp/t.txt")
    assert "消失" in ops.wait_readable(f)
    e, _ = ops.parse_wait("event:vcs/commit")
    assert "vcs/commit" in ops.wait_readable(e)
    assert ops.wait_readable(None) == ""


def test_describe_steps_lists_every_step(store):
    gid = ops.create_goal("甲; 乙", store=store).goal.id
    lines = ops.describe_steps(store.load(gid))
    assert len(lines) == 2
    assert lines[0].strip().startswith("1.")


def test_op_result_to_dict_is_serializable(store):
    """RPC 直接把结果 JSON 回传，这里确认没有不可序列化对象混进去。"""
    import json

    res = ops.create_goal("目标H", store=store)
    assert json.loads(json.dumps(res.to_dict(), ensure_ascii=False))["ok"] is True
