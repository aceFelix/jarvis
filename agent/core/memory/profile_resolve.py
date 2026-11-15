"""画像提炼的两段式裁决（P1）。

## 为什么拆成两段

单段提炼把"已有画像"与"本次对话"塞进同一个 prompt：候选与裁决互相污染、
没有"不记"的正式出口、候选真正的近邻还可能不在展示的前 80 条里。

拆开后：

1. **抽取段**：只读增量对话，输出候选事实（不判重复）；
2. **逐条召回**（纯本地）：每个候选各自带 top-K 最像的旧条目；
3. **裁决段**：模型只看"候选 + 它的近邻"，输出四操作
   `add / update / delete / noop`，并给 add/update 生成检索别名。

`noop` 是新增的关键出口——"看着像重复但不值得改写法"终于有地方放，
重复率会明显下降。两段都失败时本轮不写盘、不推进水位（下次重试）。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agent.core.memory.profile_json import parse_json_object
from agent.core.memory.profile_store import (
    CATEGORY_HINTS,
    PROFILE_CATEGORIES,
    ProfileEntry,
    ProfileStore,
)

#: 单条事实的字数上限（硬闸：超过直接拒绝，不截断）
MAX_CONTENT_CHARS = 40

#: 允许的裁决操作
_OPS = {"add", "update", "delete", "noop"}

#: 模型可能吐出的同义 operation 名 → 标准名
_OP_ALIASES = {
    "new": "add", "create": "add", "insert": "add",
    "modify": "update", "replace": "update", "change": "update",
    "remove": "delete", "drop": "delete",
    "skip": "noop", "none": "noop", "ignore": "noop", "keep": "noop",
}

#: 调用 LLM 的函数签名（由 refiner 注入，避免模块循环依赖）
CallLLM = Callable[..., str]


@dataclass
class Candidate:
    """抽取段产出的候选事实。"""

    content: str
    category: str = "other"
    confidence: float = 0.5
    recalls: list[ProfileEntry] = field(default_factory=list)


@dataclass
class Decision:
    """裁决段产出的单个操作。"""

    op: str
    content: str = ""
    category: str = "other"
    confidence: float = 0.5
    aliases: list[str] = field(default_factory=list)
    target_id: str = ""
    reason: str = ""


# ─────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────

def build_extract_prompt(dialog_text: str) -> str:
    """第①段 prompt：只抽候选，不判重复、不生成别名。"""
    cats = "\n".join(f"- {c}：{CATEGORY_HINTS.get(c, '')}" for c in PROFILE_CATEGORIES)
    return f"""你是用户画像提炼器。你的唯一任务是从对话里提取【关于用户本人】的持久事实，
让 AI 助手长期“懂这个人”，而不是“懂他手上的系统”。

## 画像是什么（只记这些）
画像回答一个问题：**他是一个什么样的人**。包括：
- 身份与背景（职业、行业、学历、城市、年龄）
- 性格与处事风格（内向/外向、急性子、完美主义、怕麻烦……）
- 爱好与消遣（平时喜欢干什么、追什么、玩什么）
- 作息与生活节奏（几点睡、是否熬夜、周末怎么过）
- 偏好与口味（审美、风格、喜欢简短还是详细）
- 关系（家人、朋友、同事、合作伙伴的身份，不记隐私细节）
- 愿望、目标、抱负，以及在意/焦虑/纠结的事（“心思”）

## 画像不是什么（一律不记）
- 贾维斯/助手自身的工作流程、工具调用、命令、端口、路径、依赖、报错与调试过程
- 具体项目的技术实现细节（框架、端口、目录结构、部署方式、配置项）
- 一次性事件、临时任务、当天安排、闹钟提醒
- 环境/网络状态、设备配置、软件安装位置
- 对话寒暄、工具输出、代码片段

判断口径：**这条 5 年后还成立吗？它描述的是“人”还是“事/系统”？**
描述事或系统的一律不记。拿不准就不记——画像宁少勿滥；
细碎事实属于长期记忆（MEMORY.md），不进画像。

## 类别（category 只能取以下值）
{cats}

