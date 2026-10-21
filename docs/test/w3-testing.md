# W3 兑现层实机走查清单（Windows / Git Bash）

> 阶段 10（W3 兑现层：副作用账本 + 不可逆动作延时窗口 + 任务脊柱）交付后的人工验收流程。
> 终端命令以 **Git Bash（MINGW64）** 为准；PowerShell 差异在括号里注明。
> 改完 `settings.toml` 要**重启后端/REPL** 才生效（本轮没有面板热更新入口）。
>
> 阶段 10 收尾切片（2026-10-21）补齐了**建目标的三个入口**：终端 `/tasks add`、
> 模型侧 `TrackGoal`、serve `tasks.exec` / `actions.exec`。所以本清单里只有
> 阶段一～三（账本）需要“让模型干活”，阶段四/五的**写台账部分不需要 LLM 参与也能跑**，
> 想直观感受“替我盯着”，从 §4.2 与 §5 开始最快。
> **想 3 秒先拿到结论：直接跑 §0.5 的一键走查脚本**（四十项逐行打灯，不经模型、不碰用户数据）。
>
> @author aceFelix　|　更新：2026-10-21

## 0. 前置（一次性）

### 0.1 两条主线各跑一次就够

| 主线 | 需要 LLM | 需要凭据 | 需要桌面壳 |
|---|---|---|---|
| W3.2 账本 + 延时窗口 + `/undo` | 只要模型肯调工具（或按 §2 用命令行直接记账） | 发真邮件要 `[email]` 的 SMTP；不想发就用 §3 的本地写 | 可不要（REPL 就够） |
| W3.1 任务脊柱 | 建目标不靠 LLM（`/tasks add` / `tasks.exec` 直接落台账）；**推进步骤**才需要（驱动器起 Subagent） | 可用 LLM 配置 | 可不要（阶段五手测 RPC 才需要 serve） |

### 0.2 三个窗口的分工

| 窗口 | 干什么 | 命令 |
|---|---|---|
| A | 跑 REPL（本轮走查以 REPL 为主） | `./.venv/Scripts/python.exe -m agent` |
| B | 看副作用流水 | `tail -F ~/.jarvis/actions.jsonl` |
| C | 看目标台账 | `watch -n 2 'ls -1 ~/.jarvis/goals/ 2>/dev/null; cat ~/.jarvis/goals/*.json 2>/dev/null'`（无 `watch` 就手动 `cat`） |

`tail` 必须用大写 `-F`：`actions.jsonl` 是**出现第一条记录时才创建**的，小写 `-f`
会直接退出（`no files remaining`）。

### 0.3 贴配置

`~/.jarvis/settings.toml` 追加（REPL 与 serve 共用同一份）：

```toml
[actions]
journal_enabled = true     # 账本总开关
undo_window_s = 30         # 不可逆动作延时窗口（秒）；走查阶段可临时改 8 便于等待
track_local_writes = true  # 本地破坏性写记一条可回滚条目
irreversible_tools = []    # 应急口子：本机确实要多保护的工具才填（跑完改回 []）
hold_blocks_equivalent = true  # 窗口未结案时网络外发命令改问人（§2.8/§2.11 验的就是它）

[tasks]
enabled = false            # 阶段 4 之前保持 false，先只验账本
blocked_ask_days = 3
step_max_attempts = 3
retry_backoff_s = 60
wait_deadline_hours = 72
```

⚠️ **TOML 转义陷阱**（同 W2）：路径里的反斜杠要写 `\\`，或者直接用正斜杠。
字符串数组要写 `["工具名"]`，不要写 `['工具名']`。

⚠️ **不要把只读工具填进 `irreversible_tools` 做演示**（2026-10-21 实测踩过）：拿 `WebFetch`/
`FileRead` 这类可重来、无副作用的工具演"不可逆动作"，只能演出一堆成本（白等、结果重复
执行），还恰好避开本节真正要验的东西。没配 SMTP 就用下面的假收件人，语义一样成立。

### 0.4 确认没有残留实例

```bash
tasklist | grep -i electron      # 有输出就在托盘图标上右键「退出」
```

REPL 与 serve **不要同时开**：两边都会装配账本并各自起延时窗口，同一条流水被两个进程
读到会出现"谁都不知道自己那份发了没有"的假象。

