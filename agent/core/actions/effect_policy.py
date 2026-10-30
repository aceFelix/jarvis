"""W3.2 副作用账本的「判定与取值」—— 从 orchestrator 抽出的无状态帮手。

## 为什么单独一个模块（2026-10）

orchestrator 在加入「文件级撤销」的写前留底之后涨到 802 行，越过项目的
800 行红线。这几段逻辑的共同特征是**不依赖编排器实例状态**（全是纯读配置、
读工具声明、读 ctx.extra），因此可以整体搬出来：

- 主文件回到安全线内，编排流程本身更容易通读；
- 单测可以直接打这些函数，不必为了验证「取哪个 cid」去搭一整套编排器。

搬动只改位置、不改口径 —— 每条判定的理由都照原样保留在对应函数上。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.tool import Tool


def actions_config(ctx: ToolContext) -> dict[str, Any]:
    """读 ``[actions]`` 配置（无 settings 时用安全缺省）。

    缺省口径：账本开、窗口 30 秒、本地写跟踪开。三者都是"少做事"方向，
    所以配置读不出来时按缺省走比按"全关"走更安全——全关等于默默放弃保护。
    @author aceFelix
    """
    s = getattr(ctx, "settings", None)
    return {
        "enabled": bool(getattr(s, "actions_journal_enabled", True)),
        "window_s": float(getattr(s, "actions_undo_window_s", 30) or 0),
        "track_local": bool(getattr(s, "actions_track_local_writes", True)),
        "extra_tools": set(getattr(s, "actions_irreversible_tools", []) or []),
    }


def is_irreversible_call(tool: Tool, args: dict[str, Any], extra_tools: set[str]) -> bool:
    """这是否一个"出了本机就收不回"的调用。

    配置里的 ``irreversible_tools`` 名单是给"代码里还没声明、但用户本机确实
    想保护"的工具留的应急口子（改配置比重启改代码快）。
    @author aceFelix
    """
    try:
        if tool.is_irreversible(args):
            return True
    except Exception:
        pass  # 工具自己报错时按"可逆"处理，不能因为它把一个正常调用拖进窗口
    return tool.name in extra_tools


def note_effect_id(ctx: ToolContext, rec_id: str) -> None:
    """把新记的账本 id 回报给宿主（``ctx.extra["effect_ids"]``），供任务脊柱回填。

    为什么需要这一环：`/undo --task <goal>` 靠 `TaskStep.journal_ids` 找到一步
    干过什么，而驱动器在子代理跑完前拿不到任何账本 id。没这个回扣链，
    `journal_ids` 永远为空，"按目标批量补偿"就只是单测里手工造得起的功能。
    宿主没传列表时静默跳过：普通对话不需要它，不该为它弄一个全局累加器。
    @author aceFelix
    """
    bucket = (ctx.extra or {}).get("effect_ids")
    if isinstance(bucket, list) and rec_id:
        bucket.append(rec_id)


def turn_checkpoint_id(ctx: ToolContext) -> str:
    """取本轮的文件检查点 ID（宿主在轮次开始前写入 ``ctx.extra``）。

    为什么要 extra 而不是去翻 messages：检查点是轮次开始前打的，但它的 cid
    要到轮次结束才挂到用户消息上（main.py / engine.py 都是这个顺序），工具
    调用时刻消息里还查不到。

    区分"键存在但为空"与"键不存在"：前者说明宿主自己管每轮 cid、本轮确实
    没有检查点（没装 git 或已关闭），必须照实返回"无"；去翻消息会拿到
    **上一轮**的 cid，把"回退到本轮开始前"做成"多退一轮"。
    @author aceFelix
    """
    extra = ctx.extra or {}
    if "checkpoint_cid" in extra:
        return str(extra.get("checkpoint_cid", "") or "")
    for m in reversed(ctx.messages or []):
        if getattr(m, "role", "") == "user":
            return str((getattr(m, "extra", None) or {}).get("checkpoint_id", "") or "")
    return ""
