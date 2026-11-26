"""终端 ``/talk`` 实时语音的转写落库适配器。

实时双工对话发生在 DashScope 实时通道内，不经 ``QueryLoop``，``ctx.messages``
不会自动增长——此前终端 ``/talk`` 的内容退出即丢，会话历史里只剩文本轮。
本模块把 UI 包一层：**所有输出事件原样透传**，同时把 user/ai 转写按轮配对，
交给回调写进会话历史（终端宿主再接 ``advance_session`` 完成保存 + 标题）。

配对规则与桌面 ``WorkbenchRealtimeUI`` 完全一致（2026-10 定稿，两条路径同口径）：

- 正常顺序：用户转写先到 → AI 转写到达即配对 ``[问, 答]``；
- 转写滞后（输入转写常晚于回复转写）：回答先到且用户开过口
  （``on_user_speaking(True)``，服务端 speech_started 的权威信号）→ 暂存，
  等用户转写到达再按 ``[问, 答]`` 顺序落库；
- 开场白/主动播报（用户从未开口）：AI 转写单独落库；
- 回声轮（转写与 AI 刚播内容重合）：丢弃不落库（外放被麦克风重新拾取）；
- 打断轮（用户转写后未获回复又开口）：未配对的旧问句先单独落库；
- 退出收尾：``flush_pending_transcripts()`` 把仍在缓冲里的残轮写入。

@author aceFelix
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = ["TalkHistoryRecorder"]


class TalkHistoryRecorder:
    """``/talk`` 的 UI 代理：透传渲染 + 按轮配对转写。

    Attributes:
        _ui: 真实 UI（如 ``RichCLI``），所有未覆盖的属性都透传给它。
        _on_turn: 落库回调 ``(user_text|None, ai_text|None) -> None``，同步调用。
        _staged_user: 待配对的用户转写（None=无在途问句）。
        _staged_ai: 滞后到达、等用户转写配对的回答。
        _speech_since_flush: 上次落库后用户是否开过口，用于区分“滞后回答”与“开场白”。
        _last_ai_text: AI 最近一次转写全文，回声轮判定基准。

    @author aceFelix
    """

    def __init__(
        self,
        ui: Any,
        on_turn: Callable[[str | None, str | None], None],
    ) -> None:
        """初始化适配器。

        Args:
            ui: 真实 UI 对象，只透传、不修改。
            on_turn: 一轮配对完成后的落库回调；异常会被静默吞掉，绝不影响语音。
        """
        self._ui = ui
        self._on_turn = on_turn
        self._staged_user: str | None = None
        self._staged_ai: str | None = None
        self._speech_since_flush = False
        self._last_ai_text = ""

    # ---- UI 透传 ----

    def __getattr__(self, name: str) -> Any:
        """未覆盖的属性/方法全部透传给真实 UI（info/warn/error 等）。"""
        return getattr(self._ui, name)

    # ---- 实时语音回调（覆盖） ----

    def on_user_speaking(self, speaking: bool) -> None:
        """用户开口信号：置位“用户开过口”，供转写滞后场景判断。"""
        self._ui.on_user_speaking(speaking)
        if speaking:
            self._speech_since_flush = True

    def on_user_transcript(self, text: str) -> None:
        """用户转写：先上屏，再按配对规则暂存或立即配对落库。"""
        self._ui.on_user_transcript(text)
        stripped = (text or "").strip()
        if not stripped:
            return
        # 回声轮过滤：与 AI 刚播内容重合 → 外放被麦克风重新拾取的伪用户轮
        if self._last_ai_text and (
            stripped in self._last_ai_text or self._last_ai_text in stripped
        ):
            return
        # 上一条问句还没配上回答又来新问句：旧轮是被打断/未获回复的轮，
        # 先单独落库（保留“说过这话”的事实），再开新一轮
        if self._staged_user is not None:
            self._persist(self._staged_user, self._staged_ai)
            self._staged_user = None
            self._staged_ai = None
        self._staged_user = stripped
        # 转写滞后：回答已先到 → 用户转写一到立即配对落库
        if self._staged_ai is not None:
            self._persist(self._staged_user, self._staged_ai)
            self._staged_user = None
            self._staged_ai = None

    def on_ai_transcript(self, text: str) -> None:
        """AI 转写：正常配对、滞后暂存或作为开场白单独落库。"""
        self._ui.on_ai_transcript(text)
        stripped = (text or "").strip()
        if not stripped:
            return
        # 回声判定基准与引擎 _last_ai_transcript 同步
        self._last_ai_text = stripped
        if self._staged_user is not None:
            # 正常顺序：用户转写先到 → 回答一到即配对 [问, 答]
            self._persist(self._staged_user, stripped)
            self._staged_user = None
            self._staged_ai = None
            self._speech_since_flush = False
        elif self._speech_since_flush:
            # 转写滞后：用户开过口但转写未到 → 暂存回答，等配对
            self._staged_ai = stripped
        else:
            # 用户从未开口：开场白/主动播报，单独落库
            self._persist(None, stripped)
            self._speech_since_flush = False

    def on_ai_transcript_delta(self, text: str) -> None:
        """AI 流式转写增量：只透传（终稿由 on_ai_transcript 负责落库）。"""
        delta = getattr(self._ui, "on_ai_transcript_delta", None)
        if delta is not None:
            delta(text)

    # ---- 收尾 ----

    def flush_pending_transcripts(self) -> None:
        """会话结束时把仍在配对缓冲里的残轮写入历史（否则永久丢失）。"""
        if self._staged_user is None and self._staged_ai is None:
            return
        self._persist(self._staged_user, self._staged_ai)
        self._staged_user = None
        self._staged_ai = None

    def _persist(self, user_text: str | None, ai_text: str | None) -> None:
        """把一对（或单项）转写交给宿主；回调异常静默，不影响语音。"""
        if not user_text and not ai_text:
            return
        try:
            self._on_turn(user_text, ai_text)
        except Exception:
            pass
