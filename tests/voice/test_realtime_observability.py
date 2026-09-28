"""实时语音可观测性回归测试 —— /talk 环境音转写与事件时间线。

覆盖 2026-09 第 1 层可观测性改动（背景：smart_turn 模式下服务端把
"检测到语音活动但语义判定为非有效轮次"的片段走 ambient_audio_transcription
事件透传且不写入对话上下文，客户端此前未订阅 → 表现为"一直在说、它不回复"）：

1. 环境音转写事件解析：delta 累积 → completed 定稿，字段缺失有兜底
2. 事件时间线日志：默认关闭零输出，开启后按序写 diag.log，高频事件只计数
3. 会话诊断摘要：只在确有语音被判定为非有效轮次时输出，并区分"有对话轮/无对话轮"
4. 拆分后的对外契约：realtime_talk.py 拆出 realtime_tools.py 后对外接口不变

响应生命周期与回声门控的回归用例见同目录
 test_realtime_response_lifecycle.py；共享替身与 fixture 抽到
 _fakes.py / conftest.py 供两个测试文件复用。

@author aceFelix
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.voice import realtime_events as ev
from agent.voice import realtime_talk as rt_mod
from agent.voice import realtime_tools as tools_mod
# 共享替身与 fixture（fake_diag / _stub_tools）已抽到 tests.voice._fakes 与 conftest
from tests.voice._fakes import FakeDiag as _FakeDiag
from tests.voice._fakes import FakeTalkUI as _FakeTalkUI
from tests.voice._fakes import FakeWS as _FakeWS


# ---- 事件摘要 ----


class TestBriefSummary:
    """_brief：把一条服务端事件压成单行摘要。"""

    def test_prefers_whitelist_fields(self) -> None:
        """白名单字段按序拼接，噪音字段不进摘要。"""
        brief = ev._brief({"item_id": "item_1", "text": "嗯", "noise": "x" * 500})
        assert "item_id=item_1" in brief
        assert "text=嗯" in brief
        assert "noise" not in brief

    def test_error_dict_uses_message(self) -> None:
        """error 是嵌套结构，只取其中的 message。"""
        brief = ev._brief({"error": {"message": "boom", "code": "x"}})
        assert brief == "error=boom"

    def test_truncates_long_text(self) -> None:
        """超长内容被截断，避免 session.updated 回带的工具定义撑爆日志。"""
        brief = ev._brief({"transcript": "长" * 500})
        assert len(brief) <= ev._BRIEF_LIMIT

    def test_falls_back_to_json(self) -> None:
        """白名单字段全缺失时回落截断的 JSON，不返回空串。"""
        brief = ev._brief({"unknown_field": "v"})
        assert "unknown_field" in brief

    def test_type_only_event_returns_empty(self) -> None:
        """事件体只有 type 时不重复它，时间线保持“#序号 事件类型”的干净形态。"""
        assert ev._brief({"type": "input_audio_buffer.speech_stopped"}) == ""


# ---- 环境音转写 ----


