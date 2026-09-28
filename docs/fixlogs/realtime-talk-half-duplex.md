# /talk 外放「AI 说完不接话」的半双工方案复盘

- 日期：2026-09-28
- 作者：aceFelix
- 严重级别：高（免提外放下多轮对话基本不可用）
- 关联修复：[realtime-talk-echo-self-cancel-fix.md](realtime-talk-echo-self-cancel-fix.md)、
  [realtime-talk-echo-guard-window.md](realtime-talk-echo-guard-window.md)（本次是其最终解法）

## 问题现象

前两轮修复（恢复麦克风衰减 + 回声保护窗口）后，用户实机 `/talk` 仍失败，并给出了
决定性的观察：

```text
🧑 你: 贾维斯在吗？
🤖 贾维斯: 在呢，先生。有什么吩咐？      ← 第一轮正常

🧑 你: 啊现在几点了？
⚠️ 回复被打断取消
🧑 你: 现在几点啦。
（无任何回复，麦克风持续显示在检测用户语音）
```

用户原话：**「首次聊天是好的，从第二轮开始，我说话后 jarvis 就一直不回答，我话已经
说完了，麦克风依然一直在检测我的声音……只要麦克风一直处于检测我声音的状态，jarvis
就没法开口。」**

## 根因

这个观察比"回声取消回复"更本质。`/talk` 用 DashScope `smart_turn` 做轮次判定：服务端
**持续接收客户端上传的麦克风音频**来判断"用户是否还在说话"。免提外放时：

1. AI 开口 → 扬声器声音 + 房间混响持续进入麦克风；
2. 这些回声被一路上传，`smart_turn` 判定「用户语音仍在继续」，迟迟不发 `speech_stopped`；
3. 没有"用户说完"的安静信号 → 服务端**不调度下一轮回复** → AI 永远不开口。

即：问题不只是"AI 回复被回声取消"（上一轮已处理），更是"**AI 说完后，回声让服务端以为
用户还在占用，永远不开始下一轮**"。第一轮之所以正常，是因为开始时扬声器安静、用户说完
有明确停顿。

**关键结论**：免提外放 + 纯软件 AEC 无法彻底区分回声与真实语音，"边听边说"（全双工）
在这种环境下**从根本上不可行**。上一轮的"压低 + 回声保护"只能缓解"回复被取消"，救不了
"下一轮不开始"——因为只要还在上传音频，`smart_turn` 就仍可能被回声持续触发。

## 方案：半双工（默认开启）

不再试图"在 AI 说话时分辨回声"，而是**AI 说话期间根本不上传麦克风音频**，从源头让
服务端安静下来：

- 新增 `_mic_muted()`：`half_duplex` 为真时，AI 说话期间（`_ai_speaking`）返回 True；
- `_send_audio` 在 `_mic_muted()` 为真时 `continue`，跳过麦克风读取与上传；
- `_end_response`（`response.done`）设 `_mic_resume_at = monotonic() + MIC_TAIL_SECONDS`
  （0.6s），AI 说完后再压一段**回声尾迹静默期**，待扬声器余音/混响散尽才恢复上传；
- 恢复后用户说下一句，`smart_turn` 正常判停、正常触发下一轮。

代价：失去"随口打断"。但外放场景本就打断不了（回声会干扰），等于无损。

**保留全双工**：戴耳机（无回声）时设 `[realtime_talk] half_duplex = false`，回到
“压低 + 回声保护窗口 + `speech_started` 打断”的全双工逻辑，随口打断体验恢复。

## 后续修正（同日）：静音必须从「响应开始」生效

半双工上线后用户实机复验，**第一轮正常，但仍出现 `⚠️ 回复被打断取消`**，据此定位到
静音时机的漏洞：

- 原实现只在 `_ai_speaking`（收到首个 `response.audio.delta`）时才静音；
- 而 `response.created` → 首个音频之间存在**空窗期**：模型思考 + Function Calling
  工具执行，需工具的提问（“现在几点了”）空窗期长达数秒；
- 空窗期内 `_ai_speaking` 仍为 False → 麦克风照旧上传 → 用户尾音/环境噪声被服务端
  当成“新的用户轮次” → 本轮响应被取消。

第一轮“贾维斯在吗”之所以正常，是因为纯闲聊响应快、空窗期极短，暴露时间不足；
这也解释了为何**需要工具的提问最易复现**。

