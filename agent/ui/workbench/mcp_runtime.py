"""工作台引擎的 MCP 接入与启动预热（自 engine.py 拆出，控引擎文件行数）。

引擎装配分两段：启动空窗后台预热（build_default_registry + 宿主钩子 + MCP
连接）与首条消息时的 ``_ensure_session``（复用预热结果或降级同步装配）。
本模块承载预热的三个步骤，函数以引擎实例为第一参数操作其内部字段
（同包内部模块，等价于原先的类方法体，方法名与行为保持不变）。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅用于类型标注：运行时不导入，避免 engine ↔ mcp_runtime 循环
    from agent.ui.workbench.engine import ChatEngine


async def prewarm(engine: ChatEngine) -> None:
    """启动后台预装配：registry（+宿主钩子）→ MCP 连接。

    MCP 多 server 并发连接实测约 9s，把它从首条消息的 _ensure_session
    前移到启动空窗后台：用户开始打字时 MCP 通常已就绪，首条消息秒进
    LLM；预热窗口内就发消息也只是先少 MCP 工具聊（工具连上自动补挂，
    与 harness 后台加载同口径）。任何异常置位 _registry_ready 后由
    _ensure_session 降级同步装配，对话链路始终可用。@author aceFelix
    """
    try:
        from agent.core.tool import build_default_registry

        registry = build_default_registry()
        if engine._registry_hook is not None:
            try:
                engine._registry_hook(registry)
            except Exception as e:
                engine._emitter.emit("info", f"⚠ 工具注册钩子失败: {type(e).__name__}: {e}")
        engine._registry = registry
        if engine._settings.enable_mcp:
            await connect_mcp(engine, registry)
    except Exception as e:
        engine._emitter.emit(
            "info", f"⚠ 启动预热失败（首条消息同步装配兜底）: {type(e).__name__}: {e}"
        )
    finally:
        engine._registry_ready.set()


def register_harness(engine: ChatEngine, registry: Any, workdir: str) -> None:
    """后台注册 CLI-Anything harness 工具（失败不影响主流程）。"""
    try:
        from agent.core.tool import register_dynamic_tools

        count = register_dynamic_tools(registry, workdir=workdir)
        if count > 0:
            engine._emitter.emit("info", f"✓ harness 工具已加载（{count} 个）")
    except Exception:
        pass


async def connect_mcp(engine: ChatEngine, registry: Any) -> None:
    """连接配置的 MCP server 并把其工具注册进 registry（对齐 main.repl 的 MCP 接入）。

    - connect_all 内部并发连接且每 server 有超时，不会把引擎装配拖成无限等待；
    - client 引用保留在 engine._mcp_client，防 GC 回收导致子进程连接断开；
    - 任何异常静默降级（仅 info 提示），不阻断文本对话装配。

    @author aceFelix
    """
    try:
        from agent.core.extensions.mcp_client import MCPClient, load_mcp_config
        from agent.core.tool import register_dynamic_tools

        client = MCPClient()
        if not client.available:
            return
        config = load_mcp_config()
        if not config:
            return
        engine._emitter.emit("status", f"正在连接 {len(config)} 个 MCP server...")
        results = await client.connect_all(config)
        connected = sum(1 for v in results.values() if v)
        if not connected:
            engine._mcp_status = {
                "connected": [],
                "failed": list(config.keys()),
                "tools": 0,
            }
            # 全部失败也是「已落定」：推就绪事件，让右栏把 init 时的 None（显示
            # 为 MCP 未启用）刷新成真实失败态。桌面壳启动早于本后台预热完成。
            engine._emitter.emit("mcp_ready", engine._mcp_status)
            engine._emitter.emit(
                "info", f"⚠ MCP: 所有 server 连接失败（{', '.join(config.keys())}）"
            )
            return
        engine._mcp_client = client
        count = register_dynamic_tools(registry, client)
        failed = [name for name, ok in results.items() if not ok]
        # 连接结果快照：供 state.get 右栏运行健康展示（成功/失败名单 + 工具数）
        engine._mcp_status = {
            "connected": [name for name, ok in results.items() if ok],
            "failed": failed,
            "tools": count,
        }
        msg = f"MCP: {connected}/{len(config)} server 已连接，注册 {count} 个工具"
        if failed:
            msg += f"（{', '.join(failed)} 连接失败，对应工具不可用）"
        engine._emitter.emit("info", msg)
        # MCP 连接结果落定：推就绪事件，桌面壳据此刷新右栏运行健康（init 拉到的
        # 快照可能仍是 None——MCP 多 server 并发连接约 9s 才完成）。@author aceFelix
        engine._emitter.emit("mcp_ready", engine._mcp_status)
    except ImportError:
        pass  # MCP SDK 未安装，跳过接入
    except Exception as e:
        engine._emitter.emit("info", f"⚠ MCP 接入异常: {type(e).__name__}: {e}")
