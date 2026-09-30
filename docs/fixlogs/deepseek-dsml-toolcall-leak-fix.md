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

## 边界

- DSML 块之后若还有正文（罕见，泄漏轮通常以调用结尾），会被一并吞入缓存不显示——可接受；
  但若兜底一个调用也没解析出来，被吞的原文会通过 `rescue_text` 补回显，不会静默丢失。
- 该兜底是「解析补偿」，不改变「优先信任结构化 tool_calls」的主路径；结构化正常时过滤器
  不介入。
- 已被污染的会话历史（`~/.jarvis/sessions/*.json` 里躺着 DSML 原文）会把「文本态工具调用」
  当示范回灌给模型，可能诱发继续照抄；复现过的会话建议弃用、另起新会话。
