# 计划：画像记忆提炼的源头治理（阶段 13 · 从"不重复落盘"到"不重复提炼"）

> 立论数据（2026-11-02 实测盘点）：用户 `~/.jarvis/memory/profile.json` 共 56 条，其中
> `习惯深夜工作`（category=schedule，source_session=`s`）**一模一样出现 6 次**
> （`ent_8572b72b`/`ent_032513c9`/`ent_ba24f47b`/`ent_3a465868`/`ent_8cfa634f`/`ent_da7d92ad`）；
> 另有措辞变体重复（`职业是Agent开发者` / `职业是Agent应用开发工程师`）、
> 抽取脏数据（`在忙的项目有…Jarvis Plu` 单条被 40 字上限硬截断）、
> 生命周期混装（一次性闹铃 `需要早上9点15分被叫醒` 与永久身份事实同库同衰减策略）。
>
> 已落地的止血（本次不计入，作为基线）：存储层确定性查重闸 `ProfileStore.merge_or_add`
> （见 `docs/fixlogs/profile-duplicate-memory-fix.md`）——**重复条目已不会再落盘**，
> 但病根（同一窗口被反复提炼、LLM 白调 N 次）仍在，且语义/生命周期两类病没治。

@author aceFelix

## 一、目标与非目标

**目标（按优先级）**

1. **源头掐重复**：提炼由"600s 轮询重喂整个重叠窗口"改为**消息水位增量 + 会话终态触发**，
   同一段对话不被反复喂给 LLM——既根除重复来源，又省掉大部分提炼 token；
2. **裁决有出口**：写入协议从"new / updates 两个出口"升级为 **ADD / UPDATE / DELETE / NOOP 四操作**，
   且每条候选事实**各自召回** top-K 相似旧条目（而非展示全库前 50 条），让模型在"看得见依据"时裁决；
3. **抽取质量有硬闸**：单条长度、类别、多值项走 schema 校验，撞上限拒绝并记遥测，不再产出截断脏数据；
4. **按生命周期分层**：`permanent` / `stateful` / `episodic` 三种事实用不同的遗忘与替换策略，
   身份类不随时间淡忘、环境类"新值即失效旧值"、一次性项到期出清；
5. （可选，需拍板）**语义向量召回**收编换措辞的同一事实。

**非目标（写进代码，不只是文档）**

- **绝不在对话路径同步跑提炼 LLM**（提炼铁律第一条）：任何"压缩时提炼"只能走"入队 + 后台消费"，
  不在 `query_loop` / `freeze_if_needed` 里同步调 LLM；
- 不破坏 `profile.json` 的**纯 JSON 用户可读可编辑**原则：向量等二进制一律旁挂文件，不塞进主 JSON；
- 不让画像提炼**每轮嗅探**（与自配置层 §非目标同律：低频 + 显式触发，不烧 token 猜意图）；
- P3 向量**默认关闭**，未拿到明确的来源决策前不进主干代码路径。

## 二、现状架构回顾（四个环节，病灶定位）

```
_auto_save（每轮对话）           ← 触发环节：600s 节流，无"提炼到第几条"的水位 → 重喂重叠窗口【病根 1】
      │ maybe_refine_async(messages, session_id, settings)
      ▼
_collect_dialog_text             ← 抽取输入：取最近 40 条压平，全量窗口 → 与水位缺失叠加放大重复
      ▼
_build_prompt + LLM              ← 裁决环节：只展示 existing[:50]，只有 new/updates 两出口，无 NOOP【病根 2】
      ▼
ProfileStore.merge_or_add        ← 落盘环节：字面查重（已落地，治住了重复落盘但没治白调）
```

- **病根 1**：`maybe_refine_async` 只看"距上次提炼 >600s 且 len(messages) ≥ min_messages"，
  不看"上次提炼到第几条"。长会话每 10 分钟把同一批早期轮次重新喂一遍 → 同一事实被重新发现 N 次。
- **病根 2**：`_build_prompt` 用固定 `existing[:50]` 展示 + 只有 ADD/UPDATE 两个出口。画像接近/超过 50 条时，
  高频稳定事实掉出窗口，模型看不见就当新条目；且拿不准时唯一能选的就是写进 new——**重复是协议逼出来的**。

## 三、分阶段方案（P0 起，P4 应尽早与 P1 并行）

