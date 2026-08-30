# 桌面文件操作升级（FileOps Desktop Upgrade）实施计划

> 对应 [VISION.md](../VISION.md) 维度四「维护」（`tools/system/maintenance.py`）与路线图 P2-6「文件系统守望」的落地文档
> 目标：从"模型临时拼 Bash 搬文件"到"原生文件工具 + 回收站 + 整理闭环"，让"帮我整理桌面/下载目录"第一次成为产品能力

---

## 1. 背景与现状缺口

JARVIS 自评"行动"维度四星，但"操作桌面文件"这个场景实际是拼凑出来的。2026-08 代码调研结论：

| # | 缺口 | 现状证据 |
|---|---|---|
| 1 | **无文件搬移类工具** | `agent/tools/file_ops/` 只有读/写/编辑/glob/grep 五件套，move/copy/rename/delete 全靠 Bash 拼 `mv`/`cp`/`rm` |
| 2 | **删除即永久，无回收站** | 全项目无 send2trash；Bash `rm` 或 FileGuard 回滚新建文件清理都是 unlink/rmtree |
| 3 | **快照未全覆盖** | FileGuard 快照只在 Bash 沙箱高风险命令路径触发（`agent/tools/bash.py:152`），FileWrite/FileEdit/GUI 均无 |
| 4 | **整理/维护零实现** | `tools/system/` 无 maintenance 模块，VISION 维度四全部停留在规划 |
| 5 | **无目录浏览工具** | 模型看一个目录里有什么只能靠 Glob（上限 100、按 mtime）或 Bash `ls`，无大小/类型元信息 |
| 6 | **编码只支持 UTF-8** | `file_read.py:84` 固定 `utf-8 + errors="replace"`，GBK 文件静默乱码（Windows 中文场景高频） |
| 7 | **桌面/Documents/Downloads 无差异化策略** | `path_guard.py` 只硬 deny `~/.ssh` 等 4 处，用户高频目录既无保护也无整理逻辑 |

**为什么现在做**：画像记忆 Phase 1a 已上线（会记住"文件按项目归类"这类偏好），记忆有了、但没有可执行的手——画像里存的整理偏好无处落地。本计划补上这只手。

---

## 2. 总体架构

```
P0 原生工具集（本周可开工，~2 天）
  FileMove / FileCopy / FileTrash / FileList 四个工具进 file_ops
  删除走回收站（send2trash），搬移接入 stale 检测与快照
        │
        ▼
P1 安全全覆盖（~2 天）
  FileWrite/FileEdit 覆盖前自动快照（推广 bash.py 既有模式）
  /undo 命令：一键回滚最近快照
  编码检测：utf-8 → gb18030 自动回退
        │
        ▼
P2 目录整理器（~4 天，本计划的核心交付物）
  agent/core/maintenance/organizer.py 规则引擎
  scan → plan(dry-run) → 用户确认 → execute 三段式
  /organize 命令 + OrganizeDirectory 工具壳（LLM 可调用）
  可选 daemon 定期整理（默认关闭）
        │
        ▼
P3 语义层（远期，不在本计划内展开）
  pywinauto UIA 语义操控资源管理器/桌面图标（对应 VISION 维度三）
```

**三条铁律**（整理器是能批量动用户文件的组件，纪律必须先立）：
1. **默认 dry-run**——organizer 的一切执行动作必须先出计划清单、经用户确认
2. **删除只进回收站**——organizer 与 FileTrash 永远不允许 unlink 原路径
3. **执行前整体快照**——每次 execute 是一个 FileGuard 快照单元，可整体回滚

---

## 3. P0：原生文件操作工具集

### 3.1 新增工具一览