## 0.5 先跑一键走查（约 3 秒，不经模型、不碰真实 ~/.jarvis）

想先确认"W3 这套东西到底跑不跑"，不必开 REPL、不必等 90 秒、不必配凭据：

```bash
./.venv/Scripts/python.exe scripts/smoke_w3_spine.py            # 全跑（含 serve WS 端到端）
./.venv/Scripts/python.exe scripts/smoke_w3_spine.py --no-serve # 缺 websockets 时只跑进程内部分
```

脚本用的是**真的命令处理器、真的 `TrackGoal`、真的 `ToolOrchestrator`、真的 WS 服务器
与真的 WS 客户端**，把下面这些一次跑完并逐行打结论（共 40 项）：

| 段落 | 亲眼看到的东西 |
|---|---|
| 入口一 `/tasks add` | 目标落盘、分号拆步、`wait` 挂 `steps[0]`、`time` 类不补兜底时限 |
| 入口二 `TrackGoal` | 一句话建目标、回显带驱动线程状态、`file_exists` 必带兜底时限 |
| 一层语义 | 两个入口的明细字段集完全一致、回显的 id 真能查回来 |
| 写入前校验 | 过去的时刻 / 无前缀 / 缺参数都没建出目标（先解析后写盘） |
| `advance` | 等待条件清空，`result` 留下「用户以 advance 跳过等待条件」 |
| 定局与删除 | `cancel` 后 `resume` 被拒且台账一字未动；`delete` 后台账文件真消失 |
| 前缀歧义 | 命中两个目标时 `find_goal` 返回 None（造两个同前缀 id 钉成确定检查） |
| 延时窗口 | 工具**一次都没被执行**、账本多一条 `held` 并冻结参数、回给模型的不是报错 |
| 绕道硬拦 | 有未结案动作时 `curl` 改问人，`git commit` / `ls` 照旧放行；开关关掉即退回 |
| 入口三 RPC | `tasks.exec create` 回结构化 `goal`、`delete` 第一拍带目标明细、`cancel_hold` 一拍生效、未知动作报错不断连、信封键不泄漏 |

⚠ **脚本测不到的三段仍然要人工走查**（所以本清单不作废）：① 到点**真执行**（脚本在到点前
就把它撤回了，见 §2.5）；② 驱动器**推进步骤**（要调 LLM，见 §4.5）；③ 桌面壳渲染
（属 jarvis-desktop 仓，见 §5.10）。

脚本只写临时目录（`~/.jarvis` 一个字都不碰），跑完自动清理；它自身也有回归
（`tests/test_w3_smoke_script.py`，4 例）：脚本坏了自己会红，不会指着假象说"通过"。

## 1. 阶段一：账本装配与遗留作废

| # | 操作 | 看到这样算过 |
|---|---|---|
| 1.1 | 窗口 A 起 REPL | 正常出 banner，无 traceback |
| 1.2 | 手工造一条遗留待发记录（模拟上次进程没发出去）：窗口 B 执行 `python - <<'PY'` 写入一行 `{"id":"stale0000001","tool":"SendEmail","args":{},"preview":"发给 test@x.com","reversible":false,"undo_spec":{"kind":"none"},"state":"held","due_at":1.0}` 到 `~/.jarvis/actions.jsonl` | `grep stale0000001 ~/.jarvis/actions.jsonl` 有输出 |
| 1.3 | 退出 REPL 再起一次 | 启动阶段直接提示「上次进程遗留一项未发出的预约动作已按取消处理：发给 test@x.com（/undo list 可查）」 |
| 1.4 | 窗口 B：`cat ~/.jarvis/actions.jsonl` | 出现两条 `stale0000001`：旧行 `state":"held"` 原样保留，新行 `"state":"cancelled"`（追加写，不重写历史） |
| 1.5 | 窗口 A：`/undo list` | 该项列在表里，状态〈已取消〉；`cat ~/.jarvis/diag.log`（`settings.debug=true` 时）有「作废上次遗留的待发动作 1 项」 |

**这条是本轮最要紧的一条**：重启后绝不能把用户没当场确认第二次的不可逆动作自动补发。

## 2. 阶段二：不可逆动作延时窗口（用邮件）

