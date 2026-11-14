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
>
> 对标依据（2026-11-13）：[docs/research/memory-systems-survey.md](../research/memory-systems-survey.md)
> ——结论是无一项可直接引入；但 memobase 的三触发 flush、mem0 的四操作裁决、memobase 的
> `context(max_token_size, prefer_topics)` 注入、以及 profile/event 双轨，四条设计被采纳进本计划。

@author aceFelix

## 一、目标与非目标

**目标（按优先级）**

1. **源头掐重复**：提炼由"600s 轮询重喂整个重叠窗口"改为**消息水位增量 + 会话终态触发**，
   同一段对话不被反复喂给 LLM——既根除重复来源，又省掉大部分提炼 token；
2. **裁决有出口**：写入协议从"new / updates 两个出口"升级为 **ADD / UPDATE / DELETE / NOOP 四操作**，
   且每条候选事实**各自召回** top-K 相似旧条目（而非展示全库前 50 条），让模型在"看得见依据"时裁决；
3. **抽取质量有硬闸**：单条长度、类别、多值项走 schema 校验，撞上限拒绝并记遥测，不再产出截断脏数据；
4. **按生命周期分层 + profile/event 分轨**：`permanent` / `stateful` / `episodic` 三种事实用不同的
   遗忘与替换策略（身份类不淡忘、环境类"新值即失效旧值"、一次性项到期出清），并让"发生过的事"
   走带时间线的事件轨，答得了"上周我说过什么"；
5. （可选，需拍板）**语义向量召回**收编换措辞的同一事实；
6. （P5，可选）**按需检索**：常驻只放少量高价值事实，其余靠 `RecallProfile` 工具按 query 捞取——
   解决"库越大、注入覆盖率越低"（实测 51 条库只注入 35 条，相关性召回为 0）。

**非目标（写进代码，不只是文档）**

- **记忆数据不出本机（2026-11-14 拍板，铁律）**：`profile.json` / `events.jsonl` / 向量旁挂文件只落
  `~/.jarvis/memory/`，不上传任何云端记忆服务；**永不引入任何数据库或需要用户部署的服务**
  （向量数据库、Postgres、Redis、FAISS / sqlite-vec 全部排除）。向量若做，只允许**本地模型**生成，
  云端 embedding 选项不存在；取值白名单 `"" | "ollama"`，其他值 fail-closed 关闭。
  > 边界说明：对话与提炼本身仍经 LLM 厂商（现状不变）；若连提炼也不想出网，把 `[memory.refine]`
  > 指向本地 Ollama 端点即可（该配置已支持任意 OpenAI 兼容地址）。
- **绝不在对话路径同步跑提炼 LLM**（提炼铁律第一条）：任何"压缩时提炼"只能走"入队 + 后台消费"，
  不在 `query_loop` / `freeze_if_needed` 里同步调 LLM；
- 不破坏 `profile.json` 的**纯 JSON 用户可读可编辑**原则：向量等二进制一律旁挂文件，不塞进主 JSON；
- 不让画像提炼**每轮嗅探**（与自配置层 §非目标同律：低频 + 显式触发，不烧 token 猜意图）；
- P3 向量**默认关闭**，不满足"未命中率 >25% 且库 >500 条"的触发条件就不进主干代码路径。

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

## 三、成熟方案盘点（2026-11-13 对标结论）

完整数据（star/语言/license/硬依赖清单/机制原文）见
[docs/research/memory-systems-survey.md](../research/memory-systems-survey.md)。此处只留影响本计划的结论：

| 项目 | Stars | 形态 | 能否直接嵌入 | 对本计划的贡献 |
|---|---:|---|---|---|
| memobase | 3.0k | 用户画像式记忆**服务器**（FastAPI+PG+Redis） | ❌ 需三个服务；**2026-01 后停更** | **三触发 flush**、`context(prefer_topics)` 注入、profile/event 双轨 |
| mem0 | 68.9k | 记忆层/平台 | ❌ 硬依赖 qdrant+sqlalchemy+**posthog** | **ADD/UPDATE/DELETE/NOOP 四操作** |
| graphiti | 32.8k | 时序知识图谱 | ❌ 硬依赖 **neo4j** | `valid_at/invalid_at` 双时间轴（对应 P2 软失效） |
| letta | 25.4k | Agent 平台 | ❌ 平台级 | core/archival 分层（常驻少量 + 按需检索） |
| memU | 14.6k | 文件式个人记忆 CLI | ❌ sqlmodel+alembic；license NOASSERTION | 文件式 + 宿主 sidecar 思路 |
| langmem / MemOS / cognee / Memori / A-mem | 1.2k–33.3k | 各自生态 | ❌ 绑框架 / TS 主 / 停更 | 仅借鉴，不引入 |

