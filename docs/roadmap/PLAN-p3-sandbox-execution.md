# P3-8 安全沙箱执行 — 升级方案

> 目标：高风险操作在隔离环境中运行，防止误操作破坏系统。参考 Claude Code sandbox-adapter 架构。

---

## 一、现有基础分析

| 已有能力 | 位置 | 说明 |
|---|---|---|
| 权限校验管线 | `agent/permissions/checker.py` | 五步判定：工具特判→路径守护→模式覆写→规则匹配→默认ASK |
| Shell 命令分类器 | `agent/permissions/shell_classifier.py` | readonly/dangerous/unknown 三态分类 |
| 路径守护 | `agent/permissions/path_guard.py` | 敏感目录硬拦截 + 符号链接逃逸检测 |
| BashTool | `agent/tools/bash.py` | Git Bash 执行 + 超时保护 |
| 权限模式 | `agent/permissions/modes.py` | DEFAULT/PLAN/ACCEPT_EDITS/YOLO 四档 |

**缺失的部分**（P3-8 的"沙箱"含义）：

| 缺失能力 | 说明 |
|---|---|
| 进程资源限制 | 无内存/CPU/进程数上限，fork bomb 或内存泄漏可拖垮系统 |
| 风险分级 | 只有 readonly/dangerous/unknown 三态，缺少 MEDIUM 级别 |
| 文件快照/回滚 | 高风险操作前无备份，误操作不可逆 |
| 网络隔离 | 沙箱内进程可自由访问网络 |
| 审计日志 | 无操作追踪，事后无法审计 |
| 沙箱感知放行 | 沙箱开启后仍需用户逐条确认中等风险命令 |

---

## 二、架构设计

```
BashTool.call()
    │
    ├─ RiskScorer.score_command() → LOW/MEDIUM/HIGH/CRITICAL
    │
    ├─ [LOW] → 直接放行，普通执行
    │
    ├─ [MEDIUM + sandbox_enabled] → 自动放行，沙箱内执行
    │
    ├─ [HIGH] → 文件快照 + 沙箱执行
    │
    └─ [CRITICAL] → 用户确认 + 文件快照 + 沙箱执行
```

### 子模块

| 模块 | 文件 | 职责 |
|---|---|---|
| 风险评分器 | `agent/core/sandbox/risk_scorer.py` | 四级风险分类（LOW/MEDIUM/HIGH/CRITICAL） |
| 沙箱执行器 | `agent/core/sandbox/executor.py` | 跨平台资源限制执行（Win Job Object / Linux rlimit / macOS sandbox-exec） |
| 文件保护 | `agent/core/sandbox/file_guard.py` | 快照创建/回滚/自动清理 |
| 审计日志 | `agent/core/sandbox/audit.py` | JSONL 格式操作追踪 |

---

## 三、各模块详细设计

### 3.1 风险评分器（risk_scorer.py）

四级风险：
- **LOW**：只读命令（ls/cat/grep/git status 等）→ 直接放行
- **MEDIUM**：有副作用但可逆（git commit/npm install/python script.py）→ 沙箱开启时自动放行
- **HIGH**：不可逆操作（rm/del/git push --force/chmod）→ 强制沙箱 + 文件快照
- **CRITICAL**：系统级破坏（rm -rf/sudo/mkfs/format/注册表）→ 沙箱 + 快照 + 用户确认

### 3.2 沙箱执行器（executor.py）

跨平台实现（无第三方依赖）：

**Windows — Job Object（ctypes 调用 Win32 API）：**
- `JOB_OBJECT_LIMIT_PROCESS_MEMORY`：内存上限
- `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`：进程数上限（防 fork bomb）
- `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`：关闭 Job 时终止所有进程树
- 网络隔离：通过设置无效 HTTP_PROXY 环境变量阻断
- 超时后 `proc.kill()` + Job Object 自动清理子进程

