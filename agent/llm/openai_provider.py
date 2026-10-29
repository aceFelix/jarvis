"""OpenAI（及兼容接口）Provider。

支持所有 OpenAI 兼容的服务: OpenAI 官方、DeepSeek、Moonshot、本地 vLLM/Ollama 等。
通过 base_url 切换。

工具调用走 OpenAI 的 function calling 格式，流式事件统一翻译成 LLMEvent。
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from agent.llm.text_tool_calls import TextualToolCallGuard

from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
# 发送前修复 tool_use / tool_result 配对：历史里的悬空调用会让 API 拒收整个
# 请求（用户表现为该会话永久失败）。见 docs/fixlogs/dangling-tool-use-fix.md。
# @author aceFelix
from agent.core.tool_pairing import ensure_tool_pairing
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

    出口兜底：转换前先跑 ensure_tool_pairing，保证每个 tool_call 后面都有配对的
    role="tool" 消息（悬空调用会被 OpenAI 兼容接口整体拒收）。
    """
    messages = ensure_tool_pairing(messages)
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
            else:
                # 只含 thinking 的消息：过滤后无内容可发，补空串占位。
                # 缺 content 字段的 assistant 消息会被部分兼容端点判为非法请求。
                entry["content"] = ""
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

            # 顺序铁律：role="tool" 消息必须紧跟 assistant 的 tool_calls，
            # 故先输出 tool 消息、再输出 user 消息。配对修复会把占位 tool_result
            # 前置进既有 user 消息（同一条消息同时含结果与文本），顺序颠倒会被
            # 兼容接口判为「tool 消息不在 tool_calls 之后」。
            # @author aceFelix
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

            if user_content:
                out.append({"role": "user", "content": user_content})
    return out


# ── P-02: 共享 httpx.AsyncClient，复用 HTTP 连接池 ──
_shared_http_client: Any | None = None


def _new_http_pool(*, max_keepalive: int = 10, max_connections: int = 50) -> Any:
    """新建一个 httpx.AsyncClient（共享池与独占池共用同一套参数）。

    @author aceFelix
    """
    import httpx
    return httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=10.0),
        limits=httpx.Limits(
            max_keepalive_connections=max_keepalive, max_connections=max_connections
        ),
    )


def _get_shared_http_client() -> Any:
    """获取或创建共享的 httpx.AsyncClient。

    所有 OpenAIProvider 实例共享同一个底层 HTTP 连接池，
    避免每次切换模型都新建 TCP 连接。

    @author aceFelix
    """
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        _shared_http_client = _new_http_pool()
    return _shared_http_client


