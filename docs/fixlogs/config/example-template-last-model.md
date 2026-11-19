# 发布模板预置 last_model 导致分层失效（CI 2 failed）

**日期**：2026-09　**作者**：aceFelix　**类型**：配置分层缺陷修复

## 现象

GitHub CI（Linux）上 `pytest tests/` 稳定失败 2 项，本地 `pytest -q` 全绿：

```
tests/config/test_models_config.py::TestLoadSettingsLayering::test_auto_split_on_load_keeps_semantics
    assert 'qwen3.7-plus' == 'deepseek-chat'
tests/config/test_models_config.py::TestLoadSettingsLayering::test_models_file_does_not_wipe_non_model_tables
    assert 'qwen3.7-plus' == 'user-model'
```

两个用例都在 `tmp` 里造了临时 `~/.jarvis`（`patch(Path.home)`）并写入自己的模型值，
但最终 `s.model` 都是模板里的 `qwen3.7-plus`。

## 根因

三段独立机制拼出来的，缺一不显：

1. **模板携带运行时状态**：`agent/configs/models.example.toml` 与
   `configs/models.example.toml` 除 `model = "qwen3.7-plus"` 外还预置了
   `last_model = "qwen3.7-plus"`，而同文件头部注释自己写着「`last_model` 由程序回写」。
2. **项目层三级回落**：`load_settings` 依次找 `<workdir>/configs/settings.toml`
   → `<pkg_root>/configs/settings.toml` → `<pkg_root>/agent/configs/settings.example.toml`，
   同层再取 `models_path_for()` 推导出的 `models.toml`。而 `configs/settings.toml`
   与 `configs/models.toml` 都在 `.gitignore` 里，**CI 上不存在** → 项目层落到 example 模板。
3. **`last_model` 优先级最高**：[settings.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/config/settings.py) 的
   `load_settings` 尾部，只要 `s.last_model` 非空就覆盖 `s.model`，位置在所有配置层与环境变量之后。

于是 CI 上「模板层 last_model」一路压过用例在用户层写的 `model`。本地不复现是因为
`configs/models.toml` 存在（项目层落到它，而它的 `last_model` 为空）。

## 真实用户影响

不只是测试环境问题：按 README 把 `models.example.toml` 复制成 `configs/models.toml`
的用户，改文件里的 `model` **不生效** —— 模板带进来的 `last_model` 会永久压住它，
表现同「配置写了不生效」那一类老问题。`last_model` 属用户状态，出厂模板不该预置。

## 修复

1. 两份 `models.example.toml` 删除 `last_model` 行，并在原位注释说明「模板故意不写
   `last_model`：它由程序写入用户级配置且优先级高于 `model`」。
2. 新增 `TestExampleTemplateLayer` 两条防回归用例
   （[tests/config/test_models_config.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/config/test_models_config.py)）：
   - `test_models_template_carries_no_last_model`：模板顶层不得含 `last_model`，
     且 `model` 仍在（守住「发布默认层不携带用户状态」这条不变量）；
   - `test_models_templates_stay_identical`：仓库根 `configs/` 与包内 `agent/configs/`
     两份模板逐字一致，防止只改一处导致语义分叉。
3. 架构文档「八、last_model 持久化」补充该约束。

## 验证

零配置路径必须显式复现，否则本地绿不代表 CI 绿：

| 步骤 | 结果 |
|---|---|
| 临时移出 `configs/settings.toml` + `configs/models.toml`、暂存模板修改后跑 `tests/config/test_models_config.py` | 3 failed（CI 的 2 项 + 新增不变量用例）→ 复现成功 |
| 同样零配置状态下跑修复后的 `tests/config tests/llm/test_config_loading.py` | 89 passed |
| 恢复本地配置后全量 `pytest -q` | 1925 passed |

复现命令形态（`try/finally` 保证用户配置一定改名回来）：

```powershell
Move-Item configs\settings.toml ~\x1.off; Move-Item configs\models.toml ~\x2.off
try { python -m pytest tests/config -q } finally {
  Move-Item ~\x1.off configs\settings.toml; Move-Item ~\x2.off configs\models.toml }
```

## 教训

- **示例模板 = 发布形态下的项目层**，凡「零项目配置」时会回落到的文件，写进它的
  每一个键都在替所有层做决定；运行时状态键（`last_model` 这类程序回写字段）不入模板。
- 测试依赖 `patch(Path.home)` 只隔离了用户层，项目层仍可能落到仓库内真实文件 ——
  本地存在而 CI 不存在的配置（被 `.gitignore` 排除的那批）是典型的「本地绿、CI 红」源。
  遇到这种差异，先把本地独有的那层临时移走复现，再谈修。
