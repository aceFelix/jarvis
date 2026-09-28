# /talk 回声自打断导致「AI 完全不回复」复盘

- 日期：2026-09-28
- 作者：aceFelix
- 严重级别：高（实时语音对话功能整体不可用）
- 关联修复：[realtime-talk-response-done-fix.md](realtime-talk-response-done-fix.md)（前一次修复引入本问题）

## 问题现象

继上一轮修复 `response.done` 缺失（表现为「只能回答一句话」）后，用户实机反馈
故障加重——**一句都不回复**：

```text
🎙️  实时双工语音对话已开启
   模型: qwen-audio-3.0-realtime-flash  ·  音色: longanqian
   AEC 回声消除已启用 · smart_turn · 说话打断 · ESC 退出
MCP: 7/7 server 已连接，注册 87 个工具
🛠️ MCP 外部工具已就绪（+ 87 个），已加入对话

🧑 你: 贾维思在吗？

🧑 你: 啥回事？

🧑 你: 乔维斯在吗？乔维斯。

已退出实时语音对话
```

关键特征：

1. 三句用户语音都成功转写（`🧑 你:` 通道正常，麦克风与服务端 ASR 都工作）
2. **没有任何一条 AI 回复**，连半句 `🔊 贾维斯:` 都没有
3. 没有出现 `🔇 未入对话轮`，排除 smart_turn 语义过滤

「连半句都没有」与上一轮「说完第一句后卡住」是不同的故障形态——说明响应不是
被本地状态卡住，而是**在服务端就被取消了**。

## 根因

上一轮修复中，我做了三处改动，其中第三处是错误判断：

| 改动 | 判断 | 实际 |
|---|---|---|
| 监听 `response.done` 复位状态 | 正确 | 生效，状态不再卡死 |
| 打断判定改用服务端事实 `_response_active` | 正确 | 生效，不再误发取消 |
| **AEC 生效时取消 0.25 麦克风衰减** | **错误** | 回声回弹，打断每一轮 |

取消衰减的理由是「AEC 已在物理层消除回声，再衰减只会压小用户插话」。但实测
（见 `agent/voice/aec.py` 注释）WebRTC AEC3 在免提外放 + 非理想延迟对齐下
**只能消除部分回声**，约 15% 以上残留需要再降约 6 dB 才不触发服务端 VAD。

完整故障链：

```text
用户说话
  ↓
服务端 response.created           → _response_active = True   （上一轮修复生效）
  ↓
AI 开始生成回复，扬声器出声
  ↓
残余回声被麦克风拾取（AEC 未消尽 + 我已取消二次压低）
  ↓
服务端识别为「用户又说话了」→ input_audio_buffer.speech_started
  ↓
_response_active == True → 客户端发 response.cancel   ← 这次是真该发，因为确实有活动响应
  ↓
本轮回复被取消，AI 一个字也没播出来
  ↓
新响应创建 → 又被回声取消 → 无限循环
```

**为什么上一轮反而能回复第一句**：那时 0.25 衰减仍在生效，替 AEC 挡住了回声，
第一句得以完成；后续轮次则是被「状态卡死 → 误发 cancel」卡住（旧 bug）。
两个 bug 恰好部分抵消，掩盖了 AEC 消除不彻底这一事实。修好旧 bug 后，回声问题
以更强烈的形态暴露出来。

这也是我在上一轮交付时明确预警过的唯一风险点，它以最坏的方式发生了。

## 改动

### 1. 恢复二次压低为默认行为，改为配置可控

`agent/voice/realtime_talk.py`：

```python
def _should_attenuate(self) -> bool:
    if not self._ai_speaking:
        return False
    if self._aec is not None and not self._echo_suppress_with_aec:
        return False
    return True
```

语义从「AEC 生效即不压低」改为「**默认始终压低**，仅当用户显式关闭
`echo_suppress_with_aec` 且 AEC 确实可用时才不压低」。

与上一版的关键区别：压低只在 AI 真正说话期间生效——`response.done` 现在能
及时复位 `_ai_speaking`，不再像早期版本那样永久压小用户语音。用户主诉的
「我的声音太小」由状态卡死引起，已由前一次修复解决，无需靠取消衰减来治。

### 2. 新增配置项

`agent/config/settings.py`：

```python
realtime_echo_suppress_with_aec: bool = True
```

对应 TOML `[realtime_talk] echo_suppress_with_aec`，两个调用方
（`commands/handlers/voice_commands.py`、`ui/workbench/engine.py`）均已透传。
默认 `true`；仅佩戴耳机场景建议关闭。

### 3. 把「自打断」变成可见信号

此前这条故障链在屏幕上完全不可见，只能靠猜。现在：

- `response.done` 带 `status=cancelled` → 提示「⚠️ 回复被打断取消」并计数
  `cancelled_responses`（`status` 兼容嵌套 `response.status` 与顶层两种结构）
- 退出时若 `ai_turns == 0` 且 `cancelled_responses > 0`，输出诊断摘要：

