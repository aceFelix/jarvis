# 实现方案：画像记忆 P6（常驻人设卡 + 全量归档分页）

> 状态：**已落地（P6a–P6f，2026-11-18）**　作者：aceFelix　日期：2026-11-18
>
> 调研依据：[research/always-on-profile-memory.md](../../research/always-on-profile-memory.md)
> 上位路线：[roadmap/memory-config/PLAN-profile-memory-upgrade.md](../../roadmap/memory-config/PLAN-profile-memory-upgrade.md)
> 前序实现：[profile-memory-p2-impl.md](profile-memory-p2-impl.md)（P0–P5 已落地）

## 一、目标与非目标

### 目标

1. **解决“一直懂我”**：每次会话常驻一张 600–800 tokens 的人设卡（L0），而不是只注入 12 条核心条目。
2. **解决“你了解我多少”**：`RecallProfile` 增加 `mode="list"` 分页枚举全库，模型能翻完再回答。
3. **不丢记忆**：全量条目继续保留（无上限），休眠只降权不删除。
4. **成本可控**：L0 硬上限 + 稳定前缀缓存；重建后台执行、低频去抖。
5. **不引新依赖**：纯本地 JSON + 现有提炼模型；不引向量库/图数据库/云端 embedding。
6. **与长期记忆划清边界**：MEMORY.md 是用户显式意志，画像/人设卡只是自动观察；
   冲突时以长期记忆为准，且画像绝不自动改写 MEMORY.md。

### 非目标

- 不把全部原始条目逐轮注入；
- 不做多用户/多租户；
- 不改 P0–P5 的水位、两段式、四操作、软删除、事件轨语义；
- 不要求用户本地部署模型（重建失败保留旧卡）。

## 二、总体架构

```
提炼成功（P0，每 7 条 / 退出）
        │  entries 变化
        ▼
persona builder（P6，后台 1 次 LLM）
  输入：分层采样条目 + 上一版人设卡
  输出：persona.json（带 source 接地校验）
        │
        ▼
system prompt 注入（每个进程/会话）
  L0 人设卡（稳定前缀，600–800 tokens）
  L1 动态细节（≤5 条，最近更新）
  L2 全量归档（不注入；RecallProfile search/list）
```

## 三、数据模型 `~/.jarvis/memory/persona.json`

```json
{
  "version": 1,
  "built_at": 1794895825.24,
  "source_count": 23,
  "source_max_updated_at": 1794895800.0,
  "token_limit": 800,
  "token_count": 712,
  "sections": [
    {
      "key": "identity",
      "title": "他是谁",
      "bullets": [
        {"text": "杭州，AI 应用开发者；偏好本地优先、轻依赖", "sources": ["ent_6c5668a2"]}
      ]
    }
  ],
  "dropped_bullets": 2
}
```

### 配套状态 `~/.jarvis/memory/persona_state.json`

```json
{"pending": true, "changed_at": 1794895825.24, "last_attempt_at": 0.0}
```

- 提炼 `advance()` → `pending=true`（只写这个小文件，不动人设卡）；
- 正常退出重建 / 手动重建 / 启动补偿成功 → `pending=false`；
- **无卡且有有效条目** → 启动时首建一次（这是“首次生成”，不属于“重建”）；
- 与 `persona.json` 一样原子写 + 文件锁；写失败不影响对话。

### 固定 section 与顺序（顺序固定 → 缓存稳定）

| key | 标题 | 内容 | 裁剪优先级 |
|---|---|---|---|
| `identity` | 他是谁 | 身份/职业/城市/背景 | 最后裁 |
| `communication` | 怎么跟他说话 | 语言、长度、语气、称呼 | 最后裁 |
| `work_style` | 工作方式 | 工具偏好、协作习惯、节奏 | 4 |
| `boundaries` | 雷区 | 明确不喜欢/禁止的做法 | 3 |
| `rhythm` | 生活节奏 | 作息、周末、精力 | 2 |
| `relationships` | 在意的人 | 家人/合作伙伴（不记隐私细节） | 5 |
| `current_focus` | 当前重点 | 项目、目标、近期安排 | 最先裁 |

