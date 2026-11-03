# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

## [未发布]

### 修复（画像记忆重复入库）

- **同一条事实被记很多遍**（如“习惯深夜工作”在 `~/.jarvis/memory/profile.json` 里出现 6 次）：
  旧实现只靠提炼 prompt 里“重复放 updates”的软约束去重，LLM 经常无视（把旧事实当新事实再报），
  叠加“已有画像”展示上限 50 条使溢出部分对模型不可见，重复事实被反复入库。修法：存储层加确定性
  查重闸 `ProfileStore.merge_or_add()`（归一化全等或字符二元组相似度 ≥0.85 即并入已有条目，置信取大、
  manual 优先保留），提炼展示上限 50→80，prompt 明确“语义相同禁进 new”，每轮提炼写盘后再做一次
  全量归并 `dedup_all()` 清掉修复前积压的重复。新增 `docs/fixlogs/profile-duplicate-memory-fix.md`。

### 新增（/memory dedup 命令）

- **`/memory dedup [yes]`**：两段式归并画像库已积压的深重复记忆（先只读预览重复组与可归并条数，
  `yes` 才执行，manual 手动条目优先保留），与 `/memory clear`、`/memory sync` 同确认体例。
  `/memory add` 改走查重入口，重复口述同一件事并入已有条目而非新增。登记点：router 无需改（`/memory`
  前缀已路由到 handle_memory），已同步 `slash_completer`、`docs/guide/commands.md`、
  `docs/guide/concepts.md`、`docs/architecture/09-记忆与压缩.md`。新增 14 例单测（合计 69 例画像测试通过）。

以下文档订正不改行为，只把**对外数字承诺**拉回与源码一致（全部真值由 AST / `tomllib` / 运行时实测得出）。

### 文档（事实性订正）

- **内置工具数**：`100+ 内置工具` → `70+ 内置工具（接入 MCP / harness 可扩展至 100+）`。
  真值：`agent/tools` 下 Tool 子类 74 个，`build_default_registry()` 实装 46（核心 21 + 延迟 25），
  接 MCP / harness 后实测可达 159——原“100+ 内置”把外部扩展误计入内置。
  涉及 `README.md`、`README.en.md`、`npm/README.md`、`pyproject.toml` 描述、`docs/blueprint/BLUEPRINT.md`、
  `docs/guide/concepts.md`、`docs/roadmap/PLAN-butler-autonomy-upgrade.md`。
- **配置可问总数**：`207` / `205` → `202`（`Settings` 字段实数与 `build_catalog()` 均为 202）。
  涉及 `README.md`、`config-docs/configuration.md`、`docs/test/config-self-service-testing.md`。
- **可代改口径**：`40 项直接改` → `28 项直接改（auto）/ 29 项改前先确认（confirm）`，禁改 1 项（真源表共 58）；
  `/config list` 实展 57 项。涉及 `README.md`、`README.en.md`、`config-docs/configuration.md`。
- **英文 README 滞后补齐**：感知源 `Five` → `Seven`（与 `factory.IMPLEMENTED_SOURCES` 7 个对齐），并补上中文已有的
  “配置不用翻文件”能力行。

### 文档（跨平台能力矩阵）

- `README.md` / `README.en.md` 原来一句笼统的“全部功能 ✅/✅/✅”换成**按能力细分的跨平台矩阵**，
  明确标出：系统维护层（`/disk` `/clean` `/health`，依赖 PowerShell + `SecurityCenter2` + 盘符 + `cleanmgr`）与
  jarvis-desktop 安装包为 **Windows-only**；窗口感知/GUI 窗口管理在非 Windows 依赖 `pygetwindow` 受限；
  日历源在非 Windows 仅 ICS（无 Outlook COM）。`docs/guide/installation.md` 同步补“能力级平台差异”指引。

### 修复（模型切换：跨厂商往返后端点停在上一个厂商）

- 现象：DeepSeek 返回 `503 Service is too busy` 后，用户 `/model qwen3.7-flash` 切去 DashScope（正常），
  再 `/model deepseek-flash` 切回，请求却被发到 `dashscope.aliyuncs.com/compatible-mode/v1`，拿回
  `404 The model deepseek-flash does not exist`——看着像“模型不存在 / 厂商坏了”，实际是 jarvis
  把请求发错了地址（DeepSeek 全程没有参与这次失败）。
- 根因：`_build_switched_provider` 靠“自定义条目与当前 provider 端点的差异”决定是否重建，而自定义
  条目的 `base_url` / `api_key` **正常就是留空的**（添加时留空即按厂商推断、不落盘；keyring 接管后
  密钥不写明文），`api_format` 两边又同为 `openai`——三条判据全部短路，于是只换模型名、不换端点。
  `_infer_model_vendor` / `_infer_base_url` 与那三条判据是同批（`4b77351`，2026-07-30）引入的，却从未
  被切换路径调用。
- 修复：新增 `_switch_target_changed_vendor`（按 **host** 判厂商归属是否变更，不比全串——
  `api.deepseek.com` 与 `…/v1` 算同一家）与 `_vendor_api_key`（按目标厂商查 keyring，取不到降级为
  继承当前 key）；重建时端点/密钥走“显式配置 > 按厂商推断 > 继承当前”。**三种情况保守复用**：
  厂商推不出来、端点由 SDK 自管、当前 host 不在厂商表里（用户自配网关）——最后一条与
  `fixlogs/init-not-applied-to-session-fix.md` 定下的“留空先沿用原值”口径一致，不把流量从代理上拐走。
- 影响版本：v2.2.2 ～ v2.3.4（该判据自 2026-07-30 就在，**升级不解决**）。临时绕过：给该自定义模型
  条目显式补上 `base_url`（已验证有效）。
- 测试：新增 `TestSwitchVendorEndpointNotSticky` 6 例（跨厂商切回 / 同厂商少 `/v1` / 自建网关 /
  厂商推不出 / keyring key 跟随 / keyring 异常降级），并给该测试文件加模块级 autouse fixture 隔离
  keyring（隔离点选在底层 `load_api_key` 而非被测函数，保证降级分支仍可测）；该文件 62 passed。
  4 类变异均被抓到，其中“host 比对退化回全串”会额外打红 3 条既有测试——反证该设计是既有契约
  的要求。复盘见 [fixlogs/model-switch-vendor-endpoint-fix.md](docs/fixlogs/model-switch-vendor-endpoint-fix.md)。

### 修复（厂商 5xx 被当成“你的网络问题”，以及它背后的六处缺陷）

