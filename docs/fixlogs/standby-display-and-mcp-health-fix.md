# 修复：语音 `<standby/>` 泄漏到上屏气泡 + MCP 就绪后右栏运行健康不刷新

- 日期：2026-09-29
- 范围：jarvis（voice/voice_loop.py、ui/workbench/mcp_runtime.py、serve/protocol.py、tests）、jarvis-desktop（api/dispatcher.ts、test）、文档同步
- 作者：aceFelix

## 现象

1. **退下标记泄漏到显示文本**：语音模式说「退下吧」，贾维斯回「好的先生，我先退下了……」，
   桌面壳 / 终端气泡里**结尾出现了字面 `<standby/>`**（用户本不该看到这个控制标记）。
2. **MCP 明明连上了，右栏却一直显示「MCP 未启用」**：桌面壳运行正常、配置了 MCP server，
   但右栏「运行健康」的 MCP 卡片停在「MCP 未启用」；且这一轮问天气，模型没走 MCP 工具
   而是去网页搜索。

## 排查与根因

### 一、`<standby/>` 泄漏（显示路径漏剥标）

`<standby/>` 是语音 system prompt 约定的**退下控制信令**：模型识别到用户想结束对话时，
说告别语 + 结尾输出该标记，`_detect_standby()` 据此切待机。它有两条出口：

- **音频路径**：`tts_text.py` 的 `_TTSFeeder._clean_inline` 一直在朗读前 `_STANDBY_TAG.sub`
  剥标——所以**听不到**，符合预期；
- **显示路径**：voice_loop 把回复外抛给宿主上屏（`events.on_ai_text` 全量 /
  `on_ai_text_delta` 流式）时**直接透传原文**，没剥标——气泡里于是残留字面 `<standby/>`。

即：剥标只做在了 TTS 侧，显示流这一路漏了。退下检测本身读的是原始 `ctx.messages`，
不受上屏文本影响，所以功能正常，纯粹是**显示层 bug**。

### 二、MCP 运行健康停在「未启用」（首刷早于后台预热，缺补刷事件）

桌面右栏的 MCP 卡片数据源是 `state.get` 的 `mcp` 快照（`{connected, failed, tools}` 或 null）。
引擎启动走**后台预热**（`mcp_runtime.prewarm` → `connect_mcp`），MCP 多 server 并发连接实测
约 **9 秒**才落定。而桌面壳 WS 一连上就收到 `init` 事件 → 七路齐刷里 `refreshState()` 拉
`state.get`：

- 此刻 `connect_mcp` 往往还没跑完，`engine._mcp_status` 仍是初始值 `None`；
- `state.get` 回 `mcp: null` → 右栏渲染为「MCP 未启用」；
- **MCP 连上后没有任何事件再驱动右栏刷新**，卡片就永久停在 None 态，直到用户手动重连。

问天气走 WebSearch 是同一根因的另一面：预热窗口内首条消息到达时 MCP 工具尚未注册进本
会话 registry，模型视野里没有专业天气工具，只能退用 WebSearch（预热完成后的后续消息即可
用上 MCP 工具）。

## 修复

### 一、voice_loop 外抛显示流前统一剥标

| 位置 | 改动 |
|---|---|
| `jarvis/agent/voice/voice_loop.py` | `_feed` 内 `events.on_ai_text_delta(_STANDBY_TAG.sub("", text))`（流式 delta 剥标）；收尾 `events.on_ai_text(_STANDBY_TAG.sub("", reply_text))`（全量剥标，桌面 `voice_ai_text` 为全量替换、是最终显示态唯一权威）。退下检测仍读原始 `ctx.messages` 不受影响 |

跨 chunk 切割的极端情形（`<standby` + `/>` 分两帧）由收尾全量替换兜底，不影响最终显示。

### 二、MCP 落定推 `mcp_ready` 事件 + 桌面壳据此补刷

| 位置 | 改动 |
|---|---|
| `jarvis/agent/ui/workbench/mcp_runtime.py` | `connect_mcp` 在**成功**与**全部失败**两条落定分支设置 `_mcp_status` 后各 `engine._emitter.emit("mcp_ready", engine._mcp_status)`；未启用 / 无配置 / 异常路径不推（None 即真实态） |
| `jarvis/agent/serve/protocol.py` | 新增事件常量 `EVT_MCP_READY = "mcp_ready"` + 协议文档事件清单补一条（payload = `state.get` 的 mcp 快照）；事件泵透传，无需改 server |
| `jarvis-desktop/src/renderer/src/api/dispatcher.ts` | 新增 `case 'mcp_ready'`：payload 结构合法则 `useRightStore.setMcp(...)` 直写快照（免再发一次 `state.get`）；结构非法时退回 `conn.refreshState()` 主动拉一次，避免谎报 |

## 验证

- jarvis：`pytest tests/voice/test_voice_standby_display.py tests/ui/test_workbench_engine_mcp.py -q`
  全绿（新增语音剥标 3 例 + MCP `mcp_ready` 断言 2 处）；全量 `pytest -q` **2186 passed**（+3，无回归）；
- jarvis-desktop：`vitest run test/renderer/dispatcher.test.ts` **43 passed**（+3 mcp_ready 用例）；
  `npm run typecheck` + `npm run build` 均通过；
- 实机走查：语音说「退下吧」→ 告别语气泡无 `<standby/>` 且正常进待机；启动桌面壳静候 ~9s →
  右栏「MCP 未启用」自动刷成「MCP N 连 · M 工具」。

## 经验

1. **一条文本多个出口时，清洗要在每个出口做**：`<standby/>` 在 TTS 侧剥了不等于显示侧也剥了。
   控制信令类标记应在**所有对外呈现路径**统一剥离，别只补音频那条最显眼的。
2. **「首刷一次」扛不住「数据源晚到」**：MCP 后台预热慢于 init 首刷，快照必然还是空的。凡是
   「异步落定的状态」都要有**落定事件**驱动消费端补刷（对齐 init 按连接首推的思路），不能只靠
   一次性快照。
3. **壳优先信 payload、结构校验兜底退回拉取**：就绪事件已携带完整快照时直写即可，但要对
   畸形 payload 保持防御（回退到权威 `state.get`），避免把谎报数据渲染成健康态。