修正：静音条件由 `_ai_speaking` 改为 **`_response_active or _ai_speaking`**——
`response.created` 一到就静音，覆盖思考与工具执行全程。判定逻辑下沉为
`realtime_audio.mic_muted()` 纯函数（`realtime_talk._mic_muted()` 仅做薄包装，
兼顾主文件 800 行上限）。

同时新增**悬挂保护** `MIC_MUTE_MAX_SECONDS`（30s）：若响应状态异常悬挂（事件丢失等），
超过上限强制解除静音，避免麦克风永久哑掉——那会变成“AI 再也听不到你说话”，
比原问题更糟。

## 第二次实机修正（同日）：客户端自打断 + 麦克风缓冲积压

再次实机复验仍是「第一轮正常、之后每次提问都 `⚠️ 回复被打断取消`」，对照官方文档
（`dashscope-docs/阿里云实时语音对话实现文档.md`）定位到**两个客户端侧根因**。

### 根因一：客户端在补发 `response.cancel`

官方文档「设置容错策略 → 打断处理」：

> server_vad / smart_turn 模式下，用户新语音会自动打断正在进行的模型回复
> （`response.done` 返回 `status=cancelled`）。

打断是**服务端行为**，但旧实现在收到 `input_audio_buffer.speech_started` 且
`_response_active` 为真时会**主动补发 `response.cancel`**，这成了压垮回复的最后一击：

1. 用户提问 → smart_turn 语义判轮**提前**判定“说完了” → `response.created`；
2. 客户端按修正一立刻静音麦克风；
3. **但服务端管道里还压着刚收到的音频尾巴**，继续处理后触发 `speech_started`；
4. 此时 `_response_active=True` → 客户端发 `response.cancel` → 本轮回复被取消。

修正：半双工下，响应期间的 `speech_started` **一律忽略**（此时麦克风本就静音，
不存在“合法的插话”）；响应结束后的 `speech_started`（用户新提问）仍走正常流程。

### 根因二：静音期间不读麦克风 → 恢复上传时先送出积压的回声

原静音实现直接 `continue` 跳过 `mic.read`，音频驱动缓冲随之积压。恢复上传时
**首批送出的正是 AI 说话期间录下的回声**，等价于没静音。

修正：静音判定移到 `mic.read` **之后**——始终持续读取以保持实时性，只在“是否上传”
上做门控。

### 配套

`_should_attenuate`（增益压低，全双工专用）下沉为 `realtime_audio.should_attenuate`
纯函数，为上述改动腾出行数余量（主文件仍 ≤800 行）。

### 已知限制

工具调用轮（`output` 全为 `function_call`）结束到最终回复 `response.created` 之间的
空档（工具执行 + 二轮推理，通常 <1s），麦克风会在 0.6s 尾迹后恢复上传。若工具执行
较慢，用户此时说话可能插入工具链。**（第三次修正已引入“工具链静音窗口”，见下文。）**

## 第三次实机修正（同日）：思考期空窗 + 工具链静音 + 当场诊断

前两次修正后实机复测**仍每轮必现**（`能听到我说话吗？` → 取消、`现在几点了？` → 无响应），
说明“响应期忽略 `speech_started`”与“静音时照常读取”都没打在真正的漏洞上。逐条对账后
确认静音窗口本身还有**两处空白**。

### 空白一：`response.created` 到达之前的思考期

半双工静音此前只在 `response_active`（由 `response.created` 置位）或 `ai_speaking`（首个
`response.audio.delta`）时生效。而 smart_turn 判停（`speech_stopped`）到模型出声之间的
空窗期**两个条件都不满足** —— 接入 79 个外部工具后该空窗可长达数秒，期间上传的尾音与
环境声会被服务端判成“新的用户轮次”，直接取消刚启动的本轮响应。

**这解释了“第一轮正常、之后每轮必现”**：第一轮时 MCP 尚未就绪，思考极短，恰好躲过空窗。

**修正**：从 `input_audio_buffer.speech_stopped` 起就抢先静音（`SPEECH_MUTE_SECONDS = 6.0`
为兜底上限），不再依赖任何响应事件；服务端判为非有效轮次（ambient 事件）时立即解除，
正常响应由 `_response_active` 接管。

> ⚠️ **该“抢先静音”已被第五次修正移除**：`speech_stopped` 事件真实存在（本节当时怀疑
> 它不触发，第四次又误判为不存在），所以这条分支实际上每次都在 `response.created` 之前
> 把静音帧发了出去——它不是“死代码”，而是**每轮取消的直接推手**。详见《第五次实机修正》。