### 约束

- 每节 0–3 条；每条 ≤100 字；第三人称中文；
- 每条 `bullets[].sources` 必须非空，且 id 存在于构建快照的有效条目中；
- 总 token 按现有预算口径（`pseudo`/`weighted`）计量，≤ `profile_persona_token_limit`；
- 无有效 section（尤其 `identity` 为空）→ 本次构建失败，保留旧卡。

## 四、生成器 `agent/core/memory/profile_persona.py`（新增，约 260 行）

### 4.1 公共 API

```python
PERSONA_FILE = "persona.json"
PERSONA_MAX_INPUT_ENTRIES = 240  # 送进 LLM 的条目上限（成本闸）
PERSONA_MAX_BULLET_CHARS = 100
PERSONA_DYNAMIC_MAX = 5          # L1 最近变化条数上限

def load_persona() -> Persona | None: ...
def build_persona(entries, settings, previous=None) -> Persona | None: ...
def maybe_rebuild_async(settings, *, force=False) -> bool: ...
def rebuild_async(settings) -> bool: ...          # /memory persona rebuild
```

### 4.2 输入采样（有界，防库变大后失控）

1. 全部 `permanent`（身份/关系/愿望，必须进人设）；
2. 每个类别按 `entry_weight` 取 top 15；
3. 最近 50 条 `updated_at` 变更；
4. 上一版人设卡（只作为“保持连续性”的参考，不作为事实来源）；
5. 合并去重后截断到 `PERSONA_MAX_INPUT_ENTRIES`。

每行格式：`[ent_id] [类别] 内容（置信度 0.xx，提及 N 次，更新于 YYYY-MM-DD）`。

### 4.3 LLM 契约

- 复用提炼模型（`[memory.refine]`）：在 `profile_refiner.py` 抽一个公共
  `call_refine_model(prompt: str, settings) -> str`，两处共用，避免复制 provider 解析链；
- temperature 0.2；max_tokens 约 1200；
- system：`你是用户画像的总结器。只根据给定条目写“这个人是谁”，不做推断，不写条目外的信息。`
- user：条目清单 + 上一版人设卡 + 输出 JSON schema + 规则；
- 输出严格 JSON（复用 `profile_json.parse_json_object`）。

### 4.4 接地校验（防幻觉固化）

1. 解析 sections；未知 key 丢弃，按固定顺序重排；
2. 每条 bullet：`sources` 必须命中快照 id；否则丢弃；
3. 文本为空/超长/重复 → 丢弃；
4. 每节最多 3 条；
5. 若 `identity` 为空 → 整次构建失败（保留旧卡）；
6. 记录 `dropped_bullets` 供诊断。

### 4.5 长度裁剪

按上表“裁剪优先级”从最先裁的开始删 bullet（每节从末尾删），直到 token ≤ 上限；
仍超限 → 构建失败，保留旧卡。

### 4.6 失败与原子写

- 任何异常：记 diag，返回 False，旧卡保持可用；
- 成功：临时文件 + `file_lock` + `os.replace` 原子写；
- 同一进程内用模块锁防止并发重建；
- 读坏/版本不符：当无卡处理（退回旧注入路径）。

## 五、注入侧 `agent/prompts/system.py`

### 5.1 有卡时

```markdown
# 用户画像（常驻人设）
## 他是谁
- ...
## 怎么跟他说话
- ...

## 最近变化
- [schedule] 习惯熬夜，常到凌晨两点（置信度 0.85）
- ...（最多 5 条，按 updated_at 倒序）
```

