"""``/repair`` 与 ``/health caps --repair`` 的命令体。

从 :mod:`system_commands` 拆出来有两个理由：一是仓库的 900 行门（
`test_engine_split_guards.py`）会盯住每个 agent 文件；二是职责本来就不同——
`/health` 是"给系统做体检"，`/repair` 是"管家自己动环境的那道闸"，同住一个文件
迟早会有人为了塞新子命令去压缩另一边的注释。

两个函数都不自己判预算与权限：所有判定都在 `agent/capabilities/repair.py`
（`decide` / `execute_plan`），这一层只负责取参数、跑线程、拼回执。
拆层的意义就在于此：换宿主（CLI / 桌面 / serve）时不该有人复制一份闸门逻辑。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 只为类型标注，运行时不需要（避开 router ↔ handler 循环导入）
    from agent.commands.router import CommandContext

# 复用同包既有的输出三件套，不再抄一份（与 media_commands 复用
# voice_commands._say 是同一口径）
from agent.commands.handlers.system_commands import _err, _say, _warn

async def run_repair(ctx: "CommandContext", rest: list[str]) -> str:
    """执行一次能力修复并组装回执（`/health caps --repair <能力名>` 的执行体）。

    为什么要求指名能力：一次「全表修复」会把几个互不相干的缺件排成一队各自
    跑一次网络安装，而在出第一条错时已经没人记得自己批准过什么。指名是把
    「代价发生在哪里」留给人的最小设计。
    """
    from agent.capabilities.repair import announce, repair_capability
    from agent.capabilities.registry import CAPABILITY_REGISTRY

    target = next((t for t in rest if not t.startswith("-")), "")
    if not target:
        candidates = sorted(
            n for n, m in CAPABILITY_REGISTRY.items() if m.repair_actions
        )
        return (
            "需要指名要修哪个能力：/health caps --repair <能力名> [--yes]\n"
            "可修复的条目：" + "、".join(candidates)
        )
    if target not in CAPABILITY_REGISTRY:
        return f"没有登记过能力「{target}」。现有条目：{'、'.join(sorted(CAPABILITY_REGISTRY))}"

    settings = getattr(ctx, "settings", None)
    confirmed = "--yes" in rest
    outcomes = await asyncio.to_thread(
        repair_capability,
        target,
        settings,
        workdir=str(getattr(ctx, "workdir", "") or ""),
        session_name=str((getattr(ctx, "extra", None) or {}).get("session_name", "")),
        permission_mode=str(getattr(getattr(ctx, "engine", None), "permission_mode", "default") or "default"),
        confirmed=confirmed,
    )
    if not outcomes:
        return f"「{target}」当前没有需要修的缺件（或没有可用的修复动作）。"

    # 复测已在 repair 层内部跑过（deep_check），这里不再重复跑一档
    parts: list[str] = []
    voice_lines: list[str] = []
    for oc in outcomes:
        text, voice = announce(oc)
        parts.append(text)
        if voice:
            voice_lines.append(voice)
    if bool(getattr(settings, "repair_announce_voice", True)) and voice_lines:
        # 播报失败不能把已完成的修复报成失败，也不能反过来：只补一句实情。
        try:
            from agent.commands.handlers.voice_commands import _say as _speak

            await asyncio.to_thread(
                _speak, ctx.ui, settings, "".join(voice_lines)
            )
        except Exception as exc:  # noqa: BLE001 - 语音不可用不影响修复本身
            parts.append(f"（语音播报没成功：{type(exc).__name__}；文字回执已在上）")
    return "\n\n".join(parts)


async def handle_repair(ctx: "CommandContext", raw: str) -> bool:
    """``/repair`` —— 自我修复的急停开关与台账。

    用法::

        /repair            # 看当前闸门状态与最近几条修复
        /repair off        # 关掉全部代执行（落盘 + 立即生效）
        /repair on
        /repair disable fetch-browser   # 只关这一类动作
        /repair enable fetch-browser

    为什么默认开还要给一个急停开关：权限大 + 默认动，就必须存在一个
    「一句话能叫停」的手段，否则用户只能去翻配置文件才能收回去。
    @author aceFelix
    """
    from agent.capabilities.repair import REPAIR_ACTIONS, history
    from agent.capabilities.report import collect_reports, iter_fix_commands
    from agent.config.config_specs import get_spec, save_value

    tokens = raw.split()[1:]
    verb = tokens[0].lower() if tokens else "status"
    settings = getattr(ctx, "settings", None)

    def _persist(spec_key: str, value: object, attr: str) -> bool:
        """落盘 + 改运行时值。两个都做，否则用户改完发现“没反应”。"""
        spec = get_spec(spec_key)
        if spec is None:
            _err(ctx, f"配置项 {spec_key} 不在真源表里，不自行开写口子")
            return False
        if not save_value(spec, value):
            _err(ctx, "写入 settings.toml 失败（文件权限或内容问题），运行时值未变")
            return False
        if settings is not None:
            setattr(settings, attr, value)
        return True

    if verb in ("on", "off"):
        value = verb == "on"
        if not _persist("repair_enabled", value, "repair_enabled"):
            return True
        _say(
            ctx,
            f"自我修复已{'开启' if value else '关闭'}。"
            + ("" if value else "关闭后仍会给出可复制的修复命令，只是不代执行。"),
        )
        return True

    if verb in ("disable", "enable"):
        action = tokens[1].lower() if len(tokens) > 1 else ""
        if action not in REPAIR_ACTIONS:
            _err(ctx, f"「{action or '（未填）'}」不是白名单动作。可用：{'、'.join(REPAIR_ACTIONS)}")
            return True
        current = [str(x) for x in (getattr(settings, "repair_disabled_actions", []) or [])]
        if verb == "disable":
            nxt = sorted(set(current) | {action})
        else:
            nxt = sorted(set(current) - {action})
        if not _persist("repair_disabled_actions", nxt, "repair_disabled_actions"):
            return True
        _say(ctx, f"已{'关掉' if verb == 'disable' else '放回'}动作 {action}。当前关闭：{'、'.join(nxt) or '（无）'}")
        return True

    # ---- 默认：闸门状态 + 现在能修什么 + 台账 ----
    disabled = [str(x) for x in (getattr(settings, "repair_disabled_actions", []) or [])]
    unknown = sorted(set(disabled) - set(REPAIR_ACTIONS))
    lines = [
        "自我修复闸门",
        f"  总闸：{'开' if bool(getattr(settings, 'repair_enabled', True)) else '关'}（/repair off 可关）",
        f"  询问线：{float(getattr(settings, 'repair_ask_threshold_mb', 200.0)):.0f}MB 以上先问",
        f"  后台线：预计超 {float(getattr(settings, 'repair_background_over_s', 30.0)):.0f}s 转后台",
        f"  播报：{'开' if bool(getattr(settings, 'repair_announce_voice', True)) else '关'}",
        f"  已关动作：{'、'.join(disabled) or '（无）'}",
    ]
    if unknown:
        # 写错不报错也不生效，所以必须把它说出来，而不是让用户以为关上了
        _warn(ctx, f"这些不在白名单里，写了也不生效：{'、'.join(unknown)}")

    reports = await asyncio.to_thread(collect_reports)
    cmds = iter_fix_commands(reports)
    if cmds:
        lines.append("")
        lines.append("当前可尝试的修复（/health caps --repair <能力名>）：")
        for cap, cmd in cmds:
            lines.append(f"  {cap}：{cmd}")

    rows = history()
    lines.append("")
    if rows:
        lines.append(f"最近 {len(rows)} 条修复：")
        for r in rows:
            lines.append(f"  [{r['when']}] {r['action']} → {r['state']}｜{r['preview']}")
    else:
        lines.append("还没有任何修复记录。")
    _say(ctx, "\n".join(lines))
    return True
