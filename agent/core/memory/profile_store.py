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
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.core.memory.compactor import estimate_text_tokens

# 合法画像类别（提炼 prompt 与存储校验共用，与计划文档一致）
PROFILE_CATEGORIES = (
    "identity",      # 身份背景（职业、技术栈、所在城市）
    "preference",    # 偏好（喜欢/不喜欢什么、表达习惯）
    "work_habit",    # 工作习惯（工作流、文件组织方式）
    "schedule",      # 作息（睡觉/起床/会议时间）
    "tool_usage",    # 工具使用（常用软件、模型、编辑器）
    "relationship",  # 联系人（同事、朋友、协作对象）
    "project",       # 项目背景（在做什么项目、用什么技术）
    "other",
)

# 类别中文说明（/memory 展示用）
CATEGORY_LABELS = {
    "identity": "身份背景",
    "preference": "偏好",
    "work_habit": "工作习惯",
    "schedule": "作息",
    "tool_usage": "工具使用",
    "relationship": "联系人",
    "project": "项目背景",
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


@dataclass
class ProfileEntry:
    """一条画像记忆。"""

    id: str
    category: str = "other"
    content: str = ""
    confidence: float = 0.5           # 0~1，提炼时 LLM 给出
    source_session: str = ""          # 溯源：来自哪个会话
    created_at: float = 0.0
    updated_at: float = 0.0
    last_referenced_at: float = 0.0   # M3 维护管线衰减依据
    ref_count: int = 0

    @staticmethod
    def new(
        content: str,
        category: str = "other",
        confidence: float = 0.5,
        source_session: str = "",
    ) -> "ProfileEntry":
        """创建新条目（生成 id 与时间戳）。"""
        now = time.time()
        return ProfileEntry(
            id=f"ent_{uuid.uuid4().hex[:8]}",
            category=category if category in PROFILE_CATEGORIES else "other",
            content=content.strip(),
            confidence=max(0.0, min(1.0, float(confidence))),
            source_session=source_session,
            created_at=now,
            updated_at=now,
        )


class ProfileStore:
    """画像记忆存储。线程安全，磁盘格式 version=1。"""

    def __init__(self, path: Path | None = None):
        self._path = path or profile_store_path()
        self._lock = threading.Lock()
        self._entries: list[ProfileEntry] = []
        self._load()

    # ---- 读取 ----

    def entries(self) -> list[ProfileEntry]:
        """全部条目快照（按 confidence 降序、更新时间新者优先）。"""
        with self._lock:
            return sorted(
                self._entries,
                key=lambda e: (e.confidence, e.updated_at),
                reverse=True,
            )

    def get(self, entry_id: str) -> ProfileEntry | None:
        with self._lock:
            for e in self._entries:
                if e.id == entry_id:
                    return e
        return None

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

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
        - updated_at 刷新、created_at 保留最早
        - source_session：manual 优先保留；否则留先到者
        返回 (存活的条目, 是否发生合并)。提炼管线与手动入口共用本方法，
        这是"同一句话说 N 遍记 N 条"的最后一道闸。@author aceFelix
        """
        with self._lock:
            for e in self._entries:
                if e.id == entry.id:
                    continue
                if profile_content_similarity(e.content, entry.content) >= _SIM_MERGE_THRESHOLD:
                    merged = ProfileEntry(
                        id=e.id,
                        category=e.category,
                        content=e.content,
                        confidence=max(e.confidence, entry.confidence),
                        source_session=(
                            e.source_session if e.source_session == "manual"
                            else (entry.source_session if not e.source_session else e.source_session)
                        ),
                        created_at=min(e.created_at, entry.created_at) or entry.created_at,
                        updated_at=time.time(),
                        last_referenced_at=max(e.last_referenced_at, entry.last_referenced_at),
                        ref_count=e.ref_count + entry.ref_count,
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
        """
        with self._lock:
            groups: list[list[ProfileEntry]] = []
            for e in self._entries:
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
                primary.ref_count = sum(e.ref_count for e in g)
                primary.last_referenced_at = max(e.last_referenced_at for e in g)
                if not primary.source_session:
                    primary.source_session = next(
                        (e.source_session for e in g if e.source_session and e.source_session != "manual"),
                        "",
                    )
                new_entries.append(primary)
                merged_away += len(g) - 1
            if merged_away:
                self._entries = new_entries
                self._save_locked()
            return merged_away

    def duplicate_group_report(self) -> list[list[ProfileEntry]]:
        """只读预演：按 merge_or_add 同款判据分组，返回长度 >1 的重复组。

        供 /memory dedup 预览使用，不写盘、不改内存。组内首个为主条目口径。
        """
        with self._lock:
            groups: list[list[ProfileEntry]] = []
            for e in self._entries:
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
        """
        with self._lock:
            if len(self._entries) <= max_entries:
                return 0
            ranked = sorted(
                self._entries,
                key=lambda e: (e.confidence, e.updated_at),
                reverse=True,
            )
            keep, drop = ranked[:max_entries], ranked[max_entries:]
            self._entries = keep
            self._save_locked()
            return len(drop)

    def decay(self, *, half_life_days: float = 30.0, floor: float = 0.15) -> int:
        """维护管线：按半衰期衰减长期未更新的条目，清除低于 floor 者。

        管家的"睡眠整理记忆"：长期没被再次提及的习惯偏好逐渐淡忘，
        避免画像库被过时信息塞满。返回清除的条目数。

        规则:
        - 时间基准 = max(updated_at, last_referenced_at)（近期更新/引用过的不衰）
        - 每过一个半衰期，confidence 减半
        - 手动添加条目（source_session="manual"）不衰减——用户亲写，最高信任
        - confidence < floor 的条目删除

        挂在 ProactiveEngine 每日维护任务（凌晨静默执行）。
        """
        now = time.time()
        removed = 0
        with self._lock:
            kept: list[ProfileEntry] = []
            for e in self._entries:
                if e.source_session == "manual":
                    kept.append(e)
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

    def render_for_prompt(self, token_limit: int = 300) -> str:
        """渲染注入 system prompt 的画像块（token 硬限额）。

        无条目或首条都放不下时返回空字符串。
        """
        lines: list[str] = []
        used = 0
        for e in self.entries():
            line = f"- {e.content}"
            cost = estimate_text_tokens(line)
            if used + cost > token_limit:
                continue  # 跳过放不下的，继续尝试更短的
            lines.append(line)
            used += cost
        if not lines:
            return ""
        return "# 关于用户（画像记忆）\n" + "\n".join(lines)

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
        return ProfileEntry(
            id=str(d.get("id", "")),
            category=d.get("category", "other"),
            content=str(d.get("content", "")),
            confidence=float(d.get("confidence", 0.5)),
            source_session=str(d.get("source_session", "")),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
            last_referenced_at=float(d.get("last_referenced_at", 0.0)),
            ref_count=int(d.get("ref_count", 0)),
        )

    def _save_locked(self) -> None:
        """原子写盘（调用方须已持锁）。失败静默——画像丢了可再提炼，不能影响主流程。"""
        try:
            data = {"version": 1, "entries": [asdict(e) for e in self._entries]}
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except Exception:
            pass
