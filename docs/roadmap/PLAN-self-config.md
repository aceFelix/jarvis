# 计划：让 jarvis 自己管配置（阶段 12 · 自配置与偏好沉淀）

> 立论数据（2026-10-26 实测盘点）：`Settings` 共 **190 个字段**，桌面面板能改的只有
> **22 个**（`[percept]` 15 + desktop 7），其余 **168 个只能手改 `settings.toml`**；
> 终端 `/config` 是**只读自查面板**（8 个分节），**模型侧一个配置工具都没有**。
> 也就是说：装完 jarvis 第一道坎仍然是"打开一个 TOML 文件"，这与"管家"该有的样子相反。

@author aceFelix

## 一、目标与非目标

**目标**（按优先级）

1. 用户只说话，jarvis 能把**体验类**配置改对并当场生效；
2. 每次变更**可解释、可撤销、进账本**，用户永远查得到"这值是谁改的"；
3. 缺口从"168 个字段要人填"压到"**只有 2–3 件必须问**"；
4. （第二阶段）从长期使用里沉淀偏好，让配置越用越贴人。

**非目标（明确不做，写进代码而不是只写在文档里）**

- 不让模型碰**安全类**配置（`sandbox` / `policy.*` 工具许可 / allow-deny / 各类凭据）；
- 不做**静默后台改配置**：所有自动变更必须是一条可见事件；
- 不让模型**每轮嗅探**"这句该不该改配置"（烧 token 且误触发），只走显式触发 + 低频提炼；
- 不让模型替代 `jarvis init`（首次接模型仍要人给 key，那是唯一必须由人做的决定）。

## 二、核心设计

### 2.1 单一真源：`agent/config/config_specs.py`

现在同一件事有四份实现（`percept_settings.PERCEPT_CONFIG_FIELDS`、
`desktop_settings.DESKTOP_SETTING_SPECS`、`configuration.md` 的表格、`settings.py` 的字段），
模型要"解释自己在改什么"就必须有一张**带人话的真源表**：

```python
@dataclass(frozen=True)
class ConfigSpec:
    key: str            # 协议键（模型/面板/终端统一用这个名字）
    section: str        # settings.toml 节名
    field: str          # TOML 字段名
    attr: str           # Settings 属性名
    kind: str           # bool / int / float / time / list
    label: str          # 中文标题（面板与播报共用）
    hint: str           # 一句人话：改它会发生什么
    tier: str           # auto / confirm / forbidden
    apply_mode: str     # live / rebuild_percept / reschedule / restart
    lo / hi / default
```

`percept_settings` / `desktop_settings` 保留现有对外 API（面板与 RPC 不动），
内部改为从这张表派生，避免一次性大改。

### 2.2 三类分级（硬编码，不给模型越界机会）

| tier | 判定标准 | 内容示例 | 模型行为 |
|---|---|---|---|
| `auto` | **纯表达层**，改坏也只是"声音小一点 / 少说一点"，且不涉及"何时能打断用户" | `tts.volume`、`tts.speech_rate`、`daemon.briefing_enabled/time`、`deadline.enabled/check_time`、`percept.*_enabled` 与各类名单 | 直接改 + 播报一句 |
| `confirm` | 影响**打断时机**或**工作范围**，模型单方面改等于给自己放宽护栏 | `policy.notify_budget.*`（`daily_max` / `quiet_hours` / `min_salience`）、`percept.enabled` 总开关、`workdir`、provider/model、`tasks.enabled`、`actions.undo_window_s`、MCP 增删 | 改之前必须**一句话确认**，不回 N 就不改 |
| `forbidden` | 权限与凭据本身 | `sandbox.*`、`policy` 许可与 allow/deny、`api_key*`、`email` 凭据、MCP server 命令行、`realtime` 端口绑定、`cli_anything` | **拒绝**，只告诉用户怎么改（`/init` 或手改） |

> 这里有一个必须纠偏的判断：`policy.notify_budget` **不能算体验类**。
> 它决定"什么时候能打断用户"，模型若能单方面调大 `daily_max` 或挪开 `quiet_hours`，
> 就等于自己给自己放宽了 W1 的护栏——与"模型改 sandbox"是同一类自我提权，
> 只是没那么刺眼。故归 `confirm`。

