# W3 实机走查：被权限拦下的步骤被报成「已完成」——四条断链的定位与修法

> 作者：aceFelix
> 日期：2026-10-22
> 相关模块：`agent/core/orchestrator.py`、`agent/core/tasks/{driver,spawn,approval_ops}.py`、
> `agent/collaboration/subagent.py`、`agent/core/checkpoint.py`、`agent/config/settings*.py`
> 相关文档：[18-W3兑现层](../architecture/18-W3兑现层.md)、
> [W3 实机走查清单](../test/w3-testing.md)、[W3 入口与 RPC 交付](w3-entry-rpc-delivery.md)

## 一、为什么会有这份复盘

W3 后端切片交付时，三份"待人工跑穿"的边界一直挂着：到点真执行、驱动器推进步骤、
桌面壳渲染。前两份只能由真 LLM 验证，所以此前**从未被实机跑过**，315 例单测里
没有一条覆盖它们。本轮把它跑穿了，一次跑出四条断链——其中两条是"看起来一切正常"
的谎报，单测全绿也照不出来。

实机跑法见 [W3 实机走查清单](../test/w3-testing.md) 的 §0.6（一条命令
`python scripts/livecheck_w3.py`，十二项判据逐行打灯）。

## 二、三轮实机的原始结果

台账与账本都注入临时目录，凭据只存在于子进程的内存 Settings 里，用户的
`settings.toml` 与 `~/.jarvis/goals` 一个字节都没被改。

| 轮次 | 时间线 | 播报 | 文件 | 账本 | `journal_ids` |
|---|---|---|---|---|---|
| 第一轮（修前） | `pending → blocked(+4s) → running(+48s) → done(+61s)` | 「已完成：1/1 步」 | **不存在** | **0 条** | `[]` |
| 第二轮（修 D2/D3 后） | 到 `running` 后停在受阻 | 「卡在第 1 步…需要授权：Bash 被拒」 | 存在（`SPAWN-OK`） | 1 条 | 有值 |
| 第三轮（判据修正后） | `pending → blocked(+4s) → running(+48s) → done(+52s)` | 「已完成：1/1 步」 | 存在 | 1 条 | `["69020a6d794f"]` |
| 脚本自跑 | 同上，等待期 20 秒 | 同上 | 存在 → 撤销后消失 | 1 条 | 非空 |

第一轮子代理在台账里的原话（这一步的 `state` 当时是 `done`）：

> 已尝试执行本步骤，但被权限拦截，因此未产生任何改动：调用 `FileWrite` 写入
> `…\w3-auto.txt` 时返回「权限拒绝: 需要用户确认但当前环境无 UI」。按规范未改用
> Bash 等其他途径绕过。**需要授权：**请为该工作目录授予文件写入权限…

**子代理完全按规范做了**（如实汇报、没有绕道），系统却把它的诚实读成了"活干完了"。

## 三、四条断链的根因（都定位到行）

### D1 子代理写死空规则集，用户配的权限规则对后台步骤无效

`agent/collaboration/subagent.py`：

```python
sub_checker = PermissionChecker(rules=RuleSet(), mode=permission_mode)
```

`RuleSet()` 是空集。宿主从 `settings.permissions_file` 读出来的规则永远传不进来，
于是"前台已经放行的目录，后台值守步骤照样被拒"——同一个工具在两个入口权限口径
不一致，用户无从理解。

注意 `FileWriteTool.check_permissions` 是**无条件 ASK**，而无人值守时 `parent_ui=None`，
管线 fail-closed 把 ASK 落成 DENY。所以 D1 的实际后果是：**后台步骤只要涉及写文件
就必然被拒**（除非另有授权租约）。

### D2 受阻被归一成成功，并向用户播报「已完成」

- `run_subagent` 的 `success=True` 判据只是"`QueryLoop` 跑完没抛异常"，不看做成了什么；
- `driver._run_step`：`if outcome.success: step.state = STEP_DONE`，进而 `goal.status=done`，
  再走定局播报「已完成：N/N 步」。

最反讽的是 `driver.py` 顶部注释本来就写着"无 UI 时 ASK 会被管线 fail-closed 拒成
DENY，驱动器把这种情况如实转成'需要授权'并来问用户"——**设计意图在，常量
`BLOCK_APPROVAL`、`_block()`、`_ask()`、`request_approval()` 全都实现好了并有单测，
唯独"从执行结果里识别受阻"这一环没人接**，所以那条通路一次也没被走过。

### D3 后台步骤的文件改动不入账，`/undo --task` 永远是空的

`sub_ctx` 的 `extra` 里没有 `checkpoint_cid`，而编排器的规则是
"拿不到本轮检查点就不记"（宁可不记，也不记一条撤不动的假可撤销）。于是：

