# 修复：serve / 桌面壳下 Bash 工具卡死（jarvis 永不回复）+ 新增停止回复

## 问题现象

桌面壳（jarvis-desktop，宿主 `python -m agent.serve`）里发消息触发工具调用后：

- 工具卡片停在 `…`（执行中），AI 永不出回复气泡，等多久都没下文；
- serve stderr 只有 MCP server 启动日志与每 2 秒的 metrics 心跳，无任何报错；
- 约 2 分钟后出现一条 recovery 重试提示，然后继续卡死（每次重试再挂 120s）；
- 任务管理器可见多个孤儿 `bash.exe -c "date ..."` 进程永不退出。

同一台机器上 REPL 终端（`jarvis` 命令）跑同样的提问秒回，工具调用正常。
问题只在 serve / 桌面壳宿主。

## 排查过程

1. **先查事件链路**：serve 的事件泵是独立线程（50ms 轮询引擎事件队列 →
   broadcast），复现脚本观察到 `tool_use` 事件后事件流并未中断（metrics 心跳
   仍在推）→ 传输层与事件泵正常，卡在**工具执行本身**。
2. **复现定位**：全链路复现脚本（spawn serve → WS 握手 → 发 message）显示
   `tool_use` 后 124 秒才出现 recovery 重试 info —— `communicate()` 吃满了
   120s 超时；`wmic` 进程树确认 8 个 `bash.exe -c "date ..."` 孤儿进程挂在那里
   永不退出（bash 启动了，但永远不结束）。
3. **排除线程/事件循环因素**：临时实验证明非主线程上 Proactor 循环创建子进程
   本身没问题（`create_subprocess_exec` + `communicate` 正常返回）。
4. **对照实验锁定根因**：写 stdin 组合矩阵脚本，对「宿主 stdin 类型 × 是否有
   线程阻塞读宿主 stdin × 子进程 stdin 继承方式」逐组合验证，唯一挂死组合为：
   **宿主 stdin 是永不关闭的管道 + 有线程阻塞读该管道 + 子进程继承该管道
   → bash 8 秒不退出（HANG）**；子进程 `stdin=DEVNULL` 或宿主 stdin 为 tty
   均正常。

## 根因分析

- **直接根因**：`BashTool._call_normal` 与沙箱执行器创建子进程时不传 `stdin`，
  子进程默认**继承宿主 stdin**。serve 宿主的 stdin 是 Electron 主进程 spawn 时
  建的**永不关闭的管道**，且 serve 内 `_watch_stdin` 线程正阻塞读该管道（停机
  信号监视）。MSYS2 bash 启动时 Cygwin 初始化会探测 stdin 句柄，探测挂在父进程
  同句柄的 pending 阻塞读上 → bash 永不退出、stdout/stderr 管道 EOF 永不到达 →
  `communicate()` 只能吃满超时 → recovery 重试再挂 → 工具调用永久卡死。
- **为什么 REPL 正常**：REPL 的 stdin 是 tty（字符句柄），Cygwin 探测 tty 不走
  管道 pending-read 路径，不触发挂死。
- **放大链**：`enable_tool_self_healing` 开启时 TIMEOUT 策略 max_retries=2，
  每次重试再挂 120s，用户感知为"永远不回复"。
- **同批发现的第二处潜伏缺陷**：`WorkbenchUI.ask_user` 用
  `threading.Event.wait(600)` **同步阻塞**等待前端回答。serve 宿主下调用方就是
  引擎事件循环线程，阻塞期间同环串行的 `_command_loop` 无法消费 `answer_user`
  指令 → 自死锁直到 600s 超时（与 voice 饿死同类问题，本次一并修复）。
- **涉及模块**：`agent/tools/bash.py`、`agent/core/sandbox/executor.py`、
  `agent/ui/workbench/bridge.py`、`agent/core/orchestrator.py`、
  `agent/core/error_recovery.py`。

## 修复方案

1. **子进程 stdin 隔离**：
   - `bash.py::_call_normal` 的 `create_subprocess_exec` 显式
     `stdin=asyncio.subprocess.DEVNULL`；
   - `sandbox/executor.py` 的 3 处 `create_subprocess_exec` 同样加
     `stdin=DEVNULL`。
2. **取消时回收子进程**：`_call_normal` 捕获 `asyncio.CancelledError`（停止回复
   取消穿透）时先 `proc.kill()` 再 re-raise，不留孤儿 bash。
3. **询问异步化**：`WorkbenchUI` 新增 `ask_user_async`（等待放
   `asyncio.to_thread` 工作线程，事件循环保持可调度）；`orchestrator` 权限询问
   与 `error_recovery` 询问站点改为 `hasattr(ctx.ui, "ask_user_async")` 优先
   异步版，同步宿主（REPL）行为不变。

## 新增功能：停止回复（reply.abort，第 18 条桌面指令）

同批交付用户点名的交互：回复进行中"发送"按钮变"■ 停止"，再点停止回复/思考。

- **协议**：`protocol.py` 新增 `CMD_REPLY_ABORT = "reply.abort"` 并入
  `DESKTOP_COMMANDS`（指令 17→18）；`server.py` 注册 rpc；`api.py` 新增
  `abort_reply()`。