- 现象：DeepSeek 过载返回 `503 Service is too busy`，jarvis 却让用户「检查网络连接 /
  base_url / HTTP_PROXY」；而且当天反复出现却**在 `~/.jarvis/jarvis.log` 里搜不到一条 503**。
  两者都不是厂商的错：`classify()` 把 `status in (502,503,504)` 塞进了 NETWORK 兜底，抢在专为
  5xx 写的 `SERVER_ERROR` 分支之前（正确文案就在其十几行之后，从未执行）；而全仓 **19 个模块**
  用 `logging.getLogger(__name__)` 取裸 logger，handler 却只挂在 `jarvis` 那一个名字上，于是
  消息只命中 Python 的 lastResort（原样甩到 stderr、不写文件）。
- 分类与恢复：新增厂商 5xx/过载分支（**状态码优先于关键词表**，同时避开 504 的 `timeout` 与
  关键词表里 `token` 的两处误伤），文案明确“这不是你的网络/base_url/代理”，并给出 `/model` 切走
  与 `vendor_fallback` 两条可操作路径；`NETWORK` 兜底摘掉 502/503/504。
- **拆掉“拿用户可见文案当控制流开关”**：`ProviderError` 新增 `category`，四个 Provider 的抛出点
  统一走 `to_provider_error()`；`query_loop` 的网络重试判据改用 `effective_category()`（category
  优先，无分类时才回退文案匹配）。旧写法是 `if "网络错误" in error_str`——改一句提示语就让重试
  静默失效且测试全绿；而厂商 5xx 误入该分支会先白耗 1.5s 退避 + 一次注定失败的请求才轮到
  `_try_failover()`，现在直接走故障转移。
- 日志可观测：handler 同时挂到 `agent` 命名空间根（幂等、`propagate=False`、保留各模块 logger
  name），一处修好 19 个模块；`set_debug()` 排除 `FileHandler`（它是 `StreamHandler` 的子类，
  以前关 DEBUG 会把**文件** handler 一并降到 WARNING，`LLM 计时` 这类 INFO 从此静默不落盘）。
- 升级提醒误报：editable 安装下 `current_version()` 拿到的 dist-info 版本停在装包那天，横幅变成
  “发现新版本 2.3.4（当前 2.2.2）”，而提示又硬编码“运行 /update 或 `pip install -U`”——后者会把
  安装换成 PyPI 正式版、**直接覆盖源码树里尚未发布的修改**。现在版本优先取 `agent.__version__`，
  升级文案按 `upgrade_command()` 分渠道生成（editable 下改为“请用 git pull”）。
- 思考面板定格：面板收尾以前只挂在 `assistant_text()` 第一行（正文第一个增量），所以上面那种
  “正文一个字都没来”的失败轮（实测日志 `思考 69 字 / 正文 0 字`）会把面板永久停在首帧。现在
  `RichCLI.end_thinking()`（幂等）登记进 `UIProtocol`，由 `_stream_once` 的 `finally` 调用，
  异常/中断/取消都收口；委托型 UI（bridge / wechat）转发。
- 顺带修掉一条潜伏的测试竞态（本轮 P5 抬高负载后才撞上）：`maybe_refine_async` 起的 daemon
  线程在用例结束后还会活约 1.6s（默认 `Settings()` 的 `api_format` 是 `openai`，真去建
  `AsyncOpenAI`），漂进后续 13 个用例；若线程启动被延迟到下下个用例装好 monkeypatch 之后，就会
  借用那个 fake 并把它撑脏，导致 `test_full_pipeline` 全量跑偶发独红、单跑必绿。现补两道自收闸
  （真 LLM 工厂“调了就记名、收尾就红” + 用例收尾 drain 线程），并把不变量写成显式用例。
- 实测结论（“慢”不属于 jarvis 的那一半原因）：同一 `system_chars=40179 / tools=1 / msgs=2` 下首字正文
  1.45s / 23.63s / 52.43s，且慢的那次“思考 0 字”——**慢在厂商侧排队**，不是本地 prompt 变大；
  88 个 MCP 工具因 `deferred` 从未进过请求（日志里 `tools` 最大 37）。
- 测试：新增 27 例（`test_llm_base_errors.py` +7、`test_query_loop_run.py` +4、`test_logging.py` +4、
  `tests/ui/test_cli_thinking.py` +4（新文件）、`test_update.py` +5、`test_remote_bridge.py` +2（委托型
  UI 转发）、`test_profile_memory.py` +1）；
  全量 **4199 passed / 0 failed / 2 skipped**（修竞态前首次全量是 `1 failed, 4195 passed`，触发率
  约 1/4）；变异矩阵 **V1-V9 + M-A/M-B 均被抓到**（其中 V1 只让文案用例红、category 用例全绿，
  实证状态码与关键词是互补双路）。复盘见
  [fixlogs/provider-busy-error-classification-fix.md](docs/fixlogs/provider-busy-error-classification-fix.md)。

### 测试（发布 / 打包契约）

- 新增 `tests/test_release_contract.py`（5 例，纯本地、无网络/端口，0.35s），把发版前“本应成立却无人把关”
  的不变量钉死：
  - **三处版本号一致**（pyproject ↔ npm/package.json ↔ `agent.__version__`）——`publish.yml` 只校前两处，
    `__version__` 那行“不在 CI 校验范围”的第三处由本测兼顶；
  - **配置目录覆盖每个 `Settings` 字段**（新字段忘进目录 = 用户问了答不上）；
  - **随包声明式文件存在且被 package-data 匹配**（`cleaners.toml` 等）；
  - **内置规则库在 POSIX 上的去向逐条对账**（详见下方“修复（回归）”条目）——Windows 上的“零静默拒绝 +
    关键 id 名单”已由 `tests/core/test_cleaner_rules.py` 把关，本文件刻意不重复它。
- `ci.yml` 新增“Install smoke”步：从干净临时目录跑 `jarvis --help` + 断言装出来的包带上了 `cleaners.toml`
  （确保 import 的是 site-packages 里那份而非源码树）——这类“漏打包”缺陷源码树里的单测测不到。
- 说明：本批只做“装配契约 + 安装冒烟”两把锁；已核对现有 `tests/serve/test_serve_protocol.py` 已
  双向覆盖 `DESKTOP_COMMANDS`↔`_rpc_types` 与指令数，故未重复；QueryLoop / orchestrator / tool_dispatch
  已由单测充分覆盖，不再加重叠的端到端对话测。
