"""serve 的 ``tasks.exec`` / ``actions.exec`` RPC 测试（W3 阶段 10 收尾切片）。

盯四件事：

1. **一条指令承载多个动词**：动作名写错必须报错，不能静默什么也不做；
2. **传输层信封键**：WS 请求帧是平铺结构，``type``/``id`` 必须在进业务前剥掉
   （``percept.config.set`` 就因为这个真实客户端 100% 必现，见 rpc_percept 头部注释）；
3. **两拍确认**：``undo`` / ``undo_task`` / ``delete`` 未带 ``confirm`` 只回影响面
   预览或 need_confirm，**绝不落手**；``cancel_hold`` 撤的是未执行的预约，不需要确认；
4. **读侧永不抛异常**：账本未启用、台账为空都返回可用快照。

台账单例由 tests/conftest.py 统一指向 tmp（spine 与 journal 都不会碰真实 ~/.jarvis）。

@author aceFelix
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent.config.settings import Settings
from agent.core.actions import STATE_APPLIED, STATE_HELD, EffectRecord, configure_journal
from agent.core.tasks import driver as driver_mod
from agent.core.tasks.spine import configure_spine_store
from agent.serve import protocol, rpc_tasks
from tests.serve.support import make_desktop_server


@pytest.fixture()
def server(tmp_path):
    """一台真服务器 + 指向 tmp 的台账与账本。"""
    spine = configure_spine_store(tmp_path / "goals")
    journal = configure_journal(enabled=True, path=tmp_path / "actions.jsonl", clear_window=True)
    srv, _eq, _cq = make_desktop_server(Settings(), hub=None)
    yield srv, spine, journal
    configure_spine_store(None)


def _tasks(server, **payload):
    return server._rpc_tasks_exec({"type": protocol.CMD_TASKS_EXEC, "id": "req-1", **payload})


def _actions(server, **payload):
    return server._rpc_actions_exec({"type": protocol.CMD_ACTIONS_EXEC, "id": "req-2", **payload})


# ---- 注册与声明 ----

def test_both_commands_declared_and_registered(server):
    """双仓契约：声明在 DESKTOP_COMMANDS 且注册进处理器表。"""
    srv, _spine, _journal = server
    for cmd in (protocol.CMD_TASKS_EXEC, protocol.CMD_ACTIONS_EXEC):
        assert cmd in protocol.DESKTOP_COMMANDS
        assert cmd in srv._ws_handlers
        assert cmd in srv._rpc_types


# ---- tasks.exec ----

def test_tasks_status_empty_ledger(server):
    srv, _spine, _journal = server
    data = _tasks(srv, action="status")
    assert data["ok"] is True and data["count"] == 0
    assert data["driver_running"] is False
    # 面板要能解释"为什么建了不动"，所以配置段必须一起回传
    assert data["tasks_config"]["enabled"] is False


def test_tasks_status_separates_thread_from_switch(server, monkeypatch):
    """``driver_running``（线程真的在跑）与 ``tasks_config.enabled``（配置意图）分开报。

    只给一列就会出现"面板显示已开、实际没线程"的假可用（W2 感知层同一个坑）。
    @author aceFelix
    """
    srv, _spine, _journal = server
    monkeypatch.setattr(driver_mod, "_driver", SimpleNamespace(running=True))
    data = _tasks(srv, action="status")
    assert data["driver_running"] is True
    assert data["tasks_config"]["enabled"] is False


def test_tasks_status_defaults_to_status_action(server):
    """不带 action 就当 status（面板首屏可以只发 {type: tasks.exec}）。"""
    srv, _spine, _journal = server
    assert _tasks(srv)["action"] == "status"


def test_tasks_unknown_action_raises(server):
    srv, _spine, _journal = server
    with pytest.raises(ValueError):
        _tasks(srv, action="frobnicate")


def test_tasks_create_writes_ledger(server):
    srv, spine, _journal = server
    data = _tasks(srv, action="create", goal="收集纪要; 生成周报")
    assert data["ok"] is True and "已建立目标" in data["message"]
    assert len(spine.list_goals(None, limit=10)) == 1


def test_tasks_create_returns_structured_goal(server):
    """回执必带结构化 ``goal``：面板不能靠从 message 文案里截 id。

    没这个字段，建完目标后面板要么重发一次 status（多一个往返），要么去正则抽
    文案里的 id —— 文案一改就碎。
    @author aceFelix
    """
    srv, spine, _journal = server
    data = _tasks(srv, action="create", goal="甲; 乙")
    goal = data["goal"]
    assert goal["id"] and goal["status"] == "active"
    assert len(goal["steps"]) == 2
    assert spine.load(goal["id"]) is not None      # 回传的 id 真能查回来


def test_tasks_create_requires_goal(server):
    srv, _spine, _journal = server
    with pytest.raises(ValueError):
        _tasks(srv, action="create")


def test_tasks_create_accepts_wait_dict_and_string(server, tmp_path):
    srv, spine, _journal = server
    a = _tasks(srv, action="create", goal="甲", wait={"kind": "file_exists", "path": str(tmp_path / "a.txt")})
    b = _tasks(srv, action="create", goal="乙", wait=f"file:{tmp_path / 'b.txt'}")
    assert a["ok"] and b["ok"]
    steps = list(spine.list_goals(None, limit=10))
    assert len(steps) == 2
    assert all(g.steps[0].wait is not None for g in steps)   # 两种写法都挂上了等待


def test_tasks_create_bad_wait_raises(server):
    srv, _spine, _journal = server
    with pytest.raises(ValueError):
        _tasks(srv, action="create", goal="甲", wait={"kind": "magic"})


def test_tasks_show_missing_id_raises(server):
    srv, _spine, _journal = server
    with pytest.raises(ValueError):
        _tasks(srv, action="show")


def test_tasks_show_unknown_id_reports_not_found(server):
    srv, _spine, _journal = server
    data = _tasks(srv, action="show", goal_id="ffffffff")
    assert data["ok"] is False and "没有目标" in data["message"]


def test_tasks_control_pause_and_resume(server):
    srv, spine, _journal = server
    gid = _tasks(srv, action="create", goal="盯一件事")["goal"]["id"]
    assert _tasks(srv, action="pause", goal_id=gid)["ok"] is True
    assert spine.load(gid).status == "paused"
    assert _tasks(srv, action="advance", goal_id=gid)["ok"] is True
    assert spine.load(gid).status == "active"


def test_tasks_control_requires_goal_id(server):
    srv, _spine, _journal = server
    with pytest.raises(ValueError):
        _tasks(srv, action="cancel")


def test_tasks_delete_needs_confirm(server):
    """删除是唯一抹掉历史的动作：未带 confirm 只回 need_confirm。"""
    srv, spine, _journal = server
    gid = _tasks(srv, action="create", goal="待删")["goal"]["id"]
    first = _tasks(srv, action="delete", goal_id=gid)
    assert first["ok"] is False and first["need_confirm"] is True
    assert spine.load(gid) is not None            # 没落手
    assert _tasks(srv, action="delete", goal_id=gid, confirm=True)["ok"] is True
    assert spine.load(gid) is None


def test_tasks_delete_preview_carries_goal_detail(server):
    """两拍确认的第一拍要带目标明细，面板才列得出“要删的是哪个”。

    查不到目标时（id 错/已被删）照实不带，也不假装预览成功。
    @author aceFelix
    """
    srv, _spine, _journal = server
    gid = _tasks(srv, action="create", goal="甲; 乙")["goal"]["id"]
    first = _tasks(srv, action="delete", goal_id=gid)
    assert first["goal"]["id"] == gid and len(first["goal"]["steps"]) == 2
    missing = _tasks(srv, action="delete", goal_id="ffffffff")
    assert missing["need_confirm"] is True and "goal" not in missing


# ---- actions.exec ----

def test_actions_status_shape(server):
    srv, _spine, _journal = server
    data = _actions(srv, action="status")
    assert data["ok"] is True and data["enabled"] is True
    assert set(["counts", "held", "held_labels", "recent", "actions_config"]) <= set(data)
    assert data["counts"]["applied"] == 0


def test_actions_unknown_action_raises(server):
    srv, _spine, _journal = server
    with pytest.raises(ValueError):
        _actions(srv, action="frobnicate")


def test_actions_disabled_journal_explains_instead_of_erroring(server, tmp_path):
    configure_journal(enabled=False, path=tmp_path / "off.jsonl", clear_window=True)
    srv, _spine, _journal = server
    data = _actions(srv, action="list")
    assert data["ok"] is True and "未启用" in data["message"]


def test_actions_cancel_hold_without_held_record(server):
    srv, _spine, _journal = server
    data = _actions(srv, action="cancel_hold")
    assert data["ok"] is False and "没有还在延时窗口里待发的动作" in data["message"]


def test_actions_cancel_hold_cancels_held_record(server):
    """撤回预约不需要确认：它做的是"还没发生的事不做了"。"""
    srv, _spine, journal = server
    rec = EffectRecord(
        tool="SendEmail", preview="发邮件给 a@b.c", state=STATE_HELD,
        args={"to": "a@b.c"}, due_at=time.time() + 30,
    )
    journal.record(rec)
    data = _actions(srv, action="cancel_hold", id=rec.id)
    assert data["ok"] is True and "已撤回" in data["message"]
    assert journal.get(rec.id).state == "cancelled"


def test_actions_undo_requires_confirm(server, tmp_path):
    """整轮回滚必须两拍：先预览、confirm=true 才落手。"""
    srv, _spine, journal = server
    proj = tmp_path / "proj"
    proj.mkdir()
    rec = EffectRecord(
        tool="FileWrite", preview="写 main.py", state=STATE_APPLIED,
        args={"path": str(proj / "main.py")},
        undo_spec={"kind": "checkpoint", "cid": "cid-1", "workdir": str(proj)},
        workdir=str(proj), reversible=True,
    )
    journal.record(rec)
    first = _actions(srv, action="undo", id=rec.id)
    # 检查点在非 git 目录里不可用，如实报"不可用"而不是假装成功
    assert first["ok"] is False
    assert "无法撤销" in first["message"] or "检查点已不可用" in first["message"]


def test_actions_undo_without_records_says_so(server):
    srv, _spine, _journal = server
    data = _actions(srv, action="undo")
    assert data["ok"] is False and "没有可撤销" in data["message"]


def test_actions_undo_task_requires_goal_id(server):
    srv, _spine, _journal = server
    with pytest.raises(ValueError):
        _actions(srv, action="undo_task")


def test_actions_undo_task_preview_then_confirm(server):
    """目标名下没有记录时：预览给 0 项，confirm 后照实说撤了 0 项。"""
    srv, _spine, _journal = server
    preview = _actions(srv, action="undo_task", goal_id="ghost")
    assert preview["ok"] is True and preview["need_confirm"] is True and preview["preview"] == []
    done = _actions(srv, action="undo_task", goal_id="ghost", confirm=True)
    assert done["ok"] is False and "撤销成功 0 项" in done["message"]


def test_actions_list_prefix_id_match(server):
    """面板上的 id 常被截断显示，前缀唯一命中也要能查得到。"""
    srv, _spine, journal = server
    rec = EffectRecord(tool="SendEmail", preview="发邮件", state=STATE_APPLIED, args={})
    journal.record(rec)
    data = _actions(srv, action="list", limit=5)
    assert any(r["id"] == rec.id for r in data["recent"])


def test_envelope_keys_never_leak_into_business_fields(server):
    """带 type/id 的真实 WS 帧不能影响动作判定（回归 percept 那次的坑）。"""
    srv, spine, _journal = server
    _tasks(srv, action="create", goal="信封测试")
    assert len(spine.list_goals(None, limit=10)) == 1
    # 目标文本里没有因为信封键多出怪字段，状态查询也照常
    assert _tasks(srv, action="status")["count"] == 1


# ---- tasks.exec approve（批准重试 = 签一条限定范围的租约 + 退回待执行） ----

def test_tasks_verb_table_contains_approve():
    """动词表是面板与文档共同的口径：漏一条就会静默报“未知动作”。"""
    assert "approve" in rpc_tasks.TASK_ACTIONS


def test_tasks_approve_requires_goal_and_step(server):
    srv, _spine, _journal = server
    with pytest.raises(ValueError):
        _tasks(srv, action="approve", goal_id="g")
    with pytest.raises(ValueError):
        _tasks(srv, action="approve", step_id="s")


def test_tasks_approve_without_driver_is_honest(server):
    """没驱动器时不签租约也不回成功：“批了但没人重试”比“没批”更难解释。"""
    srv, spine, _journal = server
    _tasks(srv, action="create", goal="甲; 乙")
    gid = spine.list_goals(None, limit=1)[0].id
    sid = spine.load(gid).steps[0].id

    data = _tasks(srv, action="approve", goal_id=gid, step_id=sid)

    assert data["ok"] is False and "驱动器没起" in data["message"]
    # 快照字段照常带回，面板不会因为一条没生效的批准就丢掉整屏状态
    assert "goals" in data and "tasks_config" in data


def test_tasks_approve_delegates_to_driver(server, monkeypatch):
    """RPC 只做参数搬运与回执原样上抛：放行范围由驱动器一处定调。"""
    srv, _spine, _journal = server
    seen = {}

    def _approve(goal_id, step_id, *, minutes):
        seen.update(goal_id=goal_id, step_id=step_id, minutes=minutes)
        return True, "已签发租约 [abcd1234]：FileWrite @ D:\\proj，有效期 30 分钟"

    monkeypatch.setattr(driver_mod, "_driver", SimpleNamespace(approve_step=_approve))
    data = _tasks(srv, action="approve", goal_id="g1", step_id="s1", minutes=15)

    assert data["ok"] is True and "abcd1234" in data["message"]
    assert seen == {"goal_id": "g1", "step_id": "s1", "minutes": 15}


def test_tasks_approve_default_minutes_reads_the_constant(server, monkeypatch):
    """不传 minutes 就用 approval_ops 的常量，不在 RPC 层再抄一份数字。"""
    from agent.core.tasks.approval_ops import APPROVAL_LEASE_TTL_MIN

    srv, _spine, _journal = server
    seen = {}

    def _approve(goal_id, step_id, *, minutes):
        seen["minutes"] = minutes
        return False, "这一步没记下被拒的工具名"

    monkeypatch.setattr(driver_mod, "_driver", SimpleNamespace(approve_step=_approve))
    data = _tasks(srv, action="approve", goal_id="g1", step_id="s1")

    assert seen["minutes"] == APPROVAL_LEASE_TTL_MIN
    assert data["ok"] is False and "被拒的工具名" in data["message"]
