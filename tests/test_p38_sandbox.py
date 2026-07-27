"""P3-8 安全沙箱执行验证脚本。"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.core.sandbox.risk_scorer import RiskLevel, RiskScorer
from agent.core.sandbox.executor import SandboxConfig, SandboxExecutor
from agent.core.sandbox.file_guard import FileGuard
from agent.core.sandbox.audit import SandboxAuditor

print("=" * 50)
print("P3-8 安全沙箱执行 - 集成验证")
print("=" * 50)

# ---- 1. 风险评分器 ----
print("\n[1] 风险评分器")
scorer = RiskScorer()

test_cases = [
    ("ls -la", RiskLevel.LOW),
    ("cat README.md", RiskLevel.LOW),
    ("git status", RiskLevel.LOW),
    ("git commit -m 'fix'", RiskLevel.MEDIUM),
    ("npm install express", RiskLevel.MEDIUM),
    ("python script.py", RiskLevel.MEDIUM),
    ("rm temp.txt", RiskLevel.HIGH),
    ("del /f file.txt", RiskLevel.HIGH),
    ("git push --force", RiskLevel.HIGH),
    ("rm -rf /", RiskLevel.CRITICAL),
    ("sudo apt install", RiskLevel.CRITICAL),
    ("format C:", RiskLevel.CRITICAL),
    ("reg delete HKLM", RiskLevel.CRITICAL),
]

all_pass = True
for cmd, expected in test_cases:
    result = scorer.score_command(cmd)
    status = "OK" if result == expected else "FAIL"
    if result != expected:
        all_pass = False
    print(f"  [{status}] '{cmd[:30]}' => {result.name} (期望 {expected.name})")

print(f"\n  风险评分: {'全部通过' if all_pass else '有失败'}")

# ---- 2. 沙箱执行器 ----
print("\n[2] 沙箱执行器")
config = SandboxConfig(enabled=True, max_memory_mb=256, max_processes=5)
executor = SandboxExecutor(config)
print(f"  平台可用: {executor.available}")
print(f"  配置: memory={config.max_memory_mb}MB, processes={config.max_processes}")
print(f"  排除检查 'docker ps': {executor.is_excluded('docker ps')}")

config2 = SandboxConfig(enabled=True, excluded_commands=["docker", "wsl"])
executor2 = SandboxExecutor(config2)
print(f"  排除检查 'docker ps' (配置排除): {executor2.is_excluded('docker ps')}")

# ---- 3. 文件保护 ----
print("\n[3] 文件保护（快照/回滚）")
import tempfile
test_dir = tempfile.mkdtemp(prefix="jarvis_sandbox_test_")
test_file = os.path.join(test_dir, "test.txt")
with open(test_file, "w", encoding="utf-8") as f:
    f.write("original content")

guard = FileGuard(max_snapshots=5)
snap_id = guard.snapshot(test_file, reason="测试快照")
print(f"  快照创建: {snap_id}")

# 修改文件
with open(test_file, "w", encoding="utf-8") as f:
    f.write("modified content")
print(f"  修改后: '{open(test_file, encoding='utf-8').read()}'")

# 回滚
success = guard.rollback(snap_id)
content_after = open(test_file, encoding="utf-8").read()
print(f"  回滚{'成功' if success else '失败'}: '{content_after}'")
assert content_after == "original content", "回滚内容不匹配!"

# 列出快照
snaps = guard.list_snapshots()
print(f"  当前快照数: {len(snaps)}")

# 清理
guard.delete_snapshot(snap_id)
import shutil
shutil.rmtree(test_dir, ignore_errors=True)
print("  清理完成")

# ---- 4. 审计日志 ----
print("\n[4] 审计日志")
import tempfile
audit_path = os.path.join(tempfile.gettempdir(), "jarvis_test_audit.jsonl")
auditor = SandboxAuditor(log_path=audit_path, enabled=True)
auditor.log_execution(command="rm -rf build/", risk_level="HIGH", sandboxed=True, exit_code=0)
auditor.log_execution(command="npm install", risk_level="MEDIUM", sandboxed=True, exit_code=0)
auditor.log_violation(command="fork_bomb()", violation_type="process_limit")
auditor.log_permission(command="git push --force", decision="deny", risk_level="HIGH")

recent = auditor.get_recent(limit=10)
print(f"  记录数: {len(recent)}")
for entry in recent:
    print(f"    [{entry.event_type}] {entry.command[:40]} risk={entry.risk_level}")

stats = auditor.get_stats()
print(f"  统计: total={stats['total']}, sandboxed={stats['sandboxed']}, violations={stats['violations']}")

# 清理
os.unlink(audit_path)
print("  清理完成")

# ---- 5. 配置加载 ----
print("\n[5] 配置加载")
from agent.config.settings import load_settings
settings = load_settings()
print(f"  sandbox_enabled: {settings.sandbox_enabled}")
print(f"  sandbox_max_memory_mb: {settings.sandbox_max_memory_mb}")
print(f"  sandbox_max_processes: {settings.sandbox_max_processes}")
print(f"  sandbox_block_network: {settings.sandbox_block_network}")
print(f"  sandbox_auto_allow_medium: {settings.sandbox_auto_allow_medium}")
print(f"  sandbox_audit: {settings.sandbox_audit}")

# ---- 6. BashTool 沙箱集成 ----
print("\n[6] BashTool 沙箱集成")
from agent.tools.bash import BashTool, _risk_scorer
tool = BashTool()
print(f"  BashTool 名称: {tool.name}")
print(f"  风险评分 'npm install': {_risk_scorer.score_command('npm install').name}")
print(f"  风险评分 'rm -rf /': {_risk_scorer.score_command('rm -rf /').name}")

print("\n" + "=" * 50)
print("P3-8 安全沙箱执行 - 验证完成 ✓")
print("=" * 50)
