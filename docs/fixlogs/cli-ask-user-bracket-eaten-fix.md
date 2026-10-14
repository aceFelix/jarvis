# 修复：权限确认提示行的 `[y]`/`[a]` 按键名被 Rich 标记吞掉

- **日期**：2026-10-14
- **作者**：aceFelix
- **影响范围**：`agent/ui/cli.py`（`RichCLI.ask_user`，即 REPL 终端里所有阻塞式询问：权限确认、AskUserTool 提问等）
- **严重级别**：低（仅显示问题，输入 `y`/`a` 功能一直正常；但按键名丢失会让用户不知道有哪些选项）

## 现象

阶段 8 W1 人工走查（`[policy] enabled = false` 对照组测试）时，删除文件的权限确认提示实际显示为：

```text
输入  允许 /  总是允许 / 其他键拒绝: : y
```

- 期望显示：`输入 [y] 允许 / [a] 总是允许 / 其他键拒绝: y`
- 实际显示：`[y]`、`[a]` 两个按键名整体消失，且结尾出现双冒号 `: :`

该问题为**升级前就存在的老 bug**，与阶段 8 无关（确认文案由管线原生 `_format_ask` 产生），人工测试才暴露。

## 排查过程

1. 首先怀疑复制粘贴丢字符——用户截图证明**屏幕上确实没有**，排除。
2. 定位文案源头：`orchestrator._format_ask` 生成 `"输入 [y] 允许 / [a] 总是允许 / 其他键拒绝: "`（字符串本身正确）。
3. 定位渲染路径：`RichCLI.ask_user` 把它拼进 Rich 标记
   `Prompt.ask("[bold magenta]❓ " + prompt + "[/bold magenta]")`。
4. 最小复现实验确认根因：`Console.print('输入 [y] 允许 / [a] 总是允许')` 输出
   `输入  允许 /  总是允许`——Rich 把 `[y]`、`[a]` 当作**样式标签**解析并吞掉（未知样式名不报错，静默移除）。
5. 双冒号 `: :` 是次生问题：`_format_ask` 结尾自带 `": "`，`Prompt.ask` 默认又追加 `prompt_suffix=": "`。

## 根因

动态外部文本（权限 reason、按键提示、工具参数 JSON）未经转义直接拼入 Rich markup。
凡包含方括号的内容都会被 Rich 标记语法吃掉——工具参数里的 JSON 数组
（如 `"paths": ["a", "b"]`）同样会被误解析。

这是本项目**第二次**踩同一类坑（第一次见 `doctor-install-cmd-markup-fix.md`：doctor 表格
extras `[voice]` 被吞）。

## 修复方案

`agent/ui/cli.py`：

1. `ask_user` 中 prompt 先经 `rich.markup.escape()` 转义再拼接颜色标记，外层
   `[bold magenta]…[/bold magenta]` 仍正常生效；
2. 双冒号消除：先去掉 prompt 自带的结尾 `": "`，由 `Prompt.ask` 渲染时自动
   追加的默认 `": "` 后缀补回。**不能传 `prompt_suffix=""`**——首版修复用了
   该参数，但项目实际安装的 rich 15.0.0 的 `Prompt.ask` 签名不支持它，用户
   实测直接 `TypeError: PromptBase.ask() got an unexpected keyword argument
   'prompt_suffix'` 炸掉整个权限确认（二次回归，已改为去尾冒号方案并用
   真实签名守卫测试兜住）；
3. 无 Rich 时的 `input()` 回退路径不受影响。

## 验证结果

- 新增回归测试 `tests/ui/test_cli_ask_markup.py`（4 例）：
  - `[y]`/`[a]` 渲染后原样保留（修复前必失败）；
  - JSON 数组方括号不报错且完整保留；
  - 单冒号断言（去尾冒号后由默认后缀补回）；
  - **签名守卫**：实际传给 `Prompt.ask` 的关键字参数必须存在于真实 rich API
    签名中（打桩测试用 `**kwargs` 全收，拦截不了传错参数——prompt_suffix
    二次回归的教训）。
