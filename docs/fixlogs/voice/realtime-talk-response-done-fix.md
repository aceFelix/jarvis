# 实时双工语音 `/talk`「只能回答一句话」修复记录 —— response.done 缺失导致状态卡死

## 问题现象

用户装好 AEC 后实机使用 `/talk`，**第一轮正常，之后所有轮次全部无响应**：

```
🎙️  实时双工语音对话已开启
   模型: qwen-audio-3.0-realtime-flash  ·  音色: longanqian
   AEC 回声消除已启用 · smart_turn · 说话打断 · ESC 退出
   识别结果：✅ 进入对话轮 ｜ 🔇 未入对话轮（服务端判为非有效语音）
========================================================

🧑 你: 贾维斯在吗？
MCP: 7/7 server 已连接，注册 87 个工具
🛠️ MCP 外部工具已就绪（+ 87 个），已加入对话

🤖 贾维斯: 在的，先生。有什么吩咐？

🧑 你: 现在几点了？          ← 只有转写，无回复
🧑 你: 贾维斯。              ← 只有转写，无回复
🧑 你: 现在几点了？          ← 只有转写，无回复
```

> 用户原话：「为什么 jarvis 的 /talk 语音只能回答我一句话，之后的问题都不回复我，麦克风一直我在说」

**关键观察**：三条后续语音全部出现 `🧑 你:` 转写
（`conversation.item.input_audio_transcription.completed`），**一条 `🔇` 都没有**
→ 语音都进入了有效对话轮、服务端确实听到了并写入了对话上下文，
问题不在语义过滤，而在响应生成环节。

---

## 排查过程

### 第一阶段：用第 1 层可观测性排除"语义过滤"假设

刚交付的环境音转写显示立刻给出结论：`🔇` 一行未出现 → "服务端判为非有效轮次"的
假设不成立。这条零行为风险的可观测性改动第一次实机就完成了它的使命。

### 第二阶段：核对服务端事件契约

对照 `dashscope-docs/阿里云实时语音对话实现文档.md` 与
`dashscope-docs/阿里云FunctionCalling实现.md`：

- 官方协议中**响应结束的权威事件是 `response.done`**：两份文档的完整事件序列
  均以 `response.done` 结尾，FunctionCalling 文档里的处理函数也叫
  `handleResponseDone`
- 文档「打断处理」明确：`response.done` 返回 `status=cancelled` 表示被用户打断
- 全文检索：**`response.audio.done` 并未定义在 DashScope 协议中**

而 `agent/voice/realtime_talk.py` 的 `_recv_events` 只在等 `response.audio.done`。

### 第三阶段：定位连锁故障

`response.audio.done` 永不到达 → `self._ai_speaking` 从第一轮 AI 开口起
**永久为 True**，`self._response_start_ts` 同样永久非 None，引发两个连锁故障：

1. **每轮都误发 `response.cancel`**：`speech_started` 分支用
   `had_active_response = (self._response_start_ts is not None or self._ai_speaking)`
   推断"是否有活动响应"，状态卡死后**恒为 True** → 用户每次开口都向服务端发出取消，
   把正要生成的新轮次响应一并取消掉
2. **麦克风被永久压低**：`_send_audio` 在 `self._ai_speaking` 为真时把音频乘
   `ECHO_SUPPRESS_FACTOR = 0.25`，于是用户从第二轮起一直被以 1/4 音量发给服务端

第 1 轮之所以正常，是因为那一次 `speech_started` 时 `_ai_speaking` 还是 False。

---

## 根因

客户端监听了一个**协议中并不存在的响应结束事件**（`response.audio.done`），
使"AI 正在说话"这一本地状态在 AI 说完后永不复位，进而污染打断判定与麦克风增益。

---

## 改动

### 1. 补齐响应生命周期（权威事件）

| 事件 | 客户端动作 |
|---|---|
| `response.created`（新增接线） | `_response_active = True` |
| `response.done`（新增接线） | `_end_response(ui)` |
| `response.audio.done`（保留兼容） | `_end_response(ui)` |

`_end_response()` 统一复位四处状态，避免分散赋值漏项：

```python
def _end_response(self, ui) -> None:
    self._response_active = False
    self._ai_speaking = False
    self._response_start_ts = None   # 防御性超时计时器
    ui.on_ai_speaking(False)
    ui.on_status("standby")
```

### 2. 打断判定改用服务端事实状态

```python
# 之前：用本地播放态推断，状态一残留就误判
had_active_response = (self._response_start_ts is not None or self._ai_speaking)
# 之后：只看服务端是否真在一次活动响应中
had_active_response = self._response_active
```

