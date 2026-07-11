"""DashScope 原生 SDK Provider。

用阿里云 dashscope SDK 调用 qwen 系列模型，走 DashScope 原生 API
（非 OpenAI 兼容协议）。支持流式输出、思维链、工具调用。

dashscope SDK 是同步的，用 asyncio.Queue + 线程桥接到 async 环境。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, AsyncIterator

from agent.core.message import (
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
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


def _messages_to_dashscope(messages: list[Message], system: str) -> list[dict[str, Any]]:
    """把 agent.core 消息翻译成 DashScope Generation 格式（content 为字符串）。

    用于纯文本模型（qwen-plus / qwen-max / deepseek-v4-pro 等）。
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        if msg.role == "system":
            continue
        if msg.role == "assistant":
            content_text = "".join(
                b.text for b in msg.content if isinstance(b, TextContent)
            )
            tool_calls = []
            for b in msg.content:
                if isinstance(b, ToolUseContent):
                    tool_calls.append({
                        "id": b.id,
                        "type": "function",
                        "function": {
                            "name": b.name,
                            "arguments": json.dumps(b.input, ensure_ascii=False),
                        },
                    })
            entry: dict[str, Any] = {"role": "assistant"}
            if content_text:
                entry["content"] = content_text
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        elif msg.role == "user":
            # tool_result -> role="tool" 的独立 message
            tool_results = [b for b in msg.content if isinstance(b, ToolResultContent)]
            if tool_results:
                for tr in tool_results:
                    out.append({
                        "role": "tool",
                        "tool_call_id": tr.id,
                        "content": tr.output if isinstance(tr.output, str) else json.dumps(tr.output, ensure_ascii=False),
                    })
            # 普通 user 文本
            text_parts = [b.text for b in msg.content if isinstance(b, TextContent)]
            if text_parts:
                out.append({"role": "user", "content": "".join(text_parts)})
        else:
            out.append({"role": msg.role, "content": msg.get_text()})
    return out


def _messages_to_dashscope_multimodal(
    messages: list[Message], system: str, *, skip_images: bool = False
) -> list[dict[str, Any]]:
    """把 agent.core 消息翻译成 DashScope MultiModalConversation 格式。

    与 _messages_to_dashscope 的区别：content 是 list of dict（[{"text": ...}, {"image": ...}]），
    支持图片传入。用于多模态模型（qwen3.5-flash / qwen-vl-plus 等）。

    skip_images=True 时（纯文本模式兜底），图片块转为文本占位符，避免 API 报错。
    """
    from agent.core.message import ImageContent

    def _to_content_list(text: str = "", images: list | None = None) -> list[dict[str, Any]]:
        """构造 MultiModalConversation 的 content list。"""
        cl: list[dict[str, Any]] = []
        if text:
            cl.append({"text": text})
        if images and not skip_images:
            for img in images:
                cl.append({"image": f"data:{img.media_type};base64,{img.data}"})
        return cl

    out: list[dict[str, Any]] = [{"role": "system", "content": [{"text": system}]}]
    for msg in messages:
        if msg.role == "system":
            continue
        if msg.role == "assistant":
            content_text = "".join(
                b.text for b in msg.content if isinstance(b, TextContent)
            )
            tool_calls = []
            for b in msg.content:
                if isinstance(b, ToolUseContent):
                    tool_calls.append({
                        "id": b.id,
                        "type": "function",
                        "function": {
                            "name": b.name,
                            "arguments": json.dumps(b.input, ensure_ascii=False),
                        },
                    })
            entry: dict[str, Any] = {"role": "assistant"}
            cl = _to_content_list(content_text)
            if cl:
                entry["content"] = cl
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        elif msg.role == "user":
            # 收集 ToolResultContent（含图片）和普通文本/图片块
            text_parts = [b.text for b in msg.content if isinstance(b, TextContent) and b.text]
            combined_text = "".join(text_parts)

            # 收集所有图片（ToolResultContent.images + ImageContent）
            all_images = []
            for b in msg.content:
                if isinstance(b, ToolResultContent) and b.images:
                    all_images.extend(b.images)
                    # tool_result 的文本内容追加到 combined_text
                    if b.content:
                        combined_text = (combined_text + "\n" + b.content).strip()
                elif isinstance(b, ImageContent):
                    all_images.append(b)

            # 纯 tool_result（无图片）→ role="tool" 的独立 message
            tool_results_no_img = [b for b in msg.content
                                   if isinstance(b, ToolResultContent) and not b.images]
            for tr in tool_results_no_img:
                tr_text = tr.output if isinstance(tr.output, str) else json.dumps(tr.output, ensure_ascii=False)
                out.append({
                    "role": "tool",
                    "tool_call_id": tr.tool_use_id,
                    "content": [{"text": tr_text}],
                })

            # user 消息（文本 + 图片）
            content_list = _to_content_list(combined_text, all_images)
            if content_list:
                out.append({"role": "user", "content": content_list})
        else:
            out.append({"role": msg.role, "content": [{"text": msg.get_text()}]})
    return out


