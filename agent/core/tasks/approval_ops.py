"""任务脊柱的「阻塞与授权」一族（从 ``driver.py`` 拆出）。

职责边界：本文件只管**一个步骤卡住之后怎么办**——标成阻塞、要不要问用户、同一
原因不重复打扰、用户答复后怎么收尾。什么时候该醒、怎么推进、失败怎么定局全在
``driver.py``。拆开的原因不是"文件太长就随便切一刀"：阻塞与询问的变化原因是
"打扰预算策略又调了""面板要一个可编程的放行入口"，而调度节拍的变化原因是
"等待条件种类变了""退避算法要改"，两者本来就不该互相牵动。

形态沿用 serve 那边的先例（一族指令一个混入）：``TaskDriver`` 继承本混入后
调用点一字不改（``self._block`` / ``self._ask`` / ``self.request_approval``
仍然有效），拆分的唯一可见结果是模块边界。

2026-10-22 实机走查后新增两条口径（见 docs/fixlogs/w3-driver-approval-blocked-report.md）：
- 受阻步骤必须挂 ``APPROVAL_HOLD_S`` 安全退避（受阻没有 ``wait`` 护身，不退避
  就是每轮重跑一次 LLM 的死循环）；
- 用户答复后必须清零退避并立刻唤醒，且**没命中步骤时返回 False**——这个返回值
  是给面板看的承诺，批了个不存在的步骤却回成功，等于把谎报从后端搬到前端。

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


class ApprovalOpsMixin:
    """阻塞标记、询问投递与授权答复三件事（由 ``TaskDriver`` 继承）。

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
