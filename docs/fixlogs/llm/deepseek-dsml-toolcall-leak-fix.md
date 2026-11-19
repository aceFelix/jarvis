# DeepSeek DSML 工具调用泄漏进正文的兜底修复

> 2026-09 · LLM Provider 层 · @author aceFelix

## 现象

用 `deepseek-flash` 时，模型偶发把工具调用以文本形式吐出来，界面显示成乱码，且工具
不执行，用户要「重新叫一句」才好使：

```
我来查一下您所在地的天气。

<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="Location">
</｜｜DSML｜｜ invoke>
<｜｜DSML｜｜ invoke name="Bash">
<｜｜DSML｜｜ parameter name="command" string="true">date +"%Y-%m-%d %A"</｜｜DSML｜｜ parameter>
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>
```

下一轮重问，模型走回了结构化通道，工具正常触发（🔧 Location / Bash 正常出结果）。

## 根因（接缝在两处）

1. **模型/端点侧（诱因）**：`<｜｜DSML｜｜ ...>` 是 DeepSeek 内部工具调用序列化格式
   （DSML，`｜` 为全角竖线 U+FF5C，码点曾误写为 U+FF5D，见下方「二次补漏」）。正常由 API 服务端拦截并转成 OpenAI 结构化
   `tool_calls` 字段。偶发未拦截（多见于「正文前言 + 工具调用」同轮输出，或第三方兼容
   网关未实现 token 拦截）时，原始 DSML 漏进 `delta.content` 文本字段。
2. **jarvis 解析侧（缺陷）**：[openai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/openai_provider.py)
   的 `stream()` 只认结构化 `delta.tool_calls`，对 `delta.content` 里混进的 DSML 毫无
   兜底，于是原样 `TextDelta` 回显、不产生任何工具调用。

不是延迟加载 ToolSearch 的问题，也不是工具层的问题——是 provider 响应解析缺一道文本态
tool-call 兜底。

## 修复

新增 [text_tool_calls.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/text_tool_calls.py)，
在 `openai_provider.stream()` 接两道防线：

| 组件 | 职责 |
|---|---|
| `StreamingLeakFilter` | 逐块过滤 `content`：命中泄漏标记 `<｜｜` 前的正文照常输出，标记起的后续文本转入缓存、**停止回显**（避免裸码闪现）；含标记前缀的尾缓冲回退，兼容标记被拆进多个 chunk |
| `normalize_leaked_markup` | 正则 `[|｜]{2}DSML[|｜]{2}\s*` 去掉 DSML 命名空间前缀，归一化成 XML 形态 |
| `parse_textual_tool_calls` | 归一化后抽 `<invoke name>` / `<parameter name>`，按 `string="true/false"` 决定值类型；仅保留工具名在已注册集合内的调用 |

收尾时机：流结束时，**若无结构化 `tool_calls` 且发生过抑制**，把缓存文本解析成真实
`ToolCall` 事件，工具照常执行。

- 只接在 `OpenAIProvider` 一处，即覆盖 deepseek / dashscope 兼容 / moonshot / 智谱兼容 /
  openai 等所有走该 Provider 的厂商；Anthropic / Zai 原生 Provider 自带结构化工具块，不接入。
- 正文里正常的 `<`、HTML 标签不会误触发（信号是罕见的「全角竖线组合 `<｜｜`」）。

## 测试

- 单元：[tests/llm/test_text_tool_calls.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/llm/test_text_tool_calls.py)
  —— DSML/XML 两形态解析、值类型强转、valid_names 过滤、流式抑制与尾缓冲释放、标记跨块识别。
- 集成：[tests/llm/test_openai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/llm/test_openai_provider.py)
  `TestTextualToolCallFallback` —— mock 一个「content 带 DSML、无结构化 tool_calls」的流，
  断言解析出真实 ToolCall、前言正常回显、裸 DSML 不出现在任何 TextDelta；并断言正常尖括号/HTML
  不被误抑制。

样本一律用 `chr(0xFF5D)` 构造全角竖线，不依赖网络。（⚠ 这一写法本身就是错的：样本与
实现共用同一常量，错误码点被测试「合法化」，详见下方「二次补漏」。）

```powershell
python -m pytest tests/llm/test_text_tool_calls.py tests/llm/test_openai_provider.py -q   # 全绿
python -m pytest tests/ -q                                                                  # 1957 passed
```

