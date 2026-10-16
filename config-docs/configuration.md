# J.A.R.V.I.S 配置指南

> 每个配置项的含义、默认值、示例。

---

## 配置层级（后者覆盖前者）

```
1. 内置默认值（Settings 数据类字段默认值）
2. configs/settings.toml（项目级通用配置，随仓库分发）
3. configs/models.toml（项目级模型域，同层覆盖上一项）
4. ~/.jarvis/settings.toml（用户级通用配置；兼容 ~/.my-agent/）
5. ~/.jarvis/models.toml（用户级模型域，同层覆盖上一项）
6. 环境变量（JARVIS_* 前缀 + 厂商专属 *_API_KEY；兼容 MY_AGENT_*）
7. CLI 参数（--model / --provider / --api-key 等）
```

查看最终生效配置：`jarvis --config-show` 或 REPL 内 `/config show`。

---

## LLM（写在 `models.toml`）

> 本表字段的落盘位置是 **`models.toml`**（项目级 `configs/models.toml` / 用户级 `~/.jarvis/models.toml`）。
> 写在 `settings.toml` 里同样生效（向后兼容），但 `/models`、`jarvis init`、切换模型的回写一律落到 `models.toml`。
> 顶层键必须写在任何 `[section]` 之前（写进 `[llm.models]` 之后会被 TOML 静默解析进该子表）。

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

`~/.jarvis/models.toml`：

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
| `JARVIS_UPDATE_CHECK` | `0` 关闭启动时的版本更新检查（`JARVIS_NO_UPDATE_CHECK=1` 等价） |

> Windows PowerShell：`$env:DASHSCOPE_API_KEY = "sk-xxx"`

---

## 语音

### TTS（文字 → 语音）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `tts_model` | `"cosyvoice-v3-flash"` | TTS 模型 |
| `tts_voice` | `"longanlang_v3"` | 音色（自定义音色见 `custom_voices`；切换时若音色适配模型与 `tts_model` 不兼容会自动联动切模型并落盘） |
| `custom_voices` | `{}` | 自定义音色 `{音色名: {voice_id, model, vendor, description}}`（`/tts-voice` 添加，`[tts.custom_voices]` 节；`model` = 适配模型，空 = 不限） |
| `tts_volume` | `50` | 音量（0-100）；桌面壳设置面板可运行时调（`settings.set` 滑杆，先落盘再改运行时） |
| `tts_speech_rate` | `1.0` | 语速倍率（0.5-2.0）；桌面壳设置面板可运行时调 |
| `tts_pitch_rate` | `1.0` | 音调倍率 |

### STT（语音 → 文字）

单一后端 QwenASR（DashScope OmniRealtime 流式识别，断句由服务端 VAD 完成）。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `stt_model` | `"qwen3-asr-flash-realtime"` | STT 模型名（原样透传，非 qwen 实时模型会在建连阶段被拒绝） |
| `stt_max_seconds` | `15.0` | 最长录音秒数（客户端兜底超时） |
| `stt_silence_seconds` | `1.5` | 服务端 VAD 静音多少秒后断句 |

### 实时双工（/talk）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `realtime_model` | `"qwen-audio-3.0-realtime-flash"` | Realtime 模型 |
| `realtime_voice` | `"longanqian"` | 实时语音音色 |
| `realtime_half_duplex`（`[realtime_talk].half_duplex`） | `true` | 半双工：AI 说话期静音麦克风，外放稳定多轮；戴耳机设 false |
| `realtime_rescue`（`[realtime_talk].rescue`） | `true` | 响应救援：服务端吞轮时补发一次 `response.create`（三闸门防重复回答） |
| `realtime_turn_detection`（`[realtime_talk].turn_detection`） | `"server_vad"` | 轮次检测：`server_vad`（默认，尾音延长当前轮）/ `smart_turn`（语义判停，尾音易被重检） |
| `realtime_silence_ms`（`[realtime_talk].silence_ms`） | `500` | server_vad 判停静音时长（毫秒，200~6000） |
| `realtime_tools_mode`（`[realtime_talk].tools_mode`） | `"builtin"` | 语音会话工具面：`builtin` 2 内置工具低延迟 / `all` Registry+MCP 全量 |
| `voice_barge_in` | `false` | 麦克风语音打断（默认关：watcher 会开第二个 PyAudio 实例，与 TTS 并发有崩溃风险） |
| `voice_barge_in_key` | `true` | 键盘打断（ESC 键） |

> 桌面壳 jarvis-desktop 的语音模式为真全双工（浏览器 AEC + `talk.audio`/`talk_audio`
> 音频桥接），路径内固定 `half_duplex=False`，不受上述配置影响。

---

## 权限

