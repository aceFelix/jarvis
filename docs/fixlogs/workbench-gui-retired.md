# 三栏 GUI 工作台退役：只删窗口壳，保住桌面后端

日期：2026-10-14｜作者：aceFelix｜影响版本：v2.2.2 → 下一版

## 一、决策

`jarvis-desktop`（Electron 桌面应用）已完整承担图形化桌面，本仓库里基于
pywebview 的三栏 GUI 工作台属于**重复建设**：两套前端要各自维护气泡渲染、
会话列表、模型选择器、主题皮肤与反应炉动画，口径漂移只能靠人工对齐（历史上
`session-ready-first-bubble-swallow`、聊天折叠降噪等修复都是两边各改一遍）。

因此本次把三栏 GUI 窗口整体退役，图形化入口唯一收敛到 jarvis-desktop，
终端仓库只保留 REPL 与 `--serve` 后端。

## 二、关键判断：`workbench/` 不等于「三栏 GUI」

`agent/ui/workbench/` 共 5104 行，直觉上会被当成 GUI 实现整体淘汰，**但这是错的**。
`jarvis --serve` 的后端就装配在这套零件上，[`agent/serve/app.py`](../../agent/serve/app.py)
原文写着：

> 装配：与 workbench app.run_workbench 同一套零件，仅宿主从 pywebview 换成 WS

依赖分析结果（外部引用方为 `agent/serve/{app,server}.py` 与 `agent/voice/voice_events.py`）：

| 模块 | 行数 | 归属 | 处置 |
|---|---|---|---|
| `engine.py` | 1240 | serve 引擎（含 `ServeVoiceAdapter`） | **保留** |
| `api.py` | 717 | serve 指令/事件门面 | **保留**（删窗口控制死代码） |
| `extensions_api.py` | 488 | 桌面扩展服务面板 | 保留 |
| `model_admin.py` | 349 | serve 直接引用 | 保留 |
| `slash_bridge.py` | 339 | 桌面斜杠命令透传 | 保留 |
| `bridge.py` | 288 | 引擎事件→前端事件适配 | 保留 |
| `remote_bridge.py` | 261 | 多端协同 | 保留 |
| `checkpoint_ops.py` | 187 | 回溯预览 | 保留 |
| `project_switch.py` / `mcp_runtime.py` / `metrics.py` / `render.py` / `model_switch.py` / `voice_adapter.py` | 600 | 引擎协作件 | 保留 |
| **`app.py`** | **259** | pywebview 建窗 + 线程编排 | **删除** |
| **`single_instance.py`** | **216** | 二次双击聚焦窗口 | **删除** |
| **`win32_icon.py`** | **112** | 任务栏 AUMID 图标 | **删除** |
| **`window_geometry.py`** | **28** | 透明窗口工作区几何 | **删除** |
| **`assets/`** | ~70KB | index.html / app.js / reactor.js / style.css | **删除** |

判定依据：这 4 个模块的外部引用数为 0（`external<-[]`），内部只被 `app.py`
引用，`app.py` 自身除 `autostart.ensure_window_icon` 外不被任何模块依赖——
是干净的叶子。删掉后 workbench 剩 4489 行，全部服务于 serve。

打包链同步核对：[`packaging/jarvis-serve.spec`](../../packaging/jarvis-serve.spec)
用 `collect_submodules("agent")` 全量收集、`datas` 只带 `agent/configs`，
从未打包 `workbench/assets`（`MANIFEST.in` 同样没有条目），故删除不影响
桌面安装包。反过来这也说明：三栏 GUI 在 pip 分发包里本就缺前端资源，
只有源码安装才跑得起来——它从来不是一个被交付过的功能。

## 三、为什么没走「插件化」

曾考虑把窗口壳搬进 `jarvis-plugins` 当插件保留。查 `agent/core/extensions/plugins.py`
与 `marketplace.json` 后放弃：插件清单的扩展点只有 mcp / tools / skills /
plugins / harness 五类，**没有任何 UI 宿主或 entrypoints 字段**，插件无法注册
主进程窗口与 argparse 启动参数。要走这条路得先给扩展体系新增一类 UI 宿主能力，
工程量大于退役本身，而做完后目标场景已被 jarvis-desktop 覆盖。

## 四、连带改动清单