- 修复（回归）：新增的 `test_builtin_rulebook_parses_clean` 在 Linux CI 误红——内置规则库 15 条
  `paths` 型规则用 `%LOCALAPPDATA%` 等 Windows 环境变量，POSIX 展不开被 fail-closed 闸如实拒绝
  （属正确行为），测试却把它当成了跨平台不变量。**初版修法给整条用例打 `_WINDOWS_ONLY` skip，
  随即推翻**：`rules.py` 四道校验闸里只有一道受平台影响，skip 会把命令白名单、`risk`/`kind` 合法性、
  `auto_ok` 互斥这三道 Linux 本来能抓的闸一起关掉。改为 `test_builtin_rulebook_rejects_only_platform_paths`：
  在 Linux 上**正向断言**被拒的恰好是全部 `paths` 型、`command` 型全部通过、kind 与声明逐条一致、
  声明里每条不得静默消失，同时反向坐实 README 平台矩阵那句“POSIX 如实拒绝”（README 中/英与
  `docs/architecture/19-系统维护层.md` 已回指）。本地用 `mock.patch.dict(os.environ, …, clear=True)`
  在 Windows 上精确复现 POSIX 行为（`loaded=4 / rejected=15`，拒因与 CI 日志逐字一致），并用 5 类
  变异确认断言真能拦截。复盘见 `docs/fixlogs/release-contract-rulebook-linux-skip-fix.md`。

## [2.3.4] — 2026-11-01

这一版收两处**误导**。一处在交互上：项目区写着 A、你打开的会话却属于 B。
一处在目录结构上：一个从不渲染界面的引擎层，因为历史上服务过已退役的窗口壳，
一直挂在 `agent/ui/` 下，还得在 `agent/ui/__init__.py` 里写一段「这是例外」的注释来圆谎。

### 新增能力

- **打开历史会话，项目区跟着走（桌面壳）**
  左栏「历史会话」里点一条旧会话，下方「项目」区会切到那条会话当初所在的目录。
  会话本来就记着 `SessionMeta.workdir`，恢复它却不恢复目录，就会出现「面板写着 A
  项目、对话内容全是 B 项目」——用户据此判断“我现在在哪”会误判；接着发消息，读写
  文件和跑命令还会落在 A 里。

  落地复用切项目的同一条主链（重建系统提示词 + 重挂 harness + 置顶最近项目），但
  **不开新会话、不弹“已切换到项目”**：用户的动作是“打开这条会话”，不是“切项目”，
  刚载入的对话不能因换目录被清掉。会话目录已被删除或改名时，保持当前项目并提示一
  句，历史消息照常可看——不能因为目录没了就看不了旧对话。

  桌面壳侧**零代码改动**：`project_switched` 事件本来就会写回当前项目、重拉项目区与
  会话列表，高亮自然跟上。

  终端 `/load` 未同步此行为（REPL 仍只恢复消息与轮数）。

### 移除

- **删掉 `--gui` / `--talk` 两个参数与退役提示**
  这两个参数在 2026-10 窗口壳退役后并没有立刻删，而是作为一块垫子留着：历史
  版本写入的开机自启快捷方式指向 `jarvis --gui`，在 wscript 静默运行下若直接报
  unknown argument，用户根本看不到原因。所以它会捕获这两个参数、打一段下线说明与
  自启清理路径再返回 0。

  垫子到期了，连同 `announce_workbench_retired()` 整块移除。代价是：若仍有旧自启项
  在跑，它现在会静默失败而非报错——存量自启残留的处理方式见 desktop 指南与
  `workbench-gui-retired.md` 第七节，那两处本来就是权威位置。

  顺带把引擎层从终端 UI 层里**掘出来**：`agent/ui/workbench/` → `agent/serve/engine/`。
  这 19 个文件从来没渲染过界面，是 `jarvis --serve` 与桌面壳共用的对话引擎
  （ChatEngine + WorkbenchAPI + 指标采集），只因历史上服务于已退役的窗口壳才被
  放在 `agent/ui/` 下。迁完后 `agent/ui/` 只剩纯终端渲染零件。

  `WorkbenchAPI` / `WorkbenchUI` 这两个类名**刻意未改**：它们是 `--serve` 的 WS
  协议契约名，jarvis-desktop 的 `contracts.ts` 按此镜像，改名只会制造一处无收益的
  跨仓不一致。测试同步归位：`tests/ui/test_workbench_*.py` 等 15 个引擎用例迁到
  `tests/serve/engine/` 并去掉 `workbench_` 前缀，`tests/ui/` 只剩 5 个终端用例。

  `api.py` 里「暴露给前端 JS 的 pywebview API」「窗口控制 window_minimize /
  window_close」这些早已不成立的描述一并删掉——那两个方法本体在 2026-10 就不存在了，
  只有 docstring 还留着，比不写更误导。

### 工程

- 全量 `pytest`：**4148 passed, 1 skipped**（会话与项目区联动新增 7 例：
  `tests/serve/engine/test_session_workdir_link.py` 5 例 + `test_project_switch.py` 2 例）。
- 清掉 3 处 lint 告警：`api.py` 一个从未被引用的局部变量、`slash_bridge.py` 的返回
  类型注解改在 `TYPE_CHECKING` 下导入（延迟导入的约定不变，注解不求值、运行时零开销），
  以及测试里一个未使用的 `tempfile` 导入。
- 跨仓口径对齐：jarvis-desktop 的 `contracts.ts` / `metricsStore.ts` 与四份文档里
  「数据源见 jarvis 侧 `agent/ui/workbench/xxx.py`」的注释同步改到新址。

## [2.3.3] — 2026-11-01

这一版解决同一个毛病的两面：**配置项写着，但改了不起作用，或者根本没人读**。

一面是**问不出来**。`Settings` 有 202 个字段，而自配置层只收 58 项（都是“可以
代改”的）。剩下 144 项用户想调时，模型的处境是**既不知道它存在，也说不清它在哪**，
只能含糊回一句“请打开 settings.toml”。而命令行 `/config list` 也帮不上忙：它要用户
先知道有这条命令。**用户不懂配置时最自然的入口是问，不是敲命令。**

另一面是**写了不生效**。7 项配置要么无人读、要么挂在没有驱动点的分支上，其中久坐
提醒还是“测试全绿却从不触发”的假交付。这一版逐项收口：5 项接线、2 项删除。

### 新增能力

