# REPL 命令参考

> [← 返回 README](../../README.md) · [安装指南](installation.md) · [语音系统](voice.md) · [核心机制](concepts.md)

启动后输入 `/` 弹出命令列表，Tab 键自动补全。

---

## 对话控制

| 命令 | 说明 |
|---|---|
| `/help` `/h` | 查看所有命令帮助 |
| `/exit` `/quit` `/q` | 退出贾维斯 |
| `/reset` `/clear` | 清空对话历史，重新开始 |
| `/compact` | 手动压缩上下文（摘要旧消息节省 Token） |
| `/cost` | 显示本会话 token 用量与估算成本（含 system prompt 统计、缓存命中率） |
| `/context` | 查看上下文窗口使用情况（按角色分组统计，含 system prompt token；窗口口径取用户配置的 `context_window`（如 200000），统计头显示「窗口」，仅未配置回退默认值时才标注「假设窗口」） |
| `/rewind [n]` | 回退最近 n 条消息（默认 1 条）；若该轮涉及文件修改，列出改动清单确认后连带回滚工作区（shadow git 检查点），加 `--chat-only` 跳过询问仅回退对话 |
| `/diff [path]` | 显示工作目录的 git diff（可指定路径） |

## 模型管理

| 命令 | 说明 |
|---|---|
| `/model <前缀>` | 前缀匹配切换模型（支持模糊输入，多匹配时弹选择器） |
| `/models` | 交互式模型管理（↑↓选择、Enter切换、空格编辑配置，按厂商分组） |
| `/think` | 开关/调节深度思考（`/think on\|off\|low\|medium\|high`，支持模糊前缀与 1/2/3 速记；桌面输入区同提供四档选择器） |

## 权限控制

| 命令 | 说明 |
|---|---|
| `/mode <模式>` | 切换权限模式（default / plan / accept_edits / yolo，无参时弹选择器） |
| `/tools` | 列出所有可用工具 |

## 会话管理

| 命令 | 说明 |
|---|---|
| `/save [名称]` | 保存当前会话 |
| `/load <前缀>` | 前缀匹配加载已保存会话 |
| `/loads` | 列出并交互选择已保存会话 |
| `/sessions` `/ls-sessions` | 列出所有已保存会话 |

## 记忆与知识

| 命令 | 说明 |
|---|---|
| `/memory` | 画像记忆管理（查看/add/del/clear/refine；`file` 看长期记忆文件） |
| `/skills` | 列出已加载的技能包 |

## 语音功能

| 命令 | 说明 |
|---|---|
| `/voice` | 进入语音对话模式（连续 STT→LLM→TTS 循环） |
| `/talk` | 进入实时双工语音对话（终端半双工轮替；桌面壳为说话即打断的真全双工） |
| `/tts-voice [前缀]` | 切换/添加 TTS 音色（仅 DashScope；音色带适配模型，不兼容自动联动 tts_model） |
| `/say <文本>` | TTS 朗读指定文字 |
| `/listen` `/mic` | 录音并识别为文字 |

详见 [语音系统](voice.md)。

## 图片输入

| 命令 | 说明 |
|---|---|
| `/image <路径>` `/img <路径>` | 添加本地图片到待发送列表 |
| `/paste` `/p` `/clipboard` | 添加剪贴板图片到待发送列表（同 Ctrl+V） |

> 图片在下次发送消息时附带。支持格式：PNG / JPG / WEBP / BMP。自动缩放到最长边 1280px。
> 终端输入框按 **Ctrl+V** 即可粘贴剪贴板图片；剪贴板**不再自动检测**（残留图片会被误带进普通提问）。

## 多 Agent 与插件

| 命令 | 说明 |
|---|---|
| `/agents` | 查看多 Agent 团队状态与成员 |
| `/tasks` | 查看共享任务列表进度 |
| `/plan` | 切换规划模式（进入/退出只读规划） |
| `/plugin` `/plugins` | 列出已安装插件（Plugin 系统） |
| `/plugin search [关键词]` | 搜索 Plugin 系统市场 |
| `/plugin install <名称>` | 安装 Plugin 系统的插件 |
| `/plugin uninstall <名称>` | 卸载 Plugin 系统的插件 |
| `/plugin info <名称>` | 查看 Plugin 插件详情 |
| `/plugin update` | 检查 Plugin 插件更新 |
| `/plugin enable <名称>` | 启用被禁用的 Plugin 插件 |
| `/plugin disable <名称>` | 禁用 Plugin 插件，不卸载 |
| `/plugin create <名称>` | 创建 Plugin 插件脚手架 |
| `/plugin validate <路径>` | 校验 plugin.json 合法性 |
| `/cli_anything` `/harnesses` | 列出已安装 CLI-Anything harness |
| `/cli_anything market` | 列出市场可用 harness |
| `/cli_anything install <id>` | 安装指定 harness |
| `/cli_anything uninstall <id>` | 卸载指定 harness |
| `/cli_anything enable <id>` | 启用被禁用的 harness |
| `/cli_anything disable <id>` | 禁用 harness，不卸载 |
| `/cli_anything create <id>` | 创建 harness 脚手架 |
| `/cli_anything validate <路径>` | 校验 SKILL.md 合法性 |

