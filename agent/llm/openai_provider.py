"""OpenAI（及兼容接口）Provider。

支持所有 OpenAI 兼容的服务: OpenAI 官方、DeepSeek、Moonshot、本地 vLLM/Ollama 等。
通过 base_url 切换。

工具调用走 OpenAI 的 function calling 格式，流式事件统一翻译成 LLMEvent。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from agent.core.message import (
    ImageContent,
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


def _messages_to_openai(
    messages: list[Message], system: str, *, skip_images: bool = False
) -> list[dict[str, Any]]:
    """把 agent.core 消息翻译成 OpenAI chat 格式。

    OpenAI 的 tool_call 与 tool_result 都在 message 的 content/字段里:
    - assistant 的 tool_use -> message["tool_calls"]
    - user 的 tool_result -> role="tool" 的独立 message

    skip_images=True 时（纯文本模型），图片块转为文本占位符，
    避免 API 报错（很多文本模型不接受 image_url content part）。
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        if msg.role == "system":
            # 系统消息已合并，跳过
            continue
        if msg.role == "assistant":
            content_text = "".join(
                b.text for b in msg.content
                if isinstance(b, TextContent)
                # 跳过 ThinkingContent（思考块不发给 API）
            )
            tool_calls = []
            for b in msg.content:
                if isinstance(b, ToolUseContent):
                    tool_calls.append(
                        {
                            "id": b.id,
                            "type": "function",
                            "function": {
                                "name": b.name,
                                "arguments": json.dumps(b.input, ensure_ascii=False),
                            },
                        }
                    )
            entry: dict[str, Any] = {"role": "assistant"}
            if content_text:
                entry["content"] = content_text
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        elif msg.role == "user":
            # user 消息里可能是普通文本、图片，也可能是 tool_result
            user_content: list[dict[str, Any]] = []
            has_user_image = False
            tool_results: list[ToolResultContent] = []

            for b in msg.content:
                if isinstance(b, TextContent):
                    if b.text:
                        user_content.append({"type": "text", "text": b.text})
                elif isinstance(b, ImageContent):
                    has_user_image = True
                    if not skip_images:
                        user_content.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{b.media_type};base64,{b.data}"
                                },
                            }
                        )
                elif isinstance(b, ToolResultContent):
                    tool_results.append(b)

            if has_user_image and skip_images and user_content:
                # 纯文本模型：用文字描述替代图片
                user_content[0]["text"] += "\n[附带图片（当前为纯文本模型，图片已省略）]"

            if user_content:
                out.append({"role": "user", "content": user_content})

            for b in tool_results:
                if b.images:
                    if skip_images:
                        # 纯文本模型：用文字描述替代图片
                        img_count = len(b.images)
                        content_list = [
                            {"type": "text",
                             "text": b.content + f"\n[附带 {img_count} 张图片（当前为纯文本模型，图片已省略）]"}
                        ]
                    else:
                        content_list: list[dict[str, Any]] = [
                            {"type": "text", "text": b.content}
                        ]
                        for img in b.images:
                            content_list.append(
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{img.media_type};base64,{img.data}"
                                    },
                                }
                            )
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b.tool_use_id,
                            "content": content_list,
                        }
                    )
                else:
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b.tool_use_id,
                            "content": b.content,
                        }
                    )
    return out