- **配置问答：202 项全都有话说**
  新增 `agent/config/catalog.py`——把两百多个配置项变成能回答的知识，不只是能改的
  白名单。实测覆盖率 **202/202 = 100%**（每条都有说明、当前值、所在文件与段、能否代改）。

  关键是不造第五份人工表：项目里已有四套配置视图（真源表 / `percept_settings` /
  `desktop_settings` / `settings.example.toml`），再手写一份 202 项的中文说明必然漂移。
  所以目录**只派生**，说明按精确度四级取：真源表 `hint`（58 项）→ 模板行尾注释（补到
  76%）→ 段级用途 / 人工补丁（补齐到 100%）。

  实现里踩到两个坑：①`[email]` 段把带说明的字段整行注掉当文档用（`# smtp_user =
  "x@163.com"  # 发件邮箱账号`），正则不认行首 `#` 就从这一段一个说明都拿不到；
  ②两份模板（包内 `agent/configs/` 与仓库根 `configs/`）段与字段**互补**，只读一份会
  白丢二十来项。

- **`ConfigExplain` —— 让模型先查再答**
  第四个自配置工具。按关键词或段名查全量目录，返回里直接标明“我可以代改”还是
  “只能自己改（文件 + 段名）”，模型照旗标说话即可，不用自己判断。
  用户说得模糊时（“监控太烦了”）拿这一段出来挑，比反问“你指的是哪个配置”有用。

- **`/config explain <关键词>` / `--overview`**
  人自己用的入口，与工具同一份目录。用户翻两百行 TOML 时，敲一句比问模型快。

### 修复

- **“不认识的配置项”这句话是错的**
  `/config set maintenance_auto_clean_time 03:00` 会得到“不认识的配置项”——可这项
  确实存在，只是没进真源表。用户看到这句会以为自己拼错了，然后去两百多行里白找一场。
  现在按四种实情分开回：**存在但没人读**（说“未接线”+ 替代改法）、**存在但未纳管**
  （给文件 + 段名 + 说明）、**拼得接近**（列相近项）、**真不存在**（才说没有）。
  模型侧 `ConfigSet.validate_input` 也做了同样区分。

- **`[maintenance]` 十项进真源表**
  2.3.2 刚发的维护层当时一项都没纳管——用户想改“自动清理挪到凌晨 3 点”只能手改文件。
  现在纳管 10 项（`auto_clean_time` / `min_mb` / `notify_mb` / `quarantine_days` /
  `scan_*` / `report_min_mb` / `allow_full_scan` 等），另 2 项进护栏：
  `auto_clean_max_risk`（决定自动清理能碰到多高风险，是许可边界本身）与 `rules_file`
  （换规则文件等于换一套靶子）。

  生效方式一律没标 `reschedule`：自动清理确实是启动时注册的调度快照，但
  `hot_update_schedule` 目前只重注册简报/截止日期。标成 reschedule 会回一句“已按新配置
  重注册”——而实际什么也没变，比不标更糟。能现读的标 `live`，注册期快照标 `restart`。

- **7 项“死配置”如实标出来，并全部处理完**
  逐项核对“这字段到底谁在读”时揪出一类更隐蔽的：字段存在、面板可见、TOML 写得进去，
  但**没有任何代码读它**——改了完全不生效。共 7 项。

  处理不是“也给它开个代改口子”，而是先如实标出来：每项挂一句“改了不生效 + 那到底
  该改哪个”，`editable` 对它**强制为假**（将来误加进真源表也不放行），提示排在
  “能否代改”之前——把人推去翻文件等于骗他白改一场。然后逐项收口：

  - `[monitor] enabled` —— **删**。真开关是 `[percept] system_enabled`；留着只会让人
    在 `[monitor]` 段关了半天，而监控照跑。
  - `[monitor] check_interval` —— **接线**。`percept/factory.py` 改读
    `monitor_check_interval`，以前这里写死常量 60s，模板里那句注释等于白写。
  - `[monitor] high_cpu_duration` —— **删**。它对应的 `check_high_cpu_processes()` 只
    返回 `"chrome.exe (PID: 1) CPU: 67%"` 这种字符串，而 `/health` 的进程区块给的是
    CPU + 内存 + 启动时间的结构化 Top N，还守着“只发现、只解释、只建议”的处置原则。
    两套并存只会让人搞不清该信哪个。
  - `[monitor] work_break_interval` —— **接线**。久坐提醒原先方法写完整了、10 个测试
    也全绿，但它**不在 `_do_check()` 里**——而 `_do_check()` 是全工程唯一的监控驱动点
    （`SystemSource.collect()` 每 60s → `check_now()` → `_do_check()`；`start()` 从来
    没人调过）。测试自己直接调方法，绕过了真实入口，所以绿得毫无意义。
    现在挂进 `_do_check()`，并顺手修掉口径缺陷：原来“空闲 ≥60 秒就清零计时”，用户
    读段文档、想会儿问题就把 2 小时的账清掉，阈值永远攒不满；改为**离开超过 5 分钟
    才算真中断**，起点按“真正落座那一刻”回溯。
  - `[calendar] enabled` —— **接线**。新建 `proactive_wiring.py` 按 `[calendar]` 装配
    `CalendarSource` 传给 `ProactiveEngine`（hub 那句“日历源与系统监控本期不接线”
    把两件半成品混着说），简报里从此真有“今日日程”。
  - `[skills] enable` / `[plugins] enable` —— **接线**。两个都用模块级开关：加载入口
    散在 UI / 工具 / 命令三条路上（技能 8 处、插件 11 处），逐层传参漏一处就会出现
    “面板里关掉了、模型还在用”。插件开关只管装/卸/启/停这类写动作，读操作不拦——
    关掉系统不等于连“我装了什么”都不给人看。

  至此 `catalog.DEAD_FIELDS` 清零。表留着不拆：下一个“字段在、没人读”的情况还得往
  这里放，拆了等于拆掉检测能力。

- **用属性名问配置时被误判成“不能代改”**
  `/config set monitor_work_break_interval …` 这种按 `Settings` 属性名问的写法，明明
  这项已经纳管、一句话就能改，却被回“不在可以代改的范围”，把人打发去翻文件。
  现在这种情况会把真源表的协议键直接给出来：`/config set work_break_interval <值>`。

- **`[monitor]` / `[context]` 与能力总开关 13 项进真源表**
  `monitor` 那组阈值一直存在、却一直没人读——现在真管告警的是感知源的 `SystemSource`，
  它装配时吃进 `cpu_threshold` / `memory_threshold` / `disk_threshold` / `alert_cooldown`
  / `disk_trend_days` / `check_interval` / `work_break_interval`，所以这七项标
  `percept`（标 `live` 就是“落盘不生效”）。`context`
  四项与 `enable_mcp` / `enable_lsp` / `deferred_loading` / `chat_detection` 都在启动或
  重建 engine 时构造，标 `restart`。用户以前改这些只能手翻文件。

  顺带一处写进注释的取舍：`enable_tool_self_healing` **没有**纳管——它不在
  `settings_domains` 任何映射里，`save_value` 找不到落盘位置。纳管它等于回一句“已改好”
  而实际没落盘。

