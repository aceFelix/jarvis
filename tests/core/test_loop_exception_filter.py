"""回归：REPL 事件循环异常处理器过滤 MCP stdio 关闭刷屏。

背景：``asyncio.run`` 退出时 ``loop.shutdown_asyncgens()`` 逐个 ``aclose()``
async generator，MCP ``stdio_client`` 跨 task 关闭抛 ``BaseExceptionGroup``，
经 ``loop.call_exception_handler`` 打印成
"an error occurred during closing of asynchronous generator ..." 加上 anyio
的 ``cancel scope`` 报错。这条路径**不经过** asyncgen 的 GC finalizer，只能
在事件循环异常处理器上过滤（见 agent.main._install_quiet_loop_exception_handler）。

@author aceFelix
"""

from __future__ import annotations

import asyncio


class TestQuietLoopExceptionHandler:
    def _collect(self) -> list[dict]:
        import agent.main as m

        recorded: list[dict] = []

        async def _run() -> None:
            m._install_quiet_loop_exception_handler()
            loop = asyncio.get_running_loop()
            handler = loop.get_exception_handler()
            assert handler is not None
            # 拦截 default_exception_handler：只应记录"未被过滤"的上下文
            loop.default_exception_handler = recorded.append  # type: ignore[method-assign]
            handler(loop, {
                "message": "an error occurred during closing of "
                           "asynchronous generator <asyncgen X>",
                "exception": RuntimeError(
                    "Attempted to exit cancel scope in a different task "
                    "than it was entered in"
                ),
                "asyncgen": object(),
            })
            handler(loop, {
                "message": "boom",
                "exception": RuntimeError("... cancel scope ..."),
            })
            handler(loop, {"message": "真实错误"})

        asyncio.run(_run())
        return recorded

    def test_filters_mcp_shutdown_noise(self) -> None:
        """MCP 关闭噪音被丢弃，其它异常照常交给默认处理器。"""
        assert self._collect() == [{"message": "真实错误"}]
