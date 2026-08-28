# J.A.R.V.I.S 配置指南

> 每个配置项的含义、默认值、示例。

---

## 配置层级（后者覆盖前者）

```
1. 内置默认值（Settings 数据类字段默认值）
2. configs/settings.toml（项目级，随仓库分发）
3. ~/.jarvis/settings.toml（用户级；兼容 ~/.my-agent/）
4. 环境变量（JARVIS_* 前缀；兼容 MY_AGENT_*）
5. CLI 参数（--model / --provider 等）
```

查看最终生效配置：`jarvis --config-show` 或 REPL 内 `/config show`。

---

## LLM

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `provider` | str | `"mock"` | 厂商名（dashscope / deepseek / openai / zhipu / anthropic …）|
| `api_format` | str | `"openai"` | API 协议格式——决定用哪个 Provider 类。可选：`openai` / `anthropic` / `dashscope_sdk` / `zai_sdk` |
| `model` | str | `""` | 模型名。空 = 用厂商默认。启动时 `last_model` 会覆盖它 |
| `last_model` | str | `""` | 上次使用的模型（自动持久化，下次启动恢复） |
| `api_key` | str | `""` | API Key（推荐用环境变量，避免明文存 TOML） |
| `dashscope_api_key` | str | `""` | DashScope 专属 Key（实时语音/多模态必须独立配置） |
| `base_url` | str | `""` | API 端点。空 = 用内置默认（DashScope/DeepSeek 等自动识别） |
| `max_tokens` | int | `4096` | 单轮最大输出 token |
| `temperature` | float | `None` | 采样温度（None = 厂商默认） |
| `enable_thinking` | bool | `true` | 深度思考（思维链）开关。REPL 内 `/think on/off` 实时切换 |
| `thinking_budget` | int | `2000` | 思考过程 token 上限 |
| `vendor_fallback` | str | `""` | 主模型挂了自动切备选厂商。如 `"deepseek"` |

### 示例

```toml
provider = "deepseek"
api_format = "openai"
model = "deepseek-chat"
base_url = "https://api.deepseek.com/v1"
enable_thinking = true
```

---

## 环境变量

| 变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | 阿里云 DashScope |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `ZAI_API_KEY` | 智谱 AI（GLM） |
| `ANTHROPIC_API_KEY` | Anthropic Claude |
| `KIMI_API_KEY` | Moonshot（Kimi） |
| `MINIMAX_API_KEY` | MiniMax |
| `MIMO_API_KEY` | 小米 MiMo |
| `OPENAI_API_KEY` | OpenAI 及兼容服务（通用兜底） |
| `JARVIS_API_KEY` | 通用兜底（新名） |
| `MY_AGENT_API_KEY` | 通用兜底（兼容旧名） |
| `JARVIS_PROVIDER` | 覆盖 provider |
| `JARVIS_MODEL` | 覆盖 model |
| `JARVIS_BASE_URL` | 覆盖 base_url |
| `JARVIS_PERMISSION_MODE` | 覆盖权限模式 |
| `JARVIS_DEBUG` | `1` 开启调试模式 |

> Windows PowerShell：`$env:DASHSCOPE_API_KEY = "sk-xxx"`

---

## 语音

### TTS（文字 → 语音）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `tts_model` | `"cosyvoice-v3-flash"` | TTS 模型 |
| `tts_voice` | `"longanlang_v3"` | 音色 |
| `tts_volume` | `50` | 音量（0-100） |
| `tts_speech_rate` | `1.0` | 语速倍率 |
| `tts_pitch_rate` | `1.0` | 音调倍率 |

### STT（语音 → 文字）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `stt_model` | `"paraformer-realtime-v2"` | STT 模型 |
| `stt_max_seconds` | `15.0` | 最长录音秒数 |
| `stt_silence_seconds` | `1.5` | 静音多少秒后自动结束 |

