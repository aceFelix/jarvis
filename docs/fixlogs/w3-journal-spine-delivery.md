# W3 兑现层交付复盘：副作用账本 + 任务脊柱（含 4 个被测试打出来的真缺陷）

> 性质：功能交付复盘（阶段 10 / 蓝图 W3），不是缺陷修复记录，但本轮**测试反向
> 钉出了 4 个真实生产缺陷**，一并记在这里，避免下次有人把那些写法当"正确姿势"抄回去。
> - 影响版本：阶段 10 新增能力（`agent/core/actions/`、`agent/core/tasks/`、`/undo`、`/tasks` 两节）
> - 影响既有链路：`orchestrator`（不可逆动作拦截 + 记账）、`proactive`（W1 闸门实现上提）、
>   `budget`（登记 `task` 源权重）、`task_list`（文件锁外提）、两个宿主（`serve/hub.py`、`main.py`）
> - 设计文档：[18-W3兑现层](../architecture/18-W3兑现层.md)　|　实机走查：[w3-testing.md](../test/w3-testing.md)
> - @author aceFelix　|　2026-10-21

## 一、这一轮实际交出什么

| 主线 | 落点 | 用户面 |
|---|---|---|
| W3.2 副作用账本 | `core/actions/journal.py`：`EffectRecord`（冻结 args + `undo_spec`）+ `actions.jsonl` 事件流 + `DelayWindow` | `/undo`（撤上一步 / `<id>` / `list` / `cancel` / `--task <id>`） |
| W3.2 拦截接入 | `core/tool.py` 新增 `is_irreversible` / `describe_irreversible` 声明位；`orchestrator` 在权限判定之后 hold；`SendEmailTool` 声明不可逆 | 不可逆动作先进 30 秒待发窗口，到点才真执行 |
| W3.1 任务脊柱 | `core/tasks/spine.py`：`TaskGoal`/`TaskStep`/`TaskWait`（`time`/`file_exists`/`percept_event` + deadline），`~/.jarvis/goals/<id>.json` | `/tasks` 扩成两节 |
| W3.1 驱动器 | `core/tasks/driver.py`：一条后台线程 + 到期唤醒 + `run_subagent` 推进 + 播报必经 W1 闸门 | 后台自主推进，等待期间零 LLM 调用 |
| 宿主接线 | `serve/hub.py::_start_tasks/_stop_tasks`、`core/tasks/cli_host.py::CliSpineHost` + `main.py` | REPL 与桌面壳都能跑脊柱 |
| 配置 | `[actions]`（默认开）/ `[tasks]`（默认关）：`settings.py` + `settings_domains.py` + 两份 `settings.example.toml` + `config-docs/configuration.md` | — |

## 二、四处与计划文档的偏离（实施时核实后改的，不是随手偏离）

| 计划原文 | 实际做法 | 为什么必须改 |
|---|---|---|
| 复用 `Tool.is_destructive` 挂延时窗口 | 新增 `is_irreversible` | `is_destructive` 含"删除/覆盖"，`FileWrite` 也是 True 但可由检查点回滚；挂在它上面**每次存文件都要干等 30 秒** |
| 新增一个展示脊柱的命令 | 把既有 `/tasks` 扩成两节 | slash 命令表只有一份（`ui/slash_completer.py`），加同义入口会让用户和模型都先猜哪个是活的 |
| 到点重放"重建 ctx 走完整权限管线" | 直接按账本里**冻结的那一份 args** 调 `tool.call` | 无 UI 时 ASK 会被 fail-closed 判成 DENY（`orchestrator` 里那句"需要用户确认但当前环境无 UI"），等于把用户刚批准过的动作自己拦掉 |
| `file_exists` 复用 `percept/source_file.py` 的白名单扫描函数 | 直接 `Path.exists` | 脊柱只需要"这一个文件在不在"；拉整个扫描函数等于让兑现层依赖感知层的目录白名单 |

另外三处是口径级修正，也一并记清：

- **台账路径**：计划写 `~/.jarvis/tasks/goals/`，实际落 `~/.jarvis/goals/`。
  `~/.jarvis/tasks/<team>/` 已被协作 `TaskList` 当团队目录用，插一个 `goals` 子目录
  会在团队恰好叫 "goals" 时串目录、双方互相读坏。