### 移除

- **删掉全局热键（`agent/daemon/` 整包）**
  热键从一开始就只服务于「召唤 pywebview 三栏工作台窗口」——那个工作台 2026-10 退役后，
  这套代码就没有服务对象了，一直挂着「待接线」。本次整体删除：

  | 删除项 | 规模 |
  |---|---|
  | `hotkey.py`（keyboard / pynput 跨平台） | 137 行 |
  | `hotkey_native.py`（Windows RegisterHotKey） | 270 行 |
  | `platform_utils.py`（只为它服务的平台探测） | 47 行 |
  | `tests/daemon/test_hotkey.py` | 72 行 |
  | `Settings.daemon_hotkey` / `_native` / `_debounce_ms` | 3 字段 |

  以及 `settings_domains` 三条映射、`settings.py` 的加载白名单、三个 `settings.toml`
  模板里的 `[daemon] hotkey*`。整个 `agent/daemon/` 包随之消失。

  **为什么是删而不是继续等接线**：现在的宿主是 jarvis-desktop（Electron），它自带
  `globalShortcut`——注册系统级快捷键是原生能力，一行代码；而 Python 侧那套要自己开
  只消息窗口、跑后台线程、再按平台分三条后端，属于**架构错配**。桌面热键应该做在
  Electron 主进程里。

  旧配置里留着 `hotkey = "..."` 不会报错（加载器对未知键静默忽略），但已经不会再有任何
  效果，所以三个字段都登进了 `REMOVED_FIELDS`：问「hotkey 这项怎么改」会直接回
  「已于 2026-11-01 删除，桌面热键归 Electron」，而不是让人去翻一个不存在的字段。

### 工程

- 真源表 30 → **58 项**（28 auto / 29 confirm / 1 forbidden），配置可代改率
  14.5% → 28.3%；目录覆盖 `Settings` 全部 202 个字段，未接线 **0** 项。
- 配置测试共 **142 例**（`test_catalog.py` 39 / `test_config_specs.py` 103），守住三条
  不变量：**一个都不漏**（目录覆盖全部 `Settings` 字段）、**一个都不空**（每条都有说明）、
  **凭据不吐值**；另有专用用例守死配置与真源表形状（死配置永不进表 / 永不判为可代改 /
  `DEAD_FIELDS` 非空时必须指出替代改法），以及“装配那一行到底传没传”——
  `test_factory_really_passes_work_break_interval_to_system_source` 就这么来的。
- 全量 `pytest`：**4143 passed, 1 skipped**。
- 桌面设置面板底部新增出口说明（中英双语）：面板只摆了十来项，其余两百多项不用翻文件
  ——聊天里问一句就能查、能改的会代改，并给出 `settings.toml` 与 `/config explain` 两条路。
- 修一条把数字写死的旧断言：`/config list` 的项数改为从真源表派生，并逐条断言每个
  可写键都出现在清单里——真正要守的是“列出的集合 == 可写的集合”，不是那个数字。
- 提示词新增“用户不懂配置时”一节：不知道就查、分清两种情形、查不到就说没有。

## [2.3.2] — 2026-10-31

本次补齐电脑管家八件事中的三件：**清理磁盘 / 查杀病毒 / 性能诊断**（排前两版一直空着的 6.1、6.2、6.4）。
设计取舍与实测数据见 [19-系统维护层](docs/architecture/19-系统维护层.md)。

### 新增能力

- **`/disk` —— 回答“空间到底被谁占了”**
  声明式规则库 `cleaners.toml`（19 条：7 low / 9 medium / 3 high，8 条需管理员）出可回收清单。
  但实测发现一个真问题：本机 C 盘 100GB 只剩 14.6GB（85.4% 占用），可回收垃圾加起来
  **只有 27MB**。所以额外做了 `/disk --top`，直接对根目录做体积排行——用户问“清一下”，
  真正想知道的是空间去哪了，不是垃圾有多少。

- **`/clean` —— 隔离区式清理，随时可撤**
  这是子系统里唯一会主动动用户文件的部分，因此：无 `--yes` 只预览；`[maintenance].enabled`
  默认关；即使开着也只动规则库白名单里的路径。
  撤销口径与直觉不同：**用移动进 `~/.jarvis/quarantine/<批次>/`，不是复制式快照**。
  现有 `FileGuard` 写前留底是复制式且阈值只 50MB，GB 级垃圾复制一份等于先把磁盘占满。
  同盘 `move` 是元数据操作，微秒级、不占新空间。还原走 `/clean --restore <批次>`
  （**不是 `/undo`**——隔离区批次与 W3 副作用账本是两套记录）。

- **`/health` —— 七区块体检报告**
  资源 / 磁盘 / 安全 / 进程 / 启动项 / 电源 / 温度，一次 PowerShell 取回，
  支持 `--markdown` 与 `--save`。非管理员跑时，温度与 SMART 会**如实写“需要以管理员
  身份运行才能读取”**——不显 0、不显 `--`、不静默丢块。

- **杀软诊断：不再被第三方杀软骗到**
  本机实测抓到一个会**系统性误报**的前提：Defender 被「电脑管家系统防护」接管后，
  `Get-MpComputerStatus` 照常返回且 `AntivirusEnabled` / `RealTimeProtectionEnabled`
  全为 `false`。只看它就会对装了第三方杀软的用户报「你没有实时防护」。
  现在以 `root/SecurityCenter2` 的 `productState` 位为**权威**（`0x1000`=启用 /
  `0x10`=库最新），Defender 自身字段降为补充；只有**所有产品都未启用**才允许说没防护。
  扫描异步执行（`/health scan` 立即返回任务 id，`/health status <id>` 查进度），
  全盘需显式开 `allow_full_scan`。

### 新配置

```toml
[maintenance]
enabled = false              # 清理总开关（关着时 /disk 与 /health 照常只读可用）
rules_file = ""               # 留空用内置 agent/config/cleaners.toml
quarantine_days = 7          # 隔离区保留天数，到期自动清
scan_max_dir_gb = 50         # 单条规则体积上限
scan_timeout = 120           # 扫描总超时（秒）
report_min_mb = 100          # 只读报告最低展示阈值（MB）
auto_clean_max_risk = "low"   # 允许自动执行的最高风险档
allow_full_scan = false      # 是否允许 Defender 全盘扫描
```

