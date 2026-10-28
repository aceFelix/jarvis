"""ChatEngine 的全双工实时对话（/talk）编排（从 engine.py 拆出）。

负责桌面壳「实时对话」这条通路：``_handle_start_talk`` 起半双工式实时会话、
``_handle_start_talk_duplex`` 起全双工（浏览器采集 + 引擎协议层）、
``feed_talk_audio`` 把渲染进程送来的音频块喂给引擎、``_handle_stop_talk`` 收尾。

这里只做编排：音频设备与 UI 装配在桌面壳/适配器侧，协议状态机在
``agent.voice.realtime_engine``（及其拆出的 recv / rescue 模块）。

拆分动机：engine.py 原 1252 行、单类 46 个方法，实时对话两个 start 分支合计约 170 行，
与文本对话回合挤在同一个类里。方法体逐行原样搬迁，行为与拆分前一致。

@author aceFelix
"""

from __future__ import annotations

import asyncio


class EngineTalkMixin:
    """全双工实时对话（talk）启停与音频投喂的方法组（原样搬自 ChatEngine）。"""

    # ---- /talk 实时语音 ----

    async def _handle_start_talk(self, cmd: dict | None = None) -> None:
        """启动实时双工语音：独立任务跑 RealtimeTalk/RealtimeEngine（配置对齐 /talk）。"""
        import os

        if getattr(self, "_talk_task", None) is not None:
            self._emitter.emit("info", "实时对话已在运行")
            return
        # 与 /voice 互斥：进入实时语音前先停半双工语音，避免麦克风/扬声器冲突
        if getattr(self, "_voice_task", None) is not None:
            await self._handle_stop_voice()
        # 桌面端全双工桥接：渲染进程采集（浏览器 AEC）→ talk.audio 帧 →
        # RealtimeEngine(half_duplex=False) → talk_audio 事件回渲染进程播放
        if cmd and cmd.get("duplex"):
            await self._handle_start_talk_duplex()
            return
        s = self._settings
        api_key = (
            s.dashscope_api_key
            or os.environ.get("DASHSCOPE_API_KEY", "")
            or s.api_key
            or os.environ.get("OPENAI_API_KEY", "")
        )
        if not api_key:
            self._emitter.emit("error", "未配置 DashScope API Key，无法启动实时语音")
            return
        try:
            from agent.voice.realtime_talk import DEFAULT_WS_URL, RealtimeTalk
        except ImportError as e:
            self._emitter.emit("error", f"实时语音模块不可用: {e}")
            return

        rt = RealtimeTalk(
            api_key=api_key,
            model=getattr(s, "realtime_model", "qwen-audio-3.0-realtime-flash"),
            voice=getattr(s, "realtime_voice", "longanqian"),
            ws_url=getattr(s, "realtime_ws_url", "") or DEFAULT_WS_URL,
            workdir=getattr(s, "workdir", "") or os.getcwd(),
            # 事件时间线日志开关（[realtime_talk] event_log，默认关）
            event_log=bool(getattr(s, "realtime_event_log", False)),
            # AI 说话期间的麦克风二次压低（[realtime_talk] echo_suppress_with_aec，
            # 默认开：关掉后残余回声会打断并取消 AI 每一轮回复）
            echo_suppress_with_aec=bool(
                getattr(s, "realtime_echo_suppress_with_aec", True)
            ),
            # 半双工（默认开）：AI 说话期间不上传麦克风，规避外放回声卡住下一轮
            half_duplex=bool(getattr(s, "realtime_half_duplex", True)),
            # 响应救援/轮次检测/工具模式（对齐 /talk 命令，2026-09-28 重构新增）
            rescue=bool(getattr(s, "realtime_rescue", True)),
            turn_detection=getattr(s, "realtime_turn_detection", "server_vad"),
            silence_duration_ms=int(getattr(s, "realtime_silence_ms", 500)),
            tools_mode=getattr(s, "realtime_tools_mode", "builtin"),
        )
        self._talk_instance = rt

        async def _talk_main() -> None:
            try:
                await rt.run(self._realtime_ui)
            except Exception as e:
                self._emitter.emit("error", f"实时对话异常: {type(e).__name__}: {e}")
            finally:
                # 收尾落库：停止瞬间仍在配对缓冲里的转写残轮写入会话历史
                self._realtime_ui.flush_pending_transcripts()
                self._talk_task = None
                self._emitter.emit("talk_stopped", "")

        self._talk_task = asyncio.get_event_loop().create_task(_talk_main())
        self._emitter.emit("talk_started", "")

    async def _handle_start_talk_duplex(self) -> None:
        """桌面全双工会话：RealtimeEngine + BridgeMic/BridgeSpk（2026-09-28）。

        与 PyAudio 路径互斥的完整替代：音频由渲染进程采集（getUserMedia，
        浏览器内置 AEC 回声消除——WebSocket 协议官方标注无 AEC，这是桌面端
        能做真全双工的关键），经 talk.audio 帧直喂 BridgeMic；引擎输出经
        talk_audio 事件逐帧广播回渲染进程播放。半双工必须关闭（回声已由
        浏览器 AEC 负责），二次增益压低同步关闭。

        工具装配与 /talk 终端适配器同口径（settings 的 turn_detection /
        tools_mode 等全部生效）。
        """
        import base64 as _b64
        import os

        from agent.serve import protocol
        from agent.voice.realtime_bridge_audio import BridgeMic, BridgeSpk
        from agent.voice.realtime_engine import RealtimeEngine
        from agent.voice.realtime_mcp import load_mcp_tools_async as mcp_load_tools
        from agent.voice.realtime_talk import DEFAULT_WS_URL, default_instructions
        from agent.voice.realtime_tools import BUILTIN_TOOLS, build_all_tools

        s = self._settings
        api_key = (
            s.dashscope_api_key
            or os.environ.get("DASHSCOPE_API_KEY", "")
            or s.api_key
            or os.environ.get("OPENAI_API_KEY", "")
        )
        if not api_key:
            self._emitter.emit("error", "未配置 DashScope API Key，无法启动实时语音")
            return

        mic = BridgeMic()

        def _emit_audio(pcm: bytes) -> None:
            # 事件泵线程安全（queue.Queue），随 response.audio.delta 逐帧广播
            self._emitter.emit(
                protocol.EVT_TALK_AUDIO, _b64.b64encode(pcm).decode("ascii")
            )

        def _emit_flush() -> None:
            # 打断：远端清空播放队列（与引擎 spk.stop_stream 语义对齐）
            self._emitter.emit(protocol.EVT_TALK_AUDIO, "")

        spk = BridgeSpk(on_chunk=_emit_audio, on_flush=_emit_flush)
        engine = RealtimeEngine(
            api_key,
            model=getattr(s, "realtime_model", "qwen-audio-3.0-realtime-flash"),
            voice=getattr(s, "realtime_voice", "longanqian"),
            # 贾维斯人设指令（与 RealtimeTalk 终端路径同口径回退）：此路径直接
            # 构造父类 RealtimeEngine，无子类的默认话术回退，不传会得到空
            # instructions → DashScope 实时模型回落自带默认人设（自称"小云"）。
            instructions=default_instructions(
                getattr(s, "realtime_tools_mode", "builtin")
            ),
            ws_url=getattr(s, "realtime_ws_url", "") or DEFAULT_WS_URL,
            turn_detection=getattr(s, "realtime_turn_detection", "server_vad"),
            silence_duration_ms=int(getattr(s, "realtime_silence_ms", 500)),
            workdir=getattr(s, "workdir", "") or os.getcwd(),
            event_log=bool(getattr(s, "realtime_event_log", False)),
            # 全双工：浏览器 AEC 负责回声——关闭半双工静音、二次压低与
            # Python 侧 AEC（双重消除会让语音发闷/丢字）
            half_duplex=False,
            echo_suppress_with_aec=False,
            use_aec=False,
            rescue=bool(getattr(s, "realtime_rescue", True)),
        )
        engine.attach_audio(mic, spk)
        self._talk_mic = mic  # feed_talk_audio 的投递点

        # 工具装配（对齐 /talk 终端适配器的 tools_mode 口径）
        tools = list(BUILTIN_TOOLS)
        registry_tool_map: dict = {}
        if getattr(s, "realtime_tools_mode", "builtin") == "all":
            registry_tools, registry_tool_map = build_all_tools(engine._workdir)
            mcp_schemas: list = []
            try:
                mcp_schemas = await asyncio.wait_for(
                    mcp_load_tools(engine, self._realtime_ui), timeout=20.0
                )
            except Exception as e:
                self._emitter.emit("warn", f"MCP 工具加载异常: {e}")
            tools = list(BUILTIN_TOOLS) + registry_tools + mcp_schemas
        engine.set_tools(tools, registry_tool_map)
        self._talk_instance = engine

        async def _talk_main() -> None:
            try:
                await engine.run_session(self._realtime_ui)
            except Exception as e:
                self._emitter.emit("error", f"实时对话异常: {type(e).__name__}: {e}")
            finally:
                mic.close()
                spk.close()
                self._talk_mic = None
                # 收尾落库：停止瞬间仍在配对缓冲里的转写残轮写入会话历史
                self._realtime_ui.flush_pending_transcripts()
                self._talk_task = None
                self._emitter.emit("talk_stopped", "")

        self._talk_task = asyncio.get_event_loop().create_task(_talk_main())
        self._emitter.emit("talk_started", "")

    def feed_talk_audio(self, b64_frame: str) -> None:
        """serve 侧 talk.audio 指令入口：麦克风帧直喂当前 duplex 会话。"""
        mic = getattr(self, "_talk_mic", None)
        if mic is None:
            return
        try:
            import base64 as _b64
            mic.feed(_b64.b64decode(b64_frame))
        except Exception:
            pass

    async def _handle_stop_talk(self) -> None:
        """停止实时语音会话（窗口保持打开）。"""
        rt = getattr(self, "_talk_instance", None)
        if rt is not None:
            try:
                rt._running = False
            except Exception:
                pass
        mic = getattr(self, "_talk_mic", None)
        if mic is not None:
            try:
                mic.close()  # 唤醒 BridgeMic.read，让发送协程立即退出
            except Exception:
                pass
        task = getattr(self, "_talk_task", None)
        if task is not None:
            task.cancel()
            self._talk_task = None
        self._emitter.emit("talk_stopped", "")