## 补漏（2026-09-30）：Anthropic 协议路径同样泄漏

**现象**：终端（`jarvis`）与桌面壳（`jarvis-desktop`）同时把 DSML 裸码显示在回复正文里，
工具不触发（用户反馈「终端和桌面都不好使」）。`~/.jarvis/sessions/*.json` 的 `text` 字段
直接存着 `<｜｜DSML｜｜ invoke name="Bash">` 原文，排除是 UI 层渲染问题。

**根因**：用户当前模型 `deepseek-flash` 配置为 `provider_type = "anthropic"`、
`base_url = https://api.deepseek.com/anthropic` → 请求走 `AnthropicProvider`；而本次修复
只接在 `openai_provider.stream()`，Anthropic 路径的 `text_delta` 直接 `yield TextDelta`。
「Anthropic 原生 Provider 自带结构化工具块」的假设对该兼容端点不成立：DeepSeek 的
`/anthropic` 端点在「前言 + 调用」同轮时会把 DSML 当 `text` block 吐回来（不是 `tool_use`
block），于是工具永远不触发。

**修复**：把「流式过滤 + 泄漏解析」抽成 `TextualToolCallGuard`
（[text_tool_calls.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/text_tool_calls.py)，
避免两条协议路径各抄一遍），`AnthropicProvider.stream()` 三处接入：

| 位置 | 动作 |
|---|---|
| `text_delta` 增量 | 过 `guard.feed()`，正常文本即时回显，命中 `<｜｜` 后转缓存不回显 |
| 流末 | `guard.flush()` 释放尾缓冲里确定安全的文本 |
| 收尾 | 无结构化 `tool_use`（`content_blocks` 为空）时 `guard.drain()` → 真实 `ToolCall` |

`OpenAIProvider` 同步改用同一 guard（行为不变，仅去重复）。

**测试**：

- [tests/llm/test_anthropic_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/llm/test_anthropic_provider.py)
  `TestTextualToolCallFallback` —— DSML 端到端转真调用 / 结构化 `tool_use` 不重复 /
  正常 `<`·HTML 不误触发。
- [tests/llm/test_text_tool_calls.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/llm/test_text_tool_calls.py)
  `TestTextualToolCallGuard` —— 守卫组合行为（回显 / 抑制 / 解析 / 工具名校验）。

```powershell
python -m pytest tests/llm -q      # 438 passed
python -m pytest tests/ -q         # 2215 passed（全量回归）
```

## 二次补漏（2026-09-30 下午）：分隔符码点写错，兜底其实从未生效

**现象**：把 Anthropic 路径接上 guard 后，用户重启终端与桌面壳重测，**仍然原样吐出
DSML 裸码**，工具依旧不触发。

**排查**（关键转折）：不再猜路径，直接按码点核对真实存档：

```python
# 从 ~/.jarvis/sessions/auto-latest.json 取泄漏原文，打印标记附近字符
[(c, hex(ord(c))) for c in s[i - 5:i + 8]]
# → ('<', '0x3c'), ('｜', '0xff5c'), ('｜', '0xff5c'), ('D', '0x44') ...
# 而实现里的标记是 "<" + chr(0xFF5D) * 2  →  `marker in text` == False
```

**根因**：DeepSeek 特殊 token 的分隔符是 **U+FF5C（FULLWIDTH VERTICAL LINE）**，而实现从
第一次修复起就误用了 **U+FF5D（FULLWIDTH RIGHT PARENTHESIS）**——两者在终端里渲染几乎
一样，肉眼看不出来。更要命的是：**测试样本用同一个错误常量 `chr(0xFF5D)` 构造**，于是
「实现 + 测试一起绿」，线上从未命中过。上一轮「Anthropic 路径漏接」是真问题，但即使
接上了，错误的码点也会让它继续失效。

**修复**：

| 文件 | 改动 |
|---|---|
| [text_tool_calls.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/text_tool_calls.py) | 新增 `_PIPE_CHARS`（全角 U+FF5C + 历史假设 U+FF5D + 半角 U+007C 三种竖线），标记从字面量 `_LEAK_MARKER` 改为正则 `_LEAK_RE`；`_DSML_PREFIX_RE` 字符类同步扩展；`_partial_marker_len` 按「`<` / `<`+单竖线」两级回看重写 |
| 三个测试文件 | `P = chr(0xFF5D)` → `chr(0xFF5C)`；新增 `TestRealLeakSample`，把真实存档原文以 Unicode 转义写死，**独立于实现常量**，并保留一条 U+FF5D 兼容用例 |

