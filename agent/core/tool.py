"""Tool 协议与工具注册中心。

这是整个系统的心脏；
核心设计思想（非常重要）：
1. 工具是"带元数据 + 权限 + 执行 + 渲染"的对象，不是函数。
2. fail-closed 原则：所有安全属性默认为"危险"侧，工具必须显式声明自己安全。
   - is_read_only 默认 False（默认假设会写）
   - is_concurrency_safe 默认 False（默认假设不能并行）
   - is_destructive 默认 False
   - check_permissions 默认返回 ASK
3. build_tool() 提供这些默认值，等价于原项目 buildTool()。
4. BaseTool 基类提供便利实现；轻量工具可继承它，复杂工具可单独实现 Tool 协议。
"""

from __future__ import annotations

import abc
import fnmatch
import functools
import re
from dataclasses import dataclass, field
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult

# 工具入参用 JSON Schema 描述，给 LLM 看（对应原 inputSchema/inputJSONSchema）
JSONSchema = dict[str, Any]


class Tool(abc.ABC):
    """工具协议。

    子类必须实现: name, description, input_schema, call()
    推荐重写: check_permissions（默认 ASK 最安全，但会让用户频繁确认）
    可选重写: is_read_only / is_destructive / is_concurrency_safe / validate_input
    """

    # ---- 必填元数据 ----
    name: str = ""
    description: str = ""
    input_schema: JSONSchema = {}

    # 工具结果超过此字符数时落盘，只把预览回传给 LLM。
    # 对应原 maxResultSizeChars。设为 float('inf') 表示永不落盘。
    max_result_chars: int = 20_000

    # 是否延迟加载。True = 不随每次请求发送完整 schema，
    # 需通过 ToolSearchTool 发现后才能调用。参考 Claude Code deferred tool loading。
    deferred: bool = False

    @abc.abstractmethod
    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行工具。必须返回 ToolResult，不可直接抛业务错误（用 ToolResult.error）。"""
        ...

    # ---- 安全属性（fail-closed 默认值）----
    def is_read_only(self, args: dict[str, Any]) -> bool:
        """是否只读操作。默认 False（假设会修改状态）。"""
        return False

    def is_destructive(self, args: dict[str, Any]) -> bool:
        """是否不可逆（删除/覆盖/发送）。默认 False。"""
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        """是否能与其他工具并行执行。默认 False（保守）。"""
        return False

    # ---- 权限与校验 ----
    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        """输入合法性校验（在权限校验之前）。默认放行。"""
        return ValidationResult.pass_()

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """工具特定的权限逻辑。默认返回 ASK（最安全）。

        通用权限规则（allow/deny/ask）在 permissions/checker.py 统一处理，
        这里只放工具自己的特例。例如 BashTool 解析命令前缀做白名单匹配。
        """
        return PermissionResult.ask("no tool-specific permission rule")

    # ---- UI 钩子（可选）----
    def user_facing_name(self, args: dict[str, Any] | None = None) -> str:
        """展示给用户的名字。默认用工具名。"""
        return self.name

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        """进行时活动描述（spinner 展示用）。返回 None 则回退到工具名。"""
        return None

    # ---- 权限规则匹配辅助（供 BashTool 等）----
    def prepare_permission_matcher(
        self, args: dict[str, Any]
    ) -> "PermissionMatcher | None":
        """准备权限规则匹配器。对应原 preparePermissionMatcher。

        例如 BashTool 解析出命令前缀（"git"、"npm"），匹配器据此判断
        规则 "Bash(git *)" 是否命中。无规则匹配需求的工具返回 None。
        """
        return None


@dataclass
class PermissionMatcher:
    """权限规则匹配器。

    用法: matcher.matches(pattern) -> bool
    pattern 形如 "Bash(git *)" / "Read(src/**)" / "Write(~/.ssh/*)"
    """

    tool_name: str
    # 解析后的工具入参中的"目标字符串"（命令、路径等），用于和 pattern 中的通配部分匹配
    targets: list[str] = field(default_factory=list)

    def matches(self, pattern: str) -> bool:
        """判断本工具调用是否匹配给定的权限规则 pattern。

        pattern 格式: ToolName(spec)
        - ToolName 必须等于 self.tool_name
        - spec 支持 fnmatch 通配符，与 targets 任一匹配即命中
        - 无 spec 的 pattern（如 "Bash"）匹配该工具的任意调用
        """
        m = re.match(r"^(\w+)(?:\((.*)\))?$", pattern.strip())
        if not m:
            return False
        rule_tool, rule_spec = m.group(1), m.group(2)
        if rule_tool != self.tool_name:
            return False
        if rule_spec is None or rule_spec == "":
            # 无约束的规则，匹配该工具任意调用
            return True
        for target in self.targets:
            if fnmatch.fnmatchcase(target, rule_spec):
                return True
        return False


# ---------------------------------------------------------------------------
# 工具注册中心
# ---------------------------------------------------------------------------


class ToolRegistry:
    """工具注册中心。管理工具的注册、查找、去重。

    对应原项目 tools.ts 的 getTools/assembleToolPool。这里简化为：
    - register(tool) 注册
    - get(name) 按名查找（含别名）
    - all() 返回全部
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._aliases: dict[str, str] = {}  # alias -> canonical name

    def register(self, tool: Tool, *, aliases: list[str] | None = None) -> None:
        if not tool.name:
            raise ValueError(f"Tool {tool!r} has empty name")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        for alias in aliases or []:
            self._aliases[alias] = tool.name

    def get(self, name: str) -> Tool | None:
        """按名或别名查找工具。"""
        if name in self._tools:
            return self._tools[name]
        canonical = self._aliases.get(name)
        if canonical:
            return self._tools.get(canonical)
        return None

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def all_core(self) -> list[Tool]:
        """始终携带的核心工具（deferred=False）。"""
        return [t for t in self._tools.values() if not t.deferred]

    def all_deferred(self) -> list[Tool]:
        """延迟加载的工具池（deferred=True）。"""
        return [t for t in self._tools.values() if t.deferred]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools or name in self._aliases


