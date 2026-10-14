# Agent 空转熔断与循环拦截修复（贪吃蛇 15 分钟零产出案）

- **日期**：2026-10-14
- **作者**：aceFelix
- **版本**：随 v2.2.0 之后合入
- **相关命令**：`/away`、`/config`
- **相关模块**：`agent/core/query_loop.py`、`agent/core/orchestrator.py`、
  `agent/core/policy/loop_detect.py`、`agent/core/memory/compactor.py`、
  `agent/prompts/system.py`

## 一、现象

语音模式下让 jarvis「写一个网页版贪吃蛇小游戏」，15 多分钟没有任何成品文件产出。
用户贴出的运行日志显示：它既没在写文件，也没在正常推进，而是在两件事之间反复横跳——

1. **满盘找 `snake-game` 项目**：`ls`/`find` 扫遍 `E:/`、`C:/`、桌面、用户目录，
   甚至钻进 `site-packages`、`SteamLibrary` 这类十万文件的深坑；
2. **在"思考过程"里用散文一截一截写游戏代码**，每写几段就被
   `⚠ 输出被截断（max_tokens），自动续写...` 掐断，下一轮又从头找项目 / 从头写。

如此循环 20 多轮，直到用户语音打断。

## 二、根因（三故障叠加）

### 故障 1：记忆里的"假线索"触发检索强迫症

`SESSION_MEMORY.md` 中有一句「有一个 snake-game 项目，单文件贪吃蛇游戏，赛博霓虹
风格」。这条记忆是**过往摘要，未重新核实**，但模型把它当成"必须先定位才能决定改旧
还是做新"的悬案。而全盘搜索每次都标着 `🛡️沙箱 | 风险=高` 被自动放行——正是
`/away` 那轮引入的"沙箱中风险自动放行"把**行为刹车彻底拆掉**了：没有任何一环会
拦下这些无意义的整盘 `find` 问一句"你到底在干嘛"。

### 故障 2：输出预算被思考占满 → 每轮截断

模型每轮要输出「数千 token 的深度思考 + 一个 FileWrite 工具调用 JSON」。工具调用
JSON 排在最后，预算先被思考吃光 → **每轮都在代码写到一半时截断**，产出的是"只剩
思考、无正文、无工具调用"的残缺轮。（用户 `models.toml` 里 `max_tokens=20480` 已
不低，但思考型模型的 reasoning 会吞掉绝大部分，问题在结构不在绝对值。）

### 故障 3：续写指令把模型推向死循环

截断恢复逻辑（`query_loop`）回灌的指令是「从中断处继续，不要重复已输出内容」。这句
话**恰好诱导模型用散文继续写代码**（而不是发起工具调用交付成品），于是每一轮都在
故障 2 的坑里越陷越深，直至烧满 `max_iterations`。

## 三、修复

四项，两条治本 + 一条纠偏 + 一条纪律：

### A. 截断续写熔断（`query_loop.py`，治本）

新增连续续写计数器 `cont_streak`，封顶 `_MAX_CONTINUATIONS = 2`。连续第 3 轮仍被
截断 → 不再回灌续写指令，直接以 `truncation_fuse` 结束本轮，并用 `warn` 给用户可
操作建议（提高 max_tokens / 简述任务）。正常产出工具调用或纯文本轮会**复位计数**，
所以零星截断不受影响，熔断只惩罚"连续"截断。

### B. 空转循环检测器（`policy/loop_detect.py` + `orchestrator.py`，治本）

签名滑动窗口：`call_signature()` 把工具调用归一化为 `工具名:动作`（`find`/`ls`/
`tree`/`du` 折叠为命令词本体，让"换个姿势同一条搜索"命中同型；`echo` 等保留参数
前缀不误伤多样操作）。同型签名在最近 6 次窗口内出现 ≥3 次即判定空转，把本应
ALLOW 的调用**强制升级 ASK** 拦刹车，提示用户"可能正在原地打转"。每轮任务开头
`reset()`，单轮最多提示 2 次后静默（防"每次都问"变成新骚扰源）。这是沙箱绿灯拆掉
后唯一能拦住行为空转的一环。

### C. 续写指令纠偏（`query_loop.py`，治标但关键）

续写提示语从「从中断处继续」改为附带硬性约束：**成品（代码/文档）必须通过工具调用
（如 FileWrite）交付，禁止在思考或正文里用散文继续写代码片段**，直接掐死故障 3。

### D. 记忆降级为线索（`compactor.py` + `system.py`，纪律）

- 压缩提示词「分析要求」加三条如实记录纪律：只记真实出现过的文件/代码、断言标注
  来源、检索未完成写"未核实"——从源头防止摘要**制造**假线索；
- `SESSION_MEMORY` 文件头与 system prompt 注入段都加「⚠ 使用守则：本文件是历史
  快照，提到的文件/项目未重新核实；不要为验证记忆反复检索文件系统；与用户当前指令
  冲突时一律以当前指令为准」——即便假线索已产生，也约束模型不去为它空转。