### 2.3 两个工具（`agent/tools/config_tool.py`）

| 工具 | 作用 | 关键点 |
|---|---|---|
| `ConfigList` | 列出可改项、当前值、`label`/`hint`、tier | 只返回 `auto`/`confirm` 项；`forbidden` 项连名字都不给（避免模型反复试探） |
| `ConfigSet` | 改一个或多个键 | 逐键校验 tier → 先落盘再改运行时 → 记变更 → 回执含**原值**与撤销方式 |

`apply_mode` 由 spec 声明，工具按它分派，**不让模型自己判断**"这个要不要重启"：

- `live`：改运行时 Settings 即可（tts 每次播报现读）
- `rebuild_percept`：调 `ProactiveHub.hot_update_percept()`（逐源开关是装配快照）
- `reschedule`：调 `ProactiveHub.hot_update_schedule()`（调度任务是启动快照）
- `restart`：只落盘并明确告知"下次启动生效"（不假装已生效）

### 2.4 变更账本与撤销

复用 W3 的 `ActionJournal`（`core/actions/journal.py` 的 `EffectRecord`）：

- 一条记录 = 一次 `ConfigSet` 调用，`undo_spec` 存 `{key: before_value}`；
- 撤销走**独立的配置回滚路径**（不是文件回滚——配置是文本外科式写入，
  `restore(cid)` 那套 git 检查点语义不适用）；
- 播报口径统一成：「`简报时间 08:00 → 07:30`（已生效），`/undo` 可回退」。

### 2.5 提示词守则（`agent/prompts/text_blocks.py` 追加一条）

1. 只在用户**显式要求**改配置时调用 `ConfigSet`，不得"顺手优化"；
2. 改完必须一句话说清"改了什么、原值多少、怎么回退"；
3. `forbidden` 项被要求时：拒绝 + 给出正确的改法，**不得换等价手段绕过**；
4. 拿不准属于哪一类时按 `confirm` 处理（宁多问一句）。

## 三、分期

| 期 | 内容 | 交付判据 |
|---|---|---|
| **P1 ✅ 已交付（2026-10-26）** | `config_specs` 统一表 + `ConfigList` / `ConfigSet` + 三类分级 + 账本 + 回执 + 测试 + 文档 | 说"把 TTS 音量调到 30"能改并回执；说"关掉前台窗口感知"能改且面板同步；`/undo` 能回退；说"关掉 sandbox"被拒 |
| **P2 ✅ 已交付（2026-10-27）** | `confirm` 确认流（✅ P1 已白送）+ 面板复用同一张表（✅）+ `/config set` 终端入口（✅） | 三类行为在终端与面板口径一致 |
| **P3 ✅ 已交付（2026-10-27）** | 偏好账本 `~/.jarvis/prefs.jsonl` + 推导层（出声闸门 + 音量/语速）+ `/prefs`（含 suggest/accept）+ 提炼（改为按需触发，不走 Scheduler）| 说一次"以后我写代码时别念出来"，次日依然生效且 `/prefs` 看得到原话 |
| **P4 ✅ 已交付（2026-10-27）** | 首启三问（厂商/Key/模型，**模型实时从厂商拉取**）+ `usage.json` 台账 + 用够 5 次/3 天后主动提议一次（候选仍需 accept）| 全新机器到能用 ≤ 3 个问题 |

## 三之二、P1 实施记录（计划外的三个修正）

计划是纸上的，写完代码才发现三处得改，记在这里以免下轮又踩：

### ① 确认流不需要新做：直接映射到 W1 权限管线

计划原文说"借 `AskUser` 一句话确认"。实际更好的做法是 `check_permissions` 直接返回
`PermissionResult.ask`——W1 那套管线已经处理了「无人值守 / 沙箱 / 租约 / 待批清单」
各种边界，另起一套必然两处口径不一致。于是 P2 的"confirm 确认流"实际上在 P1 就已经
完成了，P2 只剩终端入口与面板派生。