| 位置 | 改动 |
|---|---|
| `agent/ui/workbench/app.py` 等 4 文件 + `assets/` | 删除 |
| `agent/daemon/autostart.py`（687 行） | 删除：开机自启整个建立在 `jarvis --gui` 上（VBS 拉 `pyw -m agent.main --gui`），停 GUI 就必须停自启；桌面应用自带开机自启设置 |
| `agent/ui/workbench/__init__.py` | 不再导出 `run_workbench`，docstring 改为「serve 与桌面壳共用的引擎层」 |
| `agent/ui/workbench/api.py` | 删 `_window` / `set_window` / `window_minimize` / `window_close`（只有窗口才需要，serve 与测试均无引用） |
| `agent/main.py` | `--gui` / `--talk` 改 `help=argparse.SUPPRESS` 保留解析，新增 `announce_workbench_retired()` 打印下线说明与自启清理指引 |
| `pyproject.toml` | 删 `realtime_ui` extra（`pywebview>=5.0`）与 `all` 中一行；`uv lock` 随之移除 pywebview 及 pyobjc-framework-webkit / pythonnet / qtpy 等传递依赖 |
| `npm/install.js` | 删「实时聊天窗口」可选项 |
| `agent/daemon/__init__.py`、`agent/serve/app.py` | 更新提及 autostart / run_workbench 的描述 |
| 测试 | 删 `test_workbench_single_instance.py`（3 例）、`test_workbench_chat_fold.py`（16 例，纯断言 assets）、`test_autostart_cli.py`（3 例），共 22 例；`test_workbench_engine_api.py` 补 2 例退役入口用例 |

参数保留而非直接删除的理由：历史版本写过开机自启的用户，其 `.lnk` 指向
`jarvis --gui`。argparse 直接报 unknown argument 时，wscript 静默运行下用户
看不到任何原因；保留识别才能给出可读提示与清理路径。

## 五、踩坑：提示文案把退役逻辑自己弄崩

### 问题现象

`announce_workbench_retired()` 首版第一行是 `print("ℹ 三栏工作台窗口…")`，
单测 `main(["--gui"]) == 0` 通过，真实执行却 **exit 1 + 空白输出**：

```
UnicodeEncodeError: 'gbk' codec can't encode character '\u2139' in position 0
```

### 排查过程

1. 先按常规跑全量单测：2543 passed（含新增的两例退役入口用例），表面上一切正常；
2. 人工实跑核验输出：`python -m agent.main --gui` **退出码 1 且无任何输出**，
   与单测结果直接矛盾——这是本次排查的唯一转折点；
3. 单独抓 stderr，拿到 `UnicodeEncodeError: 'gbk' codec can't encode character
   '\u2139'`，确认挂在第一行 `print` 本身，而不是退役分支未命中或 Rich 未就绪；
4. 定性为输出编码问题而非逻辑问题：`capsys` 捕获的是内存流，按 UTF-8 读写，
   根本不过 GBK 编码这一层，故单测天然假绿；
5. 改文案 + 给测试补编码约束断言，再用真实 GBK 控制台重跑，确认退出码 0、输出完整。

### 根因分析

原因：该分支在 Rich 控制台建立之前，裸 `print` 直接写 Windows ANSI 代码页
（GBK）控制台，而 `ℹ`(U+2139) 不在 GBK 字符集内。`capsys` 捕获的是内存流，
按 UTF-8 写读，天然暴露不了这个问题。

### 修复方案

修复：文案只用 GBK 可编码字符（改为「提示：」），并给测试补
`out.encode("gbk")` 断言把这条约束钉住。

### 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/main.py` | `announce_workbench_retired()` 文案去掉 GBK 外字符（`ℹ` → 「提示：」） |
| `tests/ui/test_workbench_engine_api.py` | 退役入口用例补 `out.encode("gbk")` 断言 |

**可复用结论**：在 Rich/自定义编码兜底之前用裸 `print` 输出中文与符号，
必须保证内容可被 `gbk` 编码；此类路径的测试要显式加一行 `.encode("gbk")`，
否则 `capsys` 会给出假绿。

## 六、验证

- `uv run pytest -q` → **2543 passed, 3 warnings**（退役前 2563：删窗口壳/前端
  结构/自启相关 22 例，新增 2 例退役入口用例；3 warning 为既有
  `tests/test_sandbox_more.py` 的 proactor `__del__` 告警，与本次无关）
- `uv run pytest tests/ui/test_workbench_engine_api.py -q` → 27 passed
- 真实执行 `python -m agent.main --gui` / `--talk` → 退出码 0，GBK 控制台输出完整提示
- `python -m agent.main --help` → 不再出现 `--gui` / `--talk`，`--serve` 保留
- `import agent.ui.workbench, agent.serve.app` → 正常（引擎层未被误伤）

## 七、存量用户清理指引

新版不再写入任何自启项，但旧版残留需手动删除（`jarvis --gui` 也会打印同一份指引）：