**验证**：除单测外，另跑一次真实存档回放——把 `~/.jarvis/sessions` 里 3 条含 DSML 的原文
按 12 字符一块喂给 `TextualToolCallGuard`：

```text
emitted: '我来看看这个项目的结构。\n\n'   suppressed: True
calls: [('Bash', {'command': 'ls -la'}), ('Glob', {'pattern': '*.md'})]
```

前言正常回显、裸码全部抑制、工具调用正确还原（含桌面壳那条 `pwd && ls -la ..`）。

```powershell
python -m pytest tests/ -q   # 2219 passed
```

**经验总结**：

- **跨码点 / 不可见字符的匹配逻辑，必须用真实数据按码点核对**，不能凭渲染效果手写常量——
  U+FF5C 与 U+FF5D 在终端里看起来一模一样。
- **测试样本不能引用实现里的同一个常量**，否则错误常量会被测试「合法化」，形成自证闭环；
  锚点测试应以字面转义（`\uff5c`）写死真实数据。
- 修复后仍复现时，先做「真实数据回放」验证，再怀疑是否有第二处漏网（本次两者都有）。

## 三次补漏（2026-09-30 晚）：桌面端「思考完就停止」

**现象**：码点修好后终端完全正常（🔧 Bash 正常出结果），但**桌面壳做不到**——第一回合只
输出一句「我来看看当前目录的情况。」就结束，第二回合连正文都没有，只剩思考过程。

**取证**：读桌面会话存档 `~/.jarvis/sessions/贾维斯识别当前目录项目.json`：

```text
[1] assistant blocks=['thinking', 'text']
      - text: "我来看看当前目录的情况。\n\n"   ← DSML 已被吞（码点修复生效），但没有 tool_use
[3] assistant blocks=['thinking']                ← 整轮只剩思考
```

即「裸码不显示了，可工具也没执行」——比原来的乱码更难察觉。

**根因（两处叠加）**：

1. **纯聊天检测误判**：`_is_chat_only` 的规则是「≤ 20 字 + 不含动作关键词 + 历史无工具调用」，
   而关键词表里没有「目录 / 项目 / 当前」这类工作区与环境指代词。桌面新会话第一句
   「jarvis当前目录是一个什么项目」（17 字）因此被判为纯聊天 → `_build_tool_defs` 返回 `[]`
   → **本轮一个工具 schema 都不发给模型**。终端不复现，是因为终端会话历史里已有 tool_use，
   `has_tool_use=True` 直接跳过判定。
2. **兜底静默丢信息**：模型没收到工具清单，却凭训练习惯（和系统提示里的工具描述）照样吐出
   DSML。`TextualToolCallGuard` 的 `valid_names` 取自本轮 tools，此时是空集合 →
   `parse_textual_tool_calls` 把解析出的调用**全部过滤掉**；而 `StreamingLeakFilter` 已经把
   裸码从正文吞了。既不显示、也不执行、也不报错 → 表现为「思考完就停止」。

**修复**：

| 文件 | 改动 |
|---|---|
| [query_loop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py) | `_is_chat_only` 关键词表补两类：工作区/代码库词（目录、项目、仓库、工程、代码、结构、路径、分支、提交、依赖、日志、报错、git、repo、readme 等）与环境指代词（当前、现在、这个、这里、这是、本地、机器）；docstring 注明「误判即 0 工具，宁全勿缺」 |
| [text_tool_calls.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/text_tool_calls.py) | `drain()` 结果缓存；新增 `rescue_text` 属性——发生过抑制却一个调用也没解析出来时，返回被吞的原文 |
| [anthropic_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/anthropic_provider.py) / [openai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/openai_provider.py) | 流末 `drain()` 为空时把 `rescue_text` 作为 `TextDelta` 补发：**宁可看到裸码，也不能让信息凭空消失** |

**测试**：