| 工具 | 文件 | 语义 | 只读 | 默认权限 |
|---|---|---|---|---|
| `FileMove` | `agent/tools/file_ops/file_move.py` | 移动/重命名文件或目录（含 rename 语义） | 否 | ASK |
| `FileCopy` | `agent/tools/file_ops/file_copy.py` | 复制文件或目录（copy2 保留元数据） | 否 | ASK |
| `FileTrash` | `agent/tools/file_ops/file_trash.py` | 删除到系统回收站（**可恢复**） | 否 | ASK |
| `FileList` | `agent/tools/file_ops/file_list.py` | 列目录内容（名称/类型/大小/mtime） | 是 | 自动放行 |

内置工具 61 → 65。命名与 FileRead/FileWrite/FileEdit 对齐；**不做独立 FileRename**——rename 就是同目录 move，见设计决策表。

### 3.2 FileMove 详细设计

```python
class FileMoveTool(Tool):
    name = "FileMove"
    input_schema = {
        "type": "object",
        "properties": {
            "source_path": {"type": "string", "description": "源路径（文件或目录）"},
            "dest_path":   {"type": "string", "description": "目标路径；同目录下即重命名"},
            "overwrite":   {"type": "boolean", "description": "目标已存在时是否覆盖，默认 false"},
        },
        "required": ["source_path", "dest_path"],
    }
```

实现要点：
- `shutil.move`（跨盘自动 copy+delete 回退）；目标存在且 `overwrite=false` 时报错并列出冲突项
- 沿用 `resolve_path()`（`agent/tools/base.py:14`，Git Bash 风格路径已兼容）
- **stale 检测**：源文件被外部修改过先拒绝，报错文案沿用 FileWrite 的措辞；成功后 `invalidate(source)` + `record_file_write(dest)`（`agent/core/memory/file_state.py`）
- **双路径守护**：权限管线的 `_check_path_guard` 只看 `get_path()` 单路径（`checker.py:131`），因此 `get_path` 返回 source，`check_permissions` 里自己对 dest 额外调 `is_dangerous_write_path()` 并合并结论（ask/deny 取更严）
- 移动前对该文件建 FileGuard 快照（对齐 bash.py:152 的既有模式）

### 3.3 FileTrash 与回收站

- 依赖 `send2trash`（纯 Python、零原生依赖、Win/macOS/Linux 三平台），加入 `pyproject.toml` **核心 dependencies**（不放 extras——删除走回收站是安全底线，不该可选）
- `send2trash(str(path))`；目录同样支持（同卷下秒级完成）
- 返回信息明确告知"已移入回收站，可通过 /undo 或系统回收站恢复"
- **大文件防护**：>2GB 时要求显式 `confirm_large: true` 参数，防止误把系统级大目录扔进回收站
- YOLO 模式下放行（有回收站兜底），但 `path_guard` 的 deny 清单照常生效——顺带把 `C:\Windows`、`Program Files`、注册表敏感区补进 `path_guard.py` 的危险目录清单

### 3.4 FileList

```python
{"type": "object", "properties": {
    "dir_path": {"type": "string"},
    "pattern":  {"type": "string", "description": "可选，glob 过滤如 *.png"},
    "recursive": {"type": "boolean", "description": "默认 false 只列一层"},
}}
```

- 返回表格化清单：名称、类型（文件/目录）、大小（人类可读）、修改时间；目录在前、文件按 mtime 倒序
- 上限 500 条，超限提示加 pattern 收窄；隐藏文件默认不列，`show_hidden: true` 可见
- 只读工具（`is_read_only → True`），自动放行——这是整理场景的"眼睛"，必须让模型零成本用

### 3.5 注册与权限接线

1. 四个类导出进 `agent/tools/__init__.py` 的 `__all__`（纯标准库 + send2trash，不走可选 try-import）
2. `agent/core/tool.py` 的 `_build_default_registry_impl()` 纳入注册
3. `agent/permissions/modes.py` 的 `AUTO_ACCEPT_EDIT_TOOLS`（约 L38）追加 `FileMove`、`FileCopy`——accept_edits 语义是"编辑类操作不用逐次确认"，move/copy 不破坏内容且可回滚，符合；**FileTrash 不加入**，删除永远保持确认
4. README 工具表 + `docs/architecture/05-工具系统.md` 同步更新

