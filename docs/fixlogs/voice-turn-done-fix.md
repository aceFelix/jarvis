# 修复复盘：桌面/终端语音轮缺收尾信号，气泡「疯狂输出思考、停不下来」

- **日期**：2026-10-12
- **模块**：`agent/voice/`（voice_events / voice_loop）、`agent/ui/workbench/voice_adapter.py`、`jarvis-desktop` dispatcher
- **作者**：aceFelix

## 1. 问题现象

先生对 Jarvis 说：「明天记得提醒我，上午十二点有一场面试啊，发我邮箱。」

- **桌面 jarvis**：邮件确实发出，但 Jarvis 一直在「思考」——气泡里疯狂输出思维链，
  一分钟才把事办妥；**语音消息（TTS）已答复"办妥了"，气泡框却仍在持续输出思考，停不下来**。
- **终端 jarvis**：先生补充「终端 jarvis 也是这个毛病」，同样出现"回复已结束、思考仍在刷"。

复现路径（最小）：进入 `/voice` 半双工语音模式，对 Jarvis 说一句话完成一轮问答后观察气泡与
发送按钮（停止键）。

## 2. 排查过程

1. **先看桌面前端渲染链**：`jarvis-desktop/src/renderer/src/api/dispatcher.ts` 与
   `stores/chatStore.ts`。确认 `assistant_thinking` 增量累加进同一条 AI 气泡、由
   `finishAssistant()` 定稿（把 `streaming` 置 false）；`busy` 驱动发送键变「■ 停止」。
2. **核对语音分支**：语音轮的事件是 `voice_*` 系列（`voice_ai_text_delta` /
   `voice_ai_text` / `voice_stopped`）。发现 `voice_ai_text` 的 `if (lastAi)` 分支**只**
   把气泡 `streaming` 置 false，**既不调 `finishAssistant()`，也不清 `busy`**；
   `voice_stopped` 同样只切模式、不收尾。
3. **追后端收尾信号**：文本轮靠 `query_loop` 尾部的 `assistant_done` 收尾；但**语音轮走的是
   `agent/voice/voice_loop.py` 的 `_voice_loop_round`，其所有返回路径都不发
   `assistant_done`**（语音与文本是两条独立回路）。
4. **对照终端**：终端 `RichCLIVoiceAdapter` 不做气泡定稿（REPL 无气泡概念），所以"终端也犯"
   的根因同样是**语音轮缺一个统一的"本轮结束"信号**——只是表现在终端上是状态行/思考不收敛。
5. **确认"点停止也停不下来"**：点停止走 `reply.abort` → `aborted=true` 闸门；闸门**只由
   `assistant_done` 解除**。语音轮从不发 `assistant_done`，于是闸门永久开启，后续增量被丢弃、
   忙碌态也撤不回——与"停不下来"完全吻合。

## 3. 根因分析

**根因：语音轮（`/voice`）缺少统一的"一轮结束"收尾信号。**

- **协议层**：`VoiceSessionEvents` 只定义了"状态 / 转录 / 文本增量 / 全量文本 / 信息 / 错误"，
  **没有任何方法表达"这一轮彻底结束了"**。文本轮有 `assistant_done`，语音轮没有对等物。
- **引擎层**：`voice_loop` 每轮（听→想→说）跑完 `_voice_loop_round` 后不向宿主发收尾；
  无论成功回复、被打断、待机退下还是中途出错，宿主都收不到"本轮结束"。
- **前端层**：桌面前端把"收尾"绑定在 `assistant_done` 上（解除 `aborted` 闸门 + 定稿气泡 +
  清 `busy`）。`voice_ai_text` 只在"有流式气泡"时替换文本，**漏了定稿与清忙碌**；
  `voice_stopped`（会话级结束）也没有兜底收尾。
- **合成效果**：语音已播报完毕，但气泡 `streaming` 永为 true（思考块自动展开、光标闪烁）、
  `busy` 永为 true（发送键卡在停止态）、`aborted` 永为 true（点停止后闸门不解）——
  即先生看到的"语音已回复我办妥了，气泡却仍在疯狂输出思考、停不下来"。

> 说明：本次按先生选定的范围**只修语音收尾**（第 1 层直接对症根因）。"超长思考全量上屏 /
> 提示词导致 reasoning 先生看不到"属第 2 层根因，先生明确**暂不修**，另行处理。

## 4. 修复方案

### 4.1 后端协议：新增 `on_turn_done`

`agent/voice/voice_events.py`：

- `VoiceSessionEvents` 协议在 `on_ai_text` 之后新增 `on_turn_done()`，语义为"一轮语音
  （听→想→说）彻底收尾，无论成功 / 被打断 / 待机退下 / 出错都应发出"。
- `VoiceEventsBase` 补空实现（适配器只覆盖关心的方法）。
- `CollectingVoiceEvents` 补 `_rec("turn_done", None)`（单测可断言）。

### 4.2 后端主循环：每轮 finally 补发收尾

