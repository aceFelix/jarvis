# CLI-Anything 模块测试与使用指南

本文档覆盖 `agent/cli_anything/` 模块的测试方法、验收标准，以及如何在 Jarvis 中通过 harness 操作电脑桌面上的任意软件。

---

## 1. 模块定位

CLI-Anything 是 Jarvis 的**外部软件控制层**。它把第三方软件（Blender、Obsidian、GIMP、Godot、Safari 等）包装成 Agent 可调用的 `cli_anything__<id>` 工具，使 Jarvis 不再受限于内置工具，理论上能操作任何提供命令行接口的软件。

核心组件：

| 文件 | 职责 |
|------|------|
| `agent/cli_anything/schema.py` | `Harness`、`HarnessArg` 数据模型 |
| `agent/cli_anything/loader.py` | 扫描 `~/.jarvis/cli_anything/` 与 `<workdir>/.jarvis/cli_anything/`，解析 `SKILL.md` |
| `agent/cli_anything/runner.py` | 无 shell 子进程执行、参数转换、超时控制 |
| `agent/cli_anything/registry.py` | 把 harness 注册为 `ToolRegistry` 中的工具 |
| `agent/cli_anything/market.py` | `/cli_anything install/list/market/uninstall` 命令实现 |
| `agent/cli_anything/migrate.py` | 把官方 CLI-Anything `SKILL.md` 迁移为 Jarvis 格式 |
| `agent/tools/extensions/cli_anything_tool.py` | `CliAnythingTool`：权限、执行、图片自动编码 |

---

## 2. 测试原理

### 2.1 harness 生命周期

```text
SKILL.md + run.py
      ↓
loader.discover_harnesses() 扫描并解析
      ↓
registry.discover_and_register() 包装为 CliAnythingTool
      ↓
ToolRegistry 中出现 cli_anything__<id>
      ↓
LLM 在 system prompt 中看到能力说明
      ↓
用户说 "用 Blender 建一个立方体"
      ↓
orchestrator 调用 cli_anything__blender
      ↓
runner.run_harness() 无 shell 执行 run.py
      ↓
CliAnythingTool 解析 stdout（JSON 时自动提取图片）
      ↓
结果回传给 LLM / UI
```

### 2.2 安全模型

- **命令白名单**：`runner.py` 只允许 `python/python3/node/npm/npx/cli-anything-*` 开头的命令，拒绝裸 `cmd/bash`。
- **无 shell 执行**：使用 `asyncio.create_subprocess_exec`，参数按列表传入，避免注入。
- **默认 ASK**：`CliAnythingTool.check_permissions()` 默认返回 `PermissionResult.ask(...)`，执行前必须用户确认。
- **超时熔断**：默认 120 秒，超时强制 `proc.kill()`。
- **未知参数拒绝**：`runner._build_args()` 会拦截 harness `args` 中未声明的参数。

### 2.3 项目级覆盖用户级

`loader.discover_harnesses()` 扫描顺序：

1. `~/.jarvis/cli_anything/`
2. `~/.my-agent/cli_anything/`（兼容旧目录）
3. `<workdir>/.jarvis/cli_anything/`

扫描时维护 `seen_ids`，后扫描的项目级 harness 会**覆盖**先扫描的用户级同名 harness，实现项目内自定义软件行为。

---

## 3. 环境准备

### 3.1 最小测试 harness（test-echo）

在 `~/.jarvis/cli_anything/test-echo/` 下创建：

**SKILL.md**

```markdown
---
name: TestEcho
id: test-echo
description: 测试 harness，用于验证 CLI-Anything 模块是否正常工作
when_to_use: 当需要验证 harness 系统时使用
command: python
args:
  - name: subcommand
    type: string
    required: true
    description: 要传给 echo 的内容
---

# Test Echo
```

**run.py**

```python
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--subcommand", required=True)
parser.add_argument("--harness-dir", default="")
parser.add_argument("--workdir", default="")
args = parser.parse_args()

print(json.dumps({
    "echo": args.subcommand,
    "harness_dir": args.harness_dir,
    "workdir": args.workdir,
}, ensure_ascii=False))
```

### 3.2 确认目录结构

```powershell
Test-Path "$env:USERPROFILE\.jarvis\cli_anything\test-echo\SKILL.md"
Test-Path "$env:USERPROFILE\.jarvis\cli_anything\test-echo\run.py"
```

