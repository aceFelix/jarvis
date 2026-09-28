"""实时语音响应生命周期与回声门控回归测试。

覆盖两次线上故障的修复（均见 docs/fixlogs/）：

1. **response.done 缺失**：DashScope 协议以 response.done 标记一次响应结束
   （status=completed/cancelled/failed），代码此前只监听 response.audio.done
   —— 该事件在官方文档中并不存在 → AI 说完第一句后 _ai_speaking 永久为 True，
   后续每轮 speech_started 都误判"有活动响应"并发 response.cancel。
2. **取消二次回声压低**：修好上一条后若同时去掉 AEC 场景下的麦克风压低，
   残余回声会被服务端识别为用户语音，反过来取消 AI 的每一轮回复，
   表现为"AI 永远不开口"。故压低门控默认在 AEC 生效时依然压低。

@author aceFelix
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

from agent.voice import realtime_events as ev
from agent.voice import realtime_talk as rt_mod
from agent.voice.realtime_audio import voice_gap
from tests.voice._fakes import FakeDiag, FakeTalkUI, FakeWS


class TestResponseLifecycle:
    """响应生命周期：created → delta → done 的状态流转与打断判定依据。"""

    @staticmethod
    def _rt(**kwargs):
        """构造会话实例并挂上扬声器替身（audio delta 分支会调用 spk.write）。"""
        rt = rt_mod.RealtimeTalk("sk-test", **kwargs)
        rt._running = True
        rt._spk = SimpleNamespace(write=lambda _data: None)
        return rt

    @staticmethod
    def _delta() -> str:
        """一段可被解码的非空 audio delta。"""
        return base64.b64encode(b"\x00\x00" * 320).decode()

    async def test_response_done_resets_all_state(self, _stub_tools) -> None:
        """response.done 一次清干净：活动标志 / 说话态 / 超时计时器 / UI 状态。"""
        rt = self._rt()
        ui = FakeTalkUI()

        await rt._recv_events(FakeWS([{"type": "response.created"}]), ui)
        assert rt._response_active is True

        await rt._recv_events(
            FakeWS([{"type": "response.audio.delta", "delta": self._delta()}]), ui
        )
        assert rt._ai_speaking is True
        assert rt._response_start_ts is not None

        await rt._recv_events(
            FakeWS([{"type": "response.done", "status": "completed"}]), ui
        )
        assert rt._response_active is False
        assert rt._ai_speaking is False
        assert rt._response_start_ts is None
        assert ui.ai_speaking_events[-1] is False
        assert ui.statuses[-1] == "standby"

    async def test_response_audio_done_still_accepted(self, _stub_tools) -> None:
        """response.audio.done 作为兼容路径保留，行为与 response.done 一致。"""
        rt = self._rt()
        ui = FakeTalkUI()
        rt._response_active = True
        rt._ai_speaking = True
        rt._response_start_ts = 1.0

        await rt._recv_events(FakeWS([{"type": "response.audio.done"}]), ui)

        assert rt._response_active is False
        assert rt._ai_speaking is False
        assert rt._response_start_ts is None

    async def test_no_cancel_after_response_done(self, _stub_tools) -> None:
        """复现用户故障：AI 说完后用户再开口，不得再发 response.cancel。"""
        rt = self._rt()
        ui = FakeTalkUI()
        ws = FakeWS([
            {"type": "response.created"},
            {"type": "response.audio.delta", "delta": self._delta()},
            {"type": "response.done", "status": "completed"},
            # 第一轮结束，用户问第二句
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "现在几点了？",
            },
        ])

        await rt._recv_events(ws, ui)

        assert ws.sent == []  # 关键断言：不再误发取消，新轮次得以正常生成
        assert ui.user_transcripts == ["现在几点了？"]
        assert rt._events.interrupts == 1

    async def test_no_client_cancel_sent_during_active_response(self, _stub_tools) -> None:
        """全双工（耳机）下 AI 回复中用户插话：客户端不再自发改 cancel（2026-09-28 重构）。

        官方文档明确：模型播报期间服务端检测到用户开始说话，会**自动**返回
        response.done[cancelled)——客户端只需立即清掉本地播放缓冲，无需再发
        response.cancel（此前会偶发服务端 "no active response" 报错噪音）。
        半双工默认忽略响应期间的 speech_started（此时麦克风本就静音，不存在
        合法插话，见 TestHalfDuplex），故本用例显式关闭半双工。
        """
        rt = self._rt(half_duplex=False)
        ui = FakeTalkUI()
        ws = FakeWS([
            {"type": "response.created"},
            {"type": "input_audio_buffer.speech_started"},
        ])

        await rt._recv_events(ws, ui)

        assert ws.sent == []  # 服务端自动取消，客户端只清播放缓冲
        assert rt._response_gen == 1  # 代数递增，残余音频被丢弃

    async def test_interrupt_detail_marks_active_response(
        self, _stub_tools, fake_diag: FakeDiag
    ) -> None:
        """时间线里的 active_response 标注跟随真实活动标志，而非本地播放态。"""
        rt = self._rt(event_log=True)
        ui = FakeTalkUI()

        await rt._recv_events(FakeWS([
            {"type": "response.created"},
            {"type": "input_audio_buffer.speech_started"},
        ]), ui)

        assert any("active_response=True" in line[1] for line in fake_diag.lines)

    async def test_cancelled_response_notifies_and_counts(self, _stub_tools) -> None:
        """response.done 带 status=cancelled 时给出可见提示并计数。

        连续自打断（残余回声被当成用户语音）会让 AI 一个字也出不来，
        必须让用户和开发者当场看到“被取消”这个信号。
        """
        rt = self._rt()
        ui = FakeTalkUI()

        await rt._recv_events(FakeWS([
            {"type": "response.created"},
            {"type": "response.done", "response": {"status": "cancelled"}},
        ]), ui)

        assert rt._events.cancelled_responses == 1
        assert any("被打断取消" in text for text in ui.infos)
        # 取消后状态同样要复位，不能遗留活动响应
        assert rt._response_active is False
        assert rt._ai_speaking is False

    async def test_completed_response_stays_silent(self, _stub_tools) -> None:
        """正常完成的回复不得出现任何取消提示，也不计入取消次数。"""
        rt = self._rt()
        ui = FakeTalkUI()

        await rt._recv_events(FakeWS([
            {"type": "response.created"},
            {"type": "response.done", "response": {"status": "completed"}},
        ]), ui)

        assert rt._events.cancelled_responses == 0
        assert ui.infos == []


class TestSelfInterruptReport:
    """退出时的自打断诊断摘要：区分“语义过滤”与“回声打断”两种不回复成因。"""

    def test_report_when_no_ai_turn_completed(self) -> None:
        """AI 一条也没说完过且有取消记录时，给出回声成因诊断。"""
        log = ev.TalkEventLog()
        log.note_cancelled()
        log.note_cancelled()

        lines = log.report_lines()

        assert lines, "自打断场景必须输出诊断"
        assert "2 次回复被取消" in lines[0]
        assert any("echo_suppress_with_aec" in line for line in lines)

    def test_no_report_when_ai_replied(self) -> None:
        """AI 正常回复过（偶发打断属正常交互）时保持安静。"""
        log = ev.TalkEventLog()
        log.note_cancelled()
        log.note_ai_turn()

        assert log.report_lines() == []

    def test_no_report_when_all_normal(self) -> None:
        """既无取消也无环境音过滤时不输出任何摘要。"""
        log = ev.TalkEventLog()
        log.note_ai_turn()
        log.note_user_turn()

        assert log.report_lines() == []


class TestEchoSuppressGate:
    """AI 说话期间的麦克风二次压低门控。

    默认含 AEC 已启用场景也要压低：AEC3 只能消除部分回声，残留会
    被服务端当成用户语音触发打断，把 AI 每一轮回复都取消掉。
    """

    def test_attenuate_when_aec_absent(self, _stub_tools) -> None:
        """AEC 不可用时保留兜底衰减（残余回声抑制）。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._aec = None
        rt._ai_speaking = True
        assert rt._should_attenuate() is True

    def test_attenuate_when_aec_active(self, _stub_tools) -> None:
        """AEC 生效时默认仍要压低——去掉衰减会导致回声打断每一轮回复。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._aec = object()
        rt._ai_speaking = True
        assert rt._should_attenuate() is True

    def test_no_attenuate_when_opted_out(self, _stub_tools) -> None:
        """显式关闭 echo_suppress_with_aec 且 AEC 可用时不压低（耳机场景）。"""
        rt = rt_mod.RealtimeTalk("sk-test", echo_suppress_with_aec=False)
        rt._aec = object()
        rt._ai_speaking = True
        assert rt._should_attenuate() is False

    def test_attenuate_when_opted_out_without_aec(self, _stub_tools) -> None:
        """关了开关但 AEC 实际不可用时，开关不生效，仍必须压低。"""
        rt = rt_mod.RealtimeTalk("sk-test", echo_suppress_with_aec=False)
        rt._aec = None
        rt._ai_speaking = True
        assert rt._should_attenuate() is True

    def test_no_attenuate_when_idle(self, _stub_tools) -> None:
        """AI 没在说话时不衰减（用户语音保持正常增益）。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._ai_speaking = False
        assert rt._should_attenuate() is False


