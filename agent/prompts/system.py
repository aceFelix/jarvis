"""系统提示组装。

含工具使用规范、安全边界、环境信息等。v0.1 先给一个精简但有效的版本:

1. 角色与能力声明
2. 工具使用规范（强调先读后写、用 TodoWrite 规划）
3. 安全约束（危险操作需确认、不碰敏感目录）
4. 环境信息（OS / workdir / 可用工具列表）

系统提示在每轮对话都发给 LLM，所以保持精炼。

本模块只负责**拼装**（按注册表、平台、思考开关决定哪几段进提示）；逐字发给模型的
静态正文已拆到 :mod:`agent.prompts.text_blocks`，以满足单文件 800 行的项目结构规则。
"""

from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path

from agent.core.tool import ToolRegistry
from agent.prompts.text_blocks import (
    _BASE_PROMPT,
    _GOAL_SPINE_SECTION,
    _MAIL_SECTION,
    _NO_THINKING_SECTION,
    _PLATFORM_LINUX,
    _PLATFORM_MACOS,
    _PLATFORM_WINDOWS,
    _SCHEDULE_SECTION,
    _SELF_CONFIG_SECTION,
)


def _env_section(workdir: str) -> str:
    """环境信息小节。

    时间戳精度降到日期级别，避免每秒变化破坏跨会话的 system prompt 前缀缓存。
    """
    return (
        f"# 环境\n\n"
        f"- 操作系统: {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"- 工作目录: {Path(workdir).resolve()}\n"
        f"- 当前日期: {datetime.now().strftime('%Y-%m-%d')}\n"
    )


def _platform_section() -> str:
    """根据 platform.system() 动态生成平台操作规范。

    不同平台的 Shell、路径、权限模型差异很大，
    动态生成可避免向 macOS/Linux 用户发送 Windows 规则（反之亦然）。
    """
    system = platform.system()  # Windows / Darwin / Linux

    if system == "Windows":
        return _PLATFORM_WINDOWS
    elif system == "Darwin":
        return _PLATFORM_MACOS
    else:
        return _PLATFORM_LINUX


def _tools_section(registry: ToolRegistry) -> str:
    """可用工具列表（让模型知道有哪些工具，详细规范见 _BASE_PROMPT）。

    延迟加载模式下：核心工具列出完整描述，延迟工具仅列名字摘要。
    """
    core_tools = registry.all_core()
    deferred_tools = registry.all_deferred()

    lines = []
    # 核心工具：完整描述
    for tool in core_tools:
        desc_first = tool.description.split("\n", 1)[0]
        lines.append(f"- **{tool.name}**: {desc_first}")

    section = "# 可用工具\n\n" + "\n".join(lines) + "\n"

    # 延迟工具：仅名字摘要，提示用 ToolSearch 加载
    if deferred_tools:
        names = [t.name for t in deferred_tools]
        section += (
            "\n<available-deferred-tools>\n"
            + ", ".join(names)
            + "\n（以上为延迟加载工具，用 ToolSearch 搜索关键词加载完整定义后即可调用）\n"
            "</available-deferred-tools>\n"
        )

    return section


def _cli_anything_section(registry: ToolRegistry) -> str:
    """CLI-Anything harness 名字摘要。

    只列名字不展开详细描述——详细用法交给 ToolSearch 按需加载，
    避免 system prompt 膨胀破坏 LLM 前缀缓存。
    """
    harness_tools = [
        tool for tool in registry.all()
        if tool.name.startswith("cli_anything__")
    ]
    if not harness_tools:
        return ""

    names = [t.name for t in harness_tools]
    return (
        "# CLI-Anything 外部软件控制\n\n"
        + ", ".join(names)
        + "\n（以上为 CLI-Anything 外部软件控制工具，"
        "用 ToolSearch 搜索关键词加载完整用法后即可调用）\n"
    )


def _schedule_section(registry: ToolRegistry) -> str:
    """日程提醒段（按需注入）。

    只有当前环境实际注册了提醒工具时才返回该段：serve/桌面宿主经
    registry_hook 挂载 ScheduleReminder/ListSchedule/CancelSchedule，
    终端 CLI 不注册。若不加判断地注入，终端下的模型会以为能用提醒功能，
    却怎么 ToolSearch 都搜不到工具，进而反复空转甚至绕道自造实现。

    @author aceFelix
    """
    if "ScheduleReminder" not in registry:
        return ""
    return _SCHEDULE_SECTION


def _goal_spine_section(registry: ToolRegistry) -> str:
    """跨会话目标段（按需注入）：只有注册了 TrackGoal 才拼进提示。

    @author aceFelix
    """
    if "TrackGoal" not in registry:
        return ""
    return _GOAL_SPINE_SECTION


def _self_config_section(registry: ToolRegistry) -> str:
    """自配置段（按需注入）：只有注册了 ConfigSet 才拼进提示。

    子代理的受限工具集里往往没有这两个工具（也不该有——它不该替用户改全局配置），
    不判断就会让它去调一个手上没有的工具（同 ``_goal_spine_section`` 的坑）。

    @author aceFelix
    """
    if "ConfigSet" not in registry:
        return ""
    return _SELF_CONFIG_SECTION


