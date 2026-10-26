# 目标卡在「需要授权」时终端没有批准入口（2026-10-26）

- **模块**：`agent/commands/handlers/collab_commands.py`、`agent/core/tasks/driver.py`
- **发现方式**：人工实机测试（阶段 10 走查 §4.2，跨会话任务脊柱）
- **严重度**：体验断链（播报承诺了一个终端做不到的动作，用户被卡住且不知怎么办）

## 现象

用户按走查清单建了一个目标：

```
/tasks add "在工作目录里创建文件 w3-spine.txt，内容写 spine-ok" --wait time:+90s
已建立目标〔2b0beee547e5〕：…（1 步，先等 time）
  后台驱动线程在跑：条件满足会自己往下推
```

90 秒后到点推进，弹出一条播报：

```
⏰ 目标「"在工作目录里创建文件 w3-spine.txt」卡在第 1 步「"在工作目录里创建文件 w3-spine」：
   需要授权：FileWrite 被拒：需要用户确认但当前环境无 UI。批准后这一步会立刻重试。
```

用户敲 `y` 想批准，结果那句 `y` 被当成普通对话发给了模型，模型逐字回答
「我这边并没有待确认的事项——您这个"y"是回复哪一件事？」

三个问题同时暴露：

1. **该在哪批准？** 播报只说"批准后就会重试"，没说去哪按；
2. **敲 `y` 为什么没用？** 用户的第一反应完全合理；
3. **目标名里为什么有引号？** 播报里出现 `目标「"在工作目录…」`。

## 排查

1. **为什么被拒**：`FileWrite` 无条件 ASK，子代理跑在 `messages=[]、ui=None` 的最小上下文里，
   ASK 被 fail-closed 判成 DENY。这是**设计**（2026-10-22 已改成"如实播报受阻"而不是谎报完成），
   目标没坏，只是停在第 1 步等授权。

2. **为什么 `y` 没用**：`approval_ops._ask()` 只做两件事——`self._notify(...)` 发一条播报 +
   `step.asked = True` 打标记，**不等待任何输入**。REPL 里没有挂起的提问，`y` 就是一句普通输入。
   所以模型的回答其实是对的。

3. **为什么终端没有批准入口**（真根因）：`approval_ops.request_approval()` 的 docstring 写着
   "用户对一次『需要授权』的答复（**供终端命令与桌面「任务中心」面板调用**）"，
   但 `/tasks` 的子命令只有 `add/show/pause/resume/cancel/advance/delete` ——
   **终端从来没调用过它**；真正接上的是桌面 `tasks.exec approve` → `driver.approve_step`。
   接口留了位、注释写了意图，唯独没有接线。

4. **不干预会怎样**：`APPROVAL_HOLD_S = 3600`，受阻步骤的 `next_run_at` 是"一小时后"。
   到点它会自己再试一次，而没人签租约 → 同一个 ASK 再落 DENY → 再播一条同样的"需要授权"。
   也就是：一条不可操作的通知，等于一小时的静默失败循环。

5. **引号**：`_parse_tasks_args()` 把剩余 token 直接 `" ".join(text_parts)`。REPL 不是 shell，
   不负责拆引号，于是 `"目标文本"` 的首尾引号原样写进台账：目标名、步骤文本、
   连交给子代理的任务描述都带着（播报里那个 `目标「"在工作目录…` 就是它）。
   而走查清单 §4.2 教的正是带引号的写法。

## 根因

- **编排层**：终端对"需要授权"这一步没有出口，`request_approval` 是"半截线"；
- **文案层**：播报承诺"批准后重试"却不给可执行路径，桌面/终端两种宿主的差异没写出来；
- **解析层**：文档写的带引号用法与实际解析不一致。

## 修复

