"""end_conversation 意图门测试（2026-11-25）。

实机：用户说“停一下”被打断流程吞掉转写，模型却主动调 `end_conversation`
直接退出语音。工具层必须硬门拦截，不能只靠模型自律。

@author aceFelix
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.talk import realtime_talk as rt_mod
from agent.talk.realtime_tools import execute_tool


class TestEndIntentGate:
    def test_engine_denies_pause_words(self):
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._last_user_text = "停一下"
        assert rt._allow_end_conversation() is False

        rt._last_user_text = "等一下，先暂停"
        assert rt._allow_end_conversation() is False

    def test_engine_allows_explicit_farewell(self):
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._last_user_text = "贾维斯退下"
        assert rt._allow_end_conversation() is True

        rt._last_user_text = "没事了，再见"
        assert rt._allow_end_conversation() is True

    def test_engine_allows_natural_leave_phrases(self):
        """2026-11-26：口号里承诺的“去忙吧”等自然说法不能被硬门拒绝。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        for text in ("好的好的退下吧", "去忙吧", "先这样吧", "晚安", "不聊了"):
            rt._last_user_text = text
            assert rt._allow_end_conversation() is True, text

    def test_engine_denies_question_not_leave(self):
        """回答完一个问题不等于结束：模型在报时后自发调工具应被拦下。"""
        rt = rt_mod.RealtimeTalk("sk-test")
        rt._last_user_text = "几点啦？Friday.\n对，对，你是你是星期五。"
        assert rt._allow_end_conversation() is False

    @pytest.mark.asyncio
    async def test_tool_refuses_without_intent(self):
        called: list[int] = []
        result = await execute_tool(
            "end_conversation",
            {},
            registry_tool_map={},
            workdir=".",
            ui=SimpleNamespace(),
            on_end_conversation=lambda: called.append(1),
            allow_end=lambda: False,
        )

        # 只回中性标记：旧实现回「用户没有明确说…」，模型会原样念给用户
        assert json.loads(result) == {"result": "continue"}
        assert called == []  # 回调绝不触发

    @pytest.mark.asyncio
    async def test_tool_executes_with_intent(self):
        called: list[int] = []
        result = await execute_tool(
            "end_conversation",
            {},
            registry_tool_map={},
            workdir=".",
            ui=SimpleNamespace(),
            on_end_conversation=lambda: called.append(1),
            allow_end=lambda: True,
        )

        assert "我先退下了" in json.loads(result)["result"]
        assert called == [1]