- L0 在前、L1 在后；L0 顺序固定，同一进程内逐字节稳定；
- 追加一句：`（画像库共 N 条；更早/更细的用 RecallProfile：问具体事实用 query，问“你了解我多少”用 mode="list" 分页读全库。）`
- 总预算：persona ≤800 + L1 ≤5×~20 ≈ 900 tokens。
- **L1 是什么**：人设卡是“综合判断”，会滞后于刚聊的事；L1 是最近变化的**原文条目**（≤5 条），
  只为让“刚说的事”不被漏掉（如“最近在折腾 P6”“下周五出差”）。设 0 就只注入人设卡。
- **与长期记忆的优先级见 §六**：人设卡开头声明“自动观察，冲突时以长期记忆为准”。

### 5.2 无卡/关闭/损坏时

完全退回现有路径：`profile_inject_core_max`（默认 12 条）+ 现有提示，不改变 P1c 行为。

### 5.3 缓存与刷新

- `_PROFILE_CACHE` 仍按进程缓存；
- 人设卡**只在退出时重建**；正常退出后进程结束，下次启动自然读到新卡；
- `/memory persona rebuild` 是显式操作，完成后调用 `reload_profile_cache()`；
- 新建/切会话**不重建、也不重载人设卡**；本会话靠 L1 最近变化补新消息。

## 六、与长期记忆（MEMORY.md）的边界与仲裁

> 用户口径（2026-11-18）：**MEMORY.md 是用户明确让它记的东西；画像是自动提炼的。两者必须分清。**

### 6.1 定位对比

| 维度 | 长期记忆 `MEMORY.md` | 画像记忆 `profile.json` / 人设卡 |
|---|---|---|
| 来源 | 用户显式要求（“记住/以后都/别忘了”），桌面面板可编辑 | 后台自动提炼 |
| 内容 | 指令、约定、项目事实、跨项目偏好 | 关于“这个人”的稳定特征 |
| 权威 | **高**（用户意志） | 低（观察与推断） |
| 作用域 | 用户级（跨项目）+ 项目级（当前仓库） | 全局（单用户） |
| 注入 | 原文全量（现状） | 人设卡 + 动态 + 按需检索 |
| 删除 | 用户手动编辑 | `/memory del`；衰减只降权不删 |

### 6.2 四类冲突（现状确实存在）

1. **重复**：同一偏好在两边各存一份（MEMORY.md 写“回答简短”，画像也提炼“偏好简短”）；
2. **矛盾**：MEMORY.md 说“主力用 DeepSeek”，画像后来记“改用 GLM”，两段同时注入；
3. **越权写入**：`MemoryUpdate` 当前描述允许模型“发现稳定偏好就写”，与“用户明确要求才写”不一致；
4. **删除不同步**：用户让忘掉某件事，只删了画像或只改了 MEMORY.md，另一边仍在注入。

当前注入顺序是：`MEMORY.md → KG 画像 → 本地画像 → 会话记忆`，但**没有任何一句仲裁文案**，
KG 段还自带“与本地画像冲突以本段为准”——三套来源缺少统一优先级。

### 6.3 仲裁规则（P6 必须落地）

#### 写入边界

- `MemoryUpdate` 只在**用户显式要求**时调用；删除工具描述里的“你发现一条会反复用到的稳定偏好…”
  主动写入条款（保留 `_MEMORY_SECTION` 提示词里“用户明确说记住”的口径）；
- 画像提炼遇到“用户明确要求记住”的句子 → **不再重复提炼为画像**（那属于 MEMORY.md）；
- 画像的 `project` 类别只记“长期个人方向/项目”，仓库约定、命令、踩坑 → 项目级 MEMORY.md。

#### 读取优先级（全系统唯一口径）

```
1. MEMORY.md（用户级 + 项目级；项目级更具体）——最高
2. KG 画像（若启用）——画像内部的事实源
3. 本地画像 / 人设卡 —— 自动观察
4. 会话记忆（SESSION_MEMORY.md）——仅线索，不参与仲裁
```

注入文案：长期记忆段末尾加一句
**“以上是用户明确要求记住的内容；与后续画像记忆冲突时，以本段为准。”**

#### 写入去重（防止同一事实两处存）

