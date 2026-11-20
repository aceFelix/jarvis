"""常驻人设卡（P6）：把全量画像条目蒸馏成一张 600–800 tokens 的“他是谁”。

## 为什么

画像条目回答“有哪些事实”，人设卡回答“他是一个什么样的人”。前者按需检索，
后者每次会话常驻，解决“按需检索只能让助手这一分钟才想起我”的问题。
调研见 `docs/research/always-on-profile-memory.md`，方案见
`docs/plans/profile-memory/profile-memory-p6-persona-impl.md`。

## 铁律

1. 生成只在后台线程，绝不进对话路径；
2. 只在**正常退出 / 异常退出后的启动补偿 / 手动**时重建，新建/切会话不重建；
3. 每条 bullet 必须带 `sources`（条目 id），查不到来源即丢弃（防幻觉固化）；
4. 失败保留旧卡，绝不阻断对话；无卡时调用方回退到 P1c 核心注入。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.core.diag import diag_warn
from agent.core.memory.profile_inject import _cost  # 同包预算口径（pseudo / weighted）
from agent.core.memory.profile_json import parse_json_object
from agent.core.memory.profile_store import (
    CATEGORY_LABELS,
    PROFILE_CATEGORIES,
    ProfileEntry,
    ProfileStore,
    profile_store_path,
)
from agent.utils.filelock import ensure_dir, file_lock

_COMPONENT = "memory.profile_persona"

#: 失败后的最小重试间隔（秒）：输入指纹未变就不重复尝试，避免每次退出
#: 都白等 15 秒并重复告警。
#:
#: 为什么用退避而不是「最少 N 条」硬门槛：1 条画像也可能产出 identity，
#: 硬门槛会把「数据已够」的 3—4 条场景一并挡掉；真正的痛点是幂等失败被
#: 反复重试，退避正对这个点（2026-11-19 实测：全新安装只有 1 条画像）。
#: @author aceFelix
RETRY_INTERVAL_S = 600.0

PERSONA_FILE = "persona.json"
STATE_FILE = "persona_state.json"

#: 送进 LLM 的条目上限（成本闸；库再大也只喂这么多）
MAX_INPUT_ENTRIES = 240
#: 每条 bullet 的字数上限
MAX_BULLET_CHARS = 100
#: 每节最多 bullet 数
MAX_BULLETS_PER_SECTION = 3
#: L1 最近变化条数上限（注入侧共用）
DYNAMIC_MAX = 5

#: 固定 section：key → 中文标题；顺序固定（缓存稳定），裁剪顺序见 `_TRIM_ORDER`
SECTION_TITLES = {
    "identity": "他是谁",
    "communication": "怎么跟他说话",
    "work_style": "工作方式",
    "boundaries": "雷区",
    "rhythm": "生活节奏",
    "relationships": "在意的人",
    "current_focus": "当前重点",
}
_SECTION_ORDER = list(SECTION_TITLES)
#: 超预算时的裁剪顺序（先裁易变/低价值的，identity/communication 最后）
_TRIM_ORDER = [
    "current_focus",
    "relationships",
    "rhythm",
    "boundaries",
    "work_style",
    "communication",
    "identity",
]

_in_flight = threading.Lock()


@dataclass
class PersonaBullet:
    """一条人设判断，必须能追溯到原始条目。"""

    text: str
    sources: list[str] = field(default_factory=list)


@dataclass
class PersonaSection:
    key: str
    title: str
    bullets: list[PersonaBullet] = field(default_factory=list)


@dataclass
class Persona:
    """一张人设卡（磁盘格式见 `persona.json`）。"""

    version: int = 1
    built_at: float = 0.0
    source_count: int = 0
    source_max_updated_at: float = 0.0
    token_limit: int = 800
    token_count: int = 0
    sections: list[PersonaSection] = field(default_factory=list)
    dropped_bullets: int = 0


# ─────────────────────────────────────────────────────────────
# 路径与读写
# ─────────────────────────────────────────────────────────────

def memory_dir() -> Path:
    """画像目录：与 profile.json 同目录。"""
    d = profile_store_path().parent
    ensure_dir(d)
    return d


def persona_path() -> Path:
    return memory_dir() / PERSONA_FILE


def state_path() -> Path:
    return memory_dir() / STATE_FILE


def _lock_path() -> Path:
    return memory_dir() / ".persona.lock"


def _read_json(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    """原子写：临时文件 + 文件锁 + os.replace。"""
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with file_lock(_lock_path()):
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)


def load_state() -> dict:
    """读 pending 状态；坏文件当未 pending。"""
    data = _read_json(state_path())
    return {
        "pending": bool(data.get("pending", False)),
        "changed_at": float(data.get("changed_at", 0.0) or 0.0),
        "last_attempt_at": float(data.get("last_attempt_at", 0.0) or 0.0),
    }


def mark_pending() -> None:
    """提炼写入条目后调用：标记“人设卡需要重建”。失败静默。"""
    try:
        state = load_state()
        state["pending"] = True
        state["changed_at"] = time.time()
        _write_json(state_path(), state)
    except Exception as e:  # noqa: BLE001 - 旁路状态，失败不阻断提炼
        diag_warn(_COMPONENT, f"写 pending 失败: {type(e).__name__}: {e}")


def _clear_pending() -> None:
    try:
        state = load_state()
        state["pending"] = False
        state["last_attempt_at"] = time.time()
        _write_json(state_path(), state)
    except Exception:
        pass


def discard() -> bool:
    """丢弃人设卡与 pending 状态（`/memory clear` 专用），返回是否成功。

    为什么必须显式丢弃：人设卡是独立文件，注入层只认它是否存在。清空画像后
    若不删，仍会注入旧卡；而空库会让 `build_persona` 返回 None、启动补偿与
    手动重建都无法覆盖它——旧卡将永久残留。

    重建线程正在跑时不删（删了也会被 worker 写回），返回 False 由调用方如实
    提示；任何 IO 失败也返回 False——清空画像本身不能被旁路数据拖挂。

    @author aceFelix
    """
    if not _in_flight.acquire(blocking=False):
        return False
    ok = True
    try:
        for path in (persona_path(), state_path()):
            try:
                with file_lock(_lock_path()):
                    path.unlink(missing_ok=True)
            except Exception as e:  # noqa: BLE001 - 单文件失败仍尝试另一个
                ok = False
                diag_warn(_COMPONENT, f"丢弃人设卡失败: {type(e).__name__}: {e}")
    finally:
        _in_flight.release()
    if ok:
        # 让当前进程下次组装提示词立刻不再注入旧卡（调用方随之再 reload 也无害）
        try:
            from agent.prompts.system import reload_profile_cache

            reload_profile_cache()
        except Exception:
            pass
    return ok


def load_persona() -> Persona | None:
    """读人设卡；坏文件/版本不符当无卡。"""
    data = _read_json(persona_path())
    if not data or int(data.get("version", 0) or 0) != 1:
        return None
    sections: list[PersonaSection] = []
    for raw in data.get("sections") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key", "") or "")
        if key not in SECTION_TITLES:
            continue
        bullets: list[PersonaBullet] = []
        for b in raw.get("bullets") or []:
            if not isinstance(b, dict):
                continue
            text = str(b.get("text", "") or "").strip()
            sources = [str(s) for s in b.get("sources") or [] if str(s).strip()]
            if text:
                bullets.append(PersonaBullet(text=text, sources=sources))
        if bullets:
            sections.append(PersonaSection(key=key, title=SECTION_TITLES[key], bullets=bullets))
    if not sections:
        return None
    sections.sort(key=lambda s: _SECTION_ORDER.index(s.key))
    return Persona(
        version=1,
        built_at=float(data.get("built_at", 0.0) or 0.0),
        source_count=int(data.get("source_count", 0) or 0),
        source_max_updated_at=float(data.get("source_max_updated_at", 0.0) or 0.0),
        token_limit=int(data.get("token_limit", 800) or 800),
        token_count=int(data.get("token_count", 0) or 0),
        sections=sections,
        dropped_bullets=int(data.get("dropped_bullets", 0) or 0),
    )


def render_persona(persona: Persona | None) -> str:
    """渲染成注入块；无卡返回空串。"""
    if persona is None or not persona.sections:
        return ""
    lines = ["# 用户画像（常驻人设）"]
    for section in persona.sections:
        if not section.bullets:
            continue
        lines.append(f"## {section.title}")
        for bullet in section.bullets:
            lines.append(f"- {bullet.text}")
    lines.append("（画像为自动观察；若与长期记忆冲突，以长期记忆为准。）")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 生成
# ─────────────────────────────────────────────────────────────

def _build_refine_provider(settings: Any):
    """延迟导入 refiner 的 provider 构建（避免模块循环依赖）。"""
    from agent.core.memory.profile_refiner import _build_refine_provider as build

    return build(settings)


def _call_llm_sync(provider, model: str, prompt: str, *, max_tokens: int = 1500) -> str:
    from agent.core.memory.profile_refiner import _call_llm_sync as call

    return call(provider, model, prompt, max_tokens=max_tokens)


def _sample_entries(store: ProfileStore, limit: int = MAX_INPUT_ENTRIES) -> list[ProfileEntry]:
    """分层采样：全部 permanent + 每类 top 15 + 最近 50 条变更，去重截断。"""
    active = store.entries()
    if not active:
        return []
    weighted = store.weighted_entries()
    per_cat: dict[str, list[ProfileEntry]] = {}
    for _, entry in weighted:
        per_cat.setdefault(entry.category, []).append(entry)
    permanent = [e for e in active if e.lifecycle == "permanent"]
    top: list[ProfileEntry] = []
    for category in PROFILE_CATEGORIES:
        top.extend(per_cat.get(category, [])[:15])
    recent = sorted(active, key=lambda e: e.updated_at, reverse=True)[:50]
    out: list[ProfileEntry] = []
    seen: set[str] = set()
    for entry in permanent + top + recent:
        if entry.id in seen:
            continue
        seen.add(entry.id)
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def build_persona_prompt(entries: list[ProfileEntry], previous: Persona | None) -> str:
    """构造人设卡生成 prompt（只喂采样后的条目）。"""
    lines = []
    for e in entries:
        label = CATEGORY_LABELS.get(e.category, e.category)
        lines.append(
            f"[{e.id}] [{label}] {e.content}"
            f"（置信度 {e.confidence:.2f}，提及 {e.ref_count} 次）"
        )
    prev_text = render_persona(previous).strip() if previous else ""
    prev_block = f"\n## 上一版人设卡（仅供参考，冲突时以条目为准）\n{prev_text}\n" if prev_text else ""
    keys = " / ".join(SECTION_TITLES)
    return f"""你是用户画像的总结器。请根据下面的“条目清单”，写一张关于这个人的人设卡。

