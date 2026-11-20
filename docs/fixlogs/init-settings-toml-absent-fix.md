# 首启配置后不生成 settings.toml：按需落盘的设计与体验缺口

> 状态：**已实施**（2026-11-20；`--init` 后生成全注释骨架 + 打印配置指引）
> 发现版本：PyPI `jarvis-agent 2.6.0`
> 结论先行：`jarvis --init` 只写 `models.toml` 是**当前设计的正确行为**；
> 《装完没有 settings.toml》属于**体验缺口**，建议按第 4 节改进。

## 1. 问题现象

全新安装（`pip install "jarvis-agent[all]"`）并执行 `jarvis --init` 后：

```
C:\Users\<user>\.jarvis\
├── models.toml          ← 有
├── usage.json
├── sessions/
├── memory/
├── checkpoints/
└── （无 settings.toml）  ← 用户预期这里应该有
```

用户预期：安装/初始化后应在 `~/.jarvis/` 看到一份完整配置文件。
实际：只有模型域文件；`settings.toml` 直到**第一次显式修改非模型配置**才出现。

## 2. 排查过程

### 阶段一：确认 init 的写盘目标（2.6.0 与本地 2.5.4 一致）

`agent/commands/handlers/init_command.py`：

```python
def _save_config(vendor, api_key, model_type="text") -> Path:
    """保存配置到 ~/.jarvis/models.toml（模型域专属文件）。
    2026-09 模型域拆分后，写入实现统一在 agent/config/models_config.py
    （save_init_model）……
    """
    from agent.config.models_config import save_init_model
    return save_init_model(...)
```

模块文档也写明：`/init`、`/init full`、`/init voice` 三者**共用同一套写盘**
（`_save_config` → `models_config.save_init_model`），文件头明确"配置已保存到
`C:\Users\<user>\.jarvis\models.toml`"——这正是实测输出。

### 阶段二：确认 `load_settings` 只读不写

对 2.6.0 已安装包 grep `write_text|save_section|_write_toml`：

```
agent/config/settings.py → 仅命中 def load_settings（无任何写盘调用）
```

`load_settings` 的职责是"默认值 → 项目/用户 TOML → 环境变量 → CLI"的**合并**，
缺失文件按默认值处理，解析失败打 stderr 并继续用默认值。它从不创建文件。

### 阶段三：找出真正的写入入口

| 入口 | 触发方式 | 落点 |
|---|---|---|
| `config_ops.apply_config_patch()` | `/config set`、模型侧 `ConfigSet` 工具、桌面设置面板 | 校验 → 落盘 → 生效 → 广播 → 记账 |
| `toml_writer.save_section()` | 上述链路的底层外科式写入 | 只创建/改目标节，保留用户注释 |
| `desktop_settings.save_setting()` | 桌面面板单项保存 | 由 `config_specs` 真源派生 |
| `email_settings` / `percept_settings` | 邮箱、感知面板保存 | 各自的 `[email]` / `[percept]` 节 |
| `models_config.save_init_model()` | `jarvis --init`、`/models` 增改 | **`models.toml`（模型域）** |

即：`settings.toml` 是"按需生成的外科式落盘结果"，不是安装产物。

## 3. 根因分析

1. **模型域拆分是设计决策**：2026-09 起模型/密钥/端点等模型域配置迁到
   `models.toml`，`settings.toml` 只承载非模型配置。`--init` 的业务范围就是
   "厂商 / API Key / 模型"三问，因此只碰 `models.toml`。**行为正确**。
2. **默认值全在代码里**：`agent/config/settings.py` 的 `Settings` dataclass 提供
   全部默认值，没有 `settings.toml` 也能完整运行（实测 `jarvis` 能起、能发请求）。
3. **缺的是"配置文件可见性"**：
   - 用户在 `~/.jarvis/` 看不到 `settings.toml`，第一反应是"安装失败/配置丢了"；
   - 仓库根的 `configs/settings.example.toml` 是**私有不打包**（`MANIFEST.in`：
     仅 `agent/configs/*.toml` 作为 package-data 发布），用户目录没有模板可参考；
   - init 收尾提示里也没有一句"其余配置在哪、何时落盘、怎么查"。

同类历史教训可佐证"配置不可见 = 用户认为不存在"：`llm-models-header-missing-fix.md`
（缺 `[llm.models]` 表头导致配置被静默忽略）、`config-panel-not-refreshed-after-model-set-fix.md`
（写盘了但面板没刷新 → 用户以为没生效）。

