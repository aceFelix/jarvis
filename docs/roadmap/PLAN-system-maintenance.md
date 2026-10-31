# 系统维护三件事（清理 / 查毒 / 性能诊断）— 升级方案

> 对应 [BLUEPRINT 第 6.1 / 6.2 / 6.4 节](../blueprint/BLUEPRINT.md) 与第 11 节验收条目。
> **一句话原则：不自研引擎，只做「扫描编排 + 上下文判断 + 可撤销执行」。**

> ## ✅ 交付状态（2026-10-31）
>
> **B1 + B2 + B3 + B4 四批全部落地**，本文件从“计划稿”转为“已交付计划”。
> 代码实现说明见 [19-系统维护层](../architecture/19-系统维护层.md)，
> 实机走查清单见 [系统维护层分册](../test/TEST_CHECKLIST-system-maintenance.md)（T-397–T-427）。
>
> | 批次 | 内容 | 状态 |
> |---|---|---|
> | B1 | 规则库 + 扫描 + `DiskReport` + `/disk` | ✅ |
> | B2 | 隔离区 + `DiskClean` + `/clean` + 还原 | ✅ |
> | B3 | Defender 编排 + 异步扫描 | ✅ |
> | B4 | 体检聚合 + `SystemHealth` + `/health` | ✅ |
>
> 单测 112 例；全量回归 4030 passed / 1 skipped；本机（Windows 10 22H2、非管理员、
> Defender 被第三方接管）跑穿隔离区闭环与七区块体检。
>
> **三处与计划稿的偏离（均已就地记入实现与文档）**：
> 1. **隔离区用移动，不用复制式快照**（§4.3 原稿写“快照”）。`FileGuard` 是复制式且阈值只 50MB，
>    GB 级垃圾复制一份等于先把盘占满，与清理目的直接矛盾。撤销入口是 `/clean --restore <批次>`，
>    **不是 `/undo`**——隔离区批次是独立于 W3 副作用账本的另一套记录，本文件 §七 写的 `/undo <batch_id>` 已纠正。
> 2. **补上了计划未覆盖的 `top_dirs`（`/disk --top`）**。实测本机可回收只有 27MB，而 C 盘
>    100GB 只剩 14.6GB（85.4% 占用）——用户真正问的是“空间被谁占了”，不是“垃圾有多少”。
> 3. **杀软权威源改为 `root/SecurityCenter2` 的 `productState` 位**，`Get-MpComputerStatus`
>    降为补充。只看后者会对装了第三方杀软的用户**误报「没有实时防护」**（本机实测：Defender 被
>    「电脑管家系统防护」接管后，该命令照常返回且 `AntivirusEnabled=false`）。这是本批最有价值的发现。
>
> **本期不做**：`Remove-MpThreat` 一键处置威胁（不可逆动作，入口先留在 Defender 自身 UI）、
> 自研查杀引擎 / ClamAV（本文件 §十.4 已拍板不做）、自动杀进程、自动提权。
> 遗留：网络连通性巡检（BLUEPRINT 6.1 缺口里唯一没补的一项）。

---

## 一、现有基础分析

| 能力 | 就绪度 | 现有实现 | 可直接复用 |
|---|---|---|---|
| 资源监控 | ⭐⭐ | `agent/core/percept/source_system.py`（psutil，CPU/内存/磁盘三档阈值、600s 冷却、`disk_trend_days` 趋势） | 阈值、salience 分级、告警冷却 |
| 磁盘趋势 | ⭐ | 同上，`disk_trend_days` | **回答"磁盘为什么在涨"** |
| 文件操作可回滚 | ⭐⭐ | `agent/core/sandbox/file_guard.py` 的 `FileGuard` + `Snapshot` | 删除前快照、按轮回滚 |
| 风险分级 | ⭐⭐ | `agent/core/sandbox/risk_scorer.py` 的 `RiskLevel` | 区分「只看」与「要确认的删除」 |
| 审计留痕 | ⭐⭐ | `agent/core/sandbox/audit.py` 的 `SandboxAuditor` | 每次清理可追溯 |
| 命令执行 | ⭐⭐ | `Bash` + `SandboxExecutor` | 调 PowerShell / Defender 的通道 |
| 体检表格框架 | ⭐⭐ | `agent/doctor.py`（依赖体检，非系统体检） | 检查项抽象、状态渲染 |
| **垃圾目录知识库** | ☆ | 无 | — |
| **磁盘扫描/统计工具** | ☆ | 无 | — |
| **Defender 编排** | ☆ | 无 | — |
| **启动项/进程诊断** | ☆ | 无（`source_system` 只做阈值告警） | — |