- [tests/test_query_loop_stream.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/test_query_loop_stream.py)：
  断言「jarvis当前目录是一个什么项目」在新会话下 `_is_chat_only` 返回 False。
- [tests/llm/test_text_tool_calls.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/llm/test_text_tool_calls.py)：
  `rescue_text` 四种组合（解析成功 / 工具名不匹配 / 空工具集 / 未泄漏）。
- [tests/llm/test_anthropic_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/llm/test_anthropic_provider.py)
  `test_leak_without_tool_schema_is_not_swallowed` —— `tools=[]` 时既不产生 ToolCall，
  也必须让原文出现在 TextDelta 里。

```powershell
python -m pytest tests/ -q   # 2222 passed
```

**经验总结**：

- 「修好 A 之后 B 才暴露」是常态：码点修好后裸码被成功吞掉，反而把「工具清单为空」这个更
  上游的缺陷以静默形式显现。定位时要问「信息去哪了」，而不是只看「乱码没了」。
- 兜底逻辑必须遵守**不丢信息**契约：任何“抑制/过滤”都要有失败回退路径，否则故障从「难看」
  升级为「不可见」。
- 桌面与终端共用 QueryLoop，但**会话状态不同**（历史里有无 tool_use）会让同一句话走出完全不
  同的工具清单——跨客户端复现差异要优先怀疑这类上下文相关的分支。

**后续建议（本次未做）**：`chat_detection` 命中 0 工具时，系统提示词里仍写着可用工具，
提示词与工具清单不一致是 DSML 泄漏的诱因之一；若要根治需按轮生成提示词，但会牺牲 prompt
缓存命中率，需单独评估。

## 四次补漏（2026-10-22）：「把 a.txt 改成 alpha2」仍被误判，词表补到头了

**现象**：W3 体验走查的场景 1（`/away` 后「把 a.txt 改成 alpha2」）根本没走到 ASK 与
待批清单，终端直接回显 DSML 裸码后就结束：

```text
我先确认一下 `a.txt` 的情况。

<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="Glob">…</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>
```

**取证**：读会话存档 `~/.jarvis/sessions/把-atxt-改成-alph-2.json`（model=deepseek-flash），
`text` 字段里就是那串原文；本地直接跑判定函数（不花 token）：

```python
is_chat_only(_ctx_with(_user("把 a.txt 改成 alpha2")))   # → True（本轮 0 工具）
is_chat_only(_ctx_with(_user("重命名 a.txt")))             # → True
is_chat_only(_ctx_with(_user("把 report.md 删掉")))        # → False（只因「删」字命中）
```

**根因**：与「三次补漏」同类，但词表已补不动了。那句话 17 字，旧词表**一个都没命中**：
书面词有「修改/删除/编辑」，而用户说的是**「改成」**；工作区词表只收了名词（目录/项目/
代码），没说「把一个文件怎么看」这种口语动词组合。只要判定依赖“枚举自然语言说法”，
这类漏判就会无限复发。前三次都在堆词，方不对。

**修复**（[tool_dispatch.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/tool_dispatch.py)）：
不再只堆词，而是加一道**不依赖词表的结构判据**，闭掉整类：

| 判据 | 内容 | 取向 |
|---|---|---|
| `_FILE_TOKEN_RE` | `[A-Za-z0-9_汉字-]+\.[A-Za-z][A-Za-z0-9]{0,7}` —— `a.txt`、`纪要.md`、`weekly.txt` | 宽松（允许汉字在左侧） |
| `_PATH_TOKEN_RE` | `[A-Za-z0-9_.\-~][/\\][A-Za-z0-9_.\-]` —— `e:/lab/a.txt`、`C:\work`、`~/.jarvis` | 收紧（**两侧只认 ASCII**，否则「今天/明天」「和/或」这类散文全被当任务） |
| `ACTION_KEYWORDS` 补类 | 改成、改为、改名、重命名、换成、变成、挪到、移到、复制到、追加、清空、存成、保存、生成、新建、删掉、去掉、回退、撤销、看下、看看 | 口语动词一类，结构判据之外的第二层 |

方向仍是“宁多发不可漏发”：误伤一句问候的代价是几百 token，漏判的代价是整轮卡死。
上一轮加的 `rescue_text` 本身不改——本次界面上能裸码而不是静默结束，恰好说明它按契约
工作了（宁可看到裸码也不能丢信息）。

