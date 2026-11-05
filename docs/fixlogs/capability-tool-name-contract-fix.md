# 能力契约测把「这台机器没装」当成了「工具不存在」

日期：2026-11-05 ｜ 影响：CI（GitHub Actions Linux）独红一条，本地全绿 ｜ 状态：已修复并二次验证

## 1. 问题现象

v2.4.0 的四笔提交推上 master 后，GitHub Actions 的 pytest 步骤红了一条：

```text
platform linux -- Python 3.14.9, pytest-9.2.0
collected 4452 items
tests/capabilities/test_capability_probe.py::test_declared_tool_names_actually_exist FAILED

E   AssertionError: 表里登记了不存在的工具：CameraShot
E   assert ('CameraShot' in {'AskUser', 'Bash', 'BrowserClick', ...} or 'CameraShot' in {'LSP', 'RealtimeTalk'})
================= 1 failed, 4396 passed, 55 skipped in 45.86s ==================
```

同一份代码在本地 Windows 是 **4451 passed / 1 skipped / 0 failed**（全量刚跑过不到一天）。
也就是说：这条测在装了 opencv 的机器上永远绿，在没装的机器上永远红。

影响范围只限 CI 红灯本身——生产路径上 `CameraShot` 的可用性判定走的是探测层，
探测层给的是正确答案（`vision.camera` 在缺 cv2 的机器上就是 `missing`）。

## 2. 排查过程

| 阶段 | 假设 | 实际做法 | 结论 |
|---|---|---|---|
| 1 | 能力表把工具名拼错了 | Grep `CameraShot` 找声明处 | **不成立**：`agent/tools/vision/camera.py:71` 就是 `name = "CameraShot"`，`registry.py:385` 写的是同一个串 |
| 2 | 工具在 CI 上压根没注册 | 读 `agent/core/tool.py::_register_camera_tools` | **成立**：两道 `except ImportError: return`（导 `camera` 模块失败、以及再验一次 `import cv2`），CI 没装 opencv 就静默不注册 |
| 3 | 那就把 `CameraShot` / `ListCameras` 加进测里的手工豁免表 `CONDITIONAL_TOOLS` | 检查该表现状 | **否决**：① 豁免表里已有一条 `RealtimeTalk` 根本不在能力表中，说明它自己就在腐烂；② 按同样逻辑 browser 那 6 项也该豁免，豁免表会变成「本机装了哪些包」的登记表，而它必须逐台机器手改 |
| 4 | 判据本身错了 | 对照本层第一条纪律 | **根因**：见 §3 |

关键转折是第 3 步的否决理由：这条测存在的意义是「防能力表烂掉」，而它自己用了一个
**依赖机器环境**的判据。它犯了本层要治的那个错——`import 失败 ≠ 东西没有`。
把红点归给「表写错了」，下一步就会去改表（把真实存在的工具名删掉），那才是真事故。

为把这一点钉死，在本机做了一次可控复现：用 `sys.meta_path` 拦掉 `cv2` 与
`agent.tools.vision.camera` 的导入（等价于 CI 环境），再分别跑新旧判据：

```text
CameraShot 在运行时注册表里吗： False
ListCameras 在运行时注册表里吗： False
旧判据会红的名字： ['CameraShot', 'ListCameras']   ← 与 CI 完全一致
静态扫描是否仍看见 CameraShot： True
新判据：通过
```

## 3. 根因分析

`test_declared_tool_names_actually_exist` 用 `build_default_registry()` 的**运行时**
工具集当"真实存在"的唯一证据。而默认注册表的构成至少受三层条件影响：

1. **可选依赖**：缺 `playwright` / `opencv-python` 时整组工具直接 `return`，不注册；
2. **配置开关**：`[lsp]`、`[realtime_talk]`、`[tools] deferred_loading` 等决定是否注册；
3. **平台**：部分工具只在特定平台进表。

实测本机（Windows，装了 opencv 与 playwright）运行时注册 46 个，而源码里静态存在
72 个 Tool 名字——**差 26 个**。也就是说这条测的判据本来就有 26 个名字是「靠运气
绿」的，只是它恰好只覆盖能力表里那 26 项声明，才让问题在 Linux CI 上第一次暴露。

外部条件（装了哪些包）不该决定一条**契约测**的红绿；契约测要判的是「这个名字在源码
里有没有对应的类」。

## 4. 修复方案

只改测，不改表（表是对的）。

- **新增 `_static_tool_names()`**：用 `ast` 走 `agent/**/*.py`，收集基类名以 `Tool`
  结尾的类里的 `name = "<字符串>"` 类属性。不 import 任何模块，因此不受可选依赖、
  配置开关与平台影响；
- **`test_declared_tool_names_actually_exist` 改为查静态全集**：名字写错、工具改名
  或被删除，字面量跟着没，依旧红；可选依赖没装不算"不存在"；
