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
   （DSML，`｜` 为全角竖线 U+FF5D）。正常由 API 服务端拦截并转成 OpenAI 结构化
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

样本一律用 `chr(0xFF5D)` 构造全角竖线，不依赖网络。

```powershell
python -m pytest tests/llm/test_text_tool_calls.py tests/llm/test_openai_provider.py -q   # 全绿
python -m pytest tests/ -q                                                                  # 1957 passed
```

## 边界

- DSML 块之后若还有正文（罕见，泄漏轮通常以调用结尾），会被一并吞入缓存不显示——可接受。
- 该兜底是「解析补偿」，不改变「优先信任结构化 tool_calls」的主路径；结构化正常时过滤器
  不介入。