- 画像落盘前，读取用户级 + 项目级 MEMORY.md 的**条目行**（去掉标题/代码块/空行）；
- 候选与任一行相似度 ≥0.85 → **跳过不写**，遥测计数 `memory_md_skip`；
- 画像更新（update）同理：新值若与 MEMORY.md 同义，也不写；
- **画像永不自动改写 MEMORY.md**，哪怕它认为 MEMORY.md 过时。

#### 矛盾提示（v1 只提示，不自动改）

- 候选与 MEMORY.md 行相似度在 0.6–0.85 且内容不同 → 记 `conflict` 计数；
- `/memory` 头部提示“有 N 条可能与长期记忆不一致，建议核对”；
- 不自动删除/覆盖任何一方；P7 再考虑“一键同步到长期记忆”。

#### 忘记语义

- `/memory del` 只删画像；回复里明确提示“长期记忆需到 设置 → 长期记忆 删除”；
- P7 候选：`/forget <关键词>` 同时列出两处匹配项，让用户勾选删除。

#### KG 画像边界

- KG 段存在时，**P6 人设卡不重复注入**（KG 已承担常驻画像）；人设卡仍生成，作为 KG 不可用时的回退；
- KG 段“以本段为准”仅限**画像内部**；跨层仍以 MEMORY.md 最高。

### 6.4 注入布局（覆盖 §五）

```
base → security
     → MEMORY.md（用户显式，最高）
     → KG 画像（可选）
     → 人设卡 + L1 动态（自动观察，开头声明“冲突以长期记忆为准”）
     → 会话记忆（线索）
```

### 6.5 MEMORY.md 的体量风险（P6 不动，P7 候选）

- 单文件上限 10 万字符，且**全量注入**；它可能比画像大一个数量级；
- P7 候选：只注入标题索引 + 前 200 行（Anthropic 的 `MEMORY.md` 模式），正文按需读。

### 6.6 对现有代码的改动点

| 文件 | 改动 |
|---|---|
| `agent/tools/memory/memory_update.py` | 描述收紧为“仅用户显式请求” |
| `agent/core/memory/profile_resolve.py` | 抽取规则：显式“记住”不再提炼；`project` 类别限“个人长期方向” |
| `agent/core/memory/profile_refiner.py` | 读取 MEMORY.md 条目行，传给 `apply_decisions` |
| `agent/core/memory/store.py` | 新增 `load_memory_lines(workdir)`（只读，去标题/代码块） |
| `agent/prompts/system.py` | MEMORY 段加仲裁句；KG 存在时不注入人设卡 |

## 七、触发与调度（只认退出时机）

人设卡重建与提炼**共用退出时机**，但不是同一件事：提炼负责“从对话抽条目”，重建负责
“把条目压成人设卡”。重建只在**条目有变化（pending）**时跑，一次 LLM 调用。

| 触发 | 时机 | 条件/行为 |
|---|---|---|
| **正常退出**（`/exit`、关桌面） | 先等提炼（≤15s），再等重建（≤15s） | pending 时重建；成功后清 pending |
| **非正常退出**（崩溃/强杀） | 进程内无法执行 | **pending 保留**，下次启动补建 |
| 下次启动 | **仅当 pending=true**（异常退出或重建失败的补偿） | 成功清 pending；正常启动不重建 |
| 手动 | `/memory persona rebuild` | 无条件后台重建 |
| **新建 / 切会话** | **不重建** | 继续用上一版卡；本会话靠 L1 补最近变化 |

**pending 从哪来**：任一条目被提炼写入（`advance()`）后置 `pending=true`（写 `persona_state.json`）；
正常退出重建成功、手动重建成功、启动补偿成功 → `pending=false`；首次无卡且有有效条目 → 启动首建。

**指纹**：`(有效条目数, max(updated_at))`；保存为 `source_count` / `source_max_updated_at`，
用于判断 pending 是否真的对应新内容（避免空转）。

**不做**：不做“≥5 条变更”、不做“≥6 小时”、不做每日定时重建、不于新建/切会话时重建。

