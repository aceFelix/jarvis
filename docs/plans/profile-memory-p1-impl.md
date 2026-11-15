# 实现方案：画像记忆 P1（两段式裁决 + 四操作 + 别名）

> 状态：**P1a/P1b/P1c/P1d 已全部落地（2026-11-15）**　作者：aceFelix　日期：2026-11-15
> 上位文档：[roadmap/PLAN-profile-memory-upgrade.md](../roadmap/PLAN-profile-memory-upgrade.md) §P1
> 本文只写落点与契约；设计与成本账在 PLAN，不在本文重复。

## 一、范围

| 切片 | 内容 | 本次 |
|---|---|---|
| **P1a** | 两段式：① 抽取候选（不带旧画像）→ ② 逐条召回 → ③ 四操作裁决 | ✅ |
| **P1b** | 别名：`aliases` 字段、生成规则、并集合并、召回打分接入 | ✅ |
| **P1c** | 注入侧改造：`prefer_topics` + 两轮填充 + 预算口径切换 | ✅ |
| **P1d** | 抽取硬闸遥测在 `/memory` 展示、评测集扩量 | ✅ |

**不做**：P2 lifecycle/事件轨、P3 向量、P5 RecallProfile。

## 二、现状事实

| 事实 | 位置 |
|---|---|
| 单段提炼：一次调用同时"抽 + 判" | `profile_refiner.refine_session`（prompt 含 `已有画像` 前 80 条） |
| 写入只有 new / updates 两个出口 | 同上，第 352–386 行 |
| 存储层字面查重 | `profile_store.merge_or_add`（归一化/bigram ≥0.85） |
| 注入按置信度裁剪 | `profile_store.render_for_prompt` |
| `/memory refine` 已走 force 增量 | `session_commands.py` |

**单段的结构性问题**：
1. 抽取时把"已有画像"塞进同一个 prompt，候选与裁决互相污染；
2. 没有 NOOP 出口，模型拿不准只能写 new；
3. 只展示全库前 80 条，候选真正的"近邻"可能不在其中；
4. 无别名，换措辞的事实检索不到。

## 三、数据模型

### 3.1 `ProfileEntry.aliases`

```python
aliases: list[str] = field(default_factory=list)   # 2–5 个、每个 2–6 字
```

- `profile.json` 版本 **1 → 2**；`_load` 对 version=1 的旧文件照常读（缺字段按 `[]`）。
- `merge_or_add` / `dedup_all` 合并时**取并集**，上限 **8**；超出按"先出现者"截断。
- 归一化：去空白、去重、丢弃 >6 字的项；非法/空列表视为无别名。

### 3.2 内部候选/决策结构（`profile_resolve.py`）

```python
@dataclass
class Candidate:
    content: str
    category: str
    confidence: float
    recalls: list[ProfileEntry] = field(default_factory=list)

@dataclass
class Decision:
    op: str                         # add / update / delete / noop
    content: str = ""
    category: str = "other"
    confidence: float = 0.5
    aliases: list[str] = field(default_factory=list)
    target_id: str = ""
    reason: str = ""
```

## 四、接口契约

### 4.1 `ProfileStore`

```python
def recall_similar(self, content: str, category: str = "", top_k: int = 5) -> list[ProfileEntry]:
    """按内容相似度召回 top-K 旧条目（写入侧裁决用）。

    打分：max(bigram(content, e.content), alias 命中加成) + 同类别 +0.15；
    alias 命中：候选文本包含某个别名，或别名包含候选（短串）+0.30。
    只返回 score > 0 的条目，按分数降序。
    """
```

### 4.2 `profile_resolve`

```python
def extract_candidates(provider, model: str, dialog_text: str, settings) -> list[Candidate]:
    """第①段：只抽取候选（prompt 不含旧画像）。max_tokens=800。"""

def attach_recalls(candidates, store, top_k: int) -> None:
    """第②段（纯本地）：为每个候选填 recalls。"""

def resolve_decisions(provider, model: str, candidates) -> list[Decision]:
    """第③段：四操作裁决。max_tokens=1200。"""

def validate_candidate(raw: dict) -> Candidate | None:
    """硬闸：content 非空且 ≤40 字；category 白名单；confidence clamp 0.3~0.95。"""

def apply_decisions(decisions, store, session_id: str, report) -> None:
    """落盘：add→merge_or_add；update→delete(target)+merge_or_add；delete→store.delete；noop 跳过。
    target_id 不存在时：update/noop 降级为 add（仍走查重闸），delete 忽略。"""
```

### 4.3 `refine_session` 新流程（仍是同步函数，跑在后台线程）