新增 `self._response_active`，由 `response.created` / `response.done` 成对维护。

### 3. AEC 启用时取消麦克风二次衰减

```python
def _should_attenuate(self) -> bool:
    """AEC 未启用时兜底抑制残余回声；AEC 生效时不再压低用户语音。"""
    return self._ai_speaking and self._aec is None
```

AEC 已在物理层消除回声，再乘 0.25 只会把用户插话的真实语音一并压小。

---

## 验证

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis; python -m pytest -q
```

- `tests/voice/` **155 passed**（新增 8 项）
- 全量 **2096 passed**
- 新增回归用例（`TestResponseLifecycle` / `TestEchoSuppressGate`）：
  - `response.done` 一次清干净四项状态（活动标志 / 说话态 / 超时计时器 / UI 待机）
  - `response.audio.done` 兼容路径行为一致
  - **复现用户故障**：`response.created → delta → response.done → speech_started`
    全程 `ws.sent == []`（不再误发取消），且新轮次转写照常进入对话上下文
  - AI 回复中插话仍发 `response.cancel`（打断能力未被削弱）
  - AEC 存在时不衰减 / AEC 缺失且 AI 说话时衰减 / 待机时不衰减

---

## 经验总结

### 1. 状态机的"结束事件"必须来自协议权威定义

监听一个不存在的事件不会报错，只会让状态永久卡死——这类 bug 在"第一轮正常、
之后全坏"的形态下最难定位。核对协议时要确认**每个事件名真实存在**，而不是
"看起来合理"。

### 2. 本地推断状态不能用作协议判定依据

"AI 是否正在说话"有两个视图：本地播放态（`_ai_speaking`）与服务端事实
（`_response_active`）。打断这类**会向服务端发指令**的判定必须用后者，
否则本地状态一残留就会持续发出错误指令。

### 3. 一次状态残留在多个分支产生连锁放大

同一个 `_ai_speaking` 卡死同时污染了打断判定和麦克风增益，两个症状看起来
毫不相关。复位逻辑应集中到单一方法（`_end_response`），避免分散复位漏项。

### 4. 兜底抑制必须与主方案互斥

AEC 是回声消除主方案，0.25 衰减是兜底。两者叠加时兜底反而伤害真实信号，
因此兜底必须带"主方案未生效"的条件。

> ⚠️ **本条结论已于同日被推翻**：实测 AEC3 只能消除部分回声，去掉衰减后
> 残留回声会打断并取消 AI 的每一轮回复。现行行为与原因见
> [realtime-talk-echo-self-cancel-fix.md](realtime-talk-echo-self-cancel-fix.md)。

### 固化为规则

- 监听服务端事件前**必须**在官方文档中确认事件名存在，不得凭命名习惯推断
- 涉及"向服务端发指令"的判定**必须**基于服务端事实状态，而非本地推断状态
- 会话状态复位**必须**集中在单一方法中，禁止在多处零散赋值
- 兜底抑制逻辑**必须**带主方案失效条件，避免叠加伤害真实信号

> ⚠️ 本条规则的适用前提不成立：判断能否取消兜底，依据应是**实测残留电平**，
> 而非“主方案已启用”这一状态。详见同日后续复盘。

---

## 变更文件

| 文件 | 说明 |
|---|---|
| `agent/voice/realtime_talk.py` | 新增 `_end_response()` / `_response_active` / `_should_attenuate()`，接线 `response.done` 与 `response.created`（712 → 748 行） |
| `tests/voice/test_realtime_observability.py` | 新增 `TestResponseLifecycle` / `TestEchoSuppressGate` 共 8 项；`_FakeWS` 支持记录出站消息 |
| `docs/architecture/06-语音系统.md` | `_recv_events` 样例补响应生命周期；新增「响应生命周期与打断判定」小节 |

---

## 后续更正（同日）

本次修复的**两处生命周期改动（`response.done` 复位、打断判定改用
`_response_active`）仍然有效**；但第三处改动——「AEC 生效时取消 0.25 麦克风
衰减」——是错误判断，已回退为默认压低（新增 `echo_suppress_with_aec` 配置项）。

取消衰减后故障从“只回答一句话”加重为“一句也不回答”：残余回声被服务端
识别为用户语音 → 触发 `speech_started` → 本轮回复被 `response.cancel` 取消。
完整复盘见 [realtime-talk-echo-self-cancel-fix.md](realtime-talk-echo-self-cancel-fix.md)。

另：本次新增的两个测试类已拆分到
`tests/voice/test_realtime_response_lifecycle.py`（原文件超 800 行上限）。