```text
 本次 N 次回复被取消，AI 未能完成任何一条回复
   典型成因：扬声器回声被麦克风重新拾取→服务端当成您在说话→打断并取消回复
   建议：改用耳机，或确认 [realtime_talk] echo_suppress_with_aec = true
   事件时间线: ~/.jarvis/logs/diag.log
```

`ai_turns > 0` 时保持安静——偶发打断属正常交互，不该打扰用户。

## 验证

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
.\.venv\Scripts\python.exe -m pytest tests/voice/ -q   # 164 passed
.\.venv\Scripts\python.exe -m pytest -q                # 2105 passed
```

新增/调整回归用例（`tests/voice/test_realtime_response_lifecycle.py`）：

| 用例 | 断言 |
|---|---|
| `test_attenuate_when_aec_active` | AEC 生效时默认仍压低（防止本问题回潮） |
| `test_no_attenuate_when_opted_out` | 显式关闭 + AEC 可用 → 不压低 |
| `test_attenuate_when_opted_out_without_aec` | 关了开关但 AEC 不可用 → 仍必须压低 |
| `test_no_attenuate_when_idle` | AI 未说话 → 不压低，用户语音增益正常 |
| `test_cancelled_response_notifies_and_counts` | cancelled 提示 + 计数 + 状态复位 |
| `test_completed_response_stays_silent` | 正常完成不打扰 |
| `TestSelfInterruptReport`（3 项） | 诊断摘要的触发与静默边界 |

### 顺带完成的结构整改

`tests/voice/test_realtime_observability.py` 增删后达到 813 行，超出 800 行上限，
按代码结构规范先行拆分：

- `tests/voice/_fakes.py`：共享替身 `FakeDiag` / `FakeWS` / `FakeTalkUI`
  （沿用 `tests/daemon/_fakes.py` 的既有惯例）
- `tests/voice/conftest.py`：共享 fixture `fake_diag` / `_stub_tools`
- `tests/voice/test_realtime_response_lifecycle.py`：响应生命周期、自打断诊断、
  回声门控三组用例（234 行）
- 原可观测性测试文件回落至 501 行

## 经验总结

1. **兜底机制在被主机制「看起来够用」时容易被误删。** AEC 与 0.25 衰减不是互斥
   关系，而是「主方案 + 补偿」关系。判断能否取消兜底，依据应是实测残留电平，
   而不是「主方案已启用」这一状态。
2. **两个 bug 互相掩盖时，修一个会让另一个变严重。** 交付修复时不能只看「目标
   现象是否消失」，还要看改动是否让原本被压住的问题失去约束——本次三个改动里
   两个正确、一个错误，错误的正好是解除约束的那个。
3. **故障链必须在界面上留痕。** 「AI 不回复」的三种成因（语义过滤 / 状态卡死 /
   回声自打断）此前表现完全一致，只能靠读代码区分。现在 `cancelled` 有实时提示、
   退出有诊断摘要、`event_log` 有时间线，定性成本从「读几百行代码」降到「看一眼屏幕」。
4. **预警过的风险要优先复验。** 上一轮交付时我已写下「若 AEC 消除不彻底，回声可能
   被重新识别为语音」，当时把它作为可接受的风险交付。更稳妥的做法是把该风险做成
   配置项（默认保守）而非硬编码开关。

## 变更文件

| 文件 | 变更 |
|---|---|
| `agent/voice/realtime_talk.py` | `_should_attenuate()` 恢复默认压低；`echo_suppress_with_aec` 参数；`response.done` 读取 `status` 并提示/计数 cancelled |
| `agent/voice/realtime_events.py` | `cancelled_responses` 计数、`note_cancelled()`；`report_lines()` 增加自打断诊断分支 |
| `agent/config/settings.py` | 新增 `realtime_echo_suppress_with_aec`（默认 true）及 TOML 映射 |
| `configs/settings.example.toml` / `agent/configs/settings.example.toml` | 新增 `echo_suppress_with_aec` 示例与说明注释 |
| `config-docs/voice-setup.md` | 新增「装了 AEC 为什么还要压低麦克风」小节 |
| `docs/architecture/12-配置系统.md` | `[realtime_talk]` 映射表与字段说明 |
| `tests/voice/test_talk_terminal.py` | 补 `echo_suppress_with_aec` 透传断言（默认开 / 显式关） |
| `agent/commands/handlers/voice_commands.py` | 透传 `echo_suppress_with_aec` |
| `agent/ui/workbench/engine.py` | 透传 `echo_suppress_with_aec` |
| `tests/voice/_fakes.py` | 新建：共享测试替身 |
| `tests/voice/conftest.py` | 新建：共享 fixture |
| `tests/voice/test_realtime_response_lifecycle.py` | 新建：响应生命周期 / 自打断诊断 / 回声门控用例 |
| `tests/voice/test_realtime_observability.py` | 移出上述用例与替身定义，回落至 501 行 |
| `docs/architecture/06-语音系统.md` | 修正「衰减与 AEC 互斥」错误说明，补配置项与自打断可见性 |
| `README.md` / `README.en.md` | `[realtime_talk]` 配置示例补 `echo_suppress_with_aec` |
