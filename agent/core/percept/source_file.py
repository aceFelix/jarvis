"""文件守望感知源 —— "你登记的目录堆积了"（W2 第 2 步）。

**为什么必须自建快照**：`core/memory/file_state.py` 虽然名字像，但它是
「单文件 mtime 冲突缓存」（挂 `ToolContext.extra`、进程内、随会话消失，用途是
拒绝基于过时内容的编辑），没有目录扫描能力。本源自带目录 walk + 差分，是
PLAN 第 3.1 节那条失实前提的修正落地（详见该节注释）。

采集策略（三条都是刻意的保守选择）：

1. **只订阅白名单目录**，空名单=这个源即使被打开也不采任何东西。目录内**跳过**
   `.git`/`node_modules`/`__pycache__`/`.venv` 这类会秒变上千条的机器目录，
   并给单目录快照条数设上限（超出只记 `truncated`，不报错也不炸内存）。
2. **只对"新增"发声**：删除与修改是用户自己的动作，提醒它们是噪音；只有
   "东西在堆积"才是管家该管的事（§3.3 验收的"目录堆积"即此类）。
3. **首次扫描只建基线**：否则一打开源就把目录里现存的文件全当成"新来的"。
   基线持久化到 `~/.jarvis/percept/file_state.json`，所以 jarvis 关掉期间
   攒下的新文件，重启后会被作为一次真实堆积事件报出来（这才是要的价值）。

事件载荷只含**相对路径的文件名与计数**，不含文件内容、不含完整绝对路径，
`summary` 用目录名而非全路径，避免把用户目录结构带进播报与留痕文件。

@author aceFelix
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from agent.core.percept.event import PerceptEvent, SOURCE_FILE

#: 跳过的目录名（机器生成的巨目录，扫一次既慢又全是噪声）
SKIP_DIR_NAMES: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".idea", ".vscode", "dist", "build",
})

#: 默认忽略的文件后缀（下载中/临时文件/锁文件）
DEFAULT_IGNORE_SUFFIXES: tuple[str, ...] = (
    ".tmp", ".part", ".crdownload", ".partial", ".download",
    ".swp", "~lock", ".lock",
)

#: 事件里最多展示几个示例文件名（再多也没人看，还会撑大 payload）
SAMPLE_LIMIT = 5

_STATE_FILE = Path.home() / ".jarvis" / "percept" / "file_state.json"


def _entry_stat(path: Path) -> tuple[int, int]:
    """(mtime 取整, size)；取整是因为秒级足够判定"变没变"，且落盘更省。"""
    try:
        st = path.stat()
        return int(st.st_mtime), st.st_size
    except OSError:
        return 0, 0


def scan_dir(root: Path, *, max_entries: int = 2000,
             ignore_suffixes: tuple[str, ...] = ()) -> tuple[dict[str, list[int]], bool]:
    """递归扫一个目录。

    Returns:
        `({相对路径: [mtime, size]}, truncated)`。truncated 表示因命中
        `max_entries` 而提前停止遍历（此时快照不完整，事件需带该标记，
        否则用户会以为"只新增了两个"）。调用方必须拆两个值——
        上一版把 truncated 偷放在 dict 里当哨兵键，写测试时就会漏 pop。

    Args:
        root: 目录绝对路径。
        max_entries: 快照条目上限，命中即停止遍历。
        ignore_suffixes: 额外忽略后缀（小写结尾匹配）。
    """
    snap: dict[str, list[int]] = {}
    ignores = tuple(s.lower() for s in ignore_suffixes if s) + DEFAULT_IGNORE_SUFFIXES
    root = Path(root)
    if not root.exists():
        return snap, False
    stack = [root]
    truncated = False
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue  # 权限不足/网络盘断开：跳过该子目录而不是让整个源失败
        for entry in entries:
            if len(snap) >= max_entries:
                truncated = True
                break
            name = entry.name
            if entry.is_dir(follow_symlinks=False):
                if name in SKIP_DIR_NAMES or name.startswith("."):
                    continue
                stack.append(Path(entry.path))
                continue
            low = name.lower()
            # 隐藏文件（.DS_Store / .~lock.* / .gitignore）不入库：全是噪声
            if name.startswith(".") or any(low.endswith(sfx) for sfx in ignores):
                continue
            rel = str(Path(entry.path).relative_to(root)).replace("\\", "/")
            snap[rel] = list(_entry_stat(Path(entry.path)))
        if truncated:
            break
    return snap, truncated


class FileSource:
    """白名单目录守望器（实现总线的 `name` / `interval_s` / `collect()` 契约）。

    @author aceFelix
    """

    name = SOURCE_FILE

    def __init__(
        self,
        *,
        watch_dirs: Any = (),
        interval_s: float = 30.0,
        max_entries: int = 2000,
        grow_threshold: int = 5,
        ignore_suffixes: Any = (),
        state_path: Path | str | None = None,
        scanner: Callable[..., dict[str, list[int]]] | None = None,
    ) -> None:
        """
        Args:
            watch_dirs: 白名单目录（绝对路径或 ~ 展开路径）。
            grow_threshold: 单目录新增文件达此数才发 `dir_grew`（否则发 file_added）。
            state_path: 快照落盘路径，None 用 `~/.jarvis/percept/file_state.json`；
                测试传 tmp 路径。
            scanner: 注入的目录扫描函数，签名同 `scan_dir`（返回二元组），测试用。
        """
        self.interval_s = float(interval_s)
        self._dirs = [Path(str(d)).expanduser() for d in (watch_dirs or []) if str(d).strip()]
        self._max_entries = int(max_entries)
        self._grow = max(1, int(grow_threshold))
        self._ignores = tuple(str(s) for s in (ignore_suffixes or ()) if str(s))
        self._state_path = Path(state_path) if state_path else _STATE_FILE
        self._scan = scanner or scan_dir
        # dir → {relpath: [mtime, size]}；None 表示"还没建过基线"
        self._snaps: dict[str, dict[str, list[int]]] = {}
        self._load()

    # ---- 总线契约 ----

    def collect(self) -> list[PerceptEvent]:
        """逐目录差分，产出堆积/新增事件；顺带把新快照落盘。"""
        events: list[PerceptEvent] = []
        dirty = False
        for d in self._dirs:
            key = str(d)
            snap, truncated = self._scan(
                d, max_entries=self._max_entries, ignore_suffixes=self._ignores
            )
            prev = self._snaps.get(key)
            self._snaps[key] = snap
            if prev is None:
                dirty = True
                continue  # 首次只建基线，不把现存文件全报成"新增"
            added = sorted(set(snap) - set(prev))
            if not added:
                continue
            dirty = True
            events.append(self._event(d, added, truncated))
        if dirty:
            self._save()
        return events

    # ---- 事件构造 ----

    def _event(self, directory: Path, added: list[str], truncated: bool) -> PerceptEvent:
        """堆积事件：量大走 dir_grew（聚合），量小走 file_added（点名）。"""
        name = directory.name or str(directory)
        samples = [Path(p).name for p in added[:SAMPLE_LIMIT]]
        many = len(added) >= self._grow
        kind = "dir_grew" if many else "file_added"
        if many:
            summary = f"{name} 目录新增 {len(added)} 个文件（如 {('、'.join(samples))}）"
            # 0.55：要能过 W1 打扰门槛。预算侧 `percept:file` 权重 0.6，
            # 0.6×0.55=0.33 > 门槛 0.3；旧值 0.45 乘完 0.27 会被判“不值得打扰”，
            # 而目录堆积恰恰是本源唯一值得播报的那件事。
            hint = 0.55
        else:
            summary = f"{name} 新增文件：{('、'.join(samples))}"
            hint = 0.3   # 单个新文件不值得打断（只留痕）
        return PerceptEvent(
            source=SOURCE_FILE,
            kind=kind,
            payload={
                "summary": summary,
                "dir_name": name,
                "added": len(added),
                "samples": samples,
                "truncated": truncated,
            },
            salience_hint=hint,
            # 去重键只按目录 + 量级档：同一目录 5 分钟内反复堆积算一件事
            dedup_key=f"{name}:{kind}:{len(added) // max(1, self._grow)}",
        )

    def state(self) -> dict[str, Any]:
        """观察范围快照（导出与面板用）：目录、条目数、是否已建基线。"""
        return {
            "watch_dirs": [str(d) for d in self._dirs],
            "entries": {str(d): len(self._snaps.get(str(d), {})) for d in self._dirs},
            "baselined": {str(d): str(d) in self._snaps for d in self._dirs},
            "grow_threshold": self._grow,
            "state_path": str(self._state_path),
        }

    # ---- 快照持久化 ----

    def _load(self) -> None:
        """读回上次快照。文件损坏/结构不对一律当作"没基线"，不抛。"""
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        dirs = raw.get("dirs")
        if not isinstance(dirs, dict):
            return
        for key, snap in dirs.items():
            if isinstance(snap, dict):
                self._snaps[str(key)] = {
                    str(rel): list(val) for rel, val in snap.items()
                    if isinstance(val, (list, tuple)) and val
                }

    def _save(self) -> None:
        """原子落盘快照（临时文件 + os.replace，避免半截 JSON）。"""
        payload = {"version": 1, "dirs": self._snaps}
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(self._state_path.suffix + f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._state_path)
        except OSError:
            return  # 落盘失败只影响"重启后重复提醒一次"，不该让源停止工作
