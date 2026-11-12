"""MCP 工具熔断体验：描述里必须带状态，错误里必须带「别再试」。

## 事故（2026-11-12 实机）

`tyc-mcp` 熔断后，模型在一次任务里**连调 4 次**同一个工具，每次都拿到
``MCP server [tyc-mcp] 已熔断 (3 次失败，156s 后重试)``。原因不是模型记不住：

1. 熔断状态只出现在**调用之后**的结果里，而工具列表里那个工具看起来完全正常
   —— 模型在选择工具时没有任何依据；
2. 错误文案只是陈述，没有「别再试」的指令；
3. ``{e}`` 直接插值 KeyError，文案还带一对引号（``'...'``），更像底层异常。

修法：描述动态带状态（未熔断时逐字节不变，保证请求前缀稳定），错误标
``retryable=False`` 并写明禁令。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import time

from agent.core.extensions.mcp_client import MCPClient, McpToolDef
from agent.tools.extensions.mcp_tool import MCPToolWrapper

_SERVER = "tyc-mcp"


def _tool_def() -> McpToolDef:
    return McpToolDef(_SERVER, "search_companies", "按关键词查公司", {"type": "object"})


def _client(fails: int = 3, age: float = 0.0) -> MCPClient:
    """构造一个把 ``_breaker`` 直接摆成指定状态的 client（不连接、不发网络）。"""
    client = MCPClient()
    client._breaker[_SERVER] = (fails, time.time() - age)
    return client


class TestDescription:
    def test_normal_description_is_untouched(self) -> None:
        """未熔断：描述与基础文案逐字节相同（否则每轮都在破缓存）。"""
        wrapper = MCPToolWrapper(MCPClient(), _tool_def())
        assert wrapper.description == "[MCP/tyc-mcp] 按关键词查公司"

    def test_fused_description_warns(self) -> None:
        wrapper = MCPToolWrapper(_client(), _tool_def())
        text = wrapper.description
        assert text.startswith("[MCP/tyc-mcp] 按关键词查公司")
        assert "[不可用]" in text
        assert "已熔断" in text
        assert "改用其他工具" in text
        # 必须是 GBK 可编码的字符：描述会被 /tools 这类命令打印到 Windows 控制台，
        # emoji（如 ⛔ U+26D4）在 cp936 上直接抛 UnicodeEncodeError
        text.encode("gbk")

    def test_below_threshold_not_reported(self) -> None:
        wrapper = MCPToolWrapper(_client(fails=2), _tool_def())
        assert "已熔断" not in wrapper.description

    def test_expired_fuse_not_reported(self) -> None:
        """冷却期过了就不再谎报不可用（否则工具会一直被标死）。"""
        wrapper = MCPToolWrapper(_client(age=400), _tool_def())
        assert wrapper.description == "[MCP/tyc-mcp] 按关键词查公司"


class TestCall:
    def test_fused_call_says_stop_and_is_not_retryable(self) -> None:
        wrapper = MCPToolWrapper(_client(), _tool_def())
        result = asyncio.run(wrapper.call({}, None))
        assert result.is_error is True
        assert result.retryable is False
        data = str(result.data)
        assert "不要再调用" in data
        assert "已熔断" in data

    def test_disconnected_call_also_says_stop(self) -> None:
        wrapper = MCPToolWrapper(MCPClient(), _tool_def())
        result = asyncio.run(wrapper.call({}, None))
        assert result.is_error is True and result.retryable is False
        assert "不要再调用" in str(result.data)

    def test_error_text_has_no_keyerror_quotes(self) -> None:
        """原先把 KeyError 直接插值，文案开头带一对引号，像底层异常。"""
        wrapper = MCPToolWrapper(_client(), _tool_def())
        result = asyncio.run(wrapper.call({}, None))
        assert "'MCP server" not in str(result.data)
