# 首启引导缺了语音 Key：语音与 LLM 是两套凭据

> 日期：2026-10
> 触发：「jarvis 的安装初始配置引导还缺一个很重要的配置，那就是语音的配置，也就是
> 阿里云的 API Key，因为目前 jarvis 使的是阿里云的语音模型，所以模型不用用户配置，
> 但是 API Key 需要用户配置」
> 改动：`agent/config/voice_key.py`（新建）、`agent/config/onboarding.py`、
> `agent/config/models_config.py`、`agent/voice/voice_config.py`、
> `agent/voice/stream_tts.py`、`agent/commands/handlers/voice_commands.py`、
> `agent/commands/handlers/config_commands.py`、`agent/commands/handlers/init_command.py`、
> `agent/commands/router.py`、`agent/main.py`
> 测试：`tests/config/test_voice_key.py`（新建，51 例）、
> `tests/commands/test_init_routing.py`（新建，10 例）

---

## 0. 结论先说

用户的要求是「引导里加上语音的阿里云 Key」。查下去发现这不只是**少问一句**，
而是三个缺陷叠在一起：

| # | 缺陷 | 直接后果 |
|---|---|---|
| ① | 首启引导**完全不问**语音 Key | 选 DeepSeek / OpenAI 的用户装完「能打字但不会说话」，界面上没有任何一处说缺什么 |
| ② | `/voice` 的 `_voice_api_key()` **不读** `settings.dashscope_api_key` | 你把语音 key 写进配置文件（`models.toml` 的 `dashscope_api_key`、或 `[realtime_talk] api_key`）**完全不生效** |
| ③ | 同一个函数**无条件回退** `settings.api_key` | LLM 换成 DeepSeek 后，它拿着 **DeepSeek 的 key 去连阿里云**，必然 401；而报错只说「TTS 连接建立失败」，用户根本想不到是 key 混用 |

②③ 同源：`voice_config.py` 里那行

```python
return os.environ.get("DASHSCOPE_API_KEY", "") or getattr(settings, "api_key", "") or ""
```

**只认环境变量，然后直接借主 LLM 的 key。** 于是用户机器上语音能不能用，实际
取决于他有没有设环境变量——配置文件里写什么都不算数。

---

## 1. 语音和 LLM 为什么是两套凭据

这是整件事的认知前提，也是旧代码没想清楚的地方：

- **说话 / 听话（TTS / STT）固定走阿里云百炼 DashScope**——不用用户选，也没有第二个选项；
- **对话模型（LLM）可以是任何一家**——DashScope / DeepSeek / OpenAI / Claude / 智谱 / Kimi / MiniMax / 小米 / 硅基流动 / 自建兼容服务。

所以「LLM 厂商 = 语音厂商」只在一种情况下成立：LLM 恰好也选了百炼。旧代码把
这两件事当成一件事（"反正 `api_key` 有个值，拿去用"），就是在别家的场景下必然翻车。

`/talk` 那条路径其实早就想对了——它写的是：

```python
api_key = settings.dashscope_api_key or os.environ.get("DASHSCOPE_API_KEY", "")
if not api_key and getattr(settings, "provider", "") == "dashscope":
    api_key = settings.api_key or ""
```

**带厂商判断才复用**。但 `/voice` 是另一份实现，没有这个判断。两份代码漂移了，
而 `/voice` 是用户日常更常用的那个。

---

## 2. 真机实测（用用户本机真实配置）

用户的 `~/.jarvis/models.toml`：

```
第 23 行  provider = "dashscope"          ← 顶层：百炼
第 41 行  api_key  = "sk-ws-H.E***"       ← 顶层：百炼的 key
无        dashscope_api_key
last_model = "deepseek-v4-pro"            ← 于是运行时切到了 DeepSeek
```

`load_settings()` 之后：

```
运行时 provider      = deepseek
运行时 api_key       = sk-8d5e99***       ← DeepSeek 的 key
default_provider     = dashscope          ← 顶层原值快照
default_api_key      = sk-ws-H.E***       ← 百炼的 key
dashscope_api_key    = (空)
```

**屏蔽环境变量 `DASHSCOPE_API_KEY` 后**（模拟「只把 key 写在配置文件里」的机器）：

```
旧实现 /voice 取到 = sk-8d5e99***    ← 拿 DeepSeek 的 key 去连阿里云 → 401
新实现 /voice 取到 = sk-ws-H.E***    ← 顶层配的百炼 key
```

