# 自配置层第二批扩张（第一批：模型与上下文）工程方案

> 状态：**设计定稿，待开发**
> 基线：`4598cef`（2.5.4 + yolo 按目标域放行）
> 目标：把「模型与上下文」这一簇字段纳入自配置真源表，并在 yolo 下把它们统一置于「写入必点头」之下

---

## 1. 背景与现状

`agent/config/config_specs.py` 是自配置的**唯一真源**，`ConfigTool` 把 `tier` 直通权限管线
（`forbidden` → DENY、`confirm` → ASK、`auto` → ALLOW），不自造第二套确认流程。

当前实测口径：

| 口径 | 数量 |
|---|---|
| `Settings` 字段总数 | **250** |
| 模型可改（进表） | **63**（auto 29 / confirm 33 / forbidden 1） |
| **表外、根本没有入口** | **187** |

所谓「表外无入口」不是被拒绝，而是 `ConfigSet` 里没有这个键——`apply_config_patch` 只认表里的
协议键。所以「让 jarvis 帮我配几乎全部配置」这件事，**主要工作量在把表外字段纳入真源表**，
而不是调权限分级。

---

## 2. 目标与边界

### 2.1 目标

1. 把「模型与上下文」簇的 12 个字段纳入真源表（含类型、范围、枚举校验、`apply_mode`）。
2. 配置写入按 **「模式 × 分级」** 判定（见 §3.1）：yolo 下这 12 项**不设限**，其余模式
   **一律要用户点头**，`forbidden` 任何模式都拒绝。
3. 新增 `apply_mode = llm`：换模型/换 provider 之后真的生效，而不是"看起来改了"。
4. 为后续 4 批（工具与扩展、维护与清理、感知与日程、杂项）趟通流程与测试范式。

### 2.2 非目标（本轮明确不做）

- **不碰权限与凭据**：`permissions_file`、`permission_mode`、`permissions_yolo_guard_roots`、
  `permissions_yolo_hard_deny`、`api_key` 系列、`bridge_token`、`sandbox_*`、`log_events`
  —— 保持 DENY，**连点头都不收**。理由见 §3.2。
- 不开 `last_model`：它是运行时回填状态（模型切换后写入），不是用户意图，开了会和"当前模型"
  打架。
- 不做配置界面：桌面面板与 `/config` 的既有白名单保持不动，本表仍是第三处独立真源。
- 不改 `Settings` 的 250 个字段本身，只加"可改描述"。

---

## 3. 语义变更

### 3.1 按「模式 × 分级」二维判定

用户 2026-11 定稿的语义（本方案最初写成"yolo 下把 auto 抬到 ASK"，方向相反，已作废）：

| 模式 | `auto` | `confirm` | `forbidden` |
|---|---|---|---|
| **yolo** | **ALLOW** | **ALLOW** | **DENY** |
| default | ASK | ASK | DENY |
| accept_edits | ASK | ASK | DENY |
| plan | DENY | DENY | DENY |

一句话：**yolo 下不设限（锁死清单除外）；其他模式想改配置就要问一句；plan 是只读，直接不写。**

与旧口径的差别有两处，都要有测试盯住：

- `auto` 在 **非 yolo** 模式从 ALLOW 收紧为 ASK——用户的原话是"其他模式下 jarvis 想要修改配置文件
  才需要询问我的意见"，配置写入本身就是要点头的事，与 tier 无关；
- `confirm` 在 **yolo** 模式从 ASK 放宽为 ALLOW——tier 只在"要不要问"这一层有意���，yolo 下这层不存在。

实现位置：`ConfigTool.check_permissions`。它已经是分级直通权限管线的唯一出口，在这里按
`ctx.permission_mode` 做二维判定，**不引入新状态、不改权限管线本身**——否则连"改配置"以外的
动作也会被一起放宽。

### 3.2 保持 DENY 的清单（连点头都不收）

