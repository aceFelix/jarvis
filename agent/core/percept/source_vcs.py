"""仓库/CI 感知源 —— "你登记的仓库出状况了"（W2 第 3 步）。

一期实现范围（诚实收窄，见文末"未做"）：**本地 git 状态差分**，三种只读信号
——未提交堆积、有未推送提交、落后远端。命令只有 `git status` / `git stash list`
两条只读调用，不 fetch、不 pull、不动工作区，因此对仓库零副作用。

**未做（并写明原因）**：PLAN 第 3.1 节还列了"构建/CI 结果尾行"。它需要二选一：
要么依赖外部 `gh` CLI 与 GitHub 凭据（违背决策 1"只本机采集、不引入外部账号授权"），
要么由 jarvis 自己跑构建（那是主动执行，属 W3 兑现层的职责，不是感知层）。
因此本期的 vcs 源只做本机 git 可得到的事实；CI 结果作为需要重新定边界的独立项，
不在此假装已实现。

保守设计：
- **首采只建基线**，之后只在"状态变差的那一档"发事件（dirty 从少变多、
  ahead 从 0 变正、behind 从 0 变正），避免每次 `git status` 都提醒；
- 单个仓库路径无效 / 不是 git 仓库 / git 不可用 → **记为该仓库不可用，不抛异常**
  （配置未完成不是运行时故障，见 factory 的同一口径；且一个坏路径不该拖垮其他仓库）；
- 事件与 summary 只用目录名，不落绝对路径以外的仓库内容。

@author aceFelix
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from agent.core.percept.event import PerceptEvent, SOURCE_VCS

#: 单条 git 命令超时（秒）：大仓库 status 可能很慢，宁可不采也不卡住总线
COMMAND_TIMEOUT_S = 10.0

# `## main...origin/main [ahead 2, behind 1]` / `## main` / `## (no branch)`
_BRANCH_LINE = re.compile(r"^##\s+(?P<branch>[^\s.]+)(?:\.\.\.(?P<upstream>\S+))?")
_AHEAD_BEHIND = re.compile(r"\[ahead\s+(?P<ahead>\d+)(?:,\s+behind\s+(?P<behind>\d+))?\]")


def parse_status(stdout: str) -> dict[str, Any]:
    """解析 `git status --porcelain=v1 -b` 输出为 {branch, ahead, behind, dirty}。

    dirty 数 = 除首行分支说明外的条目数（含未跟踪文件，git 会把整个未跟踪目录
    折叠成一条，所以这是"变更规模"的合理近似，不是精确文件数）。
    """
    branch, ahead, behind, dirty = "", 0, 0, 0
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return {"branch": "", "ahead": 0, "behind": 0, "dirty": 0}
    head = lines[0]
    m = _BRANCH_LINE.match(head)
    if m:
        branch = (m.group("branch") or "").strip()
        mm = _AHEAD_BEHIND.search(head)
        if mm:
            ahead = int(mm.group("ahead") or 0)
            behind = int(mm.group("behind") or 0)
        dirty = len(lines[1:])
    else:
        dirty = len(lines)
    return {"branch": branch, "ahead": ahead, "behind": behind, "dirty": dirty}


def parse_stash_count(stdout: str) -> int:
    """`git stash list` 一行一个 stash，空输出=0。"""
    return len([ln for ln in (stdout or "").splitlines() if ln.strip()])


def _default_runner(argv: list[str], timeout: float) -> tuple[int, str]:
    """真跑一条命令，返回 (returncode, stdout)；失败/超时抛 CalledProcessError 等。"""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")  # 别让只读命令去抢 index.lock
    out = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, env=env,
        encoding="utf-8", errors="replace",
    )
    return out.returncode, out.stdout or ""


class VcsSource:
    """登记仓库的状态差分采集器（实现总线的 `name` / `interval_s` / `collect()`）。

    @author aceFelix
    """

    name = SOURCE_VCS

    def __init__(
        self,
        *,
        repos: Any = (),
        interval_s: float = 120.0,
        dirty_threshold: int = 8,
        stash_threshold: int = 3,
        runner: Callable[[list[str], float], tuple[int, str]] | None = None,
        timeout_s: float = COMMAND_TIMEOUT_S,
    ) -> None:
        """
        Args:
            repos: 登记过的仓库路径列表（只有这些仓库会被看，其余一概不问）。
            dirty_threshold: 未提交条目达此数才提醒"堆积"。
            stash_threshold: stash 数达此数才提醒"忘了捡"。
            runner: 命令执行器（测试注入用），签名 `(argv, timeout) -> (code, stdout)`。
        """
        self.interval_s = float(interval_s)
        self._repos = [Path(str(r)).expanduser() for r in (repos or []) if str(r).strip()]
        self._dirty_at = max(1, int(dirty_threshold))
        self._stash_at = max(1, int(stash_threshold))
        self._runner = runner or _default_runner
        self._timeout = float(timeout_s)
        # repo → 上次状态；缺键表示"还没建基线"
        self._last: dict[str, dict[str, Any]] = {}
        self.unavailable: dict[str, str] = {}  # repo → 不可用原因（面板可见）
        self._fatal: str = ""                   # 环境级不可用（如 git 未安装），不再空转

    # ---- 总线契约 ----

    def collect(self) -> list[PerceptEvent]:
        """遍历登记仓库，只在状态刚跨过提醒线时发事件。"""
        if self._fatal:
            return []
        events: list[PerceptEvent] = []
        for repo in self._repos:
            key = str(repo)
            state = self._read_state(repo)
            if state is None:
                continue
            prev = self._last.get(key)
            self._last[key] = state
            if prev is None:
                continue  # 首采建基线：否则一开源就把仓库现状当"变化"报一遍
            events.extend(self._diff(repo, prev, state))
        return events

    def state(self) -> dict[str, Any]:
        """采集范围与每仓库当前认知（导出/面板用）。"""
        return {
            "repos": [str(r) for r in self._repos],
            "snapshot": dict(self._last),
            "unavailable": dict(self.unavailable),
            "fatal": self._fatal,
            "dirty_threshold": self._dirty_at,
            "stash_threshold": self._stash_at,
        }

    # ---- 内部 ----

    def _read_state(self, repo: Path) -> dict[str, Any] | None:
        """读一个仓库的 {branch, dirty, ahead, behind, stash}；不可用时记原因返回 None。"""
        key = str(repo)
        if not repo.exists():
            self.unavailable[key] = "目录不存在"
            return None
        try:
            code, out = self._runner(
                ["git", "-C", key, "status", "--porcelain=v1", "-b"], self._timeout
            )
        except FileNotFoundError:
            # git 本身没装：这个源在本机没有存在意义。置致命标记而不是抛异常，
            # 避免面板把一个环境问题报成"源坏了"，也避免每 120s 空转一次。
            self._fatal = "git 命令不可用"
            self.unavailable[key] = self._fatal
            return None
        except Exception as e:  # 超时/权限/编码：跳过该仓库，不拖垮其他仓库
            self.unavailable[key] = f"git status 失败：{type(e).__name__}"
            return None
        if code != 0:
            self.unavailable[key] = f"不是可用的 git 仓库（退出码 {code}）"
            return None
        parsed = parse_status(out)
        self.unavailable.pop(key, None)  # 恢复可用就清掉旧原因
        stash = 0
        try:
            _, sout = self._runner(["git", "-C", key, "stash", "list"], self._timeout)
            stash = parse_stash_count(sout)
        except Exception:
            stash = 0  # stash 读不到不影响其他三个信号，降级为 0
        return {"branch": parsed["branch"], "dirty": parsed["dirty"],
                "ahead": parsed["ahead"], "behind": parsed["behind"], "stash": stash}

    def _diff(self, repo: Path, prev: dict[str, Any], cur: dict[str, Any]) -> list[PerceptEvent]:
        """比较两次快照，只在"刚跨过提醒线"的那一次发事件（不重复催同一状态）。"""
        name = repo.name or str(repo)
        events: list[PerceptEvent] = []

        if cur["dirty"] >= self._dirty_at and prev["dirty"] < self._dirty_at:
            events.append(self._event(
                name, "repo_dirty_grew",
                f"{name} 有 {cur['dirty']} 项未提交变更（分支 {cur.get('branch') or '未知'}）",
                0.5, {"count": cur["dirty"]},
            ))
        if cur["ahead"] > 0 and prev["ahead"] == 0:
            events.append(self._event(
                name, "repo_unpushed",
                f"{name} 有 {cur['ahead']} 个本地提交未推送", 0.55,
                {"ahead": cur["ahead"]},
            ))
        if cur["behind"] > 0 and prev["behind"] == 0:
            events.append(self._event(
                name, "repo_behind",
                f"{name} 落后远端 {cur['behind']} 个提交，可以考虑拉一下", 0.4,
                {"behind": cur["behind"]},
            ))
        if cur["stash"] >= self._stash_at and prev["stash"] < self._stash_at:
            events.append(self._event(
                name, "repo_stash_piled",
                f"{name} 攒了 {cur['stash']} 个 stash 没捡回来", 0.35,
                {"stash": cur["stash"]},
            ))
        return events

    def _event(self, name: str, kind: str, summary: str, hint: float,
               extra: dict[str, Any]) -> PerceptEvent:
        """统一构造事件：去重键按 仓库+种类，同种状态 5 分钟内只算一次。"""
        return PerceptEvent(
            source=SOURCE_VCS,
            kind=kind,
            payload=dict({"summary": summary, "repo_name": name}, **extra),
            salience_hint=hint,
            dedup_key=f"{name}:{kind}",
        )
