# 修复：语音模式下桌面壳「退出语音 / 文本 / 实时 / 打断」按钮无响应

## 问题现象

桌面壳（jarvis-desktop）进入「🎤 语音」模式（/voice 半双工）后：

- 点击聊天区「⏹ 退出语音」按钮：无任何反应，语音会话不退出；
- 点击左栏「💬 文本」「🎧 实时」模式按钮：无反应，模式不切换；
- 点击「✋ 打断」按钮：聆听/播报期间同样无反应。

此时语音功能本身正常（聆听状态、识别、播报都在跑），仅宿主控制通道失效。
REPL 终端 `/voice` 不受影响（Ctrl+C / ESC 行为正常），问题只在 serve / 桌面壳宿主。

## 排查过程

1. **先查前端**：检查 `backendStore.toggleVoice / interruptVoice`、`LeftSidebar.switchMode`、
   `dispatcher` 的 voice 事件分支——指令发送与事件回填逻辑均正确，且单测覆盖通过，排除前端。
2. **再查协议层**：`voice.stop / voice.interrupt / talk.start` 指令在 serve server 注册齐全，
   WS 收发正常（voice.start 能成功启动语音即为证明），排除协议常量漂移。
3. **最后查引擎消费链**：workbench engine 跑在独立线程的独立事件循环上，
   `_command_loop` 从指令队列取指令并 `await self._dispatch(cmd)`。关键发现：
   `voice_loop` 的 asyncio task 与 `_command_loop` **同处一个事件循环**，而
   `voice_loop` 内部的 `stt.listen()`（pyaudio 录音）与 `stream_player.finish()`
   （阻塞等播完）是**同步阻塞调用**，直接在 async 函数里执行 → 任务不挂起 →
   整个事件循环被卡死 → `_command_loop` 无法消费队列里的 stop/interrupt/talk 指令。
4. **佐证**：静音聆听时连续多轮 `listen` 之间没有任何挂起点（await 一个不会
   suspend 的协程不让出循环），因此循环被"无限期"占用，与"完全无响应"的现象吻合；
   而 `voice.start` 之所以能成功，是因为它发生在语音任务启动之前、循环还空闲时。

## 根因分析

- **直接根因**：阻塞音频 I/O（pyaudio C 扩展录音 / TTS 阻塞播放）运行在宿主 asyncio
  事件循环线程上，饿死同循环的引擎指令消费循环（serve 宿主）；桌面壳指令入队后
  永远等不到消费，表现为按钮无响应。
- **间接根因（语义缺陷）**：stt 停止标志（`stt._request_stop()`）原先一律映射为
  `KeyboardInterrupt` 退出语音模式，无法区分「退出意图」（Ctrl+C / `voice.stop`）与
  「打断意图」（`voice.interrupt` 只想中断当前录音继续聆听）；且待机阶段一旦标志
  置位就直接退出会话，打断按钮会误杀会话。
- **涉及模块**：`agent/voice/voice_loop.py`（录音/播放阻塞调用 + 意图判定）、
  `agent/ui/workbench/engine.py`（指令消费循环与语音任务同循环，无需改动但为现象载体）。

## 修复方案

1. **阻塞音频 I/O 离事件循环**（`voice_loop.py`）：
   - `_voice_loop_round` 的 `stt.listen(...)`、`stream_player.start()`、
     `stream_player.finish()` 全部改 `await asyncio.to_thread(...)` 工作线程执行；
   - `_standby_round` 的待机短录同样改 `asyncio.to_thread`；
   - 宿主事件循环在聆听/思考/播报全程保持空闲，指令循环可随时消费
     `voice.stop / voice.interrupt / talk.start` 等指令。
2. **停止 vs 打断意图区分**（`voice_loop.py`）：
   - `_voice_loop_round` 新增 `stop_event` 参数；录音被停止标志中断后：
     `stop_event` 已置位（Ctrl+C / `voice.stop`）→ 抛 `KeyboardInterrupt` 干净退出；
     未置位（`voice.interrupt`）→ 返回 True，由 `voice_loop` 清标志并继续聆听；
   - `voice_loop` 在 `stop_event is None` 时内部自建（REPL 宿主），Ctrl+C 信号处理器
     `_on_sigint` 同时置位它，保证 REPL 退出语义不变；
   - 待机阶段停止标志不再退出会话：清标志继续待机；退出只认 `stop_event` / Ctrl+C；
   - 轮询线程 `_poll_interrupt` 同时监听 `stop_event`：`voice.stop` 在播报中到达时
     立即停 TTS，让工作线程里的 `finish()` 提前返回，避免引擎 `wait_for` 3s 超时
     硬取消任务残留音频。
3. **引擎 / 前端 / 协议层零改动**：`_handle_stop_voice` 原有的
   「置 stop_event + `_request_stop()` + wait_for 干净退出」设计在循环不饿死后即生效。

## 验证结果

- 新增回归测试 `tests/voice/test_voice_loop_responsiveness.py`（4 用例）：
  - 录音阻塞 0.3s 期间心跳协程 tick ≥ 2（修复前同步 listen 时为 0，直接复现饿死）；
  - 停止标志 + stop_event → 抛 KeyboardInterrupt（退出意图）；
  - 仅停止标志 → 返回 True 继续聆听（打断意图，不误杀）；
  - voice_loop 全链路：聆听阻塞期间置 stop_event + 停止标志，3s 内干净退出且
    循环不饿死（末态 `exited`）。
- `pytest tests/voice` → 46 passed；`pytest tests/serve tests/ui` → 86 passed；
  全量 `pytest -q` → 1715 passed（1 个既有无关 warning）。
- 人工复测（待确认）：桌面壳语音模式下点「退出语音 / 文本 / 实时 / 打断」应即时生效。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `jarvis/agent/voice/voice_loop.py` | 阻塞音频 I/O 改 `asyncio.to_thread`；`_voice_loop_round` 增 `stop_event` 参数并区分退出/打断意图；待机阶段标志不再误杀会话；轮询线程兼听 `stop_event` |
| `jarvis/tests/voice/test_voice_loop_responsiveness.py` | 新增：事件循环饿死 + 意图区分 + 全链路停止回归测试 |
| `jarvis/docs/architecture/06-语音系统.md` | 单轮流程代码、关键设计、打断机制同步线程模型与意图语义 |
| `jarvis/docs/architecture/07-UI层.md` | 半双工语音接线 bullet 补充线程模型与修复指引 |

## 经验总结

- **async 函数里调用同步阻塞 I/O（尤其 pyaudio 这类 C 扩展）= 饿死整个事件循环**：
  同循环上的其他任务（指令消费、WS 收发）会全部停摆，且现象是"静默无响应"而非报错，
  排查时优先检查"阻塞调用是否在事件循环线程上"。
- **await 一个不会 suspend 的协程不让出循环**：连续多轮"阻塞→立即返回→再阻塞"
  可以无限期占用循环，不能指望轮次之间的 await 自然让出。
- **一个停止标志承载多种意图时要显式区分**：本例用「stop_event + stt 停止标志」
  组合区分退出与打断，避免打断按钮误杀会话；信号处理器负责把 Ctrl+C 映射到退出意图。
- **回归测试直接断言"循环是否空闲"**：用心跳协程 tick 数做饿死断言，比断言业务结果
  更贴近根因，修复前必败、修复后必过。