### 新增工具与命令

- 工具：`DiskReport`（只读）/ `DiskClean`（延迟加载，无 `confirm` 时只读）/ `SystemHealth`
- 命令：`/disk`、`/clean`、`/health`（含 `security` / `threats` / `scan` / `status` 子命令）

### 能力边界（如实说明）

- **不自研查杀引擎**，不接 ClamAV（Defender 已够，多一个引擎多一份维护成本与误报源）。
- **不自动杀进程**。热点进程是不是泄漏取决于用户当时在干什么，机器判断不了；“一键加速”
  杀掉的可能是跑了两小时的编译或导入到一半的模型。
- **不自研提权**（`Start-Process -Verb RunAs` 会弹 UAC 且打断授权链），只报告并提示手动提权。
- **WinSxS / 卷影 / 休眠文件不进 `/clean`**：它们只能靠 `dism` / `vssadmin` / `powercfg`
  直接做，**不可还原**。`--deep` 只给出命令文本，由用户自己跑。
- 刻意**不收录** `prefetch`（删了常用程序启动变慢）与 `node_modules`（那是工程目录）。

### 工程

- 新增 6 模块 + 3 工具 + 3 命令 + 5 个测试文件（112 例）；全量 **4030 passed, 1 skipped**，零回归。
- `cleaners.toml` 已加进 `package-data`（`config/*.toml`），否则装包后规则库找不到。
- 本机（Windows 10 22H2、非管理员、Defender 被第三方接管）跑穿隔离区闭环
  （moved 2 / protect 跳过 1 / restore 2 / 批次清空）与七区块体检（15.5s）。
- 文档：新增 [19-系统维护层](docs/architecture/19-系统维护层.md)，同步更新配置指南、
  命令参考、[系统维护层实机走查清单](docs/test/TEST_CHECKLIST-system-maintenance.md)
  （新立分册，T-397–T-427）、BLUEPRINT 6.1/6.2/6.4/6.9 与第 11 节。

### 内部重构：CPU/内存/磁盘采样收口

交付完成后顺手清了一处重复设计——同一个物理事实（CPU/内存/磁盘）原由两处代码各读一遍：
右栏三条进度条走 `workbench/metrics.py`（2 秒、非阻塞），阈值告警走 `core/daemon/monitor.py`
（10/60 秒、`cpu_percent(interval=1)` 阻塞）。这不只是代码重复：`cpu_percent(interval=None)`
返的是「距上次调用」的平均值，两个消费者会互相切碎采样时间窗（右栏 2 秒窗口尤其容易被
monitor 的 1 秒阻塞采样截断），两边数字跟着抖。

现在收敛成 `core/system_sampler.py`：

- `SystemSampler` 持**唯一** psutil 调用点，进程级共享（`default_sampler()`），默认 1 秒节流——
  右栏 2 秒节拍每次都真采（新鲜度不变），告警侧紧跟时吃缓存。
- `SystemSnapshot` 不可变快照，自带 `to_metrics_payload()`（右栏格式）与 `to_status_dict()`
  （`get_status()` 兼容格式），阈值仍归 `MonitorConfig`，快照不掺和判定。
- `metrics.py` 退化为格式转换；`monitor.py` 只留报警状态机（持续时长 / 600s 冷却 / 恢复通知），
  `get_status()` / `_do_check()` / `record_disk_usage()` 全部改读快照，**10 秒节拍的 1 秒阻塞没了**。
- CPU 首次采样短阻塞 0.1 秒预热（原来 1 秒），之后全非阻塞；磁盘 psutil 优先、`shutil` 兜底
  ——告警阈值一直按 psutil 的 `percent` 口径定，而 psutil 缺失时右栏磁盘条仍要有数。
- 新增 19 例 `tests/core/test_system_sampler.py`；`test_monitor.py` 改为注入
  `min_interval_s=0` 的采样器，保住「告警→恢复」状态翻转的可测性（单测里真实时钟几乎不走，
  节流会让两次 `_do_check()` 吃到同一份缓存）。全量 **4048 passed, 1 skipped**。

### 命令型规则：加入 Windows 磁盘清理（cleanmgr）

回应「能不能让系统自带的清理工具也参与进来」：命令白名单从 3 条扩到 4 条
（`ALLOWED_COMMAND_BINARIES` 新增 `cleanmgr.exe`），规则库同步加第 19 条 `cleanmgr_profile`。

- 它比现有 18 条能清的多得多（临时文件、更新清理、旧安装残留、缩略图…），但**范围完全
  取决于用户先用 `/sageset` 勾了什么**——里面有「下载」文件夹，勾了就会删下载文件。所以
  规则的 `desc` 把「必须先手动跑一次 `cleanmgr /sageset:64`」写在了明面上；不写这段，用户
  直接跑 `/sagerun` 什么都不会发生，只会以为是 bug。
- **不破「命令型不代执行」这条线**：`cleanmgr` 与 `dism` / `vssadmin` / `powercfg` 一样，
  只出现在 `/disk --deep` 的命令文本里，`cleaner.clean()` 依旧拒绝对 `is_command` 规则动手。
- 白名单的守门测试改成精确等值断言（`test_allowed_binaries_are_the_known_ones`），刻意
  不做子集断言——这张表是整个维护层最危险的一张，变大时应该有人停下来想一遍。
- 顺手修了一个缺陷：`/disk --deep` 之前**只给 id 和说明、不给命令**，用户看完还得自己
  去查 `clean_cmd`。现在每段都带上「要执行的命令」，提示行也区分「需执行分析命令」与
  「无分析模式」（cleanmgr 没有分析模式）。
- `/health` 的盘满建议里加了一句存储感知指路：一次性清理只能治标，真正把「盘慢慢变满」
  这个趋势管起来的是 Windows 自带的存储感知（设置 → 系统 → 存储）。
- 规则库 18 → 19 条，单测 112 → 115 例；全量 **4052 passed, 1 skipped**。

### 修复：一次真实清理对话暴露的三处误导

把「帮我清理垃圾」跑完整一遍（`/disk` → `--deep` → `DiskClean` → WinSxS）后，回看了全过程，
修掉三个不是崩溃、但**会把用户或模型带错方向**的问题：

