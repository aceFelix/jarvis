# 修复：/voice 语音模式 Ctrl+C 退出整个进程、TTS 尾音被 STT 误录、MCP 退出刷屏

> 作者：aceFelix ｜ 日期：2026-10-10 ｜ 影响模块：`agent/voice`、`agent/core/extensions`、`agent/main.py`

## 问题现象

在**终端 REPL** 中使用半双工语音模式（`/voice`）时，人工测试发现三个问题：

1. **Ctrl+C 直接退出整个 jarvis 进程**：期望是「退出语音模式、回到文本聊天提示符」，
   实际却连文本 REPL 一起杀掉，进程直接结束。
2. **贾维斯还在播报，STT 就开始录音**：它话没说完，下一轮聆听已经启动，把贾维斯
   自己回复的尾音当成「用户输入」转写（日志里出现莫名的半截句子，实为本轮回复尾音）。
3. **退出语音时 MCP 报错刷屏**：退回文本模式瞬间，终端打印一大段
   `an error occurred during closing of asynchronous generator ... cancel scope ...`
   的 asyncio 报错（来自 MCP `stdio_client`），干扰正常阅读。

复现路径：`jarvis` → `/voice` →（1）按 Ctrl+C；（2）让贾维斯读一段较长回复；
（3）说完「退下」或按 Ctrl+C 观察退出刷屏。

## 排查过程

### 阶段一：误判为 asyncgen finalizer 未安装（方向错误）

MCP 刷屏初判为「async generator 在 GC 时关闭失败」，于是在 `mcp_client.py` 安装
自定义 finalizer。第一轮改动有效果但不彻底——用户复测后反馈**刷屏仍在**。

### 阶段二：定位刷屏真正来源（关键转折）

直接阅读 venv 里的 `asyncio` 源码后发现：`asyncio.run` 在退出时会调用
`loop.shutdown_asyncgens()`，逐个 `aclose()` 所有 async generator；关闭失败**并不走**
asyncgen 的 GC finalizer，而是走 `loop.call_exception_handler` 打印。因此无论怎么换
finalizer 都拦不住，**必须在事件循环的异常处理器上过滤**。

顺带发现 `mcp_client.py` 里安装 finalizer 的 API 名写错了：用的是
`sys.getasyncgenhooks()` / `sys.setasyncgenhooks()`（**无下划线，根本不存在**），
抛出的 `AttributeError` 被 `except` 静默吞掉，等于自定义 finalizer 从未装过。

### 阶段三：Ctrl+C 撕裂 asyncio.run

阅读 `voice_loop.py` 后确认：信号处理器 `_on_sigint` 在置位停止标志后又
`raise KeyboardInterrupt`。Windows 下 `signal.signal` 的 handler 在**主线程**执行，
而此刻主线程正阻塞在事件循环的 `select()`，异常不会进入协程的 `except`，而是撕裂
`asyncio.run`，最终被 `main.py` 的 `except KeyboardInterrupt: return 130` 捕获 → 整个进程退出。

### 阶段四：TTS 尾音截断

`tts.py` 的 `_PlaybackCallback.on_close()` 在 WS 关闭时**立即** `stop_stream()`，
而 WS 关闭通常紧跟合成完成、扬声器缓冲里还有未播完的音频 → 尾音被硬截断，
下一轮 STT 随即录到残留音响。此外 `speak()` / `finish()` 用 `done.wait(timeout=30)`
一刀切：长回复（音频 >30s）会在播到一半时提前返回。

## 根因分析

| 现象 | 根因 |
|---|---|
| Ctrl+C 退出整个进程 | 信号处理器内 `raise KeyboardInterrupt` 撕裂 `asyncio.run`，被顶层 `except KeyboardInterrupt` 当作「退出进程」处理 |
| STT 录到贾维斯尾音 | ① `on_close()` 立即 `stop_stream()` 截断扬声器缓冲；② `done.wait(timeout=30)` 对长音频提前返回，物理播放尚未结束 |
| 退出时 MCP 刷屏 | 刷屏来自 `loop.shutdown_asyncgens()` → `call_exception_handler`，而非 GC finalizer；且安装 finalizer 的 API 名写错（无下划线）从未生效 |

涉及模块：`agent/voice/voice_loop.py`、`agent/voice/tts.py`、
`agent/core/extensions/mcp_client.py`、`agent/main.py`。

## 修复方案

1. **Ctrl+C 只置标志、绝不抛异常**（`voice_loop.py`）
   - 抽出 `_build_sigint_handler(stt_module, stop_event)`，handler 内仅执行
     `stt_module._request_stop()` + `stop_event.set()`；
   - `stt.listen()` 每 ~200ms 轮询 `_stop_flag` 自行返回，
     `await asyncio.to_thread` 随之结束；协程检测到 `stop_event` 后**在协程内部**
     抛 `KeyboardInterrupt`，被 `voice_loop` 的 `except KeyboardInterrupt` 正常捕获，
     干净退回文本模式；思考/播报阶段由 `_poll_interrupt` 观察 `stop_event` 触发 barge-in。

