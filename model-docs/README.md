# 模型厂商文档快照（model-docs）

各模型厂商的接口文档、思考模式与缓存规则的**外部抓取件**，统一收在这里便于比对实现。

> 这些不是本仓原创文档：内容以厂商官网为准，抓取后可能过时；修改本仓代码时以这里作为
> 「当时的协议依据」，但不要把它当成最新事实源。

## 目录

| 目录 | 厂商 | 内容 |
|---|---|---|
| [deepseek/](deepseek/) | DeepSeek | 思考模式、上下文缓存与命中规则 |
| [dashscope/](dashscope/) | 阿里云百炼（DashScope） | Realtime / WebSocket / WebRTC 接入、Qwen-Audio 事件、Function Calling、TTS/STT、上下文缓存 |
| [kimi/](kimi/) | 月之暗面 Kimi | 思考模式 |
| [mimo/](mimo/) | 小米 MiMo | 深度思考实现 |
| [minimax/](minimax/) | MiniMax | 官方 SDK、Anthropic / OpenAI SDK 接入 |
| [zai/](zai/) | 智谱（GLM / Z.ai） | 官方 Python SDK、思考模式、深度思考、上下文缓存、免费多模态模型 |

## 维护约定

1. **新增厂商**：建 `model-docs/<厂商>/`，文件名保持「厂商 + 主题」可读；不要放在仓库根目录。
2. **不做死链检查**：这些是整页抓取件，常含大量外链与站点相对路径，已由
   `scripts/check_md_links.py` 的 `SKIP_DIRS` 排除；本 README 自身的链接仍建议手工核对。
3. **不进密钥扫描**：厂商文档常带示例 Key，已在 `.gitleaks.toml` 的 allowlist 中整目录排除。
4. **引用方式**：代码注释与复盘文档引用时写全 `model-docs/<厂商>/<文件名>`，写明章节/行号更佳
   （例：`model-docs/dashscope/阿里云实时语音对话实现文档.md` 第 693 行）。
5. **更新时机**：厂商协议变更、排查语音/思考/缓存类问题、接入新厂商时同步刷新对应目录。
