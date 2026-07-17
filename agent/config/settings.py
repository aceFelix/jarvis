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

    # UI（启动动画）
    # 启动时播放 JARVIS 蓝色方舟反应炉粒子动画（致敬 Claude Code 启动动画）。
    # 终端太窄/太矮/非 TTY 时自动跳过，回退到简单横幅。--no-boot 也可关闭。
    boot_animation: bool = True

    # 上下文压缩（阶段四第一刀）
    # 对话历史超阈值时自动摘要旧消息，保留最近 N 条原消息。
    # 防止 token 爆炸 + 撞模型上下文窗口。致敬 ClaudeCode services/compact/。
    context_compaction: bool = True
    compaction_threshold: int = 8000   # 估算 token 超此值触发压缩
    keep_recent_messages: int = 6      # 压缩时保留最近 N 条原消息

    # 记忆持久化（阶段四第二刀）
    # 启动时自动恢复最近会话（/resume 也可手动恢复）
    auto_resume_session: bool = False
    # 启动时加载长期记忆注入 system prompt（~/.jarvis/MEMORY.md + 项目级）
    long_term_memory: bool = True

    # Skill 系统（阶段四第三刀）
    # 启动时加载 ~/.jarvis/skills/*/SKILL.md + 项目级，注入 system prompt
    enable_skills: bool = True

    # MCP 接入（阶段四第三刀）
    # 启动时连接 ~/.jarvis/mcp.json 配置的 MCP server，注册其工具
    enable_mcp: bool = True

    # 插件市场（阶段五第五刀）
    # /plugin search/install/uninstall 管理插件。安装的插件 skills
    # 写入 ~/.jarvis/skills/，MCP 配置合并到 ~/.jarvis/mcp.json。
    enable_plugins: bool = True
    plugin_marketplace: str = ""

    # LSP 集成（对标 Claude Code）
    # 启动时按 [lsp.servers.<name>] 配置启动语言服务器
    # .py → pylsp/pyright, .ts → typescript-language-server 等
    enable_lsp: bool = True
    lsp_servers: dict[str, dict] = field(default_factory=dict)

    # 常驻模式（阶段五第一刀）
    # jarvis --daemon 后台常驻，热键/托盘唤起
    daemon_hotkey: str = "ctrl+shift+j"   # 全局热键（keyboard 库格式）
    daemon_tray: bool = True               # 是否启用系统托盘图标

    # 主动感知（阶段五第三刀）
    # 系统资源监控：CPU/内存/磁盘超阈值时托盘通知+语音告警。
    # 依赖 psutil。enabled=false 关闭监控。
    monitor_enabled: bool = True
    monitor_cpu_threshold: float = 85.0    # CPU 使用率 %，超过告警
    monitor_memory_threshold: float = 90.0 # 内存使用率 %
    monitor_disk_threshold: float = 10.0   # 磁盘剩余 %，低于告警
    monitor_check_interval: int = 10       # 检查间隔（秒）
    monitor_alert_cooldown: int = 600      # 同类告警冷却（秒）

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
            return tomllib.load(f)
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
    project_cfg = cwd / "configs" / "settings.toml"
    if not project_cfg.is_file():
        # workdir 不是项目根目录时（如 daemon --workdir 指定了别的工作目录），
        # 退回到 agent 包同级找贾维斯的自身配置
        pkg_cfg = _pkg_root / "configs" / "settings.toml"
        if pkg_cfg.is_file():
            project_cfg = pkg_cfg
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
    s = _apply_env(s)

    # 5. 权限文件默认路径
    if not s.permissions_file:
        for candidate_dir in (cwd, _pkg_root):
            candidate = candidate_dir / "configs" / "permissions.yaml"
            if candidate.exists():
                s.permissions_file = str(candidate)
                break

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
        "boot_animation",
        "context_compaction", "compaction_threshold", "keep_recent_messages",
        "auto_resume_session", "long_term_memory",
        "enable_skills",
        "enable_mcp",
        "enable_plugins", "plugin_marketplace",
        "enable_lsp",
        "enable_thinking", "thinking_budget",
        "vendor_fallback",
        "daemon_hotkey", "daemon_tray",
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
    # [realtime_talk] 表 → realtime_* 字段
    rt_table = data.get("realtime_talk", {})
    if isinstance(rt_table, dict):
        for sub_key, field in (
            ("ws_url", "realtime_ws_url"),
            ("model", "realtime_model"),
            ("voice", "realtime_voice"),
            ("auto_start", "realtime_talk_auto_start"),
        ):
            if sub_key in rt_table:
                updates[field] = rt_table[sub_key]
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
        ):
            if sub_key in ctx_table:
                updates[field] = ctx_table[sub_key]
    # [memory] 表 → 记忆持久化字段
    mem_table = data.get("memory", {})
    if isinstance(mem_table, dict):
        for sub_key, field in (
            ("auto_resume_session", "auto_resume_session"),
            ("long_term_memory", "long_term_memory"),
        ):
            if sub_key in mem_table:
                updates[field] = mem_table[sub_key]
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
        ):
            if sub_key in plugins_table:
                updates[field] = plugins_table[sub_key]
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
            ("tray", "daemon_tray"),
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
        ):
            if sub_key in monitor_table:
                updates[field] = monitor_table[sub_key]
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


