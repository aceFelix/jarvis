"""操作轨迹（trace）：把工具调用写成可读、可回看的本地事件流。

为什么放在 Hooks 层（``agent/core/hooks.py``）：
- ``orchestrator.py`` 已在工具执行前后触发 ``tool_before`` / ``tool_after`` /
  ``file_changed``，payload 字段齐全（工具名、入参、结果、是否失败、路径）；
- 钩子自身有超时与异常隔离（"钩子失败不炸主流程"），观测代码出问题不会影响
  真正的工具执行；
- **零侵入**：不改 orchestrator、不改任何工具实现。

落盘与隐私（2026-11 操作可见性）：
- 每轮对话一个 ``~/.jarvis/traces/<run_id>.jsonl``，只记路径/窗口标题/计数，
  **不记文件内容**；
- 窗口标题命中密码管理器/银行等关键词时替换为 ``‹已脱敏窗口›``；
- 只保留最近 ``trace_keep_runs`` 轮，启动新一轮时自动清理最旧的。

@author aceFelix
"""

from __future__ import annotations

import difflib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.ui.trace_view import render_event, safe_info

TRACE_ROOT = Path.home() / ".jarvis" / "traces"

#: 需要脱敏的窗口标题关键词（小写匹配）
MASK_PATTERNS: tuple[str, ...] = (
    "1password",
    "bitwarden",
    "keepass",
    "lastpass",
    "authenticator",
    "password",
    "密码",
    "银行",
    "bank",
    "wallet",
    "钱包",
)

MASKED_WINDOW = "‹已脱敏窗口›"

_MAX_ERROR_CHARS = 120
_MAX_BUFFER = 400
_END_DEBOUNCE_S = 0.8

_recorder: "TraceRecorder | None" = None


def mask_window_title(title: str) -> str:
    """按关键词表脱敏窗口标题（命中即整条替换，不做部分遮挡）。"""
    low = (title or "").lower()
    if any(p in low for p in MASK_PATTERNS):
        return MASKED_WINDOW
    return title


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _line_diff(old: str, new: str) -> dict[str, int]:
    """用 difflib 估算增删行数；超长输入先截断，避免拖慢工具主流程。"""
    old_lines = (old or "").splitlines()[:2000]
    new_lines = (new or "").splitlines()[:2000]
    added = removed = 0
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(
        None, old_lines, new_lines
    ).get_opcodes():
        if tag in ("replace", "insert"):
            added += j2 - j1
        if tag in ("replace", "delete"):
            removed += _i2 - _i1
    return {"added": added, "removed": removed}


