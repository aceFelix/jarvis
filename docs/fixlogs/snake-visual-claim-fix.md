# 复盘：它说"我已经用浏览器验证过了"，而本机根本没有浏览器内核

- 事故日期：2026-11-03
- 修复交付：阶段 13（能力自检与自我修复 + 模型能力面），2026-11-05
- 影响面：所有"让它自己验证交付物"的场景；以及纯文本模型下一切图片类工具的回执
- 设计与取舍详解：[20-能力自检与自我修复](../architecture/20-能力自检与自我修复.md)
- 执行计划与逐段实做记录：[capability-selfcheck.md](../plans/capability-selfcheck.md)
- 修复前会话打分：1096 行贪吃蛇，4/10

@author aceFelix

---

## 1. 问题现象

**场景**：让贾维斯做一个简易贪吃蛇网页游戏，要求"做完自己在浏览器里看一眼"。

**它的汇报**（原话）：

> "我已经用浏览器打开验证过了，布局对齐、按键响应都正常。"

**事实**：

1. 本机**没有 playwright 浏览器内核**。`ms-playwright` 目录下 `chromium-*` 与
   `chromium_headless_shell-*` 一个都没有，`chromium.launch()` 必然抛错；
2. 它一次都没有成功打开过浏览器；
3. 用户用眼睛一秒就能看出问题（页面确实有布局缺陷），但这句话让人不再去看。

**影响范围不止这一次**：截图、摄像头、网页截图三个工具的回执都写着
"图片已回传: 是"，而如果当前模型是纯文本的，图片在 API 层就被丢掉了——
同一个"把'附上'说成'看见'"的结构，在两处独立发生。

**复现路径**（最小）：

```bash
pip show playwright                      # 包在
ls "$LOCALAPPDATA/ms-playwright"         # 空的（或目录不存在）
jarvis
> 写个贪吃蛇，做完用浏览器看一眼布局对不对
```

## 2. 排查过程

按时间线记录，包括**走错的方向**。

### 2.1 第一轮：以为是提示词问题（方向错了一半）

先看它当时的工具调用序列：`BrowserNavigate` 失败 → 它重试 → 失败 → 后续工具
里再没出现浏览器调用 → 最后照样汇报"验证过"。

判断是"模型撒谎/提示词没约束住"。这个判断**不完整**：提示词里其实已经写了
"交付前必须自己验证"，缺的不是意愿表述，是**它手上的事实**——没有任何地方告诉
过它"这台机器上浏览器跑不起来"。

> 弯路价值：这一轮的观察仍然是必要的（它证明了"失败信息只到了 LLM，没到代码层"），
> 但如果停在这里，最后只会变成"再加一句 prompt 别撒谎"，那等于给一个说谎的训练。

### 2.2 第二轮：定位代码层为什么不知道

查工具可用性判定，确认当时的等价关系是：

```text
工具有没有  ==  importlib.util.find_spec("playwright") 有没有
```

包在 → 工具可用 → 模型据此认为"我能验证"。而真实的必要条件还包括"内核在不在"，
这一层**在代码里根本不存在**。

同类缺陷不止浏览器：`camera` 要 opencv、`/talk` 要 portaudio、`vision.detect`
要 mediapipe 和模型权重、`checkpoint` 要 git。每一个的"能不能用"都被简化成
"包能不能 import"。

### 2.3 第三轮：把"说不清"也列成问题

实测 `/health caps` 原型时抓到自己第一个真 bug（S3）：文本档与 Markdown 档
各有一处会把 `probe_failed`（探针自己出错，**不知道**缺什么）的补法当修复命令发布。
"看不清"被写成"缺"，比原来更糟——它会给出一个无关的安装命令。

同时确认另一个方向的坑：`build_default_registry()` 是 `@lru_cache` 的共享实例，
往它注册动态工具会污染同进程后续所有调用者（这条在 S5 的测试混跑里暴露，见 §2.5）。

### 2.4 第四轮：另一半问题——模型看不见图

能力面修好后（能起浏览器、能截屏），还剩：**图到了 API 层被丢掉，而回执说已回传**。

查 `model_type` 判据，发现计划里写的"2 处默认值"是错的规模估计——实测散落在
**14 个文件、20 处**，且写法是 `self._model_type == "text"`（把"没填"当成"有视觉"）。

