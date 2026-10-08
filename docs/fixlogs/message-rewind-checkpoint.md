# 交付：消息级回溯（对话回退 + 文件回滚）

- 日期：2026-10
- 范围：jarvis（core/checkpoint.py（新建）、core/message.py、core/memory/store.py、
  config/settings.py、main.py、commands/handlers/core_commands.py、
  ui/workbench（engine.py / api.py / checkpoint_ops.py（新建））、serve（protocol / server）、
  settings.example.toml、tests）、jarvis-desktop（contracts / chatStore / backendStore /
  dispatcher / ChatArea / glyphs / i18n / chat.css、tests）、文档同步
- 作者：aceFelix

## 背景与目标

旧状：终端 `/rewind [n]` 只弹消息不回滚文件；FileGuard 快照只覆盖高风险 bash 命令且与
消息无关联；桌面无任何回溯入口。目标：**任何一条用户消息发错，可撤回该消息及其产生的
全部回答与文件修改，工作区回到发消息之前的状态**。

## 核心方案

- **shadow git 检查点**：`~/.jarvis/checkpoints/<basename>-<hash12>/git` 独立 git 目录，
  `--git-dir/--work-tree` 把用户工作目录当工作树；每轮对话前 `add -A` +
  `write-tree + commit-tree` 打**孤儿 commit**，每个检查点一条 ref
  （`refs/jarvis/ckptNNNNN`），`manifest.json` 做台账。不污染用户仓库，非 git 目录同样适用。
- **回滚语义**：检查点 C_i 在 U_i **发出前**创建；撤回从 U_i 起全部消息 →
  `read-tree -u --reset <cid>` + `clean -fd`（不带 -x，被忽略目录不受波及）。
- **归属与持久化**：`Message.extra` 新字段，用户消息记 `extra["checkpoint_id"]`，
  随会话 JSON 持久化，`/load` / 重启后仍可回溯。
- **全链路优雅降级**：无 git / 关闭开关 / 超时 / 配额修剪 → 仅对话回退，绝不报错阻断。

## 关键设计点

| 决策 | 理由 |
|------|------|
| 存储按 **workdir 指纹** 而非会话名 | `/load` 旧会话、`/rename` 改标题后仍可命中；跨目录回滚天然被拒（不同指纹查不到 cid），免做额外校验 |
| 孤儿 commit + 独立 ref 而非线性历史 | 配额修剪删最旧 ref 不牵断链，无需 rebase/gc 复杂度可控 |
| 定位口径 `user_tail_count`（尾部数第 N 条**可见**用户消息） | 引擎 emit `user_message` 时后端消息尚未创建，且上下文压缩会重写旧消息使绝对索引漂移；尾部计数两边同口径（`_is_visible_user_message`）且对压缩免疫 |
| `checkpoint.rewind` 入队即返 + `rewound` 事件落地 | 回滚与在跑轮次经指令队列天然串行；真实结果（removed/files_restored/reason）事件通道给前端，避免 RPC 长挂 |
| 文件回滚失败 → 整次撤回放弃（消息也不截断） | 「说好的回滚没发生但消息没了」是最坏体验；原子性优先 |
| 桌面裁气泡只由 `rewound` 事件驱动 | 本地发送侧同步裁会双裁；事件单一来源 |
| `checkpoint.preview` 只读不入队 | GIL 下单次 `list()` 拷贝安全；预览允许与在跑轮次并发，rewind 落地时重算 |
| 远程通道（手机/微信）不打检查点 | 其消息撤回自动退化仅对话回退，避免跨端语义纠缠 |
| FileGuard 与本模块互不依赖 | FileGuard 管「这一次命令别搞坏文件」，本模块管「这条消息引发的修改全部撤销」，粒度与手段不同 |

## 运行时链路

