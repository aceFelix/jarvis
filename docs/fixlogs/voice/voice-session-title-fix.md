# `/voice` 会话只有时间戳、从不生成标题修复复盘

> 模块：会话管理（`agent/session_manager.py`）+ 终端语音模式（`/voice`）
> 发现时间：2026-11-09（用户实测：文本聊天存下的会话是文字标题，语音会话只有
> `session-20261109-164502` 这种时间戳）
> 修复时间：2026-11-09
> 作者：aceFelix

---

## 1. 问题现象

终端里脱离 `/voice` 聊几轮再 `/exit`：

```text
> /exit
会话已自动保存（session-20261109-164502）
```

而同一台机器上用文本聊几轮，会话名会在一轮后变成用户首句的前 15 字，两轮后
再由 LLM 归纳成更贴切的短标题：

```text
📝 会话标题已生成: 帮我看看电脑为什么这么卡
```

诉求：**语音也走同一套标题逻辑（≤15 字）**；同时**会话要带时间属性**——标题不含
时间戳后，用户需要能从列表看出某个会话是什么时候的。

复现步骤：

1. `jarvis` 启动，输入 `/voice`，正常说两三轮；
2. 按 Ctrl+C 退出语音模式，再 `/exit`；
3. 查看 `~/.jarvis/sessions/` 或 `/sessions`：文件名仍是 `session-<时间戳>`。

---

## 2. 排查过程

### 阶段 1：标题是在哪里生成的

`agent/main.py` 的 REPL 主循环在每轮普通对话后执行三个动作：

```python
_dialog_count += 1
_auto_save(...)                       # 增量保存
if _dialog_count == 1:  _generate_title_from_first_user(...)   # 首句前 15 字
elif _dialog_count == 2: _generate_session_title(...)          # LLM 归纳 ≤15 字
```

标题与保存逻辑都在 `agent/session_manager.py`，本身没问题。

### 阶段 2：语音为什么不走这段

`/voice` 的命令处理器直接进 `voice_loop(adapter, settings, loop, ctx, ...)`
（`agent/commands/handlers/voice_commands.py`）。`voice_loop` 内部自己调
`await loop.run(user_text, ctx)`，**完全不经过 REPL 主循环**：

- 没有人给 `_dialog_count` 加一 → 两个标题分支永远进不去；
- 没有人调用 `_auto_save` → 语音轮只进内存，直到 `/exit` 才由主循环末尾兜底保存；
- 会话名一直是启动时的时间戳。

### 阶段 3：桌面端为什么没这个问题

`agent/serve/engine/engine.py` 的 `_after_turn()` 在语音转写落库后同样做着
“保存 + 首轮/次轮标题”，所以桌面端语音是有标题的。**只有终端 `/voice` 漏了**。

### 阶段 4：时间属性

`SessionMeta` 本来就有 `created_at` / `updated_at`（`save_session` 改名时会从
旧文件继承 `created_at`），但终端列表只显示更新时间，`sessions.list` 也没回传
`created_at`。标题取代时间戳后，用户就更难判断“这是哪天的会话”。

---

## 3. 根因分析

**同一条业务规则在两条入口各写了一份，其中一条漏写。**

- 文本入口：REPL 主循环里内联了“轮数 + 保存 + 标题”三步；
- 语音入口：`voice_loop` 绕开主循环，没有等价实现；
- 桌面入口：引擎自己实现了一份（`_after_turn`），所以没暴露。

只要规则仍内联在主循环里，任何新入口都会再次漏掉。时间属性则不是丢失，而是
**没有暴露到用户可见的地方**。

---

## 4. 修复方案

### 4.1 抽出共享收尾 `advance_session`

`agent/session_manager.py` 新增：

```python
async def advance_session(ui, messages, *, session_name, dialog_count,
                          title_generated, provider=None, model="",
                          workdir="", settings=None) -> tuple[str, int, bool]:
```

职责与文本原逻辑完全一致：轮数 +1 → `_auto_save` 落盘 → 第 1 轮取首句前 15 字 →
第 2 轮 LLM 归纳 → 返回更新后的 `(name, dialog_count, title_generated)`。

### 4.2 文本 REPL 改为调用它

