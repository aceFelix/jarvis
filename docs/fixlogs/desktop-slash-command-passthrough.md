# 设计：斜杠命令透传（slash.exec + slash_bridge）

- 日期：2026-10
- 范围：jarvis（serve(protocol|server) / workbench(api|engine|slash_bridge 新模块)、
  tests(ui/test_workbench_slash.py 新增、serve/test_serve_protocol.py 契约 45→46)）、
  jarvis-desktop（contracts / chatStore / backendStore / dispatcher / ChatArea / chat.css、
  test/renderer 两个测试文件）、文档同步
- 作者：aceFelix

## 背景

终端 J.A.R.V.I.S 有 45 个斜杠命令，桌面壳此前把 `/xxx` 当普通文本发给 LLM（引擎
`_handle_send` 无命令路由）。逐条为每个命令设计协议指令和 UI 成本太高，市面主流 Agent
的做法是**一条透传指令**：前端识别 `/` 前缀 → 命令原文转发到终端同一个命令分发器 →
捕获输出回推渲染。一次改动即把 `/compact` `/context` `/diff` `/doctor` `/tools`
`/skills` `/memory` `/plugin` 等一大批终端能力带进桌面；高频项（context 进度条、
diff 视图）后续可逐步升级为原生控件而非卡片。

## 总体链路

```
桌面输入框 doSend 拦截 '/'（无待发送附件时）
  → backendStore.execSlash（本地回显命令气泡 + 置 busy，不入对话）
  → WS 指令 slash.exec {command}
  → serve _rpc_slash_exec → WorkbenchAPI.exec_slash（形态校验入队 slash_exec）
  → ChatEngine._dispatch → slash_bridge.run_slash（复用终端 dispatch_command）
  → 捕获输出 emit slash_result {command, ok, text}
  → dispatcher case 'slash_result' → chatStore.addSlash（kind 'slash' 卡片）
  → ChatArea SlashCard 渲染（summary=命令原文、正文=等宽 pre，默认展开可收起）
```

命令执行期间不产生对话消息（白名单命令都是只读/运维型），卡片不受 `aborted`
停止闸门影响；`slash.exec` 回执仅确认受理（`{ok, command}`），真实结果一律走事件。

## 关键设计决策

### 1. 桥逻辑全部外置，引擎主体零膨胀

`engine.py` 已超千行（code-structure 红线），故新建独立模块
[slash_bridge.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/ui/workbench/slash_bridge.py)
承载全部桥接逻辑（约 290 行），engine 只加两行路由
（`elif action == "slash_exec": await slash_bridge.run_slash(...)`）。
`router` 的 `CommandContext` / `dispatch_command` 在函数内**惰性导入**：顶层导入会在
engine 导入本模块时拉起全部 handlers → 回头依赖尚未初始化完的 `agent.ui`，形成循环导入。

### 2. 输出捕获：StringIO 背书的仿 RichCLI

`SlashCaptureUI` 复刻 handlers 实际用到的 RichCLI 接口面（调研结论：只有
`info/warn/error/_console` 四个属性被使用；`ui.cli`、`ui.markdown_renderer`
是类型导入非属性访问，无需桩）。内部 `Console(file=StringIO, width=100,
no_color=True, force_terminal=False)`，命令写完输出即得纯文本。

### 3. 交互禁令（根因：serve 的 stdin 是 Electron 协议管道）

`terminal_picker` 的 `pick_from_list / pick_from_grouped_list / form_input` 直接读
stdin，serve 模式下会抢坏指令通道。双保险：

- `SlashCaptureUI.ask_user / read_user_input_async / terminal_picker` 抛
  `SlashInteractiveError`；
- 执行期 `_pickers_blocked()` 上下文管理器临时 patch
  `agent.ui.terminal_picker` 模块三函数，并用 pkgutil 扫描
  `agent.commands.handlers` 子模块、把**模块级 import 的同名引用**一并替换
  （函数内 import 的走模块 patch 自然生效），finally 逆序还原。

任何命令试图交互都干净失败回「该命令需要终端交互，请在终端 jarvis 中使用」，
不挂起、不抢管道。

### 4. 白名单口径

`ALLOWED_COMMANDS`：`/context /compact /cost /c /diff /doctor /tools /mcp /skills
/memory /plugin /plugins`。排除原则：

- 桌面已有原生控件的（mode / think / model / sessions / rewind / talk / voice）
  不透传——防双入口口径漂移；
- `/agents` `/tasks`：serve 宿主引擎无多 Agent 运行时，handler 对 None 会崩；
- `/loads /load /init /config /server` 等需要交互或起常驻进程的命令。

### 5. 技能动态放行