前提：`[email]` 里 SMTP 可用。不想真发信就用一个假收件人（发送会失败，但**窗口内的
现象完全一样**，且 §2.4 的撤回本来就不该发出去）。

| # | 操作 | 看到这样算过 |
|---|---|---|
| 2.1 | 窗口 A 说：「用 SendEmail 给 test@example.com 发一封标题为 W3 走查、正文只写 ok 的邮件」，权限询问按 `y` | 立刻回一行「待发窗口已开启（30s）：给 test@example.com 发一封信」，并且工具卡片**已结案**（不是转圈） |
| 2.2 | 看模型侧 | 模型收到的 tool_result 是**正常结果**（含「已排入 30 秒待发窗口，尚未执行」+ `/undo cancel` 提示），不是报错 |
| 2.3 | 窗口 B `tail -F` | 一条 `"state":"held"`，带 `"args"`（收件人/标题/正文都在里面）与 `"due_at"` |
| 2.4 | 30 秒内输入 `/undo cancel` | 提示「已撤回，未执行：…」；**收件人确实没收到信**；`actions.jsonl` 新增 `"state":"cancelled"`；到点后不再有 `applied` |
| 2.5 | 重跑 2.1，这次什么都不做，等 30 秒 | 到点后自动播报「预约动作已执行：…」；`actions.jsonl` 新增 `"state":"applied"`；收件人此刻才收到信 |
| 2.6 | 再重跑 2.1，然后在窗口里立刻按 `Ctrl+C`（中断本轮）后再等 | 到点不发送，转 `"state":"cancelled"`，原因写「用户已中断本轮」 |
| 2.7 | `/undo <那条 applied 的 id 前 4 位>` | 明确报**无法撤销 + 原因**（「该动作已离开本机，无法撤回」），不会假装成功 |
| 2.8 | **绕道回归项**：重跑 2.1 被 hold 后什么都不做，看模型下一步 | 两层都得成立：① 模型应停下汇报"已排入待发窗口，等您决定"，同一轮里**不应**出现顶替发送的等价调用（`Bash curl smtp://…`、`python -c "import smtplib…"`）；② 若它真试了，屏幕上**必须弹出确认询问**（带"待发窗口"字样）而不能静默执行——沙箱平时对中风险命令自动放行，窗口未结案期间这条捷径被临时关掉（`hold_blocks_equivalent`）。只看到 ①就过了、把 ② 当可选，等于没验硬拦 |
| 2.9 | 被 hold 期间随手让它做一件无关的只读事（如「看看当前目录有什么文件」） | 只读操作**不该**被窗口拖住或反复问——拦的只是那一条外发调用，不是整轮会话 |
| 2.10 | 被 hold 期间让它跑一条本机中风险命令（如 `git commit -m w3`） | 照旧**自动放行**（不收紧）：硬拦只针对网络外发通路，不该把不相干的命令一起拖进确认风暴 |
| 2.11 | 把 `hold_blocks_equivalent` 改成 `false`，重启后重跑 2.8 | 等价外发命令回到"沙箱内静默执行"——这就是关掉硬拦的代价，得亲眼看一下才知道默认开着是对的（验完改回 `true`） |

## 3. 阶段三：本地写的整轮回退

前提：当前 `workdir` 是个 git 仓库或允许 shadow git（`git --version` 有输出）。

| # | 操作 | 看到这样算过 |
|---|---|---|
| 3.1 | 窗口 A：「在当前目录新建一个文件 w3-probe.txt，内容写 before」 | 文件出现；`actions.jsonl` 新增一条 `FileWrite`，`"reversible":true`、`"undo_spec":{"kind":"checkpoint","cid":"…"}` |
| 3.2 | 窗口 A：「再往 w3-probe.txt 里追加一行 after，另外新建 w3-probe2.txt」 | 两个文件都变了；这一轮**又记了一条**（同一轮可能多条） |
| 3.3 | `/undo list` | 列出的文件类条目状态〈可撤销〉；末尾有一行「（文件类撤销是整轮回滚：恢复到该轮开始前的检查点，本轮其余改动会一并回退）」 |
| 3.4 | `/undo`（不带参数 = 撤上一步） | **先展示影响面**（哪些文件改回去、几个新增未跟踪文件会被删）再问「确认回滚工作区？〈y/N〉」 |
| 3.5 | 回答 `N` | 「已放弃撤销，工作区未改动」；文件**一个都没变**；账本状态仍是 applied |
| 3.6 | 再 `/undo`，回答 `y` | 「撤销结果：工作区已恢复到该消息发出之前」；`w3-probe2.txt` 消失、`w3-probe.txt` 回到 before 之前；`actions.jsonl` 新增 `"state":"undone"` |
| 3.7 | `/undo`（此时该轮已经撤过） | 报「该项已经撤销过了」，不会重复回退 |
| 3.8 | 临时把 `settings.toml` 的 `undo_window_s` 改成 `0`，重启后跑 2.1 | 邮件**直接发出**（不拦），但账本仍记一条 `applied` + `undo_spec.kind=none`；这就是"只延后、不豁免"与"关了窗口照样记账"的分界 |