**统一结论：零新增依赖、不引入任何包、不要求用户部署服务；只抄设计，不抄包。**
（部署形态同样一票否决：memobase / letta 要跑服务，graphiti 要 neo4j，mem0 默认 qdrant。）
五条修正分别是：
① P0 补"空闲超时"第三触发（原计划只有增量水位 + 退出终态）；
② P1 补"注入侧主题预算"（`prefer_topics`，原计划的召回只服务提炼裁决）；
③ P2 升级为 profile/event 双轨（原计划只有 lifecycle 字段）；
④ 向量维持 P3 后置——对标项目全部依赖 embedding，但 memobase 证明低延迟靠"画像 + SQL"即可；
⑤ 新增 **P5 常驻/按需分层**（letta 的 core / archival）——解决"库涨到 200 条时注入覆盖率只剩 ~17%"。

## 四、分阶段方案（P0 起，P4 应尽早与 P1 并行）

> 落地顺序：**P4（安全网）→ P0 → P1 → P2**；P3 / P5 单列为可选，P5 需 P1 稳定后再评估。
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

**空闲超时触发**（对标 memobase 第三触发，防"聊一半走开、增量永不落库"）：

- `maybe_refine_async` 本身不新增计时器：当 `delta ≥ 1` 但距上次提炼不足 `profile_refine_interval` 时
  照旧跳过；由**宿主已有的定时回路**补一次"空闲判定"——serve/桌面挂在 `ProactiveHub` 的分钟级 tick，
  REPL 挂在现有日程提醒的轮询上，判定条件 `delta ≥ 1 且 now - 最后一条消息时间 ≥ profile_refine_idle_minutes`。
- 该判定**只读水位与时间戳**，不调 LLM、不起新线程常驻，不违反 hot-path 铁律。
- 与另两个触发的关系：阈值触发管"聊得够多"、空闲触发管"聊得够久"、终态触发管"会话结束"，三者幂等
  （都走同一水位闸，重复调用只会有一次真正提炼）。

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

**两段式调用（先抽取、后裁决）**——这是 P1 的结构前提：

「逐条召回」的候选事实是 LLM 抽出来的，召回必须在抽取**之后**才有对象，否则无从知道要召回什么。
因此 P1 把现在的一次调用拆成两次 + 一次本地计算（与 mem0 的 extraction / update-resolver 同构）：

| 步骤 | 输入 | 输出 | 是否调 LLM |
|---|---|---|---|
| ① 抽取 | 规则 + **增量对话**（**不带**旧画像清单） | 候选事实 `[{content, category, confidence}]` | ✅（`max_tokens≤1500`） |
| ② 逐条召回 | 每个候选 × `recall_similar(top_k)` | 候选 + top-K 旧条目 | ❌ 纯本地字面相似度 |
| ③ 裁决 | 四操作指令 + few-shot + 候选 + 各自召回结果 | `facts[]`（含 `op` / `target_id`） | ✅ |

收益：① 不再携带旧画像清单；② 零 token；③ 只带「候选 × top-K」而非全库——**总成本不随画像库增长**
（单段式在库到 200 条时，每次要多带约 7,000 字）。代价是 LLM 调用翻倍（每次提炼 2 次），但提炼在
后台线程、有水位与节流闸，延迟不进用户路径，绝对成本仍低于现状（见本节末「成本对照」）。

**逐条召回**（新函数 `profile_store.recall_similar(content, category, top_k)`）：
对每条候选事实，用已有 `profile_content_similarity` 从现库取 **top-K 相似旧条目**
（`settings.profile_recall_top_k`，默认 5；同 category 加权）。这把"展示全库前 50"换成
"每条各自带着它的最像旧条目进 prompt"，从根上消除"看不见 → 当新的"。

