# 画像提炼鉴权失败修复（profile-refine-auth-fix）

> 日期：2026-08-27 ｜ 作者：aceFelix ｜ 影响范围：`agent/core/memory/profile_refiner.py` + 用户级 `[memory.refine]` 配置

## 现象

会话触发画像提炼时报错：

```
画像提炼失败: ProviderError: 原始错误: [invalid_request_error] Authentication Fails,
Your api key: ****Yftw is invalid
```

主对话模型（deepseek-v4-flash）一切正常，只有后台提炼失败。

## 根因

1. **提炼用独立模型**：画像提炼不走主对话模型，而是用 `~/.jarvis/settings.toml`
   `[memory.refine]` 配置的独立便宜模型——当时配的是 `qwen3.7-flash`
   （DashScope）+ 硬编码 key `...Yftw`。该 DashScope key 失效后提炼全部鉴权失败。
2. **key 回退逻辑有坑**：原 `_build_refine_provider` 在 `[memory.refine]`
   留空 api_key 时直接回退主 LLM key——主 key 常属于别的厂商（如主模型是
   DashScope 而提炼想用 DeepSeek），直接回退必然拿错 key 鉴权失败。

## 修复

### 代码（`agent/core/memory/profile_refiner.py`）

1. **api_key 四级解析链**（留空逐级回退）：
   `[memory.refine]` 显式配置 → 按厂商查环境变量（新增
   `_resolve_api_key_from_env`：deepseek→DEEPSEEK_API_KEY 等，已知厂商
   只认专属变量，不乱拿通用变量）→ 同名自定义模型的 api_key → 主 LLM key。
2. **协议 / base_url 继承自定义模型**：`[memory.refine].model` 与自定义模型
   同名时，留空的 `api_format` / `base_url` 从该自定义模型配置继承
   （与 `/model` 切换自定义模型的取值逻辑一致，兼容旧字段名
   `provider_type` / `vendor`）。

### 用户配置（`~/.jarvis/settings.toml`，脚本 `scripts/fix_refine_to_deepseek.py`）

```toml
[memory.refine]
api_format = "anthropic"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com/anthropic"
api_key = ""                       # 留空 → 走 DEEPSEEK_API_KEY 环境变量
```

原文件备份：`settings.toml.bak-refine`。

## 测试

新增 `tests/core/test_refine_provider.py`（10 用例）：环境变量按厂商取值
（专属优先/不乱拿通用/大小写）、key 解析链四级优先级、协议与 base_url
继承、无独立配置回退主 LLM、mock 跳过。

回归：`tests/core/test_profile_memory.py`（36 用例）全部通过。

真实链路冒烟（实际调用提炼模型确认鉴权）：

```powershell
Set-Location e:\2.MyProjects\MyAgentChat\J.A.R.V.I.S\jarvis
python scripts/smoke_refine_provider.py   # 已通过：AnthropicProvider 鉴权成功
```

## 验证方式

1. 确保环境变量 `DEEPSEEK_API_KEY` 已配置；
2. 重启 jarvis，聊几句后触发提炼（`/memory refine` 可立即手动提炼）；
3. `/memory` 查看画像条目有新增，且日志不再出现鉴权失败。