- **重启后的延时窗口**：计划写"由 `resume_pending()` 补投到期窗口"，实际**一律作废并播报**。
  30 秒窗口的意义是"人还坐在前面能喊停"，重启后没人在场，把外发不可逆动作自动补发
  是最不该发生的事。
- **`SendMessageTool` 不声明不可逆**：计划把它列进去。它是本机文件信箱
  （`~/.jarvis/teams/`），拖进 30 秒窗口只会让 multi-agent 协议往返成倍变慢，
  且完全没有"出了本机收不回"的属性。理由写在代码注释里，防止以后"照计划补上"。

## 三、被测试打出来的四个真实缺陷

这四个都不是"测试写错了改测试"，而是**实现确实错了**，且每一个都能在生产里咬人。

### 3.1 账本每次重读凭空多一条幽灵记录

- **现象**：`actions.jsonl` 里若有半行损坏（进程崩溃时追加写被截断）的记录，
  `/undo list` 每刷一次就多几条撤不动的空白项，条数还会一直涨。
- **根因**：`_load()` 对每行直接 `EffectRecord.from_dict(data)`，而 `from_dict` 对
  **缺失 `id`** 的字典会现造一个随机 id。于是同一坏行每次读都"合法化"成一条新记录。
- **修法**：构造前先校验 `data.get("id")`，没有 id 就整行丢弃（与"坏行跳过"同一条口径）。
- **回归锁**：`test_bad_line_skipped_not_crash`。
- **可复用教训**：**"缺字段就用默认值补齐"的宽容解析，绝不能用在标识符上**。
  补齐 id 等于凭空造实体，重读一次涨一条，且不报错——这类缺陷只有"读两次比条数"的测试能抓住。

### 3.2 取消原因被状态流转吃掉

- **现象**：重启作废遗留待发动作后，`/undo list` 只显示〈已取消〉，看不出**为什么**被取消。
- **根因**：`set_state()` 只在 `state == failed` 时保留调用方传入的 `error` 文本，
  `cancelled` 走的是"清空 error"分支；而 `resume_pending()` 正是靠 `error` 传"上次进程遗留"这个成因。
- **修法**：保留范围从"仅 failed"扩为 `failed` + `cancelled`——**这两个状态的成因都是外部事实，
  不是本模块自己推导的**，没有理由丢。
- **回归锁**：`test_stale_held_records_are_cancelled`。

### 3.3 `resume()` 的复位改动整批丢失

- **现象**：进程被强杀时停在 `running` 的步骤，重启后**没有任何一条被复位**，
  台账永久卡在"那步还在跑"，驱动线程再也不碰它。
- **根因**：实现先把 `open_goals()` 返回的对象取出来改，再调 `store.update(goal.id, mutate)`；
  而 `update()` 是"锁内重新读盘 → 执行 mutate → 写回"，mutate 收到的是**刚读出来的新对象**，
  先前改在旧对象上的字段一个都落不了盘。
- **修法**：复位逻辑全部放进 `update` 的 mutate 闭包里，按 id 在锁内重找目标；
  计数用 `counted=[0]` 闭包累加。
- **回归锁**：`test_resume_resets_stale_running`。
- **可复用教训**：**"锁内读-改-写"型 API 与"先读出来改再整体写回"是两种互斥的用法**。
  混用的失败方式是静默的——不抛异常，只是改动没生效。用这类 API 时，第一件事是确认
   mutate 拿到的是不是自己刚改的那个对象。

### 3.4 重试到上限的终态用了 `blocked`，变成无退避死循环

- **现象**：一步连续失败到 `step_max_attempts` 后，驱动器**每一轮都重跑它**，
  目标状态也不会转 `failed`——预算被无限重试吃掉。
- **根因**：`_run_step()` 里判完上限后调 `_block(...)`，而 `_block` 会把步骤状态覆盖成
  `blocked`。脊柱的语义里 `blocked` = "仍需推进"（`open_goals` 把它算活跃），
  所以终态选错就正好是自旋。
- **修法**：定局写 `STEP_FAILED` + `block_reason=BLOCK_ERROR` + `next_run_at=0`，然后直接
  `_ask()` 问用户；`_block` 里删掉 `BLOCK_ERROR` 分支（阻塞类原因不该由"重试用尽"触发）。
- **回归锁**：`test_attempts_exhausted_marks_failed_and_asks`、`test_retry_backoff_not_due_zero_llm_calls`。
- **可复用教训**：**状态机里凡是"会被重新拾起"的状态，都不能拿来当终态**。
  终态集合与"待办集合"必须显式对偶，否则退避策略形同虚设。