## 四、验证

- 新增 `tests/test_agent_loop_guard.py` 12 例：签名归一化（同型识别 / 异型区分 /
  不误伤多样操作）、检测器触发与配额静默、窗口滑动、reset、熔断终止与复位、
  orchestrator 集成（ALLOW 升级 ASK、用户拒绝则不执行）。
- `tests/conftest.py` autouse 夹具补 `get_loop_detector().reset()`：检测器是进程级
  单例，不复位会让旧套件（`test_orchestrator` 等）的重复签名跨用例累积，第三条同型
  调用被误升级 ASK 致 2 例回归。加入复位后回归消除。
- 全量 `pytest`：**2523 passed**（原 2511 + 新增 12）。

## 五、可复用经验

1. **"自动放行"类优化必须配一个"行为层"刹车**：权限层放行让无人值守更顺，但也可能
   让 agent 的空转行为一路绿灯。放权越大，越需要独立的行为异常检测（循环 / 熔断）。
2. **记忆是线索不是事实**：自动维护的会话记忆里的文件/项目断言未经核实，模型不能把
   它当必须解决的悬案。注入端与生成端都要显式声明这一口径。
3. **截断恢复要防"续写诱导"**：`不要重复已输出内容` 这类指令在思考型模型上会反向
   起效（诱导散文续写而非调用工具），恢复指令应显式要求走工具交付并设熔断上限。

## 六、二次案（面试提醒 + Ctrl+C 失效）

v2.2.1 发布后，用户语音模式下说「明天记得提醒我，十二点有一场很重要的面试」，
再次出现同类现象：连刷几十个空白「💭 思考过程」面板，且**按 Ctrl+C 完全停不下来**。

### 根因 1：Ctrl+C 在流式循环里没有检查点（已修）

语音模式的 SIGINT 处理器（`voice_loop._build_sigint_handler`）刻意**只置标志不抛
异常**——在主线程 raise 会撕裂 `asyncio.run`，被 `main.py` 的
`except KeyboardInterrupt: return 130` 接走 → 整个进程退出。它依赖
`_poll_interrupt` 线程把 `stop_event` 转成 `ctx.abort_event`，桥接本身是通的。

但 `query_loop._stream_once()` 的 `async for event in provider.stream(...)` **从头
到尾没有一处检查 `abort_event`**，而 `run()` 只在下一轮循环顶部检查它。深度思考模型
一轮流式可跑数十秒、事件源源不断 → abort 标志无人问津，用户按键形同失效。

修复：`_stream_once` 改为显式持有流生成器，**逐个事件**检查 `abort_event`，置位即
抛 `CancelledError`，由 `run()` 既有的 `except asyncio.CancelledError` 分支收口；
`finally` 里 `aclose()` 及早释放底层 HTTP 连接（关闭失败不外抛，以免掩盖真实异常）。
残缺轮**不入历史**，避免半截 `tool_use` 悬空污染会话。

### 根因 2：CLI/语音模式根本没有提醒工具（已修）

`ScheduleReminder` / `ListSchedule` / `CancelSchedule` 定义在
`agent/tools/extensions/schedule_tool.py`，但 `register_schedule_tools()` **只在
`agent/serve/app.py` 被调用**（桌面/工作台路径）。纯 CLI 与语音 REPL 从未注册这批
工具，所以日志里 `ToolSearch 未找到匹配「提醒」的工具` 是**事实而非模型幻觉**。工具
描述里已明确警告「不要继续反复搜索同一工具，也不要自行绕道拼凑实现」，模型却
转去手搓 `Register-ScheduledTask`，思考长度失控撞穿 `max_tokens`——回到故障 1 的赛道。