**裁决协议**（第 ③ 步的 `_build_prompt` 输出改一份严格 schema）：

```json
{"facts":[
  {"op":"add",    "content":"...","category":"...","confidence":0.8,
   "aliases":["熬夜","晚睡","作息晚"]},
  {"op":"update", "target_id":"ent_xxx","content":"新表述","confidence":0.9,
   "aliases":["..."]},
  {"op":"delete", "target_id":"ent_xxx","reason":"已被新事实取代/一次性项到期"},
  {"op":"noop",   "target_id":"ent_xxx","confidence":0.7}
]}
```

prompt 里每条候选下方列出它的召回结果 `- [ent_id] category: content`，并明确：
"命中已有条目 → update/noop，禁止再 add；完全新 → add；拿不准就 noop，宁可少记"。
**NOOP 是关键新增出口**——它承接"看着像重复但不值得改写法"的全部情况，重复率会断崖下降。

**别名生成（三级召回的写入侧，2026-11-14 拍板）**：

`add` / `update` 必须顺带产出 `aliases`——这是把"语义理解"从查询时挪到写入时的关键，边际成本≈0：

- **规则**：每条 2–5 个，每个 2–6 字；覆盖"用户可能怎么问这条事实"（同义词、口语、缩写/全称）；
  不重复 content 原词；专有名词保留原文（GLM / 火狐 / WPS）；**禁止编造与事实矛盾的说法**。
- **示例**：`习惯深夜工作` → `["熬夜","晚睡","作息晚","凌晨"]`；
  `使用WPS处理文档` → `["办公软件","docx","表格"]`。
- **存储**：`ProfileEntry.aliases: list[str]`，缺字段按 `[]`（老库向后兼容）；合并时**取并集**，
  上限 8 个，超出按历史查询命中次数截断。
- **成本**：每条 +~10 output token；一次提炼 5 条 ≈ +50 token，相对 P0 省下的量可忽略。
- **关闭**：`profile_aliases_enabled=false` 时 prompt 不带该字段，召回自动降为"类别 + bigram"两级。

**解析与分派**（`refine_session` 按 ①②③ 编排）：先解析抽取结果 → 逐条本地召回 → 再解析裁决结果，
按 `op` 分派：`add`→`merge_or_add`、`update`→改目标条目、`delete`→`store.delete`、`noop`→不写。
`target_id` 不存在（幻觉）降级：update/noop 当 add 走查重闸，delete 忽略。
两段任一失败都静默跳过本轮（**不回退到单段式**，避免成本失控）。

**抽取质量硬闸**（不再靠 prompt 自觉）：
- `content` 归一化后 >40 字 → **拒绝入库**并记 `RefineReport.rejected` 遥测，
  prompt 补一句"多项目/多技术栈用『等 N 个』收尾，不要列全"——修 `Jarvis Plu` 那类截断脏数据。
- `confidence` 越界 clamp 到 0.3~0.95（已有，保留）。

**不引入 tool-calling**：提炼模型刻意是"便宜可替换"的独立模型（`[memory.refine]`），
不保证支持 function calling；继续自由 JSON + `_parse_profile_json` 容错 + few-shot 钉死四操作。

**注入侧改造（对标 memobase `context(max_token_size, prefer_topics)`）**：

此前"召回"只服务提炼裁决，但用户真正感知到的"记不记得住"发生在**注入侧**。现状
`render_for_prompt` 只有一个维度（按 confidence×新近度排序、放不下就 `continue`），存在三个问题：

1. **没有主题维度**：无论用户问什么，注入的永远是"高置信那批"；
2. **伪 token 口径**（`len/4`）：对中文严重低估，名义 300 实际可装 1200 字符（≈50+ 条），
   且真实 token 远超预算；
3. **长条目挤掉短条目**：一条 40 字事实会占掉 4–5 条短事实的位置。

对应改造（全部落在新增 `agent/core/memory/profile_inject.py`，不改压缩阈值那条线）：

