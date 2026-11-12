"""`apply_mode = llm` 的生效桥：把"配置层要重建 LLM 栈"接到具体入口的装配点上。

## 为什么需要桥，而不是直接在配置层重建

`config_ops` 不知道 provider 怎么造、QueryLoop 在哪——那是 CLI / serve 各自的事。
所以配置层只发一个信号（`apply_mode=llm`），由入口在装配完成时把"怎么重建"登记进来。
没登记的入口（库调用、单测、还没接线的 serve）拿不到回调，回执就如实写"重启后生效"，
**绝不假装生效**——那正是当初 `policy` 模式诞生的原因。

## 失败语义

重建失败（key 缺失、模型名打错、网络不可用）时**保留旧栈**：先构造新 provider，
成功才切换。先拆后建会把"配置写错"升级成"会话坏掉"，是完全不同的故障级别。

@author aceFelix
"""

from __future__ import annotations

from typing import Callable

#: 入口登记的重建函数：无参，返回 (是否成功, 给人看的一句话)
_REBINDER: Callable[[], tuple[bool, str]] | None = None


def set_llm_rebinder(fn: Callable[[], tuple[bool, str]] | None) -> None:
    """登记（或清除）当前入口的重建方式。"""
    global _REBINDER
    _REBINDER = fn


def has_rebinder() -> bool:
    return _REBINDER is not None


def rebuild_llm_stack() -> tuple[bool, str]:
    """`apply_mode=llm` 的生效回调（登记给 config_specs.register_apply_hook）。"""
    if _REBINDER is None:
        return False, "当前入口没有装配 LLM 栈，配置已保存，重启后生效"
    try:
        return _REBINDER()
    except Exception as e:  # noqa: BLE001 - 生效失败不该把"配置写入成功"变成失败
        return False, f"重建 LLM 栈失败：{type(e).__name__}: {e}（旧模型继续可用）"