- `actions.jsonl` 里后台步骤一条记录都没有；
- 台账 `TaskStep.journal_ids` 永远为空；
- `/undo --task` 与 `actions.exec undo_task` 只能回"没有已执行的副作用记录"。

即：**任务脊柱改的工作，今天完全不在回退体系里**。

### D4 子 ctx 没有 `settings`，`[actions]` 的用户配置对后台步骤无效

`run_subagent` 建 `ToolContext` 时不传 `settings`，编排器读
`actions_undo_window_s` / `journal_enabled` / `track_local_writes` 全部退成默认值。
用户把延时窗口改成 90 秒或把记账关掉，后台步骤照旧按 30 秒记账。与 D1 同一类错。

## 四、修法

### 4.1 受阻信号取自「第一现场」，不靠解析文本

`orchestrator.execute_calls` 在 DENY 落地处记一条结构化事实：

```python
EXTRA_DENIALS = "denials"
record_denial(ctx, tu.name, reason)      # 只记工具名与原因，不记参数
```

容器由 `default_spawn` 建好写进子 `ctx.extra`（与既有的 `effect_ids` 同一套路：
必须是同一个列表对象），跑完取回装进 `StepOutcome.denials`。

为什么不做关键词匹配："需要授权"是模型写的自然语言，匹配既会漏判也会误判；
而被拦下的那一行代码就是最可靠的证人。

### 4.2 驱动器接回既有通路，并挂安全退避

```python
if outcome.denials and not effect_ids:
    step.next_run_at = self.blocked_hold()          # APPROVAL_HOLD_S = 3600s
    self._block(goal, step, BLOCK_APPROVAL, f"需要授权：…", mark=False)
elif outcome.success:
    step.state = STEP_DONE
    if outcome.denials:
        step.result += f"\n本步另有 {len(...)} 次操作被权限拦下：…"
```

`BLOCK_APPROVAL` 这一步**没有 `wait` 可以护身**：`_evaluate` 对无等待条件的步骤直接
执行，所以不退避就会每轮重跑一次 LLM——那正是"不烧轮询"这条验收最隐蔽的反例。
退避只是安全网：用户答复会清零它并立刻 `wake()`。

顺带修了 `request_approval` 的一个语义错：它过去只要目标存在就返回 True，**批一个
不存在的步骤也报成功**。这个返回值是给面板看的承诺，现在没命中步骤就返回 False
且一个字段都不碰。

### 4.3 任务级检查点，与消息级分开守配额

`default_spawn` 跑步骤前调 `_step_checkpoint(...)` → `CheckpointManager.create(kind="task")`。
`_prune` 改为按 `kind` 分族各自守配额：

```python
quotas = {"turn": self._max, "task": self._task_max}   # 20 与 8 互不侵占
```

若不分开，后台每推一步吃一条，几个目标就会把用户 `/rewind` 的历史挤没——
这正是副作用账本顶部警告过的做法。旧 `manifest.json` 里没有 `kind` 的条目一律
归入消息级，避免升级后按更小的配额把历史修剪掉。

配置项：`[tasks] checkpoint_max`（默认 8）。没装 git 或 `checkpoint_enabled=false`
时 `_step_checkpoint` 照实返回空串，入账数就是 0——**不假装可回退**。

### 4.4 子代理继承宿主的规则与配置

`run_subagent` 新增 `permission_rules` / `settings` 两个参数，`default_spawn` 从宿主
`Settings.permissions_file` 现取规则（不缓存：YAML 用户随时可改，缓存会让"改了规则"
变成静默失效）并原样递下。

**继承规则不等于放宽模式**：`permission_mode` 仍是 `DEFAULT`，`deny` 永不可放宽。

有一条既有保守设计必须写清楚，否则会误以为"配了规则怎么还不放行"：

| 规则写法 | 能否放宽 `FileWrite` 的特判 ASK |
|---|---|
| `- pattern: "FileWrite"` （裸工具名） | **不能**——等价于给无人值守后台开 yolo |
| `- pattern: "FileWrite(**)"` （带路径 spec） | 能，且只在这条路径覆盖的范围内 |
| `- pattern: "FileWrite(**)"` + `value: deny` | 拒绝，照旧硬拦 |

## 五、第二轮踩到的反方向错误（判据的二次修正）

第二轮跑出来的是：文件**真写成了**、账**真记上了**，播报却是「卡在第 1 步…需要
授权：Bash 被拒」。原因是我第一版判据写成"有 DENY 即受阻"，而子代理写完文件后又
调了一次 `Bash` 去确认目录，那一次被拒。

**对着一个已经完成的目标喊"需要授权"，与第一轮对着没做完的目标喊"已完成"是同一
类错误的两个方向**：前者会让用户去批一个根本不存在的阻塞，同样是在消耗用户的信任。
所以最终判据是"有 DENY **且本步没有任何副作用产出**"才算受阻；已有产出却夹着被拒，
则照实把被拒项附在结论里落 done——既不谎报，也不瞒。

