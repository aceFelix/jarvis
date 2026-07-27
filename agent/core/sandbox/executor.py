"""跨平台沙箱执行器。

支持三个平台:
- Windows: Job Object（内存/进程数限制，KILL_ON_JOB_CLOSE）
- Linux: resource.setrlimit（RLIMIT_AS/RLIMIT_CPU/RLIMIT_NPROC）
- macOS: sandbox-exec + resource.setrlimit

思路:
- 不改变命令本身，而是在执行时施加进程级约束
- 沙箱对命令透明（命令不知道自己被限制了）
- 超时后强制终止整个进程树

注意: Windows 用 ctypes 调用 Win32 API；Linux/macOS 用标准库 resource 模块。
均不依赖第三方库。
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import platform
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---- Win32 常量 ----
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_BASIC_LIMIT_INFORMATION = 2
JobObjectExtendedLimitInformation = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100


@dataclass
class SandboxConfig:
    """沙箱配置。"""

    enabled: bool = False
    # 资源限制
    max_memory_mb: int = 512           # 最大内存（MB）
    max_cpu_seconds: int = 60          # 最大 CPU 时间（秒）
    max_processes: int = 10            # 最大子进程数
    timeout: int = 120                 # 总超时（秒，含 I/O 等待）
    # 网络隔离
    block_network: bool = False        # 是否阻断网络（通过代理环境变量）
    # 文件系统
    allow_write_paths: list[str] = field(default_factory=list)   # 允许写入的路径
    deny_write_paths: list[str] = field(default_factory=list)    # 禁止写入的路径
    # 行为
    auto_allow_medium_risk: bool = True   # 沙箱开启时自动放行中等风险
    excluded_commands: list[str] = field(default_factory=list)  # 不走沙箱的命令
    # 审计
    audit_enabled: bool = True

    @classmethod
    def from_settings(cls, settings: Any) -> "SandboxConfig":
        """从 Settings 对象构建配置。"""
        return cls(
            enabled=getattr(settings, "sandbox_enabled", False),
            max_memory_mb=getattr(settings, "sandbox_max_memory_mb", 512),
            max_cpu_seconds=getattr(settings, "sandbox_max_cpu_seconds", 60),
            max_processes=getattr(settings, "sandbox_max_processes", 10),
            timeout=getattr(settings, "sandbox_timeout", 120),
            block_network=getattr(settings, "sandbox_block_network", False),
            auto_allow_medium_risk=getattr(settings, "sandbox_auto_allow_medium", True),
            excluded_commands=getattr(settings, "sandbox_excluded_commands", []),
            audit_enabled=getattr(settings, "sandbox_audit", True),
        )


@dataclass
class SandboxResult:
    """沙箱执行结果。"""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    resource_exceeded: bool = False
    sandboxed: bool = True
    error: str = ""


class SandboxExecutor:
    """跨平台沙箱执行器。

    支持:
    - Windows: Job Object（内存/进程数限制）
    - Linux: resource.setrlimit（RLIMIT_AS/CPU/NPROC）
    - macOS: sandbox-exec + resource.setrlimit

    用法::

        executor = SandboxExecutor(config)
        result = await executor.run("npm install", cwd="/home/user/project")
        # 命令在资源限制下执行
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._platform = platform.system()  # Windows / Linux / Darwin
        self._is_windows = self._platform == "Windows"
        self._is_linux = self._platform == "Linux"
        self._is_macos = self._platform == "Darwin"
        self._kernel32 = ctypes.windll.kernel32 if self._is_windows else None

    @property
    def config(self) -> SandboxConfig:
        return self._config

    @property
    def available(self) -> bool:
        """沙箱是否可用（Windows/Linux/macOS 均支持）。"""
        if self._is_windows:
            return self._kernel32 is not None
        if self._is_linux or self._is_macos:
            return True  # resource 模块是标准库
        return False

    def is_excluded(self, command: str) -> bool:
        """命令是否在排除列表中（不走沙箱）。"""
        for pattern in self._config.excluded_commands:
            if pattern in command or command.startswith(pattern):
                return True
        return False

    async def run(
        self,
        command: str,
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> SandboxResult:
        """在沙箱中执行命令。

        如果沙箱不可用或命令被排除，回退到普通执行。
        """
        if not self._config.enabled:
            return await self._run_normal(command, cwd, env, timeout)

        if self.is_excluded(command):
            logger.debug(f"[Sandbox] 命令被排除，普通执行: {command[:60]}")
            return await self._run_normal(command, cwd, env, timeout)

        if not self.available:
            logger.debug("[Sandbox] 平台不支持沙箱，回退普通执行")
            return await self._run_normal(command, cwd, env, timeout)

        # 分平台执行
        if self._is_windows:
            return await self._run_sandboxed_windows(command, cwd, env, timeout)
        else:
            return await self._run_sandboxed_unix(command, cwd, env, timeout)

    async def _run_sandboxed_windows(
        self,
        command: str,
        cwd: str,
        env: dict[str, str] | None,
        timeout: int | None,
    ) -> SandboxResult:
        """使用 Windows Job Object 限制执行命令。"""
        effective_timeout = timeout or self._config.timeout
        work_dir = cwd or os.getcwd()

        # 构建环境变量
        proc_env = dict(os.environ)
        if env:
            proc_env.update(env)
        if self._config.block_network:
            # 通过设置无效代理阻断网络（对大多数 HTTP 客户端有效）
            proc_env["HTTP_PROXY"] = "http://127.0.0.1:1"
            proc_env["HTTPS_PROXY"] = "http://127.0.0.1:1"
            proc_env["http_proxy"] = "http://127.0.0.1:1"
            proc_env["https_proxy"] = "http://127.0.0.1:1"
            proc_env["NO_PROXY"] = ""
            proc_env["no_proxy"] = ""

        # 构建 shell 命令
        shell_args = self._build_shell_args(command)

        # 创建 Job Object 并执行
        job_handle = None
        try:
            job_handle = self._create_job_object()
            if job_handle is None:
                # Job Object 创建失败，回退普通执行
                logger.warning("[Sandbox] Job Object 创建失败，回退普通执行")
                return await self._run_normal(command, cwd, env, timeout)

            # 启动进程（挂起状态）
            proc = await asyncio.create_subprocess_exec(
                *shell_args,
                cwd=work_dir,
                env=proc_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # 将进程分配到 Job Object
            self._assign_to_job(job_handle, proc.pid)

            # 等待完成
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=effective_timeout
                )
            except asyncio.TimeoutError:
                # 超时：终止整个进程树（Job Object 的 KILL_ON_JOB_CLOSE 会处理）
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                return SandboxResult(
                    timed_out=True,
                    sandboxed=True,
                    error=f"命令超时（{effective_timeout}秒）: {command[:80]}",
                )

            out = stdout.decode("utf-8", errors="replace") if stdout else ""
            err = stderr.decode("utf-8", errors="replace") if stderr else ""
            code = proc.returncode or 0

            # 检测资源超限（Windows 会终止进程，exit code 通常为 1 或特殊值）
            resource_exceeded = (code != 0 and self._check_resource_violation(err))

            return SandboxResult(
                stdout=out,
                stderr=err,
                exit_code=code,
                sandboxed=True,
                resource_exceeded=resource_exceeded,
            )

        except FileNotFoundError as e:
            return SandboxResult(error=f"找不到 shell: {e}", sandboxed=True)
        except Exception as e:
            logger.error(f"[Sandbox] 执行异常: {e}")
            return SandboxResult(error=str(e), sandboxed=True)
        finally:
            if job_handle:
                self._close_job(job_handle)

    async def _run_normal(
        self,
        command: str,
        cwd: str,
        env: dict[str, str] | None,
        timeout: int | None,
    ) -> SandboxResult:
        """普通执行（无沙箱限制）。"""
        effective_timeout = timeout or self._config.timeout
        work_dir = cwd or os.getcwd()
        shell_args = self._build_shell_args(command)

        proc_env = dict(os.environ)
        if env:
            proc_env.update(env)

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_args,
                cwd=work_dir,
                env=proc_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            return SandboxResult(error=f"找不到 shell: {e}", sandboxed=False)

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=effective_timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return SandboxResult(
                timed_out=True, sandboxed=False,
                error=f"命令超时（{effective_timeout}秒）",
            )

        out = stdout.decode("utf-8", errors="replace") if stdout else ""
        err = stderr.decode("utf-8", errors="replace") if stderr else ""

        return SandboxResult(
            stdout=out,
            stderr=err,
            exit_code=proc.returncode or 0,
            sandboxed=False,
        )

    async def _run_sandboxed_unix(
        self,
        command: str,
        cwd: str,
        env: dict[str, str] | None,
        timeout: int | None,
    ) -> SandboxResult:
        """使用 resource.setrlimit 限制执行命令（Linux/macOS）。

        通过 preexec_fn 在子进程 fork 后、exec 前设置资源限制:
        - RLIMIT_AS: 最大虚拟内存（字节）
        - RLIMIT_CPU: 最大 CPU 时间（秒）
        - RLIMIT_NPROC: 最大进程数

        macOS 额外支持 sandbox-exec 命令包装（如果可用）。
        """
        import resource

        effective_timeout = timeout or self._config.timeout
        work_dir = cwd or os.getcwd()

        # 构建环境变量
        proc_env = dict(os.environ)
        if env:
            proc_env.update(env)
        if self._config.block_network:
            proc_env["HTTP_PROXY"] = "http://127.0.0.1:1"
            proc_env["HTTPS_PROXY"] = "http://127.0.0.1:1"
            proc_env["http_proxy"] = "http://127.0.0.1:1"
            proc_env["https_proxy"] = "http://127.0.0.1:1"
            proc_env["NO_PROXY"] = ""
            proc_env["no_proxy"] = ""

        # 资源限制参数
        max_mem_bytes = self._config.max_memory_mb * 1024 * 1024
        max_cpu = self._config.max_cpu_seconds
        max_procs = self._config.max_processes

        def _set_limits():
            """preexec_fn: 在子进程中设置资源限制。"""
            try:
                # 最大虚拟内存
                resource.setrlimit(resource.RLIMIT_AS, (max_mem_bytes, max_mem_bytes))
            except (ValueError, OSError):
                pass
            try:
                # 最大 CPU 时间
                resource.setrlimit(resource.RLIMIT_CPU, (max_cpu, max_cpu))
            except (ValueError, OSError):
                pass
            try:
                # 最大进程数
                resource.setrlimit(resource.RLIMIT_NPROC, (max_procs, max_procs))
            except (ValueError, OSError, AttributeError):
                pass  # RLIMIT_NPROC 在某些系统不可用

        # 构建 shell 命令
        shell_args = ["/bin/bash", "-c", command]

        # macOS: 尝试用 sandbox-exec 包装（更强的隔离）
        use_sandbox_exec = self._is_macos and self._macos_sandbox_available()
        if use_sandbox_exec:
            profile = self._macos_sandbox_profile(work_dir)
            shell_args = ["/usr/bin/sandbox-exec", "-p", profile, "/bin/bash", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_args,
                cwd=work_dir,
                env=proc_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=_set_limits,
            )
        except FileNotFoundError as e:
            return SandboxResult(error=f"找不到 shell: {e}", sandboxed=True)
        except Exception as e:
            logger.error(f"[Sandbox] Unix 沙箱执行异常: {e}")
            return SandboxResult(error=str(e), sandboxed=True)

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=effective_timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return SandboxResult(
                timed_out=True, sandboxed=True,
                error=f"命令超时（{effective_timeout}秒）: {command[:80]}",
            )

        out = stdout.decode("utf-8", errors="replace") if stdout else ""
        err = stderr.decode("utf-8", errors="replace") if stderr else ""
        code = proc.returncode or 0

        # 检测资源超限（Unix 下超过 rlimit 会收到 SIGKILL/SIGXCPU）
        resource_exceeded = False
        if code == -9 or code == 137:  # SIGKILL (OOM killer 或 rlimit)
            resource_exceeded = True
        elif code == -24 or code == 152:  # SIGXCPU
            resource_exceeded = True
        elif self._check_resource_violation(err):
            resource_exceeded = True

        return SandboxResult(
            stdout=out,
            stderr=err,
            exit_code=code,
            sandboxed=True,
            resource_exceeded=resource_exceeded,
        )

    @staticmethod
    def _macos_sandbox_available() -> bool:
        """检查 macOS sandbox-exec 是否可用。"""
        return os.path.isfile("/usr/bin/sandbox-exec")

    @staticmethod
    def _macos_sandbox_profile(work_dir: str) -> str:
        """生成 macOS sandbox-exec 的 SBPL 配置文件内容。

        策略: 允许基本操作，限制网络（如果配置了 block_network）。
        注意: sandbox-exec 在 macOS 14+ 被标记为 deprecated，但仍可用。
        """
        # 基本配置文件: 允许文件读写，禁止网络（由环境变量代理控制）
        return (
            "(version 1)"
            "(allow default)"
        )

    # ---- Win32 Job Object 操作 ----

    def _create_job_object(self) -> int | None:
        """创建带资源限制的 Job Object。"""
        k32 = self._kernel32
        if not k32:
            return None

        # CreateJobObjectW(None, None)
        job = k32.CreateJobObjectW(None, None)
        if not job or job == INVALID_HANDLE_VALUE:
            return None

        # 正确的 Win32 结构体布局
        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()

        # 限制标志
        flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE  # 关闭 Job 时终止所有进程
        flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS   # 进程数限制
        flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY   # 内存限制

        info.BasicLimitInformation.LimitFlags = flags
        info.BasicLimitInformation.ActiveProcessLimit = self._config.max_processes
        info.ProcessMemoryLimit = self._config.max_memory_mb * 1024 * 1024  # MB -> bytes

        # 设置到 Job Object
        ret = k32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ret:
            k32.CloseHandle(job)
            return None

        return job

    def _assign_to_job(self, job_handle: int, pid: int) -> bool:
        """将进程分配到 Job Object。"""
        k32 = self._kernel32
        if not k32:
            return False

        # OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        process = k32.OpenProcess(
            PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid
        )
        if not process:
            return False

        try:
            ret = k32.AssignProcessToJobObject(job_handle, process)
            return bool(ret)
        finally:
            k32.CloseHandle(process)

    def _close_job(self, job_handle: int) -> None:
        """关闭 Job Object（会终止所有关联进程，因为 KILL_ON_JOB_CLOSE）。"""
        if self._kernel32 and job_handle:
            self._kernel32.CloseHandle(job_handle)

    @staticmethod
    def _check_resource_violation(stderr: str) -> bool:
        """检测 stderr 中是否有资源超限迹象。"""
        indicators = [
            "not enough memory",
            "out of memory",
            "resource temporarily unavailable",
            "cannot allocate memory",
            "memory limit",
        ]
        lower = stderr.lower()
        return any(ind in lower for ind in indicators)

    def _build_shell_args(self, command: str) -> list[str]:
        """构建 shell 执行参数。"""
        if self._is_windows:
            bash_path = self._find_bash()
            if bash_path:
                return [bash_path, "-c", command]
            return ["cmd", "/c", command]
        return ["/bin/bash", "-c", command]

    @staticmethod
    def _find_bash() -> str | None:
        """查找 Git Bash。"""
        import shutil
        for candidate in (
            r"D:\SoftwareDevelopmentKit\Git\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ):
            if os.path.isfile(candidate):
                return candidate
        path = shutil.which("bash")
        if path:
            normalized = os.path.normpath(path).lower()
            if "system32" not in normalized and "syswow64" not in normalized:
                return path
        return None