| 类别 | 字段 | 为什么不能开 |
|---|---|---|
| 权限规则本身 | `permissions_file`、`permission_mode` | 能换掉整套规则文件 / 把自己锁死在 yolo；**改完你翻不到依据** |
| yolo 保护域 | `permissions_yolo_guard_roots`、`permissions_yolo_hard_deny` | 能拆掉上一版刚做的 C 盘保护，等于上一版白做 |
| 凭据 | `api_key`、`dashscope_api_key`、`default_api_key`、`bridge_token` | 写入路径会被回执、日志、画像多处引用，泄漏面不可控 |
| 审计线 | `log_events` | 唯一的行为审计线，关了以后"发生过什么"无从追溯 |
| 沙箱 | `sandbox_*` 系列 | 沙箱自己不该由被沙箱的对象来改 |
| 修复动作 | `repair_disabled_actions` | 防止它把自己关掉的防护重新打开 |

**核心判断依据**：这些项的变更是**关于"护栏本身"的**，而审批发生在"护栏之内"。当任务卡住时，
jarvis 会合理地请求放宽自己——一轮点头就形成了自我授权的闭环。**用户点头能授权"做什么"，
但不该授权"拆掉监督机制"。**

**落地方式（两道闸，缺一不可）**：

- **`tier = forbidden`**（真源表内已有该键时）：决定它不进"模型可改清单"；
- **`FORBIDDEN_SPECS` / `FORBIDDEN_PREFIXES`**（名字字典）：决定给得出拒绝理由，且覆盖
  尚未进表的键——本轮新增 `permissions_` 前缀，把 `permissions_file` /
  `permissions_yolo_*` 整族锁死，避免将来扩表时"写进表里就顺手能改"。

`repair_disabled_actions` 本轮从 `confirm` 改为 `forbidden`：它记录哪些修复动作被用户关掉，
可改等于让它把自己被禁的能力重新打开。改档后仍须在字典里留理由（测试
`test_every_forbidden_spec_carries_a_reason` 会把关）。

---

## 4. 第一批字段清单（12 项）

### 4.0 先纠正一个事实：模型域**不在** `settings.toml` 里

`agent/config/models_config.py` 把模型域拆到了**同层的 `models.toml`**（`MODELS_FILE_NAME`），
`MODEL_TOP_KEYS` 已声明归它管的顶层键：`provider`、`api_format`、`model`、`last_model`、
`api_key`、`base_url`、`dashscope_api_key`、`max_tokens`、`temperature`、`enable_thinking`、
`thinking_budget`、`vendor_fallback`；section 前缀 `llm`（`[llm]` / `[llm.models]` /
`[llm.custom_models."名"]`）。**用户 `settings.toml` 里没有 `[llm]` 段**（实测为空）。

而 `ConfigSpec` 目前只有 `section`/`field`，隐含"都在 `settings.toml`"，落盘走 `save_section`。
所以本批必须先给它一个**存储维度**（S0，见 §12），否则写入会落到错误的文件里。

### 4.1 字段表

| # | 协议键 | **store** | section.field | kind | apply_mode | 校验 | label |
|---|---|---|---|---|---|---|---|
| 1 | `provider` | **models** | `[llm] provider` | enum | `llm` | 必须在 provider 注册表内 | 当前模型提供商 |
| 2 | `model` | **models** | `[llm] model` | str | `llm` | 非空；不得与 provider 明显错配（§6.2） | 当前模型 |
| 3 | `max_tokens` | **models** | `[llm] max_tokens` | int | `llm` | 256 ≤ n ≤ 384000 | 单次回复上限 |
| 4 | `temperature` | **models** | `[llm] temperature` | float | `live` | 0 ≤ n ≤ 2 | 采样温度 |
| 5 | `enable_thinking` | **models** | `[llm] enable_thinking` | bool | `llm` | — | 是否启用思考 |
| 6 | `thinking_budget` | **models** | `[llm] thinking_budget` | int | `llm` | 0 ≤ n ≤ 64000 | 思考 token 预算 |
| 7 | `models` | **models** | `[llm.models]` | alias_map | `llm` | 见 §6.3 | 模型别名表 |
| 8 | `custom_models` | **models** | `[llm.custom_models."名"]` | model_table | `llm` | 见 §6.4 | 自定义模型 |
| 9 | `thinking_effort` | settings | `[llm] thinking_effort` | enum | `llm` | low/medium/high | 思考强度 |
| 10 | `default_provider` | settings | top-level | enum | `llm` | 同 #1 | 默认提供商 |
| 11 | `max_iterations` | settings | top-level | int | `live` | 1 ≤ n ≤ 200 | 单轮最多几步工具 |
| 12 | `profile_refine_model` + `profile_refine_provider` | settings | `[profile] …` | str / enum | `llm` | 成对约束（§14） | 画像提炼用的模型 |