- **主题优先**：新增 `profile_inject_prefer_topics`，命中主题的条目置顶；空列表＝保持现状排序。
- **两轮填充**：第一轮只放"长度 ≤ 中位数"的条目，第二轮补长条目——避免单条长事实挤掉多条短事实。
- **预算口径可切换**：新增 `profile_inject_token_budget_mode`（`pseudo` 默认＝现状 `len/4`，
  `weighted` ＝ `estimate_text_tokens_weighted` 中英分权）。**默认保持 `pseudo` 不做静默行为变更**；
  切到 `weighted` 时必须同步下调 `profile_inject_token_limit` 保持实际注入量不变，具体值须在 P4
  回归集实测后确定，不得凭估算改。
- **同分排序改用三级召回打分**：`prefer_topics` 之后，同分条按 `profile_recall.py` 的三级打分
  （类别 → 别名+bigram → 向量兜底）稳定排序；只影响"谁排在前面"，不改预算裁剪本身。

### P2 · 生命周期分层 + profile/event 双轨

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

**profile / event 双轨**（对标 memobase，补 jarvis 完全缺失的时间维度）：

现行 `profile.json` 里全是"长期成立的事实"，回答不了"上周我说过什么 / 上个月在忙什么"。新增事件轨：

- 存储 `~/.jarvis/memory/events.jsonl`（append-only，字段 `ts / category / content / source_session /
  expire_at`），按 `ts` 索引，保留 `profile_event_retention_days` 天；
- **写入来源**：P1 裁决为 `lifecycle=episodic` 的事实，以及带明确时间点的事实（"下周三面试"）；
- **注入策略**：事件轨**不默认注入**（否则又变成全量塞上下文）；仅在用户问题命中时间词/主题时，
  由 `profile_inject_prefer_topics` 捞出最近 K 条随画像段一起注入；
- **互不写入**：事件轨与 profile 物理分离（与 W3 账本、协作任务列表的存储分离同律），事件过期按
  `retention_days` 清理，profile 条目不受影响。

> P2 是最重的一刀，且依赖 P1 的 update 语义先行。范围大，可再拆 P2a（lifecycle 字段 + decay 分流）、
> P2b（软删除失效链）、P2c（事件轨 events.jsonl + 过期清理）三小刀，每刀独立可验收。

### P3 · 语义向量召回（默认关闭；只允许本地；永不引数据库）

**三条铁律（2026-11-14 拍板，优先级高于本节其余内容）**：

1. **不引入任何数据库 / 不要求用户部署任何服务**：不用向量数据库（Chroma / Qdrant / Milvus /
   pgvector），也不引 FAISS / sqlite-vec / Redis。条目上限 200、单用户单查询，**旁挂文件 + 暴力余弦**
   足够，且精确检索没有 ANN 的召回损失。
2. **数据不出本机**：向量只由**本地模型**生成（Ollama）；云端 embedding（DashScope 等）**选项删除**，
   配置取值白名单 `"" | "ollama"`，出现其他值一律 fail-closed 关闭并告警。
3. **默认关闭**：`profile_vector_source=""` 时全链路走字面召回（类别过滤 + bigram 相似度），
   不 import 任何向量代码。

**实现形状**（约 120 行）：

- 存储：`profile.vectors.bin`（`array('f').tobytes()` 原始 float32）+ `vectors_index.json`
  （`entry_id → 偏移/维度`）；`profile.json` 保持纯 JSON 可读，向量坏了可直接删掉重建。
- 计算：暴力余弦。200 × 1024 维 ≈ 20 万次乘加 → 纯 Python **10–50ms**，有 numpy 走快路径 **<1ms**
  （沿用 `aec.py` 的 `_HAS_NUMPY` 惰性导入范式，**numpy 不是硬依赖**）。
- 更新：新增/更新只 embed 那一条并追加；删除只标记，季度压缩一次。
- 体积：200 × 1024 × 4B ≈ **800KB**（对比 JSON 存浮点约 4–5MB）。

**触发条件（三条同时满足才做）**：P5 上线后**前两级（类别 → 别名+bigram）**的检索未命中率 **>25%**；
库规模 **>500 条**；别名已扩到上限（8/条）仍不够。未满足就永不做。

### P4 · 记忆评测集（安全网，应尽早）

借 LongMemEval / LOCOMO 的形态，做**本地 golden 小样本**回归集 `tests/memory/test_profile_refine_golden.py`：
用 `_FakeProvider` 固定提炼输出，断言端到端裁决结果。用例族：

