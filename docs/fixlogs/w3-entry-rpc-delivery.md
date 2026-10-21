# W3 收尾切片交付复盘：建目标的三个入口 + serve RPC（含 7 个本轮自己踩出来的坑）

> 性质：功能交付复盘（阶段 10 / 蓝图 W3 的**最后一块**），不是缺陷修复记录，但本轮
> 有 7 个是实现真错、回执形状不好用或踩到单文件行数上限，被测试/写文档时打出来的，
> 一并记清，防止下次当"正确姿势"抄回去。第八节是事后追加的**一键走查脚本**（把“直观感受”
> 做成一条命令）。
> - 触发原因：上一切片把脊柱后端交付完了，但**没有任何入口能把目标写进去**——`/tasks`
>   第二节只能读、驱动器只能推已有台账，实机上第二节永远为空，用户说"替我盯着这件事"
>   没有承载体，测试也就没有直观感受。
> - 影响版本：阶段 10 新增 `core/tasks/spine_ops.py`、`tools/track_goal.py`、
>   `serve/rpc_tasks.py`，`/tasks` 从只读扩成写入子命令，协议指令 53 → **55 条**
> - 影响既有链路：`commands/handlers/collab_commands.py`（重写分发）、`commands/router.py`
>   （`/tasks ` 前缀路由）、`core/tool.py` + `tools/__init__.py`（注册 TrackGoal）、
>   `prompts/system.py`（口径注入，并因超 800 行拆出 `prompts/text_blocks.py`）、
>   `serve/protocol.py` + `serve/server.py`、`ui/slash_completer.py`
> - 设计文档：[18-W3兑现层](../architecture/18-W3兑现层.md) §5.4　|　实机走查：[w3-testing.md](../test/w3-testing.md)
> - 前一切片复盘：[w3-journal-spine-delivery](w3-journal-spine-delivery.md)（四处设计偏离）
>   与 [w3-delay-window-bypass-fix](w3-delay-window-bypass-fix.md)（两处实机缺陷）
> - @author aceFelix　|　2026-10-21

## 一、这一轮实际交出什么

| 主线 | 落点 | 用户面 |
|---|---|---|
| 共用业务层 | `core/tasks/spine_ops.py`（463 行）：`OpResult`、`split_steps`、`parse_wait`、`wait_from_spec`、`create_goal`、`control_goal`、`find_goal`、`wait_readable`、`goal_detail`、`snapshot`、`describe_steps` | — |
| 入口 1（终端） | `collab_commands.py`：`_TASKS_USAGE` / `_parse_tasks_args` / `_tasks_add` / `_tasks_show` / `_tasks_control`，`handle_tasks` 重写为子命令分发 | `/tasks add "步骤1;步骤2" --wait time:+90s`、`/tasks show <id>`、`pause/resume/cancel/advance/delete` |
| 入口 2（模型） | `tools/track_goal.py`（239 行）：`TrackGoalTool`，8 个 action，`is_read_only` 按 action 判定，恒非并发安全 | 说一句"替我盯着张三的邮件到了就归档"，模型自己建目标 |
| 入口 3（桌面） | `serve/rpc_tasks.py`（323 行）：`TasksRpcMixin`，`tasks.exec` + `actions.exec` | 桌面「任务中心」的数据源与写操作 |
| 协议契约 | `protocol.py` 两条 `CMD_` 常量 + `DESKTOP_COMMANDS` 追加；`server.py` 挂 mixin 并注册 | — |
| 提示词口径 | `prompts/system.py`：基础提示一行用法 + `_goal_spine_section` 按注册表条件注入；因超 800 行，静态正文另拆 `prompts/text_blocks.py` | 模型知道有这条能力，且不会被没注册的工具骗 |
| 补全面 | `ui/slash_completer.py`：`/tasks` 描述 + `add/show/advance/cancel` 四条 | 终端能 Tab 出来 |
| 测试 | 新增 `tests/core/test_spine_ops.py`（45）、`tests/tools/test_track_goal.py`（31）、`tests/serve/test_serve_tasks_rpc.py`（27）、`tests/commands/test_tasks_command.py` 追加写子命令 18 例，`tests/core/test_system_prompt.py` 补拆分护栏 4 例，`tests/test_w3_smoke_script.py`（走查脚本自身，4 例） | W3 单测 190 → **315 例**，全量 3017 → **3146 例** |
| 直观测试 | `scripts/smoke_w3_spine.py`（619 行）：一条命令跑完三个入口 + 延时窗口 + 硬拦，共 40 项逐行打结论 | 3 秒看到 W3 到底跑不跑，详见 §八 |

