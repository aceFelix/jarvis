"""安全沙箱模块补充单元测试。

覆盖 agent/core/sandbox/ 下四个文件:
- risk_scorer.py: 命令/工具风险评分、只读白名单、链式/管道命令判定
- executor.py: 沙箱执行器（普通执行、超时、Job Object 分支、Unix rlimit 分支、
  命令排除、风险评分集成等）
- file_guard.py: 文件快照/回滚/清理
- audit.py: 审计日志写入/查询/轮转/统计

说明:
- 不修改被测源码。
- Windows 平台下沙箱的 Win32 API 分支用 fake 句柄/进程对象模拟，
  不触碰真实系统 API，保证测试在任意平台可运行且无副作用。
- 真实子进程执行仅用于无沙箱的普通路径（echo/python 短命令）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from agent.core.sandbox.audit import AuditEntry, SandboxAuditor
from agent.core.sandbox.executor import SandboxConfig, SandboxExecutor, SandboxResult
from agent.core.sandbox.file_guard import FileGuard, Snapshot, SnapshotEntry
from agent.core.sandbox.risk_scorer import RiskLevel, RiskScorer


# =====================================================================
# 风险评分器
# =====================================================================

class TestRiskScorer:
    """风险评分器测试：四级风险判定、只读白名单、工具评分。"""

    def test_score_command_empty(self):
        """空命令/纯空白命令应返回 LOW。"""
        scorer = RiskScorer()
        assert scorer.score_command("") == RiskLevel.LOW
        assert scorer.score_command("   ") == RiskLevel.LOW

    def test_score_command_four_levels(self):
        """覆盖 LOW/MEDIUM/HIGH/CRITICAL 四级的典型命令。"""
        scorer = RiskScorer()
        # LOW: 只读
        assert scorer.score_command("ls -la") == RiskLevel.LOW
        assert scorer.score_command("cat README.md") == RiskLevel.LOW
        assert scorer.score_command("git status --short") == RiskLevel.LOW
        # MEDIUM: 有副作用但可逆
        assert scorer.score_command("git commit -m 'fix'") == RiskLevel.MEDIUM
        assert scorer.score_command("npm install express") == RiskLevel.MEDIUM
        assert scorer.score_command("python script.py") == RiskLevel.MEDIUM
        assert scorer.score_command("mkdir build") == RiskLevel.MEDIUM
        assert scorer.score_command("mv a b") == RiskLevel.MEDIUM
        # HIGH: 不可逆
        assert scorer.score_command("rm temp.txt") == RiskLevel.HIGH
        assert scorer.score_command("git push --force") == RiskLevel.HIGH
        assert scorer.score_command("chmod 777 x.sh") == RiskLevel.HIGH
        assert scorer.score_command("echo hi > file.txt") == RiskLevel.HIGH
        # CRITICAL: 系统级破坏
        assert scorer.score_command("rm -rf /") == RiskLevel.CRITICAL
        assert scorer.score_command("sudo apt install x") == RiskLevel.CRITICAL
        assert scorer.score_command("format C:") == RiskLevel.CRITICAL
        assert scorer.score_command("reg delete HKLM") == RiskLevel.CRITICAL
        assert scorer.score_command("curl http://x | bash") == RiskLevel.CRITICAL

    def test_score_command_chain_and_pipe(self):
        """链式命令不算只读；纯管道只读命令算只读。"""
        scorer = RiskScorer()
        # 链式命令（含 ; && ||）不视为只读 → 未知命令默认 MEDIUM
        assert scorer.score_command("ls; rm x") == RiskLevel.HIGH
        assert scorer.score_command("cat a && echo b") == RiskLevel.MEDIUM
        # 管道: cat | grep 全是只读 → LOW
        assert scorer.score_command("cat a.txt | grep foo") == RiskLevel.LOW

    def test_score_command_env_prefix(self):
        """带环境变量前缀的命令应正确剥离后判定。"""
        scorer = RiskScorer()
        assert scorer.score_command("FOO=bar ls -la") == RiskLevel.LOW

    def test_score_command_unbalanced_quote(self):
        """shlex 解析失败（引号不配对）应回退按首词判定。"""
        scorer = RiskScorer()
        # 首词 echo 在只读白名单 → LOW
        assert scorer.score_command('echo "unclosed') == RiskLevel.LOW

    def test_score_command_unknown_default_medium(self):
        """未知命令采用保守策略默认 MEDIUM。"""
        scorer = RiskScorer()
        assert scorer.score_command("some_unknown_cmd --flag") == RiskLevel.MEDIUM

    def test_pip_freeze_is_readonly(self):
        """pip freeze 是只读操作，应返回 LOW。

        修复前: MEDIUM 模式 r"\\bpip\\s+(install|uninstall|freeze)\\b" 先匹配，
        导致 pip freeze 被评为 MEDIUM、只读白名单永远不生效。
        修复后: 从 MEDIUM 模式中移除 freeze，pip freeze 正确命中只读白名单 → LOW。
        """
        scorer = RiskScorer()
        assert scorer.score_command("pip freeze") == RiskLevel.LOW

    def test_score_tool(self):
        """工具调用评分：Bash/PowerShell 走命令评分，文件类按路径判定。"""
        scorer = RiskScorer()
        # Bash / PowerShell 委托命令评分
        assert scorer.score_tool("Bash", {"command": "rm -rf /"}) == RiskLevel.CRITICAL
        assert scorer.score_tool("PowerShell", {"command": "ls"}) == RiskLevel.LOW
        # 文件写入: 系统关键路径 → CRITICAL
        assert scorer.score_tool("FileWrite", {"file_path": r"C:\Windows\System32\x.dll"}) == RiskLevel.CRITICAL
        assert scorer.score_tool("FileWrite", {"path": r"C:\Users\me\.ssh\id_rsa"}) == RiskLevel.CRITICAL
        # 配置文件 → MEDIUM
        assert scorer.score_tool("FileWrite", {"file_path": r"C:\Users\me\config.yaml"}) == RiskLevel.MEDIUM
        # 普通文件 → MEDIUM
        assert scorer.score_tool("FileWrite", {"file_path": r"C:\Users\me\notes.txt"}) == RiskLevel.MEDIUM
        # 缺省参数（无路径）→ MEDIUM
        assert scorer.score_tool("FileEdit", {}) == RiskLevel.MEDIUM
        # 删除 → HIGH
        assert scorer.score_tool("FileDelete", {}) == RiskLevel.HIGH
        # 只读工具 → LOW
        assert scorer.score_tool("FileRead", {}) == RiskLevel.LOW
        assert scorer.score_tool("WebSearch", {}) == RiskLevel.LOW
        # 其他工具默认 MEDIUM
        assert scorer.score_tool("SomeTool", {}) == RiskLevel.MEDIUM

    def test_risk_level_properties(self):
        """RiskLevel 枚举的标签与策略属性。"""
        assert RiskLevel.LOW.label == "低"
        assert RiskLevel.CRITICAL.label == "极高"
        assert not RiskLevel.LOW.needs_sandbox
        assert RiskLevel.MEDIUM.needs_sandbox
        assert not RiskLevel.MEDIUM.needs_snapshot
        assert RiskLevel.HIGH.needs_snapshot
        assert not RiskLevel.HIGH.needs_confirm
        assert RiskLevel.CRITICAL.needs_confirm

    def test_get_command_head(self):
        """命令头提取：二级前缀（git status 等）、环境变量剥离。"""
        assert RiskScorer._get_command_head("git status --short") == "git status"
        assert RiskScorer._get_command_head("npm list -g") == "npm list"
        assert RiskScorer._get_command_head("python script.py") == "python script.py"
        assert RiskScorer._get_command_head("ls -la") == "ls"
        assert RiskScorer._get_command_head("   ") == ""
        assert RiskScorer._get_command_head("FOO=1 BAR=2 ls") == "ls"


# =====================================================================
# 沙箱执行器
# =====================================================================

class _FakeProc:
    """模拟 asyncio 子进程对象，避免真实启动子进程。"""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"",
                 returncode: int = 0, hang: bool = False) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.pid = 4242
        self.killed = False
        self._hang = hang

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(30)  # 模拟挂起，配合 wait_for 超时
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


def _fake_subprocess(proc, exc=None, capture=None):
    """构造 create_subprocess_exec 的替代函数。

    Args:
        proc: 返回的 FakeProc
        exc: 若指定则直接抛出该异常
        capture: dict，用于捕获调用参数（env/preexec_fn 等）
    """

    async def _create(*args, **kwargs):
        if exc is not None:
            raise exc
        if capture is not None:
            capture["args"] = args
            capture["kwargs"] = kwargs
        return proc

    return _create


class _FakeKernel32:
    """模拟 Win32 kernel32，用于 Job Object 创建逻辑测试。"""

    def __init__(self, set_info_ok: bool = True) -> None:
        self.set_info_ok = set_info_ok
        self.closed_handles: list = []

    def CreateJobObjectW(self, a, b):  # noqa: N802
        return 123

    def SetInformationJobObject(self, *a):  # noqa: N802
        return 1 if self.set_info_ok else 0

    def CloseHandle(self, h):  # noqa: N802
        self.closed_handles.append(h)

    def OpenProcess(self, *a):
        return 456

    def AssignProcessToJobObject(self, *a):
        return True


class TestSandboxConfig:
    """SandboxConfig 构造与 from_settings 转换。"""

    def test_defaults(self):
        """默认配置字段。"""
        cfg = SandboxConfig()
        assert cfg.enabled is False
        assert cfg.max_memory_mb == 512
        assert cfg.max_cpu_seconds == 60
        assert cfg.max_processes == 10
        assert cfg.timeout == 120
        assert cfg.auto_allow_medium_risk is True
        assert cfg.audit_enabled is True

    def test_from_settings_full(self):
        """从完整 Settings 对象构建。"""
        from types import SimpleNamespace
        settings = SimpleNamespace(
            sandbox_enabled=True,
            sandbox_max_memory_mb=1024,
            sandbox_max_cpu_seconds=30,
            sandbox_max_processes=20,
            sandbox_timeout=60,
            sandbox_block_network=True,
            sandbox_auto_allow_medium=False,
            sandbox_excluded_commands=["docker"],
            sandbox_audit=False,
        )
        cfg = SandboxConfig.from_settings(settings)
        assert cfg.enabled is True
        assert cfg.max_memory_mb == 1024
        assert cfg.max_cpu_seconds == 30
        assert cfg.max_processes == 20
        assert cfg.timeout == 60
        assert cfg.block_network is True
        assert cfg.auto_allow_medium_risk is False
        assert cfg.excluded_commands == ["docker"]
        assert cfg.audit_enabled is False

    def test_from_settings_missing_attrs(self):
        """Settings 缺省字段时使用默认值。"""
        from types import SimpleNamespace
        cfg = SandboxConfig.from_settings(SimpleNamespace())
        assert cfg.enabled is False
        assert cfg.timeout == 120


class TestSandboxExecutor:
    """沙箱执行器测试。"""

    def test_config_property(self):
        """config 属性透传。"""
        cfg = SandboxConfig(enabled=True)
        ex = SandboxExecutor(cfg)
        assert ex.config is cfg

    def test_available_by_platform(self):
        """available 按平台判断。"""
        ex = SandboxExecutor(SandboxConfig())
        # Windows 且有 kernel32 → 可用
        ex._is_windows = True
        ex._kernel32 = object()
        assert ex.available is True
        # Windows 无 kernel32 → 不可用
        ex._kernel32 = None
        assert ex.available is False
        # Linux / macOS → 可用
        ex._is_windows = False
        ex._is_linux = True
        assert ex.available is True
        # 其他平台 → 不可用
        ex._is_linux = False
        ex._is_macos = False
        assert ex.available is False

    def test_is_excluded(self):
        """排除命令匹配：包含关系或前缀关系。"""
        ex = SandboxExecutor(SandboxConfig(excluded_commands=["docker", "wsl"]))
        assert ex.is_excluded("docker ps") is True
        assert ex.is_excluded("wsl --list") is True
        assert ex.is_excluded("git status") is False
        # 空排除列表
        ex2 = SandboxExecutor(SandboxConfig())
        assert ex2.is_excluded("docker ps") is False

    def test_find_bash(self, monkeypatch):
        """Git Bash 查找：常见安装位置 / PATH / 排除 system32。"""
        # 命中常见安装位置
        monkeypatch.setattr(
            os.path, "isfile",
            lambda p: p == r"D:\SoftwareDevelopmentKit\Git\bin\bash.exe",
        )
        assert SandboxExecutor._find_bash() == r"D:\SoftwareDevelopmentKit\Git\bin\bash.exe"
        # PATH 命中（非 system32）
        monkeypatch.setattr(os.path, "isfile", lambda p: False)
        monkeypatch.setattr(shutil, "which", lambda name: "C:/Program Files/Git/bin/bash")
        assert SandboxExecutor._find_bash() == "C:/Program Files/Git/bin/bash"
        # PATH 命中 system32 → 排除
        monkeypatch.setattr(shutil, "which", lambda name: "C:/Windows/System32/bash")
        assert SandboxExecutor._find_bash() is None
        # 都找不到
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert SandboxExecutor._find_bash() is None

    def test_build_shell_args(self, monkeypatch):
        """shell 参数构建：Windows bash / cmd 兜底 / Unix。"""
        ex = SandboxExecutor(SandboxConfig())
        # Windows + 有 bash
        ex._is_windows = True
        monkeypatch.setattr(ex, "_find_bash", lambda: r"D:\Git\bin\bash.exe")
        assert ex._build_shell_args("echo hi") == [r"D:\Git\bin\bash.exe", "-c", "echo hi"]
        # Windows + 无 bash → cmd 兜底
        monkeypatch.setattr(ex, "_find_bash", lambda: None)
        assert ex._build_shell_args("echo hi") == ["cmd", "/c", "echo hi"]
        # Unix
        ex._is_windows = False
        assert ex._build_shell_args("echo hi") == ["/bin/bash", "-c", "echo hi"]

    @pytest.mark.asyncio
    async def test_run_disabled_normal_execution(self):
        """沙箱关闭时普通执行真实命令。"""
        ex = SandboxExecutor(SandboxConfig(enabled=False))
        result = await ex.run("echo hello")
        assert result.sandboxed is False
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_run_excluded_command(self):
        """被排除的命令按普通方式执行。"""
        ex = SandboxExecutor(SandboxConfig(enabled=True, excluded_commands=["echo"]))
        result = await ex.run("echo hello")
        assert result.sandboxed is False
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_run_not_available_fallback(self, monkeypatch):
        """平台不支持沙箱时回退普通执行。"""
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = True
        ex._kernel32 = None  # Windows 下 kernel32 为空 → available False
        result = await ex.run("echo hello")
        assert result.sandboxed is False
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_run_timeout(self):
        """普通执行超时：timed_out 标记 + 进程被 kill。"""
        ex = SandboxExecutor(SandboxConfig(enabled=False, timeout=120))
        result = await ex.run('python -c "import time; time.sleep(5)"', timeout=1)
        assert result.timed_out is True
        assert result.sandboxed is False
        assert "超时" in result.error

    @pytest.mark.asyncio
    async def test_run_normal_file_not_found(self, monkeypatch):
        """普通执行找不到 shell 时返回错误结果。"""
        async def boom(*a, **k):
            raise FileNotFoundError("no shell")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
        ex = SandboxExecutor(SandboxConfig(enabled=False))
        result = await ex.run("echo hi")
        assert "找不到 shell" in result.error
        assert result.sandboxed is False

    @pytest.mark.asyncio
    async def test_windows_sandboxed_normal(self, monkeypatch):
        """Windows Job Object 分支：正常执行 + 网络阻断环境变量。"""
        ex = SandboxExecutor(SandboxConfig(enabled=True, block_network=True))
        ex._is_windows = True
        ex._kernel32 = _FakeKernel32()
        monkeypatch.setattr(ex, "_create_job_object", lambda: 123)
        monkeypatch.setattr(ex, "_assign_to_job", lambda h, pid: True)
        captured = {}
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(_FakeProc(b"out", b"err", 0), capture=captured),
        )
        result = await ex._run_sandboxed_windows("echo hi", "", {"FOO": "1"}, None)
        assert result.sandboxed is True
        assert result.stdout == "out"
        assert result.exit_code == 0
        assert result.resource_exceeded is False
        # block_network → 代理环境变量被注入
        env = captured["kwargs"]["env"]
        assert env["HTTP_PROXY"] == "http://127.0.0.1:1"
        assert env["FOO"] == "1"

    @pytest.mark.asyncio
    async def test_windows_job_create_failure_fallback(self, monkeypatch):
        """Job Object 创建失败 → 回退普通执行。"""
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = True
        ex._kernel32 = object()
        monkeypatch.setattr(ex, "_create_job_object", lambda: None)
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(_FakeProc(b"ok", b"", 0)),
        )
        result = await ex._run_sandboxed_windows("echo hi", "", None, None)
        assert result.sandboxed is False
        assert result.stdout == "ok"

    @pytest.mark.asyncio
    async def test_windows_sandboxed_timeout(self, monkeypatch):
        """Windows 分支超时：timed_out + 进程被 kill + job 被关闭。"""
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = True
        ex._kernel32 = object()
        monkeypatch.setattr(ex, "_create_job_object", lambda: 123)
        monkeypatch.setattr(ex, "_assign_to_job", lambda h, pid: True)
        closed = []
        monkeypatch.setattr(ex, "_close_job", lambda h: closed.append(h))
        proc = _FakeProc(b"", b"", 0, hang=True)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subprocess(proc))
        result = await ex._run_sandboxed_windows("sleep 30", "", None, 0.05)
        assert result.timed_out is True
        assert result.sandboxed is True
        assert proc.killed is True
        assert closed == [123]

    @pytest.mark.asyncio
    async def test_windows_sandboxed_resource_exceeded(self, monkeypatch):
        """Windows 分支检测资源超限（stderr 关键词）。"""
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = True
        ex._kernel32 = object()
        monkeypatch.setattr(ex, "_create_job_object", lambda: 123)
        monkeypatch.setattr(ex, "_assign_to_job", lambda h, pid: True)
        monkeypatch.setattr(ex, "_close_job", lambda h: None)
        proc = _FakeProc(b"", b"not enough memory", 1)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subprocess(proc))
        result = await ex._run_sandboxed_windows("heavy", "", None, None)
        assert result.resource_exceeded is True
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_windows_sandboxed_file_not_found(self, monkeypatch):
        """Windows 分支找不到 shell。"""
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = True
        ex._kernel32 = object()
        monkeypatch.setattr(ex, "_create_job_object", lambda: 123)
        monkeypatch.setattr(ex, "_close_job", lambda h: None)
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(None, exc=FileNotFoundError("no shell")),
        )
        result = await ex._run_sandboxed_windows("x", "", None, None)
        assert "找不到 shell" in result.error
        assert result.sandboxed is True

    @pytest.mark.asyncio
    async def test_windows_sandboxed_generic_exception(self, monkeypatch):
        """Windows 分支其他异常被捕获为 error 结果。"""
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = True
        ex._kernel32 = object()
        monkeypatch.setattr(ex, "_create_job_object", lambda: 123)
        monkeypatch.setattr(ex, "_close_job", lambda h: None)
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(None, exc=RuntimeError("boom")),
        )
        result = await ex._run_sandboxed_windows("x", "", None, None)
        assert result.error == "boom"
        assert result.sandboxed is True

    @pytest.mark.asyncio
    async def test_unix_sandboxed_normal(self, monkeypatch):
        """Unix 分支正常执行（fake resource 模块替代 Windows 缺失的 resource）。"""
        fake_resource = types.ModuleType("resource")
        fake_resource.setrlimit = lambda *a: None
        fake_resource.RLIMIT_AS = 1
        fake_resource.RLIMIT_CPU = 2
        fake_resource.RLIMIT_NPROC = 3
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = False
        ex._is_linux = True
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(_FakeProc(b"out", b"", 0)),
        )
        result = await ex._run_sandboxed_unix("echo hi", "", None, None)
        assert result.sandboxed is True
        assert result.stdout == "out"
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_unix_preexec_limits_all_fail(self, monkeypatch):
        """preexec_fn 内 setrlimit 全部异常时静默降级（覆盖 except 分支）。"""
        fake_resource = types.ModuleType("resource")
        calls = {"n": 0}

        def setrlimit(r, v):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad limit")
            if calls["n"] == 2:
                raise OSError("no perm")
            raise AttributeError("no RLIMIT_NPROC")

        fake_resource.setrlimit = setrlimit
        fake_resource.RLIMIT_AS = 1
        fake_resource.RLIMIT_CPU = 2
        fake_resource.RLIMIT_NPROC = 3
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        captured = {}
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(_FakeProc(b"ok", b"", 0), capture=captured),
        )
        ex = SandboxExecutor(SandboxConfig(
            enabled=True, max_memory_mb=64, max_cpu_seconds=10, max_processes=3,
        ))
        ex._is_windows = False
        ex._is_linux = True
        result = await ex._run_sandboxed_unix("echo hi", "", None, None)
        assert result.exit_code == 0
        preexec = captured["kwargs"].get("preexec_fn")
        assert preexec is not None
        preexec()  # 触发三次 setrlimit 异常路径
        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_unix_sandboxed_timeout(self, monkeypatch):
        """Unix 分支超时。"""
        fake_resource = types.ModuleType("resource")
        fake_resource.setrlimit = lambda *a: None
        fake_resource.RLIMIT_AS = 1
        fake_resource.RLIMIT_CPU = 2
        fake_resource.RLIMIT_NPROC = 3
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = False
        ex._is_linux = True
        proc = _FakeProc(b"", b"", 0, hang=True)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_subprocess(proc))
        result = await ex._run_sandboxed_unix("sleep 30", "", None, 0.05)
        assert result.timed_out is True
        assert result.sandboxed is True
        assert proc.killed is True

    @pytest.mark.parametrize("code", [-9, 137, -24, 152])
    @pytest.mark.asyncio
    async def test_unix_sandboxed_signal_codes(self, monkeypatch, code):
        """Unix 分支信号退出码 → resource_exceeded。"""
        fake_resource = types.ModuleType("resource")
        fake_resource.setrlimit = lambda *a: None
        fake_resource.RLIMIT_AS = 1
        fake_resource.RLIMIT_CPU = 2
        fake_resource.RLIMIT_NPROC = 3
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = False
        ex._is_linux = True
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(_FakeProc(b"", b"", code)),
        )
        result = await ex._run_sandboxed_unix("x", "", None, None)
        assert result.resource_exceeded is True

    @pytest.mark.asyncio
    async def test_unix_sandboxed_stderr_resource(self, monkeypatch):
        """Unix 分支 stderr 匹配资源超限关键词。"""
        fake_resource = types.ModuleType("resource")
        fake_resource.setrlimit = lambda *a: None
        fake_resource.RLIMIT_AS = 1
        fake_resource.RLIMIT_CPU = 2
        fake_resource.RLIMIT_NPROC = 3
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = False
        ex._is_linux = True
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(_FakeProc(b"", b"cannot allocate memory", 3)),
        )
        result = await ex._run_sandboxed_unix("x", "", None, None)
        assert result.resource_exceeded is True

    @pytest.mark.asyncio
    async def test_unix_sandboxed_file_not_found(self, monkeypatch):
        """Unix 分支找不到 shell。"""
        fake_resource = types.ModuleType("resource")
        fake_resource.setrlimit = lambda *a: None
        fake_resource.RLIMIT_AS = 1
        fake_resource.RLIMIT_CPU = 2
        fake_resource.RLIMIT_NPROC = 3
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = False
        ex._is_linux = True
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(None, exc=FileNotFoundError("no bash")),
        )
        result = await ex._run_sandboxed_unix("x", "", None, None)
        assert "找不到 shell" in result.error
        assert result.sandboxed is True

    @pytest.mark.asyncio
    async def test_unix_sandboxed_generic_exception(self, monkeypatch):
        """Unix 分支其他异常被捕获。"""
        fake_resource = types.ModuleType("resource")
        fake_resource.setrlimit = lambda *a: None
        fake_resource.RLIMIT_AS = 1
        fake_resource.RLIMIT_CPU = 2
        fake_resource.RLIMIT_NPROC = 3
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = False
        ex._is_linux = True
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(None, exc=RuntimeError("boom")),
        )
        result = await ex._run_sandboxed_unix("x", "", None, None)
        assert result.error == "boom"
        assert result.sandboxed is True

    @pytest.mark.asyncio
    async def test_unix_macos_sandbox_exec(self, monkeypatch):
        """macOS 分支：可用时用 sandbox-exec 包装命令。"""
        fake_resource = types.ModuleType("resource")
        fake_resource.setrlimit = lambda *a: None
        fake_resource.RLIMIT_AS = 1
        fake_resource.RLIMIT_CPU = 2
        fake_resource.RLIMIT_NPROC = 3
        monkeypatch.setitem(sys.modules, "resource", fake_resource)
        ex = SandboxExecutor(SandboxConfig(enabled=True))
        ex._is_windows = False
        ex._is_macos = True
        monkeypatch.setattr(
            SandboxExecutor, "_macos_sandbox_available",
            staticmethod(lambda: True),
        )
        captured = {}
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            _fake_subprocess(_FakeProc(b"ok", b"", 0), capture=captured),
        )
        result = await ex._run_sandboxed_unix("echo hi", "/tmp", None, None)
        assert result.exit_code == 0
        assert captured["args"][0] == "/usr/bin/sandbox-exec"

    def test_macos_helpers(self, monkeypatch):
        """macOS sandbox 可用性检查与 profile 生成。"""
        ex = SandboxExecutor(SandboxConfig())
        monkeypatch.setattr(os.path, "isfile", lambda p: p == "/usr/bin/sandbox-exec")
        assert ex._macos_sandbox_available() is True
        monkeypatch.setattr(os.path, "isfile", lambda p: False)
        assert ex._macos_sandbox_available() is False
        profile = ex._macos_sandbox_profile("/tmp/work")
        assert "(version 1)" in profile
        assert "(allow default)" in profile

    def test_create_job_object(self):
        """创建 Job Object：成功 / SetInformation 失败 / 无 kernel32。"""
        ex = SandboxExecutor(SandboxConfig(max_memory_mb=256, max_processes=5))
        ex._is_windows = True
        ex._kernel32 = _FakeKernel32(set_info_ok=True)
        assert ex._create_job_object() == 123
        ex._kernel32 = _FakeKernel32(set_info_ok=False)
        assert ex._create_job_object() is None
        ex._kernel32 = None
        assert ex._create_job_object() is None

    def test_assign_to_job_no_kernel32(self):
        """无 kernel32 时分配失败。"""
        ex = SandboxExecutor(SandboxConfig())
        ex._is_windows = True
        ex._kernel32 = None
        assert ex._assign_to_job(123, 456) is False

    def test_assign_to_job_success(self):
        """分配进程到 Job Object。"""
        ex = SandboxExecutor(SandboxConfig())
        ex._is_windows = True
        ex._kernel32 = _FakeKernel32()
        assert ex._assign_to_job(123, 456) is True

    def test_close_job(self):
        """关闭 Job Object：kernel32 为空时安全跳过。"""
        ex = SandboxExecutor(SandboxConfig())
        ex._is_windows = True
        ex._kernel32 = None
        ex._close_job(123)  # 不抛异常
        k32 = _FakeKernel32()
        ex._kernel32 = k32
        ex._close_job(123)
        assert k32.closed_handles == [123]

    def test_check_resource_violation(self):
        """stderr 资源超限关键词检测。"""
        assert SandboxExecutor._check_resource_violation("not enough memory") is True
        assert SandboxExecutor._check_resource_violation("OUT OF MEMORY") is True
        assert SandboxExecutor._check_resource_violation("resource temporarily unavailable") is True
        assert SandboxExecutor._check_resource_violation("cannot allocate memory") is True
        assert SandboxExecutor._check_resource_violation("memory limit") is True
        assert SandboxExecutor._check_resource_violation("normal output") is False

    def test_sandbox_result_defaults(self):
        """SandboxResult 默认字段。"""
        r = SandboxResult()
        assert r.exit_code == -1
        assert r.timed_out is False
        assert r.sandboxed is True
        assert r.error == ""


# =====================================================================
# 文件保护（快照/回滚）
# =====================================================================

class TestFileGuard:
    """FileGuard 快照与回滚测试。"""

    def _guard(self, tmp_path: Path, max_snapshots: int = 10) -> FileGuard:
        return FileGuard(snapshot_dir=str(tmp_path / "snaps"), max_snapshots=max_snapshots)

    def test_snapshot_and_rollback_file(self, tmp_path):
        """文件快照 → 修改 → 回滚恢复原始内容；重复回滚返回 False。"""
        guard = self._guard(tmp_path)
        f = tmp_path / "data.txt"
        f.write_text("original", encoding="utf-8")
        snap_id = guard.snapshot(str(f), reason="修改配置")
        assert (tmp_path / "snaps" / snap_id / "manifest.json").exists()
        f.write_text("modified", encoding="utf-8")
        assert guard.rollback(snap_id) is True
        assert f.read_text(encoding="utf-8") == "original"
        # 已回滚过 → 返回 False
        assert guard.rollback(snap_id) is False

    def test_snapshot_new_file_rollback_deletes(self, tmp_path):
        """快照不存在的路径（新建操作）→ 回滚删除该文件。"""
        guard = self._guard(tmp_path)
        f = tmp_path / "new.txt"
        snap_id = guard.snapshot(str(f), reason="新建文件")
        f.write_text("created", encoding="utf-8")
        assert guard.rollback(snap_id) is True
        assert not f.exists()

    def test_snapshot_directory_rollback(self, tmp_path):
        """目录快照 → 回滚恢复目录原貌（删除新增文件）。"""
        guard = self._guard(tmp_path)
        d = tmp_path / "proj"
        (d / "sub").mkdir(parents=True)
        (d / "a.txt").write_text("a", encoding="utf-8")
        snap_id = guard.snapshot(str(d), reason="目录操作")
        (d / "a.txt").write_text("changed", encoding="utf-8")
        (d / "b.txt").write_text("new", encoding="utf-8")
        assert guard.rollback(snap_id) is True
        assert (d / "a.txt").read_text(encoding="utf-8") == "a"
        assert not (d / "b.txt").exists()

    def test_snapshot_list_input(self, tmp_path):
        """支持传入路径列表，一次快照多个文件。"""
        guard = self._guard(tmp_path)
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("1", encoding="utf-8")
        f2.write_text("2", encoding="utf-8")
        snap_id = guard.snapshot([str(f1), str(f2)], reason="批量")
        info = guard.get_snapshot_info(snap_id)
        assert info is not None
        assert len(info.entries) == 2

    def test_large_file_skip_backup(self, tmp_path, monkeypatch):
        """超过阈值的大文件只记录元数据不备份，回滚不恢复内容。"""
        import agent.core.sandbox.file_guard as fg
        monkeypatch.setattr(fg, "_LARGE_FILE_THRESHOLD", 10)
        guard = self._guard(tmp_path)
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * 100)
        snap_id = guard.snapshot(str(f))
        info = guard.get_snapshot_info(snap_id)
        assert info.entries[0].backup_path == ""
        f.write_bytes(b"y" * 100)
        # 无备份：标记回滚成功但内容不恢复
        assert guard.rollback(snap_id) is True
        assert f.read_bytes() == b"y" * 100

    def test_rollback_missing_snapshot(self, tmp_path):
        """回滚不存在的快照返回 False。"""
        guard = self._guard(tmp_path)
        assert guard.rollback("nonexistent") is False

    def test_rollback_backup_lost(self, tmp_path):
        """备份文件丢失：记录日志但内容不恢复。"""
        guard = self._guard(tmp_path)
        f = tmp_path / "x.txt"
        f.write_text("v1", encoding="utf-8")
        snap_id = guard.snapshot(str(f))
        info = guard.get_snapshot_info(snap_id)
        Path(info.entries[0].backup_path).unlink()
        f.write_text("v2", encoding="utf-8")
        assert guard.rollback(snap_id) is True
        assert f.read_text(encoding="utf-8") == "v2"

    def test_list_snapshots_sorted_and_skip_broken(self, tmp_path):
        """list_snapshots 按时间倒序，损坏的 manifest 被跳过。"""
        guard = self._guard(tmp_path)
        f = tmp_path / "f.txt"
        f.write_text("x", encoding="utf-8")
        id1 = guard.snapshot(str(f), reason="first")
        id2 = guard.snapshot(str(f), reason="second")
        snaps = guard.list_snapshots()
        assert len(snaps) == 2
        assert snaps[0]["id"] == id2
        assert snaps[0]["reason"] == "second"
        assert snaps[1]["id"] == id1
        # 手动创建损坏快照目录
        broken = tmp_path / "snaps" / "broken"
        broken.mkdir()
        (broken / "manifest.json").write_text("not json", encoding="utf-8")
        assert len(guard.list_snapshots()) == 2

    def test_delete_snapshot(self, tmp_path):
        """删除快照：存在返回 True，不存在返回 False。"""
        guard = self._guard(tmp_path)
        assert guard.delete_snapshot("nope") is False
        f = tmp_path / "f.txt"
        f.write_text("x", encoding="utf-8")
        snap_id = guard.snapshot(str(f))
        assert guard.delete_snapshot(snap_id) is True
        assert not (tmp_path / "snaps" / snap_id).exists()

    def test_get_snapshot_info_missing(self, tmp_path):
        """不存在的快照详情返回 None。"""
        guard = self._guard(tmp_path)
        assert guard.get_snapshot_info("nope") is None

    def test_cleanup_old_snapshots(self, tmp_path):
        """超过保留数量自动清理最旧快照。"""
        guard = self._guard(tmp_path, max_snapshots=2)
        f = tmp_path / "f.txt"
        f.write_text("x", encoding="utf-8")
        for i in range(5):
            guard.snapshot(str(f), reason=f"r{i}")
        assert len(guard.list_snapshots()) == 2

    def test_generate_id_format(self):
        """快照 ID 格式：时间戳_哈希。"""
        sid = FileGuard._generate_id("reason")
        assert re.match(r"^\d{8}_\d{6}_[0-9a-f]{6}$", sid) is not None

    def test_dir_size(self, tmp_path):
        """目录总大小计算。"""
        d = tmp_path / "dd"
        d.mkdir()
        (d / "a").write_text("12345", encoding="utf-8")
        (d / "sub").mkdir()
        (d / "sub" / "b").write_text("123", encoding="utf-8")
        assert FileGuard._dir_size(d) == 8

    def test_snapshot_to_from_dict(self):
        """Snapshot 序列化/反序列化往返。"""
        snap = Snapshot(
            id="s1",
            timestamp=123.0,
            reason="测试",
            entries=[SnapshotEntry(original_path="/a", backup_path="/b",
                                   is_directory=True, existed=True, size=5, mtime=1.0)],
            rolled_back=True,
        )
        restored = Snapshot.from_dict(snap.to_dict())
        assert restored.id == "s1"
        assert restored.rolled_back is True
        assert restored.entries[0].original_path == "/a"
        assert restored.entries[0].is_directory is True


# =====================================================================
# 审计日志
# =====================================================================

class TestSandboxAuditor:
    """SandboxAuditor 审计日志测试。"""

    def test_audit_entry_roundtrip(self):
        """AuditEntry to_dict/from_dict 往返。"""
        e = AuditEntry(
            timestamp=1.5, event_type="execution", command="ls", risk_level="LOW",
            sandboxed=True, exit_code=0, timed_out=False, resource_exceeded=True,
            snapshot_id="", permission_decision="", detail="d", cwd="/tmp",
        )
        d = e.to_dict()
        assert d["ts"] == 1.5
        assert d["type"] == "execution"
        assert d["exit"] == 0
        assert d["res_exceeded"] is True
        e2 = AuditEntry.from_dict(d)
        assert e2.timestamp == 1.5
        assert e2.command == "ls"
        assert e2.exit_code == 0

    def test_log_and_get_recent(self, tmp_path):
        """各类事件写入与按类型过滤查询。"""
        ap = tmp_path / "audit.jsonl"
        auditor = SandboxAuditor(log_path=str(ap), max_entries=100)
        assert auditor.log_path == ap
        auditor.log_execution(command="npm install", risk_level="MEDIUM", sandboxed=True, exit_code=0)
        auditor.log_execution(command="ls", risk_level="LOW", sandboxed=False, cwd="/home")
        auditor.log_violation(command="rm -rf", violation_type="danger")
        auditor.log_snapshot("snap1", ["a.txt", "b.txt"], reason="test")
        auditor.log_rollback("snap1", True)
        auditor.log_permission(command="git push", decision="deny", risk_level="HIGH", detail="x")
        recent = auditor.get_recent(limit=10)
        assert len(recent) == 6
        assert recent[0].event_type == "permission"  # 最新在前
        assert recent[0].permission_decision == "deny"
        assert recent[2].event_type == "snapshot"
        assert recent[2].snapshot_id == "snap1"
        # 事件类型过滤
        execs = auditor.get_recent(limit=10, event_type="execution")
        assert len(execs) == 2
        assert execs[0].cwd == "/home"
        # limit 限制
        assert len(auditor.get_recent(limit=2)) == 2

    def test_get_stats(self, tmp_path):
        """统计摘要：总数/沙箱数/违规数/风险分布。"""
        ap = tmp_path / "audit.jsonl"
        auditor = SandboxAuditor(log_path=str(ap))
        auditor.log_execution(command="a", risk_level="MEDIUM", sandboxed=True, exit_code=0, timed_out=True, resource_exceeded=True)
        auditor.log_execution(command="b", risk_level="LOW", sandboxed=False)
        auditor.log_violation(command="c", violation_type="x")
        stats = auditor.get_stats()
        assert stats["total"] == 3
        assert stats["sandboxed"] == 1
        assert stats["violations"] == 1
        assert stats["timeouts"] == 1
        assert stats["resource_exceeded"] == 1
        assert stats["risk_distribution"] == {"MEDIUM": 1, "LOW": 1}

    def test_stats_missing_file(self, tmp_path):
        """日志文件不存在时返回空统计。"""
        auditor = SandboxAuditor(log_path=str(tmp_path / "no.jsonl"))
        assert auditor.get_stats() == {"total": 0}
        assert auditor.get_recent() == []
        assert auditor._count_entries() == 0

    def test_disabled_no_write(self, tmp_path):
        """enabled=False 时不写入任何记录。"""
        ap = tmp_path / "a.jsonl"
        auditor = SandboxAuditor(log_path=str(ap), enabled=False)
        assert auditor.enabled is False
        auditor.log_execution(command="x", risk_level="LOW", sandboxed=False)
        auditor.log_violation(command="y", violation_type="z")
        assert not ap.exists()

    def test_rotate(self, tmp_path):
        """超过 max_entries 自动轮转，仅保留最新 N 条。"""
        ap = tmp_path / "a.jsonl"
        auditor = SandboxAuditor(log_path=str(ap), max_entries=3)
        for i in range(6):
            auditor.log_execution(command=f"cmd{i}", risk_level="LOW", sandboxed=False)
        recent = auditor.get_recent(limit=100)
        assert len(recent) == 3
        assert recent[0].command == "cmd5"
        assert recent[-1].command == "cmd3"

    def test_broken_lines_skipped(self, tmp_path):
        """损坏 JSON 行在查询与统计中被跳过。"""
        ap = tmp_path / "a.jsonl"
        ap.write_text(
            '{"ts": 1, "type": "execution", "cmd": "ok"}\n'
            "not-json-line\n"
            '{"ts": 2, "type": "violation"}\n',
            encoding="utf-8",
        )
        auditor = SandboxAuditor(log_path=str(ap))
        assert auditor._count_entries() == 3
        assert len(auditor.get_recent(limit=10)) == 2
        stats = auditor.get_stats()
        assert stats["total"] == 2
        assert stats["violations"] == 1

    def test_clear(self, tmp_path):
        """清空日志。"""
        ap = tmp_path / "a.jsonl"
        auditor = SandboxAuditor(log_path=str(ap))
        auditor.log_execution(command="x", risk_level="LOW", sandboxed=False)
        auditor.clear()
        assert ap.read_text(encoding="utf-8") == ""
        assert auditor.get_recent() == []

    def test_long_command_truncated(self, tmp_path):
        """超长命令被截断到 200 字符。"""
        ap = tmp_path / "a.jsonl"
        auditor = SandboxAuditor(log_path=str(ap))
        long_cmd = "x" * 300
        auditor.log_execution(command=long_cmd, risk_level="LOW", sandboxed=False)
        entry = auditor.get_recent(limit=1)[0]
        assert len(entry.command) == 200

    def test_append_oserror_silent(self, tmp_path, monkeypatch):
        """写入 OSError 被静默吞掉。"""
        import builtins
        ap = tmp_path / "a.jsonl"
        auditor = SandboxAuditor(log_path=str(ap))

        def fake_open(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(builtins, "open", fake_open)
        auditor.log_execution(command="x", risk_level="LOW", sandboxed=False)  # 不抛异常
