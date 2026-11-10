"""标准流的编码兜底 —— 让「编不出来的字符」降级，而不是把整条命令打崩。

## 为什么需要这一层

2026-11-10 实机：`jarvis --doctor > log.txt`（以及 `| tee`、被父进程捕获）在简体中文
Windows 上直接崩掉——

    UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f4e6'

根因不在 Rich、也不在那张表格，而在 Python 对**非控制台**的 stdout 采用区域编码
（简体中文 Windows = cp936 / GBK）且 `errors` 是 `strict`：`agent/doctor.py` 表格
标题里的 `📦` 编不出去，异常就从打印那一行抛穿整个命令。直连控制台时 Python 走
WindowsConsoleIO（utf-8）所以平时看不见，一重定向就现形——这也是它长期没被发现
的原因：没人用管道跑诊断，而用管道跑的时候只会看到"程序崩了"。

## 口径：只放宽 errors，不换编码

- **不换成 utf-8**：管道下游（PowerShell 5.1 按 cp936 解码）会把中文读成乱码，
  问题从"崩"变成"糊"，更难排查。
- **中文字符本来就是 GBK 子集**，真正会被降级的只有 emoji 与罕见符号，诊断输出
  丢一个图标不影响读结论。
- 降级用 `replace`（变成 `?`）而不是 `backslashreplace`（变成 `\\U0001f4e6`）：
  前者在表格里占一格，后者会把列宽撑爆。

@author aceFelix
"""

from __future__ import annotations

import sys
from typing import Any

#: 会被处理的流名。顺序即调用顺序，便于测试断言。
_STREAM_NAMES = ("stdout", "stderr")


def make_stdio_crash_proof() -> list[str]:
    """把 stdout / stderr 的编码错误策略放宽为 ``replace``。

    幂等，重复调用无副作用；入口处调用一次即可。

    @return 实际调整过的流名列表（用于日志与测试断言）；一个都没改成时返回空列表
    """
    adjusted: list[str] = []
    for name in _STREAM_NAMES:
        stream: Any = getattr(sys, name, None)
        # pythonw / 冻结 GUI 下 sys.stdout 可能是 None；pytest 捕获器、StringIO
        # 之类没有 reconfigure。这两类本来也不会因编码抛错，静默跳过即可。
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (AttributeError, OSError, ValueError):
            # 已关闭或不允许改动的流：保持原样，绝不能在入口处再抛一次错——
            # 这个函数存在的意义就是不让人栽在打印上。
            continue
        adjusted.append(name)
    return adjusted
