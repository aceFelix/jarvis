# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

> 本日志已按版本系列分册：主文件只保留 **[Unreleased]** 与**当前 2.5.x 系列**；
> 某系列封版后整体移入 `changelog/`，主文件只留索引。

## 历史版本索引

| 系列 | 收录版本 | 位置 |
|---|---|---|
| 2.5.x | 2.5.0 – 2.5.4 | 本文件 |
| 2.4.x | 2.4.0 | [changelog/v2.4.md](changelog/v2.4.md) |
| 2.3.x | 2.3.0 – 2.3.5 | [changelog/v2.3.md](changelog/v2.3.md) |
| 2.2.x | 2.2.5 | [changelog/v2.2.md](changelog/v2.2.md) |

更早的版本记录见 GitHub Releases。

## [Unreleased]

### 文档（更新日志分册化，2026-11-14）

- `docs/log/CHANGELOG.md` 1420 行 → **604 行**：主文件只留 `[Unreleased]` 与当前 **2.5.x**，
  历史按 minor 系列分册：`changelog/v2.4.md`（167 行）/ `v2.3.md`（666 行）/ `v2.2.md`（13 行），
  单文件均低于 900 行门禁；主文件顶部新增「历史版本索引」。
- 册子内原日志的相对链接统一补一级（`../fixlogs/...` → `../../fixlogs/...`），
  头部回指主文件的链接保持 `../CHANGELOG.md`；markdown 链接门 0 死链。
- 切分由一次性脚本完成并自检：**14 个版本段逐行一致**（按 `## [x]` 分段对比）+ 版本段总数守恒；
  脚本用后即删，已发布内容一字未改。

### 修复（CI 环境：Linux / Python 3.14，2026-11-14）

- **5 条用例在 ubuntu CI 变红，本地 Windows 全绿**，均为测试环境假设，不涉及运行时改动：
  - `tests/tools/test_screenshot_image_ref.py` 4 条：用例的假 pyautogui 里真的 `from PIL import Image`，
    而 `pillow` 只声明在 `[gui]` / `[camera]`，CI 只装 `.[dev]` → `No module named 'PIL'` 被工具侧的
    `except ImportError` 兜成“pyautogui 未安装”。修法：`[dev]` 补 `pillow>=10.0.0`
    （刻意不装 `[gui]`：无头 Linux 的 pyautogui 还要 X11/scrot，不值得为测试引入完整 GUI 栈）。
  - `tests/permissions/test_yolo_scope.py::test_explicit_empty_hard_deny_disables_only_that_list` 1 条：
    目标取 `Path.home()/.ssh/id_rsa`，而保护根默认是 `c:\\`；POSIX 上 home 不在 C 盘，于是从 ASK 落成 ALLOW。
    修法：保护根显式传当前用户 home，让“两条清单互不顶替”这条不变量平台无关；默认 C 盘根由
    `test_guard_roots_default_is_c_drive` 单独钉住，覆盖不丢。
  - 复盘：新增 `docs/fixlogs/ci-linux-screenshot-and-yolo-fix.md`。

### 新增（图片驾驶舱 P0：终端终于能看见图，2026-11-11）

- **背景**：终端此前只有图片**入站**通道——`ScreenShot` / `CameraShot` / `BrowserScreenshot`
  把图编码成 base64 送给模型，人却看不见。模型说「我看了截图，按钮在右下角」时，用户无法
  核对它看的是不是同一张图；`operation-visibility` 计划里「失败截图未做」也卡在同一处。
- **数据面**：新增 `ImageRef`（`agent/core/images.py`）——只带路径与元信息、**不含字节**，
  与 provider 契约的 `ImageContent` 彻底分工：前者给人看（原图 + 屏幕适配），后者给模型看
  （≤1280 / JPEG85），两条尺寸链不混用。工具把引用放进 `ToolResult.trace_meta["image"]`
  （`tool_after` 的 payload 压根不含 images），`trace` 派生 `image_show` 事件时**用 `ImageRef`
  白名单重建**：直接 `**image` 展开会把上游误塞的 base64 一路落进 JSONL 与事件流。
- **展示面**：新增 `agent/ui/cockpit_image.py`（`ImageWindow` + `ImageManager`）与
  `agent/ui/image_cache.py`（缩放尺寸/缓存键/存活判定/布局纯函数）。480×600 独立窗，
  **默认屏幕左上角、整面可拖、位置记 `ui_state.json` 的 `image` 段**；大图按窗宽适配，
  缩略条点击与滚轮切换，元信息显示来源/尺寸/格式/体积/时间/路径；文件被清理时显示
  「图已过期」并保留元信息。
- **生命周期**：与抓取舱同构——有图才弹、结束保留、✕ 只隐藏、下轮自动重现、主 HUD 隐藏时
  同步隐藏、终端前台时让位置顶（`TerminalAwareTopmost` 新增 `peers`）。
- **落点**：`CameraShot` 复用 `~/.jarvis/cam`；`BrowserScreenshot` **新增落盘**
  `~/.jarvis/media/browser_*.png`（此前只在内存里编码，没有路径就没法展示）；
  `ScreenShot` 落点不变（系统临时目录）。
- **配置**：`[visualization]` 新增 `image_window`（auto/off）、`image_max`（1–24，默认 12）、
  `image_thumb_px`（48–160，默认 88），四处同步（settings / settings_domains / catalog / 模板）。
- **`ui_state.json` 改分段读-改-写**：原先 `save_position` 整文件覆盖、只写 `cockpit` 段；
  新增 `image` 段后两个窗口会互相冲掉对方位置。写入统一走 `_write_section`。
- **文案边界**：窗口标题写明「不改变模型的图片回执」，`image_receipt` 的「附上不等于看见」
  一字未改；工具回执里禁止出现「已为你显示 / 已确认」。
- **重构**：`cockpit_window.py` 已顶到 900 行门禁，先把 `_draw_feed` / `_draw_fetches` 与
  `_section` 的坐标收进 `cockpit_detail.py`（`draw_feed` / `draw_fetches` / `draw_section`），
  900 → 894 后再接线。
- **测试**：新增 `tests/ui/test_cockpit_image.py`（纯逻辑）、`tests/ui/test_image_state.py`
  （状态与生命周期，假窗口注入）、`tests/core/test_trace_images.py`（派生、脏值、无 base64）；
  `tests/ui/test_cli_cockpit.py` 的「全 off」用例同步补上图片驾驶舱，并新增「只开图片驾驶舱仍建根窗口」。
  全量 **4970 passed / 2 skipped**。
- **本地预览**：`python -m agent.ui.image_demo` 生成三张不同尺寸的测试图并只弹图片驾驶舱，
  不跑真实对话、不需要模型与 pyautogui，用来快速看形态（`agent/ui/image_demo.py`）。

