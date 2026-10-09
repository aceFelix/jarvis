# 实时语音工具模式默认值误改为 all 导致语音完全不可用（已回退）

> 记录日期：2026-10-09 ｜ 模块：实时语音工具装配 ｜ 作者：aceFelix
>
> **状态：变更已回退**，默认值恢复为 `"builtin"`。本文记录这次回归的完整过程与教训。

## 1. 问题现象

### 背景（触发变更的需求）
用户反馈「实时语音无法调用常用工具」——问天气时 AI 回复无法查询，因为默认
`tools_mode = "builtin"` 只注册 `get_current_time` / `end_conversation` 两个工具。

### 变更后（用户实测复现）
按用户选择把默认值改为 `"all"`（全量 ToolRegistry + MCP）后，**实时语音完全不可用**：

- 用户说「在吗？贾维斯。」后，界面长时间无任何 AI 回复；
- 界面反复出现：`响应救援：已提交的用户问题未获应答，补发 response.create`；
- 事件链回溯（新→旧）显示死循环：
  `response.done[cancelled] (reason: turn_deleted)` ← `conversation.item.created`
  ← `output_item.added(message)` ← `response.created` ← `input_audio_buffer.speech_started`
  ← `error` ← `response.create rescue` ← `conversation.item.input_audio_transcription.completed`
  ← ... 往复；
- 关键证据：事件链中出现 `session.update tools=294 td=server_vad（init）`——**294 个
  工具 schema 被一次性发给实时 API**。

## 2. 根因分析

### 2.1 当次结论（2026-10-09 推断，后被实测修正）

超大工具表（294 个 schema）让轮次提交后服务端**迟迟**不创建响应，客户端「响应救援」
检测到已提交问题无应答便补发 `response.create`，与尾音重检互相放大，形成
`response.done[cancelled] reason=turn_deleted` 死循环。

关键点：**这个问题在变更前就被记录在 `realtime_talk.py` 的模块注释里**
（原文：「299 工具 schema 的全量会话实测会出现"轮次提交后服务端迟迟不创建响应"」）。
我在改默认值时把这段警告删掉、换成了轻描淡写的「可能略拖慢服务端响应触发」，
把一个已验证的故障描述成了性能问题，直接导致了本次回归。

### 2.2 探针实测修正（同日补充，结论更精确）

用 DashScope 官方 WebSocket 端点做「工具数梯度」离线探针（免麦，文本触发轮
`conversation.item.create` + `response.create`，逐档测量 `response.created` 时延）：

| 工具数 N | response.created 时延 | response.done 状态 |
|---|---|---|
| 2（builtin） | 0.67s | completed |
| 38（全注册表） | 0.89s | completed |
| 60（合成垫量） | 1.19s | completed |
| 120 | 1.65s | completed |
| 200 | 2.28s | completed |
| 294 | 3.45s | completed |
| 450 | 4.89s | completed |

**修正后的结论**：服务端**始终会创建响应**，并非「拒绝/无法处理」；真正的问题是
**响应创建延迟随工具数近似线性增长（≈12ms/工具，450 个约 4.9s）**。这个秒级延迟
在生产链路上与两个既有机制叠加，才放大成「半天不回复」：

1. **响应救援**（`RESCUE_AFTER_COMMIT_SECONDS = 1.8s`）：用户轮次提交 1.8s 后客户端
   补发 `response.create`，而此时服务端其实正在准备响应 → 服务端回 `error`，且与
   正在创建的响应冲突（与 diag.log 中"未弹警告的良性 error"吻合）。
2. **server_vad 尾音重检 + 半双工静音窗口**：慢响应的关键窗口更宽，用户任何声响
   都会被判成新轮次 → `turn_detected` 取消刚创建的响应。

同时发现**具体工具的 schema 结构不是诱因**：全注册表 38 个工具名全部合法、
JSON Schema 结构干净（无 `anyOf`/`$ref`/非法 type），且合成工具垫量到 450 也能正常完成。
所以问题**纯粹是数量（上下文/受限解码开销）**，不是某个工具"有毒"。

这一修正很重要：它说明「精选常用工具子集」是**可行的**——按 ~12ms/工具估算，
10~20 个工具的创建延迟约 0.7~0.9s，接近内置基线，不会触发救援风暴。

## 3. 修复方案（回退）

