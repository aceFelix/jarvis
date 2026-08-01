# P0 工具错误自愈升级方案

> 优先级：🔴 P0
> 目标：把 Jarvis 工具执行稳定性从 ⭐⭐⭐ 提升到 ⭐⭐⭐⭐，让工具调用失败时能够自动分类、重试、降级或询问用户，而不是直接把错误抛给 LLM。
> 参考：ClaudeCode 的 `command-exec` 重试、OpenClaw 的 `retryMiddleware`、LangChain 的 `RetryTool`。

---

## 一、目标与验收标准

### 1.1 目标

1. **错误自动分类**：根据错误文本/异常类型把工具失败分为网络、限流、超时、文件缺失、权限、认证、依赖缺失、配置错误、未知错误。
2. **可恢复错误自动重试**：网络抖动、API 限流、超时、文件缺失等错误按策略自动重试。
3. **输入/环境自动修复**：超时自动延长 `timeout` 参数；写文件时自动创建缺失的父目录。
4. **重试耗尽后询问用户**：不是默默失败，而是给出建议后询问是否再试一次。
5. **自愈遥测与诊断**：进程内记录失败/恢复事件，`/doctor` 命令展示配置、错误分布、最近事件。
6. **全局可配置/可关闭**：通过 `enable_tool_self_healing` 一键开关，重试次数和退避时间可调。
7. **全入口统一生效**：REPL、headless、daemon、ACP、teammate、subagent 全部统一装配自愈能力。

### 1.2 验收标准

- [ ] Bash 命令因网络超时失败时，自动重试并最终成功。
- [ ] 文件读取因路径不存在失败时，写工具自动创建父目录。
- [ ] API 返回 429 / rate limit 时按指数退避重试。
- [ ] 命令超时时自动增加 `timeout` 参数再次尝试。
- [ ] 重试耗尽后通过 UI 询问用户是否继续。
- [ ] `/doctor` 命令显示自愈配置、历史事件、错误分布和优化建议。
- [ ] 所有新增/修改代码通过 `python -m py_compile` 检查。
- [ ] 新增单元测试覆盖分类器、重试策略、auto_fix、开关控制。
- [ ] 更新 `docs/architecture/10-扩展生态.md` 和 `README.md` 相关章节。

---

## 二、当前状态与差距

### 2.1 已具备能力

| 模块 | 能力 | 文件 |
|---|---|---|
| 工具编排 | `ToolOrchestrator` 并发执行、权限检查、结果聚合 | `agent/core/orchestrator.py` |
| 工具结果 | `ToolResult` 支持 `is_error` 标记 | `agent/core/result.py` |
| 权限系统 | 五层 fail-closed 管线，ASK/PLAN 模式可拦截 | `agent/permissions/` |
| REPL 命令 | `/doctor` 已存在，展示系统/会话状态 | `agent/main.py` |

### 2.2 关键差距

| 差距 | 影响 | 优先级 |
|---|---|---|
| 工具失败没有统一分类 | LLM 收到的是原始错误文本，无法自动决策 | 高 |
| 没有自动重试机制 | 网络抖动、临时超时直接失败 | 高 |
| 没有输入/环境自动修复 | 文件父目录缺失、timeout 偏小需要用户手动处理 | 高 |
| 没有自愈遥测 | 无法知道哪些工具经常失败、失败原因是什么 | 中 |
| 没有统一关闭开关 | 某些场景下不希望自动重试（如破坏性命令） | 中 |
| headless/daemon/teammate 等入口未统一装配 | 自愈能力只覆盖部分执行路径 | 中 |

---

## 三、详细设计

### 3.1 错误分类器（ToolErrorClassifier）

文件：`agent/core/error_recovery.py`

```python
class ToolErrorClassifier:
    def classify(self, tool_name, result, exception=None) -> ClassifiedError
```

分类结果：

| 分类 | 触发模式 | recoverable |
|---|---|---|
| `NETWORK_TRANSIENT` | timeout / connection / DNS / SSL / reset by peer | True |
| `RATE_LIMIT` | rate limit / 429 / throttled / quota exceeded | True |
| `TIMEOUT` | `subprocess.TimeoutExpired` / `asyncio.TimeoutError` / 文本 timeout | True |
| `NOT_FOUND` | No such file or directory / 不存在 / repository not found | True |
| `PERMISSION_DENIED` | Permission denied / 403 / 拒绝访问 | False |
| `AUTH_MISSING` | API key / unauthorized / 401 / 未配置 key | False |
| `DEPENDENCY_MISSING` | command not found / no module named / 未安装 | True |
| `CONFIG_INVALID` | invalid config / 配置错误 / 400 | True |
| `UNKNOWN` | 其他 | False |

