# 能力自检与自我修复 · 升级计划（`/health caps` + `agent/capabilities/`）

> 状态：**待立项批准**（本文档只做设计定稿，未动代码）
> 起因：2026-11-03 那次"写个简易贪吃蛇"的实机复盘——一个任务花了 ≈40 次工具调用、24 段思考，
> 根子是 jarvis 不知道自己的 `BrowserNavigate` 其实跑不了（playwright 包在、浏览器内核
> revision `1223` 不在），也不知道当前模型看不见截图。两头都不知道，于是用"假设"填空，
> 还向先生汇报了"刚才我验证过"。过程取证来自本机会话导出与 `~/.jarvis/MEMORY.md`
> 里 2026-11-03 那条「火狐启动误判」（先生要求记住后自行查证写的），仓库内的正式复盘待 S6 补写。
> 先生已明确授权：**自我修复的权限可以放大，前提是不对系统造成损失**——所以第 4 节是整个计划的重心。

---

## 0. 成功标准（三条，全部可测）

1. **工具列表不撒谎**：任何出现在 ToolSearch 结果里的工具，其前置条件在**当时**是可用的；
   不可用的能力要么不出现，要么带一句"缺 X → 补法 Y"，且该次调用返回 `retryable=False`。
2. **"我看过了"必须有证据**：交付自查里凡写"亲眼看过/已验证"，都能追溯到一条真实观察
   记录（截图 + 当时模型确有视觉能力，或文本级证据）；证据缺失时必须输出"未实际验证"。
3. **修完会自己复测**：修复动作执行后必须重跑同一条探测；仍失败就停手并如实报告残留，
   不许换路子硬试（对应本次实机"同一失败撞两遍、中间隔了 800 行"）。

---

## 1. 现状：四块地基，全都停在"能 import"这一层

| 现有机制 | 位置 | 已能证明 | 证明不了 |
|---|---|---|---|
| extras 声明 | `pyproject.toml`（`gui/browser/mcp/camera/vision/voice/daemon/wechat/dev/all` 共 10 组） | 哪个能力要哪些包，注释里连"Linux 需 portaudio19-dev"都写了 | 运行时无人读取，也没和工具挂钩 |
| 注册期静默降级 | `agent/core/tool.py` `_register_gui_tools`(L503) / `_register_browser_tools`(L544) | 包 import 失败 → 工具不进列表 | **包在、内核缺 → 工具照样注册**（本次实机正是这种） |
| 点状缺件文案 | `openai_provider.py`、`anthropic_provider.py`、`zai_provider.py`、`browser.py:120`、`monitor.py`、`serve/app.py` | 撞上时会说"pip install xxx" | 只有失败那刻才知道，口径各写各的 |
| 安装渠道识别 | `agent/update.py` `detect_installer()`(L214) / `upgrade_command()`(L230) / `_is_editable_install()`(L195) | 环境形态本来就可识别，且已在生产里跑 | 目前只服务"升级提示"一个消费者 |

顺带一条现成的教训：`browser.py:120` 那句 `"playwright 未安装，请运行: pip install playwright && playwright install chromium"`
**文案本身是对的**，但它只在 `import` 失败时抛；本次 playwright 包 import 正常、缺的是内核，
报错来自 `launch()` 内部的「Executable doesn't exist」，这条正确文案一次都没露过面。

---

## 2. `import 成功 ≠ 能力可用`：六类真实失配

| # | 类别 | 实例 | 特点 |
|---|---|---|---|
| 1 | 数据资产缺失 | playwright 浏览器内核（`chromium_headless_shell-1223`）、mediapipe / paddleocr 模型权重 | 包 import 正常，一调用就炸 |
| 2 | 外部二进制 | `git`（`checkpoint.py` 已按"不可用就安全降级"处理）、`ffmpeg`、`tesseract.exe`、portaudio | 不在 pip 管辖内，探测方式各异 |
| 3 | **版本耦合** | playwright 升级 → 内核 revision 号变 → 已装内核立刻失配 | 能力会"昨天还好、今天就废"，现在没有任何机制能感知 |
| 4 | 密钥/凭据后端 | keyring 服务不可用时解析链一路回退到主 LLM key | 静默生效，出错时看起来"配过了" |
| 5 | 平台差异 | `pygetwindow` 在 Linux 支持有限；两路 PyAudio 并发在 Windows 会 segfault（`settings.toml [voice]` 注释即为此） | 需要按平台标注，不能一律"可用" |
| 6 | 模型侧能力 | 当前模型收不收得到图片（详见第 5 节） | 与本机依赖无关，但同样决定"能不能自查" |