### 4.2 实施时收窄的范围（S2 实测结论）

S2 开工后按"写入落点必须真实存在"逐项核对，三项被移出本批：

| 字段 | 为什么移出 |
|---|---|
| `thinking_effort` | 既不在 `settings_domains` 的映射里，也不在 `settings.example.toml` 里——**没有持久化落点**，写进去没人读，属"改完以为生效" |
| `default_provider` | 同上：没有映射、没有示例、没有读取点 |
| `max_iterations` | 它是 settings.toml 的**顶层键**，而 `save_section("")` 会写出 `[]` 这种假 section（实测），顶层需要一个"写首个表之前"的写入器 |

另有两项落点已纠正：

- `profile_refine_model` / `profile_refine_provider` 实际住在 **`[memory]`** 表
  （`settings_domains` 的 `("memory", (("profile_refine_model", ...)))`），不是 `[profile]`；
  早期草稿写的 `[profile] refine_model` 是错的。

**S2 实际进表 10 项**（8 项 models + 2 项 memory）。三项延期的共同前提是"先给它们一个真源"，
不是权限或性能问题——这是本批最该记住的一条：**没有落点的配置项，开了就是骗人**。


| 12 | `profile_refine_model` + `profile_refine_provider` | `[profile] …` | 同名 | str/enum | `llm` | 空串=随主模型 | 画像提炼用的模型 |

> #12 是一次"成对字段"的试验：两个键必须同时改才自洽（只改 model 不改 provider 会出现
> "用着 A 家的模型名去请求 B 家"）。协议键拆成两个、校验里做**成对约束**。

---

## 5. 新增 `apply_mode = llm`

现有五种模式都不适用：

| 现有 | 语义 | 为什么不够 |
|---|---|---|
| `live` | 每次现读 | provider 客户端与 `QueryLoop` 是**装配快照**，改 attr 不重建就还是老模型 |
| `policy` | 重建打扰预算单例 | 只针对 notify_budget |
| `percept` | 重装配感知总线 | 只针对感知源 |
| `reschedule` | 重注册调度任务 | 只针对调度 |
| `restart` | 只落盘（不假装生效） | 换模型是高频操作，要求重启不可接受 |

新增 `llm`：落盘后调用 `rebuild_llm_stack(settings)`——重建 provider、重建 QueryLoop 的
model/provider 绑定、清 `_PROFILE_CACHE`/能力段落缓存（模型能力会变，视觉/思考段落必须重拼）。
回执必须写清"已生效于下一轮请求"，而不是"已生效"。

**判据**：改完立刻发一条消息，`/cost` 的「模型」行应显示新值；否则视为未生效（有测试盯这条）。

---

## 6. 值形态与校验

### 6.1 统一入口

沿用 `parse_cli_value(spec, raw)` 的思路，为三种新 kind 扩分支；**所有校验前置**
（`apply_config_patch` 第 ① 步整批校验干净再写），不做事后回滚。

### 6.2 `provider` × `model` 的错配校验

只能做**弱校验**：`model` 必须非空、不含空白；若 `model` 明显属于已知另一家（如 `glm-` 前缀
配 `provider=deepseek`），拒绝并给出正确组合。不做联网探测——配置写入不该依赖网络可用性。

