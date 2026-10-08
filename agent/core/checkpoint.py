"""消息级回溯检查点 —— shadow git 快照与回滚。

每轮对话开始前，对当前工作目录打一个 git 检查点（commit）；用户发现某条
消息发错时，可把对话回退到该消息之前，同时把工作区文件恢复到该检查点的
状态（即"这条消息引发的修改全部撤销"）。

设计（与 FileGuard 的分工）:
- FileGuard（core/sandbox/file_guard.py）：高风险 bash 命令执行前的整目录
  复制兜底，面向"这一次操作别把文件搞坏"；
- 本模块：面向"消息"粒度的回溯，每一轮一个检查点，回滚 = 恢复到发消息前
  的世界。两者互不依赖。

实现：shadow git —— 在 ~/.jarvis/checkpoints/<工作目录指纹>/git 建一个独立
git 目录，通过 --git-dir/--work-tree 参数把用户工作目录当作它的工作树。
存储键按 workdir 而非会话名：/load 恢复旧会话、/rename 改标题后检查点
仍可命中，项目切换后旧目录检查点天然隔离（跨目录回滚自然失败）。
不污染用户仓库的分支/暂存区/历史；非 git 项目目录同样适用。检查点用
plumbing 命令做成"无父提交"的孤儿 commit（write-tree + commit-tree），
每个检查点一条 ref（refs/jarvis/ckptNNNNN），配额超限时删除最旧 ref 并
gc 回收——旧检查点失效后，对应消息自动退化为"仅对话回退"。

排除策略：shadow 仓库 info/exclude 预置重目录（.venv / node_modules /
dist / build / __pycache__ 等），同时天然尊重用户仓库自己的 .gitignore；
被忽略的文件不参与快照，git clean -fd（不带 -x）也不会删它们。

git 不可用（未安装/超时/目录异常）时所有方法安全降级：create 返回
None、restore 返回 (False, 原因)，调用方据此只提供对话回退。

用法::

    mgr = CheckpointManager("E:\\project", "session-20261003")
    cid = mgr.create(reason="帮我重构登录模块")   # 每轮对话前
    # ... 用户撤回这条消息时 ...
    ok, msg = mgr.restore(cid)

@author aceFelix
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# shadow 仓库 info/exclude 预置的重目录/生成物（git 自动忽略，不快照、不清理）
_DEFAULT_EXCLUDES = [
    ".git/",
    ".venv/",
    "venv/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    "out/",
    ".jarvis/",
]

# 检查点 ref 前缀（每个检查点一条独立 ref，配额修剪的抓手）
_REF_PREFIX = "refs/jarvis/ckpt"
# 记录快照工作目录的 config 键（跨目录回滚防护）
_WORKDIR_KEY = "jarvis.workdir"

_GIT_TIMEOUT = 10  # 单条 git 命令默认超时（秒）


def _git_available() -> bool:
    """系统 PATH 中是否存在 git 可执行文件。"""
    return shutil.which("git") is not None


def _safe_session_dir_name(session_name: str) -> str:
    """把会话名压成安全目录名（防路径穿越/非法字符）。"""
    cleaned = re.sub(r"[^0-9A-Za-z._-]", "_", session_name or "default")
    return cleaned[:80] or "default"


def _workdir_key(workdir: str) -> str:
    """工作目录 → 检查点存储子目录名（哈希 + 可读 basename）。

    按 workdir 而非会话名定位：/load 旧会话、/rename 改标题后检查点
    仍可命中；Windows 路径大小写不敏感，统一小写后再哈希。
    @author aceFelix
    """
    norm = str(Path(workdir).resolve()).lower().replace("\\", "/")
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]
    # 可读段用解析后的原始大小写（仅哈希归一），方便从目录名识别项目
    base = _safe_session_dir_name(Path(workdir).resolve().name)[:32]
    return f"{base}-{digest}"


class CheckpointManager:
    """工作区消息级检查点管理器（shadow git 实现）。

    Attributes:
        workdir: 被快照的工作目录（绝对路径）。
        session_name: 会话名（仅用于日志/提交信息标识，存储目录按 workdir 定位）。
    """

    def __init__(
        self,
        workdir: str,
        session_name: str,
        *,
        enabled: bool = True,
        max_checkpoints: int = 20,
        timeout_seconds: int = _GIT_TIMEOUT,
        base_dir: str | None = None,
    ) -> None:
        self._workdir = str(Path(workdir).resolve())
        self._session_name = session_name
        self._enabled = enabled
        self._max = max(1, int(max_checkpoints))
        self._timeout = max(1, int(timeout_seconds))
        root = Path(base_dir) if base_dir else Path.home() / ".jarvis" / "checkpoints"
        self._store_dir = root / _workdir_key(self._workdir)
        self._git_dir = self._store_dir / "git"
        self._manifest_path = self._store_dir / "manifest.json"
        self._initialized = False

    # ---- 可用性 ----

    def available(self) -> bool:
        """文件检查点是否可用（启用且系统装有 git）。"""
        return self._enabled and _git_available()

    @property
    def workdir(self) -> str:
        return self._workdir

    # ---- git 执行 ----

    def _git(self, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        """在 shadow 仓库上执行一条 git 命令（--git-dir/--work-tree 定向）。

        -c core.quotepath=false：中文文件名不转义，describe 列表可读。
        """
        cmd = [
            "git",
            "-c", "core.quotepath=false",
            "--git-dir", str(self._git_dir),
            "--work-tree", self._workdir,
            *args,
        ]
        return subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=self._timeout,
            check=check,
            # cwd 锁定工作树：git add/clean 等命令按 cwd 解释相对路径，
            # 不能受 jarvis 进程启动目录影响
            cwd=self._workdir if Path(self._workdir).is_dir() else None,
        )

    def _init_shadow_repo(self) -> bool:
        """在存储位置新建 shadow git 仓库并写基础配置。

        init 必须裸执行（不能带 --git-dir/--work-tree 重定向：目标目录
        尚不存在时 git 会拒绝），并显式传绝对路径；init 自己负责递归建
        目录，提前 mkdir 反会被拒。--bare 建法 + core.bare=false：git 目
        录与工作树分离的标准姿势。返回 init 是否落地。
        @author aceFelix
        """
        try:
            subprocess.run(
                ["git", "init", "--bare", str(self._git_dir)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self._timeout,
            )
        except Exception as e:
            logger.warning(f"[Checkpoint] shadow git init 异常: {e}")
            return False
        if not (self._git_dir / "HEAD").exists():
            logger.warning("[Checkpoint] shadow git init 失败（git 不可用或目录异常）")
            return False
        self._git("config", "core.bare", "false")
        self._git("config", "core.autocrlf", "false")
        # 提交者身份内置：CI/新用户机器常无全局 git 身份，commit-tree 会直接报错
        self._git("config", "user.name", "jarvis-checkpoint")
        self._git("config", "user.email", "checkpoint@jarvis.local")
        self._git("config", _WORKDIR_KEY, self._workdir)
        return True

    def _ensure_init(self) -> bool:
        """惰性初始化 shadow 仓库；失败返回 False（后续操作全部降级）。"""
        if self._initialized:
            return True
        if not _git_available():
            return False
        try:
            if (self._git_dir / "HEAD").exists():
                wd = self._git("config", "--get", _WORKDIR_KEY).stdout.strip()
                if not wd or wd == self._workdir:
                    self._write_excludes()
                    self._initialized = True
                    return True
                # 指纹碰撞/路径规范化差异：存储目录被另一个工作目录用过，
                # 整体重建（git 目录 + manifest 台账一起换新，避免串台）
                logger.warning(
                    f"[Checkpoint] 检查点存储工作区不符（{wd} != {self._workdir}），重建"
                )
                shutil.rmtree(str(self._store_dir), ignore_errors=True)
            if not self._init_shadow_repo():
                return False
            self._write_excludes()
            self._initialized = True
            return True
        except Exception as e:
            logger.warning(f"[Checkpoint] shadow git 初始化失败: {e}")
            return False

    def _write_excludes(self) -> None:
        """把重目录排除规则写入 shadow 仓库 info/exclude（幂等）。"""
        exclude_path = self._git_dir / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        lines = [ln for ln in existing.splitlines() if ln.strip()]
        added = False
        for pattern in _DEFAULT_EXCLUDES:
            if pattern not in lines:
                lines.append(pattern)
                added = True
        if added or not existing:
            exclude_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---- manifest（检查点台账：cid ↔ ref ↔ 元信息，配额修剪依据） ----

    def _load_manifest(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_manifest(self, entries: list[dict[str, Any]]) -> None:
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(
            json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8",
        )

    # ---- 核心操作 ----

    def create(self, reason: str = "") -> str | None:
        """打一个检查点（工作区当前状态），返回 checkpoint_id。

        每轮对话开始前调用。不可用/超时/出错一律返回 None，调用方
        降级为"仅对话回退"。孤儿 commit + 独立 ref 使每个检查点互不
        牵连，便于按配额删除最旧检查点。

        @author aceFelix
        """
        if not self.available() or not self._ensure_init():
            return None
        try:
            # add -A：把当前工作树全部变动刷进 index（尊重 exclude/.gitignore）
            self._git("add", "-A")
            tree = self._git("write-tree").stdout.strip()
            if not tree:
                return None
            msg = f"jarvis checkpoint: {reason[:80]}" if reason else "jarvis checkpoint"
            cid = self._git("commit-tree", tree, "-m", msg).stdout.strip()
            if not cid:
                return None
            entries = self._load_manifest()
            seq = int(entries[-1].get("seq", 0)) + 1 if entries else 1
            ref = f"{_REF_PREFIX}{seq:05d}"
            self._git("update-ref", ref, cid)
            entries.append({
                "id": cid, "ref": ref, "seq": seq,
                "ts": time.time(), "reason": reason[:120],
            })
            self._save_manifest(entries)
            self._prune(entries)
            logger.info(f"[Checkpoint] 已创建: {cid[:8]} ({reason[:40]})")
            return cid
        except subprocess.TimeoutExpired:
            logger.warning("[Checkpoint] create 超时，跳过本次检查点")
            return None
        except Exception as e:
            logger.warning(f"[Checkpoint] create 失败: {e}")
            return None

    def _prune(self, entries: list[dict[str, Any]]) -> None:
        """配额修剪：超过 max_checkpoints 的旧检查点删 ref + gc 回收。"""
        while len(entries) > self._max:
            old = entries.pop(0)
            try:
                self._git("update-ref", "-d", old.get("ref", ""))
            except Exception:
                pass
            self._save_manifest(entries)
        if len(entries) == self._max:
            # 恰好发生修剪时回收一次（限制频率：仅在越限触发后执行）
            try:
                self._git("gc", "--prune=now", "--quiet")
            except Exception:
                pass

    def has_checkpoint(self, checkpoint_id: str) -> bool:
        """检查点是否仍存在（未被配额修剪）。"""
        if not checkpoint_id or not self.available():
            return False
        try:
            return self._git("cat-file", "-e", f"{checkpoint_id}^{{commit}}").returncode == 0
        except Exception:
            return False

    def describe(self, checkpoint_id: str) -> dict[str, Any] | None:
        """预览回滚影响：恢复该检查点会改动/删除哪些文件。

        Returns:
            {"files": [{"status", "path"}], "untracked": [...]}；
            检查点不存在或出错返回 None。
        @author aceFelix
        """
        if not self.has_checkpoint(checkpoint_id):
            return None
        try:
            # 工作树 vs 检查点：M/A/D 列表即"回滚会撤销的修改"
            diff = self._git("diff", "--name-status", checkpoint_id)
            files = []
            for line in diff.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    files.append({"status": parts[0][:1], "path": parts[-1]})
            # 检查点后新建、尚未被跟踪的文件：restore 时由 clean -fd 删除
            others = self._git("ls-files", "--others", "--exclude-standard")
            untracked = [ln for ln in others.stdout.splitlines() if ln.strip()]
            return {"files": files, "untracked": untracked}
        except Exception as e:
            logger.warning(f"[Checkpoint] describe 失败: {e}")
            return None

    def restore(self, checkpoint_id: str) -> tuple[bool, str]:
        """把工作区恢复到指定检查点。

        步骤：read-tree -u --reset（index + 工作树整体刷回检查点状态）
        → clean -fd（删除检查点之后新建的未跟踪文件；不带 -x，被忽略
        的 node_modules 等不受波及）。

        Returns:
            (是否成功, 说明信息)。跨工作目录 / 检查点已失效一律拒绝。
        @author aceFelix
        """
        if not self.available():
            return False, "文件回滚不可用（未启用或缺少 git）"
        if not self._ensure_init():
            return False, "检查点仓库初始化失败"
        if not checkpoint_id:
            return False, "该消息没有关联检查点"
        if not self.has_checkpoint(checkpoint_id):
            return False, "检查点已被清理，无法回滚文件"
        try:
            self._git("read-tree", "-u", "--reset", checkpoint_id)
            clean = self._git("clean", "-fd")
            if clean.returncode != 0:
                return False, f"清理新增文件失败: {clean.stderr.strip()[:120]}"
            logger.info(f"[Checkpoint] 已回滚工作区到 {checkpoint_id[:8]}")
            return True, "工作区已恢复到该消息发出之前"
        except subprocess.TimeoutExpired:
            return False, "回滚超时，请检查工作区是否有文件占用"
        except Exception as e:
            return False, f"回滚失败: {type(e).__name__}: {e}"
