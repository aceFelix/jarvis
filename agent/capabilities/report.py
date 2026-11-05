"""能力自检的呈现层：把探测结论排成人和模型都能直接用的报告。

为什么单独一层：`/health caps`、启动横幅、`--save` 的 Markdown 报告、以及 S4
修复前的「我打算做什么」预览，都要同一套排版。放在 `health.py` 里会把「系统体检」
和「管家自查」两种语义混进一个文件；放命令 handler 里则会让 CLI 与桌面两个宿主
各抄一遍——那正是 `daemon/monitor.py` 顶部注释里被删掉的那类并存。

一条排版纪律贯穿到底：**状态词与「看不清」绝不混用**。`probe_failed` 一律印成
「无法确认」并附原始证据，绝不印成「未安装」——本次事故里最贵的那句话就是把
「看不清」讲成了「被回收」。

@author aceFelix
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.capabilities.probe import (
    check,
    check_all,
    deep_check_all,
    smoke_requirements,
)
from agent.capabilities.registry import (
    CAPABILITY_REGISTRY,
    CapabilityReport,
    CapState,
    ReqState,
)

# 状态 → 给用户看的一个词。刻意把「没看清」和「没有」分成两个词。
_STATE_LABEL = {
    CapState.OK: "可用",
    CapState.DEGRADED: "可用（有缺项）",
    CapState.MISSING: "不可用",
    CapState.UNSUPPORTED: "本平台不适用",
    CapState.PROBE_FAILED: "无法确认",
    CapState.UNKNOWN: "未登记",
}

# 每行前面的符号：让"要不要处理"在扫一眼时就能分出来
_STATE_MARK = {
    CapState.OK: "✓",
    CapState.DEGRADED: "○",
    CapState.MISSING: "⚠",
    CapState.UNSUPPORTED: "—",
    CapState.PROBE_FAILED: "?",
    CapState.UNKNOWN: "?",
}

# 报告里非 OK 的条件状态。SKIPPED 单列：它表示"没查"，不是"查出问题"。
_NOTOK = (ReqState.MISSING, ReqState.UNSUPPORTED, ReqState.PROBE_FAILED)


def collect_reports(*, deep: bool = False) -> dict[str, CapabilityReport]:
    """取一份结论（默认只跑静态档；`deep=True` 才跑冒烟）。

    深档要跑真浏览器、真截屏，慢一到两个量级，因此调用方必须先告诉用户「这次要等
    一会儿」——不预告的长时间静默会被当成卡死，然后被中断，什么结论都拿不到。
    """
    if deep:
        return deep_check_all()
    return check_all()


def iter_fix_commands(reports: dict[str, CapabilityReport]) -> list[tuple[str, str]]:
    """(能力名, 修复命令) 清单。命令一律以当前解释器为锚点，可直接复制执行。

    只收 `MISSING`：`probe_failed` 的 fix_hint 一律不给——我们只是没看清，不知道缺
    什么，此时给一条安装命令就是编造（计划 7.2 ④ 的后一半）。UNSUPPORTED 是平台
    不适用，装什么也不会适用；optional 缺项不阻断主流程，不催人装。
    """
    out: list[tuple[str, str]] = []
    for name, rep in reports.items():
        for req in rep.requirements:
            if req.state == ReqState.MISSING and req.fix_hint and not req.optional:
                out.append((name, req.fix_hint))
    return out


def render_caps(reports: dict[str, CapabilityReport], *, deep: bool = False) -> str:
    """人读版报告。顺序按能力名稳定排列，便于两次输出做 diff。"""
    total = len(reports)
    ok = [r for r in reports.values() if r.state == CapState.OK]
    bad = [r for r in reports.values() if r.state in _BAD_STATES]
    rest = [r for r in reports.values() if r.state == CapState.DEGRADED]

    tier = "静态 + 冒烟" if deep else "静态"
    lines = [
        (
            f"能力自检（{tier}档 · 共 {total} 项 · "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}）"
        ),
        "",
    ]

    if deep:
        # 深档必须自证“到底跑了哪几项冒烟”。否则一句“13 项检查完成”会让
        # “没跑”与“跑过且通过”看不出差别——而前者才是常见情况（没声明探针）。
        ran = [r.name for r in reports.values() if any(
            q.kind == "smoke" and q.state in (ReqState.OK, ReqState.MISSING, ReqState.PROBE_FAILED)
            for q in r.requirements
        )]
        skipped = [r.name for r in reports.values() if any(
            q.kind == "smoke" and q.state == ReqState.SKIPPED for q in r.requirements
        )]
        no_probe = sorted(set(CAPABILITY_REGISTRY) - {n for n in ran} - {n for n in skipped})
        lines.append(
            f"冒烟实跑：{'、'.join(sorted(ran)) or '（无）'}"
            f"｜因缺件跳过：{'、'.join(sorted(skipped)) or '（无）'}"
            f"｜没声明探针：{len(no_probe)} 项"
        )
        lines.append("")

    for rep in bad:
        lines.append(
            f"{_STATE_MARK.get(rep.state, '?')} {rep.title}  [{rep.name}]  "
            f"{_STATE_LABEL.get(rep.state, rep.state)}"
        )
        for item in rep.missing_lines():
            lines.append(f"    · {item}")
        for req in rep.requirements:
            if req.state == ReqState.PROBE_FAILED and not req.optional:
                # 「看不清」也要把原始证据摆出来，否则用户只看到一个问号，什么信息都没拿到
                lines.append(f"    · 看不清：{req.label}｜{req.evidence}")
            elif req.state in _NOTOK and req.optional:
                lines.append(f"    · （可选）{req.label}：{req.evidence}")
        for req in smoke_requirements(rep):
            if req.state == ReqState.SKIPPED:
                lines.append(f"    · {req.label}：{req.evidence}")
            elif req.state != ReqState.OK:
                lines.append(f"    · {req.label}：{req.state}｜{req.evidence}")
        lines.append("")

    for rep in rest:
        lines.append(
            f"{_STATE_MARK.get(rep.state, '?')} {rep.title}  [{rep.name}]  "
            f"{_STATE_LABEL.get(rep.state, rep.state)}"
        )
        for line in rep.degraded_lines():
            lines.append(f"    · {line}")
        lines.append("")

    if ok:
        lines.append(f"✓ 其余 {len(ok)} 项可用：{'、'.join(r.name for r in sorted(ok, key=lambda x: x.name))}")

    cmds = iter_fix_commands(reports)
    if cmds:
        lines += ["", "可复制的修复命令（只列确认缺件的项）："]
        for cap, cmd in cmds:
            lines.append(f"  # {cap}")
            lines.append(f"  {cmd}")
    elif deep:
        lines += ["", "冒烟档全部通过，没有需要补的东西。"]
    else:
        lines += ["", "要连「装了但跑不起来」一起查，用 /health caps --deep（会真起一次浏览器）。"]

    if any(r.state == CapState.PROBE_FAILED for r in reports.values()):
        lines += ["", "注：有探针没能给出可靠结论（标 ? 的项）。那不是缺件，别照着装东西。"]

    return "\n".join(lines).rstrip() + "\n"


def render_caps_markdown(reports: dict[str, CapabilityReport], *, deep: bool = False) -> str:
    """Markdown 版（`/health --save` 那套报告口径沿用；表头字段与文本版一致）。"""
    tier = "静态 + 冒烟" if deep else "静态"
    lines = [
        "# 能力自检报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 档位：{tier}",
        f"- 条目数：{len(reports)}",
        "",
        "| 能力 | 状态 | 证据 | 修复命令 |",
        "|---|---|---|---|",
    ]
    for name, rep in sorted(reports.items()):
        notes = [f"{r.label}：{r.evidence}" for r in rep.requirements if r.state in _NOTOK]
        # 与 iter_fix_commands 同一判据：只给 MISSING 的补法，看不清的不给
        hints = [
            r.fix_hint for r in rep.requirements
            if r.state == ReqState.MISSING and not r.optional and r.fix_hint
        ]
        evidence = "<br>".join(notes) if notes else rep.summary_line()
        lines.append(
            f"| `{name}` | {_STATE_LABEL.get(rep.state, rep.state)} | {evidence} | "
            f"{'<br>'.join(hints) if hints else '—'} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_one(name: str, *, deep: bool = False) -> str:
    """单个能力的详情：声明 + 每条前置条件的实测证据。

    排障时用得上——汇总表只说「不可用」，而「playwright 包版本 1.60.0 需要的
    revision 是 1223，目录里没有」这句话才是能直接行动的信息。
    """
    if name not in CAPABILITY_REGISTRY:
        known = "、".join(sorted(CAPABILITY_REGISTRY))
        return f"没有登记过能力「{name}」。现有条目：{known}\n"
    # 只探这一项：详情视图不该为了看一个能力而把全表冒烟跑一遍（那是一两分钟）
    if deep:
        from agent.capabilities.probe import deep_check

        rep = deep_check(name)
    else:
        rep = check(name)
    lines = [
        f"{rep.title}  [{rep.name}]  {_STATE_LABEL.get(rep.state, rep.state)}",
        "",
        f"- extras 组：{rep.extras_group or '（无，修复只给建议不代装）'}",
        f"- 允许的修复动作：{'、'.join(rep.repair_actions) or '（无）'}",
        f"- 降级路径：{rep.degradable or '（无）'}",
        f"- 影响交付自查：{'是' if rep.blocks_selfcheck else '否'}",
        "",
        "前置条件实测：",
    ]
    for req in rep.requirements:
        mark = "✓" if req.state == ReqState.OK else _REQ_MARK.get(req.state, "?")
        opt = "（可选）" if req.optional else ""
        lines.append(f"  {mark} [{req.kind}] {req.label}{opt}：{req.state}｜{req.evidence}")
        if req.fix_hint:
            lines.append(f"      补法：{req.fix_hint}")
    return "\n".join(lines) + "\n"


def to_dict(reports: dict[str, CapabilityReport]) -> dict[str, Any]:
    """机器可读版（`--json`）：桌面端与后续审计脚本按这份结构取数。"""
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "capabilities": {name: rep.to_dict() for name, rep in sorted(reports.items())},
        "fix_commands": [{"capability": cap, "command": cmd} for cap, cmd in iter_fix_commands(reports)],
    }


# 汇总里算「要处理」的状态：可用有缺项（DEGRADED）单独一段，不混进这里
_BAD_STATES = (CapState.MISSING, CapState.UNSUPPORTED, CapState.PROBE_FAILED, CapState.UNKNOWN)

_REQ_MARK = {
    ReqState.MISSING: "⚠",
    ReqState.UNSUPPORTED: "—",
    ReqState.PROBE_FAILED: "?",
    ReqState.SKIPPED: "·",
    ReqState.OK: "✓",
}
