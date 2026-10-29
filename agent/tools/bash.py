"""Bash 工具 —— 执行 shell 命令。

这是权限系统最关心的工具:
- 默认 ASK（无 allow 规则时必须确认）
- 命令分类（readonly/dangerous/unknown）由 permissions/shell_classifier.py 做
- prepare_permission_matcher 解析命令前缀，让规则 "Bash(git *)" 能命中
- P3-8 沙箱集成: 高风险命令自动在 Job Object 沙箱内执行

v0.1 用 asyncio subprocess 执行，统一 stdout/stderr 合并输出，有超时。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import re
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionBehavior, PermissionResult, ToolResult
from agent.core.sandbox.audit import SandboxAuditor
from agent.core.sandbox.executor import SandboxConfig, SandboxExecutor
from agent.core.sandbox.risk_scorer import RiskLevel, RiskScorer, looks_network_outbound
from agent.core.tool import JSONSchema, PermissionMatcher, Tool
from agent.permissions.modes import SANDBOX_AUTO_ALLOW_PREFIX
from agent.permissions.shell_classifier import get_command_head

logger = logging.getLogger(__name__)

# 等子进程时的轮询粒度（秒）。用短轮询而不是纯事件唤醒：
# 语音模式的 abort_event 是**从工作线程**置位的（`_poll_interrupt`），
# 跨线程唤醒 asyncio.Event 的 waiter 不可靠，每拍再查一次 is_set() 保证打断一定生效。
_PROCESS_POLL_SEC = 0.05

# 管道残留数据的读取宽限（秒）。shell 退出后只读「此刻管道里已有」的数据，
# 绝不等待 EOF——后台子进程会继承写端让 EOF 永不到来。
_OUTPUT_DRAIN_GRACE_S = 0.05

# 单次收集输出上限（字节），防后台进程刷屏把内存打满
_MAX_CAPTURE_BYTES = 4_000_000

# kill 后等进程回收的宽限（秒）。Windows 上 TerminateProcess 只杀直接子进程，
# 孙子进程可能仍握着管道让 asyncio 的 wait() 不返回，不能无限等。
_KILL_REAP_TIMEOUT_S = 2.0

# 超时回执里的提示：模型常用前台方式拉起 GUI / 常驻程序，
# shell 会一直挂到程序退出才返回，必然吃满 timeout。
_TIMEOUT_HINT = (
    "\n提示：若是启动 GUI / 常驻程序，前台命令会一直挂到它退出才返回。"
    "请写成后台形式：命令末尾加 & 并把输出丢掉，例如 "
    "`cmd & > /dev/null 2>&1`（本工具会自动补这一步，但直接写全更稳），"
    "或适当调小 timeout。"
)

# 「以 & 结尾」的后台命令（排除 && 连接符）
_BACKGROUND_TAIL = re.compile(r"(?<!&)&\s*$")


def _detach_background(command: str) -> tuple[str, bool]:
    """把「以 & 结尾」的后台命令改写成真正能立刻返回的形式。

    为什么必须有这一步：MSYS2/Git Bash 的 ``bash -c 'cmd &'`` **并不会**立刻返回，
    它会等这个后台 job（实机实测：``sleep 3 &`` = 3.18s，
    ``sleep 3 >/dev/null &`` = 0.21s，``sleep 3 2>/dev/null &`` = 3.13s）。
    只要后台进程还接着 bash 的 stdout，bash 就不肯走。

    于是模型写 ``firefox.exe URL &`` 时，「打开一个网页」照样要卡满 timeout——
    这正是用户反馈「网页都加载出来了，jarvis 还在等」的根因（不是等页面稳定）。

    这里给后台命令补上 ``> /dev/null 2>&1`` 让它真正脱离。代价是拿不到后台进程
    的输出，但后台命令本来就不该指望工具返回它的运行日志。

    Returns:
        ``(实际执行的命令, 是否后台命令)``。第二项只看「是否以 & 结尾」，
        **与有没有被改写过无关**——调用方据此决定是否跳过沙箱，
        否则模型自己写全了 ``> /dev/null 2>&1 &`` 时会被误判成前景命令。

    @author aceFelix
    """
    stripped = command.rstrip()
    if not _BACKGROUND_TAIL.search(stripped):
        return command, False
    # 模型自己已经丢过输出的：保持原样，但它仍然是后台命令
    if re.search(r">\s*/dev/null", stripped) and (
        "2>&1" in stripped or "2>/dev/null" in stripped or "2> /dev/null" in stripped
    ):
        return command, True
    body = stripped[:-1].rstrip()
    if not body:
        return command, False
    return f"{body} > /dev/null 2>&1 &", True


class _StreamCollector:
    """后台持续吸走一个管道的数据，并在收尾时取出已收到的部分。

    为什么必须「边等边读」而不能「先等退出再读」：管道缓冲只有 64KB，输出超过它时
    shell 会阻塞在 write 上、永远不会退出——只等 ``proc.wait()`` 就会死锁到 timeout。
    而 ``communicate()`` 虽会边读，但它等的是 EOF：``firefox.exe URL &`` 让 shell
    立即退出，firefox 却继承了写端，EOF 永不到来，于是「打开一个网页」也能卡满 timeout。

    两者都不能单独用：所以这里一直读（防死锁）、但**不等 EOF**（防后台写端拖住），
    ``proc.wait()`` 一返回就收尾取数据。

    @author aceFelix
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._chunks: list[bytes] = []
        self._total = 0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._stream is None:
            return
        self._task = asyncio.ensure_future(self._pump())

    async def _pump(self) -> None:
        while True:
            try:
                chunk = await self._stream.read(65536)
            except (ValueError, OSError):
                return  # 管道已被回收，读到多少算多少
            if not chunk:
                return  # EOF
            self._chunks.append(chunk)
            self._total += len(chunk)
            if self._total >= _MAX_CAPTURE_BYTES:
                return  # 防后台进程刷屏把内存打满

    async def finish(self, grace: float = _OUTPUT_DRAIN_GRACE_S) -> bytes:
        """收尾：先在宽限窗口里等写入落地，再强行取回已收集的数据。"""
        if self._task is not None and not self._task.done():
            try:
                # shield：超时只放弃等待，不要把读取任务本身取消掉
                await asyncio.wait_for(asyncio.shield(self._task), timeout=grace)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        return b"".join(self._chunks)


