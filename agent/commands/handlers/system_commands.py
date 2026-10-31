"""系统维护命令处理器 —— ``/disk`` / ``/clean`` / ``/health``。

对应 BLUEPRINT 6.1/6.2/6.4，是 PLAN-system-maintenance 的命令层落点。

## 三条命令的定位

| 命令 | 只读？ | 说明 |
|---|---|---|
| ``/disk``   | 是 | 磁盘占用与可回收清单；``--top`` 回答「空间被谁占了」 |
| ``/clean``  | 否 | 无 ``--yes`` 只预览；``--restore`` 从隔离区还原；``--batches`` 列批次 |
| ``/health`` | 多数是 | 体检 / 安全 / 威胁记录 / Defender 扫描编排 |

## 为什么全部走线程

扫描要遍历十万级目录，体检要起 PowerShell（实测 15 秒），Defender 扫描更久。
这些直接在事件循环里跑会冻住 REPL —— 连 Ctrl+C 都要等它们跑完。所以统一
``asyncio.to_thread`` 丢到线程池，超时由各模块自己兜。

## 文案纪律

``ctx.ui.info`` **不做** Rich markup 转义（只有 warn/error 做），所以这里显示的
路径、说明、错误原文一律先过 ``rich.markup.escape``。文件路径里出现 ``[`` 的概率
不低（如 ``[system]`` 这类目录名），一旦漏转义就是 MarkupError 直接把 REPL 打崩。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

# 与同目录其它 handler 一致：只在类型检查时导入，避开 router ↔ handler 循环导入
if TYPE_CHECKING:
    from agent.commands.router import CommandContext

from agent.core.maintenance import (
    cleaner,
    defender,
    describe_commands,
    explain_rules,
    health,
    human_size,
    load_rulebook,
    restore,
    run_auto_clean,
    scan_all,
    top_dirs,
)


def _escape(text: str) -> str:
    """Rich markup 转义。``ui.info`` 不做转义，这里必须自己做。"""
    try:
        from rich.markup import escape

        return escape(str(text))
    except Exception:  # noqa: BLE001 - rich 缺失时退化为原文
        return str(text)


def _say(ctx: "CommandContext", text: str) -> None:
    ui = getattr(ctx, "ui", None)
    if ui is not None and hasattr(ui, "info"):
        ui.info(_escape(text))
    else:
        print(text)


def _warn(ctx: "CommandContext", text: str) -> None:
    ui = getattr(ctx, "ui", None)
    if ui is not None and hasattr(ui, "warn"):
        ui.warn(text)   # warn 内部已 escape
    else:
        print(text)


def _err(ctx: "CommandContext", text: str) -> None:
    ui = getattr(ctx, "ui", None)
    if ui is not None and hasattr(ui, "error"):
        ui.error(text)  # error 内部已 escape
    else:
        print(text)


def _settings(ctx: "CommandContext") -> Any:
    return getattr(ctx, "settings", None)


def _cfg(ctx: "CommandContext", field: str, default: Any) -> Any:
    settings = _settings(ctx)
    value = getattr(settings, field, None) if settings is not None else None
    return default if value is None else value


def _pad(text: str, width: int) -> str:
    """按显示宽度补空格（中文按 2 列算）。

    用 ``len`` 直接补会在中文上错位——``"临时文件"`` 是 4 个字符但占 8 列。
    """
    display = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    return text + " " * max(0, width - display)


def _truncate(text: str, width: int) -> str:
    """按显示宽度截断（中文占 2 列）。"""
    out: list[str] = []
    used = 0
    for ch in text:
        w = 2 if ord(ch) > 0x2E80 else 1
        if used + w > width:
            out.append("…")
            break
        out.append(ch)
        used += w
    return "".join(out)


# ---------------------------------------------------------------------------
# /disk
# ---------------------------------------------------------------------------


async def handle_disk(ctx: "CommandContext", raw: str) -> bool:
    """``/disk`` —— 磁盘占用与可回收清单（只读）。

    用法::

        /disk                 # 可回收清单（低于报告阈值的项不列）
        /disk --all           # 含小项
        /disk --top [目录]    # 附带目录体积排行
        /disk --deep          # 附带命令型规则（WinSxS / 卷影 / 休眠 / 磁盘清理）
        /disk <id> [id...]    # 只查这些规则
    """
    tokens = raw.split()[1:]
    want_top = "--top" in tokens
    want_all = "--all" in tokens
    want_deep = "--deep" in tokens
    top_root = ""
    if want_top:
        idx = tokens.index("--top")
        if idx + 1 < len(tokens) and not tokens[idx + 1].startswith("--"):
            top_root = tokens[idx + 1]
    skip = {"--top", "--all", "--deep"}
    scope = [t for t in tokens if not t.startswith("--")]
    if top_root and top_root in scope:
        scope.remove(top_root)

    rules_file = str(_cfg(ctx, "maintenance_rules_file", "") or "")
    book = load_rulebook(rules_file or None)
    timeout_s = float(_cfg(ctx, "maintenance_scan_timeout", 120))
    max_gb = float(_cfg(ctx, "maintenance_scan_max_dir_gb", 50.0))
    min_mb = 0.0 if want_all else float(_cfg(ctx, "maintenance_report_min_mb", 100.0))

    _say(ctx, "正在统计磁盘占用…")
    try:
        result = await asyncio.to_thread(
            scan_all, book,
            scope=scope or None, timeout_s=timeout_s,
            max_dir_gb=max_gb, min_size_mb=min_mb,
        )
    except Exception as exc:  # noqa: BLE001 - 扫描失败不能让 REPL 崩
        _err(ctx, f"扫描失败：{type(exc).__name__}: {exc}")
        return True

    # ---- 盘容量 ----
    lines: list[str] = []
    lines.append(
        f"磁盘占用 · {result.elapsed_s}s"
        + ("（已超时，结果为下限）" if result.timed_out else "")
    )
    for d in result.drives:
        trend = ""
        if d.trend_7d_gb is not None:
            arrow = "↑" if d.trend_7d_gb >= 0 else "↓"
            # 写「已用」而不只是箭头：``↓1.58`` 容易被读成「可用空间在降」，
            # 实际是已用空间降了（盘变空了）。方向标错会直接把用户带去清空盘。
            trend = f"  近 7 天已用 {arrow}{abs(d.trend_7d_gb):.2f} GB"
        lines.append(
            f"  {_pad(d.mount, 4)}{d.free_gb:>7.1f} GB 可用 / {d.total_gb:>7.1f} GB"
            f"（已用 {d.used_percent:.0f}%）{trend}"
        )

    # ---- 可回收清单 ----
    usable = [i for i in result.items if not i.unaddressable and i.size_bytes > 0]
    usable.sort(key=lambda i: i.size_bytes, reverse=True)
    lines.append("")
    lines.append(f"可回收 {human_size(result.reclaimable_bytes)}（不含 high 风险项）")
    if usable:
        lines.append(f"  {_pad('ID', 24)}{_pad('体积', 11)}{_pad('文件', 8)}{_pad('风险', 8)}说明")
        for item in usable:
            # 标记跟着说明走，不塞进体积列——体积列宽 11 装不下「12.6 MB（近期改动）」
            # 这种 17 列的内容，塞进去会把后面每一列都顶错位。
            flag = ""
            if item.truncated:
                flag = "（截断）"
            elif item.in_use_by_session:
                flag = "（近期有改动）"
            lines.append(
                f"  {_pad(item.id, 24)}{_pad(human_size(item.size_bytes), 11)}"
                f"{_pad(str(item.file_count), 8)}{_pad(item.risk, 8)}"
                f"{_truncate(item.desc, 40)}{flag}"
            )
    else:
        lines.append("  没有超过展示阈值的可清理项" + ("" if want_all else "（用 --all 看小项）"))

    # ---- 不适用 / 被拒 ----
    # 命令型规则扫描时也会带 unaddressable 标记（不参与常规扫描），要分开归类，
    # 否则会和"这台机器真没有这个目录"混在一起，读起来像一份错误清单。
    really_absent = [
        i for i in result.items
        if i.unaddressable and i.unaddressable != "命令型规则不参与常规扫描（需要 --deep）"
    ]
    cmd_rules = [
        i for i in result.items
        if i.unaddressable == "命令型规则不参与常规扫描（需要 --deep）"
    ]
    if really_absent:
        lines.append("")
        lines.append(
            "这台机器上没有：" + "、".join(f"{i.id}" for i in really_absent)
        )
    if cmd_rules and not want_deep:
        lines.append(
            "需单独评估（命令型，不可还原）："
            + "、".join(i.id for i in cmd_rules)
            + "  —— 加 --deep 查看说明"
        )
    if want_deep:
        lines.append("")
        lines.append("命令型规则（不代执行，也不可还原；命令由你自己敲）：")
        for c in describe_commands(book):
            lines.append(
                f"  {_pad(c['id'], 20)}risk={_pad(c['risk'], 8)}"
                f"{'需管理员 ' if c['requires_admin'] else ''}{_truncate(c['desc'], 60)}"
            )
            # 只给 id 不给命令，用户还得自己去查——报告等于只报问题不给路
            if c.get("clean_cmd"):
                lines.append(f"    要执行的命令：{c['clean_cmd']}")
            lines.append(f"    提示：{c['note']}")
            lines.append("")

    if result.rejected:
        lines.append("")
        _warn(ctx, "有规则未生效（配置可能写错了）：")
        for rid, why in result.rejected:
            _warn(ctx, f"  {rid}: {why}")

    # ---- 目录体积排行 ----
    if want_top:
        root = top_root or str(Path.home())
        _say(ctx, f"正在统计 {root} 下各子目录体积…")
        try:
            dirs = await asyncio.to_thread(
                top_dirs, root, limit=15,
                timeout_s=min(60.0, timeout_s), min_size_mb=max(50.0, min_mb),
            )
        except Exception as exc:  # noqa: BLE001
            _err(ctx, f"目录统计失败：{type(exc).__name__}: {exc}")
            dirs = []
        lines.append("")
        lines.append(f"空间去哪了 · {root}（一级子目录，只报告不删）")
        if dirs:
            for d in dirs:
                mark = "（截断）" if d.truncated else ""
                lines.append(
                    f"  {_pad(human_size(d.size_bytes) + mark, 12)}"
                    f"{_pad(str(d.file_count) + ' 文件', 12)}{_truncate(d.name, 40)}"
                )
            lines.append("  → 这些目录不在清理白名单内，删不删由你定（/clean 不会碰它们）")
        else:
            lines.append("  没有超过阈值的子目录")

    lines.append("")
    enabled = bool(_cfg(ctx, "maintenance_enabled", False))
    if enabled:
        lines.append("下一步：/clean <id> 预览 → /clean --yes <id> 执行（进隔离区，可还原）")
    else:
        lines.append(
            "清理功能未启用（[maintenance].enabled = false）。本命令只读，可照常使用；"
            "要真正清理请在 configs/settings.toml 里开启该开关。"
        )
    _say(ctx, "\n".join(lines))
    return True


# ---------------------------------------------------------------------------
# /clean
# ---------------------------------------------------------------------------


async def handle_clean(ctx: "CommandContext", raw: str) -> bool:
    """``/clean`` —— 可撤销清理。

    用法::

        /clean                      # 列出可清理项（等价 /disk）
        /clean <id> [id...]         # 预览
        /clean --yes <id> [...]     # 执行（移入隔离区）
        /clean --batches            # 列出隔离区批次
        /clean --restore <batch_id> # 从隔离区还原
        /clean --auto               # 手动跑一次无人值守清理（只动零感知的）
        /clean --auto-status        # 看哪些规则允许自动清、哪些不允许及原因
    """
    tokens = raw.split()[1:]
    rules_file = str(_cfg(ctx, "maintenance_rules_file", "") or "")
    book = load_rulebook(rules_file or None)

    # --auto-status 必须排在 --auto 前面判：两者是包含关系的前缀，
    # 但这里是列表精确匹配，顺序仅为了可读性
    if "--auto-status" in tokens:
        enabled = bool(_cfg(ctx, "maintenance_enabled", False)) and bool(
            _cfg(ctx, "maintenance_auto_clean_enabled", True)
        )
        lines = [
            "自动清理：" + ("已启用" if enabled else "未启用")
            + ("" if enabled else "（需 [maintenance].enabled 与 auto_clean_enabled 同时为 true）")
        ]
        lines.append(
            f"  每日 {_cfg(ctx, 'maintenance_auto_clean_time', '04:30')}"
            f" · 低于 {_cfg(ctx, 'maintenance_auto_clean_min_mb', 20)} MB 不建批次"
            f" · 超过 {_cfg(ctx, 'maintenance_auto_clean_notify_mb', 200)} MB 才提示"
        )
        lines.append("")
        lines.append("候选与原因（只有 auto_ok=true 的才会被自动清）：")
        for row in explain_rules(book):
            mark = "✓" if row["ok"] else "·"
            lines.append(f"  {mark} {_pad(row['id'], 22)}{row['name']} —— {row['reason']}")
        _say(ctx, "\n".join(lines))
        return True

    if "--auto" in tokens:
        if not bool(_cfg(ctx, "maintenance_enabled", False)):
            _err(
                ctx,
                "清理未启用：[maintenance].enabled = false。"
                "请在 configs/settings.toml 的 [maintenance] 表里设 enabled = true 后重试。",
            )
            return True
        max_risk = str(_cfg(ctx, "maintenance_auto_clean_max_risk", "low"))
        keep_days = int(_cfg(ctx, "maintenance_quarantine_days", 7))
        min_mb = float(_cfg(ctx, "maintenance_auto_clean_min_mb", 20.0))
        _say(ctx, "正在跑一次自动清理（只动零感知的那几条）…")
        result = await asyncio.to_thread(
            run_auto_clean, book,
            max_risk=max_risk, keep_days=keep_days, min_mb=min_mb,
        )
        lines = [result.summary()]
        if result.skipped:
            lines.append("")
            lines.append(f"跳过 {len(result.skipped)} 项（不假装成功）：")
            for s in result.skipped[:8]:
                lines.append(f"  {_truncate(str(s.get('path', '')), 56)} —— {s.get('reason', '')}")
        if result.ran and result.batch_id:
            lines.append("")
            lines.append(
                f"批次 {result.batch_id}，{keep_days} 天内可 /clean --restore {result.batch_id} 还原"
            )
        if not result.ran and result.rule_ids:
            lines.append(f"（候选规则：{'、'.join(result.rule_ids)}）")
        _say(ctx, "\n".join(lines))
        return True

    if "--batches" in tokens:
        batches = cleaner.list_batches()
        if not batches:
            _say(ctx, "隔离区是空的（没有清理记录）")
            return True
        lines = ["隔离区批次（越新越可靠；过期批次会在下次清理时自动清掉）"]
        for b in batches:
            broken = "（清单损坏，无法还原）" if b.get("broken") else ""
            lines.append(
                f"  {_pad(str(b.get('batch_id')), 26)}{_pad(str(b.get('entries')) + ' 项', 8)}"
                f"{_pad(str(b.get('size_mb')) + ' MB', 12)}{b.get('created_human', '')}{broken}"
            )
        lines.append("")
        lines.append("还原：/clean --restore <batch_id>")
        _say(ctx, "\n".join(lines))
        return True

    if "--restore" in tokens:
        idx = tokens.index("--restore")
        batch_id = tokens[idx + 1] if idx + 1 < len(tokens) else ""
        if not batch_id:
            _err(ctx, "用法：/clean --restore <batch_id>（先用 /clean --batches 查看）")
            return True
        _say(ctx, f"正在从隔离区还原 {batch_id}…")
        result = await asyncio.to_thread(restore, batch_id)
        if result.error:
            _err(ctx, result.error)
            return True
        lines = [f"还原完成：{result.restored} 项 / {result.restored_mb} MB"]
        if result.skipped:
            lines.append(f"有 {len(result.skipped)} 项没还原（原位置已有同名文件等情况）：")
            for s in result.skipped[:10]:
                lines.append(f"  跳过 {_truncate(s.path, 60)} —— {s.reason}")
        _say(ctx, "\n".join(lines))
        return True

    ids = [t for t in tokens if not t.startswith("--")]
    if not ids:
        _say(ctx, "要清理哪些？先跑 /disk 看清单，再 /clean <id> 预览、/clean --yes <id> 执行。")
        return True

    if "--yes" not in tokens:
        # 预览
        lines = ["清理预览（还没有动任何文件）"]
        rules_map = {r.id: r for r in book.rules}
        total = 0
        for rid in ids:
            rule = rules_map.get(rid)
            if rule is None:
                lines.append(f"  {rid}：未知规则 id")
                continue
            if rule.is_command:
                lines.append(
                    f"  {rid}：命令型规则，本工具不执行（不可还原）。"
                    f"需要时请手动运行：{' '.join(rule.clean_cmd)}"
                )
                continue
            lines.append(f"  {rid}（{rule.risk}）{rule.name}")
            lines.append(f"    位置：{'、'.join(rule.paths)}")
            lines.append(f"    说明：{rule.desc}")
        lines.append("")
        lines.append("确认无误后执行：/clean --yes " + " ".join(ids))
        _say(ctx, "\n".join(lines))
        return True

    # 真正执行
    if not bool(_cfg(ctx, "maintenance_enabled", False)):
        _err(
            ctx,
            "清理未启用：[maintenance].enabled = false。"
            "请在 configs/settings.toml 的 [maintenance] 表里设 enabled = true 后重试。",
        )
        return True

    max_risk = str(_cfg(ctx, "maintenance_auto_clean_max_risk", "low"))
    _say(ctx, f"正在清理（最高风险档 {max_risk}）…")
    result = await asyncio.to_thread(cleaner.clean, book, ids, max_risk=max_risk)
    keep_days = int(_cfg(ctx, "maintenance_quarantine_days", 7))
    purged = await asyncio.to_thread(cleaner.purge_expired, keep_days)

    if result.error and not result.reclaimed_bytes:
        _err(ctx, result.error)
    lines = [
        f"清理完成 · 批次 {result.batch_id} · 释放 {human_size(result.reclaimed_bytes)}"
        f" · {result.elapsed_s}s"
    ]
    for item in result.items:
        status = item.get("status", "ok")
        if status != "ok":
            lines.append(f"  {item.get('id')}：{status}")
        else:
            lines.append(
                f"  {_pad(str(item.get('id')), 22)}{item.get('freed_mb')} MB / "
                f"{item.get('moved')} 项"
            )
    if result.skipped:
        lines.append("")
        lines.append(f"跳过 {len(result.skipped)} 项（不假装成功）：")
        for s in result.skipped[:12]:
            lines.append(f"  {_truncate(s.path, 56)} —— {s.reason}")
        if len(result.skipped) > 12:
            lines.append(f"  …另有 {len(result.skipped) - 12} 项")
    if result.cross_drive:
        lines.append("")
        lines.append("注意：有内容跨盘移动，速度会比同盘慢（隔离区在用户目录）。")
    if purged:
        lines.append(f"已顺带清掉 {purged} 个过期隔离批次（保留 {keep_days} 天）。")
    if result.undo_hint:
        lines.append("")
        lines.append(result.undo_hint)
        lines.append("（隔离区批次不走 W3 账本，所以是 /clean --restore 而不是 /undo）")
    _say(ctx, "\n".join(lines))
    return True


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


async def handle_health(ctx: "CommandContext", raw: str) -> bool:
    """``/health`` —— 体检与安全诊断。

    用法::

        /health                  # 完整体检（文本）
        /health --markdown       # 完整体检（Markdown）
        /health --save [路径]    # 存成 .md 报告
        /health security         # 只看杀软与实时防护
        /health threats          # 威胁检测记录
        /health scan [quick|full|custom <路径>]
        /health status [job_id]  # 扫描进度
    """
    tokens = raw.split()[1:]
    sub = tokens[0].lower() if tokens and not tokens[0].startswith("-") else ""

    # ---- security / threats / scan / status ----
    if sub == "security":
        status = await asyncio.to_thread(defender.security_status)
        lines = ["安全防护状态"]
        if status.products:
            for p in status.products:
                lines.append(
                    f"  {_pad(p.name, 26)}"
                    f"{'启用' if p.enabled else '未启用'} / "
                    f"{'特征库最新' if p.up_to_date else '特征库待更新'}"
                )
        if status.signature_version:
            lines.append(f"  Defender 特征库 {status.signature_version}，引擎 {status.engine_version}")
        if status.signature_age_days is not None:
            lines.append(f"  特征库距今 {status.signature_age_days} 天")
        if status.note:
            lines.append("")
            lines.append(f"结论：{status.note}")
        if status.error:
            _warn(ctx, f"读取异常：{status.error}")
        _say(ctx, "\n".join(lines))
        return True

    if sub == "threats":
        rows, err = await asyncio.to_thread(defender.recent_threats)
        if err:
            _warn(ctx, err)
            _say(ctx, "Defender 被第三方杀软接管时读不到威胁记录，这不代表没有防护。")
            return True
        if not rows:
            _say(ctx, "最近没有威胁检测记录。")
            return True
        lines = ["最近威胁检测记录"]
        for r in rows:
            lines.append(
                f"  {_pad(r['detected_at'], 18)}{_pad(r['status'], 10)}"
                f"{_pad(r['name'], 32)}{_truncate(r['resource'], 40)}"
            )
        _say(ctx, "\n".join(lines))
        return True

    if sub == "scan":
        kind = tokens[1].lower() if len(tokens) > 1 else "quick"
        path = tokens[2] if len(tokens) > 2 else ""
        allow_full = bool(_cfg(ctx, "maintenance_allow_full_scan", False))
        job, err = defender.start_scan(kind=kind, path=path, allow_full=allow_full)
        if job is None:
            _err(ctx, err or "启动扫描失败")
            return True
        _say(
            ctx,
            f"扫描已在后台启动（{kind}）\n"
            f"  任务 id：{job.id}\n"
            f"  进程 pid：{job.pid}\n"
            f"  查进度：/health status {job.id}\n"
            "  扫描不阻塞对话，可以继续用。",
        )
        return True

    if sub == "status":
        job_id = tokens[1] if len(tokens) > 1 else ""
        if job_id:
            data = defender.scan_status(job_id)
            _say(ctx, "\n".join(f"  {k}: {v}" for k, v in data.items()))
            return True
        jobs = defender.list_jobs()
        if not jobs:
            _say(ctx, "没有扫描任务记录。用 /health scan 启动一次。")
            return True
        lines = ["最近扫描任务"]
        for j in jobs:
            lines.append(
                f"  {_pad(str(j.get('id')), 26)}{_pad(str(j.get('state')), 10)}"
                f"{j.get('kind', '')} {j.get('path', '')}"
            )
        _say(ctx, "\n".join(lines))
        return True

    # ---- report / save ----
    want_md = "--markdown" in tokens or sub == "markdown"
    want_save = "--save" in tokens or sub == "save"
    out_path = ""
    if want_save:
        for i, t in enumerate(tokens):
            if t == "--save" and i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                out_path = tokens[i + 1]
            if t == "save" and i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                out_path = tokens[i + 1]

    _say(ctx, "正在体检…")
    workdir = str(getattr(ctx, "workdir", "") or "")
    report = await asyncio.to_thread(
        health.collect,
        timeout_s=float(_cfg(ctx, "maintenance_scan_timeout", 120)),
        in_use_dirs=[workdir] if workdir else [],
    )
    markdown = health.render_markdown(report)

    if want_save:
        target = Path(out_path).expanduser() if out_path else (
            Path.home() / ".jarvis" / f"health-{time.strftime('%Y%m%d-%H%M')}.md"
        )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        except OSError as exc:
            _err(ctx, f"报告写入失败：{exc}")
            return True
        _say(ctx, f"体检报告已保存：{target}")
        return True

    if want_md:
        _say(ctx, markdown)
        return True

    # 文本渲染
    lines: list[str] = []
    lines.append(
        f"系统体检 · {report.elapsed_s}s · "
        f"{'管理员' if report.is_admin else '普通权限（部分数据需提权）'}"
    )
    for sec in report.sections:
        flag = "" if sec.ok else "（未取到）"
        admin = " [需管理员]" if sec.need_admin and not sec.ok else ""
        lines.append("")
        lines.append(f"[{sec.title}]{flag}{admin}")
        data = sec.data
        if sec.key == "resource" and sec.ok:
            lines.append(
                f"  CPU {data.get('cpu_percent')}%  "
                f"内存可用 {data.get('mem_available_gb')}/{data.get('mem_total_gb')} GB  "
                f"磁盘队列 {data.get('disk_queue')}（繁忙 {data.get('disk_busy_percent')}%）"
            )
            lines.append(
                f"  {data.get('os')} · 上次开机 {data.get('last_boot')}"
                f"（已运行 {data.get('uptime_hours')} 小时）"
            )
            for d in data.get("drives", []):
                lines.append(
                    f"    {_pad(str(d.get('mount')), 4)}{d.get('free_gb')} GB 可用 / "
                    f"{d.get('total_gb')} GB（{d.get('used_percent')}%）"
                )
        elif sec.key == "disk" and sec.ok:
            for d in data.get("disks", []):
                wear = d.get("wear_percent")
                lines.append(
                    f"  {_truncate(str(d.get('name')), 40)}（{d.get('media')}, "
                    f"{d.get('size_gb')} GB）{d.get('health')}"
                    + (f" · 磨损 {wear}%" if wear is not None else "")
                )
        elif sec.key == "security" and sec.ok:
            for p in data.get("products", []):
                lines.append(
                    f"  {_pad(p.get('name'), 26)}"
                    f"{'启用' if p.get('enabled') else '未启用'} / "
                    f"{'库最新' if p.get('up_to_date') else '库待更新'}"
                )
        elif sec.key == "processes" and sec.ok:
            for p in data.get("processes", []):
                mark = "  ← 近期启动" if p.get("recent") else ""
                lines.append(
                    f"  {_pad(str(p.get('mem_mb')) + ' MB', 12)}"
                    f"{_pad(str(p.get('name')), 26)}{p.get('pid')}{mark}"
                )
        elif sec.key == "startup" and sec.ok:
            for item in data.get("items", [])[:10]:
                lines.append(f"  {_truncate(str(item.get('name')), 30)}  {_truncate(str(item.get('command')), 50)}")
            lines.append(
                f"  另有自动服务 {data.get('auto_services')} 个 / "
                f"登录触发计划任务 {data.get('logon_tasks')} 个"
            )
        elif sec.key == "power" and sec.ok:
            if data.get("battery_percent") is not None:
                lines.append(f"  电池 {data.get('battery_percent')}%（{data.get('battery_status')}）")
            else:
                lines.append(f"  {data.get('battery_status')}")
        elif sec.key == "thermal" and sec.ok:
            lines.append(f"  CPU 温度 {data.get('cpu_c')} ℃")
        if sec.note:
            lines.append(f"  注：{sec.note}")

    if report.advice:
        lines.append("")
        lines.append("[建议]")
        for a in report.advice:
            lines.append(f"  · {a}")
    lines.append("")
    lines.append("只报告不处置：本命令不会结束进程、不会禁用启动项。")
    lines.append("存报告：/health --save [路径]   清空间：/disk --top")
    _say(ctx, "\n".join(lines))
    return True