## 抽取规则
1. 一条只装一个事实，≤40 字；超过 40 字的请概括或拆成多条
2. content 用第三人称简体中文陈述（如“习惯深夜工作”），不要写“用户说……”
3. confidence 取 0.3~0.95：明确自述取高，从行为推断取低
4. 这一阶段**不要判断是否与已有画像重复**，那是下一步的事
5. 没有值得记的就返回空数组

## 本次对话
{dialog_text}

## 输出（严格 JSON，不要输出其他任何内容）
{{"candidates": [{{"content": "...", "category": "...", "confidence": 0.8}}]}}"""


def build_resolve_prompt(candidates: list[Candidate], *, include_aliases: bool = True) -> str:
    """第③段 prompt：候选 + 各自近邻 → 四操作。"""
    blocks: list[str] = []
    for i, c in enumerate(candidates, 1):
        lines = [f"候选 {i}: {c.content} [{c.category}]"]
        if c.recalls:
            for e in c.recalls:
                lines.append(f"  已有: [{e.id}] {e.category}: {e.content}")
        else:
            lines.append("  已有: （无相似条目）")
        blocks.append("\n".join(lines))
    body = "\n\n".join(blocks)

    alias_rules = (
        "\n- add / update 必须给 2–5 个 aliases（每个 2–6 字）：覆盖用户可能怎么问这条事实，"
        "如“熬夜/晚睡/作息晚”；不要重复 content 原词，不要编造与事实矛盾的说法"
        if include_aliases
        else "\n- 不要输出 aliases 字段"
    )
    alias_field = ', "aliases": ["...", "..."]' if include_aliases else ""

    return f"""你是用户画像的裁决器。下面每条候选都附上了它的相似旧条目。
请为每条候选决定一个操作，只输出 JSON。

## 操作
- `add`：完全新的事实 → 新增
- `update`：与某条旧条目是同一事实但表述更准/更新 → 替换该条（给 target_id）
- `delete`：旧条目已失效或是一次性项 → 删除该条（给 target_id + reason）
- `noop`：看着像重复、或拿不准、或没有新增信息 → **什么都不做**

## 规则
1. 命中已有条目 → 只能 `update` 或 `noop`，**禁止再 add**
2. 拿不准 → `noop`（宁可少记，也不要制造重复）
3. update 的 content 仍须 ≤40 字、第三人称简体中文
4. target_id 只能取下面列出的 `[ent_xxx]`，不要编造
5. 可以只处理一部分候选，其余不输出{alias_rules}

## 候选与旧条目
{body}