## 二、为什么必须是一层共用语义，而不是三个入口各写一遍

三个入口的调用方完全不同（人 / 模型 / 桌面进程），但下面这些取舍**只要出现两处实现就必然漂移**，
而漂移的表现是"终端里能做、RPC 里报错"或反过来，用户只能理解为功能坏了：

- 取消目标该不该顺手清掉挂在步骤上的 `wait`；
- `advance`（跳过等待）算不算"条件成立"，要不要留下痕迹；
- 已经 `done/failed/cancelled` 的目标能不能被 `resume` 改回活跃；
- 等待条件的字符串格式、缺省 deadline、过去时刻的处理；
- 写完要不要唤醒驱动器、唤醒失败算不算操作失败。

所以 `spine_ops.py` 是唯一的规则真源，三个入口只做**参数搬运 + 呈现**。
分层上它只依赖 `spine.py`（数据与落盘）与 `driver.wake_driver`（唤醒），
不 import UI / 命令 / serve，不起线程，不调 LLM——这样它单测不需要任何宿主。

## 三、等待条件的语义取舍（这一档最容易想当然）

| 取舍 | 结论 | 理由 |
|---|---|---|
| 等待条件挂在哪一步 | **只挂 `steps[0]`** | 脊柱的推进模型是"当前步做完才看下一步"，挂中间步会让第一步永远走不到；等后面步骤的用户请用 `advance` 或分开建目标 |
| 用户只写目标、没写步骤 | **补一个与目标同文的单步骤** | 零步骤目标会被驱动器 `current_step() is None` 直接跳过，台账里看着像死数据 |
| `time:+90s` 要不要补 deadline | **不补** | `time` 自带确定时刻，到点必走；再叠一层 `deadline_at` 会出现"deadline 比 wait 先到"的自相矛盾超时 |
| `file:` / `event:` 要不要补 deadline | **必须补**（默认 `[tasks].wait_deadline_hours=72`） | 感知逐源默认全关（W2 决策），事件可能永不到来；没有兜底时限目标就永久 `blocked`，超时转 `blocked_timeout` 走 W1 闸门问人才对 |
| 过去的时刻（`time:08:00` 而现在 10:00） | **直接报错，不静默当成"立刻"** | 静默放行等于把用户写错的时间当成"现在执行"，外发动作不可撤 |
| dict 写法（工具/RPC 传结构化参数） | `wait_from_spec` 与 `parse_wait` **落同一种 `TaskWait`** | 两套解析器 = 两种字段名漂移；这里只共享最终的构造与校验 |
| id 怎么给用户 | `find_goal` 支持**完整 id > 唯一前缀**，多命中不猜 | 12 位随机 id 手敲不现实；猜一个则可能改错目标，宁可以说"没找到" |

## 四、`advance` 与两拍确认

- **`advance` 是给实机测试用的绕行口子**：清 `wait` + 清 `next_run_at`、置 `pending`，
  并**在步骤 `result` 里留「用户以 advance 跳过等待条件」的痕迹**。
  不谎称条件成立，否则日志里会出现"等到邮件了"这种从没发生过的事，
  复盘时没人分得清是真命中还是被跳过。
- **定局目标只允许 `delete`**：`pause/resume/cancel/advance` 对 `done/failed/cancelled`
  一律拒绝。`done` 的目标被 `resume` 会让驱动器**重跑已经发出去的邮件/消息**，
  最坏情况是重复外发，这比"用户想复活记录"的便利重要得多。