白名单外命中 `load_skills(settings.workdir)` 里已安装技能（`/<skill-name>`）照常
执行，且复用对话轮次的并发语义：持与手机/微信共用的 `engine._query_lock`
（`run_in_executor` 获取，不阻塞事件循环）、挂 `_send_task`（可被 `reply.abort`
停止）、轮后 `engine._after_turn()` 落盘；`CancelledError` 直接 raise 透传。

### 6. CommandContext 组装

`_build_command_context` 从引擎运行态组装终端同款上下文：orchestrator 优先复用
`loop._orchestrator`（保持权限模式一致）、`team_mgr` / `task_list` 置 None（serve
无多 Agent 运行时）、provider / registry / checker / recovery 现场取或现建。

## 测试交付

- 后端 [tests/ui/test_workbench_slash.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/ui/test_workbench_slash.py)
  11 用例：api 形态校验入队、捕获输出、三个交互原语抛错、picker patch/还原、
  三种拒绝路径（非命令/未就绪/白名单外）、白名单执行+输出捕获、未知命令、
  交互错误护栏、技能模式（含锁持有断言）、`_dispatch` 路由、上下文映射；
  `asyncio.run` 驱动（与 checkpoint_ops 测试同风格）。
- 协议契约 `test_serve_protocol.py`：`DESKTOP_COMMANDS` 总数 45→46。
- 桌面 vitest 新增 7 用例（dispatcher 3 + backendStore 4）；`npm run typecheck`、
  `npm run build` 通过。
- 全量回归：jarvis `uv run pytest tests -q` 2346 passed。

## 已知限制

- 捕获输出为无颜色纯文本（Rich 表格/面板以线字符呈现），复杂排版可读性一般；
- 需要进度条/交互反馈的长命令（如 `/compact` 自动压缩细则）只呈现最终文本；
- 后续升级方向：`/context`、`/diff` 等高频项做原生控件，卡片作为兜底保留。

---

# 迭代二：`/context` 窗口口径修正 + slash.commands 补全目录（2026-10）

透传上线后用户反馈两项体验问题，本轮一并落地。

## 1. `/context` 不要把配置的窗口写成「假设窗口」

**问题**：用户在 settings.toml 里明确配了 `context_window = 200000`，`/context` 统计头
却永远写「假设窗口 200,000 tokens」——措辞与事实不符（“假设”只剩回退默认值一种情形）。

**修法**（`agent/commands/handlers/core_commands.py`）：`handle_context` 读
`ctx.loop.context_window` 实际配置值，`window = window or 128000` 同时以
`window_configured = bool(window)` 传入 `_print_context`；标签分流
`window_label = "窗口" if window_configured else "假设窗口"`。桌面透传的 `/context`
输出共用同一 handler，随之生效。测试：
[tests/commands/test_context_window_label.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/commands/test_context_window_label.py)
4 用例（_CollectUI 桩采输出，断言配置 200000 → 「窗口 200,000」且无「假设」；未配置 →
「假设窗口 128,000」）。

## 2. `slash.commands` 补全目录 + 桌面 `/` 前缀弹层（第 47 条指令）

终端 REPL 输 `/c` 即匹配 c 开头命令，桌面没有对应手感。新增只读指令
`slash.commands`（`protocol.CMD_SLASH_COMMANDS`）返回 `[{name, description, source}]`：

- **数据源** `slash_bridge.build_desktop_commands(settings)`，三类 `source`：
  `passthrough`（`ALLOWED_COMMANDS`）、`native`（`_NATIVE_COMMANDS`：mode/think/model/
  models/sessions/reset）、`skill`（`load_skills(workdir)`，与内置重名跳过、空描述回退
  「技能包」、加载异常宽容不阻断）；描述优先取终端 `SLASH_COMMANDS` 帮助首词；
- **关键口径**：目录与 `run_slash` 执行护栏严格对齐——**补出来的每条命令必然可执行**，
  不会补出必被拒的项；`WorkbenchAPI.list_slash_commands` 只读直返不入队，`agent.ui.cli`
  惰性 import 防循环依赖；
- **桌面链路**：dispatcher `init` → `conn.refreshSlashCommands()` →
  `client.sendCommand(Cmd.SlashCommands)` 直发（不走 runCommand，失败静默保留旧目录）→
  `slashStore`；`SlashAutocomplete.tsx`（`slashTriggerOf`/`filterSlashCommands` 纯函数 +
  `useSlashAutocomplete` hook）：`^\/\S*$` 触发、↑↓/Tab/Enter/Esc、选中回填「命令名+
  空格」、完整命令名 Enter 直通发送、弹层 portal + fixed 锚 textarea 上沿、复用
  ThemedSelect 皮肤类；
- **配套拆分（code-structure 800 行红线）**：ChatArea.tsx 811→约 570 行（消息渲染子组件
  外移 `ChatMessageViews.tsx`）；backendStore.ts 849→约 690 行（资源/协同动作外移
  `resourceActions.ts`/`remoteActions.ts`，动作工厂 + spread 展开、依赖注入防环引用）。

