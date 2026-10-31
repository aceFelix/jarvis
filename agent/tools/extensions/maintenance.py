"""系统维护工具 —— ``DiskReport`` / ``DiskClean`` / ``SystemHealth``。

对应 BLUEPRINT 6.1（监控健康）/ 6.2（清理垃圾）/ 6.4（杀毒）的落点。

## 三个工具的分工与安全姿态

| 工具 | 读/写 | 权限 | 说明 |
|---|---|---|---|
| ``DiskReport`` | 只读 | ALLOW | 扫体积、给清单，一个字节都不动 |
| ``DiskClean`` | 写（移入隔离区） | ASK | 没有 ``confirm=true`` 只回预览；``maintenance_enabled=false`` 直接 DENY |
| ``SystemHealth`` | 多数只读 | ALLOW / scan 走 ASK | 体检报告、安全状态、扫描编排 |

## 两个容易写错的地方

1. **``DiskClean`` 必须 ``is_irreversible=False``**。它确实删东西（``is_destructive=True``），
   但删进隔离区、7 天内可 ``/clean --restore`` 还原，所以**不该进 30 秒延时发送窗口**。
   账本的正确用法就是"有补偿手段就不冒充不可逆"（PLAN 4.6）。

   **还原命令是 ``/clean --restore <batch_id>``，不是 ``/undo``。** ``/undo`` 走的是
   W3 副作用账本（``core/actions/journal.py``），跟隔离区不是同一套记录；写错会让用户
   敲一条注定落空的命令，然后以为删掉的文件找不回来了。见 ``cleaner.RESTORE_COMMAND``。
2. **参数里不许出现 ``path`` / ``file_path`` 这类键**。``file_effects.extract_targets``
   命中这些键会给目标做写前留底；把 ``%LOCALAPPDATA%\\Temp`` 整个复制一份，
   比清理本身还占空间。本模块的参数只用 ``ids`` / ``top_root`` / ``scan_path``，
   刻意避开那组键。

@author aceFelix
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.maintenance import (
    cleaner,
    defender,
    describe_commands,
    health,
    human_size,
    load_rulebook,
    scan_all,
    top_dirs,
)
from agent.core.maintenance import scanner as scanner_mod
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import Tool

#: 维护类工具统一的前缀（权限规则里可以写 ``Maintenance*`` 批量放行）
_NAME_PREFIX = ""


def _settings_of(ctx: ToolContext) -> Any:
    """取 Settings；没有就返回 None（工具要在裸上下文里也不崩）。"""
    return getattr(ctx, "settings", None)


def _cfg(ctx: ToolContext, field: str, default: Any) -> Any:
    """读 ``[maintenance]`` 配置项，缺失时用默认值。"""
    settings = _settings_of(ctx)
    value = getattr(settings, field, None) if settings is not None else None
    return default if value is None else value


def _enabled(ctx: ToolContext) -> bool:
    return bool(_cfg(ctx, "maintenance_enabled", False))


def _rulebook(ctx: ToolContext):
    rules_file = str(_cfg(ctx, "maintenance_rules_file", "") or "")
    return load_rulebook(rules_file or None)


def _in_use_dirs(ctx: ToolContext) -> list[str]:
    """本次会话正在用的目录（条件化建议用）。

    只认 ``ctx.workdir``：它是最可靠的一条。不翻 ``ctx.messages`` 猜路径——
    从对话里正则提取路径会把用户随口提到的目录当成"在用"，反而误导。
    """
    workdir = str(getattr(ctx, "workdir", "") or "")
    return [workdir] if workdir else []


class DiskReportTool(Tool):
    """磁盘占用报告（只读）。回答「我 C 盘为什么满了」。

    @author aceFelix
    """

    name = "DiskReport"
    description = (
        "查看磁盘占用与可回收空间：给出各清理条目的大小、风险等级和「删了会怎样」的说明，"
        "可选附带指定目录下一级子目录的体积排行（回答「空间被谁占了」）。全程只读，不删任何东西。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "只查这些规则 id；留空查全部",
            },
            "min_size_mb": {
                "type": "number",
                "description": "小于此体积的条目不列出；默认读 [maintenance].report_min_mb",
            },
            "include_top": {
                "type": "boolean",
                "description": "是否附带目录体积排行（找出占空间的大目录）",
            },
            "top_root": {
                "type": "string",
                "description": "体积排行的根目录，默认用户主目录",
            },
            "deep": {
                "type": "boolean",
                "description": "是否列出需要单独执行分析命令的条目（WinSxS / 卷影副本 / 休眠文件）",
            },
        },
    }

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读磁盘扫描，不修改任何文件")

    def user_facing_name(self, args: dict[str, Any] | None = None) -> str:
        return "磁盘占用报告"

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "正在统计磁盘占用…"

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        book = _rulebook(ctx)
        timeout_s = float(_cfg(ctx, "maintenance_scan_timeout", 120))
        max_gb = float(_cfg(ctx, "maintenance_scan_max_dir_gb", 50.0))
        min_mb = args.get("min_size_mb")
        if min_mb is None:
            min_mb = float(_cfg(ctx, "maintenance_report_min_mb", 100.0))
        scope = args.get("scope")
        scope_list = [str(x) for x in scope] if isinstance(scope, list) and scope else None

        result = scan_all(
            book, scope=scope_list, timeout_s=timeout_s,
            max_dir_gb=max_gb, min_size_mb=float(min_mb),
        )

        # 0 字节的"路径不存在"条目不该混进主清单——它们是"这台机器没有"，不是"没有空间可清"
        usable = [i for i in result.items if not i.unaddressable and i.size_bytes > 0]
        absent = [i for i in result.items if i.unaddressable]
        usable.sort(key=lambda i: i.size_bytes, reverse=True)

        payload: dict[str, Any] = {
            "total_reclaimable_mb": round(result.reclaimable_bytes / 1024 / 1024, 1),
            "total_reclaimable_human": human_size(result.reclaimable_bytes),
            "drives": [d.to_dict() for d in result.drives],
            "items": [i.to_dict() for i in usable],
            "not_applicable": [
                {"id": i.id, "name": i.name, "reason": i.unaddressable} for i in absent
            ],
            "rejected_rules": [{"id": rid, "reason": why} for rid, why in result.rejected],
            "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_s": result.elapsed_s,
            "timed_out": result.timed_out,
            "enabled": _enabled(ctx),
        }

        if args.get("deep"):
            payload["command_rules"] = describe_commands(book)

        if args.get("include_top"):
            home = Path.home()
            root = str(args.get("top_root") or home)
            payload["top_root"] = root
            if Path(root).is_dir():
                entries = [d.to_dict() for d in top_dirs(
                    root, limit=15, timeout_s=min(45.0, timeout_s),
                    min_size_mb=max(50.0, float(min_mb)),
                )]
                payload["top_dirs"] = entries
                if not entries:
                    payload["top_note"] = (
                        f"{root} 下没有超过 {max(50.0, float(min_mb)):.0f} MB 的一级子目录，"
                        "或者权限不足读不到"
                    )
            else:
                # 实测（2026-10-31 本机）：模型把用户主目录猜成 ``C:\Users\acefelix``
                # （真实是中文用户名），拿到空数组后只能自己连续跑 ls/du 反复试错。
                # 静默返回 [] 是这里最贵的错——它看起来像"没有东西占空间"。
                payload["top_dirs"] = []
                payload["top_error"] = (
                    f"top_root 不存在：{root}。不传这个参数就会用用户主目录 {home}"
                )

        return ToolResult.ok(payload)


class DiskCleanTool(Tool):
    """可撤销清理：移入隔离区，7 天内可还原。

    @author aceFelix
    """

    name = "DiskClean"
    description = (
        "清理指定条目：把文件移入隔离区（不是直接删除，可用 /clean --restore 还原），"
        "并如实报告被跳过的项与原因。必须显式给出要清理的条目 id，不接受「全清」。"
        "首次调用不带 confirm 只返回预览。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要清理的规则 id 列表（必填）",
            },
            "confirm": {
                "type": "boolean",
                "description": "为 true 才真正执行；否则只返回将要清理的预览",
            },
        },
        "required": ["ids"],
    }
    deferred = True

    def is_read_only(self, args: dict[str, Any]) -> bool:
        # 不带 confirm 时确实只读（只生成预览），带了就真的移动文件
        return not bool(args.get("confirm"))

    def is_destructive(self, args: dict[str, Any]) -> bool:
        return bool(args.get("confirm"))

    def is_irreversible(self, args: dict[str, Any]) -> bool:
        """**必须为 False**：删进隔离区、7 天内可 /clean --restore 还原。

        详见模块头注释第 1 条。
        """
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        if not _enabled(ctx):
            return PermissionResult.deny(
                "[maintenance].enabled = false，清理功能未启用；"
                "只读的 DiskReport / SystemHealth 仍可用"
            )
        if not bool(args.get("confirm")):
            return PermissionResult.allow("仅生成清理预览，不移动任何文件")
        ids = args.get("ids")
        if not isinstance(ids, list) or not ids:
            return PermissionResult.deny("必须显式给出 ids，不接受全清")
        scope = "、".join(str(x) for x in ids)
        return PermissionResult.ask(
            f"将把 {scope} 移入隔离区（可用 /clean --restore 还原），确认执行？"
        )

    def user_facing_name(self, args: dict[str, Any] | None = None) -> str:
        return "清理磁盘" if (args or {}).get("confirm") else "清理预览"

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "正在清理…" if (args or {}).get("confirm") else "正在统计待清理项…"

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        ids = args.get("ids")
        if not isinstance(ids, list) or not ids:
            return ToolResult.error(
                "必须显式给出 ids 列表；本工具不支持「全清」（先跑 DiskReport 看清单）",
                retryable=False,
            )
        id_list = [str(x) for x in ids]
        book = _rulebook(ctx)

        if not bool(args.get("confirm")):
            # 预览：不做任何移动，只把每条规则的实际体积与风险摆出来
            preview = []
            for rid in id_list:
                rule = book.by_id(rid)
                if rule is None:
                    preview.append({"id": rid, "error": "未知规则 id"})
                    continue
                preview.append({
                    "id": rule.id,
                    "name": rule.name,
                    "risk": rule.risk,
                    "requires_admin": rule.requires_admin,
                    "desc": rule.desc,
                    "paths": list(rule.paths),
                    "supported": not rule.is_command,
                    "note": "命令型规则不可还原，本工具不执行" if rule.is_command else "",
                })
            return ToolResult.ok({
                "mode": "preview",
                "items": preview,
                "hint": "确认无误后带 confirm=true 再次调用；执行后 7 天内可用 /clean --restore <batch_id> 还原",
            })

        max_risk = str(_cfg(ctx, "maintenance_auto_clean_max_risk", "low"))
        result = cleaner.clean(
            book, id_list,
            max_risk=max_risk,
            on_progress=lambda msg: None,   # 进度由终端层按需接管，工具内不刷屏
        )
        keep_days = int(_cfg(ctx, "maintenance_quarantine_days", 7))
        purged = cleaner.purge_expired(keep_days)

        payload = result.to_dict()
        if purged:
            payload["purged_expired_batches"] = purged
        if result.error and not result.reclaimed_bytes:
            return ToolResult.error(result.error, retryable=False)
        return ToolResult.ok(payload)


class SystemHealthTool(Tool):
    """体检与杀软编排（多数只读，扫描走 ASK）。

    @author aceFelix
    """

    name = "SystemHealth"
    description = (
        "系统体检与安全状况：生成 CPU/内存/磁盘/进程热点/开机自启/电源的体检报告，"
        "读取杀软与实时防护状态（会识别第三方杀软接管的情况），查看威胁记录，"
        "或编排 Windows Defender 扫描。只报告不处置——不会结束进程、不会禁用启动项。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["report", "security", "threats", "scan", "scan_status", "save"],
                "description": "report=完整体检 / security=安全区块 / threats=威胁记录 / "
                               "scan=启动扫描 / scan_status=查扫描进度 / save=存体检报告到文件",
            },
            "format": {
                "type": "string",
                "enum": ["text", "markdown"],
                "description": "返回格式，默认 text",
            },
            "detail": {
                "type": "boolean",
                "description": "report 是否附带完整区块明细（默认 true）",
            },
            "scan_kind": {
                "type": "string",
                "enum": ["quick", "full", "custom"],
                "description": "scan 动作的扫描类型，默认 quick",
            },
            "scan_path": {
                "type": "string",
                "description": "scan_kind=custom 时的目标目录",
            },
            "job_id": {
                "type": "string",
                "description": "scan_status 要查的任务 id；留空列最近任务",
            },
            "out_file": {
                "type": "string",
                "description": "save 动作的报告输出路径（.md）",
            },
        },
    }
    deferred = False

    _READ_ACTIONS = ("report", "security", "threats", "scan_status", "save")

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return str(args.get("action") or "report") in self._READ_ACTIONS

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        action = str(args.get("action") or "report")
        if action in self._READ_ACTIONS:
            return PermissionResult.allow("只读体检，不修改系统状态")
        if action == "scan":
            kind = str(args.get("scan_kind") or "quick")
            if kind == "full" and not bool(_cfg(ctx, "maintenance_allow_full_scan", False)):
                return PermissionResult.deny(
                    "全盘扫描默认关闭（[maintenance].allow_full_scan）；改用 quick 或先开启该开关"
                )
            return PermissionResult.ask(
                f"将启动 Windows Defender {kind} 扫描（后台执行，不阻塞对话），确认？"
            )
        return PermissionResult.allow("未知动作按只读处理")

    def user_facing_name(self, args: dict[str, Any] | None = None) -> str:
        return {
            "security": "安全状况",
            "threats": "威胁记录",
            "scan": "病毒扫描",
            "scan_status": "扫描进度",
            "save": "体检报告",
        }.get(str((args or {}).get("action") or "report"), "系统体检")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        action = str((args or {}).get("action") or "report")
        return "正在体检…" if action == "report" else None

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        action = str(args.get("action") or "report")

        if action == "security":
            status = defender.security_status()
            return ToolResult.ok(status.to_dict())

        if action == "threats":
            rows, err = defender.recent_threats()
            data: dict[str, Any] = {"threats": rows}
            if err:
                data["note"] = err
            data["hint"] = "Defender 被第三方杀软接管时读不到威胁记录，这属正常，不代表没有防护"
            return ToolResult.ok(data)

        if action == "scan":
            kind = str(args.get("scan_kind") or "quick")
            job, err = defender.start_scan(
                kind=kind,
                path=str(args.get("scan_path") or ""),
                allow_full=bool(_cfg(ctx, "maintenance_allow_full_scan", False)),
            )
            if job is None:
                return ToolResult.error(err or "启动扫描失败", retryable=False)
            return ToolResult.ok({
                "job": job.to_dict(),
                "hint": f"扫描已在后台启动，用 SystemHealth action=scan_status job_id={job.id} 查进度",
            })

        if action == "scan_status":
            job_id = str(args.get("job_id") or "")
            if job_id:
                return ToolResult.ok(defender.scan_status(job_id))
            return ToolResult.ok({"jobs": defender.list_jobs()})

        # report / save
        report = health.collect(
            timeout_s=float(_cfg(ctx, "maintenance_scan_timeout", 120)),
            in_use_dirs=_in_use_dirs(ctx),
        )
        markdown = health.render_markdown(report)

        if action == "save":
            out = str(args.get("out_file") or "")
            if not out:
                out = str(Path.home() / ".jarvis" / f"health-{time.strftime('%Y%m%d-%H%M')}.md")
            target = Path(out).expanduser()
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(markdown, encoding="utf-8")
            except OSError as exc:
                return ToolResult.error(f"报告写入失败：{exc}", retryable=False)
            return ToolResult.ok({
                "saved_to": str(target),
                "bytes": len(markdown.encode("utf-8")),
                "summary": report.to_dict(),
            })

        payload = report.to_dict()
        payload["markdown"] = markdown
        return ToolResult.ok(payload)


def register_maintenance_tools(registry: Any) -> int:
    """注册三个维护工具，返回注册数。

    ``DiskReport`` / ``SystemHealth`` 进核心（用户常问"磁盘为什么满了""电脑怎么样"）；
    ``DiskClean`` 延迟加载——它是唯一会动文件的一个，不该在每轮请求里占 schema。
    """
    count = 0
    for tool in (DiskReportTool(), SystemHealthTool(), DiskCleanTool()):
        try:
            registry.register(tool)
        except ValueError:
            continue   # 重名已注册（多次装配同一 registry）不重复加
        count += 1
    return count