# 模块级单例（懒初始化）
_risk_scorer = RiskScorer()
_sandbox_executor: SandboxExecutor | None = None
_sandbox_auditor: SandboxAuditor | None = None


def get_sandbox_executor(settings: Any = None) -> SandboxExecutor:
    """获取沙箱执行器单例。"""
    global _sandbox_executor
    if _sandbox_executor is None:
        config = SandboxConfig.from_settings(settings) if settings else SandboxConfig()
        _sandbox_executor = SandboxExecutor(config)
    return _sandbox_executor


def get_sandbox_auditor(settings: Any = None) -> SandboxAuditor:
    """获取沙箱审计器单例。"""
    global _sandbox_auditor
    if _sandbox_auditor is None:
        enabled = getattr(settings, "sandbox_audit", True) if settings else True
        _sandbox_auditor = SandboxAuditor(enabled=enabled)
    return _sandbox_auditor


def _hold_pending_reason(command: str, settings: Any) -> str:
    """待发窗口未结案时，给出“这条命令不能自动放行”的理由；不需要收紧时返回空。

    为什么必须有这一段（W3.2 绕道收紧，口径 A 收窄版）：延时窗口拦的是一条
    tool_use，“把这件事做成”是模型的目标。实机测过（2026-10-21）：只靠返回文案
    约束时，模型会换 `Bash curl` 把被拦下的外发动作真做掉，而沙箱对中风险
    命令默认自动放行——用户以为拦住了，实际已发生。所以预约未结案期间，网络
    外发类命令的“自动放行”这条捷径关掉，改问人；其余命令不受影响。
    关掉 `actions.hold_blocks_equivalent` 即退回“只靠文案约束”的旧行为。
    @author aceFelix
    """
    if settings is not None and not getattr(settings, "actions_hold_blocks_equivalent", True):
        return ""
    if not looks_network_outbound(command):
        return ""    # 收窄：只看外发通路，不相干的命令一律照旧
    from agent.core.actions import get_window

    if get_window().size <= 0:
        return ""
    labels = get_window().pending_labels()
    what = labels[0][:40] if labels else "一项不可逆动作"
    return (
        f"有 {len(labels) or 1} 项动作在待发窗口里尚未执行（如：{what}），"
        f"该命令可能达成同一目的，不再自动放行：{command}"
    )