**顺序**：先提炼落盘，再重建。退出时先等提炼、再等重建；任一步超时，pending 不丢，
下次启动补（不丢，只晚）。

**失败**：不设定时重试；下次退出/启动/手动再试。

## 八、`RecallProfile` 双模式

### 8.1 入参

| 参数 | search（默认） | list |
|---|---|---|
| `query` | 必填 | 可省略 |
| `category` | 可选 | 可选 |
| `top_k` | 1–20，默认 8 | — |
| `offset` | — | 默认 0 |
| `limit` | — | 1–50，默认 20 |

`required: []`；`validate_input` 按 mode 校验（search 无 query 报错，list 忽略 query）。

### 8.2 list 输出

```
画像全库（共 23 条，第 1–20 条，按类别+权重排序）
分类：身份 1 / 作息 3 / 偏好 5 / …
- [作息] 习惯凌晨两点睡（置信度 0.85，提及 2 次）
- [偏好] 主力使用 GLM（置信度 0.85，提及 1 次）
- …（休眠条目追加「休眠」标记）
下一批：mode="list", offset=20
```

- 排序：`PROFILE_CATEGORIES` 顺序 → 权重降序 → `updated_at` 降序（分页稳定）；
- 包含休眠条目（用户问“你了解多少”时应该看到全部）；
- `offset >= total` → `已到末尾，共 N 条`；
- `max_result_chars` 从 1500 提到 3000（20 条约 700–900 字，留余量）。

### 8.3 工具描述与提示词

- 描述写清：**“用户问『你了解我多少/你都记得我什么』→ 必须用 list 分页读完再总结，不要只根据注入的人设卡回答”**；
- 注入侧提示同一口径（见 §5.1）。

## 九、命令与遥测

| 命令 | 行为 |
|---|---|
| `/memory persona` | 显示状态（开关、构建时间、来源条数、token 数）+ 完整人设卡 |
| `/memory persona rebuild` | 后台重建；提示“已开始，稍后用 /memory persona 查看” |
| `/memory` | 头部增加一行 `人设卡：800 tokens · 23 条来源 · 3 小时前`（无卡则显示“未构建”） |

遥测：`persona.json` 的 `built_at / source_count / token_count / dropped_bullets`；
失败写 diag（`memory.profile_persona`），不进对话回复。

## 十、配置

```toml
profile_persona_enabled = true       # 常驻人设卡（P6）
profile_persona_token_limit = 800    # 人设卡 token 上限
```

- 复用 `profile_inject_core_max` 作为 L1 上限（persona 启用时取 `min(core_max, 5)`；关闭/无卡时仍是 12）；
- `profile_refine_model` 同时用于人设卡重建（不新增模型配置）；
- 其余阈值（240 输入 / 100 字 / 每节 3 条）作为模块常量，不开放配置；**不设任何频率/间隔参数**。

## 十一、失败模式与降级

| 失败 | 行为 |
|---|---|
| 无 LLM / 重建报错 | 保留旧卡；diag；下次时机（启动/切换/退出/手动）重试 |
| 输出非法 JSON / 全部 bullet 无源 | 构建失败，保留旧卡 |
| `identity` 为空 | 构建失败，保留旧卡 |
| 超 token 且裁不完 | 构建失败，保留旧卡 |
| 磁盘写失败 | 内存不更新；下次重试 |
| 卡片损坏 | 当无卡，回退 P1c 核心注入 |
| persona 关闭 | 立即回退 P1c；`persona.json` 保留不删 |

## 十二、测试矩阵

### 单元（新增 `tests/core/test_profile_persona.py`，约 25 例）