这就是 ②③ 的现场：用户明明在配置里配了百炼 key，旧实现不但**不用它**，还拿了一把
**别家的** key 去连，而错误信息完全指不到「key 混用」这个方向。

用户当前机器上之所以还能用，纯粹是因为他**恰好设了环境变量**。换台机器、或清一下
环境变量，语音就废了——而且看不出原因。

---

## 3. 修复

### 3.1 单一真源：`agent/config/voice_key.py`（新建）

三个入口（`/voice`、`/talk`、`/init`）共用一个解析器，优先级四层：

```
1. settings.dashscope_api_key     显式配置（引导写这里；env.py 也会用环境变量灌入）
2. 环境变量 DASHSCOPE_API_KEY      给「改了配置但进程没重读」兜底
3. settings.api_key               仅当【当前 LLM】就是 DashScope 时复用
4. settings.default_api_key       仅当【配置顶层】就是 DashScope 时复用
```

第 3 条是修 ③ 的关键（**带厂商判断才复用**）。第 4 条是实测后补的，值得单独说：

> 用户顶层写的是百炼，但 `last_model` 切到了 DeepSeek。此时 `provider` / `api_key`
> 都指向 DeepSeek，第 3 条不成立，于是他明明配了百炼 key 却"未配置"。第 4 条从
> `default_api_key`（`load_settings` 在 `last_model` 覆盖前留的快照）里把它找回来。

**这层让用户零配置就能用**——不用环境变量、不用再写一遍 `dashscope_api_key`。

厂商判定用三个信号（任一命中即算 DashScope），宁可多判也不漏判：

- `provider == "dashscope"`
- `api_format == "dashscope"`（旧配置）
- `base_url` 含 `dashscope.aliyuncs.com`（provider 手填成 openai 但地址指向百炼）

多判的代价是「拿 DashScope 的 key 连 DashScope」——本来也对；漏判的代价是一个
看不出原因的 401。

### 3.2 首启引导加「条件第四问」

`onboarding._ask_voice_key()`。关键是**它不总问**，三种情况一律跳过：

| 情况 | 处理 |
|---|---|
| LLM 选的就是 DashScope | **复用**它的 key，不问、也**不写盘**（同一串 key 存两份，日后改一处忘另一处就是下一个 bug） |
| 配置里已有 `dashscope_api_key`（含第 4 层能复用的 `default_api_key`） | 不问，只告知来源 |
| 环境变量已有 `DASHSCOPE_API_KEY` | 不问，只告知来源 |
| **以上都不成立**（LLM 用别家且语音没配） | 问一次，**可回车跳过** |

于是「首启 ≤ 3 问」的承诺对最常见路径（选阿里云百炼）依然成立，只有真正会缺
语音能力的人才多看到一问。`FIRST_RUN_QUESTIONS` 常量也保持三元素不变，
docstring 里写明语音是"条件第四问"。

跳过时不是静默结束，而是在结尾一屏提示里给出补配方法（`/init`、`models.toml` 的
`dashscope_api_key`、环境变量、以及百炼控制台地址）。

### 3.3 写盘位置：`models.toml` 顶层，不是 `settings.toml`

`models_config.save_dashscope_api_key()`。为什么必须是 `models.toml`：

`dashscope_api_key` 是 `MODEL_TOP_KEYS` 的成员，启动期的 `split_model_config()`
会把 settings.toml 里的这类键**搬进** models.toml。写错文件的效果就是
「引导里填了，重启后却没了」。

同一个函数里**同时写 keyring 和 TOML 明文**，并且注释写明原因：`load_settings`
只从 TOML 顶层读这个字段（`env.py` 也只对 `settings.provider` 去 keyring 查**主**
key），并不回填 dashscope 这一项。所以 keyring 只是额外副本，TOML 才是被读取的
那份——别"优化"成只写 keyring。

### 3.4 报错要指向解法

`stream_tts.StreamTTSPlayer` 建连时的硬失败改成：

```
语音需要阿里云百炼（DashScope）API Key，但未取到可用值。
三种配法：运行 /init；在 models.toml 顶层写 dashscope_api_key；
或设环境变量 DASHSCOPE_API_KEY（key 在 bailian.console.aliyun.com 取）
```

最底层抛的是"没 key"，用户看见的却是"语音用不了"——中间必须有人把桥搭上。

### 3.5 `/config` 面板：语音 Key 挪到语音面板

