"""统一事件总线 —— W2 感知层的中枢（阶段 9）。

一句话：把 N 个感知源的 ``PerceptEvent`` 收进来，做**去重 / 节流 / 故障隔离 /
落盘留痕**，再投给订阅方；订阅方（通常是 serve 侧的 ProactiveHub 适配器）
拿去拼文本并交 W1 ``NotifyBudget`` 裁决"该不该现在打扰"。

与 W1 的分工（避免两道闸门语义重叠，这是本节最容易写歪的地方）：
- 总线管**事件层**的噪声：同一件事 5 分钟内只算一次、同源投递有最小间隔、
  明显低于地板分的直接丢；不做"打扰预算"计数，不关心静默时段。
- W1 管**打扰层**：静默时段只入库、窗口内聚合成一条、超日预算丢弃、
  away 期间暂缓。两者串起来才是完整链路，任何一侧都不必知道对方存在。

为什么用"单一轮询线程 + 可注入 now 的 tick()"而不是每源一个线程：
- 验收要求常驻 CPU 增量 < 1%、**无源开启时为 0**：线程只在至少有一个启用
  源时才起，且轮询周期取启用源的最小采样间隔，源数为 0 时不起线程；
- ``tick(now)`` 是纯函数式的到点判定，测试可以完全确定性地驱动"哪个源该采了"，
  不必 sleep 真实时间（本模块的测试全部走这条路）。

异步源（IMAP IDLE 这类服务器主动推的）不必实现 collect，直接调
``publish()`` 即可；总线对 pull 与 push 两种入水口一视同仁。

@author aceFelix
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from agent.core.percept.event import PerceptEvent, clamp_payload

logger = logging.getLogger("jarvis.percept")

#: 源采集回调：无参，返回若干事件（或单个）。
CollectFn = Callable[[], Iterable[PerceptEvent] | PerceptEvent | None]
#: 订阅回调：收到一条事件，返回值忽略。
SubscribeFn = Callable[[PerceptEvent], None]


@dataclass
class BusConfig:
    """总线参数（与 Settings 解耦：宿主负责把配置字段翻译到这里）。

    Attributes:
        dedup_window_s: 同一去重键在该窗口内只投递一次，重复条数记入统计。
        min_salience: 投递地板分，低于此值直接丢（只裁明显噪声；真正的
            重要度裁决在 W1 ``NotifyBudget``，默认 0 表示总线不裁）。
        history_max: 内存环形历史条数（诊断与桌面"最近事件"面板用）。
        auto_disable_after_errors: 单源连续失败达到该值即自动停用（故障
            隔离的硬保证：坏源不能无限烧 CPU），可经 set_source_enabled 复权。
        throttle_s: 每源投递最小间隔（秒），键为源名；未登记的源不节流。
            与 dedup 的区别：throttle 按源计数（不管是不是同一件事），
            dedup 按键计数（同一件事不管间隔多久才刚过窗口）。
    """

    dedup_window_s: int = 300
    min_salience: float = 0.0
    history_max: int = 200
    auto_disable_after_errors: int = 3
    throttle_s: dict[str, int] = field(default_factory=dict)

    def throttle_for(self, source: str) -> float:
        """取某源的投递最小间隔（未配置=0，即不节流）。"""
        try:
            return max(0.0, float(self.throttle_s.get(source, 0) or 0))
        except (TypeError, ValueError):
            return 0.0


@dataclass
class PublishResult:
    """一次投递的裁决结果（语义对齐 W1 ``NotifyAction``，便于日志统一阅读）。

    stage 取值：``emit`` 已投递 / ``dedup`` 窗口内重复合并 /
    ``throttle`` 同源投递过密 / ``low_salience`` 低于地板分 /
    ``no_subscriber`` 无人订阅（只记账不投递）。
    """

    stage: str
    reason: str
    event: PerceptEvent | None = None

    @property
    def emitted(self) -> bool:
        return self.stage == "emit"


class PollSource:
    """把一个采集函数包成源（既有件收编与测试都走这条最省事的路上来）。

    Attributes:
        name: 源名（须与 event 里的 source 一致，否则投递时会被纠正）。
        interval_s: 采样间隔秒数；0 表示"不由总线轮询"（纯 push 源）。
        collect: 采集函数，返回事件/事件列表/None。
    """

    def __init__(self, name: str, collect: CollectFn, *, interval_s: float = 0.0) -> None:
        self.name = name
        self.interval_s = float(interval_s)
        self._collect = collect

    def collect(self) -> Iterable[PerceptEvent] | PerceptEvent | None:
        """执行一次采集（异常不在此处理，由总线统一隔离）。"""
        return self._collect()


@dataclass
class _SourceRuntime:
    """一个源的运行时账本：开关、到点时间、故障计数。"""

    source: Any
    name: str
    interval_s: float
    enabled: bool = False
    last_run: float | None = None        # monotonic 基准的上次采集时刻
    events: int = 0                      # 该源 publish 成功投递的事件数
    collected: int = 0                   # 该源被采集出的原始条数（含被裁掉的）
    errors: int = 0                      # 累计异常次数
    consecutive_errors: int = 0          # 连续异常次数（成功一次即归零）
    last_error: str = ""                 # 末次异常摘要（面板显示"为什么停了"）
    auto_disabled: bool = False          # 是否因连续失败被自动停用


class PerceptBus:
    """感知事件总线：源注册 → 采集/投递 → 去重节流 → 订阅分发 → 留痕。

    用法::

        bus = PerceptBus(BusConfig(dedup_window_s=300), log_path=tmp / "events.jsonl")
        bus.register_source(PollSource("vcs", my_collector, interval_s=60))
        bus.set_source_enabled("vcs", True)
        bus.subscribe(lambda ev: forward_to_budget(ev))
        bus.start()          # 有启用源才起轮询线程
        ...
        bus.stop()

    线程安全：``publish`` 可能被源自己的线程调用（IDLE 推送），``tick``
    由总线线程调用，故所有账本读写都在 ``_lock`` 内。

    @author aceFelix
    """

    def __init__(self, config: BusConfig | None = None, *, log_path: Path | str | None = None) -> None:
        self.config = config or BusConfig()
        self._log_path = Path(log_path) if log_path else None
        self._lock = threading.RLock()
        self._sources: dict[str, _SourceRuntime] = {}
        self._subs: list[tuple[SubscribeFn, frozenset[str] | None, frozenset[str] | None]] = []
        # 去重键 → (首发 monotonic 秒, 窗口内合并条数)
        self._seen: dict[str, tuple[float, int]] = {}
        # 源名 → 上次成功投递的 monotonic 秒（throttle 判定）
        self._last_emit: dict[str, float] = {}
        self._history: deque[PerceptEvent] = deque(maxlen=max(1, self.config.history_max))
        self._counters = {"emit": 0, "dedup": 0, "throttle": 0, "low_salience": 0,
                          "no_subscriber": 0, "log_errors": 0, "subscriber_errors": 0}
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

    # ---- 源注册与开关 ----

    def register_source(self, source: Any, *, enabled: bool = False,
                        interval_s: float | None = None) -> None:
        """登记一个源。同名重复注册视为替换（宿主热重载配置的场景）。

        Args:
            source: 具备 ``name`` 与 ``collect()`` 的对象（``PollSource`` 或
                鸭子同型的任意类）。
            enabled: 是否立即启用。**默认 False**——逐源默认关是隐私口径，
                未获用户同意前总线一个字节都不采。
            interval_s: 覆盖源自身声明的采样间隔（0=不由总线轮询）。
        """
        name = str(getattr(source, "name", "") or "").strip()
        if not name:
            raise ValueError("感知源必须有非空 name")
        if not callable(getattr(source, "collect", None)):
            raise ValueError(f"感知源 {name} 缺少 collect() 方法")
        interval = float(source.interval_s if interval_s is None else interval_s)
        with self._lock:
            self._sources[name] = _SourceRuntime(
                source=source, name=name, interval_s=max(0.0, interval), enabled=enabled,
            )

    def unregister_source(self, name: str) -> bool:
        """摘除一个源（返回是否存在）。用于宿主退出时彻底解绑。"""
        with self._lock:
            existed = self._sources.pop(name, None) is not None
            if existed:
                self._last_emit.pop(name, None)
            return existed

    def source_names(self) -> list[str]:
        """已登记的全部源名（稳定顺序，面板与导出用）。"""
        with self._lock:
            return sorted(self._sources)

    def is_source_enabled(self, name: str) -> bool:
        with self._lock:
            rt = self._sources.get(name)
            return bool(rt and rt.enabled)

    def source_detail(self, name: str) -> dict[str, Any]:
        """源实例自己报的运行细节（鸭子型 `state()`，没这个方法就返回空 dict）。

        总线账本（events/errors/last_error）只到“投递了多少”，而“为什么一个
        字节都没采到”这类环境原因（没装依赖/摄像头不可用/白名单空）只有源
        自己知道。面板要能说清后者，故开这一个只读口子：取不到状态时给空
        dict，绝不抛（面板不能因为一个源的细节而白屏）。

        @author aceFelix
        """
        with self._lock:
            rt = self._sources.get(name)
            getter = getattr(rt.source, "state", None) if rt else None
        if not callable(getter):
            return {}
        try:
            detail = getter()
        except Exception:  # noqa: BLE001 - 源自报状态失败不影响总线其他部分
            return {}
        return detail if isinstance(detail, dict) else {}

    def set_source_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        """开/关某个源，返回新的源状态（桌面逐源开关的落地入口）。

        关闭时清掉该源的故障标记，让用户手动重开后是一次干净尝试；
        开启一个"从未登记"的源是配置错误，直接报错而不是静默新建，
        避免面板上出现采集不到的幽灵开关。

        **不擅自带线程**：本方法只改开关，宿主需自己调 ``start()``（总线在
        无轮询源时会自行退出并置 ``running=False``，故重开后 start 就能接上）。
        这样批量开关不会误起线程，测试也能纯确定性驱动 ``tick()``。
        """
        with self._lock:
            rt = self._sources.get(name)
            if rt is None:
                raise KeyError(f"未登记的感知源: {name}")
            rt.enabled = bool(enabled)
            if enabled:
                rt.auto_disabled = False
                rt.consecutive_errors = 0
                rt.last_error = ""
            else:
                rt.last_run = None
            return self._source_state_locked(rt)

    # ---- 订阅 ----

    def subscribe(self, handler: SubscribeFn, *, sources: Iterable[str] | None = None,
                  kinds: Iterable[str] | None = None) -> Callable[[], None]:
        """注册订阅方，返回退订函数。

        Args:
            sources / kinds: 可选过滤（None=收全部）。过滤在分发侧做，
                总线不因订阅方数量变化而改变任何投递裁决。
        """
        src_set = frozenset(sources) if sources is not None else None
        kind_set = frozenset(kinds) if kinds is not None else None
        with self._lock:
            self._subs.append((handler, src_set, kind_set))

        def _unsubscribe() -> None:
            with self._lock:
                self._subs[:] = [s for s in self._subs if s[0] is not handler]

        return _unsubscribe

    # ---- 投递主链路 ----

    def publish(self, event: PerceptEvent, *, now: float | None = None) -> PublishResult:
        """投递一条事件：地板分 → 去重 → 节流 → 落盘 → 分发。

        Args:
            event: 待投递事件。
            now: monotonic 基准时刻（测试注入用；None 取 ``time.monotonic()``）。
                与 ``tick(now)`` 共用同一时基，保证去重/节流窗口可被确定性验证。
        """
        if event is None or not getattr(event, "source", ""):
            return PublishResult("low_salience", "空事件被忽略")
        mono = time.monotonic() if now is None else float(now)
        key = event.key()

        with self._lock:
            rt = self._sources.get(event.source)
            # ① 地板分：只裁明显噪声，真正的重要度裁决在 W1
            if float(event.salience_hint) < self.config.min_salience:
                self._counters["low_salience"] += 1
                return PublishResult("low_salience",
                                     f"先验 {event.salience_hint:.2f} 低于地板 {self.config.min_salience:.2f}")

            # ② 去重窗口：同一件事只算一次，重复计数不投递
            self._prune_seen_locked(mono)
            seen = self._seen.get(key)
            if seen is not None and mono - seen[0] < self.config.dedup_window_s:
                merged = seen[1] + 1
                self._seen[key] = (seen[0], merged)
                self._counters["dedup"] += 1
                if rt is not None:
                    rt.collected += 1
                return PublishResult("dedup",
                                     f"{int(mono - seen[0])}s 内同事件已计（累计合并 {merged} 条）")

            # ③ 同源节流：不管是不是同一件事，投得太密就压掉
            gap_limit = self.config.throttle_for(event.source)
            last = self._last_emit.get(event.source)
            if gap_limit and last is not None and mono - last < gap_limit:
                self._counters["throttle"] += 1
                return PublishResult("throttle",
                                     f"同源投递间隔不足（{int(mono - last)}s < {int(gap_limit)}s）")

            # 通过三道闸：正式记账
            event.payload = clamp_payload(event.payload)
            self._seen[key] = (mono, 1)
            self._last_emit[event.source] = mono
            self._history.append(event)
            self._counters["emit"] += 1
            if rt is not None:
                rt.events += 1
                rt.collected += 1
            handlers = [h for (h, sset, kset) in self._subs
                        if (sset is None or event.source in sset)
                        and (kset is None or event.kind in kset)]
            if not handlers:
                self._counters["no_subscriber"] += 1

        # 落盘与分发都在锁外：磁盘慢/订阅方阻塞都不该卡住其他源的投递
        self._append_log(event)
        if not handlers:
            return PublishResult("emit", "已记账（当前无订阅方）", event)
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:  # 订阅方异常隔离：坏订阅方不能拖垮总线
                with self._lock:
                    self._counters["subscriber_errors"] += 1
                logger.warning("感知订阅方异常(%s.%s): %s", event.source, event.kind, e)
        return PublishResult("emit", "已投递", event)

    def tick(self, now: float | None = None) -> int:
        """驱动一轮采集：只调用"已启用 + 有采样间隔 + 已到点"的源。

        Returns:
            本轮**实际被采集的源个数**（不是事件个数）——轮询线程据此
            判断自己要不要继续，而事件条数在 ``stats`` 里按源记账。
        """
        mono = time.monotonic() if now is None else float(now)
        with self._lock:
            due = [rt for rt in self._sources.values()
                   if rt.enabled and rt.interval_s > 0
                   and (rt.last_run is None or mono - rt.last_run >= rt.interval_s)]
            for rt in due:
                rt.last_run = mono
            snapshot = [(rt.name, rt.source) for rt in due]
        for name, source in snapshot:
            self._collect_one(name, source, mono)
        return len(snapshot)

    def _collect_one(self, name: str, source: Any, now: float | None = None) -> None:
        """采集单源并逐条投递；异常按源隔离，连续失败自动停用。

        `now` 直接沿用 ``tick()`` 算到点用的同一个 monotonic 值，使去重/节流
        与调度共用一个时基（否则注入时间驱动测试时，投递窗口仍在走真实时钟）。

        自动停用是**故障隔离**的硬要求：源里的 pywin32/imaplib 一旦持续抛错，
        不能让它每秒重试把 CPU 烧满，也不能让它拖坏其他源。停用后面板上仍可
        见 last_error，用户可手动重开（set_source_enabled）。
        """
        try:
            produced = source.collect()
        except Exception as e:
            last_error, disabled, errors = "", False, 0
            with self._lock:
                rt = self._sources.get(name)
                if rt is not None:
                    rt.errors += 1
                    rt.consecutive_errors += 1
                    rt.last_error = f"{type(e).__name__}: {e}"[:200]
                    errors = rt.consecutive_errors
                    # 阀值为 0 表示不自动停用（只隔离不罢工）
                    disabled = 0 < self.config.auto_disable_after_errors <= errors
                    if disabled:
                        rt.enabled = False
                        rt.auto_disabled = True
                        last_error = rt.last_error
            if disabled:
                logger.warning("感知源 %s 连续 %s 次异常，已自动停用：%s",
                               name, errors, last_error)
            else:
                logger.warning("感知源 %s 采集异常（第 %s 次连续失败）: %s", name, errors, e)
            return

        events = _as_list(produced)
        # 成功即清零连败计数：偶发一次网络抖动不该累积到停用
        with self._lock:
            rt = self._sources.get(name)
            if rt is not None:
                rt.consecutive_errors = 0
        for ev in events:
            self.publish(ev, now=now)

    # ---- 生命周期 ----

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> bool:
        """启动轮询线程；无任何启用源则不起线程（验收口径：空载 CPU 增量为 0）。

        Returns:
            是否真正起了线程（幂等：已在跑则直接返回原状态）。
        """
        with self._lock:
            if self._running:
                return True
            active = [rt for rt in self._sources.values() if rt.enabled and rt.interval_s > 0]
            if not active:
                logger.info("感知总线未起线程：无启用且需轮询的源")
                return False
            self._running = True
            period = min(rt.interval_s for rt in active)
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(period,), name="jarvis-percept", daemon=True,
        )
        self._thread.start()
        logger.info("感知总线已启动，轮询周期 %.1fs，启用源 %s", period,
                    ",".join(sorted(rt.name for rt in self._sources.values() if rt.enabled)))
        return True

    def stop(self) -> None:
        """停止轮询线程（幂等）。源自身的释放由 ``shutdown_sources`` 负责。"""
        with self._lock:
            was_running = self._running
            self._running = False
        self._stop_evt.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if was_running:
            logger.info("感知总线已停止")

    def shutdown_sources(self) -> None:
        """逐个调用源的 ``close()``（若有），释放句柄/连接；异常同样隔离。

        与 stop() 分开是因为语义不同：stop 只停投递节拍，close 要断 IMAP
        连接、释放窗口句柄。每个源独立 try，保证一个源析构失败不影响其余。
        """
        with self._lock:
            pairs = [(rt.name, rt.source) for rt in self._sources.values()]
        for name, source in pairs:
            closer = getattr(source, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as e:
                    logger.warning("感知源 %s 释放异常: %s", name, e)

    def _loop(self, period: float) -> None:
        """轮询线程主体：tick 到点采集，无事可做则按最小节拍休眠。"""
        # 下限 0.5s：窗口源理论上可以要 1s 以内的间隔，但再密就没意义了，
        # 且会把"常驻 CPU < 1%"的预算吃光。
        base = max(0.5, float(period))
        while not self._stop_evt.is_set():
            try:
                did = self.tick()
            except Exception as e:  # tick 自身不该抛，兜底保住线程
                logger.warning("感知总线 tick 异常: %s", e)
                did = 0
            if did == 0:
                # 本轮无源可采：若已无任何需轮询的启用源就退出，
                # 让宿主下次 set_source_enabled 后重新 start() 决策
                if not self._has_pollable_source():
                    break
            sleep_for = self._sleep_seconds(base)
            if self._stop_evt.wait(sleep_for):
                break
        with self._lock:
            self._running = False

    def _has_pollable_source(self) -> bool:
        """是否还有"已启用且需轮询"的源（线程去留的唯一判据）。"""
        with self._lock:
            return any(rt.enabled and rt.interval_s > 0 for rt in self._sources.values())

    def _sleep_seconds(self, base: float) -> float:
        """下一轮休眠时长：取启用源里"最快到点"的剩余时间，避免空转。"""
        mono = time.monotonic()
        with self._lock:
            waitings = [rt.interval_s - (mono - rt.last_run)
                        for rt in self._sources.values()
                        if rt.enabled and rt.interval_s > 0]
        if not waitings:
            return base
        return max(0.05, min(base, min(waitings)))

    # ---- 观测与导出 ----

    def stats(self) -> dict[str, Any]:
        """运行统计：总线计数 + 每源状态（面板"正在观察什么"的数据源）。"""
        with self._lock:
            return {
                "running": self._running,
                "counters": dict(self._counters),
                "dedup_window_s": self.config.dedup_window_s,
                "history": len(self._history),
                "log_path": str(self._log_path) if self._log_path else "",
                "sources": {rt.name: self._source_state_locked(rt)
                            for rt in sorted(self._sources.values(), key=lambda r: r.name)},
            }

    def recent(self, limit: int = 20) -> list[PerceptEvent]:
        """最近若干条已投递事件（倒序，最新在前）。"""
        with self._lock:
            items = list(self._history)[-max(1, int(limit)):]
        return list(reversed(items))

    def export(self, limit: int = 200) -> dict[str, Any]:
        """采集范围一键导出（隐私自查用：用户能看见"到底采了什么"）。

        包含当前逐源开关、采样间隔、最近事件的完整 payload，
        不含任何隐藏字段——审计口径要求"可见即可导出"。
        """
        return {
            "config": {
                "dedup_window_s": self.config.dedup_window_s,
                "min_salience": self.config.min_salience,
                "throttle_s": dict(self.config.throttle_s),
            },
            "stats": self.stats(),
            "events": [ev.to_dict() for ev in self.recent(limit)],
        }

    def _prune_seen_locked(self, mono: float) -> None:
        """清掉过窗的去重键，防常驻进程里 ``_seen`` 无限增长。

        窗口源每 5 秒就能产一个新键，不剪枝就是内存泄漏。只在键数超过
        阈值时才扫（正常量级不付扫描代价）；剪枝只丢过期项，不影响
        窗口内重复合并的语义。调用方持锁。
        """
        window = max(1, int(self.config.dedup_window_s))
        if len(self._seen) <= max(64, window):
            return
        for k in [k for k, (ts, _n) in self._seen.items() if mono - ts >= window]:
            self._seen.pop(k, None)

    def _source_state_locked(self, rt: _SourceRuntime) -> dict[str, Any]:
        """单源状态快照（调用方持锁）。"""
        return {
            "enabled": rt.enabled,
            "interval_s": rt.interval_s,
            "events": rt.events,
            "collected": rt.collected,
            "errors": rt.errors,
            "auto_disabled": rt.auto_disabled,
            "last_error": rt.last_error,
        }

    # ---- 留痕落盘 ----

    def _append_log(self, event: PerceptEvent) -> None:
        """追加一行 JSONL；磁盘异常只记计数器与日志，绝不向上抛。

        数据仅本地落盘（隐私硬约束），路径由宿主传入；未传路径即完全
        不留痕（用户可在面板关掉"保留采集历史"的落地方式）。
        """
        if self._log_path is None:
            return
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            self._trim_log()
        except OSError as e:
            with self._lock:
                self._counters["log_errors"] += 1
            logger.warning("感知事件落盘失败(%s): %s", self._log_path, e)

    def _trim_log(self) -> None:
        """按行数上限裁剪 jsonl（保留最近 N 条，N = history_max*10）。

        常驻运行的历史文件不能无限增长；裁剪失败不影响本次写入，
        只在计数器里留痕，避免"因为清理失败而丢新事件"。
        """
        assert self._log_path is not None
        limit = max(1, self.config.history_max) * 10
        try:
            with open(self._log_path, "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return
        if len(lines) <= limit:
            return
        keep = "\n".join(lines[-limit:]) + "\n"
        tmp = self._log_path.with_suffix(self._log_path.suffix + f".tmp{os.getpid()}")
        try:
            tmp.write_text(keep, encoding="utf-8")
            os.replace(tmp, self._log_path)
        except OSError as e:
            logger.warning("感知历史裁剪失败: %s", e)


def _as_list(produced: Any) -> list[PerceptEvent]:
    """把 collect() 返回值归一成事件列表（None/单个/可迭代都接受）。"""
    if produced is None:
        return []
    if isinstance(produced, PerceptEvent):
        return [produced]
    if isinstance(produced, dict):
        return [PerceptEvent.from_dict(produced)]
    out: list[PerceptEvent] = []
    try:
        for item in produced:
            out.extend(_as_list(item))
    except TypeError:
        return []
    return out
