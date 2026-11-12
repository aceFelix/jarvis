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
2. 在 **yolo** 下，对这 12 项的写入**一律要用户点头**（把 `auto` 也升级为 ASK）。
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

| # | 协议键 | section.field | attr | kind | apply_mode | 校验 | label |
|---|---|---|---|---|---|---|---|
| 1 | `provider` | `[llm] provider` | `provider` | enum | `llm` | 必须在 provider 注册表内 | 当前模型提供商 |
| 2 | `model` | `[llm] model` | `model` | str | `llm` | 非空；不得与 provider 明显错配（见 §6.2） | 当前模型 |
| 3 | `default_provider` | `[llm] default_provider` | `default_provider` | enum | `llm` | 同 #1 | 默认提供商 |
| 4 | `max_tokens` | `[llm] max_tokens` | `max_tokens` | int | `llm` | 256 ≤ n ≤ 384000 | 单次回复上限 |
| 5 | `temperature` | `[llm] temperature` | `temperature` | float | `live` | 0 ≤ n ≤ 2 | 采样温度 |
| 6 | `enable_thinking` | `[llm] enable_thinking` | `enable_thinking` | bool | `llm` | — | 是否启用思考 |
| 7 | `thinking_budget` | `[llm] thinking_budget` | `thinking_budget` | int | `llm` | 0 ≤ n ≤ 64000 | 思考 token 预算 |
| 8 | `thinking_effort` | `[llm] thinking_effort` | `thinking_effort` | enum | `llm` | low/medium/high | 思考强度 |
| 9 | `max_iterations` | top-level | `max_iterations` | int | `live` | 1 ≤ n ≤ 200 | 单轮最多几步工具 |
| 10 | `models` | `[llm.models]` | `models` | alias_map | `llm` | 见 §6.3 | 模型别名表 |
| 11 | `custom_models` | `[llm.custom_models.*]` | `custom_models` | model_table | `llm` | 见 §6.4 | 自定义模型 |
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
2. **分级即权限**：`tier` 在 `ConfigTool.check_permissions` 直通 ASK/DENY，不新增确认流程。
3. **forbidden 兜底**：`apply_config_patch` 里的 `forbidden_reason()` 保留——即使权限层被绕过，
   凭据与权限类键也写不进去。
4. **写入必须留痕**：进副作用账本（`entry=model`），`/undo` 能撤回；回执写清
   "改了什么 / 什么时候生效 / 怎么撤"。
5. **不假装生效**：`apply_mode` 判错的后果是"用户以为改了其实没有"，比不做更糟（`policy`
   那次踩过：`_budget_singleton` 懒建，不重建就只落盘）。
6. **行数预算**：`config_specs.py` 现 799 行，本批 +12 项约 +140 行 → **触 900 行门禁**。
   方案：本批先把 `CONFIG_SPECS` 按域拆到 `config_specs_llm.py`（只搬位置不改口径），
   再新增字段。**不允许**为过门禁压缩注释或删 hint。
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
4. **审批疲劳**：yolo 下每次改配置都问一句，高频调参会很烦。缓解方向是把"同一轮内的多次
   调参"合并成一次审批（本轮不做，先记录）。

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

@author aceFelix
