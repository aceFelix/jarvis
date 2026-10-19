"""TOML 表 → Settings 字段的声明式映射（从 settings.py 的 `_apply_toml` 拆出）。

为什么拆（aceFelix）：`_apply_toml` 原本串行写着 20+ 个结构完全同构的
「取表 → isinstance(dict) → 遍历 (子键, 字段) → 命中就写 updates」循环，
每加一个功能域就要再抄一遍，settings.py 因此逼近/突破单文件 800 行上限
（阶段 9 加 `[percept]` 表时正好撞上）。本模块把映射**降级为数据**：
新增一个配置项只需在对应域的元组里加一行。

保持的行为不变量（逐条对齐拆分前的实现）：
1. **表缺失或不是 dict → 整表跳过**，绝不写 updates（否则 models.toml 这类
   不含该表的模型域文件会把已从 settings.toml 读到的值清空——2026-09 模型域
   拆分后真实踩过，见 lsp 域的「没有表 vs 空表」注释）。
2. **表内未出现的键静默忽略**：废弃键（如 `[policy].enabled/path`、
   `[memory].auto_resume_session`）不在映射表里就等于被忽略，旧配置照常工作。
3. 数组字段统一 `list()` 归一（`with_overrides` 不做类型转换，TOML 给的是 list，
   但仍要显式复制一份，避免多个 Settings 实例共享同一个可变列表）。
4. 特殊转换只有两处：`[update].check` 取 bool、`[tts].custom_voices` 需
   「非空 + 逐项 dict」过滤，都在本模块内显式处理并注释原因。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

#: 扁平映射：(表名, ((子键, Settings 字段), ...))
FLAT_DOMAINS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    # [tts] 表（嵌套子表 custom_voices 见 DICT_CHILDREN）
    ("tts", (
        ("model", "tts_model"),
        ("voice", "tts_voice"),
        ("volume", "tts_volume"),
        ("speech_rate", "tts_speech_rate"),
        ("pitch_rate", "tts_pitch_rate"),
    )),
    ("stt", (
        ("model", "stt_model"),
        ("max_seconds", "stt_max_seconds"),
        ("silence_seconds", "stt_silence_seconds"),
    )),
    ("voice", (
        ("barge_in", "voice_barge_in"),
        ("barge_in_key", "voice_barge_in_key"),
    )),
    # [realtime_talk] 表 → realtime_* 字段 + dashscope_api_key（实时语音必须用
    # DashScope 专属 key，不能与当前 LLM 的 key 混用）
    ("realtime_talk", (
        ("api_key", "dashscope_api_key"),
        ("ws_url", "realtime_ws_url"),
        ("model", "realtime_model"),
        ("voice", "realtime_voice"),
        ("event_log", "realtime_event_log"),
        ("echo_suppress_with_aec", "realtime_echo_suppress_with_aec"),
        ("half_duplex", "realtime_half_duplex"),
        ("rescue", "realtime_rescue"),
        ("turn_detection", "realtime_turn_detection"),
        ("silence_ms", "realtime_silence_ms"),
        ("tools_mode", "realtime_tools_mode"),
    )),
    ("bridge", (  # P3-1 跨设备协同
        ("http_port", "bridge_http_port"),
        ("ws_port", "bridge_ws_port"),
        ("token", "bridge_token"),
    )),
    ("ui", (
        ("boot_animation", "boot_animation"),
    )),
    ("context", (
        ("compaction", "context_compaction"),
        ("tool_result_keep_recent", "tool_result_keep_recent"),
        ("context_window", "context_window"),
        ("compact_ratio", "compact_ratio"),
        ("compact_refreeze_growth", "compact_refreeze_growth"),
        ("compact_max_output_tokens", "compact_max_output_tokens"),
    )),
    # [memory] 表：auto_resume_session 已下线，用户旧配置里残留该键时静默忽略；
    # 画像记忆（Phase 1a）字段 + 嵌套 [memory.refine] 简写形式见 SUB_TABLES
    ("memory", (
        ("long_term_memory", "long_term_memory"),
        ("profile_enabled", "profile_enabled"),
        ("profile_max_entries", "profile_max_entries"),
        ("profile_inject_token_limit", "profile_inject_token_limit"),
        ("profile_refine_min_messages", "profile_refine_min_messages"),
        ("profile_refine_interval", "profile_refine_interval"),
        ("profile_refine_model", "profile_refine_model"),
        ("profile_refine_provider", "profile_refine_provider"),
        ("profile_refine_base_url", "profile_refine_base_url"),
        ("profile_refine_api_key", "profile_refine_api_key"),
    )),
    ("skills", (("enable", "enable_skills"),)),
    ("mcp", (("enable", "enable_mcp"),)),
    ("profile_bridge", (  # P2 画像双向同步（知识图谱桥）
        ("enabled", "profile_bridge_enabled"),
        ("server", "profile_bridge_server"),
        ("token_limit", "profile_bridge_token_limit"),
    )),
    ("plugins", (
        ("enable", "enable_plugins"),
        ("marketplace_url", "plugin_marketplace"),
        ("marketplace_local", "plugin_market_local"),
    )),
    ("cli_anything", (  # 自定义 harness 市场
        ("market_url", "harness_market_url"),
        ("market_local", "harness_market_local"),
    )),
    ("email", (  # 邮件：SMTP 发件 + IMAP 收件（W2 mail 源）；读写口径单一真源见 config/email_settings.py
        ("enabled", "email_enabled"),
        ("smtp_host", "email_smtp_host"),
        ("smtp_port", "email_smtp_port"),
        ("smtp_user", "email_smtp_user"),
        ("smtp_password", "email_smtp_password"),
        ("sender", "email_sender"),
        ("default_recipient", "email_default_recipient"),
        ("imap_host", "email_imap_host"),
        ("imap_port", "email_imap_port"),
        ("imap_user", "email_imap_user"),
        ("imap_password", "email_imap_password"),
        ("imap_mailbox", "email_imap_mailbox"),
    )),
    ("lsp", (("enable", "enable_lsp"),)),  # 嵌套 servers 见 DICT_CHILDREN
    ("daemon", (  # 常驻模式热键 + P2-3 每日简报 + 二期主动播报 TTS 总开关
        ("hotkey", "daemon_hotkey"),
        ("hotkey_native", "daemon_hotkey_native"),
        ("hotkey_debounce_ms", "daemon_hotkey_debounce_ms"),
        ("briefing_enabled", "briefing_enabled"),
        ("briefing_time", "briefing_time"),
        ("briefing_catchup_window_min", "briefing_catchup_window_min"),
        ("proactive_tts_enabled", "proactive_tts_enabled"),
    )),
    ("monitor", (  # 系统监控阈值 + P2-3 增强项
        ("enabled", "monitor_enabled"),
        ("cpu_threshold", "monitor_cpu_threshold"),
        ("memory_threshold", "monitor_memory_threshold"),
        ("disk_threshold", "monitor_disk_threshold"),
        ("check_interval", "monitor_check_interval"),
        ("alert_cooldown", "monitor_alert_cooldown"),
        ("disk_trend_days", "monitor_disk_trend_days"),
        ("high_cpu_duration", "monitor_high_cpu_duration"),
        ("work_break_interval", "monitor_work_break_interval"),
    )),
    ("deadline", (
        ("enabled", "deadline_enabled"),
        ("check_time", "deadline_check_time"),
    )),
    ("calendar", (
        ("enabled", "calendar_enabled"),
        ("backend", "calendar_backend"),
        ("ics_path", "calendar_ics_path"),
        ("ics_url", "calendar_ics_url"),
        ("remind_minutes_before", "calendar_remind_minutes_before"),
    )),
    ("sandbox", (  # P3-8 安全沙箱
        ("enabled", "sandbox_enabled"),
        ("max_memory_mb", "sandbox_max_memory_mb"),
        ("max_cpu_seconds", "sandbox_max_cpu_seconds"),
        ("max_processes", "sandbox_max_processes"),
        ("timeout", "sandbox_timeout"),
        ("block_network", "sandbox_block_network"),
        ("auto_allow_medium", "sandbox_auto_allow_medium"),
        ("audit", "sandbox_audit"),
        ("max_snapshots", "sandbox_max_snapshots"),
        ("excluded_commands", "sandbox_excluded_commands"),  # 数组，见 LIST_FIELDS
    )),
    ("checkpoint", (  # 消息级回溯检查点（shadow git）
        ("enabled", "checkpoint_enabled"),
        ("max_per_session", "checkpoint_max_per_session"),
        ("timeout_seconds", "checkpoint_timeout_seconds"),
    )),
    ("policy", (  # W1 运行时层：enabled/path 两键已废弃（策略引擎并入管线），不在此映射即静默忽略
        ("away_idle_minutes", "away_idle_minutes"),
    )),
    ("percept", (  # W2 感知层：子键不带 percept_ 前缀（与 [policy.notify_budget] 同口径）
        ("enabled", "percept_enabled"),
        ("dedup_window_s", "percept_dedup_window_s"),
        ("min_salience", "percept_min_salience"),
        ("history_max", "percept_history_max"),
        ("log_events", "percept_log_events"),
        ("auto_disable_after_errors", "percept_auto_disable_after_errors"),
        ("window_enabled", "percept_window_enabled"),
        ("window_interval_s", "percept_window_interval_s"),
        ("window_title_max_chars", "percept_window_title_max_chars"),
        ("window_title_full_enabled", "percept_window_title_full_enabled"),
        ("window_exclude_apps", "percept_window_exclude_apps"),
        ("window_linger_minutes", "percept_window_linger_minutes"),
        ("file_enabled", "percept_file_enabled"),
        ("file_watch_dirs", "percept_file_watch_dirs"),
        ("file_interval_s", "percept_file_interval_s"),
        ("file_max_entries", "percept_file_max_entries"),
        ("file_grow_threshold", "percept_file_grow_threshold"),
        ("file_ignore_suffixes", "percept_file_ignore_suffixes"),
        ("file_min_interval_s", "percept_file_min_interval_s"),
        ("vcs_enabled", "percept_vcs_enabled"),
        ("vcs_repos", "percept_vcs_repos"),
        ("vcs_interval_s", "percept_vcs_interval_s"),
        ("vcs_min_interval_s", "percept_vcs_min_interval_s"),
        ("mail_enabled", "percept_mail_enabled"),
        ("mail_min_interval_s", "percept_mail_min_interval_s"),
        ("mail_watch_senders", "percept_mail_watch_senders"),
        ("calendar_enabled", "percept_calendar_enabled"),
        ("system_enabled", "percept_system_enabled"),
        ("schedule_enabled", "percept_schedule_enabled"),
        ("vision_enabled", "percept_vision_enabled"),
        ("vision_camera_index", "percept_vision_camera_index"),
        ("vision_fps", "percept_vision_fps"),
        ("vision_gesture_enabled", "percept_vision_gesture_enabled"),
        ("vision_auto_stop_minutes", "percept_vision_auto_stop_minutes"),
        ("vision_interval_s", "percept_vision_interval_s"),
    )),
    ("tools", (  # 工具延迟加载
        ("deferred_loading", "tools_deferred_loading"),
        ("chat_detection", "tools_chat_detection"),
    )),
)

#: 嵌套子表映射：(父表, 子表键, ((子键, Settings 字段), ...))
SUB_TABLES: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    # [memory.refine] → profile_refine_* 简写形式（写全名与简写等价，全名优先由
    # 遍历顺序保证：子表在后，故 refine 子表会覆盖全名键——与拆分前保持一致）
    ("memory", "refine", (
        ("model", "profile_refine_model"),
        ("provider", "profile_refine_provider"),
        ("base_url", "profile_refine_base_url"),
        ("api_key", "profile_refine_api_key"),
        ("interval", "profile_refine_interval"),
    )),
    # [policy.notify_budget] → W1 打扰预算
    ("policy", "notify_budget", (
        ("enabled", "notify_budget_enabled"),
        ("daily_max", "notify_daily_max"),
        ("quiet_hours", "notify_quiet_hours"),
        ("aggregate_window_s", "notify_aggregate_window_s"),
        ("min_salience", "notify_min_salience"),
    )),
)

#: 整体搬运的字典型子表：(父表, 子表键, Settings 字段, 值形态)
#: 值形态 "shallow"=dict(child)；"dict-of-dict"=逐项取 dict 且过滤非 dict 值
DICT_CHILDREN: tuple[tuple[str, str, str, str], ...] = (
    # [tts.custom_voices] → /tts-voice 添加的自定义音色（空表表示"没配过"，
    # 不写入，避免把已加载的音色清掉）
    ("tts", "custom_voices", "custom_voices", "dict-of-dict"),
    # [lsp.servers.<name>] → lsp_servers。**必须区分「没有 [lsp] 表」与
    # 「[lsp] 表为空」**：models.toml 等模型域文件不含 [lsp]，若无条件写
    # lsp_servers 会清空先从 settings.toml 读到的值（2026-09 模型域拆分后暴露）。
    # 子表存在才覆盖，显式空子表可表达"清空全部服务器"。
    ("lsp", "servers", "lsp_servers", "shallow"),
)

#: 需要显式复制成 list 的字段（可变默认值不能跨实例共享）
LIST_FIELDS: frozenset[str] = frozenset({
    "sandbox_excluded_commands",
    "percept_window_exclude_apps",
    "percept_file_watch_dirs",
    "percept_file_ignore_suffixes",
    "percept_vcs_repos",
    "percept_mail_watch_senders",
})


def apply_domain_tables(data: dict[str, Any], updates: dict[str, Any]) -> None:
    """按声明式映射把各 TOML 表写入 ``updates``（就地修改，不返回新对象）。

    只做「表内已有的键」的写入：任何缺失键都不进 updates，因此多文件分层
    合并（项目级 → 用户级 → 模型域）时后加载的文件不会用默认值覆盖前面
    已读到的值——这一条是 `_apply_toml` 多次回归的核心防线。
    """
    for table_name, pairs in FLAT_DOMAINS:
        table = data.get(table_name)
        if not isinstance(table, dict):
            continue
        for sub_key, field in pairs:
            if sub_key in table:
                updates[field] = table[sub_key]

    for parent, child_key, pairs in SUB_TABLES:
        table = data.get(parent)
        if not isinstance(table, dict):
            continue
        child = table.get(child_key, {})
        if not isinstance(child, dict):
            continue
        for sub_key, field in pairs:
            if sub_key in child:
                updates[field] = child[sub_key]

    for parent, child_key, field, shape in DICT_CHILDREN:
        table = data.get(parent)
        if not isinstance(table, dict):
            continue
        child = table.get(child_key)
        if not isinstance(child, dict):
            continue
        if shape == "dict-of-dict":
            if not child:
                continue
            updates[field] = {str(k): dict(v) for k, v in child.items() if isinstance(v, dict)}
        else:
            updates[field] = dict(child)

    for field in LIST_FIELDS:
        if field in updates:
            updates[field] = list(updates[field] or [])

    # [update] 表 → 版本自更新（Phase 1）：只认 check 键，且强制 bool
    update_table = data.get("update")
    if isinstance(update_table, dict) and "check" in update_table:
        updates["update_check"] = bool(update_table["check"])