> 落地顺序：**P4（安全网）→ P0 → P1 → P2**，P3 单列为待决策。
> P4 先行是因为改阈值/改提示若无回归集就是裸改，违反 `testing.md`；其余按性价比递增。

### P0 · 提炼触发改为消息水位增量 + 终态提炼

**水位台账**（新文件 `agent/core/memory/profile_ledger.py`，从 refiner 拆出守行数）：
`~/.jarvis/memory/refine_ledger.json`，结构 `{session_id: {"refined_until": int, "last_ts": float}}`，
复用 `agent/utils/filelock.py` 的 `file_lock` + 临时文件 + `os.replace` 原子写；读坏当空表，不拦提炼。

**`maybe_refine_async` 改造**：

1. 先读 `refined_until`，算 `delta = len(messages) - refined_until`；
2. `delta < settings.profile_refine_min_delta`（新键，默认 6）→ **直接跳过**（不再看全量 len）；
3. `_collect_dialog_text` 只喂 `messages[refined_until:]` 的**增量区间**（不再喂重叠窗口）；
4. 提炼成功后 `refined_until = len(messages)` 写回台账；失败不推进（下次重试同样增量）。
5. 600s 节流保留，作为"同一会话极高频保存"的成本兜底，与水位是 **AND** 关系。

**终态提炼**（保证最后一段增量不丢）：

- `maybe_refine_async(..., force=False)` 增 `force` 形参：`force=True` 时**无视节流、只认水位**（`delta≥1` 即提炼）。
- 挂钩点（都在对话路径之外 / 后台线程）：
  - REPL：`main.py` 退出前那次最终 `_auto_save` 传 `force=True`；
  - serve / 桌面：`engine.py` 会话切换与 `_handle_load`（切会话）时，对旧会话 `force=True` 收尾；
  - 崩溃恢复：不额外挂，靠水位——下次同 `session_id` 继续提炼未处理增量。

**不做（写进代码注释）**：不在 `query_loop.freeze_if_needed` / `compact_reactive` 处同步提炼——
那是对话路径，违反铁律。若将来要"压前提炼被压掉的前缀"，只能把被压区间 `(start,end)` 入队、
后台线程消费，作为 P2 之后的独立小刀，不在 P0。

### P1 · 四操作裁决协议 + 逐条召回

**逐条召回**（新函数 `profile_store.recall_similar(content, category, top_k)`）：
对每条候选事实，用已有 `profile_content_similarity` 从现库取 **top-K 相似旧条目**
（`settings.profile_recall_top_k`，默认 5；同 category 加权）。这把"展示全库前 50"换成
"每条各自带着它的最像旧条目进 prompt"，从根上消除"看不见 → 当新的"。

**裁决协议**（`_build_prompt` 输出改一份严格 schema）：

```json
{"facts":[
  {"op":"add",    "content":"...","category":"...","confidence":0.8},
  {"op":"update", "target_id":"ent_xxx","content":"新表述","confidence":0.9},
  {"op":"delete", "target_id":"ent_xxx","reason":"已被新事实取代/一次性项到期"},
  {"op":"noop",   "target_id":"ent_xxx","confidence":0.7}
]}
```

prompt 里每条候选下方列出它的召回结果 `- [ent_id] category: content`，并明确：
"命中已有条目 → update/noop，禁止再 add；完全新 → add；拿不准就 noop，宁可少记"。
**NOOP 是关键新增出口**——它承接"看着像重复但不值得改写法"的全部情况，重复率会断崖下降。

**解析与分派**（`refine_session`）：按 `op` 分派，`add`→`merge_or_add`、`update`→改目标条目、
`delete`→`store.delete`、`noop`→不写。`target_id` 不存在（幻觉）降级：update/noop 当 add 走查重闸，delete 忽略。

**抽取质量硬闸**（不再靠 prompt 自觉）：
- `content` 归一化后 >40 字 → **拒绝入库**并记 `RefineReport.rejected` 遥测，
  prompt 补一句"多项目/多技术栈用『等 N 个』收尾，不要列全"——修 `Jarvis Plu` 那类截断脏数据。
- `confidence` 越界 clamp 到 0.3~0.95（已有，保留）。

**不引入 tool-calling**：提炼模型刻意是"便宜可替换"的独立模型（`[memory.refine]`），
不保证支持 function calling；继续自由 JSON + `_parse_profile_json` 容错 + few-shot 钉死四操作。

