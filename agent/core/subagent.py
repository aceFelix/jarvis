"""子代理协作 —— 让主 agent 派生子 agent 并行处理子任务。

阶段五第二刀。致敬 Claude Code 的 AgentTool + LocalAgentTask 架构，
用 Python 重写为更简洁的实现。

核心概念:
1. **AgentDefinition**: 子 agent 的"人格定义"——system prompt、允许的工具集、
   模型选择。类比 ClaudeCode 的 BuiltInAgentDefinition。
2. **SubagentRunner**: 子 agent 的执行器。创建独立的 QueryLoop + 受限
   ToolRegistry + 独立 messages 列表，跑到子 agent 不再调工具为止，
   返回最终文本回复。子 agent 的对话历史独立，不污染主对话。
3. **内置子 agent 类型**:
   - ``explorer``: 只读搜索专家（Glob/Grep/FileRead/Bash只读），快速找文件/代码
   - ``researcher``: 研究分析专家（完整只读工具），深度调研分析
   - ``coder``: 代码编写专家（完整工具），写代码/改文件/跑测试
   - ``general``: 通用助手（完整工具），处理复杂多步任务

工作流:
    主 agent 收到复杂任务
      → 调 SubagentTool(prompt="找所有 TODO", agent_type="explorer")
      → SubagentRunner 创建 explorer 子 agent（只读工具集）
      → 子 agent 独立跑 QueryLoop（Glob/Grep/FileRead...）
      → 子 agent 完成后返回文本报告
      → 报告作为 tool_result 回灌给主 agent
      → 主 agent 据此继续（综合多个子 agent 的结果）

并行: 主 agent 可同时调多个 SubagentTool（orchestrator 支持并行工具执行），
      多个子 agent 同时跑，各自独立 LLM 会话。

与 ClaudeCode 的区别:
- 无 Task 框架（不做后台任务/进度跟踪/可中断），子 agent 同步阻塞执行
- 无 sidechain transcript（子 agent 对话不落盘，仅返回最终文本）
- 无 fork（不继承主 agent 对话上下文，每次从空白开始）
- 这些简化在 v0.1 足够用，后续按需扩展

依赖: agent.core.query_loop.QueryLoop（复用主循环逻辑）
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.message import Message
from agent.core.orchestrator import ToolOrchestrator
from agent.core.query_loop import QueryLoop
from agent.core.tool import ToolRegistry, build_default_registry
from agent.permissions import PermissionChecker
from agent.permissions.modes import PermissionMode
from agent.permissions.rules import RuleSet


# ---------------------------------------------------------------------------
# 子 agent 定义
# ---------------------------------------------------------------------------


@dataclass
class AgentDefinition:
    """子 agent 的人格定义。

    类比 ClaudeCode 的 BuiltInAgentDefinition。

    Attributes:
        agent_type: 类型标识符（explorer/researcher/coder/general）。
        description: 给主 agent 看的"何时用此 agent"说明。
        system_prompt: 子 agent 的系统提示（角色 + 能力 + 规范）。
        allowed_tools: 允许的工具名列表。None=继承全部工具；
            空列表=无工具（纯文本 agent）。Explorer 只给只读工具。
        model: 子 agent 用的模型。None=继承主 agent 的模型。
        max_iterations: 子 agent 最大工具迭代数（默认 15，比主 agent 的 25 小，
            防止子 agent 跑飞）。
    """

    agent_type: str
    description: str
    system_prompt: str
    allowed_tools: list[str] | None = None
    model: str | None = None
    max_iterations: int = 15


# ---- 只读工具集（explorer/researcher 用）----
_READ_ONLY_TOOLS = ["Glob", "Grep", "FileRead", "Bash", "TodoWrite"]


# ---- 内置子 agent 的 system prompt 前缀 ----
_AGENT_PREFIX = """\
你是贾维斯（JARVIS）派出的子代理，负责独立完成主代理交办的一个子任务。

你是贾维斯的延伸，性格和贾维斯一致：冷静、专业、忠诚。但你的对话对象是主代理\
（不是用户），所以回复要简洁、信息密度高，像工作汇报而非闲聊。