### ② 新增了第五种 `apply_mode`：`policy`

计划只想到 `live` / `percept` / `reschedule` 三种。写的时候发现
`[policy.notify_budget]` 四项看着像现读，其实 `get_budget()` 是**懒建单例**
（`_budget_singleton is None` 才重建）——不显式重建就只落盘不生效。所以单列一档，
由工具自己调 `init_policy(settings)`（进程内的事，不需要宿主）。

### ③ 覆盖面从"四组"扩到 22 项

计划说"先覆盖 `tts` / `daemon` / `deadline` / `percept` 四组"。实测发现 `[percept]`
一组就有 8 个可改项，再加 `[policy.notify_budget]` 四项（打扰预算是用户最想调的
东西之一），最终 22 项（15 auto / 7 confirm）。`[actions]` 只收了 `undo_window_s`：
`journal_enabled` 关掉等于把已发生的副作用记不住，归 forbidden 更合适。

### ④ 顺手把提示词段做了

计划 §2.5 要求追加一条守则，已落为 `_SELF_CONFIG_SECTION`（条件注入：没注册
`ConfigSet` 就不拼）。内容不是"介绍工具"，而是三条最容易越界的纪律：
只按用户明确要求改 / 沙箱与凭据一律不碰 / confirm 项不代用户答复。

## 三之三、P2 实施记录：面板派生（2026-10-27）

计划写的是"面板复用同一张表"，具体做下来有三处得记：

### ① 派生前先补了 6 项真源，而不是"先把表复制过来"

计划没预料到：面板能改的 22 项与真源表的 22 项**并不是同一个集合**。补进真源表才能派生，
所以一次性把那 6 项收进来（并重定口径）：

| 新收项 | tier | 理由 |
|---|---|---|
| `file_watch_dirs` / `vcs_repos` / `mail_watch_senders` / `window_exclude_apps` | `confirm` | 改的是"看什么"：扩大监视范围不该由模型自己决定 |
| `vision_auto_stop_minutes` | `confirm` | 摄像头在多长时间后松开，属于隐私边界 |
| `log_events` | `forbidden` | 本地留痕是感知行为的审计线，模型关它等于让采集不留证据（**但面板上人仍可关**）|

### ② `tier=forbidden` 允许进表了，但硬约束两条

P1 的不变量是"禁改项根本不进 `CONFIG_SPECS`"。`log_events` 让这条破了——它需要
`section/field/attr` 才能派生面板，需要 `label` 才能给模型解释，但模型一律不许改。

改后的不变量（比原来更强）：

1. `list_specs()` 里一个 forbidden 都不能出现 → 模型**连名字都看不到**；
2. 每个 `tier=forbidden` 项必须在 `FORBIDDEN_SPECS` 里有自洽理由（否则得到一句空拒绝，
   用户拿不到"该怎么改"）。

真正拦住模型仍然是权限层：`check_permissions` 先查 `forbidden_reason()`，`call()` 里再兜一道。

### ③ “同一项两个名字”是本次要修的核心隐患，而不是改名

`enabled`（面板）/ `percept_enabled`（协议）——曾想过干脆统一改名，最后没那么做：
在面板对象里 `config.enabled` 本身就清楚（对象名限定了语境），而模型面对的是扁平键
列表，叫 `enabled` 会与 `[deadline] enabled` 撞名。两边各自合理。

所以把差异写成**显式别名表** `PANEL_KEY_ALIASES`（只一条）+ 三条守卫测试（别名指向
真实项 / 不多不少 / 面板键要么同名要么在表里）。风险从"隐性"变成"写在代码里且会变红"。

### 顺带修的两个隐患（派生时暴露的）

- `read_value` 对 `list` 缺省会把不可变的 `()` 直接递出去（前端只认数组），且会把共享的
  零值列表对象递出去（调用方 append 就污染全局）。已改成一律返回新 list。
- `human_value` 对列表会输出 `['E:/a']`——用户看到的是一条"程序内部字符串"。已改成
  `E:/a、E:/b`（空名单写"（空）"）。