def _apply_env(s: Settings) -> Settings:
    """环境变量覆盖。JARVIS_PROVIDER / JARVIS_MODEL / 等（兼容 MY_AGENT_*）。"""
    updates: dict[str, object] = {}

    def _env(*names: str) -> str | None:
        for n in names:
            v = os.environ.get(n)
            if v:
                return v
        return None

    provider = _env("JARVIS_PROVIDER", "MY_AGENT_PROVIDER")
    if provider:
        updates["provider"] = provider
    model = _env("JARVIS_MODEL", "MY_AGENT_MODEL")
    if model:
        updates["model"] = model

    # 常见 LLM API key 环境变量直通
    # 顺序: 先认各家专属变量（DASHSCOPE_API_KEY / ANTHROPIC_API_KEY），
    # 再认通用变量（OPENAI_API_KEY / JARVIS_API_KEY / MY_AGENT_API_KEY）。
    # 实时语音/多模态等 DashScope 专属能力需要独立的 dashscope_api_key。
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY")
    if dashscope_key and not s.dashscope_api_key:
        updates["dashscope_api_key"] = dashscope_key
    if not s.api_key:
        for key_env in (
            "DASHSCOPE_API_KEY",    # 阿里云百炼 DashScope
            "ANTHROPIC_API_KEY",     # Anthropic Claude
            "OPENAI_API_KEY",        # OpenAI 官方及兼容服务
            "JARVIS_API_KEY",        # 通用兜底（新名）
            "MY_AGENT_API_KEY",      # 通用兜底（兼容旧名）
        ):
            kv = os.environ.get(key_env)
            if kv:
                updates["api_key"] = kv
                break

    base_url = _env("JARVIS_BASE_URL", "MY_AGENT_BASE_URL")
    if base_url:
        updates["base_url"] = base_url

    mode = _env("JARVIS_PERMISSION_MODE", "MY_AGENT_PERMISSION_MODE")
    if mode:
        updates["permission_mode"] = parse_mode(mode)

    debug = _env("JARVIS_DEBUG", "MY_AGENT_DEBUG")
    if debug:
        updates["debug"] = debug.lower() in ("1", "true", "yes")

    boot = _env("JARVIS_BOOT_ANIMATION")
    if boot:
        updates["boot_animation"] = boot.lower() in ("1", "true", "yes")

    compaction = _env("JARVIS_CONTEXT_COMPACTION")
    if compaction:
        updates["context_compaction"] = compaction.lower() in ("1", "true", "yes")

    return s.with_overrides(**updates) if updates else s


