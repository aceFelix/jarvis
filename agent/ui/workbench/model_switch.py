"""工作台引擎的模型热切换（自 engine.py 拆出，控引擎文件行数）。

serve 侧 ``models.select``（桌面壳左栏点选模型 → WS 指令）在写盘之后入队
``{"cmd": "switch_model"}``，由引擎线程调用本模块：按目标模型重建 provider、
就地替换 QueryLoop 的 provider / 模型，使「切模型」立即生效，不再要求重启
引擎（切换前 `models.list` 的 current 永远停在进程启动时的模型）。

与 REPL 的 ``/model`` 切换共用 ``model_manager._build_switched_provider``，
保证两端「自定义 / 内置模型的端点重建」口径一致。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注：运行时不导入，避免 engine ↔ model_switch 循环
    from agent.ui.workbench.engine import ChatEngine


async def handle_switch_model(engine: ChatEngine, name: str, force: bool = False) -> None:
    """热切换运行中的对话模型（serve models.select → 引擎线程内串行执行）。

    复刻 REPL /model 的切换链路：provider 按与「当前 provider 端点」的
    比较结果重建（model_manager._build_switched_provider），QueryLoop 就地
    换 provider / 模型（不重建，保留会话 token 累计与思考模式覆盖），
    旧 provider 的 HTTP client 关闭。切换在指令队列里排队：若正有一轮回复
    在跑，队列被它占用，切换在回复结束后才落地 —— 不存在「半轮换模型」
    的竞态，也不会打断流式输出。

    会话尚未装配时只记账（engine._model_override），等 _ensure_session
    装配完成再落地，避免为一次切换提前触发重型装配（MCP 连接 + 提示词生成）。

    Args:
        engine: 工作台对话引擎实例。
        name: 目标模型名（models.list 里的名字）。
        force: 目标与当前同名时仍按现配置重建 provider —— models.edit 改了
            运行中模型的端点/模型类型后用（否则「同名即当前」短理会把改动
            挡在门外），提示文案也随之改为「配置已更新」。

    @author aceFelix
    """
    name = (name or "").strip()
    if not name:
        return
    loop = getattr(engine, "_query_loop", None)
    provider = getattr(engine, "_provider", None)
    if loop is None or provider is None:
        engine._model_override = name
        engine._emitter.emit("model_switched", {"model": name})
        if force:
            engine._emitter.emit("info", f"模型配置已更新：{name}")
        else:
            engine._emitter.emit("info", f"模型已切换为 {name}")
        return
    if name == str(getattr(engine, "_model", "")) and not force:
        # 已是当前模型（重复点选/竞态）：不重建 provider，只回执让前端收敛
        engine._emitter.emit("model_switched", {"model": name})
        return
    from agent.model_manager import _build_switched_provider

    try:
        new_provider, desc, used_settings = _build_switched_provider(
            engine._provider_settings, provider, name
        )
    except Exception as e:
        # 不推 model_switched：前端「待生效」标记保留，用户可重试或改选
        engine._emitter.emit("warn", f"模型切换失败: {type(e).__name__}: {e}")
        return
    new_provider._model = name
    old_provider = loop.switch_model(new_provider, name)
    if old_provider is not None:
        try:
            await old_provider.close()
        except Exception:
            pass
    engine._provider = new_provider
    engine._provider_settings = used_settings
    engine._model = name
    engine._emitter.emit("model_switched", {"model": name})
    if force:
        engine._ui.info(f"模型配置已更新：{name}（{desc}）" if desc else f"模型配置已更新：{name}")
    else:
        engine._ui.info(f"模型已切换为 {name}（{desc}）" if desc else f"模型已切换为 {name}")