**Linux — resource.setrlimit（标准库）：**
- `RLIMIT_AS`：最大虚拟内存（字节）
- `RLIMIT_CPU`：最大 CPU 时间（秒）
- `RLIMIT_NPROC`：最大进程数
- 通过 `preexec_fn` 在 fork 后、exec 前设置限制
- 资源超限检测：SIGKILL(-9/137) / SIGXCPU(-24/152)

**macOS — sandbox-exec + rlimit：**
- 优先使用 `/usr/bin/sandbox-exec` 命令包装（Apple Sandbox）
- 同时叠加 resource.setrlimit 资源限制
- 注意：sandbox-exec 在 macOS 14+ 标记 deprecated 但仍可用

### 3.3 文件保护（file_guard.py）

- 快照存储：`~/.jarvis/sandbox_snapshots/<timestamp>_<hash>/`
- 每个快照含 manifest.json + 备份文件
- 大文件（>50MB）只记录元数据不备份
- 自动清理：保留最近 N 个（默认 20）
- 回滚：恢复原始内容 / 删除新建文件

### 3.4 审计日志（audit.py）

- 格式：JSON Lines（`~/.jarvis/sandbox_audit.jsonl`）
- 事件类型：execution / violation / snapshot / rollback / permission
- 自动轮转：超过 500 条截断旧记录
- 统计接口：get_stats() 返回风险分布/违规次数等

---

## 四、配置项汇总

```toml
[sandbox]
enabled = false              # 总开关
max_memory_mb = 512          # 沙箱内最大内存（MB）
max_cpu_seconds = 60         # 沙箱内最大 CPU 时间（秒）
max_processes = 10           # 沙箱内最大子进程数
timeout = 120                # 沙箱命令总超时（秒）
block_network = false        # 是否阻断沙箱内网络
auto_allow_medium = true     # 沙箱开启时自动放行中等风险
audit = true                 # 记录审计日志
max_snapshots = 20           # 文件快照最大保留数
excluded_commands = []       # 不走沙箱的命令（如 ["docker", "wsl"]）
```

---

## 五、验收标准

- [x] 风险评分器正确分类 13 种典型命令
- [x] Windows Job Object 创建成功，命令在沙箱内执行（sandboxed=True）
- [x] Linux rlimit 沙箱实现（RLIMIT_AS/CPU/NPROC + preexec_fn）
- [x] macOS sandbox-exec + rlimit 实现
- [x] 文件快照创建→修改→回滚→内容恢复
- [x] 审计日志写入/读取/统计/轮转
- [x] 配置加载正确（settings.toml [sandbox] 段）
- [x] BashTool 集成：沙箱开启时中等风险自动放行
- [x] 所有文件 py_compile 通过
- [x] 导入测试通过

---

## 六、风险与回退

| 风险 | 应对 |
|---|---|
| Job Object 创建失败 | 自动回退普通执行（不阻塞用户） |
| 沙箱内命令行为异常 | 审计日志追踪 + 文件快照可回滚 |
| 性能开销 | 仅 MEDIUM+ 命令走沙箱，LOW 直接执行 |
| Linux RLIMIT_NPROC 不可用 | try/except 静默跳过，其他限制仍生效 |
| macOS sandbox-exec deprecated | 仍可用；不可用时回退纯 rlimit |

回退方案：`settings.toml` 中 `[sandbox] enabled = false` 即完全关闭。

---

## 七、涉及文件

**新增：**
- `agent/core/sandbox/__init__.py`
- `agent/core/sandbox/risk_scorer.py`
- `agent/core/sandbox/executor.py`
- `agent/core/sandbox/file_guard.py`
- `agent/core/sandbox/audit.py`
- `tests/test_p38_sandbox.py`

**修改：**
- `agent/tools/bash.py` — 集成沙箱执行 + 风险感知权限
- `agent/config/settings.py` — 新增 11 个 sandbox_* 配置字段
- `configs/settings.toml` — 新增 [sandbox] 配置段
- `docs/roadmap/jarvis-upgrade-roadmap.md` — P3-8 标记 ✅