- **还原命令写错了 8 处。** `DiskClean` 的工具描述、`ask` 确认提示、预览 hint、模块注释、
  `cleaners.toml` 的回收站说明里，全都写成「可用 `/undo` 还原」。而 `/undo` 走的是 W3 副作用
  账本，隔离区批次不在里面——敲下去注定落空，用户会以为删掉的东西找不回来了。
  正确入口是 `/clean --restore <batch_id>`（`cleaner.RESTORE_COMMAND`，同一模块里本来就写了
  「**不能写成 `/undo`**」的注释）。危害在于**模型会把工具描述原样转述**：实测回复里就出现了
  「DISM 安全清理，不可用 `/undo` 还原」。守门测试改成断言文案不含 `/undo`，而不是只断言
  「命令型不可还原」——后者在命令名写错时照样通过。
- **`trend_7d_gb` 的裸数字被读反方向。** 实测 `-1.58` 的真实含义是「可用空间变多」，模型却
  读成「近 7 天又少了 1.58GB」，并据此制造紧迫感催清理。实现没错，错在字段名没交代是
  「已用增」还是「可用减」。现在 `DriveInfo` 同时给 `trend_7d_text`（「近 7 天占用下降
  1.58 GB（已用变少、可用变多）」），`/disk` 终端输出也改成「近 7 天**已用** ↓1.58 GB」。
- **`top_root` 不存在时静默返回空数组。** 模型把用户主目录猜成 `C:\Users\acefelix`（真机
  是中文用户名），拿到 `top_dirs: []` 后只能自己连跑 `ls` / `du` 反复试错。空数组看起来
  像「没有东西占空间」，是这里最贵的误导。现在返回 `top_error` 并直接写出该传什么；目录
  存在但无一超阈值时另给 `top_note`。

- 单测 115 → 119 例（新增 `trend_text` 方向、`top_root` 缺失必报、`DiskClean` 文案不误指
  `/undo`）；全量 **4055 passed, 1 skipped**。

### 无人值守自动清理：许可独立于 risk

回应「那些完全不影响系统和我日常工作的垃圾，可以让 jarvis 随时监控并随时清理」。

- **关键决定：自动许可不复用 `risk` 档。** `risk` 衡量「删了会怎样」，自动清理要问的是
  「删了用户会不会发现」。`risk=low` 里混着 `recycle_bin`（用户刚删的文件就在里面）、
  `user_temp`（可能正被程序占用）、`thumbcache` / `shader_cache`（重建会卡一下）——
  拿风险档当自动许可等于用严重度顶替意愿。新增独立字段 `auto_ok`，默认 `false`，
  逐条人工评估后显式打开，fail-closed：新加规则不会因为漏标风险档而被悄悄执行。
- **当前只开两条**，标准是「删了用户永远不会察觉」：`crash_dumps`（崩溃转储）与
  `wer_reports`（错误上报队列）。19 条里其余 17 条各自有明确不选的理由。
- **两道反向闸**（`rules.py` 直接作废违规规则）：`kind=command` 不许 `auto_ok`
  （命令型不可还原，隔离区救不了它，允许就等于给「从不代执行」开后门）；
  `requires_admin` 不许 `auto_ok`（后台进程提不了权，只会白跑）。
- **三个触发点**，任何一层生效就够：每日 04:30（`Scheduler` daily 任务）、
  启动时机会清理（距上次超 20 小时；纯 CLI 是短会话，光靠定时可能永远遇不上）、
  手动 `/clean --auto`。三条都走同一个 `run_auto_clean()`，不会出现「定时清的和
  手动清的不是一回事」。
- **两条路都装上了**：`ProactiveEngine`（桌面常驻）与 `CliReminderHost`（REPL）。
  新增 `daemon/auto_clean_schedule.py` 作为共用宿主——挂 `Scheduler` 而不是
  `ProactiveEngine`，因为后者只有桌面路径有。
- **两个阈值**：`auto_clean_min_mb`（默认 20）以下不建批次，零感知垃圾常只有几 MB，
  天天建隔离区目录再删一遍成本高于收益；`auto_clean_notify_mb`（默认 200）以下**完全静默**，
  例行公事不该消耗注意力。
- **自动清掉的东西必须能还原**：走的就是 `cleaner.clean()`，隔离区、`protect` 名单、
  跨盘检测全部照旧。单测有一条专门钉它（`test_auto_cleaned_content_is_restorable`）——
  它失效的话「零感知清理」就变成「零感知删除」。
- 新命令 `/clean --auto-status`：逐条给出「为什么不在自动名单里」，不让用户去读 TOML。
- 单测 119 → 137 例（新增 `tests/core/test_auto_clean.py` 18 例）；全量
  **4074 passed, 1 skipped**。

### 修复：Linux CI 上 13 条测试整片失败

开发机（Windows）全绿，推到 GitHub 后 `ci.yml` 的 ubuntu 矩阵报 `13 failed,
4024 passed, 38 skipped`。三类报错、一个根子：**这套能力是 Windows 专属，
测试却没做平台门禁**。详见 [修复复盘](docs/fixlogs/maintenance-tests-linux-ci-fix.md)。

- **9 条规则库用例**：内置 19 条里 15 条 `paths` 型，路径全是 `%LOCALAPPDATA%` /
  `%SYSTEMROOT%` / `%SYSTEMDRIVE%`。POSIX 上这些环境变量不存在，`expand_path`
  按设计保留占位符、后续校验判为非绝对路径而拒绝——**这是正确的 fail-closed
  行为，不是缺陷**。所以给这类用例打 `_WINDOWS_ONLY` 门禁，而不是把期望值换成
  `/tmp` 去迁就平台（那样绿了也不再验证真正会出事的场景）。
- **3 条扫描参数用例**：`defender.start_scan()` 原来先查 `powershell_path()`
  再校参数，于是 Linux 上 `kind='turbo'` 只得到「没有 PowerShell」，把调用方的
  参数错误藏了起来。改为**参数校验在前、平台检查在后**——这也不是只为测试：
  Windows 上 PowerShell 被策略禁用时，同样会把所有参数错误退化成同一个环境错误。
- **1 条预览用例**（`KeyError: 'supported'`）是前两类的下游效应，随之消失。
- 修顺序必须配一条**跨平台**用例，否则下次谁把顺序改回去，Windows 本地照样全绿：
  新增 `test_param_validation_precedes_platform_check` 与
  `test_platform_check_still_blocks_valid_request`，把 `powershell_path` 打成
  `None` 来重放「没有 PowerShell」场景，不限平台。
- 单测 137 → 139 例；全量 **4076 passed, 1 skipped**（Windows 上 0 新增 skip）。

---

## [2.3.1] — 2026-10-30

本次只有一件事：**改错的文件终于能撤了**。

### 新增能力