### 6.3 `models`（别名表，dict[str,str]）

输入形态：`"qwen3.8-max=通义千问 3.8 Max;..."`（分号分隔、等号连接），空串表示清空。
落盘为 `[llm.models]` 表的键值对；写入时**整段替换**而非增量合并——增量合并会产生
"删不掉的旧别名"，而 TOML 没有删除语义。

### 6.4 `custom_models`（dict[str, dict]）

结构复杂（`name/provider_type/model_type/base_url/...`），本轮只支持**增删改单个模型**：
`"add <name> provider_type=openai model_type=text"`、`"remove <name>"`。
一次性整表替换不支持——那是手工编辑 TOML 的领域。

### 6.5 未知键

`ConfigSet` 对表外键**继续报"未知配置项"并列出最接近的三个键**（编辑距离），
不静默忽略。这条是 §2 目标的反面守门：表扩张是显式的，不能因为"键存在"就顺带能改。

---

## 7. 工程实践（必须落地的细节）

1. **真源唯一**：字段的 label/hint/校验/apply_mode 只写在 `CONFIG_SPECS`。桌面面板、`/config`、
   `ConfigTool` 三者从它派生或引用，不得各写一份。
2. **分级 × 模式即权限**：`tier` 与 `ctx.permission_mode` 在 `ConfigTool.check_permissions`
   一处做二维判定（§3.1），只产出 ALLOW / ASK / DENY 交给既有管线，**不新增确认流程、
   不改权限管线本身**。两条禁止来源（`tier=forbidden` 与名字字典）都必须查——只查一道，
   扩表时就会出现"写进了表、却是禁止项"的漏洞（2026-11 首刷已踩）。
3. **forbidden 兜底**：`apply_config_patch` 里的 `forbidden_reason()` 保留——即使权限层被绕过，
   凭据与权限类键也写不进去。
4. **写入必须留痕**：进副作用账本（`entry=model`），`/undo` 能撤回；回执写清
   "改了什么 / 什么时候生效 / 怎么撤"。
5. **不假装生效**：`apply_mode` 判错的后果是"用户以为改了其实没有"，比不做更糟（`policy`
   那次踩过：`_budget_singleton` 懒建，不重建就只落盘）。
6. **行数预算**：`config_specs.py` 现 **799 行**（Python `splitlines()` 实测），本批 +12 项约
   +140 行 → **触 900 行门禁**。

   方案修正（初稿写错）：初稿说"先把 llm 段的 spec 拆出去"——**现有 63 项里一个 llm 段的
   都没有**（模型/提供商从来不在表内），没东西可搬。正确做法是**反向**：
   新建 `config_specs_llm.py` 承载**本批新增的 12 项**，`config_specs.py` 用
   `CONFIG_SPECS = _BASE_SPECS + LLM_SPECS` 拼接，导出名与顺序（先表达层、后确认层）
   保持不变。这样门禁天然过，且后续批次可照同一形状继续拆（tools/maintenance/...）。

   **不允许**为过门禁压缩注释或删 hint——那会把"给模型看的人话"变成牺牲品，而这层存在的
   理由就是"让模型解释得清自己在改什么"。
7. **测试矩阵**（每项都要有）：
   - 正向：合法值写入 → 运行时 attr 变 → 落盘 TOML 正确 →
     `/config list` 显示当前值
   - 反向：越界/类型错/非法枚举 → 拒绝且**不改动任何字节**
   - 空值：空串清空（针对 `profile_refine_model`）
   - 模式矩阵：`auto` / `confirm` 在 **yolo 下必须 ALLOW**、在 **default 下必须 ASK**、
     在 **plan 下必须 DENY**（三条都要，防止只改一个方向改出过度收紧或过度放开）
   - DENY 清单：八类键在 yolo 下仍被拒，且理由是"不许改"而不是"不支持"；
     `tier=forbidden` 的项走另一条禁止路径，yolo 下同样要拦（两条路径各一个用例）
   - 未知键：拼错的键被拒且给出最接近建议
   - 生效：`llm` 类改完后 `/cost` 的模型行立刻反映新值
