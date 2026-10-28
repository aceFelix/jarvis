"""ChatEngine 的只读状态面（从 engine.py 拆出）。

把「对外汇报当前状态」这一类只读属性/方法集中到一处：会话用量与上下文占用、
轮次与消息计数、会话名、当前模型/厂商/权限模式/思考强度、MCP 连接状态、是否忙碌。
它们只读取 self 上的状态，不改任何字段，是 serve 层（``WorkbenchAPI`` → 桌面壳右栏）
与前端面板的唯一数据口径。

拆分动机：engine.py 原 1252 行、单类 46 个方法，状态面与命令处理、语音编排混在一起，
读改都要在巨类里翻。方法体逐行原样搬迁（含 @property 装饰器），行为与拆分前一致；
以 mixin 形式挂回 ChatEngine，调用方（api.py / server.py / 测试）无需改动。

@author aceFelix
"""

from __future__ import annotations

from typing import Any


class EngineStatusMixin:
    """只读状态面的方法组（原样搬自 ChatEngine，行为不变）。"""

    # ---- 只读状态快照（serve 右栏数据源：cost.get / state.get 经 WorkbenchAPI 读取） ----

    @property
    def session_usage(self) -> dict[str, Any]:
        """会话级累计 token 用量（cost.get 数据源；引擎未装配/假 loop 时全 0）。

        除四类 token 外附 ``cache_hit_rate``（百分数，保留一位小数），口径
        统一由 Usage.cache_hit_rate 计算，与 REPL /cost 同一份实现；桌面壳
        用量卡直接展示该值，不再自己重算分母。@author aceFelix
        """
        usage = getattr(getattr(self, "_query_loop", None), "session_usage", None)
        if usage is None:
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "cache_hit_rate": 0.0,
            }
        return {
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_tokens": int(getattr(usage, "cache_read_tokens", 0) or 0),
            "cache_creation_tokens": int(getattr(usage, "cache_creation_tokens", 0) or 0),
            "cache_hit_rate": round(float(getattr(usage, "cache_hit_rate", 0.0) or 0.0), 1),
        }

    @property
    def dialog_count(self) -> int:
        """已完成对话轮数（cost.get 数据源；未装配时为 0）。"""
        return int(getattr(self, "_dialog_count", 0) or 0)

    @property
    def message_count(self) -> int:
        """当前会话消息条数（cost.get 数据源；未装配时为 0）。"""
        return len(getattr(self, "_messages", None) or [])

    @property
    def context_usage(self) -> dict[str, Any]:
        """上下文窗口占用估算（cost.get 数据源，桌面壳右栏用量卡）。

        口径与 REPL /context 完全一致：已用 token = 当前会话消息估算
        （estimate_tokens）+ system prompt 估算（estimate_text_tokens，system
        不在 messages 里但实占窗口）；窗口优先取引擎配置的 context_window
        （如用户设的 200000），未配置（0）回退保守假设值 128000。仅用于
        估算占比，非模型侧精确值。context_configured 区分窗口来源，供文案
        层区分「窗口」/「假设窗口」。@author aceFelix
        """
        from agent.core.memory.compactor import estimate_tokens, estimate_text_tokens

        loop = getattr(self, "_query_loop", None)
        messages = getattr(self, "_messages", None) or []
        system_prompt = str(getattr(loop, "_system", "") or "") if loop else ""
        window = int(getattr(loop, "context_window", 0) or 0) if loop else 0
        used = estimate_tokens(list(messages)) + (
            estimate_text_tokens(system_prompt) if system_prompt else 0
        )
        effective = window or 128000
        percent = round(used / effective * 100, 1) if effective else 0.0
        return {
            "context_used": int(used),
            "context_window": int(effective),
            "context_configured": bool(window),
            "context_percent": percent,
        }

    @property
    def session_name(self) -> str:
        """当前会话名（sessions.list 的 current 标记数据源；未装配时为空串）。

        会话名在新建/恢复/标题改名时都会变（_session_name），
        列表每次刷新现读现比，前端无需跟踪事件序列。@author aceFelix
        """
        return str(getattr(self, "_session_name", "") or "")

    @property
    def current_model(self) -> str:
        """当前生效的模型名（models.list 的 current / cost.get 数据源）。

        会话已装配 → QueryLoop 正在跑的模型（热切换即时更新）；未装配 →
        待生效的切换目标（models.select 早于首条消息时先记账）；
        都没有 → 启动配置快照。@author aceFelix
        """
        if getattr(self, "_session_ready", False):
            current = getattr(self, "_model", "")
        else:
            current = self._model_override
        return str(current or self._settings.model or "")

    @property
    def current_vendor(self) -> str:
        """当前生效模型的厂商（state.get 的 provider 字段数据源）。

        provider 端点快照随热切换更新（自定义模型可能换厂商），
        故不能一律读启动 settings。@author aceFelix
        """
        vendor = getattr(self._provider_settings, "provider", "")
        return str(vendor or self._settings.provider or "")

    @property
    def current_permission_mode(self) -> str:
        """当前权限模式（state.get 的 permission_mode 字段，桌面工作模式选择器初值）。

        permission_mode 为枚举时取 .value，兼容字符串。@author aceFelix
        """
        mode = getattr(self._settings, "permission_mode", "default")
        return str(getattr(mode, "value", mode) or "default")

    @property
    def current_thinking_effort(self) -> str:
        """当前思考强度档位（state.get 的 thinking_effort 字段，桌面思考选择器初值）。

        已装配则取 loop 实际生效档位，否则回退 settings（off/on/low/medium/high）。@author aceFelix
        """
        loop = getattr(self, "_query_loop", None)
        if loop is not None and getattr(self, "_session_ready", False):
            eff = loop.is_thinking_effort()
            if eff is not None:
                return str(eff)
        s = self._settings
        if not getattr(s, "enable_thinking", True):
            return "off"
        return str(getattr(s, "thinking_effort", "on") or "on")

    @property
    def mcp_status(self) -> dict[str, Any] | None:
        """MCP 连接结果快照（state.get 数据源；None=MCP 未启用或未装配）。"""
        return self._mcp_status

    @property
    def is_busy(self) -> bool:
        """是否处于对话轮次或语音会话（主动播报 TTS「忙时跳过」探针）。

        三路任一未结束即忙：文本 send 轮次（_send_task）、半双工语音
        （_voice_task）、实时双工（_talk_task）。忙时主动播报只推事件、
        不做 TTS 朗读，避免打断正在进行的对话或抢占语音通道。

        @author aceFelix
        """
        task = self._send_task
        if task is not None and not task.done():
            return True
        for attr in ("_voice_task", "_talk_task"):
            t = getattr(self, attr, None)
            if t is not None and not t.done():
                return True
        return False