把 `tools_mode` 默认值在全部 6 处恢复为 `"builtin"`，并把原始警告**强化**保留
（附本次实机证据），防止后人再次误改：

| 文件 | 改动说明 |
|---|---|
| `agent/config/settings.py` | `realtime_tools_mode` 默认回退 `"builtin"`，注释含实机故障描述与「勿改为 all」警示 |
| `agent/voice/realtime_talk.py` | 构造参数 `tools_mode` 与 `default_instructions()` 默认回退 `"builtin"`；模块头注释恢复并强化警告（含 294 schema 与救援死循环证据） |
| `agent/commands/handlers/voice_commands.py` | 终端 /talk getattr 兜底回退 `"builtin"` |
| `agent/ui/workbench/engine.py` | 桌面全双工/PyAudio 两条路径 3 处 getattr 兜底回退 `"builtin"` |
| `agent/configs/settings.example.toml` | `[realtime_talk] tools_mode` 示例回退 `"builtin"`，加显著警告 |
| `docs/guide/voice.md` | Function Calling 条目与配置示例回退，标注 all 会挂死 |
| `jarvis-desktop/docs/guide/features-voice.md` | 同步为「默认 builtin，勿改 all」警示 |

## 4. 验证结果

- `python -m compileall -q agent/` 通过；
- `pytest tests/voice tests/ui -q` 全绿（回退不涉及新增测试）；
- 待人工复测：重启桌面壳 → 实时语音说「在吗」→ 应正常应答（不再出现响应救援死循环）。

## 5. 结论与产品定位（2026-10-09 定案）

排查后用户决定**不再继续投入**，接受当前形态并固化产品定位：

| 场景 | 承载方式 | 工具能力 |
|---|---|---|
| 闲聊 / 低延迟对话 | 实时语音（`/talk`、桌面「实时聊天」） | 内置 2 个（时间查询、结束对话） |
| 需要调用工具（天气、文件、命令等） | **半双工语音（`/voice`）** | 全部工具（标准 LLM API 全量工具表） |

即：**实时语音不再追求全量工具**，工具调用统一走 `/voice` 半双工语音。

> 若未来仍想恢复实时语音的工具能力，探针数据给出了明确路径：**精选 10~20 个常用工具
> 子集**（如天气 + 时间 + 提醒 + 文件读取）。按 ≈12ms/工具估算，该规模创建延迟约
> 0.7~0.9s，接近内置基线，不会触发救援风暴。**不要再一次性注册全量工具表。**

## 6. 经验总结

- **删除"已知故障"注释 = 删除了一道防线**：改代码时只更新了注释措辞，把「会让服务端
  不建响应」这个硬故障降级成「可能略慢」。**变更前必须原样保留已验证的失败记录**，
  不能因为"我要改成新默认"就顺手重写掉不利描述。
- **性能保守默认往往是踩过坑的产物**：默认值 `builtin` 不是随手写的，是实机验证后
  的选择。改默认值前应先问「当初为什么这么设」，并查阅 fixlogs。
- **回归的影响面**：本次变更直接让核心功能（实时语音）完全不可用，比原需求
  （缺个天气工具）严重得多——**"功能缺失"优先于"性能优化"的排序在这里被用反了**。
- **用户提供了决定性证据**：界面的事件链回溯（`tools=294` + 救援死循环）一眼锁定根因，
  这类诊断信息应在排查时主动向用户索取。
- **"服务端不建响应"和"服务端建响应很慢"是两种病、两种药**：本次先按"不建响应"推断，
  写进了注释与复盘；直到用官方 WS 端点跑工具数梯度探针，才确认服务端**始终会建**，
  只是延迟线性增长。**推断结论在写入"永久性注释/文档"之前，必须先做一次可复现的实测**，
  否则会把错误认知固化成下一道误导后人的"防线"。
- **探针要隔离变量**：合成工具垫量（数量）与真实注册表工具（内容）对照，一次就排除了
  "某个工具 schema 有毒"这个方向，避免盲目二分定位。
- **能力缺口可以换成合适的载体**：实时语音协议工具表开销大且与低延迟目标冲突，而
  半双工 `/voice` 走标准 LLM API 天然支持全量工具。与其在实时链路上硬撑，不如按场景
  分工——**先问"这个能力该由哪条链路承载"，再谈优化**。