---

## 3. 设计：三层结构 + 一份表驱动清单

### 3.1 L1 声明——`agent/capabilities/registry.py`（新建）

形态沿用仓库已验证的"表驱动 + 能力元数据"（`agent/llm/provider_registry.py` 13 项注册），
和语音解耦计划里的 `VOICE_REGISTRY` 保持同一种形状，**不要三条线各发明一套**。

```python
CAPABILITY_REGISTRY: dict[str, CapabilityMeta]     # key = 能力名，如 "browser.selfcheck"

CapabilityMeta:
    name                    # browser.selfcheck / voice.duplex / gui.input / ocr / mcp / ...
    extras_group            # 对应 pyproject 的 extras 名，用于生成修复命令（可空）
    python_reqs             # [(distribution, version_spec)]，用 sys.executable 查
    binaries                # [(可执行名, 探测命令, 期望特征)]，如 ("git", ["--version"], ...)
    assets                  # 数据资产：(标识, 探测方式) —— 内核 revision 必须问库自己
    secrets                 # 需要哪些 keyring / 环境变量项
    platforms               # 支持平台；不适用时给原因文案
    smoke_probe             # 秒级端到端冒烟（可选），如真的起一次 headless
    repair_actions          # 允许的自我修复动作 ID 列表（见第 4 节白名单）
    degradable              # 不可用时能降级到什么（含"降级为文本级验证"）
    blocks_selfcheck        # 该能力缺失是否会让「交付自查」不成立（true 时要进 prompt）
```

查找顺序与语音表同口径：命中 → 返回状态；不命中 → **显式 `unknown`，禁止静默当作可用**。

### 3.2 L2 探测——`agent/capabilities/probe.py`（新建）

| 档 | 内容 | 成本 | 何时跑 |
|---|---|---|---|
| 静态 | `find_spec`、`shutil.which`、读 revision、keyring 可读性 | 毫秒级 | 启动时全量跑一遍，结果进缓存 |
| 冒烟 | 起一次 headless 打开 `about:blank`、录 0.1s 音频、跑一次 `git --version` | 秒级 | 首次要用该能力时，或 `/health caps --deep` |

**三条硬约束（都是本次实机学到的，写进实现纪律）**

1. **只认 `sys.executable`**。本次 revision `1223` 绑的是 `jarvis/.venv` 里那份 playwright
   `1.60.0`；用父 PATH 上的另一个 python 查，结论就是错的。任何自检都不许借用别的解释器。
2. **revision 号必须问库自己**（`playwright install --dry-run` 或包内 `browsers.json`），
   绝不硬编码——否则库一升级，检查逻辑立刻变成假报警。
3. **"探针挂了" ≠ "东西没有"**，两者必须分开报。本次最贵的教训：沙箱里 `tasklist` 返回空输出，
   jarvis 把它当"进程被回收"的证据并编出整套机制讲给先生听。**空输出不得单独支撑否定结论**，
   必须换第二种探针交叉验证后才能下判断——这条要落成框架级断言，不靠模型自觉。

### 3.3 L3 决策——同一份结果喂三个消费者

- **工具注册 / ToolSearch**：不可用能力不伪装成可用；缺失时把"缺 X → 补法 Y"写进工具描述或
  返回文案，让模型不必去撞（本次 `BrowserNavigate` 被自动重试两轮 ×2 次调用，纯浪费）。
- **失败分级**：新增能力状态轴，**不复用 `ErrorCategory`**（现有 8 个值全是 LLM 协议错误：
  `auth/rate_limit/model_not_found/context_too_long/network/server_error/bad_request/unknown`，
  把"本机缺件"塞进去只会让两件事互相污染）。做法是 `ToolResult` 侧统一
  `retryable=False` + 结构化 `capability` 字段，`query_loop` 现有重试判据（只认 `NETWORK`）天然不受影响。
- **启动横幅 / `/health caps` / system prompt**：一份"能力 × 状态 × 证据 × 修复命令"矩阵；
  prompt 里给精简版"你今天的实际能力清单"。这一条直接消掉"纪律要求亲眼看过、其实看不见"的自相矛盾。