| 字段 | 默认值 | 说明 |
|---|---|---|
| `permission_mode` | `"default"` | 可选：`default` / `accept_edits` / `plan` / `yolo` |

---

## W1 运行时层（`[policy]`，阶段 8）

授权租约 + 打扰预算 + 无人值守，挂在五层权限管线之后（DENY 不可放宽，
away 时沙箱中风险自动放行也升级待批，无租约且非 away 时零介入）。
声明式免确认直接写 `permissions.yaml` 规则
（带 spec 的 allow 可放宽工具 ASK，见 [04-权限系统](../docs/architecture/04-权限系统.md)）；
机制详见 [16-W1 运行时层](../docs/architecture/16-W1判断层.md)。

> 旧字段 `[policy] enabled` / `[policy] path`（策略引擎总开关与
> `policy.yaml` 位置）已随 2026-10 降级合并删除，残留键静默忽略。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `[policy] away_idle_minutes` | `0` | 0 = 仅认手动 `/away`；>0 启用空闲推断自动 away |
| `[policy.notify_budget] enabled` | `true` | 打扰预算闸门（false = 提醒不经预算直接播） |
| `[policy.notify_budget] daily_max` | `10` | 每日主动播报上限 |
| `[policy.notify_budget] quiet_hours` | `"23:00-07:00"` | 静默时段（支持跨午夜；空串 = 不启用） |
| `[policy.notify_budget] aggregate_window_s` | `300` | 同源事件聚合窗口（秒） |
| `[policy.notify_budget] min_salience` | `0.3` | 重要度门槛（低于此值只入库不播） |

