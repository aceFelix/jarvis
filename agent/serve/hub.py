"""主动播报中枢 —— 复活 59f746a 休眠的主动感知套件，宿主从托盘换成 WS 事件流。

老 daemon 时代（``agent/daemon/notifications.py`` NotificationMixin）的三通道
播报（终端日志 + 托盘通知 + 待机 TTS）随常驻托盘架构下线而休眠，提交信息
明确「主动感知全套保留，等待 GUI 接线」。本模块即桌面壳（jarvis-desktop）
的接线体：

- 装配 ``Scheduler``（定时轮询）+ ``ProactiveEngine``（每日简报/截止日期）
  + ``DeadlineTracker``（截止日期追踪），配置字段沿用 settings 既有口径；
- 到期/通知投 ``proactive_notify`` 事件进 serve 事件队列，由事件泵 broadcast
  给桌面壳（聊天气泡 + 系统通知）；并行复活老 daemon「待机 TTS」语音通道
  （二期：开关 ``proactive_tts_enabled``，对话/语音忙时跳过朗读，朗读失败
  静默降级，不影响事件通道）；
- 提醒工具（ScheduleReminder 等）经 ChatEngine registry_hook 挂载同一
  Scheduler 实例，对话里说「提醒我」即可创建任务；
- **W2 感知层接线（阶段 9 第 4 步）**：``percept_enabled`` 打开时装配事件总线
  （``register_sources`` + 订阅 + ``start``），感知事件经 ``ProactiveEngine.gate_notify``
  ——与简报同一个闸门——投出去；人脸离开/进入画面同时驱动 W1 的 away 推断。

与老 daemon 常驻语义的差异：serve 进程随桌面壳启停，播报仅在运行期间
生效。补救链：Scheduler 自带错过补偿（一次性任务 ≤1 小时）+ 本模块的
简报补播窗口（错过 ≤ settings.briefing_catchup_window_min 分钟、默认 120
即 2 小时，启动时补播一次）。

@author aceFelix
"""

from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from typing import Any, Callable

from agent.config.settings import Settings
from agent.core.daemon.deadline import DeadlineTracker
from agent.core.daemon.proactive import ProactiveConfig, ProactiveEngine
from agent.core.daemon.scheduler import Scheduler, ScheduleTask
from agent.core.policy import get_budget  # W1 打扰预算（阶段 8）@author aceFelix
from agent.serve import protocol

# 补播窗口（分钟）已改由 settings.briefing_catchup_window_min 配置（默认 120），
# 便于随时调整“错过多久以内才补播”，不再作为模块常量硬编码。

# 补播延迟（秒）：等桌面壳 WS 连上再投事件，否则广播时无客户端接收。
# 属启动时序技术参数（非用户日常调整项），故保持常量、不进配置文件。
CATCHUP_DELAY_SEC = 5.0

# 截止日期提醒文本前缀（ProactiveEngine._fire_deadline_check 的固定格式），
# 用于在 on_notify 单一回调里区分简报与截止日期两类内容
_DEADLINE_PREFIX = "📋 截止日期提醒"

# TTS 朗读口径（复刻老 daemon NotificationMixin）：简报/截止日期文本较长，
# 只播前 200 字 + 引导后缀；提醒类加称呼前缀整段播。
_TTS_TEXT_LIMIT = 200
_TTS_MORE_SUFFIX = "……详细内容请查看桌面壳。"
_REMINDER_PREFIX = "先生，提醒您："

logger = logging.getLogger("jarvis.serve.hub")

# ---- W2 感知层接线口径（阶段 9 第 4 步）----

#: 感知事件播报的桌面渲染 kind。桌面壳 dispatcher 对未知 kind 走默认渲染
#: （全文上屏 + 系统通知），因此新增这个值不需要壳侧改代码；contracts.ts 的
#: kind 也是开放联合（`| string`）。
PERCEPT_NOTIFY_KIND = "percept"
PERCEPT_NOTIFY_TITLE = "贾维斯观察"

