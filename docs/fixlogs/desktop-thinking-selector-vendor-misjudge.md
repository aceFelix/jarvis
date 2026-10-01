# 桌面思考选择器置灰修复记录（自定义模型厂商误判）

## 问题概述

桌面 jarvis 输入区的「思考强度」选择器长期显示为灰化的「关闭思考」，无法点开、
无法选择任何档位。用户当前模型为 `deepseek-flash`，终端 `/think` 一切正常，但桌面
选择器却是死的。

## 现象

- 桌面输入区「关闭思考 ▼」置灰，点击无任何反应
- 同一份配置下，终端 jarvis 横幅显示 `provider deepseek`，`/think` 可正常开关与调档
- 桌面能正常对话（说明后端配置、密钥均有效，非连接问题）

## 排查过程

1. **前端链路核对**：`ChatArea.tsx` 中 `thinkingDisabled = thinkingSupported.length === 0`，
   即选择器置灰当且仅当后端上报的 `thinking_supported` 为空。`parseRuntimeState`
   对缺失字段也降级为 `[]`。前端解析、`THINKING_EFFORTS` 白名单、`refreshState`
   触发时机均正常。
2. **后端逻辑核对**：`api._thinking_levels()` → `supported_efforts(lookup_thinking_key(vendor))`，
   对 `vendor="deepseek"` 实测返回 `['off','low','medium','high']`，非空。
3. **进程/环境核对**：确认运行中的 serve 进程（`python -m agent.serve`）启动时间晚于
   代码改动，且所用解释器 `D:\Program\Python\Python313\python.exe` 加载的是仓库最新
   editable 代码——排除旧进程、环境偏差。
4. **锁定真因**：用该解释器执行 `load_settings()` 打印实际字段：

   ```
   provider   = 'anthropic'      ← 不是 deepseek！
   model      = 'deepseek-flash'
   api_format = 'anthropic'
   levels     = []               ← 空 → 选择器置灰
   ```

## 根因

用户的历史自定义模型段（`~/.jarvis/models.toml`）形如：

```toml
[llm.custom_models."deepseek-flash"]
base_url      = "https://api.deepseek.com/anthropic"
provider_type = "anthropic"     # 传输协议（Anthropic 兼容端点）
vendor        = "deepseek"      # 真实厂商（支持思考）
# 没有 provider 键
```

`settings._apply_toml` 归一化自定义模型时，`provider` 键缺失就直接用 `api_format`
兜底：

```python
if "provider" not in c:
    c["provider"] = c.get("api_format", "openai")   # 丢掉了 vendor！
```

于是 `provider` 被填成传输协议 `anthropic`，而 `anthropic` 不在 `THINKING_CONFIGS`
里 → `supported_efforts("anthropic")` 返回 `[]` → 桌面选择器按设计置灰。

> 关键对照：`AnthropicProvider._derive_name(base_url)` 对含 `deepseek` 的 URL 返回
> `"deepseek"`，所以**终端横幅**（读 provider 实例的派生名）显示正确；而**桌面**
> `state.get` 的 `provider` 读的是 `settings.provider` 字段（被误置为 anthropic），
> 两者数据源不同，才出现「终端对、桌面错」的割裂。

## 修复

厂商（`provider` 字段，用于按厂商判定思考能力）缺失时，优先取 `vendor`，再退回
`api_format`（传输协议）。两处同口径：

**`agent/config/settings.py`**

| 位置 | 变更 |
|------|------|
| `_apply_toml` 自定义模型归一化 | `c["provider"] = c.get("vendor") or c.get("api_format", "openai")` |
| `last_model` 恢复段 | `provider = cfg.get("provider") or cfg.get("vendor") or api_fmt` |

修复后实测：`provider='deepseek'`、`api_format='anthropic'`（传输协议正确保留）、
`levels=['off','low','medium','high']`。

## 生效方式

⚠️ 已在运行的 serve 进程在启动时已把 `settings.provider` 算成 `anthropic` 并缓存，
改代码不影响存活进程。**必须彻底退出桌面（托盘 → 退出，关闭窗口只会隐藏、不杀进程）
再重新打开**，让主进程重新拉起 serve，才会加载修正后的归一化逻辑。

## 测试

- 新增 `tests/llm/test_config_loading.py::test_llm_custom_models_provider_prefers_vendor`：
  复现 provider_type=anthropic + vendor=deepseek 无 provider 键，断言归一化后
  `provider=="deepseek"` 且思考档位非空。
- 更新 `tests/config/test_models_config.py::test_last_model_restores_custom_model_from_models_file`：
  原断言 `provider=="anthropic"` 编码的是旧（错误）行为，改为 `provider=="deepseek"`。
- 全量回归：`pytest -q` → **2259 passed**。

## 补充说明

`deepseek-flash` 走 Anthropic 兼容端点时，`AnthropicProvider` 对 DeepSeek 类端点
仅支持思考**开/关**（`thinking={"type":"enabled/disabled"}`），low/medium/high 档位
不改变实际下发参数。选择器点亮后，开关一定生效；三档强度对原生 OpenAI 协议的
deepseek 端点才有区分意义。

## 相关代码索引

- [`settings.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/config/settings.py) — `_apply_toml` 归一化、`last_model` 恢复
- [`api.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/ui/workbench/api.py) — `_thinking_levels` / `get_state`
- [`thinking.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/thinking.py) — `THINKING_CONFIGS` / `supported_efforts`
- [`anthropic_provider.py`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/llm/anthropic_provider.py) — `_derive_name`（deepseek 兼容端点派生名）
- [`ChatArea.tsx`](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis-desktop/src/renderer/src/components/ChatArea.tsx) — 选择器置灰判定