class BashTool(Tool):
    name = "Bash"
    description = (
        "执行 shell 命令并返回输出。命令在工作目录下执行。"
        "Windows 上使用 Git Bash（bash -c），支持 Unix 风格路径和命令。"
        "默认会询问用户确认；只读命令（ls/cat/grep 等）和用户配置了 allow 规则的命令会自动放行。"
        "有超时保护（默认 120 秒）。"
        "启动 GUI 程序或常驻服务（浏览器、编辑器、dev server 等）必须写成后台形式并丢弃输出，"
        "如 `firefox.exe https://x.com > /dev/null 2>&1 &`；写成前台会一直挂到程序退出为止。"
        "命令非零退出属正常结果，会以 [exit=N] 表头原样返回输出，由你自行判断下一步。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
            "timeout": {
                "type": "integer",
                "description": "超时秒数（默认 120，最大 600）",
                "minimum": 1,
                "maximum": 600,
            },
            "cwd": {
                "type": "string",
                "description": "工作目录（绝对路径，如 E:\\\\project）。留空则用默认工作目录。",
            },
        },
        "required": ["command"],
    }
    max_result_chars = 20_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        # 用分类器判断（readonly 才算只读）
        from agent.permissions.shell_classifier import classify
        return classify(args.get("command", "")) == "readonly"

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        # 只读命令可并行；写类命令保守起见不可并行
        return self.is_read_only(args)

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 工具特判: 用风险评分器 + 分类器快速决定
        from agent.permissions.shell_classifier import classify
        cmd = args.get("command", "")
        kind = classify(cmd)
        if kind == "dangerous":
            return PermissionResult.deny(f"命令匹配危险模式: {cmd}")
        if kind == "readonly":
            return PermissionResult.allow("只读命令自动放行")

        # P3-8 沙箱感知: 沙箱开启时，中等风险命令可自动放行
        risk = _risk_scorer.score_command(cmd)
        settings = ctx.settings
        sandbox_enabled = getattr(settings, "sandbox_enabled", False) if settings else False
        auto_allow_medium = getattr(settings, "sandbox_auto_allow_medium", True) if settings else True

        if sandbox_enabled and auto_allow_medium and risk == RiskLevel.MEDIUM:
            # 预约未结案时不许走“自动放行”这条捷径（口径与代价见上面函数）
            blocked = _hold_pending_reason(cmd, settings)
            if blocked:
                return PermissionResult.ask(blocked)
            # reason 前缀是跨层协议标记：/away 时 W1 闸门据此把这类
            # 自动放行升级回 ASK 走待批（详见 permissions/modes.py）
            return PermissionResult.allow(
                f"{SANDBOX_AUTO_ALLOW_PREFIX} 沙箱已开启，中等风险命令在沙箱内执行"
            )

        # unknown 一律 ASK（fail-closed）
        return PermissionResult.ask(f"需要确认命令: {cmd}")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext):
        from agent.core.result import ValidationResult
        if not args.get("command", "").strip():
            return ValidationResult.fail("command 不能为空")
        return ValidationResult.pass_()

    def prepare_permission_matcher(
        self, args: dict[str, Any]
    ) -> PermissionMatcher | None:
        """解析命令头，让 "Bash(git *)" 这类规则能命中。"""
        cmd = args.get("command", "")
        head = get_command_head(cmd)
        # targets 同时给完整命令和命令头，匹配更宽松
        return PermissionMatcher(tool_name="Bash", targets=[cmd, head])

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = args["command"]
        timeout = min(600, max(1, int(args.get("timeout", 120))))

        # 工作目录：优先用 args["cwd"]，其次 ctx.workdir
        work_dir = args.get("cwd", "") or ctx.workdir

        # P3-8: 沙箱执行路径
        settings = ctx.settings
        sandbox_enabled = getattr(settings, "sandbox_enabled", False) if settings else False
        risk = _risk_scorer.score_command(command)

        # 后台化命令（以 & 结尾）不进沙箱。
        # 沙箱在 Windows 上就是一个 Job Object，且带 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE：
        # 命令一结束就把组内**所有**进程杀干净。而这类命令的语义恰恰是
        # 「启动后不管」（开浏览器、起 dev server）——进沙箱等于必然被回收，
        # 实机表现就是「窗口开了又消失了」。模型当时只能靠 schtasks 委托系统服务
        # 绕出去，而那是沙箱逃逸，不该是产品的解法。
        # 风险仍由权限层兜住：HIGH/CRITICAL 照常 ASK，MEDIUM 走已有的 auto_allow 规则。
        # 注意判据是「是否后台命令」而非「有没有被改写」：模型自己写全
        # `> /dev/null 2>&1 &` 时不会被改写，但它同样需要跳过沙箱。
        # @author aceFelix
        _run_cmd, is_background = _detach_background(command)

        if sandbox_enabled and risk.needs_sandbox and not is_background:
            return await self._call_sandboxed(command, work_dir, timeout, risk, ctx)

        # 普通执行路径
        return await self._call_normal(command, work_dir, timeout, ctx)

    async def _call_sandboxed(
        self, command: str, work_dir: str, timeout: int, risk: RiskLevel, ctx: ToolContext
    ) -> ToolResult:
        """在沙箱中执行命令。"""
        executor = get_sandbox_executor(ctx.settings)
        auditor = get_sandbox_auditor(ctx.settings)

        # 高风险操作先做文件快照
        snapshot_id = ""
        if risk.needs_snapshot:
            from agent.core.sandbox.file_guard import FileGuard
            guard = FileGuard(
                max_snapshots=getattr(ctx.settings, "sandbox_max_snapshots", 20) if ctx.settings else 20
            )
            snapshot_id = guard.snapshot(work_dir, reason=f"沙箱执行: {command[:60]}")
            auditor.log_snapshot(snapshot_id, [work_dir], reason=command[:60])

        # 与 ``ctx.abort_event`` 竞速：``_call_normal`` 早就有这一层，沙箱路径
        # 一直漏着。后果是沙箱里跑长命令时 Ctrl+C 完全无效——实机 2026-10：
        # 语音模式下命令超时后又要重试一轮，总等待两百多秒，用户按 Ctrl+C
        # 毫无反应，最后只能强杀进程退出。
        # 进程树的回收仍在 executor 内部做（它管着 Job Object），这里只负责
        # 「及时放弃等待」并显式告知用户是被中断的。
        # @author aceFelix
        run_task = asyncio.ensure_future(
            executor.run(command, cwd=work_dir, timeout=timeout)
        )
        abort_wait = asyncio.ensure_future(ctx.abort_event.wait())
        try:
            await asyncio.wait(
                {run_task, abort_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            # 兼查 is_set()：abort_event 由工作线程置位，跨线程唤醒未必可靠。
            # 但 run_task 已完成时优先取结果（命令已经跑完了，不算中断）。
            if ctx.abort_event.is_set() and not run_task.done():
                run_task.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(run_task, timeout=_KILL_REAP_TIMEOUT_S)
                return ToolResult.error(
                    f"已取消（用户中断）: {command[:80]}", retryable=False
                )
            result = run_task.result()
        finally:
            abort_wait.cancel()

        # 审计记录
        auditor.log_execution(
            command=command,
            risk_level=risk.name,
            sandboxed=result.sandboxed,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            resource_exceeded=result.resource_exceeded,
            cwd=work_dir,
        )

        # 超时判定**必须在 error 之前**（2026-10 实测卡死根因）：executor 超时会
        # 同时设 ``timed_out=True`` 与 ``error="命令超时（...）"``，所以 `if result.error`
        # 先命中并返回了一个 **retryable 默认 True** 的 ToolResult，下面这条
        # retryable=False 的分支永远执行不到。后果就是超时被自愈层当成可重试，
        # 明明已经等满 120 秒，还要再等一轮——用户看到的是「卡住 + 退不出去」。
        # @author aceFelix
        if result.timed_out:
            # retryable=False：超时意味着命令真跑满了时长，重试只会再等一遍、
            # 副作用还多出一次（前台拉起 GUI 程序尤其典型），该交回模型换方案。
            return ToolResult.error(f"[沙箱] 命令超时（{timeout}秒）: {command[:80]}", retryable=False)

        if result.error:
            return ToolResult.error(f"[沙箱] {result.error}")

        # 组装输出
        parts = []
        if result.stdout:
            parts.append(result.stdout.rstrip())
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        body = "\n\n".join(parts) if parts else "(无输出)"

        sandbox_tag = "🛡️沙箱" if result.sandboxed else "普通"
        header = f"[exit={result.exit_code} | {sandbox_tag} | 风险={risk.label} | cwd={work_dir}]\n"

        if result.resource_exceeded:
            header += "⚠️ 资源超限（内存/进程数），进程已被终止\n"

        # 非零退出是命令的正常业务结果（curl 连不上=7、grep 无匹配=1、&& 断链等），
        # 带 [exit=N] 表头原样回传给大模型自行决策；不标记 is_error，避免自愈层
        # 把它误分类为「未知错误」后重试 + 弹「是否重试」阻塞问句导致整轮卡死。
        # 真正的工具级失败（找不到 shell/超时/沙箱错误）仍走上面的 error 分支。
        # @author aceFelix
        return ToolResult.ok(data=header + body)

    async def _call_normal(
        self, command: str, work_dir: str, timeout: int, ctx: ToolContext
    ) -> ToolResult:
        """普通执行（无沙箱）。

        执行期间与 ``ctx.abort_event`` 竞速：工具层是长操作里唯一能感知「用户按了
        Ctrl+C / 点了停止」的地方（``core/context.py`` 的契约就是要求工具自己检查
        它）。不这么做的话，命令跑满 ``timeout`` 期间中断完全无效——实机上表现为
        「语音模式让 jarvis 打开浏览器，Ctrl+C 一点反应都没有，只能干等 120 秒」。

        @author aceFelix
        """
        is_win = platform.system() == "Windows"
        # 后台化命令先让它真正脱离：否则 git bash 会等那个后台 job，
        # 「打开网页」也变成等满 timeout（详见 _detach_background）
        run_command, is_background = _detach_background(command)
        if is_win:
            # 查找 Git Bash（优先用 PATH 里的 bash）
            bash_path = self._find_bash()
            if bash_path:
                shell_args = [bash_path, "-c", run_command]
            else:
                # 回退到 cmd（不推荐，路径处理会有问题）
                shell_args = ["cmd", "/c", run_command]
        else:
            shell_args = ["/bin/bash", "-c", run_command]

        try:
            # stdin 必须显式 DEVNULL，不能继承宿主 stdin：serve 宿主的 stdin 是
            # Electron 永不关闭的管道且有 _watch_stdin 线程阻塞读，MSYS2 bash
            # 继承该管道后 Cygwin 初始化探测 stdin 句柄会挂死（bash 永不退出、
            # 管道 EOF 永不到达），见 docs/fixlogs/serve-bash-hang-fix.md。
            # @author aceFelix
            proc = await asyncio.create_subprocess_exec(
                *shell_args,
                cwd=work_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            return ToolResult.error(f"找不到 shell: {e}")

        # 立即开始吸走两个管道：既防大输出把 shell 卡在 write 上（死锁），
        # 又为后面的「不等 EOF」收尾做好准备。详见 _StreamCollector。
        out_c = _StreamCollector(proc.stdout)
        err_c = _StreamCollector(proc.stderr)
        out_c.start()
        err_c.start()

        # 等「shell 进程退出」，而不是等「管道 EOF」。
        # 两者不等价：`firefox.exe URL &` 让 shell 立即退出，但 firefox 继承了
        # stdout/stderr 写端，管道 EOF 永不到来——旧实现用 communicate() 等 EOF，
        # 于是打开一个网页也要卡满 timeout。这是本次现象的根因。
        # @author aceFelix
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        wait_task: asyncio.Future | None = None
        outcome = "timeout"
        try:
            wait_task = asyncio.ensure_future(proc.wait())
            abort_wait = asyncio.ensure_future(ctx.abort_event.wait())
            try:
                while True:
                    done, _pending = await asyncio.wait(
                        {wait_task, abort_wait},
                        timeout=_PROCESS_POLL_SEC,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if wait_task in done:
                        outcome = "done"
                        break
                    # 兼查 is_set()：abort_event 由工作线程置位，
                    # 跨线程唤醒 waiter 未必可靠，靠这一拍兜底
                    if ctx.abort_event.is_set():
                        outcome = "aborted"
                        break
                    if loop.time() >= deadline:
                        break  # outcome 保持 timeout
            finally:
                abort_wait.cancel()
        except asyncio.CancelledError:
            # 用户中断回复（reply.abort 取消）：回收子进程避免孤儿，再抛取消
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            if wait_task is not None:
                wait_task.cancel()
            raise

        if outcome != "done":
            # 中断或超时：先回收进程，再区分两者的回执
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            if wait_task is not None:
                # 回收等待任务，但只给短宽限：Windows 上 TerminateProcess 后
                # 子进程（如 MSYS2 的 sleep）可能还握着管道，
                # asyncio 的 wait() 未必立即返回——这里无限等的话，
                # 用户按了 Ctrl+C 仍然会「没反应」。
                try:
                    await asyncio.wait_for(wait_task, timeout=_KILL_REAP_TIMEOUT_S)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    wait_task.cancel()
                except Exception:
                    pass
            # 顺手收掉两个读取任务，避免遗留 pending task
            await asyncio.gather(out_c.finish(0.0), err_c.finish(0.0))
            # 显式关闭子进程 transport：进入这里意味着命令被打断/超时，可能还有
            # 孙进程握着管道，不关会报 unclosed transport（ResourceWarning）。
            # asyncio 没有公开的关闭接口，只能取 _transport。
            transport = getattr(proc, "_transport", None)
            if transport is not None:
                with contextlib.suppress(Exception):
                    transport.close()
            if outcome == "aborted":
                return ToolResult.error(
                    f"已取消（用户中断）: {command[:80]}", retryable=False
                )
            # 超时同样标 retryable=False：命令已真跑满时长，重试只是再等一遍，
            # 且副作用会重复（实机：前台开浏览器被重试了两次，两个窗口两个标签页）。
            return ToolResult.error(
                f"命令超时（{timeout}秒）: {command}{_TIMEOUT_HINT}",
                retryable=False,
            )

        # shell 已退出：取走两个管道里已收到的输出（不等 EOF）
        out_b, err_b = await asyncio.gather(out_c.finish(), err_c.finish())
        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        code = proc.returncode

        parts = []
        if out:
            parts.append(out.rstrip())
        if err:
            parts.append(f"[stderr]\n{err.rstrip()}")
        body = "\n\n".join(parts) if parts else "(无输出)"

        header = f"[exit={code} | cwd={work_dir}]\n"
        if is_background:
            header += (
                "（已在后台启动：命令已脱离本工具，其后续输出不再捕获；"
                "后台命令不进沙箱，否则会被 Job Object 回收）\n"
            )
        # 同上：非零退出属正常命令结果，原样回传交模型判断，不进自愈/交互阻塞链路。
        # @author aceFelix
        return ToolResult.ok(data=header + body)

    @staticmethod
    def _find_bash() -> str | None:
        """查找 Git Bash 可执行文件路径（跳过 WSL relay）。"""
        import shutil

        # 1. 优先检查用户 Git 安装路径（避免 WSL relay 误导）
        for candidate in (
            r"D:\SoftwareDevelopmentKit\Git\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if os.path.isfile(candidate):
                return candidate

        # 2. PATH 里的 bash（排除 System32 下的 WSL relay）
        path = shutil.which("bash")
        if path:
            normalized = os.path.normpath(path).lower()
            if "system32" not in normalized and "syswow64" not in normalized:
                return path

        # 3. 常见便携 Git 路径
        home = os.path.expanduser("~")
        for sub in (".workbuddy/vendor/PortableGit/bin/bash.exe",
                    ".workbuddy/vendor/PortableGit/usr/bin/bash.exe"):
            candidate = os.path.join(home, sub)
            if os.path.isfile(candidate):
                return candidate

        return None

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        if args and args.get("command"):
            cmd = args["command"]
            return f"运行 {cmd[:60]}{'...' if len(cmd) > 60 else ''}"
        return None
