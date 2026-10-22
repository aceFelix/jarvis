"""W3 实机链路自检：到点真的自己把活干了，而且这件事真的可以撤销。

`scripts/smoke_w3_spine.py` 钉住的是"不经模型"的那一半；本脚本补的是另一半——
**只有真 LLM 才能验的那一段**：等待条件到点后，后台驱动器会不会自己醒来、派子代理
把步骤做完、把产生的副作用按目标归属入账，以及那份账能不能真的撤掉。

它做三件事：
1. 起一个真实的 `agent.serve`（子进程），用内存里的 Settings 打开 ``[tasks]``；
2. 经 WebSocket 建一个 ``time:+Ns`` 的目标，然后**什么都不做**，轮询台账看它自己推进；
3. 到点后核对四件事：文件真被写了、账本里真有一条以 ``task_id`` 归属的记录、台账
   ``journal_ids`` 真回填了、``actions.exec undo_task`` 真能把工作区撤回去。

隔离与成本：
- 台账与副作用账本都注入系统临时目录（``configure_spine_store`` /
  ``configure_journal``），**不写用户的 ``~/.jarvis/goals`` 与 ``actions.jsonl``**；
- 凭据、模型、开关全部只存在于子进程的内存 Settings 里，**一个字节都不改用户的
  ``settings.toml``**；
- 任务级 shadow git 会在 ``~/.jarvis/checkpoints`` 下按工作目录指纹建一个仓库，
  脚本结束时按本轮目录名删掉自己建的那一个；
- 会真花一次 LLM 调用（单个步骤，几百 token 量级），所以要凭据。

用法（Git Bash）::

    export DEEPSEEK_API_KEY=...              # 或 --key-env 指定别的变量名
    .venv/bin/python scripts/livecheck_w3.py

    # 换一个模型/端点/等待时长
    python scripts/livecheck_w3.py --model deepseek-chat --wait 60

退出码：0=全部判据通过，1=有判据未通过（逐行打了 ✗），2/3=前置条件不满足。

@author aceFelix
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = str(Path(sys.executable))
RUN_BASE = Path(tempfile.gettempdir()) / "jarvis-w3-live"
GOAL_FILE_NAME = "w3-auto.txt"

#: 判据汇总：每项 (说明, 取值的来源键)
CHECKS: list[tuple[str, str]] = [
    ("serve 里驱动器真的起了线程", "driver_running"),
    ("等待期间状态是 blocked（不烧轮询的那一段）", "saw_blocked"),
    ("到点后自己进入 running（无人干预）", "saw_running"),
    ("播报以 task 源推出来了", "got_task_notify"),
    ("结论与实际产出一致（不谎报）", "honest_verdict"),
    ("步骤真把文件写出来了", "goal_file_exists"),
    ("副作用进了账本（不是假可撤销）", "journal_lines"),
    ("台账 journal_ids 回填了（/undo --task 查得到）", "step_journal_ids"),
    ("undo_task 第一拍只给影响面、不落手", "undo_needs_confirm"),
    ("undo_task 第二拍真撤掉、文件消失", "file_gone_after_undo"),
    ("delete 两拍确认成立", "delete_two_step"),
    ("stdout 握手行之前没有杂质（Electron 逐行解析）", "stdout_clean"),
]


def log(msg: str) -> None:
    """带相对时刻地打一行（同时落 timeline.log，供事后核对）。"""
    line = f"[{time.monotonic() - _T0:7.1f}s] {msg}"
    _TL.append(line)
    print(line, flush=True)


_TL: list[str] = []
_T0 = time.monotonic()


# ------------------------------------------------------------------ 子进程：起 serve

def child(run_dir: Path, args: argparse.Namespace) -> int:
    """内存里拼一个可用配置起 serve：凭据与开关都不落盘，台账钉到临时目录。"""
    sys.path.insert(0, str(REPO))
    from agent.config.settings import Settings
    from agent.core.actions import journal as journal_mod
    from agent.core.tasks.spine import configure_spine_store
    from agent.serve.app import run_serve

    key = os.environ.get(args.key_env, "")
    if not key:
        print(f"缺少凭据环境变量 {args.key_env}", file=sys.stderr)
        return 4
    s = Settings()
    s.api_format = args.provider
    s.provider = args.provider
    s.model = args.model
    s.api_key = key
    s.base_url = args.base_url
    s.workdir = str(run_dir)
    s.tasks_enabled = True
    # 后台步骤默认会写文件被权限拦下（FileWrite 无条件 ASK）。这里配一条**带路径
    # spec** 的 allow 规则把它放行：裸工具名的 allow 放宽不了工具特判的 ASK，这是
    # 权限管线既有的保守设计，不是本轮改动。
    rules_file = run_dir / "permissions.yaml"
    rules_file.write_text(
        'rules:\n  - pattern: "FileWrite(**)"\n    value: allow\n    source: project\n',
        encoding="utf-8",
    )
    s.permissions_file = str(rules_file)
    # 关键隔离：台账与账本都不碰用户的 ~/.jarvis
    configure_spine_store(run_dir / "goals")
    journal_mod.configure_journal(
        enabled=True, path=run_dir / "actions.jsonl", clear_window=True)
    return run_serve(s)


# ------------------------------------------------------------------ WebSocket 观测

def observe(hand: dict, args: argparse.Namespace) -> dict:
    """连上 serve，建目标、等它自己推进、核对账本与撤销。"""
    import asyncio

    import websockets

    v: dict = {"events": []}

    async def main() -> None:
        url = f"ws://127.0.0.1:{hand['port']}/?token={hand['token']}"
        async with websockets.connect(url) as ws:
            rx: "queue.Queue[dict]" = queue.Queue()

            async def pump() -> None:
                """所有帧先入队，call 只挑自己要的那条回执。"""
                while True:
                    try:
                        rx.put(json.loads(await asyncio.wait_for(ws.recv(), 1)))
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        return

            task = asyncio.create_task(pump())

            async def call(cmd: str, **payload) -> dict:
                await ws.send(json.dumps(
                    {"type": cmd, "id": f"lc-{time.monotonic():.4f}", **payload},
                    ensure_ascii=False))
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        d = await asyncio.to_thread(rx.get, True, 1.0)
                    except queue.Empty:
                        continue
                    data = d.get("data") or {}
                    if d.get("event") == "reply" and data.get("type") == cmd:
                        return data.get("result") if isinstance(
                            data.get("result"), dict) else data
                    # 非回执帧留档：播报有没有真往外推，就看这里
                    body = data if isinstance(data, dict) else {}
                    ev = {"event": d.get("event") or "?", "kind": body.get("kind"),
                          "text": str(body.get("text") or "")[:200]}
                    v["events"].append(ev)
                    if ev["event"] not in ("metrics", "usage", "status", "info"):
                        log(f"  ← 事件 {ev['event']} kind={ev['kind']} {ev['text'][:60]}")
                raise TimeoutError(f"{cmd} 回执超时")

            st = await call("tasks.exec", action="status")
            v["driver_running"] = bool(st.get("driver_running"))
            log(f"tasks.exec status: driver_running={v['driver_running']} "
                f"tasks_config={st.get('tasks_config', {}).get('enabled')}")

            step = (f"在工作目录 {args.run_dir} 里创建文件 {GOAL_FILE_NAME}，"
                    f"内容只写一行 SPAWN-OK，不要做别的事")
            c = await call("tasks.exec", action="create",
                           goal=f"到点写一个探针文件：{step}",
                           steps=[step], wait=f"time:+{args.wait}s")
            gid = str((c.get("goal") or {}).get("id", ""))
            v["goal_id"] = gid
            log(f"建目标 id={gid}")

            t0 = time.monotonic()
            seen: set[str] = set()
            logged: set[str] = set()
            while time.monotonic() - t0 < args.wait + args.budget:
                g = (await call("tasks.exec", action="show", goal_id=gid)).get("goal") or {}
                st0 = (g.get("steps") or [{}])[0]
                state = str(st0.get("state"))
                seen.add(state)
                v["step_state"] = state
                v["goal_status"] = g.get("status")
                v["step_journal_ids"] = st0.get("journal_ids") or []
                v["step_last_error"] = st0.get("last_error") or ""
                v["step_result"] = st0.get("result") or ""
                if state not in logged:
                    logged.add(state)
                    log(f"  状态 {state} 目标 {g.get('status')} "
                        f"journal_ids={len(v['step_journal_ids'])}")
                if g.get("status") in ("done", "failed", "cancelled"):
                    break
                await asyncio.sleep(4)

            v["saw_blocked"] = "blocked" in seen
            v["saw_running"] = "running" in seen
            v["got_task_notify"] = any(
                e.get("kind") == "task" for e in v["events"])
            goal_file = Path(args.run_dir) / GOAL_FILE_NAME
            v["goal_file_exists"] = goal_file.exists()
            jf = Path(args.run_dir) / "actions.jsonl"
            v["journal_lines"] = len(jf.read_text(
                encoding="utf-8", errors="replace").splitlines()) if jf.exists() else 0
            # 诚实性判据：报 done 就必须真写了文件并入了账；报受阻就必须确实没写。
            # 两个方向都错不得——谎报"已完成"与把已完成的目标喊成"需要授权"同样坏。
            claimed_done = v.get("goal_status") == "done"
            v["honest_verdict"] = (
                (claimed_done and v["goal_file_exists"] and v["journal_lines"] >= 1)
                or (not claimed_done and not v["goal_file_exists"]
                    and "被拒" in str(v["step_last_error"]))
            )

            u1 = await call("actions.exec", action="undo_task", goal_id=gid)
            u2 = await call("actions.exec", action="undo_task", goal_id=gid, confirm=True)
            v["undo_needs_confirm"] = bool(u1.get("need_confirm"))
            v["undo_message"] = u2.get("message")
            v["file_gone_after_undo"] = not goal_file.exists()
            log(f"撤销：第一拍 need_confirm={v['undo_needs_confirm']} "
                f"第二拍 {v['undo_message']} 文件消失={v['file_gone_after_undo']}")

            p1 = await call("tasks.exec", action="delete", goal_id=gid)
            p2 = await call("tasks.exec", action="delete", goal_id=gid, confirm=True)
            v["delete_two_step"] = bool(p1.get("need_confirm")) and bool(p2.get("ok"))

            task.cancel()

    asyncio.run(main())
    return v


# ------------------------------------------------------------------ 父进程编排

def _reader(stream, sink: "queue.Queue[str]") -> None:
    """把子进程管道逐行搬进队列（Windows 下 readline 不能带超时，故用线程）。"""
    for line in iter(stream.readline, ""):
        sink.put(line.rstrip())
    stream.close()


def wait_handshake(proc: subprocess.Popen, out_q: "queue.Queue[str]",
                   pre: list[str]) -> dict | None:
    """等 stdout 上的单行握手 JSON，并记录在此之前出现过的杂质行。"""
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            line = out_q.get(True, 2.0)
        except queue.Empty:
            if proc.poll() is not None:
                log(f"子进程已退出 code={proc.returncode}")
                return None
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            pre.append(line)
            log(f"  握手前的 stdout 杂质: {line[:100]}")
            continue
        if isinstance(d, dict) and d.get("type") == "jarvis-serve-ready":
            return d
    return None


def passed(key: str, val: object) -> bool:
    """一条判据是否成立：布尔直接看，列表/计数看"有没有东西"。

    单独成函数而不是在报表里堆三元：否则"journal_ids 为空算不算过"这类口径
    会散落在打印循环里，看不清也改不动。
    """
    if isinstance(val, list):
        return len(val) > 0
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return val >= 1
    return bool(val)


def report(v: dict) -> bool:
    """逐行打判据，返回是否全绿。"""
    print("\n==== 判据 ====")
    ok_all = True
    for label, key in CHECKS:
        val = v.get(key)
        hit = passed(key, val)
        ok_all &= hit
        shown = len(val) if isinstance(val, list) else val
        print(f"{'✓' if hit else '✗'} {label}: {shown}")
    for k in ("goal_id", "step_state", "goal_status", "step_last_error",
              "undo_message", "stdout_pre_handshake_lines",
              "checkpoint_store_removed"):
        if k in v:
            print(f"· {k}: {v[k]}")
    return ok_all


def _drain(q: "queue.Queue[str]", limit: int = 40) -> None:
    """把管道里积下的行打出来（子进程起不来时报错一定在 stderr，不能吞掉）。"""
    for _ in range(limit):
        try:
            print(f"  | {q.get_nowait()}", flush=True)
        except queue.Empty:
            return


def parent(args: argparse.Namespace) -> int:
    global _T0
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "goals").mkdir(parents=True, exist_ok=True)
    gf = run_dir / GOAL_FILE_NAME
    if gf.exists():
        gf.unlink()

    _T0 = time.monotonic()
    log(f"本轮运行目录: {run_dir}")
    out_q: "queue.Queue[str]" = queue.Queue()
    pre: list[str] = []
    proc = subprocess.Popen(
        [PY, "-X", "utf8", str(Path(__file__)), "--child", str(run_dir)]
        + sys.argv[1:],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace")
    threading.Thread(target=_reader, args=(proc.stdout, out_q), daemon=True).start()
    err_q: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_reader, args=(proc.stderr, err_q), daemon=True).start()

    hand = wait_handshake(proc, out_q, pre)
    v: dict = {"stdout_pre_handshake_lines": pre,
               "stdout_clean": not pre}
    if hand is None:
        log("拿不到握手 JSON，自检终止（以下是子进程的 stderr）")
        _drain(err_q)
        proc.terminate()
        _dump(run_dir, v)
        return 3
    log(f"握手: port={hand['port']} pid={hand['pid']}")
    args.run_dir = str(run_dir)
    try:
        v.update(observe(hand, args))
    except Exception as e:  # noqa: BLE001 - 自检要把任何异常原样报出来
        log(f"观测异常: {type(e).__name__}: {e}")
        v["error"] = f"{type(e).__name__}: {e}"
        _drain(err_q, 20)   # 驱动器与子代理那边的 traceback 都在 stderr，不能吞
    finally:
        proc.terminate()

    # 本轮在 ~/.jarvis/checkpoints 下留的 shadow 仓库按目录名清掉（只删自己建的）
    ck_root = Path.home() / ".jarvis" / "checkpoints"
    removed: list[str] = []
    if args.clean_checkpoints and ck_root.exists():
        for d in ck_root.iterdir():
            if d.is_dir() and d.name.startswith(run_dir.name + "-"):
                shutil.rmtree(str(d), ignore_errors=True)
                removed.append(d.name)
    v["checkpoint_store_removed"] = removed

    ok = report(v)
    _dump(run_dir, v)
    print(f"\n证据文件: {run_dir / 'verdict.json'}")
    return 0 if ok else 1


def _dump(run_dir: Path, v: dict) -> None:
    """判据与时间线落盘（PowerShell 管道会二次编码，文件才是可信凭证）。"""
    (run_dir / "verdict.json").write_text(
        json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "timeline.log").write_text("\n".join(_TL), encoding="utf-8")


def build_parser() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="W3 到点自动执行链路的实机自检")
    p.add_argument("--provider", default="deepseek", help="api_format（默认 deepseek）")
    p.add_argument("--model", default="deepseek-chat", help="模型名")
    p.add_argument("--base-url", default="https://api.deepseek.com", help="API 端点")
    p.add_argument("--key-env", default="DEEPSEEK_API_KEY", help="凭据所在的环境变量名")
    p.add_argument("--wait", type=int, default=45, help="等待条件秒数")
    p.add_argument("--budget", type=int, default=240, help="到点后给子代理的预算秒数")
    p.add_argument("--run-dir", default=str(RUN_BASE / time.strftime("%Y%m%d-%H%M%S")))
    p.add_argument("--keep-checkpoints", dest="clean_checkpoints",
                   action="store_false", help="保留本轮 shadow git 仓库")
    p.add_argument("--child", dest="child_dir", help=argparse.SUPPRESS)
    p.set_defaults(clean_checkpoints=True)
    return p.parse_args()


if __name__ == "__main__":
    a = build_parser()
    if a.child_dir:
        sys.exit(child(Path(a.child_dir), a))
    sys.exit(parent(a))