class DashScopeProvider(LLMProvider):
    """DashScope 原生 SDK Provider。

    用 dashscope.Generation.call(stream=True) 流式调用，
    通过线程+Queue 桥接到 async 环境。
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        enable_thinking: bool = True,
        thinking_budget: int = 2000,
        model_type: str = "multimodal",
    ) -> None:
        import dashscope

        if api_key:
            dashscope.api_key = api_key
        # 注意：DashScope 原生 SDK 内部已默认 base_api_url = https://dashscope.aliyuncs.com/api/v1
        # 用户在 /models 表单里填的 base_url（如 https://dashscope.aliyuncs.com）缺 /api/v1 后缀，
        # 直接赋给 dashscope.base_api_url 会触发 "400 url error"。
        # 只有当 base_url 显式包含 /api/v1 路径时才覆盖（用于私有部署/代理场景），
        # 否则强制重置为 SDK 默认值（避免被同进程内之前的错误实例污染模块级全局状态）。
        if base_url and "/api/v1" in base_url:
            dashscope.base_api_url = base_url.rstrip("/")
        else:
            dashscope.base_api_url = "https://dashscope.aliyuncs.com/api/v1"

        self._dashscope = dashscope
        self.default_model = model or "qwen3.5-flash"
        self.name = "dashscope"
        self._model_type = model_type
        self._enable_thinking = enable_thinking
        self._thinking_budget = thinking_budget
        self._force_no_thinking = False

    @property
    def base_url(self) -> str:
        return getattr(self._dashscope, "base_api_url", "https://dashscope.aliyuncs.com")

    def set_thinking_enabled(self, enabled: bool) -> None:
        self._enable_thinking = bool(enabled)
        self._force_no_thinking = not bool(enabled)

    def is_thinking_enabled(self) -> bool:
        return self._enable_thinking and not self._force_no_thinking

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
        # 根据 model_type 选择 API：
        # - multimodal → MultiModalConversation.call()（multimodal-generation 端点）
        #   用于 qwen3.5-flash / qwen-vl-plus 等视觉模型，用 Generation.call() 会报 "400 url error"
        # - text → Generation.call()（text-generation 端点）
        #   用于 qwen-plus / qwen-max / deepseek-v4-pro 等纯文本模型
        use_multimodal = self._model_type == "multimodal"
        if use_multimodal:
            from dashscope import MultiModalConversation as _Api
            api_msgs = _messages_to_dashscope_multimodal(messages, system)
        else:
            from dashscope import Generation as _Api
            api_msgs = _messages_to_dashscope(messages, system)

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

        # 思考模式：inc_thinking 控制
        thinking_on = self._enable_thinking and not self._force_no_thinking

        call_kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": api_msgs,
            "max_tokens": max_tokens,
            "stream": True,
            "result_format": "message",
            "inc_thinking": thinking_on,
        }
        if tool_schemas:
            call_kwargs["tools"] = tool_schemas
        if temperature is not None:
            call_kwargs["temperature"] = temperature
        if thinking_on and self._thinking_budget > 0:
            call_kwargs["thinking_budget"] = self._thinking_budget

        # dashscope SDK 是同步的，用线程+Queue 桥接到 async
        queue: asyncio.Queue[Any] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _run_sync() -> None:
            """在线程中运行同步的 dashscope 流式调用。"""
            try:
                responses = _Api.call(**call_kwargs)
                prev_text = ""
                prev_reasoning = ""
                tool_calls_map: dict[int, dict[str, Any]] = {}
                final_usage = Usage()
                finish_reason = "stop"

                for resp in responses:
                    if resp.status_code != 200:
                        # 把错误放入队列
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            Exception(f"DashScope API error: {resp.status_code} - {resp.message}"),
                        )
                        return

                    # usage
                    if resp.usage:
                        final_usage = Usage(
                            input_tokens=resp.usage.get("input_tokens", 0),
                            output_tokens=resp.usage.get("output_tokens", 0),
                        )

                    choices = resp.output.choices if resp.output else []
                    if not choices:
                        continue
                    choice = choices[0]

                    if choice.finish_reason:
                        finish_reason = choice.finish_reason

                    msg = choice.message

                    # 思考内容
                    # Generation 流式：reasoning_content 是累积的（完整到当前点），需取增量
                    # MultiModalConversation 流式：reasoning_content 是增量的（仅含新增），直接用
                    reasoning = getattr(msg, "reasoning_content", None) or ""
                    if reasoning:
                        if use_multimodal:
                            # 增量模式：直接发出
                            loop.call_soon_threadsafe(queue.put_nowait, ThinkingDelta(text=reasoning))
                        elif len(reasoning) > len(prev_reasoning):
                            # 累积模式：取增量
                            delta = reasoning[len(prev_reasoning):]
                            prev_reasoning = reasoning
                            loop.call_soon_threadsafe(queue.put_nowait, ThinkingDelta(text=delta))

                    # 文本内容
                    # Generation 返回 string（累积）；MultiModalConversation 返回 list[{"text": "..."}]（增量）
                    raw_content = msg.content
                    if isinstance(raw_content, list):
                        text = "".join(
                            item.get("text", "") for item in raw_content
                            if isinstance(item, dict)
                        )
                    else:
                        text = raw_content or ""
                    if text:
                        if use_multimodal:
                            # 增量模式：直接发出整个 text
                            loop.call_soon_threadsafe(queue.put_nowait, TextDelta(text=text))
                        elif len(text) > len(prev_text):
                            # 累积模式：取增量
                            delta = text[len(prev_text):]
                            prev_text = text
                            loop.call_soon_threadsafe(queue.put_nowait, TextDelta(text=delta))

                    # 工具调用
                    tool_calls = getattr(msg, "tool_calls", None) or []
                    for tc in tool_calls:
                        idx = tc.get("index", 0) if isinstance(tc, dict) else 0
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {
                                "id": "",
                                "name": "",
                                "args": "",
                            }
                        entry = tool_calls_map[idx]
                        fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                        if fn.get("id"):
                            entry["id"] = fn["id"]
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        if fn.get("name"):
                            entry["name"] = fn["name"]
                        if fn.get("arguments"):
                            entry["args"] += fn["arguments"]

                # 发出累积的工具调用
                for entry in tool_calls_map.values():
                    if not entry["name"] and not entry["args"]:
                        continue
                    try:
                        parsed = json.loads(entry["args"]) if entry["args"] else {}
                    except json.JSONDecodeError:
                        parsed = {}
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ToolCall(id=entry["id"] or f"call_{entry['name']}", name=entry["name"], input=parsed),
                    )
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        ToolCallEnd(id=entry["id"] or f"call_{entry['name']}"),
                    )

                # 结束标记
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    Stop(reason=finish_reason or "stop", usage=final_usage),
                )

            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, e)

        # 启动线程
        thread = threading.Thread(target=_run_sync, daemon=True)
        thread.start()

        # 从 queue 读取事件
        try:
            while True:
                item = await queue.get()
                if isinstance(item, Exception):
                    if isinstance(item, ProviderError):
                        raise item
                    raise ProviderError(str(item)) from item
                yield item
                if isinstance(item, Stop):
                    break
        finally:
            if thread.is_alive():
                thread.join(timeout=5)

    async def close(self) -> None:
        pass