class OpenAIProvider(LLMProvider):
    """OpenAI 兼容 provider。"""

    name = "openai"  # 默认，下面 property 根据 base_url 动态覆盖
    default_model = "gpt-4o"

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
        try:
            from openai import AsyncOpenAI  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise ProviderError(
                "openai 包未安装，请运行: pip install openai"
            ) from e
        if model:
            self.default_model = model
        self._enable_thinking = enable_thinking
        self._thinking_budget = thinking_budget
        self._model_type = model_type  # "text" 或 "multimodal"
        self._base_url = base_url or ""
        # _force_no_thinking: 强制关闭思考的兜底标志（即使 _enable_thinking=True 也不发 enable_thinking）
        # voice_loop 语音模式用 set_thinking_enabled(False) 统一控制，内部同时管理这两个标志。
        self._force_no_thinking = False
        # 根据 base_url 推断实际后端名
        self._display_name = self._derive_name(self._base_url)
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    @property
    def name(self) -> str:  # type: ignore[override]
        """根据 base_url 动态返回后端名，而非固化为 'openai'。"""
        return self._display_name

    def set_thinking_enabled(self, enabled: bool) -> None:
        """统一开关深度思考模式。

        同时管理 _enable_thinking 和 _force_no_thinking 两个标志:
        - _enable_thinking: 主开关，控制是否在 extra_body 注入 enable_thinking=True
        - _force_no_thinking: 兜底标志，即使 _enable_thinking=True 也强制不发 enable_thinking
          （保留给历史兼容，也作为"无条件关闭"的双保险）
        """
        self._enable_thinking = bool(enabled)
        self._force_no_thinking = not enabled

    def is_thinking_enabled(self) -> bool:
        """返回当前思考模式是否开启（综合两个标志判断）。"""
        return self._enable_thinking and not self._force_no_thinking

    @staticmethod
    def _derive_name(base_url: str) -> str:
        if not base_url:
            return "openai"
        url_lower = base_url.lower()
        if "dashscope" in url_lower:
            return "dashscope"
        if "deepseek" in url_lower:
            return "deepseek"
        if "api.openai.com" in url_lower:
            return "openai"
        if "open.bigmodel.cn" in url_lower:
            return "zhipu"
        if "moonshot" in url_lower:
            return "moonshot"
        if "minimax" in url_lower:
            return "minimax"
        if "xiaomimimo" in url_lower:
            return "xiaomimimo"
        if "generativelanguage" in url_lower or "googleapis" in url_lower:
            return "google"
        if "siliconflow" in url_lower:
            return "siliconflow"
        # 其他未知的 OpenAI 兼容服务
        return "openai_compatible"

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

        # 深度思考——仅对已知支持的服务发送对应参数。
        # 注意：qwen3 系列模型默认开启思考，必须显式传 enable_thinking=False 才能关闭
        thinking_on = self._enable_thinking and not getattr(self, '_force_no_thinking', False)
        extra = request_kwargs.setdefault("extra_body", {})
        if self.name == "deepseek":
            # DeepSeek 官方 API 使用 thinking.type 开关思考模式
            extra["thinking"] = {"type": "enabled" if thinking_on else "disabled"}
            if thinking_on:
                # DeepSeek 思考强度默认 high，可通过 reasoning_effort 调整
                request_kwargs.setdefault("reasoning_effort", "high")
        elif self.name == "dashscope":
            # 阿里云 DashScope OpenAI 兼容接口使用 enable_thinking
            extra["enable_thinking"] = thinking_on
            if thinking_on and self._thinking_budget > 0:
                extra["thinking_budget"] = self._thinking_budget
        elif self.name == "zhipu":
            # 智谱 BigModel OpenAI 兼容接口使用 thinking.type 开关思考模式
            # GLM-5.x 默认开启 thinking，关闭时必须显式传 disabled
            extra["thinking"] = {"type": "enabled" if thinking_on else "disabled"}
            if thinking_on:
                # reasoning_effort 控制推理强度，仅 GLM-5.2 及以上支持
                # 默认 high，在推理深度和响应速度之间取平衡；需要更深推理可设 max
                request_kwargs.setdefault("reasoning_effort", "high")
        # 其他 OpenAI 兼容服务（Moonshot、MiniMax、OpenAI、Anthropic 等）
        # 不支持 enable_thinking/thinking_budget/thinking，不发送这些字段，避免 405/400 错误。

        # 累积工具调用参数（OpenAI 分片发 arguments）
        tool_acc: dict[int, dict[str, Any]] = {}

        try:
            stream = await self._client.chat.completions.create(**request_kwargs)
            final_usage = Usage()
            finish_reason = "stop"
            async for chunk in stream:
                if chunk.usage:
                    # 读取缓存命中统计（OpenAI/DashScope 兼容）
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
                # 思维链思考内容（reasoning_content 先于 content 到达）
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning and self.is_thinking_enabled():
                    yield ThinkingDelta(text=reasoning)
                if delta.content:
                    yield TextDelta(text=delta.content)
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
                            # 后续分片可能带 id/name（一般只有首片）
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
                # 跳过无效工具调用（空 name + 空 args = 流式解析残留）
                if not name and not raw_args:
                    continue
                parsed = _parse_tool_args(raw_args, name)
                yield ToolCall(
                    id=entry["id"] or f"call_{name or 'unknown'}",
                    name=name,
                    input=parsed,
                )
                yield ToolCallEnd(id=entry["id"] or f"call_{name or 'unknown'}")
            yield Stop(reason=finish_reason or "stop", usage=final_usage)
        except Exception as e:
            raise ProviderError(f"OpenAI API error: {e}") from e

    async def close(self) -> None:
        await self._client.close()