1. `agent/commands/handlers/collab_commands.py`
   - 新增 **`/tasks approve <目标id前缀>`**：`_tasks_approve()` 复用 `driver.approve_step()`，
     与桌面「批准并重试」**同一条后端**——按本步 `denied_tools` + 目标 `workdir/**` + 30 分钟
     签一条只收窄范围的租约，再把步骤退回待执行（光复位不够：重试走同一条无 UI 判定，
     同一个 ASK 会再落成 DENY）。
   - 三种拒绝路径都如实说：目标没找到 / 当前不是等授权状态 / 驱动线程未起（后者给出
     "`/lease` 签租约 + `/tasks advance` 手动推"的退路，不假装批好了）。
   - `_TASKS_USAGE` 与 `agent/ui/slash_completer.py` 补上 `approve`（顺带修掉补全里
     `event:<源>/< kind>` 的多余空格）。
   - 新增 `_strip_wrapping_quotes()`：只剥**整体包住**文本的一层成对引号（半角/全角/「」），
     判据是"内层不得再出现同名引号"——`"a.txt" 与 "b.txt" 都改成 c` 这种真的在用引号的句子
     必须原样保留，不能被削成 `a.txt" 与 "b.txt`。

2. `agent/core/tasks/driver.py`
   - 受阻播报补上可执行路径，并把目标 id 前缀直接写进去：
     「需要授权：FileWrite 被拒…。终端敲 `/tasks approve 2b0b`，或桌面右栏该目标行点
     「批准并重试」；批完这一步会立刻重试。」
   - 注释写明为什么必须带这句（原句只有"批准后会重试"，用户就只能敲 `y`）。

3. 文档同步：`docs/guide/commands.md`（命令表）、`docs/architecture/18-W3兑现层.md`
   （子命令表 + §十"三条路"改四条并说明后两条同源）、`docs/test/w3-testing.md`
   新增 §4.2e 走查项（含"不要敲 y"）、`docs/roadmap/PLAN-butler-autonomy-upgrade.md`
   与 `README.md` 的子命令列表。

## 验证

```
python -m pytest tests/commands/test_tasks_command.py -q                 # 38 passed（新增 4 例）
python -m pytest tests/core/test_driver_approval_blocked.py tests/commands -q   # 93 passed
python -m pytest tests/core/test_spine_ops.py tests/commands -q          # 119 passed（含新增 2 例）
python -m pytest -q                                                      # 3237 passed，无回归
python scripts/check_md_links.py                                         # dead links: 0 / untracked: 0
```

新增 4 例：整体引号被剥（目标名与步骤文本都干净）、内层引号保留（反例钉住"只剥一层"）、
非等授权状态时给明确拒绝、驱动线程未起时不假装批准。

**待人工复验**：同一目标重跑一遍 §4.2，到点受阻后敲 `/tasks approve <id前缀>`，
几秒内该步变 `done`、`w3-spine.txt` 真落盘，`~/.jarvis/leases.json` 有那条限定租约；
目标名不再带引号。

## 同日后续：走查途中连带发现的六处小缺陷

接着按清单走完 §4（任务脊柱）/ §5（整轮回滚）/ §6（待发窗口）/ §7（无人值守）时，
又撞出六处“一句话的事”，全部当天修掉：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `/back` 呈批后写「重放执行属阶段 10 W3」 | 阶段 8 时代的预告，W3 已交付但**交付的是任务脊柱受阻步重试**，待批清单仍不重放——等于预告不存在的功能 | 改「只呈批不自动重放：回来说一遍即可；不想再被反问就用 `/lease`… 或 permissions.yaml 的 allow 规则放行」 |
| 2 | 打完 `lease FileWrite …`（漏斜杠）后模型自行重试了被拒的 `FileWrite` | 模型把“看起来像授权”的话当成了许可，违反了 tool_result 里已明写的「请勿重试」 | 核心原则新增第 7 条**权限被拒即止**：不得重试/换等价工具/改写命令；即使先生的话听起来像授权也不能当许可，只有明确重新下达同一操作才算 |
| 3 | `--wait time:+600` 报「等待时间已过期（1970-01-01T08:10:00）」 | 漏单位的相对时间不匹配 `^\+(\d+)([smhd])$`，落入 `parse_iso` 被当成 **epoch 600 秒**；用户完全看不出是自己少写了单位 | 新增 `_hint_relative_unit()`（`parse_wait` 与 `wait_from_spec` 两处共用）：「相对时间要带单位：+600 → 写成 +90s / +10m / +2h / +1d」 |
| 4 | `/tasks approve` 回显「已签发租约 ：」，租约号消失 | 回执里的 `[lease-id]` 被 Rich 当样式标签吞掉；桌面走 RPC 不经 Rich，所以只有终端暴露 | CLI 侧对该回执 `_esc()` 转义 |
| 5 | 重启后等条件的步骤显示「◇ 卡在第 1 步…（waiting）」 | 驱动复查会把等待中的步骤标 `blocked(waiting)`，而展示层对所有 blocked 统一说“卡在”——用户第一反应是“出事了” | `block_reason == waiting` 且有等待条件时改说「○ 第 N 步在等：xxx（最迟 …）」 |
| 6 | `/undo` 整轮回滚后，同轮的 `FileEdit` 仍写「可撤销」 | `mgr.restore(cid)` 撤的是**整个检查点**，而 `undo_record` 只 `set_state` 被点的那一条 | 新增 `ActionJournal.same_checkpoint(cid)`；回滚成功后把同 cid 下所有 `applied` 一并标 `undone`，回执注明“同轮另 N 项一并记为已撤销”（别的轮次不动） |