class TestAmbientTranscription:
    """环境音转写：delta 累积 → completed 定稿。"""

    def test_delta_accumulates_and_returns_empty(self) -> None:
        """delta 阶段只累积，不定稿（避免逐片刷屏）。"""
        log = ev.TalkEventLog()
        assert log.feed_ambient(ev.AMBIENT_DELTA, {"item_id": "i1", "text": "嗯"}) == ""
        assert log.feed_ambient(ev.AMBIENT_DELTA, {"item_id": "i1", "text": "啊"}) == ""
        assert log.ambient_turns == []

    def test_completed_falls_back_to_buffer(self) -> None:
        """completed 未带文本时，回落 delta 缓冲累积。"""
        log = ev.TalkEventLog()
        log.feed_ambient(ev.AMBIENT_DELTA, {"item_id": "i1", "text": "嗯"})
        log.feed_ambient(ev.AMBIENT_DELTA, {"item_id": "i1", "text": "啊"})
        assert log.feed_ambient(ev.AMBIENT_COMPLETED, {"item_id": "i1"}) == "嗯啊"
        assert log.ambient_turns == ["嗯啊"]

    def test_completed_prefers_transcript(self) -> None:
        """completed 带 transcript 时以它为准（与用户转写事件字段一致）。"""
        log = ev.TalkEventLog()
        log.feed_ambient(ev.AMBIENT_DELTA, {"item_id": "i1", "text": "旧"})
        final = log.feed_ambient(
            ev.AMBIENT_COMPLETED, {"item_id": "i1", "transcript": "定稿", "text": "次选"}
        )
        assert final == "定稿"
        assert log.ambient_turns == ["定稿"]

    def test_completed_accepts_text_field(self) -> None:
        """completed 直接给 text（文档示例字段名）也能定稿。"""
        log = ev.TalkEventLog()
        assert log.feed_ambient(ev.AMBIENT_COMPLETED, {"item_id": "i1", "text": "噪声"}) == "噪声"
        assert log.ambient_turns == ["噪声"]

    def test_completed_without_text_not_recorded(self) -> None:
        """完全拿不到文本时不记账，避免误报"被过滤"。"""
        log = ev.TalkEventLog()
        assert log.feed_ambient(ev.AMBIENT_COMPLETED, {"item_id": "i1"}) == ""
        assert log.ambient_turns == []

    def test_items_do_not_mix(self) -> None:
        """不同 item_id 的片段互不串扰，定稿时各自清缓冲。"""
        log = ev.TalkEventLog()
        log.feed_ambient(ev.AMBIENT_DELTA, {"item_id": "a", "text": "甲"})
        log.feed_ambient(ev.AMBIENT_DELTA, {"item_id": "b", "text": "乙"})
        assert log.feed_ambient(ev.AMBIENT_COMPLETED, {"item_id": "a"}) == "甲"
        assert log.feed_ambient(ev.AMBIENT_COMPLETED, {"item_id": "b"}) == "乙"
        assert log.ambient_turns == ["甲", "乙"]


# ---- 事件时间线 ----