`agent/main.py` 删掉内联三步，改为一次 `advance_session(...)`，并把三个返回值
同步回 `cmd_ctx`（`session_name` / `dialog_count` / `title_generated`）与
`ctx.extra["session_name"]`。命令分发后新增两行回读，保证 `/voice` 内部推进的
轮数与标题能带回主循环。

### 4.3 `/voice` 接入同一收尾

- `voice_commands._voice_mode(...)` 新增 `cmd_ctx` 参数（`handle_voice` 传入），
  构造 async 回调 `_on_turn_end`：调用 `advance_session` 并写回 `CommandContext`；
- `voice_loop` 已有的 `on_turn_end` 钩子从“只支持同步”放宽为“可返回 awaitable”
  （`inspect.isawaitable` 则 `await`），桌面端的同步 `_after_turn` 行为不变。

效果：语音每轮结束即落盘，第 1 轮后标题变成用户首句前 15 字，第 2 轮后由 LLM
归纳；被打断的半轮同样会保存（钩子在 `finally` 之后按消息增长判断）。

### 4.4 时间属性可见

- `sessions.list`（`agent/serve/engine/api.py`）新增 `created_at` 字段；
- `/sessions` 表格新增 **创建时间** 列（纯文本模式同样输出）；
- `/load` 选择器每项描述改为 `N条消息  创建 MM-DD HH:MM · 更新 MM-DD HH:MM`；
- `_rename_session_file` 改名时 `created_at` 原样继承，新增用例把这条锁住。

没有引入新配置项、没有改会话文件格式（`created_at` 早已存在）。

---

## 5. 验证结果

新增/补充用例：

- `tests/test_session_manager.py::TestAdvanceSession`（5 条）：
  首轮标题不调 LLM 且 ≤15 字、次轮走 LLM 标题、已有标题不重生成、空消息不动状态、
  **改名后 `created_at` 不变**；
- `tests/voice/test_voice_loop_turn_end.py::test_on_turn_end_awaits_async_callback`：
  async 回调必须被 await，否则保存/标题会静默丢失。

测试结果：

- `python -m pytest tests/test_session_manager.py -q` → 52 passed
- `python -m pytest tests/voice -q` → 全绿
- 全量 `python -m pytest tests -q` → **4711 passed, 2 skipped**

人工走查（待用户复测）：`/voice` 说一轮 → 会话名变首句前 15 字；再说一轮 → 变成
LLM 归纳的短标题；`/sessions` 能看到创建与更新时间。

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/session_manager.py` | 新增 `advance_session` 共享收尾；`/sessions` 与 `/load` 显示创建时间 |
| `agent/main.py` | 文本 REPL 改调 `advance_session`；命令分发后回读会话名/标题状态 |
| `agent/commands/handlers/voice_commands.py` | `_voice_mode` 接收 `cmd_ctx`，新增 async `on_turn_end` |
| `agent/voice/voice_loop.py` | `on_turn_end` 支持 async 回调（`inspect.isawaitable`） |
| `agent/serve/engine/api.py` | `sessions.list` 返回 `created_at` |
| `tests/test_session_manager.py` | 新增 `TestAdvanceSession` 5 条用例 |
| `tests/voice/test_voice_loop_turn_end.py` | 新增 async 回调用例 |
| `README.md` / `README.en.md` | 三层记忆能力行补充语音标题与创建/更新时间 |
| `docs/guide/voice.md` | `/voice` 增补“会话保存与标题”说明 |
| `docs/guide/commands.md` | `/sessions`、`/loads` 列出创建/更新时间 |
| `CHANGELOG.md` | Unreleased 记录本次修复 |

---

## 7. 经验总结

- **规则内联在主循环里，就一定会被新入口漏掉**。凡是“每轮对话后都要做”的事
  （保存、轮数、标题、恢复点），都应有一个入口无关的共享函数；桌面端已经各自
  实现一份，本身就是信号。
- **“有属性”和“看得到属性”是两件事**。`created_at` 一直在 meta 里，但没有任何
  终端界面展示它；标题替代时间戳后，这个缺口才被用户感知到。
- 钩子类型要按最宽需求声明：`Callable[[], None]` 会诱导调用方以为只能同步；
  改为允许 awaitable 后，同步宿主零改动。
- 建议以后新增任何对话入口（`/talk` 实时语音、微信、手机）时，先确认是否调用
  `advance_session`，否则同样会丢标题与轮数。