## 4. 阶段四：任务脊柱（跨会话目标 + 断点续跑）

脊柱现在有三个入口能把目标写进台账，**语义完全一致**（都走
`agent/core/tasks/spine_ops.py`）：终端 `/tasks <子命令>`（用户直接测，不经模型）、
模型侧 `TrackGoal`（一句话建目标）、serve `tasks.exec`（见阶段五）。
先把 `[tasks].enabled = true` 再重启 REPL。

> 下文把目标 id 写成 `w3probe1` 只是为了方便 `grep`；用入口建的目标是 12 位随机 id，
> **拿回显的 id 前 4 位当前缀用即可**（`/tasks show a1b2`、`/undo --task a1b2` 都认前缀，
> 但前缀命中多个会报“没找到”而不猜）。想固定 id 就用附录 A 的手工写法。

| # | 操作 | 看到这样算过 |
|---|---|---|
| 4.1 | 窗口 A 起 REPL（`tasks_enabled=true`） | verbose 模式下有「✓ 任务脊柱已启动（驱动线程已起，复位上次中断步骤 0 个）」 |
| 4.2 | 窗口 A 直接敲（终端入口，不经模型）：`/tasks add "在工作目录里创建文件 w3-spine.txt，内容写 spine-ok" --wait time:+90s` | 回显带新目标 id、步骤拆解结果、等的条件与“驱动线程在不在跑”；窗口 C 能看到 `~/.jarvis/goals/<id>.json` 落盘 |
| 4.2b | 写错等待条件时不会落脏数据：`/tasks add "随便一个目标" --wait time:2000-01-01T08:00`（过去的时刻）或 `--wait blah`（没前缀） | 直接报错并回用法，**`~/.jarvis/goals/` 里不新增文件**（先解析后写盘，不是先建再回滚） |
| 4.2c | 模型侧入口：窗口 A 说「用 TrackGoal 替我盯着：等 w3-later.txt 出现后，在当前目录建 w3-spine2.txt，内容写 spine2-ok」 | 模型调 `TrackGoal(action="create")` 并回一个目标；卡片/返回文本里**带驱动线程状态**（没人推时会直说“当前没有驱动线程”） |
| 4.2d | 附：手工往台账写一个固定 id 的目标（方便后续 `grep`，写法见附录 A） | 与 4.2 同形，只是 id 固定为 `w3probe1` |

附录 A 给了一个能直接粘贴的手工写台账脚本（固定 id `w3probe1`）；
下文用 `w3probe1` 作目标名的步骤（§4.6b / §4.6c / §4.7 / 阶段六收尾）拿它跑最顺手。