- **文件写前自动留底，`/undo` 按文件恢复**
  此前 `FileWrite` / `FileEdit` 覆盖即丢原文（代码注释里写着"依赖外部 git"，
  而实际使用中工作目录经常根本不是 git 仓库）。现在所有破坏性写在执行前把原文件
  复制一份到 `~/.jarvis/file_snapshots`，改错了一条命令还原文。

  与之前的关键差别是**粒度**：以前本地写只能引用本轮 shadow git 检查点，撤销就等于
  “退回整轮”，改错一个文件会连带撤掉同一轮里所有正确的改动；终端宿主不打检查点时
  更是**一条都不记**。现在只动这次碰过的那几个文件。

  留底是复制而非硬链接，因此有两道体积闸：单文件 50MB、单次 64MB（目录超限就不留底，
  日志里明说，不假装能撤）。配额与 bash 沙箱的目录兜底**分开计**（默认各 50 / 20 条）。

### 修复

- **相对路径曾按进程工作目录解析**：模型传 `note.txt` 这类相对路径时，留底会落到
  jarvis 进程的 cwd 上，把一个**确实存在**的文件记成“不存在”——于是“恢复”变成删除
  用户文件。已改为按工具的工作目录解析。
- `/undo list` 不再把两种代价完全不同的操作笼统写成“可撤销”，改为分别标注
  `可按文件恢复` / `可整轮回滚` / `可回退配置`。

### 新配置

```toml
[actions]
file_snapshots = 50      # 文件留底保留条数；0 = 关闭（与 checkpoint 无关）
file_snapshot_mb = 64    # 单次留底体积上限（MB）
```

### 能力边界（如实说明）

`Bash` 里的 `rm` 与脚本改文件**不做**文件级留底 —— `BashTool` 只拿到一个命令字符串，
可靠地从中提取删除目标需要解析 shell 语法，而 `rm -rf $(...)`、管道、脚本调用都能
绕开静态分析。做一套“看着能撤、实际经常漏”的机制比明确说清危险。这类改动仍走
`/undo`（整轮检查点）或 `/rewind`。

### 工程

- 新增 `tests/core/test_file_effects.py` 30 例；全量 **3919 passed**。
- `orchestrator.py` 回到 800 行内（无状态判定抽到 `core/actions/effect_policy.py`）。
- 复盘：`docs/fixlogs/file-change-not-recoverable-fix.md`。

---

## [2.3.0] — 2026-10-30

本次跨三个阶段交付（阶段 10 W3 兑现层、阶段 11 感知层扩充、阶段 12 自配置层），
并修复了三个影响日常使用的关键缺陷。**桌面端用户强烈建议升级** —— 2.2.5 及更早
版本在桌面壳里每轮对话都会固定卡顿约 10 秒。

### 新增功能

- **自配置层（阶段 12）**：jarvis 开始自己管配置。22 项配置真源表、三级生效分级
  （立即 / 重装配 / 重启），`/config set` 终端入口与桌面设置面板走**同一条写入路径**，
  不再出现"面板改了但终端没变"的口径分裂。
- **邮箱工具**：IMAP 只读读取、检索与回复。含主机名推导与网易邮箱 ID 握手兼容，
  默认只读权限，发件需显式授权。
- **任务兑现层（阶段 10 W3）**：副作用账本 + 任务脊柱 + 建目标三入口。提醒改走
  打扰预算，支持静默降级投递（邮件/屏幕），受阻审批补齐终端出口。
- **感知层扩充（阶段 11）**：日历与系统资源两个休眠件收编进感知总线，
  阈值直接复用既有的 `monitor_*` 配置组。
- **模棱两可先问清楚**：提示词层面强化"不确定就先确认"。语音场景下用嘴问，
  而不是默默调一堆工具试错。

### 关键修复

- **共享连接池被临时 provider 关闭，导致「重启后第一句正常、之后每句必挂」**
  `provider.close()` 不再触碰进程级共享 httpx 连接池，另立
  `close_shared_http_client()` 由进程退出时统一释放；画像提炼与 `/init`
  连通性测试改用**独占连接池**。此前每 10 分钟一次的画像提炼会静默毒死主对话。
- **桌面壳每轮对话固定卡顿 10 秒**（本次体感差距的元凶）
  轮前同步 `git add -A` 打工作区快照，在 2.4 万文件的工作区上必然吃满 10 秒超时后
  静默放弃。改为线程池异步 + 0.4 秒宽限，结果在轮末取回 —— **用户感知延迟从
  10 秒降到 0.42 秒，撤回文件的能力一点没少**，且不再随工作区大小恶化。终端
  本来就没有这一步，所以此前只有桌面端中招。
- **bash 沙箱超时最长卡死 240 秒**
  超时不再被误判为可重试（重试只是再等一遍，且副作用会重复执行）；沙箱路径补齐
  中断竞速，Ctrl+C 立即生效并回收整个进程树。
- **错误分类收紧**：429 与请求超时不再混为一谈，工具可声明自身不可重试。
- **模型配置改完即生效**：修掉"提示已更新、下句话仍打旧端点"的问题，
  `/init` 子命令也终于能被正确路由。
- **语音**：API Key 统一解析（不再分场景各找各的）、唤醒词同音容错、
  流式 TTS 生命周期修复（长回答不再中途断掉或录到自己的尾音）。

### 可观测性

- **流式请求分段计时**：记录「首包思考 / 首字正文 / 全程」三段耗时与思考、正文
  字数。排查"为什么慢"时，一次请求就能分清是网络、服务端排队还是思考阶段。
- **桌面壳本地前处理计时**：检查点与等锁耗时分开记录，终端与桌面端的延迟差距
  从此可量化。
- **错误链提取**：顺着 `__cause__` 挖出真实原因。此前所有连接故障都显示成
  一句毫无信息量的 `Connection error`。

### 工程

- 引擎按职责拆成 mixin，单文件回到 900 行以内。
- 修复 CI 上 4 个失败：Python 3.14 时间解析变化、一处假绿测试、依赖缺失未跳过。
- 新增大量回归测试，全量 **3888 个用例通过**。

### 升级提示

```bash
# PyPI
pip install "jarvis-agent[all]" --upgrade

# npm
npm install -g @acefelix/jarvis
```

桌面端请下载新版安装包（`JARVIS-Desktop-Setup-1.1.0.exe`），内置了本次修复后的后端。

---

## [2.2.5] — 2026-10

- 阶段 9 W2 感知层：统一事件总线 + 窗口/目录/仓库/来信/在场五类事件源，
  逐源默认关、首次启动询问、桌面「感知源」面板。
- 阶段 12 P1 自配置层预演。

更早的版本记录见 GitHub Releases。