8. **CI 门禁**：`test_catalog_covers_every_settings_field`（每个 Settings 字段都要有 doc）与
   `test_every_entry_has_doc` 都会对新字段亮红——新字段必须同时补 catalog 文档与
   `settings.example.toml` 行尾注释（`_example_docs` 从那里捞说明）。

---

## 8. 分批与验收

| 批次 | 范围 | 状态 |
|---|---|---|
| **1** | 模型与上下文（本篇 12 项） | **设计定稿，待开发** |
| 2 | 工具与扩展开关（MCP/LSP/技能/插件/延迟加载/浏览器/GUI/摄像头） | 待排期 |
| 3 | 维护与清理（clean/quarantine/repair/阈值/disk） | 待排期 |
| 4 | 感知与日程（percept 逐源、schedule、deadline、briefing 细项） | 待排期 |
| 5 | 杂项 | 待排期 |

每批验收同一条尺子：**进表 → 校验 → apply_mode 正确 → 测试全绿 → 全量不回归 → 索引同步**。

---

## 9. 风险与已知局限

1. **弱校验挡不住"能跑但很怪"的组合**（如 `max_tokens` 设超大导致频繁 400）。这类只能靠
   回执里的显式提醒，不能靠配置层猜。
2. **`custom_models` 的后端可能有独立校验**，配置层放行不代表端点接受；回执必须区分
   "写入成功"和"可用"。
3. **`llm` 重建失败**（新 provider 无 key、网络不可用）时：**保留旧栈继续可对话**，
   并把失败原因回报给模型与用户——不许落进"配置写成功但会话坏掉"的状态。
4. **反向的审批疲劳（已按新口径消解，保留记录）**：最初设计是"yolo 下每次改配置都问一句"，
   高频调参会很烦；用户 2026-11 改为 yolo 下不设限，这一条自然消失。但**非 yolo 模式变成
   每次必问**——若将来觉得吵，缓解方向是把"同一轮内的多次调参"合并成一次审批（本轮不做）。

5. **yolo 下配置写入不留确认痕迹**：既然不问，出问题时就只剩副作用账本可查。所以
   `apply_config_patch` 的账本记录与回执**必须写全**（改了什么、是否已生效、怎么撤回），
   这是 yolo 放开之后唯一的事后依据。

---

## 10. 附：本批明确不开的字段及理由

| 字段 | 理由 |
|---|---|
| `last_model` | 运行时回填状态，不是用户意图；开了会与 `model` 冲突 |
| `bridge_token` | 凭据（跨设备桥接令牌），进 DENY |
| `tts_model` / `stt_model` / `realtime_model` | 语音域，属第 2/4 批 |
| `crawl_timeout_s` | 爬虫域，属第 2 批 |
| `sandbox_timeout` | 沙箱，进 DENY |
| `checkpoint_timeout_seconds` | 检查点域，属第 3 批 |
| `profile_inject_token_limit` / `profile_bridge_token_limit` | 画像注入预算，属第 4 批 |

---

# 第二部分：分阶段实施计划

> 用户 2026-11 定的规矩：**每个阶段都先把文档写到能照着敲，再写代码。**
> 本节把「阶段 → 改动点 → 出口条件」摊开，每阶段结束回填实测结果（含偏离说明）。

## 11. 阶段总览与出口条件