### ④ 终端入口不是"再写一遍工具"，而是抽出一条共用写路径

计划说"`/config set <key> <value>`"时没想清实现位置。真做时立刻碰到一个问题：
工具与命令要做的六步（禁改兜底 → 校验 → 剔无变更 → 原子落盘 → 生效 → 广播+记账+回执）
**一字不差**。两处各写一份，迟早一边补了广播、另一边忘了。

所以新建 `agent/config/config_ops.py`：`apply_config_patch()` 与 `render_spec_list()`
两个函数，两侧各只留自己的体例（工具留 schema/权限分级，命令留子命令分发/两段式/转义）。
`config_tool.py` 因此从 344 行瘦到 148 行，且行为一字未变（旧测试全绿）。

两个入口的真实差异只剩三条，都已写成测试：

| 差异 | 模型侧 | 终端侧 |
|---|---|---|
| `confirm` 项 | 权限管线 ASK | 两段式：先预览，再加 `--yes` |
| 值从哪来 | JSON 原生类型 | 一行文本，按 `kind` 解析（布尔认中英文）|
| 账本来源 | `args.entry="model"` | `args.entry="terminal"` |

计划里没写到的三个边界（都是写测试时才想起来的）：

- **名单类不准裸写就清空**：`/config set vcs_repos` 后不跟值必须报错并提示写 `""`；
- **预览回显规范化值**：用户敲 `A, B ,A`，回显必须是 `A, B`，否则照抄回去很脏；
- **`forbidden` 与人无关**：护栏是配置本身的属性，`/config set sandbox_enabled false --yes`
  照样拒（不是"人就是主人所以能改"——那是把护栏写到调用方去了）。

## 三之四、P3 实施记录：偏好账本（2026-10-27，已交付前半）

计划只写了"偏好账本 + 推导层 + `/prefs` + 每日提炼"四个词。前三件已落地，
每日提炼留待下一轮。真做下来有五处判断值得记：

### ① "配置 = 偏好 + 上下文"不是改 `read_value`，而是落在一个个决策点上

最初的直觉是让 `read_value(spec, settings, context)` 接受上下文——但那会把**所有**
读配置的地方（包括纯展示的面板、日志、测试）都变成"要有上下文"，代价极高，
而且大多数读取根本不需要上下文。

改成：只在那几个**真的做行为决策**的地方求值。当前只接了一个——主动播报的
出声闸门（`notify_gate_action`）。这也让可施放范围必须显式声明
（`ENFORCEABLE_KEYS`）：键可以记、可以查，但没接决策点就不会自动生效，
而不是"恰好碰上了判空就当生效"。

### ② 偏好必须写在闸门上，不能只写在提示词里

如果不接闸门、只在提示词里加一句"用户不喜欢听"，那就是在**鼓励**模型遵守。
模型明天忘了，行为就复发，而用户完全无法归因（"为什么今天又念了"）。
所以命中判定与施放都是确定性代码，模型只负责**记**与**忘**。

### ③ "只降出声不降投递"是硬约束，不是细节

用户说的是"别念出来"，不是"别告诉我"。若实现成 suppress，就真会丢消息。
闸门只把 `speak` 置假，`kind` 仍为 `emit`（气泡/系统通知照旧）。

### ④ 边界两道：只能 auto 域 + 必须带条件

- 只能 `auto` ：「以后每天最多提醒 50 次」若能进偏好，就是用一句话固化了
  一次本该当场问人的决定——偏好会变成绕过 `confirm` 的后门；
- 必须带条件：无条件偏好就是偷偷永久改配置，且**不入账本、不可 `/undo`**，
  比直接改配置更差。两道都在 `remember` / `validate_input` 里硬拦。

### ⑤ 测试隔离：闸门读的是真实账本，这会弄脏别人的测试

闸门读 `~/.jarvis/prefs.jsonl`。开发机上若真有一条「在 Code.exe 时不出声」而跑测试的
终端恰好就是 Code.exe，原有的打扰预算/后台驱动测试就会随机变红。已在
`test_task_driver.py` 与 `test_hub_reminder_delivery.py` 加 autouse fixture 屏掉。