```
dialog_text 为空 → skipped/advanceable
provider 无 → skipped/不可推进
① extract_candidates(dialog_text)        ← 1 次 LLM
   过滤：≤40 字、类别白名单、非空；被拒计入 report.rejected
   候选为空 → 本轮结束（可推进）
② attach_recalls(store.recall_similar)   ← 纯本地
③ resolve_decisions(candidates)          ← 1 次 LLM
④ apply_decisions(...)                   ← 本地写入
store.dedup_all() + prune_over_limit()
report.advanceable = True
```

## 五、Prompt 契约

### 5.1 抽取段（`_build_extract_prompt`）

保留"画像是什么 / 不是什么 / 判断口径 / 类别表"，追加：

```
输出：{"candidates":[{"content":"…","category":"…","confidence":0.8}]}
- 只抽候选，不要判断是否重复（下一步会做）
- 一条只装一个事实，≤40 字
```

### 5.2 裁决段（`_build_resolve_prompt`）

```
每条候选下列出它的召回结果：
候选 1: 习惯深夜工作 [schedule]
  已有: [ent_ab] schedule: 作息很晚（相似 0.82）

输出：{"facts":[
  {"op":"add","content":"…","category":"…","confidence":0.8,"aliases":["熬夜","晚睡"]},
  {"op":"update","target_id":"ent_x","content":"…","aliases":["…"]},
  {"op":"delete","target_id":"ent_x","reason":"已被新事实取代/一次性项到期"},
  {"op":"noop","target_id":"ent_x","confidence":0.7}]}

规则：
- 命中已有 → update 或 noop；禁止再 add
- 完全新 → add
- 拿不准 → noop（宁可少记）
- add/update 必须给 2–5 个别名（2–6 字，覆盖用户会怎么问它，不重复原词）
```

## 六、别名规则

| 项 | 规则 |
|---|---|
| 数量/长度 | 2–5 个，每个 2–6 字 |
| 内容 | 同义词、口语说法、缩写/全称；专有名词保留原文 |
| 禁止 | 与 content 原词重复；编造与事实矛盾的说法 |
| 存储 | `aliases` 字段；合并取并集 ≤8 |
| 关闭 | `profile_aliases_enabled=false` → prompt 不带该字段，召回只用 bigram |

## 七、配置（P1a/P1b 新增 2 键，205 → 207；P1c 再增 2 键 → 209）

| 键 | 默认 | 说明 |
|---|---|---|
| `memory.profile_recall_top_k` | 5 | 每条候选召回的旧条目数 |
| `memory.profile_aliases_enabled` | true | 是否生成/使用别名 |
| `memory.profile_inject_prefer_topics` | `[]` | P1c：优先注入的类别（空＝旧排序） |
| `memory.profile_inject_token_budget_mode` | `pseudo` | P1c：预算口径（`pseudo`/`weighted`） |

不加：`profile_alias_max`（硬编码 5/8）、`profile_recall_alias_weight`（常数 0.3）。
`weighted` 口径下 `profile_inject_token_limit` 应同步调成多少，需 P4 实测后定，
不凭估算改（所以此处只给模式开关，不给另一套默认值）。

## 八、代码落点

| 文件 | 改动 | 预估 |
|---|---|---|
| `agent/core/memory/profile_resolve.py`（新） | 候选/决策结构、两段 prompt、校验与落盘 | 约 260 |
| `agent/core/memory/profile_refiner.py`（509 行） | `refine_session` 改为编排；旧 `_build_prompt` 拆成两段 | 约 80 行改动 |
| `agent/core/memory/profile_store.py`（约 465 行） | `aliases` 字段、`recall_similar`、并集合并、version 2 | 约 60 |
| `agent/commands/handlers/session_commands.py` | `/memory` 列表展示别名（可选） | +5 |
| `tests/core/test_profile_resolve.py`（新） | 抽取校验 / 四操作分派 / 降级 | 约 220 |
| `tests/core/test_profile_memory.py` | 别名合并、version 兼容、recall_similar | +60 |
| 文档 | 09 架构图、CHANGELOG、PLAN 的 P1 状态 | — |

## 九、测试矩阵