### 空白二：工具轮结束到最终回复之间

工具轮的 `response.done` 会按普通回复走 0.6s 尾迹，之后工具执行 + 二次推理期间麦克风
恢复上传，同样会被判成新用户语音（表现为“问时间这类要调工具的提问没反应”）。

**修正**：`response.done` 时判定工具轮（`output` **全为** `function_call` —— 官方文档
明确“一轮响应也可能同时包含普通消息和函数调用”，故必须“全为”才算），改用
`MIC_MUTE_MAX_SECONDS` 兜底静音；最终回复的 `response.done` 会用普通尾迹覆盖它。

### 配套：不依赖配置的当场诊断

三次修正都隔着屏幕猜测，代价太大。现补两条零配置的诊断路径：

- **取消时直接回溯事件链**：`TalkEventLog.recent` 保留最近 8 条事件类型（连续重复折叠），
  `cancel_hint()` 在 `⚠️ 回复被打断取消` 下方打印“新→旧”事件链，一眼分辨“服务端收到
  新语音后取消”（链上先出现 `speech_started`）与“客户端补发 cancel”（链上没有）。
- **客户端指令进入时间线**：`note_client()` 记录 `response.create` / `response.cancel` /
  `session.update` 与麦克风静音窗口进出，带 `→` 前缀与服务端事件区分。

### 结构拆分

`_init_mcp_tools` / `_load_mcp_tools_async` 移入新建的 `agent/voice/realtime_mcp.py`
（会话中途的 `session.update` 与本次故障时间线高度重合，独立成文件便于排查），
主文件 794 → 766 行，为上述修正腾出空间。

### 残余风险

本轮修正**已经实机复验：未解决问题**（见下一节）。两处静音空白仍是推理得出的，
真正缺的是一份权威事件契约核对。

## 第四次实机修正（同日）：`speech_stopped` 不存在，改由客户端本地判停

> ⚠️ **本节的结论已被推翻（见下方《第五次实机修正》）**：`input_audio_buffer.speech_stopped`
> **真实存在**——实机事件链里反复出现；“契约无此事件”的判断源于官方文档残缺。由该错误
> 前提推出的“本地判停→说完抢先静音”方案不仅无效，而且正是**每轮取消的真正原因**。
> 本节保留作为排查弯路记录，请勿照此实现。

第三次修正上线后**仍然每轮取消**，但实机回报的事件链给出了决定性证据：

```
响应 1（成功）：🧑 你: 贾维斯，能听到我说话吗？贾维斯。 → 🤖 贾维斯: 能听到，先生。
响应 2..n（全部取消）：
  事件链回溯（新→旧）: response.done ← response.output_item.done
  ← response.content_part.done ← response.audio.done ← response.audio_transcript.done
  ← response.content_part.added ← conversation.item.created ← response.output_item.added
```

### 证据指向什么

链上**没有任何 `response.audio.delta`** —— 服务端走完了完整的“完成”流程，却没有下发一帧
音频；也没有 `speech_started`。即：**这一轮不是在说话时被打断，而是在出声之前就被取消**。
用户同时反馈“我都说完话了，麦克风依然一直在检测我”，指向同一个根因。

### 真因：`input_audio_buffer.speech_stopped` 不在 DashScope 契约里

对照官方文档（`dashscope-docs/阿里云实时语音对话实现文档.md`）：全文只在第 693、708 行
出现 `input_audio_buffer.speech_started`，**从未定义 `speech_stopped`**。

于是第三次修正的优先级逻辑成了死代码：

1. “话音一落就抢先静音”挂在 `speech_stopped` 上 → 事件不来，**从未触发过**，思考期照旧上传；
2. `ui.on_user_speaking(False)` 同样只在 `speech_stopped` 里调用 → 指示器永不复位，
   这正是用户看到的“麦克风一直在检测我”。

**教训：修 bug 前先核对事件契约。** 把 OpenAI Realtime 的事件名想当然地搬过来，
写出来的分支看着合理、单测也能过（测试自己造了该事件），实机却永远走不到。

### 修正：客户端自己判“说完”

新增 `realtime_audio.voice_gap()`（纯函数，便于单测），在 `_send_audio` 的上传门控前调用：

- 麦克风 RMS 连续低于 `VOICE_RMS`(0.02) 达 `SPEECH_GAP_SECONDS`(1.0s) → 判定“这一轮说完”，
  立即进入 `SPEECH_MUTE_SECONDS`(6.0s) 等待静音 → 堵住思考期空窗；
