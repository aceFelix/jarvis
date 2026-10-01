"""跨设备协同的桌面宿主适配 —— 手机 PWA / 微信 ClawBot 接入 serve 引擎。

把终端 /connect-phone、/connect-wechat 的能力搬到桌面：桌面壳点下拉按钮 →
WorkbenchAPI 入队 connect_phone / connect_wechat 指令 → 本模块在**引擎自己的
asyncio loop** 上装配桥接，与桌面文本对话共享同一 query_loop / ctx / messages。

并发口径（与终端一致）：三通道（桌面文本 / 手机 / 微信）都调度到引擎 loop 执行
query_loop.run，抢引擎持有的**唯一共享 query 锁**（engine._query_lock）串行化，
保证「两端都能发、绝不同时发」，共享 messages 不被并发写坏。

二维码回传：手机 URL / 微信登录二维码统一经 engine._emitter 推 ``qrcode`` 事件
（payload {channel, url}），桌面在中间聊天区内联渲染；微信数字配对码经引擎的
``wechat_pairing`` 指令回喂到 login 线程阻塞的队列。

设计约束：
- connect_phone / connect_wechat 是 ``async def``，由引擎 _dispatch 在引擎 loop
  上 await；手机桥接启动（含 HTTP 就绪等待最多 3s）丢 run_in_executor，不冻结
  指令循环。
- 微信 login 会**阻塞等待配对码**，绝不能占引擎 loop，故放独立线程 + 独立 loop
  跑，配对码用 threading 队列与引擎解耦。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.ui.workbench.engine import ChatEngine


class _StatusUI:
    """微信桥接状态输出适配器：把 info/warn/error 转成桌面事件。

    WeChatBridge.login 会调用 ui.info/warn/error 播报扫码进度（终端是 RichCLI）。
    桌面宿主下把这些降级为引擎 info/warn/error 事件，前端聊天区以系统提示呈现。

    @author aceFelix
    """

    def __init__(self, engine: "ChatEngine") -> None:
        self._engine = engine

    def info(self, msg: str) -> None:
        self._engine._emitter.emit("info", f"微信：{msg}")

    def warn(self, msg: str) -> None:
        self._engine._emitter.emit("warn", f"微信：{msg}")

    def error(self, msg: str) -> None:
        self._engine._emitter.emit("error", f"微信：{msg}")


def _bridge_ports(settings: Any) -> tuple[int, int, str]:
    """从配置读取桥接 HTTP/WS 端口与 token（与终端 /connect-phone 同源）。"""
    http_port = int(getattr(settings, "bridge_http_port", 8765) or 8765)
    ws_port = int(getattr(settings, "bridge_ws_port", 8766) or 8766)
    token = str(getattr(settings, "bridge_token", "") or "")
    return http_port, ws_port, token


# ---- 手机 PWA ----


async def connect_phone(engine: "ChatEngine") -> None:
    """启动（或重启）绑 0.0.0.0 的手机桥接，共享引擎会话，回推二维码 URL 事件。

    在引擎 loop 上执行：先 ensure_session 拿到 query_loop/ctx，再以引擎唯一 query
    锁 + 引擎 loop 起手机 BridgeServer。桥接启动含 HTTP 就绪等待（≤3s）丢线程池，
    避免阻塞指令循环。与终端一致：每次连接先 stop 旧的，生成新 token / URL。

    @author aceFelix
    """
    from agent.bridge import start_bridge_in_thread, stop_bridge

    await engine._ensure_session()
    loop = asyncio.get_running_loop()
    http_port, ws_port, token = _bridge_ports(engine._settings)
    try:
        # 重连即重置：先停旧桥接（换新 token / URL），再在引擎 loop 上起新的
        await loop.run_in_executor(None, stop_bridge)

        def _start() -> Any:
            return start_bridge_in_thread(
                query_loop=engine._query_loop,
                ctx=engine._ctx,
                http_port=http_port,
                ws_port=ws_port,
                token=token,
                workdir=engine._settings.workdir,
                main_loop=engine._loop,        # query 调度回引擎 loop
                query_lock=engine._query_lock,  # 与桌面文本共享唯一锁
                on_turn_end=engine._after_turn,  # 每轮手机对话结束落盘到电脑会话历史
                on_query_begin=engine._remote_query_begin,  # 登记任务使桌面可停止手机轮次
            )

        server = await loop.run_in_executor(None, _start)
        # 连接态以「手机 WS 客户端真正接入」为准（而非桥接启动）：挂接入/离开回调推
        # remote_state，二维码先出、connected 待手机扫上后再置真，避免未扫即显「已连接」。
        server.on_client_connected = lambda: engine._emitter.emit(
            "remote_state", {"channel": "phone", "connected": True}
        )
        server.on_client_disconnected = lambda: engine._emitter.emit(
            "remote_state", {"channel": "phone", "connected": False}
        )
        # fresh=True：本次为一次新连接，前端在聊天区底部新建二维码卡片（不复用
        # 历史旧卡片），避免重连后还要往上翻找二维码。@author aceFelix
        engine._emitter.emit(
            "qrcode", {"channel": "phone", "url": server.url, "fresh": True}
        )
        # 复用已运行的桥接单例时可能已有手机在线，补推一次连接态（否则错过回调）
        if server._clients:
            engine._emitter.emit("remote_state", {"channel": "phone", "connected": True})
        engine._emitter.emit("info", "🌐 手机协同已启动，手机扫码或访问上方地址")
    except Exception as e:
        engine._emitter.emit("error", f"手机连接启动失败: {type(e).__name__}: {e}")


async def disconnect_phone(engine: "ChatEngine") -> None:
    """停止手机桥接并推断开状态。

    @author aceFelix
    """
    from agent.bridge import stop_bridge

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, stop_bridge)
        engine._emitter.emit("remote_state", {"channel": "phone", "connected": False})
        engine._emitter.emit("info", "手机协同已断开")
    except Exception as e:
        engine._emitter.emit("error", f"手机断开失败: {type(e).__name__}: {e}")


# ---- 微信 ClawBot ----


async def connect_wechat(engine: "ChatEngine") -> None:
    """启动微信扫码登录：装配桥接后把 login 丢独立线程，二维码走事件回推。

    login 流程会阻塞等待用户在手机端看到的数字配对码（verify_callback），因此
    绝不能占用引擎指令循环 —— 放独立线程 + 独立 asyncio loop 跑，配对码经
    engine._wechat_pairing_q（threading 队列）与引擎 wechat_pairing 指令解耦。
    与终端一致：先 stop_wechat 再全新登录。

    @author aceFelix
    """
    from agent.wechat import start_wechat_in_thread, stop_wechat

    await engine._ensure_session()
    # 每次登录一个新的配对码回喂队列
    pairing_q: queue.Queue[str] = queue.Queue()
    engine._wechat_pairing_q = pairing_q
    try:
        stop_wechat()
        bridge = start_wechat_in_thread(
            query_loop=engine._query_loop,
            ctx=engine._ctx,
            ui=_StatusUI(engine),
            workdir=engine._settings.workdir,
            main_loop=engine._loop,        # 微信 query 调度回引擎 loop
            query_lock=engine._query_lock,  # 与桌面文本 / 手机共享唯一锁
            on_turn_end=engine._after_turn,  # 每轮微信对话结束落盘到电脑会话历史
            on_query_begin=engine._remote_query_begin,  # 登记任务使桌面可停止微信轮次
        )
    except Exception as e:
        engine._emitter.emit("error", f"微信桥接启动失败: {type(e).__name__}: {e}")
        return

    # login 放独立线程：内部自带 asyncio loop，阻塞点（等配对码 / 等扫码确认）
    # 全在该线程，不影响引擎指令循环。
    threading.Thread(
        target=_wechat_login_worker,
        args=(bridge, engine, pairing_q),
        name="wechat-login",
        daemon=True,
    ).start()
    engine._emitter.emit("info", "微信连接中，请扫码…")


def _wechat_login_worker(bridge: Any, engine: "ChatEngine", pairing_q: queue.Queue[str]) -> None:
    """独立线程中的微信登录协程驱动：出二维码 → 等配对码 → 成功后起消息循环。

    qrcode_callback 把二维码 URL 经 emitter 推给桌面（线程安全）；verify_callback
    阻塞在 pairing_q.get() 等桌面回填数字码（带超时兜底，避免永久挂起）。登录
    成功后调 start_wechat_loop 启动长轮询消息线程，并推 remote_state。

    @author aceFelix
    """

    # 首帧二维码标 fresh=True（新连接，前端底部新建卡片）；登录内因二维码过期
    # 重生成的后续帧 fresh=False（就地刷新同一张，不堆叠）。@author aceFelix
    first_qr = {"v": True}

    def _qrcode_cb(url: str) -> None:
        fresh = first_qr["v"]
        first_qr["v"] = False
        engine._emitter.emit(
            "qrcode", {"channel": "wechat", "url": url, "fresh": fresh}
        )

    def _verify_cb(retry: bool) -> str:
        try:
            # 等待桌面 wechat.pairing 回喂；60s 无输入返回空串触发上层重试/刷新
            return pairing_q.get(timeout=60)
        except queue.Empty:
            return ""

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        ok = loop.run_until_complete(
            bridge.login(verify_callback=_verify_cb, qrcode_callback=_qrcode_cb)
        )
        # 只有真正拿到 bot_token（用户扫码确认）才算「已连接」并启动消息轮询。
        # login() 的 already_connected 分支不写 bot_token，此时 bridge.connected 为假
        # ——若照常报 connected，会出现「还没扫码就显示已连接、却收不到消息」的假
        # 连接（connected 属性即 bool(bot_token)）。故以 bridge.connected 为准。
        # @author aceFelix
        if ok and bridge.connected:
            from agent.wechat import start_wechat_loop

            start_wechat_loop()
            engine._emitter.emit(
                "remote_state", {"channel": "wechat", "connected": True}
            )
            engine._emitter.emit("info", "✅ 微信已连接，在微信给 ClawBot 发消息即可对话")
        elif ok:
            # already_connected 但无有效凭证：不谎报已连接，保留二维码让用户重新扫码
            engine._emitter.emit(
                "warn", "微信未真正连接（无有效凭证），请重新扫码"
            )
        else:
            engine._emitter.emit("warn", "微信登录未完成，请重新点击连接")
    except Exception as e:
        engine._emitter.emit("error", f"微信登录异常: {type(e).__name__}: {e}")
    finally:
        try:
            loop.close()
        except Exception:
            pass


async def disconnect_wechat(engine: "ChatEngine") -> None:
    """停止微信桥接并推断开状态。

    @author aceFelix
    """
    from agent.wechat import stop_wechat

    engine._wechat_pairing_q = None
    try:
        stop_wechat()
        engine._emitter.emit("remote_state", {"channel": "wechat", "connected": False})
        engine._emitter.emit("info", "微信已断开")
    except Exception as e:
        engine._emitter.emit("error", f"微信断开失败: {type(e).__name__}: {e}")
