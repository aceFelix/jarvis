"""WeChat Bridge —— 微信 ClawBot 与 JARVIS 的桥接服务。

通过腾讯 iLink Bot API 接入微信 ClawBot：
1. /connect-wechat → 扫码登录 → 拿到 bot_token
2. 后台长轮询 getupdates 接收微信消息
3. 消息交给 QueryLoop 处理（共享 REPL 的 messages）
4. 回复通过 sendmessage 发回微信

架构与 agent/bridge/server.py 一致：独立线程 + 单例管理。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable

from agent.core.context import ToolContext
from agent.wechat.ilink import ILinkClient
from agent.wechat.ui import WeChatUI

# 微信单条消息最大长度（超过则分段发送）
MAX_MSG_LENGTH = 2000

# iLink token 有效期 24h，提前 2h 提醒重连
_SESSION_DURATION = 24 * 3600
_WARNING_BEFORE = 2 * 3600

# 全局单例
_global_wechat: "WeChatBridge | None" = None
_global_wechat_thread: threading.Thread | None = None
_global_wechat_loop: asyncio.AbstractEventLoop | None = None


def get_wechat_bridge() -> "WeChatBridge | None":
    """获取当前 WeChatBridge 单例（未启动则返回 None）。"""
    return _global_wechat


class WeChatBridge:
    """微信 ClawBot 桥接服务。

    在独立线程中运行 asyncio loop，长轮询接收微信消息，
    通过 run_coroutine_threadsafe 调度到主线程 loop 执行 QueryLoop。

    @author aceFelix
    """

    def __init__(
        self,
        query_loop: Any,
        ctx: ToolContext,
        ui: Any,
        *,
        workdir: str = "",
    ) -> None:
        """
        Args:
            query_loop: QueryLoop 实例（共享 REPL 的）。
            ctx: ToolContext 实例（共享 REPL 的 messages）。
            ui: 终端 UI（RichCLI），用于显示状态和二维码。
            workdir: 工作目录。
        """
        self._query_loop = query_loop
        self._ctx = ctx
        self._ui = ui
        self._workdir = workdir or ctx.workdir

        self._client = ILinkClient()
        self._running = False
        self._login_time: float = 0
        # 跨线程串行化锁：保护共享 messages
        self._query_lock = threading.Lock()
        # 主线程 asyncio loop（REPL loop），query 在此执行
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # typing_ticket 缓存（per user）
        self._typing_tickets: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return self._running

    @property
    def connected(self) -> bool:
        """是否已登录（有 bot_token）。"""
        return bool(self._client.bot_token)

    # ---- 登录流程 ----

    async def login(
        self,
        verify_callback: Callable[[bool], str] | None = None,
        qrcode_callback: Callable[[str], None] | None = None,
    ) -> bool:
        """执行扫码登录流程。

        Args:
            verify_callback: 需要配对码时调用（参数: 是否重试），返回用户输入。
            qrcode_callback: 二维码 URL 就绪时调用（用于终端渲染）。

        Returns:
            True 登录成功，False 失败。
        """
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                data = await self._client.fetch_login_qrcode()
            except Exception as e:
                self._ui.error(f"获取二维码失败: {e}")
                return False

            qrcode = data.get("qrcode", "")
            qrcode_url = data.get("qrcode_img_content", "") or qrcode
            if not qrcode:
                self._ui.error("服务端未返回二维码")
                return False

            # 通知上层渲染二维码
            if qrcode_callback:
                qrcode_callback(qrcode_url)

            def _status_cb(status: str) -> None:
                if status == "scanned":
                    self._ui.info("已扫码，等待手机端确认...")
                elif status == "expired":
                    self._ui.warn("二维码已过期")

            result = await self._client.wait_login_confirmation(
                qrcode,
                timeout_seconds=600,
                verify_callback=verify_callback,
                status_callback=_status_cb,
            )

            if result.get("bot_token"):
                self._client.bot_token = result["bot_token"]
                if result.get("baseurl"):
                    self._client.base_url = result["baseurl"]
                self._login_time = time.time()
                return True
            if result.get("already_connected"):
                self._ui.info("服务端提示已连接过，沿用当前连接")
                self._login_time = time.time()
                return True
            if result.get("expired"):
                self._ui.warn(f"二维码过期，正在重新生成 ({attempt + 1}/{max_attempts})...")
                continue
            if result.get("timeout"):
                self._ui.warn("扫码超时")
                return False
            if result.get("verify_code_blocked"):
                self._ui.warn("配对码多次错误，正在刷新二维码...")
                continue

        self._ui.error("多次登录失败，请稍后重试")
        return False

    # ---- 主消息循环 ----

    async def run_loop(self) -> None:
        """主消息循环：长轮询 getupdates → 处理消息。在独立线程的 loop 中运行。"""
        self._running = True
        buf = ""

        # 重置 aiohttp session：login() 在主线程 loop 中创建了 session，
        # 而 run_loop() 在独立线程的新 loop 中运行，必须重建 session。
        self._client.reset_session()

        self._ui.info("微信消息监听已启动，发消息给 ClawBot 即可与贾维斯对话")

        # 启动重连监控
        reconnect_task = asyncio.create_task(self._reconnect_monitor())

        try:
            while self._running:
                try:
                    result = await self._client.get_updates(buf)
                except Exception as e:
                    if not self._running:
                        break
                    # 网络错误等，短暂等待后重试
                    self._ui.warn(f"微信轮询异常: {e}，5s 后重试")
                    await asyncio.sleep(5)
                    continue

                buf = result.get("get_updates_buf") or buf

                for msg in result.get("msgs") or []:
                    if msg.get("message_type") != 1:
                        continue
                    item_list = msg.get("item_list") or [{}]
                    text = item_list[0].get("text_item", {}).get("text", "") if item_list else ""
                    from_id = msg.get("from_user_id", "")
                    context_token = msg.get("context_token", "")

                    if not text or not from_id:
                        continue

                    await self._handle_message(from_id, context_token, text)
        except asyncio.CancelledError:
            pass
        finally:
            reconnect_task.cancel()
            try:
                await reconnect_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _handle_message(self, from_id: str, context_token: str, text: str) -> None:
        """处理单条微信消息：调用 QueryLoop 并回复。"""
        # 1. 显示"正在输入"
        typing_ticket = self._typing_tickets.get(from_id, "")
        if not typing_ticket:
            try:
                cfg = await self._client.get_config(from_id, context_token)
                typing_ticket = cfg.get("typing_ticket", "")
                self._typing_tickets[from_id] = typing_ticket
            except Exception:
                pass
        await self._client.send_typing(from_id, typing_ticket, 1)

        # 2. 调用 QueryLoop 处理
        reply = await self._run_query(text)

        # 3. 发送回复（分段）
        if not reply:
            reply = "（贾维斯未产生回复）"
        await self._send_reply(from_id, context_token, reply)

        # 4. 取消"正在输入"
        await self._client.send_typing(from_id, typing_ticket, 2)

    async def _run_query(self, text: str) -> str:
        """在主线程 loop 中加锁执行 QueryLoop，返回回复文本。"""
        ui = WeChatUI(desktop_ui=self._ctx.ui)
        ui.user_message(text)

        # 构建微信端 ToolContext（共享 messages，default 权限）
        wechat_ctx = ToolContext(
            workdir=self._workdir,
            messages=self._ctx.messages,  # 共享引用
            abort_event=asyncio.Event(),
            permission_mode="default",
            ui=ui,
            settings=self._ctx.settings,
        )

        try:
            if self._main_loop is not None and asyncio.get_running_loop() is not self._main_loop:
                # 调度到主线程 loop 执行
                future = asyncio.run_coroutine_threadsafe(
                    self._exec_query(text, wechat_ctx),
                    self._main_loop,
                )
                # 在子线程 loop 中等待结果
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, future.result, 300)  # 5min 超时
            else:
                await self._exec_query(text, wechat_ctx)
        except Exception as e:
            ui.error(f"查询失败: {e}")

        return ui.get_reply()

    async def _exec_query(self, text: str, ctx: ToolContext) -> None:
        """加锁执行 query_loop.run。"""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._query_lock.acquire)
        try:
            await self._query_loop.run(text, ctx)
        finally:
            self._query_lock.release()

    async def _send_reply(self, to_id: str, context_token: str, text: str) -> None:
        """发送回复，超过 MAX_MSG_LENGTH 则分段。"""
        chunks = _split_message(text, MAX_MSG_LENGTH)
        for chunk in chunks:
            try:
                await self._client.send_message(to_id, context_token, chunk)
            except Exception as e:
                self._ui.warn(f"微信消息发送失败: {e}")
            # 分段间短暂间隔，避免频率限制
            if len(chunks) > 1:
                await asyncio.sleep(0.5)

    # ---- 重连监控 ----

    async def _reconnect_monitor(self) -> None:
        """监控 token 有效期，到期前提醒重新扫码。"""
        while self._running:
            elapsed = time.time() - self._login_time
            remaining = _SESSION_DURATION - elapsed
            if remaining <= _WARNING_BEFORE:
                hours = remaining / 3600
                self._ui.warn(
                    f"微信连接将在 {hours:.1f}h 后到期，"
                    f"届时需重新执行 /connect-wechat 扫码续期"
                )
                # 只提醒一次，然后等到结束
                break
            # 每 30 分钟检查一次
            await asyncio.sleep(min(1800, remaining - _WARNING_BEFORE))

    # ---- 生命周期 ----

    async def stop(self) -> None:
        """停止消息循环并关闭 HTTP session。"""
        self._running = False
        await self._client.close()


# ---- 工具函数 ----


def _split_message(text: str, max_len: int) -> list[str]:
    """按 max_len 分段消息，优先在换行处断开。"""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # 尝试在换行处断开
        cut = text.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


# ---- 模块级单例 API ----


def start_wechat_in_thread(
    query_loop: Any,
    ctx: ToolContext,
    ui: Any,
    *,
    workdir: str = "",
    main_loop: asyncio.AbstractEventLoop | None = None,
) -> WeChatBridge:
    """在独立线程启动 WeChatBridge，返回单例。

    如果已有实例在运行，则复用并返回现有实例。

    Args:
        main_loop: 主线程 asyncio loop（REPL loop），query 在此执行。
    """
    global _global_wechat, _global_wechat_thread, _global_wechat_loop

    if (
        _global_wechat is not None
        and _global_wechat_thread is not None
        and _global_wechat_thread.is_alive()
    ):
        if main_loop is not None:
            _global_wechat._main_loop = main_loop
        return _global_wechat

    if main_loop is None:
        try:
            main_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    bridge = WeChatBridge(query_loop=query_loop, ctx=ctx, ui=ui, workdir=workdir)
    bridge._main_loop = main_loop
    _global_wechat = bridge

    def _run() -> None:
        global _global_wechat_loop
        _global_wechat_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_global_wechat_loop)
        try:
            _global_wechat_loop.run_until_complete(bridge.run_loop())
        except Exception:
            pass

    _global_wechat_thread = threading.Thread(target=_run, name="wechat-bridge", daemon=True)
    # 注意：线程先不启动，等 login 成功后再启动消息循环
    return bridge


def start_wechat_loop() -> None:
    """登录成功后启动消息循环线程。"""
    global _global_wechat_thread
    if _global_wechat_thread is not None and not _global_wechat_thread.is_alive():
        _global_wechat_thread.start()


def stop_wechat() -> None:
    """停止当前 WeChatBridge 单例。"""
    global _global_wechat, _global_wechat_thread, _global_wechat_loop

    if _global_wechat_loop is not None and _global_wechat is not None:
        try:
            asyncio.run_coroutine_threadsafe(
                _global_wechat.stop(), _global_wechat_loop
            ).result(timeout=5)
        except Exception:
            pass
        try:
            _global_wechat_loop.call_soon_threadsafe(_global_wechat_loop.stop)
        except Exception:
            pass
    if _global_wechat_thread is not None and _global_wechat_thread.is_alive():
        _global_wechat_thread.join(timeout=5)
    _global_wechat = None
    _global_wechat_thread = None
    _global_wechat_loop = None
