# jarvis-desktop 实机测试修复复盘（serve 桥接首轮实测五连修）

- 日期：2026-09
- 作者：aceFelix
- 范围：jarvis（`--serve` 后端桥接）+ jarvis-desktop（Electron 桌面壳）首轮实机测试暴露的五个问题
- 关联：[docs/plans/jarvis-desktop.md](../plans/jarvis-desktop.md)、[docs/architecture/08-Daemon模式.md](../architecture/08-Daemon模式.md)

## 1. 问题现象

jarvis-desktop 立项落地后首次实机使用，连续暴露五个问题：

1. **启动横幅报错**：桌面应用启动瞬间左栏弹出红色横幅「未连接到后端」，随后又自动恢复连接；
2. **jarvis 不说话**：后端状态长时间「启动中」，serve 子进程无响应，桌面应用连不上 WS；
3. **刚开始回答就空白**：发出第一条消息、流式回复渲染到一半时整屏气泡被清空；
4. **加载会话不好使**：点击历史会话报「会话不存在或为空: auto-latest/session-xxx」红横幅（多条）；
5. **回复逐词重复**：模型回复出现「用户用户问问」式口吃文本，每个增量词重复一遍。

## 2. 排查过程

### 问题 1：启动横幅报错（jarvis-desktop）

- 假设：后端真的没起来 → 排除，日志显示 WS 随后连接成功；
- 确认：`LeftSidebar` 首次渲染时 `backendStatus` 还是初始值 `idle`，守卫把「尚未探测」误判成「连接失败」渲染横幅；
- 根因确认：渲染竞态——状态探测结果回来前组件已按初值渲染。

### 问题 2：serve 子进程 hang（jarvis）

- 现象：`jarvis --serve` 子进程启动后 CPU 归零、端口不监听；
- 弯路：先怀疑端口占用 / token 生成阻塞，均排除；
- 转折点：py-spy dump 栈停在 numpy/OpenBLAS 动态库加载阶段；
- 根因确认：OpenBLAS 的 DllMain 在子进程加载器锁上下文里死锁（Windows 已知加载器死锁模式）。

### 问题 3：刚开始回答就空白（jarvis + 双前端）

- 假设：前端渲染 bug → 排除，落盘消息完整；
- 转折点：抓事件流发现首轮回复结束后引擎重发了一次 `session_ready`；
- 根因确认：`engine._after_turn` 标题生成改名后复用 `session_ready` 事件通知前端，而 `session_ready` 在两个前端（workbench `app.js`、desktop `dispatcher.ts`）都是「清空气泡重新初始化」语义 → 刚渲染的回复被清掉。

### 问题 4：加载会话失败（jarvis + 磁盘存量数据）

- 现象：列表里的会话名点进去报不存在；
- 转折点：对比磁盘文件——12 个会话文件「文件名 = 中文标题、文件内 meta.name = 旧名」；
- 根因确认：`_rename_session_file` 只 `Path.rename` 改文件名，不回写文件内 `meta.name`；而 `list_sessions` 上报的是文件内 meta.name → 列表旧名 → 按旧名定位文件失败。另 `old_name == "auto-latest"` 时 rename 把启动恢复指针文件整体移走。

### 问题 5：回复逐词重复（jarvis-desktop）

- 假设：模型输出重复 → 排除，落盘文本干净（临时脚本读会话 json 验证）；
- 假设：bridge broadcast 重复 → 排除，服务端每客户端只发一次；
- 转折点：netstat 抓到渲染进程对 serve 端口存在**两条 ESTABLISHED WS 连接**，每条客户端各自 dispatch 一遍流式增量 → 逐词重复；
- 弯路：StrictMode 双挂载、ws 重连夹缝、模块双实例等假设均与现有 store 守卫矛盾，第二连接的创建路径未能从代码确证；
- 决策：放弃考古，上结构性保险（realm 级单例）+ 日志桥留证。

## 3. 根因分析

| # | 根因 | 模块 |
|---|---|---|
| 1 | 组件按状态初值渲染，把「未探测」当「失败」 | jarvis-desktop `LeftSidebar.tsx` |
| 2 | OpenBLAS DllMain 子进程加载器死锁 | jarvis `agent/serve/app.py` 子进程导入链 |
| 3 | 事件语义混用：改名通知复用了带清屏语义的 `session_ready` | jarvis `engine.py` + 双前端 dispatcher |
| 4 | 改名只改文件名不回写 meta.name；auto-latest 指针被 rename 移走 | jarvis `session_manager.py` |
| 5 | 渲染进程双 WS 连接双消费流式增量 | jarvis-desktop `backendStore.ts` |

## 4. 修复方案