---

## 4. P1：安全全覆盖

### 4.1 FileWrite / FileEdit 接入快照

两工具的 `call()` 在覆盖已存在文件前插入：

```python
from agent.core.sandbox.file_guard import FileGuard
guard = get_shared_file_guard()          # 新增共享单例，避免 bash 与工具层各建目录
snap_id = guard.snapshot(str(path), reason=f"{self.name} 覆盖前快照")
```

- 快照上限 20 个、>50MB 只记元数据——`file_guard.py` 既有逻辑直接复用，不改
- 新建文件（原不存在）不快照，错误回滚由 `Snapshot.existed=False` 语义天然覆盖

### 4.2 `/undo` 命令

`agent/commands/handlers/core_commands.py` 新增：

| 命令 | 功能 |
|---|---|
| `/undo` | 回滚最近一次快照（列出将被恢复的文件，需确认） |
| `/undo list` | 表格列出最近快照（id、时间、reason、文件数） |
| `/undo <id>` | 回滚指定快照 |

与 CLI 级 `Ctrl+C` 无关，这是操作级撤销——"AI 刚才那个 mv 我后悔了"的一键后悔药。

### 4.3 编码检测（gb18030 回退）

- `file_read.py`：先 `utf-8` strict 试读；`UnicodeDecodeError` 则回退 `gb18030`（覆盖 GBK/GB2312 超集）；再失败才 `errors="replace"`，并在返回头部注明 `encoding: gb18030`
- `file_write.py` / `file_edit.py`：新增可选 `encoding` 参数，默认 `utf-8`；对已存在文件写回时若读取时检测到非 UTF-8 编码，按原编码写回（读取侧把检测结果写入 `file_state` 缓存传递）
- 不引入 chardet——两步回退覆盖 Windows 中文场景 99% 的情况，复杂检测留给真需求出现之后

### 4.4 配置项（`settings.example.toml` + `settings.py`）

```toml
[safety]
auto_snapshot = true        # FileWrite/FileEdit 覆盖前自动快照
```

---

## 5. P2：目录整理器（核心交付物）

### 5.1 架构

```
agent/core/maintenance/__init__.py
agent/core/maintenance/organizer.py      # 核心引擎：scan/plan/execute（纯逻辑，可测试）
agent/tools/extensions/maintenance_tool.py  # OrganizeDirectory 工具壳（暴露给 LLM）
agent/commands/handlers/tool_commands.py    # /organize 命令（面向用户）
```

职责切分：organizer 不依赖 LLM 也能跑（规则引擎），LLM 只在两个点介入——① `/organize smart` 时对规则拿不准的文件做语义分类；② 借助画像记忆个性化目标结构（"用户习惯按项目归类"）。

### 5.2 规则引擎

内置规则（按优先级，`~/.jarvis/organize_rules.toml` 可覆盖）：

```toml
# 目标结构相对于整理目录本身（如 downloads 内建子目录）
[[rules]]
match_ext   = [".jpg", ".png", ".jpeg", ".heic", ".webp"]
destination = "图片/"

[[rules]]
match_ext   = [".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt"]
destination = "文档/"

[[rules]]
match_ext   = [".zip", ".rar", ".7z"]
destination = "压缩包/"

[[rules]]
match_ext   = [".exe", ".msi"]
destination = "安装包/"

[[rules]]                     # 兜底：按修改月份归档，不丢不乱
match = "other"
destination = "其他/{yyyy}-{mm}/"

[options]
conflict = "skip"             # rename / skip / overwrite（默认 skip 最保守）
```

- 文件名含日期的可识别归档（`截图 2026-08-29.png → 图片/2026-08/`）
- 规则不匹配 + `smart` 开启 → 便宜模型批量分类（一次调用、严格 JSON、成本可忽略），**拿不准的一律进"待定/"不动**