| # | 操作 | 看到这样算过 |
|---|---|---|
| 4.3 | 窗口 A：`/tasks` | 第二节「跨会话任务脊柱」列出 `W3 走查：等 90 秒后打印一句话（0/1）｜等条件：time`；后台驱动线程〈在跑〉；行尾提示开关与 `~/.jarvis/goals/` 路径 |
| 4.4 | 等 90 秒**之前**反复 `/tasks`、并在旁边开着 `tail -F` 模型侧日志 | **没有任何 LLM 请求发生**（驱动器只 `Event.wait`，不轮询烧钱）；台账里那步变成 `blocked` + `block_reason":"waiting"` |
| 4.5 | 过 90 秒 | 该步的 `wait` 被清掉并推进：驱动起一个 Subagent 建出 `w3-spine.txt`；播报「目标「W3 走查…」已完成：1/1 步」经 W1 闸门（静默时段/日额度到了会先入库不硬念） |
| 4.6 | 窗口 B：`cat ~/.jarvis/goals/w3probe1.json` | `steps[0].state":"done"`、`result` 有汇报摘要；`goal.status":"done"` |
| 4.6b | **副作用归属回归项**（2026-10-21 实机暴露过链路断）：窗口 B `grep w3probe1 ~/.jarvis/actions.jsonl` | 该步建的本地写真进了账：能看到 `"task_id":"w3probe1"` 的行，且台账里 `"journal_ids": [...]` **非空**（两处都有才算接通：前者是查询键、后者是归属线索）；全空就是接线又断了 |
| 4.6c | 窗口 A：`/undo --task w3probe1` | 列出该目标名下已执行的副作用并逐项报"撤了 / 撤不动 + 原因"（不能再出现"没有已执行的副作用记录"——那当时就是断链的表象） |
| 4.7 | 把 §4.2 的脚本再跑一遍（新 id），然后**退出 REPL 再进** | 重启后目标照样被推进（断点续跑）；若退出时该步正 `running`，重启日志里会报「复位上次中断步骤 1 个」，`attempts` 计 1 |
| 4.8 | 造一个永远不会来的等待：`"kind": "percept_event", "spec": {"source": "mail"}, "deadline_at": <30 秒后>` | 超时后播报「目标…卡在第 1 步…：等条件超时（…），要不要继续等、换做法，还是先搁下？」；台账 `block_reason":"timeout"`；**同一件事不重复问**（再等一轮不会再播） |
| 4.9 | `/tasks` | 第二节该行显示「◇ 卡在第 1 步：…（timeout），等 percept_event（最迟 …）」——等什么与最迟什么时候都在 |
| 4.10 | 把 `[tasks].enabled` 改回 `false`，重启后 `/tasks` | 第二节照样列出台账，但「后台驱动线程：未起」；无 provider 时同理（不起线程，读侧不受影响） |
| 4.11 | **不等了，直接推**：`/tasks add "在当前目录建 w3-advance.txt，内容写 adv-ok" --wait file:~/never-appears.txt` 然后 `/tasks advance <id 前缀>` | 该行立刻被推进；台账里该步 `wait` 已清空，且 `result` 带「用户以 advance 跳过等待条件」的痕迹——**不能谎称条件成立**，否则日志里会出现从没发生过的「等到了文件」 |
| 4.12 | 管控四个动作：`/tasks pause <id>` → `/tasks` → `/tasks resume <id>` → `/tasks cancel <id>` | pause 后状态变「已暂停」且驱动器不再推；resume 后回活跃并立刻 `wake`；cancel 后为「已取消」；每次回显都带目标当前状态，而不是只说一句“好了” |
| 4.13 | **定局目标不能改活**：对 4.12 已 cancel 的目标执行 `/tasks resume <id>` | 明确拒绝并说明原因（已定局的目标重跑会把已经发出去的事再发一遍）；`/tasks delete <id>` 则能删，台账文件真的从 `~/.jarvis/goals/` 消失 |
| 4.14 | **前缀不猜**：建两个目标后用一个能同时命中两个的前缀（如两个 id 都以同一位开头）执行 `/tasks show <前缀>` | 报“没找到这个目标”并提示用更长的 id，**绝不随便选一个改** |

## 5. 阶段五：serve RPC（桌面侧两个入口）

这一阶段验的是：桌面拿到同一套能力，而且**写操作不会一问都不问就落**。
起 serve：`./.venv/Scripts/python.exe -m agent --serve`，然后用任意 WS 客户端
（或 jarvis-desktop）发 `{"type":"tasks.exec","id":"1","action":"status"}` 这样的平铺帧。