## 4. 改进方案（待实施）

### 推荐：init 后生成中性模板 + 一句提示

1. `jarvis --init` 成功收尾时，若 `~/.jarvis/settings.toml` **不存在**，从
   **包内** `agent/configs/settings.example.toml` 复制一份；
2. 复制后不写入任何运行时状态：**禁止预设 `last_model`、`model`、`provider`
   等运行期键**（`example-template-last-model.md` 的事故：模板预设 `last_model`
   破坏配置分层并让两条 CI 用例变红）；
3. 收尾多打一行：

   ```
   其余配置：/config list 查看可改项 · /config explain <键> 查看说明与原值
   配置文件：C:\Users\<user>\.jarvis\settings.toml（仅在你修改后才会写入实际值）
   ```

4. 若无法写盘（权限/只读 HOME），**降级为只打印提示**，不得让 init 失败。

### 备选（更保守）

不生成文件，仅做上面的第 3 步提示。缺点是用户仍然没有可参考的模板。

### 硬约束

- 必须从**包内路径** `agent/configs/settings.example.toml` 读取，不能读仓库根
  `configs/`（不打包，装包后不存在）；
- 模板必须能被 `load_settings` 正常解析（全注释即空语义，不能破坏分层合并）；
- 新字段仍按既有四处同步纪律：`config_specs` / `settings_domains` / 两份
  `*.example.toml` / 架构文档；
- 若用户已存在 `settings.toml`，**绝不覆盖**（外科式写入是既有纪律）。

## 5. 验证结果

**已实施并跑通测试（2026-11-20）**：

- `agent/commands/handlers/init_command.py` 新增 `_ensure_settings_scaffold()`：
  `~/.jarvis/settings.toml` 缺失时写入全注释骨架（无任何运行期键），已存在则
  原封不动；写盘失败只降级为不打印路径，**不会让 `--init` 失败**；
- `--init` 收尾多打一行 `✓ 用户配置模板: …` 与 `/config list | explain` 指引；
- `tests/commands/test_init_routing.py` 新增 `TestSettingsScaffold`：断言骨架
  逐行均为注释、二次调用不覆盖。

**与本文原方案的差异**：没有直接复制 `settings.example.toml`——该模板含
`workdir = "./workspace"` 等**未注释的实际键**，复制到用户目录会改变默认工作
目录。改用「纯注释骨架」，既不改变任何默认值，又让配置文件可见。

**仍待实机验证**：

1. 全新 HOME（或用临时 `HOME`/`USERPROFILE` 指向空目录）执行 `jarvis --init`
   → `~/.jarvis/settings.toml` 存在且带注释；
2. `jarvis` 能正常启动，`/config list`、`/config explain checkpoint.timeout_seconds`
   均可用（说明模板未破坏 `catalog` 全字段覆盖）；
3. 手动把 `[checkpoint] timeout_seconds = 30` 写入后重启 → 生效（证明模板与
   `settings_domains` 映射一致）；
4. 回归：`tests/test_release_contract.py` 与配置分层用例全绿（模板中性）；
5. 已存在 `settings.toml` 的用户再次 `--init` → 文件未被覆盖/未被截断。

## 6. 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/commands/handlers/init_command.py` | init 收尾生成模板（缺失时）+ 打印配置指引 |
| `agent/configs/settings.example.toml` | 作为模板来源；保持中性、不得预设运行期键 |
| `MANIFEST.in` | 复核 `agent/configs/*.toml` 已进包（现网已包含） |
| `docs/guide/installation.md` | 说明"首启只写 models.toml，其余配置按需落盘" |
| `docs/fixlogs/init-settings-toml-absent-fix.md` | 本复盘 |

## 7. 经验总结

- **"配置文件看不见"就等于"用户认为不存在"**。安装/初始化是用户建立心智模型的
  时刻，产物清单必须与用户预期对齐，否则会被读成安装失败。
- **默认值住在代码里不是问题，"默认值不可见"才是问题**。一份带注释的模板
  比十行文档更有效。
- **模板必须中性**：它只是说明，不是运行时状态。预设 `last_model`/`model`
  这类键会直接破坏分层与 CI（有前车之鉴）。
- **写盘入口必须收敛**：本次排查之所以能快速定性，是因为写入路径只有
  `config_ops` / `toml_writer` / 域级面板三类，`load_settings` 明确只读。
  新增配置写入口时必须沿用这条纪律。
