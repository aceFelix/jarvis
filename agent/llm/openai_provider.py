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
            elif tool_calls:
                # OpenAI 规范要求 assistant message with tool_calls 的 content 字段存在且为 null；
                # 智谱 GLM 等兼容接口在字段缺失时可能挂起或报错。
                entry["content"] = None
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
                        tool_content = (
                            b.content + f"\n[附带 {img_count} 张图片（当前为纯文本模型，图片已省略）]"
                        )
                    else:
                        # OpenAI 规范要求 role="tool" 的 content 为 string；
                        # 智谱 GLM 等兼容接口在收到 list 类型 content 时可能挂起或报错。
                        # 先把图片描述以文本形式附加，后续如需视觉 tool_result 再追加独立 user 消息。
                        tool_content = b.content + f"\n[附带 {len(b.images)} 张图片]"
                    out.append(
                        {
                            "role": "tool",
                            "tool_call_id": b.tool_use_id,
                            "content": tool_content,
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


# ── P-02: 共享 httpx.AsyncClient，复用 HTTP 连接池 ──
_shared_http_client: Any | None = None


def _get_shared_http_client() -> Any:
    """获取或创建共享的 httpx.AsyncClient。

    所有 OpenAIProvider 实例共享同一个底层 HTTP 连接池，
    避免每次切换模型都新建 TCP 连接。

    @author aceFelix
    """
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        import httpx
        _shared_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=50),
        )
    return _shared_http_client


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
        kwargs: dict[str, Any] = {"timeout": 180.0}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        # P-02: 共享 HTTP 连接池，避免每次切换模型新建 TCP 连接
        kwargs["http_client"] = _get_shared_http_client()
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

    def set_model_type(self, model_type: str) -> None:
        """动态切换模型类型（multimodal / text）。

        切换模型时可能复用同一个 provider 实例，但需要改变图片处理方式。
        """
        self._model_type = model_type

    @staticmethod
    def _derive_name(base_url: str) -> str:
        """根据 base_url 自动检测厂商名 —— 配置表驱动。

        新增厂商只需在 PROVIDER_REGISTRY 加 url_patterns，无需修改此方法。
        """
        from agent.llm.provider_registry import lookup_by_url
        return lookup_by_url(base_url)

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

        # 深度思考 —— 配置表驱动，新增厂商只需在 THINKING_CONFIGS 加一行
        # 此前通过 if-else 硬编码各厂商的思考参数差异，
        # 现在由 apply_thinking() 根据厂商名查表统一注入。
        from agent.llm.thinking import THINKING_CONFIGS, apply_thinking
        thinking_on = self._enable_thinking and not getattr(self, '_force_no_thinking', False)
        cfg = THINKING_CONFIGS.get(self.name)
        if cfg:
            apply_thinking(request_kwargs, cfg, thinking_on, self._thinking_budget)

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
            from agent.llm.errors import classify
            classified = classify(e)
            raise ProviderError(classified.user_message) from e

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
    from agent.core.logging import get_logger
    get_logger().debug(
        "_parse_tool_args fallback: tool=%s raw_len=%d raw=%r",
        tool_name or "?", len(raw), raw[:200],
    )
    return {}