### P2 · 生命周期分层与"替换即失效"

`ProfileEntry` 增正交字段 `lifecycle ∈ {permanent, stateful, episodic}`（不改 category 枚举，加一维）：

| lifecycle | 典型内容 | 遗忘策略 | 替换语义 |
|---|---|---|---|
| `permanent` | 姓名、学历、出生 | **永不 decay**，只能被 update 显式替换 | 直接改写 |
| `stateful` | 所在地、在用项目、网络环境、主力模型 | 不靠半衰期磨 | 新值到来 → 旧条目打 `invalidated_at` + `superseded_by`，**软删除保历史** |
| `episodic` | 一次性闹铃、临时任务 | 带 `expiry_at`，到期直接出清 | — |

- `decay()` 改为按 lifecycle 分流：`permanent` 跳过、`episodic` 看过期、仅 `stateful/其它` 走半衰期。
- `stateful` 的"新值即失效"由 P1 的 `op=update` 落地：update 同 slot（同 category 同主体）旧条目时，
  保留旧条目打失效标记而非物理删（轻量对齐 Zep temporal 思路），`render_for_prompt` 只渲染未失效的。
- **schema 迁移** version 1 → 2：`profile_store._load` 对缺字段给默认 `lifecycle="stateful"`（最保守：可衰减可失效），
  并配一次性脚本 `scripts/migrate_profile_lifecycle.py`（幂等、可重跑）按 category 粗分初始 lifecycle。

> P2 是最重的一刀，且依赖 P1 的 update 语义先行。范围大，可再拆 P2a（lifecycle 字段 + decay 分流）/
> P2b（软删除失效链）。

### P3 · 语义向量召回（默认关闭，需决策）

仅当 P1 字面召回覆盖不了"换了说法的同一事实"时才上。两个待拍板项：

1. **向量来源**（二选一，影响本地优先与依赖）：
   - A：DashScope `text-embedding`（复用已有百炼 key，零新 SDK，但**违反本地优先**、多一次网络调用）；
   - B：**本地 Ollama embedding**（本地优先，用户机在用 Ollama，但要额外进程与模型体积）。
   - 决策建议：**先 A 试验证收益**，收益不明显就止步于 P1；确需长期再迁 B。
2. **向量存放**：旁挂 `profile.vectors.npz`（按 `entry_id` 对齐），`profile.json` 保持纯 JSON 可读。
   召回 = cosine，`profile_recall_top_k` + `profile_vector_min_score`（默认 0.82）。
3. 未开启时（`profile_vector_source=""`）全链路走 P1 字面召回，**不加载任何向量代码**。

> 这一步破"感知源零新增依赖"的同类戒律，务必单独确认，不默认实施。

### P4 · 记忆评测集（安全网，应尽早）

借 LongMemEval / LOCOMO 的形态，做**本地 golden 小样本**回归集 `tests/memory/test_profile_refine_golden.py`：
用 `_FakeProvider` 固定提炼输出，断言端到端裁决结果。用例族：

- 重复合并：同事实再报 → `added==0`、库内仍一条（已有雏形，扩量）；
- NOOP 出口：候选与旧条目语义同、措辞异 → 期望 `op=noop`，不落 new；
- 跨措辞同义（P3 前用字面能到的边界）；
- 截断拒绝：>40 字候选 → `rejected` 计数 +1，不入库；
- 生命周期：`stateful` 新值 update → 旧条目 `invalidated_at` 有值、`render_for_prompt` 不含旧值；
- `permanent` 跑 decay → confidence 不变。

阈值/提示的任何调整都先过这套，杜绝无测试裸改。

## 四、配置新增键（进 `config_specs` 真源表 + `catalog` + 两份 `example.toml`）

| 键 | 默认 | tier | 说明 |
|---|---|---|---|
| `memory.profile_refine_min_delta` | 6 | auto | 自上次水位后新增多少消息才提炼（替代旧的"看全量 len"） |
| `memory.profile_refine_on_exit` | true | auto | 退出/切会话时做无视节流的终态提炼 |
| `memory.profile_recall_top_k` | 5 | auto | 每条候选事实召回的相似旧条目数 |
| `memory.profile_lifecycle_enabled` | false | auto | P2 开关，关时全按旧行为（单一 decay） |
| `memory.profile_vector_source` | "" | confirm | P3 向量来源（"" 关闭 / dashscope / ollama）：决定私人事实是否外发，故 confirm |
| `memory.profile_vector_min_score` | 0.82 | auto | 向量召回相似下限 |