### 3.5 顺带两条（同属"实现确实错了"）

- **hold 路径不回 `tool_result` 给 UI**：`_maybe_hold_irreversible` 已经通过 `ui.tool_use`
  开了一张工具卡片，却只返回 LLM 侧的 `ToolResultContent`，不给 `ctx.ui.tool_result`
  结案 —— 桌面/工作台上那张卡片**永远转圈**。（LLM 侧必须回非 error 结果这条由
  [dangling-tool-use-fix](dangling-tool-use-fix.md) 的老教训兜着，UI 侧是它的镜像缺口。）
- **失败退避只记在内存**：`TaskStep` 没有落盘字段，进程重启后未达上限的失败步骤被立刻
  重跑，"退避"在真实重启场景里等于不存在 → 新增 `TaskStep.next_run_at` 落盘。

## 四、顺手消除的三处重复（不新增能力，但少养一份实现）

| 重复 | 收敛到 | 收益 |
|---|---|---|
| `main.py` / `checkpoint_ops.py` / `journal.py` 各写一份 `CheckpointManager(...)` 构造（参数还不完全一致） | `core/checkpoint.py::manager_from_settings(settings, *, workdir, session_name, fallback_session)` | 检查点口径（启用开关、每会话配额、git 超时）只有一份；`main.py` 顺带退回 800 行以内 |
| 跨进程文件锁在 `collaboration/task_list.py` 里私有实现 | `agent/utils/filelock.py`（`ensure_dir` + `file_lock`），TaskList 与脊柱共用 | 计划里"不重复造第二套锁"的要求落地；两套台账不会互相写坏 |
| W1 打扰闸门实现挂在 `ProactiveEngine.gate_notify`（CLI REPL 没有 engine） | 模块级 `core/daemon/proactive.py::apply_notify_gate(...)`，`gate_notify` 变薄包装 | 脊柱播报不必为了过闸门而伪造一个 engine；**闸门只有一份实现**这条不变量继续成立 |

## 五、验证结果

| 层 | 内容 | 结果 |
|---|---|---|
| 账本 | `tests/core/test_action_journal.py` **30 例** | passed |
| 编排接入 | `tests/core/test_orchestrator_irreversible.py` **26 例**（含"被 hold 也必须回非 error 结果""工具一次都没被调""参数冻结"） | passed |
| 台账 | `tests/core/test_task_spine.py` **35 例**（三种等待各命中一次、deadline、坏文件不崩、崩溃安全落盘） | passed |
| 驱动器 | `tests/core/test_task_driver.py` **35 例**（等待期与退避期 LLM 调用数为 0、一次一步、定局 failed、播报必经闸门、`task` 权重乘算过门槛） | passed |
| 命令 | `tests/commands/test_undo_commands.py` **21 例** + `test_tasks_command.py` **16 例** | passed |
| 全量 | `pytest` | **2989 passed, 1 failed**（新增 163 例；见下行） |
| 文档链接 | `scripts/check_md_links.py` | dead 0；untracked = 本轮新增文件尚未入 git 索引 |

那 1 个失败是 `tests/test_md_link_checker.py::test_repo_docs_links_are_all_git_tracked`：
它要求文档链接的目标**已在 git 索引里**（CI 只检出被跟踪的文件）。本轮新增的
`18-W3兑现层.md`、`docs/test/w3-testing.md`、`agent/core/actions/`、`agent/core/tasks/`、
`undo_commands.py` 都还是未跟踪状态，所以必然报红 —— **`git add` 之后即消解，
不是内容缺陷**。基线核对：本轮开始前全量 2827 例（2821 passed + 6 failed，那 6 个失败是
既有测试替身 `engine._ctx = object()` 跟不上新增的 `ctx.extra["checkpoint_cid"]`，已改为
构造真 `ToolContext`）；2827 + 163 = 2990 = 当前收集数，**无既有用例被删或跳过**。

**尚未做人工实机走查**：账本与脊柱的行为目前只有单测与集成级证据。
逐项跑法与判据见 [w3-testing.md](../test/w3-testing.md)（含可直接粘贴的
`[actions]`/`[tasks]` 配置与手工目标 JSON）。

## 六、遗留边界（不是缺陷，是切片边界）