class TestTimelineLog:
    """时间线日志：默认关闭零输出，开启后逐条落盘。"""

    def test_disabled_writes_nothing(self, fake_diag: _FakeDiag) -> None:
        """关闭时只计数，不产生任何日志。"""
        log = ev.TalkEventLog()
        assert log.enabled is False
        log.observe("conversation.item.created", {"item_id": "i1"})
        assert fake_diag.lines == []
        assert log.event_total == 1

    def test_enabled_writes_one_line_per_event(self, fake_diag: _FakeDiag) -> None:
        """开启后每条非高频事件写一行，带序号与事件类型。"""
        log = ev.TalkEventLog(enabled=True)
        log.observe("conversation.item.created", {"item_id": "i1"})
        log.observe("response.done", {})
        assert len(fake_diag.lines) == 2
        assert fake_diag.lines[0][0] == "realtime"
        assert "#1 conversation.item.created" in fake_diag.lines[0][1]
        assert "#2 response.done" in fake_diag.lines[1][1]

    def test_aggregated_events_counted_not_logged(self, fake_diag: _FakeDiag) -> None:
        """音频/流式转写 delta 只计数：否则音频帧会瞬间刷爆轮转日志。"""
        log = ev.TalkEventLog(enabled=True)
        log.observe("response.audio.delta", {"delta": "base64" * 100})
        log.observe("response.audio_transcript.delta", {"delta": "好"})
        assert fake_diag.lines == []
        assert log.aggregated_events == 2
        assert log.event_total == 2

    def test_detail_overrides_auto_brief(self, fake_diag: _FakeDiag) -> None:
        """调用方给的 detail 优先，用于标注 speech_started 时是否有活动回复。"""
        log = ev.TalkEventLog(enabled=True)
        log.observe("input_audio_buffer.speech_started", {}, detail="active_response=True")
        assert "active_response=True" in fake_diag.lines[0][1]

    def test_note_client_shares_timeline(self, fake_diag: _FakeDiag) -> None:
        """客户端指令与服务端事件共用一条时间线，→ 前缀区分来源。

        这是定位“每轮回复被打断取消”的关键：不区分来源就分不出是服务端
        收到新语音（真打断）还是客户端自己补发了 response.cancel。
        2026-09-28：detail 并入回溯链标签（→response.cancel user interrupt），
        取消归因时不用猜当时的状态。
        """
        log = ev.TalkEventLog(enabled=True)
        log.observe("response.created", {})
        log.note_client("response.cancel", "user interrupt")

        assert log.recent == ["response.created", "→response.cancel user interrupt"]
        assert "→response.cancel user interrupt" in fake_diag.lines[-1][1]

    def test_note_client_silent_when_disabled(self, fake_diag: _FakeDiag) -> None:
        """关闭时客户端指令同样不落盘（保持零行为风险）。

        回溯链带 detail：mic 静音/恢复上传一眼可辨（诊断"打断时麦克风
        到底有没有在上传音频"不再靠猜）。
        """
        log = ev.TalkEventLog()
        log.note_client("mic", "静音")

        assert fake_diag.lines == []
        assert log.recent == ["→mic 静音"]  # 计数与回溯不受开关影响

    def test_cancel_hint_orders_newest_first(self) -> None:
        """取消回溯提示按“新→旧”排列，且只收语义节点（音频帧被过滤）。

        链长有限：每轮响应的尾部事件（content_part/audio/transcript）若入链
        会把关键节点挤出去，之前 8 条全被单轮尾部占满，看不到这一轮是怎么开始的。
        """
        log = ev.TalkEventLog()
        log.observe("response.created", {})
        log.observe("response.audio.delta", {"delta": "x"})
        log.observe("response.audio.delta", {"delta": "y"})
        log.observe("input_audio_buffer.speech_started", {})
        log.observe("response.done", {"response": {"status": "cancelled"}})

        assert log.cancel_hint() == (
            "response.done[cancelled] ← input_audio_buffer.speech_started"
            " ← response.created"
        )

    def test_chain_records_output_item_type(self) -> None:
        """输出项节点带 item.type：function_call 与 message 直接可分（工具轮判定）。"""
        log = ev.TalkEventLog()
        log.observe("response.output_item.added", {"item": {"type": "function_call"}})
        log.observe("response.output_item.added", {"item": {"type": "message"}})

        assert log.cancel_hint() == (
            "output_item.added(message) ← output_item.added(function_call)"
        )

    def test_dump_cancel_diag_writes_even_when_disabled(
        self, fake_diag: _FakeDiag
    ) -> None:
        """取消诊断快照直接落盘，与 event_log 开关无关。

        用户反馈“又取消了”时往往已无法复现；落盘一份链 + 累计次数，
        事后直接从日志末尾取用。
        """
        log = ev.TalkEventLog()  # 默认关闭时间线日志
        log.note_cancelled()
        log.observe("response.created", {})
        log.dump_cancel_diag()

        assert len(fake_diag.lines) == 1
        assert fake_diag.lines[0][0] == "realtime"
        assert "取消诊断 第1次" in fake_diag.lines[0][1]
        assert "response.created" in fake_diag.lines[0][1]

    def test_cancel_hint_empty_without_events(self) -> None:
        """没有任何事件时返回空串，调用方不会打出空提示。"""
        assert ev.TalkEventLog().cancel_hint() == ""


# ---- 计数与诊断摘要 ----


class TestTurnCounters:
    """计数与退出诊断摘要。"""

    def test_counters(self) -> None:
        """三类计数各记各的。"""
        log = ev.TalkEventLog()
        log.note_user_turn()
        log.note_user_turn()
        log.note_ai_turn()
        log.note_interrupt()
        assert (log.user_turns, log.ai_turns, log.interrupts) == (2, 1, 1)

    def test_report_empty_without_ambient(self) -> None:
        """没发生过语义过滤时不输出摘要，正常对话不受打扰。"""
        log = ev.TalkEventLog()
        log.note_user_turn()
        log.note_ai_turn()
        assert log.report_lines() == []

    def test_report_flags_no_user_turn(self) -> None:
        """只有环境音、没有任何对话轮 → 直指"一直在说它不回复"。"""
        log = ev.TalkEventLog()
        log.feed_ambient(ev.AMBIENT_COMPLETED, {"item_id": "i1", "text": "咋回事儿"})
        lines = log.report_lines()
        assert lines and "1 段语音被服务端判为非有效轮次" in lines[0]
        assert any("没有任何语音进入对话轮" in line for line in lines[1:])

    def test_report_notes_healthy_turns(self) -> None:
        """同时有对话轮时，摘要说明链路本身正常。"""
        log = ev.TalkEventLog()
        log.feed_ambient(ev.AMBIENT_COMPLETED, {"item_id": "i1", "text": "嗯"})
        log.note_user_turn()
        assert any("另有 1 段进入对话轮" in line for line in log.report_lines())

    def test_report_includes_log_path_when_enabled(self, fake_diag: _FakeDiag) -> None:
        """开关开启时摘要附上时间线日志路径，方便用户直接翻。"""
        log = ev.TalkEventLog(enabled=True)
        log.feed_ambient(ev.AMBIENT_COMPLETED, {"item_id": "i1", "text": "嗯"})
        assert any("diag.log" in line for line in log.report_lines())

    def test_log_hint_empty_when_disabled(self, fake_diag: _FakeDiag) -> None:
        """开关关闭时不提示日志路径（横幅也不多印一行）。"""
        assert ev.TalkEventLog().log_hint() == ""
        assert ev.TalkEventLog(enabled=True).log_hint().endswith("diag.log")


