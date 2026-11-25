# 实时语音「真打断」最终方案（2026-11-25）

> 目标：打电话体验——用户开口后 ≤300ms 扬声器静音，当前轮次作废，下一句直接进新轮次。
> 状态：**队列方案已回退**（实机证明会让播放断续/混乱），现为「直写播放 + 停流再启
> + 分片写」，待人工实测验收。

---

## 1. 两次失败的尝试（必须记住）

### 1.1 为什么不能用「应用层播放队列」

2026-11-25 第一版引入了 `PlaybackBuffer`（40ms 拆帧、0.4s 有界、预缓冲 120ms），
目标是把"接收"和"写扬声器"解耦，打断时整队丢弃。实机结果相反：

- **声音断断续续**：`diag.log` 出现 `playback: underrun #1/#20/#100`，writer 频繁
  在空队列上等待；预缓冲只能缓解、不能消除服务端供帧节奏与线程调度的抖动。
- **每次只播开头**：本地 VAD 在 0.5 的异常电平下触发（见下），把刚开播的响应取消。
- **混乱播放**：日志在 10ms 内出现两次 `local barge fired` 与两次 `interrupt epoch=1`，
  证明有两个引擎同时开麦/放音；AEC 参考对不上对方的扬声器声音，回声更消不掉。

结论：原播放路径（接收协程直接写扬声器）虽然"打断慢"，但**连续、稳定**。
队列改变了正常播放时序，收益不抵风险，整条回退。

### 1.2 本地 VAD 为什么会误触发

`local vad sample level=0.500`（正好 0.5）不是正常语音电平，而是另一路扬声器
声音被当前麦克风拾到、AEC 参考不匹配的结果。再加上单帧触发与 0.03 的过低门槛，
就变成"AI 自己把自己打断"。

修复：门槛 0.08（-22dBFS）、首帧出声后 500ms 内不判、连续 2 帧超阈才触发；
AEC 参考信号回到 recv **入队时**喂（与队列方案前一致）。

### 1.3 双会话互斥

`voice_state` 锁原先只保护 `/voice`；现在 `/talk` 启动也 `acquire_voice_lock()`，
两个 `/talk` 或 `/talk` 与 `/voice` 同时启动会被明确拒绝，不再出现两路互相打断。

### 1.4 close() 输出流会 segfault

2026-11-25 第二版用 `_TerminalSpeaker.abort()`：`stop + close` 后 `open` 新流，
想“真正丢弃” PortAudio 缓冲。实机一次打断后直接 `Segmentation fault`。
根因：PyAudio/PortAudio 在 Windows 非线程安全，`close()/open()` 与另一线程的
麦克风 `read()` 并发时直接崩（无 Python 异常）。

最终约束：**输出流只允许 stop/start，绝不 close**；残余缓冲靠“分片写 + 写前
代数检查”压到 40ms 以内。

### 1.5 模型把“停一下”当“退下”

2026-11-25 实机：用户说“停一下”没被转写进会话，模型却主动调 `end_conversation`，
直接退出语音。不能只靠提示词约束，工具层加硬门：`end_conversation` 仅在最近
一轮用户转写含“退下/结束对话/再见/拜拜/没事了…”时才执行；否则返回“未结束对话”，
让模型继续对话。

## 2. 最终架构

```text
WebSocket recv ──audio.delta──► 分片(40ms)直写 spk.write
       │
       └─ speech_started / 本地 VAD ──► abort_playback()
                                          │
                                          ├─ _TerminalSpeaker.abort(): stop + start
                                          │    （不 close，避免与录音线程并发崩）
                                          └─ response.cancel（仅响应进行中）
```

### 2.1 终端输出：`_TerminalSpeaker`（`realtime_talk.py`）

- 正常路径：`write()` 直接写 PyAudio，**不排队、不预缓冲**——恢复原来的连续播放。
- `abort()`：只 `stop_stream() + start_stream()`，**绝不 close/open**（见 1.4）。
  代价是残余一小段设备缓冲；靠“接收端分片写（≤40ms/片）+ 每片前检查代数”
  把打断后最多多播限制在一片。
- 会话收尾才 `close()`（此时录音已停，无并发）。

### 2.2 打断触发

| 触发源 | 动作 |
|---|---|
| 服务端 `input_audio_buffer.speech_started` | `_abort_playback()` + 递增代数；服务端自行取消回答 |
| 本地 VAD（仅 AEC 可用、非半双工） | 同上；若 `_response_active` 再补发 `response.cancel` |
| 用户 ESC / 退出 | 会话收尾，`_TerminalSpeaker.close()` |

本地 VAD 参数：

- 门槛 `LOCAL_BARGE_RMS = 0.04`（-28dBFS）：0.08 实机收不到“停一下”
  （AEC 后电平约 0.05~0.07）；防误触发靠收敛窗 + 双帧，不靠高门槛；
- 首帧出声后 `LOCAL_BARGE_WARMUP_SECONDS = 0.5` 内不判（AEC 收敛窗）；
- 连续 2 帧（~200ms）超阈才算插话，单帧尖噪/回声跳变不算；
- 冷却 1s，避免连续帧重复 cancel；
- `local vad sample` 每 2s 一条（**无论电平高低**），用于判断 VAD 是否根本
  没收到人声。

### 2.3 播放期防"先写一帧再丢弃"

`audio.delta` 按 40ms 分片写，**每片写设备前**检查打断代数（`gen_at_decode`）：
打断后到达的帧、以及当前帧剩余分片全部丢弃；不再出现"打断后还先播一帧"。
整个循环仍在接收协程里顺序执行，不排队、不换线程，保持连续播放。

**音频闸门（2026-11-25 补）**：打断时置 `_discard_audio=True`，此后所有
`audio.delta` **一律不播**，直到下一轮 `response.created` 才解闸。
原因：服务端 TTS 会提前发送后续帧，这些帧在打断后到达时携带的是**新代数**，
单靠 gen 检查拦不住——这正是“叫他停，他还是把整段说完”的原因。

## 3. 保留 / 回退清单

| 项 | 结果 |
|---|---|
| `PlaybackBuffer`（有界队列/预缓冲/独立 writer） | **已删除**（文件与单测一并移除） |
| 本地 VAD | 保留（门槛/收敛窗/双帧门/日志） |
| AEC `feed_reference` | 回退到 recv 入队时喂 |
| `EchoCanceller` 互斥锁 | 保留（`process_mic` 与参考信号可能分线程） |
| `client_cancelled` 友好提示 | 保留 |
| 取消事件链 20s 节流 | 保留 |
| 打断后音频闸门（丢弃至下一轮） | 新增 |
| `/talk` 麦克风互斥锁 | 新增 |
| `end_conversation` 退出意图门 | 新增 |

## 4. 验收标准（人工实测）

1. 让 Jarvis 讲 ≥10 秒内容：**播放必须连续**，无卡顿、无中断（恢复改造前水平）；
2. 中途说「停一下」：≤300ms 静音，出现「⏹ 已按你的插话停止本轮播报」；
3. 连续打断 3 次都能立即停，且会话不退出；
4. 两个终端同时 `/talk`：第二个被拒，提示麦克风被占用；
5. `pytest tests/voice` 全绿。

## 5. 排障

`~/.jarvis/logs/diag.log`（不用开 `event_log`）：

- `local vad sample level=...`：每 2s 一条，判断回声是否越过 0.08；
- `local barge fired level=...`：本地判定你插话；
- `取消诊断 事件链`：`response.done[cancelled] ← response.created` 且无
  `speech_started` = 客户端主动 cancel；带 `speech_started` = 服务端判定打断。
