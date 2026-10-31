"""配置目录：把「两百多个配置项」变成能回答的知识，而不只是能改的白名单。

为什么要有它（aceFelix，2026-10-31）：`config_specs` 的真源表只回答「模型能不能
替用户改」，收了 58 项。而 `Settings` 有 202 个字段——剩下 144 项用户想调时，
模型的处境是**既不知道它存在，也说不清它在哪**，只能含糊回一句「请打开
settings.toml」，用户还得自己去两百行里翻。

真源表解决不了这件事，因为两者答案的口径根本不同：

- 真源表答「**谁有资格改**」——所以只收够格被工具改的项；
- 本目录答「**这是什么、现在是多少、在哪改、怎么生效**」——所以要对**全部**
  字段有话说，包括那些永远不该让模型代改的凭据与护栏。

用户不懂配置时最自然的入口是**问**，不是敲命令——"CPU 阈值在哪改"比
`/config list | grep cpu` 自然得多。对话是主入口，本目录就是给对话备的底。

## 一、不新造第五份人工表

项目里已经有四套配置视图：真源表、`percept_settings`、`desktop_settings`、
`settings.example.toml`。再手写第五份 202 项的中文说明表，必然与代码漂移。
所以本目录**只派生，不重复**：

| 要知道什么 | 从哪来 |
|---|---|
| 字段挂在哪个 TOML 段、叫什么名字 | `settings_domains`（162 项，代码真源） |
| 中文名与"改它会发生什么" | 真源表 `hint`（58 项，最精确） |
| 其余字段的行尾说明 | 包内 `settings.example.toml` 的注释（约七成覆盖） |
| 整个段是干什么的 | `SECTION_DOCS`（人工，一句话） |

真源表没覆盖的字段，说明来自模板注释——那是人写给人看的原话，比模型现编可靠。
两处都没有的字段，如实在结果里留白，不编造。

## 二、凭据一律不吐值

`smtp_password`、`api_key` 这类字段即使是用户本人问，也只回「已设置 / 未设置」。
模型看到的上下文可能被它自己的推理带进别的地方，值一旦进了上下文就收不回来。

## 三、与真源表的分工写进每条结果

每条目录项都带 `editable`：`True` 表示"可以让 jarvis 代改"，`False` 表示
"只能自己编辑文件"。模型照着这个旗标说话，就不会出现"我帮你改了"却其实
什么都没改——或者反过来，明明能代改却把用户推去翻文件。

@author aceFelix
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.config.config_specs import CONFIG_SPECS
from agent.config.settings_domains import FLAT_DOMAINS, SUB_TABLES

# ---- 段的用途（一句话）----
#
# 只写"这个段管什么"，不重复字段级细节——细节由字段自己的说明承担。段说明的
# 作用是**让检索能命中**：用户问"怎么让邮件能用"，段名 email 与这句话之间没有
# 任何字面重合，靠这段中文才能对上。
SECTION_DOCS: dict[str, str] = {
    "tts": "文字转语音：播报用的音色、音量、语速与音调",
    "stt": "语音识别：把你说的话转成文字，含单次录音上限与静音判定",
    "voice": "语音交互的行为：播报中开口打断、按 ESC 打断、唤醒与退出词",
    "realtime_talk": "实时语音对话（DashScope 双向流，边听边说）",
    "bridge": "桌面端与后端之间的本地桥接：HTTP/WebSocket 端口与访问令牌",
    "ui": "终端界面的表现，例如启动时的方舟反应炉动画",
    "context": "上下文压缩：会话变长时怎么压缩，防 token 爆炸与撞上下文窗口",
    "memory": "记忆：会话自动恢复、长期记忆注入、用户画像提炼",
    "memory.refine": "画像提炼专用的独立小模型（省钱：不用主模型干粗活）",
    "skills": "技能包（SKILL.md）的加载开关",
    "mcp": "MCP 外部工具服务器的接入开关",
    "profile_bridge": "知识图谱画像桥：把图谱里的画像注入对话，并支持回写",
    "plugins": "插件市场地址（远程 marketplace.json 或本地目录）",
    "cli_anything": "harness 市场：CLI-Anything 风格的工具适配清单来源",
    "email": "邮件：SMTP 发信、IMAP 收信的账号与服务器",
    "lsp": "代码智能：跳转定义、找引用、看诊断（依赖外部 language server）",
    "daemon": "常驻模式：全局热键唤起、系统托盘、每日简报",
    "monitor": "系统资源监控：CPU/内存/磁盘超阈值时托盘通知 + 语音告警",
    "deadline": "截止日期追踪：登记后按 7/3/1/0 天分级提醒，逾期每天提醒",
    "calendar": "日历事件：读 Outlook 或 ICS，在简报里展示并提前提醒",
    "sandbox": "安全沙箱：高风险管理命令在受限环境里跑，限制内存/CPU/进程数",
    "checkpoint": "任务检查点：长任务中断后能从最近一步接着跑",
    "policy": "策略层的零散项，例如离开多久算离开",
    "policy.notify_budget": "打扰预算：每天最多吵你几次、什么时段闭嘴、多重要才值得开口",
    "actions": "副作用账本：破坏性操作留底，给一个可以后悔的时间窗口",
    "tasks": "后台任务驱动器：多步目标自动推进，卡住了怎么办",
    "maintenance": "系统维护：磁盘清理（隔离区可撤销）/ Defender 查毒 / 七区块体检",
    "percept": "感知层：自动观察前台窗口、目录堆积、仓库、来信、摄像头、日历与系统资源",
    "tools": "工具延迟加载：省 token 的两把刀（按需加载 + 纯聊天零工具）",
}

# ---- 不在任何 TOML 段里的顶层字段 ----
#
# 这些字段是"整体行为"而非"某个功能的参数"，TOML 里直接写在文件顶端（模型域
# 则在 models.toml）。段名一律记 `"（顶层）"`，让检索与渲染能如实指路。
TOP_LEVEL_DOCS: dict[str, str] = {
    "workdir": "默认工作目录：文件操作、开发、项目管理都在它下面进行",
    "permission_mode": "权限模式：default / plan / accept_edits / yolo，决定哪些操作要问你",
    "max_iterations": "单轮对话最多跑多少步工具（防止原地打转烧 token）",
    "permissions_file": "额外的权限规则文件路径（YAML）",
    "system_prompt_append": "追加到系统提示词末尾的自定义指令",
    "debug": "调试模式：输出更详细的内部日志",
    "verbose": "啰嗦模式：把更多中间过程打出来",
    "quick_start": "快速开始：跳过部分初始化检查直接进对话",
    "update_check": "启动时检查新版本",
    "provider": "当前使用的模型提供商（llm 段，通常由 /model 或 jarvis init 设置）",
    "api_format": "接口协议格式：openai / anthropic 等，应与 provider 匹配",
    "model": "当前使用的模型名",
    "api_key": "访问模型的密钥",
    "base_url": "模型接口地址（自建网关或代理时改它）",
    # 下面四项是模型域的“出厂值”：切换模型后回退用，不是当前生效值
    "default_provider": "模型域的出厂提供商（切换模型后回退用）",
    "default_api_format": "模型域的出厂协议格式（切换模型后回退用）",
    "default_base_url": "模型域的出厂接口地址（切换模型后回退用）",
    "default_api_key": "模型域的出厂密钥（切换模型后回退用）",
    "max_tokens": "单次回复最多多少 token",
    "temperature": "采样温度：越低越稳定，越高越发散",
    "enable_thinking": "是否让模型先思考再回答（支持推理的模型才有）",
    "thinking_budget": "思考阶段最多花多少 token",
    "thinking_effort": "思考力度档位（部分模型用它代替 budget）",
    "vendor_fallback": "主模型不可用时是否自动换一家兜底",
    "last_model": "上一次用过的模型名（切换后回填用）",
    "models": "模型列表（多模型切换用）",
    "custom_models": "自定义模型条目，持久化在 models.toml 的 [llm.custom_models]",
    "custom_voices": "自定义音色条目，持久化在 settings.toml 的 [tts.custom_voices]",
    "lsp_servers": "language server 启动命令表，持久化在 [lsp.servers.<语言>]",
    "voice_max_seconds": "单次语音输入最长秒数",
    "enable_tool_self_healing": "工具调用报错时是否自动重试/自愈",
    "tool_retry_max": "工具失败最多重试几次",
    "tool_retry_backoff_base": "重试退避的起始间隔（秒）",
    "tool_retry_backoff_max": "重试退避的上限间隔（秒）",
    "notify_budget_enabled": "打扰预算总开关（见 [policy.notify_budget] 段）",
    "notify_daily_max": "每天主动播报次数上限（见 [policy.notify_budget] 段）",
    "notify_quiet_hours": "静默时段，形如 23:00-07:00（见 [policy.notify_budget] 段）",
    "notify_aggregate_window_s": "同源消息聚合窗口秒数（见 [policy.notify_budget] 段）",
    "notify_min_salience": "重要度门槛，低于它不开口（见 [policy.notify_budget] 段）",
    "notify_reminder_channels": "提醒投递渠道（见 [policy.notify_budget] 段）",
    "notify_quiet_email_to": "静默时段里的提醒改发到这个邮箱（见 [policy.notify_budget] 段）",
}

# ---- 模板里没写说明的字段：在这里补 ----
#
# 两份模板共缺五十来项（`[email]` 的服务器地址、`[realtime_talk]` 的十几个调参项
# 等）。这些不是可以不说的——恰恰相反，它们多半是用户自己配不明白才来问的。
# 只补模板确实缺的那些：模板已有说明的绝不复写，否则就有了第二处会漂移的文案。
FIELD_DOCS: dict[str, str] = {
    # [calendar]
    "calendar_backend": "日历来源：auto=先 Outlook 后 ICS / outlook=仅 Outlook COM / ics=仅 ICS 文件",
    "calendar_enabled": "简报里带今日日程（读 Outlook 或 ICS）；感知源那份开关在 [percept] calendar_enabled",
    "calendar_ics_path": "本地 .ics 文件路径（从任意日历应用导出后放这里）",
    "calendar_ics_url": "远程 .ics 订阅地址（Google Calendar / Nextcloud 等）",
    # [cli_anything]
    "harness_market_local": "harness 市场的本地目录，远程不可达时回退用它",
    "harness_market_url": "harness 市场的远程 raw 地址前缀",
    # [context]
    "context_compaction": "上下文自动压缩总开关；关掉后长会话会直接撞模型窗口报错",
    # [daemon]
    "briefing_catchup_window_min": "简报补发窗口：错过简报时间后多少分钟内仍会补播一次",
    # [email]
    "email_enabled": "邮件能力总开关；关掉后 SendEmail / EmailBox 一律直接返回错误",
    "email_imap_host": "收件服务器地址（163 是 imap.163.com）",
    "email_imap_password": "收件授权码；多数邮箱与发件授权码是同一个",
    "email_smtp_host": "发件服务器地址（163 是 smtp.163.com）",
    "email_smtp_port": "发件端口：465 走 SSL（常见），587 走 STARTTLS",
    # [lsp] / [mcp] / [plugins] / [skills] / [sandbox] / [monitor]
    "enable_lsp": "代码智能总开关：跳转定义、找引用、看诊断，依赖外部 language server",
    "enable_mcp": "MCP 外部工具接入总开关；开后会连 mcp.json 里配的服务器",
    "enable_plugins": "插件系统开关；只管安装/卸载/启停这几个动作，已装插件是否加载看 [skills] enable",
    "enable_skills": "技能包（SKILL.md）加载总开关；插件最后也是落成技能才生效，所以关它会连带关掉已装插件",
    "sandbox_enabled": "沙箱总开关：开启后中等风险命令自动在受限环境里跑，不再逐个问你",
    # [memory] 画像
    "profile_enabled": "用户画像总开关：关掉后既不提炼也不往提示词里注入",
    "profile_refine_api_key": "画像提炼模型的密钥；留空则回退到自定义模型配置",
    "profile_refine_base_url": "画像提炼模型的接口地址",
    "profile_refine_model": "画像提炼用的模型名（用便宜的小模型，别拿主模型干粗活）",
    "profile_refine_provider": "画像提炼模型的协议格式（openai / anthropic / dashscope）",
    "profile_bridge_enabled": "知识图谱画像桥总开关：把图谱里的画像注入对话，并支持回写",
    "plugin_marketplace": "远程插件市场地址（marketplace.json）",
    "plugin_market_local": "本地插件市场目录；支持扁平与仓库两种布局",
    # [percept]
    "percept_file_interval_s": "目录堆积检查间隔（秒）；调小更及时，代价是更费 IO",
    "percept_vcs_interval_s": "仓库状态检查间隔（秒）",
    # [realtime_talk]
    "dashscope_api_key": "实时语音专用的 DashScope 密钥；不能用当前 LLM 的 key",
    "realtime_model": "实时语音模型名",
    "realtime_voice": "实时语音的音色",
    "realtime_ws_url": "实时语音的 WebSocket 地址（走官方默认即可）",
    "realtime_event_log": "记录实时语音事件流水；排查断句 / 不出声问题时打开",
    "realtime_echo_suppress_with_aec": "回声抑制：开启后扬声器里自己的声音不会被当成你在说话",
    "realtime_half_duplex": "半双工：AI 说话期间不拾音，避免外放回声把对话卡成哑巴；戴耳机可关",
    "realtime_rescue": "响应救援：服务端迟迟不答时客户端补发一次请求，防「永远不回复」",
    "realtime_silence_ms": "服务端判停静音时长（毫秒）；越短响应越快、但短停顿会误触发，对话建议 400~800",
    "realtime_turn_detection": "断句方式：server_vad 按静音判停（免提稳）；smart_turn 按语义判停（耳机用）",
    "realtime_tools_mode": "语音会话工具模式：builtin 只带两个工具求低延迟——改成 all 会让实时语音完全没响应",
    # [tools] / [tts] / [stt] / [ui] / [voice]
    "tools_deferred_loading": "工具延迟加载：可选工具只发名字，模型需要时用 ToolSearch 取回来（省大量 token）",
    "tools_chat_detection": "纯聊天检测：短消息且无动作意图时一个工具都不发",
    "tts_model": "播报用的语音合成模型（v3-flash 快，v3-plus 好听）",
    "tts_voice": "播报音色",
    "stt_model": "语音识别模型；填错会在建连阶段直接报错",
    "boot_animation": "启动时的方舟反应炉动画；终端太窄或非 TTY 时自动跳过",
    "voice_barge_in": "语音打断：播报中开口说话就停——Windows 上两个 PyAudio 并发会崩，默认关",
    "voice_barge_in_key": "键盘打断：播报中按 ESC 停止；不占 PyAudio，无崩溃风险",
    "voice_wake_words": "唤醒词清单；留空则用内置默认",
}

# ---- 当前版本没人读的配置项（比“未纳管”更糟的一类）----
#
# 这些字段存在、面板看得到、TOML 写得了，但**没有任何代码读它**——改了完全不生效。
# 一律不纳管（纳管等于让用户去调一个没用的开关），并且在目录里如实标出来。
# 这正是“让用户不被误导”这件事最该覆盖的情形：说不清“改了没反应”比说不清
# “能不能代改”伤害更大。
#
# 每条都附上“那到底该改哪个”，否则用户只知道这条路死了、不知道活路在哪里。
#
# **当前为空**——曾经的 7 项已全部处理完（2026-10-31）：
#
# | 字段 | 处理 |
# |---|---|
# | ``[monitor] enabled`` | 删（真开关是 ``[percept] system_enabled``）|
# | ``[monitor] check_interval`` | 接线：``percept/factory.py`` 真读 |
# | ``[monitor] high_cpu_duration`` | 删（功能已由 ``/health`` 进程 Top N 取代）|
# | ``[monitor] work_break_interval`` | 接线：挂在 ``SystemMonitor._do_check`` |
# | ``[calendar] enabled`` | 接线：``proactive_wiring`` 真装配日历源 |
# | ``[skills] enable`` | 接线：模块级开关 + 启动时写入 |
# | ``[plugins] enable`` | 接线：模块级开关 + 写操作守卫 |
#
# 表留着不拆：下一个“字段在、没人读”的情况还得往这里放，拆了等于拆掉检测能力。
DEAD_FIELDS: dict[str, str] = {}

# ---- 已经从 Settings 里删掉的字段 ----
#
# 旧配置文件里往往还留着这些行（加载器对未知键是静默忽略的，不会报错）。用户看到
# 自己文件里有这个字段、改了半天没反应，问起来必须能说清“它已经不存在了、去哪了”
# ——否则就和当初“不认识的配置项”那句错话同一个毛病。
#
# 与 ``DEAD_FIELDS`` 的区别：那些是**字段还在**但没人读；这些是**字段本身没了**。
REMOVED_FIELDS: dict[str, str] = {
    "monitor_enabled": (
        "已于 2026-10-31 删除：真正管监控启停的是 [percept] system_enabled，"
        "在 [monitor] 段写它不会有任何效果"
    ),
    "monitor_high_cpu_duration": (
        "已于 2026-10-31 删除：异常进程检测这个功能由 /health 的进程 Top N 取代"
        "（带 CPU/内存/启动时间，且只建议不动手）"
    ),
    "daemon_hotkey": (
        "已于 2026-11-01 删除：全局热键只服务于已退役的 pywebview 工作台；"
        "桌面热键归 jarvis-desktop 的 Electron globalShortcut，Python 侧不再实现"
    ),
    "daemon_hotkey_native": (
        "已于 2026-11-01 随全局热键整体下线删除（Windows RegisterHotKey 后端）"
    ),
    "daemon_hotkey_debounce_ms": (
        "已于 2026-11-01 随全局热键整体下线删除（热键去抖毫秒）"
    ),
}

#: 已删字段的 **TOML 侧名字** → 属性名。
#
# 用户是从自己的配置文件里抄名字来问的，那里写的是 TOML 短名（``hotkey``），而不是
# ``Settings`` 属性名（``daemon_hotkey``）。只认后者的话，用户问「hotkey 这项怎么改」
# 会得到一句「不认识的配置项」——而那个字段明明就躺在他文件里。
#
# 刻意的边界：``enabled`` 这类多段通用的名字**不收**。``[monitor] enabled`` 与
# ``[email] enabled`` 是两个东西，笼统回一句「已删除」会给后者帮倒忙。
_REMOVED_ALIASES: dict[str, str] = {
    "high_cpu_duration": "monitor_high_cpu_duration",
    "hotkey": "daemon_hotkey",
    "hotkey_native": "daemon_hotkey_native",
    "hotkey_debounce_ms": "daemon_hotkey_debounce_ms",
}

#: 凭据类：值一律不吐，只报「已设置 / 未设置」。
_SENSITIVE_ATTRS = frozenset({
    "api_key", "default_api_key", "dashscope_api_key", "bridge_token",
    "email_smtp_password", "email_imap_password", "profile_refine_api_key",
})
#: 后缀兜底：新加凭据字段忘了登记也不会漏（宁可多遮）。
_SENSITIVE_SUFFIXES = ("_api_key", "_password", "_secret", "_token")

#: 段名 → 配置文件（给人指路用）。模型域单独一份文件，指错等于让人白翻。
_LLM_FILE = "~/.jarvis/models.toml"
_SETTINGS_FILE = "~/.jarvis/settings.toml"


@dataclass(frozen=True)
class CatalogEntry:
    """配置目录里的一条：一个 Settings 字段的全部对外说法。

    Attributes:
        attr: ``Settings`` 属性名（内部用，也给模型确认改的是哪一项）。
        section: TOML 段名；顶层字段为 ``"（顶层）"``。
        field: 段内字段名（写进 TOML 的就是它）。
        doc: 一句话说明；两处真源都没有时为空串，不编造。
        kind: bool / int / float / time / list / str，用于给值排版。
        editable: 是否收录进真源表（能经 ``ConfigSet`` 代改）。
        spec_key: 真源表协议键；``editable`` 为假时是 ``None``。
        sensitive: 凭据类，值只回"已设置/未设置"。
        dead: 非空 = 当前版本**没有任何代码读它**（改了不生效）；值是原因与替代改法。
    """

    attr: str
    section: str
    field: str
    doc: str
    kind: str
    editable: bool
    spec_key: str | None
    sensitive: bool
    dead: str = ""

    @property
    def toml_path(self) -> str:
        """该字段在配置文件里的完整位置，例如 ``~/.jarvis/settings.toml [monitor]``。"""
        head = _LLM_FILE if self.attr in _LLM_ATTRS else _SETTINGS_FILE
        if self.section == "（顶层）":
            return f"{head} 顶端"
        return f"{head} [{self.section}]"


#: 模型域：这些字段落在 models.toml 而不是 settings.toml。
_LLM_ATTRS = frozenset({
    "provider", "api_format", "model", "api_key", "base_url", "last_model",
    "models", "custom_models", "max_tokens", "temperature", "enable_thinking",
    "thinking_budget", "thinking_effort", "vendor_fallback",
    "default_provider", "default_api_format", "default_base_url", "default_api_key",
})

#: 从示例模板里捞行尾注释：``cpu_threshold = 85.0   # CPU 使用率 %``
#:
#: 行首的 ``#`` 也认：有些段（如 ``[email]``）把带说明的字段整行注掉当文档用，
#: ``# smtp_user = "x@163.com"   # 发件邮箱账号``——不认它就从这些段一个说明都拿不到。
_COMMENT_RE = re.compile(r"^\s*#?\s*([a-z_][a-z0-9_]*)\s*=\s*(.+?)\s+#\s*(.+?)\s*$")
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


@lru_cache(maxsize=1)
def _example_docs() -> dict[tuple[str, str], str]:
    """模板里的行尾注释 → ``(段, 字段) → 说明``。

    模板是给人看配置用的，每个字段的注释就是**作者本人写的人类语言**，比任何
    现编的说明都准。

    两份模板都读、按顺序合并：包内那份随 wheel 发布，是安装后的唯一来源；仓库
    根那份在源码态存在，段与字段反而更全（各补对方缺的若干段）。先读的优先，
    后读的只补空缺——这样发布的权威性不被开发目录盖过。

    两份都读不到（安装方式被裁剪、文件缺失）时返回空表并让上游回退，不让一个
    提示性功能把主流程拖垮。
    """
    here = Path(__file__).resolve()
    candidates = (
        here.parent.parent / "configs" / "settings.example.toml",        # 包内（随 wheel 发布）
        here.parent.parent.parent / "configs" / "settings.example.toml",  # 仓库根（源码态）
    )
    docs: dict[tuple[str, str], str] = {}
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        section = "（顶层）"
        for line in text.splitlines():
            m_sec = _SECTION_RE.match(line)
            if m_sec:
                section = m_sec.group(1).strip()
                continue
            m_kv = _COMMENT_RE.match(line)
            if m_kv:
                docs.setdefault((section, m_kv.group(1)), m_kv.group(3).strip())
    return docs


def _kind_of(attr: str) -> str:
    """按字段名粗判类型，只用于把值排版成人话（不参与校验）。"""
    if attr.startswith("notify_quiet_hours") or attr.endswith("_time"):
        return "time"
    for suffix in ("_enabled", "_animation", "_thinking", "_audit"):
        if attr.endswith(suffix):
            return "bool"
    if attr in ("debug", "verbose", "quick_start", "update_check"):
        return "bool"
    for suffix in ("_dirs", "_repos", "_senders", "_apps", "_commands",
                   "_suffixes", "_words", "_servers", "_models", "_voices"):
        if attr.endswith(suffix):
            return "list"
    for suffix in ("_seconds", "_ms", "_min", "_minutes", "_days", "_count",
                   "_max", "_min", "_mb", "_gb", "_threshold", "_rate",
                   "_interval", "_ratio", "_tokens", "_budget", "_iterations"):
        if attr.endswith(suffix):
            return "float"
    return "str"


def _spec_index() -> dict[str, Any]:
    """``Settings`` 属性名 → 真源表条目（只含可代改的项）。"""
    rows = CONFIG_SPECS if isinstance(CONFIG_SPECS, (list, tuple)) else list(CONFIG_SPECS.values())
    return {spec.attr: spec for spec in rows}


def _collect() -> dict[str, tuple[str, str]]:
    """``Settings`` 属性名 → ``(段, 字段)``，覆盖所有能在 TOML 里写出来的项。

    三处映射合起来看：普通段（``FLAT_DOMAINS``）、嵌套子表（``SUB_TABLES``，
    如 ``[memory.refine]``）、以及不属于任何段的顶层字段（``TOP_LEVEL_DOCS``）。
    早出现的优先——``SUB_TABLES`` 里 ``profile_refine_provider`` 被两个子键映射
    到同一字段，取第一个（``provider``）才是它在文件里的真实写法。
    """
    out: dict[str, tuple[str, str]] = {}
    for table, pairs in FLAT_DOMAINS:
        for sub, attr in pairs:
            out.setdefault(attr, (table, sub))
    for parent, child, pairs in SUB_TABLES:
        for sub, attr in pairs:
            out.setdefault(attr, (f"{parent}.{child}", sub))
    for attr in TOP_LEVEL_DOCS:
        out.setdefault(attr, ("（顶层）", attr))
    return out


@lru_cache(maxsize=1)
def build_catalog() -> tuple[CatalogEntry, ...]:
    """构建完整配置目录（惰性 + 缓存，进程内只算一次）。

    说明的取用顺序是**精确优先**：真源表 ``hint`` > 顶层人工说明 > 示例模板
    注释 > 段说明。段说明兜底而不是留空，是因为用户问"邮件怎么配"时，哪怕
    只答出"这个段管邮件收发"加上字段清单，也远好过一堆没有上下文的字段名。
    """
    specs = _spec_index()
    example = _example_docs()
    entries: list[CatalogEntry] = []
    for attr, (section, field) in _collect().items():
        spec = specs.get(attr)
        doc = ""
        if spec is not None and getattr(spec, "hint", ""):
            doc = str(spec.hint)
        if not doc:
            # 顶层字段不挂在任何段里，模板里也没它们的位置，说明只在这张表里
            doc = TOP_LEVEL_DOCS.get(attr, "")
        if not doc:
            doc = example.get((section, field), "")
        if not doc:
            doc = FIELD_DOCS.get(attr, "")
        sensitive = attr in _SENSITIVE_ATTRS or attr.endswith(_SENSITIVE_SUFFIXES)
        dead = DEAD_FIELDS.get(attr, "")
        entries.append(
            CatalogEntry(
                attr=attr,
                section=section,
                field=field,
                doc=doc,
                kind=(spec.kind if spec is not None else _kind_of(attr)),
                # 死配置永不可代改：就算将来有人误把它加进真源表也不放行
                editable=spec is not None and not dead,
                spec_key=(getattr(spec, "key", None) if spec is not None else None),
                sensitive=sensitive,
                dead=dead,
            )
        )
    entries.sort(key=lambda e: (e.section, e.field))
    return tuple(entries)


def _tokens(query: str) -> list[str]:
    """把查询切成检索词：按空白与常见标点断开，丢掉单字。

    中英文混排时不做分词器——用户问的是配置，词表就是配置名本身；切成两字以上
    的片段再逐段匹配，命中率已够，还免了一个分词依赖。
    """
    raw = re.split(r"[\s,，。?？/、\|]+", query.strip())
    out: list[str] = []
    for tok in raw:
        tok = tok.strip()
        if len(tok) >= 2:
            out.append(tok.lower())
    return out


def search_catalog(query: str, *, limit: int = 8) -> list[CatalogEntry]:
    """按关键词检索配置目录，返回最相关的若干条（分数高者在前）。

    打分口径刻意简单：命中段名/字段名/属性名（**机器名**，用户可能直接抄过来）
    权重最高，命中中文说明次之，命中段说明再次之。全部不命中时退回一次"全字段
    名包含"的宽松匹配，让"cpu"这种半个词的问法也能出结果。

    Args:
        query: 用户原话或关键词，如 ``"CPU 阈值"``、``"邮件"``、``"自动清理"``。
        limit: 最多返回几条（工具输出要控制长度，别把上下文塞满）。

    Returns:
        匹配到的目录项；空列表表示确实没找到，调用方应如实说"没这项"。
    """
    entries = build_catalog()
    tokens = _tokens(query) or [query.strip().lower()]
    # 先认段：用户的说法命中某个段的中文用途时，**整段**都算相关。
    # 不这么做就会出现「问邮件、答撤销窗口」——那条 hint 里正好有「发邮件」三个字，
    # 于是它抢在 [email] 段前面；而用户想问的显然是邮箱怎么配。
    hot_sections = {
        name for name, doc in SECTION_DOCS.items()
        if any(tok in doc.lower() for tok in tokens)
    }
    scored: list[tuple[int, CatalogEntry]] = []
    for entry in entries:
        hay_machine = f"{entry.attr} {entry.section} {entry.field}".lower()
        hay_doc = entry.doc.lower()
        score = 0
        for tok in tokens:
            if tok in hay_machine:
                score += 12
            elif tok in hay_doc:
                score += 4
        if entry.section in hot_sections:
            score += 8
        if score:
            scored.append((score, entry))
    if not scored:
        # 兜底：拿整串去撞字段名，覆盖 "cpu_threshold" 这类直接抄名字的问法
        needle = query.strip().lower()
        scored = [(1, e) for e in entries if needle and needle in e.attr.lower()]
    scored.sort(key=lambda pair: (-pair[0], pair[1].section, pair[1].field))
    return [entry for _, entry in scored[:limit]]


def sections_touched(entries: list[CatalogEntry]) -> list[str]:
    """结果里涉及哪些段（去重保序），用于在回执末尾提示"相关的还有哪些"。"""
    seen: list[str] = []
    for entry in entries:
        if entry.section not in seen:
            seen.append(entry.section)
    return seen


def read_current(settings: Any, entry: CatalogEntry) -> Any:
    """读当前生效值；读不到（实例缺该属性）返回 ``None`` 而不是抛。"""
    try:
        return getattr(settings, entry.attr)
    except (AttributeError, TypeError):
        return None


def human_value(entry: CatalogEntry, value: Any) -> str:
    """把值排版成人话；凭据类只回"已设置/未设置"。

    凭据取值一旦进了模型上下文就收不回来（它可能被推理带进别处、被写进日志），
    所以这里是最早的一道闸：连值都不给它看见。
    """
    if entry.sensitive:
        return "已设置" if value not in (None, "", [], {}) else "未设置"
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "开" if value else "关"
    if isinstance(value, (list, tuple)):
        if not value:
            return "（空）"
        head = "、".join(str(v) for v in value[:4])
        return head + (f" … 共 {len(value)} 项" if len(value) > 4 else "")
    text = str(value)
    return text if len(text) <= 40 else text[:37] + "…"


def resolve_section(name: str) -> str | None:
    """把用户口中的段名对到真实段名（容忍拼不全 / 多写了尾巴）。

    用户不会照着 `[policy.notify_budget]` 敲——他会写 ``perceptio``、``notify``。
    先精确、再做前缀互容；只在**唯一**命中时采用，否则交回调用方走关键词检索：
    猜错段比不给段更槽，会把一整段无关项摊到用户面前。

    Args:
        name: 用户给的段名（大小写不敏感）。

    Returns:
        真实段名；无法唯一确定时 ``None``。
    """
    needle = str(name or "").strip().lower()
    if not needle:
        return None
    known = {e.section for e in build_catalog()}
    if needle in {s.lower() for s in known}:
        return next(s for s in known if s.lower() == needle)
    loose = [s for s in known if s.lower().startswith(needle) or needle.startswith(s.lower())]
    return loose[0] if len(loose) == 1 else None


def entries_of_section(section: str) -> list[CatalogEntry]:
    """取某一整段的所有项（用户说"监控怎么配"时用它把整段摊开）。

    段名先过一道 :func:`resolve_section`，于是 ``perceptio`` 这种拼不全的写法
    也能拿到 ``[percept]`` 整段，而不是直接回一个空列表。
    """
    real = resolve_section(section)
    if real is None:
        return []
    return [e for e in build_catalog() if e.section == real]


def section_doc(section: str) -> str:
    """段的用途一句话；没有登记的段返回空串（不编造）。"""
    return SECTION_DOCS.get(section, "")


def render_entries(entries: list[CatalogEntry], settings: Any) -> str:
    """把若干目录项按**段分组**渲染成给模型/用户看的纯文本块。

    分组而不是平铺，是因为用户问"邮件怎么配"时真正需要的上下文是"这一整块在
    干什么、大概要填哪几个"——平铺一串字段名反而看不出结构。段首那句用途同时
    承担了"模型能 paraphrase 成人话"的职责。
    """
    if not entries:
        return (
            "没有找到匹配的配置项。可以换个说法（例如用中文功能名而不是字段名），"
            "或直接问「有哪些配置」看总览。"
        )
    lines: list[str] = []
    current = None
    for entry in entries:
        if entry.section != current:
            current = entry.section
            head = f"[{entry.section}]"
            doc = section_doc(entry.section)
            lines.append(f"{head} {doc}" if doc else head)
        current_value = human_value(entry, read_current(settings, entry))
        lines.append(f"  · {entry.field} = {current_value}")
        if entry.doc:
            lines.append(f"      {entry.doc}")
        if entry.dead:
            # 比“能不能代改”更优先告诉用户：这项改了根本不会生效
            lines.append(f"      ⚠ 当前版本未接线：{entry.dead}")
        elif entry.editable:
            lines.append(f"      → 我可以代改：/config set {entry.spec_key} <值>")
        else:
            lines.append(f"      → 只能自己改：{entry.toml_path}")
    return "\n".join(lines)


def render_overview() -> str:
    """配置总览：每个段一行（用途 + 项数 + 项数里有几项可代改）。

    用户问“都有哪些配置”时给这张表——两百个字段平铺出去没人看得下去，按段收敛
    到三十行才能一眼找到“我要找的那块大概在哪”。
    """
    by_section: dict[str, list[CatalogEntry]] = {}
    for entry in build_catalog():
        by_section.setdefault(entry.section, []).append(entry)
    lines = [f"共 {len(build_catalog())} 项配置，分布在 {len(by_section)} 个段："]
    for section, entries in sorted(by_section.items()):
        editable = sum(1 for e in entries if e.editable)
        tail = f"（{editable} 项可代改）" if editable else ""
        doc = section_doc(section)
        lines.append(f"  [{section}] {doc}{tail}")
    lines.append("想看某段细节：/config explain <段名或功能词>")
    return "\n".join(lines)


def describe_missing(key: str) -> str | None:
    """``/config set`` 撞到“表里没有”时，判断它是「不存在」还是「存在但未纳管」。

    这是两类完全不同的答案，混成一句“不认识的配置项”会让用户以为自己拼错了：
    实际上常常是**这项存在**、只是没进真源表（现阶段只能改文件）。所以按三段回：
    能按名称或属性名精确命中目录 → 说明它在哪、怎么改；能模糊命中 → 提示可能想问
    的是哪几个；完全没命中 → 才如实说没这项。

    Args:
        key: 用户敲的键名（真源表协议键、TOML 字段名或 Settings 属性名都有可能）。

    Returns:
        给用户看的说明文本；``None`` 表示目录里也确实没有这个名字。
    """
    entries = build_catalog()
    needle = key.strip().lower()
    removed = REMOVED_FIELDS.get(needle)
    if removed is None:
        # 也认 TOML 侧的短名：用户手里那份配置写的就是 ``hotkey``，不是
        # ``daemon_hotkey``。
        alias = _REMOVED_ALIASES.get(needle)
        removed = REMOVED_FIELDS.get(alias) if alias else None
    if removed:
        # 已删字段要最先说：用户是从自己的配置文件里抄来的名字，先告诉他“这个字段
        # 已经没了”，比绕一圈“没找到、相近的有…”更直白。
        return f"{key} 这个字段已经不存在了（旧配置里写了也会被静默忽略）。{removed}"
    exact = [e for e in entries if needle in (e.attr.lower(), e.field.lower(), (e.spec_key or "").lower())]
    dead = [e for e in exact if e.dead]
    if dead:
        # 死配置要第一个说，而且说在「能不能代改」之前：它连改都不生效，
        # 把人推去翻文件等于骗他白改一场。
        lines = [f"{key} 这项当前版本未接线（改了不会生效）："]
        for entry in dead:
            lines.append(f"  {entry.toml_path} 里的 {entry.field}")
            lines.append(f"    {entry.dead}")
        return "\n".join(lines)
    if exact:
        # 命中里可能有**已经纳管**的项：用户多半是用了 Settings 属性名
        # （``monitor_work_break_interval``）或 TOML 字段名，而真源表收的是协议键
        # （``work_break_interval``）。这种情况说“不能代改”是错的，该直接把
        # 协议键告诉他——否则用户明明能一句话改掉，却被打发去翻文件。
        live = [e for e in exact if e.editable]
        if live:
            lines = [f"{key} 这项可以代改，用协议键："]
            for entry in live:
                lines.append(f"  /config set {entry.spec_key} <值>")
                lines.append(f"    位置：{entry.toml_path} 里的 {entry.field}")
            return "\n".join(lines)
        lines = [f"{key} 这项存在，但不在「可以让我代改」的范围内："]
        for entry in exact:
            lines.append(f"  {entry.toml_path} 里的 {entry.field}")
            if entry.doc:
                lines.append(f"    {entry.doc}")
        lines.append("需要你自己编辑该文件；改完按提示重启或等下次启动生效。")
        return "\n".join(lines)
    near = search_catalog(key, limit=4)
    if near:
        names = "、".join(f"{e.field}({e.section})" for e in near)
        return f"没有 {key} 这个键。相近的配置项有：{names}。用 /config explain <关键词> 看详细。"
    return None