### 还有没做的（下一轮）

- **每日提炼**（已做，但换了形式）：改成按需 `/prefs suggest`，不走 `Scheduler`——
  理由见下。
- **更多决策点**（已做一半）：`tts_volume` / `tts_speech_rate` 已接（"晚上小点声"）；
  再往下（如 `briefing_time`）需要该处能拿到上下文，暂不接。

### ⑥ 提炼为什么没走 Scheduler（与计划不同的一处）

计划写的是"每日提炼（走 `Scheduler`）"。真做时否了：每天凌晨扫一遍，没新意的时候
只会多响一次，而 W1 打扰预算存在的意义就是"别无关紧要地响"。

改成 `prefs.suggest_from_records()` 纯函数 + 用户主动跑 `/prefs suggest`。好处是
好测、无副作用，且**候选不生效**（必须 `/prefs accept`）。

### ⑦ 提炼必须有证据，否则宁可不提

最初写的规则是"同一键改同一值 ≥2 次就提"。试了一下就发现这是编造：9 点调低一次、
15 点调低一次，凭什么说"白天都调低"？所以加了一条硬门槛：
**这些改动要有共同上下文**（同一个前台程序，或同一个小时）。没有共同点就不提——
那不是偏好，是"用户想要这个新值"，而那个值已经改好了。

为此在记账本时多存一个 `args.context`（改配置那一刻的 app 与 hour）：不去记就无从
提炼，只能靠猜，而靠猜造偏好正是用户最不想要的魔法。

### ⑧ 施放只能有一个入口

接第二个决策点（音量）时立刻出现了"各写一份"的诱惑：闸门里写一套"查表→比键名→
判值"，音量那边再写一套。所以改成 `prefs.effective(key, base) -> (值, 原因)`，
两个决策点都调它。`ENFORCEABLE_KEYS` 仍是白名单：只加名字没接调用点，就是
"能记但不生效"的假功能。

## 三之五、P4 实施记录：首启三问（2026-10-27）

计划只写了一句"首启只问三件 + 用几天后主动提议调整"。做完四处得记：

### ① "三件"里的"模型"顺手把厂商问题吞了

原设计是"厂商、模型、key、工作目录、模型类型"五问。真正要做成三问，关键是别把
"模型"和"厂商"拆开问：选项直接是**各厂商的默认模型**（`qwen3.7-plus` /
`deepseek-chat` / `gpt-4o`…），选它就是选厂商+模型，api_format / base_url / model_type
全部取默认。

### ② model_type 保守给 text，不猜多模态

很多默认模型确实是多模态的，但"猜成 multimodal"会让不支持视觉的模型在用户发图时
报错，而用户不会想到是首启时被写错了。宁可少给一项能力，也把改的法子写明（/models）。

### ③ 非交互不问：这是安全阀不是优化

`stdin` 不是终端（管道 / CI / 脚本）时直接跳过。一道交互提示会把自动化卡死，
这比"没引导"严重得多。同理，取消过一次就写 `declined`，以后不再自动问——
"用户已经说不"不该被每启动一次重问一遍。

### ④ "主动提议"的频度比内容重要

最初想的是每天扫一遍。问题在于：大多数日子不会有新习惯，于是这个功能就从"帮我记"
退化成"每天多一行字"。改成三个门槛同时满足才提（启动≥5 次 + 首启满 3 天 + 没提过），
且**没候选也算提过**（否则每次启动都要扫账本）。

顺手修了一个暴露出来的文案 bug：候选"依据"里出现"你在在 Code.exe 时"——`describe()`
的 app 分支已自带"在"，模板又拼了一个。已加测试钉住。

### ⑤ 真机实测后补的两处（这条最值钱）

用户拿真机跑完一遍后，暴露了计划里完全没想到的一类失败：

**`when_app=Code.exe` 记下去，可能永远不会命中。** 实测时前台是 `mintty.exe`，
而"写代码"时候的前台很可能是 `Qoder.exe`（VSCode 的独立发行版，进程名不是
`Code.exe`）。单值条件只能命中一个进程，于是：用户以为记住了，实际上永远不生效，
`/prefs` 里只会一直写"命中 0 次"，而原因无从得知。

