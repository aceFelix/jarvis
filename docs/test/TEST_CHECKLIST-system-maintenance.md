# 系统维护层实机走查清单（清理 / 查毒 / 性能诊断）

> 分册说明：主清单 [TEST_CHECKLIST.md](TEST_CHECKLIST.md) 已接近 900 行上限，且其文首
> 明确要求“后续新增成体系的新模块请另立分册”，故本组用例独立成册。
> **编号与主清单连续（T-397–T-433），不重号**。
>
> 机制与设计取舍详见 [19-系统维护层](../architecture/19-系统维护层.md)；
> 计划文档 [PLAN-system-maintenance](../roadmap/PLAN-system-maintenance.md)；
> 配置项见 [配置指南·系统维护层](../../config-docs/configuration.md)。
>
> 自动化单测（**139 例，已全绿**）：
> `tests/core/test_cleaner_rules.py`（29）、`tests/core/test_disk_scanner.py`（19 + 1 skip）、
> `tests/core/test_disk_cleaner.py`（19）、`tests/core/test_system_health.py`（28）、
> `tests/tools/test_maintenance_tools.py`（26）、`tests/core/test_auto_clean.py`（18）。
> 本册是**实机人工走查**那一半 —— 单测能钉住逻辑，钉不住“真机上 PowerShell 到底怎么回”。
>
> ⚠️ 所有用例都在临时目录或系统临时目录跑，不碰真实目录；带「会动文件」标记的
> T-405 / T-409 两条会真的移动和还原文件，先看清楚再跑。
>
> @author aceFelix

---

## 37.1 磁盘诊断（`/disk`，纯只读）

| 编号 | 测试目的 | 测试步骤 | 通过标准 |
|------|---------|---------|---------|
| T-397 | 总览可读 | `[maintenance].enabled = false` 下直接 `/disk` | **照常输出**（只读不读开关）；列出各盘总容量/已用/百分比 + 可回收汇总 |
| T-398 | 体积排行回答“被谁占了” | `/disk --top`（不带参数查系统盘） | 下半段出现一级目录体积排行；末尾明说“这些目录不在清理白名单内，`/clean` 不会碰它们” |
| T-399 | 小项与截断诚实 | `/disk` 对照 `/disk --all`；找一个大目录把 `scan_max_dir_gb = 0.001` 临时改小后重扫 | `--all` 多出小项；截断项标“已截断”而不是假装扫完 |
| T-400 | 命令型规则不代执行 | `/disk --deep` | 列出 WinSxS / 卷影 / 休眠 / 磁盘清理四条，每条都附**要执行的命令**与提示，无任何执行动作 |
| T-401 | 规则库拒非法写法 | 往 `cleaners.toml` 加一条 `paths = ["C:\\\\"]` / `paths = ["C:/Users/*"]` / 无 `desc` 的规则，重跑 `/disk` | 该条进 `rejected` 并在输出里提示原因；**其余规则照常工作**，不整库崩 |
| T-428 | 磁盘清理前提交代清楚 | `/disk --deep` 看 `cleanmgr_profile` 那一段 | 说明里明写「必须先手动跑一次 `cleanmgr /sageset:64`」；提示行是「无分析模式」（区别于其它三条的「需执行分析命令」） |
| T-429 | 白名单守门 | 往 `cleaners.toml` 加一条 `clean_cmd = ['powershell.exe', '-c', 'rm -rf']` 的规则，重跑 `/disk` | 该条被拒、原因含“不在白名单”；`cleanmgr_profile` 照常加载（白名单是精确等值，不因外部写法松动） |

## 37.1b 无人值守自动清理

