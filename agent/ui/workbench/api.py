"""工作台 JSBridge：暴露给前端 JS 的 pywebview API。

JS 通过 ``pywebview.api.*`` 调用，全部为轻量只读/入队操作，
不阻塞 GUI 线程。能力分四组：

- 事件拉取：poll_events（前端轮询引擎事件）
- 文本对话：send_message / new_session / list_sessions / load_session
- 实时语音：start_talk / stop_talk
- 左栏数据：list_models / set_model / list_voices / set_voice / get_state
- 窗口控制：window_minimize / window_close（无边框自绘标题栏；不提供全屏，
  启动即铺满工作区且真全屏会盖住任务栏）

@author aceFelix
"""

from __future__ import annotations

import queue
from typing import Any

from agent.config.settings import Settings
from agent.ui.workbench.engine import ChatEngine


class WorkbenchAPI:
    """pywebview js_api 对象：前端调用入口集合。

    @author aceFelix
    """

    def __init__(
        self,
        event_queue: queue.Queue[dict[str, Any]],
        command_queue: queue.Queue[dict[str, Any]],
        engine: ChatEngine,
        settings: Settings,
    ) -> None:
        self._event_queue = event_queue
        self._command_queue = command_queue
        self._engine = engine
        self._settings = settings
        self._window: Any = None  # 窗口句柄（供自绘标题栏控制按钮使用）

    def set_window(self, window: Any) -> None:
        """由 app.py 在建窗后注入窗口句柄。"""
        self._window = window

    # ---- 窗口控制（无边框窗口自绘标题栏） ----

    def window_minimize(self) -> None:
        """最小化到任务栏（不做托盘）。"""
        try:
            if self._window is not None:
                self._window.minimize()
        except Exception:
            pass

    def window_close(self) -> None:
        """关闭窗口（进程退出，引擎/守卫在 app.py 的 finally 里收尾）。"""
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass

    # ---- 事件 ----

    def poll_events(self) -> list[dict[str, Any]]:
        """JS 轮询获取引擎/采集线程产生的事件。"""
        items: list[dict[str, Any]] = []
        try:
            while True:
                items.append(self._event_queue.get_nowait())
        except queue.Empty:
            pass
        return items

    # ---- 文本对话 ----

    def send_message(self, text: str) -> None:
        """发送一条文本消息给对话引擎。"""
        self._post({"cmd": "send", "text": text})

    def new_session(self) -> None:
        """新建会话（清空中栏气泡）。"""
        self._post({"cmd": "new_session"})

    def load_session(self, name: str) -> None:
        """恢复指定历史会话到中栏。"""
        self._post({"cmd": "load_session", "name": name})

    def list_sessions(self) -> list[dict[str, Any]]:
        """历史会话列表（左栏面板数据源，按更新时间倒序）。"""
        try:
            from agent.core.memory.store import list_sessions

            return [
                {
                    "name": s.name,
                    "updated_at": s.updated_at,
                    "message_count": s.message_count,
                    "model": s.model,
                }
                for s in list_sessions()
            ]
        except Exception:
            return []

    def answer_user(self, text: str) -> None:
        """回答引擎的 ask_user 弹窗（权限确认等）。"""
        self._post({"cmd": "answer_user", "text": text})

    # ---- 实时语音 ----

    def start_talk(self) -> None:
        """启动 /talk 实时双工语音。"""
        self._post({"cmd": "start_talk"})

    def stop_talk(self) -> None:
        """结束实时语音会话（窗口保持）。"""
        self._post({"cmd": "stop_talk"})

    # ---- 左栏：模型与音色 ----

    def get_state(self) -> dict[str, Any]:
        """窗口初始状态：当前模型/音色/厂商等（前端首屏渲染）。"""
        s = self._settings
        return {
            "provider": s.provider,
            "model": s.model or "",
            "tts_voice": s.tts_voice,
            "realtime_model": getattr(s, "realtime_model", ""),
            "realtime_voice": getattr(s, "realtime_voice", ""),
            "workdir": s.workdir,
        }

    def list_models(self) -> list[dict[str, Any]]:
        """可选模型列表：与 REPL /models 对齐 = 内置模型（[llm.models]）+ 自定义模型（[llm.custom_models]）。

        当前模型置顶并标 current；厂商经 model_manager._infer_model_vendor 推断，
        内置名+自定义覆盖合并去重（同 /models 的 builtin/custom 合并规则）。
        """
        s = self._settings
        try:
            # 复用 /models 的厂商推断逻辑，避免两处口径不一致（导入失败时降级空厂商）
            from agent.model_manager import _infer_model_vendor
        except Exception:
            def _infer_model_vendor(name: str, cfg: dict | None = None) -> str:
                return s.provider or ""

        current = s.model or ""
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 当前模型置顶（即使它不在两张表里，也保证可见可标）
        if current:
            cfg = (s.custom_models or {}).get(current)
            cfg = cfg if isinstance(cfg, dict) else None
            desc = (s.models or {}).get(current, "")
            items.append({
                "name": current,
                "vendor": _infer_model_vendor(current, cfg),
                "current": True,
                "desc": desc if isinstance(desc, str) else "",
            })
            seen.add(current)

        # 内置模型表（项目级 [llm.models]，含用户级覆盖合并后的结果）
        for name, desc in (s.models or {}).items():
            if name in seen:
                continue
            cfg = (s.custom_models or {}).get(name)
            cfg = cfg if isinstance(cfg, dict) else None
            items.append({
                "name": name,
                "vendor": _infer_model_vendor(name, cfg),
                "current": False,
                "desc": desc if isinstance(desc, str) else "",
            })
            seen.add(name)

        # 自定义模型（/models 添加的，非内置的）
        for name, cfg in (s.custom_models or {}).items():
            if name in seen or not isinstance(cfg, dict):
                continue
            items.append({
                "name": name,
                "vendor": _infer_model_vendor(name, cfg),
                "current": False,
            })
            seen.add(name)
        return items

    def set_model(self, name: str) -> bool:
        """切换文本对话模型：持久化到 settings.toml（重启引擎生效）。"""
        try:
            from agent.config.model_registry import save_last_model

            return save_last_model(name)
        except Exception:
            return False

    def list_voices(self) -> list[dict[str, Any]]:
        """TTS 音色列表：当前音色 + 自定义音色（/tts-voice 添加的）。"""
        s = self._settings
        items: list[dict[str, Any]] = []
        if s.tts_voice:
            items.append({"name": s.tts_voice, "current": True})
        for name, cfg in (s.custom_voices or {}).items():
            if name == s.tts_voice:
                continue
            items.append({
                "name": name,
                "description": cfg.get("description", ""),
                "current": False,
            })
        return items

    def set_voice(self, name: str) -> bool:
        """切换 TTS 音色：持久化到 settings.toml。"""
        try:
            from agent.config.model_registry import save_tts_voice

            return save_tts_voice(name)
        except Exception:
            return False

    # ---- 内部 ----

    def _post(self, cmd: dict[str, Any]) -> None:
        """把指令放入引擎队列（非阻塞）。"""
        try:
            self._command_queue.put_nowait(cmd)
        except Exception:
            pass
