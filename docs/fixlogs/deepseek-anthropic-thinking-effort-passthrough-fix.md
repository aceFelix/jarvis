# deepseek 走 anthropic 协议时思考强度档位不下发修复

- 日期：2026-10-03
- 仓库：jarvis
- 作者：aceFelix

## 一、问题现象

桌面工作台「思考强度」选择器切到 思考·中 / 思考·高 后，模型的思考输出
几乎没有区别。用户反馈："高强度和中等强度，思考输出的文字差不多"。

## 二、排查过程

1. 档位下发链路：桌面选择器 → `think.set` → `engine._handle_set_thinking`
   → `QueryLoop.set_thinking_effort` → `provider.set_thinking_effort`，
   最终在 `AnthropicProvider.stream()` 请求时翻译为厂商参数；
2. 用户当前模型 `deepseek-flash` 走 **anthropic 兼容协议**
   （`provider_type="anthropic"`，`base_url` 含 deepseek → `name == "deepseek"`）。
   `stream()` 中该分支只注入 `thinking={"type":"enabled"}`，注释写着
   "DeepSeek 等兼容端点仅支持开/关，档位不改变参数"——**低/中/高发出的
   请求完全相同**，只有 `off`（显式 `disabled`）真正生效；
3. 而桌面选择器仍显示 低/中/高，是因为 `api._thinking_levels` 按厂商 key
   （`deepseek`）查 `THINKING_CONFIGS`（OpenAI 兼容路径的配置表）判定支持
   强度档位，没有区分实际走的协议路径——UI 能力与运行路径口径不一致；
4. 对照 DeepSeek 官方 Anthropic 兼容文档（api-docs.deepseek.com）：
   `thinking.budget_tokens` **is ignored**，但 `output_config` 的
   **effort 字段受支持**（档位 low/high/max，medium 官方映射为 high）——
   "兼容端点仅支持开/关"的旧结论已过时。

## 三、根因

`AnthropicProvider.stream()` 对 deepseek 兼容端点**没有透传思考强度**：
档位只记录在 `_thinking_effort`，构造请求时被丢弃，中/高发出同样的请求。

## 四、修复方案

[anthropic_provider.py](../../agent/llm/anthropic_provider.py) `stream()`
兼容端点分支：`name == "deepseek"` 且有强度档位时，注入顶层
`output_config={"effort": ...}`，映射与项目 OpenAI 路径
`THINKING_CONFIGS["deepseek"].effort_map` 同口径：

| 桌面档位 | output_config.effort |
|---|---|
| low | `low` |
| medium | `high`（DeepSeek 无 medium，官方映射） |
| high | `max` |
| on / 其余兼容端点 | 不发（用端点默认力度） |

anthropic 原生端点维持 `thinking.budget_tokens`（low 1024 / medium 4096 /
high 10000）不变；`off` 仍显式 `thinking={"type":"disabled"}` 且不带档位。
anthropic SDK 0.111.0 `messages.create` 原生支持 `output_config` 参数
（已用签名核实）。

## 五、认知补充：思考强度 ≠ 思考字数

即使档位正确下发，"中/高思考文字差不多"也不会必然消失：档位/预算是
**上限与意愿**，不是保证字数。简单问题模型用不满预算，档位差异主要体现在
复杂推理任务的正确率与耗时上，而非可见思考文本的长度。

## 六、验证

- 新增 [`tests/llm/test_anthropic_provider.py::TestThinkingEffortInjection`](../../tests/llm/test_anthropic_provider.py)
  4 用例：deepseek 三档映射、`on` 不发档位、`off` 不泄漏残留档位、
  anthropic 原生仍走 budget_tokens——单文件 33 用例全过；
- 全量回归 `uv run pytest tests -q`：**2335 passed**；
- 文档同步：[docs/architecture/03-LLMProvider层.md](../architecture/03-LLMProvider层.md)
  Anthropic Provider 小节新增档位下发说明与代码示例。

## 七、已知边界

- `anthropic_compatible`（未知第三方兼容端点）不发档位：`output_config`
  支持情况无文档依据，保守维持开/关；
- UI 能力口径（`_thinking_levels` 按厂商查表）本次未动：deepseek 现在
  两条协议路径都真正支持档位，选择器展示与实际能力已重新一致。
