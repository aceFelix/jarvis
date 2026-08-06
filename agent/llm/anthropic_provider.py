"""Anthropic Claude Provider。
Anthropic官方 anthropic SDK 的流式接口。
消息格式转换要点:
- agent.core.Message 的 content blocks 翻译成 Anthropic 的 content 格式
- system 消息单独传（Anthropic API 的 system 参数）
- ToolUseContent -> {"type": "tool_use", ...}
- ToolResultContent -> {"type": "tool_result", "tool_use_id": ..., "content": ...}
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from agent.core.message import ImageContent, Message, TextContent, ToolResultContent, ToolUseContent
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
from agent.llm.cache_policy import CACHE_POLICIES


def _block_to_anthropic(block: Any) -> dict[str, Any]:
    """把 agent.core 内容块翻译成 Anthropic content 格式。"""
    if isinstance(block, TextContent):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseContent):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
    if isinstance(block, ImageContent):
        # 用户直接传入的图片（/image /paste）
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": block.media_type,
                "data": block.data,
            },
        }
    if isinstance(block, ToolResultContent):
        # 多模态: 带 images 时 content 序列化成 [text, image...] 列表，
        # 让支持视觉的 Claude 直接看到图片（如 ScreenShot 截图回传）。
        if block.images:
            content_list: list[dict[str, Any]] = [
                {"type": "text", "text": block.content}
            ]
            for img in block.images:
                content_list.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": img.media_type,
                            "data": img.data,
                        },
                    }
                )
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": content_list,
                "is_error": block.is_error,
            }
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    raise ValueError(f"Unknown content block: {block!r}")


def _messages_to_anthropic(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """转换对话历史。system 消息提取出来单独返回（Anthropic API 要求）。"""
    from agent.core.message import ThinkingContent
    system_parts: list[str] = []
    api_msgs: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.get_text())
            continue
        # 过滤掉 ThinkingContent：思考内容是模型内部状态，
        # DeepSeek 等第三方 anthropic 兼容端点不支持 thinking block，
        # 传过去会报 "Unknown content block" 错误
        blocks = [_block_to_anthropic(b) for b in msg.content
                  if not isinstance(b, ThinkingContent)]
        api_msgs.append(
            {
                "role": msg.role,
                "content": blocks,
            }
        )
    return "\n\n".join(system_parts), api_msgs


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider。需要 ANTHROPIC_API_KEY 环境变量。"""

    default_model = "claude-3-5-sonnet-20241022"

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        try:
            import anthropic  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise ProviderError(
                "anthropic 包未安装，请运行: pip install anthropic"
            ) from e
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**kwargs)
        # 根据 base_url 推断实际后端名（如 deepseek 走 anthropic 兼容协议时显示 deepseek）
        self._display_name = self._derive_name(base_url or "")
        # 思考模式标志：默认开启（与 OpenAI Provider 的 enable_thinking=True 保持一致）。
        # voice_loop 通过 set_thinking_enabled(False) 统一关闭。
        # stream() 会根据此标志注入 thinking={"type": "enabled"} 参数。
        self._thinking_enabled = True

    @property
    def name(self) -> str:  # type: ignore[override]
        """根据 base_url 动态返回后端名，而非固化为 'anthropic'。

        Anthropic 兼容协议被多家服务采用（如 DeepSeek），
        根据 URL 区分实际后端，避免 banner 误显示为 anthropic。
        """
        return self._display_name

    @staticmethod
    def _derive_name(base_url: str) -> str:
        if not base_url:
            return "anthropic"
        url_lower = base_url.lower()
        if "deepseek" in url_lower:
            return "deepseek"
        if "anthropic.com" in url_lower:
            return "anthropic"
        return "anthropic_compatible"

    def set_thinking_enabled(self, enabled: bool) -> None:
        """统一开关深度思考模式。

        Anthropic 的 extended thinking 需在 stream() 中传 thinking={"type": "enabled"}。
        DeepSeek 的 Anthropic 兼容端点同样通过此参数开启思考模式。
        voice_loop 进入语音模式时调 set_thinking_enabled(False) 关闭。
        """
        self._thinking_enabled = bool(enabled)

    def is_thinking_enabled(self) -> bool:
        """返回当前思考模式是否开启。"""
        return self._thinking_enabled

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
        # 把 agent.core 消息合并进 system
        sys_from_msgs, api_msgs = _messages_to_anthropic(messages)
        full_system = "\n\n".join(p for p in [system, sys_from_msgs] if p)

        tool_defs = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

        # ---- Prompt Caching: 在 system 和 tools 上标记 cache_control 断点 ----
        # Anthropic 缓存前缀匹配：system + tools 是每次请求最稳定的前缀，
        # 标记后服务端会缓存这部分的 KV，后续请求命中时 input token 费用降 90%。
        # 策略配置见 agent.llm.cache_policy.CACHE_POLICIES["anthropic"]
        system_blocks: list[dict[str, Any]] = [
            {"type": "text", "text": full_system, "cache_control": {"type": "ephemeral"}}
        ]
        # 在最后一个 tool 上标记断点（tools 整体作为前缀的一部分被缓存）
        if tool_defs:
            tool_defs[-1]["cache_control"] = {"type": "ephemeral"}

        # 滚动断点：最后一条 user 消息上打标记 —— 多轮对话中该位置逐轮滚动，
        # 每轮命中即续期 5 分钟 TTL，且不会破坏 system/tools 的独立缓存块。
        # 注意：CACHE_POLICIES 必须在此处（函数内首次引用前）绑定，
        # 否则下方 usage 解析的 import 会把它遮蔽为局部变量导致 UnboundLocalError。
        from agent.llm.cache_policy import CACHE_POLICIES, apply_cache_markers
        apply_cache_markers(api_msgs, CACHE_POLICIES.get("anthropic"), tool_defs=tool_defs)

        request_kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "system": system_blocks,
            "messages": api_msgs,
            "max_tokens": max_tokens,
        }
        if tool_defs:
            request_kwargs["tools"] = tool_defs

        # ---- 思考模式参数注入 ----
        # DeepSeek Anthropic 兼容端点：thinking={"type": "enabled/disabled"} 控制开关
        #   ⚠ DeepSeek 思考模式默认打开，必须显式传 {"type": "disabled"} 才能关闭，
        #     仅不传参数会走默认值（开），导致 /think off 后仍输出思考过程。
        # Anthropic 原生端点：需要额外 budget_tokens（最小 1024），默认关闭，
        #   不传参数即关闭，但显式传 disabled 也兼容。
        # 思考模式不支持 temperature（DeepSeek 文档：设置不会报错但不生效）
        if self._thinking_enabled:
            if self.name == "anthropic":
                # Anthropic 原生 extended thinking 需要预算 token
                request_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": 10000,
                }
            else:
                # DeepSeek 等兼容端点只需 type，不需要 budget_tokens
                request_kwargs["thinking"] = {"type": "enabled"}
            # 思考模式下不传 temperature（DeepSeek 文档：思考模式不支持 temperature）
        else:
            # 显式关闭思考模式——DeepSeek 兼容端点默认是开的，不传会漏
            request_kwargs["thinking"] = {"type": "disabled"}
            if temperature is not None:
                request_kwargs["temperature"] = temperature

        try:
            async with self._client.messages.stream(**request_kwargs) as stream:
                # 工具调用参数按 index 累积（Claude Code 风格）
                # 不依赖 SDK 的 get_final_message()——DeepSeek 等兼容端点
                # 可能不正确实现 final message 聚合。
                content_blocks: dict[int, dict[str, Any]] = {}
                text_buf = ""

                async for event in stream:
                    if event.type == "message_start":
                        continue
                    # 文本增量
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            text_buf += delta.text
                            yield TextDelta(text=delta.text)
                        elif delta.type == "thinking_delta":
                            # 思考模式下的思维链内容（DeepSeek/Anthropic extended thinking）
                            # thinking_delta 带 .thinking 属性，不是 .text
                            yield ThinkingDelta(text=delta.thinking)
                        elif delta.type == "input_json_delta":
                            # 累积工具参数 JSON 片段（关键修复！）
                            idx = event.index
                            if idx in content_blocks:
                                if "input_json" not in content_blocks[idx]:
                                    content_blocks[idx]["input_json"] = ""
                                content_blocks[idx]["input_json"] += delta.partial_json
                    elif event.type == "content_block_start":
                        block = event.content_block
                        idx = event.index
                        if block.type == "tool_use":
                            content_blocks[idx] = {
                                "id": block.id,
                                "name": block.name,
                                "input_json": "",
                            }
                        elif block.type == "text":
                            # text block 已在 content_block_delta 处理
                            pass
                        # thinking block 的内容通过 thinking_delta 事件流式接收，
                        # 这里不需要额外处理 content_block_start

                # 流结束后，解析累积的工具调用
                for idx in sorted(content_blocks.keys()):
                    entry = content_blocks[idx]
                    raw_json = entry.get("input_json", "")
                    if raw_json:
                        try:
                            parsed_input = json.loads(raw_json)
                        except json.JSONDecodeError:
                            # 容错：尝试修复未闭合的 JSON
                            fixed = raw_json
                            while fixed.count("{") > fixed.count("}"):
                                fixed += "}"
                            try:
                                parsed_input = json.loads(fixed)
                            except json.JSONDecodeError:
                                parsed_input = {"_raw": raw_json}
                    else:
                        parsed_input = {}

                    yield ToolCall(
                        id=entry["id"],
                        name=entry["name"],
                        input=parsed_input,
                    )
                    yield ToolCallEnd(id=entry["id"])

                # 获取 usage（不依赖 final message 的 content）
                try:
                    final = await stream.get_final_message()
                    u = final.usage
                    # 缓存统计按实际厂商查表归一化——Anthropic 兼容端点可能是
                    # DeepSeek 等第三方实现，其 cache_read_input_tokens 语义与
                    # Anthropic 官方不同（如 DeepSeek 返回累计值而非单次命中），
                    # 必须按 self.name 对应的策略字段解析。
                    # 关键：Anthropic 协议下 input_tokens 不含缓存命中部分，
                    # 必须传 input_includes_cache=False，否则备选字段的合理性
                    # 校验会误判命中数（远大于未命中 input_tokens）为累计值异常而丢弃。
                    from agent.llm.cache_policy import CACHE_POLICIES, parse_cache_usage
                    cache_cfg = CACHE_POLICIES.get(self.name)
                    cached, created = (0, 0)
                    if cache_cfg:
                        cached, created = parse_cache_usage(
                            u, cache_cfg,
                            input_tokens=u.input_tokens,
                            input_includes_cache=False,  # Anthropic 协议：input_tokens 不含缓存
                        )
                    else:
                        # 未注册厂商回退 Anthropic 官方字段
                        cached = getattr(u, "cache_read_input_tokens", 0) or 0
                        created = getattr(u, "cache_creation_input_tokens", 0) or 0
                    usage = Usage(
                        input_tokens=u.input_tokens,
                        output_tokens=u.output_tokens,
                        cache_read_tokens=cached,
                        cache_creation_tokens=created,
                    )
                    stop_reason = final.stop_reason or "stop"
                except Exception:
                    usage = Usage()
                    stop_reason = "stop"

                yield Stop(reason=stop_reason, usage=usage)
        except Exception as e:
            from agent.llm.errors import classify
            raise ProviderError(classify(e).user_message) from e

    async def close(self) -> None:
        await self._client.close()