- 静音期间再次出现语音能量 → **立即解除**（用户续话不被吞掉，也不会被静音窗口误伤）；
- 从未说过话（纯底噪）→ 不静音，正常监听不受影响；
- 判定“说完”的那一刻同步 `ui.on_user_speaking(False)`，复位界面指示。

另外在 `conversation.item.input_audio_transcription.completed`（该事件**存在**，且转写完成
即代表说完）处也复位一次指示器，作为双保险。`speech_stopped` 分支保留但注明
“契约未定义、实机不触发”，兼容服务端未来可能补上该事件。

### 配套：诊断升级

第三次的事件链被**单轮尾部事件刷满**（8 条全是 content_part/audio/done），看不到更早的
“这一轮是怎么开始的”。故：

- 缓冲 8 → **24** 条；
- 新增 `_CHAIN_EVENTS` 白名单，**只收语义节点**（音频/转写 delta 与各类 done 不再入链）；
- `response.done` 节点带上 status（`response.done[cancelled]`），`response.output_item.added`
  带上 `item.type`（一眼分出工具轮与消息轮）；
- 取消时除终端输出外，**快照落盘** `~/.jarvis/logs/diag.log`（`dump_cancel_diag()`，
  不依赖 `event_log` 开关），用户只需提供日志末尾。

### 残余风险（已随第五次修正失效）

`SPEECH_GAP_SECONDS = 1.0` 是“句内自然停顿”与“真的说完了”之间的折中。修正后本地判停
**不再触发静音**，仅提前复位界面指示，切句不再造成“接着说时已被静音”，仅多一次无害的
指示复位（服务端 `speech_stopped` 到达时还会再复位一次）。

## 第五次实机修正（同日）：禁止在 `response.created` 之前静音

> ⚠️ **本节的根因结论已被推翻（见下方《第六次修正》）**：“静音在 `response.created`
> 之前会致服务端立刻取消响应”的观测，全部来自 **MCP 中途 `session.update` 毒化会话**
> 这一混淆变量（被毒化的会话里每条响应本就活不过 ~100ms 且签名相同）。本节保留
> 作为排查记录，其中“静音时机与成败强相关”的观察属实，但因果归因错误。

第四次修正上线后**仍然每轮取消**。这次拿到了带状态后缀的完整事件链（来自
`~/.jarvis/logs/diag.log` 的取消快照；终端显示的那份因旧版 `_chain_label` 不带
`[cancelled]` 后缀而看不出取消来源），且同一次会话里天然形成了对照组：

| 轮次 | `response.created` 之前紧邻的事件 | 结果 |
|---|---|---|
| 第一轮 | `conversation.item.created`（无客户端指令） | ✅ `response.done[completed]` |
| 第二、三轮 | `…transcription.completed` → **`→mic`（进入静音）** | ❌ `response.done[cancelled]` |

### 真因：客户端抢在响应创建之前静音

失败链完整形态（新→旧）：

```
response.done[cancelled] ← conversation.item.created ← output_item.added(message)
← response.created ← →mic ← conversation.item.input_audio_transcription.completed
← conversation.item.created ← input_audio_buffer.speech_stopped ← →mic
← input_audio_buffer.speech_started ← →mic ← response.done[completed] ← …
```

三个决定性事实：

1. **`input_audio_buffer.speech_stopped` 真实存在**——链上反复出现。第四次修正“契约里
   没有该事件”的判断是错的：当时的官方文档残缺（只写了 `speech_started`），而实机
   日志证明服务端会下发它。**文档残缺 ≠ 契约不存在。**
2. **取消不是“被打断”**：链上既无 `speech_started`（服务端没检测到新语音），也无
   `→response.cancel`（客户端没发取消）——是服务端在响应刚创建的瞬间自行中止；有的链
   短到只有 `response.done[cancelled] ← response.created` 两条，连 item 都没生成。
3. **唯一与成败完全相关的变量是静音时机**：成功轮 `response.created` 之前没有 `→mic`，
   失败轮全部紧挨一次 `→mic`，三轮零例外。

机理：本地判停窗口（1s）与服务端“判停 → 转写 → 创建响应”耗时接近，客户端几乎每次都
先一步把静音帧发出去；服务端随后在“刚收到静音流”的状态下建响应，判定本轮无有效输入
而中止。也解释了为何第一轮能活：首轮 `_mic_resume_at` 为 None，未走判停路径，
响应创建前从未静音。

### 修正：删掉一切“响应前静音”

