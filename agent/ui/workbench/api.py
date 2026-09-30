"""工作台 JSBridge：暴露给前端 JS 的 pywebview API。

JS 通过 ``pywebview.api.*`` 调用，全部为轻量只读/入队操作，
不阻塞 GUI 线程。能力分四组：

- 事件拉取：poll_events（前端轮询引擎事件）
- 文本对话：send_message / new_session / list_sessions / load_session
- 实时语音：start_talk / stop_talk
- 半双工语音：start_voice / stop_voice / interrupt_voice
- 左栏数据：list_models / set_model / list_voices / set_voice / add_voice /
  delete_voice / get_state（音色目录含适配模型与联动预告，与 REPL /tts-voice 同源）
- 窗口控制：window_minimize / window_close（无边框自绘标题栏；不提供全屏，
  启动即铺满工作区且真全屏会盖住任务栏）

@author aceFelix
"""

from __future__ import annotations

import queue
from typing import Any

from agent.config.settings import Settings
from agent.ui.workbench import model_admin
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

    def send_message(
        self,
        text: str,
        images: list[dict[str, Any]] | None = None,
        files: list[dict[str, Any]] | None = None,
    ) -> None:
        """发送一条消息（可带图片/文本文件附件）给对话引擎。

        - images：[{data: base64, media_type}]，走 vision 链路；
        - files：[{name, content}]，引擎拼进消息正文。
        校验在 serve/server.py 入队前完成，这里只做透传。

        @author aceFelix
        """
        payload: dict[str, Any] = {"cmd": "send", "text": text}
        if images:
            payload["images"] = images
        if files:
            payload["files"] = files
        self._post(payload)

    def new_session(self) -> None:
        """新建会话（清空中栏气泡）。"""
        self._post({"cmd": "new_session"})

    def load_session(self, name: str) -> None:
        """恢复指定历史会话到中栏。"""
        self._post({"cmd": "load_session", "name": name})

    def rename_session(self, name: str, new_name: str) -> None:
        """会话改名（成功由引擎推 session_renamed 刷列表）。@author aceFelix"""
        self._post({"cmd": "rename_session", "name": name, "new_name": new_name})

    def delete_session(self, name: str) -> None:
        """删除会话（成功推 session_deleted；删当前会话另推 session_new）。@author aceFelix"""
        self._post({"cmd": "delete_session", "name": name})

    def list_sessions(self) -> list[dict[str, Any]]:
        """历史会话列表（左栏面板数据源，按更新时间倒序）。

        每项带 current 标记（与引擎当前会话名现比），
        左栏据此渲染选中态：恢复/新建/改名后任一次刷新即自愈。@author aceFelix
        """
        try:
            from agent.core.memory.store import list_sessions

            current = self._engine.session_name
            return [
                {
                    "name": s.name,
                    "updated_at": s.updated_at,
                    "message_count": s.message_count,
                    "model": s.model,
                    # 会话归属的项目目录（桌面按当前项目过滤/分组历史会话）。
                    # @author aceFelix
                    "workdir": s.workdir,
                    "current": s.name == current,
                }
                for s in list_sessions()
            ]
        except Exception:
            return []

    def answer_user(self, text: str) -> None:
        """回答引擎的 ask_user 弹窗（权限确认等）。"""
        self._post({"cmd": "answer_user", "text": text})

    def abort_reply(self) -> bool:
        """停止当前回复（桌面壳发送按钮二次点击）。

        直接调引擎的线程安全取消（不经指令队列：队列被当前 send 轮次
        串行占用，入队会自死锁）。返回是否真的有轮次被取消。

        @author aceFelix
        """
        return self._engine.abort_current_reply()

    # ---- 实时语音 ----

    def start_talk(self, duplex: bool = False) -> Any:
        """启动 /talk 实时双工语音。

        Args:
            duplex: True 时走桌面全双工桥接（浏览器 getUserMedia 采集 → WS
                talk.audio 帧 → RealtimeEngine half_duplex=False），音频不经
                PyAudio，由渲染进程播放 talk_audio 事件。False 保持 PyAudio
                半双工路径（兼容旧客户端）。
        """
        self._post({"cmd": "start_talk", "duplex": duplex})
        return None

    def stop_talk(self) -> None:
        """结束实时语音会话（窗口保持）。"""
        self._post({"cmd": "stop_talk"})

    def feed_talk_audio(self, b64_frame: str) -> None:
        """桌面全双工会话的麦克风音频帧直喂（serve 的 talk.audio 指令入口）。

        走引擎直连（不在 command_queue 排队——50Hz 小帧排队会拖慢引擎指令
        循环）；无活动 duplex 会话时静默丢弃。
        """
        try:
            self._engine.feed_talk_audio(b64_frame)
        except Exception:
            pass

    # ---- 半双工语音（/voice） ----

    def start_voice(self) -> None:
        """启动 /voice 半双工语音（连续 听→答 循环）。"""
        self._post({"cmd": "start_voice"})

    def stop_voice(self) -> None:
        """结束半双工语音会话。"""
        self._post({"cmd": "stop_voice"})

    def interrupt_voice(self) -> None:
        """打断当前播报 / 推理（回到聆听）。"""
        self._post({"cmd": "interrupt_voice"})

    # ---- 左栏：模型与音色 ----

    def get_state(self) -> dict[str, Any]:
        """窗口初始状态：当前模型/音色/厂商 + MCP 连接快照（前端首屏与右栏渲染）。"""
        s = self._settings
        return {
            "provider": self._engine.current_vendor,
            "model": self._engine.current_model,
            "tts_voice": s.tts_voice,
            "realtime_model": getattr(s, "realtime_model", ""),
            "realtime_voice": getattr(s, "realtime_voice", ""),
            "workdir": s.workdir,
            # MCP 连接快照：{"connected": [...], "failed": [...], "tools": int}
            # 或 None（MCP 未启用/未装配）——右栏运行健康区块据此渲染
            "mcp": self._engine.mcp_status,
        }

    def get_cost(self) -> dict[str, Any]:
        """会话用量统计（serve cost.get 指令数据源，桌面壳右栏用量卡）。

        口径与 REPL /cost 一致：token 四类累计（输入/输出/缓存读/缓存写）
        取自引擎 QueryLoop.session_usage，另附对话轮数与消息条数。

        @author aceFelix
        """
        s = self._settings
        return {
            "provider": self._engine.current_vendor,
            "model": self._engine.current_model,
            **self._engine.session_usage,
            "dialogs": self._engine.dialog_count,
            "messages": self._engine.message_count,
        }

    def list_models(self) -> list[dict[str, Any]]:
        """可选模型列表：内置模型（[llm.models]）+ 自定义模型（[llm.custom_models]）。

        当前模型置顶并标 current；每项另带 source / removable / config 等管理
        元信息（桌面壳模型面板就地改删的依据，实现见 model_admin.list_models）。

        @author aceFelix
        """
        return model_admin.list_models(self._settings, self._engine)

    def set_model(self, name: str) -> bool:
        """切换文本对话模型：持久化到 ~/.jarvis/models.toml + 立即热切换引擎。

        写盘（last_model，重启后自动恢复）成功后把 ``{"cmd": "switch_model"}``
        入队，由引擎线程在自己的 asyncio 循环里替换运行中的 provider / QueryLoop：
        切换立即生效（models.list 的 current 随之移动），无需重启引擎；若此刻
        正有一轮回复在跑，切换在该轮结束后落地（指令队列串行）。入队即返回，
        不等引擎落地 —— 落地与失败分别由 model_switched / warn 事件回执。

        @author aceFelix
        """
        try:
            from agent.config.model_registry import save_last_model

            if not save_last_model(name):
                return False
        except Exception:
            return False
        self._post({"cmd": "switch_model", "name": name})
        return True

    def add_model(
        self,
        name: str,
        *,
        vendor: str = "deepseek",
        api_format: str = "openai",
        base_url: str = "",
        api_key: str = "",
        model_type: str = "text",
    ) -> dict[str, Any]:
        """添加（或覆盖）自定义模型：写用户级 models.toml 并即时更新内存配置。

        字段与持久化口径与 REPL /models → 添加其他模型 完全一致
        （实现见 model_admin.add_model）：base_url 留空按厂商推断；api_key
        非空时同步系统 keyring；写盘失败抛错，由 serve 回 ok=false。

        @author aceFelix
        """
        return model_admin.add_model(
            self._settings,
            name,
            vendor=vendor,
            api_format=api_format,
            base_url=base_url,
            api_key=api_key,
            model_type=model_type,
        )

    def edit_model(
        self,
        name: str,
        *,
        new_name: str = "",
        vendor: str = "",
        api_format: str = "",
        base_url: str = "",
        api_key: str = "",
        model_type: str = "",
    ) -> dict[str, Any]:
        """修改模型配置（桌面壳双击模型项 → 编辑表单 → models.edit）。

        内置模型名锁定、自定义模型可改名；api_key 留空=保持原 Key；改的正是
        运行中的模型时入队强制热切换（端点/类型立即生效）。实现见
        model_admin.edit_model。

        @author aceFelix
        """
        return model_admin.edit_model(
            self._settings,
            self._engine,
            self._post,
            name,
            new_name=new_name,
            vendor=vendor,
            api_format=api_format,
            base_url=base_url,
            api_key=api_key,
            model_type=model_type,
        )

    def remove_model(self, name: str) -> dict[str, Any]:
        """删除自定义模型（桌面壳右键模型项 → 删除按钮 → models.remove）。

        内置模型不可删（仅回退覆盖配置）；用户级 models.toml 中不存在该段时
        同样拒绝（项目级配置的模型删了会重启复活）。实现见
        model_admin.remove_model。

        @author aceFelix
        """
        return model_admin.remove_model(self._settings, self._engine, name)

    def list_voices(self) -> list[dict[str, Any]]:
        """TTS 音色全量目录：内置 + 自定义（/tts-voice 同源，REPL/桌面壳共用）。

        每项 {name, voice_id, description, vendor, model, current, custom,
        linked}：model = 适配模型（空 = 不限）；linked = 点选后将联动切换的
        TTS 模型（兼容/不限时 None，前端据此预告）；当前音色置顶（保留
        旧版「首项即当前」口径），自定义项 custom=True 供前端显删除/编辑。

        @author aceFelix
        """
        from agent.voice.tts_voices import aligned_tts_model, all_tts_voices
        s = self._settings
        voices = all_tts_voices(s)
        custom_names = set((s.custom_voices or {}).keys())
        items: list[dict[str, Any]] = []
        for name, cfg in voices.items():
            is_cur = name == s.tts_voice or cfg["voice_id"] == s.tts_voice
            items.append({
                "name": name,
                "voice_id": cfg["voice_id"],
                "description": cfg["description"],
                "vendor": cfg["vendor"],
                "model": cfg.get("model", ""),
                "linked": aligned_tts_model(cfg.get("model", ""), s.tts_model),
                "current": is_cur,
                "custom": name in custom_names,
            })
        # 当前置顶（稳定排序，其余保持目录序）
        items.sort(key=lambda it: not it["current"])
        return items

    def set_voice(self, name: str) -> dict[str, Any]:
        """切换 TTS 音色：含音色-模型硬约束联动与持久化（voices.select）。

        返回 {ok, name?, voice_id?, linked_model?, old_model?, error?}；
        音色不在目录内时 ok=False 带 error（不盲写未知 voice_id）。

        @author aceFelix
        """
        from agent.voice.tts_voices import apply_voice_switch
        res = apply_voice_switch(self._settings, name)
        if res is None:
            return {"ok": False, "error": f"音色未找到：{name}"}
        return {"ok": True, **res}

    def add_voice(self, payload: dict[str, Any]) -> dict[str, Any]:
        """添加/更新自定义 TTS 音色（桌面壳音色表单 → voices.add）。

        字段口径同 REPL /tts-voice 添加表单：name/voice_id 必填，
        model = 适配模型（可空 = 不限），description 可选；同名覆盖（编辑
        即重提）。持久化到 [tts.custom_voices]，非法入参抛 ValueError（由
        serve 回 ok=false 错误回执）。

        @author aceFelix
        """
        name = str(payload.get("name") or "").strip()
        voice_id = str(payload.get("voice_id") or "").strip()
        if not name:
            raise ValueError("缺少音色名 name")
        if not voice_id:
            raise ValueError("缺少音色 ID voice_id")
        s = self._settings
        # 内置音色名不可被自定义项遮蔽（保存会改内置项展示，重启复活冲突）
        from agent.voice.tts_voices import VOICE_CATALOG
        if name in VOICE_CATALOG and name not in (s.custom_voices or {}):
            raise ValueError(f"「{name}」是内置音色名，换一个名字")
        config = {
            "name": name,
            "voice_id": voice_id,
            "description": str(payload.get("description") or "").strip() or name,
            "vendor": "dashscope",
            "model": str(payload.get("model") or "").strip(),
        }
        from agent.config.model_registry import save_custom_voice
        if not save_custom_voice(name, config):
            raise ValueError("保存失败：找不到 ~/.jarvis/settings.toml")
        s.custom_voices[name] = config
        return {"ok": True, "name": name}

    def delete_voice(self, name: str) -> dict[str, Any]:
        """删除自定义 TTS 音色（桌面壳右键删除 → voices.delete）。

        仅自定义音色可删（内置音色后端拒绝）；删的是当前在用音色时仍允许
        （[tts] voice 值不变，下次合成仍用该 ID，与模型面板删当前模型同口径）。

        @author aceFelix
        """
        name = (name or "").strip()
        s = self._settings
        if name not in (s.custom_voices or {}):
            raise ValueError(f"仅自定义音色可删除，「{name}」不是自定义音色")
        s.custom_voices.pop(name, None)
        from agent.config.model_registry import remove_custom_voice
        remove_custom_voice(name)  # 段不存在返回 False，内存已清不阻断
        return {"ok": True, "name": name}

    # ---- 左栏：项目工作区 ----

    def set_project(self, path: str) -> None:
        """切换当前项目（工作目录）：入队 set_workdir，结果走 project_switched 事件。

        与 set_model 同口径：路径校验在 serve/server.py 入队前完成（绝对 +
        存目录），引擎侧重建（换 workdir → 重生提示词/重挂 harness/开新会话）
        在指令队列串行落地。入队即返回，不等引擎落地。

        @author aceFelix
        """
        self._post({"cmd": "set_workdir", "path": path})

    def get_project(self) -> dict[str, Any]:
        """当前项目：{workdir, name, persisted}（persisted = 已在 projects.toml 登记）。

        @author aceFelix
        """
        from pathlib import Path

        from agent.config import projects_registry

        workdir = str(self._settings.workdir or "")
        recent = {p["path"] for p in projects_registry.list_projects()}
        return {
            "workdir": workdir,
            "name": Path(workdir).name if workdir else "",
            "persisted": workdir in recent,
        }

    def list_projects(self) -> list[dict[str, Any]]:
        """最近项目列表（左栏项目区数据源，按 last_opened 倒序）。

        每项附 exists 标记（目录是否仍在），供前端对失效项置灰/提示移除。

        @author aceFelix
        """
        from pathlib import Path

        from agent.config import projects_registry

        items = projects_registry.list_projects()
        for it in items:
            it["exists"] = bool(it.get("path")) and Path(it["path"]).is_dir()
        return items

    def forget_project(self, path: str) -> bool:
        """从最近列表移除项目（不删磁盘目录）；返回是否确有该记录被移除。

        @author aceFelix
        """
        from agent.config import projects_registry

        return projects_registry.forget(path)

    # ---- 内部 ----

    def _post(self, cmd: dict[str, Any]) -> None:
        """把指令放入引擎队列（非阻塞）。"""
        try:
            self._command_queue.put_nowait(cmd)
        except Exception:
            pass