原先 `dashscope_api_key` 展示在 **LLM** 面板里，会让人以为换 LLM 就要重配它。
移到 `_show_voice` 首行，并同时显示**脱敏值和它的来源**（配置 / 环境变量 /
复用 LLM / 复用顶层）。用户最常问的"我明明配了 key 怎么没声音"，现在能一眼看出来。

---

## 4. 改动的文件

| 文件 | 改动 |
|---|---|
| `agent/config/voice_key.py` | **新建**（165 行）：四层解析 + 来源判定 + 排查提示，单一真源 |
| `agent/config/onboarding.py` | `_ask_voice_key()` 条件第四问；写盘；跳过时给补配提示 |
| `agent/config/models_config.py` | `save_dashscope_api_key()`：写 models.toml 顶层 + keyring |
| `agent/voice/voice_config.py` | `_voice_api_key()` 改为委托 `voice_key.resolve()` |
| `agent/voice/stream_tts.py` | 无 key 时的报错改成可执行的配置指引 |
| `agent/commands/handlers/voice_commands.py` | `_realtime_talk` 改用统一解析（原实现逻辑对，但与 `/voice` 各写一份） |
| `agent/commands/handlers/config_commands.py` | 语音 Key 从 LLM 面板移到语音面板，显示来源 |
| `agent/commands/handlers/init_command.py` | **新增 `/init voice`**：已配好了也能换 Key（首启引导会跳过它）；`describe_detail` 展示当前是哪把 |
| `agent/commands/router.py` | `PREFIX_HANDLERS` 补 `("/init ", handle_init)`——以前 `/init voice` 与 `/init full` 根本路由不到，被当成普通对话 |
| `agent/main.py` | `--init` 改 `nargs="?"+const=""`，支持 `jarvis --init voice` / `full`（逻辑本身搬回了 init_command） |
| `configs/models.example.toml` | 新增「语音专用 Key」段（含四层优先级说明） |
| `configs/settings.example.toml` | `[realtime_talk] api_key` 的说明补全 |
| `config-docs/configuration.md` | 首启流程加④；`dashscope_api_key` 行重写；补 `/init voice` |
| `config-docs/voice-setup.md` | 新增「语音 Key：必须先配的那一项」整节；补 `/init voice` 与“为什么需要它” |
| `docs/architecture/06-语音系统.md` | 「API Key 隔离」整条重写为四层优先级 |
| `docs/architecture/12-配置系统.md` | 首启章节加「条件第四问」+ 三种粒度表 |
| `docs/guide/commands.md` / `USER_GUIDE.md` | `/init` 说明补充；新增 `/init voice` 行 |
| `docs/test/TEST_CHECKLIST.md` | 新增 T-387–T-392 |
| `tests/config/test_voice_key.py` | **新建**（51 例） |
| `tests/commands/test_init_routing.py` | **新建**（10 例）：从 `dispatch_command` 入口真跑，锁住 `/init` 子命令路由 |

---

## 5. 测试

`tests/config/test_voice_key.py` 分六块，每块钉一个曾经踩过或差点踩的点：

| 块 | 钉住什么 |
|---|---|
| `TestResolve` | 四层优先级；**LLM 不是 DashScope 时绝不借用它的 key**；空串/纯空格算未配置 |
| `TestReuseDefaultKey` | 顶层百炼 + `last_model` 切走的情形（真机场景）；运行中层优先于顶层 |
| `TestLlmIsDashscope` | 三个厂商信号；别家不算 |
| `TestHints` | 提示能区分"LLM 不是 DashScope"与"顶层是百炼但没填 key"；含获取地址 |
| `TestVoiceConfigUsesSharedResolver` | `/voice` 真的读配置里的 key、真的不借别家 key |
| `TestSaveDashscopeKey` | 写 models.toml 顶层、在任何 `[section]` 之前、保留其他内容、写盘后能读回 |
| `TestAskVoiceKeyBranch` | 三种「不问」+ 一种「问」+ 跳过时的提示 |
| `TestDescribeDetail` | 脱敏值 + 具体来源（配置 / 环境变量 / 复用）且**从不回显完整 key** |
| `TestInitVoice` | `/init voice` 能写新 key、回车不改、展示当前状态脱敏、LLM 是百炼时解释“为什么还要单独配” |
| `TestInitVoiceDispatch` | `/init voice`、`/init VOICE`、`/init -v` 分流正确，且不吃掉默认三问与 `full` |
| `TestQuickSetupIntegration` | 跑完整首启：选 DeepSeek 时语音 key 真落盘；选百炼时不写第二份 |