| 层 | 改动 |
|---|---|
| `core/checkpoint.py`（新建） | `CheckpointManager`：`create/restore/describe/has_checkpoint/available` + 配额修剪 + exclude 预置；全部异常路径静默降级 |
| `core/message.py` / `memory/store.py` | `extra: dict` 字段与序列化（旧文件默认空 dict 兼容） |
| `main.py` | REPL 每轮 `loop.run` 前 `create(reason=前80字)`，轮末把 cid 绑到用户消息；mgr 经 `ctx.extra["checkpoint_mgr"]` 注入命令上下文 |
| `core_commands.py` | `/rewind [n] [--chat-only]`：范围内找最早有效检查点 → describe 列清单 → y/N 确认 → restore 成功后再弹消息 |
| `settings.py` / example.toml | `[checkpoint]` enabled / max_per_session / timeout_seconds，`/config` 展示 |
| `workbench/engine.py` | `_handle_send` 轮前打点，finally 绑定（出错轮次同样可撤回） |
| `workbench/checkpoint_ops.py`（新建） | preview / rewind 编排（自 engine 拆出控行数），截断后 `_auto_save` 防残留 |
| `serve/protocol.py` / `server.py` | `checkpoint.preview` / `checkpoint.rewind` 入 `DESKTOP_COMMANDS`；`rewound` 事件 |
| 桌面 `contracts.ts` | Cmd 2 条 + `CheckpointFileChange` / `CheckpointPreviewResult` / `RewoundPayload` |
| 桌面 `chatStore.ts` | `rewindTailFromUser(userTailCount)`：按可见用户消息尾部计数裁列表并复位 busy/askPrompt |
| 桌面 `backendStore.ts` | `previewCheckpoint` / `rewindMessage`（runCommand 路由） |
| 桌面 `dispatcher.ts` | `case 'rewound'`：失败弹错；成功按 `removed_user` 裁气泡 + 系统提示 + 刷会话/成本 |
| 桌面 `ChatArea.tsx` + `chat.css` + `i18n` + `glyphs` | 用户气泡 hover「撤回」按钮（`[RWK]` glyph）；`RewindDialog`（loading / 预览失败 / 无检查点 / 文件清单 + 回滚复选框 + 确认/取消，reqId 防预览竞态）；14 键 zh+en |

## 测试

- 后端：`tests/core/test_checkpoint.py`（真 git 临时仓库：create/restore/describe/修剪/
  跨 workdir/非 git 目录）、store extra 往返 + 旧格式兼容、`/rewind` 三路联动、serve
  `checkpoint.*` 与 `rewound`；全量 `uv run pytest tests -q` **2329 passed**。
- 桌面：chatStore 5 + dispatcher 5 + backendStore 6 新用例；typecheck ✓、
  vitest **343 passed（18 文件）**、`npm run build` ✓。

## 已知边界（明确不做）

- 不回滚工作区之外的文件（如 `~/.jarvis` 内被工具改动的文件）；
- 被 `.gitignore`/exclude 排除的目录不参与回滚——这正是期望行为；
- 跨 workdir（项目切换后）不允许对旧目录回滚，仅提示；
- 检查点被配额修剪后旧消息只支持「仅对话回退」。

## 文档同步

README.md / README.en.md（命令表 + `[checkpoint]` 配置）、USER_GUIDE.md（「发错了怎么办」
场景 + 配置详解）、docs/architecture/00-索引、07-UI层（撤回交互链路）、09-记忆与压缩
（extra 持久化）、12-配置系统（新节）、**15-消息回溯与检查点.md（新增）**、
docs/test/TEST_CHECKLIST.md（T-349–T-358）、桌面 README / architecture.md（撤回章节）。

## Linux CI 平台差异修复（2026-10-08）

推送后 jarvis CI（ubuntu pytest）1 例失败：`test_workdir_key_stable_and_distinct` 断言
`_workdir_key(d) == _workdir_key(d.upper())`。根因：实现无条件 `.lower()` 归一后哈希 + 可读段
保留原始大小写，开发现实基于 Windows（resolve() 会把大小写异拼路径归一到盘上真实大小写，
两拼写天然同键）而编写了 Windows 语义断言；Linux 上 `Proj`/`PROJ` 本就是两个不同目录，
键理应区分。**修复**：`_workdir_key` 改为仅 `os.name == "nt"` 时小写归一，POSIX 保留原样；
测试用 `os.path.normcase("Aa") == "aa"` 探测文件系统语义分支断言（Windows 验同键、POSIX 建
同名异大小写真目录验区分）。本地 Windows 10/10、checkpoint+rewind 相关 42 例全过；
Linux 分支由 CI 重跑验收。