- 重复合并：同事实再报 → `added==0`、库内仍一条（已有雏形，扩量）；
- NOOP 出口：候选与旧条目语义同、措辞异 → 期望 `op=noop`，不落 new；
- 跨措辞同义（P3 前用字面能到的边界）；
- 截断拒绝：>40 字候选 → `rejected` 计数 +1，不入库；
- 生命周期：`stateful` 新值 update → 旧条目 `invalidated_at` 有值、`render_for_prompt` 不含旧值；
- `permanent` 跑 decay → confidence 不变；
- 事件轨（P2c）：写入事件后 `profile.json` 字节不变；`retention_days` 过期清理不影响 profile 条目；
- 按需检索（P5）：问到"我的邮箱/常用工具"而 core 注入未覆盖时，模型应发起 `RecallProfile` 并命中；
  不发起时用例算不过（反向验证提示词那一行真的生效）；
- 三级召回：15 组"提问 → 期望条目"，**前两级 top-3 命中 ≥12 组**；关闭别名后下降但不报错。

阈值/提示的任何调整都先过这套，杜绝无测试裸改。

### P5 · 按需检索（RecallProfile 工具，可选后置）

现状注入是"全量或按置信度裁剪"，**不是检索**：300 伪单位只装得下约 37 条（实测 51 条库注入 35 条，
覆盖率 69%，相关性召回为 0）；库涨到 200 上限时覆盖率跌到 **~17%**。P5 把"全塞进 system prompt"
改成"常驻少量 + 按需捞取"，即 letta 的 core / archival 分层。

**分层口径**：

| 层 | 内容 | 注入方式 | 预算 |
|---|---|---|---|
| core（常驻） | `permanent` + 高置信（≥0.85） | 每轮 system prompt | `profile_inject_token_limit` 默认 300 不变；开 `core_only` 后可降到 ~150 |
| archival（按需） | 其余全部条目（含 stateful / episodic） | 模型调 `RecallProfile` 时返回 | 单次 ≤ top_k 条、≤1.5KB |

**工具契约**（新文件 `agent/tools/recall_profile.py`，约 140 行）：

```
RecallProfile(query: str, category: str = "", top_k: int = 8)
```

- **只读**：`is_read_only=True`，权限 ALLOW，不写 `profile.json`；
- **召回算法（三级级联，2026-11-14 拍板）**：
  1. **类别过滤**：`category` 命中优先，未指定不限制（免费，先砍量级）；
  2. **别名 + bigram**：`score = 0.6×别名命中率 + 0.4×profile_content_similarity(content, query)`，
     别名权重由 `profile_recall_alias_weight` 调；常见路径 **<1ms、零网络**；
  3. **向量兜底**：仅当 1–2 级 **0 命中**且 `profile_vector_source="ollama"` 时调用本地余弦；
     未开启向量就到此为止（返回"未命中"，不编造）；
- **返回**：`- [ent_id] category (conf 0.85): content`，最多 `top_k` 条，末尾补"共 N 条候选，已显示 M 条"；
  单条 ≤40 字、总长 ≤1.5KB（回灌对话历史的内容会占后续轮次上下文）；
- **副作用**：命中条目 `ref_count += 1`、`last_referenced_at = now`——**被真正用到的记忆不再被 decay 误杀**
  （现状 `decay` 看 `last_referenced_at`，但这个字段从未被写入过，等于衰减只看 updated_at）；
- **注册**：`deferred=True`，与 MCP 工具同律，经 ToolSearch 发现，避免每轮都带 schema。

**系统提示**：新增一行（**仅当工具真注册时注入**，与 ScheduleReminder 同律）：
"当需要用户的具体细节（偏好/工具/项目/作息）而当前上下文没有时，调用 `RecallProfile(query)` 检索，
不要凭印象编造。"

**成本**：

- 常驻注入从 ~538 real tokens 降到 ~270（开 `core_only` 时）；
- 每次 `RecallProfile` ≈ 150 输入 + 200 输出 ≈ 350 token，按需触发（估 5 次/天 ≈ 1.7k/天）；
- 净效果：**总开销下降，且覆盖率不再随库规模衰减**——覆盖率由 top-K 决定，与库大小弱相关。

**验收**：

