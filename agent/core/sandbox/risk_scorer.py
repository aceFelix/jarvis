"""操作风险评分器。

将命令/操作分为四级风险:
- LOW: 只读操作（ls, cat, grep, git status 等）
- MEDIUM: 有副作用但可逆的操作（git commit, npm install, pip install 等）
- HIGH: 不可逆或影响范围大的操作（rm, del, git push --force 等）
- CRITICAL: 系统级破坏性操作（格式化、注册表修改、系统服务操作等）

沙箱策略:
- LOW: 直接放行，无需沙箱
- MEDIUM: 沙箱开启时自动放行（在沙箱内执行）；未开启时 ASK
  （例外：W3.2 延时窗口里还压着未结案的不可逆外发预约时，网络外发类命令不再
  自动放行，见 ``looks_network_outbound`` 与 docs/fixlogs/w3-delay-window-bypass-fix.md）
- HIGH: 强制沙箱执行 + 文件快照保护
- CRITICAL: 沙箱 + 快照 + 用户确认（即使沙箱开启也需确认）
"""

from __future__ import annotations

import re
from enum import IntEnum
from typing import Literal


class RiskLevel(IntEnum):
    """风险等级（数值越大越危险）。"""
    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3

    @property
    def label(self) -> str:
        return {0: "低", 1: "中", 2: "高", 3: "极高"}[self.value]

    @property
    def needs_sandbox(self) -> bool:
        """是否需要沙箱执行。"""
        return self.value >= RiskLevel.MEDIUM

    @property
    def needs_snapshot(self) -> bool:
        """是否需要文件快照保护。"""
        return self.value >= RiskLevel.HIGH

    @property
    def needs_confirm(self) -> bool:
        """即使沙箱开启，是否仍需用户确认。"""
        return self.value >= RiskLevel.CRITICAL


# ---- 只读命令（LOW）----
_READONLY_COMMANDS = frozenset({
    "ls", "ll", "dir", "cat", "type", "head", "tail", "less", "more",
    "grep", "egrep", "fgrep", "rg", "ag", "findstr",
    "find", "which", "where", "whereis", "file", "stat",
    "wc", "sort", "uniq", "cut", "tr",
    "echo", "printf", "date", "whoami", "hostname", "uname", "ver",
    "pwd", "cd", "env", "printenv", "set",
    "ps", "top", "tasklist", "df", "du", "free", "wmic",
    "git status", "git diff", "git log", "git show", "git branch",
    "git remote", "git tag", "git stash list",
    "npm list", "npm ls", "pip list", "pip show", "pip freeze",
    "python --version", "node --version", "python -V",
    "ipconfig", "ifconfig", "ping", "nslookup", "netstat",
    "systeminfo", "getmac",
})

# ---- 中等风险命令（MEDIUM）—— 有副作用但通常可逆 ----
_MEDIUM_PATTERNS = [
    r"\bgit\s+(add|commit|checkout|switch|merge|rebase|stash|pull|fetch)\b",
    r"\bnpm\s+(install|ci|update|run|init|publish)\b",
    r"\bpip\s+(install|uninstall)\b",  # freeze 是只读，已列入 _READONLY_COMMANDS,
    r"\buv\s+(add|remove|sync|pip)\b",
    r"\bpython\s+\S+\.py",                    # 运行脚本
    r"\bnode\s+\S+\.(js|mjs|ts)",             # 运行脚本
    r"\bmkdir\b",
    r"\btouch\b",
    r"\bcp\b", r"\bcopy\b", r"\bxcopy\b",
    r"\bmv\b", r"\bmove\b", r"\bren\b",
    r"\bgit\s+push\b(?!.*--force)",           # 普通 push（非 force）
    r"\bdocker\s+(build|run|pull|push)\b",
    r"\bcargo\s+(build|run|test)\b",
    r"\bmake\b",
    r"\bgradle\b", r"\bmvn\b",
]
_MEDIUM_RE = [re.compile(p, re.IGNORECASE) for p in _MEDIUM_PATTERNS]

