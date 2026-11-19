# 悬空 tool_use 与会话永久失效修复（协议配对不变量 + 源头补齐 + 出口兜底）

> 日期：2026-09-27 ｜ 涉及：`agent/core/tool_pairing.py`（新增）、`agent/core/team_notify.py`（新增，顺带拆分）、`agent/core/query_loop.py`、`agent/core/orchestrator.py`、`agent/llm/openai_provider.py`、`agent/llm/anthropic_provider.py`

## 现象

用户连续两次在 jarvis CLI 里发同一句需求，每次都收到：

```
❌ LLM 调用失败: 原始错误:  messages.6: `tool_use` ids were found without `tool_result` blocks
immediately after: call_00_W5BETHrgw5tGlRKZiWtu3701, call_01_DfyIVhw3stHuatphBOPB3869. Each
`tool_use` block must have a corresponding `tool_result` block in the next message.
[ 请求参数错误 ] 尝试 /model 切换到兼容的模型
```

特征：**重试无效、换措辞无效、换模型也无效**——只要恢复这个会话，每次请求都在受理前被 API 拒收。

查看本地会话文件可复现该状态（`~/.jarvis/sessions/WPS写Jarvis介绍文档.json`）：

| 索引 | role | 内容 |
|---|---|---|
| 5 | assistant | `text` + `tool_use(Bash, call_00_W5BE...)` + `tool_use(Bash, call_01_DfyI...)` |
| 6 | user | `text`（用户新输入），**没有任何 tool_result** |

索引 5 的两个工具调用被写进了历史，配对结果却没有写进去；后续所有请求都带着这个残缺片段。

## 根因

LLM 协议（Anthropic / OpenAI 一致）要求两个方向都配对：

- 每个 `assistant.tool_use` 必须在**紧随的下一条消息**里有 `tool_result`
- 每个 `tool_result` 必须有对应的 `tool_use`

违反时不是"这一轮报错"，而是**整个请求被拒收**——残缺片段已经落进会话历史，于是该会话被永久毒化。

代码里有四条会产出悬空 `tool_use` 的路径：

| # | 路径 | 位置 | 触发场景 |
|---|---|---|---|
| 1 | 工具执行中被用户中断 | `query_loop.py` 工具执行段 `CancelledError` 分支 | 用户按 Esc / Ctrl+C 打断正在跑的工具。assistant（含 tool_use）已 append 进历史，异常 break 前没补结果 |
| 2 | 工具执行期抛非取消异常 | 同上，异常冒泡出 `run()` | `orchestrator.execute_calls()` 内部抛出未被捕获的异常（如编排器自身 bug、Hook 异常） |
| 3 | 输出被 max_tokens 截断且含 tool_use | `query_loop.py` 截断续写分支 | 模型在长回复里夹带工具调用就被截断。该分支直接 `continue` 续写，**不执行工具**，但 assistant 已带 tool_use 入历史 |
| 4 | 编排器调度缺项 | `orchestrator.py` 结果对齐 | 原实现 `if tu.id in results_by_id` 静默过滤缺项 → 输出比输入短，模型收到"少了几条结果"的历史 |

**路径 3 的关键证据**：`anthropic_provider.py` 在流结束后对每个 `content_blocks` 条目**无条件** `yield ToolCall`（`input_json` 被截断时容错补 `}`，仍不可解析则退化为 `{"_raw": ...}`）。因此"截断轮不会产生工具调用"这个假设不成立，截断轮确实会产出 `tool_use`。

**容易误判的一点**：工具**执行失败**反而是安全的——它走的是正常路径，会产出 `is_error=True` 的 `tool_result`，配对完整。真正中毒的只有"调用已记录、结果未写入"这半截状态。

## 修复

采用「一道不变量 + 三道源头 + 一道出口」的分层防御。

### 不变量（新增 `agent/core/tool_pairing.py`）

`ensure_tool_pairing(messages) -> list[Message]`：输入对话历史，返回满足配对不变量的历史。