**测试**（[tests/test_tool_dispatch.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/test_tool_dispatch.py)，新增 3 例）：

- `test_change_verb_on_filename_is_not_chat_only` —— 用户原话与同类短句均不得判纯聊天；
- `test_structural_file_token_works_without_keyword` —— 结构判据独立生效，并钉住两个
  **不应命中**的反例（`3.14`、`今天/明天`）；写这一例是因为第一版路径正则把汉字也收在两侧，
  实跑当场被「今天/明天」打回，才改成只认 ASCII 邻右；
- `test_pure_chat_not_overtriggered_by_structural_rule` —— 真问候仍为零工具。

```powershell
python -m pytest tests/test_tool_dispatch.py tests/test_query_loop_stream.py -q   # 全绿
python -m pytest -q                                                               # 3208 passed
```

**仍未根治的诱因**（记下未做）：`chat_detection` 命中 0 工具时，系统提示词里仍列着可用
工具，“提示词说能用 / 清单里没有”这个不一致依旧存在（上轮已记）。彻底做法是按轮生成提示词，
代价是牺牲 prompt 缓存命中率；本次只把入口误判率压下去，没碰提示词。

**给测试者的一句话**：同一个会话历史里已经存了 DSML 原文，会被当“文本态调用的示范”
回灌给模型——修完请**另起新会话**重测场景 1；真要彻底关掉这条判定用
`[tools] chat_detection = false`（或 `JARVIS_TOOLS_CHAT_DETECTION=0`）。

# 五次补漏（2026-10-29）：「看一下吧」→ 整轮零工具，又一堆裸码

**现象**：语音模式下：

```
🧑 你说: 看一下吧。
（思考过程里模型纠结：“工具列表里 available-deferred-tools 有 ScreenShot”，
  但“我没看到 ToolSearch 在工具列表里”）
<｜｜DSML｜｜ calls>
<｜｜DSML｜｜ invoke name="ScreenShot">
</｜｜DSML｜｜ invoke>
</｜｜DSML｜｜ calls>
🧑 你说: 你说什么呢，兄弟。
（又来一遍）
```

用户原话：「jarvis 这是又犯病了」。

### 根因一：词表又漏了一个字

```
is_chat_only('看一下吧')   → True      用户原话
is_chat_only('看一下屏幕') → True      明确要看屏幕
is_chat_only('看屏幕')     → True
is_chat_only('截个屏')     → True
is_chat_only('看看')       → False     ← 词表里有「看看」「看下」
```

`ACTION_KEYWORDS` 只有「看下 / 看看」，差一个字就漏。和前四次一样。

### 根因二（真正要命的）：`return []` 把 ToolSearch 也一起砍了

`build_tool_defs` 纯聊天命中后直接返回**空列表**。ToolSearch 虽然 `deferred=False`
（核心工具），也在这刀底下没了。于是本轮请求处于一个**自相矛盾**的状态：

| 位置 | 内容 |
|---|---|
| 系统提示词 | “以上为延迟加载工具，用 ToolSearch 搜索关键词加载完整定义后即可调用” + `available-deferred-tools: ScreenShot` |
| 本轮 tools | **空**——既无 ScreenShot，也无 ToolSearch |

模型唯一能做的就是凭训练习惯把调用写成文本。而这正是模型在思考里反复纠结的地方
（“工具列表里没有 ToolSearch 定义”“让我尝试直接调 ScreenShot 看看”）。

### 为什么前四次都没修到点上

四次补漏都在**堆词**。但 `return []` 这个设计把“省 token”的手段实现成了
“**完全无工具**”——后者是一个**不可恢复的故障态**：模型没有自救手段，
只能泄漏；泄漏又被 `valid_names` 全量过滤；最后由 `rescue_text` 补回裸码。
只要判定漏一次，整轮就废一次。

### 修复（改成结构性的，不再只是补词）

