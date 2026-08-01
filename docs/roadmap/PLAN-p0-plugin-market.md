# P0 Plugin 市场完善升级方案

> 优先级：🔴 P0  
> 目标：把 Jarvis Plugin 系统从 ⭐⭐ 提升到 ⭐⭐⭐，让 Plugin 市场和 CLI-Anything harness 市场彻底独立、本地市场可搜索、命令清晰不混淆，用户能方便地搜索/安装/管理插件。
> 参考：VS Code 扩展市场、npm registry、ClaudeCode `unified-extensions` 设计思路。

---

## 一、目标与验收标准

### 1.1 目标

1. **Plugin 与 harness 市场彻底分离**：Plugin 系统只管理基于 `plugin.json` + `skills/` 的插件；CLI-Anything 只管理基于 `SKILL.md` + `run.py` 的 harness。两者命令、逻辑、仓库互不干扰。
2. **本地插件市场可搜索**：支持配置本地插件市场目录，搜索时合并远程 `marketplace.json` 和本地目录，本地插件优先。
3. **远程市场保持可用**：默认从 GitHub `aceFelix/jarvis-plugins` 拉取 `marketplace.json`。
4. **安装逻辑完善**：本地插件直接复制；远程插件 `git clone` 后提取 skills、合并 MCP、更新 `installed.json`。
5. **命令体系清晰**：
   - `/plugin search/install/uninstall/list/info/check-updates/enable/disable/create/validate` 管理 Plugin
   - `/cli_anything market/install/uninstall/list/enable/disable/create/validate` 管理 harness
6. **移除 AI 统一市场搜索工具**：不再通过 `MarketSearchTool` 把两个市场混在一起交给 AI 决策。
7. **统一市场门面退化为通用层**：`UnifiedMarket` 仅用于跨市场通用命令的底层实现，不替代独立命令。

### 1.2 验收标准

- [ ] `/plugin search` 能搜到远程 `marketplace.json` 中的插件。
- [ ] `/plugin search` 能搜到本地 `jarvis-plugins/plugins/<name>/plugin.json` 中的插件。
- [ ] 本地插件与远程插件同名时，本地版本优先。
- [ ] `/plugin install <name>` 能从本地或远程市场正确安装。
- [ ] `/cli_anything market` 仍能独立工作，不受 Plugin 改动影响。
- [ ] 删除 `MarketSearchTool`，AI 不再统一搜索两个市场。
- [ ] `UnifiedMarket` 文档/注释明确说明其职责边界。
- [ ] 更新 `README.md`、`docs/architecture/10-扩展生态.md`。
- [ ] 新增/修改代码通过 `python -m py_compile` 检查。

---

## 二、当前状态与差距

### 2.1 已具备能力

| 模块 | 能力 | 文件 |
|---|---|---|
| PluginManager | 远程 marketplace.json 拉取、git clone 安装、skills 复制、MCP 合并 | `agent/core/extensions/plugins.py` |
| CLI-Anything harness | registry.json 解析、pip 型/目录型安装、market 命令 | `agent/cli_anything/market.py` |
| 统一市场 | 跨市场搜索、enable/disable/create/validate 通用功能 | `agent/core/extensions/market.py` |
| 配置 | 远程市场 URL、本地市场目录字段 | `agent/config/settings.py` |

### 2.2 关键差距

| 差距 | 影响 | 优先级 |
|---|---|---|
| PluginManager 不支持本地插件目录扫描 | 用户本地 `jarvis-plugins` 中的插件搜不到 | 高 |
| Plugin 与 harness 市场被 AI 统一搜索工具混在一起 | 用户困惑，命令边界不清 | 高 |
| `UnifiedMarket` 职责描述不清 | 容易误解为替代独立命令 | 中 |
| 本地市场目录结构未知（仓库根还是 `plugins/` 子目录） | 代码无法正确加载 | 高 |

---

## 三、详细设计

### 3.1 本地市场目录结构

本地市场仓库 `jarvis-plugins` 采用如下布局（与远程仓库一致）：

```text
jarvis-plugins/
├── marketplace.json          # 远程市场清单
├── plugins/
│   ├── weather/
│   │   ├── plugin.json
│   │   └── skills/
│   ├── everything-sdk/
│   │   ├── plugin.json
│   │   └── skills/
│   └── speech-sense/
│       ├── plugin.json
│       └── skills/
└── README.md
```

扫描时同时识别：

1. `<marketplace_local>/plugins/<name>/plugin.json`
2. `<marketplace_local>/<name>/plugin.json`（兼容扁平布局）
3. `<marketplace_local>/marketplace.json` 中的条目，若 `source.type == "local"` 或 `subdir` 指向本地目录

### 3.2 PluginManager 本地市场加载

```python
def _load_local_marketplace(self) -> list[dict[str, Any]]:
    """扫描本地插件市场目录，注入 _is_local / _local_path / source。"""
    if not self._marketplace_local:
        return []
    root = Path(self._marketplace_local)
    if not root.is_dir():
        return []

    # 1. 扫描 plugins/ 子目录
    # 2. 扫描根目录
    # 3. 读取本地 marketplace.json 并映射本地源
```

合并规则：

```python
# 远程 + 本地合并，本地覆盖远程同名插件
seen: set[str] = set()
merged: list[dict[str, Any]] = []
for p in plugins + local_plugins:
    name = p.get("name", "")
    if name in seen:
        merged = [x for x in merged if x.get("name") != name]
    merged.append(p)
    seen.add(name)
```

### 3.3 本地插件安装