def _mail_section(registry: ToolRegistry) -> str:
    """邮件段（按需注入）：只有注册了 EmailBox 才拼进提示。

    为什么要按需：这段教的是「播报只有一句话、正文要用 EmailBox 拉」——
    工具不在手上时讲这些，只会让模型拿用户的“念一下”去调一个不存在的
    工具，然后道个歉把话题接回能力边界，比他一开始就说“看不到正文”更差。

    @author aceFelix
    """
    if "EmailBox" not in registry:
        return ""
    return _MAIL_SECTION


def build_system_prompt(
    workdir: str,
    registry: ToolRegistry,
    *,
    enable_thinking: bool = True,
    settings: object | None = None,
) -> str:
    """组装完整系统提示。

    顺序: 基础原则 -> 长期记忆 -> 画像记忆 -> 会话记忆 -> 技能包 -> 环境 -> 工具列表。

    LLM 缓存友好设计：
    - 所有子段均为静态常量或文件读取，同一会话内 system prompt 完全相同
    - 日期精度降至日期级（strftime('%Y-%m-%d')），而非秒级 time.time()
    - harness 描述精简为名字列表（ToolSearch 按需加载详细用法）
    - 平台规范按 platform.system() 静态选择，不随请求变化
    - 画像记忆首次渲染后进程内缓存（后台提炼更新下个会话生效）
    """
    from agent.core.memory.store import memory_section
    from agent.core.extensions.skills import skills_section
    from agent.core.memory.compactor import load_session_memory

    session_mem = load_session_memory(workdir)

    # 根据思考模式开关替换系统提示中的思考规范
    if enable_thinking:
        base = _BASE_PROMPT
    else:
        thinking_start = _BASE_PROMPT.find("# 思考规范（思维链）")
        thinking_end = _BASE_PROMPT.find("\n\n# 工具使用规范", thinking_start)
        if thinking_start != -1 and thinking_end != -1:
            base = (
                _BASE_PROMPT[:thinking_start]
                + _NO_THINKING_SECTION
                + _BASE_PROMPT[thinking_end:]
            )
        else:
            base = _BASE_PROMPT

    return "\n".join(
        [
            base,
            memory_section(workdir),
            _profile_section(settings),
            # 会话记忆标题内嵌使用守则：记忆是历史快照不是事实——防止
            # agent 拿记忆里的旧断言（如“用户有一个 X 项目”）当必须核实的
            # 悬案，反复全盘检索空转（2026-10-14 贪吃蛇案）。
            # @author aceFelix
            (
                "# 会话记忆（历史快照，仅作线索）\n\n"
                "以下内容为过往对话的摘要，其中提到的文件/项目/任务状态均未重新核实："
                "1. 不要为了验证记忆里的断言而反复检索文件系统；"
                "2. 用户当前指令与记忆冲突时，一律以当前指令为准；"
                "3. 确实需要核实最多尝试一两次，找不到就直接按新任务从头做。\n\n"
                f"{session_mem}"
            ) if session_mem else "",
            skills_section(workdir),
            _env_section(workdir),
            _platform_section(),
            _schedule_section(registry),
            _goal_spine_section(registry),
            _self_config_section(registry),
            _mail_section(registry),
            _cli_anything_section(registry),
            _tools_section(registry),
        ]
    )


# ── 画像记忆注入（Phase 1a M2）──

# 进程内缓存：同一会话内 system prompt 必须逐字节稳定（LLM 前缀缓存），
# 后台提炼新写入的画像条目在下一次进程启动（或 /memory reload）时才生效。
_PROFILE_CACHE: str | None = None


def _profile_section(settings: object | None) -> str:
    """画像记忆段。首次调用渲染并缓存，之后直接复用。

    两段合并（P2 画像双向同步）：知识图谱画像（唯一事实源，启动时经 MCP
    预加载到 profile_bridge 缓存）+ 本地画像记忆（profile_refiner 提炼）。
    """
    global _PROFILE_CACHE
    if _PROFILE_CACHE is not None:
        return _PROFILE_CACHE
    _PROFILE_CACHE = ""
    try:
        sections: list[str] = []
        # 知识图谱画像段（已启动预加载则直接取；未开启/拉取失败为空）
        from agent.core.extensions.profile_bridge import kg_profile_section
        kg_section = kg_profile_section()
        if kg_section:
            sections.append(kg_section)

        if settings is not None and getattr(settings, "profile_enabled", False):
            from agent.core.memory.profile_store import ProfileStore

            limit = int(getattr(settings, "profile_inject_token_limit", 300))
            local = ProfileStore().render_for_prompt(token_limit=limit)
            if local:
                sections.append(local)
        _PROFILE_CACHE = "\n\n".join(sections)
    except Exception:
        pass  # 画像注入失败不影响启动
    return _PROFILE_CACHE


def reload_profile_cache() -> None:
    """/memory 系列命令增删后主动刷新缓存（供下轮 system prompt 重建时生效）。"""
    global _PROFILE_CACHE
    _PROFILE_CACHE = None
