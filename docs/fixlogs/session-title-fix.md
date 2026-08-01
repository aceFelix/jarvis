# 会话标题生成失效修复复盘

> 关键词：会话标题、LLM 标题、messages 脱钩、DeepSeek 空回复、max_tokens

## 1. 问题现象

用户连续两轮反馈会话标题生成/保存异常：

### 第一次反馈（标题变时间戳）

聊完天后通过 `Ctrl+C` 正常退出，但会话文件以时间戳命名（如 `session-20260731-213000.json`），
标题没有生成。

### 第二次反馈（标题不更新）

聊天 4 轮（含工具调用、多轮对话）后退出，会话仍以**第 1 轮**生成的标题保存：

```
> jarvis,在干嘛
📝 会话标题已生成: jarvis在干嘛
> 现在几点了            （第 2 轮，应触发 LLM 重生成标题，但没有）
> 明天天气如何          （第 3 轮）
> OK                   （第 4 轮）
会话已自动保存（jarvis在干嘛）
```

第 2 轮起应该由 LLM 根据前两轮对话生成更准确的标题，但实际没有触发，最终仍以第 1 轮
"jarvis在干嘛"保存。

### 第三次反馈（跳过崩溃恢复后标题仍取旧会话）

修复上述根因后，用户启动 jarvis：`auto_resume_session` 自动恢复了上次会话
「auto-latest」(30 条消息)，随后崩溃恢复检测提示是否恢复，用户选择 `n` 跳过，
新会话第一轮提问 "jarvis，在吗"，标题却生成为旧会话首句 "jarvis在干嘛-2"。

## 2. 排查过程

### 阶段一：定位"时间戳命名"（第一次反馈）

沿 `main.py` 的标题触发链路排查：

```python
# main.py REPL 循环
_dialog_count += 1
if _dialog_count == 1:                       # 第 1 轮：首句前 15 字
    _session_name = await _generate_title_from_first_user(...)
elif _dialog_count == 2 and len(messages) >= 4 and not _title_generated:
    _session_name = await _generate_session_title(...)   # 第 2 轮：LLM 生成
```

发现两个问题：

- **Bug A（撞名放弃）**：`_rename_session_file` 在目标文件名已存在时直接返回旧名
  （时间戳），标题生成了但被丢弃。多轮对话时用户首句重复（如多次"在干嘛"）
  就会撞名。
- **Bug B（messages 脱钩）**：`query_loop.py` 中 `ctx.messages = layered.messages`
  是**重绑定**——`LayeredContext.messages` 属性每次返回**新列表拷贝**
  （`self._frozen + self._active`），重绑定后 main.py 持有的 `messages` 列表引用
  与内部列表脱钩，assistant 消息永远进不了调用方历史。

**验证 Bug B**：仿真运行发现修复前 `main.messages` 停在 `[u1]`，
`len(messages) >= 4` 永不满足 → LLM 标题永不触发；且自动保存丢失 assistant 回复。

### 阶段二：修复后仍未更新标题（第二次反馈）

修复 Bug A/B 后用户实测"聊完两轮并没有更新标题"。此时检查保存的会话文件，
发现已有 24 条含 assistant 的消息（脱钩已解决），但 `_generate_session_title`
调用返回的 `title_text` 仍为空——**LLM 标题生成本身返回空文本**。

### 阶段三：隔离变量，定位两个隐藏根因（关键转折点）

用真实 provider（AnthropicProvider / deepseek-v4-flash）写隔离测试脚本
`_test_system.py`，对 `system` 和 `max_tokens` 做交叉验证：

| 变量组合 | 结果 |
|---|---|
| `system=""` + `max_tokens=30` | 空输出 |
| `system=" "` + `max_tokens=30` | 正常输出 |
| `system="你是标题助手..."` + `max_tokens=30` | 正常输出 |
| `system=""` + `max_tokens=100` | 空输出 |
| `system="你是标题助手..."` + `max_tokens=100` | 正常输出（"贾维斯报时间"） |