class TestEchoGuard:
    """回声保护窗口：AI 说话期间的低电平 speech_started 判为回声并忽略。

    外放时 AI 自己的声音被麦克风拾取，若衰减后仍触发服务端 VAD，客户端
    需要能区分“回声误触发”与“用户真实插话”：前者电平低、忽略；后者电平高、打断。
    """

    @staticmethod
    def _rt(**kwargs):
        rt = rt_mod.RealtimeTalk("sk-test", **kwargs)
        rt._running = True
        rt._spk = SimpleNamespace(write=lambda _data: None)
        return rt

    async def test_low_level_speech_started_treated_as_echo(self, _stub_tools) -> None:
        """AI 说话中、衰减后电平很低 → 判为回声，不发 cancel、不取消回复。"""
        rt = self._rt()
        ui = FakeTalkUI()
        rt._ai_speaking = True
        rt._response_active = True
        rt._last_mic_rms = rt_mod.ECHO_GUARD_RMS * 0.1  # 远低于门限

        await rt._recv_events(
            FakeWS([{"type": "input_audio_buffer.speech_started"}]), ui
        )

        assert rt._last_mic_rms < rt_mod.ECHO_GUARD_RMS
        assert rt._events.echo_guard_hits == 1

    async def test_loud_interrupt_still_cancels(self, _stub_tools) -> None:
        """全双工（耳机）下 AI 说话中、电平超过门限（真实插话）→ 正常打断。

        2026-09-28 重构：打断动作由服务端自动 cancel，客户端不发 response.cancel，
        只清本地播放缓冲 + 递增打断代数；echo_guard 不拦截（电平超门限）。
        """
        rt = self._rt(half_duplex=False)
        ui = FakeTalkUI()
        rt._ai_speaking = True
        rt._response_active = True
        rt._last_mic_rms = rt_mod.ECHO_GUARD_RMS * 10  # 高于门限
        ws = FakeWS([{"type": "input_audio_buffer.speech_started"}])

        await rt._recv_events(ws, ui)

        assert ws.sent == []  # 服务端自动取消，客户端不再自发改 cancel
        assert rt._ai_speaking is False
        assert rt._response_gen == 1
        assert rt._events.echo_guard_hits == 0

    async def test_guard_not_applied_when_ai_idle(self, _stub_tools) -> None:
        """AI 未说话时低电平不触发保护（新一轮语音的 speech_started 不受影响）。"""
        rt = self._rt()
        ui = FakeTalkUI()
        rt._ai_speaking = False
        rt._response_active = False
        rt._last_mic_rms = 0.0
        ws = FakeWS([{"type": "input_audio_buffer.speech_started"}])

        await rt._recv_events(ws, ui)

        # 待机下既不发 cancel（无活动响应），也不计入回声保护
        assert ws.sent == []
        assert rt._events.echo_guard_hits == 0

    def test_report_shows_echo_guard(self) -> None:
        """退出摘要会提示回声保护拦下的误打断次数。"""
        log = ev.TalkEventLog()
        log.note_ai_turn()  # 避免走自打断分支
        log.note_echo_guard()
        log.note_echo_guard()

        lines = log.report_lines()

        assert any("2 次回声" in line for line in lines)