规则：
1. **只能使用清单里明确存在的事实**，禁止推断、补全或写清单外的信息；
2. 每条 bullet 必须带 `sources`（至少一个条目 id，取自方括号里的 ent_xxx）；
3. section key 只能取：{keys}；没有内容的 section 省略；每节 1–{MAX_BULLETS_PER_SECTION} 条；
4. 每条 bullet ≤{MAX_BULLET_CHARS} 字，第三人称中文，写“他是一个什么样的人”，不写流水账；
5. 不写项目技术细节（框架/端口/目录/命令）——那属于长期记忆 MEMORY.md；
6. 不要重复同一条事实。

## 条目清单
{chr(10).join(lines)}
{prev_block}
## 输出（严格 JSON，不要输出其他内容）
{{"sections": [{{"key": "identity", "bullets": [{{"text": "...", "sources": ["ent_xxx"]}}]}}]}}"""


def parse_persona_output(
    data: dict,
    entries: list[ProfileEntry],
    settings: Any,
    *,
    previous: Persona | None = None,
) -> Persona | None:
    """把模型输出校验/裁剪成 Persona；任何结构性错误返回 None（保留旧卡）。"""
    if not isinstance(data, dict):
        diag_warn(_COMPONENT, "模型输出不是 JSON 对象")
        return None
    valid_ids = {e.id for e in entries}
    by_key: dict[str, list[PersonaBullet]] = {}
    dropped = 0
    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list):
        diag_warn(_COMPONENT, "模型输出缺少 sections 列表")
        return None
    for raw in raw_sections:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        key = str(raw.get("key", "") or "").strip()
        if key not in SECTION_TITLES:
            dropped += 1
            continue
        bullets: list[PersonaBullet] = []
        seen_text: set[str] = set()
        for item in raw.get("bullets") or []:
            text = str(item.get("text", "") if isinstance(item, dict) else item).strip()
            if not text or len(text) > MAX_BULLET_CHARS:
                dropped += 1
                continue
            sources = []
            if isinstance(item, dict):
                sources = [
                    str(s) for s in (item.get("sources") or [])
                    if str(s) in valid_ids
                ]
            if not sources:
                dropped += 1
                continue
            normalized = text.strip("。！？.!? ")
            if normalized in seen_text:
                dropped += 1
                continue
            seen_text.add(normalized)
            bullets.append(PersonaBullet(text=text, sources=sources))
            if len(bullets) >= MAX_BULLETS_PER_SECTION:
                break
        if bullets:
            by_key.setdefault(key, []).extend(bullets)

    if not by_key.get("identity"):
        # 不写原因的话，上层只能报一句「构建失败」，用户无从行动（用户原话：
        # 「起码要告诉我是啥原因吧」）。丢弃数能区分「模型跑偏」与「数据太少」。
        # @author aceFelix
        diag_warn(
            _COMPONENT,
            f"模型输出缺 identity 段（有效 id {len(valid_ids)} 个，丢弃 {dropped} 条）",
        )
        return None

    sections = [
        PersonaSection(key=key, title=SECTION_TITLES[key], bullets=by_key[key][:MAX_BULLETS_PER_SECTION])
        for key in _SECTION_ORDER if by_key.get(key)
    ]
    persona = Persona(
        version=1,
        built_at=time.time(),
        source_count=len(entries),
        source_max_updated_at=max(e.updated_at for e in entries),
        token_limit=int(getattr(settings, "profile_persona_token_limit", 800) or 800),
        sections=sections,
        dropped_bullets=dropped,
    )
    persona.token_count = _cost(render_persona(persona), _budget_mode(settings))
    _trim_to_limit(persona, settings)
    if persona.token_count > persona.token_limit:
        diag_warn(
            _COMPONENT,
            f"人设卡 token 超限（{persona.token_count} > {persona.token_limit}），放弃本次结果",
        )
        return None
    return persona


def _budget_mode(settings: Any) -> str:
    mode = str(getattr(settings, "profile_inject_token_budget_mode", "pseudo") or "pseudo")
    return mode if mode in ("pseudo", "weighted") else "pseudo"


def _trim_to_limit(persona: Persona, settings: Any) -> None:
    """按 `_TRIM_ORDER` 从末尾删 bullet，直到 token ≤ 上限。"""
    mode = _budget_mode(settings)
    for key in _TRIM_ORDER:
        section = next((s for s in persona.sections if s.key == key), None)
        if section is None:
            continue
        while section.bullets and persona.token_count > persona.token_limit:
            section.bullets.pop()
            if not section.bullets and section in persona.sections:
                persona.sections.remove(section)
            persona.token_count = _cost(render_persona(persona), mode)
    persona.sections = [s for s in persona.sections if s.bullets]


def build_persona(
    settings: Any,
    store: ProfileStore | None = None,
    previous: Persona | None = None,
    *,
    provider=None,
) -> Persona | None:
    """同步生成一张人设卡；失败返回 None（调用方保留旧卡）。"""
    store = store or ProfileStore()
    entries = _sample_entries(store)
    if not entries:
        diag_warn(_COMPONENT, "没有可用的画像条目，跳过人设卡生成")
        return None
    provider = provider or _build_refine_provider(settings)
    if provider is None:
        diag_warn(_COMPONENT, "精炼模型不可用（provider=None），无法生成人设卡")
        return None
    model = getattr(settings, "profile_refine_model", "") or getattr(settings, "model", "")
    try:
        raw = _call_llm_sync(
            provider, model, build_persona_prompt(entries, previous), max_tokens=1200
        )
        persona = parse_persona_output(
            parse_json_object(raw), entries, settings, previous=previous
        )
        if persona is not None:
            # 指纹必须对应**全库**（不是采样集），否则库大时永远判定“过期”。
            active = store.entries()
            persona.source_count = len(active)
            persona.source_max_updated_at = max(
                (e.updated_at for e in active), default=0.0
            )
        return persona
    finally:
        try:
            import asyncio

            asyncio.run(provider.close())
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# 触发（退出 / 异常补偿 / 手动）
# ─────────────────────────────────────────────────────────────

def _fingerprint(store: ProfileStore) -> tuple[int, float]:
    entries = store.entries()
    return (len(entries), max((e.updated_at for e in entries), default=0.0))


def _needs_build(store: ProfileStore, persona: Persona | None) -> bool:
    if not store.entries():
        return False
    if persona is None:
        return True
    return _fingerprint(store) != (persona.source_count, persona.source_max_updated_at)


def _worker(settings: Any, force: bool) -> None:
    try:
        store = ProfileStore()
        persona = load_persona()
        if not force and not _needs_build(store, persona):
            _clear_pending()
            return
        new = build_persona(settings, store=store, previous=persona)
        if new is None:
            # 具体原因已由 build_persona / parse_persona_output 写到上一条日志；
            # 这里只补状态与「有卡/无卡」的差异，不再说假话。@author aceFelix
            state = load_state()
            state["last_attempt_at"] = time.time()
            state["last_fingerprint"] = list(_fingerprint(store))
            _write_json(state_path(), state)
            if persona is not None:
                diag_warn(_COMPONENT, "人设卡未更新（原因见上一条日志），保留旧卡")
            else:
                diag_warn(
                    _COMPONENT,
                    "本次未生成人设卡（原因见上一条日志）；"
                    "画像有新增或稍后会自动重试",
                )
            return
        _write_json(persona_path(), {
            "version": new.version,
            "built_at": new.built_at,
            "source_count": new.source_count,
            "source_max_updated_at": new.source_max_updated_at,
            "token_limit": new.token_limit,
            "token_count": new.token_count,
            "dropped_bullets": new.dropped_bullets,
            "sections": [asdict(s) for s in new.sections],
        })
        _clear_pending()
        # 让下一次组装 system prompt 用上新卡（已组装的缓存由下次 reload 生效）
        try:
            from agent.prompts.system import reload_profile_cache

            reload_profile_cache()
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001 - 后台线程不能因单次失败挂掉
        diag_warn(_COMPONENT, f"人设卡重建失败: {type(e).__name__}: {e}")
    finally:
        _in_flight.release()


def maybe_rebuild_async(
    settings: Any,
    *,
    force: bool = False,
    wait_seconds: float = 0.0,
) -> bool:
    """按需异步重建人设卡；返回是否启动线程。

    触发点由调用方决定（正常退出 / 启动补偿 / 手动）；本函数只做
    “是否已有卡、指纹是否变化、是否已有线程”的闸门。
    """
    if not getattr(settings, "profile_enabled", False):
        return False
    if not getattr(settings, "profile_persona_enabled", True):
        return False
    try:
        store = ProfileStore()
        entries = store.entries()
        if not entries:
            return False
        # 数据不足不是故障，也不靠硬门槛拦截：直接尝试，失败有退避兜底。
        state = load_state()
        persona = load_persona()
        # 失败退避：输入指纹未变且在退避窗口内，不重复尝试（幂等失败不刷屏）。
        # @author aceFelix
        if not force and persona is None:
            last = float(state.get("last_attempt_at", 0.0) or 0.0)
            same_input = tuple(state.get("last_fingerprint") or ()) == _fingerprint(store)
            if same_input and last and (time.time() - last) < RETRY_INTERVAL_S:
                return False
        if not force and not _needs_build(store, persona):
            return False
        if not _in_flight.acquire(blocking=False):
            return False
    except Exception:
        return False

    thread = threading.Thread(target=_worker, args=(settings, force), name="persona-builder", daemon=True)
    thread.start()
    if wait_seconds > 0:
        thread.join(timeout=float(wait_seconds))
    return True


def startup_check_async(settings: Any) -> bool:
    """进程启动补偿：只有 pending 或无卡且有条目时才建，正常启动不重建。"""
    try:
        state = load_state()
        persona = load_persona()
        store = ProfileStore()
        if not store.entries():
            return False
        if not state.get("pending") and persona is not None:
            return False
        # pending 但指纹没变（例如上次失败后没新条目）：清掉 pending，不重建
        if state.get("pending") and not _needs_build(store, persona):
            _clear_pending()
            return False
    except Exception:
        return False
    return maybe_rebuild_async(settings, force=False)


def status(settings: Any | None = None) -> dict:
    """给 /memory 展示的状态摘要。"""
    persona = load_persona()
    state = load_state()
    return {
        "enabled": bool(getattr(settings, "profile_persona_enabled", True)) if settings else True,
        "exists": persona is not None,
        "built_at": persona.built_at if persona else 0.0,
        "source_count": persona.source_count if persona else 0,
        "token_count": persona.token_count if persona else 0,
        "pending": bool(state.get("pending")),
    }