第 3 条是本轮唯一影响数据判断的：它让用户以为自己的命令对了。第 4/5 条都是**同一后端两种宿主**
不一致的老毛病（桌面正常、终端不正常），值得警惕。

## 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/commands/handlers/collab_commands.py` | 新增 `/tasks approve`、`_strip_wrapping_quotes`、用法与分派；租约回执转义；等待中改说“在等” |
| `agent/core/tasks/spine_ops.py` | 新增 `_hint_relative_unit()`（`time:+600` 类漏单位不再报 1970 年） |
| `agent/core/tasks/driver.py` | 受阻播报补“终端 /tasks approve / 桌面批准并重试”路径 |
| `agent/commands/handlers/policy_commands.py` | `/back` 去掉“重放属阶段 10 W3”的过期预告 |
| `agent/prompts/text_blocks.py` | 核心原则新增第 7 条「权限被拒即止」 |
| `agent/ui/slash_completer.py` | 补 `/tasks approve` 补全项；修 `event:<源>/< kind>` 空格 |
| `tests/commands/test_tasks_command.py` | 新增 `TestQuotesAndApprove` 4 例；help 断言加 `approve`；等待态措辞断言更新 |
| `tests/core/test_spine_ops.py` | 新增 2 例（漏单位要报单位、四种粒度都算得出） |
| `agent/core/actions/journal.py` | 新增 `ActionJournal.same_checkpoint()`；整轮回滚后一并标记同轮条目 |
| `tests/core/test_action_journal.py` | 新增 1 例：同轮连带标 undone、别的轮次不动 |
| `docs/guide/commands.md`、`docs/architecture/18-W3兑现层.md`、`docs/test/w3-testing.md`、`docs/roadmap/PLAN-butler-autonomy-upgrade.md`、`README.md` | 子命令表与走查项同步 |

## 经验总结

1. **"注释里写了对接意图"不等于接上线。** `request_approval` 的 docstring 明写"供终端命令与
   桌面面板调用"，而终端的子命令表里根本没有它——和当年"`schedule` 权重登记了但提醒不过闸"
   是同一类缺陷：接口留位、线没接，看着像已完成。凡是注释里出现"供 X 调用"，都该 grep 一下 X。
2. **播报文案只要承诺一个动作，就必须同时给出"在哪做"。** 没有操作路径的通知不是提示，
   是把用户留在原地——他只会做最自然的那个动作（敲 `y`），然后被模型反问一句"你要确认什么"。
3. **REPL 不是 shell。** 文档里凡是写 `"带引号的参数"` 的命令，解析层就得自己剥引号，
   否则引号会进数据（这里还一路传给了子代理的任务描述）。剥的判据要窄：只剥整体成对的，
   内层同名引号说明用户真的在用它。
4. **受阻退避是 1 小时，所以"不可操作"的代价被放大了 60 倍。** 一条没给出口的通知不是
   "等一下就好"，而是"一小时后原样再发生一次"。凡是带退避的阻塞态，文案比逻辑更要紧。
