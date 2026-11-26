"""实时语音测试替身 —— /talk 事件流与 UI 协议的假对象。

供 tests/voice 下多个测试文件复用（可观测性、响应生命周期、回声门控），
避免在各测试文件里重复粘贴同一套替身实现。

设计要点：
- 全部替身都不触碰真实网络、音频设备与磁盘日志；
- FakeWS 记录客户端出站消息，使"是否误发 response.cancel"这类判定可断言。

@author aceFelix
"""

from __future__ import annotations

import json


class FakeDiag:
    """diag_log 替身：收集调用，避免测试写真实 ~/.jarvis/logs/diag.log。"""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def diag_log(
        self,
        component: str,
        msg: str,
        *,
        level: str = "info",
        exc_info: bool = False,
    ) -> None:
        self.lines.append((component, msg))

    def get_log_path(self) -> str:
        return r"C:\fake\.jarvis\logs\diag.log"


class FakeWS:
    """WebSocket 替身：按序吐出预设事件后结束异步迭代，并记录出站消息。"""

    def __init__(self, events: list[dict]) -> None:
        self._events = events
        # 客户端出站消息（如 response.cancel），用于断言打断判定
        self.sent: list[dict] = []

    def __aiter__(self):
        async def _gen():
            for item in self._events:
                yield json.dumps(item, ensure_ascii=False)

        return _gen()

    async def send(self, payload: str) -> None:
        """记录出站消息（替代真实 WebSocket 发送，便于断言取消行为）。"""
        self.sent.append(json.loads(payload))


class FakeTalkUI:
    """RealtimeTalk 的 UI 协议替身：记录各通道收到的内容。"""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.user_transcripts: list[str] = []
        self.ai_transcripts: list[str] = []
        self.statuses: list[str] = []
        self.ai_speaking_events: list[bool] = []

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warn(self, text: str) -> None:
        self.infos.append(text)

    def error(self, text: str) -> None:
        self.infos.append(text)

    def on_status(self, status: str) -> None:
        self.statuses.append(status)

    def on_volume(self, level: float) -> None:
        pass

    def on_user_speaking(self, speaking: bool) -> None:
        pass

    def on_ai_speaking(self, speaking: bool) -> None:
        self.ai_speaking_events.append(speaking)

    def on_user_transcript(self, text: str) -> None:
        self.user_transcripts.append(text)

    def on_ai_transcript(self, text: str) -> None:
        self.ai_transcripts.append(text)

    def on_ai_transcript_delta(self, text: str) -> None:
        pass
