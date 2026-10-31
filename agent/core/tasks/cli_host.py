"""W3 兑现层的 CLI 宿主装配体：账本遗留处理 + 任务脊柱驱动器起停。

为什么单独一个文件（而不是写在 ``main.py`` 里）：REPL 的启动路径已经很长，
把"作废上次遗留的待发动作 + 装后台驱动器 + 退出回收"堆进 ``main.py`` 只会让它
继续超出单文件规模上限；且 serve 侧同类装配在 ``serve/hub.py`` 里已经有一份，
宿主各留一处薄调用比两处都写满细节更好维护。

与 ``core/daemon/cli_reminder.py`` 的 ``CliReminderHost`` 同构：宿主只管起停，
推进与播报口径全在 ``driver.py``，播报出口沿用 ``ui.notify_reminder``——它是唯一
能在 prompt_toolkit 持有屏幕时安全插播的入口（直接 print 会把输入行顶掉）。

@author aceFelix
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jarvis.tasks")


class CliSpineHost:
    """REPL 侧的 W3 装配体（账本遗留处理 + 脊柱驱动器）。

    用法::

        host = CliSpineHost(settings, ui=ui, provider=provider, session_name=name)
        host.start()      # 作废遗留待发动作 + 起驱动线程（禁用态什么都不做）
        ...
        host.stop()       # 退出前收线程，别拖住进程退出

    @author aceFelix
    """

    def __init__(
        self,
        settings: Any,
        *,
        ui: Any,
        provider: Any = None,
        session_name: str = "",
    ) -> None:
        """
        Args:
            settings: 全局配置（读 ``[actions]`` / ``[tasks]`` 字段）。
            ui: CLI 实例（用 ``warn`` 报遗留动作、``notify_reminder`` 播目标结果）。
            provider: 当前会话的 LLM provider；驱动器复用它跑步骤子代理。
            session_name: 会话名（写进副作用账本，让 /undo 报得出"哪一轮"）。
        """
        self._settings = settings
        self._ui = ui
        self._provider = provider
        self._session_name = session_name
        self._driver: Any = None

    @property
    def driver(self) -> Any:
        """已装配的驱动器（脊柱禁用/装配失败时为 None；台账读侧不依赖它）。"""
        return self._driver

    def start(self) -> None:
        """装配账本并作废遗留待发动作，再按开关起驱动线程。

        本方法不得抛出：账本与脊柱都是增强能力，装配失败只降级为"不记账/不推进"，
        不能让 REPL 起不来。

        @author aceFelix
        """
        try:
            from agent.bootstrap import init_actions, init_extension_switches

            # 任务宿主是子进程，同样不经 main()：技能包/插件开关得在这里补一次
            init_extension_switches(self._settings)
            for stale in init_actions(self._settings):
                self._ui.warn(
                    f"上次进程遗留一项未发出的预约动作已按取消处理：{stale.label}（/undo list 可查）"
                )
        except Exception as e:  # noqa: BLE001 - 记账能力异常不影响对话
            logger.warning("副作用账本装配失败，本次不记账: %s", e)

        if not bool(getattr(self._settings, "tasks_enabled", False)):
            return
        try:
            from agent.core.tasks.driver import build_driver, make_gate_notifier

            def _emit(text: str) -> None:
                notify = getattr(self._ui, "notify_reminder", None) or self._ui.info
                notify(text)

            self._driver = build_driver(
                self._settings,
                notifier=make_gate_notifier(_emit),
                provider=self._provider,
                session_name=self._session_name,
                workdir=str(getattr(self._settings, "workdir", "") or ""),
            )
            reset = self._driver.resume()
            started = self._driver.start()
            if getattr(self._settings, "verbose", False):
                self._ui.info(
                    f"✓ 任务脊柱已启动（驱动线程{'已起' if started else '未起（provider 不可用）'}，"
                    f"复位上次中断步骤 {reset} 个）"
                )
        except Exception as e:  # noqa: BLE001 - 脊柱异常不影响 REPL
            logger.warning("任务脊柱装配失败，本次不自动推进目标: %s", e)
            self._driver = None

    def stop(self) -> None:
        """停驱动线程并清除进程级句柄（幂等）。

        句柄必须一起清：驱动器闭包持有 notifier→ui，留着它下次 ``get_driver()``
        会取到一个指向已退出会话的实例。
        @author aceFelix
        """
        driver, self._driver = self._driver, None
        if driver is None:
            return
        try:
            driver.stop()
        except Exception:
            pass
        try:
            from agent.core.tasks.driver import set_driver

            set_driver(None)
        except Exception:
            pass