验证结果：`tests/config + tests/voice + tests/commands` **829 passed**；
全量 `pytest tests` **3852 passed**。

---

## 6. 实测追问：「没有第四问啊」

改完让用户实跑 `/init`，他贴回来的输出里有一句：

```
语音（阿里云百炼）：dashscope_api_key 已有值（配置文件写入或环境变量灌入），此项跳过
```

他的反问是「没有第四问啊」。**逻辑上没错**（他机器上 `DASHSCOPE_API_KEY` 有值，
``env.py`` 把它灌进了 `dashscope_api_key`，判为已配置），但这句提示暴露出三个问题：

### ① 提示是技术黑话，用户无法自证

「dashscope_api_key 已有值（配置文件写入或环境变量灌入）」——他既不知道是哪把 key，
也分不清是自己写的还是机器上残留的环境变量。一句“已经自动处理了”式的噪声。

改成带脱敏值与具体来源（新增 `voice_key.describe_detail()`）：

```
语音（阿里云百炼）：已配置 sk-w...RO5Q，来自环境变量 DASHSCOPE_API_KEY，此项跳过
  要更换这个 Key：运行 /init voice
```

想看脱敏值能不能对上自己的环境变量 → 能对上；想换 → 知道去哪。
靠**复用**（LLM 的 key / 顶层 key）而非显式配置时，额外说一句“当前没有独立的语音
Key，是靠上层配置复用出来的”——这种“配了”是推导出来的，用户很可能以为自己
从来没配过语音。

### ② 标题写死「3 个问题」，和实际显示对不上

```
═══ J.A.R.V.I.S 首次配置（3 个问题）═══   ← 下面却出现了 4 个环节的记录
```

语音那一问是条件出现的，写死数字只会让人怀疑“是不是漏了/多了”。改成不写数字的
标题，副标题里说清“语音的 Key 仅在还缺时才多问一句”。

### ③ 最严重：跳过 = 想换也没有入口

“已配置就跳过”本身是对的（不该每次启动都问）。但跳过之后，用户想**更换**就没有
任何交互入口了——只能手改文件；而“已配置”往往只是环境变量被灌进来的，他连
“有这回事”都不知道，更不会去改。

补了一个独立入口：**`/init voice`**（CLI：`jarvis --init voice`）。它先打印当前是
哪把 Key、从哪来（脱敏），再问新值：

- 输新 Key → 写 `models.toml` 顶层的 `dashscope_api_key`（优先于环境变量）；
- 直接回车 → 「未改动」，一个字节都不写。

顺带把 `--init` 从 `store_true` 改为 `nargs="?" + const=""`——裸 `--init` 行为不变，
跟一个词才选流程（`full` / `voice`），且以 `-` 开头的 token 不会被当成值，
`jarvis --init --workdir x` 仍然安全。

> 教训补一条：**“跳过”必须配一个“仍能改”的入口**。自动流程为了不打扰而跳过某项
> 时，别忘了用户主动想改的那一刻——那是另一条不冲突的路径，得单独留出来。

---

## 7. 第二次实测：`/init voice` 什么都没发生

入口做完了，用户跑：

```
> /init voice
>
```

提示符直接回来，**一个字都没打印**。

### 根因不在 handler，在路由表

`dispatch_command` 的判定顺序是：

```python
cmd = stripped.lower()
handler = COMMAND_HANDLERS.get(cmd)      # ← 拿【整串】精确查表
if handler is None:
    for prefix, h in PREFIX_HANDLERS:    # ← 前缀表里只有 "/init"，没有 "/init "
        ...
# 都不中 → 落到 skill 匹配 → return False → 被当成普通对话发给模型
```

所以：

| 输入 | 结果 |
|---|---|
| `/init` | 精确命中 ✓ |
| `/init full` | **匹配不到** → 当普通对话 |
| `/init voice` | **匹配不到** → 当普通对话 |

`handle_init` 里那套 `tokens = stripped.split()[1:]` 的分流早就写好了，但它**从来
没在 REPL 里被调用过**——因为命令根本没送到它手上。`/init full` 同样中招（CLI 的
`jarvis --init full` 走 `run_init_cli` 另一条路，所以一直没人发现）。

### 修复

`PREFIX_HANDLERS` 补一行：

```python
("/init ", handle_init),   # 注意带空格：才不会把 /initxyz 这类别的命令吃掉
```

