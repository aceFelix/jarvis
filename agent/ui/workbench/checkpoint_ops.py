"""工作台引擎的消息级回溯（自 engine.py 拆出，控引擎文件行数）。

serve 侧 ``checkpoint.preview`` / ``checkpoint.rewind``（桌面气泡「撤回」）
在引擎线程调用本模块：把对话截断回"某条用户消息发出之前"，并可选地把
工作区文件回滚到该消息对应的 shadow git 检查点（core/checkpoint.py）。

定位口径：「用户消息」只数含文本/图片块的 user 消息（纯工具回填虽也
role=="user" 但不成桌面气泡，见 _is_visible_user_message），桌面气泡与
后端这一口径共享先后顺序，故用「从尾部数第 N 条用户消息」定位撤回起点
（user_tail_count），不依赖消息 id —— 引擎 emit user_message 时后端消息
尚未由 query_loop 创建，且上下文压缩会重写旧消息使绝对索引漂移，而尾部
计数不受前缀压缩影响。

远程通道（手机/微信）的轮次不打检查点（其消息撤回时自动退化为仅对话回退）。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型标注：运行时不导入，避免 engine ↔ checkpoint_ops 循环
    from agent.ui.workbench.engine import ChatEngine


def make_manager(engine: ChatEngine):
    """按引擎当前 settings 构建检查点管理器（无状态构造，随取随用）。

    workdir 运行时切换（project.set）后再次调用即指向新目录，无需缓存；
    检查点存储本身按 workdir 定位，跨目录的 checkpoint_id 天然查不到。
    @author aceFelix
    """
    from agent.core.checkpoint import CheckpointManager

    s = engine._settings
    return CheckpointManager(
        s.workdir,
        getattr(engine, "_session_name", "") or "workbench",
        enabled=getattr(s, "checkpoint_enabled", True),
        max_checkpoints=getattr(s, "checkpoint_max_per_session", 20),
        timeout_seconds=getattr(s, "checkpoint_timeout_seconds", 10),
    )


def _is_visible_user_message(m: Any) -> bool:
    """是否算作一条"用户消息"（与桌面气泡/render 口径对齐）。

    后端 messages 里工具回填也是 role=="user"（纯 ToolResultContent），
    但不成桌面气泡、不进历史渲染（见 render._messages_to_render），
    故只数含非空文本或图片块的 user 消息——桌面按气泡数传
    user_tail_count，两边必须同口径否则错位。图片-only 轮引擎已补
    最小文本（_handle_send），文本判定已覆盖；仍兼容 ImageContent 防口径漂移。
    @author aceFelix
    """
    from agent.core.message import ImageContent, TextContent

    if getattr(m, "role", "") != "user":
        return False
    return any(
        (isinstance(b, TextContent) and b.text.strip()) or isinstance(b, ImageContent)
        for b in getattr(m, "content", []) or []
    )


def find_rewind_start(messages: list[Any], user_tail_count: int) -> int:
    """定位「从倒数第 user_tail_count 条用户消息起」的截断下标。

    Returns:
        起始消息下标（该条及其后的全部消息将被移除）；用户消息不足
        user_tail_count 条时返回 -1。
    @author aceFelix
    """
    seen = 0
    for i in range(len(messages) - 1, -1, -1):
        if _is_visible_user_message(messages[i]):
            seen += 1
            if seen >= user_tail_count:
                return i
    return -1


def find_target_checkpoint(messages: list[Any], start: int, mgr) -> str:
    """在撤回范围 [start:] 内找最早一条用户消息的有效检查点 id。

    最早 = 回滚幅度最大（覆盖范围内所有轮次的文件修改）；无有效检查点
    （未打点/已被配额修剪/跨目录）返回空串，调用方退化为仅对话回退。
    @author aceFelix
    """
    for m in messages[start:]:
        cid = (getattr(m, "extra", None) or {}).get("checkpoint_id", "")
        if cid and _is_visible_user_message(m) and mgr.has_checkpoint(cid):
            return cid
    return ""


def preview(engine: ChatEngine, user_tail_count: int) -> dict[str, Any]:
    """checkpoint.preview：预览撤回会连带回滚哪些文件（只读，不入队）。

    直接读引擎消息列表快照（GIL 下单次 list() 拷贝安全；正有轮次在跑时
    尾部消息可能未定型，预览以调用时刻为准，rewind 落地时重新计算）。

    Returns:
        {ok, user_tail_count, has_checkpoint, files, untracked, reason}
    @author aceFelix
    """
    messages = list(getattr(engine, "_messages", None) or [])
    if user_tail_count < 1:
        return {"ok": False, "reason": "user_tail_count 必须 ≥ 1", "has_checkpoint": False}
    start = find_rewind_start(messages, user_tail_count)
    if start < 0:
        return {"ok": False, "reason": "用户消息数不足", "has_checkpoint": False}
    mgr = make_manager(engine)
    if not mgr.available():
        return {"ok": True, "has_checkpoint": False, "files": [], "untracked": [],
                "reason": "文件检查点未启用或缺少 git"}
    cid = find_target_checkpoint(messages, start, mgr)
    if not cid:
        return {"ok": True, "has_checkpoint": False, "files": [], "untracked": [],
                "reason": "该范围没有可用检查点（仅能回退对话）"}
    desc = mgr.describe(cid) or {"files": [], "untracked": []}
    return {"ok": True, "has_checkpoint": True, "checkpoint_id": cid,
            "files": desc.get("files", []), "untracked": desc.get("untracked", []),
            "reason": ""}


async def rewind(engine: ChatEngine, user_tail_count: int, restore_files: bool) -> dict[str, Any]:
    """checkpoint.rewind：截断对话并可选回滚工作区文件（引擎队列内执行）。

    在指令队列协程里被 await：与在跑轮次天然串行（队列被 send 占用时
    排队等待），无需另拿 query 锁；截断 _messages 后立即 _auto_save，
    防会话文件残留已撤回内容。结果同时以返回值（RPC 回执）与 rewound
    事件（前端裁气泡）双通道给出。

    Returns:
        {ok, removed, files_restored, reason}
    @author aceFelix
    """
    messages = engine._messages
    if user_tail_count < 1:
        return {"ok": False, "removed": 0, "files_restored": False,
                "reason": "user_tail_count 必须 ≥ 1"}
    start = find_rewind_start(messages, user_tail_count)
    if start < 0:
        return {"ok": False, "removed": 0, "files_restored": False,
                "reason": "用户消息数不足"}

    files_restored = False
    reason = ""
    if restore_files:
        mgr = make_manager(engine)
        if mgr.available():
            cid = find_target_checkpoint(messages, start, mgr)
            if cid:
                ok, reason = mgr.restore(cid)
                files_restored = ok
                if not ok:
                    # 文件回滚失败 → 放弃本次撤回（消息也不截断，前端保持原样）
                    return {"ok": False, "removed": 0, "files_restored": False,
                            "reason": reason}
            else:
                reason = "无可用检查点，仅回退对话"
        else:
            reason = "检查点未启用或缺少 git，仅回退对话"

    removed = len(messages) - start
    del messages[start:]
    # 截断后立即落盘，防下次 /load 或崩溃恢复带回已撤回消息
    try:
        from agent.session_manager import _auto_save

        _auto_save(
            engine._ui, messages,
            workdir=engine._settings.workdir,
            model=engine._model,
            provider=engine._settings.provider,
            session_name=engine._session_name,
            verbose=False,
            dialog_count=max(0, engine._dialog_count - user_tail_count),
            title_generated=engine._title_generated,
            settings=engine._settings,
        )
        engine._dialog_count = max(0, engine._dialog_count - user_tail_count)
    except Exception:
        pass
    return {"ok": True, "removed": removed, "removed_user": user_tail_count,
            "files_restored": files_restored, "reason": reason}