- **健康历史零拷贝**：无悬空/无孤儿时直接返回**同一个列表对象**，不重建、不改时间戳
- **只修发送副本**：返回副本，绝不就地改历史（冻结前缀被 `cache_control` 锁定，改了就破 prompt cache）
- **补齐缺失**：悬空 `tool_use` → 注入 `is_error=True` 占位结果，文案说明"该调用已失效，如需完成请重新发起调用"
- **丢弃孤儿**：无对应 `tool_use` 的 `tool_result` 直接移除（协议同样不允许，且保留会让模型困惑）
- **去重**：重复的 `tool_result` 只留第一条
- **role 交替**：占位结果优先并入**紧随其后的 user 消息**（`tool_result` 块前置）；若下一个是 assistant 或已到末尾，才另起一条 user 消息
- **保序**：占位结果按原 `tool_use` 顺序排列
- **保存原型**：重建 user 消息时保留原 `id` / `timestamp`

占位文案统一模板：

```
[系统补充] 工具 {name} 的调用没有返回结果（{reason}）。该调用已失效，如需完成此步骤请重新发起调用。
```

原因常量：`会话被用户中断` / `输出被截断，工具未执行` / `工具执行抛出异常` / `上一条回复未回收结果` / `编排器未产出结果`。

### 三道源头（缺什么补什么，把残缺挡在历史外）

1. **截断分支**（`query_loop.py`）：截断轮若含 `tool_use`，先 `make_placeholder_results(..., REASON_TRUNCATED)` 补占位，再与既有的续写提示合并成**一条** user 消息（避免连续两条 user）。
2. **中断分支**（`query_loop.py`）：`CancelledError` → 补 `REASON_ABORTED` 占位 → 保留原 `aborted` 语义（置 `abort_event`、重置）→ break。
3. **异常分支**（`query_loop.py`，新增）：工具执行抛非取消异常 → 补 `REASON_EXEC_ERROR` 占位 → UI 报错 → `stopped_reason="tool_error"` → break。不再让异常带悬空调用冒泡。
4. **编排器等长保证**（`orchestrator.py`）：结果对齐从"过滤缺项"改为"逐项 `get` + 缺失补 `REASON_ORPHAN` 占位"，**输出与输入严格等长**。

### 一道出口（兜底所有 LLM 请求路径）

在两个 provider 的消息转换入口调用 `ensure_tool_pairing`：

- `openai_provider.py::_messages_to_openai`
- `anthropic_provider.py::_messages_to_anthropic`

出口兜底的价值：主循环之外还有压缩、标题生成、realtime 语音等多条发 LLM 请求的路径，逐一改成本高且易漏；放在转换入口可一次覆盖，并且能**自愈已经中毒的旧会话**（用户无需丢弃历史）。

顺带修掉两个转换细节：

- **OpenAI 输出顺序**：`role="tool"` 消息必须先于同批的 user 文本消息输出（否则 tool 回执不在 `tool_calls` 之后，接口同样拒收）。
- **空内容占位**：assistant 既无正文也无 tool_calls → `content=""`；Anthropic 侧 blocks 为空 → 补 `{"type": "text", "text": "(此消息不含可发送内容)"}`。**不跳过**空消息，避免破坏 role 交替。

### 顺带拆分（800 行规则）

`query_loop.py` 修复前已 788 行，加代码会超 800 行上限。把 `_inject_teammate_notifications()`（78 行）抽到新文件 `agent/core/team_notify.py`，`query_loop.py` 保留同名 re-export（兼容既有测试的 monkeypatch 路径 `agent.core.query_loop._inject_teammate_notifications`）。

## 测试

- `tests/core/test_tool_pairing.py`（新增，25 用例）：
  - `ensure_tool_pairing` 12 例：健康历史零拷贝（断言返回同一对象）/ 空列表 / 末尾悬空 / 悬空并入下一条 user / 部分缺失 / 撞上 assistant 另起消息 / 孤儿丢弃其文本保留 / 重复去重 / 不就地修改且保留 `id`+`timestamp` / 幂等 / system 不打断配对 / role 交替
  - 占位工厂 2 例：文案含工具名与原因、批量保序
  - 双协议 payload 5 + 4 例：OpenAI `tool` 消息紧跟 `tool_calls`、孤儿丢弃、顺序、空 assistant；Anthropic `tool_result` 块前置、空 blocks 占位
  - 编排器等长 2 例：缺项补占位、正常路径不受影响
