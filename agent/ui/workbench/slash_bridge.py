"""斜杠命令桥 —— 桌面 slash.exec 透传到终端命令路由。

背景：终端 J.A.R.V.I.S 有约 45 个斜杠命令（agent/commands/router.py），而桌面壳
聊天输入过去的 "/" 前缀只会当普通文本发给 LLM。本模块把命令文本经引擎指令队列
（cmd="slash_exec"）转进来，在引擎线程内组装终端同款的 CommandContext，复用
``dispatch_command`` 执行，捕获输出后以 slash_result 事件回推桌面渲染 ——
「一条协议指令带出一批终端能力」。

设计要点（2026-10）：

- **输出捕获**：命令处理器全部打在 RichCLI 形接口上（info/warn/error/_console，
  表格/面板经 ``ui._console.print`` 输出）。``SlashCaptureUI`` 用 StringIO 背书的
  Rich Console 复刻这些方法，执行结束把捕获全文经 slash_result 事件回推。
- **交互禁令**：桌面没有终端 stdin，且 ``pick_from_list`` 一类交互原语直接读
  stdin —— serve 模式下 stdin 是协议管道，一旦被读会抢坏指令通道。执行前临时
  把 terminal_picker 的三个交互函数（含已被 handlers 模块级 import 进各自命名
  空间的引用）替换为抛 ``SlashInteractiveError``，SlashCaptureUI 的 ask_user /
  read_user_input_async 同样抛。任何命令试图交互都会干净失败，不挂起不抢管道。
- **白名单**：只放行纯展示/自包含动作命令（context/compact/cost/diff/doctor/
  tools/mcp/skills/memory/plugin 等）。桌面已有原生控件的命令（mode/think/
  model/sessions/rewind/talk/voice…）不透传，避免双入口口径漂移；引擎无多
  Agent 团队运行时，/agents /tasks 也不放行。动态技能（/<skill-name> 命中
  已安装技能）额外放行，与手机/微信通道共用 query 锁串行跑一轮对话，
  可被 reply.abort 中止。
- **不膨胀引擎主体**：engine.py 体量已超 code-structure 红线，本功能只在
  engine._command_loop 加一条路由（2 行），上下文组装/白名单/捕获/回推全部
  收在本文件（单一职责，也便于单测直接驱动 run_slash）。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from typing import Any

from rich.console import Console

# 注：agent.commands.router 会拉起全部 handlers（进而 import agent.ui.cli），
# 放模块顶层 import 有循环依赖风险（engine 导入本模块时 ui 包尚未初始化完），
# 故 CommandContext / dispatch_command 均在各函数内惰性导入。

# 白名单：token（命令首个词，小写）在此集合才透传执行。/c 是 /cost 缩写别名。
# 维护口径：新增条目须确认处理器不依赖终端交互（stdin picker/input/ask_user）。
ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "/context",   # 上下文窗口占用
    "/compact",   # 手动压缩对话
    "/cost",      # token 用量统计
    "/c",         # /cost 缩写
    "/diff",      # 工作区改动预览（shadow git）
    "/doctor",    # 环境自检
    "/tools",     # 已注册工具清单
    "/mcp",       # MCP 服务器状态
    "/skills",    # 已安装技能列表
    "/memory",    # 画像记忆查看/维护（子命令自带 yes 二次确认，无终端交互）
    "/plugin",    # 插件管理（search/info/install 等均非交互）
    "/plugins",
})


class SlashInteractiveError(RuntimeError):
    """命令试图使用终端交互（picker/input/ask_user）时抛出，由 run_slash 转成友好报错。"""


class _NoInteractive:
    """ui.terminal_picker 属性替身：访问任何成员都立即抛交互禁令。"""

    def __getattr__(self, name: str) -> Any:
        raise SlashInteractiveError(f"桌面不支持终端交互选择器（terminal_picker.{name}）")


class SlashCaptureUI:
    """RichCLI 形输出捕获替身 —— 只实现命令处理器用到的输出/交互接口。

    info/warn/error/_console 输出进同一个 StringIO-backed Rich Console
    （markup 照常解析、no_color 去样式，得到渲染后的纯文本），
    ask_user / read_user_input_async / terminal_picker 一律抛
    SlashInteractiveError（交互禁令）。

    @author aceFelix
    """

    def __init__(self) -> None:
        self._buf = io.StringIO()
        # 宽度固定 100：脱离终端环境，Rich 无法探测列宽；表格/面板按此折行
        self._console = Console(
            file=self._buf, width=100, no_color=True, force_terminal=False,
            legacy_windows=False,
        )
        self.terminal_picker = _NoInteractive()

    # ---- RichCLI 输出接口 ----

    def info(self, text: str) -> None:
        self._console.print(text)

    def warn(self, text: str) -> None:
        self._console.print(f"⚠️  {text}")

    def error(self, text: str) -> None:
        self._console.print(f"❌ {text}")

    # ---- 交互接口：一律禁止（桌面没有 stdin） ----

    def ask_user(self, prompt: str) -> str:
        raise SlashInteractiveError("该命令需要终端询问交互")

    async def read_user_input_async(self, prompt: str = "") -> str:
        raise SlashInteractiveError("该命令需要终端输入交互")

    def get_output(self) -> str:
        """取捕获全文（去首尾空白），供 slash_result 事件回推。"""
        return self._buf.getvalue().strip()


@contextlib.contextmanager
def _pickers_blocked():
    """执行期间把 terminal_picker 交互原语替换为抛错函数。

    handlers 有两种引用方式：函数内 ``from ... import pick_from_list``（调用时
    重新取模块属性，patch 模块即生效）与模块级 import（引用已烧进各自命名空间，
    需逐模块替换）。这里两种都处理：先 patch 源模块，再扫描 agent.commands.
    handlers 包的全部子模块，把指向同一函数对象的属性一并换掉。

    @author aceFelix
    """
    import importlib
    import pkgutil

    from agent.commands import handlers as handlers_pkg
    from agent.ui import terminal_picker as tp

    names = ("pick_from_list", "pick_from_grouped_list", "form_input")

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise SlashInteractiveError("该命令需要终端选择交互")

    originals: list[tuple[Any, str, Any]] = []
    # 源模块本体
    for n in names:
        originals.append((tp, n, getattr(tp, n)))
        setattr(tp, n, _blocked)
    # handlers 子模块里的模块级 import 引用
    for mod_info in pkgutil.iter_modules(handlers_pkg.__path__):
        mod = importlib.import_module(f"{handlers_pkg.__name__}.{mod_info.name}")
        for n in names:
            attr = getattr(mod, n, None)
            if attr is not None and any(attr is o for _, _, o in originals[:3]):
                originals.append((mod, n, attr))
                setattr(mod, n, _blocked)
    try:
        yield
    finally:
        for obj, n, orig in reversed(originals):
            setattr(obj, n, orig)


def _skill_exists(settings: Any, token: str) -> bool:
    """token（含斜杠）是否命中已安装技能名（动态 /<skill-name> 放行依据）。"""
    try:
        from agent.core.extensions.skills import load_skills

        name = token.lstrip("/").lower()
        return any(s.name.lower() == name for s in load_skills(settings.workdir))
    except Exception:
        return False


# 桌面原生控件对应的命令（只补全不透传，回车后由壳原生链路处理/提示）：
# 与白名单排除口径一致，补全列表里标 source="native" 区分。
_NATIVE_COMMANDS: tuple[str, ...] = (
    "/mode", "/think", "/model", "/models", "/sessions", "/reset",
)


def build_desktop_commands(settings: Any) -> list[dict[str, str]]:
    """桌面输入框 / 命令补全目录（slash.commands 指令的数据源）。

    口径与 run_slash 的执行护栏严格对齐，只收录桌面上能执行的命令：

    - source="passthrough"：白名单透传命令（slash.exec 执行）；
    - source="native"：桌面已有原生控件的命令（输入后回车会提示改用控件）；
    - source="skill"：已安装技能（/<skill-name> 动态放行）。

    描述复用终端 SLASH_COMMANDS 注册表（取命令首词对应的描述）；终端独有
    且桌面不可执行的命令（/exit /init /voice …）不进目录，避免补全出
    一条必被拒绝的命令。惰性导入 agent.ui.cli（同 dispatch_command 口径，
    防循环依赖）。技能目录随 workdir 变化，每次请求现算。

    @author aceFelix
    """
    from agent.ui.cli import SLASH_COMMANDS

    # 命令首词 → 描述（/mode <m> 这类带参条目取首词登记）
    desc_map: dict[str, str] = {}
    for name, desc in SLASH_COMMANDS:
        token = name.split(None, 1)[0].lower()
        desc_map.setdefault(token, desc)

    items: list[dict[str, str]] = []
    for token in sorted(ALLOWED_COMMANDS):
        items.append({"name": token, "description": desc_map.get(token, ""), "source": "passthrough"})
    for token in _NATIVE_COMMANDS:
        items.append({"name": token, "description": desc_map.get(token, ""), "source": "native"})
    try:
        from agent.core.extensions.skills import load_skills

        for skill in load_skills(settings.workdir):
            cmd = f"/{skill.name}"
            if cmd.lower() in desc_map:
                continue  # 与内置命令重名的技能不重复列出（执行时内置命令优先）
            items.append({"name": cmd, "description": skill.description or "技能包", "source": "skill"})
    except Exception:
        pass  # 技能目录加载失败不影响主列表（与 _skill_exists 同口径宽容）
    return items


def _build_command_context(engine: Any, ui: SlashCaptureUI) -> CommandContext:
    """从引擎运行态组装终端同款 CommandContext（字段对照 main.repl 装配处）。

    checker/recovery/orchestrator 白名单命令基本不碰，按引擎 _handle_set_mode
    的口径现建；team_mgr/task_list 引擎无多 Agent 运行时，置 None（/agents
    /tasks 已被挡在白名单外）。

    @author aceFelix
    """
    from agent.bootstrap import _build_checker, _build_recovery_executor
    from agent.commands.router import CommandContext
    from agent.core.orchestrator import ToolOrchestrator

    s = engine._settings
    loop = engine._query_loop
    checker = _build_checker(s)
    recovery = _build_recovery_executor(s)
    # 优先复用运行中 loop 的 orchestrator（权限模式与终端切换保持同态）
    orchestrator = getattr(loop, "_orchestrator", None) or ToolOrchestrator(
        registry=engine._active_registry,
        permission_checker=checker,
        recovery_executor=recovery,
    )
    return CommandContext(
        ui=ui,
        settings=s,
        provider=getattr(engine, "_provider", None),
        registry=engine._active_registry,
        checker=checker,
        recovery=recovery,
        orchestrator=orchestrator,
        loop=loop,
        ctx=engine._ctx,
        model=engine._model,
        messages=engine._messages,
        dialog_count=engine._dialog_count,
        team_mgr=None,
        task_list=None,
        mcp_client=getattr(engine, "_mcp_client", None),
        session_name=engine._session_name,
        title_generated=engine._title_generated,
        system_prompt=getattr(loop, "_system", "") or "",
    )


async def run_slash(engine: Any, text: str) -> None:
    """引擎线程内执行一条斜杠命令并把结果以 slash_result 事件回推桌面。

    流程：形态校验 → 会话就绪校验 → 白名单/技能匹配 → 组装上下文 →
    交互禁令下 dispatch_command → 捕获输出回推。技能命令额外持 query 锁
    并挂 _send_task（与 _handle_send 同构，串行化 + 可被停止）。

    @author aceFelix
    """
    from agent.commands.router import dispatch_command

    text = (text or "").strip()
    emitter = engine._emitter

    def _result(ok: bool, out: str) -> None:
        emitter.emit("slash_result", {"command": text, "ok": ok, "text": out})

    if not text.startswith("/"):
        _result(False, "不是斜杠命令")
        return
    if not getattr(engine, "_session_ready", False):
        _result(False, "会话尚未初始化：先随便发一条消息初始化引擎，再试斜杠命令")
        return

    token = text.split(None, 1)[0].lower()
    skill_mode = False
    if token not in ALLOWED_COMMANDS:
        # 白名单外唯一放行的动态入口：/<skill-name> 命中已安装技能
        skill_mode = _skill_exists(engine._settings, token)
        if not skill_mode:
            _result(
                False,
                f"{token} 暂不支持在桌面执行（可在终端 jarvis 中使用）。"
                f"桌面已放行：{' '.join(sorted(ALLOWED_COMMANDS))} 及已安装技能",
            )
            return

    ui = SlashCaptureUI()
    cmd_ctx = _build_command_context(engine, ui)
    ok = True
    try:
        with _pickers_blocked():
            if skill_mode:
                # 技能命令 = 跑一轮对话：与手机/微信/桌面消息轮共用 query 锁
                # 串行；挂上 _send_task 让桌面「停止」能取消（query_loop 的
                # finally 必发 assistant_done，前端闸门照常收尾）。
                prev_task = getattr(engine, "_send_task", None)
                engine._send_task = asyncio.current_task()
                await asyncio.get_running_loop().run_in_executor(
                    None, engine._query_lock.acquire
                )
                try:
                    handled = await dispatch_command(cmd_ctx, text)
                finally:
                    engine._query_lock.release()
                    engine._send_task = prev_task
                    # 技能轮也产生消息：与 _handle_send 同规则落盘会话
                    engine._after_turn()
            else:
                handled = await dispatch_command(cmd_ctx, text)
        if not handled:
            ui.error(f"未知命令：{token}（输入 /help 可看终端全部命令）")
            ok = False
    except SlashInteractiveError as e:
        ok = False
        ui.error(f"该命令需要终端交互，请在终端 jarvis 中使用（{e}）")
    except asyncio.CancelledError:
        # 停止指令取消本协程：不再回推结果（对话流事件已由 query_loop 收尾），
        # 交回引擎指令循环继续服务
        raise
    except Exception as e:
        ok = False
        ui.error(f"命令执行失败: {type(e).__name__}: {e}")
    output = ui.get_output()
    _result(ok, output or ("(无输出)" if ok else "命令执行失败"))