## 输出（严格 JSON，不要输出其他任何内容）
{{"facts": [
  {{"op": "add", "content": "...", "category": "...", "confidence": 0.8{alias_field}}},
  {{"op": "update", "target_id": "ent_xxx", "content": "...", "confidence": 0.9{alias_field}}},
  {{"op": "delete", "target_id": "ent_xxx", "reason": "..."}},
  {{"op": "noop", "target_id": "ent_xxx"}}
]}}"""


# ─────────────────────────────────────────────────────────────
# 解析与校验
# ─────────────────────────────────────────────────────────────

def _clamp_conf(value: Any, default: float = 0.5) -> float:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        conf = default
    return max(0.3, min(0.95, conf))


def validate_candidate(raw: dict) -> Candidate | None:
    """硬闸：content 非空且 ≤40 字；类别白名单；置信度 clamp。"""
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("content", "")).strip()
    if not content or len(content) > MAX_CONTENT_CHARS:
        return None
    category = str(raw.get("category", "other")).strip()
    if category not in PROFILE_CATEGORIES:
        category = "other"
    return Candidate(
        content=content,
        category=category,
        confidence=_clamp_conf(raw.get("confidence", 0.5)),
    )


def parse_candidates(data: dict) -> tuple[list[Candidate], int]:
    """解析抽取结果，返回 (合法候选, 被拒条数)。"""
    raw_items = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        return [], 0
    out: list[Candidate] = []
    rejected = 0
    for item in raw_items:
        cand = validate_candidate(item)  # type: ignore[arg-type]
        if cand is None:
            rejected += 1
        else:
            out.append(cand)
    return out, rejected


def _norm_op(op: Any) -> str:
    key = str(op or "").strip().lower()
    key = _OP_ALIASES.get(key, key)
    return key if key in _OPS else ""


def parse_decisions(data: dict) -> tuple[list[Decision], int]:
    """解析裁决结果，返回 (合法操作, 被拒条数)。"""
    raw_items = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(raw_items, list):
        return [], 0
    out: list[Decision] = []
    rejected = 0
    for item in raw_items:
        if not isinstance(item, dict):
            rejected += 1
            continue
        op = _norm_op(item.get("op"))
        if not op:
            rejected += 1
            continue
        target = str(item.get("target_id", "")).strip()
        if op in ("update", "delete") and not target:
            rejected += 1
            continue
        content = str(item.get("content", "")).strip()
        if op in ("add", "update"):
            if not content or len(content) > MAX_CONTENT_CHARS:
                rejected += 1
                continue
        category = str(item.get("category", "other")).strip()
        if category not in PROFILE_CATEGORIES:
            category = "other"
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        out.append(Decision(
            op=op,
            content=content,
            category=category,
            confidence=_clamp_conf(item.get("confidence", 0.5)),
            aliases=[str(a) for a in aliases],
            target_id=target,
            reason=str(item.get("reason", ""))[:200],
        ))
    return out, rejected


# ─────────────────────────────────────────────────────────────
# 三段执行
# ─────────────────────────────────────────────────────────────

def extract_candidates(
    provider: Any,
    model: str,
    dialog_text: str,
    call_llm: CallLLM,
) -> tuple[list[Candidate], int]:
    """第①段：调 LLM 抽候选。返回 (候选, 被拒条数)。"""
    raw = call_llm(provider, model, build_extract_prompt(dialog_text), max_tokens=900)
    return parse_candidates(parse_json_object(raw))


def attach_recalls(candidates: list[Candidate], store: ProfileStore, top_k: int) -> None:
    """第②段（纯本地）：为每个候选填 top-K 近邻。"""
    for cand in candidates:
        cand.recalls = store.recall_similar(cand.content, cand.category, top_k=top_k)


def resolve_decisions(
    provider: Any,
    model: str,
    candidates: list[Candidate],
    call_llm: CallLLM,
    *,
    include_aliases: bool = True,
) -> tuple[list[Decision], int]:
    """第③段：调 LLM 做四操作裁决。返回 (操作, 被拒条数)。"""
    prompt = build_resolve_prompt(candidates, include_aliases=include_aliases)
    raw = call_llm(provider, model, prompt, max_tokens=1200)
    return parse_decisions(parse_json_object(raw))


def apply_decisions(
    decisions: list[Decision],
    store: ProfileStore,
    session_id: str,
    report: Any,
) -> None:
    """第④段：落盘。

    - add → merge_or_add（存储层查重仍生效）
    - update → 建新条目 + 旧条目**打失效标记**（P2b 软删除，保留历史）；
      target 幻觉/已失效时降级为 add
    - delete → store.delete（显式删除仍物理删）
    - noop → 不写
    计数全部记在 report 上（added/updated/merged/deleted/noop）。
    """
    def _bump(name: str) -> None:
        setattr(report, name, int(getattr(report, name, 0) or 0) + 1)

    for d in decisions:
        if d.op == "noop":
            _bump("noop")
            continue
        if d.op == "delete":
            if d.target_id and store.delete(d.target_id):
                _bump("deleted")
            continue
        if d.op == "add":
            entry = ProfileEntry.new(
                d.content, d.category, d.confidence, session_id, aliases=d.aliases
            )
            _, merged = store.merge_or_add(entry)
            _bump("merged" if merged else "added")
            continue
        if d.op == "update":
            # target 不存在（幻觉/已失效）→ 当 add 处理，仍走存储层查重闸
            if d.target_id and store.get(d.target_id):
                entry = ProfileEntry.new(
                    d.content, d.category, d.confidence, session_id, aliases=d.aliases
                )
                kept, merged = store.merge_or_add(entry)
                # P2b：旧值不物理删——打失效标记并指向新条目。kept 可能就是 target
                # 自身（update 只是把旧事实再确认/微调），那种情况不能把它标成失效。
                if kept.id != d.target_id:
                    store.invalidate(d.target_id, superseded_by=kept.id)
                _bump("merged" if merged else "updated")
            else:
                entry = ProfileEntry.new(
                    d.content, d.category, d.confidence, session_id, aliases=d.aliases
                )
                _, merged = store.merge_or_add(entry)
                _bump("merged" if merged else "added")
