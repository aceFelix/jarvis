"""主动感知引擎 —— 贾维斯的"主动意识"中枢。

P2-3 主动提醒系统的核心调度器。统一管理所有主动提醒源：
- DailyBriefing: 每日简报（定时播报今日概览）
- DeadlineTracker: 截止日期分级提醒
- ReminderEscalation: 提醒升级/确认机制
- CalendarSource: 日历事件提前提醒

ProactiveEngine 不自己起线程，而是利用现有 Scheduler 注册定时任务，
到期时通过 on_notify 回调播报（走通知回调 + TTS 通道）。

工作流:
    daemon 启动 → 创建 ProactiveEngine → engine.start()
      → 在 Scheduler 中注册每日简报任务（daily, briefing_time）
      → 在 Scheduler 中注册截止日期检查任务（daily, check_time）
      → 在 Scheduler 中注册日历检查任务（每 30 分钟）
      → 到期触发 → 收集数据 → 组装文本 → on_notify 播报

设计要点:
- **不自起线程**: 复用 Scheduler 的轮询机制，轻量无额外开销
- **幂等注册**: start() 多次调用不会重复注册任务（通过 note 标记识别）
- **优雅降级**: 任何子模块失败不影响其他模块
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any, Callable

from agent.core.daemon.scheduler import Scheduler, ScheduleTask
from agent.core.daemon.deadline import DeadlineTracker

# ---------------------------------------------------------------------------
# 配置数据类
# ---------------------------------------------------------------------------


class ProactiveConfig:
    """主动感知引擎配置。从 Settings 字段构建。"""

    def __init__(
        self,
        *,
        briefing_enabled: bool = True,
        briefing_time: str = "08:30",
        deadline_enabled: bool = True,
        deadline_check_time: str = "09:00",
        calendar_enabled: bool = False,
        calendar_check_interval_min: int = 30,
        calendar_remind_minutes_before: int = 30,
        profile_maintenance_enabled: bool = True,
        auto_clean_enabled: bool = False,
        auto_clean_time: str = "04:30",
        auto_clean_min_mb: float = 20.0,
    ) -> None:
        self.briefing_enabled = briefing_enabled
        self.briefing_time = briefing_time
        self.deadline_enabled = deadline_enabled
        self.deadline_check_time = deadline_check_time
        self.calendar_enabled = calendar_enabled
        self.calendar_check_interval_min = calendar_check_interval_min
        self.profile_maintenance_enabled = profile_maintenance_enabled
        self.calendar_remind_minutes_before = calendar_remind_minutes_before
        # 自动清理：默认关，且还要 maintenance_enabled 总闸同时开（在两个开关
        # 那里分别把关，任一处关掉都不吵不闹）
        self.auto_clean_enabled = auto_clean_enabled
        self.auto_clean_time = auto_clean_time
        self.auto_clean_min_mb = auto_clean_min_mb


# ---------------------------------------------------------------------------
# ProactiveEngine
# ---------------------------------------------------------------------------

# 任务 note 标记（用于识别 ProactiveEngine 注册的任务，避免重复）
_BRIEFING_NOTE = "__proactive_briefing__"
_DEADLINE_NOTE = "__proactive_deadline__"
_CALENDAR_NOTE = "__proactive_calendar__"
_PROFILE_MAINT_NOTE = "__proactive_profile_maintenance__"


def _is_auto_clean_note(task: ScheduleTask) -> bool:
    """是不是自动清理任务。

    判定委托给 auto_clean_schedule（它拥有那个 note 常量）：两边各写一份字符串
    的话，改一处忘一处就变成“任务注册了但从不触发”，而且不报错。
    """
    from agent.core.daemon import auto_clean_schedule as acs

    return acs.is_auto_clean_task(task)


def _preference_silence_reason() -> str:
    """查一次上下文偏好：该不该把这条播报降级成"只上屏不朗读"。

    Returns: 非空串 = 不朗读的原因（含用户原话），空串 = 没命中偏好。

    为什么在这里施放而不是往提示词里注入一句"用户不喜欢听"：偏好必须是确定性
    执行的规则。靠模型自觉遵守的"记住"等于只是鼓励了一下，明天它忘了就复发——
    而用户说不出为什么又变回去了。

    代价必须有界：账本是本地小文件，且本函数只在**已经把播报裁决为要投递**时
    才被调用（一天十几次量级）；出任何错都当"没命中"，绝不让取偏好失败把播报
    搞丢。

    @author aceFelix
    """
    try:
        from agent.config import prefs

        value, reason = prefs.effective("proactive_tts_enabled", True)
        return "" if value else reason
    except Exception:  # noqa: BLE001 - 偏好求值失败不该影响播报本身
        return ""


def notify_gate_action(
    source: str,
    text: str,
    *,
    dedup_key: str = "",
    urgency: float = 0.6,
    budget: Any = None,
    silent_delivery: bool = False,
) -> Any:
    """W1 打扰闸门的**完整裁决**版入口：返回 ``NotifyAction`` 本身。

    与 ``apply_notify_gate`` 的分工：后者只回答“该不该播、播什么”，丢掉了
    “为什么不播”与 speak 降级标记；桌面提醒路径需要知道“这是一次只上屏不朗读的
    降级投递”才能把 `speak=False` 传给事件出口，所以它走本函数。

    Args:
        source: 播报源名（预算权重表的键，用户自设提醒是 ``schedule``）。
        text: 拟播报文本。
        dedup_key: 聚合去重键。
        urgency: 时效紧迫度。
        budget: ``NotifyBudget`` 实例；None = 不设预算闸（仅保留 away 拦截）。
        silent_delivery: 静默/超额时不入库而是**降级投递**（气泡照投、不朗读）。
            只给用户明确要过的提醒用——整条延后是错的，而冲刷靠下一条请求触发。

    Returns: ``NotifyAction``（kind: emit/queue/suppress，emit 时看 speak 决定是否朗读）。

    @author aceFelix
    """
    import logging

    from agent.core.policy.budget import NotifyAction

    logger = logging.getLogger("jarvis.proactive")
    # away 分流：人不在，会出声的播报暂缓入库（TTS 不对空房间说话）
    try:
        from agent.core.policy import get_policy_state

        away, _ = get_policy_state().is_away()
    except Exception:
        away = False
    if budget is not None:
        try:
            action = budget.gate(
                text, source, dedup_key=dedup_key, urgency=urgency,
                silent_delivery=silent_delivery,
            )
            # away 只拦“会出声”的那类：降级投递本就不发声，气泡上屏无害
            # 且信息不丢（旧行为下提醒会在 away 期间被整条暂存，静默时段叠加时
            # 用户两头都看不到）。
            if away and action.kind == "emit" and action.speak:
                action = NotifyAction("queue", text, "无人值守，暂缓入库")
            # 偏好闸放在预算闸之后：两者的口径都是"降低出声强度而不丢消息"，
            # 且都是只影响 speak 不影响 kind，叠加时后说的那条原因更具体。
            action = _apply_preference_silence(action, source, logger)
            if not action.should_emit:
                logger.info("播报暂缓[%s]: %s（%s）", source, action.reason, text[:40])
            elif not action.speak:
                logger.info("降级投递[%s]: %s", source, action.reason)
            return action
        except Exception as e:
            logger.warning("打扰预算异常，降级直接播报: %s", e)
            # 不落到末尾那句“未设预算闸”上：reason 会说谎，排查时会把人带偏
            fallback = NotifyAction("emit", text, "预算异常，降级直接播报")
            return _apply_preference_silence(fallback, source, logger)
    elif away:
        logger.info("无人值守，跳过播报[%s]", source)
        return NotifyAction("queue", text, "无人值守，跳过播报")
    action = NotifyAction("emit", text, "未设预算闸，直接播报")
    return _apply_preference_silence(action, source, logger)


def _apply_preference_silence(action: Any, source: str, logger: Any) -> Any:
    """命中"这个场合别念出来"的偏好时，把出声降级（投递不变）。

    只降声不漏事：气泡/通知照旧，只是不朗读。用户说的是"别念出来"，不是
    "别告诉我"——两个诉求差得很远，搞错了会真丢消息。

    @author aceFelix
    """
    from agent.core.policy.budget import NotifyAction

    if not (action.should_emit and action.speak):
        return action
    reason = _preference_silence_reason()
    if not reason:
        return action
    logger.info("偏好降级[%s]: %s", source, reason)
    return NotifyAction("emit", action.text, reason, speak=False)


def apply_notify_gate(
    source: str,
    text: str,
    *,
    dedup_key: str = "",
    urgency: float = 0.6,
    budget: Any = None,
) -> str | None:
    """W1 打扰闸门的文本入口：返回该播的文本，None = 不播。

    本函数是 ``notify_gate_action`` 的薄包装（只把裁决压成 text|None）：闸门
    只依赖“预算单例 + 在场状态”，跟“引擎有没有注册定时任务”无关。W3 任务
    脊柱的后台推进跑在没有 ``ProactiveEngine`` 的宿主里（CLI REPL 就没有一个），
    它也要过同一道闸；放在模块层就不必为“另一个宿主也要闸门”再抄一份实现
    （口径分叉比没有闸门更危险）。

    ``ProactiveEngine.gate_notify`` 仍是本函数的薄包装，保持既有调用点与
    “预算未注入时只在 away 情况下静默丢弃”的行为不变。

    ⚠ 需要区分“出声播”与“只上屏不朗读”的调用方（桌面提醒）请用
    ``notify_gate_action``，本函数会把 speak=False 的降级投递当成普通播出。

    Args:
        source: 播报源名（预算权重表的键，如 ``briefing`` / ``percept:mail`` / ``task``）。
        text: 拟播报文本。
        dedup_key: 聚合去重键（同键短时间内不叠两条）。
        urgency: 时效紧迫度（与来源权重相乘）。
        budget: ``NotifyBudget`` 实例；None = 不设预算闸（仅保留 away 拦截）。

    @author aceFelix
    """
    action = notify_gate_action(
        source, text, dedup_key=dedup_key, urgency=urgency, budget=budget,
    )
    return action.text if action.should_emit else None


class ProactiveEngine:
    """主动感知引擎。

    统一管理每日简报、截止日期检查、日历事件提醒。
    通过 Scheduler 注册定时任务，到期触发 on_notify 回调。

    用法::

        engine = ProactiveEngine(
            scheduler=scheduler,
            config=ProactiveConfig(),
            deadline_tracker=tracker,
            on_notify=lambda msg: print(msg),
        )
        engine.start()
        # ... daemon 运行中 ...
        engine.stop()
    """

    def __init__(
        self,
        *,
        scheduler: Scheduler,
        config: ProactiveConfig,
        deadline_tracker: DeadlineTracker | None = None,
        calendar_source: Any | None = None,
        monitor: Any | None = None,
        on_notify: Callable[[str], None],
        budget: Any | None = None,
    ) -> None:
        """
        Args:
            scheduler: Scheduler 实例（复用其定时触发机制）。
            config: 主动感知配置。
            deadline_tracker: DeadlineTracker 实例（可选）。
            calendar_source: CalendarSource 实例（可选）。
            monitor: SystemMonitor 实例（可选，用于简报中的系统状态）。
            on_notify: 通知回调。接收组装好的文本，由 daemon 负责播报。
            budget: W1 打扰预算（NotifyBudget，可选）。传入后所有播报
                统一过闸：静默时段/低重要度只入库，窗口内同源聚合，
                超日预算丢弃；away 期间一律暂缓（不对空房间说话）。
                @author aceFelix
        """
        self._scheduler = scheduler
        self._config = config
        self._deadline_tracker = deadline_tracker
        self._calendar_source = calendar_source
        self._monitor = monitor
        self._on_notify = on_notify
        self._budget = budget
        self._started = False
        # 注册的任务 ID（stop 时取消）
        self._task_ids: list[str] = []

    def start(self) -> None:
        """启动主动感知引擎。在 Scheduler 中注册定时任务。"""
        if self._started:
            return
        self._started = True

        # 注册每日简报任务
        if self._config.briefing_enabled:
            self._register_briefing()

        # 注册截止日期检查任务
        if self._config.deadline_enabled and self._deadline_tracker:
            self._register_deadline_check()

        # 注册画像记忆每日维护任务（Phase 1a M3：衰减 + 上限淘汰，凌晨静默执行）
        if self._config.profile_maintenance_enabled:
            self._register_profile_maintenance()

        # 注册每日自动清理（只动 cleaners.toml 里 auto_ok=true 的零感知垃圾）
        if getattr(self._config, "auto_clean_enabled", False):
            self._register_auto_clean()

        # 注册日历检查任务（暂用 daily 任务模拟，后续可改为更频繁）
        # 注意：Scheduler 目前只支持 once/daily/weekly，
        # 日历检查暂用 daily 在 briefing_time 时一并检查
        # TODO: 后续扩展 Scheduler 支持 hourly/minutely 重复模式

    def stop(self) -> None:
        """停止主动感知引擎。取消注册的定时任务。"""
        if not self._started:
            return
        self._started = False
        for task_id in self._task_ids:
            self._scheduler.cancel_task(task_id)
        self._task_ids.clear()

    def update_schedule_config(
        self,
        *,
        briefing_enabled: bool,
        briefing_time: str,
        deadline_enabled: bool,
        deadline_check_time: str,
    ) -> None:
        """热更新简报/截止日期配置并重注册每日任务（桌面 settings.set 运行时生效）。

        调度任务是 start() 时的快照：改配置后必须先按 note 撤旧任务再重注册，
        否则新开关/新时间要到重启才生效。未 start() 时仅更新配置字段。

        @author aceFelix
        """
        self._config.briefing_enabled = briefing_enabled
        self._config.briefing_time = briefing_time
        self._config.deadline_enabled = deadline_enabled
        self._config.deadline_check_time = deadline_check_time
        if not self._started:
            return
        for note in (_BRIEFING_NOTE, _DEADLINE_NOTE):
            task = self._find_task_by_note(note)
            if task is not None:
                self._scheduler.cancel_task(task.id)
                if task.id in self._task_ids:
                    self._task_ids.remove(task.id)
        if self._config.briefing_enabled:
            self._register_briefing()
        if self._config.deadline_enabled and self._deadline_tracker:
            self._register_deadline_check()

    # ---- 注册定时任务 ----

    def _register_briefing(self) -> None:
        """注册每日简报任务。"""
        # 检查是否已存在（幂等）
        if self._find_task_by_note(_BRIEFING_NOTE):
            return

        trigger_at = self._today_at(self._config.briefing_time)
        task = self._scheduler.add_task(
            content="每日简报",
            trigger_at=trigger_at,
            repeat="daily",
            note=_BRIEFING_NOTE,
        )
        self._task_ids.append(task.id)

    def _register_deadline_check(self) -> None:
        """注册截止日期每日检查任务。"""
        if self._find_task_by_note(_DEADLINE_NOTE):
            return

        trigger_at = self._today_at(self._config.deadline_check_time)
        task = self._scheduler.add_task(
            content="截止日期检查",
            trigger_at=trigger_at,
            repeat="daily",
            note=_DEADLINE_NOTE,
        )
        self._task_ids.append(task.id)

    def _register_profile_maintenance(self) -> None:
        """注册画像记忆每日维护任务（凌晨 3:30，管家"睡眠整理记忆"）。

        纯本地计算（无 LLM、无打扰）：置信度衰减 + 超限淘汰。
        """
        if self._find_task_by_note(_PROFILE_MAINT_NOTE):
            return

        trigger_at = self._today_at("03:30")
        task = self._scheduler.add_task(
            content="画像记忆维护",
            trigger_at=trigger_at,
            repeat="daily",
            note=_PROFILE_MAINT_NOTE,
        )
        self._task_ids.append(task.id)

    def _register_auto_clean(self) -> None:
        """注册每日自动清理（只动 cleaners.toml 里 auto_ok=true 的零感知垃圾）。

        为什么错开画像维护半小时：深夜两件事挤在一起，出问题时不好判断
        是谁把机器拖起来的。

        为什么委托给 ``auto_clean_schedule``：纯 CLI 路径没有 ProactiveEngine，
        它挂在 ``CliReminderHost`` 上；两边走同一个注册函数，不存在“桌面清的和
        终端清的不是一回事”这种分叉。
        """
        import logging

        from agent.core.daemon import auto_clean_schedule as acs

        try:
            acs.register(self._scheduler, time_str=self._config.auto_clean_time)
        except Exception as e:  # noqa: BLE001 - 注册失败不影响其它定时任务
            logging.getLogger("jarvis.maintenance.auto_clean").warning(
                "自动清理任务注册失败: %s", e
            )

    def _fire_auto_clean(self) -> None:
        """触发一次自动清理。

        **另起线程**：本方法由 Scheduler 的调度线程调用，而清理要扫盘 + 移文件。
        直接跑会把同一时刻到期的提醒往后压，用户只会看到“提醒迟到了”。
        清理完成后静默——零感知垃圾的例行公事不该占用注意力。
        """
        from agent.core.daemon import auto_clean_schedule as acs

        acs.fire(
            max_risk="low",
            min_mb=float(getattr(self._config, "auto_clean_min_mb", 20.0) or 0.0),
        )

    def _fire_profile_maintenance(self) -> None:
        """执行画像维护：decay + prune。静默（写日志，不打扰用户）。"""
        import logging

        logger = logging.getLogger("jarvis.memory.profile")
        try:
            from agent.core.memory.profile_store import ProfileStore

            store = ProfileStore()
            removed = store.decay()
            pruned = store.prune_over_limit(200)
            if removed or pruned:
                logger.info(
                    "画像维护完成: 衰减清除 %d 条, 超限淘汰 %d 条, 剩余 %d 条",
                    removed, pruned, len(store),
                )
        except Exception as e:
            logger.warning("画像维护失败: %s", e)

    # ---- 任务触发处理 ----

    def handle_task_fire(self, task: ScheduleTask) -> None:
        """处理 ProactiveEngine 注册的任务触发。

        由 daemon 的 _on_schedule_fire 调用，根据 note 标记分发。

        Args:
            task: 触发的 ScheduleTask。
        """
        if task.note == _BRIEFING_NOTE:
            self._fire_briefing()
        elif task.note == _DEADLINE_NOTE:
            self._fire_deadline_check()
        elif task.note == _CALENDAR_NOTE:
            self._fire_calendar_check()
        elif task.note == _PROFILE_MAINT_NOTE:
            self._fire_profile_maintenance()
        elif _is_auto_clean_note(task):
            self._fire_auto_clean()

    def is_proactive_task(self, task: ScheduleTask) -> bool:
        """判断任务是否是 ProactiveEngine 注册的（用于 daemon 分发）。"""
        return task.note in (
            _BRIEFING_NOTE, _DEADLINE_NOTE, _CALENDAR_NOTE, _PROFILE_MAINT_NOTE,
        ) or _is_auto_clean_note(task)

    # ---- 通知出口（W1 预算闸门）----

    def gate_notify(self, source: str, text: str, *, dedup_key: str = "",
                    urgency: float = 0.6) -> str | None:
        """W1 打扰闸门入口（实现已提到模块层 ``apply_notify_gate``）。

        为何单独拆出来：W2 感知层的事件由 serve 宿主（ProactiveHub）订阅总线后
        投递，它需要同一道闸门，但**不能复用 `on_notify` 回调**（简报与感知事件的
        桌面渲染 kind 不同）。拆开后闸门只有一份实现，`_dispatch_notify` 只是
        它的“播完就走 on_notify”包装，不变量“所有打扰都过同一个闸”仍然成立。

        W3 任务脊柱的宿主没有 ``ProactiveEngine``（CLI REPL），它们直接调
        ``apply_notify_gate`` 并递 ``get_budget()``，与本方法共用同一份实现。

        @author aceFelix
        """
        return apply_notify_gate(
            source, text, dedup_key=dedup_key, urgency=urgency, budget=self._budget,
        )

    def gate_notify_action(self, source: str, text: str, *, dedup_key: str = "",
                           urgency: float = 0.6,
                           silent_delivery: bool = False) -> Any:
        """完整裁决版闸门入口（返回 ``NotifyAction``，带 kind 与 speak）。

        为什么不只给 ``gate_notify``：那个入口把裁决压成了 text|None，丢掉了
        “为什么不播”与“只上屏不朗读”两种信息。桌面提醒需要据此分别处理
        （见 ``serve/hub._deliver_reminder``），而“所有打扰都过同一个闸”
        这条不变量靠共用本实现维持，不允许提醒路径自己另判一次静默时段。

        @author aceFelix
        """
        return notify_gate_action(
            source, text, dedup_key=dedup_key, urgency=urgency,
            budget=self._budget, silent_delivery=silent_delivery,
        )

    def _dispatch_notify(self, source: str, text: str, *, dedup_key: str = "",
                         urgency: float = 0.6) -> bool:
        """所有主动播报的唯一出口：先过打扰预算，再走 on_notify。

        返回是否真正播报。未播 reasons（静默/聚合/预算外/away）由预算模块
        给解释文本，落 daemon 日志可审计；积压在静默时段结束后聚合为一条。
        未注入 budget 时保持旧行为直接播报（向后兼容）。
        闸门本体在 ``gate_notify``（W2 感知事件走同一个闸）。
        @author aceFelix
        """
        emitted = self.gate_notify(source, text, dedup_key=dedup_key, urgency=urgency)
        if emitted is None:
            return False
        self._on_notify(emitted)
        return True

    # ---- 每日简报 ----

    def _fire_briefing(self) -> None:
        """生成并播报每日简报（经打扰预算闸门）。"""
        try:
            briefing = self._build_briefing()
            if briefing:
                self._dispatch_notify("briefing", briefing, urgency=0.9)
        except Exception:
            pass  # 简报生成失败不影响 daemon

    def _build_briefing(self) -> str:
        """组装每日简报文本。"""
        today = date.today()
        lines: list[str] = []

        # 问候
        hour = datetime.now().hour
        if hour < 6:
            greeting = "夜深了，先生"
        elif hour < 12:
            greeting = "早上好，先生"
        elif hour < 14:
            greeting = "中午好，先生"
        elif hour < 18:
            greeting = "下午好，先生"
        else:
            greeting = "晚上好，先生"

        lines.append(f"{greeting}。以下是今日简报：")
        lines.append("")

        # 1. 节假日/工作日状态
        try:
            from agent.core.daemon.holidays import is_holiday, is_workday, get_holiday_name
            holiday_name = get_holiday_name()
            if holiday_name:
                lines.append(f"📅 今天是节假日：{holiday_name}")
            elif not is_workday():
                lines.append("📅 今天是周末，好好休息")
            else:
                lines.append("📅 今天是工作日")
        except Exception:
            pass

        # 2. 今日待触发提醒
        try:
            today_str = today.isoformat()
            pending = self._scheduler.list_pending()
            today_reminders = [
                t for t in pending
                if t.trigger_at.startswith(today_str) and t.note not in (_BRIEFING_NOTE, _DEADLINE_NOTE, _CALENDAR_NOTE)
            ]
            if today_reminders:
                lines.append(f"\n⏰ 今日提醒（{len(today_reminders)} 个）：")
                for t in today_reminders:
                    time_part = t.trigger_at[11:16] if len(t.trigger_at) > 16 else t.trigger_at
                    lines.append(f"  • {time_part} {t.content}")
            else:
                lines.append("\n⏰ 今日无待触发提醒")
        except Exception:
            pass

        # 3. 活跃截止日期
        if self._deadline_tracker:
            try:
                summary = self._deadline_tracker.get_summary()
                if summary:
                    lines.append(f"\n📋 活跃截止日期：")
                    lines.append(summary)
            except Exception:
                pass

        # 4. 系统健康摘要
        if self._monitor:
            try:
                status = self._monitor.get_status()
                if "error" not in status:
                    lines.append(
                        f"\n💻 系统状态：CPU {status['cpu_percent']}% | "
                        f"内存 {status['memory_percent']}% | "
                        f"磁盘剩余 {status['disk_free_gb']}GB"
                    )
            except Exception:
                pass

        # 5. 日历事件
        if self._calendar_source:
            try:
                events = self._calendar_source.get_today_events()
                if events:
                    lines.append(f"\n📆 今日日程（{len(events)} 个）：")
                    for ev in events[:10]:  # 最多显示 10 个
                        lines.append(f"  • {ev}")
            except Exception:
                pass

        lines.append("\n祝您今天顺利，先生。")
        return "\n".join(lines)

    # ---- 截止日期检查 ----

    def _fire_deadline_check(self) -> None:
        """检查截止日期并播报提醒（经打扰预算闸门）。"""
        if not self._deadline_tracker:
            return
        try:
            messages = self._deadline_tracker.check_today()
            if messages:
                text = "📋 截止日期提醒：\n" + "\n".join(messages)
                self._dispatch_notify("deadline", text, urgency=0.8)
        except Exception:
            pass

    # ---- 日历检查 ----

    def _fire_calendar_check(self) -> None:
        """检查即将到来的日历事件并提前提醒（经打扰预算闸门）。"""
        if not self._calendar_source:
            return
        try:
            upcoming = self._calendar_source.get_upcoming_events(
                minutes_before=self._config.calendar_remind_minutes_before
            )
            if upcoming:
                text = "📆 日程提醒：\n" + "\n".join(f"  • {ev}" for ev in upcoming)
                self._dispatch_notify("calendar", text, urgency=0.85)
        except Exception:
            pass

    # ---- 工具方法 ----

    def _find_task_by_note(self, note: str) -> ScheduleTask | None:
        """在 Scheduler 中查找指定 note 的 pending 任务。"""
        for t in self._scheduler.list_pending():
            if t.note == note:
                return t
        return None

    @staticmethod
    def _today_at(time_str: str) -> str:
        """把 "HH:MM" 转成今天的 ISO datetime 字符串。

        如果时间已过，则用明天的（确保 daily 任务首次触发在未来）。
        """
        try:
            parts = time_str.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            hour, minute = 8, 30

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # 如果今天的时间已过，用明天
        if target <= now:
            from datetime import timedelta
            target += timedelta(days=1)

        return target.strftime("%Y-%m-%dT%H:%M:%S")
