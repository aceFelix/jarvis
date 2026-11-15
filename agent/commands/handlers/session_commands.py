"""会话相关命令处理器。

包含 /save, /load, /loads, /sessions, /memory 等命令。

@author aceFelix
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from agent.session_manager import (
    _load_by_name,
    _load_by_picker,
    _list_sessions,
    _save_session,
)
from agent.ui.terminal_picker import pick_from_list

if TYPE_CHECKING:
    from agent.commands.router import CommandContext


async def handle_save(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /save [name]。"""
    _save_session(ctx.ui, ctx.settings, stripped, ctx.messages,
                  dialog_count=ctx.dialog_count, title_generated=ctx.title_generated)
    return True


async def handle_load(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /load <prefix>：前缀匹配加载会话。

    加载后把恢复信息（轮数/标题状态）存到 ctx.last_load_info，
    由 REPL 主循环应用到 _dialog_count/_title_generated/_session_name。
    """
    parts = stripped.split(None, 1)
    if len(parts) > 1 and parts[1].strip():
        want = parts[1].strip().lower()
        from agent.core.memory.store import list_sessions

        sessions = list_sessions()
        exact = next((s for s in sessions if s.name.lower() == want), None)
        if exact:
            info = _load_by_name(ctx.ui, ctx.settings, exact.name, ctx.messages)
            if info:
                ctx.last_load_info = info
        else:
            matches = [s for s in sessions if s.name.lower().startswith(want)]
            if not matches:
                ctx.ui.warn(f"无匹配会话: {want}（用 /sessions 查看保存列表）")
            elif len(matches) == 1:
                info = _load_by_name(ctx.ui, ctx.settings, matches[0].name, ctx.messages)
                if info:
                    ctx.last_load_info = info
            else:
                match_items = [
                    (s.name, s.name, f"{s.message_count} 条消息 | {s.workdir or '(无)'}")
                    for s in matches
                ]
                picked = pick_from_list(match_items, title=f"「{want}」匹配 {len(matches)} 个会话")
                if picked:
                    info = _load_by_name(ctx.ui, ctx.settings, picked, ctx.messages)
                    if info:
                        ctx.last_load_info = info
    else:
        ctx.ui.warn("用法: /load <会话名前缀>（用 /sessions 查看并选择会话）")
    return True


async def handle_loads(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /loads /sessions /ls-sessions：列出并选择已保存会话。"""
    _load_by_picker(ctx.ui, ctx.messages)
    return True


async def handle_sessions(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /sessions：列出所有已保存会话。"""
    _list_sessions(ctx.ui)
    return True


def _show_memory_file(ui, settings) -> None:
    """/memory file — 查看长期记忆文件（MEMORY.md）。"""
    from agent.core.memory.store import get_memory_files, load_long_term_memory

    files = get_memory_files(settings.workdir)
    ui.info("长期记忆文件:")
    for label, path in files.items():
        exists = "✓" if path and path.exists() else "✗"
        ui.info(f"  [{label}] {exists} {path}")

    mem = load_long_term_memory(settings.workdir)
    if mem:
        ui.info("当前加载的记忆内容:")
        if ui._console:
            ui._console.print(mem)
        else:
            print(mem)
    else:
        ui.info("（暂无长期记忆。可手动创建上述文件写入需要记住的信息。）")


def _format_last_refine(state) -> str:
    """把台账里的上轮提炼计数拼成一行人话（P1d 遥测；无记录返回空串）。

    只做展示：让人能看出"提炼确实跑了、卡在哪一步"（例如 rejected 不为 0
    说明候选被硬闸拦下，noop 多说明重复事实被正确跳过）。
    @author aceFelix
    """
    report = getattr(state, "last_report", None) or {}
    ts = float(getattr(state, "last_refine_ts", 0.0) or 0.0)
    if not ts:
        return ""
    when = time.strftime("%m-%d %H:%M", time.localtime(ts))
    if not report:
        return f"上次提炼 {when}：暂无新事实入库"
    labels = (
        ("extracted", "抽取"),
        ("added", "新增"),
        ("updated", "更新"),
        ("deleted", "删除"),
        ("merged", "合并"),
        ("noop", "跳过"),
        ("rejected", "拒绝"),
    )
    parts = [f"{name} {int(report.get(key, 0))}" for key, name in labels if report.get(key)]
    return f"上次提炼 {when}：{' / '.join(parts)}"


def _show_profile(ui, settings, session_name: str = "") -> None:
    """/memory — 画像记忆列表（Phase 1a）+ 上轮提炼遥测（P1d）+ 权重展示。"""
    from agent.core.memory import profile_ledger
    from agent.core.memory.profile_store import (
        CATEGORY_LABELS,
        DORMANT_WEIGHT_FLOOR,
        ProfileStore,
    )

    store = ProfileStore()
    weighted = store.weighted_entries()
    entries = [e for _, e in weighted]
    # 遥测先展示：库为空时也能看出"最近一次提炼是否跑过、拒了多少"
    telemetry = _format_last_refine(profile_ledger.load_state(session_name))
    if not entries:
        ui.info(
            "画像记忆为空。和贾维斯聊聊你的习惯与偏好（如\"我习惯熬夜写代码\"），"
            "会话结束后会自动提炼入库；也可用 /memory add <内容> 手动添加。"
        )
        if telemetry:
            ui.info(f"  {telemetry}")
        return

    dormant = sum(1 for w, _ in weighted if w < DORMANT_WEIGHT_FLOOR)
    limit = getattr(settings, "profile_inject_token_limit", 300)
    mode = getattr(settings, "profile_inject_token_budget_mode", "pseudo") or "pseudo"
    extra = f"，其中休眠 {dormant} 条" if dormant else ""
    ui.info(
        f"画像记忆（{len(entries)} 条{extra}，按权重排序；"
        f"注入限额 {limit} token，口径 {mode}）:"
    )
    if telemetry:
        ui.info(f"  {telemetry}")
    for weight, e in weighted:
        label = CATEGORY_LABELS.get(e.category, e.category)
        src = e.source_session or "-"
        # 提及次数 > 0 时才展示：这是“被反复确认”的事实，比单纯置信度更能说明分量
        seen = f" | 提及 {e.ref_count} 次" if e.ref_count else ""
        # 权重 = 置信度 × 新鲜度；低于阈值标“休眠”（不注入但仍在库里）
        state = " | 休眠" if weight < DORMANT_WEIGHT_FLOOR else ""
        ui.info(
            f"  [{e.id}] {label} | {e.content}\n"
            f"      置信度 {e.confidence:.2f} | 权重 {weight:.2f}{state} | 来源 {src}{seen}"
        )
    # P2b：被新事实取代的旧条目不再展示，但告诉用户它们还在（历史可追）
    history_n = len(store.entries(include_invalidated=True)) - len(entries)
    if history_n:
        ui.info(f"  （另有 {history_n} 条已被新事实取代，保留为历史）")


def _rebuild_system_prompt(ctx) -> None:
    """画像增删后重建 system prompt，让变更在当前会话立即生效。"""
    try:
        from agent.prompts.system import compose_system_prompt, reload_profile_cache

        reload_profile_cache()
        # 口径（workdir / enable_thinking / append 段 / 视觉段）全部从 ctx.settings 现读，
        # 走统一入口避免漏拼用户自定义段。
        ctx.loop._system = compose_system_prompt(ctx.settings, ctx.registry)
    except Exception:
        pass  # 重建失败不影响命令本身


async def _sync_profile_to_kg(ctx, confirm: bool) -> None:
    """/memory sync：把本地画像同步到 aceFelix 知识图谱（P2 画像双向同步反向链路）。

    流程：临时连接图谱 MCP server → 本地画像汇总成交给 ingest_text 抽取管线：
    - 无 yes：dry_run=True 预览（将新建哪些实体/关系、跳过哪些重复）
    - 有 yes：dry_run=False 正式写入（管线写入前自动备份，可回滚）
    管线查重保证幂等：已在图谱中的条目自动跳过，重复执行不会产生重复实体。
    """
    if not getattr(ctx.settings, "profile_bridge_enabled", False):
        ctx.ui.warn(
            "知识图谱画像桥未开启。在 settings.toml 中添加：\n"
            "  [profile_bridge]\n  enabled = true\n"
            "（前提：~/.jarvis/mcp.json 已配置 acefelix-knowledge server）"
        )
        return

    from agent.core.extensions.mcp_client import MCPClient, load_mcp_config
    from agent.core.extensions.profile_bridge import sync_to_kg

    server = getattr(ctx.settings, "profile_bridge_server", "acefelix-knowledge")
    config = load_mcp_config()
    if server not in config:
        ctx.ui.warn(f"~/.jarvis/mcp.json 未配置 MCP server [{server}]，无法同步")
        return

    # 临时连接目标 server（命令低频显式触发，不复用启动时长驻连接，避免线程/循环归属问题）
    client = MCPClient()
    if not client.available:
        ctx.ui.warn("mcp SDK 未安装（pip install mcp）")
        return
    conn = await client.connect(server, config[server])
    if conn is None:
        ctx.ui.warn(f"连接图谱 MCP server [{server}] 失败，请确认后端依赖已安装")
        return

    try:
        outcome = await sync_to_kg(client, ctx.settings, dry_run=not confirm)
    finally:
        await client.disconnect_all()

    if not outcome["ok"]:
        ctx.ui.warn(f"同步失败: {outcome['error']}")
        return

    result = outcome["result"]
    # 门禁拦截（密度/价值预判）：直接展示原因，无写入也无预览
    if "rejected" in str(result.get("gate", "")):
        ctx.ui.info(f"图谱抽取管线拒绝本次同步: {result['gate']}")
        return

    created_e = result.get("created_entities", [])
    created_r = result.get("created_relations", [])
    dup_e = result.get("skipped_duplicate_entities", [])
    dup_r = result.get("skipped_duplicate_relations", [])
    pending = result.get("pending_review", [])
    skipped_r = result.get("skipped_relations", [])

    def _fmt_entities(items):
        return "、".join(f"{e['name']}[{e['type']}]" for e in items if isinstance(e, dict))

    def _fmt_relations(items):
        return "、".join(
            f"{r.get('source')} → {r.get('type')} → {r.get('target')}"
            for r in items if isinstance(r, dict)
        )

    if not confirm:
        # 预览模式：展示将写入的内容，等用户 /memory sync yes 确认
        if not created_e and not created_r:
            ctx.ui.info(
                f"预览：无新增内容（本地画像已在图谱中，"
                f"跳过重复实体 {len(dup_e)} 个、重复关系 {len(dup_r)} 条）"
            )
            return
        ctx.ui.info(
            "同步预览（尚未写入）：\n"
            f"  新增实体 {len(created_e)} 个: {_fmt_entities(created_e)}\n"
            f"  新增关系 {len(created_r)} 条: {_fmt_relations(created_r)}\n"
            f"  跳过重复实体 {len(dup_e)} 个、重复关系 {len(dup_r)} 条"
        )
        if pending:
            ctx.ui.info(f"  待人工确认 {len(pending)} 个（类型不在白名单，不会写入）")
        if skipped_r:
            ctx.ui.info(f"  跳过无效关系 {len(skipped_r)} 条（类型不在白名单/端点不存在）")
        ctx.ui.info("确认写入请输入: /memory sync yes")
        return

    # 确认写入模式：展示实际写入结果（图谱为唯一事实源，写入前管线已自动备份）
    ctx.ui.info(
        f"已同步到知识图谱：新增实体 {len(created_e)} 个、关系 {len(created_r)} 条"
        f"（跳过重复实体 {len(dup_e)} 个、重复关系 {len(dup_r)} 条）"
    )
    if created_e:
        ctx.ui.info(f"  实体: {_fmt_entities(created_e)}")
    if created_r:
        ctx.ui.info(f"  关系: {_fmt_relations(created_r)}")
    if pending:
        ctx.ui.info(f"  另有 {len(pending)} 个待人工确认（类型不在白名单），可在图谱 Web 端处理")
    ctx.ui.info("图谱已自动备份，如需回滚可在 Web 端操作；新画像下次启动时自动注入 system prompt")


async def handle_memory(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /memory 系列命令。

    /memory              查看画像记忆（Phase 1a 自动提炼的用户偏好/习惯）
    /memory add <文本>    手动添加画像条目（深重复则并入已有条目）
    /memory del <id>     删除指定条目
    /memory clear yes    清空全部画像（需 yes 二次确认）
    /memory refine       立即提炼新增量（不等节流；只喂水位之后的消息）
    /memory dedup [yes]  画像去重整理（预览可归并的深重复条数，yes 执行）
    /memory sync         同步本地画像到知识图谱（先预览，/memory sync yes 确认写入）
    /memory events [n]   查看事件轨最近 n 条（临时事项，默认 5、最多 50；不注入对话）
    /memory file         查看长期记忆文件（MEMORY.md，旧功能保留）
    """
    from agent.core.memory.profile_store import ProfileStore

    parts = stripped.split(None, 1)
    sub = parts[1].strip() if len(parts) > 1 else ""

    if not sub:
        _show_profile(ctx.ui, ctx.settings, session_name=getattr(ctx, "session_name", ""))
        return True

    action, _, rest = sub.partition(" ")
    action = action.lower()
    rest = rest.strip()

    if action == "file":
        _show_memory_file(ctx.ui, ctx.settings)
        return True

    if action == "events":
        # P2c：事件轨只做查看；默认不注入 system prompt（临时事项不该长期占预算）
        from agent.core.memory import profile_events

        limit = 5
        if rest.isdigit():
            limit = max(1, min(50, int(rest)))
        events = profile_events.recent_events(limit=limit)
        if not events:
            ctx.ui.info(
                "事件轨为空。带明确时间边界的临时事项（如“下周三面试”）会在提炼时留痕，"
                "默认不注入对话。"
            )
            return True
        ctx.ui.info(f"最近 {len(events)} 条事件（未过期，默认不注入）:")
        for ev in events:
            when = time.strftime("%m-%d %H:%M", time.localtime(ev.ts))
            expire = (
                f" | 到期 {time.strftime('%m-%d %H:%M', time.localtime(ev.expire_at))}"
                if ev.expire_at
                else ""
            )
            ctx.ui.info(f"  {when} [{ev.category}] {ev.content}{expire}")
        return True

    if action == "refine":
        if not getattr(ctx.settings, "profile_enabled", False):
            ctx.ui.warn("画像记忆未开启（settings.toml [memory] profile_enabled）")
            return True
        if len(ctx.messages) < 2:
            ctx.ui.warn("当前会话消息太少，先和贾维斯聊聊再提炼")
            return True
        from agent.core.memory.profile_refiner import maybe_refine_async

        # 走 force 增量路径：只提炼水位之后的新消息，成功后推进水位。
        # 旧实现直接调 refine_session 把整个会话重喂一遍，既费 token 又让
        # 来源字段变成 "manual-refine"，与自动提炼的口径不一致。
        sid = str(getattr(ctx, "session_name", "") or "manual-refine")
        started = maybe_refine_async(list(ctx.messages), sid, ctx.settings, force=True)
        if started:
            ctx.ui.info("已开始后台提炼新增消息（只喂增量，不会重跑历史）")
        else:
            ctx.ui.info("没有需要提炼的新消息（或已有提炼在进行中）")
        return True

    if action == "sync":
        await _sync_profile_to_kg(ctx, confirm=(rest.lower() == "yes"))
        return True

    if action == "add":
        if not rest:
            ctx.ui.warn("用法: /memory add <要记住的内容>（如 /memory add 习惯用 GLM 写代码）")
            return True
        from agent.core.memory.profile_store import ProfileEntry

        entry = ProfileEntry.new(rest, "other", confidence=0.99, source_session="manual")
        # 走查重入口：用户反复口述同一件事时并入已有条目，不再叠加重复记忆
        kept, merged = ProfileStore().merge_or_add(entry)
        _rebuild_system_prompt(ctx)
        if merged:
            ctx.ui.info(f"已并入相似记忆: [{kept.id}] {kept.content}（未新增重复条目）")
        else:
            ctx.ui.info(f"已记住: {rest}（[{kept.id}]）当前会话即生效")
        return True

    if action == "del":
        if not rest:
            ctx.ui.warn("用法: /memory del <id>（id 用 /memory 查看）")
            return True
        if ProfileStore().delete(rest):
            _rebuild_system_prompt(ctx)
            ctx.ui.info(f"已删除画像条目 {rest}")
        else:
            ctx.ui.warn(f"未找到条目: {rest}（id 用 /memory 查看）")
        return True

    if action == "clear":
        if rest.lower() != "yes":
            ctx.ui.warn("清空全部画像不可恢复。确认请输入: /memory clear yes")
            return True
        n = ProfileStore().clear()
        _rebuild_system_prompt(ctx)
        ctx.ui.info(f"已清空画像记忆（{n} 条）")
        return True

    if action == "dedup":
        # 两段式与 clear/sync 同体例：先只读预演条数，yes 才真删改画像库
        store = ProfileStore()
        groups = store.duplicate_group_report()
        if not groups:
            ctx.ui.info("画像记忆没有可归并的深重复条目，无需整理。")
            return True
        total = sum(len(g) - 1 for g in groups)
        if rest.lower() != "yes":
            ctx.ui.info(f"发现 {len(groups)} 组深重复记忆，可归并 {total} 条：")
            for g in groups:
                ctx.ui.info("  「" + g[0].content + "」 × " + str(len(g)))
            ctx.ui.info("确认整理请输入: /memory dedup yes（manual 手动条目优先保留）")
            return True
        n = store.dedup_all()
        _rebuild_system_prompt(ctx)
        ctx.ui.info(f"已归并 {n} 条重复记忆（{len(groups)} 组），画像库更清爽了。")
        return True

    ctx.ui.warn(
        "用法: /memory [add <内容> | del <id> | clear yes | dedup [yes] | refine | sync | file]\n"
        "  /memory              查看画像\n"
        "  /memory add <文本>    手动添加\n"
        "  /memory del <id>     删除条目\n"
        "  /memory clear yes    清空全部\n"
        "  /memory dedup        归并深重复记忆（先预览，加 yes 执行）\n"
        "  /memory refine       立即提炼当前会话\n"
        "  /memory sync         同步画像到知识图谱（先预览，加 yes 确认写入）\n"
        "  /memory file         查看 MEMORY.md 长期记忆文件"
    )
    return True
