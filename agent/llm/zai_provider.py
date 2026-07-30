"""智谱官方 zai-sdk Provider。

使用智谱官方 Python SDK（zai.ZhipuAiClient）调用 GLM 系列模型，
绕过 OpenAI 兼容层在工具调用、消息格式等场景下的兼容性问题，
获得更稳定的响应速度和更原生的功能支持（thinking、function calling、多模态）。

zai-sdk 是同步客户端，内部流式返回结构与 OpenAI 兼容，
本模块通过 asyncio.Queue + 后台线程把同步流桥接到 async generator。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, AsyncIterator

from agent.core.message import Message
from agent.llm.base import (
    LLMEvent,
    LLMProvider,
    ProviderError,
    Stop,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallEnd,
    ToolDef,
    Usage,
)
from agent.llm.openai_provider import _messages_to_openai, _parse_tool_args


class ZaiProvider(LLMProvider):
    """智谱官方 zai-sdk Provider。

    使用 ZhipuAiClient 直连智谱 BigModel，支持 GLM-5.x / GLM-4.x / GLM-V 等全系模型。

    @author aceFelix
    """

    name = "zhipu"
    default_model = "glm-4.7-flash"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        *,
        enable_thinking: bool = True,
        thinking_budget: int = 2000,
        model_type: str = "multimodal",
    ) -> None:
        """初始化智谱 SDK 客户端。

        Args:
            api_key: 智谱 API Key。为空时尝试从环境变量读取。
            base_url: 自定义 endpoint。为空时使用 SDK 默认值。
            model: 默认模型名。
            enable_thinking: 是否启用深度思考模式。
            thinking_budget: 思考 token 预算（智谱 SDK 当前主要用 reasoning_effort）。
            model_type: "multimodal" 或 "text"，控制图片是否随消息发送。

        @author aceFelix
        """
        try:
            from zai import ZhipuAiClient  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise ProviderError(
                "zai-sdk 未安装，请运行: pip install zai-sdk"
            ) from e

        if model:
            self.default_model = model
        self._enable_thinking = enable_thinking
        self._thinking_budget = thinking_budget
        self._model_type = model_type
        self._base_url = base_url or ""
        # _force_no_thinking: 强制关闭思考的兜底标志（即使 _enable_thinking=True 也不发 thinking）
        # voice_loop 语音模式用 set_thinking_enabled(False) 统一控制。
        self._force_no_thinking = False

        kwargs: dict[str, Any] = {"timeout": 180.0}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = ZhipuAiClient(**kwargs)

    def set_thinking_enabled(self, enabled: bool) -> None:
        """统一开关深度思考模式。

        同时管理 _enable_thinking 和 _force_no_thinking 两个标志：
        - _enable_thinking: 主开关，控制是否向智谱 SDK 发送 thinking={"type": "enabled"}
        - _force_no_thinking: 兜底标志，确保语音模式等场景下思考一定被关闭

        @author aceFelix
        """
        self._enable_thinking = bool(enabled)
        self._force_no_thinking = not enabled

    def is_thinking_enabled(self) -> bool:
        """返回当前思考模式是否开启（综合两个标志判断）。

        @author aceFelix
        """
        return self._enable_thinking and not self._force_no_thinking

    def set_model_type(self, model_type: str) -> None:
        """动态切换模型类型（multimodal / text）。

        切换模型时可能复用同一个 provider 实例，但需要改变图片处理方式。

        @author aceFelix
        """
        self._model_type = model_type

    async def stream(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        tools: list[ToolDef],
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMEvent]:
        """流式调用智谱 SDK，并转换为统一的 LLMEvent 序列。

        流程：
        1. 用 _messages_to_openai 把内部 Message 转成 OpenAI 兼容格式
           （智谱 SDK 的 chat.completions 接口与 OpenAI 完全兼容）。
        2. 在后台线程中执行同步的 client.chat.completions.create(stream=True)。
        3. 通过 asyncio.Queue 把 chunk 事件桥接到当前 async 协程。
        4. 解析 reasoning_content / content / tool_calls，yield 对应事件。

        @author aceFelix
        """
        api_msgs = _messages_to_openai(messages, system, skip_images=(self._model_type == "text"))
        tool_schemas = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

        request_kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": api_msgs,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tool_schemas:
            request_kwargs["tools"] = tool_schemas
        if temperature is not None:
            request_kwargs["temperature"] = temperature

        # 智谱 GLM 思考模式 —— 配置表驱动（zai_sdk: top_level thinking.type）
        from agent.llm.thinking import THINKING_CONFIGS, apply_thinking
        thinking_on = self._enable_thinking and not self._force_no_thinking
        cfg = THINKING_CONFIGS.get("zai_sdk")
        if cfg:
            apply_thinking(request_kwargs, cfg, thinking_on)

        # 异步队列 + 后台线程桥接同步 SDK
        queue: asyncio.Queue[Any] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        # 累积工具调用参数（流式分片）
        tool_acc: dict[int, dict[str, Any]] = {}

        def _run_sync() -> None:
            """在后台线程中运行同步的智谱 SDK 流式调用。"""
            try:
                stream = self._client.chat.completions.create(**request_kwargs)
                final_usage = Usage()
                finish_reason = "stop"

                for chunk in stream:
                    if chunk.usage:
                        cached = 0
                        details = getattr(chunk.usage, "prompt_tokens_details", None)
                        if details:
                            cached = getattr(details, "cached_tokens", 0) or 0
                        final_usage = Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                            cache_read_tokens=cached,
                        )

                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    # 思考内容（reasoning_content 先于 content 到达）
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning and self.is_thinking_enabled():
                        loop.call_soon_threadsafe(
                            queue.put_nowait, ThinkingDelta(text=reasoning)
                        )

                    # 正式文本内容
                    if delta.content:
                        loop.call_soon_threadsafe(
                            queue.put_nowait, TextDelta(text=delta.content)
                        )

                    # 工具调用分片累积
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_acc:
                                tool_acc[idx] = {
                                    "id": tc.id or "",
                                    "name": (tc.function.name if tc.function else "") or "",
                                    "args": "",
                                }
                            else:
                                if tc.id:
                                    tool_acc[idx]["id"] = tc.id
                                if tc.function and tc.function.name:
                                    tool_acc[idx]["name"] = tc.function.name
                            if tc.function and tc.function.arguments:
                                tool_acc[idx]["args"] += tc.function.arguments

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                # 发出累积的工具调用
                for entry in tool_acc.values():
                    raw_args = entry["args"] or ""
                    name = entry["name"] or ""
                    if not name and not raw_args:
                        continue
                    parsed = _parse_tool_args(raw_args, name)
                    call_id = entry["id"] or f"call_{name or 'unknown'}"
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ToolCall(id=call_id, name=name, input=parsed),
                    )
                    loop.call_soon_threadsafe(
                        queue.put_nowait, ToolCallEnd(id=call_id)
                    )

                # 结束标记
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    Stop(reason=finish_reason or "stop", usage=final_usage),
                )

            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, e)

        thread = threading.Thread(target=_run_sync, daemon=True)
        thread.start()

        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    if isinstance(item, ProviderError):
                        raise item
                    raise ProviderError(f"Zhipu AI API error: {item}") from item
                yield item
                if isinstance(item, Stop):
                    break
        finally:
            if thread.is_alive():
                thread.join(timeout=5)

    async def close(self) -> None:
        """关闭智谱 SDK 客户端（同步客户端无显式 close，为空实现）。"""
        return None
