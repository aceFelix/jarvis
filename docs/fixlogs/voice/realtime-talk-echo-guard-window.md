# /talk 外放回声自打断的「智能折中」方案复盘

- 日期：2026-09-28
- 作者：aceFelix
- 严重级别：中高（外放场景下多轮对话不稳定）
- 关联修复：[realtime-talk-echo-self-cancel-fix.md](realtime-talk-echo-self-cancel-fix.md)（本次在其基础上进一步处理外放回声）

> ⚠️ **后续演进（同日）**：本方案的“深衰减 + 回声保护”仍未能彻底解决免提外放的
> 多轮问题——只要 AI 说话期间持续上传麦克风，`smart_turn` 就会把回声当成“用户仍在
> 说话”，导致 AI 说完后不开下一轮。最终解法是改为**半双工（默认）**：AI 说话期间不
> 上传麦克风。本文的回声保护与衰减逻辑仅在用户显式设 `half_duplex = false`（全双工/
> 耳机）时生效。详见 [realtime-talk-half-duplex.md](realtime-talk-half-duplex.md)。

## 问题现象

上一轮修复（恢复 0.25 衰减 + 自打断可见性）后，用户实机 `/talk` 日志显示：

```text
🧑 你: 贾维斯在吗？
🤖 贾维斯: 在呢，先生。有什么吩咐？        ← 第一轮成功回复
🔇 未入对话轮（服务端判为非有效语音）: 街上。  ← 回声/背景碎片被语义过滤

🧑 你: 现在几点了？
⚠️ 回复被打断取消（…多为回声残留…）          ← 第 2 轮被回声取消
🧑 你: 说话呀。
⚠️ 回复被打断取消（…）                       ← 第 3 轮同样被取消
📋 …4 段进入对话轮，对话链路本身正常
```

诊断系统准确定位了问题：**第一轮能回复，AI 一旦开口，后续轮次被自己的外放回声打断取消**。

## 根因与约束

- 用户环境为笔记本**免提外放**，扬声器与麦克风距离近、无硬件回声对齐。
- WebRTC AEC3 + 0.25 衰减仍挡不住 AI 长回复时的大音量回声：残余能量足以触发
  服务端 VAD 的 `speech_started`，客户端据 `_response_active=True` 发出 `response.cancel`，
  本轮回复被取消。
- **根本矛盾**：「外放连续对话」与「随口打断」互斥。回声和真实插话都会以
  `speech_started` 形式到达，纯外放、纯软件下无法 100% 区分。

经与用户确认，选择**智能折中**方向：优先保证外放能连续多轮，接受"插话需说得更响"的代价。

## 方案

### 1. 加深说话期麦克风衰减：0.25 → 0.1

`ECHO_SUPPRESS_FACTOR = 0.1`。AI 说话期间送出更少的回声能量，降低触发服务端 VAD 的概率。
代价：用户真实插话需更响。

### 2. 回声保护窗口（核心）

用"衰减后的实时麦克风电平"区分回声与真实插话：

- `_send_audio` 在 AI 说话期间逐帧记录衰减后 RMS 到 `self._last_mic_rms`
  （只在说话期计算，避免非说话期逐帧开销）。
- `speech_started` 到达时，若 **AI 正在说话**（`_ai_speaking`）**且** `_last_mic_rms`
  低于 `ECHO_GUARD_RMS`（0.012 ≈ -38dBFS），判定为回声误触发，**忽略本次打断**：
  不发 `response.cancel`、不清扬声器、不复位 `_ai_speaking`，仅计入 `echo_guard_hits`。

判据自洽性：送出去给服务端的是衰减后的信号，服务端 VAD 也基于它判定。若衰减后能量
很低却仍触发了 `speech_started`，即低能量回声被 VAD 误判，忽略之；真实插话要能打断，
需说够响使衰减后电平超过门限。

### 3. 保护只作用于「AI 正在说话时」

AI 已说完后的新一轮 `speech_started`（`_ai_speaking` 已为 False）不受保护影响，正常进入
对话轮。因此保护不会误伤正常的"等 AI 说完再接话"。

### 4. 可观测性补充

`TalkEventLog` 新增 `echo_guard_hits` 计数与 `note_echo_guard()`；`report_lines()` 重构为
可叠加输出三类信号（自打断 / 语义过滤 / 回声保护工作状态）。退出时若有回声被拦下：

```text
🛡️ 已自动忽略 N 次回声触发的误打断（外放保护生效）
```

## 验证

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
.\.venv\Scripts\python.exe -m pytest tests/voice/ -q   # 168 passed
.\.venv\Scripts\python.exe -m pytest -q                # 2109 passed
```

新增 `TestEchoGuard`（`tests/voice/test_realtime_response_lifecycle.py`）：

| 用例 | 断言 |
|---|---|
| `test_low_level_speech_started_treated_as_echo` | AI 说话 + 低电平 → 忽略打断，`echo_guard_hits=1` |
| `test_loud_interrupt_still_cancels` | AI 说话 + 高电平 → 仍发 `response.cancel`，打断能力保留 |
| `test_guard_not_applied_when_ai_idle` | AI 未说话 → 不触发保护，新一轮语音不受影响 |
| `test_report_shows_echo_guard` | 退出摘要提示被拦下的回声次数 |

另更新既有 `test_speech_started_records_active_response`：显式设高电平模拟真实插话，
以免被新增的回声保护拦下（该用例验证的是真实打断路径）。

## 经验总结

1. **纯软件 AEC 对免提外放有物理上限，不要指望"消除"，要靠"抑制 + 判定"组合。**
   消除不彻底时，退一步在客户端对"打断"这个动作加电平判据，比继续调 AEC 参数更有效。
2. **有 tradeoff 的修复应先与用户对齐方向再动手。** 本例三个方向（外放稳定优先 /
   打断灵敏优先 / 智能折中）互斥，选哪个取决于用户更在意什么，代码层面无法替他决定。
3. **阈值类改动必须留下可观测信号。** `echo_guard_hits` 让"保护在工作"和"保护过头
   导致打不断"这两种情况在退出摘要里一眼可辨，便于后续按实测调 `ECHO_GUARD_RMS`。
4. **参数是初值，不是终值。** 0.1 与 0.012 是基于外放场景的合理起点，需实机回音反馈迭代。

## 已知限制

- 外放音量大、AI 回复长时，仍可能有强回声超过门限触发误打断——进一步表现为需要
  用户说得更响。彻底解决需硬件 AEC 或佩戴耳机。
- 阈值 `ECHO_GUARD_RMS` 目前为常量，未做成配置项；若实测需要按环境调整，再提为
  `[realtime_talk]` 配置。

## 变更文件

| 文件 | 变更 |
|---|---|
| `agent/voice/realtime_talk.py` | `ECHO_SUPPRESS_FACTOR` 0.25→0.1；新增 `ECHO_GUARD_RMS`、`_last_mic_rms`；`_send_audio` 记录衰减后电平；`speech_started` 回声保护分支 |
| `agent/voice/realtime_events.py` | `echo_guard_hits` 计数、`note_echo_guard()`；`report_lines()` 重构为可叠加三类信号 |
| `tests/voice/test_realtime_response_lifecycle.py` | 新增 `TestEchoGuard`（4 项） |
| `tests/voice/test_realtime_observability.py` | `test_speech_started_records_active_response` 设高电平避开回声保护 |
| `docs/architecture/06-语音系统.md` | 补「回声保护窗口」小节；衰减系数 0.25→0.1 |
| `docs/architecture/12-配置系统.md` / `config-docs/voice-setup.md` / 两份 `settings.example.toml` | 系数与回声保护说明同步 |
