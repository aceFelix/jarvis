"""配置加载。

配置来源（文件/环境变量/MDM/keychain/CLI 参数），
v0.1 用 TOML 文件 + 环境变量两层即可。

配置查找顺序（后者覆盖前者）:
1. 内置默认值
2. configs/settings.toml（项目级，随仓库分发）
3. configs/models.toml（项目级模型域，同层覆盖 settings.toml）
4. ~/.jarvis/settings.toml（用户级；兼容回退 ~/.my-agent/）
5. ~/.jarvis/models.toml（用户级模型域，同层覆盖 settings.toml）
6. 环境变量（JARVIS_* 前缀；兼容 MY_AGENT_*）
7. CLI 参数（在 main.py 里处理）

模型域（provider/model/api_key/custom_models 等）独立成 models.toml，
动机与读写实现见 agent/config/models_config.py。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib  # type: ignore[import-not-found]
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found,no-redef]

from agent.config.models_config import (
    auto_split_user_config,
    models_path_for,
    user_config_paths,
)
from agent.config.settings_domains import apply_domain_tables
from agent.permissions.modes import PermissionMode, parse_mode


@dataclass
class Settings:
    """应用配置。

    CLI 参数会覆盖这些字段（在 main.py 里合并）。
    """

    # LLM
    provider: str = "mock"       # 模型提供商（vendor），如 dashscope/deepseek/openai/anthropic，仅显示用途
    api_format: str = "openai"   # API 协议格式——控制用哪个 LLM Provider 类（openai 或 anthropic）
    model: str = ""              # 空字符串 = 用 provider 默认（启动时若 last_model 存在则被覆盖）
    last_model: str = ""         # 上次使用的模型（自动持久化，下次启动时恢复）
    # settings.toml 原始 provider/api_format/base_url/api_key（last_model 覆盖前的值）
    # _switch_model 切回内置模型时用这些值重建 provider，避免停留在自定义模型配置上
    default_provider: str = ""
    default_api_format: str = ""
    default_base_url: str = ""
    default_api_key: str = ""
    api_key: str = ""
    base_url: str = ""
    # DashScope 专属 API Key（实时语音/多模态等必须使用 DashScope key，
    # 不能与当前 LLM 的 deepseek/openai key 混用）
    dashscope_api_key: str = ""
    max_tokens: int = 4096
    temperature: float | None = None
    # 可选模型列表 {model_name: description}，/model 命令列出并切换
    models: dict[str, str] = field(default_factory=dict)
    # 用户自定义模型配置 {model_name: {provider, base_url, api_key, api_format, model_type}}
    # 通过 /models → 添加其他模型 创建，持久化到 ~/.jarvis/models.toml [llm.custom_models]
    custom_models: dict[str, dict] = field(default_factory=dict)
    # 用户自定义 TTS 音色 {voice_name: {voice_id, description, vendor, model}}
    # 通过 /tts-voice → 添加音色 创建，持久化到 ~/.jarvis/settings.toml [tts.custom_voices]
    # model = 适配模型（家族前缀或具体模型，空 = 不限），切换音色时联动校正 tts_model
    custom_voices: dict[str, dict] = field(default_factory=dict)
    # 深度思考（思维链）—— enable_thinking 通过 extra_body 传给 DashScope
    enable_thinking: bool = True
    thinking_budget: int = 2000
    # 思考强度统一档位：off/low/medium/high/on（桌面壳/终端运行期可改）。
    # 后端按厂商 THINKING_CONFIGS 翻译成 thinking_budget / reasoning_effort。
    thinking_effort: str = "high"
    # Provider 故障转移：主模型挂了自动切到备选厂商的模型
    # 留空表示不做故障转移。如设置为 "deepseek" 则主模型失败后尝试 DeepSeek 模型。
    vendor_fallback: str = ""

    # 运行时
    workdir: str = ""
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    max_iterations: int = 25

    # 路径
    permissions_file: str = ""
    system_prompt_append: str = ""

    # 调试
    debug: bool = False
    verbose: bool = False

    # 语音（阶段三）
    tts_model: str = "cosyvoice-v3-flash"
    tts_voice: str = "longanlang_v3"
    tts_volume: int = 50
    tts_speech_rate: float = 1.0
    tts_pitch_rate: float = 1.0
    # STT（阶段三第二刀）—— 单一后端 QwenASR，断句由服务端 VAD 完成
    stt_model: str = "qwen3-asr-flash-realtime"
    stt_max_seconds: float = 15.0
    stt_silence_seconds: float = 1.5
    # 语音模式最长录音时间（/voice 模式），超时自动提交。默认 5 分钟
    voice_max_seconds: float = 300.0
    # 语音模式选项（阶段三第四刀）
    # 语音打断（麦克风通道）：TTS 播报期间由 _BargeInWatcher 监听用户开口自动打断。
    # 默认关闭：watcher 会开第二个 PyAudio 实例，与 TTS 的 PyAudio 并发在 Windows
    # 上可能 segfault（见 configs/settings.example.toml 同名说明），环境稳定后再开。
    voice_barge_in: bool = False
    # barge_in_key（键盘打断）默认启用：TTS 播报期间用 keyboard 库全局钩子监听 ESC，
    # 按下立即停止播报并切回聆听。不占 PyAudio，无 segfault 风险，daemon 无窗口也能捕获。
    voice_barge_in_key: bool = True
    # 唤醒 / 退下 / 打断三张词表（[voice] wake_words / exit_words / interrupt_words）。
    # 留空 = 用 voice_config.py 里的内置词表，所以老配置不受影响。
    # 唤醒词除字面匹配外还参与**拼音同音**匹配：配「贾维斯」即可命中「加维思」
    # 「贾伟斯」「假维斯」等近音写法（详见 agent/voice/wake_words.py）。
    voice_wake_words: tuple[str, ...] = ()
    voice_exit_words: tuple[str, ...] = ()
    voice_interrupt_words: tuple[str, ...] = ()

    # 实时双工语音对话（/talk）
    # endpoint 默认使用 DashScope 公共域名；如使用业务空间专属域名，在此覆盖
    realtime_ws_url: str = ""
    realtime_model: str = "qwen-audio-3.0-realtime-flash"
    realtime_voice: str = "longanqian"
    # 事件时间线日志：开启后把 /talk 收到的每条服务端事件按序写入
    # ~/.jarvis/logs/diag.log（component=realtime），用于复盘"谁在何时打断谁"。
    # 默认关闭。环境音转写显示（🔇 前缀）与退出诊断摘要不受此开关控制，始终生效。
    realtime_event_log: bool = False
    # AI 说话期间是否对麦克风二次压低增益（默认 true）。
    # WebRTC AEC3 只能消除部分回声，残留会被服务端重新识别为用户语音，
    # 触发打断并取消本轮回复——关掉后表现为“AI 永远不开口”。
    # 仅在佩戴耳机（无扬声器回声）时才建议关闭。
    realtime_echo_suppress_with_aec: bool = True
    # 半双工开关（默认 True）：AI 说话期间不上传麦克风，说完后留一段回声尾迹
    # 静默期再恢复拾取。免提外放 + 软件 AEC 下可靠多轮的唯一方案（否则回声会
    # 让 smart_turn 误判“用户仍在说话”→ AI 永远不开下一轮）；戴耳机想随口
    # 打断时设为 False 切回全双工。
    realtime_half_duplex: bool = True
    # 响应救援（默认 True）：用户轮次已提交但服务端迟迟不创建响应、或响应被
    # turn_detected 取消后不再补答时，客户端在 1.2~1.8s 后补发一次
    # response.create（官方允许"等待用户下一轮输入时"手动触发）。兜底防
    # "贾维斯永远不回复"；若实测与预期行为冲突可关闭排查。
    realtime_rescue: bool = True
    # 轮次检测模式（默认 "server_vad"，官方免提推荐）：声学 VAD + 静音时长判停，
    # 尾音会延长当前轮而非触发新轮次，对免提外放最稳。"smart_turn" 为语义判停，
    # 可过滤"嗯/啊"等附和声，但语义判停偏早，用户尾音易被重新检出为新轮次
    # （turn_detected 取消响应），仅建议安静环境/耳机使用。
    realtime_turn_detection: str = "server_vad"
    # server_vad 判停静音时长（毫秒，范围 200~6000，默认 500）：越短响应越快，
    # 但短暂停顿会误触发；对话场景推荐 400~800。
    realtime_silence_ms: int = 500
    # 语音会话工具模式（默认 "builtin"，勿改为 "all"）：只注册 get_current_time /
    # end_conversation 两个工具，低延迟优先。"all" 装配 ToolRegistry + MCP 全量
    # 工具（实测 294 个 schema）——实机验证会导致 DashScope 实时 API 在轮次提交后
    # 迟迟不创建响应：用户说完话半天无回复，且触发「响应救援」反复补发
    # response.create 的死循环（事件链 response.done[cancelled] reason=turn_deleted）。
    # 2026-10-09 曾误将默认改为 "all" 造成实时语音完全不可用，已回退，详见
    # docs/fixlogs/realtime-talk-tools-mode-default-fix.md。
    realtime_tools_mode: str = "builtin"

    # P3-1 跨设备协同（手机通过 PWA 连接）
    bridge_http_port: int = 8765
    bridge_ws_port: int = 8766
    bridge_token: str = ""

    # UI（启动动画）
    # 启动时播放 JARVIS 蓝色方舟反应炉粒子动画。
    # 终端太窄/太矮/非 TTY 时自动跳过，回退到简单横幅。--no-boot 也可关闭。
    boot_animation: bool = True

    # 上下文压缩：总 token（冻结摘要+活跃窗口+system prompt）超窗口比例才自动摘要，
    # 防止 token 爆炸 + 撞模型上下文窗口。未超比例绝不压缩，用满窗口前半程。
    context_compaction: bool = True
    tool_result_keep_recent: int = 4   # 工具结果折叠时保留最近 N 条完整输出（其余缩成一行摘要）
    context_window: int = 128000       # 模型上下文窗口大小（token）
    compact_ratio: float = 0.5         # 自动压缩触发比例（占总窗口），0.5 = 超 50% 才压缩
    compact_refreeze_growth: float = 1.25  # 防抖：冻结后总量增长不足此倍数不重复压缩
    compact_max_output_tokens: int = 2048  # 压缩摘要请求的输出 token 上限

    # 记忆持久化（阶段四第二刀）
    # 注：曾有 auto_resume_session 启动自动恢复（全局 auto-latest 指针，
    # 不区分项目目录、多项目串台），已下线；历史会话靠 /load 手动恢复。
    # 启动时加载长期记忆注入 system prompt（~/.jarvis/MEMORY.md + 项目级）
    long_term_memory: bool = True

    # 画像记忆（长期记忆 Phase 1a：自动从会话提炼用户偏好/习惯，
    # 存 ~/.jarvis/memory/profile.json，注入 system prompt）
    profile_enabled: bool = True
    profile_max_entries: int = 200             # 条目上限（超出按置信度×新近度淘汰）
    profile_inject_token_limit: int = 300      # 注入 system prompt 的 token 硬限额
    profile_refine_min_messages: int = 6       # 会话至少多少条消息才触发提炼
    profile_refine_interval: int = 600         # 两次提炼的最小间隔（秒），防止频繁无意义提炼
    # 独立提炼模型（便宜模型跑后台提炼；留空 = 用主 LLM 配置）
    profile_refine_model: str = ""
    profile_refine_provider: str = ""          # api_format，如 openai/dashscope
    profile_refine_base_url: str = ""
    profile_refine_api_key: str = ""

    # Skill 系统（阶段四第三刀）
    # 启动时加载 ~/.jarvis/skills/*/SKILL.md + 项目级，注入 system prompt
    enable_skills: bool = True

    # MCP 接入（阶段四第三刀）
    # 启动时连接 ~/.jarvis/mcp.json 配置的 MCP server，注册其工具
    enable_mcp: bool = True

    # 知识图谱画像桥（P2 画像双向同步，upgrade-plan.md §4）
    # 启动时经 MCP 拉取 aceFelix 知识图谱画像注入 system prompt（图谱为唯一事实源），
    # /memory sync 可把本地画像经图谱抽取管线回写。默认关闭，配置后开启。
    profile_bridge_enabled: bool = False
    profile_bridge_server: str = "acefelix-knowledge"  # mcp.json 中的 server 名
    profile_bridge_token_limit: int = 400              # 图谱画像段注入 token 限额

    # 工具错误自愈（P0：维度 5 工具执行稳定性）
    # 工具调用失败时自动分类、重试、降级、询问用户，而不是直接抛错给 LLM。
    enable_tool_self_healing: bool = True  # 总开关
    tool_retry_max: int = 3                # 默认最大重试次数
    tool_retry_backoff_base: float = 1.0   # 指数退避基数（秒）
    tool_retry_backoff_max: float = 30.0   # 最大退避时间（秒）

    # 插件市场（阶段五第五刀）
    # /plugin search/install/uninstall 管理插件。安装的插件 skills
    # 写入 ~/.jarvis/skills/，MCP 配置合并到 ~/.jarvis/mcp.json。
    enable_plugins: bool = True
    plugin_marketplace: str = ""           # 远程 marketplace.json URL
    plugin_market_local: str = ""          # 本地插件市场目录（绝对或相对路径）

    # CLI-Anything 自定义市场
    # 自定义 harness 市场源（优先远程，回退本地）。
    # market_url: GitHub raw 前缀，如 https://raw.githubusercontent.com/user/jarvis-harness-market/main
    # market_local: 本地市场仓库路径（绝对或相对于 jarvis 项目目录）
    harness_market_url: str = ""
    harness_market_local: str = ""

    # 邮件发送（EmailTool）
    # 用于 Jarvis 主动向用户发送邮件提醒、摘要、附件等。
    # smtp_password 通常填写邮箱授权码（如 163/QQ 邮箱），而非登录密码。
    email_enabled: bool = False
    email_smtp_host: str = "smtp.163.com"
    email_smtp_port: int = 465
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_sender: str = ""
    email_default_recipient: str = ""
    # 邮件**收件**（W2 mail 事件源，决策 3：IMAP IDLE 长连接 + 断线降级轮询）。
    # 只读取信封头（FROM/SUBJECT/DATE），永不拉正文；imap_password 留空时
    # 源回退到 smtp_password（163/QQ 等厂商两边用同一个授权码，不强制用户录两遍）。
    # @author aceFelix
    email_imap_host: str = ""
    email_imap_port: int = 993
    email_imap_user: str = ""
    email_imap_password: str = ""
    email_imap_mailbox: str = "INBOX"

    # LSP 集成
    # 启动时按 [lsp.servers.<name>] 配置启动语言服务器
    # .py → pylsp/pyright, .ts → typescript-language-server 等
    enable_lsp: bool = True
    lsp_servers: dict[str, dict] = field(default_factory=dict)

    # 全局热键（为新 GUI 工作台保留：热键召唤/置顶窗口）。
    # 原"无窗口 daemon + 托盘"常驻模式已下线，热键配置在 GUI 阶段继续生效。
    # 作者：aceFelix
    daemon_hotkey: str = "ctrl+shift+j"        # 全局热键（keyboard 库格式）
    daemon_hotkey_native: bool = True            # Windows 优先使用 RegisterHotKey（更快）
    daemon_hotkey_debounce_ms: int = 200         # 热键去抖毫秒，防双击触发

    # 快速启动模式（P1-2 热键响应优化）
    # --quick 时启用，用于跳过可选初始化、延迟加载 harness
    quick_start: bool = False

    # 主动感知（阶段五第三刀）
    # 系统资源监控：CPU/内存/磁盘超阈值时通知+语音告警。
    # 依赖 psutil。enabled=false 关闭监控。
    monitor_enabled: bool = True
    monitor_cpu_threshold: float = 85.0    # CPU 使用率 %，超过告警
    monitor_memory_threshold: float = 90.0 # 内存使用率 %
    monitor_disk_threshold: float = 10.0   # 磁盘剩余 %，低于告警
    monitor_check_interval: int = 10       # 检查间隔（秒）
    monitor_alert_cooldown: int = 600      # 同类告警冷却（秒）
    # P2-3 监控增强
    monitor_disk_trend_days: int = 7       # 磁盘趋势预测：预测几天后将满
    monitor_high_cpu_duration: int = 600   # 异常进程：高 CPU 持续秒数阈值
    monitor_work_break_interval: int = 7200  # 工作时长：连续工作多少秒提醒休息

    # 主动提醒系统（P2-3）
    # 每日简报：每天定时播报今日概览（提醒/节假日/系统状态/截止日期/日程）
    briefing_enabled: bool = True
    briefing_time: str = "08:30"           # 每日简报时间（HH:MM）
    briefing_catchup_window_min: int = 120  # 简报补播窗口（分钟）：启动时错过 ≤ 此值才补播一次
    # 主动播报 TTS（二期 2026-09）：proactive_notify 触发时并行用本机 CosyVoice 朗读，
    # 复活老 daemon 三通道播报中的「待机 TTS」通道；对话/语音忙时跳过朗读（不打断），
    # 事件通道（桌面壳气泡 + 系统通知）不受此开关影响。作者：aceFelix
    proactive_tts_enabled: bool = True
    # 截止日期追踪：注册 deadline，分级提醒（7/3/1/0 天 + 逾期每天）
    deadline_enabled: bool = True
    deadline_check_time: str = "09:00"     # 每日检查截止日期的时间
    # 日历集成：读取 Outlook/ICS 日历事件，提前提醒
    calendar_enabled: bool = False
    calendar_backend: str = "auto"         # auto / outlook / ics
    calendar_ics_path: str = ""            # 本地 .ics 文件路径
    calendar_ics_url: str = ""             # 远程 .ics 订阅 URL
    calendar_remind_minutes_before: int = 30  # 事件前多少分钟提醒

    # 安全沙箱（P3-8）
    # 高风险操作在隔离环境中运行，防止误操作破坏系统。
    # 基于 Windows Job Object 限制进程资源（内存/CPU/进程数）。
    sandbox_enabled: bool = False
    sandbox_max_memory_mb: int = 512       # 沙箱内最大内存（MB）
    sandbox_max_cpu_seconds: int = 60      # 沙箱内最大 CPU 时间（秒）
    sandbox_max_processes: int = 10        # 沙箱内最大子进程数
    sandbox_timeout: int = 120             # 沙箱命令总超时（秒）
    sandbox_block_network: bool = False    # 是否阻断沙箱内网络访问
    sandbox_auto_allow_medium: bool = True # 沙箱开启时自动放行中等风险命令
    sandbox_excluded_commands: list = field(default_factory=list)  # 不走沙箱的命令
    sandbox_audit: bool = True             # 是否记录沙箱审计日志
    sandbox_max_snapshots: int = 20        # 文件快照最大保留数

    # 消息级回溯检查点（shadow git，见 core/checkpoint.py）：每轮对话前
    # 对工作目录打检查点，/rewind / 桌面撤回可连带回滚文件。@author aceFelix
    checkpoint_enabled: bool = True        # 总开关（关闭后仅支持对话回退）
    checkpoint_max_per_session: int = 20   # 每会话检查点保留上限（超出修剪最旧）
    checkpoint_timeout_seconds: int = 10   # 单条 git 命令超时（秒）

    # W1 运行时层（阶段 8，见 core/policy/）：授权租约 + 打扰预算 +
    # 无人值守。挂在五层管线之后；声明式规则已并入管线 permissions.yaml。
    # 旧字段 policy_enabled/policy_path 已随策略引擎废弃删除，旧配置残留
    # 对应键静默忽略（白名单映射）。
    # @author aceFelix
    away_idle_minutes: int = 0             # 0=仅认手动 /away；>0 启用空闲推断自动 away
    notify_budget_enabled: bool = True     # 打扰预算闸门（关闭=提醒不经预算直接播）
    notify_daily_max: int = 10             # 每日主动播报上限
    notify_quiet_hours: str = "23:00-07:00"  # 静默时段（支持跨午夜；空串=不启用）
    notify_aggregate_window_s: int = 300   # 同源事件聚合窗口（秒）
    notify_min_salience: float = 0.3       # 重要度门槛（低于此值直接抑制、只留痕，**不入队也不补播**）
    # 静默时段/额度用尽时，**用户自设提醒**（权重表键 `schedule`）的送达通道。
    # 默认只降级不延后：气泡与系统通知照投、TTS 不朗读——“10 分钟后提醒我喝水”
    # 这种话整条押到下一次有别的通知才播，等于丢了（冲刷靠新请求触发）。
    # 可选值："screen"=上屏不出声 / "email"=另发一封邮件（收件人见
    # notify_quiet_email_to）/ "sms"=留位，尚未接通道，配了会警告并忽略。
    # 空列表 = 关掉降级，退回“入库待补播”的老行为（简报/感知事件本来就走这条）。
    # @author aceFelix
    notify_reminder_channels: list = field(
        default_factory=lambda: ["screen"]
    )
    notify_quiet_email_to: str = ""        # 邮件通道收件人（留空 = 用 [email].default_recipient）

    # W2 感知层（阶段 9，见 core/percept/）：统一事件总线 + 逐源采集。
    # 隐私口径已拍板（PLAN 第 8 节决策 2）：总开关与**逐源开关全部默认关**，
    # 未获同意时源的 collect() 一次都不会被调用；默认值一律偏保守（关/少/短）。
    # @author aceFelix
    percept_enabled: bool = False          # 感知层总开关（关=不起线程、不采一个字节）
    percept_dedup_window_s: int = 300      # 同一事件去重窗口（秒）
    percept_min_salience: float = 0.0      # 总线地板分（0=不裁，重要度裁决交给 W1）
    percept_history_max: int = 200         # 内存环形历史条数（留痕行数上限=此值×10）
    percept_log_events: bool = True        # 事件本地留痕（false=完全不碰磁盘）
    percept_auto_disable_after_errors: int = 3  # 源连败自动停用阈值（0=只隔离不罢工）

    # 前台窗口源——唯一有明显隐私属性的源，默认只留「进程名 + 标题前 40 字」
    percept_window_enabled: bool = False
    percept_window_interval_s: int = 5     # 采样间隔（秒）
    percept_window_title_max_chars: int = 40
    percept_window_title_full_enabled: bool = False   # 显式开才保留完整标题
    percept_window_exclude_apps: list = field(default_factory=list)  # 排除应用（子串、不分大小写）
    percept_window_linger_minutes: int = 0  # >0 才发「在同一应用待太久」

    # 文件守望源——只订阅白名单目录（空列表=该源即使开了也不采）
    percept_file_enabled: bool = False
    percept_file_watch_dirs: list = field(default_factory=list)
    percept_file_interval_s: int = 30
    percept_file_max_entries: int = 2000   # 单目录快照条目上限（超出截断并记 truncated）
    percept_file_grow_threshold: int = 5   # 新增文件达此数才聚合提醒（避免一个个刷）
    percept_file_ignore_suffixes: list = field(default_factory=list)  # 额外忽略后缀
    percept_file_min_interval_s: int = 0   # 文件源同源投递最小间隔（0=不额外节流）

    # 仓库与邮件源（第 3 步）、既有源收编（第 4 步）
    percept_vcs_enabled: bool = False
    percept_vcs_repos: list = field(default_factory=list)
    percept_vcs_interval_s: int = 120
    percept_vcs_min_interval_s: int = 60   # 同源投递最小间隔（总线节流）
    percept_mail_enabled: bool = False
    percept_mail_min_interval_s: int = 0   # 0=靠 IDLE 事件驱动，不额外节流
    percept_mail_watch_senders: list = field(default_factory=list)  # 发件人白名单（空=全报）
    # 收编型源（阶段 11 接入 calendar/system；schedule 仍留位，理由见
    # core/percept/factory.py 的 PENDING_SOURCES 注释）：底层能力本来就在 daemon/ 里
    # 休眠，这里只放“要不要采”的开关——后端与阈值一律复用既有字段，
    # 不另造一份真相（否则面板调的那个值压根不是真实生效值）
    percept_calendar_enabled: bool = False  # 后端/提前量复用 [calendar] 段
    percept_system_enabled: bool = False    # 阈值复用 monitor_* 那一组字段
    percept_schedule_enabled: bool = False  # 留位未接入（会重复播报，详见 factory.py）
    # 视觉在场源（唯一会碰摄像头的源，默认关）：在场事实与手势进总线
    percept_vision_enabled: bool = False
    percept_vision_camera_index: int = 0
    percept_vision_fps: int = 2          # 低帧率：常驻 CPU 成本按帧率线性增长
    percept_vision_gesture_enabled: bool = True
    percept_vision_auto_stop_minutes: int = 0   # >0 空闲自动关摄像头（代价：关闭期间检测不到回来）
    percept_vision_interval_s: int = 5          # 总线排空本源的节拍（秒）

    # W3 兑现层（阶段 10，见 core/actions/journal.py 与 core/tasks/spine.py）：
    # 副作用账本 + 不可逆动作延时窗口 + 跨会话任务脊柱。
    # 两个默认值取向不同是有意的：账本**默认开**，它是"敢放手"的前提，关掉等于
    # 把已经发生的副作用记不住，/undo 就无从补偿；任务脊柱**默认关**，它会自主调
    # LLM 花钱、还会自己动手执行，必须用户显式授权（同 W2 逐源默认关的口径）。
    # @author aceFelix
    actions_journal_enabled: bool = True        # 副作用账本总开关（关=不记账也不延时）
    actions_undo_window_s: int = 30             # 不可逆动作延时窗口（秒）；0=不拦直接执行
    actions_track_local_writes: bool = True     # 本地破坏性写是否记一条可回滚条目
    actions_irreversible_tools: list = field(default_factory=list)  # 额外当不可逆处理的工具名
    actions_hold_blocks_equivalent: bool = True  # 待发窗口未结案时，等价外发命令不再自动放行

    tasks_enabled: bool = False                 # 任务脊柱驱动器总开关（关=只读台账不自走）
    tasks_blocked_ask_days: int = 3             # 阻塞超此天数主动问一次（走 W1 预算闸门）
    tasks_step_max_attempts: int = 3            # 单步失败重试上限
    tasks_retry_backoff_s: int = 60             # 重试退避间隔（秒）；本期固定间隔不翻倍
    tasks_wait_deadline_hours: int = 72         # 条件等待最长时限（超时转 blocked_timeout 来问）
    # 后台值守每推一步打一个 shadow git 检查点（改动可回退）；该配额与
    # [checkpoint].max_per_session 分开计，避免把 /rewind 的消息级回溯挤没
    tasks_checkpoint_max: int = 8

    # 工具延迟加载
    # 核心工具始终携带，MCP/harness/可选工具仅发名字摘要，通过 ToolSearch 按需加载。
    # 纯聊天检测：短消息 + 无动作意图 + 不含文件名/路径形态 → 不发任何工具（0 token）。
    # 结构判据是 2026-10-22 加的：只枚举口语说法补不全，误判会让本轮 0 工具、
    # 模型改吐文本态 DSML（见 docs/fixlogs/deepseek-dsml-toolcall-leak-fix.md）。
    tools_deferred_loading: bool = True    # 总开关（false = 回退到全量发送）
    tools_chat_detection: bool = True      # 纯聊天零工具检测

    # 版本自更新（Phase 1）：启动时后台检查 PyPI 最新版并提示，/update 一键升级。
    # 仅检查不自动改文件，避免运行中替换自身；可用 --no-update-check 或
    # JARVIS_UPDATE_CHECK=0 关闭。@author aceFelix
    update_check: bool = True

    def with_overrides(self, **kwargs: object) -> "Settings":
        """返回一个用 kwargs 覆盖部分字段的新 Settings（None 值不覆盖）。"""
        data = {k: v for k, v in kwargs.items() if v is not None}
        new = Settings(**{**self.__dict__, **data})
        return new


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            raw = f.read()
        # 兼容 Windows 编辑器/PowerShell 写入的 UTF-8 BOM
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return tomllib.loads(raw.decode("utf-8"))
    except Exception as e:
        # 解析失败不能静默吞掉：否则用户配置不生效且无任何线索（作者：aceFelix）。
        # 输出到 stderr 提醒修复配置文件，不阻断启动（返回空配置回退默认值）。
        print(
            f"\u26a0\ufe0f  配置文件解析失败，已忽略并使用默认值：{path}\n   原因：{e}",
            file=sys.stderr,
        )
        return {}


def load_settings(workdir: str | None = None) -> Settings:
    """加载配置，按优先级合并。

    workdir 用于解析相对配置路径，并作为默认工作目录。

    项目配置查找策略（按优先级）：
    1. ``<workdir>/configs/settings.toml`` — 先从工作目录找（支持多项目各自配置）
    2. ``<agent_pkg>/../configs/settings.toml`` — 回退到 agent 包同级（贾维斯自身配置）
    3. 不存在则使用默认值

    同层模型域文件 ``models.toml`` 紧随其后应用（模型域专属文件优先）；
    用户级同样先 settings.toml 后 models.toml，整体覆盖项目级。
    """
    cwd = Path(workdir or os.getcwd()).resolve()
    # agent 包所在的项目根目录（用于回退查找配置）
    _pkg_root = Path(__file__).resolve().parent.parent.parent  # settings.py → config → agent → project_root

    # 1. 默认值
    s = Settings(workdir=str(cwd))

    # 2. 项目级 configs/settings.toml
    # 发布场景下不打包用户私有的 settings.toml，只提供 settings.example.toml 作为默认模板。
    # 查找顺序：
    #   1) <workdir>/configs/settings.toml（用户项目级覆盖）
    #   2) <pkg_root>/configs/settings.toml（开发者本地配置）
    #   3) <pkg_root>/agent/configs/settings.example.toml（发布版默认模板）
    #   4) 缺失则使用 Settings 默认值
    project_cfg = cwd / "configs" / "settings.toml"
    if not project_cfg.is_file():
        # workdir 不是项目根目录时（如 daemon --workdir 指定了别的工作目录），
        # 退回到 agent 包同级找贾维斯的自身配置
        pkg_cfg = _pkg_root / "configs" / "settings.toml"
        if pkg_cfg.is_file():
            project_cfg = pkg_cfg
    if not project_cfg.is_file():
        # 发布 wheel 中 configs/ 不在 site-packages 根目录，
        # 默认模板作为 agent 包的 package-data 放在 agent/configs/ 下。
        example_cfg = _pkg_root / "agent" / "configs" / "settings.example.toml"
        if example_cfg.is_file():
            project_cfg = example_cfg
    s = _apply_toml(s, _read_toml(project_cfg))
    # 2b. 项目级同层 models.toml（模型域：模型选择/密钥/可选模型/自定义模型）
    # 拆分动机见 agent/config/models_config.py——模型配置与程序回写内容独立成文件，
    # 避免顶层键落进 [llm.models] 或重复 section 破坏整份配置。作者：aceFelix
    s = _apply_toml(s, _read_toml(models_path_for(project_cfg)))

    # 3. 用户级 ~/.jarvis/settings.toml（兼容 ~/.my-agent/）
    # 成对解析 settings/models 路径：两者必须同目录，否则模型域配置会读不到。
    user_cfg, user_models_cfg = user_config_paths()
    # 迁移用户级配置（schema 升级时自动应用，失败不阻塞启动）
    if user_cfg.exists():
        try:
            from agent.config.migrations import run_migrations
            run_migrations(user_cfg)
        except Exception:
            pass  # 迁移失败不影响启动，诊断日志已记录
        # 模型域内容拆分到同层 models.toml（幂等；老配置留在原处仍生效，失败静默）
        auto_split_user_config(user_cfg)
    s = _apply_toml(s, _read_toml(user_cfg))
    # 3b. 用户级 models.toml（同层覆盖 settings.toml，用户级整体覆盖项目级）
    s = _apply_toml(s, _read_toml(user_models_cfg))

    # 4. 环境变量
    from agent.config.env import apply_env_overrides
    s = apply_env_overrides(s)

    # 5. 权限文件默认路径
    # 查找顺序：workdir/configs → pkg_root/configs → pkg_root/agent/configs（发布版）
    if not s.permissions_file:
        for candidate_dir in (cwd, _pkg_root, _pkg_root / "agent"):
            candidate = candidate_dir / "configs" / "permissions.yaml"
            if candidate.exists():
                s.permissions_file = str(candidate)
                break

    # 6. 解析本地市场目录的相对路径
    # plugin_market_local / harness_market_local 允许相对于 workdir 或 jarvis 项目根目录
    for field_name in ("plugin_market_local", "harness_market_local"):
        raw = getattr(s, field_name, "")
        if raw:
            resolved = _resolve_local_path(raw, cwd, _pkg_root)
            if resolved:
                s = s.with_overrides(**{field_name: str(resolved)})

    # last_model 是用户上次手动切换的模型，优先级高于 config 文件中的 model。
    # 若 last_model 是自定义模型，同时恢复其 base_url/api_key/provider_type，
    # 这样启动时 _build_provider 构造的就是自定义模型的 provider，
    # banner 显示的 provider.name 也会正确（如 deepseek 而非 dashscope）。
    # 同时保存原始 provider/api_format/base_url/api_key 到 default_* 字段，
    # 供 _switch_model 切回内置模型时恢复（避免停留在自定义模型配置上）。
    if s.last_model:
        # 先保存原始值（last_model 覆盖前的 settings.toml 值）
        s = s.with_overrides(
            default_provider=s.provider,
            default_api_format=s.api_format,
            default_base_url=s.base_url,
            default_api_key=s.api_key,
        )
        if s.last_model in s.custom_models:
            cfg = s.custom_models[s.last_model]
            # 兼容旧配置字段名 provider_type → api_format
            api_fmt = cfg.get("api_format") or cfg.get("provider_type", s.api_format)
            # 自定义模型的厂商（provider 字段，供思考能力等按厂商判定）：优先取
            # provider，其次 vendor，最后才退回 api_format（传输协议）。旧配置常把
            # 厂商记在 vendor、provider_type 记传输协议（如 deepseek 走 anthropic 兼容
            # 端点），只按 api_format 兜底会把厂商误判成 anthropic。@author aceFelix
            provider = cfg.get("provider") or cfg.get("vendor") or api_fmt
            s = s.with_overrides(
                model=s.last_model,
                provider=provider,
                api_format=api_fmt,
                base_url=cfg.get("base_url", s.base_url) or s.base_url,
                api_key=cfg.get("api_key", s.api_key) or s.api_key,
            )
        else:
            # 内置模型：只覆盖 model，provider/base_url 用 config 默认值
            s = s.with_overrides(model=s.last_model)

    return s


def _resolve_local_path(raw: str, cwd: Path, pkg_root: Path) -> Path | None:
    """解析本地市场目录路径。

    优先按原样（绝对路径直接使用），否则相对于 cwd 解析；
    若 cwd 解析后的路径不存在，则回退到相对于 jarvis 项目根目录解析。

    Returns:
        解析后的绝对路径；若均不存在则返回 cwd 下的解析结果（让用户看到原路径）。

    @author aceFelix
    """
    p = Path(raw)
    if p.is_absolute():
        return p
    candidate = (cwd / raw).resolve()
    if candidate.exists():
        return candidate
    fallback = (pkg_root / raw).resolve()
    if fallback.exists():
        return fallback
    return candidate


def _apply_toml(s: Settings, data: dict) -> Settings:
    """把 TOML 顶层字段与各功能域表映射到 Settings。

    表 → 字段的映射是纯数据，声明在 agent/config/settings_domains.py（原先是
    20+ 个同构循环，加一个域就抄一遍，文件因此突破 800 行上限）。本函数只保留
    顶层平铺键、permission_mode 解析与 [llm] 模型域归一这三处真正的逻辑。
    """
    if not data:
        return s
    updates: dict[str, object] = {}
    for key in (
        "provider", "api_format", "model", "last_model", "api_key", "base_url",
        "dashscope_api_key",
        "max_tokens", "temperature",
        "max_iterations",
        "permissions_file", "system_prompt_append",
        "debug", "verbose",
        "tts_model", "tts_voice", "tts_volume", "tts_speech_rate", "tts_pitch_rate",
        "stt_model", "stt_max_seconds", "stt_silence_seconds",
        "voice_max_seconds",
        "realtime_ws_url", "realtime_model", "realtime_voice", "realtime_event_log",
        "realtime_echo_suppress_with_aec",
        "realtime_half_duplex",
        "realtime_rescue",
        "realtime_turn_detection", "realtime_silence_ms", "realtime_tools_mode",
        "bridge_http_port", "bridge_ws_port", "bridge_token",
        "boot_animation",
        "context_compaction",
        "context_window", "compact_ratio",
        "compact_refreeze_growth", "compact_max_output_tokens",
        "long_term_memory",
        "enable_skills",
        "enable_mcp",
        "enable_plugins", "plugin_marketplace", "plugin_market_local",
        "harness_market_url", "harness_market_local",
        "enable_tool_self_healing", "tool_retry_max",
        "tool_retry_backoff_base", "tool_retry_backoff_max",
        "enable_lsp",
        "enable_thinking", "thinking_budget", "thinking_effort",
        "vendor_fallback",
        "daemon_hotkey", "daemon_hotkey_native", "daemon_hotkey_debounce_ms",
    ):
        if key in data:
            updates[key] = data[key]
    if "permission_mode" in data:
        updates["permission_mode"] = parse_mode(str(data["permission_mode"]))
    # 各功能域表（[tts]/[stt]/[voice]/[monitor]/[sandbox]/[policy]/[percept]/…）
    # → Settings 字段，映射表见 settings_domains.py；表缺失整表跳过
    apply_domain_tables(data, updates)
    # [llm] 表 → models 列表 + 自定义模型配置
    # [llm] 表 → models 列表 + 自定义模型配置（需归一化旧字段名，是真正的逻辑，
    # 故不入 settings_domains.py 的声明式映射）
    llm_table = data.get("llm", {})
    if isinstance(llm_table, dict):
        models_table = llm_table.get("models", {})
        if isinstance(models_table, dict) and models_table:
            updates["models"] = models_table
        custom_table = llm_table.get("custom_models", {})
        if isinstance(custom_table, dict) and custom_table:
            # 向后兼容：旧配置用 provider_type，新配置用 api_format
            normalized: dict[str, dict] = {}
            for cname, cfg in custom_table.items():
                c = dict(cfg)
                if "provider_type" in c and "api_format" not in c:
                    c["api_format"] = c.pop("provider_type")
                # provider（厂商，用于思考能力等按厂商判定）缺失时：优先取 vendor
                # 字段，再退回 api_format（传输协议）。只按 api_format 兜底会把
                # “deepseek 走 anthropic 兼容端点”这类模型的厂商误判成 anthropic，
                # 导致桌面思考选择器置灰。@author aceFelix
                if "provider" not in c:
                    c["provider"] = c.get("vendor") or c.get("api_format", "openai")
                normalized[cname] = c
            updates["custom_models"] = normalized
    # 注：[update] 表的 check 键已由 apply_domain_tables 强制 bool 处理
    return s.with_overrides(**updates)


# ── 以下函数已拆分到独立模块，此处保留重导出以兼容现有导入 ──
from agent.config.model_registry import save_custom_model, save_last_model  # noqa: E402, F401
from agent.config.model_registry import save_custom_voice, save_tts_voice, save_tts_model  # noqa: E402, F401
from agent.config.model_registry import remove_custom_voice  # noqa: E402, F401
from agent.config.models_config import (  # noqa: E402, F401
    auto_split_user_config as auto_split_user_config,
    models_path_for as models_path_for,
    split_model_config as split_model_config,
    user_config_paths as user_config_paths,
)