# 工作规范

1. **专注任务**: 只做主代理交办的事，不扩展范围。任务模糊就按最合理理解执行。
2. **自主完成**: 你有独立工具集，自行调用工具收集信息、执行操作，不需要追问。
3. **结果导向**: 任务完成后，用一段简洁的文字汇报结果——做了什么、发现了什么、\
有无异常。主代理会据此继续决策。
4. **不啰嗦**: 汇报只讲关键信息，不复述过程。代码/路径用反引号包裹。
"""


def _explorer_prompt() -> str:
    return _AGENT_PREFIX + """

# 你的角色：探索者（Explorer）

你是只读搜索专家，擅长在代码库中快速定位文件、搜索代码、理解结构。

## 限制（重要）

你是**只读**的，绝对不能修改任何文件。你只有以下工具:
- **Glob**: 按通配符找文件
- **Grep**: 在文件内容里搜正则
- **FileRead**: 读文件
- **Bash**: 只能跑只读命令（ls/cat/git log/git diff/find 等），不能写
- **TodoWrite**: 任务清单（可选，复杂搜索时用来规划）

## 搜索策略

- 不确定位置时先用 Glob 广撒网，再用 Grep 精确定位
- 找到关键文件后用 FileRead 读内容确认
- 多个搜索可以并行（一次调多个工具）
- 汇报时列出找到的文件路径 + 关键发现，不要贴大段源码
"""


def _researcher_prompt() -> str:
    return _AGENT_PREFIX + """

# 你的角色：研究员（Researcher）

你是研究分析专家，擅长深度调研、理解复杂系统、输出结构化分析。你有完整只读工具集。

## 能力

- **Glob/Grep/FileRead**: 搜索和阅读代码/文档
- **Bash**: 只读命令（git log/stat/diff、ls、cat 等）
- **TodoWrite**: 规划调研步骤

## 工作方式

- 先广后深：先搜全局了解结构，再聚焦关键文件细读
- 多角度验证：不只看一个文件，交叉印证
- 汇报结构化：分点陈述发现，标注信息来源（文件路径+行号）
- 给判断不只给事实：发现风险/机会要明确指出
"""


def _coder_prompt() -> str:
    return _AGENT_PREFIX + """

# 你的角色：编码者（Coder）

你是代码编写专家，擅长实现功能、修复 bug、重构代码。你有完整工具集（含写操作）。

## 能力

- **FileRead/FileEdit/FileWrite**: 读/改/写文件
- **Glob/Grep**: 搜索定位
- **Bash**: 执行命令（跑测试、装依赖、git 操作等）
- **TodoWrite**: 拆解实现步骤

## 工作规范

- **先读后写**: 改文件前先 FileRead 看清现状，不盲改
- **小步前进**: 复杂改动用 TodoWrite 拆步骤，逐步完成
- **改完验证**: 能跑测试就跑测试验证，能 lint 就 lint
- **汇报具体**: 改了哪些文件、加了什么、为什么这么改，简明交代
"""


def _general_prompt() -> str:
    return _AGENT_PREFIX + """

# 你的角色：通用助手（General）

你是通用任务执行者，什么都能做。适合主代理无法归类到 explorer/researcher/coder \
的复杂多步任务。你有完整工具集。

## 工作规范

