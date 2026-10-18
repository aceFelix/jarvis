"""percept.* 桌面指令（标题栏「管家服务 → 感知源」面板）—— RPC 混入。

两条指令（aceFelix，2026-10 W2 第 5 步）：

- ``percept.status.get``（只读）：总开关 / 逐源启用 / 总线计数 / 源自报细节 /
  最近事件 + 留痕文件路径，一条指令把面板要的信息全带上（面板不做多路拼装）；
- ``percept.config.set``（写）：改 `[percept]` 白名单里的开关与名单，
  **先落盘 settings.toml 再改运行时**，然后让宿主重装配总线（逐源开关是装配期
  快照，不重装配就变成"面板显示已开、实际没在采"的假可用）。

安全范式与 `email.config.*` 同一条线：字段白名单在
`agent/config/percept_settings.py`（只放开关与名单，极客参数不入面板），
白名单外的键直接报错而不是静默忽略。感知域没有任何密钥，故读侧无需脱敏；
真正敏感的是"采了什么"，所以读侧反而要把最近事件原样回传（隐私自查承诺）。

依赖宿主类（`DesktopBridgeServer`）提供：``_settings`` / ``_hub``。

@author aceFelix
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agent.config.percept_settings import (
    apply_percept_config,
    read_percept_config,
    save_percept_config,
    validate_percept_config,
)
from agent.core.percept.status import build_status

if TYPE_CHECKING:  # 仅为 IDE/类型检查声明宿主属性，运行期不引入循环导入
    from agent.config.settings import Settings

logger = logging.getLogger("jarvis.serve.rpc_percept")

#: WS 请求帧是 ``{"type": <指令名>, ...参数}`` 的**平铺**结构（见 `protocol.py`
#: 头部注释与桌面壳 `ws.ts::sendCommand`），分发时整份 dict 原样交给 handler，
#: 所以 `data` 里必然带协议信封键。它们属于传输层、不是配置项，白名单校验前
#: 必须剥掉——否则面板每一次写回都会被「不支持的感知配置项 type」挡掉（真实
#: 客户端 100% 必现；单测直传业务 dict 所以测不出来，2026-10 人工走查才抓到）。
TRANSPORT_ENVELOPE_KEYS = frozenset({"type", "id"})


class PerceptRpcMixin:
    """percept.status.get / percept.config.set 两个指令的处理器。

    @author aceFelix
    """

    if TYPE_CHECKING:
        _settings: "Settings"
        _hub: Any

    def _rpc_percept_status_get(self, data: dict) -> dict:
        """percept.status.get：感知层状态快照（面板渲染 + 隐私自查）。

        总线实例只从宿主（ProactiveHub）取——serve 没跑或总开关关着时取到
        None，状态里的统计与事件段就照常返回空值，面板据此显"未启用"而不是
        报错（读侧永不抛异常是面板可用性的底线）。

        @author aceFelix
        """
        bus = getattr(self._hub, "percept_bus", None) if self._hub is not None else None
        kwargs: dict[str, Any] = {"hub_enabled": self._hub is not None}
        limit = data.get("event_limit")
        if isinstance(limit, int) and limit > 0:
            kwargs["event_limit"] = limit
        return self._percept_snapshot(bus, **kwargs)

    def _percept_snapshot(self, bus: Any, **kwargs: Any) -> dict[str, Any]:
        """状态快照 + `config` 段（面板可编辑的 `[percept]` 原始值）。

        为什么在这里拼而不是写进 :func:`build_status`：core/percept 刻意不反向
        依赖配置读侧（见 status.py 模块头的分层说明），而 serve 正是两个域相遇的
        那一层。面板必须一次调用就拿全「在采什么」与「当初是怎么配的」——逐源
        enabled 只是 AND 后的结论，名单与阈值（file_watch_dirs / vcs_repos /
        mail_watch_senders / window_exclude_apps / 两个分钟数）只能从配置段取，
        否则编辑表单无从回填。

        @author aceFelix
        """
        status = build_status(self._settings, bus, **kwargs)
        status["config"] = read_percept_config(self._settings)
        return status

    def _rpc_percept_config_set(self, data: dict) -> dict:
        """percept.config.set：改感知配置（校验 → 落盘 → 运行时 → 重装配 → 回读）。

        顺序与 `settings.set` / `email.config.set` 完全一致：落盘失败就回执报错且
        **不动运行时**，避免"这次生效、重启回退"的口径分裂。落盘成功后必走
        `hot_update_percept()`（停旧总线会真正释放 IMAP 连接与摄像头，所以
        "面板上关掉就立刻停采"这条承诺在这里兑现）。

        回执额外带两个面板要用的字段：`hot_applied`（宿主是否真的重装配过；
        hub 不在或重装配抛异常都为 False，面板据此提示"需重启桌面壳"而不是假装
        已生效）与 `applied`（本次实际写入的规范化补丁）。

        @author aceFelix
        """
        # 先剥传输层信封，再进白名单：业务键里的未知键仍然照报（见常量注释）
        patch = validate_percept_config(
            {k: v for k, v in data.items() if k not in TRANSPORT_ENVELOPE_KEYS}
        )
        if not patch:
            raise ValueError("percept.config.set 至少需要一个有效的配置项")
        if not save_percept_config(patch):
            raise RuntimeError("settings.toml 落盘失败，请检查磁盘写权限")
        apply_percept_config(self._settings, patch)
        hot_applied = self._rewire_percept()
        if "enabled" in patch:
            # 总开关拨到哪一档就是用户对感知层的表态（W2 第 7 步的首次引导
            # 据此不再出现）；记账失败不能反过来说“配置没保存”
            self._record_percept_choice(bool(patch["enabled"]))
        bus = getattr(self._hub, "percept_bus", None) if self._hub is not None else None
        status = self._percept_snapshot(bus, hub_enabled=self._hub is not None)
        status["hot_applied"] = hot_applied
        status["applied"] = patch
        return status

    def _record_percept_choice(self, enabled: bool) -> None:
        """把面板上的总开关表态落进询问记录（consent），两条路共用一份真源。

        为什么需要：否则“已经在用感知层”与“还没被问过”只能从运行时开关推断，
        而开关随时能被改回 off；表态落盘后，即使明天关掉总开关也不会再弹
        一次引导。异常一律吃掉：这只是留档，不影响配置已落盘已生效的事实。

        @author aceFelix
        """
        try:
            from agent.core.percept.consent import record_choice

            record_choice("on" if enabled else "off")
        except Exception as e:  # noqa: BLE001 - 留档失败不影响本次保存
            logger.warning("感知表态记录失败: %s", e)

    def _rewire_percept(self) -> bool:
        """请宿主重装配感知总线；没宿主或装配报错都返回 False（配置已经落盘）。

        重装配失败不能反过来说“配置没写进去”：TOML 与运行时都已改完，
        只是本次会话还按旧拓扑跑。因此这里只降级回报，不抛异常。

        @author aceFelix
        """
        hot = getattr(self._hub, "hot_update_percept", None)
        if not callable(hot):
            return False
        try:
            hot()
        except Exception:  # noqa: BLE001 - 重装配失败仅影响“是否已生效”，不影响已落盘配置
            return False
        return True