- Windows：`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\JARVIS.lnk`、
  `~/.jarvis/start_workbench.vbs`、`~/.jarvis/jarvis_window.ico`
- macOS：`~/Library/LaunchAgents/` 下 JARVIS 的 `.plist`

替代路径：图形化用 jarvis-desktop；终端用 `jarvis`（REPL）或
`jarvis --serve`（配合桌面壳）。`/talk` 命令不受影响——2026-09 起它就是
纯终端全双工，不依赖这个窗口。

## 八、文档口径同步与死链核查

退役结论铺到所有活文档（历史技术结论只加 ❌ 标注降级为历史快照，不整段删除，
避免要么全删丢了复盘、要么留着误导人）：

| 文档 | 改动 |
|---|---|
| `docs/architecture/07-UI层.md` | 顶部概述改「两种本地 UI」；workbench 节改「引擎层」+新增
「窗口壳（❌ 已退役）」小节；serve 对比表标退役列；设计取舍表新增「图形化窗口宿主
= jarvis-desktop」决策行 |
| `docs/architecture/08-Daemon模式.md` | 第二节桌面入口重写为 jarvis-desktop 形态；
第五节开机自启整节标「已退役」并给出存量清理命令 |
| `docs/architecture/01-总体架构.md` | 技术栈表、目录树、启动流程图、multiprocessing Q&A |
| `docs/test/TEST_CHECKLIST.md` | 第 16 节重写：删 15 行窗口用例，新增退役入口用例
T-359–T-362（含 GBK 无 `UnicodeEncodeError`、`--help` 隐藏、零副作用）；用例总数修正为
348 项（原 171-175/177-181 十个重复编号随窗口用例删除自动消除） |
| `USER_GUIDE.md`、`README.md`、`README.en.md` | 能力清单、桌面入口表、模型三入口、
参数表、平台支持行 |
| `config-docs/configuration.md`、`config-docs/voice-setup.md` | 参数表 `--gui`/`--talk`
合为退役条（顺带补上原来缺失的 `--serve` 条）；删 `jarvis --talk` 启窗口示例 |
| `docs/plans/workbench-gui.md` | 标题标「已完结并退役」+ 顶部退役块，正文声明为历史快照 |
| jarvis-desktop 仓 8 处 | `README.md`/`README.en.md`/`docs/guide/installation.md`/
`docs/architecture.md`/`docs/development.md` 与 `scripts/gen_icon.py`、`src/main/index.ts`、
`src/renderer/src/{api/dispatcher.ts,stores/chatStore.ts,stores/leftStore.ts,
styles/main.css}`、`test/renderer/dispatcher.test.ts` 注释：把「与 pywebview 工作台
同口径」改为「历史口径」，引擎层引用则明确为 `agent/ui/workbench/` |

同步修正的实测口径（旧文档均为过期值）：`protocol.py` 桌面指令 **51** 条（原写 30/48）、
`tests/serve` **105** 项、`tests/ui` **238** 项（原写 82/126）。

### 核查工具

新增 [`scripts/check_md_links.py`](../../scripts/check_md_links.py)：扫描仓内所有 md 的
本地文件链接（含 `file:///` 绝对与相对写法），跳过外部站点/纯锚点/站点绝对路径/
`data:` 内联资源与厂商文档快照目录，报告不存在的目标。退出码 0=无死链、1=有死链，
可直接接入 CI 门禁；可选参数指定扫描根（也可用于核查 jarvis-desktop）。

配套测试 [`tests/test_md_link_checker.py`](../../tests/test_md_link_checker.py)（7 例）：
相对链接命中/未命中、锚点片段剥离、非本地链接一律跳过、目录跳过规则、
`file:///` 按绝对路径解析、CLI 退出码与报告输出，以及**全仓零死链门禁**用例。

核查结果：`jarvis` 122 个 md / `jarvis-desktop` 13 个 md，**死链 0**。本次除了修掉退役
带来的失效链接，还顺带修正了 14 处与退役无关的既有相对路径错写（如
`docs/blueprint/BLUEPRINT.md`、`README.en.md`、`03-LLMProvider层.md` 等，以及
jarvis-desktop fixlog 里跨仓链接少写一层 `../`）。

## 九、相关文档

- 计划文档：[docs/plans/workbench-gui.md](../plans/workbench-gui.md)（已标记完结）
- 窗口壳单实例历史：[workbench-single-instance-release-fix.md](workbench-single-instance-release-fix.md)
- 桌面入口移交历史：[docs/guide/desktop.md](../guide/desktop.md)
