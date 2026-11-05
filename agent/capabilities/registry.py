"""能力登记表 —— jarvis「我能不能做到」的事实来源。

为什么需要这一层（2026-11-03 实机复盘，计划见
docs/plans/capability-selfcheck.md）：一次"写个简易贪吃蛇"的任务花了约 40 次工具调用，
根因是系统对能力的认知**只到"包能不能 import"这一层**。playwright 包装得好好的，
`BrowserNavigate` 因此照常注册、照常出现在 ToolSearch 结果里，而真正缺的是浏览器内核
（revision 1223）。模型不知道，于是撞上去、自动重试、再撞；最后用"假设"填空，
向用户汇报了"刚才我验证过"。

本模块只负责**声明**：一个能力由哪些前置条件构成、缺了能不能降级、允许哪些自我修复动作、
关联哪些工具。探测在 probe.py，修复在 repair.py（S4）。三者分开是为了让"读"这条链
（S1-S3 全只读）可以先落地并被验收，再放"写"（S4）进来。

形态沿用仓库已验证的表驱动范式（`agent/llm/provider_registry.py`）：新增能力只加一行，
不在调用点写散落的 try/except。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 状态常量（用字符串而不是 Enum：要直接进 JSON 账本与 LLM 文案，省一层序列化）
# ---------------------------------------------------------------------------


class ReqState:
    """单条前置条件的探测结论。

    `PROBE_FAILED` 与 `MISSING` **必须分开**：前者是"我没看清"，后者才是"确实没有"。
    把探针故障当成缺件，系统就会编出一个不存在的根因——2026-11-03 那次沙箱里
    `tasklist` 返回空输出、jarvis 据此断定"进程被回收"并讲给用户听，就是这个坑。
    """

    OK = "ok"                        # 条件满足，有实测证据
    MISSING = "missing"              # 确认不存在（包/二进制/资产真的没有）
    PROBE_FAILED = "probe_failed"    # 探测手段自身失败（超时/无权限/解析不了），不得判缺失
    SKIPPED = "skipped"              # 本阶段不探测，显式承认不知道
    UNSUPPORTED = "unsupported"      # 平台不支持（区别于"没装"）


class CapState:
    """能力聚合状态。"""

    OK = "ok"
    DEGRADED = "degraded"            # 可选条件缺失，能力可用但功能面变小
    MISSING = "missing"              # 必需条件缺失，能力不可用
    PROBE_FAILED = "probe_failed"    # 有探针挂了 ⇒ 整体结论不可信，**不得当 missing 用**
    UNSUPPORTED = "unsupported"      # 当前平台不适用
    UNKNOWN = "unknown"              # 表里没有这个能力：消费者必须按不可靠处理


# ---------------------------------------------------------------------------
# 前置条件声明
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PythonReq:
    """一个 Python 包依赖。

    Attributes:
        distribution: PyPI 包名，用于 `importlib.metadata.version()` 与 extras 对应。
        import_name: 实际 import 的顶层名；与包名不同时才填（如 `pillow` → `PIL`）。
        version_spec: 版本约束，仅用于展示与"版本漂移"提示，不做硬判定。
        optional: True 表示缺了只让能力降级而不是不可用。
    """

    distribution: str
    import_name: str = ""
    version_spec: str = ""
    optional: bool = False

    @property
    def probe_name(self) -> str:
        """import 探测用的顶层名（默认同包名）。"""
        return self.import_name or self.distribution

    @property
    def label(self) -> str:
        return f"{self.distribution}{self.version_spec}" if self.version_spec else self.distribution


@dataclass(frozen=True)
class BinaryReq:
    """PATH 上的外部可执行文件（git、ffmpeg 之类，pip 管不到）。"""

    name: str
    args: tuple[str, ...] = ("--version",)
    expect_pattern: str = ""      # 输出里必须出现的串（判"是不是它"，防撞到同名怪东西）
    optional: bool = False
    note: str = ""


@dataclass(frozen=True)
class AssetReq:
    """数据资产：包已装、但要另下一次的东西（playwright 内核、OCR 权重）。

    kind 决定用哪个探针（见 probe.py 的 `_ASSET_PROBES`）：
    - ``playwright-browsers``：问 playwright 自己要多高的 revision，再查 ms-playwright 目录。
      **绝不硬编码 revision 号**，否则库一升级本表就变成假报警源。
    - ``dir-exists`` / ``file-exists``：按 path 模板展开后查存在性。
    """

    label: str
    kind: str
    path: str = ""
    optional: bool = False
    note: str = ""


@dataclass(frozen=True)
class SecretReq:
    """凭据类前置（环境变量或 keyring 条目）。

    静默回退是本类最容易埋祸的地方：语音密钥解析链里"回退到主 LLM key"就曾在报错文案里
    冒充成"没配 DashScope Key"。所以这里把"用哪个 key、能不能回退"写明白。
    """

    label: str
    env_var: str = ""
    keyring_service: str = ""
    fallback_allowed: bool = False   # True 时需说明回退给谁，避免"配过却报没配"
    optional: bool = False
    note: str = ""


@dataclass(frozen=True)
class PlatformReq:
    """平台适用性。`systems` 用 `sys.platform` 的取值（win32 / darwin / linux）。"""

    systems: tuple[str, ...]
    reason: str = ""
    optional: bool = False       # True 表示"该平台只失去部分功能"，不判整个能力不可用


# ---------------------------------------------------------------------------
# 能力条目
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityMeta:
    """一个能力的完整声明。

    Attributes:
        name: 能力标识，点分命名（`browser.selfcheck`）。表 key 与之一致。
        title: 给人看的一行名字。
        extras_group: 对应 `pyproject.toml` 的 extras 名；`pip-extras` 修复动作据此生成命令。
        python_reqs / binaries / assets / secrets / platforms: 前置条件清单。
        smoke_probe: 冒烟探针的名字（S3 实现，S1 只声明）。
        repair_actions: 允许的自我修复动作 ID 白名单（见计划第 4.1 节）。
        degradable: 不可用时能退到哪里；空串表示无法降级。
        blocks_selfcheck: 缺失是否让「交付自查」不成立（True 时必须进 system prompt）。
        tools: 该能力承载的工具名，供契约测要求"每个 deferred 工具都得有归属"。
        tool_prefixes: 动态工具的名前缀（如 MCP 的 `mcp__`）——这类工具运行时才
            注册、名字由外部 server 决定，没法逐个写进 tools。
    """

    name: str
    title: str
    extras_group: str = ""
    python_reqs: tuple[PythonReq, ...] = ()
    binaries: tuple[BinaryReq, ...] = ()
    assets: tuple[AssetReq, ...] = ()
    secrets: tuple[SecretReq, ...] = ()
    platforms: tuple[PlatformReq, ...] = ()
    smoke_probe: str = ""
    repair_actions: tuple[str, ...] = ()
    degradable: str = ""
    blocks_selfcheck: bool = False
    tools: tuple[str, ...] = ()
    tool_prefixes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# 探测结果（不是 frozen：带时间戳与缓存标记）
# ---------------------------------------------------------------------------


@dataclass
class RequirementStatus:
    """一条前置条件的实测结果。"""

    kind: str            # python | binary | asset | secret | platform
    label: str
    state: str           # ReqState.*
    evidence: str = ""   # 实测证据，必须可复核（版本、路径、命令输出片段）
    fix_hint: str = ""   # 人类可复制的一条命令或一句说明
    optional: bool = False


@dataclass
class CapabilityReport:
    """一个能力的体检结论。`/health caps`、ToolSearch 文案、prompt 清单都消费它。"""

    name: str
    title: str
    state: str                       # CapState.*
    requirements: list[RequirementStatus] = field(default_factory=list)
    checked_at: float = 0.0
    from_cache: bool = False
    smoke_run: bool = False          # 本次结论里是否含冒烟探针
    extras_group: str = ""
    repair_actions: tuple[str, ...] = ()
    degradable: str = ""
    blocks_selfcheck: bool = False

    @property
    def usable(self) -> bool:
        """能力是否可用（DEGRADED 算可用，因为调用方仍能得到明确边界）。"""
        return self.state in (CapState.OK, CapState.DEGRADED)

    def missing_lines(self) -> list[str]:
        """必需条件的缺失项（含修复提示），给 ToolSearch / 错误文案用。"""
        out: list[str] = []
        for r in self.requirements:
            if r.optional or r.state not in (ReqState.MISSING, ReqState.UNSUPPORTED):
                continue
            out.append(f"{r.label}：{r.evidence}；补法：{r.fix_hint}" if r.fix_hint else f"{r.label}：{r.evidence}")
        return out

    def degraded_lines(self) -> list[str]:
        """可选条件缺失（功能面变小）的说明。"""
        return [
            f"{r.label}：{r.evidence}"
            for r in self.requirements
            if r.optional and r.state in (ReqState.MISSING, ReqState.UNSUPPORTED)
        ]

    def _absent_labels(self, *, optional: bool) -> list[str]:
        """按「是否可选」取出缺失条件的名字（DEGRADED 与 MISSING 只差这一维）。"""
        return [
            r.label for r in self.requirements
            if r.optional is optional
            and r.state in (ReqState.MISSING, ReqState.UNSUPPORTED)
        ]

    def summary_line(self) -> str:
        """一行结论，给横幅与 `/health caps` 表格用。"""
        if self.state == CapState.OK:
            return "可用"
        if self.state == CapState.PROBE_FAILED:
            bad = "、".join(r.label for r in self.requirements if r.state == ReqState.PROBE_FAILED)
            return f"结论不可信（探针失败：{bad or '未指明'}）"
        if self.state == CapState.MISSING:
            miss = self._absent_labels(optional=False) or ["未指明"]
            return "不可用（缺：" + "、".join(miss) + "）"
        if self.state == CapState.DEGRADED:
            return "可用（部分功能缺：" + "、".join(self._absent_labels(optional=True)) + "）"
        if self.state == CapState.UNSUPPORTED:
            return "本平台不适用"
        return "未登记（按不可靠处理）"

    def to_dict(self) -> dict[str, Any]:
        """进 `actions.jsonl` / 诊断输出用的结构化形式。"""
        return {
            "capability": self.name,
            "state": self.state,
            "checked_at": round(self.checked_at, 3),
            "from_cache": self.from_cache,
            "smoke_run": self.smoke_run,
            "requirements": [
                {"kind": r.kind, "label": r.label, "state": r.state, "evidence": r.evidence,
                 "optional": r.optional}
                for r in self.requirements
            ],
        }


# ---------------------------------------------------------------------------
# 表本体：首批能力
#
# 声明依据全部实测过：Python 包名取自各工具文件的真实 import（screen.py 用 pyautogui+PIL、
# browser.py 用 playwright、voice/tts.py 用 dashscope+pyaudio、voice/aec.py 用
# aec_audio_processing、mcp 在 core/extensions/mcp_client.py、密钥在 config/keyring_store.py）。
# ---------------------------------------------------------------------------

_PLATFORM_ALL = (PlatformReq(("win32", "darwin", "linux")),)


CAPABILITY_REGISTRY: dict[str, CapabilityMeta] = {
    # ---- 浏览器自查：本次事故的当事能力 ----
    "browser.selfcheck": CapabilityMeta(
        name="browser.selfcheck",
        title="浏览器自动化（交付自查的『亲眼看过』）",
        extras_group="browser",
        python_reqs=(PythonReq("playwright", version_spec=">=1.40.0"),),
        assets=(
            AssetReq(
                label="playwright 浏览器内核",
                kind="playwright-browsers",
                note="包装了不等于能跑：内核是另一份下载物，且 revision 号与包版本绑定",
            ),
        ),
        smoke_probe="browser-launch",
        repair_actions=("fetch-browser", "pip-extras"),
        degradable="文本级验证（DOM 结构自检、控制台错误抓取、WindowList 标题）",
        blocks_selfcheck=True,
        tools=("BrowserNavigate", "BrowserScreenshot", "BrowserClick",
               "BrowserType", "BrowserGetText", "BrowserClose"),
    ),
    # ---- 桌面 GUI 操作：分两层，屏幕/键盘 与 窗口管理 依赖不同 ----
    "gui.input": CapabilityMeta(
        name="gui.input",
        title="鼠标键盘输入模拟",
        extras_group="gui",
        python_reqs=(PythonReq("pyautogui"), PythonReq("pillow", import_name="PIL")),
        smoke_probe="",
        repair_actions=("pip-extras",),
        degradable="只读屏幕信息不可用时改为向用户确认",
        tools=("MouseClick", "MouseDrag", "MouseMove", "MouseScroll",
               "TypeText", "KeyTap", "WaitFor", "VisualClick"),
    ),
    "gui.window": CapabilityMeta(
        name="gui.window",
        title="窗口枚举与激活",
        extras_group="gui",
        python_reqs=(
            PythonReq("pygetwindow", optional=True),
            PythonReq("pywinauto", optional=True),
            PythonReq("pyautogui"),
        ),
        platforms=(PlatformReq(("win32", "darwin"), reason="Linux 上 pygetwindow 支持有限", optional=True),),
        repair_actions=("pip-extras",),
        degradable="用 ScreenShot 看画面，不做窗口级定位",
        tools=("WindowList", "WindowFocus", "WindowClose", "WindowMove",
               "WindowRect", "WindowClick"),
    ),
    "gui.screen_capture": CapabilityMeta(
        name="gui.screen_capture",
        title="屏幕截图（视觉证据的采集端）",
        extras_group="gui",
        python_reqs=(PythonReq("pyautogui"), PythonReq("pillow", import_name="PIL")),
        # 有声明不等于截得到：远程会话、锁屏、无桌面的服务跑都会让它静默给黑图。
        # 这一项交付自查直接依赖（“亲眼看过”靠的就是截帧），所以要有冒烟。
        smoke_probe="screen-capture",
        repair_actions=("pip-extras",),
        blocks_selfcheck=True,
        tools=("ScreenShot", "GetScreenSize"),
    ),
    # ---- 语音：密钥 + 音频后端 + AEC，三层都可能单独缺 ----
    "voice.duplex": CapabilityMeta(
        name="voice.duplex",
        title="实时双工语音（/talk）",
        extras_group="voice",
        python_reqs=(
            PythonReq("dashscope"),
            PythonReq("pyaudio"),
            PythonReq("aec-audio-processing", import_name="aec_audio_processing", optional=True),
            PythonReq("numpy", optional=True),
        ),
        secrets=(SecretReq(label="语音服务密钥", env_var="DASHSCOPE_API_KEY",
                           fallback_allowed=True,
                           note="缺 key 时解析链会回退主 LLM key；报错文案必须点名是哪一家"),),
        platforms=(PlatformReq(("win32", "darwin", "linux"),
                               reason="Linux 需系统 portaudio19-dev、macOS 需 brew install portaudio"),),
        repair_actions=("pip-extras", "config-fix"),
        degradable="退回文本对话，语音整段关闭",
        tools=(),
    ),
    "voice.half": CapabilityMeta(
        name="voice.half",
        title="半双工语音对话（/voice：录音 + 识别 + 播报）",
        extras_group="voice",
        python_reqs=(PythonReq("dashscope"), PythonReq("pyaudio")),
        secrets=(SecretReq(label="语音服务密钥", env_var="DASHSCOPE_API_KEY", fallback_allowed=True),),
        platforms=(PlatformReq(("win32", "darwin", "linux"),
                               reason="Linux 需 portaudio19-dev；缺声卡输入设备时录音不可用"),),
        repair_actions=("pip-extras", "config-fix"),
        degradable="退回文字输入",
        tools=(),
    ),
    # ---- 视觉/摄像头 ----
    "camera.capture": CapabilityMeta(
        name="camera.capture",
        title="摄像头取景",
        extras_group="camera",
        python_reqs=(PythonReq("opencv-python", import_name="cv2"),
                     PythonReq("numpy"), PythonReq("pillow", import_name="PIL")),
        repair_actions=("pip-extras",),
        tools=("CameraShot", "ListCameras"),
    ),
    "vision.detect": CapabilityMeta(
        name="vision.detect",
        title="实时视觉检测（手势/人脸）",
        extras_group="vision",
        python_reqs=(PythonReq("mediapipe", optional=True),
                     PythonReq("opencv-python", import_name="cv2", optional=True),
                     PythonReq("paddleocr", optional=True)),
        assets=(AssetReq(label="OCR 模型权重", kind="dir-exists",
                         path="~/.jarvis/assets/paddleocr", optional=True,
                         note="首次使用会自行下载；本阶段不自动下载，只报告"),),
        repair_actions=("pip-extras", "fetch-model"),
        tools=(),
    ),
    # ---- 外部服务与工具链 ----
    "mcp.connect": CapabilityMeta(
        name="mcp.connect",
        title="MCP 外部服务接入",
        extras_group="mcp",
        python_reqs=(PythonReq("mcp"),),
        repair_actions=("pip-extras",),
        degradable="退回内置工具与 WebSearch",
        tools=(),
        tool_prefixes=("mcp__",),
    ),
    "lsp.python": CapabilityMeta(
        name="lsp.python",
        title="Python 语言服务（诊断/跳转）",
        # 没有对应的 extras 组（pyproject 里 `dev` 是 pytest/ruff），所以修复只给建议不代装；
        # 想让 `pip-extras` 能自动修它，先要新增 extras 组，那是 S4/S6 的一个决定。
        extras_group="",
        python_reqs=(PythonReq("python-lsp-server", import_name="pylsp", optional=True),),
        binaries=(BinaryReq("pylsp", args=("--version",), optional=True,
                            note="语言服务器按命令启动；pip 装它在当前环境的 Scripts 目录里，"
                                 "PATH 没包含时 which 找不到"),),
        repair_actions=(),
        degradable="退回 grep/编译级检查",
        tools=("LSP",),
    ),
    # ---- 支撑类：缺了会静默降级的 ----
    "checkpoint.git": CapabilityMeta(
        name="checkpoint.git",
        title="轮次检查点（shadow git，/undo 的底）",
        binaries=(BinaryReq("git", args=("--version",), expect_pattern="git version"),),
        repair_actions=(),      # git 属系统级依赖：只给建议，不代装（见禁止清单）
        degradable="/undo 只剩文件级留底，整轮回退不可用",
        tools=(),
    ),
    "secrets.keyring": CapabilityMeta(
        name="secrets.keyring",
        title="系统密钥环（API key 不落明文）",
        # keyring 从未写进 pyproject（core 依赖与 extras 里都没有），所以缺它是必然结果：
        # keyring_store.py L68 会打一条 debug 日志后静默退回 TOML 明文。实机已验证。
        python_reqs=(PythonReq("keyring", optional=True),),
        repair_actions=(),
        degradable="密钥退回配置文件明文/环境变量，需在汇报里说明",
        tools=(),
    ),
    "system.monitor": CapabilityMeta(
        name="system.monitor",
        title="系统资源监控（/health、主动播报阈值）",
        extras_group="daemon",
        python_reqs=(PythonReq("psutil", optional=True),),
        repair_actions=("pip-extras",),
        degradable="监控与阈值告警关闭，其余功能不受影响",
        tools=("SystemHealth",),
    ),
}

# 没有任何外部前置的 deferred 工具：契约测要求「每个 deferred 工具都要能被表查到」，
# 这类工具显式登记在豁免名单里，避免为了凑数给它们编一个能力条目。
DEPENDENCY_FREE_TOOLS: frozenset[str] = frozenset({
    "DiskClean",   # 纯本地文件操作，走隔离区不依赖第三方库
})


def get_meta(name: str) -> CapabilityMeta | None:
    """按能力名取声明，没有返回 None（调用方必须按 `CapState.UNKNOWN` 处理）。"""
    return CAPABILITY_REGISTRY.get(name)


def capability_for_tool(tool_name: str) -> str:
    """反查一个工具属于哪个能力，返回能力名；无归属返回空串。

    给两处用：① ToolSearch 在工具不可用时补一句"缺什么"；② 契约测要求
    每个 deferred 工具都能在这里查到归属（漏声明直接红，防表烂掉）。
    动态工具（MCP 那类）按名前缀匹配。
    """
    for cap in CAPABILITY_REGISTRY.values():
        if tool_name in cap.tools:
            return cap.name
        for prefix in cap.tool_prefixes:
            if tool_name.startswith(prefix):
                return cap.name
    return ""


def all_declared_tools() -> set[str]:
    """表里声明过的工具名集合（契约测的白名单来源）。"""
    out: set[str] = set()
    for cap in CAPABILITY_REGISTRY.values():
        out.update(cap.tools)
    return out


def capabilities_blocking_names() -> list[str]:
    """表中声明 `blocks_selfcheck=True` 的能力名（按名字排序，保证输出稳定）。"""
    return sorted(cap.name for cap in CAPABILITY_REGISTRY.values() if cap.blocks_selfcheck)


def extras_for_group(group: str) -> list[str]:
    """某 extras 组下的包名清单（`pip-extras` 修复动作据此生成命令）。

    事实来源仍是 `pyproject.toml`——本函数只提供"能力 → extras 组"的反向可查性，
    安装时到底装什么由 repair 层读 pyproject，避免两处各写一份而漂移。
    """
    pkgs: list[str] = []
    for cap in CAPABILITY_REGISTRY.values():
        if cap.extras_group != group:
            continue
        for req in cap.python_reqs:
            if req.distribution not in pkgs:
                pkgs.append(req.distribution)
    return pkgs
