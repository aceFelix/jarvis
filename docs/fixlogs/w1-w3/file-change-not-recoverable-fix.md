# 文件改错之后不知道怎么恢复

> 修复日期：2026-10-30
> 影响范围：所有本地文件写（`FileWrite` / `FileEdit`，以及终端与桌面两种宿主）
> 严重程度：能力缺失 —— 不崩溃、不报错，但改错了只能手动改回来
> 作者：aceFelix

## 1. 问题现象

用户的原话是：

> 「贾维斯不能干的是，文件变动之后，他不知道如何恢复为原来的状态」

不是"不知道"，是"**没有留过底**"。模型覆盖了一个文件，原文就没了；用户事后想退回去，只能自己回忆原来写了什么，或者依赖项目本身就是 git 仓库、改动还没提交。

## 2. 排查：三条断链叠在一起

一查下来不是缺一个开关，是三条各自成立的断链：

### 2.1 `FileWrite` / `FileEdit` 从来不做备份

两个工具的文件头注释写得很清楚：

```python
# file_write.py（修复前）
"""FileWrite 工具 —— 写入文件（覆盖或新建）。
语义: 把 content 完整写入指定文件。
已存在的文件会被覆盖（v0.1 不做备份，依赖外部 git/versionControl）。"""
```

注释里的「依赖外部 git」在真实使用中几乎不成立：终端里 jarvis 经常被指向一个**不是** git 仓库的目录（`~/Desktop`、`D:\下载`），覆盖之后一点退路都没有。

### 2.2 `FileGuard` 早就写好了，但没接到这条路上

`agent/core/sandbox/file_guard.py`（303 行）实现了完整的快照 + 回滚：`Snapshot` / `SnapshotEntry` 数据结构、`existed=False`（原文件不存在 → 回滚 = 删除新建的）语义、大小阈值、条数配额、自动清理。

但它**只在一条路径上被调用**：

```python
# tools/bash.py:321-329（修复前）
snapshot_id = ""
if risk.needs_snapshot:
    guard = FileGuard(max_snapshots=...)
    snapshot_id = guard.snapshot(work_dir, reason=f"沙箱执行: {command[:60]}")
    auditor.log_snapshot(snapshot_id, [work_dir], reason=command[:60])
```

三个问题：
1. 只有 bash 沙箱的高风险命令会触发；
2. 快照的是**整个 work_dir** —— 在 2.4 万文件的工作区上复制一遍，代价极高；
3. 快照 ID 只进了 `SandboxAuditor` 的审计日志，`/undo` 这扇门**根本看不到它**。

### 2.3 本地写的补偿计划一律退到"整轮"

`orchestrator._record_effect` 原来的逻辑：

```python
else:
    cid = self._turn_checkpoint_id(ctx)
    if not cid:
        return                      # ← 没有检查点就一条都不记
    undo_spec = {"kind": UNDO_CHECKPOINT, "cid": cid, "workdir": ctx.workdir}
```

两个后果：

- **粒度错**：用户只想撤"刚才改坏的那一个文件"，实际会退掉同一轮里所有正确的改动；
- **条件苛刻**：`cid` 只由能打整轮检查点的宿主提供。终端 REPL 不写它、后台任务步骤的子 ctx 没有它、检查点被配额修剪后也没有它 —— 这些场景下**连一条记录都不记**，`/undo` 只会回"没有可撤销的已执行副作用"。

## 3. 修法

### 3.1 新增 `undo_spec` 的第四种形态：`files`

```python
UNDO_FILES = "files"   # {"kind","snap_id","paths"}
```

语义：**按写前留底还原文，只动这次碰过的文件**。与 `checkpoint` 的关键差别是粒度，也是为什么 `_undo_files` **刻意不做同轮连带标记**：

- 整轮检查点回滚确实会让同轮其它条目一起失效，所以那边必须连带标 `undone`（否则 `/undo list` 会继续显示"可撤销"，用户以为还有一层能撤）；
- 文件级恢复只动自己被碰过的那几个文件，同轮其它记录**仍然有效**，一并标记反而是错报。

### 3.2 留底的发生位置：执行前、权限后

插在 `orchestrator._run_one` 里，`_maybe_hold_irreversible` 之后、`tool.call` 之前：

```python
write_snap = snapshot_before_write(
    tool.name, effective_input,
    read_only=bool(tool.is_read_only(effective_input)),
    destructive=bool(tool.is_destructive(effective_input)),
    settings=getattr(ctx, "settings", None),
    workdir=str(getattr(ctx, "workdir", "") or ""),
)
```

三个位置选择的理由：

