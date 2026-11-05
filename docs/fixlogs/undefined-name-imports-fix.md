# REPL 里打 `/doctor` 直接把会话打崩：`Path` 从来没被导入过

日期：2026-11-05 ｜ 影响：`/doctor` 命令 100% 崩、LSP 工具四条操作 100% 崩 ｜ 状态：已修复并二次验证

## 1. 问题现象

用户在 REPL 里打 `/doctor`，前两张表画出来之后整个进程带着 traceback 退出：

```text
> /doctor
JARVIS 系统诊断
        环境信息 …（表格正常）
      Provider 状态 …（表格正常）
Traceback (most recent call last):
  File "...\agent\main.py", line 422, in repl
    handled = await dispatch_command(cmd_ctx, stripped)
  File "...\agent\commands\router.py", line 273, in dispatch_command
    result = handler(ctx, stripped)
  File "...\agent\commands\handlers\core_commands.py", line 630, in handle_doctor
    _doctor(ctx.ui, ctx.settings, ctx.provider, ctx.model, ctx.messages)
  File "...\agent\commands\handlers\core_commands.py", line 540, in _doctor
    user_cfg = Path.home() / ".jarvis" / "settings.toml"
NameError: name 'Path' is not defined
```

影响面比看起来更大：`/doctor` 在 `serve` 模式的白名单里
（`agent/serve/engine/slash_bridge.py` 放行了 `/doctor`），所以桌面端通过 slash 通道
发同一条命令也会炸——只是那边会被引擎的错误分支吞掉，症状只是"没反应"。

## 2. 排查过程

| 阶段 | 假设 | 做法 | 结论 |
|---|---|---|---|
| 1 | 上一轮改动碰坏了这个文件 | `git log --oneline -4 -- core_commands.py` | 最近一次是 `87f104a`（能力自检层），但它没动导入区 |
| 2 | 导入被某次重构删掉了 | `git log -S "from pathlib import Path" -- core_commands.py` | **0 命中**：这个字符串在该文件历史上从来没出现过，不是被删，是**从来没写过** |
| 3 | 那它什么时候开始崩的 | `git log -S "user_cfg = Path.home()" -- core_commands.py` | `4b77351`（main 拆分 + 配置文件拆分那次）——从那时起 `/doctor` 就是坏的 |
| 4 | 为什么全量测试没抓到 | Grep `tests/` 里 `handle_doctor` / `_doctor` / `/doctor` | 只有 `tests/serve/engine/test_slash.py` 断言"`/doctor` 会被派发到 handle_doctor"，**没有任何用例执行过函数体** |
| 5 | 是不是只此一处 | `ruff check --select F821 agent/` | **17 处**未定义名字，见 §3 |

关键转折是第 5 步：把一个具体的崩溃当成一类缺陷去扫，而不是修完这一处就走。

## 3. 根因分析

两种不同的成因，症状一样（运行时 NameError）：

**成因 A：拆文件时导入没跟着走。** `_doctor()` 从 `main.py` 拆到 `core_commands.py` 时，
函数体里用的 `Path` 需要一条新的模块级导入，而当时只搬了函数没补导入。
`compileall` 只查语法，不查名字是否可解析；CI 又没有任何 lint 步骤，于是这条缺陷
在仓库里躺了两个月。

**成因 B：函数内局部导入只对那个函数可见。** `lsp_tool._format_result()` 里写了
`from agent.lsp.client import uri_to_path`，但真正用这个名字的是它调用的四个
**模块级**函数（`_format_location` / `_format_call_items` / `_format_incoming_calls` /
`_format_outgoing_calls`）。分发器自己不崩，callee 一碰到带 `uri` 的返回就 NameError：

```text
goToDefinition     NameError: name 'uri_to_path' is not defined
findReferences     NameError: name 'uri_to_path' is not defined
goToImplementation NameError: name 'uri_to_path' is not defined
prepareCallHierarchy NameError: name 'uri_to_path' is not defined
```

而 LSP 工具只在 `[lsp]` 启用时才注册，默认配置下根本不进 registry，所以连"有人撞过它"
的机会都没有。这类错误还会被自愈层当成可重试错误反复重试，用户看到的是"LSP 用不了"。

### 17 处 F821 的分类（实测）