| 编号 | 测试目的 | 测试步骤 | 通过标准 |
|------|---------|---------|---------|
| T-430 | 自动名单只放零感知的 | `/clean --auto-status` | 19 条里**只有** `crash_dumps` / `wer_reports` 打钩；`recycle_bin`、`user_temp`、`thumbcache`、`shader_cache` 均不钩且理由为「未标记 auto_ok」；命令型四条的理由是「从不代执行」；需管理员的理由是「提不了权」 |
| T-431 | `auto_ok` 反向闸 | 往 `cleaners.toml` 分别给**命令型**规则和 `requires_admin = true` 的规则加 `auto_ok = true`，重跑 `/clean --auto-status` | 两条均被拒并在 `rejected` 里给原因（“命令型不可还原” / “后台进程提不了权”）；**其余规则照常加载**，不整库崩 |
| T-432 | 阈值不建批次 | `auto_clean_min_mb = 99999` 后跑 `/clean --auto`，再 `/clean --batches` | 输出为「低于阈值，不值得建批次」；**没有新批次产生**；`~/.jarvis/maintenance_state.json` 的 `last_auto_clean_ts` 不更新 |
| T-433 | 自动清的必须能还原 | 手动造一个 `%LOCALAPPDATA%\CrashDumps\x.dmp`（几 MB），`auto_clean_min_mb = 0` 后跑 `/clean --auto` 拿到批次号，再 `/clean --restore <批次号>` | 文件先被移走、随后原样回到原位；说明自动清理走的是隔离区而不是删除 |

## 37.2 清理与还原（`/clean`，会动文件）

| 编号 | 测试目的 | 测试步骤 | 通过标准 |
|------|---------|---------|---------|
| T-402 | 未启用时只读仍可用 | `enabled = false` 下 `/clean user_temp`（不带 `--yes`） | 只输出预览，**不报“未启用”**、不动文件 |
| T-403 | 未启用时禁止真清 | `enabled = false` 下 `/clean --yes user_temp` | 报“清理未启用：`[maintenance].enabled = false`”并给出改法；一个文件都没移 |
| T-404 | 预览不动文件 | `enabled = true`，`/clean user_temp` 后查隔离区 | `~/.jarvis/quarantine/` 无新批次；目标目录文件数不变 |
| T-405（会动文件） | 执行进隔离区 | `/clean --yes user_temp` | 输出批次号 + 释放体积 + 每项 moved 数；隔离区出现 `clean-YYYYMMDD-HHMMSS/manifest.json`，且清单里每个 `stored` 真实存在 |
| T-406 | 目录本身保留 | 同上后 `ls %LOCALAPPDATA%\\Temp` | 目录还在（只清内容）——目录消失后 Windows 不会重建，只会报怪错 |
| T-407 | protect 命中记原因 | 先手放一个 `keep.log` 到目标目录（命中 protect 模式）再清 | 文件还在，跳过列表里明写“命中保护规则”——**不许只报成功体积不说没删什么** |
| T-408 | 风险档上限 | `auto_clean_max_risk = "low"`，`/clean --yes browser_cache`（medium） | 拒绝执行并提示“超过当前允许上限”；文件未动 |
| T-409（会动文件） | 还原闭环 | `/clean --yes <id>` → `/clean --batches` → `/clean --restore <batch_id>` | 列批次能看到；还原后文件回原位；全部还原后**空壳批次被清掉**，`--batches` 不再列它 |
| T-410 | 还原不覆盖 | 清完后先往原位置放一个新同名文件，再 `--restore` | 新文件内容**不被旧内容盖掉**，跳过原因写“未覆盖” |
| T-411 | 隔离区不自我吞噬 | 把隔离区目录本身配成一条规则后 `--yes` | 拒绝处理，隔离区里已有文件一件不少 |
| T-412 | 命令型不在清理入口 | `/clean --yes winsxs_cleanup` | 拒绝并提示“命令型规则，本工具不执行（不可还原）”，给出需手动跑的命令 |

## 37.3 杀软与扫描（`/health security|threats|scan|status`）