- 构造 100 条库、问题只与排名第 80 的条目相关 → 默认 core 注入不含它，`RecallProfile` **top-3 必须命中**；
- 调用后该条 `ref_count ≥ 1`、`last_referenced_at > 0`，连续 90 天不被 decay 清除；
- 工具未注册/关闭时，行为与现状**完全一致**（回退开关）。

**风险**：

- 模型不主动调用 → 靠提示一行 + P4 golden 用例（"问到未注入的细节应发起检索"）；
  仍不调用时的兜底：`profile_inject_core_only` **默认 false**，不开启就维持现状；
- 检索质量弱于向量 → 明确写"P3 未开时为字面召回，同义改写可能漏"，不承诺语义召回。

### 成本对照（token 账，按现码常量估算）

估算依据：`_MAX_MESSAGES=40`、`_MAX_CHARS_PER_MSG=300`、旧画像展示 50→80 条、`max_tokens=1500`、
中文字符 0.6 token（`estimate_text_tokens_weighted` 口径）。场景：一天 20 轮 / 40 条 user+assistant
消息 / 平均 80 字。

| 阶段 | 提炼次数/天 | 单次输入构成 | 单次日 token | 日合计（提炼） | 注入侧/轮 |
|---|---:|---|---:|---:|---|
| 现状 | 12（每 600s） | 模板 550 + 旧画像 1,750 + **重喂窗口 3,200** | ~3,300 | **~43k** | 名义 ~720 real tokens，命中前缀缓存后 ~72 |
| P0 后 | ~7（40 ÷ delta 6） | 模板 550 + 旧画像 2,800 + **增量 480** | ~2,300 | **~18k（−58%）** | 同现状 |
| P1 后 | ~7 | ①抽取 780 字 + ②裁决 1,775 字 | ~2,000 | **~14k（−67%）** | 切 `weighted` 后可降到 300–400 |

三条结论：

1. 现状的大头是"**重复发送对话**"（12 次 × 最近 40 条 = 最多 480 条消息副本）；P0 让每条消息只发一次，
   对话部分从 38,400 字降到 3,200 字（**−92%**）。
2. **P0 之后大头变成旧画像清单**——这正是 P1 两段式存在的理由；两段式只带「候选 × top-K」，
   不随画像库增长，而单段式在库到 200 条时每次要多带约 7,000 字。
3. 记忆系统总开销从约占真实对话的 19% 降到约 8%——**P0 的主要价值是止血与省重复，不是省大钱**；
   绝对成本按便宜提炼模型的单价量级可忽略。

> 以上是按代码常量的**估算**而非实测。实测路径：`/cost` 的会话用量，或 `diag.log` 中
> `profile-refiner` 线程的调用次数。P4 宜补一条"提炼提示词字符数不得超阈值"的断言，
> 防止后续改 prompt 时无意识膨胀。
>
> 别名生成使单次裁决输出 +~50 token（5 条 × 10），相对 P0 每天省下的 ~25k 可忽略；
> 但它把检索成本从"每次查询做语义计算"变成"写入时一次付清"，这是别名方案效率高的根本原因。
>
> P5 若开启，上表的"注入侧/轮"要再降一半（~270 real tokens），代价是每次检索 ~350 token；
> 总量仍下降，但成本从"每轮固定"变成"按需波动"，预算估算方式不同，需单独建一栏观测。

## 五、配置新增键（进 `config_specs` 真源表 + `catalog` + 两份 `example.toml`）

