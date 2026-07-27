"""Bridge Server —— 跨设备协同服务器。

在电脑端启动两个服务：
1. HTTP 服务（http.server.ThreadingHTTPServer，独立线程，默认 8765 端口）：
   提供 PWA 静态文件 + /api/config（返回 WS 端口）+ /upload（图片上传，MVP 留接口）。
2. WebSocket 服务（websockets 库，asyncio loop，默认 8766 端口）：
   与手机端双向通信，流式推送 UI 事件。

会话桥接核心：
- 手机端连接 WS 后，创建新的 ToolContext，但 messages 共享 daemon 的（共享对话历史）。
- permission_mode="plan"：手机端只读，不允许执行写操作，保证安全。
- asyncio.Lock 确保同一时间只有一个 query 在跑（避免并发污染共享 messages）。
- Token 认证：WS 连接时通过 URL 参数 ?token=xxx 校验，token 为空时自动生成。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse, parse_qs

from agent.bridge.ui import BridgeUI
from agent.core.context import ToolContext

# websockets 是可选依赖（与 voice/realtime_talk.py 一致），缺失时在 start() 报错
try:
    import websockets
except ImportError:  # pragma: no cover
    websockets = None

# 全局单例状态：一个 REPL 会话只能有一个 BridgeServer
_global_bridge: "BridgeServer | None" = None
_global_bridge_thread: threading.Thread | None = None
_global_bridge_loop: asyncio.AbstractEventLoop | None = None


def _get_local_ip() -> str:
    """获取本机局域网 IP，失败时回退 127.0.0.1。"""
    import socket

    try:
        # 优先用 UDP 连接外网地址的方式获取本地出口 IP（不真发数据包）
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("223.5.5.5", 53))
            ip = s.getsockname()[0]
            if ip and ip != "127.0.0.1":
                return ip
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and ip != "127.0.0.1":
            return ip
    except Exception:
        pass
    return "127.0.0.1"


def get_bridge_server() -> "BridgeServer | None":
    """获取当前 REPL 会话中的 BridgeServer 单例（未启动则返回 None）。"""
    global _global_bridge
    return _global_bridge


class BridgeServer:
    """跨设备协同服务器，让手机通过 PWA 与电脑端 JARVIS 对话。

    共享 REPL 的 QueryLoop 和 ToolContext.messages，实现"手机和电脑同屏对话"。
    生命周期：start() 启动两个服务 → 手机连接 → 跑 query → stop() 关闭。

    REPL 会话单例管理：通过全局 _global_bridge 保证一个会话只有一个实例。

    @author aceFelix
    """

    def __init__(
        self,
        query_loop: Any,
        ctx: ToolContext,
        *,
        http_port: int = 8765,
        ws_port: int = 8766,
        token: str = "",
        workdir: str = "",
    ) -> None:
        """
        Args:
            query_loop: QueryLoop 实例（共享 REPL 的）。
            ctx: ToolContext 实例（共享 REPL 的 messages）。
            http_port: HTTP 静态文件服务端口，默认 8765。
            ws_port: WebSocket 通信端口，默认 8766。
            token: 认证 token，空则自动生成 16 位 hex。
            workdir: 工作目录，空则沿用 ctx.workdir。
        """
        self._query_loop = query_loop
        self._ctx = ctx
        self._http_port = http_port
        self._ws_port = ws_port
        # token 为空时自动生成，避免裸奔
        self._token = token or secrets.token_hex(8)
        self._workdir = workdir or ctx.workdir

        # 并发锁：确保同一时间只有一个 query（避免共享 messages 被并发写入破坏）
        self._lock = asyncio.Lock()
        # 当前运行中查询的 abort 事件（reader 检测到 abort/断连时直接 set，无需走主循环）
        self._current_abort: asyncio.Event | None = None

        # 静态文件目录：agent/bridge/static/
        self._static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

        # 运行时句柄
        self._http_server: ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._ws_server: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # 主线程 asyncio loop（REPL loop），所有 query 在此执行，避免跨线程 UI 问题
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # 跨线程串行化锁：保护共享 messages 不被并发修改
        self._query_lock = threading.Lock()
        # 当前已连接的 WebSocket 客户端集合
        self._clients: set[Any] = set()

    # ---- 对外属性 ----

    @property
    def token(self) -> str:
        """当前认证 token（用于展示给用户扫码 / 输入）。"""
        return self._token

    @property
    def http_port(self) -> int:
        """HTTP 服务端口。"""
        return self._http_port

    @property
    def ws_port(self) -> int:
        """WebSocket 服务端口。"""
        return self._ws_port

    @property
    def url(self) -> str:
        """完整的手机访问 URL（含 token）。"""
        return f"http://{_get_local_ip()}:{self._http_port}/?token={self._token}"

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动 HTTP + WS 服务。

        HTTP 在独立线程跑（同步 ThreadingHTTPServer），WS 在当前 asyncio loop 跑。
        服务绑定 0.0.0.0 以便局域网内手机可连（token 认证兜底）。
        """
        if websockets is None:
            raise RuntimeError("缺少 websockets 库，请运行: pip install websockets")

        # 保存当前 loop，供主线程 broadcast 使用
        self._loop = asyncio.get_running_loop()

        # 1. HTTP 服务（独立线程）
        self._start_http()

        # 2. WebSocket 服务（asyncio loop 内）
        # 绑定 0.0.0.0：手机通过局域网访问；token 认证防未授权访问
        self._ws_server = await websockets.serve(
            self._handle_ws, "0.0.0.0", self._ws_port
        )

    async def stop(self) -> None:
        """停止所有服务，释放端口。"""
        # 关闭 WS（兼容 loop 已关闭场景）
        if self._ws_server is not None:
            try:
                self._ws_server.close()
                await self._ws_server.wait_closed()
            except RuntimeError:
                pass
            self._ws_server = None
        # 关闭 HTTP
        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server.server_close()
            self._http_server = None
        if self._http_thread is not None and self._http_thread.is_alive():
            self._http_thread.join(timeout=2)
            self._http_thread = None

    def broadcast(self, event: str, data: Any) -> None:
        """线程安全地把事件广播给所有已连接的 WebSocket 客户端。

        由电脑终端（主线程）调用，把电脑端发送的消息 / 助手回复同步到手机端。
        内部把事件投递到 BridgeServer 子线程的 asyncio loop 中执行。

        Args:
            event: 事件名，如 "user_message" / "assistant_text" / "done"。
            data: 事件数据，任意可 JSON 序列化的对象。

        @author aceFelix
        """
        if not self._clients or self._loop is None:
            return
        payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)

        async def _send() -> None:
            # 复制当前客户端集合，避免迭代时修改
            clients = list(self._clients)
            for ws in clients:
                try:
                    await ws.send(payload)
                except Exception:
                    # 发送失败（如连接已断开）时移出集合
                    self._clients.discard(ws)

        try:
            asyncio.run_coroutine_threadsafe(_send(), self._loop)
        except Exception:
            pass