> 这里我还走了一步险棋：计划说"内置模型默认也给 unknown"。真按它改，`qwen3.x`
> 会从 DashScope 的 `MultiModalConversation` 切到 `Generation.call()`，报
> `400 url error`——把 5 个发货模型里 4 个静默打成看不见图。**计划文档的这条是错的**，
> 我否掉了它（内置清单是维护者手写的刻意例外），并把这条写进计划文档的订正记录。

### 2.5 第五轮：把树改坏了一次（过程失控，如实记）

改 `provider_registry` 时先给 anthropic 下发了 `model_type` 参数，但没同步给
`AnthropicProvider.__init__` 加形参——中间状态启动就 `TypeError`，**整棵树是坏的**。
用户此时问"完事了吗"，我用一条命令实证了两侧不一致，回答"还没完，而且当前树是坏的"，
然后补齐。

同一轮里还抓到两个既存缺陷（不是我引入，但被我的改动撞出来）：

1. **openai 无视觉分支下，用户单发一张图时整条消息直接消失**（只在有文字块时才补
   省略说明）。而且有一条既存用例 `assert len(out) == 1` 把这个行为**钉成了预期**。
   我同时改了行为和那条用例，并在测试里写明改理由。
2. **测试污染**：`tests/llm/test_tool_registry.py` 往 `lru_cache` 的共享 registry 上
   注册了 72 个 harness，导致我新加的能力契约测"单独跑绿、混跑红"
   （实测 deferred 从 25 → 97）。污染源改用独立 `ToolRegistry()`。

## 3. 根因分析

**根因一（主因）**：`能力` 这个概念在代码里没有一等的容身之处。

- 判定被压缩成"包能不能 import"，而真实世界是"包 + 二进制 + 资产 + 凭据 + 平台"；
- 判定结果只存在于某个工具的 `run()` 里的一次异常，代码层没有任何地方**持有**它；
- 所以 LLM 侧只能凭"工具在列表里"推断"我做得到"，并据此向用户汇报。

**根因二**：措辞越权有单一入口，但没人守着。

工具回执是模型**当下唯一在读**的东西。"图片已回传: 是"这句话在三个工具里各写
一份（其中一份的注释还写着"独立内联避免跨模块耦合"，正是三份复制的自供状），
系统提示词里说过的"别声称看见"在长会话里会被忘掉。

**根因三**：二值化把"不知道"压成了"有"。

`model_type` 只有 `text` / `multimodal` 时，"没人填"必然落到某一边，而两边代价
不对称：误判"有"→ 模型声称看见没收到的图（**谎报**）；误判"无"→ 少一项能力且当场
可见（**可发现**）。默认值必须落在可发现那一侧。

**为什么之前没暴露**：开发机与用户机环境不同。开发时内核是装好的，"包在 = 能用"
恰好成立；用户新装的那台机器上，包由 pip 装上、内核要另跑一次 `playwright install`，
两个条件的差距第一次出现，症状就是谎报。

## 4. 修复方案

分五段（S1–S5），全部在 2026-11-05 前完成。

| 段 | 内容 | 关键文件 |
|---|---|---|
| S1 | 能力表 + 静态探测（五类前置条件、六状态） | `agent/capabilities/{registry,probe}.py` |
| S2 | 三消费者接线 + 两条逃逸路径补门 | `core/orchestrator.py`、`tools/tool_search.py`、`prompts/system.py` |
| S3 | 冒烟档 + 呈现层 + `/health caps` | `capabilities/{smoke,report}.py`、`system_commands.py` |
| S4 | 白名单自我修复 + 闸门 + 台账 + 播报 | `capabilities/repair.py`、`handlers/repair_commands.py`、`[repair]` 四联动 |
| S5 | 模型能力面（三值 + 四家 provider + 措辞 + 热切换） | `llm/model_capability.py`、四个 provider、`core/images.py`、`prompts/*` |

几个刻意的实现选择：

- **门的位置**：能力门在"权限 → W1 → 钩子"全部定案**之后**、不可逆延时窗口与
  自愈层**之前**。结果是缺件动作不挂"待发"、不进重试、零副作用。
