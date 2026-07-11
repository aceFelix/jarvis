"""ACP (Agent Client Protocol) stdio transport.

cc-connect 通过 ACP 协议桥接贾维斯到 IM 平台（飞书/钉钉/微信）。
此模块实现 ACP agent 端的最小必要方法，通过 stdin/stdout 通信。

协议：JSON-RPC 2.0 over stdio, 每行一条消息，\n 分隔。
参考：https://agentclientprotocol.com/protocol/v1/overview
"""

from __future__ import annotations

import json
import sys
from typing import Any


PROTOCOL_VERSION = "0.1"


class ACPServer:
    """ACP stdio server。处理 initialize / session/new / session/prompt。"""

    def __init__(self):
        self._session_id = "jarvis-session"
        self._initialized = False
        self._pending_req_id: int | str | None = None

    # ---- public API ----

    def run(self) -> None:
        """阻塞式主循环：读 stdin，逐条处理 JSON-RPC。"""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._dispatch(msg)

    # ---- dispatch ----

    def _dispatch(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        req_id = msg.get("id")

        if method == "initialize":
            self._handle_initialize(req_id, msg.get("params", {}))
        elif method == "session/new":
            self._handle_session_new(req_id)
        elif method == "session/prompt":
            self._handle_prompt(req_id, msg.get("params", {}))
        elif method == "session/cancel":
            self._send_result(req_id, {})
        else:
            self._send_error(req_id, -32601, f"未知方法: {method}")

    # ---- handlers ----

    def _handle_initialize(self, req_id: Any, params: dict) -> None:
        self._send_result(req_id, {
            "protocolVersion": 1,  # cc-connect 要求整数类型
            "agentInfo": {"name": "jarvis", "version": "0.1"},
            "capabilities": {},
        })
        self._initialized = True

    def _handle_session_new(self, req_id: Any) -> None:
        if not self._initialized:
            self._send_error(req_id, -32000, "未初始化")
            return
        self._send_result(req_id, {"sessionId": self._session_id})

    def _handle_prompt(self, req_id: Any, params: dict) -> None:
        if not self._initialized:
            self._send_error(req_id, -32000, "未初始化")
            return

        prompt = params.get("prompt", [])
        text = ""
        for p in prompt:
            if isinstance(p, dict) and p.get("type") == "text":
                text += p.get("text", "")

        if not text:
            self._send_result(req_id, {"stopReason": "end_turn"})
            return

        self._pending_req_id = req_id
        # 回调交给外部 agent 循环
        self._on_prompt(text, self._notify_update, self._finish_prompt)

    # ---- callbacks for agent ----

    def _on_prompt(self, text: str, on_update, on_finish):
        """子类覆盖此方法实现 agent 逻辑。"""
        on_update("thought", f"收到消息: {text}")
        on_finish("end_turn")

    # ---- send helpers ----

    def _notify_update(self, update_type: str, text: str) -> None:
        """发送 session/update 通知（思考/回复文本）。"""
        content_type = "thought" if update_type == "thought" else "text"
        self._write({
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": self._session_id,
                "update": {
                    "type": "agent_message_chunk",
                    "content": {"type": content_type, "text": text},
                },
            },
        })

    def _finish_prompt(self, stop_reason: str = "end_turn") -> None:
        """完成本轮 prompt，发送 result。"""
        if self._pending_req_id is not None:
            self._send_result(self._pending_req_id, {"stopReason": stop_reason})
            self._pending_req_id = None

    def _send_result(self, req_id: Any, result: Any) -> None:
        if req_id is None:
            return  # 通知消息无 id，不回复
        self._write({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _send_error(self, req_id: Any, code: int, message: str) -> None:
        if req_id is None:
            return
        self._write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})

    def _write(self, msg: dict) -> None:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


class JarvisACP(ACPServer):
    """贾维斯 ACP 适配器：把 stdin JSON-RPC 转成 QueryLoop 调用。"""

    def __init__(self, settings, provider, loop, ctx, messages):
        super().__init__()
        self._settings = settings
        self._provider = provider
        self._loop = loop
        self._ctx = ctx
        self._messages = messages
        self._session_id = "jarvis-" + str(id(self))[:8]

    def _on_prompt(self, text: str, on_update, on_finish):
        """执行 agent 循环，思考/文本通过 on_update 推给 cc-connect。"""
        try:
            # 装配 UI 回调：输出走 ACP 通知
            class _ACPUI:
                verbose = False

                def info(self, *a, **kw):
                    pass

                def warn(self, *a, **kw):
                    pass

                def error(self, *a, **kw):
                    pass

                def assistant_text(self, t):
                    on_update("text", t)

                def assistant_thinking(self, t):
                    on_update("thought", t)

            self._ctx.ui = _ACPUI()
            self._ctx.on_assistant_text = None

            import asyncio
            asyncio.get_event_loop().run_until_complete(
                self._loop.run(text, self._ctx)
            )
            on_finish("end_turn")
        except Exception as e:
            on_update("thought", f"[错误] {e}")
            on_finish("error")