# ---- HTTP 服务 ----

    def _start_http(self) -> None:
        """在独立线程启动 ThreadingHTTPServer，提供静态文件 + /api/config + /upload。"""
        static_dir = self._static_dir
        ws_port = self._ws_port
        token = self._token
        upload_dir = os.path.join(static_dir, "uploads")
        os.makedirs(upload_dir, exist_ok=True)

        class _Handler(BaseHTTPRequestHandler):
            """HTTP 请求处理器：静态文件 + /api/config + /upload。

            闭包捕获 static_dir / ws_port / token / upload_dir，避免全局状态。
            """

            # 关闭默认的请求日志，避免刷屏
            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
                return

            def _send_json(self, obj: dict, status: int = 200) -> None:
                """以 JSON 响应。"""
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _send_file(self, path: str, content_type: str) -> None:
                """发送静态文件内容。"""
                try:
                    with open(path, "rb") as f:
                        body = f.read()
                except FileNotFoundError:
                    self.send_error(404, "Not Found")
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
                """处理 GET：/ 返回 index.html，/api/config 返回配置，其它走静态文件。"""
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/" or path == "/index.html":
                    self._send_file(
                        os.path.join(static_dir, "index.html"),
                        "text/html; charset=utf-8",
                    )
                    return
                if path == "/api/config":
                    # 返回 WS 端口；token 由 URL 参数传递（不在此返回，避免无 token 访问泄露）
                    self._send_json({"ws_port": ws_port, "token_required": bool(token)})
                    return
                # 其它静态资源（防止目录穿越逃出 static_dir）
                safe = os.path.normpath(path).lstrip("/\\")
                full = os.path.join(static_dir, safe)
                if (
                    os.path.isfile(full)
                    and os.path.commonpath([static_dir, full]) == os.path.normpath(static_dir)
                ):
                    self._send_file(full, "application/octet-stream")
                    return
                self.send_error(404, "Not Found")

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
                """处理 POST：/upload 接收图片（MVP 留接口，简单存盘）。"""
                parsed = urlparse(self.path)
                if parsed.path != "/upload":
                    self.send_error(404, "Not Found")
                    return

                content_type = self.headers.get("Content-Type", "")
                content_length = int(self.headers.get("Content-Length", 0))
                # 10MB 上限，防恶意大文件
                if content_length <= 0 or content_length > 10 * 1024 * 1024:
                    self._send_json({"ok": False, "error": "文件过大或为空"}, 400)
                    return
                data = self.rfile.read(content_length)
                # 简单存盘到 uploads/，文件名用时间戳
                import time as _t

                ext = ".png"
                if "jpeg" in content_type or "jpg" in content_type:
                    ext = ".jpg"
                elif "webp" in content_type:
                    ext = ".webp"
                fname = f"upload_{int(_t.time())}{ext}"
                try:
                    with open(os.path.join(upload_dir, fname), "wb") as f:
                        f.write(data)
                except Exception as e:
                    self._send_json({"ok": False, "error": f"保存失败: {e}"}, 500)
                    return
                self._send_json({"ok": True, "name": fname, "path": f"/{fname}"})

        self._http_server = ThreadingHTTPServer(("0.0.0.0", self._http_port), _Handler)
        self._http_thread = threading.Thread(
            target=self._http_server.serve_forever, name="bridge-http", daemon=True
        )
        self._http_thread.start()

    # ---- WebSocket 服务 ----

    async def _handle_ws(self, ws, path: str | None = None) -> None:
        """处理单个 WebSocket 连接：认证 → 接收消息 → 跑查询 → 推送结果。

        Args:
            ws: WebSocket 连接对象。
            path: 连接路径（兼容旧版 websockets 传 2 参；新版从 ws.request.path 取）。
        """
        # 1. 认证：从 URL 参数 ?token=xxx 取
        if path is None:
            # 兼容 websockets 不同版本：>=11 用 ws.request.path，<11 用 ws.path
            try:
                path = ws.request.path  # type: ignore[attr-defined]
            except AttributeError:
                path = getattr(ws, "path", "")
        params = parse_qs(urlparse(path).query)
        client_token = params.get("token", [""])[0] if params.get("token") else ""
        if not self._token or client_token != self._token:
            try:
                await ws.close(code=4401, reason="unauthorized")
            except Exception:
                pass
            return

        # 注册客户端：用于主线程 broadcast 推送电脑端消息
        self._clients.add(ws)
        try:
            # 2. 接收循环与查询并发：
            #    reader 持续读 ws（abort 直接 set 事件，其余入队），
            #    主循环串行处理 message（加锁跑 query），互不阻塞。
            incoming: "asyncio.Queue[dict]" = asyncio.Queue()

            async def _reader() -> None:
                """持续读取 WS 消息：abort 直接中断当前查询，其余入队等主循环处理。"""
                try:
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                        except Exception:
                            await incoming.put({"type": "_error", "text": "无效的 JSON 消息"})
                            continue
                        t = data.get("type")
                        if t == "abort":
                            # 中断当前正在运行的查询（同一 loop，无需加锁）
                            if self._current_abort is not None:
                                self._current_abort.set()
                            continue
                        await incoming.put(data)
                except Exception:
                    pass
                finally:
                    # 连接断开：中断正在运行的查询并通知主循环退出
                    if self._current_abort is not None:
                        self._current_abort.set()
                    await incoming.put({"type": "_closed"})

            reader_task = asyncio.create_task(_reader())
            while True:
                data = await incoming.get()
                t = data.get("type")

                if t == "_closed":
                    # 手机端断开，退出本连接处理
                    break
                if t == "_error":
                    # reader 解析失败：回一个 error 事件
                    try:
                        await ws.send(
                            json.dumps(
                                {"event": "error", "data": data.get("text", "消息解析失败")},
                                ensure_ascii=False,
                            )
                        )
                    except Exception:
                        pass
                    continue
                if t == "message":
                    text = (data.get("text") or "").strip()
                    if not text:
                        continue
                    await self._run_query(ws, text)
                # 其它 type 静默忽略（MVP 只处理 message / abort）
        finally:
            self._clients.discard(ws)
            reader_task.cancel()
            try:
                await reader_task
            except Exception:
                pass

    async def run_query(
        self, text: str, ctx: ToolContext, images: list[Any] | None = None
    ) -> Any:
        """在主线程 loop 中加锁执行 query_loop.run。

        所有 query（手机端和电脑端）都在主线程 loop 执行，避免跨线程 UI 问题。
        用 threading.Lock 串行化，保护共享 messages 不被并发修改。

        Args:
            text: 用户输入文本。
            ctx: ToolContext（共享 messages）。
            images: 可选图片列表。

        Returns:
            query_loop.run 的返回值（QueryStats）。

        @author aceFelix
        """
        # 如果当前不在主线程 loop，则调度到主线程 loop 执行
        if self._main_loop is not None and asyncio.get_running_loop() is not self._main_loop:
            future = asyncio.run_coroutine_threadsafe(
                self.run_query(text, ctx, images),
                self._main_loop,
            )
            return await asyncio.wrap_future(future)

        # 在主线程 loop 中执行：获取 threading.Lock 串行化
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._query_lock.acquire)
        try:
            return await self._query_loop.run(text, ctx, images=images)
        finally:
            self._query_lock.release()

    async def _run_query(self, ws, text: str) -> None:
        """跑一轮手机端对话查询，把结果流式推送到 ws。

        创建独立 ToolContext（共享 messages，plan 权限），最终通过 run_query 加锁执行。
        流程：建 ctx + BridgeUI → 启动 stream 任务 → run_query → 收尾（finish + done）。
        """
        abort_event = asyncio.Event()
        self._current_abort = abort_event
        # 同时把事件转发给电脑终端 UI，实现手机和电脑同屏
        ui = BridgeUI(desktop_ui=self._ctx.ui)

        # 1. 先在两端显示用户消息
        ui.user_message(text)

        # 手机端独立上下文：
        # - messages 共享引用 → 与电脑端同步对话历史
        # - permission_mode="plan" → 只读，禁止写操作（安全）
        # - abort_event 独立 → 手机端可单独中断，不影响电脑端
        mobile_ctx = ToolContext(
            workdir=self._workdir or self._ctx.workdir,
            messages=self._ctx.messages,  # 共享引用
            abort_event=abort_event,
            permission_mode="plan",
            ui=ui,
            settings=self._ctx.settings,
        )

        # 启动流式推送任务（与 query_loop.run 并发）
        stream_task = asyncio.create_task(ui.stream(ws))
        try:
            await self.run_query(text, mobile_ctx)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            ui.error(f"查询失败: {e}")
        finally:
            # 1. 投递结束哨兵，让 stream 任务把剩余事件发完再退出
            ui.finish()
            self._current_abort = None
            # 2. 等待所有 UI 事件推送完毕
            try:
                await stream_task
            except Exception:
                pass
            # 3. 显式推送 done 事件，通知手机端本轮结束
            try:
                await ws.send(
                    json.dumps({"event": "done", "data": None}, ensure_ascii=False)
                )
            except Exception:
                pass
    # ---- 模块级单例 API ----