| 类别 | 站点 | 会不会真崩 |
|---|---|---|
| 函数体内使用未导入的名字 | `core_commands.py:540/542/544`（`Path`） | ✅ 会，用户已撞到 |
| 同上 | `lsp_tool.py:251/390/411/434`（`uri_to_path`） | ✅ 会，四条 LSP 操作全中 |
| 字符串注解引用未导入的名字 | `core/tool.py:294`（`workdir: "Path \| str \| None"`）、`market.py:397/640`（`ToolRegistry`）、`bootstrap.py:105`（`ToolRecoveryExecutor`）、`cache_policy.py:301`×2（`Message`）、`boot_animation.py`×4（`Any`） | ❌ 不会（`from __future__ import annotations` 下注解不求值），但 `typing.get_type_hints()` 与任何静态读者都会拿到一个不存在的名字 |

## 4. 修复方案

**真崩的两处补模块级导入**：

- `agent/commands/handlers/core_commands.py`：加 `from pathlib import Path`；
- `agent/tools/extensions/lsp_tool.py`：加 `from agent.lsp.client import uri_to_path`
  （`client.py` 只依赖标准库，顶层导入不成环），并**删掉分发器里那句冗余的函数内导入**——
  留着它，下一个人还会以为局部导入够用。

**10 处注解站点**：补 `TYPE_CHECKING` 块（`market.py` / `bootstrap.py` /
`cache_policy.py`）或直接补 `from typing import Any`（`boot_animation.py`）、
`from pathlib import Path`（`core/tool.py`）。运行期行为不变，只让名字真的可解析。

**顺带拆了 `market.py`**（这是被自家门禁拦下来的，不是顺手重构）：给 `market.py`
补 `TYPE_CHECKING` 后它到 905 行，越过仓库 900 行上限，
`tests/test_engine_split_guards.py::test_no_agent_file_exceeds_limit` 直接红。按
"接近上限先拆再写"的规则：harness 启用/禁用状态（82 行）移到
`agent/cli_anything/harness_state.py`，`_normalize_id` 这个纯函数移到
`agent/cli_anything/ids.py`——它同时被两边需要，留在任一侧都会形成
`market ↔ harness_state` 循环。market.py 905 → 828 行，顶部重导出那四个入口，
所以调用方与按 `market.enable_harness` 打桩的用例一行都不用改。

**门禁（这才是重点）**：

- `ci.yml` 与 `publish.yml` 各加一步 `ruff check --select F821 agent/`。
  只选一条规则：仓库还有上百条既存 lint 噪音，全量 ruff 会立刻把 CI 变成红海，
  反而没人管——门禁要能长期绿着才有人看。
- 新增 `tests/commands/test_doctor_command.py`（7 例）与
  `tests/tools/test_lsp_format_paths.py`（14 例），把"函数体真被执行一遍"变成契约。
  两个文件里各有一条结构测（`mod.Path is not None` /
  `getattr(lsp_tool, "uri_to_path", None) is not None`），直接钉住根因。

没有做的事：不把 `--doctor` 与能力表同源（那是另一件事，已记在
[capability-tool-name-contract-fix.md](capability-tool-name-contract-fix.md) 与
[20-能力自检与自我修复 §11](../architecture/20-能力自检与自我修复.md)）。

## 5. 验证结果

| 验证项 | 命令 / 做法 | 结果 |
|---|---|---|
| 同类缺陷清零 | `ruff check --select F821 agent/` | `All checks passed!`，exit 0（修复前 17 处） |
| 编译 | `python -m compileall -q agent/ tests/` | exit 0 |
| 新测通过 | `pytest tests/commands/test_doctor_command.py tests/tools/test_lsp_format_paths.py -q` | 21 passed |
| 变异：撤掉 `Path` 导入 | 删掉那行再跑 doctor 测 | **7/7 全红**，报的就是用户撞到的 `NameError` |
| 变异：撤掉 `uri_to_path` 导入 | 同上 | **8 红 4 绿**，红的全是 NameError |
| 变异暴露的次生问题 | 上一版 LSP 测里 `goToDefinition` 的 payload 形状写错（多套一层 `{"location": …}`） | 那 2 条**空转**：走的是"缺 uri → `<unknown>`"分支，压根没到 `uri_to_path`。已改成真实 LSP 形状（`Location[]` 与 `LocationLink[]` 各一条），并加断言「输出里不许出现 `<unknown>`」 |
| 全量 | `pytest tests/ -q` | **4472 passed / 1 skipped / 0 failed**（138.81s，2026-11-05；比基线 4452 正好多出本笔新增的 21 例） |

