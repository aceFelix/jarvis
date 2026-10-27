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

终端里也能直接改（与让模型改走**同一条写入路径**，只是入口不同）：

```
/config list                              列出 27 项可改配置（协议键 / 当前值 / 要不要确认）
/config set tts_volume 30                 可直接改的项当场改并落盘
/config set daily_max 20                  需确认的项先给预览（原值 → 新值 + 为什么要问）
/config set daily_max 20 --yes            看一眼没问题再执行
/config set window_exclude_apps "" --yes  清空名单类（必须显式写一对引号）
```

---

## 懒得改？直接对它说（阶段 12 自配置层）

装完就能用的默认值不需要你碰文件。**体验类配置可以直接用人话让它改**：

```
你：太吵了，音量小一点
它：播报音量 50 → 30，已当场生效；要撤回就说"撤回"（账本 a1b2c3d4）

你：以后每天 7:15 给我出简报
它：每日简报已在 07:15 重注册（不用重启）

你：把每天最多提醒次数改成 20
它：要改每日播报上限——这类设置会影响何时能打断你，确认？
```

| 它**能**自己改（改前不问） | 改前**必须问你** | 一律**拒绝**（要你自己改） |
|---|---|---|
| 音量/语速/主动播报出声、简报与截止日期开关+时间、感知逐源开关、停留阈值 | 静默时段/每日上限/重要度门槛/聚合窗口、感知总开关、任务驱动器、不可逆动作延时窗口 | `[sandbox]` 全部、`api_key*`、邮箱密码 |

- 改完的每一条都进副作用账本（`~/.jarvis/actions.jsonl`），`/undo` 可退回原值；
- 落盘只是**外科式**改那几行，你在 `settings.toml` 里写的注释与同段其他字段原样保留；
- **桌面设置面板会跟着变**：它推一条 `settings_changed` 事件，滑杆/开关立即跳到新值
  （改感知项时感知源面板也一起刷）——不用重开壳，也不会出现“改了但界面没变”；
- 终端 REPL 场景没有感知总线/调度宿主，涉及重装配的那几项会如实说"重启后生效"，
  不会假装已经生效。

**三个入口各有分工，但改同一张表**：你让它改（`ConfigSet`）需要走权限确认；
你自己在终端敲（`/config set`）是两段式（先预览再加 `--yes`）；你在桌面面板上拖滑杆
则是直接写。三处共用 `agent/config/config_ops.py`，所以“校验、落盘、生效、记账、
广播”这五步不会因为入口不同而漂移。

