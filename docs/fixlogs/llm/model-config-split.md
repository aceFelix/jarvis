# 改造：模型配置拆分到 models.toml（密钥与模型配置同文件）

- **日期**：2026-09-26
- **作者**：aceFelix
- **影响范围**：`agent/config/models_config.py`（新增）、`agent/config/settings.py`、
  `agent/config/model_registry.py`、`agent/model_manager.py`、`agent/commands/handlers/init_command.py`、
  `configs/*.toml`、`~/.jarvis/settings.toml`（拆分出 `~/.jarvis/models.toml`）

## 背景与目标

模型配置（12 个顶层键 + `[llm]` / `[llm.models]` / `[llm.custom_models.*]`）此前与运行时配置
同放在 `settings.toml`，带来两个问题：

1. **程序频繁回写易踩坑**：`/models`、`jarvis init`、切换模型都会改写文件，一旦顶层键
   落在 `[section]` 之后，就会被 TOML 静默解析进子表（参见
   [llm-models-header-missing-fix.md](llm-models-header-missing-fix.md)）。
2. **密钥与无关配置混排**：轮换密钥、单独备份模型配置都不方便。

用户要求：模型配置分出去，且**模型密钥必须和模型配置放在同一个文件**。

## 方案

- **边界**：12 个模型域顶层键（`provider` / `api_format` / `model` / `last_model` / `api_key` /
  `base_url` / `dashscope_api_key` / `max_tokens` / `temperature` / `enable_thinking` /
  `thinking_budget` / `vendor_fallback`）+ `[llm*]` 段；`[tts.custom_voices]` 等仍留 settings.toml。
- **加载**：settings/models 四文件分层（项目级 → 用户级，同层 models 覆盖 settings）。
- **写入**：所有回写点统一走 `models_config`（`upsert_top_scalar` / `upsert_table_block` /
  `upsert_custom_model` / `upsert_last_model` / `save_init_model` / `remove_custom_model`）。
- **迁移**：启动期 `auto_split_user_config` 幂等拆分，仅首次创建 `.bak`；失败静默 + 诊断日志。

## 实施中修掉的真 bug（5 个）

1. **段匹配失效（9 个测试失败的根因）**：`upsert_table_block` 传入的 header 带方括号，
   而 `_normalize_section` 未剥离方括号 → 目标名与正则捕获组永不相等，段永远不会被替换，
   只反复追加重复段。修复：归一化时 `.strip("[]")`。
2. **用户级 models.toml 读不到**：用 `models_path_for(user_cfg)` 推导路径会跟随
   `~/.my-agent` 回退，导致 `~/.jarvis/models.toml` 被完全忽略（表现为模型选择回落到项目级值）。
   修复：新增 `user_config_paths()` 成对返回 settings/models 路径，`settings.py` 与所有写入点共用。
3. **下一段注释被上一段带走**：块的「空行 + 注释」被算进上一块，`# 语音` 之类的段落说明被搬进
   `models.toml`，源文件反而丢注释、两段贴在一起。修复：`_detach_next_leading` 把尾部注释与
   分隔空行交还给下一块。
4. **孤立注释永不清除**：`remove_custom_model` 用 substring 判断「是否还有自定义模型段」，
   而 `models.toml` 头部说明里也含该文本 → 判断恒为真。修复：改行首正则
   `^\[llm\.custom_models\.`（`re.MULTILINE`）。
5. **模型域文件清空 LSP 配置（拆分后暴露的既有缺陷）**：`_apply_toml` 处理 `[lsp.servers]` 时
   用 `data.get("lsp", {})` + `isinstance(..., dict)`，无法区分「没有 `[lsp]` 表」与「表为空」，
   加载不含 `[lsp]` 的 `models.toml` 会把先从 `settings.toml` 读到的 `lsp_servers` 清空。
   由用户级全字段比对发现（副本里有 python server 配置，实际加载后为空）。
   修复：改为 `data.get("lsp")` / `lsp_table.get("servers")`，缺表或缺子表即跳过，
   显式空子表仍可表意清空；其余表（tts / memory / sandbox / llm 等）已逐段审计，
   均为「键存在才覆盖」，无同类问题。

## 验证

- **语义等价**：以 `dataclasses.fields(Settings)` 全字段快照 + `difflib` 比对，
  项目级配置三次重跑拆分均输出 `IDENTICAL`；用户级以 `settings - 副本.toml` 为基准
  与「当前 `settings.toml` + `models.toml`」做 121 字段比对，输出 `IDENTICAL`
  （含 provider / api_format / model / last_model / base_url / api_key / max_tokens /
  enable_thinking / thinking_budget / models 9 项 / custom_models 6 项 / tts_voice）。
- **测试**：`pytest tests/config tests/llm -q` → 465 passed；全量 `pytest -q` → 1852 passed
  （新增 2 个回归用例：`test_absent_section_does_not_wipe_existing_values`、
  `test_models_file_does_not_wipe_non_model_tables`）。
- **模板链路**：`models_path_for(agent/configs/settings.example.toml)` 指向
  `agent/configs/models.example.toml`（存在），两个示例模板叠加 `_apply_toml` 后模型域字段
  （model / provider / api_format / max_tokens / enable_thinking / thinking_budget / models 5 项）全部正确。
- **向后兼容**：模型键留在 `settings.toml` 的老配置仍被 `_apply_toml` 正常读取。

## 教训

- **不要把 `*.bak` 当作「刚刚那次改动」的备份回滚**：拿它覆盖前必须核对时间戳。
  本次实施中曾误用 8/29 的 `settings.toml.bak` 覆盖当天在用的用户配置，
  所幸用户自己留有当日副本（`settings - 副本.toml`），按它恢复后逐字段校验一致。
  **改动用户真实配置前，先做带时间戳的独立备份，不依赖任何既有 `.bak`。**
- **块级迁移算法的难点是「注释归属」而非键值搬运**：测试必须覆盖块首注释、块尾注释、
  分组空行、文件头注释这几类边界，否则会出现「值搬对了、说明搬丢了」。
- **路径推导要有单一事实来源**：settings 与 models 必须成对解析，
  否则「settings 读老目录、models 读新目录」的错配极难发现（本次即由该错配引出 bug 2）。