> `profile_vector_source` 归 **confirm**：它决定"要不要把关于用户的私人对话片段发到外部 embedding 服务"，
> 属隐私边界，与 `notify_budget` 归 confirm 同理——模型不该自己给自己放宽数据外发范围。
> 现有 `profile_refine_interval` / `profile_refine_min_messages` 保留但语义降级：水位增量成为主判据，
> 旧两键退化为成本兜底与最小门槛（`profile_refine_min_messages` 仍约束首次提炼的绝对下限）。

## 五、代码落点与解耦（守 `code-structure.md`）

| 文件 | 改动 | 行数预案 |
|---|---|---|
| `agent/core/memory/profile_refiner.py`（413 行） | 触发逻辑外拆，本体只留编排：瘦身为主 | 增改后仍 <500 |
| `agent/core/memory/profile_ledger.py`（新） | P0 水位台账读写 | 约 120 |
| `agent/core/memory/profile_resolve.py`（新） | P1 逐条召回 + 四操作解析分派 + 抽取硬闸 | 约 200 |
| `agent/core/memory/profile_store.py`（432 行） | 增 `recall_similar`；P2 lifecycle 字段与分流 decay、软删除 | 拆分风险高则 `profile_store_lifecycle.py` 派生 |
| `agent/commands/handlers/session_commands.py`（365 行） | `/memory` 展示补 lifecycle 标签与失效标记 | <450 |
| `agent/main.py` / `serve/engine/engine.py` | 终态 `force=True` 提炼挂钩各一处 | — |
| `scripts/migrate_profile_lifecycle.py`（新） | P2 一次性幂等迁移 | 约 80 |

依赖方向：`refiner → {ledger, resolve} → store`；`ledger`/`resolve` 不反向依赖 refiner。
提炼、召回、裁决、落盘各自单一职责，`_build_prompt` 留 refiner、召回进 resolve。

## 六、验收判据（每阶段"真做到"的硬标准）

- **P0**：构造 20 轮会话、每轮 `_auto_save`，断言 LLM 实际被调用次数 ≤ `⌈20/min_delta⌉`（而非旧的"每 600s 一次全窗口"）；
  同一事实在增量里出现一次即不再被后续提炼重复"发现"；退出会话时最后增量被终态提炼收干。
- **P1**：golden 集"措辞不同但同义"用例走 `noop`/`update`，新库不再产同义重复条目；>40 字候选 100% 被拒并计数。
- **P2**：`permanent` 跑 90 天 decay 不变；`stateful` 换所在地后旧值在 `render_for_prompt` 消失但仍在盘上可追溯；
  `episodic` 闹铃过 `expiry_at` 自动出清。
- **P3**（若做）：一句"我习惯晚上写码"能召回并合到"习惯深夜工作"，`added==0`。
- **P4**：阈值/提示改动后本地回归集全绿才算交付。

## 七、风险与回退

- **水位算错导致漏提炼**：以 `session_id` 为键、只增不减；台账损坏退化为"全量提炼一次"再重建水位（安全侧漏提炼优于漏记忆）。
- **四操作 JSON 更复杂、小模型解析失败率升高**：`_parse_profile_json` 已有容错；解析不出 `op` 时**默认按 add 走存储层查重闸**（退回已验证的兜底），不丢内容。
- **P2 schema 迁移写坏用户库**：迁移前自动备份 `profile.json.bak`（对齐图谱"写入前自动备份"）；加载兼容缺字段，老库照常可读。
- **每档都有开关**：`profile_refine_on_exit` / `profile_lifecycle_enabled` / `profile_vector_source` 关掉即回旧行为，
  P0 水位是唯一"默认生效且无回退开关"的改动（它就是治本本体），但逻辑局部、可单测钉死。

## 八、建议的最小可行首刀

若要立刻见效、又不一次铺太大：**P4（评测集）+ P0（水位增量）** 一起做——
P0 单独就能把"白花 N 次 token"和"雪球式重复"从源头压掉，改动局部、有 P4 兜底、无新依赖；
P1 四操作作为紧随其后的第二刀。P2/P3 各自独立评估后再上。