目前覆盖 28 项（15 auto + 12 confirm + 1 禁改），完整清单可用「你能改哪些配置」问出来
（模型可见 27 项；那 1 项禁改的是感知事件本地留痕，面板上可改、模型不可改），
字段定义见 `agent/config/config_specs.py`；机制与取舍见
[12-配置系统](../docs/architecture/12-配置系统.md#十四自配置层阶段-12模型侧配置入口)。

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
| `[policy.notify_budget] quiet_hours` | `"23:00-07:00"` | 静默时段（支持跨午夜；空串 = 不启用）。提醒类在其中走 `reminder_channels` 的**降级投递**（只上屏不朗读、不入队不占额度）；未配已实现通道时其余播报入库待补播 |
| `[policy.notify_budget] aggregate_window_s` | `300` | 同源事件聚合窗口（秒） |
| `[policy.notify_budget] min_salience` | `0.3` | 重要度门槛（低于此值**直接抑制**：只留痕、不入队也不补播；闸门顺序里门槛排在静默入库之前，2026-10-19 修正） |
| `[policy.notify_budget] reminder_channels` | `["screen"]` | 静默时段/额度用尽时**用户自设提醒**的送达通道：`screen`=上屏不出声、`email`=另发一封、`sms`=留位未接入（警告并忽略）；空列表=退回入库待补播 |
| `[policy.notify_budget] quiet_email_to` | `""` | 邮件通道的收件人，留空用 `[email].default_recipient` |

> 提醒与简报/感知事件的差别是有意的：`escalate_count` 那套升级重发只有字段、无人递增，
> 而冲刷靠“下一条通知请求”触发——对用户明说要过的提醒，整条延后就等于丢了，
> 所以默认降级为“气泡照投、不朗读”而不是入库排队。机制见
> [16-W1 判断层](../docs/architecture/16-W1判断层.md) 第四节。

相关命令：`/away` `/back` `/pending` `/lease`（见 [命令参考](../docs/guide/commands.md#权限控制)）。

---

## W2 感知层（`[percept]`，阶段 9）

统一事件总线 + 逐源采集：把“时钟驱动”扩成“工作现场驱动”。总线只裁**事件层
噪声**（去重窗口 / 同源节流 / 地板分 / 连败自动停用），“该不该现在打扰”仍由
上面的 `[policy.notify_budget]` 决定。机制详见
[17-W2 感知层](../docs/architecture/17-W2感知层.md)。

> 🔒 **隐私口径（已拍板的决策 2）**：总开关与逐源开关**全部默认关**，未显式打开的
> 源一个字节都不采；事件只本地落盘（`~/.jarvis/percept/events.jsonl`），不随对话上传。
> “问过没”只记在一个本机文件里：`~/.jarvis/percept/consent.json`（无记录且从未开过
> 总开关且本文件无 `[percept]` 表时，桌面壳连上后才会收到一条只出现一次的引导）。
> ✅ **当前状态（W2 第 5/6/7 步已接线）**：serve 宿主 `ProactiveHub` 启动时装配总线，
> 感知事件与简报/截止日期走**同一个打扰预算闸门**（首次引导例外：它不过闸门、不播报）。改开关有两条路：
> - **桌面壳「管家服务 → 感知源」面板**（已上线）：经 `percept.status.get` / 
>   `percept.config.set` 改逐源开关与名单，**先落本文件再改运行时，并由宿主重装配
>   总线即时生效**（关掉视觉就当场释放摄像头），不必重启；面板可改字段见
>   `agent/config/percept_settings.py`（只放开关与名单）；
> - **直接改本文件的 `[percept]` 表**：仍需**重启桌面壳/`--serve`** 才生效（逐源开关
>   与采样间隔在启动时快照），极客参数（去重窗口/地板分/采样间隔/帧率）只能走这条。
> ⚠️ 仅 `--serve` / 桌面壳运行期间采集（终端 REPL 不装总线）。

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
| `vision_enabled` / `vision_camera_index` / `vision_fps` | `false` / `0` / `2` | 视觉在场源（**唯一会碰摄像头的源**，需 `pip install "jarvis-agent[vision]"`） |
| `vision_gesture_enabled` / `vision_auto_stop_minutes` / `vision_interval_s` | `true` / `0` / `5` | 是否跑手势识别；`0` = 不自动关摄像头，`>0` 空闲自动关（代价：关闭期间检测不到“回来了”）；总线排空节拍 |
| `calendar_enabled` | `false` | 日历源（阶段 11 已接）：日程进入提前窗口时报**一次**；后端与提前量取 `[calendar]` 段的 `backend`/`ics_path`/`ics_url`/`remind_minutes_before`，感知层不另设一份 |
| `system_enabled` | `false` | 系统资源源（阶段 11 已接）：CPU/内存/磁盘阈值告警，阈值取 `[monitor]` 那一组；`psutil` 缺失时该源自报不可用而不是报错 |
| `schedule_enabled` | `false` | **留位未接入**：提醒已由提醒通道过 W1 闸门播报（静默时段降级投递），再进总线会重复播报；面板也不给这个开关 |

> 👀 **视觉源与 away**：打开 `vision_enabled` 后，人脸离开画面会触发无人值守（away），
> 回来时由人脸重新出现解除；而摄像头一旦不再工作（开了 `vision_auto_stop_minutes`、
> 被视觉工具停止、启动失败）就再也等不到“回来”，所以视觉推断的 away 有
> **30 分钟有效期**（`agent/core/policy/state.py` 的 `VISION_AWAY_TTL_MIN`），到期自动不再
> 压制播报。手动 `/away` 不被摄像头取消也不过期（优先级：手动 > 视觉 > 空闲推断）。

邮箱收件凭据配在 `[email]` 表（见下方“邮件”节），需同时开 `[percept] mail_enabled`。

---

## W3 兑现层（`[actions]` / `[tasks]`，阶段 10）

“敢放手”与“替我盯着”两组开关：前者把工具调用记成可执行的补偿计划（`/undo`），
后者把目标拆成带条件等待的步骤、由后台驱动器推进。机制详见
[18-W3兑现层](../docs/architecture/18-W3兑现层.md)。

> 🔒 **两个默认值取向不同是有意的**：账本**默认开**（关掉等于把已发生的副作用记不住，
> `/undo` 就无从补偿）；任务脊柱**默认关**（它会自主调 LLM 花钱并自己动手执行，必须用户
> 显式授权，同 `[percept]` 逐源默认关的口径）。
> 两本台账都只落 `~/.jarvis/`（`actions.jsonl` 与 `goals/<id>.json`），含收件人等敏感字段，不随对话上传。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `[actions] journal_enabled` | `true` | 副作用账本总开关（`false` = 不记账也不延时，`/undo` 无从下手） |
| `[actions] undo_window_s` | `30` | 不可逆动作延时窗口（秒）；`0` = 不拦、直接执行（**照样记账**） |
| `[actions] track_local_writes` | `true` | 本地破坏性写是否记一条可回滚条目（引用本轮 shadow-git 检查点） |
| `[actions] irreversible_tools` | `[]` | 应急口子：确实要多保护的工具才填（改配置不改代码）。❗ 拿 `WebFetch` 这类只读工具演示窗口是错的测法：它无副作用、可重来，只能演出一堆成本（见 [w3-delay-window-bypass-fix](../docs/fixlogs/w3-delay-window-bypass-fix.md)） |
| `[actions] hold_blocks_equivalent` | `true` | 待发窗口未结案时，网络外发类中风险命令（`curl`/`wget`/`Invoke-WebRequest`…）取消沙箱自动放行、改问人；本机命令不受影响。无 UI 环境 ASK→DENY 误伤不可接受时设 `false` |
| `[tasks] enabled` | `false` | 驱动器总开关（`false` = 只读台账不自走，`/tasks` 第二节照旧能列） |
| `[tasks] blocked_ask_days` | `3` | 阻塞超此天数主动问一次（走 `[policy.notify_budget]` 闸门；同一件事不重复问） |
| `[tasks] step_max_attempts` | `3` | 单步失败重试上限（到上限定局 `failed` 并来问，不会无退避自旋） |
| `[tasks] retry_backoff_s` | `60` | 重试退避间隔（秒）；本期为**固定间隔**，不按失败次数翻倍 |
| `[tasks] wait_deadline_hours` | `72` | 条件等待最长时限（超时转 `blocked_timeout` 来问用户，绝不静默挂起）。只对 `file_exists` / `percept_event` 兜底；`time` 类自带确定时刻，不补 deadline（叠加会出现“到点前先超时”的自相矛盾）。单条目标可用 `/tasks add … --deadline <小时>` 临时覆盖 |

> ⚠️ **延时窗口只延后、不豁免**：拦截点就在五层权限判定与 W1 策略闸门**之后**，
> DENY 永不放宽；到点重放用账本里冻结的那一份参数，因此模型后续改口也改写不了已排队的动作。
> 窗口期间也不会给等价通路留后门：外发类中风险命令的自动放行被临时关掉（只把 ALLOW 变 ASK，
> 从不把 DENY 变 ALLOW）。
> 进程重启时仍未发出的预约动作**一律作废**（启动时逐条播报），不会把上一个进程里你没确认第二次的
> 不可逆动作自动补发。

相关命令：`/undo`（`list` / `cancel` / `<id>` / `--task <id>`）见
[命令参考·对话控制](../docs/guide/commands.md#对话控制)；`/tasks`（两节 + 写入子命令
`add`/`show`/`pause`/`resume`/`cancel`/`advance`/`delete`）见
[命令参考·多 Agent 与插件](../docs/guide/commands.md#多-agent-与插件)。
建目标还有另两个入口：对我说人话（`TrackGoal` 工具）与桌面指令 `tasks.exec`，
三个入口共用 `core/tasks/spine_ops.py`，语义完全一致。
桌面右栏已于 2026-10-22 接完「跨会话目标」与「副作用账本」两个区块（消费 `tasks.exec` /
`actions.exec`），目标卡在等授权时可行内点**「批准并重试」**：后端按本步实际被拦的工具
+ 该目标的工作目录 + **默认 30 分钟**（常量 `APPROVAL_LEASE_TTL_MIN`，**不是配置项**；
RPC 可临时传 `minutes`）签一条限定范围的授权租约，查看与提前撤销仍用 `/lease`。
该通路只放宽 ASK，且**对不带路径参数的调用（如 `Bash`）无效**——限了路径的租约命中不了它。
驱动器只在 REPL（`-m agent`）与 `--serve` / 桌面壳两种宿主下启动，两者不要同时开。

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
| `tools_deferred_loading` | `true` | 工具延迟加载（16 核心 + ToolSearch） |
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