## 迭代二测试交付

- 后端：`test_workbench_slash.py` 追加 3 用例（build_desktop_commands 三类 source/
  重名跳过/空描述回退、技能加载失败宽容、api 只读委托不入队）；
  `test_context_window_label.py` 4 用例；协议契约 `DESKTOP_COMMANDS` 总数 46→47；
  全量 `uv run pytest tests -q` **2353 passed**；
- 桌面：`slashAutocomplete.test.tsx` 13 用例 + `backendStore.test.ts` 3 用例 +
  `dispatcher.test.ts` init 断言更新；typecheck ✓、vitest 19 files **368 passed**、
  `npm run build` ✓；
- 文档同步：jarvis README（命令表 `/context` 口径、serve bullets、协议计数 47、
  init 十路齐刷）、07-UI层.md 小节改名与两条 bullet、desktop README/architecture.md
  小节、本节 fixlog。

## 迭代三：右栏上下文窗口占比 + 压缩按钮替掉主屏截屏

用户需求：①把上下文窗口使用信息显示到右栏；②用手动压缩上下文按钮替掉输入栏的
主屏截屏按钮（截屏无意义，需要截什么用户自己会截），彻底删除截屏功能。

### 1. 右栏上下文窗口占比（扩展 `cost.get`，不新增指令）

复用现有刷新节奏（init + `assistant_done` 已刷 `cost.get`，右栏用量卡已消费），
在引擎侧算好后并入，前端只渲染：

- **后端** `ChatEngine.context_usage` 新增只读属性（口径与 REPL `/context` 严格一致）：
  已用 = `estimate_tokens(messages)` + `estimate_text_tokens(system_prompt)`（system 不在
  messages 里但实占窗口）；窗口 = `loop.context_window`，未配置（0）回退 128000；
  `context_configured=bool(window)` 区分「窗口」/「假设窗口」；`context_percent`=
  `round(used/effective*100,1)`；全部 `getattr` 容错（loop/messages 未装配安全回退）。
  `WorkbenchAPI.get_cost` 用 `**self._engine.context_usage` 展开并入返回体；
- **桌面** `rightStore.CostInfo` 加 4 个可选 `context_*` 字段（旧后端缺失时隐藏整行）；
  `RightSidebar.tsx` UsageCard 新增一行百分比 + gauge 进度条（>85% 标红，复用全局
  `.gauge`/`.gauge-fill`），title 透出「窗口/假设窗口 {window}，已用 {used} token」。

### 2. 压缩按钮替截屏 + 删除截屏全链路

- 🗜 压缩按钮直接复用迭代一的透传链路：`onClick={() => void execSlash('/compact')}`，
  执行结果走既有 `slash_result` 命令输出卡片，无需新协议；glyph `capture`→`compact`
  （`{CMP}`/`<CMP>`/`[CMP]`）；i18n `chat.captureTip`→`chat.compactTip`（中英）。
- **端到端删除截屏**（避免留无入口的死代码）：`contracts.ts`（`IpcChannels.CaptureScreen`
  + `ScreenCapture` 接口）、`main/index.ts`（`ipcMain.handle(CaptureScreen)` +
  `desktopCapturer`/`ScreenCapture` import）、`preload/index.ts`（`api.captureScreen`）、
  `preload/index.d.ts`（`captureScreen` 声明）、`ChatArea.tsx`（`doCapture` + 不再使用的
  `addImage`）、i18n（`captureOk`/`captureFailNoSource`/`captureFail` 删除）。grep 证实
  `src` 与 `test` 已无任何 `capture`/`CaptureScreen`/`ScreenCapture`/`desktopCapturer` 残留
  （`talkCapture.ts` 为语音麦克风采集，与截屏无关，保留）。

### 迭代三测试交付

- 后端：`test_workbench_engine_api.py` 新增 2 用例（配置窗口 200000 → window/configured/
  used/percent；未配置→回退 128000、无 `_query_loop` 也安全）；`test_serve_routing.py`
  `cost.get` shape 断言补 `context_*` 四键及默认值（未装配 used=0/window=128000/
  percent=0.0/configured=False）；全量 `uv run pytest tests -q` **2355 passed**；
- 桌面：`components.test.tsx` 截屏用例→压缩用例（`execSlash` spy 断言传 `/compact`）、
  禁用用例 `btn-capture`→`btn-compact`、新增 2 个上下文卡用例（渲染 + 无字段隐藏）；
  typecheck ✓、vitest 19 files **370 passed**、`npm run build` ✓；
- 文档同步：jarvis README（右栏四区块 bullet 补上下文占比 + 截屏→压缩、L1112）、
  07-UI层.md（`cost.get` 数据源补 `context_usage`）、desktop README（输入栏 2×2 控制区、
  右栏用量、快捷操作去向）、architecture.md（`cost.get` 字段行、会话与用量区块、快捷
  操作去向/截屏删除）、development.md 走查清单（`[CAP]`→`[CMP]`、复制与压缩项）、本节 fixlog。