同时**顺手体检了全部 handler**：把「自己解析 `stripped.split()[1:]`」的函数和
两张路由表做了交叉比对，看还有没有同样的漏配。

```
handle_compact  core_commands  路由=精确       ← 误报（它不解析参数）
handle_exit     core_commands  路由=精确       ← 误报
handle_plan     core_commands  路由=精确       ← 误报
handle_init     init_command   路由=精确,前缀   ← 就这一个是真的
handle_diff / handle_rewind / handle_undo  路由=精确,前缀 ✓
```

另外三个是探针正则的范围太宽（文件里任意一处有 `split()[1:]`，就把该文件所有
handler 都标上了），逐个人工确认后都不是问题。

### 路由层补了测试

`tests/commands/test_init_routing.py`（10 例）——之前**这个缺口完全没有测试**：

| 用例 | 锁什么 |
|---|---|
| `test_bare_init_hits_exact_table` | 裸 `/init` 仍走精确表 |
| `test_subcommand_is_dispatched` | `voice` / `VOICE` / `-v` / `full` / `FULL` / `-f` 六种写法都必须 `return True`（以前全落到普通对话） |
| `test_voice_and_full_go_to_different_branches` | 两个子命令分别进不同分支 |
| `test_extra_spaces_still_work` | `/init` + 多空格不能因为前缀带空格而漏掉 |
| `test_prefix_does_not_swallow_unrelated_words` | `/initxyz` 不被 `/init ` 前缀吃掉 |

> 教训：「handler 里写了分流」不等于「用户能用」。**注册表（COMMAND_HANDLERS /
> PREFIX_HANDLERS）与 handler 的分支能力必须一一对应**，而这件事只能靠
> “从 dispatch 入口真跑一遍”的测试来守，单元测 handler 内部是看不见的。
> 另一个信号：新加的子命令如果没人用过，很可能第一**就从没走通过**——
> `/init full` 在文档里写了很久，但实际不可用。

---

## 8. 教训

### ① 「缺一个配置项」往往是「读取路径本来就坏着」

用户的原话是"引导缺一个配置"，听起来是加一个提问那么简单。但先把读取路径查清楚
才发现：**就算引导问了、写进配置了，`/voice` 也读不到**——因为它只认环境变量。
只加引导，等于让用户填一个存起来但没人用的值，比不加更糟。

**新加一个配置项之前，先确认"读它的那条路现在是通的"。** 判断方法是拿真实配置
跑一次解析，看结果对不对，而不是读代码觉得应该对。

### ② 两份实现里总有一份是错的，而且错的那份更常用

`/talk` 的 key 解析带厂商判断（对的），`/voice` 不带（错的）。两份都在，
谁都没发现，因为**只有切到别家 LLM 的人才会触发**，而开发者自己通常用百炼。

这类"分支条件苛刻、结果只是静默失败"的分歧，靠 review 很难看出来。只能靠
**问一句「这件事还有没有第二个地方也这么做」**——有的话就是把它们合并。

### ③ 兜底链里"随便拿个值顶上"比报错危险得多

旧代码末端的 `or getattr(settings, "api_key", "")` 看起来是善意的降级
（"至少有个 key 能用"）。实际上它把**一个明确的"未配置"变成了一个会 401 的错
key**，而错误信息指不到真正的原因。于是用户的所有排查方向都是错的。

**降级必须带上"这个值在语义上是否等价"的判断。** 拿 DeepSeek 的 key 去连阿里云
不是降级，是换错。

### ④ 真机配置里的"空字段"可能是环境变量灌进去的

第一次实测时 `dashscope_api_key` 显示有值，我差点以为"用户早就配了"。屏蔽环境
变量后它变空——才知道**配置文件里根本没有这一项**，值一直是 `env.py` 从环境变量
灌进来的。看配置值时必须区分「文件里写的」和「运行时算出来的」，否则结论会正好
相反。

### ⑤ 条件提问要按"会不会缺"设计，不是按"要不要问"设计

语音 key 是必需品也是可选品：不配能打字，配了才算完整。硬塞进"三问"会破坏
既有承诺、也会让选百炼的人多答一个本可自动复用的问题。所以它做成**条件第四问**：
能复用就不问（LLM 已是百炼）、已配就不问、只有真会缺的人才看到。

**把"必须问的问题"和"可能缺的配置"分成两类**，前者守死数量，后者按需出现。