- **`delete` 是硬删**，连同等待与预约一起抹掉，所以它是 `TrackGoal` 里**唯一**返回
  `ASK` 的 action（其余写台账都是 ALLOW），RPC 的 `delete`/`undo`/`undo_task`
  未带 `confirm=true` 时只回 `need_confirm` + `preview`。
  无 UI 时 `ASK` 会被 fail-closed 成 DENY——后台子代理不该能悄悄抹掉用户台账。
- **写完必 `wake_driver()`**，且返回值要拿走：`advance`/`resume` 时若无人推
  （`driver_running` 为假），文案必须补一句"当前没有驱动线程，需先开 `[tasks] enabled`
  才会真的执行"。唤醒失败**不算操作失败**（目标确实写进去了），但瞒着用户说"已推进"就是骗。
- **`snapshot()` 把"线程在跑"与"配置开了"分开报**（`driver_running` vs 配置意图）：
  实机上 `[tasks]` 开了但宿主没起来、或 REPL 里没装配 `CliSpineHost`，两种状态的用户
  下一步动作完全不同，合成一个布尔就没法提示。

## 五、本轮实现真错 / 真不好用 / 踩到结构红线的七个地方

前五个都能在生产里咬人，第六个是写文档时发现的回执形状问题（面板只能去文案里截 id），
第七个是单文件行数上限。

### 5.1 `_driver_note(snapshot)` 把函数对象当返回值传

- **现象**：`TrackGoal` 一调用就 `AttributeError: 'function' object has no attribute 'get'`。
- **根因**：漏写调用括号，`snapshot` 而非 `snapshot()`。
- **教训**：**同名的模块函数与它的返回值在同一文件里混用时，返回值一律先赋给变量**。
  这类错误单测第一行就能抓住，别靠肉眼。

### 5.2 `journal.held()` 不存在

- **现象**：`actions.exec` 的 `status` 分支 `AttributeError`。
- **根因**：凭印象写方法名。真实接口是 `ActionJournal.held_records()`。
- **修法**：改 `held_records()`。**教训**：跨模块调用先 grep 定义，别信记忆里的方法名。

### 5.3 `EffectRecord(label=...)` 构造参数不存在

- **现象**：测试构造记录时 `TypeError`。
- **根因**：`EffectRecord` 的字段是 `preview`，`label` 只是**只读 property**。
- **附带**：`APPLIED` 记录还要 `reversible=True` 才谈得上撤销，否则 `undo` 直接拒。
- **教训**：property 与字段同名时，**构造用字段、读取用 property**，测试夹具最容易踩。

### 5.4 `delete` 分支调了两次 `store.delete()`

- **现象**：读代码时发现，第二次删除靠"文件已不存在也不报错"掩盖，行为上看不出来。
- **根因**：改造成 `control_goal` 统一入口时，把旧的直接删除调用留在了分支里没删。
- **教训**：**改写既有分支时，旧调用要当场删净**，双写不会报错但会掩盖"第一次就没成功"。

### 5.5 RPC 未剥传输信封键

- **现象**：`tasks.exec` 收到的 `params` 里混着 `type` / `id`，业务字段校验莫名多两项。
- **根因**：serve 的 WS 帧是**平铺结构**，`type`/`id` 属传输层；直接拿整个 dict 当业务参数用。
- **修法**：`_payload(data)` 复用 `rpc_percept.py` 里的 `TRANSPORT_ENVELOPE_KEYS` 剥键。
- **教训**：这个坑 W2 就记过一次（[percept-config-set-envelope-fix](percept-config-set-envelope-fix.md)）——
  **真实客户端必现、单测直传业务 dict 测不出**。所以本轮 RPC 测试专门写了一条
  `test_envelope_keys_never_leak_into_business_fields`，用带信封键的入参做回归锁。

### 5.6 RPC 回执里没有结构化 `goal`，面板只能从文案里截 id

- **现象**：`tasks.exec create` 只回 `message`，目标 id 混在中文文案的方括号里；
  单测因此写成 `gid = data["message"].split("〔")[1].split("〕")[0]`。
- **为什么算缺陷**：从文案里抽 id 是"文案一改就碎"的耦合，面板要么多一个往返（建完再查
  一次 status），要么写正则。写测试时的那两行 `split` 就是坏形状的味道。