def build_default_registry() -> ToolRegistry:
    """构造默认工具集。延迟 import 避免循环依赖。

    P-03 改进：结果通过 @lru_cache 缓存，多次调用复用同一 Registry。
    工具内部不依赖 ToolRegistry，Registry 依赖工具，所以这里做装配点。
    """
    return _build_default_registry_impl()


@functools.lru_cache(maxsize=1)
def _build_default_registry_impl() -> ToolRegistry:
    """build_default_registry 的实际实现（缓存）。"""
    # 延迟 import: 工具模块 import 了 core 层，core 层不能反向 import 工具
    from agent.tools.ask_user import AskUserTool
    from agent.tools.bash import BashTool
    from agent.tools.file_ops.file_edit import FileEditTool
    from agent.tools.file_ops.file_read import FileReadTool
    from agent.tools.file_ops.file_write import FileWriteTool
    from agent.tools.file_ops.glob import GlobTool
    from agent.tools.file_ops.grep import GrepTool
    from agent.tools.todo import TodoWriteTool
    from agent.tools.location import LocationTool
    from agent.tools.extensions.dev_server_tool import DevServerTool
    from agent.tools.extensions.email_tool import SendEmailTool
    from agent.tools.web.web import WebFetchTool, WebSearchTool

    registry = ToolRegistry()
    registry.register(BashTool())
    registry.register(FileReadTool())
    registry.register(FileEditTool())
    registry.register(FileWriteTool())
    registry.register(GlobTool())
    registry.register(GrepTool())
    registry.register(LocationTool())
    registry.register(TodoWriteTool())
    registry.register(AskUserTool())
    registry.register(WebFetchTool())
    registry.register(WebSearchTool())
    registry.register(SendEmailTool())
    registry.register(DevServerTool())

    # 阶段二: GUI 工具（可选，依赖 pyautogui/pygetwindow，缺失则跳过）
    _register_gui_tools(registry)

    # 阶段二: 浏览器工具（可选，依赖 playwright，缺失则跳过）
    _register_browser_tools(registry)

    # 摄像头工具（可选，依赖 opencv-python，缺失则跳过）
    _register_camera_tools(registry)

    return registry


def register_dynamic_tools(
    registry: ToolRegistry,
    mcp_client: "Any | None" = None,
    workdir: "Path | str | None" = None,
) -> int:
    """注册动态加载的工具（CLI-Anything harness、MCP 等）。返回注册数。

    在 build_default_registry 之后调用，用于注入运行时发现的工具。

    Args:
        registry: ToolRegistry 实例。
        mcp_client: 已连接的 MCPClient 实例（None 则跳过 MCP 工具）。
        workdir: 当前工作目录，用于加载项目级 harness。
    """
    count = 0

    # 1. CLI-Anything harness（~/.jarvis/cli_anything/ 与 <workdir>/.jarvis/cli_anything/）
    try:
        from agent.cli_anything import discover_and_register
        count += discover_and_register(registry, workdir=workdir)
    except ImportError:
        pass

    # 2. MCP 工具
    if mcp_client is not None:
        try:
            from agent.tools.extensions.mcp_tool import register_mcp_tools
            count += register_mcp_tools(registry, mcp_client)
        except ImportError:
            pass
    return count