| 键 | 默认 | tier | 说明 |
|---|---|---|---|
| `memory.profile_refine_min_delta` | 6 | auto | 自上次水位后新增多少消息才提炼（替代旧的"看全量 len"） |
| `memory.profile_refine_on_exit` | true | auto | 退出/切会话时做无视节流的终态提炼 |
| `memory.profile_refine_idle_minutes` | 60 | auto | 空闲多久后补一次增量提炼（防"聊一半走开"增量不落库） |
| `memory.profile_recall_top_k` | 5 | auto | 每条候选事实召回的相似旧条目数 |
| `memory.profile_inject_prefer_topics` | [] | auto | 注入优先主题（命中者置顶），空＝纯按置信度 |
| `memory.profile_inject_token_budget_mode` | "pseudo" | auto | 注入预算口径：`pseudo`（`len/4`，现状）/ `weighted`（中英分权真 token） |
| `memory.profile_lifecycle_enabled` | false | auto | P2 开关，关时全按旧行为（单一 decay） |
| `memory.profile_event_enabled` | false | auto | P2 事件轨开关，关时行为与现状一致 |
| `memory.profile_event_retention_days` | 90 | auto | 事件轨保留天数 |
| `memory.profile_vector_source` | "" | confirm | P3 向量来源，白名单仅 `""`（关闭）/ `"ollama"`（本地）；其他值 fail-closed。云端 embedding 不存在 |
| `memory.profile_vector_min_score` | 0.82 | auto | 向量召回相似下限 |
| `memory.profile_inject_core_only` | false | auto | P5：注入只放 permanent + 高置信（~150 伪单位），其余交给 RecallProfile |
| `memory.profile_recall_tool_top_k` | 8 | auto | P5：RecallProfile 默认返回条数 |
| `memory.profile_recall_min_score` | 0.0 | auto | P5：工具召回的相似度下限（0＝不设限，仅按 top-K 截断） |
| `memory.profile_aliases_enabled` | true | auto | P1：提炼时生成检索别名（关＝降级为类别+bigram 两级） |
| `memory.profile_alias_max` | 5 | auto | P1：每条事实生成的别名上限（合并后最多保留 8） |
| `memory.profile_recall_alias_weight` | 0.6 | auto | P1/P5：三级召回中别名命中率的权重（其余给 bigram） |

> `profile_vector_source` 归 **confirm** 的理由已变：不再是"隐私外发"（云端选项已删除），
> 而是它会拉起一个**本地模型进程**（显存/内存开销），属于资源决策。
> 现有 `profile_refine_interval` / `profile_refine_min_messages` 保留但语义降级：水位增量成为主判据，
> 旧两键退化为成本兜底与最小门槛（`profile_refine_min_messages` 仍约束首次提炼的绝对下限）。

## 六、代码落点与解耦（守 `code-structure.md`）

| 文件 | 改动 | 行数预案 |
|---|---|---|
| `agent/core/memory/profile_refiner.py`（413 行） | 触发逻辑外拆，本体只留编排：瘦身为主 | 增改后仍 <500 |
| `agent/core/memory/profile_ledger.py`（新） | P0 水位台账读写 + 空闲判定纯函数 | 约 140 |
| `agent/core/memory/profile_resolve.py`（新） | P1 两段式编排 + 逐条召回 + 四操作/别名解析分派 + 抽取硬闸 | 约 260 |
| `agent/core/memory/profile_recall.py`（新） | P1/P5 三级召回打分（类别 → 别名+bigram → 向量兜底），注入与工具共用 | 约 100 |
| `agent/core/memory/profile_inject.py`（新） | P1 注入侧：主题优先 + 两轮填充 + 预算口径切换 | 约 150 |
| `agent/core/memory/profile_events.py`（新） | P2 事件轨读写与过期清理 | 约 120 |
| `agent/tools/recall_profile.py`（新） | P5 RecallProfile 工具：只读召回 + ref 计数 | 约 140 |
| `agent/core/tool.py` | 注册 RecallProfile（`deferred=True`，走 ToolSearch） | +5 |
| `agent/prompts/system.py` | 工具注册时注入"按需检索"一行指令 | +8 |
| `agent/core/memory/profile_store.py`（432 行） | 增 `recall_similar`、`aliases` 字段与并集合并；P2 lifecycle 字段与分流 decay、软删除 | 拆分风险高则 `profile_store_lifecycle.py` 派生 |
| `agent/commands/handlers/session_commands.py`（365 行） | `/memory` 展示补 lifecycle 标签与失效标记 | <450 |
| `agent/main.py` / `serve/engine/engine.py` | 终态 `force=True` 提炼与空闲检查挂钩 | — |
| `scripts/migrate_profile_lifecycle.py`（新） | P2 一次性幂等迁移 | 约 80 |

依赖方向：`refiner → {ledger, resolve} → store`；`inject` 只依赖 `store.entries()` 与配置，
可被 `prompts/system.py` 直接调用；`events` 独立，不被 `store` 依赖。
提炼、召回、裁决、注入、落盘各自单一职责，`_build_prompt` 留 refiner。

