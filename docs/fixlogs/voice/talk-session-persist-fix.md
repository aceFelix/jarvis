# 终端 `/talk` 会话落库缺失修复复盘

> 模块：实时双工语音（`/talk`）→ 会话历史 / 标题生成
> 发现时间：2026-11-09（用户：`/voice` 会话标题修好后追问「/talk 也需要存会话」）
> 修复时间：2026-11-09
> 作者：aceFelix

---

## 1. 问题现象

终端 `jarvis` 里用 `/talk` 实时双工聊完并退出后：

- `/sessions` 里对应的会话**看不到这几轮语音内容**（只有文本轮）；
- 退出时保存的会话名仍是启动时间戳，标题也不会按语音问句生成。

桌面端没有这个问题：`WorkbenchRealtimeUI` + `ChatEngine.commit_voice_turn`
会把转写配对落库。**终端 `/talk` 从来没接过这条链路。**

复现步骤：

1. 启动 `jarvis`，输入 `/talk`，说两轮（有问有答）；
2. ESC 退出，再 `/exit`；
3. `/sessions` 打开刚保存的会话：语音内容不在历史里。

---

## 2. 排查过程

### 阶段 1：确认 `/talk` 与 `/voice` 的差异

`/voice` 虽然也绕开 REPL 主循环，但走 `QueryLoop.run()`，消息会写进
`ctx.messages`；上一轮已通过 `voice_loop.on_turn_end` 接上 `advance_session`。

`/talk` 完全走 DashScope 实时 WebSocket（`RealtimeEngine`），**不经过
`QueryLoop`**，只有 `on_user_transcript` / `on_ai_transcript` 两条 UI 回调。

### 阶段 2：对照桌面实现

`agent/serve/engine/bridge.py::WorkbenchRealtimeUI` 已经把这些回调按轮配对，
并在配对完成后调用 `ChatEngine.commit_voice_turn(user, ai)` 落库 + `_after_turn`
生成标题。可直接复用同一套配对规则，只是终端缺这层适配器。

### 阶段 3：为什么不能在回调里直接 await

UI 回调是同步签名（`on_ai_transcript(text)`），而 `advance_session` 是 async
（第 2 轮要调 LLM 生成标题）。直接在回调里 `await` 不可行；用
`create_task` 又会让多轮任务并发争用 `dialog_count` / `session_name`。

---

## 3. 根因分析

1. **实时语音是独立通道**：`/talk` 不写 `ctx.messages`，所以既没有会话内容，
   也没有轮数推进 → 标题逻辑永远不会触发。
2. **终端侧从未适配“转写配对”**：桌面端有 `WorkbenchRealtimeUI`，终端直接把
   `RichCLI` 传给引擎，它只负责上屏，不认识“这是一轮对话”。
3. **同步回调 vs async 收尾**：配对回调是同步的，保存/标题是异步的，需要一个
   串行化的衔接层。

---

## 4. 修复方案

### 4.1 新增转写配对适配器

`agent/voice/talk_history.py::TalkHistoryRecorder`：

- 包装真实 UI（`__getattr__` 透传），覆盖
  `on_user_speaking` / `on_user_transcript` / `on_ai_transcript` /
  `on_ai_transcript_delta`；
- 配对规则与桌面 `WorkbenchRealtimeUI` **完全一致**：
  - 正常顺序：用户转写先到，AI 转写到达即配对；
  - 转写滞后：回答先到且用户开过口 → 暂存等配对；
  - 开场白/主动播报（用户从未开口）：AI 转写单独落库；
  - 回声轮（转写与 AI 刚播内容重合）：丢弃；
  - 打断轮：未配对的旧问句先单独落库；
  - `flush_pending_transcripts()`：退出时把残轮写入。

### 4.2 命令接线 + 串行落库

`voice_commands._realtime_talk(ui, settings, cmd_ctx=None)`：

- 传入 `cmd_ctx` 时，`_commit(user, ai)` 同步追加
  `Message.user_text` / `Message.assistant_text` 到 `cmd_ctx.messages`，
  并向 `asyncio.Queue` 投一个信号；
- 单个 worker 任务串行消费信号、调用 `advance_session`，更新
  `session_name / dialog_count / title_generated`——多轮不会互相覆盖；
- `finally`：先 `flush_pending_transcripts()`，再投哨兵并 `await` worker，
  保证最后一轮不丢；
- 未传 `cmd_ctx`（旧调用点/测试）时 UI 原样传入，行为零变化。

### 4.3 未做的事

- 不把 `/talk` 强行塞进 `QueryLoop`：实时通道的低延迟与工具模式是独立设计，
  只为“落库”改架构不划算；
- 不新增配置项，也不需要用户操作。

---

## 5. 验证结果

新增 `tests/voice/test_talk_history.py`（9 条）：

- 配对规则 7 条：正常顺序、转写滞后、开场白、回声丢弃、打断轮、
  flush 残轮、透传/增量兼容；
- 命令接线 2 条：`_realtime_talk` 后 `messages` 为 `[user, assistant]` 且轮数 +1；
  未传 `cmd_ctx` 时仍是原始 UI。

全量 `python -m pytest tests -q` → **4720 passed, 2 skipped**。

人工走查（待用户复测）：`/talk` 说两轮 → `/exit` → `/sessions` 能看到标题与
这两轮内容；`/load` 恢复后语音轮与文本轮按顺序出现。

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/voice/talk_history.py` | 新增 `TalkHistoryRecorder`（转写配对 + flush） |
| `agent/commands/handlers/voice_commands.py` | `_realtime_talk` 接 `cmd_ctx`、串行落库 worker、退出 flush |
| `agent/commands/handlers/voice_commands.py::handle_talk` | 传入 `CommandContext` |
| `tests/voice/test_talk_history.py` | 新增 9 条测试 |
| `README.md` / `README.en.md` | 语音能力行补充“两种语音都写入会话历史” |
| `docs/guide/voice.md` | `/talk` 增加“会话落库与标题”说明 |
| `CHANGELOG.md` | Unreleased 记录本功能 |

---

## 7. 经验总结

- **“同一件事在不同入口各自实现”是长期漏洞源**：桌面有、终端没有；文本有、
  实时没有。凡是“每轮结束要做什么”，都应有入口无关的实现（这里是
  `advance_session` + 配对适配器）。
- **同步回调里做异步收尾，要用队列串行化**：直接 `create_task` 会让状态字段
  竞争；队列 + 单 worker 既保证顺序，也能在退出时 `await` 到最后一轮落盘。
- **配对规则是业务知识，不要重写**：桌面 `WorkbenchRealtimeUI` 的六条规则来自
  实机踩坑（转写滞后、回声轮、开场白），终端直接对齐同一套，避免再踩一遍。
- 新增语音/IM/远程入口时，先问三件事：消息是否进 `messages`？轮数是否推进？
  退出时是否有 flush？