两处修补：

- `when_app` 支持**逗号分隔多个**（任一命中即算），顺手把`app=" , "`这类拆不出
  名字的输入归为空条件（否则它是一条永远不命中的幽灵偏好，连"必须带条件"都拦不住）；
- 回执与 `/prefs` 都把**当前前台程序**抵出来（`当前不命中：当前前台是 Qoder.exe，
  15 点；条件写的是「在 Code.exe 时」`）——只说"不命中"是没用的信息，把两边都
  列出来，用户一眼就能看出漏了谁。

这条教训值得记下来：**“你能看见它没生效”比“它尽力生效”重要**。隐蔽的失效比脆弱的
失效代价大得多——后者马上被发现，前者会让人对整套系统失去信任。

### ⑥ 首启模型清单：从“内置一张表”改成“向厂商要”（用户一句话推翻了原设计）

最初的首启把各厂商默认模型写死在 `_VENDORS` 里。用户实测时指出"这些都不是最新型号"，
接着补了一句关键的话：**"jarvis 是要推荐给别人的，他们喜欢什么模型我不能决定吧"**。

这句话把问题从"表该更新成什么"换成了"**该不该有一张表**"：

- 表会过时（`deepseek-chat` 已于 2026-07-24 被官方弃用，`gpt-4o` 是 2024 年型号）；
- 表是在**替用户做决定**，而模型偏好属于用户。

所以改成：三问 = **厂商 / API Key / 模型**，第 ③ 问的清单由
`agent/llm/model_catalog.py` **实时向厂商拉取**（OpenAI 兼容与 Anthropic 两路
`models.list()`，10s 超时，失败返空）。拉不到就回落内置兑底**并明说没拉到**，
且永远保留"手动输入模型名"。

连带两个决定：

- **工作目录不再单独问**。问券预算只有三问，要留给没有安全默认值的那件事——
  工作目录默认当前目录不会错，型号写错第一次调用就失败。
- **厂商表的 `name/desc` 不再写具体型号**（"GLM-4.7/GLM-5.2"这类），
  `default_model` 只作断网兑底。写上去就是下一张会过期的表。

调研时还有个反面收获：公开搜索对"各厂商最新型号"给出的答案**互相矛盾**
（同一家在不同来源里差着两三个大版本），绝大多数是第三方聚合站。
**这种信息不能写进首启**——型号错了用户第一次调用就报错。让厂商自己回答才可靠。

**上线前用真实 key 实测 `GET /models`，结果直接证明了上面这句话**：

| 厂商 | 实测返回 | 说明 |
|---|---|---|
| DashScope | 181 个 | 含 `qwen3.8-*`、`deepseek-v4.1-flash-1017`、`kimi/kimi-k3.1-pro` 等，比任何“最新表”都新 |
| DeepSeek | 2 个：`deepseek-flash`、`deepseek-v4-pro` | **`deepseek-v4-flash` 不在列表里**——按搜索结果写死就会让用户选中一个不存在的型号 |
| Moonshot | 5 个 | `kimi-k3`、`kimi-k3.1-pro`、`kimi-k2.7-code` 等 |
| 智谱 | 11 个 | `glm-4.5` → `glm-5.3-flash`；内置兑底 `glm-5.2` 在列 |
| MiniMax | 9 个 | 返回的是 **`MiniMax-M3`**（id 大小写敏感，内置写的 `minimax-m3` 对不上） |
| 小米 MiMo | 7 个 | **没有 `mimo-v2.5-pro`**；且前四个是 asr/tts 语音模型 |

于是把 DeepSeek 兑底也改成实测存在的 `deepseek-flash`，并在选模型时提醒用户选
**对话模型**（181 个里混着图像/语音专用模型；不能按名字猜着过滤，各家命名不同）。
MiniMax 兑底改为 `MiniMax-M3`、小米改为 `mimo-v2.6-flash`。

