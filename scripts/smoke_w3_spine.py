"""W3 兑现层一键实机走查：三个入口 + 两条 RPC + 全部语义口径，不经 LLM、不碰真实 ~/.jarvis。

为什么需要这个脚本（aceFelix，2026-10 阶段 10 收尾切片）：`docs/test/w3-testing.md`
是给人在终端里逐步敲的清单，要开 REPL、要等 90 秒、要配 LLM 凭据；想先确认
"W3 这套东西到底跑不跑"就得有个几秒出结论的东西。本脚本把清单里**不依赖模型**
的部分一次性跑完：

- 入口一：终端 ``/tasks add|show|pause|resume|cancel|advance|delete``（真的命令处理器）
- 入口二：``TrackGoal`` 工具（真的工具类，模型侧走的就是它）
- 入口三：serve ``tasks.exec`` / ``actions.exec``（真起 WS 服务器 + 真 WS 客户端收发）
- 语义口径：等待条件取舍、advance 留痕、定局拒改活、前缀不猜、两拍确认、信封键
- W3.2 延时窗口：真走 ToolOrchestrator，看“不可逆动作被扣住、一次都没执行”，以及
  窗口未结案时等价外发命令被改问人（``hold_blocks_equivalent``）

台账与账本都指向临时目录（``configure_spine_store`` / ``configure_journal``），
所以可以随时跑、跑坏了也不污染用户数据；结束后把单例复位回默认。

工具桩（``_SpyTool`` / ``_AllowChecker``）与本仓 tests 里的同名桩形状一致，但刻意
不引用 tests：打包安装（pip install jarvis）后只有 agent 包与 scripts/，没有 tests 目录。

用法::

    python scripts/smoke_w3_spine.py             # 全跑（含 serve WS 端到端）
    python scripts/smoke_w3_spine.py --no-serve  # 只跑进程内两个入口与语义口径

退出码：0 全部通过；1 有失败项（失败行会打期望与实际，便于直接定位）。

@author aceFelix
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

# 仓库根进 sys.path：脚本既能在仓库里跑，也能从别处 cwd 跑
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bridge import server as bridge_server  # noqa: E402
from agent.commands.handlers.collab_commands import handle_tasks  # noqa: E402
from agent.config.settings import Settings  # noqa: E402
from agent.core.actions import STATE_APPLIED, STATE_CANCELLED, STATE_HELD, EffectRecord, get_window  # noqa: E402
from agent.core.actions import journal as journal_mod  # noqa: E402
from agent.core.context import ToolContext  # noqa: E402
from agent.core.message import ToolUseContent  # noqa: E402
from agent.core.orchestrator import ToolOrchestrator  # noqa: E402
from agent.core.result import PermissionResult, ToolResult  # noqa: E402
from agent.core.result import PermissionBehavior  # noqa: E402
from agent.core.tool import Tool, ToolRegistry  # noqa: E402
from agent.core.tasks import driver as driver_mod  # noqa: E402
from agent.core.tasks.spine import (  # noqa: E402
    GOAL_CANCELLED,
    STEP_BLOCKED,
    configure_spine_store,
    get_spine_store,
)
from agent.core.tasks.spine_ops import (  # noqa: E402
    WAIT_FILE_EXISTS,
    WAIT_TIME,
    create_goal,
    find_goal,
    goal_detail,
    parse_wait,
)
from agent.serve import protocol  # noqa: E402
from agent.serve.server import DesktopBridgeServer  # noqa: E402
from agent.tools.bash import BashTool  # noqa: E402
from agent.tools.track_goal import TrackGoalTool  # noqa: E402
from agent.ui.cli import RichCLI  # noqa: E402
from agent.ui.workbench.api import WorkbenchAPI  # noqa: E402
from agent.ui.workbench.engine import ChatEngine  # noqa: E402

# 控制台可能是 GBK，遇到渲染不了的字符用「?」顶上而不是整个脚本崩掉
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

TOKEN = "smoke-token"


class _Report:
    """走查计数：每项打一行通过/失败，失败时补期望与实际。"""

    def __init__(self) -> None:
        self.total = 0
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        print("\n" + "=" * 68)
        print(title)
        print("=" * 68)

    def show(self, line: str) -> None:
        """把被测代码的真实回显原样打出来（走查要看的就是这些文案）。"""
        for one in str(line).splitlines():
            print(f"    | {one}")

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        """打一行结论；失败时把「期望」补在下一行，方便直接定位而不是回头猜。"""
        self.total += 1
        print(f"  [{'通过' if ok else '失败'}] {name}")
        if not ok:
            self.failures.append(name)
            if detail:
                print(f"        期望：{detail}")
        return ok


def _echo_ui() -> RichCLI:
    """真 UI（Rich 渲染），这样屏幕上的文案与 REPL 里敲 ``/tasks`` 看到的一致。"""
    return RichCLI(verbose=False, boot_animation=False)


def _command_ctx(ui, workdir: str):
    """拼一个 ``handle_tasks`` 需要的最小 CommandContext。

    刻意与 tests/commands/test_tasks_command.py 同形（ui / task_list / settings /
    ctx / session_name 五个字段），但 ui 用真 RichCLI：走查要的是肉眼可见的输出。
    """
    return SimpleNamespace(
        ui=ui,
        task_list=None,
        settings=SimpleNamespace(tasks_wait_deadline_hours=72),
        ctx=SimpleNamespace(workdir=workdir),
        session_name="smoke-w3",
    )


def _tool_ctx(workdir: str):
    """TrackGoal 需要的最小 ToolContext（workdir / session_name / settings）。"""
    return SimpleNamespace(
        workdir=workdir,
        session_name="smoke-w3",
        settings=SimpleNamespace(tasks_wait_deadline_hours=48),
        extra={},
    )


async def _run_command(ui, text: str, workdir: str) -> None:
    ctx = _command_ctx(ui, workdir)
    assert await handle_tasks(ctx, text) is True


def _goal_files(store) -> list[str]:
    return sorted(p.name for p in Path(store.base_dir).glob("*.json"))


# ---------------------------------------------------------------- 进程内两个入口

async def _check_terminal_and_tool(rep: _Report, workdir: str) -> dict[str, str]:
    """入口一（终端）与入口二（模型工具）各建一个目标，返回两个 id。"""
    ui = _echo_ui()
    spine = get_spine_store()

    rep.section("入口一：终端 /tasks add（不经模型）")
    before = _goal_files(spine)
    await _run_command(ui, "/tasks add 冒烟：建文件 w3-smoke.txt; 汇报结果 --wait time:+90s", workdir)
    news = [f for f in _goal_files(spine) if f not in before]
    goals = spine.open_goals(limit=10)
    term_goal = next((g for g in goals if any(g.id in f for f in news)), None)
    rep.check("新目标落盘 ~/.jarvis/goals/（此处为临时目录）", bool(news) and term_goal is not None, "add 之后目录里多一个 json 文件")
    rep.check("拆成 2 个步骤", bool(term_goal) and len(term_goal.steps) == 2, "分号分段")
    rep.check(
        "等待条件挂在第 1 步",
        bool(term_goal) and term_goal.steps[0].wait is not None
        and term_goal.steps[0].wait.kind == WAIT_TIME,
        "wait 只挂 steps[0]：先等再动手是唯一有物理意义的顺序",
    )
    rep.check(
        "time 类不补兜底时限",
        bool(term_goal) and term_goal.steps[0].wait.deadline_at == 0.0,
        "确定时刻到点必走，补 deadline 反而会出现「等到了却报超时」",
    )

    rep.section("入口二：TrackGoal 工具（模型侧同一层）")
    tool = TrackGoalTool()
    res = await tool.call(
        {
            "action": "create",
            "goal": "冒烟：等 w3-never.txt 出现后打印一句话",
            "wait": {"kind": "file_exists", "path": f"{workdir}/w3-never.txt"},
        },
        _tool_ctx(workdir),
    )
    text = str(res.data)
    ui.info(text)
    tool_goal = next((g for g in spine.open_goals(limit=10) if g.goal.startswith("冒烟：等")), None)
    rep.check("工具建目标成功并回一行文案", tool_goal is not None and "已建立目标" in text, "返回文案要能接话")
    rep.check(
        "如实告知驱动线程状态",
        "驱动线程" in text or "没有驱动线程" in text,
        "没有这句，模型会替用户以为已经在盯",
    )
    rep.check(
        "file_exists 类必带兜底时限",
        tool_goal is not None and tool_goal.steps[0].wait.deadline_at > time.time(),
        "等一个永远不来的文件必须超时转 blocked 来问人",
    )

    rep.section("一层语义：两个入口写出同一种台账")
    if term_goal is not None and tool_goal is not None:
        detail_a, detail_b = goal_detail(term_goal), goal_detail(tool_goal)
        rep.check("明细字段集完全一致", set(detail_a) == set(detail_b), repr(sorted(detail_a)))
        rep.check(
            "回显的 id 真能查回来",
            spine.load(term_goal.id) is not None and spine.load(tool_goal.id) is not None,
            "find_goal/load 必须认得入口给的 id",
        )
    return {"term": term_goal.id if term_goal else "", "tool": tool_goal.id if tool_goal else ""}


async def _check_wait_guards(rep: _Report, workdir: str) -> None:
    """坏等待条件绝不落脏数据 + 兜底时限口径（先解析后写盘）。"""
    ui = _echo_ui()
    spine = get_spine_store()

    rep.section("写入前的校验：坏条件不落盘")
    before = _goal_files(spine)
    for bad in ("time:2000-01-01T08:00", "blah", "file:"):
        await _run_command(ui, f"/tasks add 坏条件冒烟 --wait {bad}", workdir)
    rep.check(
        "过去的时刻 / 无前缀 / 缺参数都没建出目标",
        _goal_files(spine) == before,
        "期望目录里一个文件都没多（先解析后写盘，不是先建再回滚）",
    )

    wait_t, err_t = parse_wait("time:+2h", deadline_hours=72)
    wait_f, err_f = parse_wait(f"file:{workdir}/x.txt", deadline_hours=12)
    rep.check("time:+2h 解析通过且不设 deadline", err_t == "" and wait_t.deadline_at == 0.0, repr(err_t))
    rep.check(
        "file: 解析通过并按 deadline 兜底",
        err_f == "" and wait_f.kind == WAIT_FILE_EXISTS
        and 0 < wait_f.deadline_at - time.time() < 12 * 3600 + 5,
        f"实际 deadline 距现在 {(wait_f.deadline_at - time.time()) / 3600:.2f} 小时",
    )


def _check_prefix_rule(rep: _Report) -> None:
    """前缀歧义：命中多个就当没找到，绝不猜一个改。

    真实 id 是随机十六进制，跑一次未必碰得上歧义前缀，所以往**临时台账**里造两个
    同前缀目标把规则钉成确定检查（目录随脚本结束一起删，不碰用户数据）。
    @author aceFelix
    """
    rep.section("前缀歧义：宁可说没找到，也不猜一个改")
    spine = get_spine_store()
    proto = create_goal("歧义冒烟：只用于前缀判定")
    crafted = ("a1b2c3d4e501", "a1b2c3d4e502")
    try:
        if proto.goal is None:
            rep.check("先造一个样板目标", False, proto.message)
            return
        for gid in crafted:
            blob = proto.goal.to_dict()
            blob["id"] = gid
            (Path(spine.base_dir) / f"{gid}.json").write_text(
                json.dumps(blob, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        rep.check(
            "前缀命中两个目标时 find_goal 返回 None",
            find_goal(spine, "a1b2c3d4e5") is None,
            "改错目标比改不了严重得多",
        )
        rep.check(
            "前缀唯一时照旧认（拿回正确那个）",
            (find_goal(spine, "a1b2c3d4e501") or getattr(proto, "goal", None)).id == crafted[0],
            "否则用户每次都得敲完整 12 位",
        )
    finally:
        for gid in crafted:
            spine.delete(gid)


async def _check_lifecycle(rep: _Report, ids: dict[str, str]) -> None:
    """advance 留痕、定局拒改活、前缀不猜、删除真的删。"""
    ui = _echo_ui()
    spine = get_spine_store()
    workdir = ids.get("workdir", "")

    rep.section("advance：不等了，直接推（跳过而不是谎称等到了）")
    term_id = ids["term"]
    await _run_command(ui, f"/tasks advance {term_id}", workdir)
    goal = spine.load(term_id)
    rep.check(
        "等待条件已清空且步骤不再处于 blocked",
        goal is not None and goal.steps[0].wait is None
        and goal.steps[0].state != STEP_BLOCKED,
        "advance 只跳过等待，不改步骤内容",
    )
    rep.check(
        "在 result 里留下「用户以 advance 跳过等待条件」的痕迹",
        bool(goal) and "advance" in goal.steps[0].result,
        f"实际 result：{goal.steps[0].result if goal else '(无目标)'}",
    )

    rep.section("管控与定局：cancel 之后不许改活")
    tool_id = ids["tool"]
    await _run_command(ui, f"/tasks cancel {tool_id}", workdir)
    cancelled = spine.load(tool_id)
    rep.check("cancel 后状态为已取消", cancelled is not None and cancelled.status == GOAL_CANCELLED, "")
    before = json.dumps(cancelled.to_dict() if cancelled else {}, ensure_ascii=False, sort_keys=True)
    await _run_command(ui, f"/tasks resume {tool_id}", workdir)
    after_obj = spine.load(tool_id)
    after = json.dumps(after_obj.to_dict() if after_obj else {}, ensure_ascii=False, sort_keys=True)
    rep.check("对定局目标 resume 被拒且台账一字未动", before == after, "已定局的目标重跑会把发出去的事再发一遍")

    rep.section("delete：台账文件真的消失")
    await _run_command(ui, f"/tasks delete {tool_id}", "")
    rep.check("删除后 load 取不到、目录里也没有", spine.load(tool_id) is None, tool_id)
    _check_prefix_rule(rep)


# ---------------------------------------------------------------- W3.2 延时窗口与绕道硬拦

class _AllowChecker:
    """恒放行的权限桩：这一段只验窗口与硬拦，不重测整条权限管线。"""

    def check(self, tool: Tool, args: dict, ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("冒烟放行")


class _SpyTool(Tool):
    """外发工具桩：记下每一次真实调用，用来证明「被扣住 = 一次都没执行」。"""

    description = "冒烟用的外发工具"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.name = "SendEmail"
        self.calls: list[dict] = []

    async def call(self, args: dict, ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult.ok("已发送")

    def is_irreversible(self, args: dict) -> bool:
        return True

    def describe_irreversible(self, args: dict) -> str:
        return f"给 {args.get('to', '?')} 发一封信"


def _window_settings(**over) -> SimpleNamespace:
    """延时窗口与硬拦要读的字段（其余一律不读，免得搭进无关配置）。"""
    base = {
        "actions_journal_enabled": True,
        "actions_undo_window_s": 30,
        "actions_track_local_writes": True,
        "actions_irreversible_tools": [],
        "actions_hold_blocks_equivalent": True,
        "sandbox_enabled": True,
        "sandbox_auto_allow_medium": True,
        "workdir": ".",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _bash_gate(ctx: ToolContext, cmd: str) -> str:
    """走真 BashTool 权限判定，返回 ALLOW/ASK/DENY（硬拦验的就是这一层）。"""
    return BashTool().check_permissions({"command": cmd}, ctx).behavior.value


async def _check_delay_window(rep: _Report) -> tuple[str, _SpyTool]:
    """不可逆动作进窗口 + 窗口未结案时堵掉捷径（返回待发 id 给 RPC 段撤回）。"""
    rep.section("延时窗口：不可逆动作被扣住（不经模型，真走 ToolOrchestrator）")
    tool = _SpyTool()
    reg = ToolRegistry()
    reg.register(tool)
    orch = ToolOrchestrator(reg, _AllowChecker())
    journal = journal_mod.get_journal()
    outbound = "curl -s https://example.com"

    ctx = ToolContext(workdir=".", messages=[], ui=_echo_ui(),
                      settings=_window_settings(), extra={})
    rep.check(
        "没有待发项时，中风险外发命令照旧自动放行",
        _bash_gate(ctx, outbound) == PermissionBehavior.ALLOW.value,
        "硬拦只在窗口未结案期间生效，不能把会话拖进确认风暴",
    )

    results = await orch.execute_calls(
        [ToolUseContent(id="t-smoke", name="SendEmail", input={"to": "w3-smoke@example.com"})], ctx
    )
    held = journal.held_records()
    rep.check("工具一次都没被执行", tool.calls == [], f"实际调了 {len(tool.calls)} 次")
    rep.check("账本里多一条 held 并冻结了参数", len(held) == 1 and held[0].args.get("to") == "w3-smoke@example.com", repr(held[:2]))
    rep.check(
        "回给模型的 tool_result 不是报错",
        bool(results) and results[0].is_error is False and "待发窗口" in str(results[0].content),
        "历史里已经留下了 tool_use，缺配对 tool_result 会让这个会话后续请求被 API 拒收",
    )
    rep.check(
        "窗口登记表里有展示文案（权限层要解释“为什么突然要确认”）",
        get_window().pending_labels() != [], repr(get_window().pending_labels()),
    )

    rep.section("绕道硬拦：窗口未结案时，等价外发命令不再静默执行")
    rep.check(
        "等价外发命令（curl）降级为问人",
        _bash_gate(ctx, outbound) == PermissionBehavior.ASK.value,
        "沙箱平时对中风险自动放行，窗口未结案期间这条捷径必须临时关掉",
    )
    rep.check(
        "不相干的本机中风险命令不受影响（git commit 照旧放行）",
        _bash_gate(ctx, "git commit -m w3-smoke") == PermissionBehavior.ALLOW.value,
        "拦的只是那一条外发通路，不是整轮会话",
    )
    rep.check(
        "只读命令绝不被窗口拖住（ls -la 照旧放行）",
        _bash_gate(ctx, "ls -la") == PermissionBehavior.ALLOW.value,
        "",
    )
    off_ctx = ToolContext(workdir=".", messages=[], ui=_echo_ui(),
                          settings=_window_settings(actions_hold_blocks_equivalent=False), extra={})
    rep.check(
        "把 hold_blocks_equivalent 改 false 就退回静默执行",
        _bash_gate(off_ctx, outbound) == PermissionBehavior.ALLOW.value,
        "这就是关掉硬拦的代价，看一眼就知道默认开着是对的",
    )
    return (held[0].id if held else ""), tool


# ---------------------------------------------------------------- serve WS 端到端

def _build_server() -> DesktopBridgeServer:
    """装一台真服务器（与 serve/app.py 的 _serve_main 同零件，去掉 hub 与事件泵）。

    tasks.exec / actions.exec 只读 settings 与本机文件，不需要引擎真的在跑对话，
    所以这里构造 ChatEngine 只为满足 WorkbenchAPI 的依赖，不调 engine.start()。
    """
    cfg = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(cfg, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, cfg)
    return DesktopBridgeServer(api, cfg, token=TOKEN)


async def _call(ws, cmd_type: str, **payload) -> dict:
    """发一帧平铺请求，收到该指令的回执（中间的无关事件跳过）。"""
    await ws.send(json.dumps({"type": cmd_type, "id": f"smoke-{cmd_type}", **payload}, ensure_ascii=False))
    deadline = time.time() + 10
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        msg = json.loads(raw)
        data = msg.get("data") or {}
        if msg.get("event") == "reply" and data.get("type") == cmd_type:
            return data
    raise TimeoutError(f"{cmd_type} 回执超时")


async def _check_rpc(rep: _Report, workdir: str, hold_id: str = "", spy: object | None = None) -> str:
    """入口三：真起 WS 服务器、真发帧，验桌面侧拿到的形状与两拍确认。"""
    rep.section("入口三：serve tasks.exec / actions.exec（真实 WS 往返）")
    server = _build_server()
    spine = get_spine_store()
    journal = journal_mod.get_journal()
    goal_id = ""
    try:
        await server.start()
        import websockets

        base = f"ws://127.0.0.1:{server.ws_port}/"
        async with websockets.connect(base + f"?token={TOKEN}") as ws:
            # 每连接首帧是 init 事件（桌面壳首屏数据源），先把它吃掉
            first = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            rep.check("连接后立即收到 init 首帧", first.get("event") == "init", repr(first)[:120])

            status = (await _call(ws, protocol.CMD_TASKS_EXEC, action="status")).get("result") or {}
            rep.check(
                "tasks.exec status 分开报线程与配置",
                "driver_running" in status and status.get("tasks_config", {}).get("enabled") is False,
                "只给一个字段就会出现「面板显示已开、实际没线程」的假可用",
            )

            created = await _call(ws, protocol.CMD_TASKS_EXEC, action="create", goal="RPC 冒烟：甲; 乙")
            result = created.get("result") or {}
            goal = result.get("goal") or {}
            goal_id = str(goal.get("id") or "")
            rep.check(
                "create 回执带结构化 goal（id/status/steps）",
                result.get("ok") is True and bool(goal_id) and len(goal.get("steps") or []) == 2,
                "面板不能靠从 message 文案里截 id：文案一改就碎",
            )
            rep.show(result.get("message", ""))
            rep.check("RPC 建的目标在同一台账里", bool(goal_id) and spine.load(goal_id) is not None, goal_id)

            preview = (await _call(ws, protocol.CMD_TASKS_EXEC, action="delete", goal_id=goal_id)).get("result") or {}
            rep.check(
                "delete 不带 confirm 只回预览 + 要删的是哪个",
                preview.get("need_confirm") is True and (preview.get("goal") or {}).get("id") == goal_id
                and spine.load(goal_id) is not None,
                "第一拍绝不落手",
            )

            # 副作用归属：往账本记一条挂在目标名下的已执行副作用
            rec = EffectRecord(
                tool="FileWrite", preview="写 w3-rpc.txt", task_id=goal_id,
                state=STATE_APPLIED, workdir=workdir, session_id="serve",
            )
            journal.record(rec)
            undo_task = (await _call(ws, protocol.CMD_ACTIONS_EXEC, action="undo_task", goal_id=goal_id)).get("result") or {}
            rep.check(
                "actions.exec undo_task 第一拍给出该目标名下的副作用清单",
                undo_task.get("need_confirm") is True and len(undo_task.get("preview") or []) == 1,
                repr(undo_task)[:200],
            )

            acts = (await _call(ws, protocol.CMD_ACTIONS_EXEC, action="status")).get("result") or {}
            rep.check(
                "actions.exec status 与 /undo list 同源",
                acts.get("enabled") is True and {"counts", "held", "window_size", "recent"} <= set(acts),
                repr(sorted(acts))[:200],
            )

            if hold_id:
                # 面板上的「撤回待发」：一拍生效，而且被扣住的那个动作真的没执行
                cancel = (await _call(ws, protocol.CMD_ACTIONS_EXEC, action="cancel_hold", id=hold_id)).get("result") or {}
                now_state = (journal.get(hold_id).state if journal.get(hold_id) else "")
                rep.check(
                    "actions.exec cancel_hold 一拍生效且工具一次都没执行",
                    cancel.get("ok") is True and now_state == STATE_CANCELLED and not getattr(spy, "calls", []),
                    f"状态实际为 {now_state or '(无记录)'}，工具调用次数 {len(getattr(spy, 'calls', []) or [])}",
                )
                rep.check("撤回后窗口清空", get_window().size == 0, str(get_window().size))

            bad = await _call(ws, protocol.CMD_TASKS_EXEC, action="frobnicate")
            rep.check(
                "未知动作报错误列合法动词、连接不断",
                bad.get("ok") is False and "create" in str(bad.get("error") or ""),
                repr(bad)[:200],
            )

            leaked = (await _call(ws, protocol.CMD_TASKS_EXEC, action="show", goal_id=goal_id)).get("result") or {}
            rep.check(
                "传输层信封键没漏进业务字段",
                "type" not in str(leaked.get("goal") or {}) and leaked.get("goal", {}).get("id") == goal_id,
                "真实客户端发的就是这种平铺帧，单测直传业务 dict 测不出",
            )

            confirmed = (await _call(ws, protocol.CMD_TASKS_EXEC, action="delete", goal_id=goal_id, confirm=True)).get("result") or {}
            rep.check("confirm=true 才真删", confirmed.get("ok") is True and spine.load(goal_id) is None, repr(confirmed)[:160])
            goal_id = ""
    finally:
        await server.stop()
        if goal_id:
            spine.delete(goal_id)
    return ""


# ---------------------------------------------------------------- 驱动与收尾

def _isolate(tmp: Path) -> None:
    """台账/账本/延时窗口/驱动器句柄全部指向临时目录（与 tests/conftest 同口径）。"""
    configure_spine_store(tmp / "goals")
    journal_mod.configure_journal(enabled=True, path=tmp / "actions.jsonl", clear_window=True)
    driver_mod.set_driver(None)


def _release() -> None:
    get_window().clear()
    journal_mod._journal = None
    configure_spine_store(None)
    driver_mod.set_driver(None)


async def run_all(workdir: str, *, with_serve: bool = True) -> _Report:
    """按「入口 → 语义 → RPC」的顺序跑完全部检查。"""
    rep = _Report()
    ids = await _check_terminal_and_tool(rep, workdir)
    ids["workdir"] = workdir
    await _check_wait_guards(rep, workdir)
    await _check_lifecycle(rep, ids)
    hold_id, spy = await _check_delay_window(rep)
    if with_serve:
        await _check_rpc(rep, workdir, hold_id, spy)
    return rep


def main() -> int:
    parser = argparse.ArgumentParser(description="W3 兑现层一键实机走查")
    parser.add_argument("--no-serve", action="store_true", help="跳过 serve WS 端到端（只跑进程内部分）")
    args = parser.parse_args()

    if bridge_server.websockets is None and not args.no_serve:
        print("未安装 websockets，自动跳过 serve 段")
        args.no_serve = True

    tmp = Path(tempfile.mkdtemp(prefix="jarvis-w3-smoke-"))
    workdir = str((tmp / "proj"))
    Path(workdir).mkdir(parents=True, exist_ok=True)
    print(f"临时台账目录：{tmp}")
    _isolate(tmp)
    try:
        rep = asyncio.run(run_all(workdir, with_serve=not args.no_serve))
    finally:
        _release()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 68)
    if rep.failures:
        print(f"结果：{rep.total - len(rep.failures)}/{rep.total} 通过，失败 {len(rep.failures)} 项")
        for name in rep.failures:
            print(f"  - {name}")
        return 1
    print(f"结果：{rep.total}/{rep.total} 全部通过 —— W3 三个入口与两条 RPC 实机可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