---

## 4. 测试清单

| 编号 | 测试项 | 类型 | 关键命令/入口 |
|------|--------|------|---------------|
| TC-01 | 模块导入无报错 | 冒烟 | `python -c "from agent.cli_anything import *"` |
| TC-02 | 已安装 harness 列表 | 功能 | `/cli_anything list` |
| TC-03 | 市场 harness 列表 | 功能 | `/cli_anything market` |
| TC-04 | 安装 harness | 功能 | `/cli_anything install <id>` |
| TC-05 | 卸载 harness | 功能 | `/cli_anything uninstall <id>` |
| TC-05b | 远程失败回退本地 | 兼容 | 断开网络或篡改 raw URL 后安装 |
| TC-06 | harness 执行与参数传递 | 功能 | `run_harness()` / LLM 调用 |
| TC-07 | 项目级覆盖用户级 | 功能 | 创建同名项目级 harness |
| TC-08 | JSON 中图片自动编码 | 功能 | harness 输出图片路径 |
| TC-09 | 命令白名单拦截 | 安全 | 创建 `cmd.exe` harness |
| TC-10 | 未知参数拒绝 | 安全 | 传入未声明参数 |
| TC-11 | 必填参数缺失报错 | 安全 | 不传必填参数 |
| TC-12 | 超时控制 | 边界 | 让 harness sleep |
| TC-13 | 默认 ASK 权限 | 安全 | `check_permissions()` |
| TC-14 | 无 Pillow 环境 fallback | 兼容 | 卸载 Pillow 后跑图片测试 |
| TC-15 | 不影响原有工具 | 回归 | 检查 `ToolRegistry` 前后差异 |
| TC-16 | system prompt 注入 | 集成 | `--verbose` 查看日志 |
| TC-17 | Node harness 支持 | 兼容 | 创建 `command: node` harness |

---

## 5. 测试用例与步骤

### TC-01 模块导入无报错

**目的**：确认所有新增文件语法正确、依赖可导入。

**步骤**：

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
python -c "
from agent.cli_anything import (
    Harness, HarnessArg,
    discover_harnesses, parse_skill_md, run_harness,
    register_harness_tool, discover_and_register,
    list_installed, list_market, install_harness, uninstall_harness,
)
from agent.tools.extensions.cli_anything_tool import CliAnythingTool
from agent.core.tool import register_dynamic_tools
print('imports ok')
"
```

**预期结果**：输出 `imports ok`，无异常。

**测试原理**：Python 导入会触发模块顶层代码执行，可一次性检查语法、循环依赖、缺失第三方库。

---

### TC-02 已安装 harness 列表

**目的**：验证 `loader.py` 能正确扫描并解析 `SKILL.md`。

**步骤**：

```powershell
python -m agent.main --once "/cli_anything list"
```

或在 REPL 中输入：

```text
/cli_anything list
```

**预期结果**：列出 `test-echo` 等已安装 harness，包含 id、name、description。

---

### TC-03 市场 harness 列表

**目的**：验证 `market.py` 能优先从 CLI-Anything 官方 GitHub 仓库读取 `registry.json`。

**步骤**：

```text
/cli_anything market
```

**预期结果**：显示市场可用 harness 列表，包含 `installed: 是/否`。列表数量应与官方仓库一致。

**测试原理**：`_load_registry()` 优先远程拉取 `https://raw.githubusercontent.com/HKUDS/CLI-Anything/main/registry.json`，成功后缓存到 `~/.jarvis/cache/cli_anything/registry.json`；远程失败时回退缓存，再失败回退本地 `CLI-Anything-main/registry.json`。

---

### TC-04 安装 harness

**目的**：验证 `install_harness()` 能优先从远程下载 `SKILL.md` 并迁移到用户目录。

**步骤**：

```text
/cli_anything install anygen
```

**预期结果**：提示 "已安装 harness: anygen（从远程下载）"，并且 `~/.jarvis/cli_anything/anygen/SKILL.md` 存在。

**测试原理**：`install_harness` 先从 registry 拿到 `skill_md`，构造 raw URL 下载官方 `SKILL.md`；下载成功则写入临时文件并调用 `migrate.migrate_one()` 生成 Jarvis 格式；远程失败再 fallback 到本地 `CLI-Anything-main/skills/.../SKILL.md`；最后调用 `discover_and_register()` 刷新注册表。