分类优先级：TIMEOUT > RATE_LIMIT > AUTH > PERMISSION > NOT_FOUND > DEPENDENCY > CONFIG > NETWORK > UNKNOWN。

### 3.2 自愈策略（RecoveryPolicy）

```python
@dataclass
class RecoveryPolicy:
    category: ToolErrorCategory
    max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    auto_fix: bool           # 是否尝试修复输入/环境
    ask_user_on_fail: bool   # 重试耗尽后是否询问
```

默认策略：

| 分类 | max_retries | backoff_base | auto_fix |
|---|---|---|---|
| NETWORK_TRANSIENT | 3 | 1s | False |
| RATE_LIMIT | 3 | 2s | False |
| TIMEOUT | 2 | 1s | True |
| NOT_FOUND | 1 | 0.5s | True |
| PERMISSION_DENIED | 0 | - | False |
| AUTH_MISSING | 0 | - | False |
| DEPENDENCY_MISSING | 1 | 1s | True（仅建议） |
| CONFIG_INVALID | 1 | 0.5s | True |
| UNKNOWN | 1 | 1s | False |

### 3.3 自愈执行器（ToolRecoveryExecutor）

```python
class ToolRecoveryExecutor:
    async def execute(
        self,
        tool_name: str,
        call_fn: Callable[[dict, ToolContext], Any],
        args: dict,
        ctx: ToolContext,
        tool_is_read_only: bool = False,
    ) -> RecoveryResult
```

执行流程：

```text
调用 tool.call()
    │
    ├─ 成功 → 返回
    │
    ▼ 失败
分类错误
    │
    ├─ 自愈关闭 → 记录遥测，直接返回错误
    │
    ▼ 按策略重试（最多 max_retries 次）
        ├─ auto_fix 为真 → 尝试修复 args/环境
        ├─ 指数退避等待
        ├─ 再次调用 tool.call()
        └─ 成功 → 记录遥测，返回结果
    │
    ▼ 仍失败
    ├─ ask_user_on_fail → 询问用户是否再试一次
    │       ├─ 用户确认 → 再调用一次
    │       └─ 用户放弃 → 返回错误
    │
    └─ 返回错误并记录遥测
```

### 3.4 自动修复规则

| 分类 | 修复动作 | 说明 |
|---|---|---|
| TIMEOUT | `timeout = max(timeout + 60, timeout * 2)`，上限 600s | 自动放宽超时 |
| NOT_FOUND | 对写工具，若 `file_path` 父目录不存在，自动 `mkdir(parents=True)` | 自动创建目录 |
| DEPENDENCY_MISSING | 不自动安装，返回安装建议 | 避免误操作 |

### 3.5 遥测（RecoveryTelemetry）

单例，进程内共享：

```python
class RecoveryTelemetry:
    def record(tool_name, category, recoverable, attempts, resolved, message)
    def get_summary() -> dict
    def get_recent(n=10) -> list[RecoveryIncident]
    def top_category() -> str | None
```

### 3.6 集成点

在 `ToolOrchestrator._run_one()` 中，原来直接 `await tool.call()` 的地方改为：

```python
if self._recovery and self._recovery.is_enabled():
    recovery_result = await self._recovery.execute(...)
    result = recovery_result.final_result
else:
    result = await tool.call(...)
```

所有构造 `ToolOrchestrator` 的地方都传入 `recovery_executor`：

- `agent/main.py` REPL
- `agent/main.py` headless
- `agent/main.py` ACP
- `agent/daemon/daemon.py` daemon
- `agent/collaboration/teammate.py` teammate
- `agent/collaboration/subagent.py` subagent

### 3.7 /doctor 诊断命令增强

在现有 `_doctor` 基础上新增自愈面板：

```python
def _doctor_recovery(ui, settings):
    # 展示：
    # - 自愈开关、最大重试、退避基数/最大值
    # - 历史事件总数、已自愈数、未恢复数
    # - 错误分布（按分类）
    # - 最近 5 次自愈事件
    # - 高频错误类型警告
```

### 3.8 配置

