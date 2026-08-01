# Voice Mode 语音模式修复记录

## 问题概述

用户在使用 `/voice` 语音对话模式时，遇到三个渐进式问题：

1. **二次进入语音模式自动待机** — 首次 `/voice` 正常，退出后再次 `/voice`，说任何话都直接进入待机
2. **二次进入语音模式 LLM 无响应** — 修复问题 1 后，二次进入语音不待机了，但 LLM 不回复任何内容
3. **说"退下"不进入待机** — 修复问题 1、2 后，首次语音正常，但说"退下"后不进入待机，继续聆听

---

## 问题一：二次进入语音模式自动待机

### 现象

```
第一次 /voice：
  🧑 你说: 在吗？（3.9s）
  在的，先生。随时待命...
  🧑 你说: 退下吧。（3.2s）
  好的先生，我先退下了，有事随时叫我。\n\n<standby/>
  🛌 贾维斯已退下（说「贾维斯」唤醒）
  💤 待机中，说「贾维斯」唤醒我

退出语音模式

第二次 /voice：
  🧑 你说: 贾维斯，现在几点了？（4.5s）
  🛌 贾维斯已退下（说「贾维斯」唤醒）    ← 直接待机，根本没回复
  💤 待机中，说「贾维斯」唤醒我
```

### 日志证据

```
[03:39:52] 第一次 voice_loop 启动
[03:39:59] LLM reply: text='在的，先生。随时待命...'  thinking=''  stopped_reason=end_turn  iterations=1  tools=0
[03:40:09] LLM reply: text='好的，先生。您慢慢想...'  thinking=''  stopped_reason=end_turn  iterations=1  tools=0
[03:40:19] LLM reply: text='好的先生，我先退下了...<standby/>'  thinking=''  stopped_reason=end_turn  iterations=1  tools=0
[03:40:24] voice_loop 退出
[03:40:30] 第二次 voice_loop 启动
[03:40:34] LLM reply: text='好的，先生。您慢慢想...'  thinking=''  stopped_reason=end_turn  iterations=1  tools=0  ← 注意：这是上次的回复！
```

### 根因

`_detect_standby()` 遍历 `ctx.messages` 找最后一条 `role=assistant` 的消息。第二次进入语音时，`ctx.messages` 中残留了上一次会话的 goodbye 消息（含 `<standby/>`）。由于 LLM 没生成新回复（原因见问题二），最后一条 assistant 消息仍然是旧 goodbye，导致 `_detect_standby` 误判为待机。

### 修复

**修改 1：`_detect_standby` 增加 `since_index` 参数**

```python
# before
def _detect_standby(messages: list) -> bool:

# after
def _detect_standby(messages: list, since_index: int = 0) -> bool:
```

只检查 `since_index` 之后新增的 assistant 消息，旧会话的 `<standby/>` 不再导致误触发。

**修改 2：`_voice_loop_round` 中记录消息数**

```python
msg_count_before = len(ctx.messages)  # loop.run() 前记录
# ...
if _detect_standby(ctx.messages, since_index=msg_count_before):
```

**修改 3：新增 `_clean_standby_messages` 函数**

```python
def _clean_standby_messages(messages: list) -> None:
    """移除最后一条含 <standby/> 的 goodbye 消息及其前一条 user 消息。"""
```

在 `voice_loop()` 的 `finally` 块中调用，退出语音模式时自动清理残留。

---

## 问题二：二次进入语音模式 LLM 无响应

### 现象

修复问题一后，二次进入语音不自动待机了，但 LLM 不回复任何内容：

```
第二次 /voice：
  🧑 你说: 卡维斯在吗？（4.0s）
  🎤 聆听中...（说话即可，停顿后自动结束）  ← 直接回去聆听，没有回复
  🧑 你说: 啥意思？（6.7s）
  🎤 聆听中...（说话即可，停顿后自动结束）
```

### 日志证据

```
[03:40:34] LLM reply: text='好的，先生。您慢慢想，我就在这儿候着。'  stopped_reason=aborted  iterations=0  tools=0
```

关键信息：
- `stopped_reason=aborted` — LLM 被中断
- `iterations=0` — LLM 根本**没被调用**，`loop.run()` 第一行检查 `abort_event.is_set()` 就跳过了
- `text=...` 是上一次会话残留的回复

### 根因

`ctx.abort_event` 是一个 `asyncio.Event()`，在 `voice_loop()` 退出时没有被重置。ESC 监听器（`_KeyBargeInWatcher`）的 `_on_esc_barge` 回调在某个时机设置了 `abort_event`，且 `loop.run()` 内部也会替换 `abort_event` 对象（`ctx.abort_event = asyncio.Event()`），导致新 Event 被设置后残留到下一轮。