# ---- 高风险命令（HIGH）—— 不可逆或影响范围大 ----
_HIGH_PATTERNS = [
    r"\brm\b",                                 # 任何 rm
    r"\bdel\b", r"\brmdir\b", r"\brd\b",      # Windows 删除
    r"\bgit\s+push\s+.*--force",              # 强推
    r"\bgit\s+reset\s+--hard",                # 硬重置
    r"\bgit\s+clean\b",                       # 清理未跟踪文件
    r"\bchmod\b", r"\bchown\b", r"\bicacls\b",  # 权限修改
    r"\bnpm\s+(uninstall|publish)\b",
    r"\bpip\s+uninstall\b",
    r"\bdocker\s+(rm|rmi|system\s+prune)\b",
    r"\bkill\b", r"\btaskkill\b",             # 杀进程
    r"\bservice\s+\w+\s+(stop|restart)\b",    # 服务操作
    r"\bsc\s+(stop|delete|config)\b",         # Windows 服务
    r"\bnet\s+(stop|start)\b",                # Windows 服务
    r">\s*\S+",                                # 输出重定向（覆盖文件）
    r"\btruncate\b",
    r"\bshred\b",
]
_HIGH_RE = [re.compile(p, re.IGNORECASE) for p in _HIGH_PATTERNS]

# ---- 极高风险命令（CRITICAL）—— 系统级破坏 ----
_CRITICAL_PATTERNS = [
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f",         # rm -rf
    r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r",
    r"\brm\s+/",                              # rm /
    r"\brm\s+~",
    r"\bsudo\b",                              # 提权
    r"\bmkfs\b",                              # 格式化
    r"\bdd\b\s+if=",                          # 写裸盘
    r":\(\)\s*\{\s*:\|:&\s*\};:",            # fork bomb
    r">\s*/dev/[sh]d",                        # 写裸设备
    r"\bformat\b\s+[a-zA-Z]:",               # Windows format
    r"\breg\s+(delete|add)\b",               # 注册表修改
    r"\bregedit\b",
    r"\bnet\s+user\b.*(/add|/delete)",       # 用户管理
    r"\bshutdown\b", r"\brestart\b",         # 关机/重启
    r"\bsfc\b", r"\bdism\b",                 # 系统文件修改
    r"\bbcdedit\b",                           # 引导配置
    r"\bcurl\b.+\|\s*(bash|sh|zsh|powershell)\b",  # 管道执行
    r"\bwget\b.+\|\s*(bash|sh|zsh|powershell)\b",
    r"\beval\b",
    r"\biex\b",                               # PowerShell Invoke-Expression
    r"\bInvoke-Expression\b",
    r"\bSet-ExecutionPolicy\b",              # 修改执行策略
    r"\bdel\s+/s",                          # del /s（递归删除）
    r"\brd\s+/s",                            # rd /s（递归删除）
]
_CRITICAL_RE = [re.compile(p, re.IGNORECASE) for p in _CRITICAL_PATTERNS]

# ---- 网络外发通路判定（不参与风险评分，单独成表）----
#
# 只服务一件事：W3.2 延时窗口里还压着未结案的不可逆外发预约时，把"可能把同一
# 件事做掉"的命令从自动放行降级成问人。判的是**通路**而不是危险度，所以不复用
# 上面任何一张评分表——把绕道判定混进风险等级会连带改掉所有命令的分级结果。
_OUTBOUND_HEAD_RE = re.compile(
    r"(?:^|[;&|]|\$\(|\b(?:sudo|time|nohup)\s+)"
    r"\s*(curl|wget|httpie|nc|netcat|telnet|ssh|scp|sftp|rsync|aria2c|"
    r"Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b",
    re.IGNORECASE,
)
# 解释器 + 网络/邮件库：脚本里抓取或直接发信（python -c "import smtplib…"）
_OUTBOUND_SCRIPT_RE = re.compile(
    r"\b(python\d*|py|node|deno|php|ruby|perl|powershell|pwsh|cmd|sh|bash)\b"
    r".{0,400}?"
    r"\b(smtplib|sendmail|urllib|http\.client|requests|aiohttp|httpx|socket|fetch\()",
    re.IGNORECASE | re.DOTALL,
)
# 命令里出现显式外发协议/URL 即视为可能把东西送出本机
_OUTBOUND_URL_RE = re.compile(
    r"\b(?:https?|ftp|ftps|smtp|smtps|pop3|imap)://", re.IGNORECASE,
)


def looks_network_outbound(command: str) -> bool:
    """命令是否可能把数据送出本机（预约期内的绕道收紧用，不代表风险等级）。

    三个信号任一命中即算：外发命令头、解释器里带网络/邮件库、显式外发 URL。
    判定故意宽松：误判的代价是多问一次，漏判的代价是用户以为被窗口拦住的邮件
    真的发了出去——保护变成摆设比没有保护更糟。
    @author aceFelix
    """
    if not command or not command.strip():
        return False
    return bool(
        _OUTBOUND_HEAD_RE.search(command)
        or _OUTBOUND_URL_RE.search(command)
        or _OUTBOUND_SCRIPT_RE.search(command)
    )