- `realtime_audio.voice_gap()`：判停分支不再设置 `mic_resume_at`（`SPEECH_MUTE_SECONDS`
  常量整体删除），只保留“清零 `last_voice_ts`”供界面复位；
- `realtime_talk` 的 `speech_stopped` 分支：只复位“正在说话”指示，**不写静音窗口**；
- 静音来源只剩两类：**响应进行中**（`_response_active or _ai_speaking`）+ **回声尾迹
  窗口**（普通回复 `MIC_TAIL_SECONDS`，工具轮 `MIC_MUTE_MAX_SECONDS`）——两者都发生在
  响应已创建之后，不再可能取消任何响应；
- 界面“正在说话”指示保持三处复位（本地判停 / `speech_stopped` / 转写完成），互为保险。

### 这次为何能定位

前三次的日志都缺“谁在什么时候静的音”。第四次加的 `note_client()` 客户端指令时间线
（`→mic`）恰好补上这一环：把**客户端动作**与**服务端事件**放进同一条链，才让“静音抢在
响应创建之前”这个时序关系一眼可见。速通路径：`settings.toml` 里
`[realtime_talk] event_log = true`，全部事件按序写入 `~/.jarvis/logs/diag.log`
（取消快照 `dump_cancel_diag()` 与开关无关，始终落盘）。

## 第六次修正（2026-09-28）：根因翻案 + 传输无关引擎 + 桌面全双工桥接

第五次修正交付后实机仍现“每轮取消”，本轮推翻了其根因结论，并借此完成了引擎级重构。

### 翻案：第五次修正是混淆变量归因错误

决定性对照实验：**去掉 MCP 就绪后中途补发的 `session.update`**（会话配置改为在首个
update 一次性于 IDLE 完成），改为在 `response.created` **之后**才静音——取消依旧出现，
且取消时刻**早于**静音生效。两条事实同时击碎了第五次修正的因果链：

1. “响应创建前静音 → 立刻 cancelled”不成立：无中途 update 的会话里，静音在取消之后
   才生效，取消与静音无关；
2. 第五次修正看到的“失败轮全部紧挨 `→mic`”是真相关非因果——那些会话全部被
   **中途 `session.update` 毒化**（彼时每轮响应都活不过 ~100ms 且签名完全相同，
   成功的第一轮恰好发生在 MCP 就绪之前），`→mic` 只是同期发生的另一个变量。

**真实成因**：smart_turn 语义提前判停后，轮次立即提交、响应开始创建，但半双工静音
要到 `response.created` 后的下一个发送循环才生效；中间 ~100ms 里麦克风仍在直播真实
音频，用户的尾音/换气被服务端 VAD 重新检出为**新轮次** →
`response.done[cancelled, reason=turn_detected]` 掐死刚创建的响应，且被检出的“轮次”
再无下文（这正是“之后一直不接”的完整链条）。

### 对策体系（三层）

| 层 | 措施 | 落点 |
|---|---|---|
| 会话配置 | 一切配置（含 MCP tools）在首个 `session.update` 一次性 IDLE 完成，禁止中途补发 | `realtime_talk.run()` / `_handle_start_talk_duplex`：先加载 MCP 再 `run_session` |
| 轮次检测 | 默认从 smart_turn 切为官方免提推荐的 **server_vad**（声学判停要求真实静音，尾音只会延长当前轮，天然免疫）；smart_turn 可选，启用时叠加**轮末静音窗** `MIC_TURN_END_MUTE_SECONDS=3.0`（speech_stopped 起发静音帧堵尾音，用户真在说话则 RMS 超阈立即清窗，不吞话） | `realtime_engine.speech_stopped` 分支（仅 smart_turn）+ `realtime_audio.py` |
| 兜底 | **响应救援 v2**（`rescue`，默认开）：turn_detected 取消后 +1.2s / 轮次提交后无 response.created +1.8s，补发一次 `response.create`（官方允许“等待下一轮输入时”手动触发）；三闸门防重复回答：任何 response.done 撤销待发救援+距上次应答≥1s、phantom 轮（单字/纯标点/回声）不武装、两次救援最小间隔 2s；救援后强制静音保持 1s | `realtime_engine._response_rescue` 协程 |

另有**工具表瘦身**：实测 299 schema 全量表会让服务端“轮次提交后迟迟不建响应”，
默认 `tools_mode="builtin"`（2 内置工具）；工具名经 `sanitize_tools_for_realtime` 清洗。

### 重构：RealtimeEngine + 双适配器