- **不能更早**（权限判定之前）：会给被拒绝、被延时窗口拦下的调用白复制一份文件；
- **不能更晚**（执行之后）：原文已经被覆盖了，留底无从谈起；
- **不放在 `FileWrite` / `FileEdit` 自己里面**：那样只能覆盖这两个工具，将来新增的写类工具、带路径参数的 bash 类工具都要再改一遍。放编排器是单点覆盖。

前置条件是两道一起用：`not read_only` **且** `is_destructive`。只用前者不够 —— `is_read_only` 的基类默认值就是 `False`，光靠它会让**任何**带 `file_path` 参数的工具（包括只查不动的那类）都去复制一遍文件。

### 3.3 复用 `FileGuard`，但独立目录与独立配额

不重造快照引擎（`Snapshot` 的 `existed` 语义、大小阈值、清理逻辑都是现成的），但存储目录**必须分开**：

```
~/.jarvis/sandbox_snapshots/   ← bash 沙箱高风险命令的整目录兜底（20 条）
~/.jarvis/file_snapshots/      ← 文件写的单文件留底（默认 50 条）
```

共用的话，两次聊天产生的文件留底就能把 bash 的整目录兜底挤出配额 —— 那是个"出事时才需要"的保险，不该被高频动作冲掉。

同理，`[actions].file_snapshots = 0` 是**独立**的关闭开关，不看 `checkpoint_enabled`：用户可能想要前者而嫌后者占盘（或反过来）。把两者合在一起，等于"我不想每轮存一份工作区快照"顺手把"改错一个文件能不能撤"也关了。

### 3.4 体积两道闸

留底是**复制**而非硬链接。一个 8GB 的目录被 `copytree` 一次就能把磁盘写满，所以：

| 闸 | 位置 | 阈值 |
|---|---|---|
| 单文件 | `FileGuard` 原有逻辑 | 50MB |
| 单次调用（含目录） | `file_effects._measure` | `[actions].file_snapshot_mb`，默认 64MB |

`_measure` 没有直接用 `FileGuard._dir_size`：后者是完整 `rglob`，在一个几万文件的目录上光算大小就要好几秒 —— 而这个函数位于**每次工具执行的必经之路**上，为"判断是否超限"付这个代价不值得。改用 `scandir` 逐层走，一超限立即返回。

## 4. 修的过程中抓到的两个真缺陷

### 4.1 相对路径按进程 cwd 解析（会删用户文件）

第一版只做了 `Path(raw).expanduser()`。测试立刻暴露：

```
sandbox = D:/temp/pytest-.../test_overwrite_is_recoverable_0
模型传的是 "note.txt"（相对路径）
Path("note.txt") → 相对进程 cwd → 判断"文件不存在" → 记 existed=False
→ 撤销时不是写回原文，而是 **把用户的文件删掉**
```

修法：加 `workdir` 参数，相对路径按工具的工作目录解（与工具的 `resolve_path` 同一口径）。

### 4.2 只靠 `is_read_only` 会让无关工具也复制文件

`Tool.is_read_only` 的基类默认值是 `False`（fail-closed），所以一个名叫 `file_write_doc`、参数里恰好带 `file_path` 的工具也会被当成"要写文件"而去留底。加上 `is_destructive` 作为第二道前置后，只有工具自己声明"我会改动/删除"才会留底。

这两个都属于"测试写出来才发现"的类型 —— 端到端探针先跑通，再补单测，反而能抓到更多。

## 5. 验证

### 5.1 端到端探针（临时脚本，用完即删）

```
=== [1] 覆盖已有文件后能恢复原文 ===
  [OK] 账本记了一条 -> 3a443d5abc89
  [OK] 用的是文件级形态 -> {'kind': 'files', 'snap_id': '20261030_180716_a9a3d5'}
  [OK] 内容回到原文 -> 原始内容 v1
=== [3] 新建文件的撤销 = 删掉它 ===
  [OK] 新文件已被删除
=== [4] 只读工具不留底 ===      [OK] 没有新增留底
=== [5] 超过体积上限的目录不留底 ===  [OK] 超限不留底
=== [6] 留底被清掉时如实报错 ===  [OK] 留底快照已不在了（可能已被配额清理）
=== [7] 路径推导（保守）===
  [OK] 不认 new_string 里的路径
=== 结论：全部通过 ===
```

### 5.2 单测

`tests/core/test_file_effects.py`（30 例）覆盖：路径推导的保守性、只读/非破坏性/关闭开关三种不留底、相对路径解析、超限目录、引擎异常降级、恢复的三条结局（还原文 / 删新建 / 如实报错）、`UNDO_FILES` 的 `undoable` 与状态流转、**不做同轮连带标记**、回执文案、以及走真实编排器的端到端（证明不依赖任何 `checkpoint_cid`）。

全量：**3919 passed**（原 3888 + 新增 31）。

## 6. 顺带做的两件事

### 6.1 `orchestrator.py` 回到 800 行以内