### 5.3 三段式流程

```
scan   → FileList 语义扫目录（含大小统计、耗时预估）
plan   → 输出移动清单：源 → 目标（每行一条，冲突标注 skip/rename）
         [dry-run 默认终点] 展示 + 落盘 ~/.jarvis/organize_plans/<id>.json
execute→ 用户确认（命令行 y / LLM 传 confirm=true）后：
         整体一个 FileGuard 快照 → 逐条 FileMove 语义执行 →
         失败即中止并自动 rollback → 输出报告（成功 N / 跳过 M / 失败 0）
```

- execute 内部不直接 shutil，而是调用与 FileMove 工具相同的底层函数，保证 stale 检测、冲突、缓存失效行为一致
- 报告附 `/undo` 提示，`<id>.json` 记录快照 id 供 `/undo <id>` 精确回滚

### 5.4 入口

| 入口 | 形态 | 说明 |
|---|---|---|
| `/organize <dir>` | REPL 命令 | 只出 plan 不执行；`--run <plan_id>` 确认执行 |
| `/organize downloads` | 快捷目录 | 支持 `downloads` / `desktop` / `documents` 快捷名（读 Windows 已知文件夹路径，不硬编码） |
| `OrganizeDirectory` 工具 | LLM 工具壳 | "帮我把下载文件夹整理一下"走工具壳，参数 `mode: preview/execute`，**execute 需用户在对话中明确同意**（工具层 check_permissions 固定 ASK，yolo 也不放行——这是铁律 1 的唯一例外硬编码） |
| daemon 定期 | 可选 | `[maintenance] auto_organize = false` 默认关；开启后每周对 downloads 出 plan、**只通知不执行** |

### 5.5 与画像记忆联动

organizer 出 plan 时读取 ProfileStore（`agent/core/memory/profile_store.py`），若存在 `work_habit` 类条目含文件组织偏好，注入 plan 头部作为"个性化依据"，规则缺省值优先匹配画像。这让 Phase 1a 存的偏好第一次产生行动价值。

---

## 6. P3：语义层（仅登记，不在本计划实施）

对应 VISION 维度三，此处只留接口约定：`agent/tools/system/window.py` 已预留 pywinauto 位（L7 注释），后续以 `tools/app_integrations/explorer.py` 提供"读资源管理器地址栏/选中项、枚举桌面图标"能力。**桌面图标视觉拖拽整理不进本计划**——坐标方案对文件整理可靠性不合格，等 UIA 语义层。

---

## 7. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| rename 是否独立工具 | 否，FileMove 同目录即 rename | 少一个工具少一分模型误用面；语义等价 |
| send2trash 放哪 | 核心 dependencies | 删除走回收站是安全底线，不做可选 |
| FileTrash 权限 | 默认 ASK；accept_edits 不放行；yolo 放行 | 回收站兜底使 yolo 风险可控；删除与编辑必须区别对待 |
| OrganizeDirectory 在 yolo 下 | 仍固定 ASK | 铁律 1 唯一硬编码例外：批量动用户文件不允许绕过确认 |
| 冲突默认策略 | skip | 整理器宁可不動，不可覆盖；rename 是第二选项，overwrite 需显式配置 |
| LLM 参与分类 | 可选、批量、拿不准进"待定/" | 成本可控且绝不猜；误分类损失 > 少分类损失 |
| 编码方案 | utf-8 → gb18030 两步回退 | 零新依赖覆盖中文 Windows 主流场景；chardet 留给真需求 |
| 快照共享 | get_shared_file_guard() 单例 | bash 沙箱与工具层共用一个快照目录与 20 个配额，避免双份膨胀 |

---

## 8. 里程碑

### M1：P0 工具集（预计 2 天）
- [ ] FileMove / FileCopy / FileTrash / FileList 四工具 + 单元测试
- [ ] 注册接线（tools/__init__.py、tool.py、modes.py、pyproject.toml）
- [ ] path_guard 危险目录清单扩充（Windows/Program Files）
- **验收**：模型不借助 Bash，仅用四工具完成"把桌面所有 .png 移到 图片/ 子目录"（含一次同名冲突处理）；`pytest tests/tools/` 全绿