## 六、实证与回归

`scripts/livecheck_w3.py`（本轮新增）十二项判据全绿，其中三条正是本次修复的对象：

```
✓ 结论与实际产出一致（不谎报）: True
✓ 副作用进了账本（不是假可撤销）: 1
✓ 台账 journal_ids 回填了（/undo --task 查得到）: 1
✓ undo_task 第二拍真撤掉、文件消失: True
```

单测新增 47 例（全量 3193 passed）：

| 文件 | 例数 | 钉住什么 |
|---|---|---|
| `tests/core/test_orchestrator_denials.py` | 13 | 拒绝登记在第一现场；ALLOW/被批的 ASK 不记；通路把清单回报 |
| `tests/core/test_driver_approval_blocked.py` | 14 | 受阻不落成 done；退避与只问一次；批准后清零并唤醒；批不存在的步骤回 False |
| `tests/core/test_checkpoint_task_quota.py` | 8 | 两族配额互不侵占、修的是本族最旧、旧 manifest 向后兼容、任务级照样能回滚 |
| `tests/collaboration/test_subagent_permission_rules.py` | 6 | 规则继承生效；**裸名 allow 不放宽工具 ASK**；deny 永胜；checker 用传入的规则 |
| `tests/core/test_w3_step_side_effects.py` | 6 | 真编排器 + 真 FileWrite + 真 shadow git：入账→回填→按目标撤销；没检查点就不记 |

其中"有产出却夹着被拒"两条是对照写的（同一 denial，有无产出决定走 done 还是走问人），
这样下一次有人把判据往任一方向改歪，都会立刻红。

## 七、结构治理

`driver.py` 本次改动会突破单文件 800 行上限，所以按"一族职责一个模块"拆出
`agent/core/tasks/approval_ops.py`（`_block` / `_ask` / `request_approval` /
`APPROVAL_HOLD_S`），`TaskDriver` 继承 `ApprovalOpsMixin` 后调用点一字不改——
形态与 serve 的 `PerceptRpcMixin` 一致。拆的理由也写在模块顶部：阻塞与询问的变化
原因是"打扰预算又调了""面板要一个放行入口"，与调度节拍的变化原因不同。

## 八、遗留与边界（不得当成已完成）

- **到点真执行的"成功"依赖用户配置**：默认配置下后台步骤写文件仍会被拒（D1 修的是
  "配了规则能生效"，不是"默认放行"）。这条现在是**如实播报"需要授权"**而不是谎报，
  但用户要真的无人值守写文件，得配 `FileWrite(<路径spec>)` allow 规则或授权租约。
- **桌面「任务中心」面板仍未做**：`tasks.exec` / `actions.exec` 已有单测与实机证据，
  面板在 jarvis-desktop 仓，那边要消费的是本轮新增的 `need_confirm` + `preview` 两拍
  与 `kind=task` 播报。
- **目标拆解仍靠人给步骤**：一句话拆成多步不在本层。
- **token/费用未归集**：`SubagentResult` 不回报 usage，`TaskGoal.budget` 只是留位。
- **受阻后模型换合法通路做完了步骤**：本轮第二次实机就是这种情况（FileWrite 成功 +
  Bash 被拒），判据现在会落 done 并附说明；但"子代理是否该被禁止尝试等价通路"仍是
  绕道拦截那一层的话题，本文件不改变它的口径。

## 九、文档同步

| 文档 | 改了什么 |
|---|---|
| `docs/test/w3-testing.md` | 新增 §0.6 实机自检脚本（十二项判据 + 凭据前置 + 隔离声明）；受阻→批准一段改为可跑步骤 |
| `docs/architecture/18-W3兑现层.md` | 模块表加 `approval_ops.py`；§十边界按本轮实证改写；配置表加 `[tasks] checkpoint_max` |
| `docs/roadmap/PLAN-butler-autonomy-upgrade.md` | 进度与 §4.3 文件表补本轮四条修复 |
| `docs/blueprint/BLUEPRINT.md` | W3 里程碑行：实机已跑穿，单测 315→362 |
| `README.md` / `README.en.md` | 阶段 10 行：删去"待人工跑穿"，改为已跑穿 + 一条命令 |
| `docs/guide/development.md` | 全量测试数、`scripts/livecheck_w3.py` 用法 |
| `docs/architecture/12-配置系统.md` | dataclass 字段与 TOML 两处补 `tasks_checkpoint_max` / `[tasks] checkpoint_max` |
| `docs/architecture/00-索引.md` | 本文件的链接与摘要；补 `approval_ops.py` 与 §0.6 实机自检口径 |