def _target_of(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any] | None:
    """从工具入参提取"操作目标"，供终端展示与窗口跟随。"""
    for key in ("file_path", "filePath", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return {"kind": "file", "path": value}
    for key in ("title", "window_title", "window"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return {"kind": "window", "title": mask_window_title(value)}
    return None


@dataclass
class TraceRecorder:
    """一轮对话的轨迹采集器。

    Attributes:
        enabled: 总开关；关闭时既不落盘也不跟随窗口。
        keep_runs: 保留最近多少轮 trace 文件。
        ui: 终端 UI（实现 ``UIProtocol``），可为 None（测试/无头）。
        director: 窗口跟随器，可为 None。
        base_dir: trace 落盘目录，测试可指向 tmp_path。

    @author aceFelix
    """

    enabled: bool = True
    keep_runs: int = 20
    ui: Any = None
    director: Any = None
    observer: Any = None
    base_dir: Path = TRACE_ROOT
    current_events: list[dict[str, Any]] = field(default_factory=list)
    last_run_events: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = ""
    last_run_file: Path | None = None
    _seq: int = 0
    _steps: int = 0
    _failed: int = 0
    _files_changed: int = 0
    _started: float = 0.0
    _pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    _last_input: dict[str, Any] = field(default_factory=dict)
    _workdir: str = ""
    # 并行工具调用时 file_changed 不带 tool_use_id，按路径回填各自的入参，
    # 避免 A 工具的 diff 算到 B 文件上（@author aceFelix）
    _inputs_by_path: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 任务收尾清理：本轮开始时的窗口快照、每步新窗口、被实际跟随/操作过的 hwnd
    _before_windows: dict[int, str] = field(default_factory=dict)
    _step_start: dict[int, str] = field(default_factory=dict)
    _step_new: list[set[int]] = field(default_factory=list)
    _involved: set[int] = field(default_factory=set)
    _end_timer: threading.Timer | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- 生命周期 ----

    def start_run(self, input_chars: int = 0) -> None:
        """开始新一轮；上一轮若还挂着先收尾。"""
        if not self.enabled:
            return
        self._cancel_end_timer()
        if self.run_id and self.current_events:
            self.finish_run()
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.current_events = []
        self._seq = 0
        self._steps = 0
        self._failed = 0
        self._files_changed = 0
        self._pending.clear()
        self._inputs_by_path.clear()
        self._before_windows = self._snapshot_windows()
        self._step_start = {}
        self._step_new = []
        self._involved = set()
        self._started = time.monotonic()
        self._rotate()
        self._emit({"type": "run_start", "input_chars": int(input_chars)})

    def finish_run(self) -> None:
        """结束当前轮并写入汇总事件。"""
        if not self.run_id:
            return
        self._cancel_end_timer()
        if self.observer is not None:
            try:
                self.observer.cancel_all()
            except Exception:
                pass
        # 收尾清理：关掉本轮中间产物窗口，保留最终结果（最后一步/当前前台）
        self._cleanup_transient_windows()
        duration = int((time.monotonic() - self._started) * 1000)
        self._emit(
            {
                "type": "run_end",
                "steps": self._steps,
                "failed": self._failed,
                "files_changed": self._files_changed,
                "duration_ms": duration,
            }
        )
        with self._lock:
            self.last_run_events = list(self.current_events)
            self.last_run_file = self._run_path() if self.run_id else None
            self.current_events = []
            self.run_id = ""

    # ---- Hook 回调 ----

    def on_user_prompt(self, payload: dict[str, Any]) -> None:
        """用户提交输入：开一轮。"""
        if not self.enabled:
            return
        text = payload.get("text") or payload.get("prompt") or ""
        self.start_run(len(str(text)))

    def on_tool_before(self, payload: dict[str, Any]) -> None:
        """工具执行前：记 tool_start，并触发窗口跟随。"""
        if not self.enabled:
            return
        self._cancel_end_timer()
        if not self.run_id:
            self.start_run(0)
        tool = str(payload.get("tool_name") or "?")
        tool_input = dict(payload.get("tool_input") or {})
        tool_use_id = str(payload.get("tool_use_id") or "")
        self._last_input = tool_input
        self._step_start = self._snapshot_windows()
        # 记录本轮工作目录：file_changed 里的相对路径要靠它补全（@author aceFelix）
        ctx = payload.get("ctx")
        workdir = getattr(ctx, "workdir", "") if ctx is not None else ""
        if isinstance(workdir, str) and workdir:
            self._workdir = workdir
        file_path = (
            tool_input.get("file_path")
            or tool_input.get("filePath")
            or tool_input.get("path")
        )
        if isinstance(file_path, str) and file_path:
            self._inputs_by_path[file_path] = tool_input
        self._pending[tool_use_id] = {
            "started": time.monotonic(),
            "tool": tool,
            "input": tool_input,
        }
        self._steps += 1
        self._emit(
            {
                "type": "tool_start",
                "tool": tool,
                "tool_use_id": tool_use_id,
                "target": _target_of(tool, tool_input),
                "input": _slim_input(tool_input),
            }
        )
        # 窗口跟随：交由通用观察器判定（快照差分 + 入参线索），不再按工具名分支
        if self.observer is not None:
            try:
                self.observer.before(tool, tool_input, tool_use_id, payload.get("ctx"))
            except Exception:
                pass

    def note_action(self, action: dict[str, Any]) -> None:
        """记录窗口动作；目标 hwnd 进"被操作过"集合，供收尾清理判定。"""
        hwnd = action.get("hwnd")
        if isinstance(hwnd, int):
            self._involved.add(hwnd)
        self._emit({"type": "window_action", **action})

    def on_tool_after(self, payload: dict[str, Any]) -> None:
        """工具执行后：记 tool_end（耗时、成功/失败、错误摘要）。"""
        if not self.enabled:
            return
        tool_use_id = str(payload.get("tool_use_id") or "")
        pending = self._pending.pop(tool_use_id, None)
        started = pending["started"] if pending else time.monotonic()
        duration = int((time.monotonic() - started) * 1000)
        is_error = bool(payload.get("is_error"))
        if is_error:
            self._failed += 1
        error = ""
        if is_error:
            error = str(payload.get("result") or "").strip().splitlines()
            error = error[0][:_MAX_ERROR_CHARS] if error else ""
        self._emit(
            {
                "type": "tool_end",
                "tool": str(payload.get("tool_name") or "?"),
                "tool_use_id": tool_use_id,
                "ok": not is_error,
                "duration_ms": duration,
                "error": error,
                "result_chars": len(str(payload.get("result") or "")),
            }
        )
        # 记录这一步新出现的窗口，供收尾判定"哪些是中间产物、哪些是最终结果"
        current = self._snapshot_windows()
        self._step_new.append(set(current) - set(self._step_start))
        self._step_start = {}
        # 启动观察器探测（延迟后判断是否出现"新窗口且变前台"）
        if self.observer is not None:
            try:
                self.observer.after(tool_use_id)
            except Exception:
                pass

    def on_file_changed(self, payload: dict[str, Any]) -> None:
        """文件变更：记 diff 估算，并在写/改后定位目录。"""
        if not self.enabled:
            return
        path = str(payload.get("path") or "")
        operation = str(payload.get("operation") or "")
        # 读文件不产生 file_change（工具调用本身已在 tool_start 里），
        # 也避免把"读"算进 summary 的"几个文件"。
        if operation not in ("write", "edit"):
            return
        diff = _diff_for(self._inputs_by_path.pop(path, self._last_input), operation)
        self._files_changed += 1
        self._emit(
            {
                "type": "file_change",
                "path": path,
                "operation": operation,
                "diff": diff,
            }
        )
        if self.director is not None and path:
            try:
                action = self.director.on_file_changed(
                    path, operation, workdir=self._workdir
                )
            except Exception:
                action = None
            if action:
                self._emit({"type": "window_action", **action})

    def on_assistant_response(self, payload: dict[str, Any]) -> None:
        """助手回复完成：延迟收尾；若紧接着又有工具调用则取消（防误判一轮结束）。"""
        if not self.enabled or not self.run_id:
            return
        self._cancel_end_timer()
        timer = threading.Timer(_END_DEBOUNCE_S, self.finish_run)
        timer.daemon = True
        timer.start()
        self._end_timer = timer

    # ---- 查询（供 /trace） ----

    def recent_events(self) -> list[dict[str, Any]]:
        """当前轮事件；没有活动轮时返回最近结束的一轮。"""
        return list(self.current_events or self.last_run_events)

    def list_runs(self, limit: int = 10) -> list[Path]:
        """最近的 trace 文件（按修改时间倒序）。"""
        try:
            files = sorted(
                self.base_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []
        return files[:limit]

    def set_enabled(self, value: bool) -> None:
        """运行时开关（``/trace on|off``）。"""
        self.enabled = bool(value)
        if not value:
            self.finish_run()

    # ---- 窗口快照与收尾清理 ----

    def _snapshot_windows(self) -> dict[int, Any]:
        if self.director is None:
            return {}
        try:
            return self.director.snapshot_records()
        except Exception:
            return {}

    def _cleanup_transient_windows(self) -> None:
        """关闭本轮新开、被实际操作过、且不属于最终结果的窗口。

        保留三类：未被操作过的新窗口（可能是用户自己开的）、最后一步打开的窗口
        （通常就是最终结果）、当前前台窗口（用户正在看）。未保存窗口由
        ``WindowDirector`` 跳过并上报。
        """
        if self.director is None:
            return
        try:
            current = self.director.snapshot_records()
        except Exception:
            return
        new_windows = set(current) - set(self._before_windows)
        involved = new_windows & self._involved
        keep: set[int] = set(new_windows - involved)
        if self._step_new:
            keep |= self._step_new[-1] & self._involved
        fg = None
        try:
            fg = self.director.foreground_hwnd()
        except Exception:
            fg = None
        if isinstance(fg, int):
            keep.add(fg)
        try:
            result = self.director.close_transients(self._before_windows, keep)
        except Exception:
            return
        closed = result.get("closed") or []
        skipped = result.get("skipped") or []
        if not closed and not skipped:
            return
        parts: list[str] = []
        if closed:
            parts.append(f"已关闭 {len(closed)} 个临时窗口")
        if skipped:
            parts.append(f"保留 {len(skipped)} 个未保存窗口")
        self._emit(
            {
                "type": "window_action",
                "action": "close",
                "ok": True,
                "message": "；".join(parts),
            }
        )

    # ---- 内部 ----

    def _run_path(self) -> Path:
        return self.base_dir / f"{self.run_id}.jsonl"

    def _rotate(self) -> None:
        """只保留最近 ``keep_runs`` 个文件。"""
        keep = max(1, int(self.keep_runs))
        try:
            files = sorted(
                self.base_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for old in files[max(0, keep - 1):]:
            try:
                old.unlink()
            except OSError:
                pass

    def _emit(self, event: dict[str, Any]) -> None:
        """补齐公共字段 → 落盘 → 上屏。"""
        with self._lock:
            self._seq += 1
            record = {
                "v": 1,
                "ts": _now_iso(),
                "run_id": self.run_id,
                "seq": self._seq,
                **event,
            }
            if len(self.current_events) < _MAX_BUFFER:
                self.current_events.append(record)
            try:
                self.base_dir.mkdir(parents=True, exist_ok=True)
                with self._run_path().open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                pass
        line = render_event(record)
        if line:
            safe_info(self.ui, line)

    def _cancel_end_timer(self) -> None:
        timer = self._end_timer
        if timer is not None:
            timer.cancel()
            self._end_timer = None


def _slim_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    """入参只保留前 3 个短字段，避免 trace 文件被大段内容撑爆。"""
    slim: dict[str, Any] = {}
    for key, value in list(tool_input.items())[:3]:
        if isinstance(value, (str, int, float, bool)) or value is None:
            text = str(value)
            slim[key] = text[:120] if len(text) > 120 else value
    return slim


def _diff_for(tool_input: dict[str, Any], operation: str) -> dict[str, int] | None:
    """按工具入参估算增删行数（不读文件、不碰 checkpoint）。"""
    old = str(tool_input.get("old_string") or "")
    new = str(tool_input.get("new_string") or tool_input.get("content") or "")
    if not new and not old:
        return None
    return _line_diff(old, new)


def get_recorder() -> "TraceRecorder | None":
    """返回已装配的采集器；未安装时 None。"""
    return _recorder


def install_observability(settings: Any, ui: Any) -> TraceRecorder:
    """注册 trace + 窗口跟随钩子（宿主启动时调用一次，重复调用幂等）。

    Args:
        settings: 已加载配置（读 ``[ui]`` 段）。
        ui: 终端 UI。

    Returns:
        进程级 ``TraceRecorder`` 单例。

    @author aceFelix
    """
    global _recorder
    if _recorder is not None:
        return _recorder

    from agent.core.hooks import HookEvent, get_hooks

    director = None
    try:
        from agent.core.window_follow import WindowDirector

        director = WindowDirector(settings=settings)
        reason = director.describe_platform()
        if reason:
            safe_info(ui, f"🪟 {reason}，窗口跟随已关闭")
    except Exception as exc:  # noqa: BLE001 - 窗口后端异常不能影响启动
        safe_info(ui, f"🪟 窗口跟随初始化失败：{exc}")

    recorder = TraceRecorder(
        enabled=bool(getattr(settings, "trace_enabled", True)),
        keep_runs=max(1, int(getattr(settings, "trace_keep_runs", 20) or 20)),
        ui=ui,
        director=director,
    )

    # 通用窗口观察器：快照差分决定跟随，不依赖工具名（见 docs/plans/window-observer.md）
    if director is not None:
        try:
            from agent.core.window_observer import WindowObserver

            recorder.observer = WindowObserver(
                settings=settings, director=director, sink=recorder.note_action
            )
        except Exception as exc:  # noqa: BLE001 - 观察器故障不影响对话
            safe_info(ui, f"🪟 窗口观察器初始化失败：{exc}")

    hooks = get_hooks()
    hooks.register(HookEvent.USER_PROMPT, recorder.on_user_prompt, name="trace-run-start")
    hooks.register(HookEvent.TOOL_BEFORE, recorder.on_tool_before, name="trace-tool-before")
    hooks.register(HookEvent.TOOL_AFTER, recorder.on_tool_after, name="trace-tool-after")
    hooks.register(HookEvent.FILE_CHANGED, recorder.on_file_changed, name="trace-file-changed")
    hooks.register(
        HookEvent.ASSISTANT_RESPONSE,
        recorder.on_assistant_response,
        name="trace-run-end",
    )
    _recorder = recorder
    return recorder


def uninstall_observability() -> None:
    """卸载钩子并清空单例（测试用）。"""
    global _recorder
    from agent.core.hooks import get_hooks

    hooks = get_hooks()
    for name in (
        "trace-run-start",
        "trace-tool-before",
        "trace-tool-after",
        "trace-file-changed",
        "trace-run-end",
    ):
        hooks.unregister(name)
    if _recorder is not None:
        _recorder._cancel_end_timer()
    _recorder = None