class TestHalfDuplex:
    """半双工：AI 说话期间及说完后的尾迹窗口内麦克风不上传。

    免提外放 + 软件 AEC 下，持续上传会让 smart_turn 把回声当成“用户仍在说话”，
    AI 说完后永远等不到安静信号→不开下一轮。半双工从源头切断上传。
    """

    def test_muted_while_ai_speaking(self, _stub_tools) -> None:
        """AI 说话期间麦克风静音。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._ai_speaking = True
        assert rt._mic_muted() is True

    def test_muted_during_tail_window(self, _stub_tools) -> None:
        """AI 说完但回声尾迹窗口未过时，仍保持静音。"""
        import time as _t
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._ai_speaking = False
        rt._mic_resume_at = _t.monotonic() + 5.0
        assert rt._mic_muted() is True

    def test_unmuted_after_tail(self, _stub_tools) -> None:
        """尾迹窗口过后恢复上传，并清空恢复时刻。"""
        import time as _t
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._ai_speaking = False
        rt._mic_resume_at = _t.monotonic() - 0.1
        assert rt._mic_muted() is False
        assert rt._mic_resume_at is None

    def test_full_duplex_never_mutes(self, _stub_tools) -> None:
        """全双工（耳机）模式从不静音，保留随口打断。"""
        rt = rt_mod.RealtimeTalk("sk-test", half_duplex=False)
        rt._ai_speaking = True
        assert rt._mic_muted() is False

    def test_end_response_sets_tail_window(self, _stub_tools) -> None:
        """AI 说完时半双工会设回声尾迹恢复时刻。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        ui = FakeTalkUI()
        rt._ai_speaking = True
        rt._response_active = True
        rt._end_response(ui)
        assert rt._mic_resume_at is not None

    def test_muted_from_response_start(self, _stub_tools) -> None:
        """响应一开始（AI 还没出声）就静音。

        锁死实机故障：此前只在 `_ai_speaking`（首个 audio delta）时静音，
        `response.created` → 首个音频的思考/工具调用空窗期内麦克风仍在上传，
        用户尾音被服务端当成新用户轮次而取消本轮（⚠️ 回复被打断取消，
        需工具的提问最易复现）。
        """
        import time as _t
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._response_active = True
        rt._ai_speaking = False  # 模型还在思考 / 工具还在执行
        rt._response_begin_ts = _t.monotonic()
        assert rt._mic_muted() is True

    def test_unmuted_when_response_hangs(self, _stub_tools) -> None:
        """响应状态悬挂超时后不再静音，避免麦克风永久哑掉。"""
        import time as _t
        from agent.voice.realtime_audio import MIC_MUTE_MAX_SECONDS
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._response_active = True
        rt._ai_speaking = True
        rt._response_begin_ts = _t.monotonic() - MIC_MUTE_MAX_SECONDS - 1
        assert rt._mic_muted() is False

    @staticmethod
    def _rt(**kwargs):
        """构造会话实例并挂上扬声器替身（speech_started 分支会操作扬声器流）。"""
        rt = rt_mod.RealtimeTalk("sk-test", **kwargs)
        rt._running = True
        rt._spk = SimpleNamespace(write=lambda _data: None)
        return rt

    async def test_ignores_speech_started_in_response(self, _stub_tools) -> None:
        """半双工：响应期间收到 speech_started 一律忽略、不发 cancel。

        锁死实机故障：smart_turn 会提前判停并启动响应（response.created），
        管道中剩余的音频稍后才被服务端处理到并触发 speech_started；此时若
        据此发 response.cancel，服务端会以 status=cancelled 结束本轮，
        表现为用户每次提问都“⚠️ 回复被打断取消”。
        """
        rt = self._rt()
        ui = FakeTalkUI()
        rt._response_active = True
        ws = FakeWS([{"type": "input_audio_buffer.speech_started"}])

        await rt._recv_events(ws, ui)

        assert ws.sent == []  # 不发 cancel
        assert rt._response_active is True  # 响应不被中止
        assert rt._events.echo_guard_hits == 1  # 记为被忽略的打断信号

    async def test_new_turn_speech_started_not_blocked(self, _stub_tools) -> None:
        """半双工：响应已结束（用户新提问）时 speech_started 仍走正常流程。"""
        rt = self._rt()
        ui = FakeTalkUI()
        rt._response_active = False
        ws = FakeWS([{"type": "input_audio_buffer.speech_started"}])

        await rt._recv_events(ws, ui)

        assert ws.sent == []  # 无活动响应本就不发 cancel
        assert rt._response_gen == 1  # 但切轮逻辑照常执行

    async def test_speech_stopped_opens_turn_end_mute_window_smart_turn(self, _stub_tools):
        """半双工 + smart_turn：speech_stopped 进入轮末静音窗口。

        实机事件链：smart_turn 语义判停后客户端仍在直播真实麦克风，用户尾音
        被服务端 VAD 重新检出为新轮次（response.done[cancelled, reason=
        turn_detected]），在 ~100ms 内掐死刚创建的响应。故从判停时刻起进入
        MIC_TURN_END_MUTE_SECONDS 窗口，response.created 后由响应期静音接管。
        （2026-09 第五次修正的"禁止抢先静音"系被中途 session.update 毒化会话
        误导的混淆归因，已推翻，见 realtime_audio 注记。）

        2026-09-28 重构：轮末窗口仅 smart_turn 模式启用（构造时显式指定）；
        server_vad（默认）靠真实静音判停天然免疫，见下一个用例。
        """
        import time as _t
        rt = self._rt(turn_detection="smart_turn")
        ui = FakeTalkUI()
        ws = FakeWS([{"type": "input_audio_buffer.speech_stopped"}])

        await rt._recv_events(ws, ui)

        assert rt._mic_resume_at is not None  # 已进入轮末静音窗口
        assert rt._mic_resume_at > _t.monotonic()  # 窗口在未来
        assert rt._mic_muted() is True

    async def test_speech_stopped_no_turn_end_mute_server_vad(self, _stub_tools):
        """半双工 + server_vad（默认）：speech_stopped 不开轮末窗口。

        server_vad 要求真实静音满 silence_duration_ms 才判停，判停即意味着
        用户已静默，speech_stopped→response.created 之间无尾音可重检——轮末
        静音是 smart_turn 的专项缓解，server_vad 下不开（避免吞掉用户的
        快速连续提问）。
        """
        rt = self._rt()  # 默认 server_vad
        ui = FakeTalkUI()
        ws = FakeWS([{"type": "input_audio_buffer.speech_stopped"}])

        await rt._recv_events(ws, ui)

        assert rt._mic_resume_at is None
        assert rt._mic_muted() is False

    async def test_speech_stopped_keeps_mute_during_active_response(self, _stub_tools):
        """半双工边界 2：响应进行中收到 speech_stopped（打断）时不重置窗口。"""
        import time as _t
        rt = self._rt()
        ui = FakeTalkUI()
        rt._response_active = True
        rt._response_begin_ts = _t.monotonic()  # 悬挂保护在兜底上限内
        before = _t.monotonic() + 99.0
        rt._mic_resume_at = before
        ws = FakeWS([{"type": "input_audio_buffer.speech_stopped"}])

        await rt._recv_events(ws, ui)

        # 响应进行中不进入轮末窗口分支，窗口保持原值（响应期静音分支接管）
        assert rt._mic_resume_at == before
        assert rt._mic_muted() is True

    async def test_full_duplex_no_turn_end_mute(self, _stub_tools):
        """全双工（耳机）：轮末窗口不适用，speech_stopped 不改变静音状态。"""
        rt = self._rt(half_duplex=False)
        ui = FakeTalkUI()
        ws = FakeWS([{"type": "input_audio_buffer.speech_stopped"}])

        await rt._recv_events(ws, ui)

        assert rt._mic_resume_at is None
        assert rt._mic_muted() is False

    async def test_ambient_clears_leftover_mute_window(self, _stub_tools) -> None:
        """半双工：服务端判为非有效轮次时清掉残留的静音窗口。

        否则用户说一声“嗯”（不上对话轮）之后，会被残留的尾迹窗口挡在外面。
        """
        import time as _t
        rt = self._rt()
        rt._mic_resume_at = _t.monotonic() + 5.0  # 模拟残留窗口
        ui = FakeTalkUI()
        ws = FakeWS([{
            "type": "conversation.item.ambient_audio_transcription.completed",
            "item_id": "i1",
            "text": "嗯",
        }])

        await rt._recv_events(ws, ui)

        assert rt._mic_resume_at is None

    async def test_tool_turn_keeps_mic_muted(self, _stub_tools) -> None:
        """半双工：工具轮结束后继续静音，等下一轮最终回复。

        工具执行 + 二次推理通常 1~3s，此时若按普通回声尾迹恢复上传，环境声会
        被当成新用户语音并取消最终回复（表现为“问时间这类要调工具的提问没反应”）。
        """
        import time as _t
        from agent.voice.realtime_audio import MIC_TAIL_SECONDS
        rt = self._rt()
        ui = FakeTalkUI()
        ws = FakeWS([{
            "type": "response.done",
            "response": {
                "status": "completed",
                "output": [{"type": "function_call", "name": "get_current_time"}],
            },
        }])

        await rt._recv_events(ws, ui)

        assert rt._mic_resume_at is not None
        # 远长于普通回声尾迹：覆盖工具执行 + 二次推理的整段空窗
        assert rt._mic_resume_at - _t.monotonic() > MIC_TAIL_SECONDS * 2

    async def test_normal_turn_keeps_short_tail(self, _stub_tools) -> None:
        """半双工：普通回复结束后只留短回声尾迹，不长时间静音。"""
        import time as _t
        from agent.voice.realtime_audio import MIC_TAIL_SECONDS
        rt = self._rt()
        ui = FakeTalkUI()
        ws = FakeWS([{
            "type": "response.done",
            "response": {"status": "completed", "output": [{"type": "message"}]},
        }])

        await rt._recv_events(ws, ui)

        assert 0 < rt._mic_resume_at - _t.monotonic() <= MIC_TAIL_SECONDS + 0.1

    # ---- 本地语音活动跟踪（仅复位界面指示 / 提前解除静音） ----

    def test_voice_gap_resets_after_silence_without_muting(self) -> None:
        """本地判停：说过后持续静默超阈值 → 清零活动时刻，但**不设静音窗口**。

        实机事件链（2026-09 第五次修正）：客户端在 response.created 之前静音，
        会让服务端在响应刚创建的瞬间返回 response.done[cancelled]。判“说完”
        只用于复位界面指示，静音一律交给响应进行中的判定。
        """
        from agent.voice.realtime_audio import SPEECH_GAP_SECONDS
        now = 100.0
        last, resume = voice_gap(
            0.001, now - SPEECH_GAP_SECONDS - 0.1, None, now, muted=False
        )
        assert last == 0.0
        assert resume is None  # 不再进入静音窗口

    def test_voice_gap_ignores_pure_silence(self) -> None:
        """从未说过话时（纯房间底噪）不静音，避免正常监听被误锁。"""
        last, resume = voice_gap(0.0005, 0.0, None, 100.0, muted=False)
        assert last == 0.0
        assert resume is None

    def test_voice_gap_tracks_speech(self) -> None:
        """说话期间持续刷新说话时刻，且不会提前静音中断用户发言。"""
        last, resume = voice_gap(0.5, 0.0, None, 100.0, muted=False)
        assert last == 100.0
        assert resume is None

    def test_voice_gap_releases_on_voice_resume(self) -> None:
        """静音期间再次出现语音 → 立即解除，用户续话不被吞掉。"""
        last, resume = voice_gap(0.5, 0.0, 106.0, 100.0, muted=True)
        assert resume is None

    # ---- 静音必须“继续发送”，不能断流 ----

    async def test_muted_still_streams_silence_frames(self, _stub_tools) -> None:
        """半双工静音期间**仍持续发送**（等长静音帧），不中断音频流。

        实机故障（2026-09 第四次）：此前“静音=停止上传”，每轮都在
        `response.created` 后立刻 `cancelled`；唯一成功的首轮恰好是还没进静音
        窗口的那一次，且链上全程无 `speech_started`。官方文档要求客户端
        “持续发送麦克风采集的音频流”——断流会让服务端判轮状态机卡住并中止本轮。
        """
        import asyncio
        import json
        rt = self._rt()
        rt._mic = SimpleNamespace(read=lambda n, blocking: b"\x40" * n)
        rt._ai_speaking = True  # 半双工下即为静音
        ws = FakeWS([])

        task = asyncio.create_task(rt._send_audio(ws, FakeTalkUI()))
        # 轮询等首帧：固定 sleep 在整套测试连跑时不可靠（线程池调度波动）
        for _ in range(60):
            if ws.sent:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert ws.sent, "静音期间必须继续发送音频（否则服务端中止本轮）"
        for msg in ws.sent:  # FakeWS.sent 存的是已解析的出站消息
            payload = base64.b64decode(msg["audio"])
            assert len(payload) == rt_mod.CHUNK_BYTES
            assert payload == b"\x00" * len(payload)  # 内容是静音，不回传回声

    async def test_unmuted_streams_raw_mic_data(self, _stub_tools) -> None:
        """未静音时上传真实麦克风音频，而非静音帧（替换只发生在静音窗口内）。

        断言的是“不是全零静音帧”而非“与输入逐字节相同”：启用 AEC 时麦克风
        数据会先经 `process_mic` 重写（输出为残差，不再等于输入），本用例只
        关心“真实音频有没有被误替换成静音帧”。
        """
        import asyncio
        rt = self._rt()
        rt._mic = SimpleNamespace(read=lambda n, blocking: b"\x40" * n)
        ws = FakeWS([])

        task = asyncio.create_task(rt._send_audio(ws, FakeTalkUI()))
        for _ in range(60):
            if ws.sent:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert ws.sent
        for msg in ws.sent:
            payload = base64.b64decode(msg["audio"])
            assert any(payload), "未静音时不得替换为静音帧"