| # | 操作 | 看到这样算过 |
|---|---|---|
| 5.1 | `tasks.exec` 带 `action=status` | 返回 `goals` / `count` / `driver_running` / `base_dir`；**`driver_running` 与 `tasks_enabled` 是分开的两个字段**（配置开了但宿主没起来时，前者假后者真） |
| 5.2 | `tasks.exec` 带 `action=create`、`goal`、`steps`（数组可省）、`wait`（字符串 `"time:+90s"` 或对象 `{"kind":"file_exists","path":"…"}` 两种写法都收） | 返回体里 `ok=true` 且带**结构化 `goal`**（含 `id`/`status`/`steps`，面板不必从 `message` 文案里截 id）；`~/.jarvis/goals/<id>.json` 落盘；省 `steps` 时自动补一个与目标同文的单步骤（零步骤会被驱动器跳过） |
| 5.3 | `tasks.exec` 带 `action=delete`、不带 `confirm` | **不删**，只回 `need_confirm=true` + `goal` 明细（要删的是哪个）；补 `confirm=true` 重发才真删（与终端 `/undo` 读 y/N 是同一条约束的两种实现） |
| 5.4 | `actions.exec` 带 `action=status` | 返回 `counts` / `held` / `held_labels` / `window_size` / `recent` / `actions_config`，与 `/undo list` 看到的是同一回事 |
| 5.5 | `actions.exec` 带 `action=undo`、`id=<可撤销条目 id>`，不带 `confirm` | 回 `need_confirm` + `preview`（预览来自 `CheckpointManager.describe(cid)`，能看到影响面），未执行回滚 |
| 5.6 | `actions.exec` 带 `action=cancel_hold`、`id=…` | **不需确认直接生效**：撤的是尚未执行的预约，不是抹已发生的事；`actions.jsonl` 新增一条 `cancelled` |
| 5.7 | `actions.exec` 带 `action=list` 与 `action=undo_task`、`task_id=<目标 id 前缀>` | 列出该目标名下的副作用；`undo_task` 逐项报撤了/撤不动（与 §4.6c 终端链路同口径） |
| 5.8 | **信封键泄露回归项**：发一帧带 `type`/`id`/`action` 的平铺入参（真实客户端就是这么发的） | `type`/`id` 不会混进业务字段（不出现 `ok=false` 的“未知字段”或 `goals` 里多一项）；单测直传业务 dict 测不出这条，所以专门盯（`test_envelope_keys_never_leak_into_business_fields`） |
| 5.9 | 发一个不存在的 `action`（如 `tasks.exec` 带 `action=frobnicate`） | 回明确错误并列出合法动作，不抛异常不断连接 |
| 5.10 | 桌面壳（可选）：起 serve + `npm run dev`，让脊柱推完一步 | 聊天区出现一条气泡，标题「任务脊柱」；`kind":"task"` 未被桌面壳当未知事件丢弃（默认渲染路径） |

## 6. 阶段六：回归确认

| # | 操作 | 看到这样算过 |
|---|---|---|
| 6.1 | `./.venv/Scripts/python.exe -m pytest -q` | 全部通过，无新增失败（本切片后全量 **3146 例**，W3 单测 **315 例**） |
| 6.1b | `./.venv/Scripts/python.exe scripts/smoke_w3_spine.py` | 一行结论「全部通过」（§0.5），这是不靠肉眼就能重复跑的那层 |
| 6.2 | `./.venv/Scripts/python.exe scripts/check_md_links.py` | `dead links: 0`、`untracked links: 0` |
| 6.3 | `tests/serve/test_serve_protocol.py` | 协议计数 55：新增的 `tasks.exec` / `actions.exec` 已在 `DESKTOP_COMMANDS` 与 RPC 分发表两边对上 |
| 6.4 | `tests/core/test_system_prompt.py::TestPromptTextBlocksSplit` | 4 例通过：`system.py` 里的同名常量与 `text_blocks.py` 里是**同一个对象**、人设行/平台段/思考段仍逐字进提示、目标段仍按 `TrackGoal` 注册与否条件注入、两个提示词文件都≤ 800 行 |
| 6.5 | 走查收尾 | 删掉 `~/.jarvis/goals/` 下的探针目标（含 `w3probe1.json` 与本轮建的随机 id 目标）与 `w3-probe*.txt`、`w3-spine*.txt`、`w3-advance.txt`，并把 `undo_window_s` 改回 `30` |

## 7. 常见问题

