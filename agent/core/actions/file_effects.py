"""本地文件写的「文件级撤销」—— 写前留底，事后按文件精确恢复。

## 为什么需要这一层（2026-10）

用户的原话是「文件变动之后，jarvis 不知道如何恢复为原来的状态」。查下来的实情
比"缺一个工具"更具体，是三条断链叠在一起：

1. ``FileWrite`` / ``FileEdit`` 的文件头注释明写「v0.1 不做备份，依赖外部
   git」—— 覆盖写即丢原文，用户没有任何退路；
2. 已有的 ``FileGuard``（``core/sandbox/file_guard.py``）**能快照、能回滚**，
   但只在 ``tools/bash.py`` 的沙箱高风险路径被调用，而且快照 ID 只写进了
   ``SandboxAuditor`` 的审计日志，``/undo`` 这扇门根本看不到它；
3. orchestrator 记的本地写条目引用的是**本轮 shadow git 检查点**，回滚粒度
   是整轮：用户只想撤这一个文件的改动，会连带把本轮所有改动一起退掉。

于是「改错了一个文件」只能靠外部 git 或用户自己回忆。本模块补的就是这段：
在写发生**之前**把原文件留一份底，并把快照登记成账本里可撤销的一条。

## 与既有零件的分工（刻意不重造轮子）

- 快照/回滚引擎直接用 ``FileGuard``：它的 ``Snapshot`` 数据结构、``existed``
  语义（原文件不存在 → 回滚 = 删除新建的）都已经写好了，重写一遍没有意义；
- 存储目录**独立**于沙箱快照（``~/.jarvis/file_snapshots``）：文件写是高频
  动作，若与 bash 沙箱共用 ``sandbox_snapshots`` 那 20 条配额，两次聊天就能把
  高风险命令的整目录兜底挤掉；
- 账本语义沿用 ``journal``，本模块只负责"造快照、给 undo_spec"，怎么撤销由
  ``journal._undo_files`` 落地。

## 体积红线

快照是**复制**，不是硬链接。一个 8GB 的目录被 copytree 一次就能把磁盘写满。
所以两道闸：单文件走 ``FileGuard`` 自己的 50MB 阈值；目录在交给它之前先用
``_measure`` 快速估算，超限直接放弃（宁可如实告诉用户"这个太大没留底"，也不
要为了"看起来能撤"把机器写爆）。

@author aceFelix
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

#: 快照目录（与沙箱快照分开，见模块头「存储目录独立」）
_SNAPSHOT_ROOT = "file_snapshots"

#: 单次快照的总体积上限缺省值（MB）。目录超过它就不备份 —— 复制而非硬链接，
#: 大目录一次 copytree 足以写满磁盘。可用 ``[actions].file_snapshot_mb`` 覆盖。
_DEFAULT_MAX_SNAPSHOT_MB = 64

#: 保留的快照条数缺省值。文件写比 bash 高频得多，20 条不够用（一天就把
#: 用户上午的改动滚掉了）。可用 ``[actions].file_snapshots`` 覆盖。
_DEFAULT_MAX_SNAPSHOTS = 50

#: 工具参数里可能是"本地路径"的键名。不同工具各叫各的，按此顺序取第一个。
_PATH_KEYS = ("file_path", "filePath", "path", "target", "destination", "dst", "output_path")

#: 一次调用里最多快照几个路径。批量工具（如多文件替换）会传一长串路径，
#: 逐个复制会拖慢本次工具调用；超过就只留前 N 个并如实标注。
_MAX_PATHS_PER_CALL = 20


def extract_targets(args: dict[str, Any] | None) -> list[str]:
    """从工具参数里推导这次调用会碰到的本地路径。

    只做**保守**推导：认不出来的参数一律当"没有路径"，宁可漏备份也不误解参数
    语义（把 ``new_string`` 里的路径字面量当成目标文件去复制，后果更糟）。

    Args:
        args: 工具调用参数。

    Returns:
        去重后的路径字符串列表（可能为空）。
    @author aceFelix
    """
    if not isinstance(args, dict):
        return []
    found: list[str] = []
    for key in _PATH_KEYS:
        raw = args.get(key)
        if isinstance(raw, str) and raw.strip():
            found.append(raw.strip())
    # 明确支持多路径形态（paths / files 是数组）
    for key in ("paths", "files", "sources"):
        raw = args.get(key)
        if isinstance(raw, (list, tuple)):
            found.extend(str(x).strip() for x in raw if isinstance(x, (str, os.PathLike)))
    seen: set[str] = set()
    out: list[str] = []
    for p in found:
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out[:_MAX_PATHS_PER_CALL]


def _measure(path: Path, limit_bytes: int) -> int:
    """快速估算 path 的体积，**超过 limit 立刻返回**（不做完整遍历）。

    为什么不直接用 ``FileGuard._dir_size``：那是完整 rglob，在一个几万文件的
    目录上光算大小就要好几秒 —— 而这里的调用点位于每次工具执行的必经之路上，
    为"估算体积"付这个代价不值得。这里用 scandir 逐层走，一超限就停。

    Args:
        path: 目标路径。
        limit_bytes: 上限（字节）；超过即视为超限。

    Returns:
        估算出的字节数（超限时返回一个必然大于 limit 的值）。
    @author aceFelix
    """
    try:
        if not path.is_dir():
            return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            if total > limit_bytes:
                                return total  # 超限即停：目的只是"判断是否超限"
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _settings_int(settings: Any, attr: str, default: int) -> int:
    """从 Settings 读一个配置数值，读不出/非法时退回缺省。"""
    try:
        value = int(getattr(settings, attr, default))
    except (TypeError, ValueError):
        return default
    return value


def snapshots_enabled(settings: Any = None) -> bool:
    """文件留底是否开启（``[actions].file_snapshots = 0`` 表示关闭）。

    为什么要一个显式的"0 = 关"，而不是只看 ``checkpoint_enabled``：两者是
    两件事 —— 检查点是**整轮** shadow git 快照，本模块是**单个文件**复制。
    用户可能想要前者而嫌后者占盘（或反过来）。把开关合在一起，等于让
    “我不想每轮存一份工作区快照” 顺手把 “改错一个文件能不能撤” 也关了。

    @author aceFelix
    """
    return _settings_int(settings, "actions_file_snapshots", _DEFAULT_MAX_SNAPSHOTS) > 0


def _snapshot_root() -> Path:
    """快照根目录。

    ``_SNAPSHOT_ROOT`` 允许是**绝对路径**：测试需要把留底写到临时目录而不碰
    用户真实的 ``~/.jarvis``，靠改这个模块变量就能做到，不必给每个函数加参数。

    @author aceFelix
    """
    root = Path(_SNAPSHOT_ROOT).expanduser()
    return root if root.is_absolute() else Path.home() / ".jarvis" / root


def guard_for(settings: Any = None):
    """构造文件撤销专用的 FileGuard（独立目录 + 独立配额）。

    Args:
        settings: 运行时配置；None 时全用缺省。

    Returns:
        FileGuard 实例。
    @author aceFelix
    """
    from agent.core.sandbox.file_guard import FileGuard

    return FileGuard(
        snapshot_dir=str(_snapshot_root()),
        max_snapshots=max(1, _settings_int(
            settings, "actions_file_snapshots", _DEFAULT_MAX_SNAPSHOTS,
        )),
    )


def snapshot_before_write(
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    read_only: bool,
    destructive: bool,
    settings: Any = None,
    reason: str = "",
    workdir: str = "",
) -> str:
    """在破坏性写发生**之前**给目标文件留底。

    调用点在工具执行之前、权限判定之后 —— 那时"要不要做这件事"已经确定，
    只差"做"。任何异常一律吞掉并返回空串：留底是辅助能力，不能因为它让用户的
    正常写入失败（同 ToolAuditor / diag 的惯例）。

    Args:
        tool_name: 工具名（只进日志/原因文案）。
        args: 工具调用参数。
        read_only: 该工具此次调用是否为只读。True 时不留底（读不改文件）。
        destructive: 该工具自己声明的"会改动/删除"。False 时不留底：
            ``is_read_only`` 默认就是 False，光靠它会让**任何带 file_path
            参数的工具**（含只查不动的那类）都去复制一遍文件。两道前置一起用，
            才只把留底花在真会改文件的调用上。
        settings: 运行时配置，用于读配额与体积上限。
        reason: 给人看的快照原因。
        workdir: 工具的工作目录，用于把**相对路径**解成绝对路径。

    Returns:
        快照 ID；空串表示"这次不需要或没能留底"。
    @author aceFelix
    """
    if read_only or not destructive:
        return ""
    if not snapshots_enabled(settings):
        return ""
    targets = extract_targets(args)
    if not targets:
        return ""

    limit_mb = _settings_int(settings, "actions_file_snapshot_mb", _DEFAULT_MAX_SNAPSHOT_MB)
    limit_bytes = limit_mb * 1024 * 1024

    backupable: list[str] = []
    for raw in targets:
        try:
            path = Path(raw).expanduser()
        except (OSError, ValueError):
            continue
        # 相对路径必须按**工具的工作目录**解（工具的 resolve_path 就是这么做的）。
        # 少了这一步会按进程 cwd 解析：轻则备份了无关文件（白复制），重则把
        # "确实存在的文件"记成不存在 —— 恢复时不是写回原文，而是把用户文件删掉。
        # @author aceFelix
        if not path.is_absolute() and workdir:
            path = Path(workdir) / path
        # 不存在也能备份：FileGuard 会记成 existed=False，回滚时把新建的删掉。
        # 这恰恰是「FileWrite 新建了一个不该建的文件」需要的补偿。
        if _measure(path, limit_bytes) > limit_bytes:
            continue
        backupable.append(str(path))
    if not backupable:
        return ""

    try:
        guard = guard_for(settings)
        return guard.snapshot(backupable, reason=reason or f"{tool_name} 写前留底")
    except Exception:
        return ""


def undo_spec_for(snap_id: str, paths: list[str] | None = None) -> dict[str, Any]:
    """构造 ``kind=files`` 的补偿计划。

    Args:
        snap_id: ``snapshot_before_write`` 返回的快照 ID。
        paths: 涉及路径（只用于展示，回滚本身靠快照清单）。

    Returns:
        可直接塞进 ``EffectRecord.undo_spec`` 的字典。
    @author aceFelix
    """
    return {"kind": "files", "snap_id": snap_id, "paths": list(paths or [])}


def restore(snap_id: str, settings: Any = None) -> tuple[bool, str]:
    """按快照恢复文件，返回 (是否成功, 说明)。

    说明文案要能回答用户的"到底恢复了什么"：成功时报恢复的文件数，失败时报
    具体原因（快照被配额滚掉 / 文件被占用），而不是一句"失败"。

    Args:
        snap_id: 快照 ID。
        settings: 运行时配置。

    Returns:
        (是否成功, 说明)。
    @author aceFelix
    """
    if not snap_id:
        return False, "这条记录没有留底快照，无法恢复文件"
    try:
        guard = guard_for(settings)
        info = guard.get_snapshot_info(snap_id)
        if info is None:
            return False, "留底快照已不在了（可能已被配额清理），文件无法恢复"
        if info.rolled_back:
            return False, "这份留底已经恢复过一次了"
        names = [Path(e.original_path).name for e in info.entries]
        ok = guard.rollback(snap_id)
        if not ok:
            return False, "恢复失败（文件可能正被其它程序占用）"
        head = f"已恢复 {len(names)} 个文件"
        if names:
            shown = "、".join(names[:5]) + ("……" if len(names) > 5 else "")
            head += f"：{shown}"
        return True, head
    except Exception as e:  # noqa: BLE001 - 辅助能力不得冒泡进工具收尾流程
        return False, f"恢复失败：{type(e).__name__}: {e}"[:200]


def list_entries(snap_id: str, settings: Any = None) -> list[dict[str, Any]]:
    """列出快照里的文件条目（供 ``/undo`` 展示"到底会恢复什么"）。

    文件清单**从快照清单读**而不是从 ``undo_spec.paths`` 读：后者是调用时
    写下的候选路径，前者才是真的复制下来的东西（路径可能被 resolve 过、
    也可能因为超限而没有全部留底）。拿后者做展示会报出"本来就没备份"的文件。

    Args:
        snap_id: 快照 ID。
        settings: 运行时配置。

    Returns:
        条目列表，每项含 ``path`` / ``existed`` / ``size``；快照不在时返回空表。
    @author aceFelix
    """
    if not snap_id:
        return []
    try:
        info = guard_for(settings).get_snapshot_info(snap_id)
    except Exception:
        return []
    if info is None:
        return []
    return [
        {
            "path": e.original_path,
            "existed": bool(e.existed),
            "size": int(e.size),
        }
        for e in info.entries
    ]


def describe_snapshot(snap_id: str, settings: Any = None) -> str:
    """给回执用的一句话：留底了哪几个文件、可以用什么命令撤。

    工具回执里带这句话，模型和用户都能当场知道"这一步改动能反悔"，
    不用等出事才去翻 ``/undo list``。

    @author aceFelix
    """
    if not snap_id:
        return ""
    try:
        info = guard_for(settings).get_snapshot_info(snap_id)
    except Exception:
        return ""
    if info is None:
        return ""
    names = [Path(e.original_path).name for e in info.entries]
    shown = "、".join(names[:3]) + ("……" if len(names) > 3 else "")
    return f"原内容已留底（{shown}），/undo 可恢复"
