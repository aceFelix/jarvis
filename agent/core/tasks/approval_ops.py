"""任务脊柱的「阻塞与授权」一族（从 ``driver.py`` 拆出）。

职责边界：本文件只管**一个步骤卡住之后怎么办**——标成阻塞、要不要问用户、同一
原因不重复打扰、用户答复后怎么收尾（包括批准时该签多大范围的授权租约）。什么
时候该醒、怎么推进、失败怎么定局全在 ``driver.py``。拆开的原因不是"文件太长就随便
切一刀"：阻塞与询问的变化原因是
"打扰预算策略又调了""面板要一个可编程的放行入口"，而调度节拍的变化原因是
"等待条件种类变了""退避算法要改"，两者本来就不该互相牵动。

形态沿用 serve 那边的先例（一族指令一个混入）：``TaskDriver`` 继承本混入后
调用点一字不改（``self._block`` / ``self._ask`` / ``self.request_approval``
仍然有效），拆分的唯一可见结果是模块边界。

2026-10-22 实机走查后新增三条口径（见 docs/fixlogs/w3-driver-approval-blocked-report.md）：
- 受阻步骤必须挂 ``APPROVAL_HOLD_S`` 安全退避（受阻没有 ``wait`` 护身，不退避
  就是每轮重跑一次 LLM 的死循环）；
- 用户答复后必须清零退避并立刻唤醒，且**没命中步骤时返回 False**——这个返回值
  是给面板看的承诺，批了个不存在的步骤却回成功，等于把谎报从后端搬到前端；
- 「批准」不能只把步骤退回 pending：重试走同一条无 UI 判定、同一个 ASK 还会落成
  DENY，那就只是个空转按钮。``approve_step`` 先按**本步实际被拒的工具 + 本目标
  工作目录 + 短 TTL** 三重收窄签一条租约，再退回待执行（租约只放宽 ASK）。

@author aceFelix
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from agent.core.tasks.spine import (
    BLOCK_APPROVAL,
    BLOCK_TIMEOUT,
    GOAL_PAUSED,
    STEP_BLOCKED,
    STEP_PENDING,
    TaskGoal,
    TaskStep,
)

#: 受阻后的安全退避（秒）。用户答复走 :meth:`ApprovalOpsMixin.request_approval`
#: 会立刻清零并唤醒，所以这个值不是"多久重试一次"，而是"万一没人理也不能每轮
#: 重跑一次 LLM"的闸门：与蓝图里"条件等待命中前不重复调 LLM"是同一条约束。
APPROVAL_HOLD_S = 3600.0

#: 「批准」顺带签发的授权租约时长（分钟）。刻意只给半个钟头：它买的是
#: “这一步重试时别再问我”，不是“这个工具以后都别问我”——长期免打断应该用
#: permissions.yaml 的带路径 allow 规则或 `/lease` 显式签发（可查可撤）。
APPROVAL_LEASE_TTL_MIN = 30


class ApprovalOpsMixin:
    """阻塞标记、询问投递、授权答复与批准重试四件事（由 ``TaskDriver`` 继承）。

    Attributes:
        _store: 台账存储（驱动器持有，本混入只读写目标）。
        _notify: ``(source, text, dedup_key)`` 播报回调，宿主内部必须先过 W1 闸门。
        _ask_days: 阻塞超过这么多天允许再问一次（``[tasks].blocked_ask_days``）。

    @author aceFelix
    """

    if TYPE_CHECKING:  # 仅为 IDE/类型检查声明宿主属性，运行期不引入循环导入
        _store: Any
        _ask_days: float

        def _notify(self, text: str, *, dedup_key: str = "") -> None: ...

        def _index_of(self, goal: TaskGoal, step: TaskStep) -> int: ...

        def wake(self, reason: str = "") -> None: ...

    # ---- 受阻后的节拍 ----

    def blocked_hold(self) -> float:
        """受阻步骤下一次允许尝试的时刻（调用方把它写进 ``next_run_at``）。

        由本模块给出而不是让驱动器自己算：``APPROVAL_HOLD_S`` 的语义属于
        "授权一族"，改它时应该只动一个地方。
        """
        return time.time() + APPROVAL_HOLD_S

    def _block(
        self,
        goal: TaskGoal,
        step: TaskStep,
        reason: str,
        note: str,
        *,
        mark: bool = True,
    ) -> None:
        """把步骤标成阻塞，并按原因决定要不要问用户。

        只管"还在等"的两类（等条件超时、等授权）：重试到上限的失败走
        ``_run_step`` 里的 failed 终态路径，不进本函数（见那边的注释）。
        @author aceFelix
        """
        step.state = STEP_BLOCKED
        step.block_reason = reason
        goal.blocked_since = goal.blocked_since or time.time()
        if mark:
            self._store.save(goal)
        if reason == BLOCK_TIMEOUT:
            self._ask(goal, step, f"等条件超时（{note}），要不要继续等、换做法，还是先搁下？")
        elif reason == BLOCK_APPROVAL:
            self._ask(goal, step, note)

    def _ask(self, goal: TaskGoal, step: TaskStep, question: str) -> None:
        """就一个步骤问用户一次（同一原因不重复问，走 W1 预算闸门）。

        阻塞超过 ``blocked_ask_days`` 也允许再问一次（用户可能真忘了，长期静默的
        目标比多打扰一次更糟），所以判据是"本步骤本轮未问"而不是"从未问过"。
        @author aceFelix
        """
        if step.asked:
            if not self._ask_days:
                return
            aged = (
                goal.blocked_since
                and (time.time() - goal.blocked_since) / 86400.0 >= self._ask_days
            )
            if not aged:
                return
        step.asked = True
        self._notify(
            f"目标「{goal.goal[:24]}」卡在第 {self._index_of(goal, step) + 1} 步"
            f"「{step.desc[:20]}」：{question}",
            dedup_key=f"ask:{goal.id}:{step.id}",
        )
        self._store.save(goal)

    # ---- 用户的答复 ----

    def request_approval(self, goal_id: str, step_id: str, *, approve: bool) -> bool:
        """用户对一次"需要授权"的答复（供终端命令与桌面「任务中心」面板调用）。

        Args:
            goal_id: 目标 ID。
            step_id: 步骤 ID。
            approve: True=放行（把步骤退回 pending、清掉 asked 与安全退避，等驱动器
                重试）；False=整目标暂停（不再自动推进，等用户明确恢复）。

        Returns:
            是否真的改动过目标。**没找到该步骤时返回 False 且一个字段都不碰**：
            面板据此提示"这个步骤已经不存在了"，而不是拿着一个"成功"去显示
            "已批准，等待执行"。

        @author aceFelix
        """
        hit = False

        def _apply(g: TaskGoal) -> None:
            nonlocal hit
            for s in g.steps:
                if s.id != step_id:
                    continue
                hit = True
                if approve:
                    s.state = STEP_PENDING
                    s.block_reason = ""
                    s.asked = False
                    s.attempts = 0
                    # 放行后重试会重新写被拒清单，上一轮的不能留着：它是下一次
                    # 签租约的依据，陈旧名单等于拿旧的同意范围放新的行
                    s.denied_tools = []
                    # 受阻时挂了安全退避（APPROVAL_HOLD_S），放行就得把它清零：
                    # 否则用户批完了还要等退避到期才重试，看上去像"没生效"。
                    s.next_run_at = 0.0
                else:
                    s.state = STEP_BLOCKED
                    s.block_reason = BLOCK_APPROVAL
                    g.status = GOAL_PAUSED
            if hit:
                g.refresh_status()

        applied = self._store.update(goal_id, _apply) is not None and hit
        if applied:
            # 不等下一轮自然唤醒：用户刚刚表过态，立刻复查一次
            self.wake("用户已答复授权")
        return applied

    # ---- 批准重试（带真放行，不是只重试一次） ----

    def approve_step(
        self,
        goal_id: str,
        step_id: str,
        *,
        minutes: int = APPROVAL_LEASE_TTL_MIN,
    ) -> tuple[bool, str]:
        """批准一个等授权的步骤：先签一条**只收窄范围**的租约，再把步骤退回待执行。

        为什么光复位步骤不够（这是 2026-10-22 实机之后定下的口径）：重试走的是
        同一条无 UI 的默认权限判定，同一个 ASK 还会再落成 DENY——按了按钮等于
        没按，只是把一个谎报换成了一个空转。租约是 W1 里唯一的“用户表过态就免
        打断”通路（`policy_gate` 在编排器内部，后台子代理同样吃得到），而且它
        只放宽 ASK，放宽不了 DENY 与路径守护。

        放行范围三重收窄，缺一不可：
          1. tools = 本步实际被拒的工具名（不用通配，只放用户刚刚批过的那几个）；
          2. path_within = 本目标工作目录下的 glob（出了这个目录照样问）；
          3. TTL 默认 30 分钟（只够本次重试，不长期有效）。

        Args:
            goal_id: 目标 ID。
            step_id: 步骤 ID（必须是 blocked + block_reason=approval）。
            minutes: 租约时长（分钟），最小 1。

        Returns:
            ``(是否放行成功, 给用户看的一句话)``。失败一律带原因，绝不回“已批准”
            但其实什么都没签。

        @author aceFelix
        """
        from agent.core.policy.lease import get_lease_store

        goal = self._store.load(goal_id)
        if goal is None:
            return False, f"没有目标：{goal_id}"
        step = next((s for s in goal.steps if s.id == step_id), None)
        if step is None:
            return False, f"目标「{goal.goal[:24]}」里没有这个步骤，可能已被删掉或定局"
        if step.state != STEP_BLOCKED or step.block_reason != BLOCK_APPROVAL:
            return False, (
                f"步骤「{step.desc[:20]}」现在不是等授权状态（{step.state}），"
                "没有要放行的东西"
            )
        tools = [str(t) for t in step.denied_tools if t]
        if not tools:
            # 宁可不动：没有清单就只能放行全部工具，那比用户同意的更大
            return False, (
                "这一步没记下被拒的工具名（旧台账或重试后已清），"
                "不签范围不明的租约；请用 `/lease <工具> [路径] [分钟]` 或 "
                "permissions.yaml 的带路径 allow 规则显式放行后再批准"
            )

        workdir = str(goal.workdir or "")
        # 无工作目录就不限路径（回执会照实说出来，不默不作声地扩大放行面）
        scope = [f"{workdir}/**"] if workdir else []
        ttl = max(1, int(minutes))
        idx = self._index_of(goal, step) + 1
        lease = get_lease_store().grant(
            tools, scope, ttl_minutes=ttl,
            note=f"批准目标「{goal.goal[:24]}」第 {idx} 步",
        )
        applied = self.request_approval(goal_id, step_id, approve=True)
        where = workdir or "不限路径"
        msg = (
            f"已签发租约 [{lease.id}]：{'、'.join(tools)} @ {where}，有效期 {ttl} 分钟；"
            "这一步已退回待执行，驱动器会立刻重试。"
            "注意：租约只放宽文件类调用的确认，Bash 这类不带路径参数的调用它放宽不了"
            "（命中不了限路径的租约），仍会被拦下如实播报。"
        )
        if not applied:
            msg = f"租约已签 [{lease.id}]，但台账里这一步已不在，未能退回待执行。"
        return applied, msg