1. `load_persona`：正常/坏 JSON/版本不符/缺字段；
2. 接地：未知 source 丢弃、重复文本去重、超长丢弃、每节 ≤3；
3. `identity` 为空 → 构建失败；
4. token 裁剪顺序（current_focus 先裁、identity 最后）；
5. 触发时机：正常退出重建；新建/切会话**不**重建；异常退出 pending 保留、下次启动补建；手动无条件；
6. 失败保留旧卡；原子写；并发锁；
7. 输入采样：permanent 全带、每类 top 15、最近 50、截断 240；
8. **跨记忆去重**：MEMORY.md 行解析（去标题/代码块）；≥0.85 跳过并计数；
   0.6–0.85 记 conflict；画像写入后 MEMORY.md 未被修改。

### 集成（`tests/core/test_profile_memory.py` 增补）

1. fake provider 返回人设 JSON → 构建 → `/memory persona` 显示；
2. 注入顺序：L0 → L1 → 提示；无卡回退 12 条；
3. 人设卡变化后 `reload_profile_cache` 生效；
4. refine 成功后按去抖触发重建；
5. 仲裁文案：注入提示含“以长期记忆为准”；
6. KG 段存在时：不再注入人设卡（避免两段画像）。

### 工具（`tests/tools/test_recall_profile.py`）

1. list 分页：23 条 → 2 页（20+3），无重复/遗漏；
2. 排序稳定、category 过滤、休眠标记；
3. offset 越界 → 末尾提示；
4. search 仍要求 query；非法 mode 报错；
5. `max_result_chars` 覆盖 20 条。

### Golden（新增 `tests/memory/test_profile_persona_golden.py`）

- 固定 30 条画像条目（覆盖 7 类）+ 10 个“你了解我多少”式问题；
- 仅凭生成的人设卡回答，关键词覆盖 ≥80%；
- 人设卡 token ≤ 上限；
- 同一输入两次构建，section 顺序与核心事实稳定。

## 十三、验收标准

- [ ] **重建时机**：只在正常退出、异常退出后的启动补偿、手动时重建；新建/切会话不重建；无任何时间/频率参数；无变化不重建；
- [ ] 人设卡 ≤ `profile_persona_token_limit`（默认 800）；
- [ ] 单次重建 1 次 LLM 调用；失败率不影响对话；
- [ ] 10 个元问题：L0 覆盖 ≥80% 关键事实；
- [ ] `list` 30 条 → 2 页，无重复/遗漏；
- [ ] **跨记忆**：显式“记住 X”后画像不新增 X；MEMORY.md 已有 X 时同义候选跳过；
- [ ] **跨记忆**：两边矛盾不自动改任何一方，`/memory` 有冲突提示；
- [ ] **跨记忆**：注入文案含“以长期记忆为准”；KG 启用时不出现两段画像；
- [ ] 注入 token：相对全量画像（200 条≈4k）下降 ≥70%；相对当前 12 条略增但覆盖度提升；
- [ ] 全量回归通过；链接门 0 死链；`Settings` 259 → 261（+2 键）；
- [ ] 关掉 `profile_persona_enabled` 能 1 秒回退到 P1c 行为。

## 十四、实施切片与文件清单

| 切片 | 内容 | 文件（估行） |
|---|---|---|
| P6a | **跨记忆边界**：MemoryUpdate 收紧、提炼跳过显式记忆、MEMORY.md 行去重、仲裁句 | `memory_update.py`（+4）、`profile_resolve.py`（+30）、`profile_refiner.py`（+15）、`store.py`（+35）、测试（+60） |
| P6b | `RecallProfile` list 模式 + 测试 | `tools/memory/recall_profile.py`（+80）、`tests/tools/test_recall_profile.py`（+80） |
| P6c | 人设卡数据模型/构建/接地/降级 + 单测 | 新增 `core/memory/profile_persona.py`（260）、`tests/core/test_profile_persona.py`（240） |
| P6d | 注入接线 + 退出重建 + 命令 | `prompts/system.py`（+50）、`commands/handlers/session_commands.py`（+60）、`session_manager.py`（退出钩子，+10）、`main.py`/`serve/engine/engine.py`（退出等待，+10） |
| P6e | golden 集 + token 实测 + 退出重建验证 | 新增 `tests/memory/test_profile_persona_golden.py`（150） |
| P6f | 配置键 + 模板 + 文档 + 索引 | `settings.py`/`settings_domains.py`（+4）、两份 `example.toml`（+4）、文档 5 处 |

