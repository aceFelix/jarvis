# 测试覆盖工程中发现的源码 Bug 修复复盘

## 1. 问题现象

在为 jarvis 核心模块补全单元测试（目标覆盖率 80%+）的过程中，通过隔离测试和覆盖率分析发现了 **4 个隐藏的源码 Bug**。这些 Bug 在正常使用中不易触发或被 except 静默吞掉，但通过测试用例的白盒验证被精确定位。

---

## 2. 排查过程

### 阶段一：memory 模块测试

为 `agent/core/memory/compactor.py` 编写测试时，发现 `update_session_memory` 函数在所有测试中都返回 `None`，而非预期的记忆摘要字符串。

- **假设 1**：mock provider 返回空文本 → 排除（其他用例用相同 mock 正常工作）
- **假设 2**：函数逻辑有 bug → 逐行检查发现 `os.path.exists` 调用，但模块顶部没有 `import os`
- **验证**：`monkeypatch.setattr(compactor, "os", os, raising=False)` 注入后函数正常返回 → 确认是 `NameError` 被 except 静默吞掉

### 阶段二：orchestrator 模块测试

为 `agent/core/orchestrator.py` 编写"工具抛异常"测试时，预期异常被封装为 `ToolResult.error`，实际却抛出 `NameError`。

- **排查**：`except Exception: result = ToolResult.error(...)` 中的 `ToolResult` 未在模块顶部 import
- **验证**：`pytest.raises(NameError)` 固化了该行为，确认是 import 遗漏

### 阶段三：sandbox 模块测试

为 `agent/core/sandbox/risk_scorer.py` 编写测试时，发现 `pip freeze` 被评为 `MEDIUM` 而非 `LOW`。

- **排查**：`score_command` 检查顺序为 CRITICAL → HIGH → MEDIUM → readonly(LOW)。`_MEDIUM_PATTERNS` 中 `\bpip\s+(install|uninstall|freeze)\b` 先于只读白名单匹配 `pip freeze`
- **验证**：从 MEDIUM 模式中移除 `freeze` 后，`pip freeze` 正确命中 `_READONLY_COMMANDS` 白名单 → LOW

### 阶段四：query_loop 模块测试

为 `agent/core/query_loop.py` 的队友通知注入功能编写测试时，发现注入的消息永远无法进入对话历史。

- **假设 1**：`_inject_teammate_notifications` 未被调用 → 排除（mock 确认被调用）
- **假设 2**：注入的消息被下一轮覆盖 → 检查发现 `ctx.messages[:] = layered.messages` 在循环顶部执行，会覆盖注入消息
- **根因确认**：`_inject_teammate_notifications(ctx)` 读到的 `ctx.messages` 是上一轮的旧快照（不含刚追加到 `layered` 的工具结果），且注入后 `len(ctx.messages) > len(layered.messages)` 的比较基准错误 → 注入消息无法同步回 `layered`
- **修复验证**：在调用注入前先 `ctx.messages[:] = layered.messages` 同步最新状态，注入消息正确进入 layered 并保留在历史中

---

## 3. 根因分析

| Bug | 根因 | 影响 |
|---|---|---|
| compactor 缺 `import os` | 模块顶部遗漏 `import os`，`update_session_memory` 中 `os.path.exists` 抛 `NameError` 被 except 吞掉 | **会话记忆写入功能从未生效**，每次调用都静默返回 None |
| orchestrator 缺 `import ToolResult` | `except Exception` 分支引用了未导入的 `ToolResult` | 工具抛异常时 `NameError` 直接向外传播，而非封装为错误结果回传 LLM |
| risk_scorer 白名单失效 | MEDIUM 正则 `\bpip\s+(install\|uninstall\|freeze)\b` 在只读白名单检查之前匹配 | `pip freeze` 被误评为 MEDIUM，只读白名单中的 `pip freeze` 条目永远不生效 |
| query_loop 队友通知死代码 | `_inject_teammate_notifications` 调用前未同步 `ctx.messages` 到 `layered` 最新状态 | **多 Agent 队友通知注入功能实际不生效**，注入的消息被丢弃 |

