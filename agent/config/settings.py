"""配置加载。

配置来源（文件/环境变量/MDM/keychain/CLI 参数），
v0.1 用 TOML 文件 + 环境变量两层即可。

配置查找顺序（后者覆盖前者）:
1. 内置默认值
2. configs/settings.toml（项目级，随仓库分发）
3. ~/.jarvis/settings.toml（用户级；兼容回退 ~/.my-agent/）
4. 环境变量（JARVIS_* 前缀；兼容 MY_AGENT_*）
5. CLI 参数（在 main.py 里处理）
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
    # 通过 /models → 添加其他模型 创建，持久化到 ~/.jarvis/settings.toml [llm.custom_models]
    custom_models: dict[str, dict] = field(default_factory=dict)
    # 用户自定义 TTS 音色 {voice_name: {voice_id, description, vendor}}
    # 通过 /tts-voice → 添加音色 创建，持久化到 ~/.jarvis/settings.toml [tts.custom_voices]
    custom_voices: dict[str, dict] = field(default_factory=dict)
    # 深度思考（思维链）—— enable_thinking 通过 extra_body 传给 DashScope
    enable_thinking: bool = True
    thinking_budget: int = 2000
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
    # STT（阶段三第二刀）
    stt_model: str = "paraformer-realtime-v2"
    stt_max_seconds: float = 15.0
    stt_silence_seconds: float = 1.5
    stt_silence_threshold: int = 500
    # 语音模式最长录音时间（/voice 模式），超时自动提交。默认 5 分钟
    voice_max_seconds: float = 300.0
    # 语音模式选项（阶段三第四刀）
    # 语音打断：TTS 播报期间短录检测"闭嘴""等一下"等中断词
    # 可能偶尔因 PyAudio 冲突静默失败（不影响对话），ESC 仍可用
    voice_barge_in: bool = True
    # barge_in_key（键盘打断）默认启用：TTS 播报期间用 keyboard 库全局钩子监听 ESC，
    # 按下立即停止播报并切回聆听。不占 PyAudio，无 segfault 风险，daemon 无窗口也能捕获。
    voice_barge_in_key: bool = True

    # 实时双工语音对话（/talk）
    # endpoint 默认使用 DashScope 公共域名；如使用业务空间专属域名，在此覆盖
    realtime_ws_url: str = ""
    realtime_model: str = "qwen-audio-3.0-realtime-flash"
    realtime_voice: str = "longanqian"
    realtime_talk_auto_start: bool = False

    # P3-1 跨设备协同（手机通过 PWA 连接）
    bridge_http_port: int = 8765
    bridge_ws_port: int = 8766
    bridge_token: str = ""

    # UI（启动动画）
    # 启动时播放 JARVIS 蓝色方舟反应炉粒子动画。
    # 终端太窄/太矮/非 TTY 时自动跳过，回退到简单横幅。--no-boot 也可关闭。
    boot_animation: bool = True

    # 上下文压缩（阶段四第一刀）
    # 对话历史超阈值时自动摘要旧消息，保留最近 N 条原消息。
    # 防止 token 爆炸 + 撞模型上下文窗口。致敬 ClaudeCode services/compact/。
    context_compaction: bool = True
    compaction_threshold: int = 8000   # 估算 token 超此值触发压缩
    keep_recent_messages: int = 6      # 压缩时保留最近 N 条原消息
    tool_result_keep_recent: int = 4   # 工具结果折叠时保留最近 N 条完整输出（其余缩成一行摘要）

    # 记忆持久化（阶段四第二刀）
    # 启动时自动恢复最近会话（/resume 也可手动恢复）
    auto_resume_session: bool = False
    # 启动时加载长期记忆注入 system prompt（~/.jarvis/MEMORY.md + 项目级）
    long_term_memory: bool = True

    # 画像记忆（长期记忆 Phase 1a：自动从会话提炼用户偏好/习惯，
    # 存 ~/.jarvis/memory/profile.json，注入 system prompt）
    profile_enabled: bool = True
    profile_max_entries: int = 200             # 条目上限（超出按置信度×新近度淘汰）
    profile_inject_token_limit: int = 300      # 注入 system prompt 的 token 硬限额
    profile_refine_min_messages: int = 6       # 会话至少多少条消息才触发提炼
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

    # LSP 集成（对标 Claude Code）
    # 启动时按 [lsp.servers.<name>] 配置启动语言服务器
    # .py → pylsp/pyright, .ts → typescript-language-server 等
    enable_lsp: bool = True
    lsp_servers: dict[str, dict] = field(default_factory=dict)

    # 常驻模式（阶段五第一刀）
    # jarvis --daemon 后台常驻，热键/托盘唤起
    daemon_hotkey: str = "ctrl+shift+j"        # 全局热键（keyboard 库格式）
    daemon_hotkey_native: bool = True            # Windows 优先使用 RegisterHotKey（更快）
    daemon_hotkey_debounce_ms: int = 200         # 热键去抖毫秒，防双击触发
    daemon_text_terminal_warm: bool = False      # 预启动隐藏文本终端（极速唤起，但常驻内存）
    daemon_tray: bool = True                     # 是否启用系统托盘图标

    # 快速启动模式（P1-2 热键响应优化）
    # --quick 时启用，用于跳过可选初始化、延迟加载 harness
    quick_start: bool = False

    # 主动感知（阶段五第三刀）
    # 系统资源监控：CPU/内存/磁盘超阈值时托盘通知+语音告警。
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

    # 工具延迟加载（参考 Claude Code deferred tool loading）
    # 核心工具始终携带，MCP/harness/可选工具仅发名字摘要，通过 ToolSearch 按需加载。
    # 纯聊天检测：短消息 + 无动作意图 → 不发任何工具（0 token）。
    tools_deferred_loading: bool = True    # 总开关（false = 回退到全量发送）
    tools_chat_detection: bool = True      # 纯聊天零工具检测

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
    except Exception:
        return {}


def load_settings(workdir: str | None = None) -> Settings:
    """加载配置，按优先级合并。

    workdir 用于解析相对配置路径，并作为默认工作目录。

    项目配置查找策略（按优先级）：
    1. ``<workdir>/configs/settings.toml`` — 先从工作目录找（支持多项目各自配置）
    2. ``<agent_pkg>/../configs/settings.toml`` — 回退到 agent 包同级（贾维斯自身配置）
    3. 不存在则使用默认值
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

    # 3. 用户级 ~/.jarvis/settings.toml（兼容 ~/.my-agent/）
    user_cfg = Path.home() / ".jarvis" / "settings.toml"
    if not user_cfg.exists():
        user_cfg = Path.home() / ".my-agent" / "settings.toml"
    # 迁移用户级配置（schema 升级时自动应用，失败不阻塞启动）
    if user_cfg.exists():
        try:
            from agent.config.migrations import run_migrations
            run_migrations(user_cfg)
        except Exception:
            pass  # 迁移失败不影响启动，诊断日志已记录
    s = _apply_toml(s, _read_toml(user_cfg))

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
            provider = cfg.get("provider", api_fmt)  # 自定义模型的 provider（vendor）字段
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
    """把 TOML 顶层字段映射到 Settings。

    [tts] 表的子字段映射到 tts_* 顶层字段。
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
        "stt_model", "stt_max_seconds", "stt_silence_seconds", "stt_silence_threshold",
        "voice_max_seconds",
        "realtime_ws_url", "realtime_model", "realtime_voice",
        "realtime_talk_auto_start",
        "bridge_http_port", "bridge_ws_port", "bridge_token",
        "boot_animation",
        "context_compaction", "compaction_threshold", "keep_recent_messages",
        "auto_resume_session", "long_term_memory",
        "enable_skills",
        "enable_mcp",
        "enable_plugins", "plugin_marketplace", "plugin_market_local",
        "harness_market_url", "harness_market_local",
        "enable_tool_self_healing", "tool_retry_max",
        "tool_retry_backoff_base", "tool_retry_backoff_max",
        "enable_lsp",
        "enable_thinking", "thinking_budget",
        "vendor_fallback",
        "daemon_hotkey", "daemon_hotkey_native", "daemon_hotkey_debounce_ms",
        "daemon_text_terminal_warm", "daemon_tray",
    ):
        if key in data:
            updates[key] = data[key]
    if "permission_mode" in data:
        updates["permission_mode"] = parse_mode(str(data["permission_mode"]))
    # [tts] 表 → tts_* 字段
    tts_table = data.get("tts", {})
    if isinstance(tts_table, dict):
        for sub_key, field in (
            ("model", "tts_model"),
            ("voice", "tts_voice"),
            ("volume", "tts_volume"),
            ("speech_rate", "tts_speech_rate"),
            ("pitch_rate", "tts_pitch_rate"),
        ):
            if sub_key in tts_table:
                updates[field] = tts_table[sub_key]
        # [tts.custom_voices] 子表 → settings.custom_voices（/tts-voice 添加的音色）
        cv_table = tts_table.get("custom_voices")
        if isinstance(cv_table, dict) and cv_table:
            updates["custom_voices"] = {
                str(k): dict(v) for k, v in cv_table.items() if isinstance(v, dict)
            }
    # [stt] 表 → stt_* 字段
    stt_table = data.get("stt", {})
    if isinstance(stt_table, dict):
        for sub_key, field in (
            ("model", "stt_model"),
            ("max_seconds", "stt_max_seconds"),
            ("silence_seconds", "stt_silence_seconds"),
            ("silence_threshold", "stt_silence_threshold"),
        ):
            if sub_key in stt_table:
                updates[field] = stt_table[sub_key]
    # [voice] 表 → voice_* 字段
    voice_table = data.get("voice", {})
    if isinstance(voice_table, dict):
        for sub_key, field in (
            ("barge_in", "voice_barge_in"),
            ("barge_in_key", "voice_barge_in_key"),
        ):
            if sub_key in voice_table:
                updates[field] = voice_table[sub_key]
    # [realtime_talk] 表 → realtime_* 字段 + dashscope_api_key
    rt_table = data.get("realtime_talk", {})
    if isinstance(rt_table, dict):
        for sub_key, field in (
            ("api_key", "dashscope_api_key"),
            ("ws_url", "realtime_ws_url"),
            ("model", "realtime_model"),
            ("voice", "realtime_voice"),
            ("auto_start", "realtime_talk_auto_start"),
        ):
            if sub_key in rt_table:
                updates[field] = rt_table[sub_key]
    # [bridge] 表 → bridge_* 字段（P3-1 跨设备协同）
    bridge_table = data.get("bridge", {})
    if isinstance(bridge_table, dict):
        for sub_key, field in (
            ("http_port", "bridge_http_port"),
            ("ws_port", "bridge_ws_port"),
            ("token", "bridge_token"),
        ):
            if sub_key in bridge_table:
                updates[field] = bridge_table[sub_key]
    # [ui] 表 → UI 字段
    ui_table = data.get("ui", {})
    if isinstance(ui_table, dict):
        for sub_key, field in (
            ("boot_animation", "boot_animation"),
        ):
            if sub_key in ui_table:
                updates[field] = ui_table[sub_key]
    # [context] 表 → 上下文压缩字段
    ctx_table = data.get("context", {})
    if isinstance(ctx_table, dict):
        for sub_key, field in (
            ("compaction", "context_compaction"),
            ("compaction_threshold", "compaction_threshold"),
            ("keep_recent_messages", "keep_recent_messages"),
            ("tool_result_keep_recent", "tool_result_keep_recent"),
        ):
            if sub_key in ctx_table:
                updates[field] = ctx_table[sub_key]
    # [memory] 表 → 记忆持久化字段
    mem_table = data.get("memory", {})
    if isinstance(mem_table, dict):
        for sub_key, field in (
            ("auto_resume_session", "auto_resume_session"),
            ("long_term_memory", "long_term_memory"),
            # 画像记忆（Phase 1a）
            ("profile_enabled", "profile_enabled"),
            ("profile_max_entries", "profile_max_entries"),
            ("profile_inject_token_limit", "profile_inject_token_limit"),
            ("profile_refine_min_messages", "profile_refine_min_messages"),
            ("profile_refine_model", "profile_refine_model"),
            ("profile_refine_provider", "profile_refine_provider"),
            ("profile_refine_base_url", "profile_refine_base_url"),
            ("profile_refine_api_key", "profile_refine_api_key"),
        ):
            if sub_key in mem_table:
                updates[field] = mem_table[sub_key]
        # [memory.refine] 子表 → profile_refine_* 简写形式
        refine_table = mem_table.get("refine", {})
        if isinstance(refine_table, dict):
            for sub_key, field in (
                ("model", "profile_refine_model"),
                ("provider", "profile_refine_provider"),
                ("base_url", "profile_refine_base_url"),
                ("api_key", "profile_refine_api_key"),
            ):
                if sub_key in refine_table:
                    updates[field] = refine_table[sub_key]
    # [skills] 表 → Skill 系统字段
    skills_table = data.get("skills", {})
    if isinstance(skills_table, dict):
        for sub_key, field in (
            ("enable", "enable_skills"),
        ):
            if sub_key in skills_table:
                updates[field] = skills_table[sub_key]
    # [mcp] 表 → MCP 接入字段
    mcp_table = data.get("mcp", {})
    if isinstance(mcp_table, dict):
        for sub_key, field in (
            ("enable", "enable_mcp"),
        ):
            if sub_key in mcp_table:
                updates[field] = mcp_table[sub_key]
    # [plugins] 表 → 插件市场字段
    plugins_table = data.get("plugins", {})
    if isinstance(plugins_table, dict):
        for sub_key, field in (
            ("enable", "enable_plugins"),
            ("marketplace_url", "plugin_marketplace"),
            ("marketplace_local", "plugin_market_local"),
        ):
            if sub_key in plugins_table:
                updates[field] = plugins_table[sub_key]
    # [cli_anything] 表 → 自定义 harness 市场字段
    cli_anything_table = data.get("cli_anything", {})
    if isinstance(cli_anything_table, dict):
        for sub_key, field in (
            ("market_url", "harness_market_url"),
            ("market_local", "harness_market_local"),
        ):
            if sub_key in cli_anything_table:
                updates[field] = cli_anything_table[sub_key]
    # [email] 表 → 邮件发送字段
    email_table = data.get("email", {})
    if isinstance(email_table, dict):
        for sub_key, field in (
            ("enabled", "email_enabled"),
            ("smtp_host", "email_smtp_host"),
            ("smtp_port", "email_smtp_port"),
            ("smtp_user", "email_smtp_user"),
            ("smtp_password", "email_smtp_password"),
            ("sender", "email_sender"),
            ("default_recipient", "email_default_recipient"),
        ):
            if sub_key in email_table:
                updates[field] = email_table[sub_key]
    # [lsp] 表 → LSP 集成字段
    lsp_table = data.get("lsp", {})
    if isinstance(lsp_table, dict):
        for sub_key, field in (
            ("enable", "enable_lsp"),
        ):
            if sub_key in lsp_table:
                updates[field] = lsp_table[sub_key]
        # [lsp.servers.<name>] → lsp_servers
        servers_table = lsp_table.get("servers", {})
        if isinstance(servers_table, dict):
            updates["lsp_servers"] = dict(servers_table)
    # [daemon] 表 → 常驻模式字段
    daemon_table = data.get("daemon", {})
    if isinstance(daemon_table, dict):
        for sub_key, field in (
            ("hotkey", "daemon_hotkey"),
            ("hotkey_native", "daemon_hotkey_native"),
            ("hotkey_debounce_ms", "daemon_hotkey_debounce_ms"),
            ("text_terminal_warm", "daemon_text_terminal_warm"),
            ("tray", "daemon_tray"),
            # P2-3 每日简报
            ("briefing_enabled", "briefing_enabled"),
            ("briefing_time", "briefing_time"),
        ):
            if sub_key in daemon_table:
                updates[field] = daemon_table[sub_key]

    # [monitor] 表 → 监控字段
    monitor_table = data.get("monitor", {})
    if isinstance(monitor_table, dict):
        for sub_key, field in (
            ("enabled", "monitor_enabled"),
            ("cpu_threshold", "monitor_cpu_threshold"),
            ("memory_threshold", "monitor_memory_threshold"),
            ("disk_threshold", "monitor_disk_threshold"),
            ("check_interval", "monitor_check_interval"),
            ("alert_cooldown", "monitor_alert_cooldown"),
            # P2-3 增强
            ("disk_trend_days", "monitor_disk_trend_days"),
            ("high_cpu_duration", "monitor_high_cpu_duration"),
            ("work_break_interval", "monitor_work_break_interval"),
        ):
            if sub_key in monitor_table:
                updates[field] = monitor_table[sub_key]
    # [deadline] 表 → 截止日期追踪字段
    deadline_table = data.get("deadline", {})
    if isinstance(deadline_table, dict):
        for sub_key, field in (
            ("enabled", "deadline_enabled"),
            ("check_time", "deadline_check_time"),
        ):
            if sub_key in deadline_table:
                updates[field] = deadline_table[sub_key]
    # [calendar] 表 → 日历集成字段
    calendar_table = data.get("calendar", {})
    if isinstance(calendar_table, dict):
        for sub_key, field in (
            ("enabled", "calendar_enabled"),
            ("backend", "calendar_backend"),
            ("ics_path", "calendar_ics_path"),
            ("ics_url", "calendar_ics_url"),
            ("remind_minutes_before", "calendar_remind_minutes_before"),
        ):
            if sub_key in calendar_table:
                updates[field] = calendar_table[sub_key]
    # [sandbox] 表 → 安全沙箱字段（P3-8）
    sandbox_table = data.get("sandbox", {})
    if isinstance(sandbox_table, dict):
        for sub_key, field in (
            ("enabled", "sandbox_enabled"),
            ("max_memory_mb", "sandbox_max_memory_mb"),
            ("max_cpu_seconds", "sandbox_max_cpu_seconds"),
            ("max_processes", "sandbox_max_processes"),
            ("timeout", "sandbox_timeout"),
            ("block_network", "sandbox_block_network"),
            ("auto_allow_medium", "sandbox_auto_allow_medium"),
            ("audit", "sandbox_audit"),
            ("max_snapshots", "sandbox_max_snapshots"),
        ):
            if sub_key in sandbox_table:
                updates[field] = sandbox_table[sub_key]
        # excluded_commands 是数组
        if "excluded_commands" in sandbox_table:
            updates["sandbox_excluded_commands"] = list(sandbox_table["excluded_commands"])
    # [tools] 表 → 工具延迟加载字段
    tools_table = data.get("tools", {})
    if isinstance(tools_table, dict):
        for sub_key, field in (
            ("deferred_loading", "tools_deferred_loading"),
            ("chat_detection", "tools_chat_detection"),
        ):
            if sub_key in tools_table:
                updates[field] = tools_table[sub_key]
    # [llm] 表 → models 列表 + 自定义模型配置
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
                # 如果没写 provider（vendor），从 api_format 推断
                if "provider" not in c:
                    c["provider"] = c.get("api_format", "openai")
                normalized[cname] = c
            updates["custom_models"] = normalized
    return s.with_overrides(**updates)


# ── 以下函数已拆分到独立模块，此处保留重导出以兼容现有导入 ──
from agent.config.model_registry import save_custom_model, save_last_model, save_realtime_talk_auto_start  # noqa: E402, F401
from agent.config.model_registry import save_custom_voice, save_tts_voice  # noqa: E402, F401
