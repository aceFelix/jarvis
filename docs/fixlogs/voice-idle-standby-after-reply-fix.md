# /voice 长回答结束后立刻待机修复复盘

> 模块：语音模式（`/voice`）静默待机
> 发现时间：2026-11-09（用户实机，jarvis 回答约 10 秒后）
> 修复时间：2026-11-09
> 作者：aceFelix

---

## 1. 问题现象

用户开启 `/voice` 后，让 JARVIS 执行 `ls` 并口头汇报目录内容。JARVIS 的思考 + 工具 + 播报总共超过 10 秒，
**话刚说完就立即输出**：

```text
先生，当前目录是 jarvis-work，里面有一共五个东西……
✓ 完成 · 共 1 步 · 文件工具 0 次 · 0 失败 · 4.2s
🛌 10 秒没有听到你说话，进入待机（说唤醒词继续）
💤 待机中，说「贾维斯」唤醒我
```

用户并没有沉默 10 秒——这 10 秒里 JARVIS 自己一直在思考/播报。正确的判定应该是：
**JARVIS 说完之后，才开始给用户计算 10 秒的聆听窗口。**

复现：`/voice` → 问一个需要工具且回答较长的任务（如列目录并播报）→ 等它说完 → 立即待机。

---

## 2. 排查过程

### 阶段 1：确认不是 STT 误判

终端里没有“没听清，再说一次”，而是直接出现“10 秒没有听到你说话”，说明是
`_IdleStandbyTimer` / `_silence_expired` 的静默计时判定触发，不是 STT 识别问题。

### 阶段 2：检查静默计时起点

`_voice_loop_round` 在识别到用户文本时执行：

```python
state["last_speech"] = time.time()
```

之后进入 LLM 思考、工具调用、TTS 播报。整个链路可能远超 10 秒。
下一轮 `_voice_loop_round` 开头的判断：

```python
remaining = idle_seconds - (time.time() - state["last_speech"])
if remaining <= 0: return False  # 进待机
```

于是 LLM + TTS 的耗时被算成了“用户沉默”，话一说完就直接待机。

---

## 3. 根因

静默计时的起点放在“用户说话被识别”的时刻，而不是“JARVIS 说完、重新开始聆听”的时刻。
中间思考/工具/播报阶段并不属于用户聆听窗口，却被计入 10 秒阈值。

---

## 4. 修复方案

`agent/voice/voice_loop.py`：

- 在**每一轮成功/被打断/出错后准备回到聆听**时，重新设置
  `state["last_speech"] = time.time()`：
  - 正常播报结束、退下检测未触发 → 重置后 `return True`；
  - LLM 推理中被打断 → 重置；
  - TTS 播报中被打断 → 重置；
  - LLM 抛 `KeyboardInterrupt` / 普通异常 → 重置；
- 空的识别结果（“没听清”）**不重置**，连续多次空结果仍会累计到 10 秒；
- 失败/静默超时直接待机时也不重置。

效果：用户每次都有完整的 10 秒聆听窗口，从 JARVIS 停止说话那一刻开始算；
连续“没人说话”的空轮次仍然可以累计到阈值。

---

## 5. 验证

- 新增回归测试 `test_silence_timer_restarts_after_reply`：
  - 模拟 LLM 思考 0.18s + TTS 0.05s，静默阈值 0.1s；
  - 修复前结束时会认为已静默 ~0.23s（立刻待机）；
  - 修复后断言 `now - last_speech < 0.1`，证明计时在“说完”时重置。
- `tests/voice/test_voice_idle_standby.py`：6 passed。
- 全量测试：`4670 passed, 2 skipped`。
- 待用户复测：长回答结束后应仍有约 10 秒聆听窗口，不再立刻待机。

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/voice/voice_loop.py` | 每轮回到聆听前重置 `silence_state["last_speech"]` |
| `tests/voice/test_voice_idle_standby.py` | 新增长回答不计入沉默的回归测试 |
| `docs/guide/voice.md` | 说明静默计时从 JARVIS 说完开始 |
| `docs/architecture/06-语音系统.md` | 同上 |

---

## 7. 经验总结

- **任何“用户静默”计时都必须绑定聆听窗口**，不能从用户上一次说话开始算；
  思考、工具、TTS 播报都是系统工作时间，不属于用户沉默。
- 计时状态必须是“窗口开始时间”，而不是“最后活动时间”——后者在系统耗时长的场景
  会直接吞掉整个等待窗口。
- 回归测试要显式模拟“系统耗时 > 阈值”，否则短回复的现有用例永远覆盖不到这个问题。