相关命令：`/away` `/back` `/pending` `/lease`（见 [命令参考](../docs/guide/commands.md#权限控制)）。

---

## W2 感知层（`[percept]`，阶段 9）

统一事件总线 + 逐源采集：把“时钟驱动”扩成“工作现场驱动”。总线只裁**事件层
噪声**（去重窗口 / 同源节流 / 地板分 / 连败自动停用），“该不该现在打扰”仍由
上面的 `[policy.notify_budget]` 决定。机制详见
[17-W2 感知层](../docs/architecture/17-W2感知层.md)。

> 🔒 **隐私口径（已拍板的决策 2）**：总开关与逐源开关**全部默认关**，未显式打开的
> 源一个字节都不采；事件只本地落盘（`~/.jarvis/percept/events.jsonl`），不随对话上传。
> ⚠️ **当前状态**：总线与四个源已落地并有单测，但**宿主接线（serve 侧订阅总线）尚未
> 完成**（W2 第 4 步）——现在打开配置不会有任何播报。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `[percept] enabled` | `false` | 感知层总开关（关 = 不起轮询线程、不采任何源） |
| `[percept] dedup_window_s` | `300` | 同一件事的去重窗口（秒） |
| `[percept] min_salience` | `0.0` | 总线地板分（0 = 不裁，重要度裁决交给 W1） |
| `[percept] history_max` | `200` | 内存环形历史条数（留痕文件上限 = 此值×10 行） |
| `[percept] log_events` | `true` | 本地留痕；`false` = 完全不碰磁盘 |
| `[percept] auto_disable_after_errors` | `3` | 单源连续失败达此数自动停用（0 = 只隔离不罢工） |
| `window_enabled` / `window_interval_s` | `false` / `5` | 前台窗口源（唯一有明显隐私属性的源） |
| `window_title_max_chars` / `window_title_full_enabled` | `40` / `false` | 标题截断长度；开后者才保留完整标题（仍剥凭据片段） |
| `window_exclude_apps` / `window_linger_minutes` | `[]` / `0` | 排除应用（留空则用内置敏感名单）；停留提醒阈值 |
| `file_enabled` / `file_watch_dirs` | `false` / `[]` | 文件守望源（**只订阅白名单目录**，空列表 = 开了也不采） |
| `file_interval_s` / `file_max_entries` / `file_grow_threshold` / `file_ignore_suffixes` / `file_min_interval_s` | `30` / `2000` / `5` / `[]` / `0` | 文件源参数 |
| `vcs_enabled` / `vcs_repos` / `vcs_interval_s` / `vcs_min_interval_s` | `false` / `[]` / `120` / `60` | 仓库源（登记制，只跑 `git status`/`stash list` 两条只读命令） |
| `mail_enabled` / `mail_min_interval_s` / `mail_watch_senders` | `false` / `0` / `[]` | 邮件源（IMAP 只读 IDLE）；白名单非空 = 只报命中的人 |
| `calendar_enabled` / `system_enabled` / `schedule_enabled` / `vision_enabled` | `false` | 既有能力收编位（W2 第 4 步接线） |

邮箱收件凭据配在 `[email]` 表（见下方“邮件”节），需同时开 `[percept] mail_enabled`。

---

## 上下文管理

| 字段 | 默认值 | 说明 |
|---|---|---|
| `context_compaction` | `true` | 自动压缩旧消息 |
| `context_window` | `128000` | 模型上下文窗口大小（token），换模型时同步修改；勿设 0 |
| `compact_ratio` | `0.5` | 压缩触发比例：总 token ≥ 窗口×比例才压缩，未超比例绝不压缩 |
| `compact_refreeze_growth` | `1.25` | 防抖：冻结后总量增长不足此倍数不重复压缩 |
| `compact_max_output_tokens` | `2048` | 压缩摘要请求的输出 token 上限 |
| `tool_result_keep_recent` | `4` | 工具结果折叠时保留最近 N 条完整输出 |
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

## 桌面入口 / 全局热键（原常驻模式已下线）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `daemon_hotkey` | `"ctrl+shift+j"` | 全局热键（召唤三栏工作台，待接线） |
| `daemon_hotkey_native` | `true` | Windows 优先 RegisterHotKey |
| `daemon_hotkey_debounce_ms` | `200` | 热键去抖毫秒 |

---

## 主动播报（每日简报 / 提醒 / 截止日期）

由 serve 宿主的 `ProactiveHub` 消费，经 jarvis-desktop 播报（仅 `--serve` / 桌面壳运行期间生效）。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `briefing_enabled` | `true` | 每日简报开关；桌面壳设置面板可运行时开关（`settings.set` 落盘 + ProactiveHub 调度热重注册，无需重启） |
| `briefing_time` | `"08:30"` | 每日简报时间 HH:MM；桌面壳设置面板可运行时改（同上，改完即按新时间重注册） |
| `briefing_catchup_window_min` | `120` | 简报补播窗口（分钟）：启动时错过 ≤ 此值才补播一次；≤0 关闭补播 |
| `proactive_tts_enabled` | `true` | 待机 TTS 朗读：到期播报并行用本机 CosyVoice 朗读；对话/语音忙时跳过不打断；提醒加「先生，提醒您：」前缀，简报/截止日期只读前 200 字；音色/音量复用 `tts_model`/`tts_voice`/`tts_volume` 等，API Key 走 `_voice_api_key`（DashScope 专用）；需 `pip install "jarvis-agent[voice]"` 依赖，缺失时静默降级不影响事件通道；桌面壳设置面板可运行时开关（`settings.set` 先落盘本字段再改运行时，重启保持） |
| `deadline_enabled` | `true` | 截止日期分级提醒开关；桌面壳设置面板可运行时开关（调度热重注册） |
| `deadline_check_time` | `"09:00"` | 每日检查截止日期的时间 HH:MM；桌面壳设置面板可运行时改 |
| `calendar_enabled` | `false` | 日历集成（本期未接线，保留） |

> 定时任务持久化在 `~/.jarvis/schedule.json`；对话里说“提醒我…”即可创建一次性任务（需桌面壳/`--serve` 在跑）。

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
# 收件（W2 mail 事件源，需同时开 [percept].mail_enabled）：IMAP 只读，
# 只取信封头 FROM/SUBJECT/DATE/MESSAGE-ID，永不拉正文、不把邮件标成已读。
# imap_user / imap_password 留空则复用 SMTP 同一套（163/QQ 两边同一个授权码）。
email_imap_host = "imap.163.com"
email_imap_port = 993            # 993 = SSL；143 走 STARTTLS
email_imap_user = "your@163.com"
email_imap_password = "授权码"
email_imap_mailbox = "INBOX"
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
| `--serve` | 启动 headless WebSocket 后端（仅监听 `127.0.0.1` + 随机端口，供 jarvis-desktop 等外部前端接入） |
| `--gui` / `--talk` | ❌ 已退役（2026-10）：三栏 pywebview 工作台窗口已删除，执行仅打印下线说明与自启清理指引；参数仍保留解析但不再出现在 `--help`（图形化桌面请用 jarvis-desktop） |
| `--no-update-check` | 跳过启动时的版本更新检查（等价 `[update] check = false`） |

---

## 版本更新

启动时后台检查 PyPI 最新版（非阻塞，最多 24 小时一次），发现更新仅打印提示；会话内用 `/update` 一键升级（自动识别 pip / uv 渠道，重启生效）。

```toml
# ~/.jarvis/settings.toml
[update]
check = true    # 是否在启动时检查更新（false 关闭）
```

| 字段 | 默认值 | 说明 |
|---|---|---|
| `check` | `true` | 启动时是否检查 PyPI 最新版本 |

> `/update` 只在 pip / uv tool 安装下可自动升级；开发模式（`pip install -e .`）与未作为包安装的场景会跳过，仅给出指引。升级只更新主包，已安装的 extras 依赖不会被卸载。
