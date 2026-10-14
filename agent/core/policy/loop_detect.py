"""空转循环检测器（W1 运行时层第三环，2026-10-14 贪吃蛇空转案）。

症状：agent 拿着记忆里的假线索（如"用户有一个 snake-game 项目"）反复
换姿势全盘 ls/find，每次都被沙箱"中风险自动放行"绿灯通过，没有任何
一环能把空转拦下来问人——15 分钟烧在同一个悬案上。

对策：对闸门后即将执行的工具调用做**签名滑动窗口**统计。同一签名在
最近 _WINDOW 次调用中出现 ≥ _THRESHOLD 次即判定空转，把本次调用强制
升级 ASK 问用户（拦刹车）。与 query_loop 的截断熔断互补：熔断治
"输出预算饿死"，本器治"行为原地打转"。

设计约束（零配置哲学同租约/预算）：
- 未构成循环时零介入，无开关、无持久化；
- 每轮用户消息开头 reset()，只统计当前任务内的调用序列；
- 每轮最多提示 _max_fires 次，防"每次都问"变成新的骚扰源
  （用户连续放行后本器静默，信任人的判断）。

@author aceFelix
"""

from __future__ import annotations

from collections import deque
from typing import Any

# 滑动窗口大小与循环判定阈值：同型调用在最近 6 次里出现 3 次即空转
_WINDOW = 6
_THRESHOLD = 3
# 单轮任务内最多强制 ASK 次数（超过则静默，不再骚扰）
_MAX_FIRES = 2


def call_signature(tool_name: str, args: dict[str, Any]) -> str:
    """工具调用签名：工具名 + 最像一个动作的入参归一化。

    Bash 取 command，File* 取 path，其余取最短字符串参数（信息量最高）。
    归一化：小写、连续空白压成单空格、只取前 48 字符——ls -la /e 与
    ls  /E:/ 视为同型，find 加不同 -maxdepth 也算同型（都在翻目录）。

    @param tool_name: 工具名
    @param args: 工具入参
    @return: 归一化签名字符串
    """
    key = ""
    if tool_name.lower() == "bash":
        key = str(args.get("command", ""))
    elif "path" in args:
        key = str(args["path"])
    else:
        # 兜底：取最短的非空字符串参数（动作目标通常比说明文字短）
        strs = [str(v) for v in args.values() if isinstance(v, str) and v.strip()]
        key = min(strs, key=len) if strs else ""
    norm = " ".join(key.lower().split())[:48]
    # find/ls/tree 等目录翻找类：进一步折叠参数，只留命令词本体，
    # 让"换个姿势同一条搜索"也命中同型
    head = norm.split(" ")[0] if norm else ""
    if head in ("find", "ls", "tree", "du"):
        norm = head
    return f"{tool_name}:{norm}"


class LoopDetector:
    """签名滑动窗口空转检测器（进程内单例，按轮复位）。"""

    def __init__(self) -> None:
        self._recent: deque[str] = deque(maxlen=_WINDOW)
        self._fires = 0

    def reset(self) -> None:
        """新一轮用户消息开始时清空历史与提示配额。"""
        self._recent.clear()
        self._fires = 0

    def observe(self, signature: str) -> bool:
        """记录一次调用签名，返回是否判定空转（本器提示一次即静默）。

        @param signature: call_signature() 产出的归一化签名
        @return: True 表示应强制 ASK 拦刹车；False 表示正常放行观测
        """
        self._recent.append(signature)
        if self._fires >= _MAX_FIRES:
            return False
        if signature in self._recent and list(self._recent).count(signature) >= _THRESHOLD:
            self._fires += 1
            return True
        return False


# 进程级单例：orchestrator 与 query_loop 共用同一观察序列
_detector = LoopDetector()


def get_loop_detector() -> LoopDetector:
    """获取全局空转检测器单例。@return: LoopDetector 实例"""
    return _detector


def check_tool_loop(tool_name: str, args: dict[str, Any]) -> str | None:
    """管线/闸门放行前的空转拦截检查。

    @param tool_name: 工具名
    @param args: 工具入参
    @return: 判定空转时返回给用户的提示语（作为 ASK reason）；否则 None
    """
    sig = call_signature(tool_name, args)
    if get_loop_detector().observe(sig):
        return (
            f"检测到重复的同型操作（{sig} 在最近 {_WINDOW} 次调用中出现 "
            f"{_THRESHOLD} 次），可能正在原地打转。继续执行请回复允许，"
            "或换个思路/告诉我新的指令"
        )
    return None