| 阶段 | 产出 | 出口条件（可判定） |
|---|---|---|
| **S0** | `ConfigSpec.choices` 字段 + 三种新 kind 的零值登记 | 现有 63 项构造不变；`tests/config` 全绿 |
| **S1** | 三种新 kind 的解析与校验 | 每 kind 三个用例：合法 / 非法 / 空值；非法时**不改动任何字节** |
| **S2** | `config_specs_llm.py` + 12 项进表 + 接线 | `ConfigList` 能看到 12 项；`config_specs.py` ≤ 900 行 |
| **S3** | `apply_mode = llm` 与宿主挂钩 | 改完模型后 `/cost` 的「模型」行立刻变；无钩子时回执写"重启后生效" |
| **S4** | `settings.example.toml` 行尾注释 + catalog 文档 | `test_every_entry_has_doc` / `test_catalog_covers_every_settings_field` 全绿 |
| **S5** | 索引同步 + CHANGELOG | `aoci check` 不再因本批文件亮红 |

**阶段间不可并行的点**：S2 依赖 S1（新 kind 不能进表却解析不了）；S3 依赖 S2（没有 llm 段字段
就没有要生效的东西）；S4 必须与 S2 同一次提交（示例注释缺了会让 doc 门禁在同一笔里红）。

### 11.1 进度回填（每阶段完成后更新此表）

| 阶段 | 状态 | 提交 | 实测 |
|---|---|---|---|
| S0 | ✅ 完成 | `a16a839` | `ConfigSpec` + `store`/`choices`；`_KIND_ZERO` 补 3 项；`tests/config` 484 |
| S1 | ✅ 完成 | 见本笔 | 三种 kind 解析+校验；**门禁提前触发**（见下），拆出 `config_kinds.py` |
| S2 | ✅ 完成（10/13 协议键） | 见本笔 | `config_specs_llm.py` 10 项 + `config_store.py` 分派；3 项延期见 §4.2 |
| S2c | ✅ 完成 | 见本笔 | `custom_models` 已接线：给 `upsert/remove_custom_model` 加了 `path` 形参 |
| S3 | ⬜ | — | — |
| S4 | ⬜ | — | — |
| S5 | ⬜ | — | — |

**S1 的偏离说明（原计划放 S2 才拆）**：S1 的解析代码直接进了 `config_specs.py`，
行数从 832 冲到 **1008**，越过 900 门禁、`test_no_agent_file_exceeds_limit` 当场红。
处置：把三种 kind 的解析/校验整体拆到 `agent/config/config_kinds.py`（175 行），主表
**867 行**（余量 33），并在主表顶部 `re-export` 保持调用方不变。

教训记在这里：**门禁是"加之前就该量"，不是"加完再看"**——832 → +176 一定会超，
这个算术在写第一行代码前就能做。后续 S2 的 12 项同理，直接进新模块。


---

## 12. S0 前置：`ConfigSpec` 加 `choices`，登记新 kind

```python
# config_specs.py
choices: tuple[str, ...] = ()   # kind == "enum" 时的可选值；其它 kind 忽略
store: str = "settings"         # "settings" | "models"；决定落盘去哪个文件（§4.0）
```

- `store` 默认 `"settings"`，**现有 63 项零改动**；只有模型域那 8 项显式写 `"models"`。
- 落盘分派（S2 实现）：`store == "settings"` → 现有 `save_section(...)`；
  `store == "models"` → `models_config` 的 `read_toml_text` / `upsert_top_scalar` /
  `upsert_table_block` / `write_toml_text`，路径用 `models_path_for(settings_path)`。
  **不新建写入器**——`models_config` 已经有外科式 upsert，并且处理了"与 settings.toml 同层"
  的项目级/用户级推导；另写一套必然漂移。
- 两个 store 的"读"不同：models 域要读 `models.toml` 再回落 `settings.toml`（现有分层口径），
  所以 `_current_value()` 也要按 store 分派，不能只 getattr。

- 校验处（S1）要求：`kind == "enum"` ⇒ `choices` 非空，否则视为表写错（启动即报，不留到运行时）。
- `_KIND_ZERO` 增加三项：`"enum": ""`、`"alias_map": {}`、`"model_table": {}`。
  这三项决定"读侧属性缺失时回退什么值"，缺了会在 `_current_value()` 抛 KeyError。