修复方向取「补齐能力」而非「硬止损」：新增
[`agent/core/daemon/cli_reminder.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/daemon/cli_reminder.py)
的 `CliReminderHost`，在 `main.py` 装配 REPL 时启动 Scheduler 后台线程并挂载
schedule 一族四个工具（`ScheduleReminder` / `ListSchedule` / `CancelSchedule` /
`AcknowledgeReminder`），三条装配路径（CLI / 语音 / serve）的提醒能力自此对齐。
系统提示里的「# 日程提醒」段是按 registry 是否含 `ScheduleReminder` 条件注入的
（见 [schedule-prompt-conditional-fix.md](schedule-prompt-conditional-fix.md)），
工具一注册便在终端自动恢复注入，无需改提示词逻辑。

刻意不做的三件事，避免造出「假能力」或引入新故障：

1. **不注册 deadline 一族**：`AddDeadline` 等工具只做 tracker 增删改，到期真正提醒
   依赖 `ProactiveEngine` 的每日检查，而 CLI 宿主不起引擎——注册等于「能加、不会提醒」。
2. **不做 TTS 播报**：语音模式下 PyAudio 并发有已知崩溃陷阱（`barge_in` 默认关闭即
   为此），主动语音播报仍只属于桌面/serve 路径。
3. **不跨进程常驻**：CLI 关闭后不再提醒。Scheduler 会加载 `~/.jarvis/schedule.json`，
   所以未到期任务在下次启动仍能按时触发，但错过超过 1 小时的一次性任务按既有规则
   直接标记 fired，不会轰炸。

播报出口 `RichCLI.notify_reminder()` 跑在 Scheduler 后台线程：先结束当前助手输出行
再打印 `⏰ …`，随后调 `prompt_toolkit` 的 `app.invalidate()` 让提示符重绘到提醒下方，
否则提醒会叠在用户正在输入的行上把屏幕顶乱；全程 try/except，一次 UI 故障绝不能
终止整个调度循环。

启示：**能力缺位本身就是空转诱因**。工具注册表在 CLI 与 serve 两条装配路径上不一致，
会让同一句指令在一端能做、另一端发抽风；要么补齐能力，要么让 ToolSearch 的「未注册」
结论能立即硬止损（直接告知用户功能不可用并结束本轮）。本次两条都做：先补齐，
同时保留上次交付的 ToolSearch 兜底与熔断作为护栏。

### 根因 3：连刷空白「💭 思考过程」面板（未定位，待复现）

同一段日志里约 40 个只有标题、几乎无正文的思考面板。上一轮的窗口化修复
（`_THINKING_MAX_LINES = 12`，提交 `c171b75`，已含于 v2.2.0）**确实生效了**——
日志里能看到「…（前略）」截断标记，说明面板内容已按尾部窗口截断。

但面板仍在刷屏，最大的嫌疑是 Rich Live 在用户终端里**原地擦除失效**：每次
`update()` 都相当于追加一帧。`assistant_thinking()` 已有非交互终端降级
（`is_terminal` 为假时走静态模式只打一次），而用户环境（Windows Terminal 与
Git Bash 混用，Git Bash 下的 mintty 不报告真实终端能力）恰好能过 `is_terminal`
检查却拿不到可用的光标移动能力——两个条件的交集就是本 bug 的埋藏点。

> 本项尚未修复：需要先确定哪个终端会触发，才能选择正确的判据（如改用
> `TERM`/`COLORTERM` 白名单，或给 Live 加输出频率上限）。现象已随根因 1 的
> Ctrl+C 修复变得可退出，不再是阻塞问题，但必须记入待办。
>
> 已排除的方向：提醒工具补齐后，模型不再需要绕道手搓定时任务，思考长度不再
> 撞穿 `max_tokens`，本刷屏的触发频率会大降低；但“Live 擦除失效”本身仍存在，
> 任何长思考都可能导致，因此仍是一个独立缺陷。

### 验证

- `tests/test_agent_loop_guard.py` 新增 2 例：思考流中途置 abort → 当轮立即终止且
  不消费后续事件、生成器被 `aclose`；被打断的残缺轮不入历史且 `abort_event` 已复位。
- `tests/test_cli_reminder.py` 新增 9 例：未 start 不注册、四个延迟工具挂载、重复注册
  幂等、到期回调文本与 UI 异常吞掉、未启动即 stop 安全、真 Scheduler 端到端触发
  （持久化重定向 tmp_path）、`notify_reminder` 三种终端状态下的打印与提示符重绘。
- 结构拆分回归：`tests/test_tool_dispatch.py` 新增 14 例、`tests/ui/test_slash_completer.py`
  新增 15 例（详见下一节）。
- 全量 `pytest`：**2563 passed**。

## 七、附带的结构合规拆分

`cli.py`（原 1144 行）要新增 `notify_reminder()`、`query_loop.py`（原 878 行）要新增中断
检查点，而单文件 800 行上限要求“改前先拆”，故本次顺带完成两次拆分。手法沿用仓内先例：
抽出新模块 + 原位置保留**薄委托/旧名别名**，调用点、测试与文档引用路径全部不变。

| 拆出 | 新文件 | 原文件保留 | 行数变化 |
|---|---|---|---|
| 工具 schema 下发决策（延迟分组 + 纯聊天零工具） | `agent/core/tool_dispatch.py`（152 行） | `_build_tool_defs()` / `_is_chat_only()` 薄委托 | `query_loop.py` 878 → 786 |
| 斜杠命令表 + 补全器 + 工具名语音描述 | `agent/ui/slash_completer.py`（368 行） | `SLASH_COMMANDS` / `_SlashCompleter` / `_tool_voice_desc` 别名重导 | `cli.py` 1144 → 784 |

拆分时顺手消除了一处复制粘贴：五个 `_complete_*` 里完全相同的「(名称,描述) →
Completion 渲染」内联代码，统一抽为 `slash_completer._build_completion_list()`
（列宽/描述色/回退位置作为参数）。拆分属于行为不变的重构，因此额外各加一例
“委托结果与模块函数等价”的护栏测试，防拆分期间发生漂移。

> 尚未处理的超限文件（本次未触碰，列入待拆清单）：`agent/ui/workbench/engine.py` 1240、
> `agent/cli_anything/market.py` 898、`agent/prompts/system.py` 810、`agent/serve/server.py` 807。