### 实时双工（/talk）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `realtime_model` | `"qwen-audio-3.0-realtime-flash"` | Realtime 模型 |
| `realtime_voice` | `"longanqian"` | 实时语音音色 |
| `voice_barge_in` | `true` | 语音打断（说"闭嘴"等中断词） |
| `voice_barge_in_key` | `true` | 键盘打断（ESC 键） |

---

## 权限

| 字段 | 默认值 | 说明 |
|---|---|---|
| `permission_mode` | `"default"` | 可选：`default` / `accept_edits` / `plan` / `yolo` |

---

## 上下文管理

| 字段 | 默认值 | 说明 |
|---|---|---|
| `context_compaction` | `true` | 自动压缩旧消息 |
| `compaction_threshold` | `8000` | 估算 token 超此值触发压缩（绝对阈值模式，`context_window=0` 时生效） |
| `context_window` | `0` | 模型上下文窗口大小（token）。>0 启用比例模式：总 token ≥ 窗口×compact_ratio 才压缩 |
| `compact_ratio` | `0.5` | 自动压缩触发比例（占窗口百分比），仅 `context_window > 0` 时生效 |
| `compact_refreeze_growth` | `1.25` | 防抖：冻结后总量增长不足此倍数不重复压缩 |
| `compact_max_output_tokens` | `2048` | 压缩摘要请求的输出 token 上限 |
| `keep_recent_messages` | `6` | 压缩时保留最近 N 条原消息 |
| `long_term_memory` | `true` | 加载 `~/.jarvis/MEMORY.md` 长期记忆 |
| `auto_resume_session` | `false` | 启动时自动恢复上次会话 |

---

## 工具

| 字段 | 默认值 | 说明 |
|---|---|---|
| `tools_deferred_loading` | `true` | 工具延迟加载（14 核心 + ToolSearch） |
| `tools_chat_detection` | `true` | 纯聊天零工具 |
| `enable_tool_self_healing` | `true` | 工具执行自愈 |
| `tool_retry_max` | `3` | 最大重试次数 |
| `enable_mcp` | `true` | MCP server 集成 |
| `enable_lsp` | `true` | LSP 代码智能 |
| `enable_skills` | `true` | Skill 系统 |

---

## 安全沙箱

| 字段 | 默认值 | 说明 |
|---|---|---|
| `sandbox_enabled` | `false` | 高风险命令在沙箱执行 |
| `sandbox_max_memory_mb` | `512` | 沙箱内存上限 |
| `sandbox_timeout` | `120` | 沙箱命令超时（秒） |
| `sandbox_block_network` | `false` | 阻断沙箱网络 |

---

## Daemon / 常驻模式

| 字段 | 默认值 | 说明 |
|---|---|---|
| `daemon_hotkey` | `"ctrl+shift+j"` | 全局热键 |
| `daemon_tray` | `true` | 系统托盘图标 |

---

## 邮件

```toml
email_enabled = true
email_smtp_host = "smtp.163.com"
email_smtp_port = 465
email_smtp_user = "your@163.com"
email_smtp_password = "授权码"    # 不是登录密码！
email_sender = "your@163.com"
email_default_recipient = "target@qq.com"
```

---

## CLI 参数

| 参数 | 说明 |
|---|---|
| `--init` | 交互式首次配置向导 |
| `--config-show` | 展示当前生效的完整配置 |
| `--model <name>` | 指定模型 |
| `--provider <name>` | 指定厂商 |
| `--api-key <key>` | 指定 API Key |
| `--workdir <path>` | 工作目录 |
| `--debug` | 调试模式 |
| `--verbose` | 详细输出 |
| `--no-boot` | 跳过启动动画 |
| `--quick` | 快速启动（跳过 MCP/LSP） |
| `--daemon` | 常驻后台 |
| `--talk` | 直接启动实时语音对话 |
| `--voice` | 直接启动语音对话模式 |
