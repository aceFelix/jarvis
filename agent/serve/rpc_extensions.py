"""extensions.exec 桌面指令（标题栏「扩展服务」面板）—— RPC 混入。

从 `agent/serve/server.py` 拆出（aceFelix，2026-10），配合 800 行上限的拆分，
指令语义与回执口径**一字不改**。

一条通用指令承载五类扩展（MCP 服务 / 内置工具 / 技能 / 插件 / harness）的清单
与增删改启停，业务实现全在 `agent/ui/workbench/extensions_api.py`；本混入只做
协议信封拆装 + 线程编排：插件/harness 安装含网络 IO（git 克隆、远程下载），
必须走 `asyncio.to_thread`，否则冻住事件循环、流式事件全停。

依赖宿主类（`DesktopBridgeServer`）提供：``_api`` / ``_send_json``。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agent.serve import protocol

if TYPE_CHECKING:  # 仅为 IDE/类型检查声明宿主属性，运行期不引入循环导入
    from agent.ui.workbench.api import WorkbenchAPI


class ExtensionsRpcMixin:
    """extensions.exec 指令的处理器（异步，含网络 IO）。

    @author aceFelix
    """

    if TYPE_CHECKING:
        _api: "WorkbenchAPI"

        async def _send_json(self, ws: Any, msg: dict) -> None: ...  # pragma: no cover

    async def _cmd_extensions(self, ws: Any, data: dict) -> None:
        """extensions.exec 指令：桌面「扩展服务」面板的通用扩展动作入口（异步）。

        payload 形如 ``{"category": ..., "action": ..., ...}``；业务实现见
        agent/ui/workbench/extensions_api.py。插件/harness 安装含网络 IO（git 克隆/
        远程下载），故放 asyncio.to_thread 工作线程执行，避免冻住事件循环。回执
        result = {"ok": bool, "data": ..., "message": str}；异常（非法分类/动作）
        走 ok=false + 错误原文。@author aceFelix
        """
        try:
            payload: dict[str, Any] = {
                "category": str(data.get("category") or ""),
                "action": str(data.get("action") or ""),
            }
            # 透传其余动作参数（name/command/args/env/keyword/id 等），
            # 排除协议信封字段（type 由 WS 层消费，category/action 已单独取）
            for key, value in data.items():
                if key not in ("type", "category", "action"):
                    payload[key] = value
            result = await asyncio.to_thread(self._api.run_extension, payload)
            msg = protocol.build_reply(protocol.CMD_EXTENSIONS, ok=True, result=result)
        except Exception as e:  # noqa: BLE001 - 回执携带错误，连接不中断
            msg = protocol.build_reply(
                protocol.CMD_EXTENSIONS, ok=False, error=f"{type(e).__name__}: {e}"
            )
        await self._send_json(ws, msg)