---

### TC-05 卸载 harness

**目的**：验证 `uninstall_harness()` 能删除 harness 目录。

**步骤**：

```text
/cli_anything uninstall anygen
```

**预期结果**：提示 "已卸载 harness: anygen（重启后生效）"，目录被删除。

---

### TC-05b 远程失败回退本地

**目的**：验证 `install_harness()` 在远程不可用时能回退到本地 `CLI-Anything-main/skills/`。

**步骤**：

临时把远程地址改成一个无效 URL（只影响当前进程）：

```python
from agent.cli_anything import market
market._REMOTE_RAW_BASE = "https://invalid.example.com/CLI-Anything/main"

from agent.core.tool import ToolRegistry
r = ToolRegistry()
res = market.install_harness("anygen", r)
print(res)
```

**预期结果**：仍然提示 "已安装 harness: anygen"（不带 "从远程下载" 字样），并且 `~/.jarvis/cli_anything/anygen/SKILL.md` 存在。

**测试原理**：远程请求失败后，`install_harness` 会检查本地 `CLI-Anything-main/skills/cli-anything-<id>/SKILL.md`，存在则使用本地文件迁移。

---

### TC-06 harness 执行与参数传递

**目的**：验证 runner 能正确拼接命令、传参、回传 stdout。

**步骤**：

```powershell
python -c "
import asyncio
from agent.cli_anything import discover_harnesses, run_harness
h = [x for x in discover_harnesses(workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis') if x.id == 'test-echo'][0]
r = asyncio.run(run_harness(h, {'subcommand': 'hello'}, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis'))
print(r)
"
```

**预期结果**：

- `exit_code == 0`
- stdout 是 JSON，包含 `"echo": "hello"`
- 包含 `"harness_dir"` 和 `"workdir"`

---

### TC-07 项目级覆盖用户级

**目的**：验证 `<workdir>/.jarvis/cli_anything/` 优先级高于 `~/.jarvis/cli_anything/`。

**步骤**：

```powershell
# 1. 用户级
mkdir -Force "$env:USERPROFILE\.jarvis\cli_anything\test-override"
@'---
name: TestOverrideUser
id: test-override
description: 用户级版本
command: python
---'@ | Out-File -Encoding utf8 "$env:USERPROFILE\.jarvis\cli_anything\test-override\SKILL.md"

# 2. 项目级
mkdir -Force "e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\.jarvis\cli_anything\test-override"
@'---
name: TestOverrideProject
id: test-override
description: 项目级版本
command: python
---'@ | Out-File -Encoding utf8 "e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\.jarvis\cli_anything\test-override\SKILL.md"

# 3. 检查
python -c "
from agent.cli_anything import discover_harnesses
h = [x for x in discover_harnesses(workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis') if x.id == 'test-override'][0]
print(h.name)
"
```

**预期结果**：输出 `TestOverrideProject`。