总计新增约 800 行、修改约 300 行；单文件均 <900 行。

## 十五、文档同步清单

- `docs/architecture/09-记忆与压缩.md`：新增「常驻人设卡（P6）」小节、数据流、配置块；
- `docs/guide/concepts.md`：一句“常驻人设 + 按需细节”；
- `docs/guide/commands.md`：`/memory persona`、`/memory persona rebuild`；
- `docs/research/always-on-profile-memory.md`：状态从“待立 P6”改为“已立 P6”，链接本文；
- `docs/plans/README.md`：画像分类 4→5、合计 20→21；
- `docs/log/CHANGELOG.md`：P6 条目（若并行改动未提交，单独一批提交）；
- `docs/blueprint/BLUEPRINT.md`：关联文档表补本文；
- `docs/fixlogs/`：若实机暴露问题，按 `bugfix-review.md` 补复盘。

## 十六、风险与回滚

| 风险 | 对策 |
|---|---|
| 摘要失真/固化错误 | 接地校验 + 保留原文 + 可重建/可查看 |
| 人设卡漂移 | 带上一版做连续性约束；节顺序固定 |
| 缓存失效 | 只在退出时重建；自动重建不刷当前会话 |
| 成本失控 | 输入 240 条上限、输出 1200 tokens；只在退出时重建，失败下次时机再试 |
| 长会话一直用旧卡 | 人设卡只在退出时更新；会话内靠 L1 补最近变化；下次启动生效 |
| 用户不喜欢人设卡 | `profile_persona_enabled=false` 立即回退，数据保留 |

## 十七、待拍板（默认值）

1. **人设卡默认开启**：是（验收通过后 `true`；P6c–P6d 开发期 `false`）；
2. **L1 最近变化条数**：5（0 = 只注入人设卡）；
3. **是否包含“雷区”节**：是；
4. **`/memory` 默认是否打印完整卡**：否，只打印摘要；完整卡用 `/memory persona`；
5. **MemoryUpdate 收紧为“仅用户显式请求”**：是；
6. **画像与 MEMORY.md 重复时写入跳过**：是（≥0.85）；
7. **KG 启用时人设卡不重复注入**：是。

## 十八、实施记录（2026-11-18）

全部切片已落地，测试与验证结果：

| 切片 | 落地文件 | 测试 |
|---|---|---|
| P6a | `store.load_memory_lines`、`profile_resolve.apply_decisions(memory_lines=)`、`MemoryUpdate` 描述收紧、`_MEMORY_SECTION` 仲裁句 | `test_profile_resolve` 跨记忆 4 例 + `test_profile_memory` 行解析 2 例 |
| P6b | `RecallProfile` `mode=list`、`offset/limit`、`max_result_chars=3000` | `test_recall_profile` list 7 例 |
| P6c | `core/memory/profile_persona.py`（生成/接地/裁剪/pending/补偿） | `test_profile_persona` 13 例 |
| P6d | `system.py` 人设卡 + L1 注入；`/memory persona`；`main`/`engine` 退出重建；启动补偿 | `test_profile_memory` 注入/命令 2 例 |
| P6e | 30 条 fixture + 10 个关键事实 golden | `test_profile_persona_golden` 4 例 |
| P6f | 2 个配置键 + 两份模板 + README/09/concepts/commands/plans 索引 | 全量 **5377 passed / 2 skipped** |

实现偏差：

- 按用户口径，重建只在**正常退出 / 异常退出后的启动补偿 / 手动**；新建/切会话不重建；
- 人设卡默认 `true`（实现完成即验收口径）；关闭开关可 1 秒回退 P1c；
- `CHANGELOG` 因工作树存在并行驾驶舱改动未提交而暂缓，待该批提交后补一行。