确认是两个**相互独立**的根因叠加：

- **Bug C1：`system=''` 空字符串**。DeepSeek 的 Anthropic 兼容端点对空 system
  静默返回空文本（实测验证），传非空 system 即正常。
- **Bug C2：`max_tokens=30` 过小**。标题 prompt + 前两轮对话文本较长时，
  DeepSeek 的思考过程耗尽全部 30 个 token，可见输出为 0。

### 阶段四：跳过崩溃恢复后标题仍取旧会话（第三次反馈）

用户日志显示：`auto_resume_session` 启动时恢复了「auto-latest」(30 条消息)，
崩溃恢复提示选 `n` 后，第一轮标题生成 "jarvis在干嘛-2"。

排查 `main.py` 启动链路：

```python
# 1) 自动恢复上次会话（进入 REPL 之前）
if settings.auto_resume_session:
    ...
    messages.extend(session.messages)      # 30 条旧消息进入 messages

# 2) 崩溃恢复检测（banner 之后）
if answer.strip().lower() in ("y", "yes"):
    messages.clear(); messages.extend(point.messages)   # 恢复分支有清空
else:
    clear_recovery_point()                 # 跳过分支【没有清空 messages】← 根因
```

确认 **Bug D**：跳过崩溃恢复时只清除了恢复点，`messages` 里仍保留
`auto_resume_session` 加载的 30 条旧消息。`_generate_title_from_first_user`
取 messages 第一条 user 消息 → 旧会话首句 "jarvis在干嘛"，撞名加序号变
"jarvis在干嘛-2"。

## 3. 根因分析

| 编号 | 根因 | 所属模块 |
|---|---|---|
| A | `_rename_session_file` 目标已存在时退回时间戳旧名，标题白生成 | session_manager.py |
| B | `ctx.messages = layered.messages` 重绑定导致调用方列表与内部列表脱钩 | query_loop.py |
| C1 | DeepSeek Anthropic 兼容端点对 `system=""` 静默返回空文本 | session_manager.py（调用方） |
| C2 | `max_tokens=30` 太小，长 prompt 时思考过程吞光 token | session_manager.py（调用方） |
| D | 跳过崩溃恢复时未清空 `auto_resume_session` 加载的旧会话消息 | main.py |

前三个根因**相互叠加**：Bug B 让 LLM 标题根本不会被调用，修掉 B 后 C1/C2
又让调用返回空文本，最终层层掩盖到只剩"标题不更新"一个表象。
Bug D 独立存在于启动流程：即使前三个全修好，跳过恢复时标题仍会取旧会话首句。

## 4. 修复方案

### query_loop.py：in-place 切片同步（修 Bug B）

```python
# 发送 LLM 前同步快照（L237）
ctx.messages[:] = layered.messages

# 轮末 Hook 同步（L346）
ctx.messages[:] = layered.messages
```

用 in-place 切片保留列表对象身份，所有持有原引用者（REPL/daemon/语音）都能看到
完整对话历史。改为重绑定是根因——`LayeredContext.messages` 返回的是新列表拷贝。

### session_manager.py：撞名加序号（修 Bug A）

`_rename_session_file` 目标已存在时自动追加 `-2`、`-3`…（最多 99 次），
不再退回时间戳旧名：

```python
for n in range(2, 100):
    candidate = f"{title}-{n}"
    candidate_path = sessions_dir() / f"{candidate}.json"
    if not candidate_path.exists():
        old_path.rename(candidate_path)
        return candidate
```

### session_manager.py：system 非空 + max_tokens 提高（修 Bug C1/C2）

```python
events = provider.stream(
    model=model,
    system="你是会话标题生成助手，只输出标题，不输出解释。",
    messages=msgs,
    tools=[],
    max_tokens=100,   # 原 30；长 prompt 时 30 会被思考吞掉，输出为空
    temperature=0.3,
)
```

### main.py：跳过恢复时清空 messages（修 Bug D）