### M2：P1 安全全覆盖（预计 2 天）
- [ ] FileWrite/FileEdit 快照接入 + shared guard 单例
- [ ] `/undo` 三形态（undo / list / <id>）
- [ ] 编码检测读写两侧 + file_state 传递
- **验收**：FileWrite 覆盖前有快照、`/undo` 能恢复；GBK 编码的 .txt 读不出乱码、编辑后保持 GBK；全量回归通过

### M3：P2 整理器（预计 4 天）
- [ ] organizer 引擎（scan/plan/execute/rollback）+ 规则文件加载
- [ ] `/organize` 命令 + OrganizeDirectory 工具壳
- [ ] 画像联动 + smart 模式（可选 LLM 分类）
- [ ] 文档：README「维护」节 + 架构文档新增 `15-目录整理.md`
- **验收**：对真实下载目录（50+ 混杂文件）出 plan，人工核对零误判后执行，`/undo` 整体回滚成功；构造一个冲突场景验证 skip 与自动 rollback

### M4：P3 语义层 —— 另立计划，不在本文档验收范围

---

## 9. 测试计划

`tests/tools/test_file_ops_desktop.py`（M1/M2）+ `tests/core/test_maintenance_organizer.py`（M3），覆盖：

- move/copy/trash/list 正常路径、同名冲突、跨盘、源不存在、stale 拒绝
- 双路径守护：source 正常 + dest 危险目录 → deny； dest 在工作目录外 → ask
- accept_edits 下 move/copy 放行、trash 仍 ASK（含 yolo + OrganizeDirectory 仍 ASK）
- 回收站：mock send2trash 验证调用与 >2GB confirm_large 拦截
- 编码：GBK 文件读回退与按原编码写回；utf-8 不受影响
- 快照：覆盖前快照生成、/undo 回滚、20 配额清理不误删
- organizer：规则命中/兜底月归档/冲突 skip/rename、plan 落盘、execute 中止即 rollback、画像注入

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| send2trash 平台行为差异（Linux 无统一回收站） | Linux 上 freedesktop 规范可用；无回收站环境（纯 CLI）自动降级为"移入 ~/.jarvis/trash/"本地 trash 目录 |
| 跨盘移动超大目录耗时长 | copy+delete 回退前检测总大小 >1GB 时提示并要求确认；执行中输出进度 |
| 整理规则误判搬错位置 | dry-run 默认 + skip 冲突策略 + 整体快照 + 回收站四层兜底，最坏情况 `/undo` 一步还原 |
| yolo 用户习惯性全放行 | OrganizeDirectory 与 FileTrash 的大文件确认不随 yolo 放行，守住批量操作底线 |
| 快照配额 20 被整理器大动作挤占 | organizer execute 使用独立 reason 标记，配额满时优先保留带 maintenance 标记的快照 |

---

## 11. 非目标

- ❌ 不做全盘自动整理——只做用户显式指定目录，daemon 定期任务默认关且只通知不执行
- ❌ 不做桌面图标视觉拖拽整理——等 P3 UIA 语义层
- ❌ 不做文件内容去重/相似图清理——是另一个独立课题
- ❌ 不做网络盘/UNC 路径/OneDrive 按需文件的特殊处理——v1 明确不保证，遇到提示跳过
- ❌ 不替代专业工具（Everything/TotalCommander）——CLI-Anything harness 接入它们的桥留给社区

---

**文档版本**：v1.0 | **创建**：2026-08-29 | **状态**：待评审
**关联**：[VISION.md](../VISION.md) 维度四「维护」· 维度六「安全」| roadmap P2-6、Phase 4 maintenance.py | 依赖画像记忆 Phase 1a（已完成）