2. **TTS 播放排空 + 双上限**（`tts.py`）
   - 新增 `_PlaybackCallback.remaining_play_seconds()`：以首块音频到达为播放起点，
     按累计字节数折算总时长减去已过时间，估算扬声器剩余播放时长；
   - `on_close()` 先按剩余时长排空（`time.sleep(min(rem+0.15, _DRAIN_MAX_SECONDS))`）
     再 `stop_stream()`，被打断时（`_stop_flag`）跳过以保证 barge-in 及时；
   - 新增 `CosyVoiceTTS._wait_for_playback(done, sc_error)` 取代 `done.wait(timeout=30)`：
     停滞判定 `_STALL_SECONDS=15s`（长时间无新音频且无完成信号 → 判 WS 卡死）、
     总量上限 `_SYNTH_TOTAL_CAP_SECONDS=300s` 兜底，正常返回前排空扬声器缓冲。

3. **MCP 静音：修正 API 名 + 循环异常过滤**（`mcp_client.py` + `main.py`）
   - `sys.getasyncgenhooks()/setasyncgenhooks()` → `sys.get_asyncgen_hooks()/set_asyncgen_hooks()`；
     finalizer 内改用 `asyncio.get_running_loop()`（避开 3.14 废弃的 `get_event_loop`），
     task 追加 `add_done_callback` 主动取回并丢弃 `BaseExceptionGroup`，无运行 loop 时
     `coro.close()`；
   - `main.py` 新增 `_install_quiet_loop_exception_handler()`（`repl()` 首行调用），
     在事件循环上 `set_exception_handler`，过滤含
     `closing of asynchronous generator` 及 `cancel scope` 的 context，其余转交
     `default_exception_handler`——**这才是刷屏的真正堵点**。

## 验证结果

- 新增 / 更新回归测试：
  - `tests/voice/test_tts_playback_drain.py`（新增）：`remaining_play_seconds` 折算、
    `on_close` 排空 / 打断跳过、`_wait_for_playback` 排空；
  - `tests/voice/test_voice_loop_responsiveness.py`：新增
    `TestSigintHandler::test_sets_flags_without_raising`（断言 handler 只置标志不抛异常）；
  - `tests/core/test_loop_exception_filter.py`（新增）：验证 MCP 关闭噪音被过滤；
  - `tests/core/test_mcp_client.py`：改为断言使用的是带下划线的正确 API 名
    （避免 import 顺序带来的 finalizer 归属依赖）。
- `pytest tests/core tests/voice` → **287 passed**。
- 人工复测（待确认）：重启 jarvis 后，`/voice` 中 Ctrl+C 应回到文本模式、
  长回复播完才录音、退出时无 MCP 报错刷屏。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `jarvis/agent/voice/voice_loop.py` | 新增 `_build_sigint_handler`，Ctrl+C 只置标志不抛异常 |
| `jarvis/agent/voice/tts.py` | 新增 `remaining_play_seconds` / `_wait_for_playback`，`on_close` 排空尾音，停滞 / 总量双上限 |
| `jarvis/agent/core/extensions/mcp_client.py` | 修正 asyncgen hooks API 名，finalizer 改用 `get_running_loop` 并静默 `BaseExceptionGroup` |
| `jarvis/agent/main.py` | 新增 `_install_quiet_loop_exception_handler`，过滤 MCP 关闭刷屏 |
| `jarvis/tests/voice/test_tts_playback_drain.py` | 新增：播放排空与剩余时长回归测试 |
| `jarvis/tests/voice/test_voice_loop_responsiveness.py` | 新增：SIGINT 处理器只置标志的回归测试 |
| `jarvis/tests/core/test_loop_exception_filter.py` | 新增：事件循环异常过滤回归测试 |
| `jarvis/tests/core/test_mcp_client.py` | 改为断言正确的 asyncgen hooks API 名 |

## 经验总结

- **信号处理器里永远不要抛异常**：`signal.signal` 的 handler 在事件循环阻塞于
  `select()` 时于主线程执行，抛出的异常会撕裂 `asyncio.run` 的收敛路径，被顶层
  当成进程级错误。正确做法是「只置标志」，由协程内部轮询后自行抛异常退出。
- **`asyncio` 退出期的报错要走 `call_exception_handler`**：`shutdown_asyncgens()`
  关闭失败的输出**不经过** asyncgen 的 GC finalizer，改 finalizer 无用，必须在
  `loop.set_exception_handler` 上过滤。
- **API 名写错 + 静默 `except` = 隐形失效**：`sys.setasyncgenhooks`（无下划线）
  根本不存在，被 `except AttributeError` 吞掉，导致「看起来装了 finalizer、
  实际从未生效」。涉及 `sys.*` 的钩子 API 要核对官方签名，异常别静默吞。
- **「合成完成」≠「播放完成」**：`on_complete` 只代表服务端音频发完，物理扬声器
  存在缓冲滞后；流式 TTS 收尾必须按剩余播放时长排空，否则下一轮 STT 必然录到尾音。
- **排查方向被推翻要如实记录**：本轮先误判为 finalizer 问题，用户复测后仍报错才
  转向 `call_exception_handler`——阅读依赖库源码（`asyncio`）比反复试错更快收敛。