| # | 位置 | 改动 |
|---|---|---|
| 1 | [tool_dispatch.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/tool_dispatch.py) | 纯聊天命中不再 `return []`，改为 `_safety_defs()`：只发 **ToolSearch + 已发现的延迟工具** |
| 2 | 同上 | 词表补视觉类口语：`看一下 / 看一眼 / 看眼 / 看屏幕 / 看一下屏幕 / 屏幕上 / 截个图 / 截屏 / 截个屏 / 屏幕截图 / 画面 / 瞅` |
| 3 | [base.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/base.py) 及 5 个 provider | `stream()` 新增 `known_tool_names`：**库内真实存在**的全部工具名 |
| 4 | [openai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/openai_provider.py) / [anthropic_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/anthropic_provider.py) | 兜底校验取 `本轮tools ∪ known_tool_names` |
| 5 | [query_loop.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/core/query_loop.py) | 传 `known_tool_names={t.name for t in registry.all()}` |

两条防线分层，职责不同：

- **`tools` 决定模型「看得见」什么**（省 token 靠它）；
- **`known_tool_names` 决定泄漏的调用「能不能被救回来执行」**
  （即使模型没先 ToolSearch，直接写出 `ScreenShot`，也能当次就正常执行）。

修复后的行为：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 纯聊天误判 | 0 工具 → 只能泄漏 → 裸码 | 仅 ToolSearch → 模型自己 `ToolSearch("截图")` 把工具捞回来 |
| 模型跳过 ToolSearch 直接写 `ScreenShot` | valid_names 空 → 全过滤 → 裸码 | valid_names 含 ScreenShot → **直接执行成功** |
| 模型瞎编一个不存在的工具 | 过滤 → rescue_text 补裸码 | 仍然过滤（不让它凭空造工具） |

### 测试

- [tests/test_tool_dispatch.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/test_tool_dispatch.py) 新增 3 例：
  - `test_look_at_screen_is_not_chat_only` —— 「看一下吧」「看屏幕」「截个屏」等 11 种说法都不得判纯聊天；
  - `test_chat_turn_still_sends_tool_search` —— 纯聊天轮必须留 ToolSearch，但普通核心工具仍被省掉；
  - `test_chat_turn_keeps_already_discovered_tools` —— 已发现的延迟工具在下一句闲聊里不能丢。
- [tests/llm/test_openai_provider.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/llm/test_openai_provider.py) 新增 2 例：
  - `test_undeclared_tool_rescued_by_known_names` —— 本轮 `tools=[]`，但 `known_tool_names` 含 ScreenShot → 必须产生真 ToolCall 且不吐裸码；
  - `test_unknown_tool_still_filtered` —— 库内也不存在的工具名仍过滤，并靠 rescue_text 补原文。
- 同步更新测试桩：`tests/_query_loop_fakes.py`、`tests/test_agent_loop_guard.py`、
  `tests/test_query_loop_stream.py`、`tests/ui/test_abort_latency.py` 的 `stream()` 补 `known_tool_names` 参数。

```powershell
python -m pytest tests -q   # 3878 passed
```

### 经验总结

- **“省 token”的实现不能以“进入不可恢复故障态”为代价。** 纯聊天优化的本意只是
  不发重型 schema，却做成了 0 工具；模型一旦想用工具就没有任何办法。现在留一个
  约 250 token 的 ToolSearch，就把“误判”从“整轮报废”降级为“多一次搜索”。
- **同一个 bug 修了五次，说明修错了层级。** 前四次都在词表（数据）上打补丁，
  而真正的缺陷在 `return []`（结构）上。判断标准：**如果同样的现象换了说法就
  重现，那被修的一直是症状。**
- **“看得见什么”和“能不能用”是两个维度。** `tools` 管前者，`known_tool_names`
  管后者；合并成一个集合会让省 token 与可用性互相绑架。
- 模型在思考过程里说的抱怨（「我没看到 ToolSearch 在工具列表里」）本身就是
  根因线索——它比最终输出更早知道系统哪里不一致。

---

## 边界

- DSML 块之后若还有正文（罕见，泄漏轮通常以调用结尾），会被一并吞入缓存不显示——可接受；
  但若兜底一个调用也没解析出来，被吞的原文会通过 `rescue_text` 补回显，不会静默丢失。
- 该兜底是「解析补偿」，不改变「优先信任结构化 tool_calls」的主路径；结构化正常时过滤器
  不介入。
- 已被污染的会话历史（`~/.jarvis/sessions/*.json` 里躺着 DSML 原文）会把「文本态工具调用」
  当示范回灌给模型，可能诱发继续照抄；复现过的会话建议弃用、另起新会话。