入口选择：**扩 `/health` 而不是新增 `/doctor`**（`core/maintenance/health.py` 已是体检入口，
`slash_completer.py` L91 已有一组 `/health` 子命令）。理由就是 `agent/core/daemon/monitor.py`
顶部注释里那句既有判断：两套并存只会让人搞不清该信哪个（当年 `check_high_cpu_processes()`
与 `/health` 的进程区块并存，最后删的是前者）。

---

## 4. 自我修复：把"权限大点"翻译成"有预算、可回滚、能复测"

先生授权放大权限，所以这里不是"要不要动手"，而是**动完之后怎么保证系统没受损失**。
维护层已有的三段式口径直接继承：`/disk → /clean <id> 预览 → --yes 执行`，隔离区 7 天可还原
（`settings.py` L442 注释、`cleaner.py restore()`）。自我修复沿用同一形状：这是维护层头一次
会「改环境」的动作，需要一个自己的闸。

### 4.1 动作白名单（超出白名单一律只给命令、不代执行）

| ID | 动作 | 实际执行什么 | 落点 | 体积/耗时预算 | 回滚方式 |
|---|---|---|---|---|---|
| `fetch-browser` | 下载 playwright 内核 | `sys.executable -m playwright install chromium [--only-shell]` | 只写 `%LOCALAPPDATA%\ms-playwright\` | 无头壳 112.4 MB / 完整内核 181.9 MB（2026-11-03 HEAD 实测）| 删该目录下本次新增的 revision 目录 |
| `fetch-model` | 下载 OCR / 检测模型权重 | 按能力表里的 URL 清单下到本地 | 只写 `~/.jarvis/assets/` | ≤500MB（估算，未实测） | 同上，按目录整删 |
| `pip-extras` | 往**当前 `sys.executable` 所在环境**装 extras 里的包 | 按 `detect_installer()` 渠道生成 `pip/uv install jarvis-agent[<extras_group>]` | 该 venv | 逐包确认 | 先 `checkpoint.create(kind="repair")`，失败 `pip uninstall` + 快照核对 |
| `config-fix` | 改配置项（如 `model_type` 标错） | 走既有 `ConfigSet` | `~/.jarvis/settings.toml` / `models.toml` | 即时 | 复用 `undo_spec {kind:"config", before:…}` |

`pip-extras` 的包名来源**只能是 `pyproject.toml` 的 extras 白名单**，不接受自由包名；
命令由程序按 `detect_installer()` 的渠道结果生成（uv tool / pip / editable 各不同），不是拼字符串。

表里的「体积/耗时预算」是**硬上限**（超了就不自动执行、转人工）；与 4.3 第 2 条的 **ASK 线**
是两档：预算内自己做，超 ASK 线先问，超硬上限只给命令。两档各管一件事：**体积决定要不要问，
耗时决定前台还是后台**（见 4.3 第 2、3 条）。

### 4.2 禁止清单（"不对系统造成损失"的具体化）

- **不自动 `pip install -U jarvis-agent`**：editable 环境下这会用 PyPI 正式版**直接覆盖源码树里
  未发布的修改**（今天刚踩，`update.py` L293-298 已把这条写进注释）。
- 不改系统 `PATH`、不写注册表、不装系统级依赖（apt / brew / `choco` 一律只输出建议命令）。
- 不碰 `.ssh` / `.aws` / `.gnupg` 等敏感目录（沙箱既有硬保护沿用，不为其开例外）。
- 不做任何删除用户数据的动作；修复只新增，回收交给 `/clean` 那条已审计过的链路。
- `/away` 无人值守时**不执行任何有代价的修复**：沿用 `SANDBOX_AUTO_ALLOW_PREFIX` + W1 闸门口径
  （`modes.py` 据此把自动放行升回待批），不新造一套判断。

### 4.3 执行纪律

1. **开关默认开**：`repair_enabled` 默认 **`True`**（先生 2026-11-03 定）。既然预算门与审计成了
   唯一防线，就得同时给三个东西：单个动作可关（`repair_disabled_actions = ["pip-extras"]` 这种写法）、
   首次自动修复后**主动汇报**（“我刚为了 X 装了 Y，共 Z MB，可用 /undo 退”）、以及一个
   修复历史查看入口（`/health caps --history`，直接读 `actions.jsonl`）。
2. **预算门（只管体积）**：下载量 >200MB 仍要问——这条线有实据：无头壳 112.4MB 在下边（自动做），
   完整 chromium 181.9MB 加无头壳合计约 296MB 在上边（先问），刚好卡在“日常自查够用”与
   “要看真实窗口”之间，不是拍脑袋定的数。
3. **耗时走后台而不是走询问**：预计 >30s 的动作一律转后台任务、不阻塞对话，完成后回报——
   沿用 `/health scan`（“启动 Defender 扫描（后台执行，不阻塞对话）”）与 `core/tasks/spawn.py`
   的既有口径。原计划的“>60s 也 ASK”已改：网慢就弹询问只会让人多点一次回车，不解决任何问题。
4. **动手前留底**：`checkpoint.create(reason, kind="repair")`（配额与 `/rewind` 分开计，沿用
   `spawn.py` L105 那条既有口径），并写一条 `actions.jsonl`（`reversible` + `undo_spec` + `state`）。
   ⚠ **S3 实做时发现本条原写法有一处错**（2026-11-03 读到 `checkpoint.py` 实现后确认）：
   `CheckpointManager` 快照的是**工作区**（shadow git of `workdir`），而四个修复动作写的都是
   工作区外的东西（`%LOCALAPPDATA%\ms-playwright\`、`~/.jarvis/assets/`、venv 的 `site-packages`、
   `~/.jarvis/*.toml`）——检查点**盖不到它们**。所以“`/undo` 天然覆盖修复动作”这句是错的。
   S4 改为：每条修复记录 `reversible=False` + `UNDO_NONE`，并在 `undo_spec.reason` 里写清
   **精确的手工回滚法**（新增目录的绝对路径 / 对应的 `pip uninstall` 命令）；不得把
   “我打了个检查点”当成“它能退回”。另外回收仍走 `/clean` 那条已审计链路，不自建删除。
5. **审计**：每条修复命令都过 `SandboxAuditor.log_execution()`，事后可回答"那天到底改了什么"。
6. **修完必复测**：重跑同一探测 → 通过则销掉标记；仍失败即停手，报告残留与下一步人工命令。
   **禁止**在同一次失败上连续换路子（本次 Chrome/Firefox 那 8 个 Bash 就是反面样本）。
7. **运行中自愈**：你手动装完内核后，正在跑的实例下次调用该能力时重探测并恢复可用，
   不能"没重启就永远不可用"。探测结果缓存带过期时间。

---

## 5. 打通能力面的另一端：模型能不能"看见"

这是这套机制在模型侧的镜像，但它是**独立的一半**——内核装好了，看不见图照样白截。

### 5.1 问题规模（订正：不是两处，是 14 个文件 20 处）

原计划只点了两处默认值，实跑差集后是这样：`bootstrap.py`（自定义缺失 + 内置各一处）、
`models_config.py` 写盘默认、`provider_registry.py` 厂商级字段、四个 provider 构造函数默认、
`model_manager.py` 四处（含一处硬写 `"multimodal"`）、`query_loop.py` 故障转移、
`model_admin.py` 表单预填与枚举、`slash_completer.py`、`model_commands.py` 三处列表前缀、
`config_commands.py` 表格标签——全部把「没人填」当成「有视觉」。

于是两种错同时存在：纯文本模型收到图片块（不少端点直 400），或者图片被 provider
静默抽掉而工具回执写着「图片已回传: 是」。`text_blocks.py` 里还写着两句无条件的
「你能真正看到屏幕内容 / 你能真正看到网页」，那是同一个错在提示词里的版本。

### 5.2 已实施口径（S5）

1. **单一裁决点** `agent/llm/model_capability.py`：`MODEL_TYPE_TEXT / MULTIMODAL / UNKNOWN` +
   `normalize_model_type`（认不出来一律 unknown，不给别名开口子）+ `model_has_vision`
   （**只有明确 multimodal 算有**）+ `resolve_model_type` + `vision_tag` / `describe_model_type` /
   `vision_form_default` + `IMAGE_OMITTED_NOTE`。20 处散落默认值全部收进来。
2. **三值而不是两值**，保守方向沿用仓库自己定过的决定
   （`docs/roadmap/PLAN-self-config.md`「P4 实施记录 ② model_type 保守给 text，不猜多模态」）：
   拿不准按看不见处理，同时把改的办法写明。
3. **一处刻意的例外**：内置发货清单（`configs/models.toml` 的 `[llm.models]`）在没有覆盖
   配置时取 `BUILTIN_MODEL_TYPE = multimodal`，不是 unknown。qwen3.x 走 DashScope 多模态
   端点，改成 `Generation.call()` 会「400 url error」——把发货就配好的模型集体打成看不见，
   是用新错误修旧错误。有覆盖配置（`/models` 改内置模型写的就是 `custom_models[name]`）
   时仍以用户声明为准。
4. **provider 层四家同口径**：`skip_images = not model_has_vision(...)`；`anthropic_provider` 补齐
   这一分支（以前它根本没有 `model_type` 参数，注册表也没给它下发），同时补 `set_model_type`
   与其他三家对齐（以前 `hasattr` 探测会静默跳过它）；`provider_registry.ProviderMeta.model_type`
   是死字段且正是错源——直接删除，视觉是「厂商 × 具体模型」两维决定的，厂商级默认值
   一定会被当成事实。
5. **省略必须留文字，不许静默**：四家统一用 `IMAGE_OMITTED_NOTE`。顺带抓出一处真缺陷：
   openai 那支原先要求「有文字块可挂」才写省略说明，于是用户单发一张图时整条 turn
   直接消失（既存用例把这个行为钉成预期，已按新决定改用例并写明理由）。dashscope 的
   Generation 支以前连文字都不留。
6. **回执与提示词都不再越权**：工具措辞唯一出处 `agent/core/images.image_receipt()`
   （「图片附上: 是…附上不等于看见」），三处工具（`screen.py` / `camera.py` / `browser.py`）改齐；
   `prompts/system.py` 新增 `_vision_section()`（无视觉时给降级链 + 改法，有视觉时**零字节**）；
   `text_blocks.py` 交付自查纪律补第 5 条「自查手段按能力降级」，并改掉那两句无条件断言。
7. **热切换要跟上**：`_vision_flipped()` 判据 + 只在图片通路翻转时重拼 system prompt
   （REPL 与工作台两条路径共用判据）；新增 `compose_system_prompt()` 收拢
   「拼提示词 + 补 `system_prompt_append`」这两步（存量 4 处调用点待 S6 迁）。

### 5.3 明确没做的一条（以及为什么）

原计划里「能力表给每个 (vendor, model) 记 `vision: yes|no|unknown`」这一条**没做**：
没有任何一手依据可以填这张表，凭印象写「deepseek-flash 无视觉」与原来的「默认当有」是同
一种错，只是换了个方向。现在的机制是：声明过就尊重声明，没声明就保守降级并把改法写明。

实机核对：本机 7 个自定义模型全部显式声明过 `model_type`，所以本次改动**没有让任何
真实配置静默失去视觉**。同时它也指认了事故机制——`deepseek-flash` 声明的是
`multimodal`，而声明本身对不对，代码无从核实。可做的下一步是「端点报错驱动降级」：
当 API 明确拒收图片块时把该模型降下来并告知用户，这比预先猜一张表可靠。


---

## 6. 改动点清单

| 文件 | 改动 |
|---|---|
| `agent/capabilities/registry.py`（新建） | `CapabilityMeta` + `CAPABILITY_REGISTRY`，首批覆盖 browser / gui / voice / camera / vision / mcp / lsp / mail / checkpoint.git / model.vision |
| `agent/capabilities/probe.py`（新建） | 静态与冒烟两级探测、缓存与过期、`sys.executable` 唯一锚点、"探针失败 ≠ 能力缺失"分离 |
| `agent/capabilities/repair.py`（新建） | 白名单动作实现、预算门、留底 + 审计 + 复测；**每个文件 <900 行**（仓库单文件上限） |
| `agent/core/tool.py` | 注册期接 L3：不可用能力带原因注册或跳过；GUI / browser 两处 `except ImportError: return` 改为查表后决定 |
| `agent/core/tool.py`（`Tool` 基类） | 新增可选 `prerequisites() -> list[str]`（返回能力名），供 ToolSearch 与调用前检查共用 |
| `agent/llm/model_capability.py`（新建） | 模型视觉能力的唯一裁决点：三值枚举 + normalize/resolve/标签/表单默认值 + 省略文案 |
| `agent/llm/provider_registry.py` | 删除厂商级 `model_type` 死字段（默认 multimodal 即「拿不准算有」的源头之一）；给 anthropic 下发 model_type |
| `agent/config/models_config.py` | 写盘默认改走 `normalize_model_type`，未填落 unknown 不落 multimodal |
| `agent/llm/anthropic_provider.py` | 补 `skip_images` 分支 + `model_type` 参数 + `set_model_type`/`has_vision`，与另三家同口径 |
| `agent/tools/system/screen.py`、`vision/camera.py`、`web/browser.py` | 回执措辞统一来自 `core/images.image_receipt()`（不再自称"已回传: 是"） |
| `agent/prompts/system.py` | 新增 `_vision_section()`：无视觉时给降级链与改法，有视觉时不加一个字节；新增 `compose_system_prompt()` |
| `agent/prompts/text_blocks.py` | 交付自查纪律补「自查手段按能力降级」；两处"你能真正看到"改为条件句 |
| `agent/core/maintenance/health.py` | 新增 `/health caps`（+ `--deep` 触发冒烟；`--repair` 走修复） |
| `agent/ui/slash_completer.py` | 补 `/health caps` / `--deep` / `--repair` 提示项 |
| `pyproject.toml` | extras 与能力表的对应关系成为事实来源（能力表从这里取包名清单） |

---

## 7. 测试与验收（不靠"本地绿"）

1. **契约测**（照 `tests/test_release_contract.py` 那个"目录必须覆盖每个 `Settings` 字段"的精神）：
   每个可选工具必须在 `CAPABILITY_REGISTRY` 里有归属条目，漏声明直接红——否则表三个月就烂。
2. **故障注入**：把已装的 `chromium_headless_shell-1223` 目录临时改名，断言
   ① 静态探测报"缺 revision 1223（证据：路径不存在）"而不是"未知错误"；
   ② ToolSearch 结果带缺失说明；③ 调用返回 `retryable=False` 且不自动重试；
   ④ `/health caps` 给出可复制的正确修复命令；⑤ 目录改回后**不重启**即恢复。
3. **探针分离用例**：模拟探针自身失败（无权限 / 命令不存在 / 超时），断言输出
   `probe_failed` 而**不是** `missing`——这是本次 `tasklist` 骗局的回归护栏。
4. **修复动作可逆性**：对 4 个白名单动作各做一次 dry-run + 真执行（CI 里只做 dry-run 与
   沙箱内路径断言），验证 `checkpoint(kind="repair")` 落盘、`actions.jsonl` 条目完整、`/undo` 能退。
5. **预算门与后台化**：>200MB 在 yolo 下仍必须 ASK；≤200MB 但预计 >30s 必须转后台任务
   而非前台阻塞；`/away` 时任何修复都升回待批。
6. **模型能力**：`model_type=unknown` 时 prompt 走降级链；纯文本模型下断言截图结论
   不得写成"已验证"（用例可直接检查渲染后的 system prompt 片段）。
7. **变异矩阵**（交付纪律要求）：至少覆盖"探测缓存永不过期""缺件被标 retryable"
   "修复跳过复测直接宣布成功""回执仍写已回传: 是""预算门读反"五个变异，逐个确认被抓。

---

## 8. 明确不做（本阶段）

- 系统级依赖安装（apt / brew / choco / MSI）——只输出建议命令；
- 自动升级 jarvis 本体，尤其禁止对 editable 环境执行 `pip install -U`；
- 为自由包名安装（只认 extras 白名单）；
- 任何删除类修复；
- 桌面端（`jarvis-desktop`）侧协议改动：能力矩阵以桌面契约的既有形态透出，不新增命令面。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 探测假报警（版本漂移） | revision 号一律问库自己；探测结果带证据与时间戳，`/health caps` 可复核 |
| 启动变慢 | 启动只跑静态档（预算 <50ms），冒烟一律延后 |
| 修复把环境改得更坏 | 留底 + 白名单 + 复测收敛 + 失败即停；禁止清单守住底线 |
| pip 覆盖 editable 源码树 | 禁止清单第 1 条 + 只装进 `sys.executable` 所在环境 + 命令按渠道生成 |
| 能力表与实际不一致 | 第 7 节第 1 条契约测把一致性变成 CI 门禁 |
| 模型"看不见"仍报已验证 | 第 5 节：`unknown` 即按 `no` 降级 + 回执措辞统一 + prompt 禁词 |
| 权限放大后被静默滥用 | `repair_enabled` 默认开 ⇒ 预算门与审计从加分项变为主防线：首次修复必主动汇报 +
   全程 `actions.jsonl` 账本 + `/undo` 可达 + 单动作可关 |
| 下载量估算与实际不符 | 体积阈值不写进代码常量，走配置；能力表里记录实测值（2026-11-03 已测 playwright 两个包） |

---

## 10. 交付顺序与进度

```
S0 现状盘点 ──► 你批准 ──► S1 能力表+静态探测 ──► S2 三消费者接线 ──► S3 /health caps ──► S4 白名单修复 ──► S5 模型能力面 ──► S6 文档同步与变异矩阵
    ✅ 完成       ✅ 完成         ✅ 完成               ✅ 完成                 ✅ 完成              ✅ 完成             ✅ 完成            ✅ 完成
```

- [x] **S0** 现状盘点（本文档第 1–2 节，依据 2026-11-03 实机复盘，全部锚点到具体文件行号）
- [x] **关卡**：你已拍板（第 11 节三条全部落定，含 `repair_enabled` 默认开与急停开关）
- [x] **S1** `agent/capabilities/` registry + probe（静态档先行，**不含任何修复动作**）
  → 13 项能力、54 例；实机结论当场复现了本次事故（`browser.selfcheck` = missing）
- [x] **S2** 三消费者接线：ToolSearch 标注缺失、调用前拦截、system prompt 能力清单
  → 额外堵了两个逃逸口：不可逆动作到点执行那条、实时语音自己拼调度那条；24 例
- [x] **S3** `/health caps` 与 `--deep`（只读，先让它能诚实报告）
  → 冒烟档两条探针（真起 headless / 真截一帧），32 例；实跑发现并修掉一处真 bug：
  `probe_failed` 的补法会混进「修复命令」清单（文本版与 Markdown 版各一处）
- [x] **S4** `repair.py` 四个白名单动作 + 预算门（体积档）+ 后台化（耗时档）+ 留底 + 复测；
  `repair_enabled` 默认开，所以上述四件不是可选而是开工前提（见 4.3 第 1 条）
  → 新增 41 例（收尾时 `--collect-only` 实测；本行先前写的 45 是把拆分之计数当成了最终例数，已订正）；
  新增 `[repair]` 配置域五字段（`settings.py` / `settings_domains.py` /
  `settings.example.toml` / `config_specs.py` / `config-docs/configuration.md` 四处同步）——
  **其中 `config_specs.py` 实只进了四个，漏了 `background_over_s`，由 S6 收尾时订正**（见下）；
  命令 `/repair on|off|status|disable|enable` 与 `/health caps --repair|--history`；
  命令体因 900 行门拆到 `agent/commands/handlers/repair_commands.py`（被仓库自带的行门抓住）
  → 偏差一处（已在 4.3 第 4 条订正）：修复记录**不进 `/undo`**，只写精确手工退法
- [x] **S5** 模型能力面（第 5 节）—— 单一裁决点 + 四家 provider 同口径 + 回执/提示词不越权
  → 新增 `agent/llm/model_capability.py` 与 `tests/llm/test_model_capability.py`（80 例），
  全量 **4435 passed / 1 skipped / 0 failed**；新文件 ruff 零命中，所有文件均在 900 行内
  → 偏差两处，已写回第 5 节：① 内置发货模型保留 multimodal（不是改 unknown）；
  ② (vendor, model) 的 vision 表**没做**，因为没一手依据可填
  → 顺带抓出并修掉两个既存缺陷：纯图片 turn 在 openai 那支会整条消失（含旧用例改写）；
  `tests/llm/test_tool_registry.py` 往 `lru_cache` 的共享 registry 里注册 harness 会污染后续用例
- [x] **S6** 文档同步、尾活收敛、变异矩阵（2026-11-05）
  → **文档**：新建 `docs/architecture/20-能力自检与自我修复.md`（十六节，含“明确没做”表与
  变异矩阵结果）；`00-索引.md` 登记第 20 篇 + 阅读建议 + 设计哲学第 10 条；`README.md` 与
  `README.en.md` 各四处（能力表行 / 快速开始 / 平台矩阵 / 文档地图，英文篇数此前已滞后一轮）；
  `docs/guide/commands.md` 补 `/health caps` 全族与 `/repair`（含 `--yes` 越不过 `/away` 这条实测结论）；
  `docs/guide/installation.md` 补「pip 装不上浏览器内核」这个最容易误判的坑；
  `docs/guide/development.md` 目录结构 + 新模块；`docs/blueprint/BLUEPRINT.md` 登记阶段 13 与
  §2.3 三行订正；`CHANGELOG.md` 新节（**写为 `[Unreleased]`，因为三处版本号仍是 2.3.5**，
  不在发布前谎报一个已存在的版本）；`docs/fixlogs/snake-visual-claim-fix.md` 本次事故七段式复盘
  → **对外数字订正**：配置可问总数 `202 → 207`、真源表 `58 → 63`、`/config list`
  `57 → 62`（29 auto + 33 confirm）、段数 `29 → 30`、未纳管 144 不变；全部取自闭包
  实测（`build_catalog()` / `list_specs()` / `Settings` 字段数），其中“可代改到底是 62 还是 63”
  靠实测才分清（63 含那 1 项 forbidden，不进列表）
  → **尾活（两个数字估计错了，以实测为准）**：拼提示词 + 补 append 的残留调用点不是 4 处而是
  **5 处**（`main.py` / `engine_session.py` / `project_switch.py` / `core_commands.py` /
  `session_commands.py`），已全部迁到 `compose_system_prompt()`（为此新增 `workdir` 覆盖参数，
  专为切项目的两阶段提交）；重复的 `_encode_image` 不是两份而是**三份逐字相同**（含
  `browser.py`，它的注释写的正是“独立内联避免跨模块耦合”——三份副本的自供状），已收拢为
  `core/images.py::encode_pil_image()`，顺带消除 core 层反向 import tools 层的分层违规与
  pyautogui 缺失被误报成「缺少 Pillow」；新增结构约束测防副本复活
  → **S6 又撞出一件缺陷**：`[repair]` 五个设置字段只进了真源表四个（漏 `background_over_s`）。
  后果不是“改不了”：`describe_missing()` 只认目录，会被报成「没有这个配置项」——正好是
  本仓反复强调的“存在但未纳管 ≠ 不存在”的反面。已补登记，并新增 `[repair]` 段的
  **反方向对账测**（旧测只查 spec → 真源，查不出漏登记）
  → 另修一处 REPL 既存缺陷：提示词在 `ToolSearch` 注册**之前**就拼完了（桌面端
  `engine_session.py` 注释里写过这个坑并修过，REPL 没同步），现已摆正顺序
  → **变异矩阵（第 7 节第 7 条）**：五个变异 **5/5 全部被抓**——“探测缓存永不过期”
  （抓到 TTL 过期重探断言）、“缺件被当可重试”（`assert tool.calls == []`）、“修复跳过复测
  直接宣布成功”（`assert outcome.state == "failed"`）、“回执仍写已回传: 是”
  （`assert "已回传" not in image_receipt(True)`）、“预算门读反”（`assert d.state == "auto"`）。
  注入方式为改生产代码→跑指定测→逐字节校验还原，脚本用完已删
  → 全量 **4451 passed / 1 skipped / 0 failed**（能力层 151 + 模型能力面 80 + 图片编码 13 +
  反向对账 1）；链接门 `scripts/check_md_links.py` 死链 0 / 未入库 0（172 个 md / 764 条判定）
  → 全量跑又抽出一件：`tests/test_command_router.py` 两条 `/think` 测在 patch
  `core_commands.build_system_prompt`，而我把该模块的入口改成了 `compose_system_prompt`，
  patch 不存在的属性名直接 `AttributeError`——只有全量能抽到它（`tests/serve` 跑不到）。
  已把两条的打桩目标改为 `compose_system_prompt` 并写明原因

**为什么顺序是"先能诚实报告，再给动手权限"**：S1–S3 全部只读，做完就能验证第 0 节的三条
成功标准里的前两条；S4 才引入写环境的能力。把授权推迟到能观测之后，是让"权限大点"这件事
本身也有据可查。

---

## 11. 拍板记录

**已定（先生 2026-11-03）**：

1. **四个白名单动作照单收下**（`fetch-browser` / `fetch-model` / `pip-extras` / `config-fix`），
   不新增“重跑 `jarvis --init` 某一步”这类配置修复——`config-fix` 走既有 `ConfigSet` 已够。
2. **`repair_enabled` 默认 `True`**：权限大 = 默认就动，但必须同时有三件东西：体积 ASK 线 200MB、
   单动作可关（`repair_disabled_actions`）、以及审计与留底。
3. **耗时档不走询问**：预计 >30s 一律转后台任务、完成后回报（“>60s 也 ASK”已作废）。
4. **首次自动修复强制播报**（文字 + 语音）：默认开之下，你至少要知道它动了什么、花了多少、怎么退。
5. **急停开关 `/repair on|off` 默认开**：一句说得出就能关掉全部代执行。