#: 只记账不播报的 kind：在场信号的价值在于驱动 away 推断，不是在耳边报
#: “先生您离开了/回来了”。人反复进出房间时这类事件会成串出现，若都走
#: 播报就会把日额度吃光（而且体验很差），所以在这里直接拦下。
PERCEPT_SILENT_KINDS: frozenset[str] = frozenset(
    {"face_appear", "face_disappear", "watch_stopped"}
)


class ProactiveHub:
    """主动播报中枢：Scheduler + ProactiveEngine 的 serve 宿主装配体。

    用法::

        hub = ProactiveHub(settings, event_queue)
        hub.start()
        # ... serve 运行中，到期任务投 proactive_notify 事件 ...
        hub.stop()

    @author aceFelix
    """

    def __init__(
        self,
        settings: Settings,
        event_queue: queue.Queue[dict[str, Any]],
        busy_probe: Callable[[], bool] | None = None,
    ) -> None:
        """
        Args:
            settings: 全局配置（取 briefing_*/deadline_*/proactive_tts_enabled/tts_* 字段）。
            event_queue: serve 事件队列（与 ChatEngine 共用，事件泵消费）。
            busy_probe: 「贾维斯忙吗」探针（serve 宿主传 engine.is_busy）；
                返回 True 时主动播报只推事件、不做 TTS 朗读。也可事后经
                set_busy_probe 注入（hub 先于 engine 装配的场景）。
        """
        self._settings = settings
        self._event_queue = event_queue
        self._busy_probe = busy_probe
        # TTS 朗读串行锁 + 当前朗读实例：多条播报排队播不叠音，停机时可打断
        self._tts_lock = threading.Lock()
        self._current_tts: Any = None
        self._scheduler = Scheduler(on_fire=self._on_fire)
        self._deadline_tracker = DeadlineTracker()
        self._engine = ProactiveEngine(
            scheduler=self._scheduler,
            config=ProactiveConfig(
                briefing_enabled=settings.briefing_enabled,
                briefing_time=settings.briefing_time,
                deadline_enabled=settings.deadline_enabled,
                deadline_check_time=settings.deadline_check_time,
                # 日历源与系统监控本期不接线（二期），传 None 即优雅降级
                calendar_enabled=False,
                profile_maintenance_enabled=True,
            ),
            deadline_tracker=self._deadline_tracker,
            on_notify=self._on_notify,
            # 打扰预算闸门：settings 已注入 init_policy 时按配置建单例，
            # 未启用预算（notify_budget_enabled=False）时 None=直接播 @author aceFelix
            budget=get_budget(),
        )
        self._started = False
        self._catchup_timer: threading.Timer | None = None
        # W2 感知总线接线态（未启用时全部为 None/空，面板可据此区分“没装配”与“装配了但全关”）
        self._bus: Any = None
        self._bus_unsub: Callable[[], None] | None = None
        self._percept_status: dict[str, bool] = {}

    # ---- 对外只读访问（registry_hook 注册提醒/截止日期工具用） ----

    @property
    def scheduler(self) -> Scheduler:
        """内部 Scheduler 单例（register_schedule_tools 挂载点）。"""
        return self._scheduler

    @property
    def percept_bus(self) -> Any:
        """感知总线实例（未启用时为 None）；`percept.*` RPC 与面板读统计用。"""
        return self._bus

    @property
    def percept_status(self) -> dict[str, bool]:
        """逐源启用结果（装配时快照，供面板与日志）。"""
        return dict(self._percept_status)

    @property
    def deadline_tracker(self) -> DeadlineTracker:
        """内部 DeadlineTracker 单例（register_deadline_tools 挂载点）。"""
        return self._deadline_tracker

    # ---- 生命周期 ----

    def start(self) -> None:
        """启动调度器与主动感知引擎，并做简报补播检查（幂等）。"""
        if self._started:
            return
        self._started = True
        self._scheduler.start()
        self._engine.start()
        # W2 感知层：总开关打开才装配总线（关着时不起线程、不注册采集器）
        self._start_percept()
        # 补播判定仅在进程启动时做一次，不持久化（重启不重复补）
        if self.missed_briefing_minutes() is not None:
            self._catchup_timer = threading.Timer(
                CATCHUP_DELAY_SEC, self._fire_catchup_briefing
            )
            self._catchup_timer.daemon = True
            self._catchup_timer.start()

    def stop(self) -> None:
        """停止引擎与调度器（幂等）。先撤补播定时器再停引擎。"""
        if not self._started:
            return
        self._started = False
        # 打断正在朗读的 TTS（避免停机后残留语音播完才停）
        tts = self._current_tts
        if tts is not None:
            try:
                tts.stop()
            except Exception:
                pass
        if self._catchup_timer is not None:
            self._catchup_timer.cancel()
            self._catchup_timer = None
        # 先断感知源（否则引擎已停、总线还在往队列投事件），再停引擎与调度器
        self._stop_percept()
        self._engine.stop()
        self._scheduler.stop()

    def hot_update_schedule(self) -> None:
        """桌面 settings.set 改 briefing/deadline 后：同步 Settings 现值并重注册每日任务。

        调度任务是引擎启动时快照，不重注册则新开关/新时间需重启才生效；
        tts/proactive_tts 等项每次播报现读 Settings，无需在此同步。

        @author aceFelix
        """
        self._engine.update_schedule_config(
            briefing_enabled=self._settings.briefing_enabled,
            briefing_time=self._settings.briefing_time,
            deadline_enabled=self._settings.deadline_enabled,
            deadline_check_time=self._settings.deadline_check_time,
        )

    def acknowledge(self, task_id: str) -> bool:
        """确认提醒任务（桌面壳 proactive.ack 指令入口，停止升级重发）。"""
        return self._scheduler.acknowledge(task_id)

    # ---- 到期/通知回调（复刻老 NotificationMixin 语义，通道换成事件流） ----

    def _on_fire(self, task: ScheduleTask) -> None:
        """定时任务到期回调（Scheduler 后台线程触发）。

        ProactiveEngine 注册的任务转引擎自处理（生成简报/检查截止日期）；
        用户提醒任务投 proactive_notify 事件（老语义：托盘通知 + TTS 播报，
        桌面壳语义：聊天气泡 + 系统通知）。
        """
        if self._engine.is_proactive_task(task):
            self._engine.handle_task_fire(task)
            return
        self._emit(kind="reminder", title="贾维斯提醒", text=task.content, task_id=task.id)

    def _on_notify(self, message: str) -> None:
        """主动感知引擎通知回调（每日简报 / 截止日期提醒共用出口）。

        老语义按内容无差别走托盘 + TTS；这里按固定前缀区分 kind，
        让桌面壳能对简报与截止日期提醒做差异化渲染。
        """
        kind = "deadline" if message.startswith(_DEADLINE_PREFIX) else "briefing"
        self._emit(kind=kind, title="贾维斯主动提醒", text=message)

    def _emit(self, *, kind: str, title: str, text: str, task_id: str = "") -> None:
        """投递 proactive_notify 事件进 serve 事件队列（线程安全），并并行触发 TTS 朗读通道。"""
        self._event_queue.put_nowait(
            {
                "type": protocol.EVT_PROACTIVE_NOTIFY,
                "payload": {
                    "kind": kind,
                    "title": title,
                    "text": text,
                    "task_id": task_id,
                },
            }
        )
        self._spawn_tts(kind, text)

    # ---- W2 感知层接线（阶段 9 第 4 步：总线 → 同一个 W1 闸门 → 事件流） ----

    def _start_percept(self) -> None:
        """装配感知总线：注入配置 → 登记源 → 订阅 → 起轮询线程。

        总开关 `percept_enabled` 关着时直接返回（不注册也不订阅）：既然后续
        一切入口都以总线为中枢，装配与否就应当与配置一致，否则面板上会出现
        “没装配”与“装配了但全关”两种无法区分的状态。本路径全程不得抛异常：
        感知是增强能力，装配失败只能降级为“不感知”，不能拖垮 serve 启动。

        @author aceFelix
        """
        if not getattr(self._settings, "percept_enabled", False):
            return
        try:
            from agent.core.percept import init_percept, register_sources

            bus = init_percept(self._settings)
            self._percept_status = register_sources(bus, self._settings)
            self._bus_unsub = bus.subscribe(self._on_percept_event)
            self._bus = bus
            started = bus.start()
            logger.info(
                "感知总线已装配：启用源 %s，轮询线程%s",
                ",".join(sorted(k for k, v in self._percept_status.items() if v)) or "（无）",
                "已起" if started else "未起（无需轮询的源）",
            )
        except Exception as e:
            logger.warning("感知总线装配失败，本次不启用感知: %s", e)
            self._bus = None
            self._bus_unsub = None

    def _stop_percept(self) -> None:
        """退订并释放感知源（幂等；没装配过就是空操作）。

        先退订再停线程：订阅方持有 self，不退订则总线单例里会留着
        一个指向已停 hub 的回调（重启 hub 时同一个单例会把旧回调也叫起来）。
        `shutdown_sources()` 负责真正释放资源（IMAP 登出、关摄像头）。

        @author aceFelix
        """
        unsub = self._bus_unsub
        self._bus_unsub = None
        if callable(unsub):
            try:
                unsub()
            except Exception as e:  # 退订失败不影响后面的释放
                logger.warning("感知订阅退订异常: %s", e)
        bus = self._bus
        self._bus = None
        if bus is None:
            return
        try:
            bus.stop()
            bus.shutdown_sources()
        except Exception as e:
            logger.warning("感知总线停机异常: %s", e)

    def _on_percept_event(self, event: Any) -> None:
        """总线投递来的一条感知事件：先做状态迁移，再决定要不要打扰。

        本回调跑在总线轮询线程里（或 push 型源的自有线程），所以只能做
        内存安全的事：拼文本、过闸门（预算文件读写）、投事件队列。
        任何异常都不能外溢——总线虽然对订阅方做了隔离，但这里自己吃掉
        并记日志能给出更准确的上下文。

        @author aceFelix
        """
        try:
            self._apply_presence(event)
            if getattr(event, "kind", "") in PERCEPT_SILENT_KINDS:
                return
            text = event.summary()
            if not text:
                return
            emitted = self._engine.gate_notify(
                f"percept:{event.source}",
                text,
                # 去重键沿用总线口径：预算侧聚合与总线去重对齐，不会“总线觉得
                # 是新事、预算觉得是旧事”各算一份
                dedup_key=event.key(),
                urgency=float(getattr(event, "salience_hint", 0.5) or 0.5),
            )
            if emitted is None:
                return
            self._emit(kind=PERCEPT_NOTIFY_KIND, title=PERCEPT_NOTIFY_TITLE, text=emitted)
        except Exception as e:
            logger.warning("感知事件处理失败(%s.%s): %s",
                          getattr(event, "source", "?"), getattr(event, "kind", "?"), e)

    def _apply_presence(self, event: Any) -> None:
        """人脸进/出画面 → W1 的 away 推断（补上阶段 8 递延的视觉不在场判定）。

        为什么“离开”靠摄像头、“回来”不全靠摄像头：视觉源空闲时会自动关摄像头
        （隐私与 CPU），而人回来时摄像头可能已关。所以 `is_away()` 给视觉推断加了
        有效期（`VISION_AWAY_TTL_MIN`），到期自动不再压制播报；手动 /away 不参与，
        摄像头也不得替用户取消（见 `set_presence_away`）。

        @author aceFelix
        """
        if getattr(event, "source", "") != "vision":
            return
        kind = getattr(event, "kind", "")
        if kind == "face_disappear":
            active = True
        elif kind == "face_appear":
            active = False
        else:
            return
        try:
            from agent.core.policy import get_policy_state

            if get_policy_state().set_presence_away(active):
                logger.info("感知推断在场状态: %s", "离开" if active else "回到工位")
        except Exception as e:
            logger.warning("在场推断写入失败: %s", e)

    # ---- TTS 待机朗读通道（二期：复活老 daemon「待机 TTS」，开关 proactive_tts_enabled） ----

    def set_busy_probe(self, probe: Callable[[], bool] | None) -> None:
        """注入「贾维斯忙吗」探针（serve 宿主传 ``lambda: engine.is_busy``）。

        探针返回 True（对话轮次 / 语音会话进行中）时主动播报只推事件、
        不做 TTS 朗读，避免打断正在进行的对话或抢占语音通道；探针抛
        异常按忙处理（保守跳过朗读，事件通道不受影响）。

        @author aceFelix
        """
        self._busy_probe = probe

    def _spawn_tts(self, kind: str, text: str) -> None:
        """开关/忙时判定通过后，起独立守护线程朗读（不阻塞 Scheduler 线程）。"""
        if not getattr(self._settings, "proactive_tts_enabled", False):
            return
        if self._busy_probe is not None:
            try:
                if self._busy_probe():
                    return
            except Exception:
                return
        thread = threading.Thread(
            target=self._speak_worker,
            args=(kind, text),
            name="proactive-tts",
            daemon=True,
        )
        thread.start()

    def _speak_worker(self, kind: str, text: str) -> None:
        """TTS 朗读工作线程：清洗文本 → 按 kind 截断 → CosyVoice 整段播放。

        口径复刻老 daemon NotificationMixin：reminder 加「先生，提醒您：」
        前缀整段播；briefing/deadline 超 200 字只播前 200 字 + 引导后缀。
        语音依赖缺失（dashscope/pyaudio 未装）或朗读失败静默降级，
        不影响事件通道与调度器。

        @author aceFelix
        """
        try:
            from agent.voice.tts import CosyVoiceTTS
            from agent.voice.tts_text import clean_for_tts
            from agent.voice.voice_config import _voice_api_key
        except ImportError:
            return
        try:
            cleaned = clean_for_tts(text or "").strip()
            if not cleaned:
                return
            if kind == "reminder":
                speak_text = f"{_REMINDER_PREFIX}{cleaned}"
            else:
                speak_text = cleaned[:_TTS_TEXT_LIMIT]
                if len(cleaned) > _TTS_TEXT_LIMIT:
                    speak_text += _TTS_MORE_SUFFIX
            s = self._settings
            tts = CosyVoiceTTS(
                api_key=_voice_api_key(s),
                model=s.tts_model,
                voice=s.tts_voice,
                volume=s.tts_volume,
                speech_rate=s.tts_speech_rate,
                pitch_rate=s.tts_pitch_rate,
            )
            # 串行朗读：多条播报先后到期时排队播，避免 pyaudio 叠音
            with self._tts_lock:
                self._current_tts = tts
                try:
                    tts.speak(speak_text)
                finally:
                    self._current_tts = None
        except Exception:
            pass

    # ---- 简报补播 ----

    def missed_briefing_minutes(self) -> float | None:
        """计算今天简报错过多久（分钟），判定是否补播。

        补播窗口取自 ``settings.briefing_catchup_window_min``（默认 120 分钟）。

        Returns:
            需要补播时返回错过分钟数（0 < x ≤ 补播窗口）；否则 None
            （简报关闭 / 时间或窗口非法 / 时间未到 / 错过超窗口）。纯判定
            无副作用，便于单测。
        """
        if not self._settings.briefing_enabled:
            return None
        try:
            hour_s, minute_s = str(self._settings.briefing_time).split(":")
            target = datetime.now().replace(
                hour=int(hour_s), minute=int(minute_s), second=0, microsecond=0
            )
            # 窗口一并入 try：配置写了非法值时安全降级为“不补播”，不拖垮 serve 启动
            window_min = float(self._settings.briefing_catchup_window_min)
        except (ValueError, IndexError, TypeError):
            return None
        missed_min = (datetime.now() - target).total_seconds() / 60.0
        if 0 < missed_min <= window_min:
            return missed_min
        return None

    def _fire_catchup_briefing(self) -> None:
        """补播每日简报（Timer 线程触发）。异常静默，不影响 serve 主流程。"""
        try:
            self._engine._fire_briefing()
        except Exception:
            pass