- `tests/test_query_loop_run.py`：`FakeOrchestrator` 新增 `raise_error`；`test_cancelled_during_orchestrator` 追加历史断言（断言补了 `is_error=True` 占位）；新增 `test_truncated_tool_use_gets_placeholder_result`、`test_orchestrator_exception_padded_and_stopped`

### 顺势拆分超长测试文件（800 行规则）

改动目标文件 `tests/test_query_loop_run.py` 本已 1510 行（超 800 行上限），本次又新增用例，故按职责拆分（与 `tests/daemon/_fakes.py` 的共享方式保持一致）：

| 文件 | 职责 | 行数 |
|---|---|---|
| `tests/_query_loop_fakes.py` | 共享测试替身与工厂（`ScriptedProvider` / `FakeOrchestrator` / `registry` / `make_loop` 等） | 207 |
| `tests/test_query_loop_run.py` | `run()` 主流程 / 中断 / Provider 错误与故障转移 / 边界场景 | 489 |
| `tests/test_query_loop_stream.py` | 内容累积与 Hooks / 辅助方法 / `_stream_once` / 切片同步回归点 | 411 |
| `tests/test_query_loop_branches.py` | 团队邮箱注入 / 补充分支覆盖（hooks 容错、延迟工具、清理函数） | 292 |
| `tests/test_query_loop_session.py` | 会话持久化 / 模型切换 | 240 |

拆分校验：逐文件 `py_compile` 通过；`--collect-only` 用例 ID 集合与原文件**完全一致**（59 = 59）；全量测试仍 **1993 passed**。

### 既有用例适配

4 处原先拿"孤儿 `tool_result`"当输入的用例补上配对 `assistant.tool_use`（否则孤儿被丢弃后断言索引失效）；`test_user_tool_result_and_text_combined` 断言顺序改为 tool 先于 user。

### 全量结果

```bash
python -m pytest tests/ -q    # → 1993 passed, 3 warnings（既有 sandbox / swig 噪声，与本次无关）
```

### 端到端自愈验证（真实中毒会话）

对本地三份真实会话文件跑「加载 → 修复 → 按不变量审计 → 喂进真实 provider 转换函数审计 payload」：

| 会话 | 修复前违规 | 修复形态 | 修复后 | Anthropic payload | OpenAI payload |
|---|---|---|---|---|---|
| `WPS写Jarvis介绍文档.json`（11 条） | 末尾悬空 2 个 `Bash` 调用 | 并入下一条 user | 0 | 11 条 / 0 违规 | 16 条 / 0 违规 |
| `打开WPS写一份介绍jarvi.json`（6 条） | 末尾悬空 2 个调用 | 另起一条 user | 0 | 7 条 / 0 违规 | 11 条 / 0 违规 |
| `jarvis现在几点了.json`（16 条） | 中间悬空 1 个调用（msgs[13]，被 msgs[14] 的 user 文本跨过） | 并入 msgs[14] | 0 | 16 条 / 0 违规 | 18 条 / 0 违规 |

覆盖了两种真实悬空形态：**末尾悬空**（用户中断后没再发消息）与**中间悬空**（用户直接发了下一条消息，残缺片段被埋进历史）。

## 边界与已知限制

- **占位结果是"提示"不是"重放"**：不重放原调用（用户已中断/输出已截断，重放可能造成意外副作用）。模型看到占位后如需该步骤会重新发起调用。
- **出口兜底会丢弃孤儿 `tool_result`**：历史里若存在协议违规的孤儿结果，其文本内容会一并丢失（除非该消息还有其他合法块）。这是协议约束下的必然取舍——保留就会被拒收。
- **未做结果缓存**：`ensure_tool_pairing` 是 O(n) 单次扫描，健康历史零拷贝返回，性能开销可忽略；不缓存结果（历史每轮都在变）。
- **Prompt cache 影响**：修复只在有违规时重建，健康历史返回同一对象、`id`/`timestamp` 不变，冻结前缀的 `cache_control` 标记不受影响。
- **未做 interactive 实机验证**：验证止于"真实会话 → 真实 provider 转换函数 → payload 合法"，未实际发起线上 LLM 请求（避免消耗用户额度）。