`agent/voice/voice_loop.py`：把主循环里的 `cont = await _voice_loop_round(...)` 用
`try/finally` 包裹，`finally` 中**无条件** `events.on_turn_done()`。这样任何退出路径
（正常回复、被打断、异常）都能补发一次收尾信号。

### 4.3 serve 适配器：映射为 `assistant_done`

`agent/ui/workbench/voice_adapter.py`：`ServeVoiceAdapter.on_turn_done()` →
`self._emitter.emit("assistant_done", "")`，**复用文本轮的同一收尾事件**，让桌面前端
走既有 `assistant_done` 分支（解除闸门 + 定稿 + 清 busy + 刷用量）。

### 4.4 桌面前端：`voice_ai_text` / `voice_stopped` 就地收尾兜底

`jarvis-desktop/src/renderer/src/api/dispatcher.ts`：

- `voice_ai_text`：替换全量文本后统一补 `finishAssistant()` + `setBusy(false)`
  （语音轮收尾信号要等 TTS 播完才到，先就地定稿，避免气泡残留流式）。
- `voice_stopped`：会话结束兜底，补 `setAborted(false)` + `finishAssistant()` +
  `setBusy(false)`，防止异常退出时收尾信号缺席导致闸门/忙碌态永久卡住。

## 5. 验证结果

- 新增单元测试（后端）：`tests/voice/test_voice_events.py`
  - `TestServeVoiceAdapter::test_turn_done_maps_to_assistant_done`：`on_turn_done` →
    `("assistant_done", "")`；
  - `TestVoiceEventsProtocol::test_collecting_records_order` / `test_base_default_noop`
    纳入 `turn_done`。
- 新增单元测试（后端）：`tests/voice/test_voice_loop_turn_end.py`
  - `test_turn_done_fires_once_per_growing_round`（2 轮 → 2 个 turn_done）；
  - `test_turn_done_fires_even_on_empty_round`（空轮也发 1 个）；
  - `test_turn_done_fires_on_round_exception`（轮次抛异常经 `finally` 也发 1 个）。
- 新增单元测试（前端）：`jarvis-desktop/test/renderer/dispatcher.test.ts`
  - `voice_ai_text 定稿气泡并清忙碌态` / `无流式气泡时新建并定稿` /
    `voice_stopped 兜底收尾：解闸门 + 定稿 + 清忙碌态`。
- 定向验证：`pytest tests/voice -q` → **260 passed**。
- 全量回归（后端）：`pytest -q` → **2434 passed**。
- 全量回归（前端）：`npx vitest run` → **431 passed（21 文件）**；
  `npm run build` / `npm run typecheck` 均通过。
- 未引入回归；行为影响面仅限语音轮收尾信号与桌面收尾分支。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/voice/voice_events.py` | 协议 + 基类 + 收集实现新增 `on_turn_done()` |
| `agent/voice/voice_loop.py` | 主循环 `_voice_loop_round` 用 try/finally 包裹，finally 无条件发 `on_turn_done()` |
| `agent/ui/workbench/voice_adapter.py` | `ServeVoiceAdapter.on_turn_done` → `assistant_done` 事件 |
| `jarvis-desktop/src/renderer/src/api/dispatcher.ts` | `voice_ai_text` 补定稿+清忙碌；`voice_stopped` 补解闸门+定稿+清忙碌 |
| `tests/voice/test_voice_events.py` | 新增 ServeVoiceAdapter/协议 turn_done 断言 |
| `tests/voice/test_voice_loop_turn_end.py` | 新增 3 例 turn_done 单测；`_run_voice_loop` 改返回 events 对象 |
| `jarvis-desktop/test/renderer/dispatcher.test.ts` | 新增 3 例语音收尾断言 |
| `docs/guide/desktop.md` | 注明语音轮收尾信号（on_turn_done → assistant_done）语义 |

## 7. 经验总结

- **"收尾信号"是每条回路的必需品**：文本轮有 `assistant_done`，语音轮当初没补对等信号，
  导致前端把"收尾"绑死在 `assistant_done` 上时语音轮永远收不了尾。**新增任何一条独立的
  回复回路（语音 / 实时 / 主动播报）时，都要先问一句"这条回路怎么收尾"**。
- **收尾要放在 `finally`**：一轮结束可能因成功、打断、待机、异常等多种路径退出，收尾信号
  必须在 `finally` 无条件补发，否则总有一条路径漏掉，现象就是"偶发停不下来"。
- **前端收尾就地兜底**：后端收尾信号可能因时序（等 TTS 播完）或异常缺席，前端对
  `voice_ai_text` / `voice_stopped` 这类"确定一轮已结束"的事件应就地定稿，不依赖远端信号。
- **闸门（aborted）要有兜底解除路径**：`aborted` 若只由单一事件解除，一旦该事件缺席就会
  永久卡住并吞掉后续增量；关键闸门应设置多个解除入口（`assistant_done` / `voice_stopped`）。
- **桌面/终端共享引擎**：同一后端缺陷会在两端以不同表象出现（桌面＝气泡停不下来，
  终端＝状态不收敛）。排查"停不下来"类反馈时，把两端都纳入比对能快速收敛到引擎层。