- 真实 `Prompt.ask` 端到端冒烟（替身 stdin 喂 `y`，不打桩）：不抛 TypeError、
  `[y]`/`[a]` 保留、无双冒号。
- `uv run pytest tests/ui/test_cli_ask_markup.py tests/ui/test_boot_banner.py tests/ui/test_capped_console.py -q` → 22 passed。
- 全量 `uv run pytest -q` → 2501 passed。
- 人工复测：REPL 触发权限确认，提示行按键名完整显示、无双冒号（待用户确认）。

## 同期发现：policy 测试隔离漏洞（一并修复）

同一轮人工走查后跑全量，`tests/policy/test_policy_commands.py::test_lease_grant_list_revoke`
稳定失败：用户按测试指南复制了真实的 `~/.jarvis/policy.yaml`，而 conftest 只重置了引擎单例
却没有像 lease/state 那样用 tmp 路径**预建**——`policy_gate` 内 `get_engine(None)` 复用单例
时会回退默认路径，读到真实用户配置（模板里恰有 `FileWrite → ~/reports/** allow` 规则），
撤销租约后引擎仍直接放行。

- 修复：conftest 在重置后调 `engine_mod.get_engine(paths["policy_yaml"])` 把引擎钉到 tmp。
- 教训：**单例隔离必须“重置 + 预建”两步齐全**，只置 None 等于把默认路径暴露给真实用户目录；
  测试不能假设用户机器上没有跑过本功能。提交时全过、用户实测后就红，正是此因。

## 同期发现二：W1 新命令漏登记 REPL 补全表

用户实测打 `/l` 时补全弹层里没有 `/lease`：阶段 8 只注册了 `router.COMMAND_HANDLERS`
路由与 `/help` 文本，漏了 `agent/ui/cli.py` 的静态补全登记表 `SLASH_COMMANDS`。
已补 `/away` `/back` `/pending` `/lease` 四条，并在 `tests/policy/test_policy_commands.py`
新增守卫断言（四命令必须在补全表中）。

- 教训：**新增斜杠命令的登记面有三处**——路由表、帮助文本、补全表 `SLASH_COMMANDS`，
  缺一不可；已用单测把第三处钉住。

## 同期发现三：真实 away 状态污染全套测试（最严重）

用户实测 `/away` 后跑全量，`test_p23_proactive`（收集期即报错）与
`test_orchestrator` 的 5 个 ASK 用例失败：这些旧测试没隔离 policy 单例，
`policy_gate` 读到用户真实 `~/.jarvis/policy_state.json` 的 `away=true`，
把 ASK 误转 DENY；更糟的是测试的 fake_tool 条目被写进了用户**真实待批清单**
（污染 10 条，已脚本清理）。另确认：away 拒绝后模型并未反复重试（真实清单
只 1 条），空面板刷屏是思考流开头纯换行增量过早建面板的渲染问题，
`assistant_thinking` 已改为“非空白内容到达才启动渲染”并补 2 例单测；
away 拒绝文案同步加了“请勿重试或改用其他工具绕过”的模型引导。

- 修复：新建根 `tests/conftest.py` autouse 夹具，**全套每个用例**默认把
  policy 引擎/租约/状态/待批四个单例重置并预建到 tmp 路径；
  `test_p23_proactive.py`（模块级断言脚本，夹具盖不到收集期）自身补了导入时隔离。
- 教训：**接入新的进程级状态时必须同步审计存量测试的隔离**；测试写真实用户
  目录的配置/状态文件是双向污染（测试结果不可信 + 污染用户环境），
  必须在根 conftest 兜底封死。

## 同期发现四：/diff 在非 git 目录崩掉整个 REPL（同族问题，2026-10 实测）