async def close_shared_http_client() -> None:
    """关闭进程级共享连接池（**唯一**允许关它的地方）。

    必须与 ``OpenAIProvider.close()`` 区分开：provider 是短命对象（记忆提炼、
    连通性测试每次都新建一个），连接池是进程级资源。两者混在一起关，就会
    出现「一个临时 provider 下班，全员断网」的事故。

    @author aceFelix
    """
    global _shared_http_client
    if _shared_http_client is not None and not _shared_http_client.is_closed:
        try:
            await _shared_http_client.aclose()
        except Exception:
            pass
    _shared_http_client = None


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
        dedicated_client: bool = False,
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
        # _thinking_effort: 运行时思考强度档位（low/medium/high；None/on = 用厂商默认）。
        # 由 set_thinking_effort 统一维护，stream() 传给 apply_thinking 翻译成厂商参数。
        self._thinking_effort: str | None = None
        # 根据 base_url 推断实际后端名
        self._display_name = self._derive_name(self._base_url)
        kwargs: dict[str, Any] = {"timeout": 180.0}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        # P-02: 共享 HTTP 连接池，避免每次切换模型新建 TCP 连接。
        # dedicated_client=True 时为后台任务（记忆画像提炼等）建**独占池**：
        # 这类任务跑在独立线程、与主对话并发，共用一套连接池时，后台任务的
        # 流量与关闭都会影响前台可用连接。物理隔开最省心——各自用各自的池，
        # 退场时只关自己的（见 close()）。
        # @author aceFelix
        if dedicated_client:
            self._own_pool = _new_http_pool(max_keepalive=4, max_connections=8)
            kwargs["http_client"] = self._own_pool
        else:
            self._own_pool = None
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

    def set_thinking_effort(self, level: str | None) -> None:
        """设置思考强度档位：off 关闭思考；low/medium/high 开启并记录档位；on/None 用默认。

        档位仅记录到 _thinking_effort，真正的参数翻译在 stream() 里由
        apply_thinking 按当前厂商 THINKING_CONFIGS 完成（不同厂商语义不同）。
        @author aceFelix
        """
        if level in (None, "off"):
            self.set_thinking_enabled(False)
            self._thinking_effort = None
            return
        self.set_thinking_enabled(True)
        self._thinking_effort = None if level == "on" else level

    def is_thinking_effort(self) -> str | None:
        """返回当前思考强度档位（关闭时为 None）。"""
        if not self.is_thinking_enabled():
            return "off"
        return self._thinking_effort or "on"

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
        known_tool_names: set[str] | None = None,
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

        # 每次请求都记下「这一发到底发给了谁」。
        # 2026-10 排查「重启后第一句正常、之后必挂」时，缺的正是这份现场：
        # 只知道报 Connection error，却不知道第二次请求的 base_url / 模型名 /
        # provider 实例是否被悄悄换掉。用 id(self) 与 id(self._client) 能看出
        # provider 有没有被重建过（重建后 id 必然变），对定位极关键。
        # api_key 只留前 6 位，日志可外传不泄密。@author aceFelix
        from agent.core.logging import get_logger
        _key_prefix = str(getattr(self._client, "api_key", "") or "")[:6]
        get_logger().debug(
            "LLM 请求: model=%s base_url=%r key=%s... provider_id=%s client_id=%s "
            "msgs=%d tools=%d system_chars=%d thinking=%s effort=%s",
            model, self._base_url, _key_prefix, id(self), id(self._client),
            len(api_msgs), len(tool_schemas), len(system),
            self.is_thinking_enabled(), getattr(self, "_thinking_effort", None),
        )

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
            apply_thinking(
                request_kwargs, cfg, thinking_on, self._thinking_budget,
                getattr(self, '_thinking_effort', None),
            )

        # 上下文缓存 —— 配置表驱动（显式模式注入 cache_control，隐式不动）
        from agent.llm.cache_policy import CACHE_POLICIES, apply_cache_markers
        cache_cfg = CACHE_POLICIES.get(self.name)
        if cache_cfg:
            apply_cache_markers(api_msgs, cache_cfg, tool_defs=tool_schemas)

        # 累积工具调用参数（OpenAI 分片发 arguments）
        tool_acc: dict[int, dict[str, Any]] = {}
        # 文本态工具调用兜底：过滤泄漏进 content 的 DSML/XML，避免裸码回显
        # valid_names 取「本轮下发的」∪「库内真实存在的」：参见 base.stream 的
        # known_tool_names 说明。只取本轮会把模型凭提示词习惯直接写出的延迟工具
        # 调用全部抹掉（表现为裸码、工具不执行）。@author aceFelix
        guard = TextualToolCallGuard(set(t.name for t in tools) | set(known_tool_names or ()))

        # ---- 分段计时（2026-10 性能排查）----
        # 桌面壳比终端慢，但「慢在哪一段」此前完全不可见：只知道用户发完消息要
        # 等一会儿，分不清是排队等网、模型思考、还是正文生成，于是只能靠猜
        # （改档位、关功能——都是降级）。这里埋四个时间点，一次请求就能把
        # 总耗时拆开，让优化有证据可依。@author aceFelix
        _t0 = time.monotonic()
        _t_first_think: float | None = None
        _t_first_text: float | None = None
        _think_chars = 0
        _text_chars = 0

        stream = None
        try:
            stream = await self._client.chat.completions.create(**request_kwargs)
            final_usage = Usage()
            finish_reason = "stop"
            async for chunk in stream:
                if chunk.usage:
                    # 读取缓存命中/创建统计（各厂商字段差异由 cache_policy 归一化）
                    from agent.llm.cache_policy import parse_cache_usage
                    cache_cfg = CACHE_POLICIES.get(self.name)
                    cached, created = (0, 0)
                    if cache_cfg:
                        cached, created = parse_cache_usage(chunk.usage, cache_cfg)
                    final_usage = Usage(
                        input_tokens=chunk.usage.prompt_tokens,
                        output_tokens=chunk.usage.completion_tokens,
                        cache_read_tokens=cached,
                        cache_creation_tokens=created,
                    )
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                # 思维链思考内容（reasoning_content 先于 content 到达）
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning and self.is_thinking_enabled():
                    if _t_first_think is None:
                        _t_first_think = time.monotonic() - _t0
                    _think_chars += len(reasoning)
                    yield ThinkingDelta(text=reasoning)
                if delta.content:
                    # 走泄漏过滤器：正常文本即时放行，命中 DSML 标记后转入缓存不回显
                    _safe = guard.feed(delta.content)
                    if _safe:
                        if _t_first_text is None:
                            _t_first_text = time.monotonic() - _t0
                        _text_chars += len(_safe)
                        yield TextDelta(text=_safe)
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
            # 释放过滤器里尾缓冲的剩余安全文本
            _tail = guard.flush()
            if _tail:
                yield TextDelta(text=_tail)
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
            # 兜底：无结构化 tool_calls 但正文泄漏了工具调用标记 → 解析成真实调用
            # （DeepSeek DSML / 部分兼容网关把调用吐进 content 文本，见 text_tool_calls.py）
            if not tool_acc:
                for pc in guard.drain():
                    yield ToolCall(id=pc.id, name=pc.name, input=pc.input)
                    yield ToolCallEnd(id=pc.id)
                # 兜底未产出任何调用（如本轮未发工具 schema）时，把吞掉的原文
                # 补回显，避免「半句话静默结束」——宁可看到裸码也不能丢信息
                _rescued = guard.rescue_text
                if _rescued:
                    yield TextDelta(text=_rescued)
            # 三段耗时一次性落地：
            #   首包思考 = 建连 + 服务端排队 + 模型想出第一个字（网络/服务端慢就卡在这）
            #   首字正文 = 思考阶段长度（档位高就拉长这一段）
            #   全程     = 你实际要等的时间
            # -1 表示本次没有该类内容（例如纯思考后直接发工具调用）。
            # @author aceFelix
            _total = time.monotonic() - _t0
            get_logger().info(
                "LLM 计时: 首包思考 %.2fs | 首字正文 %.2fs | 全程 %.2fs | "
                "思考 %d 字 | 正文 %d 字",
                _t_first_think if _t_first_think is not None else -1.0,
                _t_first_text if _t_first_text is not None else -1.0,
                _total, _think_chars, _text_chars,
            )
            yield Stop(reason=finish_reason or "stop", usage=final_usage)
        except Exception as e:
            from agent.llm.errors import classify
            classified = classify(e)
            raise ProviderError(classified.user_message) from e
        finally:
            # 必须显式关闭流（2026-10 关键修复）。
            # openai 的 AsyncStream 不会因为 async for 跑完就自动关闭：没关的
            # response 会让底层连接带着未读完的残留数据回到**共享连接池**
            # （见 _get_shared_http_client），下一次请求复用到这条脏连接就会
            # RemoteProtocolError 被 SDK 包成「Connection error」。
            # 结合用户现场「重启后第一句正常、之后每次必挂」完全吻合。
            # 注：提前 break / 被取消时也走这里，不会漏关。@author aceFelix
            if stream is not None:
                try:
                    await stream.close()
                except Exception:
                    # 关闭失败不能影响正常返回，也不能掩盖原始异常
                    pass

    async def close(self) -> None:
        """释放 provider 自身（**不会关闭全局共享连接池**）。

        为什么不能关共享池（2026-10 铁证）：P-02 之后所有 OpenAIProvider 共用
        一个进程级 httpx client（见 ``_get_shared_http_client``）。而
        ``AsyncOpenAI.close()`` 会把传入的 http_client 一并 ``aclose()``，
        于是任何一个短命 provider 下班都会把大家的连接池关死。

        实机现场：记忆画像提炼每轮新建一个临时 provider，提炼后走 finally
        里的 ``provider.close()``；主对话下一次请求就直接
        ``RuntimeError: Cannot send a request, as the client has been closed``
        —— 被 openai SDK 包成「Connection error」，只有重启进程才恢复，
        表现就是「刚启动第一句正常，之后每次必挂」。

        当前规则：**只有独占池（dedicated_client=True）才真正关闭**；
        共享池的生命周期由 ``close_shared_http_client()`` 在进程退出时统一处理。
        @author aceFelix
        """
        own_pool = getattr(self, "_own_pool", None)
        if own_pool is not None and not own_pool.is_closed:
            try:
                await own_pool.aclose()
            except Exception:
                pass


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