- **引擎**：`_handle_send` 记录 `_send_task = asyncio.current_task()`；
  `abort_current_reply()` 经 `loop.call_soon_threadsafe(task.cancel)` 线程安全
  取消（**不入指令队列**：队列被当前 send 轮次串行占用，入队会自死锁——与
  answer_user 同型陷阱）。CancelledError 沿 `_stream_once` / 工具层优雅退出，
  `_handle_send` 的 finally 仍发 `assistant_done` 让前端收尾，`_command_loop`
  捕获后发 info「已停止回复」继续服务后续指令。
- **桌面壳**（jarvis-desktop 仓库）：`contracts.ts` 加 `Cmd.ReplyAbort`；
  `backendStore.abortReply()` 发指令并把状态栏置「正在停止...」（不改 busy，
  由 `assistant_done` 统一收尾）；`dispatcher.ts` 的 `assistant_done` 分支补
  `chat.setBusy(false)`；`ChatArea.tsx` 发送按钮改双态——busy 时渲染
  「■ 停止」（点击发 `reply.abort`），且 busy 中 Enter 不叠发消息。

## 验证结果

- 新增回归测试 `tests/ui/test_serve_stdin_abort.py`（5 用例）：
  - `_call_normal` 创建子进程必须带 `stdin=DEVNULL`（kwargs 捕获断言）；
  - 取消穿透 `communicate` 等待时子进程被 kill 回收再抛 CancelledError；
  - `ask_user_async` 等待回答期间同环心跳协程持续 tick（修复前同步版在此
    场景直接饿死循环）且答案正确返回；
  - 引擎 send 进行中 `abort_current_reply()` → assistant_done 收尾 +
    info「已停止回复」+ 后续指令仍被消费（循环存活）；
  - 无进行中回复时 abort 返回 False。
- `tests/serve/test_serve_protocol.py` 新增：`reply.abort` 注册与透传断言、
  指令总数契约 `len(DESKTOP_COMMANDS) == 18`。
- jarvis 全量 `pytest tests -q` → **1722 passed**（1 个既有无关 warning）。
- jarvis-desktop：`npm run typecheck` 通过；`npm run test` → **122 passed**
  （新增 dispatcher busy 收尾、backendStore abortReply、ChatArea 双态按钮
  与 busy 禁发共 5 用例）。
- 人工复测（待确认）：桌面壳发触发工具的提问应正常回复；回复中点「■ 停止」
  应中断并提示「已停止回复」。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `jarvis/agent/tools/bash.py` | `_call_normal` 子进程 `stdin=DEVNULL` + CancelledError 回收子进程 |
| `jarvis/agent/core/sandbox/executor.py` | 3 处 `create_subprocess_exec` 加 `stdin=DEVNULL` |
| `jarvis/agent/ui/workbench/bridge.py` | 新增 `ask_user_async`（to_thread 等待，不饿死引擎循环） |
| `jarvis/agent/core/orchestrator.py` | 权限询问站点优先 `ask_user_async` |
| `jarvis/agent/core/error_recovery.py` | 询问站点优先 `ask_user_async` |
| `jarvis/agent/ui/workbench/engine.py` | `_send_task` 句柄 + `abort_current_reply()` + `_command_loop` 捕 CancelledError 继续服务 |
| `jarvis/agent/serve/protocol.py` | `CMD_REPLY_ABORT` 常量 + 入 `DESKTOP_COMMANDS`（18 条） |
| `jarvis/agent/serve/server.py` | 注册 `reply.abort` rpc |
| `jarvis/agent/ui/workbench/api.py` | 新增 `abort_reply()` |
| `jarvis/tests/ui/test_serve_stdin_abort.py` | 新增：stdin DEVNULL / 取消回收 / ask_user_async / 引擎 abort 回归 |
| `jarvis/tests/serve/test_serve_protocol.py` | 新增：reply.abort 注册 + 指令总数 18 契约 |
| `jarvis-desktop/src/shared/contracts.ts` | `Cmd.ReplyAbort` 镜像 |
| `jarvis-desktop/src/renderer/src/stores/backendStore.ts` | `abortReply()` 动作 |
| `jarvis-desktop/src/renderer/src/api/dispatcher.ts` | `assistant_done` 补 `setBusy(false)` |
| `jarvis-desktop/src/renderer/src/components/ChatArea.tsx` | 发送/停止双态按钮 + busy 禁发 |

## 经验总结

- **子进程默认继承宿主 stdin 是隐蔽的跨宿主炸弹**：同一份工具代码在 REPL
  （tty）下正常，在 serve（Electron 管道 + watch 线程阻塞读）下挂死。凡在
  常驻服务里 spawn 子进程，不需要交互输入的一律显式 `stdin=DEVNULL`。
- **MSYS2/Cygwin 进程对管道 stdin 的初始化探测会被父进程同句柄的 pending
  阻塞读卡住**：表现为子进程启动了但永不退出、管道 EOF 永不到达，
  `communicate()` 只能吃满超时——现象是"静默卡死"而非报错。
- **对照实验矩阵是这类多因素挂死问题的最快收敛手段**：把「stdin 类型 ×
  读者 × 继承方式」做成组合矩阵逐一验证，唯一挂死组合即根因。
- **同步阻塞等待用户输入 = 饿死引擎事件循环**：与 voice-stop 修复同型；
  异步宿主必须走 `ask_user_async`（to_thread），并且"停止/回答"类指令绝不能
  入被当前轮次串行占用的指令队列（自死锁），必须走线程安全 task.cancel。
- **孤儿进程要显式回收**：取消路径（CancelledError）里先 kill 子进程再
  re-raise，否则每次停止回复都会泄漏一个 bash。