加完留底与回执提示，主文件到了 802 行，越过项目的 800 行红线。把四段**不依赖编排器实例状态**的判定搬到 `core/actions/effect_policy.py`：

| 函数 | 职责 |
|---|---|
| `actions_config` | 读 `[actions]` 配置（缺省口径：账本开、窗口 30 秒、本地写跟踪开） |
| `is_irreversible_call` | 是否"出了本机就收不回" |
| `note_effect_id` | 把账本 id 回填给宿主（任务脊柱用） |
| `turn_checkpoint_id` | 取本轮检查点 cid（"键存在但为空"与"键不存在"严格区分） |

`ToolOrchestrator._turn_checkpoint_id` 保留为一行转发 —— 既有测试直接调它，没必要因为搬家改一堆调用点。搬完 757 行。

### 6.2 `/undo` 的展示按粒度分开

原来 `/undo list` 一律显示"可撤销"，`/undo` 一律展示"整轮回滚影响面"。这会把两种代价完全不同的操作说成同一件事。现在：

```
可按文件恢复  ← 只还原这条改动碰过的文件，同轮其它改动不受影响
可整轮回滚    ← 恢复到该轮开始前，本轮其余改动会一并回退
可回退配置    ← 配置项回写原值
```

`_undo_one` 里也分成两条 UI 路径：文件级那边如果照旧去问 `manager_for(...)`，会在"本轮检查点已不可用"那里把一条**本来能撤**的记录误判成撤不动 —— 因为文件级条目根本没引用检查点。

## 7. 能力边界（如实说明）

| 场景 | 撤销方式 | 说明 |
|---|---|---|
| `FileWrite` / `FileEdit` 改错 | `/undo` 按文件恢复 | 本次新增，不需要 git |
| 配置项改错 | `/undo` 回写原值 | 既有能力（`UNDO_CONFIG`） |
| `Bash` 里 `rm` / 脚本改文件 | `/undo`（整轮检查点）或 `/rewind` | 依赖该轮确实打过检查点 |
| 超过 64MB 的目录被改 | 无文件级留底 | 复制成本过高；日志里会跳过，不假装能撤 |

**`Bash` 的删除为什么不做文件级留底**：`BashTool` 的参数只有 `command` 字符串，可靠地从中提取"哪些路径会被删"需要解析 shell 语法，而 `rm -rf $(...)`、管道、脚本调用都能绕开静态分析。做一套"看起来能撤、实际经常漏"的机制，比明确说"这条退回整轮检查点"更危险。bash 侧仍然保留原有的沙箱整目录兜底（高风险命令时），只是它不进账本。

## 8. 遗留

- **测试污染用户日志**（前序问题）：`pytest` 会往 `~/.jarvis/jarvis.log` 写 `key=<MagicMock>` 之类的行，排查线上问题时容易误读。根治方向是给测试进程设独立的日志路径，尚未做。
- **`FileGuard` 在 bash 路径仍然 `copytree` 整个 `work_dir`**：在 2.4 万文件的工作区上，高风险命令的这次复制可能非常慢。属于既有行为，本次未动（动了会影响 bash 沙箱的兜底能力，需要单独评估）。

## 9. 改动清单

| 文件 | 改动 |
|---|---|
| `agent/core/actions/file_effects.py` | **新增**（323 行）：路径推导、写前留底、恢复、清单与文案 |
| `agent/core/actions/effect_policy.py` | **新增**（88 行）：从 orchestrator 抽出的四段无状态判定 |
| `agent/core/actions/journal.py` | 加 `UNDO_FILES` 常量、`undoable` 判定、`_undo_files` 分派 |
| `agent/core/actions/__init__.py` | 导出 `UNDO_FILES` / `UNDO_CONFIG` |
| `agent/core/orchestrator.py` | `_run_one` 加写前留底；`_record_effect` 补偿计划优先级重排；回执追加"可反悔"提示；判定逻辑改为调用 `effect_policy` |
| `agent/commands/handlers/undo_commands.py` | 文件级撤销走独立 UI；`/undo list` 按粒度标注 |
| `agent/tools/file_ops/file_write.py` / `file_edit.py` | 注释与 `description` 更新（"不做备份"已不成立） |
| `agent/config/settings.py` | `actions_file_snapshots` / `actions_file_snapshot_mb` |
| `agent/config/settings_domains.py` | 两个新键的 TOML 映射 |
| `agent/config/config_specs.py` | 两个新键的 `/config set` 规格 |
| `tests/core/test_file_effects.py` | **新增**（30 例） |
| `tests/core/test_w3_step_side_effects.py` | 口径更新：检查点关掉也能撤；两条通道都关才不记 |
| `tests/commands/test_config_set_command.py` | 27 → 29 项 |