用户 workdir 不是 git 仓库，`/diff` 时 git 把 usage 文本（内含
`--break-rewrites[=<n>[/<m>]]`）写进 stderr，`_show_diff` 原样透传进
`ui.warn` → Rich 把 `[/...]` 当闭合标签抛 MarkupError；更糟的是错误处理本身
（`ui.error(f"/diff 执行失败: {e}")`）二次踩同一坑，异常直接逃出处理链，
REPL 整局崩溃。

- 修复：`RichCLI.warn/error` 接入面统一 `escape()`（全库审计过：两方法
  无任何故意传 Rich 标记的调用点；`info` 有 collab_commands 四处故意标记，
  暂不动并在 docstring 标注差异）。补 3 例回归：git usage 方括号、
  异常消息含闭合标记、普通文案不受影响。
- 教训：**只有带样式的拼接口子需要标记，动态内容一律转义**——安全策略
  应设在接入面（warn/error 这种专门印外部文本的方法），不能只修某个
  触发点；否则下一个 `{e}` 拼接处照样炸。`[<m>]` 这类未开标签 Rich 只
  吞不报，**`[/...]` 开头的才硬崩**，测试样本要取后者。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/ui/cli.py` | `ask_user` escape+去尾冒号；`SLASH_COMMANDS` 补登 W1 四命令；思考面板纯空白增量不渲染；`warn/error` 接入面 escape |
| `agent/core/policy/__init__.py` | away 拒绝文案加防重试/防绕过引导 |
| `tests/ui/test_cli_ask_markup.py` | 新增回归测试（含真实签名守卫、warn/error 标记安全 3 例） |
| `tests/ui/test_thinking_panel.py` | 新增空白增量守卫 2 例 |
| `tests/conftest.py` | **新建**：全局 autouse 隔离 policy 单例到 tmp |
| `tests/test_p23_proactive.py` | 模块级导入时隔离 away 状态 |
| `tests/policy/conftest.py` | 引擎单例预建到 tmp 路径，堵隔离漏洞 |
| `tests/policy/test_policy_commands.py` | 新增补全表登记守卫断言 |
| `docs/fixlogs/cli-ask-user-bracket-eaten-fix.md` | 本复盘文档 |

## 经验总结

- **规则**：任何动态文本（工具参数、路径、reason、命令示例）拼入 Rich markup 前必须 `escape()`，
  这与 doctor 表格教训同源。后续可排查项目内其余 `console.print(f"...{动态文本}...")` 拼接点。
- **第三方库参数不能凭文档印象传**：rich 各版本 `Prompt.ask` 签名有差异，传前必须用
  `inspect.signature` 或真实调用验证；打桩测试用 `**kwargs` 全收参数，对“传了不存在的
  参数”零拦截力——给外部 API 传关键字参数时测试里必须有真实签名守卫。
- 显示类 bug 单靠日志粘贴不可靠（终端复制可能丢字符），**以屏幕截图为准**；但反过来，
  用户贴的**报错文本**（如本次 TypeError）可信度高，可直接定位。
- `_format_ask` 的结尾冒号与宿主 UI 自加的输入引导符会叠加，跨层拼接提示语时要约定谁负责结尾。

## 后同步勘误（2026-10 降级合并）

本文写作后，策略引擎（`core/policy/engine.py` + `policy.yaml`）在用户
拍板的“降级合并”重构中被整体删除：声明式规则能力并入五层管线本身
（`permissions/checker.py` 放宽语义 + `permissions/path_match.py`）。因此：

- 上文“policy 测试隔离漏洞”中**引擎单例预建**的修复已随引擎删除而
  移除（`tests/policy/conftest.py` 不再钉 engine 路径）；但教训本身仍成立：
  **单例隔离必须“重置 + 预建”两步齐全**；
- 隔离漏洞的根因（真实用户配置污染测试）在管线侧仍存在：根
  `tests/conftest.py` 的 autouse 夹具继续把租约/状态单例钉到 tmp，
  管线规则则由测试自行构造 RuleSet 不读真实文件。

@updated aceFelix