**结论：骨架齐了，缺的是「规则库 + 三个工具 + 一层 Defender 编排」。**

---

## 二、成熟方案盘点（"市面上有没有"的答案）

### 2.1 清理垃圾

| 方案 | 可用性判断 |
|---|---|
| **BleachBit**（CleanerML 声明式规则） | **只借格式，不借规则**。它的规则集面向 Linux（`~/.cache/mozilla`、`/var/tmp` 这类），Windows 系统级垃圾（`Windows.old`、`SoftwareDistribution`、WinSxS）根本不在其覆盖范围内 |
| **Windows 自带磁盘清理** `cleanmgr` | 可编排，但只能吃预设 profile，粒度粗、不可编程列举 |
| **Dism /Cleanup-Image /StartComponentCleanup** | ⭐ 真正有效（WinSxS 常省 2–5 GB），**必须走这条** |
| **存储感知**（Storage Sense API） | 可读状态，但触发时机不可控，不适合编排 |
| 360 / 腾讯管家 | **反面教材**，不参考其产品逻辑（见第八节三个坑） |

> 通用开源里**没有**一个能直接覆盖 Windows 系统级垃圾、又安全可编排的清理器。所以规则表必须自己写——但格式照抄 CleanerML 那套声明式思路，好处是将来能社区共维护。

### 2.2 查毒

| 方案 | 判断 |
|---|---|
| **Defender PowerShell 模块**（`Defender`） | **首选**。`Get-MpComputerStatus` / `Start-MpScan` / `Get-MpThreatDetection` / `Update-MpSignature`，能查能扫能读历史，全都是结构化对象 |
| Defender `MpCmdRun.exe` | 老命令行，功能面窄、输出是非结构化文本。**降为兜底** |
| ClamAV | 可作为第二意见，但 Windows 上需另装、特征库要单独维护，与 Defender 实时防护有互扫风险。**仅手动触发，不进自动流程** |
| 自研特征库 | **明确不做**（见第八节坑 3） |

### 2.3 性能诊断（"加速"的正解）

| 维度 | 有效做法 | 无效/有害做法 |
|---|---|---|
| 开机慢 | 列启动项（`Get-CimInstance Win32_StartupCommand` + 注册表 Run 键），让用户决定禁用哪些 | 自作主张禁用 |
| 内存高 | `Get-Process` 按 WorkingSet 排序，指出大户并问「要不要关」 | 一键杀进程 |
| 磁盘满 | 见 2.1 | — |
| 系统卡 | 磁盘队列长度、CPU 占用 Top N、是否有杀软全盘扫描在跑 | "优化大师"式注册表清理 |
| SSD/HDD | SSD 走 TRIM（`Optimize-Volume -ReTrim`）；HDD 才需要碎片整理 | 对 SSD 做"碎片整理"（纯属磨损） |

> **「加速」这个词在 Win10/11 上基本是产品噱头。** Windows 自己的进程调度、内存管理已经够好；所谓"加速球"提供的是心理满足，不是性能。
> **本方案把它改名为「性能诊断」**：只负责**发现 + 解释 + 建议**，执行权永远在用户手里。

---

## 三、架构设计