- **加一条防自身失效的断言**：`{"Bash", "ToolSearch", "ScreenShot", "CameraShot"} <= static`，
  扫不到就说明取路径或解析写崩了，不能静默放行整表。`CameraShot` 特意在里面——
  它来自动态导入受限的 `vision/` 目录；
- **删掉 `CONDITIONAL_TOOLS` 手工豁免表**：静态判据下不再需要，全仓已无任何引用。

刻意没有做的两件事（避免把修复做成新负担）：

- **反方向（「新增工具忘了登记」）不静态化**。它现在遍历运行时 deferred 工具，
  在缺依赖的机器上少检查几个但不会误红；若改成静态全集判据，实测会有
  **45 个常驻工具**既不在能力表也不在 `DEPENDENCY_FREE_TOOLS`（该白名单目前只有
  1 项），等于把豁免名单从 1 项撑到 46 项——那才是真的会腐烂的东西。
- **不顺手给 CI 装 extras**。装了 opencv 这条测也会绿，但用户机器上照样缺件，
  判据必须与装了什么无关。

## 5. 验证结果

| 验证项 | 命令 | 结果 |
|---|---|---|
| 本文件测试 | `pytest tests/capabilities/test_capability_probe.py -q` | 54 passed |
| 变异（表里名字改错） | `CameraShot` → `CameraShotX` | **红**：`表里登记了不存在的工具：['CameraShotX']`，闸门没被改成摆设 |
| 模拟缺 cv2 | meta_path 拦 `cv2` + `camera` 模块 | 旧判据复现 CI 的两个红名，新判据通过 |
| 编译 | `python -m compileall -q agent/ tests/` | exit 0 |
| 全量 | `pytest tests/ -q` | **4451 passed / 1 skipped / 0 failed**（141.65s，2026-11-05） |
| 引用检查 | Grep `CONDITIONAL_TOOLS` 全仓 | 0 处，删除安全 |

## 6. 涉及文件

| 文件 | 改动 |
|---|---|
| `tests/capabilities/test_capability_probe.py` | 新增 `_static_tool_names()`；`test_declared_tool_names_actually_exist` 判据换成静态全集并加防失效断言；删除 `CONDITIONAL_TOOLS` |
| `CHANGELOG.md` | `[Unreleased]` 修复节补一条 |
| `docs/fixlogs/capability-tool-name-contract-fix.md` | 本复盘 |

## 7. 经验总结

1. **「X 不存在」这类断言，先问判据从哪来。** 判据一旦来自「运行时装配结果」，
   它就同时取决于装了什么包、开了哪些配置、跑在什么平台。契约测的判据应当是
   源码事实（静态可解析），运行事实归探测层管。
2. **本地全绿不等于跨平台全绿**，尤其当本地是「依赖装得最全」的那台机器。本轮
   4451 例全绿的置信度，被一条 CI 红点削掉一块：以后凡是「表 ↔ 注册表」这类对账测，
   写完就要主动问「缺依赖的机器上它红不红」，并且用 meta_path 拦截做一次可控复现，
   而不是等 CI 告诉你。
3. **手工豁免表是腐烂点，不是逃生门。** 出现「再加一个豁免就能绿」的冲动时，
   先量一下豁免名单会长多大（本轮实测：静态 72 个名字里本机只注册 46 个）。
   一张需要逐台机器手改的表，本身就是要被测的那个缺陷。
4. **修红灯时先证明「表是对的」**：本轮第 1 步（怀疑名字写错）如果没做就直接改测，
   可能变成「为了绿而把真实存在的东西从表里删掉」——那是用一个错误掩盖另一个错误。
5. **改判据必须带防自身失效的断言**。静态扫描这种写法，路径写错或解析崩了会
   返回空集，而「空集 ⊇ 声明」是永真命题；不钉这一条，闸门会在某次目录调整后
   静默变成摆设。
6. **「本地环境比 CI 好」本身就是一处盲区**：本轮本地装了
   opencv / playwright / pyautogui，而 CI 只装基础依赖，于是所有「运行时对账」
   类测在本地都跑在最强环境里。此类测要么用静态判据，要么把「本机实际装了什么」
   写进判据能反查的地方（本轮选了前者）。

## 附：CI 顺带暴露但未在本次处理的一项

日志里有一条 `PytestDeprecationWarning: The configuration option
"asyncio_default_fixture_loop_scope" is unset`。它不是本次红灯的原因，也不影响
结果；但它意味着 pytest-asyncio 未来版本会把异步 fixture 的作用域默认改成
`function`。要消掉它应在 `pyproject.toml` 显式设 `asyncio_default_fixture_loop_scope`
——那是全局语义变更（现在异步 fixture 缓存范围是 fixture 作用域），应当单独评估
并跑一轮全量再改，不塞进这条修复里。