def start_bridge_in_thread(
    query_loop: Any,
    ctx: ToolContext,
    *,
    http_port: int = 8765,
    ws_port: int = 8766,
    token: str = "",
    workdir: str = "",
    main_loop: asyncio.AbstractEventLoop | None = None,
) -> BridgeServer:
    """在独立线程启动 BridgeServer，返回单例。

    如果已有实例在运行，则复用并返回现有实例（不重复启动）。

    Args:
        main_loop: 主线程 asyncio loop（REPL loop），所有 query 在此执行。
                   为 None 时尝试获取当前运行中的 loop。

    @author aceFelix
    """
    global _global_bridge, _global_bridge_thread, _global_bridge_loop

    if _global_bridge is not None and _global_bridge_thread is not None and _global_bridge_thread.is_alive():
        # 已有实例：更新 main_loop 引用（可能跨 REPL 重启）
        if main_loop is not None:
            _global_bridge._main_loop = main_loop
        return _global_bridge

    # 获取主线程 loop：所有 query 在此执行，避免跨线程 UI 问题
    if main_loop is None:
        try:
            main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    server = BridgeServer(
        query_loop=query_loop,
        ctx=ctx,
        http_port=http_port,
        ws_port=ws_port,
        token=token,
        workdir=workdir,
    )
    server._main_loop = main_loop
    _global_bridge = server

    def _run() -> None:
        global _global_bridge_loop
        _global_bridge_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_global_bridge_loop)
        try:
            _global_bridge_loop.run_until_complete(server.start())
            _global_bridge_loop.run_forever()
        except Exception:
            pass

    _global_bridge_thread = threading.Thread(target=_run, name="bridge-server", daemon=True)
    _global_bridge_thread.start()

    # 等待 HTTP 服务就绪（最多 3 秒）
    import time

    deadline = time.time() + 3
    while time.time() < deadline:
        if server._http_server is not None:
            break
        time.sleep(0.05)

    return server


def stop_bridge() -> None:
    """停止当前 REPL 会话的 BridgeServer 单例。"""
    global _global_bridge, _global_bridge_thread, _global_bridge_loop

    if _global_bridge_loop is not None and _global_bridge is not None:
        try:
            asyncio.run_coroutine_threadsafe(_global_bridge.stop(), _global_bridge_loop).result(timeout=3)
        except Exception:
            pass
        try:
            _global_bridge_loop.call_soon_threadsafe(_global_bridge_loop.stop)
        except Exception:
            pass
    if _global_bridge_thread is not None and _global_bridge_thread.is_alive():
        _global_bridge_thread.join(timeout=3)
    _global_bridge = None
    _global_bridge_thread = None
    _global_bridge_loop = None