1. **LeftSidebar 竞态守卫**：新增 `wsConnected`/已探测守卫，状态探测返回前不渲染失败横幅；
2. **OpenBLAS 预加载**：serve 子进程入口 `_prewarm_native_libs()` 在安全上下文提前加载 numpy 系原生库，绕开 DllMain 死锁；
3. **事件语义拆分**：新增 `session_renamed` 事件（标题改名，前端只刷列表不清屏）；`engine._after_turn` 两处改名改发该事件；workbench `app.js` 与 desktop `dispatcher.ts` 各加 case；`serve/protocol.py` 事件清单同步；
4. **改名回写 + 指针保护**：`session_manager` 新增 `_sync_meta_name`（rename 后回写文件内 meta.name）与 `_move_or_copy_pointer`（old_name 为 auto-latest 时改复制、保恢复指针）；存量 12 个会话文件磁盘修复（meta.name := 文件名 stem）；
5. **realm 级 WS 单例保险**：`backendStore.ts` 用 `globalThis.__jarvisLiveWs` 跨模块实例共享存活客户端槽，新连接接管前 `close()` 任何漏网旧客户端；配套渲染进程→desktop.log 日志桥（`contracts.RendererLog` / preload `log` / main `ipcMain.on`），connect 带调用栈留证。

临时方案说明：问题 5 的单例保险是结构性兜底而非根因修复（第二连接创建路径未确证）；日志桥已就位，若再现可凭 desktop.log 的 `ws connect stack=` 行定位创建者。

## 5. 验证结果

- 单元/回归：`pytest tests/test_session_manager.py tests/ui/test_workbench_engine_api.py tests/test_autostart_cli.py` → 62 passed（含新增 4 个 rename/事件回归用例）；
- 类型检查：jarvis-desktop `npm run typecheck`（node + web）通过；
- 实机：重启桌面应用后 desktop.log 单条 `ws connect`（带栈）、netstat 单条 ESTABLISHED；用户窗口实测发消息不再逐词重复、历史会话可正常加载、首轮回覆不再空白；
- 磁盘：12 个存量会话文件 meta.name 与文件名一致，auto-latest 指针恢复。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `jarvis-desktop/src/renderer/src/components/LeftSidebar.tsx` | wsConnected 守卫，杜绝启动误报横幅 |
| `jarvis-desktop/src/renderer/src/stores/backendStore.ts` | realm 级 WS 单例保险 + connect/disconnect 诊断日志 |
| `jarvis-desktop/src/renderer/src/api/dispatcher.ts` | 新增 `session_renamed` case（只刷列表不清屏） |
| `jarvis-desktop/src/shared/contracts.ts` | 新增 `RendererLog` IPC 通道 |
| `jarvis-desktop/src/preload/index.ts` / `index.d.ts` | 暴露 `log()` 日志桥 + 类型声明 |
| `jarvis-desktop/src/main/index.ts` | `ipcMain.on(RendererLog)` 落 desktop.log |
| `jarvis/agent/serve/app.py` | `_prewarm_native_libs()` 预加载原生库绕开 DllMain 死锁 |
| `jarvis/agent/ui/workbench/engine.py` | 标题改名改发 `session_renamed` |
| `jarvis/agent/ui/workbench/assets/app.js` | 工作台前端处理 `session_renamed` |
| `jarvis/agent/serve/protocol.py` | 事件清单文档串同步 |
| `jarvis/agent/session_manager.py` | `_sync_meta_name` / `_move_or_copy_pointer` |
| `jarvis/tests/test_session_manager.py` | 3 个 rename 回归测试 |
| `jarvis/tests/ui/test_workbench_engine_api.py` | `session_renamed` 事件回归测试 |

## 7. 经验总结

- **事件语义要单一**：带「清屏/初始化」语义的事件绝不能复用作轻量通知，否则新前端接入即踩坑；新增通知类事件时双前端（workbench/desktop）case 要同步补；
- **改名要改全**：文件名与文件内元数据是两份状态，rename 必须双写；指针类文件（auto-latest）参与 rename 时要改复制语义；
- **Windows 子进程 + 原生库**：numpy/OpenBLAS 系导入放在子进程入口预加载，规避 DllMain 加载器死锁；hang 排查用 py-spy dump 栈最快；
- **双连接双消费**：流式增量被重复渲染时先查连接数（netstat），再查服务端 broadcast，最后查客户端实例；store 级守卫挡不住模块双实例，realm 级（globalThis）单例才是兜底；
- **落盘文本是金标准**：回复内容异常时先读会话 json 区分「引擎侧重复」还是「渲染侧重复」，一步缩小一半排查面；
- **考古失败就上结构保险**：根因无法确证时，用结构性兜底 + 日志桥留证，比无限期排查更符合交付节奏。