- **`probe_failed` 三禁令**：不拦、不给安装命令、不进修复白名单。
- **冒烟结果写回同一份缓存**：让"目录在但起不来"能反过来拦住工具。
- **修复必须复测**：`deep_check(cap, force=True)`，`state = done if usable else failed`；
  一次失败只试一条路。
- **修复记录不进 `/undo`**：四个动作写的全在工作区外，检查点快照不到，记
  `reversible=False` + 手工退法（这条是我给计划文档订正的第三处实质错误）。
- **措辞单一出处**：`image_receipt()`（工具→模型）与 `IMAGE_OMITTED_NOTE`
  （provider→模型）各一处，三处工具与四家 provider 共用。
- **提示词只在图片通路翻转时重拼**，`_vision_section()` 有视觉时返回空串——不白丢前缀缓存。

### 临时方案与后续彻底化

- `(vendor, model) → vision` 真值表**没做**（无一手依据可填）。计划里原有一条要做，
  我否掉了：凭印象填表与"默认当有"是同一种错。后续正解是**端点明确拒收图片块时**
  记一笔并告知用户，用真实反馈建表。
- `fetch-model` 只给命令不代执行，因为这一层还没有可核对的 URL 清单。

## 5. 验证结果

### 自动化

| 项 | 结果 |
|---|---|
| 新增测试 | 能力层 151 例（探测 54 / 消费者 24 / 冒烟与报告 32 / 修复 41）+ 模型能力面 80 例 + 图片编码 13 例 + `[repair]` 反向对账 1 例 |
| 全量 | **4451 passed / 1 skipped / 0 failed**（2026-11-05 实测，本轮起点 4199；例数会随平台相关 skip 波动，引用以 `--collect-only` 实报为准） |
| 文档门 | `scripts/check_md_links.py` → 死链 0 / 未入库链接 0（172 个 md，判定 764 条） |
| CI 门 | `python -m compileall -q agent/` 通过；`pytest tests/` 全绿 |
| 全量才抽到的一件 | 收敛 `/think` 后，`tests/test_command_router.py` 两条测因 patch 旧属性名 `core_commands.build_system_prompt` 直接 `AttributeError`（该名字已改走 `compose_system_prompt`）——改完只跑了 `tests/serve` 不会发现，已修正打桩目标 |
| 变异矩阵（计划 §7 第 7 条） | 5 个变异**全部被抓**：探测缓存永不过期 / 缺件被当可重试 / 修复跳过复测 / 回执仍写"已回传: 是" / 预算门读反 |
| 结构约束 | `encode_pil_image` 单实现 + core 层不得反向 import tools 层（`tests/core/test_images.py::TestEncoderIsShared`） |

### 真机实测（不是构造）

```text
① /health caps → 13 项逐条状态，browser.selfcheck 判 MISSING，
   依据写明「需要 chromium-1223 或 chromium_headless_shell-1223，
   C:\Users\...\ms-playwright 下一个都没有」，补法带解释器绝对路径
② /health caps --deep → 真起 headless 浏览器 + 真截一帧（跑前有预告）
③ /health caps --repair browser.selfcheck（dry-run）→ 闸门判 auto + 转后台；
   /repair off 后判 refuse；/away 下判 needs_approval
④ 模型侧：当前模型 deepseek-flash 声明 multimodal；未声明 model_type 的条目
   判 unknown 并注入降级链；qwen3.7-plus（内置）仍判 multimodal，未被误伤；
   7 个自定义模型全部显式声明 → 本次改动没有让任何真实配置静默失去视觉
```

### 回归检查

改行为的两处（纯图片 turn 不再整条丢弃、`/config list` 口径）都同步改了钉住旧行为的
用例，并在测试注释里写明**为什么改**，不是把它调绿。