def register_subagent_tool(
    registry: ToolRegistry,
    provider: "Any | None" = None,
    permission_mode: "Any | None" = None,
    team_mgr: "Any | None" = None,
    task_list: "Any | None" = None,
) -> bool:
    """注册子代理协作工具（Agent Tool）。返回是否注册成功。

    Args:
        registry: 工具注册表。
        provider: LLM provider（主 agent 的）。子代理复用此连接。
        permission_mode: 权限模式（PermissionMode 枚举）。
        team_mgr: 全局 TeamManager（供背景队友模式使用）。
        task_list: 全局 TaskList（供背景队友模式使用）。

    Returns: True 注册成功，False 已存在或导入失败。
    """
    try:
        from agent.tools.collaboration.subagent_tool import SubagentTool
        from agent.permissions.modes import PermissionMode

        if "Agent" in registry:
            return False  # 已注册

        mode = permission_mode or PermissionMode.YOLO
        subagent = SubagentTool(
            provider=provider,
            permission_mode=mode,
            team_mgr=team_mgr,
            task_list=task_list,
        )
        subagent.deferred = True  # 子代理工具延迟加载，通过 ToolSearch 按需发现
        registry.register(subagent)
        # 保留旧名作为别名（只加 alias，不加 _tools 避免重复 tool 对象）
        if "Subagent" not in registry:
            registry._aliases["Subagent"] = "Agent"
        return True
    except ImportError:
        return False


def register_team_tools(
    registry: ToolRegistry,
    task_list: "Any | None" = None,
    team_mgr: "Any | None" = None,
) -> int:
    """注册多 Agent 团队协作工具。返回注册数。

    Phase 1 新增: TeamCreate, TeamDelete, SendMessage,
                 TaskCreate, TaskGet, TaskList, TaskUpdate, TaskStop

    Args:
        registry: 工具注册表。
        task_list: 共享 TaskList（需在装配点注入）。
        team_mgr: 全局 TeamManager（需在装配点注入）。

    Returns: 已注册的工具数。
    """
    count = 0

    # Team 工具
    try:
        from agent.tools.collaboration.team_create import TeamCreateTool
        from agent.tools.collaboration.team_delete import TeamDeleteTool
    except ImportError:
        pass
    else:
        t1 = TeamCreateTool(team_mgr=team_mgr)
        t1.deferred = True  # 团队协作工具延迟加载
        t2 = TeamDeleteTool(team_mgr=team_mgr)
        t2.deferred = True
        registry.register(t1)
        registry.register(t2)
        count += 2

    # SendMessage 工具
    try:
        from agent.tools.collaboration.send_message import SendMessageTool
    except ImportError:
        pass
    else:
        t = SendMessageTool(team_mgr=team_mgr)
        t.deferred = True  # 团队协作工具延迟加载
        registry.register(t)
        count += 1

    # Task 工具（需要 task_list）
    if task_list is not None:
        try:
            from agent.tools.collaboration.task_create import TaskCreateTool
            from agent.tools.collaboration.task_get import TaskGetTool
            from agent.tools.collaboration.task_list import TaskListTool
            from agent.tools.collaboration.task_update import TaskUpdateTool
            from agent.tools.collaboration.task_stop import TaskStopTool
        except ImportError:
            pass
        else:
            t1 = TaskCreateTool(task_list=task_list)
            t1.deferred = True  # 任务管理工具延迟加载
            t2 = TaskGetTool(task_list=task_list)
            t2.deferred = True
            t3 = TaskListTool(task_list=task_list)
            t3.deferred = True
            t4 = TaskUpdateTool(task_list=task_list)
            t4.deferred = True
            t5 = TaskStopTool()
            t5.deferred = True
            registry.register(t1)
            registry.register(t2)
            registry.register(t3)
            registry.register(t4)
            registry.register(t5)
            count += 5

    # TeamStatus 工具（需要 task_list 提供任务统计）
    try:
        from agent.tools.collaboration.team_status import TeamStatusTool
    except ImportError:
        pass
    else:
        t = TeamStatusTool(team_mgr=team_mgr, task_list=task_list)
        t.deferred = True  # 团队协作工具延迟加载
        registry.register(t)
        count += 1

    return count