```
                    ┌─────────────────────────────┐
                    │  cleaners.toml（声明式规则）  │
                    │  位置 / 类别 / 风险 / 说明    │
                    └──────────────┬──────────────┘
                                   │
   ┌───────────────┬───────────────┴───────────────┬────────────────┐
   │  DiskReport   │        DiskClean              │  SystemHealth  │
   │   (只读扫描)   │       (可撤销清理)             │  (体检/诊断)    │
   └───────┬───────┴───────────────┬───────────────┴────────┬───────┘
           │                       │                        │
           │            ┌──────────┴─────────┐     ┌────────┴────────┐
           │            │  FileGuard 快照     │     │ PowerShell 通道  │
           │            │  隔离区（可还原）    │     │ Defender 模块    │
           │            └──────────┬─────────┘     └─────────────────┘
           │                       │
           └───────────┬───────────┴────────────┐
                       │                        │
              ┌────────┴────────┐     ┌─────────┴────────┐
              │   ToolContext   │     │ SandboxAuditor   │
              │  (会话上下文)    │     │   (留痕)          │
              └─────────────────┘     └──────────────────┘
```

### 子模块

| 模块 | 路径 | 职责 |
|---|---|---|
| 规则库 | `agent/config/cleaners.toml` + `agent/core/maintenance/rules.py` | 声明式垃圾规则，解析与校验 |
| 扫描器 | `agent/core/maintenance/scanner.py` | 遍历、大小统计、分类、最后修改时间 |
| 清理器 | `agent/core/maintenance/cleaner.py` | 走快照/隔离区执行删除 |
| 体检器 | `agent/core/maintenance/health.py` | 聚合系统指标 + Defender 状态 + 启动项 |
| 工具层 | `agent/tools/extensions/maintenance.py` | `DiskReport` / `DiskClean` / `SystemHealth` 三个 Tool |
| 命令层 | `agent/commands/handlers/system_commands.py` | `/disk`、`/clean`、`/health` |

> 单文件 ≤ 900 行的约束下，扫描/清理/体检拆三个文件而不是合成一个。

---

## 四、各模块详细设计

### 4.1 规则库 `cleaners.toml`

**借 CleanerML 的"声明式"思路，规则内容全部自写**（面向 Windows）。每条规则必须包含 `说明` 字段——**给用户看的，不是给机器看的**。

```toml
[[cleaner]]
id = "user_temp"
name = "当前用户临时文件"
paths = ["%LOCALAPPDATA%\\Temp"]
category = "temp"
risk = "low"          # low=可直接问；medium=要说明后果；high=不建议自动
requires_admin = false
# 说明会原样展示给用户，解释"删了会怎样"
desc = "程序运行时的临时文件。删除安全，但正在运行的程序占用的文件会自动跳过。"
# 保护规则：命中即跳过，绝不删
protect = ["*.log", "*.tmp.lock", "*.db", "*.db-wal"]

[[cleaner]]
id = "win_update_cache"
name = "Windows 更新缓存"
paths = ["%SYSTEMROOT%\\SoftwareDistribution\\Download"]
category = "system"
risk = "medium"
requires_admin = true
desc = "已下载的更新安装包。删除后 Windows 需要时会重新下载，不会影响已安装的更新。"
```

**字段约束**：

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | ✅ | 稳定标识，账本与审计都用它 |
| `paths` | ✅ | 只允许**白名单目录**，拒绝通配符开头的路径 |
| `risk` | ✅ | `low/medium/high`，决定是否需要二次确认 |
| `requires_admin` | ✅ | 提前告知而非执行时才失败 |
| `desc` | ✅ | **人话说明**，强制字段 |
| `protect` | ⬜ | 保护模式（命中即跳过） |
| `exclude_if_active` | ⬜ | 目录是否正被本次会话使用（见 4.5） |

**首批规则清单（Windows 实际有效的）**：