| # | 用例 | 断言 |
|---|---|---|
| 1 | 抽取段成功 | `extract_candidates` 只调 1 次 LLM，输出候选数正确 |
| 2 | 抽取段 prompt 不含旧画像 | 传入库有条目，prompt 里不出现 `[ent_` |
| 3 | 超长候选 | >40 字被拒，`report.rejected +1`，不入库 |
| 4 | 非法类别 | 落 `other` |
| 5 | 候选为空 | 两次调用只发生 1 次（不调裁决），可推进 |
| 6 | add | 新条目入库，aliases 落盘 |
| 7 | update | 旧条目删除、新内容入库、水位推进 |
| 8 | delete | 目标条目消失 |
| 9 | noop | 库不变、不新增 |
| 10 | target_id 幻觉 | update/noop 降级 add 走查重，delete 忽略 |
| 11 | 别名并集 | 同事实再次 add → 别名合并、≤8、去重 |
| 12 | 别名关闭 | prompt 不含 aliases，行为与旧版一致 |
| 13 | recall_similar | 同类别优先、alias 命中加分、top_k 截断 |
| 14 | version 1 兼容 | 旧 JSON 缺 aliases → 读出 `[]`，不报错 |

## 十、验收

```powershell
python -m pytest tests/core/test_profile_resolve.py tests/core/test_profile_memory.py tests/core/test_profile_refine_golden.py -q
python -m compileall -q agent/
```

人工走查：重启 → 说"我平时不太爱出门，喜欢在家打游戏" → 应落 `preference`/`personality`；
再说"我一般在家打游戏，不太出门" → 应走 `noop`/`update`，**不新增重复条目**。

## 十一、风险与回退

| 风险 | 处置 |
|---|---|
| 两次调用成本翻倍 | 提炼在后台；P0 已把整窗重喂干掉，总成本仍低于改造前（见 PLAN 成本对照） |
| 小模型解析失败 | 复用 `_parse_profile_json`；裁决段解析失败 → 本轮不写、不推进（下轮重试） |
| 别名质量差 | `profile_aliases_enabled=false` 一键回退；别名只影响召回，不影响落盘正确性 |
| update 误删 | 只在 `target_id` 真实存在时删除；删除前无备份（与现行为一致），由 `dedup_all` 兜底重复 |
| 版本升级读坏旧库 | version=1 兼容读；写入前不迁移、不备份（字段可选，无破坏性） |

## 十二、提交切分

1. `feat(memory): 提炼改为两段式 + 四操作裁决（含硬闸与降级）`
2. `feat(memory): 画像别名生成与召回打分`
3. `feat(memory): 注入侧主题优先与两轮填充`（P1c，单独一刀）
4. `docs(memory): P1 行为与配置同步`

## 十三、实施记录（2026-11-15，P1c）

| # | 计划 | 实际 | 原因 |
|---|---|---|---|
| 1 | `render_for_prompt` 改造后仍留在 `ProfileStore` | 渲染实现拆到 `profile_inject.py`，`ProfileStore` 保留薄委托 | 存储层不再背“怎么排、怎么裁”；`profile_inject` 对 `ProfileEntry` 只做类型引用（`TYPE_CHECKING`）+ 存储层内局部导入，两个方向都不成环 |
| 2 | — | 空白条目（`content.strip()` 为空）不渲染 | 原先会输出一行空横杠，白占预算 |
| 3 | 同分排序改用三级召回打分 | **未做** | 注入侧没有查询词，拿不到“三级召回”的输入；等 P5 RecallProfile 有 query 时再做。现在保持 `(confidence, updated_at)` |
| 4 | 预算口径默认切 weighted | **默认仍 pseudo** | 与 PLAN §P1 一致：不做静默行为变更，`weighted` 需先定 `token_limit` |

已落地：`profile_inject.py`（渲染实现）、`profile_store.render_for_prompt`（委托 + 新参数）、
`settings.py` / `settings_domains.py`（2 键 + `LIST_FIELDS`）/ 两份 `settings.example.toml`、
`prompts/system.py`（传入 prefer/mode）、`tests/core/test_profile_inject.py`（23 例）。

## 十四、实施记录（2026-11-15，P1d）

| # | 计划 | 实际 | 原因 |
|---|---|---|---|
| 1 | 遥测单独开一个台账字段 | `RefineState.last_report`（dict）+ `REPORT_FIELDS` 白名单 | 七项计数拆成七个字段过于琐碎；dict + 白名单过滤在 `_coerce` 里也能兼容脏数据 |
| 2 | 提炼完单独写一次遥测 | 扩展 `advance(..., report=)`，与水位**同一次锁+写** | 台账是全表原子写；多一次写就多一次锁争用与覆盖风险。`report=None` 时保留旧值 |
| 3 | `/memory` 展示 | 头部一行「上次提炼 mm-dd HH:mm：抽取/新增/…/拒绝」 | 只在非零项出现，不刷屏；库为空也展示（能看出“全被硬闸拦了”） |

已落地：`profile_ledger.py`（`last_report` + `_clean_report` + `advance(report=)`）、
`profile_refiner.py`（worker 写计数）、`session_commands.py`（`_format_last_refine` + 列表头部）、
测试：台账 +4、golden +2、命令 +2。