- `realtime_engine.py`（新）：协议状态机/静音策略/救援/Function Calling 全部下沉，
  音频经鸭子类型接口注入（`_mic.read(n, False)` / `_spk.write(pcm)` /
  `stop_stream/start_stream`）；
- `realtime_talk.py` → 终端适配器（PyAudio + ESC + 工具装配，常量再导出保持旧引用兼容）；
- `realtime_bridge_audio.py`（新）：`BridgeMic`（超限 5s 丢最旧/欠载补静音）+ `BridgeSpk`
  （帧回调 + 打断 flush）；
- serve 协议：`talk.start` 新增 `duplex?: bool`；新指令 `talk.audio`（base64 PCM16 16kHz
  上行，fire-and-forget，64KB 上限）；新事件 `talk_audio`（24kHz 下行，空串=flush）；
- jarvis-desktop：`talkCapture.ts`（getUserMedia AEC + AudioWorklet 重采样）+
  `talkPlayback.ts`（AudioContext 顺序排播 + flush）+ store 接线；浏览器系统级 AEC
  补齐 WebSocket 协议“回声消除：无”短板，故桌面路径 `half_duplex=False`、
  `echo_suppress_with_aec=False`、`use_aec=False` 关三重软件消除防发闷——
  **实测效果好，说话即打断的真全双工成立**。

### 配置变更汇总

`[realtime_talk]` 新增 `rescue` / `turn_detection`（默认 `server_vad`）/ `silence_ms`（500）/
`tools_mode`（`builtin`）；`voice_barge_in` 默认改 False（第二个 PyAudio 实例 Windows
segfault 风险）；`stt_model` 默认改 `qwen3-asr-flash-realtime`（`stt_silence_threshold` 删除）。

### 验证

- `python -m pytest tests/voice -q` 全绿（含新增 `test_bridge_audio.py` 缓冲/欠载/丢弃/
  flush 用例与救援三闸门用例）；全量套件无回归；
- 桌面端全双工经用户实机验收（打断、多轮、回声均正常）。

### 经验总结（追加）

7. **相关不等于因果：多个改动同时落地时，归因必须单变量对照。** 第五次修正的
   “静音时机与成败完全相关”是真的，但因果方向错了——同期还站着“MCP 中途
   session.update”这个未被控制的变量。控制变量的对照实验（只删中途 update、只改
   静音时机）才能把混淆变量从因果链里剥离。
8. **状态机类第三方服务的“配置一次性原则”**：对话式协议会话的运行时配置变更（如
   中途 `session.update`）可能把会话置于文档未描述的中间态；能启动前定型的配置
   绝不中途改。
9. **物理层解决不了的问题往上找层**：软件 AEC + 静音窗是在应用层模拟回声消除，
   而浏览器 getUserMedia 的 AEC 是系统驱动级能力——把音频链路交给有这个能力的宿主
   （桌面壳），引擎只保持传输无关，比无限调参可靠。

## 涉及文件与实现

- `agent/voice/realtime_talk.py`：新增 `half_duplex` 参数、`_mic_muted()`、`_mic_resume_at`；
  `_send_audio` 上传前静音门控；`_end_response` 设尾迹窗口；启动提示按模式显示"半双工轮替
  ·AI 说完你再说"或"说话打断"。**（第五次修正）** `speech_stopped` 分支不再写静音窗口
  （只复位界面指示）；本地语音活动跟踪仅用于复位指示，不参与静音决策。
- 因主文件逼近 800 行上限，把 `_rms` / `_attenuate_pcm` 及 `ECHO_SUPPRESS_FACTOR` /
  `ECHO_GUARD_RMS` / 新增 `MIC_TAIL_SECONDS` 抽到 `agent/voice/realtime_audio.py`（纯函数 +
  常量），主文件 `from .realtime_audio import ...`，`rt_mod.ECHO_GUARD_RMS` 等访问方式不变。
- 配置接线：`settings.py` 新增 `realtime_half_duplex`（字段 + 键列表 + `[realtime_talk]`
  TOML 映射 `half_duplex`）；`voice_commands.py`、`workbench/engine.py` 两个装配点透传。