def register_plan_tools(registry: ToolRegistry) -> int:
    """注册 Plan Mode 工具（Phase 3）。返回注册数。"""
    count = 0
    try:
        from agent.tools.collaboration.enter_plan import EnterPlanModeTool
        from agent.tools.collaboration.exit_plan import ExitPlanModeTool
    except ImportError:
        pass
    else:
        t1 = EnterPlanModeTool()
        t1.deferred = True
        t2 = ExitPlanModeTool()
        t2.deferred = True
        registry.register(t1)
        registry.register(t2)
        count += 2
    return count


def register_lsp_tool(registry: ToolRegistry) -> int:
    """注册 LSP 代码智能工具。返回注册数（0=未配置 LSP server）。"""
    try:
        from agent.lsp.manager import get_lsp_manager
    except ImportError:
        return 0

    mgr = get_lsp_manager()
    if not mgr or not mgr._configs:
        return 0  # LSP 未配置，静默跳过

    try:
        from agent.tools.extensions.lsp_tool import LSPTool
    except ImportError:
        return 0

    tool = LSPTool()
    tool.deferred = True
    registry.register(tool)
    return 1


def _register_gui_tools(registry: ToolRegistry) -> None:
    """注册 GUI 操作工具。依赖未安装时静默跳过，不影响基础工具。

    阶段二: 鼠标 / 键盘 / 屏幕 / 窗口 共 11 个工具。
    P1 增强: 新增拖拽、等待、窗口相对坐标点击、视觉定位，共 15 个工具。
    """
    try:
        from agent.tools.system.mouse import (
            MouseClickTool,
            MouseDragTool,
            MouseMoveTool,
            MouseScrollTool,
        )
        from agent.tools.system.keyboard import TypeTextTool, KeyTapTool
        from agent.tools.system.screen import GetScreenSizeTool, ScreenShotTool, WaitForTool
        from agent.tools.system.window import (
            WindowListTool,
            WindowFocusTool,
            WindowCloseTool,
            WindowMoveTool,
            WindowRectTool,
            WindowClickTool,
        )
        from agent.tools.system.gui_vision import VisualClickTool
    except ImportError:
        # pyautogui/pygetwindow 未安装，GUI 工具不可用
        return

    gui_tools = [
        MouseClickTool(), MouseDragTool(), MouseMoveTool(), MouseScrollTool(),
        TypeTextTool(), KeyTapTool(),
        GetScreenSizeTool(), ScreenShotTool(), WaitForTool(),
        WindowListTool(), WindowFocusTool(), WindowCloseTool(),
        WindowMoveTool(), WindowRectTool(), WindowClickTool(),
        VisualClickTool(),
    ]
    for t in gui_tools:
        t.deferred = True
        registry.register(t)


def _register_browser_tools(registry: ToolRegistry) -> None:
    """注册浏览器自动化工具。依赖未安装时静默跳过，不影响基础工具。

    阶段二新增: 导航 / 截图 / 点击 / 输入 / 取文本 / 关闭 共 6 个工具。
    依赖: pip install playwright && playwright install chromium
    """
    try:
        from agent.tools.web.browser import (
            BrowserClickTool,
            BrowserCloseTool,
            BrowserGetTextTool,
            BrowserNavigateTool,
            BrowserScreenshotTool,
            BrowserTypeTool,
        )
    except ImportError:
        # playwright 未安装，浏览器工具不可用
        return

    browser_tools = [
        BrowserNavigateTool(), BrowserScreenshotTool(), BrowserClickTool(),
        BrowserTypeTool(), BrowserGetTextTool(), BrowserCloseTool(),
    ]
    for t in browser_tools:
        t.deferred = True
        registry.register(t)


def _register_camera_tools(registry: ToolRegistry) -> None:
    """注册摄像头工具。依赖未安装时静默跳过，不影响基础工具。

    阶段五扩展新增: 摄像头拍照 / 列出摄像头 共 2 个工具。
    依赖: pip install opencv-python
    """
    try:
        from agent.tools.vision.camera import CameraShotTool, ListCamerasTool
    except ImportError:
        # opencv-python 未安装，摄像头工具不可用
        return

    # 进一步验证 cv2 真能导入（有时包装了但导入失败）
    try:
        import cv2  # noqa: F401
    except ImportError:
        return

    cam_tools = [CameraShotTool(), ListCamerasTool()]
    for t in cam_tools:
        t.deferred = True
        registry.register(t)
