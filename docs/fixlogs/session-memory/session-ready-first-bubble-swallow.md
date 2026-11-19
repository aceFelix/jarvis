# 修复：首发用户气泡被 session_ready 清屏语义吞掉

- 日期：2026-09-25
- 范围：jarvis-desktop（dispatcher / backendStore / 单测 / 架构文档）、jarvis（workbench `app.js`）
- 作者：aceFelix

## 现象

启动桌面壳后发送第一条消息（如「你好jarvis」）：中栏只出现 AI 回复气泡，
用户气泡消失；右栏用量卡显示「2 轮 / 6 条」证明后端消息记录完整，
重新打开该历史会话（session_loaded 回放）首条用户气泡又会出现。
第二条及以后的消息气泡均正常。用户一度怀疑「后端没启动完就发消息、渲染不及时」。

## 排查与根因

事件时序（首条 send）：

1. 渲染层 `backendStore.sendMessage` 乐观上屏：`chatStore.addUser(...)` + `setBusy(true)`；
2. 后端 `_dispatch` 收到 send → `_ensure_session()`（引擎懒装配，每个后端进程仅首次触发）
   → 装配完成时 emit `session_ready`；
3. 渲染层 dispatcher 的 `case 'session_ready': chat.clear()` —— **清屏把刚乐观上屏的
   首发用户气泡擦掉**；
4. 后续 `user_message` 回显（本地已上屏故跳过）、`assistant_text` 增量正常渲染 →
   屏幕上只剩 AI 回复。

第二条消息起 `_session_ready` 已置位、不再发 `session_ready`，气泡不再被吞——
与「只有首发消息消失」的现象精确吻合。与发送时机快慢无关：只要是一次后端启动后的
第一条消息（send/load/new 任一首指令为 send 时）必现。

`session_ready` 的清屏语义是历史遗留的「引擎装配完成 = 清屏初始化」约定；此前已因
标题改名复用该事件踩过一次「整屏空白」（见 `jarvis-desktop-serve-realtest-fix.md`，
改名改走 `session_renamed`），但事件本身的清屏语义一直保留，首发气泡是其第二个受害者。

## 修复

语义拆分：**通知归通知，清屏归连接生命周期**。

| 位置 | 改动 |
|---|---|
| `jarvis-desktop/src/renderer/src/api/dispatcher.ts` | `session_ready` 去掉 `chat.clear()`，只刷新会话列表（与 `session_renamed` 同口径） |
| `jarvis-desktop/src/renderer/src/stores/backendStore.ts` | `applyBackendStatus` 接管清屏初始化：`ready` 且后端 pid 与上一次不同（崩溃重启换代）时 `chatStore.clear()` 清旧气泡；同 pid 瞬断重连 / 首次 ready 不清 |
| `jarvis/agent/ui/workbench/assets/app.js` | `session_ready` 从 `session_new` 的 fall-through 拆出：只刷新会话列表；清屏初始化由页面加载空屏 + `session_new` 兜底（pywebview 引擎与窗口同进程，无换代场景） |
| `jarvis-desktop/test/renderer/dispatcher.test.ts` | 新增用例：`session_ready` 后首发用户气泡仍在且刷新会话列表 |
| `jarvis-desktop/test/renderer/backendStore.test.ts` | 新增 describe：pid 换代清屏 / 同 pid 不清 / 首次 ready 不清（`vi.mock` ws 模块避免 jsdom 真连） |
| `jarvis-desktop/docs/architecture.md` | 事件语义章节补 `session_ready` 新口径与清屏初始化归属 |

后端 `engine.py` 不改：`session_ready` 仍按原时机 emit（装配完成通知），
语义解释权收归前端。

## 验证

- `vitest run`（jarvis-desktop 全量）：11 files / 174 tests passed；
- `electron-vite build`：main / preload / renderer 三段构建通过；
- `tsc --noEmit`（tsconfig.node.json / tsconfig.web.json）：0 错误；
- 实机走查口径：启动后首发消息 → 用户气泡与 AI 回复同屏； kill 后端进程等主进程
  拉起新后端 → 旧气泡被清、新会话从空屏开始。

## 经验

1. **带清屏语义的事件是稀缺资源**：任何「中途」可能到达的清屏事件都会吞掉已上屏内容；
   清屏初始化应挂在连接/页面生命周期（进程换代、页面加载），而非业务事件。
2. **乐观上屏的内容要被后续事件保护**：后端回显/装配类事件到达时，先问一句
   「屏幕上有没有它不知道但用户已看到的东西」。
3. 同一事件语义已造成两次事故（整屏空白 → 首发气泡消失）后才彻底拆分——
   事件复用欠的债会连本带利回来，第一次踩坑时就该拆语义。