## 6. 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/commands/handlers/core_commands.py` | 补 `from pathlib import Path`；崩溃点加四行说明注释 |
| `agent/tools/extensions/lsp_tool.py` | 补模块级 `uri_to_path` 导入；删分发器内冗余局部导入并写明原因 |
| `agent/core/tool.py` | 补 `from pathlib import Path`（`register_dynamic_tools` 的注解） |
| `agent/cli_anything/market.py` | `TYPE_CHECKING` 补 `ToolRegistry` |
| `agent/bootstrap.py` | `TYPE_CHECKING` 补 `ToolRecoveryExecutor` |
| `agent/llm/cache_policy.py` | `TYPE_CHECKING` 补 `Message` |
| `agent/ui/boot_animation.py` | 补 `from typing import Any` |
| `agent/cli_anything/market.py` | 删掉已搬走的 82 行状态块与 `_normalize_id`，改为从两个新模块导入并重导出；补 `TYPE_CHECKING` 的 `ToolRegistry`（905 → 828 行） |
| `agent/cli_anything/harness_state.py` | 新增：harness 启用/禁用状态（`disabled.json` 读写 + 四个入口） |
| `agent/cli_anything/ids.py` | 新增：`normalize_harness_id()` 归一化规则的唯一实现 |
| `.github/workflows/ci.yml` / `.github/workflows/publish.yml` | 新增 `ruff check --select F821 agent/` 闸 |
| `tests/commands/test_doctor_command.py` | 新增 7 例 |
| `tests/tools/test_lsp_format_paths.py` | 新增 14 例 |
| `docs/fixlogs/undefined-name-imports-fix.md` | 本复盘 |
| `docs/architecture/20-能力自检与自我修复.md` | CI 门清单补 F821 一条 |
| `CHANGELOG.md` | `[Unreleased]` 修复节 |

## 7. 经验总结

1. **拆文件时，导入清单要跟着"用到这个名字的所有函数"走，而不是跟着"当前搬过来的这段
   代码"走。** 重构后原文件能跑不代表新文件的模块级命名表完整——`compileall` 只查语法，
   这类问题它一个都拦不住。
2. **函数内延迟导入是解循环依赖的手段，但它只对所在函数可见。** 把共享 helper 的名字
   只放在分发函数里，等于给每个 callee 埋雷；正确做法是模块级导入（若确实成环，
   就在每个 callee 里各自延迟导入，或把 helper 下沉到无依赖的模块）。
3. **"命令被派发了"不等于"函数体被执行了"。** 一条只断言路由的测，对处理器内部的
   任何 NameError 都是盲的。凡是给用户看的诊断/展示型命令，至少要有一条测把函数体
   跑到底，并断言最后一节渲染出来了（崩溃点之前的表能画、之后的表画不出来，
   这个"走到第几张表"本身就是定位信息）。
4. **撞到一个缺陷后，先找一条能扫全仓的静态规则，把同类一次清干净。** 本次是一条
   `ruff check --select F821`，从 1 个用户投诉扫出 17 处，其中 7 处是真会崩的。
5. **门禁要窄。** 加全量 lint 会被既存噪音淹死从而失去效力；只钉一条零噪音、高信号的
   规则，它才能长期绿着、长期有人看。
6. **测自己也要被验牙。** 本轮新写的 LSP 测里就有 2 条因 payload 形状写错而空转，
   靠"撤掉修复导入看几条红"这一步才暴露。写完测立刻做一次反向变异，是本轮唯一
   一次抓到我自己写错测的手段。
7. **行数门禁真的会拦人，这是它的价值。** 本轮给 `market.py` 加 7 行 `TYPE_CHECKING`
   就把一个 899 行的文件推过上限，测试直接红并指名道姓——如果没有这条测，这个
   文件会一直靠在墙边上。拆完反而得到了 72 行余量。
8. **拆代码时先问"这个共享函数到底归谁"，而不是先问"放哪个文件改动最小"。**
   `_normalize_id` 被 `market` 与 `harness_state` 两边需要，留在任一侧都会形成
   循环导入；它又是纯字符串变换，按仓库规则本就该独立成模块。