| id | 位置 | 典型体积 | 风险 |
|---|---|---|---|
| `user_temp` | `%LOCALAPPDATA%\Temp` | 1–3 GB | low |
| `win_temp` | `%SYSTEMROOT%\Temp` | 0.5–2 GB | low（需管理员） |
| `win_update_cache` | `%SYSTEMROOT%\SoftwareDistribution\Download` | 2–8 GB | medium（需管理员） |
| `windows_old` | `C:\Windows.old` | 10–20 GB | medium（需管理员，升级残留） |
| `winsxs_cleanup` | `Dism /StartComponentCleanup` | 2–5 GB | medium（需管理员） |
| `recycle_bin` | 回收站 | 不定 | low |
| `browser_cache` | Chrome/Edge 缓存目录 | 0.5–3 GB | **high**（会掉登录态，默认不勾） |
| `dev_cache_pip` | `%LOCALAPPDATA%\pip\cache` | 0.5–2 GB | **high**（清了下次构建变慢） |
| `dev_cache_npm` | `%APPDATA%\npm-cache` | 0.5–3 GB | **high**（同上） |
| `prefetch` | `%SYSTEMROOT%\Prefetch` | 几十 MB | **不收录**（删了前几次启动反而慢） |
| `node_modules` | 按项目发现 | 每项目几百 MB | **不自动收录**，只在上下文命中时建议 |

> 最后两行是刻意排除的：收益小、副作用大，收了就是给自己挖坑。

### 4.2 `DiskReport` 工具（L1 · 只读）

**输入**：`{"scope": ["user_temp", "dev_cache"] | "all", "min_size_mb": 100}`

**输出**（结构化 + 人话）：

```json
{
  "total_reclaimable_mb": 12450,
  "drives": [{"mount": "C:", "total_gb": 476, "free_gb": 23, "trend_7d_gb": 8.4}],
  "items": [
    {
      "id": "windows_old",
      "name": "旧版 Windows 安装残留",
      "size_mb": 14200,
      "risk": "medium",
      "requires_admin": true,
      "desc": "系统升级前的旧版本备份。删除后无法回退到上一版 Windows。",
      "last_modified": "2026-04-11",
      "in_use_by_session": false
    }
  ]
}
```

**关键点**：

- **`trend_7d_gb` 来自 `source_system` 已有的 `disk_trend_days`** —— 这是 360 答不了的问题
- **`in_use_by_session`**：拿会话历史/感知总线的窗口信息比对，标注「这个目录本次会话刚用过」
- 扫描本身要有**超时与大小上限**（默认跳过 >50 GB 的单目录，避免卡死）

### 4.3 `DiskClean` 工具（L2 · 可撤销）

**执行契约**（硬性，缺一不可）：

1. **必须显式列出 `ids`**，不接受 `all: true`
2. **先 `FileGuard` 快照**，再删除
3. **删除走隔离区**（不用 `Remove-Item -Force` 直删）：移动到 `~/.jarvis/quarantine/<批次id>/`，保留 7 天后自动清理
4. **`risk != low` 的条目必须经过确认**（走既有五层权限管线的确认档）
5. **写 `SandboxAuditor`**：批次 id、条目、字节数、耗时
6. **报告「跳过」而非只报「成功」**：被占用的文件、`protect` 命中的、权限不足的，全部列出来

**返回值示例**：

```json
{
  "batch_id": "clean-20261029-143022",
  "reclaimed_mb": 9820,
  "items": [{"id": "win_update_cache", "freed_mb": 6100, "status": "ok"}],
  "skipped": [
    {"path": "...\\Temp\\~DF3A2.tmp", "reason": "文件被占用"},
    {"path": "...\\Temp\\app.lock", "reason": "命中保护规则 *.tmp.lock"}
  ],
  "undo_hint": "如需还原：/undo clean-20261029-143022"
}
```

> **`undo_hint` 是这套方案和所有管家软件的根本区别**——它敢删，是因为删了能拿回来。

### 4.4 `SystemHealth` 工具（L3 · 体检/诊断）

聚合四块，**只报告不处置**（除用户显式要求）：

| 区块 | 数据源 | 输出 |
|---|---|---|
| 资源 | `source_system` 现有快照 | CPU/内存/磁盘使用率 + 趋势 |
| 磁盘健康 | `Get-PhysicalDisk` / SMART（`smartmontools` 可选） | 健康状态、通电时长、剩余寿命 |
| 安全 | `Get-MpComputerStatus` | 实时防护开关、特征库日期、上次扫描时间 |
| 性能热点 | `Get-Process` 按 WorkingSet/CPU 排序 Top 10 | 进程名 + 占用 + **是否本次会话启动的** |
| 启动项 | `Get-CimInstance Win32_StartupCommand` + Run 注册表 | 名称/命令/来源/是否可禁用 |