### 修复（图片驾驶舱把三个截图工具打挂，2026-11-11）

- **现象**：真实会话里 `ScreenShot` 连试三次全挂，报
  `TypeError: ToolResult.ok() got an unexpected keyword argument 'trace_meta'`；
  同一路径的 `CameraShot` / `BrowserScreenshot` 一起挂（`Crawl` / `WebExtract` 不受影响，
  它们用 `ToolResult(data=..., trace_meta=...)` 直接构造）。
- **根因**：`ToolResult` 这个 dataclass **早就有** `trace_meta` 字段，但 `ok()` 工厂方法
  从来没透传它。图片驾驶舱那三处写成 `ToolResult.ok(..., trace_meta=...)`，于是每次调用都在
  构造结果时抛 `TypeError`——典型的「加字段忘了改 classmethod」。
- **修法**：`ok()` 增加 `trace_meta` 参数并透传（`error()` 不收该参数，守卫明确禁止绕过）。
- **为什么测试没拦住**：全仓此前**没有任何用例真的跑过 `call()`**（需要 pyautogui / 摄像头 /
  浏览器），图片驾驶舱的测试都停在 `trace_meta` dict 的派生逻辑上。已补两道门禁：
  - `tests/core/test_tool_result_contract.py`：AST 扫 `agent/` 下所有 `ToolResult.ok/error(...)`
    调用，把 keyword 名与签名（唯一真源）逐个比对，命中即报**文件:行号**。反向变异验证过：
    把 `trace_meta` 从签名里去掉，守卫立刻抓到 `screen.py:205` / `camera.py:213` /
    `browser.py:328` 三处；
  - `tests/tools/test_screenshot_image_ref.py`：假 pyautogui 真跑 `ScreenShotTool.call()`，
    断言 `trace_meta["image"]` 完整、尺寸取**原图**（不是回传模型的缩放值）、不含 base64、
    回执仍带「附上不等于看见」。

### 修复（多模态模型看不到工具截图，2026-11-12）