class TestRescueV2:
    """响应救援 v2 回归（2026-09-28 实机"重复回答"修正）。

    实机 17:18 会话：server_vad + 内置工具已能正常对话，但每轮回答完毕后
    救援误触发 → 对同一上下文二次作答。成因是尾音/环境音产生的 phantom
    轮次不获服务端应答，其救援武装到期触发。v2 三闸门：
    ① response.done 到达即撤销待执行救援（回答即应答）；
    ② 单字/纯标点轮、与 AI 刚播内容重合的回声轮不武装；
    ③ 救援触发要求距上次应答完成 ≥1s。
    """

    @staticmethod
    def _rt(**kwargs):
        rt = rt_mod.RealtimeTalk("sk-test", **kwargs)
        rt._running = True
        rt._spk = SimpleNamespace(write=lambda _data: None)
        return rt

    async def test_answered_turn_not_rescued(self, _stub_tools) -> None:
        """正常流转：提交 → 创建 → 完成 → 救援被撤销（不产生重复回答）。"""
        rt = self._rt()
        ui = FakeTalkUI()
        ws = FakeWS([
            {"type": "conversation.item.input_audio_transcription.completed",
             "transcript": "贾维斯。"},
            {"type": "response.created"},
            {"type": "response.done", "response": {"status": "completed"}},
        ])

        await rt._recv_events(ws, ui)

        assert rt._rescue_at is None  # 闸门①：已应答的轮次不再救援

    async def test_single_punct_turn_not_armed(self, _stub_tools) -> None:
        """闸门②a：单字/纯标点轮（phantom 尾音轮）不武装救援。"""
        rt = self._rt()
        ui = FakeTalkUI()
        ws = FakeWS([
            {"type": "conversation.item.input_audio_transcription.completed",
             "transcript": "。"},
        ])

        await rt._recv_events(ws, ui)

        assert rt._rescue_at is None

    async def test_echo_turn_not_armed(self, _stub_tools) -> None:
        """闸门②b：与 AI 刚播内容重合的回声轮不武装救援。"""
        rt = self._rt()
        ui = FakeTalkUI()
        rt._last_ai_transcript = "在呢，先生。有什么吩咐？"
        ws = FakeWS([
            {"type": "conversation.item.input_audio_transcription.completed",
             "transcript": "在呢，先生。有什么吩咐？"},
        ])

        await rt._recv_events(ws, ui)

        assert rt._rescue_at is None

    async def test_unanswered_substantive_turn_armed(self, _stub_tools) -> None:
        """正常武装未被破坏：实质轮次提交后无响应，1.8s 内armed待救援。"""
        import time as _t
        rt = self._rt()
        ui = FakeTalkUI()
        ws = FakeWS([
            {"type": "conversation.item.input_audio_transcription.completed",
             "transcript": "现在几点了？"},
        ])

        await rt._recv_events(ws, ui)

        assert rt._rescue_at is not None
        assert rt._rescue_at > _t.monotonic()

    async def test_turn_detected_cancel_rearms_after_cleanup(self, _stub_tools) -> None:
        """turn_detected 取消：收尾后重新武装（顺序回归——武装必须在
        _end_response 撤销之后，否则会被立即清掉导致该答的问题无人答）。"""
        rt = self._rt()
        ui = FakeTalkUI()
        ws = FakeWS([
            {"type": "response.created"},
            {"type": "response.done", "response": {
                "status": "cancelled",
                "status_details": {"type": "cancelled", "reason": "turn_detected"},
            }},
        ])

        await rt._recv_events(ws, ui)

        assert rt._rescue_at is not None  # 重新武装成功

    async def test_rescue_fire_requires_quiet_period(self, _stub_tools) -> None:
        """闸门③：距上次应答完成 <1s 时救援不触发（刚答完不补发）。"""
        import asyncio
        import time as _t
        rt = self._rt()
        ui = FakeTalkUI()
        # 模拟：回答刚完成 0.3s，一个 phantom 轮的武装却到期了
        rt._last_response_done_ts = _t.monotonic() - 0.3
        rt._rescue_at = _t.monotonic() - 0.1  # 已到期
        rt._rescue_armed_at = _t.monotonic() - 1.9
        ws = FakeWS([])

        task = asyncio.create_task(rt._response_rescue(ws, ui))
        await asyncio.sleep(0.6)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert ws.sent == []  # 静默期内不补发