1. **脊柱没有模型侧创建入口**：`SpineStore.add_goal` / `TaskDriver.request_approval` 等
   写入 API 已就位，但没有能建目标的工具。实机上 `/tasks` 第二节暂时是空的。
2. **serve 协议与桌面面板未动**：`tasks.*` / `actions.*` RPC、`DESKTOP_COMMANDS` 登记、
   右栏"任务中心"改 spine 视图，全部属下一切片。
3. **token/费用未归集**：`SubagentResult` 不回报 usage，`TaskGoal.budget` 只是留位。
4. **退避是固定间隔**，不做指数翻倍（配置项名 `retry_backoff_s` 与注释已按实际语义写明）。
5. 清理/整理类维护工具（BLUEPRINT §6.2/6.3）要 W3 账本才安全 —— 账本本轮就位，工具另开。

## 七、经验总结

1. **"必须返回一个结果"的调用点，UI 侧和 LLM 侧要各自结案**。只补了协议侧（`tool_result`
   content）而漏了展示侧，表现是"卡片永远转圈"而不是报错——最难被单测覆盖的那一类。
   新增任何"绕过真实执行"的分支时，先把原来那次调用产生的**所有可见副作用**列出来逐条补。
2. **写盘状态要覆盖"崩溃后需要知道的每一件事"**：本轮两次栽在同一个模式上
   （退避时刻只在内存、复位改在旧对象）。加字段前先问：**进程现在被 kill，重启后
   还能不能推出正确行为？**
3. **计划文档里的"复用既有零件"要先核实零件的真实语义**：`is_destructive` 与
   `file_state`/`source_file` 两处在计划里都被高估了能力边界。**声明位越贴合真实意图，
   接线处越不需要写例外分支**——宁可新增一位，不要在旧位上打补丁。
4. **新事件源接旧闸门的教训会复发**：W2 因为 `percept:*` 没登记权重被整类误裁
   （[复盘](notify-budget-percept-relay-fix.md)），W3 若漏登记 `task` 就会重演"永远不吱声"。
   这条已从"记得登记"升级成可执行断言
   （`test_task_driver.py::TestBudgetWeightRegistered::test_task_weight_exists_and_clears_threshold`：
   既查权重表里有 `task`，又查 0.9×0.45 实算过 `min_salience`）。
5. **把"唯一实现"上提到模块层，比在第二个宿主里抄一遍闸门便宜**：CLI 没有 ProactiveEngine，
   正确解不是伪造一个 engine，而是让 engine 的方法变成薄包装。

## 八、实机走查后的补记（2026-10-21）

先生按 [W3 兑现层实机走查清单](../test/w3-testing.md) 亲自跑阶段二，暴露出两处单测覆盖不到的
问题，本轮已连同修复落地（细节与取舍见
[w3-delay-window-bypass-fix](w3-delay-window-bypass-fix.md)）：

| 暴露的问题 | 补的做法 | 现在的口径 |
|---|---|---|
| 不可逆动作进了待发窗口，模型改用 `Bash curl` 把同一件事做掉（沙箱对中风险命令默认自动放行） | 返回文案写死【不要绕道】并举例等价路径；再加一层硬拦窗口未结案时网络外发类中风险命令取消自动放行、改 ASK | 软约束 + 硬拦成对；应急可关 `[actions] hold_blocks_equivalent` |
| `/undo --task <goal>` 端到端列不到条目（驱动器从不写 `ctx.extra["task_id"]`，也没人回收账本 id，`TaskStep.journal_ids` 永远为空） | `run_subagent` 新增 `extra` 形参；`tasks/spawn.py`（从 `driver.py` 拆出的默认执行通路，为满足单文件行数上限）下传 `task_id`/`session_name`/`effect_ids`；编排器 `_note_effect_id` 回扣账本 id，`_run_step` 回填并落盘 | 归属两头都有单测钉住；走查清单新增 4.6b/4.6c 两个实机项 |

因此 五 那张验证表的数字已被后续补测超过（不是当时的记录有误）：`test_orchestrator_irreversible.py`
26  **31 例**、`test_task_driver.py` 35  **39 例**，并新增 `tests/tools/test_bash_hold_gate.py`
**18 例**；全量数见 README 开发指南的测试计数。六 遗留边界里的"到点执行结果不回灌对话"
与"没有模型侧创建入口"两条**依旧成立**，不在本轮修复范围内。
