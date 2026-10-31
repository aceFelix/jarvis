"""系统维护 —— 无人值守自动清理（只动「删了完全无感」的那几条）。

## 为什么不是「risk=low 就自动清」

``risk`` 衡量的是**删了会怎样**，不是**删了用户会不会发现**。实测（2026-10-31
真实对话）把两者的差距摆得很清楚：

- ``recycle_bin`` 是 low，但用户刚删的文件就在里面——自动清等于替他做了「这些
  不要了」的决定
- ``user_temp`` 是 low，但正在运行的程序可能正拿着 Temp 里的文件
- ``thumbcache`` / ``shader_cache`` 是 low，但删了用户第一次浏览相册、开游戏会
  明显卡一下——有感知，只是轻微

所以自动许可**独立于 risk**，是规则里的 ``auto_ok`` 字段：默认 False，逐条人工
评估后显式打开（见 ``cleaners.toml`` 的字段说明）。这样新加一条规则不会因为漏标
风险档而被悄悄自动执行，是 fail-closed 的。

## 当前开了什么

只有两条，标准是「删了用户永远不会察觉」：

- ``crash_dumps``  程序崩溃的转储，只在给开发者提 bug 时有用
- ``wer_reports``  Windows 错误上报队列，删了不丢已发送的报告

## 为什么要有阈值

``min_mb`` 以下不建批次。零感知垃圾往往只有几 MB，天天建一个隔离区目录、
写一份 manifest、再 7 天后删一遍，成本高于收益，还会把 ``/clean list``
刷满噪音。实测本机 ``crash_dumps`` + ``wer_reports`` 常在 5 MB 以下。

## 和其他模块的关系

- 不动 ``cleaner.py``：自动清理就是「替用户按下同一个按钮」，走的还是
  :func:`agent.core.maintenance.cleaner.clean`，隔离区、protect、跨盘检测全部照旧
- 不动 ``rules.py`` 的强校验：``auto_ok`` 的两道反向闸（command 型 /
  requires_admin）在那里，进不到本模块
- 调度在 :mod:`agent.core.daemon.auto_clean_schedule`，本模块只管「清什么、
  要不要清」，一个同步函数，可被测、可被 CLI 直接调

@author aceFelix
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.core.maintenance.rules import CleanerRule, RuleBook, load_rulebook
from agent.core.maintenance.scanner import scan_all
from agent.core.maintenance import cleaner

logger = logging.getLogger("jarvis.maintenance.auto_clean")

#: 低于这个体积不建批次（MB）。见模块头注释「为什么要有阈值」。
DEFAULT_MIN_MB = 20.0

#: 两次自动清理的最小间隔（小时）。防止「一开机就清」式的高频触发。
DEFAULT_INTERVAL_H = 20.0

#: 自动清理的运行状态（上次跑的时间），与 monitor_history 分开放：
#: 那个记的是磁盘读数，这个记的是「动作何时发生过」，混存后不好各自演进。
STATE_FILE = "maintenance_state.json"


def _state_path() -> Path:
    return Path.home() / ".jarvis" / STATE_FILE


def read_state() -> dict[str, Any]:
    """读运行状态；文件坏了当作空状态。

    刻意不抛错：状态只用来决定「这次要不要跑」，读不出来最多多跑一次清理，
    而自动清理本身是幂等且零感知的——为它崩掉启动流程完全不划算。
    """
    try:
        raw = _state_path().read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(**updates: Any) -> None:
    """合并写入运行状态（不是覆盖）。"""
    data = read_state()
    data.update(updates)
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError as e:  # pragma: no cover - 磁盘满/权限，不该影响清理本身
        logger.warning("自动清理状态写盘失败: %s", e)


def auto_rule_ids(book: RuleBook) -> list[str]:
    """挑出允许自动清理的规则 id。

    **只认 ``auto_ok``**，不看 ``risk``（原因见模块头注释）。``unaddressable``
    的也去掉——路径都不存在的规则没必要进批次。
    """
    out: list[str] = []
    for rule in book.rules:
        if not getattr(rule, "auto_ok", False):
            continue
        if rule.unaddressable:
            continue
        out.append(rule.id)
    return out


def explain_rules(book: RuleBook) -> list[dict[str, Any]]:
    """给 ``/clean --auto-status`` 一类用途：列出候选与各自为何没被选中。

    逐条给原因而不是只报选中的那几条——用户想知道「为什么这条不清」时，
    答案不该是「你得自己去读 cleaners.toml」。
    """
    rows: list[dict[str, Any]] = []
    for rule in book.rules:
        ok = bool(getattr(rule, "auto_ok", False))
        if ok and rule.unaddressable:
            reason = f"路径不存在（{rule.unaddressable}）"
            ok = False
        elif ok:
            reason = "允许自动清理"
        elif rule.is_command:
            reason = "命令型规则从不代执行"
        elif rule.requires_admin:
            reason = "需要管理员权限，后台进程提不了权"
        else:
            reason = "未标记 auto_ok（删了用户可能察觉）"
        rows.append({"id": rule.id, "name": rule.name, "ok": ok, "reason": reason})
    return rows


@dataclass
class AutoCleanResult:
    """一次自动清理的结论。

    刻意区分 ``ran`` 与 ``reclaimed_mb``：没跑（低于阈值/没有候选）和跑了但
    没清出东西，是两件需要不同对待的事——前者不该写状态、不该通知，
    后者要记一笔说明规则是不是失效了。
    """

    ran: bool = False
    reason: str = ""
    reclaimed_mb: float = 0.0
    batch_id: str = ""
    rule_ids: list[str] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    purged_batches: int = 0
    elapsed_s: float = 0.0

    def summary(self) -> str:
        """一行给人看的中文摘要（日志与播报共用）。"""
        if not self.ran:
            return f"本次未清理：{self.reason}"
        parts = [f"自动清理 {self.reclaimed_mb:.1f} MB"]
        if self.rule_ids:
            parts.append("、".join(self.rule_ids))
        if self.skipped:
            parts.append(f"{len(self.skipped)} 项跳过")
        if self.purged_batches:
            parts.append(f"顺带清掉 {self.purged_batches} 个过期批次")
        return "，".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "reclaimed_mb": round(self.reclaimed_mb, 1),
            "batch_id": self.batch_id,
            "rule_ids": list(self.rule_ids),
            "skipped": list(self.skipped),
            "purged_batches": self.purged_batches,
            "elapsed_s": round(self.elapsed_s, 2),
            "summary": self.summary(),
        }


def run_auto_clean(
    book: RuleBook | None = None,
    *,
    max_risk: str = "low",
    keep_days: int = 7,
    min_mb: float = DEFAULT_MIN_MB,
    timeout_s: float = 30.0,
    quarantine_root: str | os.PathLike[str] | None = None,
) -> AutoCleanResult:
    """执行一次自动清理。同步函数，调用方决定放前台还是线程里。

    Args:
        book: 规则库；None 则加载内置库。
        max_risk: 风险档上限，再叠一层 ``cleaner.clean`` 已有的跳过逻辑。
            ``auto_ok`` 与 ``max_risk`` 是两道独立的闸，任何一道不放行都不清。
        keep_days: 隔离区保留天数。
        min_mb: 低于这个可回收量直接跳过，不建批次。
        timeout_s: 扫描超时（只扫自动清理的那几条，通常远小于全局扫描）。
        quarantine_root: 隔离区根目录；None 用 ``~/.jarvis/quarantine``。
            存在这个参数是为了让测试能完全离开真实目录——一个会移用户文件的
            函数如果没法指认落点，测试就只能拿真实磁盘当靶场。

    Returns:
        :class:`AutoCleanResult`。**不抛异常**——自动清理在后台线程里跑，
        抛出去只会让调度器把它当故障；失败信息进 ``reason``。
    """
    started = time.time()
    result = AutoCleanResult()
    rulebook = book if book is not None else load_rulebook()

    ids = auto_rule_ids(rulebook)
    result.rule_ids = list(ids)
    if not ids:
        result.reason = "规则库里没有标记 auto_ok 的规则"
        result.elapsed_s = time.time() - started
        return result

    # 先扫一遍算总量：低于阈值就没必要建隔离区批次
    try:
        scan = scan_all(
            rulebook, scope=ids, timeout_s=timeout_s,
            max_dir_gb=1.0, min_size_mb=0.0,
        )
    except Exception as e:  # noqa: BLE001 - 扫描失败不该把调度线程带下去
        result.reason = f"扫描失败：{type(e).__name__}: {e}"
        result.elapsed_s = time.time() - started
        logger.warning("自动清理扫描失败: %s", e)
        return result

    size_mb = scan.reclaimable_bytes / 1024 / 1024
    if size_mb < min_mb:
        result.reason = (
            f"可回收 {size_mb:.1f} MB，低于阈值 {min_mb:.0f} MB，不值得建批次"
        )
        result.elapsed_s = time.time() - started
        return result

    try:
        cleaned = cleaner.clean(
            rulebook, ids, max_risk=max_risk, quarantine_root=quarantine_root,
        )
    except Exception as e:  # noqa: BLE001
        result.reason = f"清理失败：{type(e).__name__}: {e}"
        result.elapsed_s = time.time() - started
        logger.warning("自动清理执行失败: %s", e)
        return result

    payload = cleaned.to_dict()
    result.ran = True
    result.reclaimed_mb = float(payload.get("reclaimed_mb") or 0.0)
    result.batch_id = str(payload.get("batch_id") or "")
    result.skipped = list(payload.get("skipped") or [])
    result.reason = "已执行"

    try:
        result.purged_batches = cleaner.purge_expired(
            keep_days, quarantine_root=quarantine_root
        )
    except OSError as e:  # pragma: no cover
        logger.warning("隔离区过期清理失败: %s", e)

    result.elapsed_s = time.time() - started
    write_state(last_auto_clean_ts=time.time(), last_batch_id=result.batch_id)
    logger.info("自动清理完成: %s", result.summary())
    return result


def is_due(now: float | None = None, interval_h: float = DEFAULT_INTERVAL_H) -> bool:
    """距上次自动清理是否已经够久。没跑过 = 到点。"""
    ts = read_state().get("last_auto_clean_ts")
    if not isinstance(ts, (int, float)):
        return True
    gap_h = ((now if now is not None else time.time()) - float(ts)) / 3600
    return gap_h >= interval_h


def rules_for_ids(book: RuleBook, ids: list[str]) -> list[CleanerRule]:
    """按 id 取规则对象（给报告层用，缺的静默跳过）。"""
    out: list[CleanerRule] = []
    for rid in ids:
        rule = book.by_id(rid)
        if rule is not None:
            out.append(rule)
    return out