依赖方向：`refiner → {ledger, resolve} → store`；`ledger`/`resolve` 不反向依赖 refiner。
提炼、召回、裁决、落盘各自单一职责，`_build_prompt` 留 refiner、召回进 resolve。

## 七、验收判据（每阶段"真做到"的硬标准）

- **P0**：构造 20 轮会话、每轮 `_auto_save`，断言 LLM 实际被调用次数 ≤ `⌈20/min_delta⌉`（而非旧的"每 600s 一次全窗口"）；
  同一事实在增量里出现一次即不再被后续提炼重复"发现"；退出会话时最后增量被终态提炼收干；
  **模拟"聊 3 轮后静置超过 `profile_refine_idle_minutes`"→ 空闲触发必须补提炼一次，且只补一次（幂等）。**
- **P1**：golden 集"措辞不同但同义"用例走 `noop`/`update`，新库不再产同义重复条目；>40 字候选 100% 被拒并计数；
  **别名**：`add`/`update` 必须产出 2–5 个；老库缺字段按 `[]` 加载；合并取并集且 ≤8；
  `profile_aliases_enabled=false` 时降为两级且不报错；
  **两段式**：断言抽取段 prompt **不含**旧画像清单（防实现时退化成单段式），裁决段只带「候选 + top-K」；
  **注入侧**：给定 `prefer_topics=["identity"]` 时 identity 条目必须置顶；两轮填充下短条目不再被单条长条目清空；
  `budget_mode=weighted` 与 `pseudo` 在同一库上的实际注入条数差异可量化（作为调参依据）。
- **P2**：`permanent` 跑 90 天 decay 不变；`stateful` 换所在地后旧值在 `render_for_prompt` 消失但仍在盘上可追溯；
  `episodic` 闹铃过 `expiry_at` 自动出清；**事件轨**：写入 3 条事件后，`profile.json` 字节不变；
  超过 `retention_days` 的事件被清理且不影响 profile 条目。
- **P3**（若做）：一句"我习惯晚上写码"能召回并合到"习惯深夜工作"，`added==0`；
  配置 `profile_vector_source="dashscope"` 必须被拒并回退关闭（fail-closed），启动无任何网络请求。
- **P4**：阈值/提示改动后本地回归集全绿才算交付。
- **P5**（若做）：100 条库仅第 80 条相关 → 默认注入不含它，`RecallProfile` top-3 命中；调用后
  `ref_count` 增加且 90 天不衰减；不开启 `profile_inject_core_only` 时行为与现状一致。

## 八、风险与回退

- **水位算错导致漏提炼**：以 `session_id` 为键、只增不减；台账损坏退化为"全量提炼一次"再重建水位（安全侧漏提炼优于漏记忆）。
- **四操作 JSON 更复杂、小模型解析失败率升高**：`_parse_profile_json` 已有容错；解析不出 `op` 时**默认按 add 走存储层查重闸**（退回已验证的兜底），不丢内容。
- **P2 schema 迁移写坏用户库**：迁移前自动备份 `profile.json.bak`（对齐图谱"写入前自动备份"）；加载兼容缺字段，老库照常可读。
- **每档都有开关**：`profile_refine_on_exit` / `profile_lifecycle_enabled` / `profile_inject_core_only` /
  `profile_vector_source` 关掉即回旧行为，P0 水位是唯一"默认生效且无回退开关"的改动（它就是治本本体），
  但逻辑局部、可单测钉死。

## 九、建议的最小可行首刀

若要立刻见效、又不一次铺太大：**P4（评测集）+ P0（水位增量/三触发）** 一起做——
P0 单独就能把"白花 N 次 token"和"雪球式重复"从源头压掉，改动局部、有 P4 兜底、无新依赖；
P1（四操作 + 注入侧）作为紧随其后的第二刀。P2/P3/P5 各自独立评估后再上：
P2 依赖 P1 的 update 语义；P5（按需检索）依赖 P1 注入侧稳定，**不先做**。

> 调研补充（2026-11-13）：对标项目里无一可直接引入（详见
> [docs/research/memory-systems-survey.md](../research/memory-systems-survey.md)），
> 因此"自研 + 只抄设计"不是保守选择，而是当前约束下的唯一合理解。
> 若要进一步深入，优先读 memobase 的 flush 实现与 mem0 的 resolver prompt，而不是引入它们的包。