- **影响面**：现有 63 项全部靠默认值构造，零改动；新加一条测试
  `test_enum_specs_declare_choices` 防止将来有人写 `kind="enum"` 却忘了给可选值。
- **不预先注册进 `parse_cli_value`**：解析在 S1 做，S0 只改数据类与常量，保证 S0 单独提交也是绿的。

---

## 13. S1 值解析：三种 kind 的语法与失败语义

统一入口仍是 `parse_cli_value(spec, raw)`；**先 normalize → 再 validate → 全部通过才落盘**，
任一失败整批不动（沿用 `apply_config_patch` 第 ① 步的"整批校验"口径）。

### 13.1 `enum`

- 输入大小写不敏感（`HIGH` → `high`）；两侧空白裁掉。
- 允许别名表（`ALIAS = {"最高": "high", "最高档": "high", "lowest": "low"}`），别名只写在
  该字段的 spec 旁，不做一个全局大表——不同字段的取值集互不相同。
- 非法值：`ValidationError(f"{label} 只能是 {'/'.join(choices)}")`，**列出全部可选项**。

### 13.2 `alias_map`（`models`：别名 → 展示名）

- 语法：`名=展示名;名=展示名`（分号分隔，等号连接，允许中文）。
- 空串 = **清空整表**（`{}`）。
- 重复键：后者覆盖，但要在回执里写明"覆盖了 X"——静默覆盖会让人以为两份都生效了。
- 上限 `LIST_MAX_ITEMS`（50），超出拒绝并说明。
- 落盘：整段替换 `[llm.models]`（TOML 无删除语义，增量合并会留下删不掉的旧键）。

### 13.3 `model_table`（`custom_models`：单模型增删改）

- 语法：`add 名字 键=值 键=值` / `remove 名字`。**不支持整表替换**——那是手改 TOML 的领域。
- 字段白名单：`name`、`provider_type`、`model_type`、`base_url`、`api_key_env`、`max_tokens`；
  未知键直接拒绝并列出合法键（防拼错写出半残配置）。
- `remove` 不存在的名字：视为成功但回执写明"本就没有"（幂等），**不报错**——报错会诱导模型
  去改别的字段凑数。
- 落盘：读现有 `[llm.custom_models.*]` → 改目标条目 → 写回；不做全表重排。

### 13.4 失败语义（三种 kind 共用）

| 情形 | 行为 |
|---|---|
| 语法错 | 拒绝，回执给出**正确写法示例**（不是只说"格式错误"） |
| 值非法 | 拒绝，列出合法集 |
| 部分成功 | **不存在**：整批校验通过才写，一批里错一项就都不写 |

---

## 14. S2 12 项进表

- 新文件 `agent/config/config_specs_llm.py`，只放数据（`LLM_SPECS: tuple[ConfigSpec, ...]`）；
  `config_specs.py` 末尾 `CONFIG_SPECS = _BASE_SPECS + LLM_SPECS`，**导出名不变**。
- **两个 store 分派**：8 项 `store="models"`（§4.1），4 项 `store="settings"`。写入路径按
  `store` 分流到 `save_section` 或 `models_config` 的 upsert；**同一批 patch 允许跨文件**，
  但要么全成功要么全不写——跨文件的"半批"比全失败更糟（用户会以为只改了一半）。
- 顺序：`LLM_SPECS` 接在表达层之后、`ConfigList` 现有展示顺序不受影响（先 auto 后 confirm）。
- 表里已有的 `context_window`（confirm）**不重复加**——那是第一批之外的历史项。
- 成对字段 `profile_refine_model` × `profile_refine_provider`：校验里做成对约束，
  只给一个时按"另一半沿用主模型"处理并在回执说明，不报错（常见用法是只改 model）。
- 每项必须同时给：`label`、`hint`（一句"改它会发生什么"）、`kind`、范围/enum 校验、`apply_mode`、
  `store`。**hint 不许写"用于配置 XX"这类废话**——它的读者是模型，要用它向用户解释。

