"""Shell 命令分类器。

两类判定:
1. 只读白名单: 这些命令无副作用（ls/cat/grep/find/git status 等），
   可在 plan 模式或没命中 allow 规则时安全放行。
2. 危险模式: 这些命令模式需要 DENY 或强制 ASK（rm -rf, curl|sh, sudo 等）。

设计: 不追求完整，只覆盖最常见的判定。复杂命令最终落到 ASK。
"""

from __future__ import annotations

import re
import shlex
from typing import Literal

CommandKind = Literal["readonly", "dangerous", "unknown"]


# 只读命令前缀（无副作用）。完整命令前缀匹配这些时视为只读。
# 注意: 这些命令的"带写参"形态不在白名单（如 git push），会被 further 检查。
_READONLY_PREFIXES = {
    "ls", "ll", "cat", "head", "tail", "less", "more",
    "grep", "egrep", "fgrep", "rg", "ag",
    "find", "which", "where", "whereis", "file", "stat",
    "wc", "sort", "uniq", "cut", "tr",
    "echo", "printf", "date", "whoami", "hostname", "uname",
    "pwd", "env", "printenv",
    "ps", "top", "df", "du", "free",
    "git status", "git diff", "git log", "git show", "git branch", "git remote -v",
    "npm list", "pip list", "pip show",
    "python --version", "node --version",
}

# 危险模式（正则）。命中即 dangerous。
_DANGEROUS_PATTERNS = [
    r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f",          # rm -rf / rm -fr
    r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r",
    r"\brm\s+/",                                # rm / 任意
    r"\brm\s+~",
    r"\bsudo\b",                                # 任何 sudo
    r"\bmkfs\b",                                # 格式化
    r"\bdd\b\s+if=",                            # dd 写盘
    r":\(\)\s*\{\s*:\|:&\s*\};:",               # fork bomb
    r">\s*/dev/[sh]d",                          # 写裸设备
    r"\bchmod\s+-R\s+0?777\s+/",                # 递归改权限到根
    r"\bchown\s+-R",                            # 递归改属主
    r"\bcurl\b.+\|\s*(bash|sh|zsh)\b",          # curl | sh
    r"\bwget\b.+\|\s*(bash|sh|zsh)\b",          # wget | sh
    r"\beval\b",                                # eval
    r"\bgit\s+push\s+.*--force",                # 强推（按需可放开）
]
_DANGEROUS_RE = [re.compile(p) for p in _DANGEROUS_PATTERNS]


def get_command_head(cmd: str) -> str:
    """取命令的"前缀头"，用于白名单匹配。

    例如 "git status -s" -> "git status"
         "git commit -m" -> "git"（commit 不在只读白名单）
         "ls -la" -> "ls"
    """
    cmd = cmd.strip()
    # 去掉环境变量前缀 (FOO=bar baz)
    while re.match(r"^[A-Z_][A-Z0-9_]*=\S+\s+", cmd):
        cmd = re.sub(r"^[A-Z_][A-Z0-9_]*=\S+\s+", "", cmd)
    # 去掉 sudo 前缀（如果命令本身以 sudo 开头，整个判为 dangerous）
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        return cmd[:80]
    if not tokens:
        return ""
    # 二级前缀（git/npm/pip 子命令）
    if len(tokens) >= 2 and tokens[0] in {"git", "npm", "pip", "python", "node"}:
        return f"{tokens[0]} {tokens[1]}"
    return tokens[0]


def classify(cmd: str) -> CommandKind:
    """判定命令属于哪一类。"""
    if not cmd.strip():
        return "unknown"
    # 优先判危险（即便前缀是只读的，如 "ls; rm -rf /"）
    for pat in _DANGEROUS_RE:
        if pat.search(cmd):
            return "dangerous"
    # 链式命令（含 ; | && ||）一律按 unknown 处理（保守）
    if any(op in cmd for op in [";", "&&", "||", "|"]):
        # 但 cat foo | grep bar 这种纯只读链是安全的
        # 简化: 只要有管道/链就判 unknown，交给 ASK
        return "unknown"
    # 输出重定向（> >>）会让"只读命令"变成写操作，必须降级为 unknown
    # 否则 "echo hi > /etc/hosts" 会被误判为只读放行
    if _has_redirect(cmd):
        return "unknown"
    head = get_command_head(cmd)
    if head in _READONLY_PREFIXES:
        return "readonly"
    return "unknown"


# 匹配输出重定向: ">" / ">>" / "&>" / "1>" / "2>" 等，但排除 "==" 和 ">" 出现在字符串里的情况
_REDIRECT_RE = re.compile(r"(?:^|\s|\d|&)>{1,2}\s*\S")
# 显式排除比较运算（test 脚本里 [ x > y ] 这种）—— 简化: 只在 shell 语义下 > 才是重定向


def _has_redirect(cmd: str) -> bool:
    """检测命令是否含输出重定向（> >> &> 2> 等）。

    简单启发: 非引号内的 > 或 >> 视为重定向。引号内的 > 忽略。
    """
    in_squote = False
    in_dquote = False
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if ch == "'" and not in_dquote:
            in_squote = not in_squote
        elif ch == '"' and not in_squote:
            in_dquote = not in_dquote
        elif ch == ">" and not in_squote and not in_dquote:
            # 前一个非空白字符决定语义: 数字/& 表示流号，其他表示 stdout 重定向
            # 只要遇到裸 > 就是重定向
            return True
        i += 1
    return False


def matches_spec(cmd: str, spec: str) -> bool:
    """判断命令是否匹配权限规则的 spec（如 "git *"）。

    用 fnmatch 语义。空 spec 匹配任意命令。
    """
    import fnmatch

    if not spec:
        return True
    head = get_command_head(cmd)
    # 用完整命令和前缀头双重匹配
    return fnmatch.fnmatchcase(cmd, spec) or fnmatch.fnmatchcase(head, spec)