# ---- _recv_events 接线（用户可见行为） ----


class TestRecvEventsWiring:
    """事件分发接线：环境音转写可见、对话轮不受影响。

    这组用例直接验证"用户说的话去了哪条路"在屏幕上的表现，
    是本次可观测性改动的核心断言。
    """

    async def test_ambient_completed_is_surfaced(self, _stub_tools) -> None:
        """环境音转写定稿后以 🔇 行显示，并计入会话统计。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._running = True
        ui = _FakeTalkUI()

        await rt._recv_events(_FakeWS([
            {"type": ev.AMBIENT_DELTA, "item_id": "i1", "text": "嗯"},
            {"type": ev.AMBIENT_COMPLETED, "item_id": "i1", "text": "嗯啊"},
        ]), ui)

        assert any(line.startswith("🔇") and "嗯啊" in line for line in ui.infos)
        assert rt._events.ambient_turns == ["嗯啊"]
        assert rt._events.user_turns == 0

    async def test_ambient_delta_only_stays_silent(self, _stub_tools) -> None:
        """只收到 delta（未定稿）时不打印，避免逐片刷屏。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._running = True
        ui = _FakeTalkUI()

        await rt._recv_events(_FakeWS([
            {"type": ev.AMBIENT_DELTA, "item_id": "i1", "text": "嗯"},
        ]), ui)

        assert not any(line.startswith("🔇") for line in ui.infos)

    async def test_user_turn_goes_to_transcript_channel(self, _stub_tools) -> None:
        """进入对话轮的用户语音走 on_user_transcript，不显示 🔇 行。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._running = True
        ui = _FakeTalkUI()

        await rt._recv_events(_FakeWS([
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "打开浏览器",
            },
        ]), ui)

        assert ui.user_transcripts == ["打开浏览器"]
        assert not any(line.startswith("🔇") for line in ui.infos)
        assert rt._events.user_turns == 1
        assert rt._events.ambient_turns == []

    async def test_speech_started_records_active_response(self, _stub_tools) -> None:
        """打断标注：speech_started 时刻是否有活动响应被记入计数。

        全双工（耳机）场景：半双工下响应期间的 speech_started 被直接忽略
        （见 test_realtime_response_lifecycle.TestHalfDuplex）。
        """
        rt = rt_mod.RealtimeTalk("sk-test", half_duplex=False)
        rt._running = True
        rt._response_active = True
        rt._ai_speaking = True
        # 模拟真实插话：衰减后电平高于回声保护门限，才会真正触发打断
        rt._last_mic_rms = rt_mod.ECHO_GUARD_RMS * 10
        ui = _FakeTalkUI()

        await rt._recv_events(_FakeWS([
            {"type": "input_audio_buffer.speech_started"},
        ]), ui)

        assert rt._events.interrupts == 1
        # 打断分支的清状态逻辑不受可观测性改动影响
        assert rt._ai_speaking is False
        assert rt._response_gen == 1

    async def test_timeline_logged_when_enabled(self, _stub_tools, fake_diag: _FakeDiag) -> None:
        """开关开启时事件时间线落盘；关闭时零输出。"""
        ui = _FakeTalkUI()
        events = [
            {"type": "input_audio_buffer.speech_stopped"},
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": "你好"},
        ]

        off = rt_mod.RealtimeTalk("sk-test")
        off._running = True
        await off._recv_events(_FakeWS(events), ui)
        assert fake_diag.lines == []

        on = rt_mod.RealtimeTalk("sk-test", event_log=True)
        on._running = True
        await on._recv_events(_FakeWS(events), ui)
        assert [line[1] for line in fake_diag.lines] == [
            "#1 input_audio_buffer.speech_stopped",
            "#2 conversation.item.input_audio_transcription.completed transcript=你好",
        ]


# ---- 工具层（拆分后） ----


class _FakeTool:
    """ToolRegistry 工具替身。"""

    def __init__(
        self,
        *,
        data=None,
        action: str = "allow",
        reason: str = "",
        raises: Exception | None = None,
    ) -> None:
        self._data = data
        self._action = action
        self._reason = reason
        self._raises = raises

    def check_permissions(self, args, ctx):
        return SimpleNamespace(action=self._action, reason=self._reason)

    async def call(self, args, ctx):
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(data=self._data)


class TestRealtimeTools:
    """realtime_tools：内置工具、执行分派、安全策略与截断。"""

    def test_builtin_tool_names(self) -> None:
        """内置工具集合（session.update 注册给模型）。"""
        names = [t["function"]["name"] for t in tools_mod.BUILTIN_TOOLS]
        assert names == ["get_current_time", "end_conversation"]

    def test_get_current_time_returns_json(self) -> None:
        """时间工具返回可解析的 JSON，字段齐全。"""
        payload = json.loads(tools_mod.execute_builtin_tool("get_current_time", {}))
        assert set(payload) == {"datetime", "date", "time", "weekday", "timestamp"}

    def test_unknown_builtin_tool(self) -> None:
        """未知内置工具返回错误 JSON，不抛异常。"""
        payload = json.loads(tools_mod.execute_builtin_tool("nope", {}))
        assert "未知工具" in payload["error"]

    async def test_execute_tool_unknown_name(self) -> None:
        """未知工具名返回错误 JSON。"""
        result = await tools_mod.execute_tool(
            "nope", {}, registry_tool_map={}, workdir=".", ui=SimpleNamespace()
        )
        assert "未知工具" in json.loads(result)["error"]

    async def test_execute_tool_end_conversation_triggers_callback(self) -> None:
        """end_conversation 通过回调通知会话层设优雅停止，工具层不持有会话状态。"""
        called: list[bool] = []
        result = await tools_mod.execute_tool(
            "end_conversation",
            {},
            registry_tool_map={},
            workdir=".",
            ui=SimpleNamespace(),
            on_end_conversation=lambda: called.append(True),
        )
        assert called == [True]
        assert "退下" in json.loads(result)["result"]

    async def test_execute_tool_end_conversation_without_callback(self) -> None:
        """未注入回调（纯工具层调用）时不报错。"""
        result = await tools_mod.execute_tool(
            "end_conversation", {}, registry_tool_map={}, workdir=".", ui=SimpleNamespace()
        )
        assert "result" in json.loads(result)

    async def test_execute_tool_denied_by_permission(self) -> None:
        """权限拒绝的工具不执行，返回安全策略拒绝原因。"""
        tool = _FakeTool(action="deny", reason="危险操作")
        result = await tools_mod.execute_tool(
            "Bash", {"cmd": "x"}, registry_tool_map={"Bash": tool},
            workdir=".", ui=SimpleNamespace(),
        )
        assert "操作被安全策略拒绝: 危险操作" in json.loads(result)["error"]

    async def test_execute_tool_string_and_none_data(self) -> None:
        """ToolResult.data 为 str / None 时的回执格式。"""
        str_result = await tools_mod.execute_tool(
            "T", {}, registry_tool_map={"T": _FakeTool(data="raw")},
            workdir=".", ui=SimpleNamespace(),
        )
        assert str_result == "raw"
        none_result = await tools_mod.execute_tool(
            "T", {}, registry_tool_map={"T": _FakeTool(data=None)},
            workdir=".", ui=SimpleNamespace(),
        )
        assert json.loads(none_result)["result"] == "（无输出）"

    async def test_execute_tool_truncates_long_result(self) -> None:
        """超长结果被截断，避免语音播报过长。"""
        tool = _FakeTool(data="x" * (tools_mod._MAX_RESULT_CHARS + 100))
        result = await tools_mod.execute_tool(
            "T", {}, registry_tool_map={"T": tool}, workdir=".", ui=SimpleNamespace(),
        )
        assert result.endswith("（结果已截断）")
        assert len(result) <= tools_mod._MAX_RESULT_CHARS + 20

    async def test_execute_tool_swallows_tool_exception(self) -> None:
        """工具抛异常时返回错误 JSON，不向上冒泡拖垮语音会话。"""
        tool = _FakeTool(raises=RuntimeError("boom"))
        result = await tools_mod.execute_tool(
            "T", {}, registry_tool_map={"T": tool}, workdir=".", ui=SimpleNamespace(),
        )
        assert "执行失败: boom" in json.loads(result)["error"]

    def test_build_all_tools_excludes_askuser(self) -> None:
        """装配结果排除 AskUser（语音场景无法键盘输入）。"""
        schema, tool_map = tools_mod.build_all_tools(".")
        assert isinstance(schema, list) and isinstance(tool_map, dict)
        assert "AskUser" not in tool_map
        assert len(schema) == len(tool_map)


# ---- realtime_talk 对外契约 ----


class TestRealtimeTalkContract:
    """realtime_talk.py 拆分与接线后的对外契约。"""

    def test_exports_kept(self) -> None:
        """realtime_talk 仍导出 RealtimeTalk / DEFAULT_WS_URL（调用方依赖）。"""
        assert rt_mod.RealtimeTalk is not None
        assert rt_mod.DEFAULT_WS_URL.startswith("wss://")
        assert rt_mod.DEFAULT_VOICE == "longanqian"

    @pytest.mark.parametrize(
        "symbol",
        ["_BUILTIN_TOOLS", "_build_all_tools", "_execute_builtin_tool"],
    )
    def test_moved_symbols_gone(self, symbol: str) -> None:
        """工具层已迁至 realtime_tools，旧私有名不得回流（防重复定义）。"""
        assert not hasattr(rt_mod, symbol)

    def test_signature_has_no_backend_selector(self) -> None:
        """构造函数新增 event_log 关键字，其余语音参数保持原样。"""
        import inspect

        params = inspect.signature(rt_mod.RealtimeTalk.__init__).parameters
        assert "event_log" in params
        assert params["event_log"].default is False
        for keep in ("voice", "ws_url", "workdir", "instructions"):
            assert keep in params

    def test_event_log_defaults_off(self, _stub_tools) -> None:
        """默认关闭：只做内存计数，不写日志。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        assert rt._events.enabled is False

    def test_event_log_can_be_enabled(self, _stub_tools) -> None:
        """显式开启后时间线日志生效。"""
        rt = rt_mod.RealtimeTalk("sk-test", event_log=True)
        assert rt._events.enabled is True

    def test_graceful_stop_callback_sets_deadline(self, _stub_tools) -> None:
        """end_conversation 回调设置优雅停止宽限期。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        assert rt._graceful_stop_at is None
        rt._request_graceful_stop()
        assert rt._graceful_stop_at is not None


# ---- 配置契约 ----


class TestRealtimeSettingsContract:
    """[realtime_talk] event_log ←→ Settings.realtime_event_log 映射。"""

    def test_default_off(self) -> None:
        """默认关闭：不订阅时间线日志的用户不受影响。"""
        from agent.config.settings import Settings

        assert Settings().realtime_event_log is False

    def test_toml_maps_event_log(self) -> None:
        """[realtime_talk] event_log = true 生效。"""
        from agent.config.settings import Settings, _apply_toml

        merged = _apply_toml(Settings(), {"realtime_talk": {"event_log": True}})
        assert merged.realtime_event_log is True

    def test_toml_keeps_existing_keys(self) -> None:
        """新增键不干扰同表内的既有映射。"""
        from agent.config.settings import Settings, _apply_toml

        merged = _apply_toml(
            Settings(),
            {"realtime_talk": {"voice": "longanqian", "event_log": False}},
        )
        assert merged.realtime_voice == "longanqian"
        assert merged.realtime_event_log is False