六家全部实测通过（DashScope / DeepSeek / Moonshot / 智谱 / MiniMax / 小米）；
OpenAI / Anthropic / Google / SiliconFlow 本机无 key，未实测——走同一套 SDK 路径，
失败也会回落兑底并明说。

### ⑦ `/init` 配完却连不上："✓ 连接成功"不等于"现在能用"

真机再测暴露了一个凶得多的 bug：`/init` 写完盘就结束，而 REPL 的 `ctx.provider`
还是启动时按旧配置建的。于是用户看到“✓ 连接成功”，紧跟一句“你好”却报
`Connection error`——因为那一句走的还是旧 provider / 旧 key。

修复：`_apply_to_session()` 重读配置 → 原地刷新 `settings` → 复用 `/model` 已存在的
`_switch_model` 重建 provider/loop，并回写 `ctx`。回执也从“已保存、请重启”改成
“已在本会话生效：deepseek / deepseek-v4-pro”。复盘见
`docs/fixlogs/init-not-applied-to-session-fix.md`。

两层教训：

- **回执要回答“我现在能用了”，不是“凭据有效”**——这正是自配置层一直在守的口径
  （`ConfigSet` 有五档 `apply_mode`），`/init` 却漏了自己；
- **新入口上线前先问“这件事现有哪条路已经做对了”**——`/model` 的热切换就在那儿，
  `/init` 没用它。

### ⑧ 第二轮实测：热生效“切了却切错”，外加同一份默认值写了两遍

上一条修完后日志依然失败，只是原因换了个位置：

```
✓ 连接成功！
已在本会话生效：deepseek / deepseek-flash
> 你好，你是谁
❌ LLM 调用失败: 原始错误: Connection error.
```

`_apply_to_session` 先把新端点**原地写回** `ctx.settings`，再交给 `_switch_model`；
而 `_build_switched_provider` 拿 `cfg` 和 `settings` 比 → 刚被改成一样，永远相等 →
永远复用启动时那个旧 provider（旧 base_url / 旧 key）。

修复：新增 `baseline` 参数，**比较用 baseline（运行中那份端点），构造用 settings**；
`_apply_to_session` 在原地更新前先留一份 `replace(settings)` 快照传下去。

同一批还碰到另一类“两份真相”：厂商默认 base_url 在 `init_command._VENDORS` 与
`model_manager._infer_base_url` 各写一遍，已经漂移（DeepSeek 少 `/v1`、小米 MiMo
只在一边有）。抽成 `agent/config/vendors.py` 单一来源，并加测试钉住“表里每个厂商
的默认地址与实际推断结果必须一致”。

新增教训：**差异判定必须拿“正在跑的那一份”当基准**。先把目标值写进全局对象再去比，
比较就永远相等——比“没生效”更隐蔽，因为回执会理直气壮地说“已生效”。

## 四、风险与对策

| 风险 | 对策 |
|---|---|
| 配置漂移（模型反复微调把配置搅成筛子） | 账本里每条带 `source: model/user/init` 与时间；`ConfigList` 标出"模型改过的项" |
| 模型"好心"改坏体验 | 三类分级 + 回执含原值 + 一键回退；`auto` 只放纯表达层 |
| 用户不知道自己被改了什么 | 每次变更都播报（走 W1 闸门，静默时段入库待补播），并进账本 |
| 与已有四份 spec 漂移 | P1 只做"派生"不做"重写"：现有 API 全保留，测试锁住旧行为 |

## 五、验收（人工，可粘贴）

1. `把 TTS 音量调到 30` → 改成功、回执含原值与 `/undo`；`settings.toml` 的注释与其他段一字不动
2. `关掉前台窗口感知` → 走 `rebuild_percept`，桌面面板该源变未开启且不必重启
3. `把每日简报改到 07:30` → 走 `reschedule`，`~/.jarvis/schedule.json` 与 hub 的调度时间同步
4. `把沙箱关掉` → 拒绝，并告知去 `/init` 或手改（**不尝试**用 Bash 改文件绕过）
5. `把打扰预算的每日上限调到 50` → 走 `confirm`，问一句再改
6. `/undo` → 上述任意一条可回退，回执写明"配置已回退到 X"