---

## 15. S3 `apply_mode = llm` 的契约

现有五模式都不适用（`live` 不重建装配快照、`policy`/`percept`/`reschedule` 各自专用于
打扰预算/感知总线/调度、`restart` 要求重启）。新增：

```python
APPLY_LLM = "llm"

# 宿主登记（CLI: agent/main.py；serve: agent/serve/engine/*）
register_apply_hook(APPLY_LLM, rebuild_llm_stack)
```

`rebuild_llm_stack()` 的**顺序与失败处理**（顺序错了会出现"配置成功但会话坏掉"）：

1. 用新 `settings` 构造 provider（key 缺失/网络不可用会在这里失败）；
2. 成功后才替换 `QueryLoop` 的 `_provider` / `_model`；
3. 清 `_PROFILE_CACHE` 与能力段缓存（模型能力变了，视觉/思考段必须重拼，否则提示词还写着旧能力）；
4. 返回 `(True, "已切换到 X / provider Y，下一轮请求生效")`。

**失败必须保留旧栈**：第 1 步失败就 return `(False, 原因)`，绝不先换再试——
这跟 yolo 重建那次同一个道理（先构造新的，成功才替换）。

**没有钩子的场景**（纯库调用、单元测试、以及还没接线的入口）：`run_apply_hooks` 天然返回
`(False, [])`，回执改写成「已保存，重启后生效」——**不许假装生效**，那是本层最招人恨的失误
（`policy` 模式诞生的原因）。

---

## 16. S4 示例与文档同步（必须与 S2 同一次提交）

- `agent/configs/settings.example.toml`：补 `store="settings"` 那 4 项的行 + 行尾注释。
- **`agent/configs/models.example.toml`**（`MODELS_EXAMPLE_FILE_NAME` 已存在）：补
  `store="models"` 那 8 项的行 + 行尾注释。**两个示例文件都要补**——`_example_docs()` 是
  `test_every_entry_has_doc` 的说明来源，只补一个会让另一半字段红。
- `agent/config/catalog.py`：段内字段说明（`[llm]` / `[profile]`）。
- `agent/config/settings_domains.py`：`[llm]` 段的子键映射（`FLAT_DOMAINS` / `SUB_TABLES`），
  以及 `LIST_FIELDS`（若新 kind 走数组语义）。
- 文档回填：把本文件 §4.1 表里"待开发"改成实测行数与偏离说明。

## 17. S5 收尾

- `aoci maintain` → 为新增文件 `config_specs_llm.py` 与改动条目写语义（不可套模板）。
- CHANGELOG 记入下一版（2.5.5 或并入 2.6.0，看是否还带别的特性）。
- 发布前跑：全量 `pytest`、`jarvis --doctor`、手工一条真实改模型流程（`/config set model …` → `/cost` 看模型行）。

---

## 18. 每阶段测试清单

| 阶段 | 必测用例 | 通过标准 |
|---|---|---|
| S0 | `test_enum_specs_declare_choices`；现有 config 全量 | 63 项构造不变，全绿 |
| S1 | 每 kind：合法 / 非法 / 空值；非法后文件字节不变 | 15 例左右 |
| S2 | 12 项逐项：能列、能写、能读回；`ConfigList` 计数 +12 | 表内 key 唯一、行数 ≤900 |
| S3 | 有钩子时改完立刻生效；无钩子时回执写"重启后生效"；构造失败保留旧栈 | 3 例，含"失败不换栈"的反证 |
| S4 | doc 门禁两条（`test_every_entry_has_doc`、catalog 覆盖） | 全绿 |
| S5 | `aoci check` 本批文件不亮红 | 欠账不增加 |

---

## 19. 后续四批的统一模板

第二批起直接套本文件 §11–§18 的骨架，只需替换：字段清单（§4）、新增 kind（若有）、
apply_mode（若有新装配点）、验收数。**不为每批新写一套结构**——模板漂移比遗漏更难发现。

@author aceFelix
