"""磁盘扫描器 —— 把规则库变成「我能腾出多少空间」的可读报告。

## 三个必须守住的性能闸

扫描是**只读**的，但只读也可能把机器拖死：``C:\\Windows.old`` 动辄十几万个文件，
``SoftwareDistribution\\Download`` 在补丁日之后同样吓人。所以这里有三道闸：

1. **总超时**（``scan_timeout``，默认 120s）。超时不是失败，是"扫到哪算哪"并
   在报告里标明 ``truncated``——用户看到"至少 8.2 GB"比看到"扫描失败"有用得多。
2. **单条规则体积上限**（``scan_max_dir_gb``，默认 50GB）。到顶就停止累加并标记，
   避免一条规则吃满整个超时预算、让后面十几条规则一条都扫不到。
3. **不跟随符号链接**。``Temp`` 里常见指向别处的链接，跟进去会重复计数，
   更糟的是可能绕到 ``C:\\Windows`` 上，把"1 MB 的临时文件"报成"20 GB"。

## 为什么自己写而不是用 ``Path.rglob``

``rglob`` 每个条目都要构造 ``Path`` 对象并额外 ``stat``，在十万级文件上比
``os.scandir`` 慢一个量级。``scandir`` 的 ``DirEntry`` 在 Windows 上直接复用
``FindFirstFile`` 返回的元数据，``is_dir()`` / ``stat()`` 多数情况不落第二次系统调用。
不用 Everything 那套读 MFT 的方案：要管理员权限 + 自研 NTFS 解析，超出「编排层」的边界。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent.core.maintenance.rules import CleanerRule, RuleBook, match_any

#: 一条规则扫不到路径时给的说明（区分"这台机器没有"与"扫描失败"）
REASON_ABSENT = "路径不存在"

#: 目录被判定为「本会话用过」的时间窗（秒）：一小时内改过的目录算热
IN_USE_WINDOW_S = 3600


@dataclass
class ScanItem:
    """单条规则的扫描结果。"""

    id: str
    name: str
    category: str
    risk: str
    requires_admin: bool
    desc: str
    size_bytes: int = 0
    file_count: int = 0
    #: 命中 protect 而跳过的文件数（报告要给出来：用户才知道"没删干净"是故意的）
    protected_count: int = 0
    last_modified: str = ""
    #: 非空表示这条规则在这台机器上没法落地（路径不存在/平台不支持）
    unaddressable: str = ""
    #: 非空表示扫描被闸截断（超时/体积上限），大小只是下限
    truncated: str = ""
    #: 本次会话最近改动过（条件化建议用）
    in_use_by_session: bool = False

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / 1024 / 1024, 1)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size_mb": self.size_mb,
            "size_human": human_size(self.size_bytes),
            "file_count": self.file_count,
            "protected_count": self.protected_count,
            "category": self.category,
            "risk": self.risk,
            "requires_admin": self.requires_admin,
            "desc": self.desc,
            "last_modified": self.last_modified,
            "unaddressable": self.unaddressable,
            "truncated": self.truncated,
            "in_use_by_session": self.in_use_by_session,
        }


@dataclass
class DirEntrySize:
    """某个目录下一级子目录的体积（"空间被谁占了"用）。"""

    path: str
    name: str
    size_bytes: int = 0
    file_count: int = 0
    truncated: str = ""

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / 1024 / 1024, 1)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "size_mb": self.size_mb,
            "size_human": human_size(self.size_bytes),
            "file_count": self.file_count,
            "truncated": self.truncated,
        }


@dataclass
class DriveInfo:
    """一块盘的容量与趋势。"""

    mount: str
    total_gb: float
    free_gb: float
    used_percent: float
    #: 近 N 天净占用增长（GB，正数=在涨）。样本不足时为 None——不要编一个 0，
    #: 那会让用户以为"没有在涨"，而真相是"还不知道"。
    trend_7d_gb: float | None = None

    def trend_text(self) -> str:
        """把符号翻成人话。

        实测（2026-10-31 本机）：``trend_7d_gb=-1.58`` 被模型读成「近 7 天又减少了
        1.58 GB」，方向刚好相反——负数其实是**可用空间变多了**。裸数字对人和模型
        都不友好（字段名没说是"已用增"还是"可用减"），所以这里直接把结论写出来，
        不让消费方去推符号。样本不足时返回空串。
        """
        if self.trend_7d_gb is None:
            return ""
        if self.trend_7d_gb >= 0:
            return f"近 7 天占用增长 {self.trend_7d_gb:.2f} GB（已用变多）"
        return f"近 7 天占用下降 {abs(self.trend_7d_gb):.2f} GB（已用变少、可用变多）"

    def to_dict(self) -> dict:
        return {
            "mount": self.mount,
            "total_gb": self.total_gb,
            "free_gb": self.free_gb,
            "used_percent": self.used_percent,
            "trend_7d_gb": self.trend_7d_gb,
            "trend_7d_text": self.trend_text(),
        }


@dataclass
class ScanResult:
    """一次完整扫描的产物（报告层与工具层都吃它）。"""

    items: list[ScanItem] = field(default_factory=list)
    drives: list[DriveInfo] = field(default_factory=list)
    #: 被规则库拒绝的条目 + 原因
    rejected: list[tuple[str, str]] = field(default_factory=list)
    elapsed_s: float = 0.0
    timed_out: bool = False

    def by_id(self, item_id: str) -> ScanItem | None:
        for it in self.items:
            if it.id == item_id:
                return it
        return None

    @property
    def reclaimable_bytes(self) -> int:
        """可回收总量：只算 low/medium（high 默认不勾，不该混进"能省多少"）。"""
        return sum(i.size_bytes for i in self.items if i.risk != "high")


def human_size(n: int | float) -> str:
    """字节转人话（1.5 GB / 820 MB）。"""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _fmt_mtime(ts: float) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except (OSError, ValueError):
        return ""


class _Deadline:
    """超时闸。``expired`` 一旦置位就不再翻回来。"""

    __slots__ = ("_at", "_hit")

    def __init__(self, seconds: float) -> None:
        self._at = time.monotonic() + max(0.0, seconds)
        self._hit = False

    @property
    def expired(self) -> bool:
        if not self._hit and time.monotonic() >= self._at:
            self._hit = True
        return self._hit

    @property
    def hit(self) -> bool:
        return self._hit


def _walk_size(
    root: str,
    *,
    include: tuple[str, ...],
    protect: tuple[str, ...],
    deadline: _Deadline,
    max_bytes: int,
    budget_left: int,
) -> tuple[int, int, int, float, str]:
    """递归统计一个目录的体积。

    Returns:
        ``(字节数, 文件数, 保护跳过数, 最新 mtime, 截断原因)``
    """
    total = 0
    count = 0
    protected = 0
    newest = 0.0

    stack = [root]
    while stack:
        if deadline.expired:
            return total, count, protected, newest, "扫描超时"
        if total >= max_bytes:
            return total, count, protected, newest, f"超过单条规则上限 {human_size(max_bytes)}"
        if budget_left <= 0:
            return total, count, protected, newest, "达到文件数上限"
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    budget_left -= 1
                    if budget_left <= 0:
                        return total, count, protected, newest, "达到文件数上限"
                    try:
                        # 不跟随符号链接：跟进去会重复计数，甚至绕到别的大目录上
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            # include 只约束文件；目录一律递归进去
                            stack.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if include and not match_any(entry.name, include):
                            continue
                        if protect and match_any(entry.name, protect):
                            protected += 1
                            continue
                        st = entry.stat(follow_symlinks=False)
                        total += st.st_size
                        count += 1
                        if st.st_mtime > newest:
                            newest = st.st_mtime
                    except (PermissionError, OSError):
                        # 被占用/无权限：静默跳过。这里抛异常会让整条规则作废，
                        # 而"Temp 里有几个删不掉的文件"是常态，不是错误。
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            continue
    return total, count, protected, newest, ""


def scan_rule(
    rule: CleanerRule,
    *,
    deadline: _Deadline,
    max_bytes: int,
    file_budget: int = 400_000,
) -> ScanItem:
    """扫描单条规则（``kind=paths``）。

    命令型规则不走这里（见 :func:`describe_commands`）：它们的体积要跑 DISM/VSS
    才知道，属于"深扫"，由工具层按需触发。
    """
    item = ScanItem(
        id=rule.id, name=rule.name, category=rule.category, risk=rule.risk,
        requires_admin=rule.requires_admin, desc=rule.desc,
    )
    if rule.is_command:
        item.unaddressable = "命令型规则不参与常规扫描（需要 --deep）"
        return item

    hits = 0
    newest = 0.0
    for path in rule.paths:
        if not os.path.exists(path):
            continue
        hits += 1
        got, cnt, prot, mt, trunc = _walk_size(
            path, include=rule.include, protect=rule.protect,
            deadline=deadline, max_bytes=max_bytes, budget_left=file_budget,
        )
        item.size_bytes += got
        item.file_count += cnt
        item.protected_count += prot
        if mt > newest:
            newest = mt
        if trunc and not item.truncated:
            item.truncated = trunc
        if deadline.expired:
            break

    if hits == 0:
        item.unaddressable = REASON_ABSENT
    item.last_modified = _fmt_mtime(newest)
    item.in_use_by_session = bool(newest and (time.time() - newest) <= IN_USE_WINDOW_S)
    return item


def scan_all(
    book: RuleBook,
    *,
    scope: list[str] | None = None,
    timeout_s: float = 120.0,
    max_dir_gb: float = 50.0,
    min_size_mb: float = 0.0,
) -> ScanResult:
    """按规则库扫描全部（或指定）条目。

    Args:
        book: 已加载的规则库。
        scope: 只扫这些 id；留空扫全部。
        timeout_s: 总超时。到点返回已扫到的部分并标 ``timed_out``。
        max_dir_gb: 单条规则的体积上限。
        min_size_mb: 小于此体积的条目不进报告（剔除噪声，但规则本身仍在）。

    Returns:
        :class:`ScanResult`
    """
    started = time.monotonic()
    deadline = _Deadline(timeout_s)
    max_bytes = int(max_dir_gb * 1024**3)
    wanted = set(scope) if scope else None

    result = ScanResult(rejected=list(book.rejected))
    for rule in book.rules:
        if wanted is not None and rule.id not in wanted:
            continue
        if deadline.expired:
            result.timed_out = True
            break
        item = scan_rule(rule, deadline=deadline, max_bytes=max_bytes)
        if item.size_bytes / 1024 / 1024 < min_size_mb and not item.unaddressable:
            continue
        result.items.append(item)

    result.drives = drive_infos()
    result.timed_out = result.timed_out or deadline.hit
    result.elapsed_s = round(time.monotonic() - started, 2)
    return result


def describe_commands(book: RuleBook) -> list[dict]:
    """列出命令型规则（WinSxS / 卷影副本 / 休眠文件 / 磁盘清理）。

    这些不参与常规扫描：一次 DISM 分析要几十秒且要管理员，塞进 /disk 会让
    整个命令卡住。它们以"需单独评估"的形式出现在报告里，由用户显式触发。

    ``clean_cmd`` 一并返回："这条规则意味着该敲什么命令"是用户看完清单后
    最想知道的一件事，缺了它报告等于只报问题不给路。
    """
    out: list[dict] = []
    for rule in book.rules:
        if not rule.is_command:
            continue
        out.append({
            "id": rule.id,
            "name": rule.name,
            "risk": rule.risk,
            "requires_admin": rule.requires_admin,
            "desc": rule.desc,
            "clean_cmd": " ".join(rule.clean_cmd),
            # 有无 analyze_cmd 直接决定用户能不能先估算收益：
            # DISM/VSS/powercfg 都能先分析，cleanmgr 没有分析模式。
            "note": (
                "收益需执行分析命令才知道；命令不代执行，不进隔离区即无还原入口"
                if rule.analyze_cmd
                else "无分析模式，收益跑完才知道；命令不代执行，不进隔离区即无还原入口"
            ),
        })
    return out


def drive_infos() -> list[DriveInfo]:
    """枚举本机固定盘并取容量。不依赖 psutil（``shutil.disk_usage`` 是标准库）。"""
    out: list[DriveInfo] = []
    if os.name == "nt":
        import string

        mounts = [
            f"{letter}:\\"
            for letter in string.ascii_uppercase
            if os.path.exists(f"{letter}:\\")
        ]
    else:
        mounts = ["/"]
    trend = disk_trend_7d_gb()
    for mount in mounts:
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        total_gb = usage.total / 1024**3
        if total_gb < 0.01:
            continue
        out.append(DriveInfo(
            mount=mount.rstrip("\\") or mount,
            total_gb=round(total_gb, 1),
            free_gb=round(usage.free / 1024**3, 1),
            used_percent=round(usage.used / usage.total * 100, 1),
            trend_7d_gb=trend if mount.startswith("C:") or mount == "/" else None,
        ))
    return out


def disk_trend_7d_gb() -> float | None:
    """近 7 天系统盘净占用增长（GB，正数=占用在涨）。

    数据来自 ``SystemMonitor`` 每日写入的 ``~/.jarvis/monitor_history.json``。
    这是**别的管家软件答不了的问题**——它们只能看"现在多大"，说不出"为什么在涨"。
    样本不足 3 天返回 None：编一个 0 会让人误以为没在涨。
    """
    path = Path.home() / ".jarvis" / "monitor_history.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    points: list[tuple[str, float]] = []
    for row in rows[-7:]:
        if not isinstance(row, dict):
            continue
        free = row.get("disk_free_gb")
        day = row.get("date")
        if isinstance(free, (int, float)) and isinstance(day, str):
            points.append((day, float(free)))
    if len(points) < 3:
        return None
    # 首尾差即为这段时间的净变化：剩余空间变少 = 占用增长
    return round(points[0][1] - points[-1][1], 2)


def top_dirs(
    root: str,
    *,
    limit: int = 15,
    timeout_s: float = 45.0,
    max_dir_gb: float = 50.0,
    min_size_mb: float = 100.0,
) -> list[DirEntrySize]:
    """统计某个目录下**一级**子目录各自的体积，按大小倒序。

    ## 为什么需要这个（白名单清理管不到的那一半）

    实测本机（2026-10-31）：清理规则只能回收 27 MB，而 C 盘只剩 14.6 GB。也就是说
    「能清的垃圾」和「占了多少空间」完全是两个量级的问题——用户真正想问的通常是
    **“空间被谁占了”**，而不是“垃圾目录里有多少”。只回答前者会让人以为工具失灵。

    本函数就是这个补位：不判断“能不能删”，只告诉“谁占了多少”。删不删由用户定，
    因为 ``node_modules`` / ``target`` / 虚拟环境这些目录一旦被自动清掉，代价远大于收益
    （PLAN 第八节坑 1）。

    Args:
        root: 要分析的根目录（如用户目录）。
        limit: 返回前 N 个子目录。
        timeout_s: 总超时。超时后已统计完的子目录照常返回并标记。
        max_dir_gb: 单个子目录体积到顶即停止累加。
        min_size_mb: 小于此体积的不返回（剔除噪声）。

    Returns:
        按体积倒序的 :class:`DirEntrySize` 列表。
    """
    if not root or not os.path.isdir(root):
        return []
    deadline = _Deadline(timeout_s)
    max_bytes = int(max_dir_gb * 1024**3)
    out: list[DirEntrySize] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        full = os.path.join(root, name)
        try:
            if os.path.islink(full):
                continue
            if not os.path.isdir(full):
                continue
        except OSError:
            continue
        size, count, _prot, _mt, trunc = _walk_size(
            full, include=(), protect=(),
            deadline=deadline, max_bytes=max_bytes, budget_left=400_000,
        )
        if size / 1024 / 1024 < min_size_mb:
            continue
        out.append(DirEntrySize(
            path=full, name=name, size_bytes=size,
            file_count=count, truncated=trunc,
        ))
        if deadline.expired:
            break
    out.sort(key=lambda d: d.size_bytes, reverse=True)
    return out[:limit]