class RiskScorer:
    """操作风险评分器。

    用法::

        scorer = RiskScorer()
        level = scorer.score_command("rm -rf /tmp/build")
        # => RiskLevel.CRITICAL

        level = scorer.score_command("git commit -m 'fix'")
        # => RiskLevel.MEDIUM
    """

    def score_command(self, command: str) -> RiskLevel:
        """评估 shell 命令的风险等级。"""
        if not command.strip():
            return RiskLevel.LOW

        # 从最危险开始检查（CRITICAL > HIGH > MEDIUM > LOW）
        for pat in _CRITICAL_RE:
            if pat.search(command):
                return RiskLevel.CRITICAL

        for pat in _HIGH_RE:
            if pat.search(command):
                return RiskLevel.HIGH

        for pat in _MEDIUM_RE:
            if pat.search(command):
                return RiskLevel.MEDIUM

        # 检查只读白名单
        if self._is_readonly(command):
            return RiskLevel.LOW

        # 未知命令默认 MEDIUM（保守策略）
        return RiskLevel.MEDIUM

    def score_tool(self, tool_name: str, args: dict) -> RiskLevel:
        """评估工具调用的风险等级。"""
        # Bash/PowerShell 用命令评分
        if tool_name in ("Bash", "PowerShell"):
            return self.score_command(args.get("command", ""))

        # 文件写入类工具
        if tool_name in ("FileWrite", "FileEdit"):
            path = args.get("file_path", "") or args.get("path", "")
            return self._score_file_write(path)

        # 文件删除
        if tool_name in ("FileDelete",):
            return RiskLevel.HIGH

        # 只读工具
        if tool_name in ("FileRead", "Glob", "Grep", "WebFetch", "WebSearch"):
            return RiskLevel.LOW

        # 其他工具默认 MEDIUM
        return RiskLevel.MEDIUM

    def _is_readonly(self, command: str) -> bool:
        """判断命令是否只读。"""
        cmd = command.strip()
        # 链式命令（含 ; | && ||）不视为只读
        if any(op in cmd for op in [";", "&&", "||"]):
            return False
        # 管道单独处理: cat foo | grep bar 是只读的
        if "|" in cmd:
            parts = cmd.split("|")
            return all(self._single_is_readonly(p.strip()) for p in parts)
        return self._single_is_readonly(cmd)

    def _single_is_readonly(self, cmd: str) -> bool:
        """单个命令（无管道/链）是否只读。"""
        # 输出重定向不是只读
        if re.search(r"(?:^|\s|\d|&)>{1,2}\s*\S", cmd):
            return False
        # 取命令头匹配白名单
        head = self._get_command_head(cmd)
        return head in _READONLY_COMMANDS

    @staticmethod
    def _get_command_head(cmd: str) -> str:
        """取命令前缀头。"""
        import shlex
        cmd = cmd.strip()
        # 去掉环境变量前缀
        while re.match(r"^[A-Z_][A-Z0-9_]*=\S+\s+", cmd):
            cmd = re.sub(r"^[A-Z_][A-Z0-9_]*=\S+\s+", "", cmd)
        try:
            tokens = shlex.split(cmd, posix=True)
        except ValueError:
            return cmd.split()[0] if cmd.split() else ""
        if not tokens:
            return ""
        # 二级前缀
        if len(tokens) >= 2 and tokens[0] in {"git", "npm", "pip", "python", "node", "docker"}:
            return f"{tokens[0]} {tokens[1]}"
        return tokens[0]

    @staticmethod
    def _score_file_write(path: str) -> RiskLevel:
        """评估文件写入的风险。"""
        import os
        normalized = path.replace("/", os.sep).lower()
        # 系统关键路径
        critical_dirs = ("\\windows\\", "\\system32\\", "\\syswow64\\",
                         "\\.ssh\\", "\\.aws\\", "\\.gnupg\\")
        for d in critical_dirs:
            if d in normalized:
                return RiskLevel.CRITICAL
        # 配置文件
        config_exts = (".toml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".env")
        if any(normalized.endswith(ext) for ext in config_exts):
            return RiskLevel.MEDIUM
        return RiskLevel.MEDIUM