- **现象**：`deepseek-flash` 官方已是**多模态**（[Vision 文档](https://api-docs.deepseek.com/guides/vision)：
  能读截图里的文字），但 jarvis 截图后模型反复回报「我拿不到画面内容」，还写了十几秒
  内心戏。图片驾驶舱里人类看得见，模型看不见——两边各自以为对方知道。
- **根因两层**：
  1. `_messages_to_openai` 里 `role="tool"` 的 content **只能是字符串**，图片塞不进去。
     代码只在末尾拼一句 `[附带 N 张图片]`，注释写着「后续如需视觉 tool_result 再追加
     独立 user 消息」——**从未实现**，图等于被丢掉；
  2. DeepSeek 明文规定**图片只允许出现在 user 消息**（出现在 system / assistant 里
     直接 400），所以正确姿势就是另起一条 user 消息，这正是上面那步没做完的事。
- **修法**：新增 `_tool_images_message()`，把工具结果里的图（取最近 `_MAX_TOOL_IMAGES=4`
  张，远低于单请求 48MiB 上限）转成一条 `user` 消息带 `image_url`（base64 data URL，
  与用户直接贴图同构）；tool 消息末尾改写成「见下一条消息」。
- **文案配套**：`image_receipt()` 增加 `visible` 参数，判据由工具经 `vision_hint(ctx)`
  取到（唯一真源仍是 `model_capability`）：已知有视觉就直说「你可以看到这张图」，
  已知无视觉就直说「你只拿到路径，不要描述画面内容」，只有未确认时才保留旧的条件式
  提醒。模型不再拿「附上: 是」误判自己看得见。
- **实测**：造一张含 `JARVIS VISION CHECK / CODE 7391-ALPHA` 的图，走
  `_messages_to_openai` → 真发 `deepseek-flash`，返回 **`7391-ALPHA`**（思考里逐行读出
  了图上三行文字）。全量 **5007 passed / 2 skipped**。
- **顺带记录**：`max_tokens` 太小 + 思考模式会让视觉任务返回空 `content`
  （首次验证 `max_tokens=60` 全被 `reasoning_tokens` 吃掉）；依赖看图的任务要给够
  输出预算。

### 重构（三舱视觉：三色纪律 + 共享材质，2026-11-12）

- **依据**：`posters/jarvis-cyberpunk-design-philosophy.md`（Neon Doctrine）。先生指定三舱
  主色：任务驾驶舱电光青 `#00d4ff`、爬虫驾驶舱品红电流 `#ff2d78`、图片驾驶舱荧光绿 `#39ff88`。
- **`hud_theme` 重写为色板模型**：`HudTheme` + `THEME_TASK/CRAWL/IMAGE` 三个实例；
  **任务舱是全局默认**，旧常量名（`CYAN`/`GRID`…）继续指向它，因此工具卡、代理卡、
  详情浮层三个子组件零改动即换新色。
- **材质三舱共用一份**：`draw_background`（深渊黑 + 4px 扫描线 + 28px 网格 + 固定种子
  96 粒噪点 + 左右侧刻度）与 `draw_frame`（外框 + 四角 HUD 括号），替掉三个舱各自手写
  的网格/角标；`glow_text` 统一「先辉光后亮字」，支持 `fill` 覆盖让成/败数字也能发光。
- **语义色跨舱统一**：成 `#33f0c0` / 败 `#ff4d6a` / 警告 `#ffb347`，只有主色随舱变；
  品红舱的失败色刻意偏橙红，避免与主色糊成一片导致成/败不可辨。
- **改名**：「图片窗口」→「图片驾驶舱」，类 `ImageWindow` → `ImageCockpit`，标题
  `IMAGE COCKPIT // 图片驾驶舱`；爬虫舱标题补全为 `CRAWL COCKPIT // 爬虫驾驶舱`。
  配置键 `image_window` 与文件名 `image-window.md` 刻意保留（改名会破坏既有配置与链接）。
- **新增预览**：`python -m agent.ui.hud_preview` 一次弹三舱对比配色（不跑模型、不需要 key）。
- 行数：`cockpit_window` 894 → 884、`cockpit_crawl` 857 → 838（删掉重复的网格/角标实现），
  `hud_theme` 89 → 约 340。全量 **5007 passed / 2 skipped**。

### 修复（三舱可读性：文字看不清、线条不清晰，2026-11-12）

- **现象**：先生实测后反馈「文字看不太清，线条不够清晰」。
- **原因两条，都在我自己上一版里**：
  1. 为了「暗部必须保留材质」，我把 `dim` 压到 45–55% 白度，又铺了 **4px 间距的扫描线**
     ——小字压在横纹上必然糊；噪点 96 粒进一步压字。
  2. 结构线（外框、分区线、面板边、✕ 圆圈）用的是 `accent_dim`（如 `#0b4d66`），
     太暗，读者分辨不出区块边界。
- **修法**：
  - `HudTheme` 新增 **`rule`**（结构线色，介于主色与辉光之间：TASK `#1b9fc4`、
    CRAWL `#c2195c`、IMAGE `#1fbf6b`）；`draw_frame` 外框、三舱的汇总格/面板边/
    分区延长线/✕ 圆圈共 10 处全部改用它；
  - 文字提亮：`text` 提到 ~93% 白度、`dim` 提到 ~65%（三舱各自带主色色相）；
  - 纹理退后：扫描线间距 4px → **6px**、噪点 96 → **64** 粒、`grid` 各降一档
    ——单舱绘制项 344 → **262**。
- **刻意不动字号**：试过整体 +1，但各舱文本是按字符数 `clip()` 的（`clip(56)`、
  `clip(62)`…），字号一涨就会顶出面板，需同步重算所有截断宽度。本轮先靠对比度解决；
  若仍嫌小，下一轮统一调字号并一并重算 clip。

### 修复（三舱可读性 II：文字与结构线再提一档 + 爬虫舱收窄，2026-11-12）

- **第一轮修完仍看不清**。先生三张实测截图指出两处根因（与字号无关）：
  1. **分区标题用了 `accent_dim`**（如品红 `#6d1035`）——「抽取 // EXTRACT」
     「拓扑 // TOPOLOGY」几乎不可见。改为 `rule`，与结构线同级可见。
  2. **`dim` 作为信息色太暗**：耗时列、数据流、路径、说明全用它，在深渊黑上
     「背景与字体分不清」。三舱 `dim` 统一提到约 80% 白度
     （TASK `#bcd9e6` / CRAWL `#e0b9c7` / IMAGE `#c2e8d6`）。
- **结构线再亮一档**：`rule` 提到接近主色（TASK `#46cdef` / CRAWL `#ff5c9a` /
  IMAGE `#59ffab`）；任务舱的 ✕ 圆圈与两条分隔线、图片舱 footer 线一并改用它。
- **爬虫驾驶舱收窄**：`CRAWL_W` 560 → **500**；两处长行截断同步收紧
  （统计行 84 → 70、拓扑统计 88 → 72），否则会顶出面板。
- `HUD v1` 角标从 `CYAN_DIM` 改 `DIM`。
- 字号仍未动：各舱按字符数 `clip()`，需统一重算；本轮纯靠亮度层次解决。

### 修复（任务驾驶舱只剩背景和标题，2026-11-12）

- **现象**：先生截图显示任务驾驶舱只剩背景网格与标题，正文全无。
- **根因**：上一轮把 `CYAN_DIM` 换成 `RULE` 时**漏了 import**，`_draw_header` 画完
  标题后抛 `NameError`，被 `_pump` 的 `except` 记进 diag 吞掉——于是只画到标题为止。
- **为什么三道检查全绿**：`py_compile` 只查语法（`NameError` 是运行时）；`tests/ui`
  257 条用假窗口替换 `CockpitWindow`，从未执行 `_Hud.draw()`；人工冒烟只验「不崩」，
  异常被吞后进程照样活着。
- **修法**：补 import；新增 `tests/ui/test_hud_render_guard.py` 两道闸——
  ① ruff `F821` 扫 `agent/`（漏 import 即红，CI 有效）；② 真 Tk 渲染守卫（有显示
  环境才跑），断言 `draw()` 后 canvas item > 200 且文字 ≥ 10 段，"只剩背景"立刻红。
- 真机复核：任务驾驶舱 **309 items / 31 段文字**；全量 **5009 passed / 2 skipped**。

### 修复（熔断的 MCP 工具被反复调用，2026-11-12）

- **现象**：`tyc-mcp` 熔断后，模型在一次任务里**连调 4 次** `search_companies`，
  每次都拿到「已熔断 (3 次失败，156s 后重试)」，白费 4 步与近半分钟。
- **根因三条，都不是模型"记不住"**：
  1. 熔断状态只出现在**调用之后**的结果里；工具列表里那个工具看起来完全正常——
     模型在"选工具"那一刻没有任何依据；
  2. 错误文案只陈述事实，没有「别再试」的指令；
  3. `{e}` 直接插值 `KeyError`，文案还带一对引号，像底层异常而不是明确裁决。
- **修法**：
  - `MCPToolWrapper.description` 改为**动态 property**：熔断期间追加
    「`[不可用]` 该 server 已熔断（连续失败 N 次），约 R 秒后自动恢复……」；
    未熔断时逐字节返回原描述（不破请求前缀缓存）；冷却到期或未达阈值都不谎报。
    文案刻意用 GBK 可编码字符（emoji `⛔` 会让 `/tools` 一类打印在 cp936 控制台上
    直接抛 `UnicodeEncodeError`）；
  - `call()` 的未连接/熔断错误标 **`retryable=False`**（自愈层不再重试），文案写明
    「不要再调用本工具：熔断解除前每次调用都会立即失败。请改用其他来源，或直接向
    用户说明该数据暂不可得。」；顺带修掉 KeyError 的引号。
  - `MCPClient` 新增 `breaker_remaining()`（失败次数 + 剩余秒数），作为唯一状态源。
- 测试：`tests/tools/test_mcp_tool_breaker.py` 7 条（描述三态、过期不谎报、
  错误不可重试、无引号）。全量 **5016 passed / 2 skipped**。

## [2.5.4] — 2026-11-11

### 新增（`/cost` 真的开始算钱了，2026-11-11）

- **按官方价目逐笔结算**：新增 `agent/llm/pricing.py`，把 token 用量换算成人民币。
  此前 `/cost` 只有 token 数与命中率、**一行金额都没有**，而 `CACHE_POLICIES` 里 13 处
  `hit_discount` 是死字段（全仓无一处用它算钱）。DeepSeek 现行价目里缓存命中价是未命中的
  **1/50**（flash 0.04 vs 2 元/百万），用「折扣系数」近似的误差是几十倍量级。
- **峰谷按时段判定**：高峰为北京时间周一至周五 9:00–12:00、14:00–18:00（不含法定节假日），
  其余时段半价。判定复用 `agent/core/daemon/holidays.py` 的节假日数据；时区用固定 UTC+8
  （中国无夏令时，为省一个 tzdata 依赖不引入 zoneinfo）。金额按**每笔请求发生的时刻**结算后累加，
  不是看 `/cost` 那一刻的时段——跨峰谷的会话否则会把谷时便宜单算成高峰价。
- **旧模型名映射**：`deepseek-chat` / `deepseek-reasoner` / `deepseek-v4-flash` 等按官方说明
  归到现行计费名（旧名仍可调用，但按新模型价计费）。
- **未收录价目就不算**：DashScope/Qwen、GLM、MiniMax、Kimi 等没有权威价目来源的模型返回
  `priced=False`，`/cost` 显式写「该模型未收录官方价目」并照实列 token 数，不给假数字。
- **桌面壳同源**：`cost.get` 直接取 `cost_cny`，只显示不自算，避免两套价格口径。
- `/cost` 新增「缓存未命中 token」「其中思考 token（含在输出内）」「价目来源（含核对日期）」三行，
  命中率可当场验算：`148,480 + 1,517 = 149,997`、`148,480 / 149,997 = 99.0%`。

### 修复（用量口径，2026-11-11）

- **`/cost --raw` 从未生效**：`dispatch_command` 用**整行**做精确匹配，而 `/cost` 只在精确表里，
  带参数的命令要靠前缀表接住。于是 `--raw` 被静默丢弃，命令敲了什么都不发生。补
  `("/cost ", handle_cost)`（带空格，不吞 `/costly` 一类同前缀命令），并对未知参数发 warn。
- **思考 token 取明细且不重复计**：原来只读 `completion_tokens`，从不看
  `completion_tokens_details.reasoning_tokens`。归一路径抽成可测纯函数 `normalize_usage()`：
  reasoning 是 completion 的**子集**（只记录、不相加，相加即重复计费）；仅当
  `reasoning > completion`（该厂商分开计量）才以 reasoning 为准。
- **命中率不再猜协议**：厂商直接给 `prompt_cache_miss_tokens` 时用 `hit / (hit + miss)`
  精确相除，与协议无关；拿不到才退回旧的「`cache_read > input` 视为 input 不含缓存」兜底。
  金额结算同样改用显式未命中数，不再二次猜协议。
- **上下文统计改为实计**：`/cost` 的「当前上下文」改报最近一次请求的 `prompt_tokens`，
  系统提示改报字符数（字符数确定，换算成 token 必然带误差）。实测反推显示按官方语种比例
  （中文 0.6 / 英文 0.3 每字符）估算反而偏离更远：估算 14,508 而真值约 8,300——
  有实测就不该估算。
- **`/context` 交底它缺什么**：原估算**不含 tools schema**（实测 8,500 vs 真实 23,456，
  差约 64%）。现标注「字符估算，不含 tools schema」，并新增一行实计，与 `/cost` 同口径。
- 新增 `tests/llm/test_pricing.py` 26 例、`test_usage_normalization.py` 13 例、
  `test_cache_metrics.py` 11 例、`tests/commands/test_command_routing.py` 5 例；
  其中一条专门盯 `("/cost ", handler)` 是否还在前缀表里，防止下次清理表格又把它删掉。

### 文档

- **README 瘦身**：“任务驾驶舱”一行原本把滚轮滚动、缩放平移、动画时长等交互细节全写进表格（1172 字），
  改为“什么时候弹 / 显示什么 / 抓取能干什么”三条人话，细节交回三篇方案文档；
  “15 字标题生成”这类内部实现从能力表整体撤除（语音行与记忆行各有一处）——README 讲能力，不讲实现。
- **开发路线压缩**：阶段 10–13 各压成一行，实测数字与脚本路径留给架构文档；
  同时订正阶段 10 的“尚未发布”——它已随 v2.3.0 发布（阶段 11 同版）。`README.en.md` 同步全部三处，
  并补上此前缺失的 Phase 11–13（英文路线图长期只到 Phase 10）。

## [2.5.3] — 2026-11-10

### 修复（发版后安装态验收发现，2026-11-10）

- **重定向下 `jarvis --doctor` 直接崩**：`jarvis --doctor > log.txt`（以及 `| tee`、被父进程
  捕获）在简体中文 Windows 上以 `UnicodeEncodeError: 'gbk' codec can't encode character
  '\U0001f4e6'` 退出，日志里一张表都没有。根因不在 Rich，而在 Python 给**非控制台**
  的 stdout 选的是区域编码（cp936）且 `errors=strict`，而诊断表格标题带 `📦`；直连控制台
  时 Python 走 WindowsConsoleIO（utf-8）所以平时看不见。
- **修法：新增 `agent/core/stdio.py::make_stdio_crash_proof()`**，只把 `errors` 放宽为
  `replace`（不可编字符降级成 `?`）、**不换编码**——换 utf-8 会让下游按 cp936 解码的管道
  把中文读成乱码。接入两个入口：`main()`（覆盖所有 CLI 路径，排在 `parse_args` 之前）与
  `run_doctor()`（覆盖不经 main 的 `python -m agent.doctor`），幂等。
- **影响面不止 doctor**：任何经 Rich 输出 emoji 的命令，在 stdout 被重定向时都会走同一条
  崩溃路径；修在入口一次覆盖所有未来字符，而不是逐处删 emoji。
- **非本版引入**：`git log -S` 追到 `06fc104`，属长期潜伏缺陷（没人一边接管道一边看诊断）。
- 新增 `tests/core/test_stdio_guard.py` 8 例（反证必抛、降级后中文不受伤、无 `reconfigure` /
  `None` / `reconfigure` 抛错三种流静默跳过、幂等、两条入口接线），并做反向变异验牙
  （撤掉调用 → 入口级用例立刻红）。复盘见
  [doctor-redirect-encoding-fix.md](../fixlogs/doctor-redirect-encoding-fix.md)。

## [2.5.2] — 2026-11-10

### 新增（爬虫 P4：浏览器降级，2026-11-09）

- **`Crawl` 新增 `use_browser` 抓取后端**：`auto`（默认）/`always`/`never`。
  `auto` 先走 HTTP，遇到 403/503、Cloudflare 等挑战页、正文过短（< 500 字符，
  疑似 JS 空壳）或请求异常时自动降级到无头浏览器渲染；`always` 全程浏览器，
  `never` 只用 HTTP（正文短本身不算失败）。
- **反爬只识别不绕过**：命中 `Just a moment...`、`cf-challenge` 等特征即把该页
  标为失败并写明原因（浏览器仍未通过也会标红），不做任何挑战绕过。
- **浏览器后端复用共享实例**：新增 `fetch_via_browser()`（临时 page、渲染完即关，
  不改变用户/模型正在操作的页面）；一次爬取结束若浏览器是本次拉起的就释放。
  页面 `kind` 记为 `browser`，工具回执里该页后缀 ` ·browser` 便于区分。
- **新增配置**：`[crawl] use_browser = "auto"`，`settings.py`、`settings_domains.py`、
  `catalog.py`、`settings.example.toml` 四处同步；非法值回退 `auto`。
- 新增 `tests/tools/test_crawl.py::TestBrowserFallback`（策略归一、挑战页识别、
  空壳页降级、挑战页标红、always 不走 HTTP）。

### 新增（爬虫 P3：分层拓扑与窗口放大，2026-11-09）

- **爬虫驾驶舱放大到 560×640**：URL 清单 7 行、抽取表 4 行，新增约 160px 高的分层拓扑区。
- **分层拓扑**：节点 = 去重 URL、边 = `parent_url → url`、层号 = depth（根页居中）；
  成功绿 / 失败红 / 未知灰；单层超过 8 个或总数超过 `crawl_max_nodes` 时折叠为
  `+N`；按已确认方案不做缩放、平移与节点拖拽。
- **节点↔抽取行联动**：点击拓扑节点高亮对应抽取行，点击抽取行高亮节点，
  再次点击取消；单页抓取或拓扑关闭时显示对应空态。
- **新增配置**：`[visualization] crawl_topology = "auto"|"off"`、
  `crawl_max_nodes = 60`（20–200），`settings.py`、`settings_domains.py`、
  `catalog.py`、`settings.example.toml` 四处同步。
- 新增 `agent/ui/cockpit_crawl_topo.py`（纯函数布局）与
  `tests/ui/test_cockpit_crawl_topo.py`。

### 新增（爬虫 P2：Crawl 多页抓取，2026-11-09）

- **新增 `Crawl` 工具**：广度优先抓取站点，支持 `max_pages`（1–100）、
  `max_depth`（0–5）、`same_domain`（子域算同域）、`delay_ms`（默认 300）、
  `respect_robots`、`timeout_s`、`max_bytes_per_page`，并可按 CSS 选择器
  `extract` 逐页抽取（复用 WebExtract 的 `extract_fields`）。单页 4xx/5xx/
  超时不终止整次爬取；不绕过 Cloudflare 等反爬挑战。
- **驾驶舱数据源接通**：Crawl 结束时经 `ToolResult.trace_meta` 批量上报，
  `TraceRecorder` 逐条派生 `web_fetch`（带 `parent_url`/`depth`/`kind`）、
  `web_extract` 与 `crawl_stats`。URL 清单显示 `L0/L1…` 层级（单页抓取显示
  `L-`），拓扑区统计行显示队列/完成/失败/去重/深度/字节/耗时；抓取缓冲从
  20 条扩到 200 条。
- **新增 `[crawl]` 配置段**（`enabled`/`max_pages`/`max_depth`/`same_domain`/
  `delay_ms`/`respect_robots`/`timeout_s`/`max_bytes_per_page`），
  `settings.py`、`settings_domains.py`、`catalog.py`、`settings.example.toml`
  四处同步；`enabled=false` 时 Crawl 与 WebExtract 均拒绝执行。
- **阶段边界**：浏览器降级（`use_browser` / CF 识别）按计划留到 P4；
  Crawl 目前结束一次性上报，不做逐页实时刷新；拓扑连线仍待 P3。
- **验收打磨（2026-11-09 实测）**：跳过 Cloudflare 邮箱混淆链接
  （`/cdn-cgi/l/email-protection…`），不再白抓一条 404；驾驶舱“去重”格优先取
  `crawl_stats.dedup_hits`——Crawl 在入队阶段就去重，单看 fetch 会显示 0。
- 新增测试 `tests/tools/test_crawl.py`（URL 规范化、同域、链接、robots、
  BFS 层级/去重/失败继续/逐页抽取）。

### 新增（爬虫 P1：WebExtract 结构化抽取，2026-11-09）

- **新增 `WebExtract` 工具**：抓取单个网页后按 CSS 选择器把字段抽成表
  （`fields: [{name, selector, attr}]`，attr 常用 text/html/href/src）。
  选择器用 `beautifulsoup4` + 内置 `html.parser`（核心依赖新增
  `beautifulsoup4>=4.12`，不引入 lxml）。抽不到的字段记入 `missing`、不返回
  空字符串；作用范围 `selector` 未命中时全部记缺失，不退回整页抽取。
- **爬虫驾驶舱“抽取 // EXTRACT”区点亮**：`ToolResult` 新增 `trace_meta`
  旁路字段（不进入 tool_result 文本、不参与截断），orchestrator 原样传给
  tool_after 钩子，`TraceRecorder` 据此派生 `web_extract` 事件；窗口显示
  域名 + 前 3 个字段，缺失时标“缺 N”。
- 文档：`docs/architecture/05-工具系统.md` 工具表、`docs/plans/crawler-cockpit.md`
  数据契约同步；新增测试 `tests/tools/test_web_extract.py`。

### 优化（语音交互，2026-11-09）

- **`/voice` 空格打断**：键盘打断键由 ESC 改为 **Space**（`keyboard.is_pressed("space")`），
  播报/推理中按空格立即停止并回到聆听；`barge_in_key = true` 仍可总开关控制。
- **10 秒静默自动待机**：聆听阶段连续 `[voice] idle_standby_seconds`（默认 10 秒）
  没有任何识别文本（含连续“没听清”/识别失败）时，自动进入待机等唤醒词；
  `0` 可关闭。新增 `_IdleStandbyTimer` 看门狗，到点中断阻塞的 `stt.listen`，
  用户一开口（任何 partial）立即取消，避免误杀正常长句。
- **修复：长回答结束后误待机**：静默计时原先从“用户上次说话”开始算，LLM 思考 +
  工具 + TTS 播报超过 10 秒时，话一说完就立刻进待机。现在每轮回到聆听前重置
  `last_speech`，给用户完整的 10 秒窗口；连续“没听清”的空轮次仍会累计。
  复盘见 [voice-idle-standby-after-reply-fix.md](../fixlogs/voice-idle-standby-after-reply-fix.md)。
- **修复：无声音自动唤醒 / 对话幻听**：对话与待机录音都不再下发唤醒热词（避免 ASR
  在静音/噪音/TTS 尾音时幻听「贾维斯」）；待机唤醒改用 `matches_wake`（整句≤12 字 +
  唤醒词在前 8 字）；对话/待机都加 VAD 门禁——没有服务端语音事件的结果直接丢弃，
  对话阶段也不会再出现“没出声却识别出「贾维斯」”。复盘见
  [voice-false-wake-fix.md](../fixlogs/voice-false-wake-fix.md)。
- **配置**：`[voice]` 新增 `idle_standby_seconds`；`/config` 语音面板同步展示。
- 文档同步：`docs/guide/voice.md`、`docs/architecture/06-语音系统.md`、
  `docs/test/TEST_CHECKLIST.md`（T-129 空格打断、T-133 静默待机）。

### 优化 / 修复（代理卡交互与并行输出，2026-11-09）

- **终端优先：用 Git Bash / cmd 时驾驶舱与抓取舱自动让出置顶**：此前两窗常驻
  `-topmost`，会挡住终端内容。新增 `agent/ui/cockpit_zorder.py`，Tk 线程每 0.4s
  查一次前台窗口进程名（mintty / cmd / conhost / Windows Terminal / PowerShell 等），
  命中就取消两窗置顶（不 lower，避免闪烁），切到其它程序再自动恢复；非 Windows
  平台行为不变。`CrawlManager` 新增 `set_topmost()`，新弹出的抓取窗沿用当前策略。
- **修复：一次网页抓取暴露的三处体验问题**（用户实机走查）：
  1. `ToolSearch` 搜「浏览器 打开网页」时把 CLI-Anything 插件排到前面、内置
     `BrowserNavigate/BrowserGetText` 被挤出前 6，返回体 **56,423 字符**被宿主截断，
     模型白跑一步还看不清 schema。现在中文长词拆二元组 + 中英别名映射，搜索池
     加入已常驻的内置工具（标注“已可用，直接调用”），单条 schema 限 1200 字符、
     整次返回限 6000 字符。
  2. 会话标题直接截首句前 15 字，切在链接上得到 `jarvsi帮我抓取一下htt`。现在先
     剥掉代码块/行内代码/URL/绝对路径再截；只有链接时回退原文。
  3. 驾驶舱执行序列只显示最近 4 步，8 步任务里唯一的失败被挤出，出现“失败 1”
     但列表全 ✓ 的矛盾。现在失败步固定置顶（总行数仍为 4）。
  复盘见 [toolsearch-title-failed-step-fix.md](../fixlogs/toolsearch-title-failed-step-fix.md)。
- **新增左侧“爬虫驾驶舱”窗口（C2-lite，原名“抓取网络”）**：网页抓取可视化不再挤在右侧 430px 的
  驾驶舱里，改为**屏幕最左侧独立窗口**（520×520，与 HUD 同一视觉语言、整面可拖）。
  本轮出现第一条 `web_fetch` 才弹出，显示 URL 去重清单（状态/耗时/字节/×命中）、
  汇总（抓取/成功/失败/去重/累计字节/域名分布）、抽取表；任务结束保留，点 ✕ 只隐藏，
  下一轮有新抓取再出现。拓扑区先占位——没有 `parent_url` / `depth` 不画假边。
  配置：`[visualization] crawl_window = "auto" | "off"`；实现见
  `agent/ui/cockpit_crawl.py`。
- **`/talk` 也写入会话历史**：实时双工对话同样不经 `QueryLoop`，此前退出即丢。
  终端新增 `TalkHistoryRecorder`（`agent/voice/talk_history.py`）把 user/ai 转写按轮
  配对写入 `messages`，再用串行落库任务调用 `advance_session`——与文本、`/voice`
  共用「每轮保存 + 15 字标题」口径。配对规则与桌面 `WorkbenchRealtimeUI` 同口径：
  正常顺序 / 转写滞后 / 开场白 / 回声轮丢弃 / 打断轮 / 退出 flush。复盘见
  [talk-session-persist-fix.md](../fixlogs/talk-session-persist-fix.md)。
- **修复：`/voice` 会话只有时间戳、从不生成标题**：语音每轮走 `voice_loop` 直连
  `query_loop`，绕过了 REPL 的轮数统计与标题生成，退出时仍叫
  `session-YYYYMMDD-HHMMSS`。现在收尾逻辑收敛为 `advance_session`，**文本与语音
  共用同一套规则**：第 1 轮取用户首句前 15 字、第 2 轮 LLM 归纳 ≤15 字，且**每轮
  语音都即时落盘**（此前只在退出时保存）。`voice_loop.on_turn_end` 支持 async 回调。
  会话元信息保留 `created_at`/`updated_at`，`/sessions` 与 `/load` 选择器新增
  **创建时间**，`sessions.list` 返回 `created_at`。复盘见
  [voice-session-title-fix.md](../fixlogs/voice-session-title-fix.md)。
- **修复：纯闲聊也会弹出任务驾驶舱**：旧逻辑在 `run_start` 就懒创建窗口，所以每轮
  不调用任何工具的闲聊都会弹出一张“共 0 步 · 0 工具 · 任务完成”的 HUD。
  现在 `run_start` 只缓存，等本轮首个工具/抓取事件才建窗并补投 `run_start`（耗时仍从
  任务开始算）；没有工具调用的 `run_end` 直接丢弃，已显示的窗口在闲聊期间保持上一轮
  真实汇总，不再被刷成“0 步”。复盘见
  [cockpit-chat-pops-fix.md](../fixlogs/cockpit-chat-pops-fix.md)。
- **任务驾驶舱执行序列可展开**：主 HUD 的“执行序列 // EXEC”行支持点击展开/收起，
  显示该步工具名、目标、调用入参（最多 3 个短字段）、耗时与失败错误；详情面板覆盖
  数据流/抓取区，再次点击同一步收起；拖动窗口不会误触展开。
- **代理卡纵向散开 + 可拖动**：主 HUD 左侧第二列不再 `slot*18` 斜向重叠，
  改为 `slot*(140+10)` 纵向完全分开；按住卡面即可拖动，状态刷新不会重置位置。
- **工具卡点击展开**：单击青色工具卡在 `300×112` / `440×300` 之间切换，
  展开态显示目标、完整 slim 入参 JSON 与失败错误摘要；工具结果正文不落 trace，
  因此不进卡片。
- **并行代理输出不再错位**：多个子代理/队友同时输出 Markdown 表格时，逐片段
  转发会列列交错。`TeeActivityUI` 改为正文整段缓冲、工具边界/轮次结束一次性
  输出；思考按块缓冲并带 `💭 代理名` 前缀，不再共用同一个思考面板。
- **队友接入 Tee**：`TeammateRunner` 在终端存在卡片 observer 时也改用
  `TeeActivityUI`，此前队友绕过 Tee、直接写主 UI，是表格错位的主要来源。
- **`/voice` 语音模式保留操作可视化**：语音只影响 TTS 与终端思考面板，不再
  抑制主 HUD、工具卡与代理卡；语音任务执行期间同样实时更新，语音结束后窗口常驻。

### 新增（子代理任务卡 C3-lite.2，2026-11-08）

- **每个子代理/队友一张琥珀色卡**：数据源是已有的代理活动台账
  （`AgentActivityRegistry`），不再按工具弹卡。卡内五行：代理名 + `AGENT`
  标签/耗时、角色与模式（子代理 / 队友 · 团队）、任务摘要、当前工具或动作、
  轮次/工具数/状态。放在主 HUD **左侧第二列**（工具卡占第一列），与青色工具卡
  明确区分；完成/失败保留 800ms 后收起，`stopped` 立即收起，队友空闲保留
  暗色卡。默认最多 3 张，超出的只在主 HUD 页脚显示 `AGENTS n · +N`。
- **终端原始输出保留**：新增 `TeeActivityUI`（先转发宿主 UI、再写台账），
  子代理的工具卡/流式文本照常打印；只有桌面已有 `set_listener` 时仍走
  `AgentActivityUI`（桌面右栏行为不变）。
- **主任务统计隔离**：子代理的 `ctx.extra` 写入 `agent_id/agent_name/agent_mode`，
  trace 事件带上这三个字段；主 HUD 状态与工具卡对带 `agent_id` 的工具事件直接
  忽略（不计步数/失败、不再重复弹工具卡），`/trace` 回看行尾标注 `· @名字`。
- **配置**：`[visualization]` 新增 `agent_cards`（auto/off）、`agent_card_max`
  （1–6）、`agent_card_hold_ms`、`agent_card_keep_idle`、`agent_card_min_ms`；
  `agent_cards = "off"` 时完全回到旧终端行为（不注册 observer、终端零提示）。
- **批量派生区分**：`Subagent(tasks=[...])` 的并行代理名带 id 后缀
  （如 `explorer-3f2a`），代理卡不再出现多张同名卡。
- **实现说明**：纯逻辑与 Tk 分离在 `agent/ui/cockpit_agents.py`
  （`AgentCardPlanner` / `AgentCardManager`），共享造型常量在
  `agent/ui/hud_theme.py`；方案见
  [agent-cards.md](../plans/agent-cards.md)。

### 新增（多任务浮窗 C3-lite，2026-11-08）

- **多任务浮窗卡**：每个运行中的工具一张独立 tkinter `Toplevel` 小卡，
  主 HUD 左侧斜向堆叠；`auto` 模式下工具持续 ≥300ms 才弹，完成显示 ✓/✗ 后
  保留 600ms 自动收起；同时最多 4 张（可配 1–6），并行超出时主 HUD 显示 `+N`。
  卡内容：工具名、耗时、目标/入参、状态与扫描线；不抢键盘焦点。
- **统一可视化配置 `[visualization]`**：`enabled` 总开关 + `task_cockpit` /
  `tool_cards` / `tool_card_min_ms` / `tool_card_max` / `tool_card_hold_ms` /
  `effects` / `position_memory`；旧 `[ui] visual_cockpit` 保留为历史别名，
  同名时 `[visualization]` 优先。主 HUD 位置可记忆到 `~/.jarvis/ui_state.json`。
- **并行工具状态**：`CockpitState` 从单个 `current` 扩展为
  `active[tool_use_id]`，并行工具不再互相覆盖；主 HUD 显示“N 个工具并行”。
- **实现说明**：卡片逻辑拆为 `agent/ui/cockpit_cards.py`（`CardPlanner` 纯逻辑 +
  `CardManager` Tk 线程），共享配色/截断工具抽到 `agent/ui/hud_theme.py`；
  退出时显式断开卡片对 Tk 根的引用，避免 `Tcl_AsyncDelete` 崩进程。
  方案见 [cockpit-multicards.md](../plans/cockpit-multicards.md)。

### 新增（独立任务驾驶舱 C1b，2026-11-08）

- **独立 HUD 驾驶舱窗口**：tkinter 无边框深色窗口，右上角置顶、整面可拖动、
  自绘关闭按钮。首次任务开始时弹出，实时显示当前工具、三格指标（步骤 /
  失败 / 耗时）、执行序列、数据流与抓取列表。**任务结束窗口不关闭**，保留
  最终汇总；手动关闭后本会话不再弹出。`[ui] visual_cockpit = "window"`
  （默认）/ `"off"`；语音会话不弹窗。
  - 线程模型：Tk 独占后台守护线程，模型线程只投递事件队列；窗口创建失败的
    环境自动禁用并记诊断，不影响对话；退出时在 Tk 线程内释放对象，避免
    `Tcl_AsyncDelete` 崩溃。
  - 原「终端底部内嵌 Rich Live」方案（C1）按 2026-11-08 用户决策移除，
    显示阈值、高度降级与 Live 互斥一并删除；`CockpitState` 保留为窗口数据源。
  - `web_fetch` 仍由 trace 在 `on_tool_after` 派生（`WebFetch` /
    `BrowserNavigate`），工具实现零改动；缺失字段省略，不写假值。
    `/trace` 回看保留三类事件渲染。
  - 顺带保留：思考面板抽到 `agent/ui/thinking_panel.py`；修复 `tool_end`
    失败且错误摘要为空时 `render_event` 抛 `IndexError`。

### 修复（2026-11-08）

- **驾驶舱点 ✕ 导致 jarvis 退出**：关闭时 `destroy()` 销毁 Tk 后，`finally` 又调用
  `winfo_exists()`，命中已释放的 Tcl 解释器，实机报 `alloc: invalid block` 并退出
  整个进程；同时旧语义是“本会话永久关闭”，关闭后无法再显示。改为：点 ✕ 只
  `withdraw` 隐藏、不退出；下一轮任务首个工具自动 `deiconify` 重现；进程退出时
  单次销毁（不再调用任何已销毁 Tk 的方法）。复盘见
  [cockpit-window-close-exit-fix.md](../fixlogs/cockpit-window-close-exit-fix.md)。
- **cmd 下思考面板不显示**：cmd 是 legacy Windows 控制台（`legacy_windows=True`），
  Rich Live 的原地刷新/擦除不可靠，思考面板帧画不出来；Git Bash 因非 TTY 走静态
  打印反而正常。改为 legacy 控制台与“输出被重定向”同口径降级为静态模式，
  并用纯文本分隔块替代 Rich Panel（Panel 在 legacy 渲染器下会竖排乱码），
  思考结束一次性打印完整内容。复盘见
  [cmd-thinking-panel-legacy-console-fix.md](../fixlogs/cmd-thinking-panel-legacy-console-fix.md)。
- **窗口跟随首次动作被节流吞掉**：节流判断原来用 `0.0` 表示“从未执行”，
  Linux 上 `time.monotonic()` 是开机秒数，CI 新虚拟机的 uptime 低于阈值时
  首次“定位目录 / 显示窗口”会被误判为重复而直接跳过；改为 `None` 哨兵，
  首次一定放行、后续仍节流。复盘见
  [window-follow-throttle-uptime-fix.md](../fixlogs/window-follow-throttle-uptime-fix.md)。
- **任务收尾关不掉被操作窗口**：收尾清理把“当前前台窗口”一律当用户正在看而保留，
  但 WPS 等 COM 自动化会自己激活窗口，导致本轮新开的 WPS 壳窗口留在桌面上；
  现在只有“本轮没操作过”的前台窗口才保留。复盘见
  [wps-window-cleanup-foreground-fix.md](../fixlogs/wps-window-cleanup-foreground-fix.md)。
- **思考面板 Live 残留导致输出错乱**：窗口观察器（后台线程）与模型线程撞在
  同一份面板状态上，`_end_thinking` 又按“是否启动过”早退，一旦 stop 失败就留下
  泄漏的 Live，以 12fps 持续重绘把正文撕成一字一行；改为状态锁 + 句柄清单强制收口。
- **用户要求打开的 GUI 窗口被收尾误关**：`start firefox` 之后出现的窗口属于最终结果，
  旧规则只认“最后一步 / 前台 / 未被操作”，把火狐关掉了。现在由共享的 GUI 启动
  口径识别并保留；同时新增 `Tool.window_policy` 契约（`keep` / `transient` / 未声明），
  未声明时默认保留（`[ui] window_cleanup_unknown = "keep"`），只有显式关闭动作
  或声明 `transient` 才收尾关闭。两处复盘见
  [thinking-live-leak-and-gui-window-close-fix.md](../fixlogs/thinking-live-leak-and-gui-window-close-fix.md)。


## [2.5.1] — 2026-11-08

### 新增（操作可见性，2026-11）

- **终端操作轨迹**：旁路复用 `tool_before` / `tool_after` / `file_changed` 钩子，
  每轮写成 `~/.jarvis/traces/<run>.jsonl`，终端逐条显示工具、目标、耗时、失败原因
  与文件增删行数；`/trace` 回看最近一轮，`/trace list|path|export|on|off` 管理。
  只记路径与窗口标题，不记文件内容；命中密码管理器/银行等关键词的标题会脱敏。
- **窗口跟随**：`[ui] window_follow = "show"`（默认）把被操作的窗口置顶到前台，
  但**不调用 SetForegroundWindow**，不抢用户键鼠；写/改文件时用
  `explorer /select` 定位并选中文件（同目录 60 秒节流，读文件不弹）。
  `focus` 档才真正激活，并在终端明示接管输入。仅 Windows 完整支持，
  其他平台自动降级为 `off`。
- **零新增依赖**：窗口控制只用 ctypes 调 Win32 API，安装包体积不变；
  新增 `[ui]` 配置 10 项（见 `settings.example.toml`）。
- **任务收尾关窗**：一轮结束后自动关闭中间产物窗口（只关本轮新开且被操作过的），
  最终结果、当前前台与未保存窗口（标题带 `*`）保留；关闭用 `WM_CLOSE`，
  可用 `[ui] window_cleanup = "off"` 整体关闭。
- **通用窗口观察器**：窗口跟随不再按工具名登记，改为执行前后快照差分——
  任何工具（Bash/MCP/插件/harness/未来工具）开新窗且成为前台都会自动跟随；
  用户自己切已有窗口不触发，子代理默认不跟随。

### 修复（实机走查发现，2026-11-08）

- **Bash 多行后台命令误入沙箱**：`notepad ... &` 写在中途行、后面还跟 `sleep` 时，
  旧逻辑只看整段末尾的 `&`，误判为前台命令并送进沙箱，等 GUI 到 120 秒超时；
  现在逐行识别，任意一行是后台命令就跳沙箱。
- **GUI 启动被沙箱等待**：`cmd //c start` / `start ""` / `Start-Process` 按后台路由，
  不再等 GUI 退出。
- **窗口标题误配**：同一条文件名同时出现在记事本与 Notepad++ 标题里时，
  模糊匹配改为「精确优先、标题更短优先」，避免点击/输入发错窗口。
- 复盘见
  [docs/fixlogs/operation-visibility-bash-multiline-sandbox-fix.md](../fixlogs/operation-visibility-bash-multiline-sandbox-fix.md)。

### 修复（实机走查发现，2026-11-07）

- **Bash 复合后台命令卡满 timeout**：`cd <dir> && notepad.exe ... &` 的 `&` 作用于
  整个列表，`> /dev/null 2>&1` 只落在后半段，后台子 shell 仍握着管道，工具等 EOF
  到 120 秒超时。现对复合体整组加括号重定向，详见
  [docs/fixlogs/bash-compound-background-fix.md](../fixlogs/bash-compound-background-fix.md)。
- **摘要口径**：`文件工具 N 次` 只统计文件工具；GUI「另存为」产生的文件不计入，
  不再用「N 个文件」造成「没改文件」的误读。
- **按键跟随**：`KeyTap` 执行后也跟随当前前台窗口，Ctrl+S 弹出的「另存为」对话框
  能立即看到。

## [2.5.0] — 2026-11-07

### 新增（桌面打包全能力，2026-11-07）

- **冻结后端按 `[all]` 全 extras 构建**：`jarvis-serve.exe` 现在包含
  gui / browser / camera / vision / voice / mcp / daemon / wechat 八组依赖，
  桌面安装包不再出现“开发态能截图/语音、打包态没有”的落差。
- **随包 playwright 双内核**：`build_serve.ps1` 把无头壳与完整 chromium 下载到
  `dist/jarvis-serve/ms-playwright`，`serve_entry` 冻结态自动指向该目录，离线可用。
- **`jarvis-serve.exe --caps` 能力门禁**：新增 `agent/capabilities/gate.py`，
  检查八组模块、双内核与关键能力；构建脚本与发布流水线缺一即红。
- **vision extras 补 `paddlepaddle`**：paddleocr 只给算法与模型，OCR 真跑起来
  需要推理后端；此前只装 paddleocr 会“包装了却跑不动”。

### 新增（长期记忆可视化编辑，2026-11）

- **`MemoryUpdate` 工具**：模型在用户明确要求「记住…」时，把一条事实追加到
  `MEMORY.md`（`scope=user` 用户级 / `scope=project` 项目级）。只追加不覆盖：
  整条去重、单条 ≤ 2000 字符、行尾带日期；整体改写/清空只给用户。
- **`memory.get` / `memory.save` 指令**：桌面「设置 → 长期记忆」读取两个范围的
  完整快照（路径/全文/字数/修改时间）并整体保存；保存走临时文件 + `os.replace`
  原子写，覆盖前保留一份 `MEMORY.md.bak`，单文件上限 100 000 字符。
- **`reload_memory` → `memory_changed`**：保存后由引擎线程重拼本会话 system prompt，
  下一轮对话立即带上新记忆；同时推 `memory_changed` 让桌面面板刷新。
- 系统提示词新增条件段（仅注册了 `MemoryUpdate` 时注入）：何时该记、scope 分工、
  不记临时状态/密钥、重复内容不追加。

### 新增（桌面规划模式，2026-11）

- **serve 注册 `EnterPlanMode` / `ExitPlanMode`**：桌面模型可自主进入只读规划、
  提交方案到 `.jarvis/PLAN.md` 并恢复原权限模式。
- **`plan_submitted` 事件**：方案摘要 + 2000 字预览 + 文件路径推给桌面，以系统消息呈现。

### 修复（规划模式权限未生效）

- `PermissionChecker.check()` 支持调用时 `mode` 覆盖；`ToolOrchestrator` 在
  `ctx.extra["_plan_mode_entered"]` 为真时把 ctx 的 plan 模式传给 checker。
  此前 `EnterPlanMode` 只改 ctx、不改判定，写操作照样放行。
- `mode.set` 同步 `ctx.permission_mode`，避免下拉切模式后 ctx 与 checker 分叉。
