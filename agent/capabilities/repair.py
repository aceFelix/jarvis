"""自我修复：把「权限大点」翻译成「有预算、可回滚、能复测」。

这是维护层头一次会**改环境**的动作（下内核、装 extras），所以它的重点从来不是
「要不要动手」，而是**动完之后怎么保证系统没受损失**。四件事叠在一起才敢默认开：

1. 白名单：只有四个动作能被代执行，超出的一律只给命令；
2. 预算门：体积决定要不要问，耗时决定前台还是后台，两档各管一件事；
3. 留痕：每条命令进 `actions.jsonl` 与沙箱审计，事后能回答「那天到底改了什么」；
4. 修完必复测：重跑同一探测，仍失败就**停手**，不许在同一次失败上连续换路子
   （本次 Chrome/Firefox 那 8 条 Bash 就是反面样本）。

一条与直觉相反但必须遵守的实现纪律：**修复动作不声明能被 `/undo` 撤回**。
`CheckpointManager` 快照的是工作区（shadow git of `workdir`），而这里写的全是
工作区外的东西（`%LOCALAPPDATA%\\ms-playwright\\`、venv 的 `site-packages`、
`~/.jarvis/assets/`）——检查点盖不到。把「我打了个检查点」当成「它能退回」就是
越权承诺，正是这整套机制要消灭的东西。所以记录一律 `reversible=False`，
`undo_spec.reason` 里写**精确的手工回滚法**。

@author aceFelix
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from importlib import util as _util
from pathlib import Path
from typing import Any

from agent.capabilities.registry import ReqState, get_meta
from agent.core.actions import STATE_APPLIED, STATE_FAILED, EffectRecord, get_journal

# 单个修复命令的硬超时下限。网络下载的真实耗时无法预估，所以给一个「明显是挂了」
# 的界限而不是「预计值」：超过它就直接停手报告，不重试。
_MIN_TIMEOUT_S = 180.0

# 每个动作允许写入的位置（**给人看的声明**，不是运行时强制）。
# 说清落点是为了让用户能判断「这东西装了我能不能干净地拿掉」，
# 也为了将来 S6 的文档能直接从这里取表。
@dataclass(frozen=True)
class ActionSpec:
    """一个白名单修复动作的声明。

    Attributes:
        title: 给人看的动作名。
        hard_cap_mb: 硬体积上限。**超过它只给命令、绝不代执行**（与 ASK 线是两档：
            ASK 线之上问一句，硬上限之上问也没用——那是要人自己决定的事）。
        auto_executable: False = 永远只输出命令。`fetch-model` 走不通是因为
            能力表里还没有可核对的 URL 清单，`config-fix` 则必须走 `ConfigSet`
            （那里已有权限判定与配置留痕），本层不该另起一条改配置的路。
        writes: 预期写入位置的声明文本。
        undo_hint: 该动作默认的回滚说法（会被具体计划里的实测路径覆盖）。
    """

    title: str
    hard_cap_mb: float
    auto_executable: bool
    writes: str
    undo_hint: str


REPAIR_ACTIONS: dict[str, ActionSpec] = {
    "fetch-browser": ActionSpec(
        title="下载 playwright 浏览器内核",
        # 无头壳 112.4MB + 完整内核 181.9MB（2026-11-03 HEAD 实测）合计约 296MB；
        # 400 是留出余量后的硬顶，超过它就不是「补一个自查工具」的量级了。
        hard_cap_mb=400.0,
        auto_executable=True,
        writes="%LOCALAPPDATA%\\ms-playwright\\",
        undo_hint="删除 ms-playwright 下本次新增的 revision 目录（回收请走 /clean）",
    ),
    "fetch-model": ActionSpec(
        title="下载 OCR / 视觉模型权重",
        hard_cap_mb=500.0,       # 估算值，未实测：所以它只给命令，不给代执行
        auto_executable=False,
        writes="~/.jarvis/assets/",
        undo_hint="整目录删除 ~/.jarvis/assets/<本次新增的子目录>",
    ),
    "pip-extras": ActionSpec(
        title="安装可选依赖 extras 组",
        hard_cap_mb=300.0,
        auto_executable=True,
        writes="当前 sys.executable 所在环境的 site-packages",
        undo_hint="pip uninstall 本次新增的包（包名见回执）",
    ),
    "config-fix": ActionSpec(
        title="修正配置项（如 model_type 标错）",
        hard_cap_mb=0.0,
        auto_executable=False,   # 必须走 ConfigSet：那里有权限判定与配置留痕
        writes="~/.jarvis/settings.toml / models.toml",
        undo_hint="用 /config 改回原值（ConfigSet 会记 before 值，可 /undo）",
    ),
}


@dataclass
class RepairPlan:
    """一次打算做的修复。**构造出来不等于会执行**——它先要过 `decide()`。"""

    action: str
    capability: str
    argv: tuple[str, ...]
    display: str            # 可直接复制的一行命令（给人看的形态）
    est_mb: float
    est_seconds: float
    reason: str             # 为什么要做：来自缺件探测的实测证据
    undo_hint: str
    note: str = ""

    @property
    def spec(self) -> ActionSpec:
        return REPAIR_ACTIONS[self.action]

    @property
    def timeout_s(self) -> float:
        """命令超时：预估耗时的 4 倍，但不低于下限。

        取 4 倍而不是 1.5 倍，是因为网络下载的预估只是量级判断；而一旦超时，
        本层的动作是**停手并如实报告**，不是重试——宁可多等一轮再认失败，
        也不要把「慢」误判成「坏」，那正是本次事故里被误读成「进程被回收」的同类错误。
        """
        return max(_MIN_TIMEOUT_S, self.est_seconds * 4)


@dataclass
class GateDecision:
    """预算门的结论。四种状态各对应一个明确后果。"""

    state: str          # auto / ask / refuse / manual
    reason: str
    background: bool = False


@dataclass
class RepairOutcome:
    """一次修复的完整结果（含复测结论）。命令层据此回执，不二次判断。"""

    plan: RepairPlan
    state: str          # done / failed / refused / needs_approval / manual / skipped
    detail: str = ""
    exit_code: int | None = None
    recheck: str = ""   # 复测后的能力状态（done/failed 才有）
    recheck_usable: bool = False
    journal_id: str = ""
    added_paths: tuple[str, ...] = ()

    @property
    def announced(self) -> bool:
        return self.state in ("done", "failed")


@dataclass
class _JournalMeta:
    """记账要用的宿主上下文（子代理/后台任务里可能全空，都能缺省）。"""

    workdir: str = ""
    session_name: str = ""
    permission_mode: str = "default"
    settings: Any = None


# ---------------------------------------------------------------------------
# 计划生成：哪个缺件、用哪个动作补
# ---------------------------------------------------------------------------

def _ms_playwright_root() -> Path:
    """playwright 内核的落点（与 probe 里同一套环境变量口径）。"""
    override = _env("PLAYWRIGHT_BROWSERS_PATH")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = _env("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _env(name: str) -> str:
    """读环境变量（剔空串）：拿不到就当没配，回到默认落点。"""
    return os.environ.get(name, "").strip()


def _revision_dirs(root: Path) -> set[str]:
    """当前已存在的 revision 目录名（用于算「本次新增了什么」）。"""
    try:
        return {p.name for p in root.iterdir() if p.is_dir()}
    except OSError:
        return set()


def plans_for_capability(cap_name: str) -> list[RepairPlan]:
    """按能力表的声明与实际缺件，生成候选修复计划。

    刻意只认**必需项的 missing**：可选项缺失不催装（否则每次自查都要装 mediapipe
    这种几百 MB 的东西，而用户可能根本不用手势识别）；`probe_failed` 更不生成计划
    ——不知道缺什么就动手，那不叫修复，叫猜。
    """
    meta = get_meta(cap_name)
    if meta is None or not meta.repair_actions:
        return []

    from agent.capabilities.probe import check

    report = check(cap_name)
    missing = [r for r in report.requirements if r.state == ReqState.MISSING and not r.optional]
    if not missing:
        return []

    plans: list[RepairPlan] = []
    for action_id in meta.repair_actions:
        spec = REPAIR_ACTIONS.get(action_id)
        if spec is None:
            continue
        if action_id == "fetch-browser":
            p = _browser_plan(meta.name, missing, spec)
        elif action_id == "pip-extras":
            p = _extras_plan(meta.extras_group, missing, spec)
        else:
            p = _advice_plan(action_id, spec, missing, meta.name, meta.extras_group)
        if p is not None:
            plans.append(p)
    return plans


def _is_playwright_gap(reqs) -> bool:
    return any(r.kind == "asset" and "playwright" in r.label.lower() for r in reqs)


def _browser_plan(cap: str, missing, spec: ActionSpec) -> RepairPlan | None:
    """补 playwright 内核。只装无头壳：交付自查要的是「能起来、能截帧」。"""
    if not _is_playwright_gap(missing):
        return None
    evidence = next(r.evidence for r in missing if r.kind == "asset")
    argv = (sys.executable, "-m", "playwright", "install", "chromium", "--only-shell")
    return RepairPlan(
        action="fetch-browser",
        capability=cap,
        argv=argv,
        display=_quote_join(argv),
        est_mb=112.4,           # 2026-11-03 HEAD 实测的无头壳体积
        est_seconds=120.0,      # 网速不可控，所以它注定走后台而不是前台阻塞
        reason=f"浏览器内核缺失（实测：{evidence}）",
        undo_hint=spec.undo_hint,
        note="装完不用重启：探测缓存带过期时间，下次用到该能力会重新确认",
    )


def _extras_plan(extras_group: str, missing, spec: ActionSpec) -> RepairPlan | None:
    """装 extras 组里的包。

    两个必须分清的坑：

    1. **extras 的包已装上、缺的只是内核**（本次事故就是这一种）：那时跑
       `pip install jarvis-agent[browser]` 什么也补不上，还看起来在做事。所以
       这里只认 `kind == "python"` 的缺件。
    2. **editable（源码树）环境里绝不能跑 `pip install jarvis-agent[...]`**：那会用
       PyPI 正式版直接覆盖源码树里未发布的修改（今天刚踩，`update.py` 已把这条
       写进注释）。该渠道下改为按 extras 清单直接装依赖包，绕开 jarvis-agent 这个名字。
    """
    if not extras_group:
        return None
    python_gaps = [r for r in missing if r.kind == "python"]
    if not python_gaps:
        return None
    if _util.find_spec("pip") is None:
        # 当前解释器里连 pip 都没有（uv tool 的隔离环境有可能这样）：不猜路子，
        # 退回去给命令让人自己执行。
        return None

    from agent import update as _update

    channel = _update.detect_installer()
    if channel in ("pip", "uv"):
        argv = (sys.executable, "-m", "pip", "install", f"jarvis-agent[{extras_group}]")
        hint = f"pip uninstall 后重装旧版本；本次装的是 jarvis-agent[{extras_group}]"
    else:
        from agent.capabilities.registry import extras_for_group

        pkgs = extras_for_group(extras_group)
        if not pkgs:
            return None
        argv = (sys.executable, "-m", "pip", "install", *pkgs)
        hint = f"pip uninstall {' '.join(pkgs)}"
    return RepairPlan(
        action="pip-extras",
        capability=next(r.label for r in python_gaps),
        argv=argv,
        display=_quote_join(argv),
        est_mb=60.0,            # 逐包差异大，取一个「量级」估算；真实值以回执为准
        est_seconds=90.0,
        reason="缺包：" + "、".join(r.label for r in python_gaps) + f"（渠道 {channel}）",
        undo_hint=hint,
        note=f"extras 组 {extras_group}；渠道 {channel} 决定命令形态",
    )


def _advice_plan(
    action_id: str, spec: ActionSpec, missing, cap: str, extras_group: str
) -> RepairPlan | None:
    """只给命令、不代执行的两类动作（fetch-model / config-fix）。"""
    if action_id == "fetch-model":
        gaps = [r for r in missing if r.kind == "asset" and not _is_playwright_gap([r])]
        if not gaps:
            return None
        # 本层不猜测下载地址：能力表里还没有可核对的 URL 清单，猜着下载等于
        # 从任意来源往本机写几百 MB。计划里如实登记「待清单」，命令给人工做法。
        return RepairPlan(
            action=action_id,
            capability=cap,
            argv=(),
            display=f"# 待能力表补上 URL 清单后自动下载；当前请人工放置：{gaps[0].label}",
            est_mb=0.0,
            est_seconds=0.0,
            reason=f"模型资产缺失：{gaps[0].evidence}",
            undo_hint=spec.undo_hint,
            note="只给建议，不代执行（落点 ~/.jarvis/assets/）",
        )
    if action_id == "config-fix":
        cfg_gaps = [r for r in missing if r.kind == "config"]
        if not cfg_gaps:
            return None
        r = cfg_gaps[0]
        return RepairPlan(
            action=action_id,
            capability=cap,
            argv=(),
            display=f"# 用 /config 或 ConfigSet 改：{r.label} → {r.evidence}",
            est_mb=0.0,
            est_seconds=0.0,
            reason=r.evidence,
            undo_hint=spec.undo_hint,
            note="必须走 ConfigSet：那里已有权限判定与 before 值留痕",
        )
    return None


def _quote_join(argv: tuple[str, ...]) -> str:
    """Windows 下带空格路径要加引号；这里不做 shell 转义，只生成**给人复制**的形态。"""
    parts = []
    for a in argv:
        parts.append(f'"{a}"' if (" " in a or "\\" in a) else a)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 预算门
# ---------------------------------------------------------------------------

def decide(
    plan: RepairPlan,
    settings: Any,
    *,
    confirmed: bool = False,
    away: bool = False,
    away_note: str = "",
) -> GateDecision:
    """能不能自动做。顺序刻意是「先问有没有权限，再问值不值得问」。"""
    if not bool(_get(settings, "repair_enabled", True)):
        return GateDecision("refuse", "自我修复总闸已关闭（[repair].enabled=false）")
    disabled = [str(x) for x in (_get(settings, "repair_disabled_actions", []) or [])]
    if plan.action in disabled:
        return GateDecision(
            "refuse", f"该动作已被单独关掉（[repair].disabled_actions 含 {plan.action}）"
        )
    if not plan.spec.auto_executable:
        return GateDecision("manual", plan.spec.title + "：本层只给命令，不代执行")
    if not plan.argv:
        return GateDecision("manual", "没有可执行的命令形态，按建议人工处理")

    hard_cap = plan.spec.hard_cap_mb
    if hard_cap > 0 and plan.est_mb > hard_cap:
        return GateDecision(
            "refuse",
            f"预计下载 {plan.est_mb:.1f}MB 超过该动作硬上限 {hard_cap:.0f}MB："
            "这已经不是「补一个自查工具」的量级，交回给你决定",
        )
    # 无人值守：任何有代价的修复都升回待批。沿用 W1 的口径，不另造一套判断。
    if away:
        return GateDecision(
            "needs_approval",
            f"无人值守（{away_note or '/away'}），修复动作升回待批：{plan.display}",
        )
    ask_line = float(_get(settings, "repair_ask_threshold_mb", 200.0))
    if plan.est_mb > ask_line and not confirmed:
        return GateDecision(
            "ask",
            f"预计下载 {plan.est_mb:.1f}MB 超过询问线 {ask_line:.0f}MB，确认执行？\n"
            f"  {plan.display}",
        )
    background = plan.est_seconds > float(_get(settings, "repair_background_over_s", 30.0))
    return GateDecision("auto", "预算内", background=background)


# ---------------------------------------------------------------------------
# 执行 + 留痕 + 复测
# ---------------------------------------------------------------------------

def execute_plan(plan: RepairPlan, meta: _JournalMeta) -> RepairOutcome:
    """跑一条修复命令：留痕、执行、复测，三件都不能少。"""
    before = _revision_dirs(_ms_playwright_root()) if plan.action == "fetch-browser" else set()

    outcome = RepairOutcome(plan=plan, state="failed")
    try:
        proc = subprocess.run(  # noqa: S603 - 命令由程序按白名单生成，不接受自由输入
            plan.argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=plan.timeout_s,
            stdin=subprocess.DEVNULL,
            check=False,  # 要看 returncode 区分「装失败」与「装上了但复测没过」
        )
        outcome.exit_code = proc.returncode
        outcome.detail = (proc.stdout.strip() or proc.stderr.strip())[-600:]
    except subprocess.TimeoutExpired:
        outcome.detail = f"超过 {plan.timeout_s:.0f}s 未返回，已放弃本次修复（不重试）"
    except OSError as exc:
        outcome.detail = f"无法启动修复命令：{type(exc).__name__}: {exc}"

    if plan.action == "fetch-browser":
        added = _revision_dirs(_ms_playwright_root()) - before
        outcome.added_paths = tuple(sorted(added))
        if added:
            plan.undo_hint = "本次新增：" + "、".join(
                str(_ms_playwright_root() / d) for d in added
            ) + "（回收请走 /clean，不自行删除）"

    # 复测：以探测结论为准，而不是以命令退出码为准。
    # 退出码 0 但内核仍不可用是真实存在的情况（下载完整校验失败等），
    # 只认退出码就等于把「命令跑完了」当成「能力恢复了」。
    from agent.capabilities.probe import deep_check

    report = deep_check(plan.capability, force=True)
    outcome.recheck = report.state
    outcome.recheck_usable = report.usable
    outcome.state = "done" if report.usable else "failed"
    if outcome.state == "failed" and not outcome.detail:
        outcome.detail = report.summary_line()

    _record(plan, outcome, meta)
    _audit(plan, outcome)
    return outcome


def _record(plan: RepairPlan, outcome: RepairOutcome, meta: _JournalMeta) -> None:
    """写 actions.jsonl 一条修复记录。

    `reversible=False` 是本模块最重要的一个字段值：这些写入全在工作区外，
    检查点盖不到，所以不假装 `/undo` 能撤。撤销方法在 `undo_spec.reason` 里写实。
    """
    try:
        rec = EffectRecord(
            tool=f"Repair:{plan.action}",
            args={"capability": plan.capability, "command": plan.display},
            preview=f"{plan.spec.title}：{plan.reason}"[:200],
            reversible=False,
            undo_spec={"kind": "none", "reason": plan.undo_hint},
            state=STATE_APPLIED if outcome.state == "done" else STATE_FAILED,
            error="" if outcome.state == "done" else outcome.detail[:200],
            workdir=meta.workdir,
            session_id=meta.session_name,
            permission_mode=meta.permission_mode,
        )
        get_journal().record(rec)
        outcome.journal_id = rec.id
    except Exception:  # noqa: BLE001 - 记账失败不能把已完成的修复变成失败状态
        outcome.journal_id = ""


def _audit(plan: RepairPlan, outcome: RepairOutcome) -> None:
    """每条修复命令都过沙箱审计器，事后可回答「那天到底改了什么」。"""
    try:
        from agent.core.sandbox.audit import SandboxAuditor

        SandboxAuditor().log_execution(
            command=plan.display,
            risk_level="MEDIUM",
            sandboxed=False,
            exit_code=outcome.exit_code,
            cwd=plan.capability,  # 复用这个字段标注归属能力，便于按能力筛审计
        )
    except Exception:  # noqa: BLE001 - 审计层不在（缺依赖/禁用）时不阻断修复
        pass


def repair_capability(
    cap_name: str,
    settings: Any,
    *,
    workdir: str = "",
    session_name: str = "",
    permission_mode: str = "default",
    confirmed: bool = False,
) -> list[RepairOutcome]:
    """对一个能力尝试修复，返回逐计划的结果。

    一次失败只试一条路：第一个计划跑过之后，无论成败都**不再试第二个**。
    在同一次缺件上连续换路子正是本次事故里最烧时间的部分（8 条 Bash 换来一套
    编造的机制）。真有第二个必要，由人看完回执后再发起。
    """
    plans = plans_for_capability(cap_name)
    if not plans:
        return []
    meta = _JournalMeta(
        workdir=workdir, session_name=session_name,
        permission_mode=permission_mode, settings=settings,
    )
    plan = plans[0]
    decision = decide(plan, settings, confirmed=confirmed, **_away_kwargs(settings))
    if decision.state == "auto":
        return [execute_plan(plan, meta)]
    if decision.state == "manual":
        return [RepairOutcome(plan=plan, state="manual", detail=decision.reason + "\n" + plan.display)]
    return [RepairOutcome(plan=plan, state=decision.state, detail=decision.reason)]


def _away_kwargs(settings: Any) -> dict[str, Any]:
    """取无人值守状态。取不到就按「有人在场」处理——那是既不新增阻塞也不新增询问的一侧。"""
    try:
        from agent.core.policy import get_policy_state

        away, note = get_policy_state().is_away(int(_get(settings, "away_idle_minutes", 0) or 0))
        return {"away": away, "away_note": note or ""}
    except Exception:  # noqa: BLE001 - 在场状态读不到时不拦修复
        return {"away": False}


def announce(outcome: RepairOutcome) -> tuple[str, str]:
    """(文字回执, 语音短句)。文字给细节，语音只给一句——播报不是念日志。"""
    plan = outcome.plan
    if outcome.state == "done":
        text = (
            f"已完成：{plan.spec.title}\n"
            f"  原因：{plan.reason}\n"
            f"  执行：{plan.display}\n"
            f"  复测：{outcome.recheck}（可用）"
        )
        if outcome.added_paths:
            text += "\n  新增：" + "、".join(outcome.added_paths)
        text += f"\n  怎么退：{plan.undo_hint}"
        if plan.note:
            text += f"\n  {plan.note}"
        voice = f"我已经补好{plan.spec.title}，现在可以用了。"
        return text, voice
    if outcome.state == "failed":
        text = (
            f"未能恢复：{plan.spec.title}\n"
            f"  执行：{plan.display}\n"
            f"  复测：{outcome.recheck}\n"
            f"  输出：{outcome.detail[:300]}\n"
            f"  已停手，不在同一处反复试。建议人工下一步：{plan.undo_hint}"
        )
        return text, f"我试过了{plan.spec.title}，但没能恢复，详情在文字回执里。"
    return (
        f"未执行修复（{outcome.state}）：{plan.spec.title}\n{outcome.detail}\n候选命令：{plan.display}",
        "",
    )


def history(limit: int = 10) -> list[dict[str, Any]]:
    """最近的修复记录（读 `actions.jsonl`，`/health caps --history` 用）。"""
    try:
        rows = get_journal().recent(limit=200)
    except Exception:  # noqa: BLE001 - 账本不可读时给空表，报告里如实标「读不到」
        return []
    out: list[dict[str, Any]] = []
    for rec in rows:
        if not rec.tool.startswith("Repair:"):
            continue
        out.append({
            "id": rec.id,
            "action": rec.tool.split(":", 1)[1],
            "when": time.strftime("%m-%d %H:%M", time.localtime(rec.ts)),
            "state": rec.state,
            "preview": rec.preview,
            "undo_hint": rec.undo_spec.get("reason", ""),
            "error": rec.error,
        })
        if len(out) >= limit:
            break
    return out


def _get(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, None) if settings is not None else None
    return default if value is None else value


__all__ = [
    "ActionSpec",
    "GateDecision",
    "REPAIR_ACTIONS",
    "RepairOutcome",
    "RepairPlan",
    "announce",
    "decide",
    "execute_plan",
    "history",
    "plans_for_capability",
    "repair_capability",
]