## 验证

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
python -m pytest tests/voice/ -q   # 195 passed
python -m pytest -q                # 2136 passed
```

新增 `TestHalfDuplex`（`tests/voice/test_realtime_response_lifecycle.py`）：AI 说话期静音、
**响应一开始就静音（锁死空窗期漏洞）**、响应悬挂超时后解除静音、尾迹窗口内静音、
窗口过后恢复并清空、全双工从不静音、`_end_response` 设尾迹时刻、
**响应期间忽略 speech_started（不发 cancel）**、响应结束后不阻断新提问、
**工具轮结束后继续静音**、普通回复只留短尾迹、**静音期仍送静音帧**、**未静音送真实音频**，
以及**本地语音活动跟踪 4 项**（说过后清零活动时刻、纯底噪不受影响、说话期间持续刷新、
静音期重新出现语音即解除窗口）共 **19 项**；`test_talk_terminal.py` 补 `half_duplex` 默认/关闭两项透传断言；
原“插话发 cancel”两项用例改为显式全双工（半双工默认不应打断）。

第五次修正改写/新增 2 项：**`speech_stopped` 到达不得抢先静音**（`_mic_resume_at is None`
且 `_mic_muted()` 为假）、**非有效轮次清掉残留静音窗口**；`test_unmuted_streams_raw_mic_data`
的断言由“与输入逐字节相同”改为“不是全零静音帧”（启用 AEC 时 `process_mic` 会重写音频，
原断言在 AEC 环境不可能成立，与本次改动无关）。

`tests/voice/test_realtime_observability.py` 补 **7 项**：客户端指令与服务端事件共用时间线、
关闭时指令不落盘但回溯可用、取消回溯按“新→旧”且**只收语义节点（音频帧被过滤）**、
输出项节点带 `item.type`、**取消快照落盘与开关无关**、无事件时返回空串。

## 经验总结

1. **用户的具体行为观察比症状标签更有价值。** "麦克风一直检测我 + AI 不接下一轮"这一
   描述，直接推翻了"只是回复被取消"的浅层归因，指向"服务端收不到安静信号"的真因。
2. **补救式参数（衰减/门限）到极限后，要敢于改变交互范式。** 与其在外放这种物理不利
   的场景里无限调参，不如退成半双工——用"不上传"彻底消除问题域，比"上传了再想办法
   过滤回声"可靠得多。
3. **默认值应为最常见场景兜底。** 多数用户免提外放，故半双工设为默认；把全双工留给
   明确戴耳机的进阶用户，用配置显式开启。
4. **范式切换要保留退路并成体系接线。** 半双工与全双工共存于一个开关，配置、装配点、
   文档、测试同步覆盖，避免“改了默认却断了耳机用户”。
5. **诊断能力要零配置、当场可见。** 三次修正都在隔屏猜测，根因在“客户端发的”与“服务端
   发的”之间来回摇摆。把事件链回溯做成取消时的即时输出（不依赖日志开关），才能让下一次
   复现直接给出答案，而不是再猜一轮。
6. **伸手改逻辑前，先核对事件契约——但要以实机日志为准。** 第四次修正据官方文档判断
   `speech_stopped` 不存在，于是把“抢先静音”改写成客户端本地判停；第五次实机链证明该
   事件真实存在（文档残缺），而真正的凶手恰是这个新引入的“响应创建前静音”。单测能过
   不代表契约正确（测试自己造的事件），文档没写也不代表服务端不发。教训：涉及厂商事件
   名、字段、状态值时，以实机日志为准，文档只作参考。

## 已知限制

- 半双工下 AI 说话期间无法被打断（设计如此）；需等 AI 说完再接话。
- `MIC_TAIL_SECONDS = 0.6` 为经验初值：房间混响大或外放音量大时可上调；当前为常量，
  若实测需要再提为配置项。
- 用户说完到服务端建响应之间不再静音（第五次修正），该窗口内上传的是用户语音尾部与
  少量环境声；实测已被服务端正确忽略。若环境噪声持续超过 VAD 门限，仍可能被判为新
  轮次——出现反复取消时应先排查声学环境/音量，而不是重新引入“抢先静音”。

## 变更文件

| 文件 | 变更 |
|---|---|
| `agent/voice/realtime_talk.py` | `half_duplex` 参数、`_mic_muted()`、`_mic_resume_at`/`_response_begin_ts`/`_last_voice_ts`；`_send_audio` 静音门控（置于 `mic.read` 之后）；`response.created` 记录响应起始时刻；**响应期间忽略 `speech_started`**；**工具轮保持静音**；**取消时打印事件链 + 快照落盘**；**说完时复位“正在说话”指示**；MCP 装配外移；`_end_response` 尾迹窗口；启动提示分流。**（第五次修正）** `speech_stopped` 分支改为只复位界面指示、不再写静音窗口；`voice_gap` 仅跟踪语音活动；静音只保留响应期与尾迹窗口 |
| `agent/voice/realtime_audio.py` | 新建：`_rms`/`_attenuate_pcm`/`voice_gap()`/`silence_like()` + `ECHO_SUPPRESS_FACTOR`/`ECHO_GUARD_RMS`/`MIC_TAIL_SECONDS`/`MIC_MUTE_MAX_SECONDS`/`VOICE_RMS`/`SPEECH_GAP_SECONDS` + `mic_muted()` 静音判定 + `should_attenuate()` 增益压低判定。**（第五次修正）** 删去 `SPEECH_MUTE_SECONDS` 与判停抢先静音分支 |
| `agent/voice/realtime_mcp.py` | 新建：MCP 工具装配（`init_mcp_tools` / `load_mcp_tools_async`），`session.update` 后写时间线 |
| `agent/voice/realtime_events.py` | `note_client()` 客户端指令时间线、`recent` 事件链缓冲（24 条 + `_CHAIN_EVENTS` 白名单 + `_chain_label()`）、`cancel_hint()`、`dump_cancel_diag()` 取消快照落盘 |
| `agent/config/settings.py` | `realtime_half_duplex` 字段 + 键列表 + TOML 映射 |
| `agent/commands/handlers/voice_commands.py`、`agent/ui/workbench/engine.py` | 透传 `half_duplex` |
| `tests/voice/test_realtime_response_lifecycle.py` | 新增 `TestHalfDuplex`（**19 项**，含响应起始静音、悬挂保护、忽略 speech_started、工具轮静音、静音帧/真实音频、**本地语音活动跟踪 4 项**）；**（第五次修正）** 新增“`speech_stopped` 不得抢先静音”“非有效轮次清残留窗口”，未静音断言改为“不是全零静音帧”；原插话用例改为全双工 |
| `tests/voice/test_realtime_observability.py` | 客户端指令时间线、取消回溯、链过滤与落盘断言（**7 项**） |
| `tests/voice/test_talk_terminal.py` | `half_duplex` 透传断言（2 项） |
| `docs/architecture/06-语音系统.md`、`12-配置系统.md`、`config-docs/voice-setup.md`、`README.md`、`README.en.md`、两份 `settings.example.toml` | 半双工说明与配置同步；**（第五次修正）** `06-语音系统.md` 重写“禁止在 `response.created` 之前静音”一节与模块表，本 fixlog 补第五次修正节；**（第六次修正）** 根因翻案：06 重写为引擎+双适配器架构并新增“响应救援/桌面全双工桥接”两节，12/config-docs/README 补 `rescue`/`turn_detection`/`silence_ms`/`tools_mode` 四键，两份 `settings.example.toml` 同步 |
| （第六次修正，2026-09-28）`agent/voice/realtime_engine.py` | 新建（915 行）：传输无关协议引擎——会话状态机/半双工静音/轮末静音窗（仅 smart_turn）/响应救援 `_response_rescue`/Function Calling；三条硬经验沉淀于 docstring |
| `agent/voice/realtime_talk.py` | 改为终端适配器 `RealtimeTalk(RealtimeEngine)`：PyAudio 设备 + ESC watcher + 工具装配（`tools_mode` 分流，MCP 前置加载），常量再导出保持兼容 |
| `agent/voice/realtime_bridge_audio.py` | 新建：`BridgeMic`（上行帧缓冲，超限丢最旧/欠载补静音）+ `BridgeSpk`（回调转发 + flush） |
| `agent/serve/protocol.py`、`agent/serve/server.py` | `talk.start` 加 `duplex?`；新指令 `talk.audio`（64KB 校验、fire-and-forget）与新事件 `talk_audio` |
| `agent/ui/workbench/engine.py` | `_handle_start_talk` 分发 + `_handle_start_talk_duplex`（Bridge 音频 + `half_duplex=False/use_aec=False`）+ `feed_talk_audio` 投递 |
| `agent/config/settings.py` | 新增 `realtime_rescue/turn_detection/silence_ms/tools_mode`；`voice_barge_in` 默认 False；`stt_model` 默认 `qwen3-asr-flash-realtime`（删 `stt_silence_threshold`） |
| jarvis-desktop（独立仓库） | 新增 `src/renderer/src/audio/talkCapture.ts`、`talkPlayback.ts`；`contracts.ts`/`dispatcher.ts`/`backendStore.ts` 接线 `talk.audio`/`talk_audio` 与 toggleTalk |