`configs/settings.toml` 或 `~/.jarvis/settings.toml`：

```toml
[self_healing]
enable_tool_self_healing = true
tool_retry_max = 3
tool_retry_backoff_base = 1.0
tool_retry_backoff_max = 30.0
```

环境变量：`JARVIS_ENABLE_TOOL_SELF_HEALING`、`JARVIS_TOOL_RETRY_MAX` 等。

---

## 四、实施步骤

| 步骤 | 内容 | 产出文件 | 风险 |
|---|---|---|---|
| 1 | 实现错误分类器 + 分类常量 | `error_recovery.py` | 低 |
| 2 | 实现策略表 + 自愈执行器 | `error_recovery.py` | 中 |
| 3 | 实现遥测单例 | `error_recovery.py` | 低 |
| 4 | Settings 新增自愈配置 | `settings.py` | 低 |
| 5 | Orchestrator 接入自愈 | `orchestrator.py` | 中 |
| 6 | 所有入口装配 recovery_executor | `main.py`, `daemon.py`, `teammate.py`, `subagent.py` | 中 |
| 7 | 增强 `/doctor` 自愈面板 | `main.py` | 低 |
| 8 | 编写单元测试 | `tests/core/test_error_recovery.py` | 中 |
| 9 | 更新架构文档与 README | `10-扩展生态.md`, `README.md` | 低 |

---

## 五、测试计划

### 5.1 单元测试

新增 `jarvis/tests/core/test_error_recovery.py`：

| 测试 | 覆盖内容 |
|---|---|
| `test_dependency_missing` | command not found 分类为依赖缺失 |
| `test_not_found` | 文件不存在分类 |
| `test_network_transient` | connection reset 分类为网络错误 |
| `test_rate_limit` | 429 分类为限流 |
| `test_auth_missing` | API key 缺失分类 |
| `test_permission_denied` | 权限不足分类 |
| `test_timeout_from_exception` | TimeoutError 分类为超时 |
| `test_success_no_recovery` | 成功时不进入自愈流程 |
| `test_retry_then_success` | 重试后成功 |
| `test_timeout_auto_fix` | 超时自动延长 timeout 参数 |
| `test_not_found_auto_create_parent` | 文件缺失自动创建父目录 |
| `test_disabled_no_recovery` | 关闭自愈时不重试 |

### 5.2 集成测试

- Bash 执行 `curl` 临时失败，观察是否重试。
- 写一个不存在的文件路径，观察是否自动创建目录。
- 调用一个返回 rate limit 的 mock 工具，观察退避重试。

### 5.3 验证命令

```powershell
# Python 语法检查
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
python -m py_compile agent/core/error_recovery.py agent/core/orchestrator.py agent/config/settings.py agent/main.py

# 单元测试
python -m pytest tests/core/test_error_recovery.py -q
```

---

## 六、文档更新

| 文档 | 更新内容 |
|---|---|
| `docs/architecture/10-扩展生态.md` | 扩展点总览增加 Self-Healing；新增"十、工具错误自愈"章节 |
| `README.md` | 新增"工具错误自愈"章节，包含配置和 `/doctor` 命令说明 |
| `docs/fixlogs/` | 如修复过程中发现值得记录的 bug，按 `bugfix-review.md` 规则补充 |

---

## 七、风险与应对

| 风险 | 应对 |
|---|---|
| 自动重试放大破坏性行为（如重复写文件） | 只读工具可多一次重试；写工具严格按策略，超时/文件缺失类才 auto_fix |
| 指数退避导致用户等待过久 | 配置 `tool_retry_backoff_max` 上限，UI 显示等待倒计时 |
| 分类器误判 | 保留 UNKNOWN 兜底；误判时记录遥测便于后续调优 |
| 子进程 stdin 被截胡（本次 DevServer 教训） | 与自愈无关但相关：启动长期进程必须用 `stdin=DEVNULL` |
| 认证/权限错误不应自动重试 | 策略表显式关闭这两类的重试和 auto_fix |

---

## 八、备注

- 本方案保持 Jarvis 现有的 `ToolOrchestrator` 架构，只在 `_run_one()` 中包一层 `ToolRecoveryExecutor`，侵入性低。
- 参考 ClaudeCode 的容错设计，但不实现完整的沙箱重跑（超出 P0 范围）。
- 所有修改遵循现有代码风格：dataclass、Javadoc/行内注释、`@author aceFelix`。