**明确不做**：

- ❌ 自动结束进程（只给建议，用户点头才动）
- ❌ 自动禁用启动项（同上）
- ❌ 注册表"优化"（收益为负）

### 4.5 Defender 编排（`SystemHealth` 的 action 分支）

| 动作 | 命令 | 权限 |
|---|---|---|
| 查状态 | `Get-MpComputerStatus` | 普通 |
| 更新特征库 | `Update-MpSignature` | 普通（可能需管理员） |
| 快速扫描 | `Start-MpScan -ScanType QuickScan` | 普通 |
| 指定路径扫描 | `Start-MpScan -ScanType CustomScan -ScanPath <p>` | 普通 |
| 读检测历史 | `Get-MpThreatDetection` + `Get-MpThreat` | 普通 |
| 处置威胁 | `Remove-MpThreat -ThreatID <id>` | **管理员 + 强制确认** |

**长任务处理**：全盘扫描可能跑一小时以上 → **异步执行 + 进度查询**，不能阻塞主循环。复用既有的后台命令机制（`Bash` 的后台句柄模式）。

**结果解读**：把 Defender 的结构化输出翻成人话，例如「最近 30 天拦截了 3 次，两次是同一个下载器（Trojan:Win32/Wacatac），已自动隔离」。

### 4.6 与既有设施的接线

| 依赖 | 用法 |
|---|---|
| `FileGuard` | `DiskClean` 前建快照；隔离区本身也是快照的延伸 |
| `RiskScorer` | 规则 `risk` 字段映射到 `RiskLevel` |
| `SandboxAuditor` | 每次清理写审计条目 |
| W3 副作用账本 | `DiskClean` 声明 `is_irreversible=False`（因有隔离区可还原），从而**免走 30s 延时窗口**——这是账本的正确用法：有补偿手段就不该冒充不可逆 |
| `source_system` | 复用阈值与趋势，不重复采样 |
| `ToolContext.messages` | 判断 `in_use_by_session` |

---

## 五、分批交付（升级说明）

**每批独立可用、独立验收，不依赖下一批。**

### B1 · 只读诊断（约 1.5 天）— **风险为零，先做这个**

- [ ] `cleaners.toml` 规则库 + `rules.py` 解析校验（含 `protect` 规则）
- [ ] `scanner.py`：目录大小统计（带超时、大小上限、跳过符号链接）
- [ ] `DiskReport` 工具 + `/disk` 命令
- [ ] 输出**人话清单**，每项带 `desc`
- [ ] 单测：规则解析、白名单校验、超时、`protect` 命中

**交付价值**：**回答「我 C 盘为什么满了」**。大多数用户的真实需求在这一批就满足了。

### B2 · 可撤销清理（约 2 天）

- [ ] `cleaner.py`：隔离区 + 快照 + 审计
- [ ] `DiskClean` 工具 + `/clean` 命令
- [ ] 确认档接入（`risk != low` 要问）
- [ ] `/undo <batch_id>` 还原
- [ ] 单测：隔离区移动、占用文件跳过、快照回滚、审计条目

**前置**：W3 账本已就位（✅ 已满足）。**先定白名单规则再开工**，不把账本当垃圾桶逆向按钮用。

### B3 · Defender 编排（约 1 天）

- [ ] `health.py` 的 security 区块
- [ ] 四个动作（查状态/更新库/快速扫描/读历史）+ 异步执行
- [ ] 结果翻译成人话
- [ ] 单测：用假 PowerShell 输出锁定解析（不依赖真机）

**前置**：无。

### B4 · 性能诊断（约 1.5 天）

- [ ] 磁盘 SMART 健康
- [ ] 进程热点 Top N（标注是否本次会话启动）
- [ ] 启动项清单
- [ ] `SystemHealth` 工具 + `/health` 命令
- [ ] **体检报告汇总**（一条命令生成，可存 markdown）
- [ ] 单测：各区块解析、Top N 排序