## MCP 工具

| 命令 | 说明 |
|---|---|
| `/mcp` | 查看 MCP server 连接状态与工具列表 |

## 系统与诊断

| 命令 | 说明 |
|---|---|
| `/init` | 交互式首次配置引导（选厂商→输Key→测试→保存） |
| `/doctor` | 查看自愈统计与系统诊断 |
| `/config [show]` | 查看当前生效的完整配置（LLM/语音/权限/MCP/自定义模型等） |
| `/server [目录]` | 一键启动前端开发服务器 |
| `/connect-phone` `/phone` | 跨设备协同（手机扫码连接当前会话） |
| `/connect-wechat` `/wechat` | 微信扫码连接 JARVIS（通过 ClawBot 在微信中对话） |
| `/disconnect-wechat` | 断开微信 ClawBot 连接 |
| `/verbose` | 开关详细输出（token 统计、缓存命中等） |

> 已加载的 Skill 也可直接作为斜杠命令调用：`/<skill-name> [参数]`（动态技能分发）。

---

## 模型管理详解

### 内置模型

开箱即用，接入阿里云 DashScope：

- `qwen3.7-plus` — 通义千问 3.7 Plus（默认，多模态视觉）
- `qwen3.6-plus` — 通义千问 3.6 Plus
- `qwen3.6-flash` — 通义千问 3.6 Flash（快速响应）
- `qwen3.5-plus` — 通义千问 3.5 Plus
- `qwen3.5-flash` — 通义千问 3.5 Flash（快速响应）

### 添加自定义模型

通过 `/models` 命令交互式添加自定义模型，支持四种接口类型：

| 接口类型 | 适用模型 | 说明 |
|---|---|---|
| **OpenAI 兼容** | DeepSeek / GPT-4o / 各类兼容服务 | 标准 OpenAI API 格式 |
| **Anthropic 兼容** | Claude 系列 | Anthropic Messages API 格式 |
| **DashScope SDK** | qwen 系列原生协议 | 支持 MultiModalConversation 和 Generation 双端点 |
| **智谱 ZhipuAi SDK** | GLM 系列原生协议 | 绕过 OpenAI 兼容层，获得更稳定的响应 |

配置会自动保存到 `~/.jarvis/models.toml` 的 `[llm.custom_models]` 中（密钥跟随模型配置同文件，并同步写入系统 keyring），重启后保持。

### 自定义模型配置示例

写入 `~/.jarvis/models.toml`：

```toml
[llm.custom_models."deepseek-v4"]
api_format = "openai"
base_url = "https://api.deepseek.com/v1"
api_key = "sk-your-deepseek-key"
model_type = "text"              # "text" 纯文本 / "multimodal" 多模态

[llm.custom_models."glm-4.7-flash"]
provider = "zhipu"
api_format = "zai"
api_key = "sk-your-zhipu-key"
model_type = "text"              # GLM-4.7-flash 为纯文本模型
```

> 模型配置文件分层合并与常见坑见 [config-docs/configuration.md](../../config-docs/configuration.md)，各厂商接入见 [config-docs/providers.md](../../config-docs/providers.md)。

## 深度思考模式

启用后，模型在每次回复前先输出 `reasoning_content`（思考过程），形成完整的 **Think → Act → Observe** ReAct 循环。

- **视觉效果**：思考内容在终端显示为暗色面板「💭 思考过程」
- **运行中开关与强度**：`/think on` / `/think off` / `/think low|medium|high`（无需重启），参数支持
  模糊前缀（`/think l` = low、`/think h` = high）与数字速记（`1/2/3` = low/medium/high），
  输入 `/think ` 后 Tab 可补全档位；桌面输入区「思考」选择器提供 **关闭 / 低 / 中 / 高** 四档统一强度
- **配置项**：
  ```toml
  enable_thinking = true
  thinking_budget = 800    # 思考过程 Token 上限（无强度档位时的回退值）
  thinking_effort = "high" # 统一强度档位：off/on/low/medium/high（后端按厂商翻译）
  ```
  环境变量 `JARVIS_THINKING_EFFORT` 可覆盖档位。
- **厂商适配**：采用 `ThinkingConfig` 配置表驱动，把统一档位翻译成各厂商原生参数：
  - Qwen / DashScope：`enable_thinking` + `thinking_budget`（extra_body/top_level），档位经 `budget_map` 映射 低=512 / 中=2000 / 高=8000
  - DeepSeek / Kimi(Moonshot)：`thinking.type` + `reasoning_effort`，档位映射 低=`low` / 中=`high` / 高=`max`（文档无 medium，就近取档）
  - 智谱 GLM：`thinking.type` + `reasoning_effort`，档位映射原生 低/中/高
  - 小米 MiMo：`thinking.type` 仅开/关（无强度档位）
  - OpenAI / MiniMax / Google / SiliconFlow 等无干净思考控制的厂商自动跳过（桌面选择器对其置灰）
- **桌面壳联动**：输入区「工作模式」「思考」两个选择器经 `mode.set` / `think.set` 指令走引擎队列串行落地，**下一条消息生效**（与终端 `/mode`、`/think` 同口径）
- **语音模式**：自动关闭思考（降低首字延迟）