| 编号 | 测试目的 | 测试步骤 | 通过标准 |
|------|---------|---------|---------|
| T-413 | 第三方接管不误报 | 装了第三方杀软的机器跑 `/health security`（本机为“电脑管家系统防护”） | 输出两个产品及启用态；结论为“由「XXX」接管实时防护”，**绝不出现“没有实时防护”** |
| T-414 | productState 解码 | 同上，对照注册表 `HKLM\\SOFTWARE\\Microsoft\\Windows Defender Security Center\\SecurityCenter2` 的 productState | 启用位 `0x1000` / 库位 `0x10` 解对（实测 266240=启用、393472=未启用） |
| T-415 | 威胁记录读不到不装安全 | 在 Defender 被接管的环境跑 `/health threats` | 返回原因 + “不代表没有防护”提示，**不返回空列表假装安全** |
| T-416 | 全盘默认拒 | `/health scan full`（`allow_full_scan = false`） | 报“全盘扫描需 `[maintenance].allow_full_scan = true`”；不开跑 |
| T-417 | 异步不阻塞 | `/health scan quick` 后立即继续对话 | 立即返回任务 id + pid；对话不被阻塞；`/health status <job_id>` 能查到进度 |

## 37.4 体检报告（`/health`，纯只读）

| 编号 | 测试目的 | 测试步骤 | 通过标准 |
|------|---------|---------|---------|
| T-418 | 七区块齐全 | `/health` | resource / disk / security / processes / startup / power / thermal 七块都有输出，总耗时 < 60s |
| T-419 | 提权降级如实标注 | **非管理员**跑 `/health` | 温度/SMART 块显式写“需要以管理员身份运行才能读取”；**不显 0、不显 `--`、不静默丢块** |
| T-420 | 单块失败不影响其它块 | mock 掉 SMART 读命令（或断网后等 CIM 超时） | 其余六块照常输出，disk 块 `note` 写失败原因 |
| T-421 | 建议清单基于阈值 | 造磁盘 <15% / 内存 <1GB / 磁盘队列 ≥2 / 开机 ≥168h 四种情形 | 各触发对应一条建议；无异常时不堆砌套话 |
| T-422 | Markdown 与落盘 | `/health --markdown`；`/health --save ./r.md` | 标题为 `# 系统体检报告`；`.md` 能正常打开且表格对齐（中文按 2 列算宽） |

## 37.5 工具契约与接线

| 编号 | 测试目的 | 测试步骤 | 通过标准 |
|------|---------|---------|---------|
| T-423 | DiskClean 不进延时窗口 | 看工具定义与执行日志 | `is_irreversible()` 返回 `False`（有隔离区可还原）；清理**不等 30 秒** |
| T-424 | 参数键不触发写前留底 | 查三个工具的 `input_schema.properties` | 无 `path` / `file_path` / `target` 类键（否则会把 Temp 整个复制一份留底） |
| T-425 | 权限姿态 | 分别调 `DiskReport` / `DiskClean(confirm=false)` / `DiskClean(confirm=true)` / `SystemHealth(report)` / `SystemHealth(scan)` | allow / allow / ask / allow / ask；`enabled=false` 时 `DiskClean(confirm=true)` 变 deny |
| T-426 | 补全与 `/help` | 输 `/dis` 按 Tab；`/help` | 补全出 `/disk`；`/help` 里能看到 `/disk` `/clean` `/health` 三条 |
| T-427 | 裸上下文不崩 | 构造 `settings=None` 的 `ToolContext` 调 `DiskReport` | 不抛异常；`enabled` 返回 `False` |

---

## 一键实机自检（不经模型）

基础设施层的四件事可以不经过 LLM 直接验：

```bash
# 1) 规则库能不能全部解析（应输出 18 / 0）
python -c "from agent.core.maintenance import load_rulebook; b=load_rulebook(); print(len(b.rules), len(b.rejected))"

# 2) 安全状态判定（重点看 note 是否为“由 X 接管”而不是“没有实时防护”）
python -c "from agent.core.maintenance import security_status; s=security_status(); print(s.any_enabled, s.primary); print(s.note)"

# 3) 隔离区批次（应正常列出，无异常栈）
python -c "from agent.core.maintenance import list_batches; print(list_batches())"

# 4) 重启后过期批次自动清（把 quarantine_days 临时设 0 则不清）
python -c "from agent.core.maintenance import purge_expired; print('purged:', purge_expired(7))"
```

## 本册用例数

**37 项**（T-397–T-433）。不计入主清单的 382 项统计。