# 模型与 Provider

> [修复与复盘日志索引](../README.md) 的第 2 类，共 13 篇。
> 新增一篇时在本表补一行；文件名与路径保持 `<主题>-<简短描述>-fix.md`。

| 文件 | 主题 |
|---|---|
| [add-zhipu-zai-provider.md](add-zhipu-zai-provider.md) | 新增智谱官方 ZhipuAi SDK Provider |
| [deepseek-anthropic-thinking-effort-passthrough-fix.md](deepseek-anthropic-thinking-effort-passthrough-fix.md) | DeepSeek 走 Anthropic 协议时思考档位不下发 |
| [deepseek-dsml-toolcall-leak-fix.md](deepseek-dsml-toolcall-leak-fix.md) | DSML 工具调用泄漏进正文的兜底 |
| [glm-tool-call-hang-fix.md](glm-tool-call-hang-fix.md) | 智谱 GLM 工具调用后无响应 |
| [provider-busy-error-classification-fix.md](provider-busy-error-classification-fix.md) | 厂商 503 引出六处缺陷（分类/日志/面板等） |
| [shared-http-pool-close-kills-chat.md](shared-http-pool-close-kills-chat.md) | 临时 provider 关掉共享连接池，第二句必挂 |
| [model-config-split.md](model-config-split.md) | 模型配置拆分到 `models.toml` |
| [model-edit-hot-reload-fix.md](model-edit-hot-reload-fix.md) | `/models` 改完仍连旧端点（同名短路） |
| [model-hot-switch.md](model-hot-switch.md) | 切换后列表仍显示旧模型为当前 |
| [model-switch-vendor-endpoint-fix.md](model-switch-vendor-endpoint-fix.md) | 切回 `base_url` 留空的自定义模型时 404 |
| [llm-models-header-missing-fix.md](llm-models-header-missing-fix.md) | 缺 `[llm.models]` 表头 + 解析失败被吞 |
| [response-optimization-round1.md](response-optimization-round1.md) | 响应优化第一轮（109 例无回归） |
| [token-stats-compact-cache-fix.md](token-stats-compact-cache-fix.md) | `/cost`、`/context`、`/compact` 在缓存命中下算错 |
