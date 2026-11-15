"""画像记忆存储（Profile Memory Store）。

长期记忆三层架构（画像/情景/关系）的第一层：结构化存储用户的
偏好、习惯、背景事实。会话结束后由 profile_refiner 异步提炼写入，
下次会话启动时经 render_for_prompt() 限额注入 system prompt。

存储位置: ~/.jarvis/memory/profile.json（纯 JSON，用户可直接查看编辑，
/memory 命令提供查看/删除/新增入口）。

设计要点:
- 原子写（临时文件 + os.replace），进程被杀不会写坏
- threading.Lock 保护并发（提炼后台线程与主线程同时读写）
- 条目上限淘汰（confidence × 新近度 排序，超出淘汰最低者）

@author aceFelix
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

# 合法画像类别（提炼 prompt 与存储校验共用，与计划文档一致）
# 边界：画像只记“关于人”的稳定信息；助手工作流/技术细节/临时事件不属于画像。
PROFILE_CATEGORIES = (
    "identity",      # 身份背景（职业、行业、学历、城市、年龄）
    "personality",   # 性格与处事风格（内向、急性子、完美主义、怕麻烦）
    "preference",    # 爱好与口味（喜欢什么、审美、沟通偏好）
    "work_habit",    # 工作习惯（作息之外的工作方式、计划习惯）
    "schedule",      # 作息（几点睡、是否熬夜、周末怎么过）
    "relationship",  # 关系（家人、朋友、同事、合作伙伴的身份）
    "aspiration",    # 愿望与抱负（想做什么、目标、长期期待）
    "mindset",       # 心思与状态（在意什么、焦虑/纠结/反复想的事）
    "tool_usage",    # 长期偏好用什么工具（个人习惯；不记配置/路径/端口）
    "project",       # 在做什么方向（身份层面；不记技术实现细节）
    "other",
)

#: 类别的一句话判据（提炼 prompt 直接渲染，避免模型靠名字揣度）
CATEGORY_HINTS = {
    "identity": "职业、行业、学历、所在城市、年龄段",
    "personality": "性格与处事风格，如内向、急性子、完美主义、怕麻烦",
    "preference": "爱好、审美、口味、沟通偏好（喜欢简短还是详细）",
    "work_habit": "工作方式与计划习惯（如先做计划再动手）",
    "schedule": "作息与生活节奏（几点睡、是否熬夜、周末怎么过）",
    "relationship": "家人、朋友、同事、合作伙伴的身份（不记隐私细节）",
    "aspiration": "愿望、目标、抱负、长期期待",
    "mindset": "在意的事、焦虑与纠结、反复想的念头",
    "tool_usage": "长期偏好用什么软件/工具（个人习惯，不记配置与路径）",
    "project": "在做什么方向/长期投入的事（不记框架、端口、目录结构）",
    "other": "确实关于本人但无法归入上述类别的稳定事实",
}

#: 生命周期白名单（P2a）：决定 decay 与替换语义，与 category 正交
LIFECYCLES = (
    "permanent",  # 姓名/学历/出生/家人/长期抱负：永不 decay，只能被显式 update 替换
    "stateful",   # 所在地/在做什么/主力工具：会变但不会立刻失效，靠半衰期淡忘
    "episodic",   # 一次性事项：带 expire_at，到期直接出清
)

#: 稳定性排序（合并时取更稳的一个，不允许 permanent 被 stateful 降级）
_LIFECYCLE_RANK = {"permanent": 3, "stateful": 2, "episodic": 1}

#: 默认派为 permanent 的类别：只有“5 年后仍成立”的三类
_PERMANENT_CATEGORIES = frozenset({"identity", "relationship", "aspiration"})


def default_lifecycle_for_category(category: str) -> str:
    """类别 → 初始 lifecycle（P2a 的确定性派生，不靠 LLM 拍脑袋）。

    只有身份背景、关系、长期抱负走 permanent；偏好/习惯/作息/心思/工具/
    在做的事默认 stateful（会变，交给半衰期淡忘）。episodic 不在此派生——
    只有带明确到期时间的事实才是临时的（P2c 由提炼显式给出）。
    @author aceFelix
    """
    return "permanent" if str(category) in _PERMANENT_CATEGORIES else "stateful"


def _normalize_lifecycle(raw: object, category: str) -> str:
    """lifecycle 归一：空值按类别派生，非法值落 stateful。"""
    value = str(raw or "").strip()
    if not value:
        return default_lifecycle_for_category(category)
    return value if value in LIFECYCLES else "stateful"


def _most_stable_lifecycle(*lifecycles: str) -> str:
    """取最稳的生命周期（permanent > stateful > episodic）。"""
    return max(lifecycles, key=lambda v: _LIFECYCLE_RANK.get(v, 0))


# 类别中文说明（/memory 展示用）
CATEGORY_LABELS = {
    "identity": "身份背景",
    "personality": "性格",
    "preference": "偏好与爱好",
    "work_habit": "工作习惯",
    "schedule": "作息",
    "relationship": "关系",
    "aspiration": "愿望与抱负",
    "mindset": "心思与状态",
    "tool_usage": "工具偏好",
    "project": "在做的事",
    "other": "其他",
}


def profile_store_path() -> Path:
    """画像存储路径: ~/.jarvis/memory/profile.json"""
    d = Path.home() / ".jarvis" / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d / "profile.json"


# ─────────────────────────────────────────────────────────────
# 深拷贝查重（2026-11 修复：完全相同的记忆被反复提炼入库）
# 去重不能只靠提炼 prompt 里"重复放 updates"的软约束——LLM 经常无视，
# 存储层必须有确定性兜底：归一化相同或字符二元组高度相似即视为同一事实。
# @author aceFelix
# ─────────────────────────────────────────────────────────────

# 归一化时剥掉的句尾标点（"习惯深夜工作。" 与 "习惯深夜工作" 是同一事实）
_TRAILING_PUNCT = "。，、；;，.!！?？～~"

# 近重复判定阈值：x2 中文串阈值 ≈0.72，"职业是Agent开发者" vs
# "职业是Agent应用开发工程师"（0.50）属措辞差异，不该自动合并——
# 语义级去重交给提炼 LLM 的 updates，本层只拦"几乎原样重复"。
_SIM_MERGE_THRESHOLD = 0.85


def normalize_profile_content(text: str) -> str:
    """画像查重归一化：全角转半角、去全部空白、转小写、剥句尾标点。

    仅用于比较，不改写存储的原文。
    """
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        # 全角 ASCII（U+FF01~U+FF5E）→ 半角；全角空格单独处理
        if code == 0x3000:
            continue
        if 0xFF01 <= code <= 0xFF5E:
            ch = chr(code - 0xFEE0)
        if ch.isspace():
            continue
        out.append(ch)
    s = "".join(out).lower()
    return s.rstrip(_TRAILING_PUNCT)


def profile_content_similarity(a: str, b: str) -> float:
    """两条画像内容的相似度（字符二元组 Jaccard，0~1）。

    中文取集合交并比对语序不敏感、无需分词依赖；长度 <2 的短串退化
    为归一化全等判定。先做严格全等短路，避免每次合并比较都算集合。
    """
    na, nb = normalize_profile_content(a), normalize_profile_content(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if len(na) < 2 or len(nb) < 2:
        return 0.0
    ga = {na[i:i + 2] for i in range(len(na) - 1)}
    gb = {nb[i:i + 2] for i in range(len(nb) - 1)}
    inter = len(ga & gb)
    union = len(ga | gb)
    return inter / union if union else 0.0


#: 别名规则：每个 2–6 字；单条最多保留 8 个（合并后）
_ALIAS_MIN_LEN = 2
_ALIAS_MAX_LEN = 6
_ALIAS_CAP = 8


def normalize_aliases(raw: object) -> list[str]:
    """清洗别名列表：去空白、限长 2–6 字、去重、截到 8 个。

    别名只服务检索（用户会怎么问这条事实），不参与展示与去重判据，
    所以允许脏值被静默丢弃，不抛异常。@author aceFelix
    """
    out: list[str] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for item in raw:
        s = str(item or "").strip()
        if not (_ALIAS_MIN_LEN <= len(s) <= _ALIAS_MAX_LEN):
            continue
        if s in out:
            continue
        out.append(s)
        if len(out) >= _ALIAS_CAP:
            break
    return out


@dataclass
class ProfileEntry:
    """一条画像记忆。"""

    id: str
    category: str = "other"
    content: str = ""
    aliases: list[str] = field(default_factory=list)  # 检索别名（2–5 个，2–6 字）
    lifecycle: str = "stateful"       # P2a：permanent / stateful / episodic
    expire_at: float = 0.0            # 仅 episodic：到期直接出清（0 = 不设）
    confidence: float = 0.5           # 0~1，提炼时 LLM 给出
    source_session: str = ""          # 溯源：来自哪个会话
    created_at: float = 0.0
    updated_at: float = 0.0
    last_referenced_at: float = 0.0   # M3 维护管线衰减依据
    ref_count: int = 0
    invalidated_at: float = 0.0       # P2b：软删除时间（0 = 有效）
    superseded_by: str = ""            # P2b：被哪条新事实取代（目标条目 id）

    @staticmethod
    def new(
        content: str,
        category: str = "other",
        confidence: float = 0.5,
        source_session: str = "",
        aliases: list[str] | None = None,
        lifecycle: str = "",
        expire_at: float = 0.0,
    ) -> "ProfileEntry":
        """创建新条目（生成 id 与时间戳）。

        lifecycle 留空时按类别派生（P2a）；显式给非法值则落 stateful。
        """
        now = time.time()
        cat = category if category in PROFILE_CATEGORIES else "other"
        return ProfileEntry(
            id=f"ent_{uuid.uuid4().hex[:8]}",
            category=cat,
            content=content.strip(),
            aliases=normalize_aliases(aliases or []),
            lifecycle=_normalize_lifecycle(lifecycle, cat),
            expire_at=max(0.0, float(expire_at or 0.0)),
            confidence=max(0.0, min(1.0, float(confidence))),
            source_session=source_session,
            created_at=now,
            updated_at=now,
        )


class ProfileStore:
    """画像记忆存储。线程安全，磁盘格式 version=3（兼容 1/2，缺字段按类别派生）。"""

    def __init__(self, path: Path | None = None):
        self._path = path or profile_store_path()
        self._lock = threading.Lock()
        self._entries: list[ProfileEntry] = []
        self._load()

    # ---- 读取 ----

    def entries(self, *, include_invalidated: bool = False) -> list[ProfileEntry]:
        """条目快照（按 confidence 降序、更新时间新者优先）。

        P2b：默认**只返有效条目**——失效条目（被新事实取代）不再注入、不召回、
        不计入展示；需要看历史时传 `include_invalidated=True`。
        """
        with self._lock:
            items = self._entries if include_invalidated else [
                e for e in self._entries if not e.invalidated_at
            ]
            return sorted(
                items,
                key=lambda e: (e.confidence, e.updated_at),
                reverse=True,
            )

    def get(self, entry_id: str, *, include_invalidated: bool = False) -> ProfileEntry | None:
        """按 id 取条目；默认不返回已失效条目（P2b）。"""
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    if e.invalidated_at and not include_invalidated:
                        return None
                    return e
        return None

    def recall_similar(
        self, content: str, category: str = "", top_k: int = 5
    ) -> list[ProfileEntry]:
        """按内容相似度召回 top-K 旧条目（写入侧裁决 / 检索共用）。

        打分：
        - 与 content 的字符二元组相似度；
        - 别名命中（候选包含别名或反之）+0.30；别名与候选的相似度按 0.8 折算；
        - 同类别 +0.15（粗粒度语义通道）。
        只返回 score > 0 的条目，按 (score, confidence, updated_at) 降序。
        @author aceFelix
        """
        query = (content or "").strip()
        if not query or top_k <= 0:
            return []
        scored: list[tuple[float, ProfileEntry]] = []
        for e in self.entries():
            if not e.content:
                continue
            score = profile_content_similarity(e.content, query)
            for a in e.aliases:
                if not a:
                    continue
                if a in query or query in a:
                    score += 0.30
                else:
                    score = max(score, profile_content_similarity(a, query) * 0.8)
            if category and e.category == category:
                score += 0.15
            if score > 0:
                scored.append((score, e))
        scored.sort(
            key=lambda kv: (kv[0], kv[1].confidence, kv[1].updated_at), reverse=True
        )
        return [e for _, e in scored[:top_k]]

    def __len__(self) -> int:
        """有效条目数（P2b：失效条目只算历史，不计入）。"""
        with self._lock:
            return sum(1 for e in self._entries if not e.invalidated_at)

    # ---- 写入 ----

    def upsert(self, entry: ProfileEntry) -> None:
        """新增或按 id 更新（/memory add 等显式写入用；提炼写入走 merge_or_add）。"""
        with self._lock:
            for i, e in enumerate(self._entries):
                if e.id == entry.id:
                    # 更新：保留首次创建时间
                    entry.created_at = e.created_at
                    self._entries[i] = entry
                    self._save_locked()
                    return
            self._entries.append(entry)
            self._save_locked()

    def merge_or_add(self, entry: ProfileEntry) -> tuple[ProfileEntry, bool]:
        """带确定性查重的写入：命中深重复条目则合并并返回，否则原样新增。

        命中规则：归一化全等，或字符二元组相似度 ≥ _SIM_MERGE_THRESHOLD。
        合并策略（保守，不覆盖旧条目的原文与类别）：
        - confidence 取两者较大值（同一事实再被确认一次，只增信不换写）
        - updated_at / last_referenced_at 刷新、created_at 保留最早
        - ref_count：合并本身即“又被确认了一次”，在原累计值上 +1
        - source_session：manual 优先保留；否则留先到者
        返回 (存活的条目, 是否发生合并)。提炼管线与手动入口共用本方法，
        这是“同一句话说 N 遍记 N 条”的最后一道闸。@author aceFelix
        """
        with self._lock:
            now = time.time()
            for e in self._entries:
                # P2b：失效条目是历史，不参与合并（否则会把旧值“复活”成新值）
                if e.id == entry.id or e.invalidated_at:
                    continue
                if profile_content_similarity(e.content, entry.content) >= _SIM_MERGE_THRESHOLD:
                    merged = ProfileEntry(
                        id=e.id,
                        category=e.category,
                        content=e.content,
                        aliases=normalize_aliases(list(e.aliases) + list(entry.aliases)),
                        # P2a：合并取更稳的 lifecycle（permanent 不被 stateful 降级）；
                        # 到期时间取大，有 expire_at 的那个不因合并丢失
                        lifecycle=_most_stable_lifecycle(e.lifecycle, entry.lifecycle),
                        expire_at=max(e.expire_at, entry.expire_at),
                        confidence=max(e.confidence, entry.confidence),
                        # manual 来源优先；否则留先到者（空值拿另一边的）
                        source_session=e.source_session or entry.source_session,
                        created_at=min(e.created_at, entry.created_at) or entry.created_at,
                        updated_at=now,
                        # 续命依据：再次确认既刷新“最近提及”，也累计提及次数
                        last_referenced_at=now,
                        ref_count=e.ref_count + entry.ref_count + 1,
                    )
                    for i, old in enumerate(self._entries):
                        if old.id == e.id:
                            self._entries[i] = merged
                            break
                    self._save_locked()
                    return merged, True
            self._entries.append(entry)
            self._save_locked()
            return entry, False

    def dedup_all(self) -> int:
        """一次性整理：把库里已存在的深重复条目分组归并，返回合并掉的条数。

        分组按 merge_or_add 同款判据；每组保留 confidence × 最新者为主条目
        （与 entries() 排序口径一致），其余并入主条目。用于 /memory dedup
        与画像迁移清理，manual 来源优先作为主条目。

        P2b：只归并有效条目；失效条目原样保留（它们是历史证据，不属于重复）。
        """
        with self._lock:
            invalidated = [e for e in self._entries if e.invalidated_at]
            groups: list[list[ProfileEntry]] = []
            for e in self._entries:
                if e.invalidated_at:
                    continue
                placed = False
                for g in groups:
                    if profile_content_similarity(g[0].content, e.content) >= _SIM_MERGE_THRESHOLD:
                        g.append(e)
                        placed = True
                        break
                if not placed:
                    groups.append([e])
            merged_away = 0
            new_entries: list[ProfileEntry] = []
            now = time.time()
            for g in groups:
                if len(g) == 1:
                    new_entries.append(g[0])
                    continue
                # 主条目：manual 优先，其次 (confidence, updated_at) 最高
                primary = max(
                    g,
                    key=lambda e: (e.source_session == "manual", e.confidence, e.updated_at),
                )
                primary.updated_at = max(e.updated_at for e in g)
                primary.created_at = min(e.created_at for e in g if e.created_at) or now
                primary.confidence = max(e.confidence for e in g)
                primary.aliases = normalize_aliases(
                    [a for e in g for a in e.aliases]
                )
                primary.ref_count = sum(e.ref_count for e in g)
                primary.last_referenced_at = max(e.last_referenced_at for e in g)
                primary.lifecycle = _most_stable_lifecycle(*(e.lifecycle for e in g))
                primary.expire_at = max(e.expire_at for e in g)
                if not primary.source_session:
                    # 继承组内第一个非 manual 的来源会话
                    for e in g:
                        if e.source_session and e.source_session != "manual":
                            primary.source_session = e.source_session
                            break
                new_entries.append(primary)
                merged_away += len(g) - 1
            if merged_away:
                self._entries = new_entries + invalidated
                self._save_locked()
            return merged_away

    def duplicate_group_report(self) -> list[list[ProfileEntry]]:
        """只读预演：按 merge_or_add 同款判据分组，返回长度 >1 的重复组。

        供 /memory dedup 预览使用，不写盘、不改内存。组内首个为主条目口径。

        P2b：与 dedup_all 同口径，失效条目不参与归并。
        """
        with self._lock:
            groups: list[list[ProfileEntry]] = []
            for e in self._entries:
                if e.invalidated_at:
                    continue
                placed = False
                for g in groups:
                    if profile_content_similarity(g[0].content, e.content) >= _SIM_MERGE_THRESHOLD:
                        g.append(e)
                        placed = True
                        break
                if not placed:
                    groups.append([e])
            return [g for g in groups if len(g) > 1]

    def delete(self, entry_id: str) -> bool:
        """删除条目。返回是否删除成功。"""
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.id != entry_id]
            if len(self._entries) != before:
                self._save_locked()
                return True
        return False

    def touch_reference(self, entry_id: str, *, now: float | None = None) -> bool:
        """标记条目“被再次提及/确认”：刷新 last_referenced_at 且 ref_count+1。

        这是衰减依据的一半：`decay()` 的时间基准取
        `max(updated_at, last_referenced_at, created_at)`，旧实现没人写这个字段，
        导致"被引用就续命"只是文档里的说法（2026-11-15 核对发现）。
        接线点：提炼裁决为 `noop`（新消息里又说到同一事实，无需改写）时调用。
        不碰内容与置信度；失效条目返回 False。@author aceFelix
        """
        ts = time.time() if now is None else float(now)
        with self._lock:
            for e in self._entries:
                if e.id == entry_id and not e.invalidated_at:
                    e.last_referenced_at = ts
                    e.ref_count += 1
                    self._save_locked()
                    return True
        return False

    def invalidate(
        self, entry_id: str, *, superseded_by: str = "", now: float | None = None
    ) -> bool:
        """软删除：给条目打失效标记，保留历史（P2b）。

        与 delete 的区别：失效条目不再出现在 entries()/注入/召回里，但留在磁盘上，
        用 `superseded_by` 能追到"被哪条新事实取代"——update 不再抹掉旧值。
        已失效的条目不重复打标（幂等）。返回是否本次真的标了。
        @author aceFelix
        """
        ts = time.time() if now is None else float(now)
        with self._lock:
            for e in self._entries:
                if e.id == entry_id and not e.invalidated_at:
                    e.invalidated_at = ts
                    e.superseded_by = str(superseded_by or "")
                    self._save_locked()
                    return True
        return False

    def clear(self) -> int:
        """清空全部条目（/memory clear）。返回清除数量。"""
        with self._lock:
            n = len(self._entries)
            self._entries = []
            self._save_locked()
            return n

    def prune_over_limit(self, max_entries: int) -> int:
        """超出上限时淘汰低价值条目。返回淘汰数量。

        价值 = confidence 优先，同分看新近度（updated_at）。
        P2b：失效条目最先淘汰（它们是历史，保留价值低于有效条目）。
        """
        with self._lock:
            if len(self._entries) <= max_entries:
                return 0
            ranked = sorted(
                self._entries,
                # not invalidated_at：有效条目 True 排前面，失效条目先被淘汰
                key=lambda e: (not e.invalidated_at, e.confidence, e.updated_at),
                reverse=True,
            )
            keep, drop = ranked[:max_entries], ranked[max_entries:]
            self._entries = keep
            self._save_locked()
            return len(drop)

    def decay(
        self,
        *,
        half_life_days: float = 30.0,
        floor: float = 0.15,
        now: float | None = None,
    ) -> int:
        """维护管线：按**生命周期分流**决定遗忘方式，返回清除的条目数。

        管家的"睡眠整理记忆"：长期没被再次提及的习惯偏好逐渐淡忘，
        避免画像库被过时信息塞满。

        规则（P2a 分流）:
        - 手动添加条目（source_session="manual"）不衰减——用户亲写，最高信任
        - `permanent`（身份/关系/抱负）不衰减：5 年后仍成立，只靠显式 update 替换
        - `episodic` 且有 `expire_at` 且已到期 → 直接出清（临时事项）
        - 其余（stateful / episodic 无到期时间）：每过一个半衰期 confidence 减半，
          低于 floor 删除；时间基准 = max(updated_at, last_referenced_at, created_at)

        Args:
            now: 时间戳（仅测试注入；生产由每日维护任务不传）。

        挂在 ProactiveEngine 每日维护任务（凌晨静默执行）。
        """
        now = time.time() if now is None else float(now)
        removed = 0
        with self._lock:
            kept: list[ProfileEntry] = []
            for e in self._entries:
                if e.source_session == "manual":
                    kept.append(e)
                    continue
                # P2b：失效条目是历史证据，不参与衰减（也不因 floor 被清）
                if e.invalidated_at:
                    kept.append(e)
                    continue
                # P2a：permanent 不进半衰期（身份/关系/抱负不该靠反复提及续命）
                if e.lifecycle == "permanent":
                    kept.append(e)
                    continue
                # P2a：episodic 有到期时间且已过期 → 直接出清
                if e.lifecycle == "episodic" and e.expire_at and now >= e.expire_at:
                    removed += 1
                    continue
                basis = max(e.updated_at, e.last_referenced_at, e.created_at)
                age_days = max(0.0, (now - basis) / 86400.0)
                e.confidence *= 0.5 ** (age_days / half_life_days)
                if e.confidence < floor:
                    removed += 1
                else:
                    kept.append(e)
            if removed:
                self._entries = kept
                self._save_locked()
        return removed

    # ---- 注入 ----

    def render_for_prompt(
        self,
        token_limit: int = 300,
        *,
        prefer_topics: Sequence[str] = (),
        mode: str = "pseudo",
    ) -> str:
        """渲染注入 system prompt 的画像块（P1c：主题优先 + 两轮填充 + 预算口径）。

        具体排序与预算裁剪在 `profile_inject.render_for_prompt`，本方法只是把
        存储快照喂进去——渲染规则改动不再动存储层。

        Args:
            token_limit: 预算上限（按 `mode` 口径计量）。
            prefer_topics: 优先注入的类别；空＝纯按置信度×新近度。
            mode: `pseudo`（默认，len/4 兼容现状）/ `weighted`（中英分权真 token）。

        Returns:
            注入文本；无条目或预算装不下任何一条时返回空字符串。

        @author aceFelix
        """
        # 局部导入：profile_inject 依赖本模块的 ProfileEntry，模块级导入会成环
        from agent.core.memory.profile_inject import render_for_prompt as _render

        return _render(
            self.entries(),
            token_limit=token_limit,
            prefer_topics=prefer_topics,
            mode=mode,
        )

    # ---- 持久化 ----

    def _load(self) -> None:
        """启动时读取 profile.json（不存在/损坏时从空开始）。"""
        if not self._path.exists():
            self._entries = []
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            raw = data.get("entries", [])
            self._entries = [self._entry_from_dict(d) for d in raw if isinstance(d, dict)]
        except Exception:
            self._entries = []

    @staticmethod
    def _entry_from_dict(d: dict) -> ProfileEntry:
        category = d.get("category", "other")
        try:
            expire_at = max(0.0, float(d.get("expire_at", 0.0) or 0.0))
        except (TypeError, ValueError):
            expire_at = 0.0
        try:
            invalidated_at = max(0.0, float(d.get("invalidated_at", 0.0) or 0.0))
        except (TypeError, ValueError):
            invalidated_at = 0.0
        return ProfileEntry(
            id=str(d.get("id", "")),
            category=category,
            content=str(d.get("content", "")),
            aliases=normalize_aliases(d.get("aliases") or []),
            # P2a：老库（version<=2）没有该字段 → 按类别派生，不写盘迁移
            lifecycle=_normalize_lifecycle(d.get("lifecycle"), category),
            expire_at=expire_at,
            confidence=float(d.get("confidence", 0.5)),
            source_session=str(d.get("source_session", "")),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
            last_referenced_at=float(d.get("last_referenced_at", 0.0)),
            ref_count=int(d.get("ref_count", 0)),
            invalidated_at=invalidated_at,
            superseded_by=str(d.get("superseded_by", "") or ""),
        )

    def _save_locked(self) -> None:
        """原子写盘（调用方须已持锁）。失败静默——画像丢了可再提炼，不能影响主流程。

        version 3（P2a）：新增 lifecycle / expire_at 两个字段；读取对 1/2 向下兼容。
        """
        try:
            data = {"version": 3, "entries": [asdict(e) for e in self._entries]}
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except Exception:
            pass
