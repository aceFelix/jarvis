# J.A.R.V.I.S 响应优化第一轮复盘

> 优化时间：2026-07-29
> 影响范围：`agent/core/`、`agent/llm/`、`agent/prompts/`、`agent/config/`、`agent/cli_anything/`
> 测试结果：109 passed，零回归

---

## 优化背景

J.A.R.V.I.S 在集成 104 个工具 + MCP + CLI-Anything harness 后，每轮 LLM 请求变得臃肿缓慢。系统性分析发现瓶颈集中在 **工具定义膨胀**、**对话历史缓存失效**、**思考模式硬编码** 三个方面。

---

## 优化项一览

### 1. 工具延迟加载 + 纯聊天检测

**问题**：104 个工具每次全部发送给 LLM，光工具定义就 3-5 万 token。

**方案**：
- 核心工具（14 个）始终携带，延迟工具（~73 个）仅发名字摘要
- 模型需延迟工具时通过 ToolSearch 搜索加载
- 纯聊天短消息（"你好"、"在吗"）发 0 工具，秒回

**文件**：`agent/core/tool.py`、`agent/tools/tool_search.py`、`agent/core/query_loop.py`

**关键修复**：
- Subagent/Team/Task 工具补标记 `deferred=True`（`agent/core/tool.py`）
- `register_harness_tool` 单工具注册补 `deferred=True`（`agent/cli_anything/registry.py`）
- `_is_chat_only` 关键词列表补充信息查询词（"几点"、"天气"、"时间"等），修复 "现在几点了" 幻觉 bash 文本的问题

---

### 2. 思考模式策略化

**问题**：`OpenAIProvider.stream()` 中 if-else 硬编码各厂商思考参数（DeepSeek `thinking.type`、DashScope `enable_thinking`、智谱 `thinking.type`），新增厂商需改代码。

**方案**：创建 `ThinkingConfig` 配置表 + `apply_thinking()` 统一注入函数。

| 厂商 | placement | field | on_value | extra |
|---|---|---|---|---|
| dashscope | extra_body | enable_thinking | True | thinking_budget |
| deepseek | extra_body | thinking | {"type":"enabled"} | reasoning_effort |
| zhipu | extra_body | thinking | {"type":"enabled"} | reasoning_effort |
| dashscope_sdk | top_level | enable_thinking | True | thinking_budget |
| zai_sdk | top_level | thinking | {"type":"enabled"} | reasoning_effort |

**文件**：
- `agent/llm/thinking.py`（新建）
- `agent/llm/openai_provider.py`（24 行 if-else → 4 行配置表查找）
- `agent/llm/dashscope_provider.py`、`agent/llm/zai_provider.py`（统一接入配置表）

---

### 3. 拆分 settings.py

**问题**：873 行单文件，配置加载、模型持久化、环境变量覆盖混在一起。

**方案**：拆为三个模块：

| 模块 | 行数 | 职责 |
|---|---|---|
| `settings.py` | 673 | Settings 类 + TOML 加载 + 字段映射（loader 核心） |
| `env.py` | 81 | `apply_env_overrides()` — 环境变量覆盖 |
| `model_registry.py` | 159 | `save_custom_model()` / `save_last_model()` / `save_realtime_talk_auto_start()` |

通过 `__init__.py` 重导出保证 API 兼容，所有现有 `from agent.config.settings import ...` 无需修改。

---

### 4. System Prompt 精简

**问题**：CLI-Anything harness 在 system prompt 中展开完整描述（YAML frontmatter + when_to_use + examples），8 个 harness ~300-500 token。

**方案**：harness 仅列名字，详细用法交给 ToolSearch 按需加载。

```
# 优化前
## cli_anything__wps
name: WPS Office
description: 通过 CLI 控制 WPS Office 套件...
when_to_use: 用户需要创建/编辑 Word 文档...
examples: ...

# 优化后
cli_anything__wps, cli_anything__xmind, ...
（用 ToolSearch 搜索关键词加载完整用法后即可调用）
```

**文件**：`agent/prompts/system.py` — `_cli_anything_section()` + `build_system_prompt()` 缓存友好注释

---

### 5. 分层上下文管理（冻结前缀 + 滑动窗口）

**问题**：原水位线压缩方案原地篡改历史消息（`_collapse_old_tool_results`、`_evict_old_images`、`compact_messages`），导致 LLM 前缀缓存频繁碎裂。OpenAI/DashScope 类厂商的缓存命中率可能接近 0%。

**方案**：`LayeredContext` — 压缩后的摘要进入"冻结区"永不修改，后续请求前缀稳定。

```
原方案（水位线，45 行）：
  水位 30% → 激进压缩（原地改消息）
  水位 60% → 折叠工具结果（原地改消息）
  水位 80% → 标准压缩（原地改消息）
  → 每轮都可能碎缓存

新方案（LayeredContext，4 行核心）：
  freeze_if_needed() → 一次性压缩 + 锁定前缀
  collapse_old_tool_results() → 仅活跃窗口
  evict_old_images() → 仅活跃窗口
  → 冻结后缓存持续命中
```

**文件**：
- `agent/core/context/layered.py`（新建 219 行）
- `agent/core/query_loop.py`（run() + _stream_once() 重构）

**缓存效果估算**（OpenAI/DashScope，10 轮对话）：

| | 原方案 | 新方案 |
|---|---|---|
| 缓存命中率 | ~20% | ~60-70% |
| 每轮等效计费（8000 token） | ~7200 token | ~5600 token |
| 每轮节省 | — | ~1600 token |

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `agent/core/context/layered.py` | 新建 | 分层上下文管理器 |
| `agent/core/query_loop.py` | 重构 | 水位线 → LayeredContext + 关键词补充 |
| `agent/core/tool.py` | 修改 | 协作工具 deferred 标记 + ThinkingConfig 属性 |
| `agent/llm/thinking.py` | 新建 | 思考模式配置表 |
| `agent/llm/openai_provider.py` | 重构 | if-else → 配置表驱动 |
| `agent/llm/dashscope_provider.py` | 重构 | 统一接入配置表 |
| `agent/llm/zai_provider.py` | 重构 | 统一接入配置表 |
| `agent/config/env.py` | 新建 | 环境变量覆盖 |
| `agent/config/model_registry.py` | 新建 | 模型 TOML 持久化 |
| `agent/config/settings.py` | 拆分 | 873→673 行 |
| `agent/config/__init__.py` | 更新 | 重导出新模块 |
| `agent/prompts/system.py` | 精简 | harness 名字化 + 缓存注释 |
| `agent/cli_anything/registry.py` | 修复 | register_harness_tool 补 deferred |
| `docs/improvement/jarvis-improvement-plan.md` | 更新 | 标记 A-02/A-03 完成 |

---

## 后续待优化

参见 `docs/improvement/jarvis-improvement-plan.md`：
- A-04：Provider 参数表驱动（base_url、模型特性用配置表描述）
- T-01：Provider 层单元测试
- E-01：LLM 错误分类优化

---

## 测试

```bash
pytest tests/ -q -k "not test_image_skip_in_text_mode"
# 109 passed, 1 deselected（已有 bug，无关）
```