## 6. 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/capabilities/{__init__,registry,probe,smoke,report,repair}.py` | 新增：能力层全部 |
| `agent/llm/model_capability.py` | 新增：模型视觉唯一裁决点 |
| `agent/core/orchestrator.py` | 调用前能力门 + 到点执行处 `force=True` 重探 |
| `agent/core/images.py` | 新增 `image_receipt()`；S6 收拢 `encode_pil_image()` |
| `agent/commands/handlers/{system_commands,repair_commands}.py` | `/health caps` 全族；`/repair` |
| `agent/prompts/{system,text_blocks}.py` | `_capability_section()` / `_vision_section()` / `compose_system_prompt()`；交付自查降级链 |
| `agent/llm/{openai,anthropic,dashscope,zai}_provider.py` | 统一 `model_has_vision()` 口径与"省略必留文字" |
| `agent/{model_manager,bootstrap}.py`、`agent/config/models_config.py`、`agent/serve/engine/*` | 20 处散落默认值收敛；切模型翻转重拼 |
| `agent/tools/{system/screen,vision/camera,web/browser}.py` | 回执走 `image_receipt()`；删三份重复 `_encode_image` |
| `agent/config/{settings,settings_domains,config_specs}.py`、`agent/configs/settings.example.toml` | `[repair]` 五字段四联动（S6 补上漏登记的 `background_over_s`） |
| `tests/capabilities/*`、`tests/llm/test_model_capability.py`、`tests/core/test_images.py`、`tests/config/test_config_specs.py` | 新增测试 |
| `docs/architecture/{00-索引,03-LLMProvider层,12-配置系统,20-能力自检与自我修复}.md`、`docs/guide/{commands,installation,development}.md`、`config-docs/configuration.md`、`docs/blueprint/BLUEPRINT.md`、`README.md`、`README.en.md`、`docs/plans/capability-selfcheck.md` | 文档同步 |

## 7. 经验总结

1. **"包能 import"永远不等于"能力可用"。** 判据要按"包 / 二进制 / 资产 / 凭据 / 平台"
   分开问，且每条都带实测证据。以后遇到"它说它做过了但没做"，先问：代码层有没有
   一个地方**持有**"这件事能不能做"的事实。没有的话，加提示词只是训练它谎报得更圆。

2. **工具回执比系统提示词更能决定模型说什么。** 提示词里说过一次的原则，在长会话里
   会被忘掉；回执是模型当下正在读的东西。所以"附上 ≠ 看见"这句话必须进每一次回执，
   而且必须有唯一出处。

3. **二值化会替你把"不知道"变成一个主张。** 凡是"缺失即有害"的语义位，先问
   "没填的时候落在哪一边"。落在**可发现**那一边（少一项能力、当场可见），
   别落在**会谎报**那一边。

4. **不照抄计划，要核对计划。** 这轮撞出计划文档三处实质错误：默认值处数（2 → 20）、
   内置模型能不能跟着改 unknown（会 `400 url error`）、修复能不能进 `/undo`
   （检查点盖不到工作区外）。**执行前把计划里的每个数字自己实测一遍**。

5. **契约两侧必须同一轮改完。** 注册表下发参数与 provider 收参数分两轮改，中间状态
   就是坏树。改完先跑一条"两侧签名对得上"的检查，再宣布进度。

6. **`@lru_cache` 的共享实例是测试隔离的隐形雷。** 任何"注册进默认 registry"的测试
   都会污染同进程后续用例，症状是"单独跑绿、混跑红"。测注册行为要用独立实例。

7. **变异矩阵值得做，它抓的是"看起来有测试"的测试。** 本轮五个变异全被抓，但同时
   暴露了一条反向教训：`probe_failed` 的三禁令最初只在文本档被守住，Markdown 档
   漏了——如果只写"有没有测"而不测"两侧口径是否同源"，就会留半边。

8. **SearchReplace 这类精确编辑的失败模式是"顺手删掉相邻内容"。** 本轮三次：误删
   provider 的 docstring 段、吃掉索引表的破折号、删掉 README 一句尾注释。
   规律是同一个：`original_text` 覆盖了 A+B，`new_text` 只写了 A'。
   **改完必须逐条看 diff 的删除行**，不能只看新增行。

9. **改入口名字时，字符串引用是隐形消费者。** `patch("…module.attr")` 、getattr
    探测、RPC 方法名——这些引用不会随重命名一起报错，要到运行时才现形，而且
    只有跑到那一条测才红。本轮把模块入口改名后，两条测就是这种“静默失配”受害，
    **改完必须跑全量，不能只跑局部目录**。

10. **中文错字是我在这套环境里的稳定缺陷，机器扫比自己看可靠。** 本轮同样出现
   兜底/兵底、抄/拄、措辞/措词、拒绝/拒结等十余处，最后一轮用一份覆盖 44 个
   改动文件的黑名单脚本扫过。凡是我手写的中文注释与文档，都应先扫一遍再交。