- 复杂任务先用 TodoWrite 拆解
- 先理解再动手（只读工具探查现状）
- 小步前进，每步验证
- 汇报做了什么、结果如何、有无遗留问题
"""


# ---- 内置子 agent 注册表 ----
BUILTIN_AGENTS: dict[str, AgentDefinition] = {
    "explorer": AgentDefinition(
        agent_type="explorer",
        description=(
            "只读搜索专家。用于在代码库中快速找文件、搜代码、定位实现。"
            "当你需要搜索关键词/文件但不确定位置、或需要理解代码结构时用此 agent。"
            "它不能修改文件，只搜索和阅读。"
        ),
        system_prompt=_explorer_prompt(),
        allowed_tools=_READ_ONLY_TOOLS,
        max_iterations=12,
    ),
    "researcher": AgentDefinition(
        agent_type="researcher",
        description=(
            "研究分析专家。用于深度调研、理解复杂系统架构、输出结构化分析报告。"
            "当需要多角度交叉验证、深度理解某模块设计、评估技术方案时用此 agent。"
            "它是只读的，不改代码。"
        ),
        system_prompt=_researcher_prompt(),
        allowed_tools=_READ_ONLY_TOOLS,
        max_iterations=18,
    ),
    "coder": AgentDefinition(
        agent_type="coder",
        description=(
            "代码编写专家。用于实现功能、修复 bug、重构代码。有完整工具集（含写操作）。"
            "当任务明确需要修改/创建文件时用此 agent。它会在子任务范围内自主读改写验证。"
        ),
        system_prompt=_coder_prompt(),
        allowed_tools=None,  # None = 全部工具
        max_iterations=20,
    ),
    "general": AgentDefinition(
        agent_type="general",
        description=(
            "通用任务执行者。用于复杂多步任务，无法归类到 explorer/researcher/coder 时用。"
            "有完整工具集，能搜索、分析、编写、执行命令。适合需要多种能力混合的任务。"
        ),
        system_prompt=_general_prompt(),
        allowed_tools=None,
        max_iterations=18,
    ),
}


def get_agent_definition(agent_type: str) -> AgentDefinition | None:
    """按类型名获取子 agent 定义。不存在返回 None。"""
    return BUILTIN_AGENTS.get(agent_type)


def list_agent_types() -> list[str]:
    """列出所有内置子 agent 类型名。"""
    return list(BUILTIN_AGENTS.keys())


# ---------------------------------------------------------------------------
# 子 agent 执行器
# ---------------------------------------------------------------------------


def _build_sub_registry(allowed_tools: list[str] | None) -> ToolRegistry:
    """构建子 agent 的工具注册表（根据 allowed_tools 过滤）。

    allowed_tools=None → 继承全部默认工具（含 GUI/浏览器/MCP 动态工具不继承，
    只继承 build_default_registry 的内置工具）。
    allowed_tools=list → 只保留指定名字的工具。

    无论哪种模式，都排除 SubagentTool（防止子 agent 无限递归派生）。
    """
    full = build_default_registry()
    if allowed_tools is None:
        sub = ToolRegistry()
        for tool in full.all():
            if tool.name == "Subagent":
                continue  # 子 agent 不能再派生子 agent（防递归）
            # 复制注册（ToolRegistry.register 不允许重名，这里手动重建）
            sub.register(tool)
        return sub

    sub = ToolRegistry()
    for name in allowed_tools:
        tool = full.get(name)
        if tool is not None and name != "Subagent":
            sub.register(tool)
    return sub


def _build_sub_system_prompt(agent_def: AgentDefinition, workdir: str) -> str:
    """组装子 agent 的完整 system prompt: 人格 + 环境信息 + 可用工具列表。"""
    tools = _build_sub_registry(agent_def.allowed_tools)
    tool_lines = []
    for tool in tools.all():
        desc_first = tool.description.split("\n", 1)[0]
        tool_lines.append(f"- **{tool.name}**: {desc_first}")

    env = (
        f"\n# 环境\n\n"
        f"- 操作系统: {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"- 工作目录: {Path(workdir).resolve()}\n"
        f"- 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    tools_section = "\n# 可用工具\n\n" + "\n".join(tool_lines) + "\n" if tool_lines else ""

    return agent_def.system_prompt + env + tools_section


@dataclass
class SubagentResult:
    """子 agent 执行结果。

    Attributes:
        success: 是否成功完成（False = 出错或被中断）。
        report: 子 agent 的最终文本汇报（success=True 时有效）。
        error: 失败原因（success=False 时有效）。
        iterations: 子 agent 跑了多少轮工具迭代。
        tool_calls: 子 agent 调了多少次工具。
    """

    success: bool
    report: str = ""
    error: str = ""
    iterations: int = 0
    tool_calls: int = 0


async def run_subagent(
    agent_def: AgentDefinition,
    prompt: str,
    *,
    provider: Any,
    workdir: str,
    permission_mode: PermissionMode = PermissionMode.YOLO,
    parent_ui: Any = None,
) -> SubagentResult:
    """执行一个子 agent。

    创建独立的 QueryLoop + 受限 ToolRegistry + 独立 messages，跑到子 agent
    不再调工具为止，返回最终文本。

    Args:
        agent_def: 子 agent 定义（含 system_prompt / allowed_tools / model）。
        prompt: 主 agent 交给子 agent的任务描述。
        provider: LLM provider（复用主 agent 的，共享 API key/连接池）。
        workdir: 工作目录（继承主 agent 的）。
        permission_mode: 权限模式（默认 YOLO，子 agent 自主操作不需逐个确认）。
        parent_ui: 主 agent 的 UI 对象（用于打印子 agent 的进度提示）。None 则静默。

    Returns:
        SubagentResult: 子 agent 的执行结果。
    """
    if parent_ui is not None:
        parent_ui.info(f"  → 派生 {agent_def.agent_type} 子代理执行任务...")

    # 1. 构建子 agent 的工具集 + 权限校验器 + 编排器
    sub_registry = _build_sub_registry(agent_def.allowed_tools)
    sub_checker = PermissionChecker(rules=RuleSet(), mode=permission_mode)
    sub_orchestrator = ToolOrchestrator(registry=sub_registry, permission_checker=sub_checker)

    # 2. 组装 system prompt
    sub_system = _build_sub_system_prompt(agent_def, workdir)

    # 3. 构建 QueryLoop（独立的迭代限制）
    model = agent_def.model or provider.default_model
    sub_loop = QueryLoop(
        provider=provider,
        registry=sub_registry,
        orchestrator=sub_orchestrator,
        system=sub_system,
        model=model,
        max_iterations=agent_def.max_iterations,
        max_tokens=4096,
        enable_compaction=True,  # 子 agent 也支持上下文压缩，防跑飞爆 token
    )

    # 4. 独立的 messages 列表 + ToolContext
    sub_messages: list[Message] = []
    sub_ctx = ToolContext(
        workdir=workdir,
        messages=sub_messages,
        permission_mode=permission_mode.value,
        ui=parent_ui,  # 复用主 UI 打印子 agent 的工具调用提示
    )

    # 5. 跑！
    try:
        stats = await sub_loop.run(prompt, sub_ctx)
    except Exception as e:
        return SubagentResult(
            success=False,
            error=f"{type(e).__name__}: {e}",
        )

    # 6. 提取子 agent 的最终文本回复（最后一条 assistant 消息）
    report = ""
    for msg in reversed(sub_messages):
        if msg.role == "assistant":
            report = msg.get_text()
            if report.strip():
                break

    if parent_ui is not None:
        parent_ui.info(
            f"  ← {agent_def.agent_type} 子代理完成"
            f"（{stats.iterations} 轮，{stats.tool_calls} 次工具调用）"
        )

    return SubagentResult(
        success=True,
        report=report.strip() or "(子代理未输出文本)",
        iterations=stats.iterations,
        tool_calls=stats.tool_calls,
    )


async def run_subagents_parallel(
    tasks: list[tuple[AgentDefinition, str]],
    *,
    provider: Any,
    workdir: str,
    permission_mode: PermissionMode = PermissionMode.YOLO,
    parent_ui: Any = None,
) -> list[SubagentResult]:
    """并行执行多个子 agent。

    主 agent 同时调多个 SubagentTool 时，orchestrator 会并行执行它们，
    每个工具调用各自触发一次 run_subagent。这个函数用于手动批量派生场景。

    Args:
        tasks: [(agent_def, prompt), ...] 列表。
        其余参数同 run_subagent。

    Returns:
        与 tasks 等长的结果列表，顺序对应。
    """
    import asyncio

    coros = [
        run_subagent(
            agent_def, prompt,
            provider=provider, workdir=workdir,
            permission_mode=permission_mode, parent_ui=parent_ui,
        )
        for agent_def, prompt in tasks
    ]
    return await asyncio.gather(*coros)