**交付价值**：对应 BLUEPRINT 第 11 节「健康体检报告可一条命令生成」。

---

## 六、配置项

```toml
[maintenance]
# 清理总开关（默认关：安全优先）
enabled = false
# 规则文件（留空用内置）
rules_file = ""
# 隔离区保留天数
quarantine_days = 7
# 单目录扫描上限，超过则跳过并提示（防卡死）
scan_max_dir_gb = 50
# 扫描总超时（秒）
scan_timeout = 120
# 只读诊断最低展示阈值
report_min_mb = 100
# 允许自动清理的最高风险等级：low / medium / high
auto_clean_max_risk = "low"
# Defender 扫描是否允许全盘（默认只给快速）
allow_full_scan = false
```

---

## 七、验收标准

对应 BLUEPRINT 第 11 节「电脑管家八件事」条目：

- [ ] **健康体检报告**（CPU/内存/磁盘/温度/电池/启动项 + 趋势）可一条命令生成
- [ ] **清理有规则表 + 预览 + 隔离区**，且绝不碰项目文件
- [ ] **杀毒走 Defender 编排并可解读结果**
- [ ] 清理误删可从隔离区还原（`/undo` 实测通过）
- [ ] 扫描期间常驻 CPU < 5%（对比 W2 的 < 1% 门槛，扫描属主动任务，放宽）
- [ ] 所有删除动作在 `SandboxAuditor` 有记录

**三条"绝不"（验收时逐条对照）**：

1. 绝不删除任何**不在白名单目录**内的文件
2. 绝不**自动**结束进程 / 禁用启动项
3. 绝不自研查杀引擎

---

## 八、风险与回退

### 三个必须避开的坑

**坑 1：照抄"一键清理"** → 信任崩塌

删掉浏览器缓存（登录态全掉）、开发缓存（下次构建慢十倍）、`%TEMP%` 里正在用的锁文件。**用户会在三天内卸掉它。**

**规避**：`risk` 分级 + 默认不勾 high + 隔离区可还原 + 每条规则必带人话说明。

**坑 2：把"加速"做成"杀进程"**

被当成"无用进程"杀掉的，可能是跑了两小时的编译、导入到一半的模型、正在保存的文档。

**规避**：只报告不处置；`SystemHealth` 的进程区块**没有 kill 参数**。

**坑 3：自研杀毒引擎**

Defender 免费、在线更新、误报率低。自研特征库除了拖慢电脑没有任何收益。

**规避**：只做编排 + 解读；ClamAV 仅作手动第二意见。

### jarvis 的差异化在哪

**PC 管家软件是上下文盲的**：见 `node_modules` 就说是垃圾，见 `Downloads` 里的 ISO 就说是垃圾文件。

**jarvis 有感知总线 + 会话历史**，能判断：这个 `target/` 是半小时前刚构建的产物、这个 `node_modules` 属于正在开发的项目、这个 4GB ISO 是上周下载还没用。

所以产出应该是**条件化建议**而不是无条件清单：

> 「`Downloads` 占 12.4 GB：4 GB 系统镜像是三个月前的、2 GB 显卡驱动是上周的、6 GB 是这周的工作文件 —— **前三项可清，最后一项建议留**。」

这条能力落在 `4.2` 的 `in_use_by_session` 字段和 `4.1` 的 `exclude_if_active` 上。

### 回退

- 规则表出问题 → `[maintenance].enabled = false` 一键关闭，不影响其它功能
- 清理误删 → 隔离区 7 天保留 + `/clean --restore <batch_id>`（**已实现**；
  原稿写的 `/undo <batch_id>` 是错的——`/undo` 走 W3 副作用账本的 `EffectRecord`，
  隔离区批次是另一套记录，敲进去注定落空）
- Defender 调用异常 → 降级为「只报告状态不可操作」，不阻塞体检

---

## 九、涉及文件

**新增**