### 修复

**双重重置保护：**

1. 在 `_voice_loop_round` 中，`loop.run()` 之前重置：
```python
ctx.abort_event = asyncio.Event()
```

2. 在 `voice_loop()` 的 `except KeyboardInterrupt` 中重置：
```python
except KeyboardInterrupt:
    ui.info("\n退出语音模式")
    ctx.abort_event = asyncio.Event()
```

---

## 问题三：说"退下"不进入待机

### 现象

修复问题一、二后，首次语音会话正常，但说"退下"后不进入待机：

```
🧑 你说: 退下退下懂吗？（4.0s）
懂了懂了，先生。这就退下，您忙您的。有事随时唤我。
🎤 聆听中...（说话即可，停顿后自动结束）  ← 没有进入待机！
```

### 根因

`_detect_standby` 只检查 LLM 回复中是否包含 `<standby/>` 标记。但 LLM 生成 `<standby/>` 是不稳定的（模型行为，DeepSeek-V4-Flash 尤其明显）。当 LLM 回复了告别语但没有写标记时，待机不会被触发。

### 修复

增加 `_exit_detected` 兜底：如果 `_contains_any` 已经检测到用户文本中的退下关键词（"退下"、"不聊了"、"去忙吧"等），则无论 LLM 是否生成 `<standby/>`，都触发待机。

```python
# 记录用户文本是否包含退下意图
_exit_detected = _contains_any(user_text, _EXIT_WORDS)
if _exit_detected:
    ui.info("🛌 退下意图，让 LLM 告别后进入待机...")

# 最终待机检测：LLM 标记 OR 用户关键词
if _detect_standby(ctx.messages, since_index=msg_count_before) or _exit_detected:
    ui.info("🛌 贾维斯已退下（说「贾维斯」唤醒）")
    return False
```

---

## 最终修复总结

### 变更文件

**`e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\agent\voice\voice_loop.py`**

| 行号 | 变更类型 | 说明 |
|------|----------|------|
| L76-L111 | 修改 | `_detect_standby` 增加 `since_index` 参数 |
| L114-L135 | 新增 | `_clean_standby_messages` 函数 |
| L651-L653 | 修改 | `_exit_detected` 变量替代直接 `if` |
| L716-L719 | 新增 | `msg_count_before` 记录 + `abort_event` 重置 |
| L818 | 修改 | `_detect_standby` 增加 `since_index` 参数 |
| L818 | 修改 | 待机检测增加 `or _exit_detected` 兜底 |
| L1084-L1088 | 修改 | `except KeyboardInterrupt` 中重置 `abort_event` |
| L1094-L1098 | 新增 | `finally` 块中调用 `_clean_standby_messages` |

### 三层防护机制

```
┌─────────────────────────────────────────────────────────┐
│ 第一层：abort_event 重置                                  │
│ 确保每轮 LLM 调用前 abort_event 是清除状态，                │
│ 避免 ESC 监听器残留信号导致 LLM 跳过（iterations=0）       │
├─────────────────────────────────────────────────────────┤
│ 第二层：_detect_standby 范围限制 + 消息清理                 │
│ since_index 只检测本轮新增消息，退出时清理 goodbye 残留，    │
│ 避免旧会话的 <standby/> 标记误触待机                      │
├─────────────────────────────────────────────────────────┤
│ 第三层：_exit_detected 关键词兜底                          │
│ LLM 没写 <standby/> 标记时，用户文本中的退下关键词          │
│ 直接触发待机，不依赖模型行为                               │
└─────────────────────────────────────────────────────────┘
```

### 测试流程

```
1. 首次 /voice → 问"在吗？" → 说"退下" → 应进入待机 ✅
2. 说"贾维斯"唤醒 → 再问一个问题 → 应正常回复 ✅
3. Ctrl+C 退出 → 再次 /voice → 问"在吗？" → 应正常回复 ✅
4. 说"退下退下懂吗？" → 应进入待机 ✅
5. 重复步骤 1-4 多次 → 不应出现自动待机或 LLM 无响应 ✅
```

### 相关代码索引

- [`voice_loop.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/voice_loop.py) — 主修复文件
- [`query_loop.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py) — `abort_event` 替换逻辑（L240-L242, L315-L316）
- [`stream_tts.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/voice/stream_tts.py) — TTS 流式播报，与打断逻辑联动