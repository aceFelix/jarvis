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

from agent.core.gui_launch import is_gui_launch
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
# GUI 窗口从命令启动到真正出现/被置前的宽限；收尾时这类窗口属于"用户要看的结果"。
_GUI_KEEP_WINDOW_S = 10.0

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


def _agent_meta(ctx: Any) -> dict[str, str]:
    """从 ``ToolContext.extra`` 取子代理身份，供事件标注与主任务过滤。

    字段缺失时返回空字典（不写假值）。@author aceFelix
    """
    extra = getattr(ctx, "extra", None)
    if not isinstance(extra, dict):
        return {}
    agent_id = extra.get("agent_id")
    if not agent_id:
        return {}
    meta = {"agent_id": str(agent_id)}
    for key in ("agent_name", "agent_mode"):
        value = extra.get(key)
        if value:
            meta[key] = str(value)
    return meta


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
    # 事件旁路出口（C1 终端驾驶舱）：每条记录在落盘与滚动行之外再交给它一份。
    # 必须与主流程隔离——sink 抛异常不能影响工具执行或 JSONL 落盘。
    # @author aceFelix
    on_event: Any = None
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
    # 每一步工具声明的窗口意图（与 _step_new 对齐）：keep / transient / None
    _step_policy: list[str | None] = field(default_factory=list)
    _involved: set[int] = field(default_factory=set)
    # 用户要求打开的 GUI 窗口（如 Bash `start firefox`）：收尾时必须保留，
    # 即使它后来被观察器置前、也不算"最后一步"。@author aceFelix
    _keep_hwnds: set[int] = field(default_factory=set)
    _gui_launch_at: float = 0.0
    # 窗口被置前的时刻 + 最近一次"显式关闭"动作的时刻：
    # 关闭动作晚于置前 → 该窗口是做完的中间产物（如 WPS 关文档后的应用壳）。
    _show_at: dict[int, float] = field(default_factory=dict)
    _last_close_intent_at: float = 0.0
    # 未声明意图的窗口默认保留（宁多留、不误关）；可配置为 transient 恢复激进清理
    cleanup_unknown: str = "keep"
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
        self._step_policy = []
        self._involved = set()
        self._keep_hwnds = set()
        self._gui_launch_at = 0.0
        self._show_at = {}
        self._last_close_intent_at = 0.0
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
            "agent": _agent_meta(ctx),
        }
        # 子代理的工具调用不计入主任务步数；事件照常落盘并带 agent_id。
        if not _agent_meta(ctx):
            self._steps += 1
        self._emit(
            {
                "type": "tool_start",
                "tool": tool,
                "tool_use_id": tool_use_id,
                "target": _target_of(tool, tool_input),
                "input": _slim_input(tool_input),
                **_agent_meta(ctx),
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
            if action.get("action") in ("show", "focus"):
                now = time.monotonic()
                self._show_at[hwnd] = now
                # 刚跑过 GUI 启动命令、随后出现的窗口 = 用户要看的结果
                # （2026-11-08 实机：firefox 被收尾清理误关）。@author aceFelix
                if (
                    hwnd not in self._before_windows
                    and now - self._gui_launch_at <= _GUI_KEEP_WINDOW_S
                ):
                    self._keep_hwnds.add(hwnd)
        self._emit({"type": "window_action", **action})

    def on_tool_after(self, payload: dict[str, Any]) -> None:
        """工具执行后：记 tool_end（耗时、成功/失败、错误摘要）。"""
        if not self.enabled:
            return
        tool_use_id = str(payload.get("tool_use_id") or "")
        pending = self._pending.pop(tool_use_id, None)
        agent_meta = pending.get("agent") if pending else _agent_meta(payload.get("ctx"))
        started = pending["started"] if pending else time.monotonic()
        duration = int((time.monotonic() - started) * 1000)
        is_error = bool(payload.get("is_error"))
        if is_error and not (agent_meta or {}):
            # 子代理失败不计入主任务失败数；它由代理卡自己汇总
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
                **(agent_meta or {}),
            }
        )
        # 抓取类工具派生 web_fetch（工具实现零改动，见 docs/plans/terminal-cockpit.md）。
        # 字段缺失即省略：http_status / parent_url / depth 本轮无数据源，不写假值。
        # @author aceFelix
        if pending is not None:
            fetch_tool = str(pending.get("tool") or "")
            fetch_url = str((pending.get("input") or {}).get("url") or "")
            if fetch_url and fetch_tool in ("WebFetch", "BrowserNavigate"):
                derived: dict[str, Any] = {
                    "type": "web_fetch",
                    "url": fetch_url,
                    "status": "error" if is_error else "ok",
                    "duration_ms": duration,
                }
                if fetch_tool == "WebFetch":
                    # BrowserNavigate 不返回页面正文，字节数不适用，省略
                    derived["bytes"] = len(str(payload.get("result") or ""))
                derived.update(agent_meta or {})
                self._emit(derived)
            # 结构化旁路：工具把要上驾驶舱的数据放在 ToolResult.trace_meta。
            # WebExtract 上报单条 web_extract；Crawl 批量上报 web_fetch /
            # web_extract / crawl_stats，这里统一按“dict 或 list”展开。@author aceFelix
            trace_meta = payload.get("trace_meta")
            if isinstance(trace_meta, dict):
                for fetch in _as_event_list(trace_meta.get("web_fetch")):
                    if not fetch.get("url"):
                        continue
                    self._emit({"type": "web_fetch", **fetch, **(agent_meta or {})})
                for extracted in _as_event_list(trace_meta.get("web_extract")):
                    if not extracted.get("url"):
                        continue
                    extract_event: dict[str, Any] = {
                        "type": "web_extract",
                        "url": extracted.get("url"),
                        "fields": extracted.get("fields") or {},
                    }
                    missing = extracted.get("missing") or []
                    if missing:
                        extract_event["missing"] = list(missing)
                    if extracted.get("selector"):
                        extract_event["selector"] = extracted["selector"]
                    extract_event.update(agent_meta or {})
                    self._emit(extract_event)
                stats = trace_meta.get("crawl_stats")
                if isinstance(stats, dict):
                    self._emit({"type": "crawl_stats", **stats, **(agent_meta or {})})
        # 记录这一步新出现的窗口，供收尾判定"哪些是中间产物、哪些是最终结果"
        current = self._snapshot_windows()
        self._step_new.append(set(current) - set(self._step_start))
        self._step_start = {}
        # 工具声明的窗口意图（None=不声明），与 _step_new 对齐
        policy = payload.get("window_policy")
        self._step_policy.append(
            str(policy) if policy in ("keep", "transient") else None
        )
        # 显式关闭动作（WindowClose / BrowserClose / wps action=close）：
        # 之后收尾可把"关闭动作之前被置前的窗口"判为做完的中间产物。
        if pending and _is_close_intent(
            str(pending.get("tool") or ""), dict(pending.get("input") or {})
        ):
            self._last_close_intent_at = time.monotonic()
        # GUI 启动步（start firefox / Start-Process …）：窗口可能晚 1~2 秒才出现，
        # 这里只记时间点，窗口真正被观察器置前时再登记进保留集合。
        # @author aceFelix
        if pending and pending.get("tool") == "Bash":
            command = str((pending.get("input") or {}).get("command") or "")
            if is_gui_launch(command):
                self._gui_launch_at = time.monotonic()
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

    def _policy_for(self, hwnd: int) -> str | None:
        """查这个窗口所在步骤的工具意图声明（后出现的声明覆盖先前的）。

        Args:
            hwnd: 窗口句柄。

        Returns:
            ``"keep"`` / ``"transient"`` / None（该窗口没有对应声明）。
        """
        policy = None
        for index, windows in enumerate(self._step_new):
            if hwnd in windows and index < len(self._step_policy):
                declared = self._step_policy[index]
                if declared:
                    policy = declared
        return policy

    def _cleanup_transient_windows(self) -> None:
        """关闭本轮新开、但被判定为中间产物的窗口。

        判定优先级（从高到低）：

        1. 工具显式声明 ``transient`` → 关闭；显式 ``keep`` → 保留；
        2. 显式关闭动作（WindowClose / BrowserClose / `action=close`）
           晚于该窗口被置前 → 视为做完的中间产物（WPS 关文档后的应用壳）；
        3. 未声明时**默认保留**（宁多留、不误关）；只有配置
           ``[ui] window_cleanup_unknown = "transient"`` 才恢复"关被操作窗口"的激进策略；
        4. 未保存窗口由 ``WindowDirector`` 跳过并上报，绝不强杀。

        最后一步打开的窗口与 GUI 启动命令拉起的窗口始终保留。
        """
        if self.director is None:
            return
        try:
            current = self.director.snapshot_records()
        except Exception:
            return
        new_windows = set(current) - set(self._before_windows)
        aggressive = self.cleanup_unknown == "transient"
        keep: set[int] = set()
        transient: set[int] = set()
        for hwnd in new_windows:
            policy = self._policy_for(hwnd)
            if policy == "transient":
                transient.add(hwnd)
                continue
            if policy == "keep":
                keep.add(hwnd)
                continue
            # 未声明：保守保留；只有强信号才当中间产物
            shown = self._show_at.get(hwnd)
            if shown is not None and self._last_close_intent_at > shown:
                transient.add(hwnd)
                continue
            if aggressive and hwnd in self._involved:
                transient.add(hwnd)
                continue
            keep.add(hwnd)
        # 最后一步打开的窗口通常就是最终结果；但显式 transient / 关闭动作
        # 已判定为中间产物的不再拉回。
        if self._step_new:
            keep |= (self._step_new[-1] & new_windows) - transient
        fg = None
        try:
            fg = self.director.foreground_hwnd()
        except Exception:
            fg = None
        # 只有"本轮没操作过"的前台窗口才算用户正在看
        if isinstance(fg, int) and fg not in self._involved:
            keep.add(fg)
        # 用户要求打开的 GUI 窗口（如 `start firefox`）属于最终结果：
        # 后续验证步骤（sleep/截图）不会把它从保留集合里挤掉。
        keep |= self._keep_hwnds & set(current)
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
        # 驾驶舱等旁路消费者：异常完全隔离，观测失败不能中断任务
        sink = self.on_event
        if sink is not None:
            try:
                sink(record)
            except Exception:
                pass

    def _cancel_end_timer(self) -> None:
        timer = self._end_timer
        if timer is not None:
            timer.cancel()
            self._end_timer = None


def _as_event_list(value: Any) -> list[dict[str, Any]]:
    """把 ``trace_meta`` 的单条 dict 或批量 list 统一成事件列表。

    过滤非 dict 项，避免工具意外塞入字符串/None 把派生事件弄脏。

    @author aceFelix
    """
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _is_close_intent(tool_name: str, tool_input: dict[str, Any]) -> bool:
    """本次工具调用是否表达"把它关掉"的意图。

    覆盖两类：窗口/浏览器关闭工具（名字以 ``Close`` 结尾），以及
    WPS/office 这类 harness 的 ``action=close`` 参数。用于把"关闭动作
    之前被置前的窗口"判为做完的中间产物。

    @author aceFelix
    """
    name = str(tool_name or "").lower()
    if name.endswith("close"):
        return True
    return str(tool_input.get("action") or "").strip().lower() == "close"


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
        cleanup_unknown=str(
            getattr(settings, "window_cleanup_unknown", "keep") or "keep"
        ),
        # 终端驾驶舱的事件旁路；无该方法的 UI（serve/wechat/bridge）保持零副作用
        on_event=getattr(ui, "on_trace_event", None),
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
