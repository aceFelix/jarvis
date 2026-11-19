# 启动自动恢复（auto-latest 全局指针）下线：多项目会话串台治理

- 日期：2026-10-02
- 仓库：jarvis（后端）；jarvis-desktop 配套撤销前端自动打开逻辑（另见该仓库提交）
- 作者：aceFelix

## 一、现象

桌面 jarvis 左栏历史会话里常驻一条 `auto-latest`（146 条消息）的"幽灵"条目并被
默认选中；用户同时在不同项目目录开多个会话时，担心（且实际会发生）所有项目的对话
被记录到同一个恢复指针里，重启后串台。

## 二、排查

1. `session_manager._auto_save`：每轮对话除了写 `session_name` 独立会话文件，还
   **无条件**把整份消息再写一份 `~/.jarvis/sessions/auto-latest.json`；
2. `main.repl()` 启动时若 `auto_resume_session=true`，调 `store.latest_session_name()`
   （= 全局 updated_at 最新的会话，**不校验 workdir**）直接加载进上下文；
3. `store.list_sessions()` 遍历 sessions 目录所有 json，把指针文件当普通会话上报 →
   桌面左栏幽灵条目；
4. 桌面引擎（workbench engine）启动其实不读指针，每次都是新会话——被坑的是 CLI
   自动恢复 + 列表污染；前端一度加了"首启自动打开列表首项"，与本机制叠加，已撤销。

## 三、根因

`auto-latest` 是**全局单文件恢复指针**，与项目目录（workdir）零绑定：

- 多项目并行时互相覆盖，最后保存的进程赢；
- CLI 在任意目录启动都可能恢复出**别的项目**的对话，上下文串台污染；
- 指针文件混在会话目录里，列表出现无标题的幽灵条目。

## 四、修复（彻底下线自动恢复）

| # | 改动 | 文件 |
|---|---|---|
| 1 | `_auto_save` 删除写 `auto-latest` 指针的第二次 save_session；`session_name` 默认值改空串 | `agent/session_manager.py` |
| 2 | 删除 `_move_or_copy_pointer`（指针复制特例），改名统一 `rename`；移除 `shutil` 导入 | `agent/session_manager.py` |
| 3 | `list_sessions()` 对 `auto-latest*` 遗留文件**见到即删**且不上报（幽灵条目自动清史） | `agent/core/memory/store.py` |
| 4 | 删除 `latest_session_name()` | `agent/core/memory/store.py` |
| 5 | 删除 `auto_resume_session` 启动恢复块（崩溃恢复点检测保留不动）；更新跳过恢复注释 | `agent/main.py` |
| 6 | 删除 `auto_resume_session` 字段、持久化键、TOML 映射（旧配置残留键静默忽略） | `agent/config/settings.py` |
| 7 | `/config` 上下文表移除该行；example.toml 删配置项并注明下线原因 | `config_commands.py`、`settings.example.toml` |

行为变化：启动即全新会话；历史会话靠 `/load`（桌面左栏点选）手动恢复；崩溃恢复
（异常退出恢复点 + [y/N] 询问）不受影响。

## 五、验证

- 定向：`uv run pytest tests/test_session_manager.py tests/memory/test_store.py tests/ui -q` → 243 passed
- 全量：`uv run pytest tests -q` → **2286 passed**（净 -1：删 latest_session_name 2 条、
  指针复制保留 1 条改为改名语义、_auto_save 双写改单写、新增幽灵清理 1 条）
- 测试更新：`test_saves_only_named_session`（单写断言）、
  `test_list_sessions_purges_auto_latest_pointer`（幽灵清理）、
  `test_rename_moves_file_without_copy`（改名无复制特例）

## 六、涉及文件

- `agent/session_manager.py`、`agent/core/memory/store.py`、`agent/main.py`、
  `agent/config/settings.py`、`agent/commands/handlers/config_commands.py`、
  `agent/configs/settings.example.toml`
- 测试：`tests/test_session_manager.py`、`tests/memory/test_store.py`
- 文档：`README.md`、`docs/architecture/01-总体架构.md`、`09-记忆与压缩.md`、
  `12-配置系统.md`、`docs/test/TEST_CHECKLIST.md`、`docs/roadmap/jarvis-upgrade-roadmap.md`
- 桌面配套：`jarvis-desktop` 撤销 `autoRestoreDone` 首启自动打开（backendStore + 测试 + 文档）

## 七、经验

- **全局单指针 + 多项目工作流 = 必然串台**：任何"上次/最近"类状态都必须带作用域
  （workdir/项目 id），否则跨项目互相污染；
- 指针/元数据文件不要与会话数据同目录混放，否则列表类接口天然把它当业务对象；
- 删特性要删穿：字段、TOML 映射、展示行、示例配置、测试、文档一处不留，并对用户
  旧配置残留键做静默忽略保证兼容；
- 前端曾为绕过后端缺陷加过"首启自动打开"补丁，后端治理后应同步撤销，避免双层机制叠加。