# ---- 流式工具调用参数解析（容错版）----

def _parse_tool_args(raw: str, tool_name: str | None = None) -> dict[str, Any]:
    """解析流式累积的工具调用参数 JSON。

    流式传输可能导致 JSON 截断（缺 }）或转义错乱（HTML 中的 \\n, \\t, \\"）。
    多级 fallback，确保不会因为 JSON 解析失败而丢失整个工具调用。
    """
    if not raw or not raw.strip():
        return {}

    # Level 1: 标准解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Level 2: 修复未闭合的花括号
    fixed = raw
    if fixed.count('{') > fixed.count('}'):
        fixed += '}' * (fixed.count('{') - fixed.count('}'))
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Level 3: 修复未闭合的字符串（补引号 + 补花括号）
    if fixed and fixed[-1] not in ('}', '"'):
        # 最后一个字符可能是被截断的 JSON 值
        fixed = fixed.rstrip(',') + '}'
        # 计算花括号
        while fixed.count('{') > fixed.count('}'):
            fixed += '}'
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Level 4: 用 json.JSONDecoder 分段解析——从前往后逐块尝试
    decoder = json.JSONDecoder()
    offset = 0
    stripped = raw.lstrip()
    while offset < len(stripped):
        try:
            obj, end = decoder.raw_decode(stripped[offset:])
            # 如果解析出的对象有内容字段，直接返回
            if isinstance(obj, dict) and obj:
                return obj
            offset += end
        except json.JSONDecodeError:
            break

    # Level 5: 正则兜底——提取常见字段
    import re
    result: dict[str, Any] = {}
    fields_of_interest = {
        'file_path': 'file_path', 'content': 'content', 'command': 'command',
        'pattern': 'pattern', 'text': 'text', 'query': 'query',
        'url': 'url', 'path': 'path', 'timeout': 'timeout',
    }
    for json_key, python_key in fields_of_interest.items():
        # 匹配 "key": "value" 或 "key": "value with \\"escaped\\" quotes"
        for pattern in [
            rf'"{json_key}"\s*:\s*"((?:[^"\\]|\\.)*)"',
            rf'"{json_key}"\s*:\s*(\d+)',
        ]:
            m = re.search(pattern, raw)
            if m:
                val = m.group(1)
                if python_key == 'timeout':
                    try:
                        result[python_key] = int(val)
                    except ValueError:
                        pass
                else:
                    # 反转义
                    val = val.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
                    result[python_key] = val
                break

    if result:
        return result

    # 全部失败：记录诊断信息
    import sys
    print(
        f"[ACP:parse-fail] tool={tool_name or '?'} raw[{len(raw)}]={raw[:200]!r}",
        file=sys.stderr, flush=True,
    )
    return {}
