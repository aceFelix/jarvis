"""画像注入渲染（P1c）：主题优先 + 两轮填充 + 预算口径可切换。

## 为什么不用简单的"按置信度排 + 放不下就跳过"

旧实现只有 confidence×新近度一个维度，带来三个问题：

1. **没有主题维度**：无论用户问什么，注入的永远是"高置信那批"；
2. **长条目挤掉短条目**：一条 40 字事实会占掉 4–5 条短事实的位置；
3. **伪 token 口径**（`len/4`）：对中文严重低估，名义 300 实际能装 1200 字符。

对应三处改造：

- `prefer_topics`：命中主题的条目**优先注入**（空列表＝不设主题偏好）；
- **两轮填充**：先放"长度 ≤ 中位数"的条目，再补长条目——短事实优先占位；
  这一条**无条件生效**（只影响"谁先占预算"，不改预算总量），故库内条目变多后
  注入次序与旧版不同；
- `mode`：`pseudo`（现状 `len/4`，默认）/ `weighted`（中英分权真 token）。
  **默认仍用 pseudo**，切到 weighted 需同步下调 `profile_inject_token_limit`，
  具体值须在 P4 回归集实测后确定。

@author aceFelix
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Sequence

from agent.core.memory.compactor import (
    estimate_text_tokens,
    estimate_text_tokens_weighted,
)
from agent.core.memory.profile_store import entry_weight

if TYPE_CHECKING:  # 仅类型标注：运行时导入会与 profile_store 形成循环依赖
    from agent.core.memory.profile_store import ProfileEntry

#: 注入段的固定标题（与旧实现逐字节一致，避免破坏前缀缓存）
HEADER = "# 关于用户（画像记忆）"

#: 预算口径白名单
BUDGET_MODES = ("pseudo", "weighted")


def _line(entry: ProfileEntry) -> str:
    return f"- {entry.content}"


def _cost(text: str, mode: str) -> int:
    """预算口径：pseudo＝len/4（现状），weighted＝中英分权真 token。"""
    if mode == "weighted":
        return max(1, int(estimate_text_tokens_weighted(text)))
    return estimate_text_tokens(text)


def _median_len(entries: Sequence[ProfileEntry]) -> int:
    lens = sorted(len(e.content) for e in entries)
    if not lens:
        return 0
    return lens[len(lens) // 2]


def render_for_prompt(
    entries: Iterable[ProfileEntry],
    *,
    token_limit: int = 300,
    prefer_topics: Sequence[str] = (),
    mode: str = "pseudo",
    now: float | None = None,
    max_entries: int = 0,
) -> str:
    """把条目渲染成注入块。

    Args:
        entries: 已按任意顺序给出的条目（本函数内部重排）。
        token_limit: 预算上限（按 `mode` 口径计量）。
        prefer_topics: 优先注入的类别列表；空列表＝不设主题偏好。
        mode: `pseudo`（默认，兼容现状）或 `weighted`。
        now: 权重计算时间基准（仅测试注入）。
        max_entries: 最多注入的条目数（0 = 不限）；超出部分靠 RecallProfile 按需检索。

    Returns:
        注入文本；无条目或预算装不下任何一条时返回空字符串。

    @author aceFelix
    """
    # 纯空白条目等同空内容：渲染出来只是一行横杠 + 空格，白占预算
    items = [e for e in entries if str(getattr(e, "content", "") or "").strip()]
    if not items:
        return ""

    budget_mode = mode if mode in BUDGET_MODES else "pseudo"
    budget = int(token_limit)
    cap = max(0, int(max_entries or 0))
    prefer = {str(t) for t in (prefer_topics or []) if str(t)}

    def _key(e: ProfileEntry) -> tuple[float, float, float]:
        # 权重（置信度×新鲜度）优先：新近确认过的事实先占预算
        return (entry_weight(e, now=now), e.confidence, e.updated_at)

    # 主题命中单独排一队置顶；其余条目按长度中位数分两轮（短→长）
    preferred = sorted([e for e in items if e.category in prefer], key=_key, reverse=True)
    rest = [e for e in items if e.category not in prefer]
    median = _median_len(rest or items)
    short = sorted([e for e in rest if len(e.content) <= median], key=_key, reverse=True)
    long_ = sorted([e for e in rest if len(e.content) > median], key=_key, reverse=True)

    lines: list[str] = []
    used = 0
    # 顺序：主题命中 → 短条目 → 长条目；放不下就跳过，继续试后面的
    for entry in preferred + short + long_:
        if cap and len(lines) >= cap:
            break
        line = _line(entry)
        cost = _cost(line, budget_mode)
        if used + cost > budget:
            continue
        lines.append(line)
        used += cost

    if not lines:
        return ""
    return HEADER + "\n" + "\n".join(lines)