崩溃恢复选 `n` 的分支在清除恢复点之外，同时清空 auto_resume 加载的消息，
开启真正的新会话（`ctx.messages` 与 `messages` 共享同一列表，`clear()` 同步生效）：

```python
else:
    clear_recovery_point()
    # 跳过恢复 = 全新会话：启动时 auto_resume_session 已把上次会话
    # 消息加载进 messages，必须一并清空。否则第一轮标题生成会取
    # 旧会话首条消息（如「jarvis在干嘛」），新会话上下文也被旧对话
    # 污染。ctx.messages 与 messages 共享同一列表，clear() 即可同步。
    messages.clear()
    ui.info("已跳过恢复，恢复点已清除，开始全新会话")
```

注意：用户选择 `y` 恢复时原本就有 `messages.clear()` 再填充恢复点，本次修复
让 `n` 分支行为对称——恢复就载入，不恢复就清空，不会残留中间状态。

## 5. 验证结果

- **隔离验证**：`_test_system.py` 交叉验证确认 system/max_tokens 是两个独立根因。
- **最终验证**：真实调用 `_generate_session_title`（deepseek-v4-flash）返回
  `"贾维斯报时间"`，输出 `📝 会话标题已生成: 贾维斯报时间`。
- **自动化测试**：`python -m pytest tests/ -q` → 280 passed, 1 failed。
  唯一失败的 `test_image_skip_in_text_mode` 经 `git stash` 确认是改动前就存在的
  既有失败，与本次修复无关。Bug D 修复后 `py_compile` 语法检查通过。
- **待人工验证**：重启 jarvis 后，分别验证：
  1. 正常聊天 2 轮以上：第 1 轮后标题由首句生成；第 2 轮后标题被 LLM 更新；
     退出后会话文件以最终标题保存。
  2. 异常退出后重启，崩溃恢复提示选 `n`：新会话第一轮标题应基于本轮首句
     （如 "jarvis在吗"），而非旧会话首句；且新会话上下文不含旧对话。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `jarvis/agent/core/query_loop.py` | L237/L346 改为 in-place 切片同步，修复 messages 脱钩 |
| `jarvis/agent/session_manager.py` | `_rename_session_file` 撞名时追加序号；`_generate_session_title` system 传角色说明、max_tokens 30→100 |
| `jarvis/agent/main.py` | 崩溃恢复选 `n` 时 `messages.clear()`，跳过恢复即全新会话 |
| `jarvis/docs/fixlogs/session-title-fix.md` | 本复盘文档 |

## 7. 经验总结

1. **"重绑定 vs in-place"是一个易被忽视的致命差异**。凡是"属性返回新拷贝"的对象
   （如 `LayeredContext.messages`），外部持有引用者必须用 `list[:] =` 同步，
   否则静默脱钩——表现为"历史不涨、保存丢消息、条件永不满足"，极难排查。
2. **DeepSeek Anthropic 兼容端点的两个坑**：
   - `system=""` 会静默返回空文本，不报错、不降级，只能靠隔离测试发现；
   - `max_tokens` 过小会被思考过程吞光，可见输出为 0。
   凡是用兼容端点做"小任务"（标题、摘要、分类）都要警惕这两个变量。
3. **多 bug 叠加时，表象只有一层**。Bug B 掩盖了 C1/C2，只有逐一隔离才能
   暴露全部根因。排查"标题不更新"类问题时，先确认调用是否发生（打日志/
   看调用方状态），再隔离被调用函数内部的变量。
4. **真实 API 隔离测试是定位模型行为的唯一可靠手段**。对 LLM 供应商的边界行为
   （空 system、token 上限）不能靠读文档推断，写最小复现脚本跑一遍最直接。
5. **"跳过/拒绝"分支必须对称清理中间状态**。main.py 的 `y` 分支会
   `messages.clear()` 再填充，`n` 分支原本只清了恢复点——`auto_resume_session`
   先加载的旧消息成了残留中间状态。凡是"接受就载入、拒绝就放弃"的交互，
   两个分支都要显式处理资源（这里是对话历史），不能只做一半。