```python
source = entry.get("source", {})
src_type = source.get("type", "")

if src_type == "local" or entry.get("_is_local"):
    local_path = Path(entry.get("_local_path") or source.get("path", ""))
    return self._install_from_dir(local_path, source_label=str(local_path))
```

提取公共方法 `_install_from_dir(plugin_root, source_label)`：

1. 读取 `plugin.json`
2. 复制 `skills/` 中列出的目录到 `~/.jarvis/skills/`
3. 合并 `.mcp.json` 到 `~/.jarvis/mcp.json`
4. 更新 `~/.jarvis/plugins/installed.json`

### 3.4 路径解析增强

Settings 中对 `plugin_market_local` / `harness_market_local` 做相对路径解析：

```python
def _resolve_local_path(value: str, workdir: Path, pkg_root: Path) -> str:
    p = Path(value)
    if p.is_absolute():
        return str(p)
    # 先按 workdir 解析，不存在则回退 jarvis 项目根目录
    candidate = workdir / p
    if candidate.exists():
        return str(candidate.resolve())
    return str((pkg_root / p).resolve())
```

### 3.5 市场命令分离

| 命令 | 归属 | 说明 |
|---|---|---|
| `/plugin search [keyword]` | PluginManager | 搜远程 + 本地 |
| `/plugin install <name>` | PluginManager | 本地优先 |
| `/plugin uninstall <name>` | PluginManager | 删除 skills + MCP + installed.json |
| `/plugin list` | PluginManager | 已安装列表 |
| `/plugin info <name>` | PluginManager | 插件详情 |
| `/plugin enable/disable <name>` | PluginManager | 启用/禁用 |
| `/plugin create <name>` | PluginManager | 生成 plugin 脚手架 |
| `/plugin validate <path>` | PluginManager | 校验 plugin.json |
| `/cli_anything market [keyword]` | harness | 搜 harness 市场 |
| `/cli_anything install <id>` | harness | 安装 harness |
| `/cli_anything uninstall <id>` | harness | 卸载 harness |
| `/cli_anything list` | harness | 已安装 harness |
| `/cli_anything enable/disable <id>` | harness | 启用/禁用 harness |
| `/cli_anything create <id>` | harness | 生成 harness 脚手架 |
| `/cli_anything validate <path>` | harness | 校验 SKILL.md |

### 3.6 删除 MarketSearchTool

删除文件 `agent/tools/extensions/marketplace_tool.py`，并从以下位置移除注册：

- `agent/core/tool.py`
- `agent/tools/__init__.py`
- system prompt 中的相关描述

### 3.7 简化 UnifiedMarket

保留 `UnifiedMarket` 类，但仅作为 `enable/disable/create/validate` 的底层实现。文档和注释明确：

> UnifiedMarket 不替代 `/plugin` 和 `/cli_anything` 独立命令，只用于跨市场通用功能的代码复用。

若用户坚持“各管各的”，则进一步拆分：
- `/plugin enable/disable/create/validate` 下沉到 `PluginManager`
- `/cli_anything enable/disable/create/validate` 下沉到 harness market
- 删除 `UnifiedMarket`

---

## 四、实施步骤

| 步骤 | 内容 | 产出文件 | 风险 |
|---|---|---|---|
| 1 | Settings 新增本地市场字段并解析相对路径 | `settings.py` | 低 |
| 2 | PluginManager 支持本地目录扫描和本地安装 | `plugins.py` | 中 |
| 3 | main.py 命令传入本地市场参数 | `main.py` | 低 |
| 4 | 删除 MarketSearchTool 及其注册 | `marketplace_tool.py`, `tool.py` | 低 |
| 5 | 明确 UnifiedMarket 职责并更新注释 | `market.py` | 低 |
| 6 | （可选）完全拆分 enable/disable/create/validate | `plugins.py`, `cli_anything/market.py`, `market.py` | 中 |
| 7 | 编写/更新文档 | `README.md`, `10-扩展生态.md` | 低 |

---

## 五、测试计划

### 5.1 单元测试

- 本地市场扫描：分别测试 `plugins/` 子目录、根目录、`marketplace.json` 三种来源。
- 合并覆盖：本地同名插件覆盖远程。
- 路径解析：相对路径、绝对路径、Git Bash 风格路径 `/e/...`。

### 5.2 集成测试

- 配置 `plugin_market_local = "../jarvis-plugins"`
- 执行 `/plugin search` 验证本地插件出现
- 执行 `/plugin install <local-plugin>` 验证 skills 复制

### 5.3 验证命令

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
python -m py_compile agent/core/extensions/plugins.py agent/config/settings.py agent/main.py
```

---

## 六、文档更新

| 文档 | 更新内容 |
|---|---|
| `README.md` | Plugin 市场命令说明、本地市场配置 |
| `docs/architecture/10-扩展生态.md` | 明确 Plugin/harness 独立命令，删除统一搜索描述 |
| `docs/fixlogs/` | 记录本地插件搜不到的修复过程（如适用） |

---

## 七、风险与应对

| 风险 | 应对 |
|---|---|
| 本地市场目录结构多样 | 同时支持 `plugins/`、根目录、`marketplace.json` 三种来源 |
| 删除 MarketSearchTool 影响 AI 行为 | AI 仍可通过 `/plugin search` 和 `/cli_anything market` 独立调用 |
| 用户已安装的 UnifiedMarket 依赖 | 保留通用方法，仅删除工具和混淆文档 |
| 本地与远程同名插件版本冲突 | 本地优先，并在 info 中显示来源 |

---

## 八、备注

- 本方案严格区分 Plugin 和 harness 两个市场，避免概念混淆。
- 所有修改遵循现有代码风格：dataclass、Javadoc/行内注释、`@author aceFelix`。
