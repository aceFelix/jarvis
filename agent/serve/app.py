"""jarvis serve 主入口 —— headless API 服务模式。

供 jarvis-desktop（Electron 桌面壳）等外部前端接入：不渲染任何本地 UI，
装配与 pywebview 工作台完全相同的引擎零件（ChatEngine + WorkbenchAPI +
MetricsCollector），经 DesktopBridgeServer 以 WS 事件流对外服务。

进程契约（Electron 主进程侧）：
- 就绪信号：stdout 打印单行握手 JSON（见 protocol.build_handshake）；
- 退出信号：stdin EOF（父进程退出/杀管道时触发）或 SIGINT，优雅停机；
- 绑定收敛：仅 127.0.0.1 + 随机端口，不对局域网暴露。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
from typing import Any, Callable

from agent.config.settings import Settings
from agent.serve import protocol
from agent.serve.server import DesktopBridgeServer
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine
from agent.ui.workbench.metrics import MetricsCollector


def _watch_stdin(on_eof: Callable[[], None]) -> None:
    """监视 stdin 直到 EOF，随后回调（父进程退出时管道关闭触发优雅停机）。

    在独立守护线程跑：阻塞读不影响主 asyncio loop；
    终端手动运行时 Ctrl+C（KeyboardInterrupt）仍走主线程中断路径。

    @author aceFelix
    """
    try:
        for _ in sys.stdin:
            pass
    except Exception:
        pass
    try:
        on_eof()
    except Exception:
        pass


async def _serve_main(settings: Settings) -> None:
    """serve 主协程：装配 → 启动 → 握手 → 等待停机信号 → 收尾。

    @author aceFelix
    """
    # 装配：与 workbench app.run_workbench 同一套零件，仅宿主从 pywebview 换成 WS
    event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    metrics = MetricsCollector(event_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    server = DesktopBridgeServer(api, settings)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    try:
        await server.start()
        engine.start()
        metrics.start()
        server.start_event_pump(event_queue)
        # 首推 init 事件（payload 与工作台 get_state 同构，前端首屏渲染）
        event_queue.put_nowait({"type": protocol.EVT_INIT, "payload": api.get_state()})
        # 就绪握手：单行 JSON 打到 stdout，Electron 逐行解析识别
        handshake = protocol.build_handshake(
            server.ws_port, server.http_port, server.token, os.getpid()
        )
        print(json.dumps(handshake, ensure_ascii=False), flush=True)

        # stdin EOF 监视线程：父进程（Electron）退出时联动停机
        watcher = threading.Thread(
            target=_watch_stdin,
            args=(lambda: loop.call_soon_threadsafe(stop_event.set),),
            name="serve-stdin-watch",
            daemon=True,
        )
        watcher.start()
        await stop_event.wait()
    finally:
        # 优雅停机：先关传输层（含事件泵），再停采集与引擎
        try:
            await server.stop()
        except Exception:
            pass
        try:
            metrics.stop()
        except Exception:
            pass
        try:
            engine.stop()
        except Exception:
            pass


def _prewarm_native_libs() -> None:
    """启动早期（单线程阶段）预加载 numpy/cv2 等重型原生库。

    背景：首条消息触发 ``build_default_registry`` → ``import cv2`` 时，会在
    引擎线程里 LoadLibrary 加载 libscipy_openblas64_*.dll；该 DLL 的 DllMain
    在多线程进程里并发加载时可能卡在加载器临界区（OpenBLAS 初始化死锁），
    表现为 serve 子进程永久停在"正在初始化对话引擎..."。在尚未创建任何
    工作线程的启动早期单线程预加载，可使后续 import 命中 sys.modules、
    不再触发 LoadLibrary，从根上避开该死锁。缺失依赖时静默跳过（相机工具
    本就可选）。

    @author aceFelix
    """
    for name in ("numpy", "cv2"):
        try:
            __import__(name)
        except Exception:
            pass


def run_serve(settings: Settings) -> int:
    """启动 headless API 服务（阻塞直到停机信号）。

    Returns:
        退出码：0 正常停机；3 缺少 websockets 依赖。

    @author aceFelix
    """
    # websockets 是可选依赖（与手机协同桥接同一口径），缺失时明确报错退出
    from agent.bridge import server as _bridge_server

    if _bridge_server.websockets is None:
        print(
            "缺少 websockets 库，无法启动 serve 模式（pip install websockets）",
            file=sys.stderr,
        )
        return 3
    # 单线程阶段预加载原生库，规避引擎线程懒加载 cv2 的 OpenBLAS 加载器死锁
    _prewarm_native_libs()
    try:
        asyncio.run(_serve_main(settings))
    except KeyboardInterrupt:
        pass
    return 0