---

## 4. 修复方案

### Bug 1: compactor.py 补 `import os`

文件：`agent/core/memory/compactor.py`

```python
# 修复前
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field

# 修复后
from __future__ import annotations
import asyncio
import os  # 新增
from dataclasses import dataclass, field
```

### Bug 2: orchestrator.py 补 `import ToolResult`

文件：`agent/core/orchestrator.py`

```python
# 修复前
from agent.core.result import PermissionBehavior, PermissionResult

# 修复后
from agent.core.result import PermissionBehavior, PermissionResult, ToolResult
```

### Bug 3: risk_scorer.py 从 MEDIUM 模式移除 freeze

文件：`agent/core/sandbox/risk_scorer.py`

```python
# 修复前
r"\bpip\s+(install|uninstall|freeze)\b",

# 修复后（freeze 是只读操作，已列入 _READONLY_COMMANDS）
r"\bpip\s+(install|uninstall)\b",
```

### Bug 4: query_loop.py 注入前同步 ctx.messages

文件：`agent/core/query_loop.py`

```python
# 修复前
_inject_teammate_notifications(ctx)
if len(ctx.messages) > len(layered.messages):
    for extra in ctx.messages[len(layered.messages):]:
        layered.append(extra)

# 修复后
ctx.messages[:] = layered.messages  # 先同步到最新状态
_inject_teammate_notifications(ctx)
if len(ctx.messages) > len(layered.messages):
    for extra in ctx.messages[len(layered.messages):]:
        layered.append(extra)
```

---

## 5. 验证结果

- **全量测试**：1467 passed, 1 failed（`test_image_skip_in_text_mode` 为既有失败，与本次修复无关）
- **对应测试用例更新**：
  - `test_pip_freeze_is_readonly`：断言 `pip freeze` 返回 `LOW`（原断言 MEDIUM）
  - `test_teammate_injection_hook_invoked`：断言注入消息进入对话历史（原断言不进入）
  - `test_tool_exception_without_recovery_propagates`：断言异常被封装为 `is_error` 结果（原断言 NameError 传播）
  - compactor 测试移除 `monkeypatch os` 注入，验证真实行为
- **回归验证**：全量 1467 个测试无新增失败

---

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/core/memory/compactor.py` | 顶部补 `import os`，修复会话记忆写入 NameError |
| `agent/core/orchestrator.py` | import 补 `ToolResult`，修复工具异常封装 NameError |
| `agent/core/sandbox/risk_scorer.py` | MEDIUM 模式移除 `freeze`，让只读白名单生效 |
| `agent/core/query_loop.py` | 队友通知注入前同步 `ctx.messages` 到 layered 最新状态 |
| `tests/test_sandbox_more.py` | `test_pip_freeze_actual_behavior` → `test_pip_freeze_is_readonly`，断言改为 LOW |
| `tests/test_query_loop_run.py` | `test_teammate_injection_hook_invoked` 断言注入消息进入历史 |
| `tests/test_orchestrator.py` | `test_tool_exception_without_recovery_propagates` 断言异常被封装 |

---

## 7. 经验总结

1. **`except Exception` 是静默吞 bug 的重灾区**：compactor 和 orchestrator 两个 Bug 都是被 `except Exception` 包裹后静默返回 None/传播 NameError。关键路径的 except 应至少记录日志或 re-raise
2. **检查顺序决定白名单是否生效**：risk_scorer 的白名单检查在 MEDIUM 之后，导致更具体的白名单条目被更宽泛的正则先匹配。设计评分系统时，只读白名单应优先于模式匹配
3. **in-place 切片同步的遗漏点**：query_loop 在循环内多处用 `ctx.messages[:] = layered.messages` 同步，但在队友通知注入前遗漏了同步。每次 `layered.append` 后、读 `ctx.messages` 前，都应先同步
4. **测试覆盖是发现隐藏 Bug 的最有效手段**：这 4 个 Bug 在功能测试中均未暴露（要么被 except 吞掉、要么功能本就不常用），只有通过白盒测试用例精确验证每个分支才能发现