| 现象 | 原因与处置 |
|---|---|
| `/undo list` 说账本为空，但工具确实写过文件 | 本轮没打上检查点（没装 git / `checkpoint_enabled=false` / `workdir` 异常）。**这是设计行为**：宁可不记，也不记一条撤不动的假可撤销 |
| `/undo` 报「本轮检查点已被配额修剪」 | 每会话只留 20 个检查点，超出按最老修剪。回退窗口比想象短，需要更早的状态用 `/rewind` |
| 到点了邮件没发也没有播报 | ① 该进程已退出（遗留项会被作废，见阶段一）；② `abort_event` 被置位（你中断过本轮）；③ 到点执行失败 → `actions.jsonl` 里该条是 `failed` 并带 `error` |
| 脊柱目标一直不动 | 看 `/tasks` 的「后台驱动线程」：未起就是 `tasks_enabled=false` 或 LLM 配置不可用；在跑但不动就是等待条件没满足（该行会写等什么与最迟时刻） |
| 脊柱一条都没播报 | 打扰预算把它压了（静默时段/日额度/重要度门槛）。`task` 权重已在 `policy/budget.py` 登记（0.9），乘算紧迫度 0.45 = 0.405，仍在 0.3 门槛之上；若你自己调低过 `notify_min_salience` 或改过权重，先回默认再判 |
| 想临时全部关掉 | `journal_enabled = false`（不记账也不延时，行为回到 W3 之前）；`tasks_enabled = false`（只读台账、不自走） |
| `/tasks add` 建成功了但目标一动不动 | 看回显末行：写了“当前没有驱动线程，需先开 `[tasks] enabled`”就是真的没人推（属如实提示，不是失败）；目标已落盘，开关开对、重启后会被接着推 |
| `TrackGoal` 说没这个工具 | 本轮没注册上（工具表与系统提示词同源于注册表）：先确认 `build_default_registry` 里有 `TrackGoalTool`，再看是否被 `tools_enabled` 筛掉 |
| 前缀能命中的目标改不了 / 报“没找到” | 前缀命中了两个以上，按设计不猜（宁可以说没找到，也不能改错目标）；把 id 敲长到唯一 |
| RPC 返回 `need_confirm` 但以为报了错 | `delete` / `undo` / `undo_task` 未带 `confirm=true` 就只回预览，两拍确认是默认行为（避免桌面一个误点就抹掉台账） |
| 一键走查有项红了 | 脚本会把该项的「期望」打在下一行；`advance`/定局/硬拦这几项红了多半是 `spine_ops.py` 或权限判定被改动，先 `git diff` 定位再判是不是回退 |
| 走查脚本要不要手动清理 | 不用：台账/账本都在系统临时目录里，跑完随目录删掉（`tests/test_w3_smoke_script.py` 里钉了这条） |

## 附录 A：手工往台账写一个固定 id 的目标

不用入口、直接拼台账（只用于方便 `grep` 的走查；日常用 `/tasks add` 或 `TrackGoal`）：

```bash
mkdir -p ~/.jarvis/goals
python - <<'PY'
import json, time, pathlib
from datetime import datetime
at = time.time() + 90          # 90 秒后到点
goal = {
    "id": "w3probe1", "goal": "W3 走查：等 90 秒后打印一句话",
    "status": "active", "created_at": time.time(), "updated_at": time.time(),
    "session_id": "", "workdir": str(pathlib.Path.cwd()), "parent_task_id": "",
    "budget": {"tokens": 0, "usd": 0.0}, "blocked_since": 0.0,
    "steps": [{
        "id": "s1", "desc": "在工作目录里创建文件 w3-spine.txt，内容写 spine-ok",
        "state": "pending",
        "wait": {"kind": "time", "spec": {"at": datetime.fromtimestamp(at).isoformat(timespec="seconds")},
                 "deadline_at": at + 600},
        "attempts": 0, "last_error": "", "result": "", "block_reason": "",
        "asked": False, "started_at": 0.0, "finished_at": 0.0, "next_run_at": 0.0,
        "journal_ids": [],
    }],
}
pathlib.Path("~/.jarvis/goals/w3probe1.json").expanduser().write_text(
    json.dumps(goal, ensure_ascii=False, indent=1), encoding="utf-8")
print("written", at)
PY
```

⚠ 这段脚本是**绕过入口直写磁盘**，所以它不校验字段、也不唤醒驱动器——
写完要等下一个唤醒周期或重启 REPL。用 `/tasks add` 就没这两个坑（先解析后写盘、写完必 `wake`）。