| 文件 | 说明 |
|---|---|
| `agent/config/cleaners.toml` | 声明式清理规则库 |
| `agent/core/maintenance/__init__.py` | 包聚合（只做再导出） |
| `agent/core/maintenance/rules.py` | 规则解析、路径展开与三道白名单闸 |
| `agent/core/maintenance/scanner.py` | 目录体积统计 + 磁盘趋势 + `top_dirs`（后补） |
| `agent/core/maintenance/cleaner.py` | 隔离区、批次清单、可撤销删除与还原 |
| `agent/core/maintenance/psrun.py` | **后补**：PowerShell 执行通道（编码 / JSON 深度 / 提权降级收口） |
| `agent/core/maintenance/defender.py` | **拆分**：原稿把 Defender 编排全塞进 `health.py`，591 → 621 行逼近上限，拆出独立模块 |
| `agent/core/maintenance/health.py` | 体检聚合（七区块）与 Markdown 报告 |
| `agent/tools/extensions/maintenance.py` | `DiskReport` / `DiskClean` / `SystemHealth` |
| `agent/commands/handlers/system_commands.py` | `/disk` `/clean` `/health` |
| `tests/core/test_cleaner_rules.py` | 规则解析、白名单、保护模式（27 例） |
| `tests/core/test_disk_scanner.py` | 扫描、超时、大小上限（15 例 + 1 skip） |
| `tests/core/test_disk_cleaner.py` | 隔离区、还原、跳过原因（19 例） |
| `tests/core/test_system_health.py` | productState 解码、提权降级、各区块解析（26 例） |
| `tests/tools/test_maintenance_tools.py` | 三个工具的契约（24 例） |

**修改**

| 文件 | 改动 |
|---|---|
| `agent/tools/__init__.py` | 注册三个新工具（`DiskClean` 归延迟工具，`DiskReport`/`SystemHealth` 可进核心）。实际改在 `agent/core/tool.py` 的 `build_default_registry()` |
| `agent/commands/router.py` | 注册 `/disk` `/clean` `/health` 三条命令 |
| `agent/config/settings.py` + `settings_domains.py` | `[maintenance]` 配置域 |
| `agent/core/percept/source_system.py` | 暴露 `trend_7d_gb` 给体检报告复用 |
| `configs/settings.example.toml` + `agent/configs/settings.example.toml` | 示例配置（**两份都要改**，历史上有过双向漂移） |
| `config-docs/configuration.md` | 配置项说明 |
| `docs/architecture/` 新增一篇 | 系统维护层架构 |
| `docs/test/TEST_CHECKLIST.md` | 新增用例编号 |
| `docs/blueprint/BLUEPRINT.md` | 6.1/6.2/6.4 落点、6.9 优先级、第 11 节打钩 |

---

## 十、开工前的待确认项（已全部拍板）

> 四项均已在 2026-10-31 实施中拍板，结论与理由写在每条末尾。

1. **是否真的需要 B2（删除能力）？** 建议 B1 上线跑一周，看「只报告」是否已满足九成需求 —— 多数时候人想知道的是"钱花哪了"，不是"帮我花掉"。
   → **拍板：做了**，但默认关（`enabled = false`），且这个开关只拦 `/clean` 不拦 `/disk`。
   实测支持了原判断：本机可回收只有 27MB，而 C 盘只剩 14.6GB——所以补了 `top_dirs`。
2. **管理员权限怎么办？** 白名单里约一半需要提权（18 条中 8 条）。三个选项：(a) 只报告不动手；(b) 提示用户手动提权；(c) 走 `Start-Process -Verb RunAs` 拉起。**建议先 (a)**。
   → **拍板：(a)**。`need_admin=True` + “需要以管理员身份运行才能读取”如实标注；不弹 UAC。
3. **`[maintenance].enabled` 默认关闭**是否可接受？关闭状态下 `/disk` 仍可用（只读），`/clean` 报「未启用」。
   → **拍板：接受**，已按此实现，单测 2 例锁死这个不对称（T-402 / T-403）。
4. **ClamAV 要不要做？** 建议不做，Defender 已足够，多一个引擎多一份维护成本。
   → **拍板：不做**。另外顺带发现：连 `Get-MpComputerStatus` 都不能当唯一真相源（见文件头交付状态第 3 条）。