- **修法**：`_tasks_create` / `_tasks_control` 改为展开 `res.to_dict()`（带 `goal` 明细）；
  `delete` 的第一拍（未带 `confirm`）也带 `goal` 明细，面板才列得出"要删的是哪个"；
  查不到目标时照实不带。锁住：`test_tasks_create_returns_structured_goal`、
  `test_tasks_delete_preview_carries_goal_detail`。
- **可复用教训**：**回执里的数据要给结构化字段，文案只是给人看的那一份**。同一件事
  在返回体里应该有两个形状（`message` + 结构化），缺后者时调用方就会自己发明解析方法。

### 5.7 `prompts/system.py` 越过单文件 800 行上限

- **现象**：本轮往 `system.py` 里加了 `TrackGoal` 用法行与 `_GOAL_SPINE_SECTION` 后，
  文件从 812 行涨到 850 行——项目规则（code-structure）写的是“修改前已接近 800 行，
  本次修改前先完成拆分”，所以这属先违规再叠加。
- **拆法**：把七个逐字发给模型的静态正文常量（`_BASE_PROMPT`、`_NO_THINKING_SECTION`、
  `_SCHEDULE_SECTION`、`_PLATFORM_WINDOWS/MACOS/LINUX`、`_GOAL_SPINE_SECTION`）搬到新文件
  `agent/prompts/text_blocks.py`，`system.py` 重导入后只留下拼装逻辑：
  850 → **247 行**，新文件 619 行。这些常量本身不含逻辑，拆走反而更内聚。
- **验证方式**：拆分前先逐个常量取 SHA-256，拆完再比——**七个哈希全等**，
  提示词逐字不变（前缀缓存对字节敏感，不能只靠“看起来一样”）；
  再跑 `pytest -k "prompt or system"` 49 例通过。
- **可复用教训**：**拆纯文本常量块是零行为风险的重构，但必须拿指纹比对而不靠肉眼**；
  旧文件保留同名导入（而不是改调用点）能让其它模块与测试的引用不碎。

## 六、文档同步清单

| 文档 | 同步内容 |
|---|---|
| [18-W3兑现层](../architecture/18-W3兑现层.md) | 进度头与单测数；§三 模块表补 4 行；新增 §5.4「三个入口一层语义」；§七 用户面 7 行；§九 测试表；§十 边界改写；§十二 注释清单 |
| [PLAN 路线图](../roadmap/PLAN-butler-autonomy-upgrade.md) | 顶部状态、§4 进度块与文件表、§4.1 用户面条目（原"尚无模型侧入口"已过期）、§7 里程碑 190 → 311 |
| [BLUEPRINT](../blueprint/BLUEPRINT.md) | §2.1 主动性行、§9.2 阶段 10、§9.3 P3、里程碑与 v2.0 修订注 |
| [commands.md](../guide/commands.md) | `/tasks` 行 + 8 条写入子命令与示例 |
| [01-总体架构](../architecture/01-总体架构.md) | 目录树补 `tasks/spine_ops.py`、`tools/track_goal.py`；protocol 行 53 → 55 |
| [05-工具系统](../architecture/05-工具系统.md) | 核心工具 14 → 15 个、注册顺序、`TrackGoal` 表行 |
| [00-索引](../architecture/00-索引.md)、[development.md](../guide/development.md)、[configuration.md](../../config-docs/configuration.md) | 摘要行、全量测试计数、`[tasks]` 相关命令行 |
| [README.md](../../README.md) / [README.en.md](../../README.en.md) / [USER_GUIDE.md](../../USER_GUIDE.md) | 能力行补"一句话就能建目标"；删掉"入口还没做"；阶段 10 行数与入口三件套 |
| [w3-testing.md](../test/w3-testing.md) | 新增直观测试场景（自然语言 → TrackGoal、`--wait file:` + `advance`、两条 RPC 手测与两拍确认）；§4 重写为入口三件套 + 附录 A 手工写法；新增阶段五（serve RPC）与 §6.4 拆分护栏行 |
| [agent-loop-stall-fix.md](agent-loop-stall-fix.md) | 待拆清单补进展：`prompts/system.py` 已拆（810 → 247 + 619），`server.py` 已降至 749 |
| [01-总体架构](../architecture/01-总体架构.md)、[development.md](../guide/development.md) | `prompts/` 目录行改写为“system 拼装 / text_blocks 逐字正文”，平台段常量位置标注拆分去向 |
| §八 那一轮（一键走查脚本） | [w3-testing.md](../test/w3-testing.md) 新增 §0.5、§6.1b 与两行常见问题；本文件新增 §八 与遗留边界改写；[18-W3兑现层](../architecture/18-W3兑现层.md) 进度头/§九测试表加一行；[PLAN](../roadmap/PLAN-butler-autonomy-upgrade.md) 顶部、§4 进度块、§4.3 文件表、§7 里程碑；[BLUEPRINT](../blueprint/BLUEPRINT.md) W3 行与修订注；[development.md](../guide/development.md) 新增 `scripts/` 目录块与走查命令；[00-索引](../architecture/00-索引.md) 摘要；README / README.en 阶段 10 行——计数统一为 **315 / 3146** |

