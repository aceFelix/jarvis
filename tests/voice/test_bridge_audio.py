"""桌面全双工桥接音频适配器回归测试（2026-09-28，aceFelix）。

覆盖 BridgeMic（帧缓存/背压/欠载补静音/关闭唤醒）与 BridgeSpk（帧转发/
打断 flush/丢弃语义）——这两条是桌面端全双工语音的传输契约，回归即断链。
"""

from __future__ import annotations

import threading
import time

from agent.serve import protocol
from agent.voice.realtime_bridge_audio import (
    _MAX_BUFFER_BYTES,
    BridgeMic,
    BridgeSpk,
)


class TestBridgeMic:
    def test_feed_then_read_exact_size(self) -> None:
        """正常流转：推入两帧小数据，read 拉取恰好 n 字节。"""
        mic = BridgeMic()
        mic.feed(b"\x01" * 2000)
        mic.feed(b"\x02" * 2000)
        assert mic.read(3200, False) == b"\x01" * 2000 + b"\x02" * 1200
        assert mic.read(800, False) == b"\x02" * 800

    def test_starvation_pads_silence(self) -> None:
        """欠载：缓存不足时返回等长补静音数据（协议要求流持续，不得短读）。"""
        mic = BridgeMic()
        mic.feed(b"\x07" * 100)
        out = mic.read(3200, False)
        assert len(out) == 3200
        assert out[:100] == b"\x07" * 100
        assert out[100:] == b"\x00" * 3100

    def test_backpressure_drops_oldest(self) -> None:
        """背压：超过 5s 缓冲上限时丢最旧帧，缓存不超限。"""
        mic = BridgeMic()
        mic.feed(b"\x09" * (_MAX_BUFFER_BYTES + 3200))
        assert len(mic._buf) <= _MAX_BUFFER_BYTES
        # 最旧数据已被丢弃：read 到的首字节是后到的帧
        out = mic.read(3200, False)
        assert out == b"\x09" * 3200

    def test_close_wakes_blocked_reader(self) -> None:
        """关闭：阻塞中的 read 被唤醒并返回静音（引擎发送协程可立即退出）。"""
        mic = BridgeMic()
        result: list[bytes] = []

        def _reader() -> None:
            result.append(mic.read(3200, False))

        t = threading.Thread(target=_reader)
        t.start()
        time.sleep(0.05)
        mic.close()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert result and len(result[0]) == 3200

    def test_feed_after_close_ignored(self) -> None:
        mic = BridgeMic()
        mic.close()
        mic.feed(b"\x01" * 100)
        assert mic.read(100, False) == b"\x00" * 100


class TestBridgeSpk:
    def test_write_forwards_pcm(self) -> None:
        """正常流转：write 的帧经 on_chunk 转发。"""
        chunks: list[bytes] = []
        spk = BridgeSpk(on_chunk=chunks.append)
        spk.write(b"\x01" * 640)
        spk.write(b"\x02" * 640)
        assert chunks == [b"\x01" * 640, b"\x02" * 640]

    def test_stop_stream_discards_and_flushes(self) -> None:
        """打断：stop_stream 触发 on_flush 且后续帧被丢弃，start_stream 恢复。"""
        chunks: list[bytes] = []
        flushes: list[bool] = []
        spk = BridgeSpk(on_chunk=chunks.append, on_flush=lambda: flushes.append(True))
        spk.write(b"\x01" * 640)
        spk.stop_stream()   # 引擎打断：等同 PyAudio 清缓冲
        spk.write(b"\x02" * 640)  # 残余帧被丢弃
        spk.start_stream()  # 引擎立即重启流
        spk.write(b"\x03" * 640)
        assert chunks == [b"\x01" * 640, b"\x03" * 640]
        assert flushes == [True]

    def test_close_stops_forwarding(self) -> None:
        chunks: list[bytes] = []
        spk = BridgeSpk(on_chunk=chunks.append)
        spk.close()
        spk.write(b"\x01" * 640)
        assert chunks == []


class TestProtocolContract:
    def test_talk_audio_wire_names(self) -> None:
        """协议契约：talk.audio 指令与 talk_audio 事件名恒等于两端实现引用。"""
        assert protocol.CMD_TALK_AUDIO == "talk.audio"
        assert protocol.EVT_TALK_AUDIO == "talk_audio"
        assert protocol.CMD_TALK_AUDIO in protocol.DESKTOP_COMMANDS


class TestDuplexEngineFlags:
    def test_use_aec_false_disables_python_aec(self) -> None:
        """全双工桥接：use_aec=False 不创建 Python 侧 AEC（浏览器已消除回声，
        双重消除会让语音发闷/丢字）。"""
        from agent.voice.realtime_engine import RealtimeEngine

        eng = RealtimeEngine(
            "sk-test", half_duplex=False, echo_suppress_with_aec=False, use_aec=False
        )
        assert eng._aec is None

    def test_set_tools_sanitizes_names(self) -> None:
        """工具名清洗仍在引擎入口生效（set_tools 为唯一注册口）。"""
        from agent.voice.realtime_engine import RealtimeEngine

        eng = RealtimeEngine("sk-test")
        eng.set_tools(
            [{"type": "function", "function": {"name": "坏名字.工具", "parameters": {}}}],
            {},
        )
        assert eng._tools[0]["function"]["name"] == "______" or all(
            c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in eng._tools[0]["function"]["name"]
        )
        assert eng._tool_alias_map  # 别名映射可还原原名
