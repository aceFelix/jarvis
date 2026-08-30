# 修复：用户新增模型不生效（缺失 [llm.models] 表头 + 解析失败被静默吞掉）

- **日期**：2026-08-29
- **作者**：aceFelix
- **影响范围**：`~/.jarvis/settings.toml`（用户配置）、`agent/config/settings.py`、`configs/settings.example.toml`

## 现象

用户在 `~/.jarvis/settings.toml` 追加了 4 个新的阿里模型（qwen3.8-max / qwen3.8-falsh /
qwen3.7-max / qwen3.7-flash），但 `/models` 的「阿里云 DashScope」分组仍只显示内置模板的
5 个通义千问模型，新增模型不生效。

## 根因（两层）

1. **配置层**：模型条目上方缺少 `[llm.models]` 表头（注释里提到该表头，但表头行本身丢了）。
   TOML 把这些条目解析成 `[llm]` 表下的普通键，`llm.models` 子表为空，
   `/models` 回退到内置模板 `agent/configs/settings.example.toml` 的 5 个默认模型。
2. **代码层**：`_read_toml` 对解析失败 `except Exception: return {}` 静默吞错。
   即使配置有语法问题（如换行符被工具写成 `\r\r`），用户也看不到任何报错，
   表现为"配置写了但就是不生效"，极难排查。

## 修复

1. 在 `~/.jarvis/settings.toml` 模型条目前补回 `[llm.models]` 表头（CRLF 行尾对齐原文件）。
2. `agent/config/settings.py` `_read_toml`：解析失败时向 stderr 输出文件路径与原因，
   仍返回空配置回退默认值，不阻断启动。
3. `configs/settings.example.toml`：在 `[llm.models]` 注释处增加醒目警告
   ——表头行不可省略，缺失会导致新增模型不生效。

## 验证

- `tomllib` 解析：`llm.models` 9 个条目全部就位，`api_key` 仍在 `[llm]` 未混入。
- 端到端 `load_settings()`：`settings.models` 数量 = 9（含 4 个新增），
  `model`/`last_model`/`custom_models`（6 个）均正常。
- `pytest -k settings`：8 passed。

## 教训

- **静默吞错是配置类 bug 的放大器**：解析/加载失败至少要留一条可见线索（stderr/日志）。
- **改 TOML 文件必须逐字节核对换行符**：工具写入 `\n` 混入 CRLF 文件，或误产生 `\r\r`，
  都会让 tomllib 报 `Expected newline...`；修复后要按字节验证而非仅文本预览。
- **TOML 表头是语义边界**：表头缺失不会报错，键会静默归入上一个表——
  配置模板里必须用注释强调表头不可省略。
- PowerShell `Get-Content`/`Set-Content` 处理非 ASCII 文件易引入编码损伤与 BOM，
  修改用户配置优先用 Python 按字节操作。