## 七、遗留边界（不要在别处当成已完成）

- **目标拆解仍靠人给步骤**：`TrackGoal` 与 `/tasks add` 都不会替你把一句大目标拆成合理步骤，
  不写分号就是单步。真正的"模型自己规划步骤"属后续切片。
- **桌面「任务中心」面板**：本仓只交付 `tasks.exec` / `actions.exec` 两个数据与写入口，
  面板 UI 属 **jarvis-desktop** 仓。
- **本切片的人工走查只盖住了“不经模型”那一半**：`scripts/smoke_w3_spine.py`（§八）已把
  三个入口、延时窗口、绕道硬拦与两条 RPC 钉成 40 项可重复跑的实机结论；仍然缺的是
  **到点真执行**、**驱动器推进步骤**（要调 LLM）与**桌面壳渲染**三块，逐条跑法与判据见
  [w3-testing.md](../test/w3-testing.md)，里程碑 §7 因此仍不打钩。

## 八、又追加：把“直观感受”做成一条命令

### 8.1 动机

用户原话：“我想全面测试 jarvis 的 W3 的功能，不然现在测试貌似没有直观的感受。”
走查清单（§0～§7）是人肉流程：要开 REPL、要改 `settings.toml`、要等 90 秒、要配凭据，
“跑一次”的成本高到没人愿意跑——而没跑过的功能等于不存在。

### 8.2 取舍：一个脚本而不是又一个单测文件

单测已经在证明语义（315 例），再加一份只能证明同一件事；缺的是**把真实组件跑起来给人看**：

- 真 `handle_tasks` + 真 `RichCLI`：屏幕上就是 REPL 里那份回显（工具卡片、脊柱列表）；
- 真 `TrackGoalTool`、真 `ToolOrchestrator`、真 `BashTool` 权限判定；
- 真 `DesktopBridgeServer` 起端口 + 真 `websockets` 客户端发平铺帧（单测直传业务 dict
  测不到信封键与回执形状，这一层只有走线才能验）。

### 8.3 三个刻意的设计

| 取舍 | 理由 |
|---|---|
| 台账/账本全部指向系统临时目录 | 要能让用户随时跑、跑坏也不污染 `~/.jarvis`；`configure_spine_store` / `configure_journal` 本来就是为这个留的注入口 |
| 延时窗口在到点前就 `cancel_hold` 撤回 | 脚本不能真把一个邮件/外发请求放出去；“到点真执行”归人工走查 §2.5 |
| 前缀歧义用**造出来的两个同前缀 id** | 真实 id 是随机十六进制，跑一次未必碰得上歧义，否则这条检查会“偶发通过”，比没有还坑 |

### 8.4 跑出来的结果

2026-10-21 实机：**40/40 全部通过**（耗时约 2.2 秒），未推翻任何现有语义；本轮新脚本
自身也有回归 `tests/test_w3_smoke_script.py`（4 例：全跑绿、三段入口都在输出里、
`--no-serve` 也绿、只写临时目录并自清理）——脚本坏了它自己会红，不会指着假象说“通过”。