**清理**：

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.jarvis\cli_anything\test-override"
Remove-Item -Recurse -Force "e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\.jarvis\cli_anything\test-override"
```

---

### TC-08 JSON 中图片自动编码

**目的**：验证 `CliAnythingTool.call()` 能把 stdout JSON 中的图片路径转为 `ImageContent`。

**步骤**：

1. 在 `test-echo/run.py` 中让 harness 输出：

```python
print(json.dumps({"image": "e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/.jarvis/cli_anything/test-echo/demo.png"}))
```

2. 准备一张真实图片 `demo.png`。
3. 执行：

```powershell
python -c "
import asyncio
from agent.cli_anything.registry import discover_and_register
from agent.core.tool import ToolRegistry
from agent.core.context import ToolContext
r = ToolRegistry()
discover_and_register(r, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis')
t = r.get('cli_anything__test-echo')
res = asyncio.run(t.call({'subcommand': 'image'}, ToolContext(workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis', messages=[])))
print('images:', len(res.images))
print('media_type:', res.images[0].media_type if res.images else None)
"
```

**预期结果**：`images: 1`，`media_type: image/png`。

**测试原理**：`_extract_image_paths()` 递归遍历 JSON，`_encode_image_file()` 用 Pillow 缩放或 fallback 为 base64。

---

### TC-09 命令白名单拦截

**目的**：验证 runner 拒绝非白名单命令。

**步骤**：

```powershell
mkdir -Force "e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\.jarvis\cli_anything\evil-test"
@'---
name: Evil
id: evil-test
description: should be rejected
command: cmd.exe /c echo pwned
---'@ | Out-File -Encoding utf8 "e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\.jarvis\cli_anything\evil-test\SKILL.md"

python -c "
import asyncio
from agent.cli_anything import discover_harnesses, run_harness
h = [x for x in discover_harnesses(workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis') if x.id == 'evil-test'][0]
print(asyncio.run(run_harness(h, {}, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis')))
"

Remove-Item -Recurse -Force "e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis\.jarvis\cli_anything\evil-test"
```

**预期结果**：`exit_code == -1`，stderr 含 `harness command 不被允许`。

---

### TC-10 未知参数拒绝

**目的**：防止 LLM 或用户传入 harness 未声明的参数。

**步骤**：

```powershell
python -c "
import asyncio
from agent.cli_anything import discover_harnesses, run_harness
h = [x for x in discover_harnesses(workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis') if x.id == 'test-echo'][0]
print(asyncio.run(run_harness(h, {'subcommand': 'x', 'extra': 'bad'}, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis')))
"
```

**预期结果**：`exit_code == -1`，stderr 含 `未知参数`。

---

### TC-11 必填参数缺失报错

**步骤**：

```powershell
python -c "
import asyncio
from agent.cli_anything import discover_harnesses, run_harness
h = [x for x in discover_harnesses(workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis') if x.id == 'test-echo'][0]
print(asyncio.run(run_harness(h, {}, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis')))
"
```

**预期结果**：`exit_code == -1`，stderr 含 `缺少必填参数: subcommand`。

---

### TC-12 超时控制

**目的**：验证 harness 卡死时能被强制终止。

**步骤**：

1. 临时把 `test-echo/run.py` 改成：

```python
import time
time.sleep(300)
```

2. 执行：

```powershell
python -c "
import asyncio
from agent.cli_anything import discover_harnesses, run_harness
h = [x for x in discover_harnesses(workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis') if x.id == 'test-echo'][0]
print(asyncio.run(run_harness(h, {'subcommand': 'x'}, timeout=2.0, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis')))
"
```

3. 恢复 `run.py`。

**预期结果**：2 秒后返回 `exit_code == -1`，`error == "timeout"`。

---

### TC-13 默认 ASK 权限

**目的**：确认 harness 不会自动执行危险操作。

**步骤**：

```powershell
python -c "
from agent.cli_anything.registry import discover_and_register
from agent.core.tool import ToolRegistry
r = ToolRegistry()
discover_and_register(r, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis')
t = r.get('cli_anything__test-echo')
print(t.check_permissions({'subcommand': 'hi'}, None))
"
```

**预期结果**：`behavior=<PermissionBehavior.ASK: 'ask'>`。

---

### TC-14 无 Pillow 环境 fallback

**目的**：验证图片编码在缺少 Pillow 时仍能工作。

**步骤**：

```powershell
pip uninstall -y Pillow
# 跑 TC-08 图片测试
pip install Pillow
```

**预期结果**：即使 Pillow 缺失，仍返回 1 张图片（直接 base64 读取原文件）。

---

### TC-15 不影响原有工具

**目的**：确认注册动态工具不会丢失原有工具。

**步骤**：

```powershell
python -c "
from agent.core.tool import build_default_registry, register_dynamic_tools
r = build_default_registry()
before = {t.name for t in r.all()}
count = register_dynamic_tools(r, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis')
after = {t.name for t in r.all()}
print('新增:', after - before)
print('原有是否都在:', before <= after)
print('新增数量:', count)
"
```

**预期结果**：新增工具均为 `cli_anything__*` 前缀；原有工具集合是 after 的子集。

---

### TC-16 system prompt 注入

**目的**：确认 LLM 知道何时调用 harness。

**步骤**：

```powershell
$env:JARVIS_VERBOSE = "1"
python -m agent.main --once "你好"
```

**预期结果**：日志或 system prompt 中出现 `# CLI-Anything 外部软件控制` 章节，且每个 harness 的 `description` / `when_to_use` / `examples` 被注入。

---

### TC-17 Node harness 支持

**目的**：验证非 Python harness 也能被调用。

**步骤**：

创建 `~/.jarvis/cli_anything/test-node/SKILL.md`：

```markdown
---
name: TestNode
id: test-node
description: 测试 Node harness
command: node
args:
  - name: msg
    type: string
    required: true
---
```

创建 `~/.jarvis/cli_anything/test-node/run.js`：

```javascript
const args = process.argv.slice(2);
console.log(JSON.stringify({msg: args.join(' ')}));
```

执行：

```powershell
python -c "
import asyncio
from agent.cli_anything import discover_harnesses, run_harness
h = [x for x in discover_harnesses() if x.id == 'test-node'][0]
print(asyncio.run(run_harness(h, {'msg': 'hi from node'}, workdir='e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis')))
"
```

**预期结果**：`exit_code == 0`，stdout 含 `"hi from node"`。

---

## 6. 通过标准

所有测试用例必须满足下表才算模块可用：

| 编号 | 通过标准 |
|------|----------|
| TC-01 | 导入无 ImportError/语法错误 |
| TC-02 | `/cli_anything list` 正常列出 harness |
| TC-03 | `/cli_anything market` 返回市场列表 |
| TC-04 | install 后用户目录出现对应 `SKILL.md` |
| TC-05 | uninstall 后对应目录被删除 |
| TC-06 | `exit_code == 0`，stdout 正确，参数传递完整 |
| TC-07 | 项目级 harness 覆盖用户级同名 harness |
| TC-08 | JSON 中图片路径被自动编码为 `ImageContent` |
| TC-09 | 非白名单命令被拒绝，`exit_code != 0` |
| TC-10 | 未知参数被拒绝，`exit_code != 0` |
| TC-11 | 必填参数缺失被拒绝，`exit_code != 0` |
| TC-12 | 超时后返回 `error == "timeout"` |
| TC-13 | `check_permissions()` 返回 ASK |
| TC-14 | 无 Pillow 时仍能返回图片 |
| TC-15 | 原有工具不丢失 |
| TC-16 | system prompt 包含 CLI-Anything 能力说明 |
| TC-17 | Node harness 正常执行 |

**总体通过标准**：17 项中至少 15 项通过，且 TC-01、TC-06、TC-09、TC-13、TC-15 必须全部通过。

---

## 7. 使用指南：在 Jarvis 中使用 CLI-Anything

### 7.1 手动安装 harness

在 `~/.jarvis/cli_anything/<软件名>/` 下放置：

- `SKILL.md`：描述能力、参数、触发场景
- `run.py`：执行入口

示例目录：

```text
~/.jarvis/cli_anything/
├── blender/
│   ├── SKILL.md
│   └── run.py
└── obsidian/
    ├── SKILL.md
    └── run.py
```

### 7.2 从市场安装

```text
/cli_anything market                 # 查看可用 harness
/cli_anything install blender        # 安装 Blender harness
/cli_anything uninstall blender      # 卸载
/cli_anything list                   # 查看已安装
```

### 7.3 自然语言调用

启动 Jarvis 后，直接说：

```text
用 Blender 创建一个立方体并渲染
```

Jarvis 会：

1. 从 system prompt 中识别 `cli_anything__blender` 的能力。
2. 生成调用参数（如 `operation=create_mesh`, `prompt=创建立方体`）。
3. 弹出确认（ASK 权限）。
4. 执行 `run.py`。
5. 把 stdout / 图片返回给 LLM，继续对话。

### 7.4 项目级 harness

如果想让某个项目使用定制版 harness，在项目根目录创建：

```text
<project>/.jarvis/cli_anything/<id>/SKILL.md
```

项目级 harness 会覆盖全局同名 harness，方便团队协作和版本控制。

---

## 8. Jarvis 如何操作电脑桌面上的“一切软件”

CLI-Anything 的设计哲学是：**不直接控制 GUI，而是把软件的可脚本化能力暴露给 Agent**。Jarvis 能操作桌面软件的前提是：该软件能通过某种方式被命令行驱动。

### 8.1 三类驱动方式

| 方式 | 说明 | 代表软件 |
|------|------|----------|
| **原生 CLI/API** | 软件自带命令行或 REST API | Blender（Python API）、Obsidian（Local REST API）、Ollama、n8n |
| **CLI-Anything 官方 harness** | 社区已包装好的命令行适配器 | safari、blender、gimp、godot、zotero 等 |
| **自定义脚本** | 用户自己写 `run.py` 调用软件的 DLL/COM/AppleScript/Accessibility | 任何支持自动化的软件 |

### 8.2 以 Safari 为例

Safari 本身没有官方 CLI，但 CLI-Anything 社区提供了 `cli-anything-safari`，它内部通过 `safari-mcp` 与浏览器通信。对 Jarvis 来说，调用方式与其他工具没有区别：

```text
> 打开 Safari 并访问 github.com
```

Jarvis 调用 `cli_anything__safari`，harness 内部再调用 `safari-mcp` 完成实际操作。

### 8.3 以 GIMP 为例

GIMP 有 Python-Fu 脚本接口。`cli-anything-gimp` 的 `run.py` 会启动 GIMP 并执行脚本，Jarvis 只需：

```text
> 用 GIMP 把 test.jpg 转成灰度图
```

### 8.4 操作没有 CLI 的软件

如果某软件没有现成 CLI，可以通过以下方式包装：

1. **Windows COM / UIA**：用 `pywinauto` 或 `uiautomation` 模拟点击、输入。
2. **macOS AppleScript / Accessibility**：用 `osascript` 或 `pyautogui`。
3. **Linux D-Bus / xdotool**：调用桌面自动化工具。
4. **截屏 + OCR + 鼠标键盘**：通过 `ScreenShot`、`MouseClick`、`TypeText` 等内置工具与 harness 组合使用。

这些自定义脚本统一放在 `~/.jarvis/cli_anything/<id>/run.py` 中，Jarvis 通过 CLI-Anything 层把它们变为可理解、可确认、可编排的工具。

### 8.5 不是“一切”都能操作

CLI-Anything 不能做到：

- 操作完全没有脚本/API/自动化入口的软件。
- 绕过操作系统安全机制（如 UAC、权限对话框）。
- 在软件未运行时自动启动所有 GUI 软件（部分 harness 会自己启动软件，部分需要用户先打开）。

它的价值在于：**把“软件能否被脚本化”的问题下沉到 harness 实现层**，Jarvis 本身只需要理解 harness 提供的能力描述，从而无限扩展可操作软件的边界。

---

## 9. 故障排查

| 现象 | 可能原因 | 排查方法 |
|------|----------|----------|
| `/cli_anything list` 为空 | 用户级目录不存在或没有 `SKILL.md` | `ls ~/.jarvis/cli_anything` |
| harness 执行报 `harness command 不被允许` | command 不在白名单 | 改为 `python` / `node` / `cli-anything-*` |
| 参数未传进 run.py | args 名称不匹配 | 检查 `SKILL.md` 的 `args.name` 与 run.py 的 `--name` |
| 市场 install 失败 | 网络无法访问 GitHub raw，或 `skill_md` 指向非 raw 页面 | 检查 `https://raw.githubusercontent.com/HKUDS/CLI-Anything/main/registry.json` 是否可访问；确认本地 `../CLI-Anything-main` 作为 fallback 存在 |
| 图片未自动显示 | 路径不存在或不是真实图片 | 检查路径、确认 Pillow 可读取 |
| LLM 不调用 harness | system prompt 未注入 | `--verbose` 查看 prompt 是否含 CLI-Anything 章节 |
| 项目级 harness 未生效 | workdir 未正确传入 | 检查启动时的工作目录 |

---

## 10. 相关文件

- `agent/cli_anything/loader.py` — harness 扫描与解析
- `agent/cli_anything/runner.py` — 安全子进程执行
- `agent/cli_anything/registry.py` — 工具注册
- `agent/cli_anything/market.py` — 市场命令
- `agent/cli_anything/migrate.py` — 官方 harness 迁移
- `agent/tools/extensions/cli_anything_tool.py` — Tool 封装与图片编码
- `agent/core/tool.py` — `register_dynamic_tools()` 入口
- `agent/prompts/system.py` — system prompt 中的 CLI-Anything 章节
- `README.md` — 用户级使用说明

---

*文档维护：aceFelix*
