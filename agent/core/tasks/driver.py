"""W3.1 任务脊柱驱动器 —— 让目标在条件满足后自己往前挪一步。

设计要点（都在守"敢放手"与"不烧钱"这两条线）：

1. **不轮询调 LLM**：后台只有一条线程，平时阻塞在 ``Event.wait(下一次唤醒秒数)`` 上。
   唤醒时刻由条件本身算出来（``time`` 等到点、``file_exists`` 按廉价 stat 复查节拍、
   ``percept_event`` 由总线订阅回调即时唤醒）。等待期间一次 LLM 都不调，这是验收项
   "条件等待命中前不重复调 LLM"的实现口径。
2. **一次只跑一步**：后台自主执行不该并发烧钱，也不该同时改工作区两处；跑完一步
   再评估下一个目标。
3. **推进交给 Subagent**：复用 ``collaboration/subagent.run_subagent``（独立 QueryLoop、
   受限工具集、不写主会话转录），因此**不抢桌面/手机的 query 锁**，用户正在对话也
   不打断。权限模式刻意用 ``default`` 而非子代理默认的 ``yolo``：无人值守时不该
   默认放行任何需确认的操作；无 UI 时 ASK 会被管线 fail-closed 拒成 DENY，驱动器把
   这种情况如实转成"需要授权"并来问用户。
4. **播报只走 W1 闸门**：结果经宿主注入的 ``notifier`` 投递，宿主内部必须先过
   ``proactive.apply_notify_gate``（静默时段/日额度/重要度，与简报、感知事件同一份
   实现），驱动器自己不 print、不 TTS。
5. **崩溃安全**：进程死在半路时步骤会停在 ``running``；``resume()`` 把它复位成
   ``pending`` 并计一次失败，避免"永远假装在跑"。

本期不做（写清楚免得被当成已有能力）：
- token/费用归集：``SubagentResult`` 不回报 usage，``TaskGoal.budget`` 只是留位；
- 多机共享台账：存储是本机文件，跨进程锁只保护同一台机器上的多个 jarvis 进程。

@author aceFelix
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from agent.core.tasks.approval_ops import ApprovalOpsMixin
from agent.core.tasks.spawn import (
    StepOutcome,
    default_spawn,
    default_step_definition,
)

from agent.core.tasks.spine import (
    BLOCK_APPROVAL,
    BLOCK_ERROR,
    BLOCK_TIMEOUT,
    BLOCK_WAIT,
    GOAL_CANCELLED,
    GOAL_DONE,
    GOAL_FAILED,
    GOAL_PAUSED,
    STEP_BLOCKED,
    STEP_DONE,
    STEP_FAILED,
    STEP_PENDING,
    STEP_RUNNING,
    TaskGoal,
    TaskStep,
    TaskWait,
    WAIT_FILE_EXISTS,
    WAIT_PERCEPT_EVENT,
    WAIT_TIME,
    SpineStore,
    get_spine_store,
    parse_iso,
)

logger = logging.getLogger("jarvis.tasks")

#: 廉价复查节拍：``file_exists`` 这类等待靠 stat 判定，60 秒一次既不吃 CPU，
#: 也不会有"文件出现了却一小时后才发现"的失准。
FILE_CHECK_INTERVAL_S = 60.0

#: 两次唤醒的最小间隔，防止到期时刻密集时热循环
_MIN_WAKE_GAP_S = 0.5

#: 播报源名（必须在 ``policy/budget.py`` 的权重表里显式登记，否则会落 default
#: 被误裁——同 notify-budget-percept-relay-fix.md 的教训）
NOTIFY_SOURCE = "task"

#: 脊柱播报的紧迫度档位：后台目标“做完一步/卡住了”不是突发紧事，给中低档；
#: 但 ``task`` 权重已在预算表里拉到与 ``schedule`` 同级，乘算后仍在 min_salience
#: 之上，不会出现“目标静默失败”。
GATE_URGENCY = 0.45


class TaskDriver(ApprovalOpsMixin):
    """任务脊柱的后台驱动器（一条线程 + 到期唤醒 + 总线订阅）。

    阻塞标记、询问投递与授权答复三件事由 :class:`ApprovalOpsMixin` 提供
    （变化原因不同：一个是“什么时候醒”，一个是“卡住之后怎么跟用户打交道”）。

    用法::

        driver = build_driver(settings, notifier=make_gate_notifier(emit))
        driver.resume()          # 复位上次遗留的 running 步骤
        driver.start()           # 起线程（幂等）
        ...
        driver.stop()

    @author aceFelix
    """

    def __init__(
        self,
        *,
        settings: Any = None,
        provider: Any = None,
        store: SpineStore | None = None,
        notifier: Callable[[str, str, str], None] | None = None,
        spawn: Callable[[Any, str], StepOutcome] | None = None,
        workdir: str = "",
        session_name: str = "",
        bus_getter: Callable[[], Any] | None = None,
    ) -> None:
        """
        Args:
            settings: 全局配置（读 ``[tasks]`` 字段）。
            provider: LLM provider；None 时不自动推进（台账仍可读）。
            store: 台账存储；None 用全局单例。
            notifier: ``(source, text, dedup_key)`` 播报回调，宿主内部必须先过 W1 闸门。
            spawn: 步骤执行函数（注入用，单测据此免调 LLM）；None 走真实子代理。
            workdir: 步骤执行的工作目录（缺省取 settings.workdir）。
            session_name: 归属会话名（写进副作用账本，供 /undo --task 关联）。
            bus_getter: 取当前感知总线的回调；None 时惰性取 ``percept.get_bus``。
        """
        self._settings = settings
        self._provider = provider
        self._store = store or get_spine_store()
        self._notifier = notifier
        self._spawn = spawn
        # 注入通路能不能接任务归属参数（按签名定一次，不每次试错）
        self._spawn_takes_task: bool | None = None
        self._workdir = workdir or str(getattr(settings, "workdir", "") or "")
        self._session_name = session_name
        self._bus_getter = bus_getter

        # 配置：缺省值取向是"少动手、少花钱"
        self._enabled = bool(getattr(settings, "tasks_enabled", False))
        self._max_attempts = max(1, int(getattr(settings, "tasks_step_max_attempts", 3) or 3))
        self._backoff_s = max(1, int(getattr(settings, "tasks_retry_backoff_s", 60) or 60))
        self._ask_days = max(0, int(getattr(settings, "tasks_blocked_ask_days", 3) or 3))
        self._deadline_hours = max(1, int(getattr(settings, "tasks_wait_deadline_hours", 72) or 72))

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._running = False
        self._busy = False            # 一步只跑一步的闸门
        self._unsub_bus: Callable[[], None] | None = None
        self._next_file_check_at = 0.0

    # ---- 可用性 ----

    @property
    def enabled(self) -> bool:
        """是否具备自动推进条件：开关开 + 真能拿到执行体。

        “能拿到”指三种之一：注入了 ``spawn``（测试/宿主自定义编排）、宿主传了
        ``provider``、或 settings 里的 api_format/api_key 足以现建一个 provider。
        最后一种不在启动时就建：驱动器可能在 serve 里长期后台，未启用脊柱时
        不应为它拉一个 LLM 客户端。
        @author aceFelix
        """
        return self._enabled and self._can_execute()

    @property
    def running(self) -> bool:
        return self._running

    def _can_execute(self) -> bool:
        """不构造客户端就能判定的“能不能跑 LLM”（只看配置完整性）。"""
        if self._spawn is not None or self._provider is not None:
            return True
        if self._settings is None:
            return False
        try:
            from agent.llm.provider_registry import PROVIDER_REGISTRY

            fmt = str(getattr(self._settings, "api_format", "") or "").strip().lower()
            return bool(fmt) and fmt in PROVIDER_REGISTRY and bool(
                getattr(self._settings, "api_key", "")
            )
        except Exception:  # noqa: BLE001 - 注册表都读不到就只能不跑
            return False

    def _resolve_provider(self) -> Any:
        """取执行用 provider：宿主给的优先，否则按配置现建并缓存。"""
        if self._provider is not None:
            return self._provider
        from agent.bootstrap import _build_provider, _model_type_for

        self._provider = _build_provider(self._settings, model_type=_model_type_for(self._settings))
        return self._provider

    # ---- 生命周期 ----

    def start(self) -> bool:
        """启动驱动线程。禁用态/无 provider 时返回 False 且**不起线程**（口径同 W2）。"""
        if self._running:
            return True
        if not self.enabled:
            logger.info("任务脊柱未启用或缺少 provider，本次不起驱动线程")
            return False
        self._stop.clear()
        self._running = True
        self._subscribe_bus()
        self._thread = threading.Thread(target=self._loop, name="jarvis-task-driver", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """停止驱动线程并退订总线（幂等）。"""
        self._stop.set()
        self._wake.set()          # 让阻塞中的 wait 立刻返回
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._running = False
        if self._unsub_bus is not None:
            try:
                self._unsub_bus()
            except Exception:
                pass
            self._unsub_bus = None

    def wake(self, reason: str = "") -> None:
        """请驱动器立刻复查一次（新建目标、用户回复、外部事件都用它）。"""
        if reason:
            logger.debug("任务脊柱唤醒：%s", reason)
        self._wake.set()

    def resume(self) -> int:
        """复位上次进程遗留的 ``running`` 步骤，返回复位条数。

        进程被杀时步骤可能停在 running，重启后若不处理，驱动器会以为"还在跑"而永不
        再推进——台账就变成一份假的活字典。复位成 pending 并计入一次失败，让它要么
        重试、要么按重试上限转成需要人来管。

        为什么在锁内的新对象上按 id 重找一遍而不是改 ``open_goals()`` 读到的那些：
        ``store.update`` 会重新加载一份目标，改旧对象的字段不会落盘（上一版就坑在这：
        表面上返回了条数、实际上台账里的 running 一条都没动）。
        @author aceFelix
        """
        n = 0
        for goal in self._store.open_goals(limit=200):
            stale_ids = {s.id for s in goal.steps if s.state == STEP_RUNNING}
            if not stale_ids:
                continue

            counted = [0]     # 闭包里改列表元素，不往目标对象上挂临时字段

            def _fix(g: TaskGoal, _ids: set[str] = stale_ids, _c=counted) -> None:
                for s in g.steps:
                    if s.id in _ids and s.state == STEP_RUNNING:
                        s.state = STEP_PENDING
                        s.attempts += 1
                        s.block_reason = BLOCK_ERROR
                        s.last_error = "进程重启前该步骤未跑完，已复位重试"
                        _c[0] += 1

            if self._store.update(goal.id, _fix) is not None:
                n += counted[0]
        if n:
            logger.info("任务脊柱复位 %d 个中断步骤", n)
        return n

    # ---- 主循环 ----

    def _loop(self) -> None:
        """线程主体：阻塞到下一次唤醒 → 复查 → 再阻塞。

        永不 ``sleep(1)`` 轮询：等待时长由 ``_next_wait()`` 按条件算出来，
        期间被 ``Event.wait`` 挂住，CPU 增量接近 0（总线订阅或用户操作可提前唤醒）。
        @author aceFelix
        """
        while not self._stop.is_set():
            try:
                waited = self._tick()
            except Exception as e:  # noqa: BLE001 - 单轮异常不该终结驱动线程
                logger.warning("任务脊柱一轮复查异常: %s", e)
                waited = self._backoff_s
            self._wake.wait(timeout=max(_MIN_WAKE_GAP_S, min(waited, 3600.0)))
            self._wake.clear()

    def _tick(self) -> float:
        """复查所有活跃目标，推进就绪步骤。

        Returns:
            距离下一次该醒来的秒数（无目标时给一个长值，靠 wake()/总线事件提前唤）。
        @author aceFelix
        """
        if self._busy:
            return _MIN_WAKE_GAP_S
        now = time.time()
        next_wait = 3600.0
        goals = self._store.open_goals(limit=50)
        if not goals:
            return next_wait
        for goal in goals:
            if goal.status == GOAL_PAUSED:
                continue
            wait_s = self._evaluate(goal, now)
            next_wait = min(next_wait, wait_s)
        # file_exists 类等待靠节拍复查：把它并进下次唤醒时间
        if self._next_file_check_at:
            next_wait = min(next_wait, max(0.0, self._next_file_check_at - now))
        return next_wait

    def _evaluate(self, goal: TaskGoal, now: float) -> float:
        """评估一个目标的当前步骤。返回该目标下次该醒的秒数。

        三条出路：条件满足→执行；条件未到→给出唤醒时刻；等待超时→阻塞并问人。
        @author aceFelix
        """
        step = goal.current_step()
        if step is None:
            # 没有可做的步骤：可能全做完了，也可能一步没建（用户只写了目标）
            self._store.update(goal.id, lambda g: g.refresh_status())
            return 3600.0

        # 失败退避未到点：一次 LLM 都不调，只把唤醒时刻交回上层
        if step.next_run_at and now < step.next_run_at:
            return step.next_run_at - now

        if step.wait is not None:
            satisfied, note = step.wait.check(now)
            if satisfied:
                step.result = (step.result + f"\n{note}").strip()[:400]
                step.wait = None
                step.block_reason = ""
                step.state = STEP_PENDING
                self._store.save(goal)
                return 0.0
            if step.wait.expired(now):
                self._block(goal, step, BLOCK_TIMEOUT, note or "等待已超时")
                return 3600.0
            if step.wait.kind == WAIT_FILE_EXISTS:
                self._next_file_check_at = now + FILE_CHECK_INTERVAL_S
            if step.state != STEP_BLOCKED:
                self._block(goal, step, BLOCK_WAIT, note)
            return self._wake_seconds(step, now)

        # 无等待条件：直接跑这一步（一次只跑一步，跑完立刻返回让上层重算节拍）
        self._run_step(goal, step)
        return 0.0

    def _wake_seconds(self, step: TaskStep, now: float) -> float:
        """按条件算出下次该醒的秒数（这就是"不轮询"的具体实现）。"""
        wait = step.wait
        if wait is None:
            return 3600.0
        if wait.kind == WAIT_TIME:
            # ``spec["at"]`` 存 ISO 文本，每次比较都重解一次：一次 float 解析的代价
            # 远低于在台账里多养一个冗余时间字段还可能与它不一致的风险
            at = parse_iso(wait.spec.get("at"))
            cand = [x for x in (at, wait.deadline_at) if x]
            return max(_MIN_WAKE_GAP_S, min(cand) - now) if cand else 3600.0
        if wait.kind == WAIT_FILE_EXISTS:
            cand = [wait.deadline_at - now] if wait.deadline_at else []
            return max(_MIN_WAKE_GAP_S, min(FILE_CHECK_INTERVAL_S, *(cand or [FILE_CHECK_INTERVAL_S])))
        # percept_event：靠订阅回调唤醒，只需要在 deadline 处收尾
        if wait.deadline_at:
            return max(_MIN_WAKE_GAP_S, wait.deadline_at - now)
        return 3600.0

    # ---- 步骤执行 ----

    def _run_step(self, goal: TaskGoal, step: TaskStep) -> None:
        """执行一步（同步跑在当前线程内，期间置 ``_busy`` 防止并发推进）。"""
        self._busy = True
        try:
            goal.updated_at = time.time()
            step.state = STEP_RUNNING
            step.attempts += 1
            step.started_at = time.time()
            step.block_reason = ""
            self._mark_step(goal, step)

            prompt = self._build_prompt(goal, step)
            # 本步产生的账本 id 回扣容器：必须是同一个列表对象跑完再读，因此
            # 由驱动器创建并往下传（编排器往里 append）
            effect_ids: list[str] = []
            outcome = self._dispatch(goal, step, prompt, effect_ids)
        finally:
            self._busy = False

        now = time.time()
        step.finished_at = now
        if effect_ids:
            # 回填副作用归属（/undo --task 靠它）；去重且保留已有项：重试同一
            # 步时上一次的记录不能被新一次覆盖掉
            step.journal_ids = list(dict.fromkeys(list(step.journal_ids) + effect_ids))
        if outcome.denials and not effect_ids:
            # 受阻：本步有权限拒绝、且一个副作用都没产出——真的是一件也没做成。
            # 绝不能报成已完成（2026-10-22 实机：子代理如实汇报被拦，旧代码凭
            # success=True 把步骤定成 done 并向用户播报“已完成”）。
            d0 = outcome.denials[0]
            step.result = (outcome.report or step.result)[:400]
            step.last_error = (
                f"{d0.get('tool', '?')} 被拒：{d0.get('reason', '')}"
                + (f"（另有 {len(outcome.denials) - 1} 项）" if len(outcome.denials) > 1 else "")
            )[:200]
            step.next_run_at = self.blocked_hold()
            # 接回已写好却从来没人调的那条询问通路（BLOCK_APPROVAL → _ask →
            # request_approval）；mark=False 让落盘统一走下面的收尾
            self._block(
                goal, step, BLOCK_APPROVAL,
                f"需要授权：{step.last_error}。批准后这一步会立刻重试。",
                mark=False,
            )
        elif outcome.success:
            step.state = STEP_DONE
            step.result = (outcome.report or "已完成")[:400]
            if outcome.denials:
                # 已有产出就不算卡住（否则会对着一个干完了的目标说“需要授权”），
                # 但被拦下的那几次必须如实附在结论里：隐去它就是拿“看起来成功”
                # 换掉真实历史（2026-10-22 第二轮实机：FileWrite 已写成并入账，
                # 随后的 Bash 确认被拒，当时被误判成整步卡住）。
                step.result = (
                    step.result
                    + f"\n本步另有 {len(outcome.denials)} 次操作被权限拦下："
                    + "、".join(str(d.get("tool", "?")) for d in outcome.denials[:3])
                )[:400]
            step.last_error = ""
            step.asked = False
            step.next_run_at = 0.0
        else:
            step.last_error = (outcome.error or "执行失败")[:200]
            if step.attempts >= self._max_attempts:
                # 定局成 failed 而不是 blocked：``open`` 把 blocked 算“仍需推进”，
                # 用 blocked 做终态会让驱动器下一轮又跑一次（无退避的死循环重试，
                # 是“不烧轮询”这条验收最隐蔽的反例）
                step.state = STEP_FAILED
                step.block_reason = BLOCK_ERROR
                step.next_run_at = 0.0
                goal.blocked_since = goal.blocked_since or now
                self._ask(goal, step, f"重试 {step.attempts} 次仍失败：{step.last_error}")
            else:
                # 未达上限：退回 pending，并把退避时刻算进步骤（重启也不丢）
                step.state = STEP_PENDING
                step.block_reason = BLOCK_WAIT
                step.next_run_at = now + self._backoff_s
                goal.blocked_since = now
        self._mark_step(goal, step)
        status = goal.refresh_status()
        self._store.save(goal)
        # 只在目标定局时报一句（进行中不报，否则每个步骤都打扰一次）；
        # 状态常量而不是字面量：新增一个定局状态时这里会跟着编译期可查
        if status in (GOAL_DONE, GOAL_FAILED, GOAL_CANCELLED):
            done, total = goal.progress()
            verb = "完成" if status == GOAL_DONE else "停在失败"
            self._notify(
                f"目标「{goal.goal[:24]}」已{verb}：{done}/{total} 步",
                dedup_key=f"goal:{goal.id}:{status}",
            )

    def _dispatch(
        self, goal: TaskGoal, step: TaskStep, prompt: str, effect_ids: list[str]
    ) -> StepOutcome:
        """调用执行通路（注入的 ``spawn`` 优先，便于测试与宿主自定义编排）。

        任务归属只在通路肯接的时候才接：注入的 spawn 可能是宿主写的旧式
        ``(definition, prompt)`` 两参函数，硬塞关键字参数会直接 TypeError——宁可
        少记一笔归属，不能因为接线把整步打挂。
        @author aceFelix
        """
        try:
            definition = default_step_definition()
            if self._spawn is not None:
                if self._spawn_accepts_task_kwargs():
                    return self._spawn(
                        definition, prompt,
                        task_id=goal.id, session_name=self._session_name,
                        effect_ids=effect_ids,
                    )
                return self._spawn(definition, prompt)
            return default_spawn(
                definition, prompt,
                provider=self._resolve_provider(), workdir=self._workdir_for(goal),
                task_id=goal.id, session_name=self._session_name, effect_ids=effect_ids,
                settings=self._settings,
            )
        except Exception as e:  # noqa: BLE001 - 子代理整体异常也要归一成失败结果
            logger.warning("步骤执行异常(goal=%s step=%s): %s", goal.id, step.id, e)
            return StepOutcome(success=False, error=f"{type(e).__name__}: {e}")

    def _spawn_accepts_task_kwargs(self) -> bool:
        """注入的 ``spawn`` 是否接受任务归属关键字（结果缓存在 ``_spawn_takes_task``）。

        用签名判定而不是 try/except TypeError：后者会把子代理内部真的
        TypeError 误判成"通路不收这个参数"，从此静默丢归属。
        @author aceFelix
        """
        if self._spawn_takes_task is None:
            import inspect

            try:
                params = inspect.signature(self._spawn).parameters  # type: ignore[arg-type]
                self._spawn_takes_task = "task_id" in params or any(
                    p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
                )
            except (TypeError, ValueError):   # 内建函数等拿不到签名：按不收处理
                self._spawn_takes_task = False
        return bool(self._spawn_takes_task)

    def _build_prompt(self, goal: TaskGoal, step: TaskStep) -> str:
        """把目标上下文与本步骤拼成给子代理的任务文本。"""
        done, total = goal.progress()
        lines = [
            f"总目标：{goal.goal}",
            f"进度：{done}/{total}，现在只做第 {self._index_of(goal, step) + 1} 步。",
            f"本步骤：{step.desc}",
        ]
        workdir = self._workdir_for(goal)
        if workdir:
            lines.append(f"工作目录：{workdir}")
        prev = [s for s in goal.steps if s.state == STEP_DONE and s.result]
        if prev:
            lines.append("前序步骤结论（仅作线索，别当作当前事实）：")
            for s in prev[-3:]:
                lines.append(f"- {s.desc[:30]}：{s.result[:120]}")
        return "\n".join(lines)

    @staticmethod
    def _index_of(goal: TaskGoal, step: TaskStep) -> int:
        try:
            return goal.steps.index(step)
        except ValueError:
            return 0

    def _workdir_for(self, goal: TaskGoal) -> str:
        """步骤在哪个目录里跑：目标自带的目录优先（目标可能属于另一个会话的项
        目），否则用驱动器自己的缺省。用错目录是最坏的一类错：改动没报错，只是
        落到了另一个仓库。"""
        return goal.workdir or self._workdir

    def _notify(self, text: str, *, dedup_key: str = "") -> None:
        """播报一律经宿主注入的 notifier（内部必须先过 gate_notify）。"""
        if self._notifier is None or not text:
            return
        try:
            self._notifier(NOTIFY_SOURCE, text, dedup_key)
        except Exception as e:  # noqa: BLE001 - 播报失败不影响推进
            logger.debug("任务播报投递失败: %s", e)

    def _mark_step(self, goal: TaskGoal, step: TaskStep) -> None:
        """把单个步骤的最新值写回台账（驱动器是唯一写入方，按整目标覆盖）。"""
        for i, s in enumerate(goal.steps):
            if s.id == step.id:
                goal.steps[i] = step
                break
        goal.updated_at = time.time()
        self._store.save(goal)

    # ---- 感知事件接线 ----

    def _subscribe_bus(self) -> None:
        """订阅 W2 总线（只收 ``percept_event`` 类等待真正关心的源）。

        总线未装配（感知层没开）时订阅不到东西，但等待仍会按 deadline 正常收尾，
        不会因为"源没开"就永远挂着。
        @author aceFelix
        """
        if self._unsub_bus is not None:
            return
        try:
            bus = self._bus_getter() if self._bus_getter else self._default_bus()
            if bus is None:
                return
            self._unsub_bus = bus.subscribe(self.on_percept_event)
        except Exception as e:  # noqa: BLE001 - 订阅失败只是退化回 deadline 收尾
            logger.debug("任务脊柱订阅感知总线失败: %s", e)
            self._unsub_bus = None

    @staticmethod
    def _default_bus() -> Any:
        """取当前感知总线实例；感知层未装配时返回 None。"""
        try:
            from agent.core.percept import get_bus

            return get_bus()
        except Exception:
            return None

    def on_percept_event(self, event: Any) -> None:
        """总线回调：命中某个 ``percept_event`` 等待就登记命中并唤醒线程。

        跑在总线轮询线程里，所以只做内存安全的事：比对、改台账、set()。
        @author aceFelix
        """
        source = str(getattr(event, "source", "") or "")
        kind = str(getattr(event, "kind", "") or "")
        try:
            summary = event.summary()  # type: ignore[attr-defined]
        except Exception:
            summary = ""
        hit = False
        for goal in self._store.open_goals(limit=50):
            step = goal.current_step()
            if step is None or step.wait is None or step.wait.kind != WAIT_PERCEPT_EVENT:
                continue
            if not self._event_matches(event, step.wait, source, kind):
                continue
            step.wait.mark_event_hit(getattr(event, "ts", None) or time.time(), summary=summary)
            self._store.save(goal)
            hit = True
        if hit:
            self.wake("percept_event 命中")

    @staticmethod
    def _event_matches(
        event: Any, wait: TaskWait, source: str, kind: str,
    ) -> bool:
        """事件是否满足等待条件（源必配、kind 与文本子串按需配）。"""
        want_source = str(wait.spec.get("source") or "")
        if want_source and want_source != source:
            return False
        want_kind = str(wait.spec.get("kind") or "")
        if want_kind and want_kind != kind:
            return False
        contains = str(wait.spec.get("contains") or "")
        if contains:
            try:
                blob = f"{kind} {event.summary()}"  # type: ignore[attr-defined]
            except Exception:
                blob = f"{kind} {getattr(event, 'payload', '')}"
            if contains.lower() not in blob.lower():
                return False
        return True


# ---- 宿主接线（两个宿主共用一套参数解析，避免各写一份） ----

#: 进程级驱动器句柄：只一个宿主会装一个，读侧（/tasks）靠它报“是否在驱动”。
_driver: TaskDriver | None = None


def make_gate_notifier(emit: Callable[[str], None]) -> Callable[[str, str, str], None]:
    """把驱动器播报接到 W1 打扰闸门（两个宿主共用一份接法）。

    Args:
        emit: 闸门放行后的投递回调（ ``(text) -> None`` ，宿主决定上屏/事件/TTS）。

    闸门实现只有 ``proactive.apply_notify_gate`` 一份：这里递 ``get_budget()`` 拿
    同一个预算单例，不另建规则。闸门自身异常时降级为直接投递——
    “目标跑完了却永远不吱声”比“少过一道闸”更糟，与简报侧口径一致。

    @author aceFelix
    """
    def _notify(source: str, text: str, dedup_key: str = "") -> None:
        emitted = text
        try:
            from agent.core.daemon.proactive import apply_notify_gate
            from agent.core.policy import get_budget

            emitted = apply_notify_gate(
                source, text,
                dedup_key=dedup_key,
                urgency=GATE_URGENCY,
                budget=get_budget(),
            )
        except Exception as e:  # noqa: BLE001 - 过闸失败不阻断投递（降级直投）
            logger.debug("任务播报过闸失败，降级直接投递: %s", e)
        if emitted:
            emit(emitted)

    return _notify


def build_driver(
    settings: Any,
    *,
    notifier: Callable[[str, str, str], None] | None = None,
    provider: Any = None,
    store: SpineStore | None = None,
    session_name: str = "",
    workdir: str = "",
    bus_getter: Callable[[], Any] | None = None,
) -> TaskDriver:
    """按配置装配驱动器（不自动 start，起停时机由宿主定）。

    ``bus_getter`` 传的是“取当前总线”的回调而不是总线实例：感知总线随 W2
    开关可能晚于驱动器装配、也可能在停机时先被抽走，订阅时现取才不会
    锁定在一个已过期的实例上。

    @author aceFelix
    """
    driver = TaskDriver(
        settings=settings,
        provider=provider,
        store=store,
        notifier=notifier,
        workdir=workdir,
        session_name=session_name,
        bus_getter=bus_getter,
    )
    set_driver(driver)
    return driver


def get_driver() -> TaskDriver | None:
    """当前进程的驱动器（未装配返回 None）。"""
    return _driver


def set_driver(driver: TaskDriver | None) -> None:
    """登记/清除进程级驱动器句柄（宿主 start/stop 两侧都要维护）。"""
    global _driver
    _driver = driver


def wake_driver(reason: str = "") -> bool:
    """请驱动器立刻复查一次；无驱动器时返回 False（读侧不因此报错）。"""
    driver = _driver
    if driver is None:
        return False
    driver.wake(reason)
    return True