def save_custom_model(name: str, config: dict[str, str]) -> bool:
    """保存自定义模型到 ~/.jarvis/settings.toml 的 [llm.custom_models] 节。

    如果模型已存在则更新，否则追加。支持 name/base_url/api_key/provider_type/model_type。
    返回 True 表示保存成功。
    """
    import re

    toml_path = Path.home() / ".jarvis" / "settings.toml"
    if not toml_path.exists():
        return False

    content = toml_path.read_text(encoding="utf-8")

    # 构建新子表条目
    entry = f'''
[llm.custom_models."{name}"]
name = "{name}"
base_url = "{config.get('base_url', '')}"
api_key = "{config.get('api_key', '')}"
provider_type = "{config.get('provider_type', 'openai')}"
model_type = "{config.get('model_type', 'multimodal')}"
vendor = "{config.get('vendor', 'dashscope')}"
'''

    marker = f'[llm.custom_models."{name}"]'
    if marker in content:
        # 已存在 → 替换旧段
        start = content.index(marker)
        rest = content[start + len(marker):]
        m = re.search(r'\n\[', rest)
        if m:
            end = start + len(marker) + m.start()
            # 跳过末尾的空行
            while end < len(content) and content[end] == '\n':
                end += 1
            content = content[:start].rstrip() + "\n" + entry.strip() + "\n" + content[end:]
        else:
            content = content[:start].rstrip() + "\n" + entry.strip()
    else:
        # 不存在 → 追加
        if "[llm.custom_models" not in content:
            # 首次添加，确保有节头注释
            content = content.rstrip() + "\n\n# 自定义模型（通过 /models 添加）\n"
        content = content.rstrip() + "\n" + entry.strip() + "\n"

    toml_path.write_text(content, encoding="utf-8")
    return True


def save_last_model(model_name: str) -> bool:
    """保存最近使用的模型到 ~/.jarvis/settings.toml 顶层 last_model 字段。

    last_model 和 model/provider/base_url 一样是顶层字段（不在 [llm] 节内），
    这样 _apply_toml 才能正确读取。
    下次启动时若未指定 --model，会自动恢复此模型。
    返回 True 表示保存成功。
    """
    import re

    toml_path = Path.home() / ".jarvis" / "settings.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)

    if not toml_path.exists():
        toml_path.write_text(f'last_model = "{model_name}"\n', encoding="utf-8")
        return True

    content = toml_path.read_text(encoding="utf-8")

    # last_model 是顶层字段，必须在任何 [...] 节头之前
    # 策略：找到第一个 [...] 节头位置，在该位置之前操作顶层字段
    first_section = re.search(r'^\[', content, re.MULTILINE)
    top_end = first_section.start() if first_section else len(content)
    top_part = content[:top_end]
    rest_part = content[top_end:]

    # 在顶层部分查找/更新/插入 last_model
    if re.search(r'^last_model\s*=', top_part, re.MULTILINE):
        # 替换已有值
        new_top = re.sub(
            r'^last_model\s*=.*$',
            f'last_model = "{model_name}"',
            top_part,
            flags=re.MULTILINE,
        )
    else:
        # 插入：优先放在 model = 行后面，否则放在顶层末尾
        model_line = re.search(r'^model\s*=.*$', top_part, re.MULTILINE)
        if model_line:
            insert_at = model_line.end()
            new_top = top_part[:insert_at] + f'\nlast_model = "{model_name}"' + top_part[insert_at:]
        else:
            new_top = top_part.rstrip() + f'\nlast_model = "{model_name}"\n'

    content = new_top + rest_part
    toml_path.write_text(content, encoding="utf-8")
    return True


def save_realtime_talk_auto_start(enabled: bool) -> bool:
    """保存实时语音对话自动启动开关到 ~/.jarvis/settings.toml 的 [realtime_talk] 节。

    返回 True 表示保存成功。
    """
    import re

    toml_path = Path.home() / ".jarvis" / "settings.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)

    value = "true" if enabled else "false"
    if not toml_path.exists():
        toml_path.write_text(f"[realtime_talk]\nauto_start = {value}\n", encoding="utf-8")
        return True

    content = toml_path.read_text(encoding="utf-8")

    # 定位或创建 [realtime_talk] 节
    section_match = re.search(r'^\[realtime_talk\]\s*$', content, re.MULTILINE)
    if section_match:
        section_start = section_match.end()
        next_section = re.search(r'^\[', content[section_start + 1:], re.MULTILINE)
        section_end = section_start + 1 + (next_section.start() if next_section else len(content[section_start + 1:]))
        section = content[section_start + 1:section_start + 1 + section_end - (section_start + 1)]

        if re.search(r'^auto_start\s*=', section, re.MULTILINE):
            new_section = re.sub(
                r'^auto_start\s*=.*$',
                f"auto_start = {value}",
                section,
                flags=re.MULTILINE,
            )
        else:
            new_section = section.rstrip() + f"\nauto_start = {value}\n"

        content = content[:section_start + 1] + new_section + content[section_start + 1 + section_end - (section_start + 1):]
    else:
        content = content.rstrip() + f"\n\n[realtime_talk]\nauto_start = {value}\n"

    toml_path.write_text(content, encoding="utf-8")
    return True