## 迭代三·补丁：加载历史会话后上下文窗口仍显示 0

**现象**：左栏点开历史会话（已恢复提示上屏、160 条消息回放），但右栏「会话与用量」
的上下文窗口、对话轮数/消息数仍停在 0.0% / 0 轮 / 0 条。

**根因**：刷新时机缺口。`cost.get` 只在 `init` 与 `assistant_done` 刷新；`dispatcher.ts`
的 `session_loaded` 只回放历史 + 刷会话列表，未刷用量卡。而引擎侧 `_handle_load` 已
`_messages.clear()+extend(session.messages)`、`_dialog_count` 从 meta 恢复，数据就绪只是
没人去拉。`context_usage` 由 `_messages` 估算，故只要补一次刷新即可反映。

**修法**（`jarvis-desktop/src/renderer/src/api/dispatcher.ts`）：`session_loaded` 与
`session_new` 两个 case 各补 `void conn.refreshCost()`——前者拉回真实占比/轮数，
后者确保新会话用量卡归零（不刷会残留上一会话占比）。测试：`dispatcher.test.ts` 两
用例各补 `expect(conn.refreshCost).toHaveBeenCalled()`；vitest 69 passed、typecheck ✓、
`npm run build` ✓。（输入/输出/缓存 token 仍为 0 属预期：那三项是运行时 QueryLoop
累计值、不随会话持久化恢复，与上下文窗口估算无关。）

## 迭代三·补丁二：补全弹层滚动条不跟随选中项

**现象**：输入 `/` 弹层候选多于可视行数时，用 ↑↓ 把高亮移到折叠线外的项（如
末项 `/memory`），高亮行不在可视区内、滚动条不动，看不到选中了哪条。

**根因**：`SlashAutocomplete.tsx` 的 `active` 只驱动高亮样式（`.active`），没有任何
把高亮项滚入可视区的逻辑；弹层容器 `.themed-select-menu` 自身 `overflow-y: auto`，
项超出 `maxHeight` 就落在滚动区外。

**修法**（`SlashAutocomplete.tsx`）：给弹层容器加 `menuRef`、选项加 `data-idx`，新增
一个 `useEffect([active, open, matches.length])`：按 `data-idx` 查到高亮项，比较
`offsetTop`/`offsetTop+offsetHeight` 与容器 `scrollTop`/`scrollTop+clientHeight`，仅在越界时
手动推 `scrollTop`（上溢滚到顶、下溢滚到底）。不用 `scrollIntoView` 以免连带滚动背后
页面；弹层 fixed、选项 `offsetParent` 即容器，`offsetTop` 相对容器顶计算精确。
测试：`slashAutocomplete.test.tsx` 新增用例（jsdom 无布局，`Object.defineProperty` 手工
给出 `clientHeight=52`、各项 `offsetTop=i*26`/`offsetHeight=26`，断言两次 ArrowDown 后
`scrollTop===26`、两次 ArrowUp 后回 0）；vitest 14 passed、全量 19 files **371 passed**、
typecheck ✓、`npm run build` ✓。

## 迭代三·补丁三：弹层内部滚动误触发收起（滚不到 `/memory` 之后的命令）

**现象**：输入 `/` 弹层只看到 `/c`…`/memory` 八条，鼠标滚轮往下滚时整个弹层直接
消失，再也看不到后面的命令。用户误以为 `/memory` 是最后一个。

**根因**：`SlashAutocomplete.tsx` 用 `window.addEventListener('scroll', close, true)`（capture）
做“页面滚动就收起浮层”（浮层 fixed，背后滚动会错位）。但 capture 会捕获一切 scroll
事件，**包括弹层自身内部的滚动**：候选多于 `MAX_VISIBLE=8` 行时，鼠标滚轮滚列表、
以及补丁二新加的“↑↓ 跟随改 `menu.scrollTop`”都会派发 scroll → 命中 `close` → `setRect(null)`
收起浮层。实际目录不止 8 条（`/memory` 后还有 `/plugin` `/plugins` + 原生命令 + 技能）。

**修法**（`SlashAutocomplete.tsx`）：`close` 接 `event` 参数，若 `e.target` 是 `Node` 且
`menuRef.current.contains(e.target)`（即滚动发生在弹层内部）则忽略，不收起；只有关键的
背后页面/窗口滚动与 resize 才收起。测试：`slashAutocomplete.test.tsx` 新增用例（
`fireEvent.scroll(menu)` 后弹层仍在、`fireEvent.scroll(window)` 后收起）；vitest 15 passed、
typecheck ✓、`npm run build` ✓。
