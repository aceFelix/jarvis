"""依赖健康检查模块（jarvis --doctor）。

一键检查 J.A.R.V.I.S 运行所需的全部依赖是否就绪：
1. Python 可选包（按 extras 组归类）：用 importlib.util.find_spec 探测，不实际 import，
   避免触发未安装包的副作用（如 pyaudio 缺 portaudio 会冒一堆日志）。
2. 系统级依赖：Python 版本、pip、uv、Playwright 浏览器、WebView2 Runtime（Windows）等。
3. 配置文件：settings.toml 是否存在、API key 是否配置、permissions.yaml 是否就绪。

输出用 rich 表格渲染，包含功能/包名/状态/安装命令四列，并在末尾汇总通过率。
不显示任何敏感信息（API key 只输出"已配置/未配置"）。

@author aceFelix
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table


# Python 包检查表：(功能, import 名, extras 组)
# extras 组对应 pyproject.toml [project.optional-dependencies] 中的分组名，
# "核心依赖" 表示是主依赖（pyproject.toml dependencies），无需 extras 安装。
# 注意：import 名是 Python 模块名，可能与 pip 包名不同（如 zai-sdk → import zai）。
_PKG_CHECKS: list[tuple[str, str, str]] = [
    # LLM 核心依赖（主 dependencies，理论上一定装了）
    # zai-sdk 的 import 名是 "zai"（项目代码 from zai import ZhipuAiClient）
    ("LLM 核心", "anthropic", "核心依赖"),
    ("LLM 核心", "openai", "核心依赖"),
    ("LLM 核心", "zai", "核心依赖"),
    # 语音 TTS/STT
    ("语音 TTS/STT", "dashscope", "voice"),
    ("语音 TTS/STT", "pyaudio", "voice"),
    ("语音 TTS/STT", "aec_audio_processing", "voice"),
    ("语音 TTS/STT", "numpy", "voice"),
    # 系统监控（主依赖，新 GUI 工作台右栏指标数据源）
    ("系统监控", "psutil", "核心依赖"),
    # GUI 操作
    ("GUI 操作", "pyautogui", "gui"),
    ("GUI 操作", "PIL", "gui"),
    # 浏览器
    ("浏览器", "playwright", "browser"),
    # 摄像头
    ("摄像头", "cv2", "camera"),
    # 视觉监控
    ("视觉监控", "mediapipe", "vision"),
    ("视觉监控", "cv2", "vision"),
    ("视觉监控", "paddleocr", "vision"),
    # MCP
    ("MCP", "mcp", "mcp"),
    # 实时窗口
    ("实时窗口", "webview", "realtime_ui"),
    # 微信
    ("微信", "aiohttp", "wechat"),
    ("微信", "qrcode", "wechat"),
]


# 安装命令模板：extras 组 → pip install 命令片段
# 核心依赖无需安装命令；其余按 extras 名拼接 pip install "jarvis-agent[<extras>]"。
_EXTRAS_INSTALL_TEMPLATE = 'pip install "jarvis-agent[{extras}]"'


def _is_pkg_available(import_name: str) -> bool:
    """用 importlib.util.find_spec 探测包是否可导入。

    不实际执行 import，避免触发未安装包的副作用日志
    （例如 pyaudio 缺 portaudio 库时直接 import 会打印 ALSA 警告）。

    @param import_name: 顶层模块名（如 "cv2"、"PIL"、"zai_sdk"）
    @return True 表示已安装且 find_spec 找到，False 表示未安装
    """
    try:
        # find_spec 返回 ModuleSpec 或 None；None 即未找到该包
        spec = importlib.util.find_spec(import_name)
        return spec is not None
    except (ImportError, ValueError):
        # 某些包名在 find_spec 阶段可能抛 ImportError（如命名空间包冲突）
        return False


def _build_pkg_rows() -> list[tuple[str, str, str, str]]:
    """构造 Python 包检查表格的行数据。

    @return 行列表，每行四元素：(功能, 包名, 状态文本, 安装命令)
    """
    rows: list[tuple[str, str, str, str]] = []
    for feature, pkg, extras in _PKG_CHECKS:
        ok = _is_pkg_available(pkg)
        if ok:
            status = "[green]✓ 已安装[/green]"
            install_cmd = ""
        else:
            status = "[red]✗ 未安装[/red]"
            # 核心依赖缺失属于异常情况，提示重装主包
            if extras == "核心依赖":
                install_cmd = 'pip install "jarvis-agent" --force-reinstall'
            else:
                install_cmd = _EXTRAS_INSTALL_TEMPLATE.format(extras=extras)
        rows.append((feature, pkg, status, install_cmd))
    return rows


def _check_python_version() -> tuple[bool, str]:
    """检查 Python 版本是否 >= 3.11（pyproject.toml requires-python）。

    @return (是否通过, 说明文本)
    """
    major, minor = sys.version_info[:2]
    py_ver_str = f"Python {major}.{minor}.{sys.version_info[2]}"
    if sys.version_info >= (3, 11):
        return True, f"{py_ver_str}，满足 >=3.11"
    return False, f"{py_ver_str}，不满足 >=3.11，请升级 Python"


def _check_cli_tool(name: str) -> bool:
    """检查某个命令行工具（pip/uv 等）是否在 PATH 中可用。

    @param name: 工具名（如 "pip"、"uv"）
    @return True 表示 PATH 中能找到
    """
    return shutil.which(name) is not None


def _check_playwright_browsers() -> tuple[bool, str]:
    r"""检查 Playwright 浏览器是否已下载。

    Playwright 浏览器默认安装在用户缓存目录：
    - Windows: %USERPROFILE%\AppData\Local\ms-playwright
    - macOS/Linux: ~/.cache/ms-playwright

    @return (是否安装, 说明文本)
    """
    system = platform.system()
    if system == "Windows":
        # Windows 下 Playwright 浏览器缓存路径
        local_appdata = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        cache_dir = Path(local_appdata) / "ms-playwright"
    else:
        cache_dir = Path.home() / ".cache" / "ms-playwright"

    if not cache_dir.exists():
        return False, f"未找到 {cache_dir}，运行: playwright install"

    # 目录里有子目录才算真正装了浏览器（chromium-*/firefox-*/webkit-*）
    try:
        subdirs = [p for p in cache_dir.iterdir() if p.is_dir()]
    except OSError:
        return False, f"无法读取 {cache_dir}"

    if not subdirs:
        return False, f"{cache_dir} 为空，运行: playwright install"

    browser_names = ", ".join(sorted(p.name for p in subdirs))
    return True, f"已安装: {browser_names}"


def _check_webview2_runtime() -> tuple[bool, str]:
    """检查 Windows 上 Edge WebView2 Runtime 是否安装（realtime_ui 依赖）。

    通过查询注册表判断（pywebview Windows 后端默认依赖 WebView2）。
    非 Windows 平台直接跳过。

    @return (是否安装, 说明文本)
    """
    if platform.system() != "Windows":
        return True, "非 Windows 平台，跳过"

    # 通过 reg query 查注册表，避免引入 winreg 依赖（跨平台可 import）
    # 注册表路径对应 WebView2 Runtime 的 Core 版本键
    reg_paths = [
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    ]
    for reg_path in reg_paths:
        try:
            # subprocess 静默查询，不输出到控制台
            result = subprocess.run(
                ["reg", "query", reg_path],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return True, "已安装（Edge WebView2 Runtime）"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # reg 命令不存在（理论上 Windows 一定有）或超时
            continue
    return False, "未检测到 Edge WebView2 Runtime，Win10/11 通常预装"


def _build_system_rows() -> list[tuple[str, str, str]]:
    """构造系统级依赖检查表格的行数据。

    @return 行列表，每行三元素：(检查项, 状态文本, 说明文本)
    """
    rows: list[tuple[str, str, str]] = []

    # Python 版本
    py_ok, py_msg = _check_python_version()
    py_status = "[green]✓[/green]" if py_ok else "[red]✗[/red]"
    major, minor = sys.version_info[:2]
    rows.append((f"Python {major}.{minor}.{sys.version_info[2]}", py_status, py_msg))

    # pip
    pip_ok = _check_cli_tool("pip")
    pip_status = "[green]✓[/green]" if pip_ok else "[red]✗[/red]"
    pip_msg = "可用" if pip_ok else "未找到 pip，请检查 Python 安装"
    rows.append(("pip", pip_status, pip_msg))

    # uv（推荐）
    uv_ok = _check_cli_tool("uv")
    uv_status = "[green]✓[/green]" if uv_ok else "[yellow]✗[/yellow]"
    uv_msg = "可用（推荐）" if uv_ok else "推荐安装: pip install uv"
    rows.append(("uv", uv_status, uv_msg))

    # Playwright 浏览器
    pw_ok, pw_msg = _check_playwright_browsers()
    pw_status = "[green]✓[/green]" if pw_ok else "[red]✗[/red]"
    rows.append(("Playwright 浏览器", pw_status, pw_msg))

    # Windows 专属：WebView2 Runtime
    if platform.system() == "Windows":
        wv2_ok, wv2_msg = _check_webview2_runtime()
        wv2_status = "[green]✓[/green]" if wv2_ok else "[red]✗[/red]"
        rows.append(("Edge WebView2 Runtime", wv2_status, wv2_msg))

    # 麦克风权限（仅提示，不实际检测）
    rows.append(("麦克风权限", "[blue]ℹ[/blue]", "请确认系统已授予 Python 麦克风访问权限（仅提示，不实际检测）"))

    return rows


def _check_settings_toml() -> tuple[bool, str, Path | None]:
    """检查用户级 settings.toml 是否存在。

    检查 ~/.jarvis/settings.toml（兼容回退 ~/.my-agent/settings.toml）。

    @return (是否存在, 说明文本, 文件路径)
    """
    user_cfg = Path.home() / ".jarvis" / "settings.toml"
    if user_cfg.exists():
        return True, f"已存在: {user_cfg}", user_cfg
    # 兼容旧路径
    legacy_cfg = Path.home() / ".my-agent" / "settings.toml"
    if legacy_cfg.exists():
        return True, f"已存在（旧路径）: {legacy_cfg}", legacy_cfg
    return False, "未找到，运行: jarvis --init", None


def _check_api_key(settings_path: Path | None) -> tuple[bool, str]:
    """检查 settings.toml 中是否配置了 api_key（不显示 key 内容）。

    也会检查 DashScope 专属 key（realtime_talk.api_key）以及常见环境变量。
    仅判断"已配置/未配置"，绝不输出 key 本身。

    @return (是否配置, 说明文本)
    """
    # 1. 先查环境变量（DASHSCOPE_API_KEY / OPENAI_API_KEY 等常见厂商）
    env_keys = [
        "DASHSCOPE_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ZAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "KIMI_API_KEY",
        "MINIMAX_API_KEY",
        "MIMO_API_KEY",
        "JARVIS_API_KEY",
    ]
    for env_name in env_keys:
        val = os.environ.get(env_name, "").strip()
        if val:
            return True, f"已配置（环境变量 {env_name}）"

    # 2. 查 settings.toml 文件中的 api_key 字段
    if settings_path is None or not settings_path.exists():
        return False, "未配置，运行: jarvis --init"

    try:
        if sys.version_info >= (3, 11):
            import tomllib
        else:  # pragma: no cover
            import tomli as tomllib  # type: ignore[import-not-found,no-redef]
        with open(settings_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        # 解析失败视为未配置（避免把异常栈当 key 泄露）
        return False, f"settings.toml 解析失败，请检查格式"

    # 顶层 api_key
    top_key = str(data.get("api_key", "")).strip()
    if top_key:
        return True, "已配置（settings.toml [api_key]）"

    # realtime_talk.api_key（DashScope 实时语音）
    realtime = data.get("realtime_talk", {})
    if isinstance(realtime, dict):
        rt_key = str(realtime.get("api_key", "")).strip()
        if rt_key:
            return True, "已配置（settings.toml [realtime_talk.api_key]）"

    return False, "未配置，运行: jarvis --init 或设置环境变量"


def _check_permissions_yaml() -> tuple[bool, str]:
    """检查 permissions.yaml 是否存在。

    查找路径：
    1. ~/.jarvis/configs/permissions.yaml（用户级）
    2. 项目级 configs/permissions.yaml（随包分发，作为兜底默认规则）

    @return (是否存在, 说明文本)
    """
    # 用户级
    user_perm = Path.home() / ".jarvis" / "configs" / "permissions.yaml"
    if user_perm.exists():
        return True, f"已存在: {user_perm}"

    # 项目级：尝试在 agent 包的同级目录找 configs/permissions.yaml
    # agent 包路径：agent/__init__.py 的父目录是 agent/，再上一层是项目根
    try:
        agent_pkg_dir = Path(__file__).resolve().parent
        project_root = agent_pkg_dir.parent
        project_perm = project_root / "configs" / "permissions.yaml"
        if project_perm.exists():
            return True, f"使用项目级默认规则: {project_perm}"
    except Exception:
        pass

    # 没有自定义权限文件也没关系：PermissionChecker 内置了硬编码的"危险目录拒绝"规则
    return True, "未找到自定义权限文件，使用内置默认规则（危险目录拒绝）"


def _build_config_rows() -> list[tuple[str, str, str]]:
    """构造配置检查表格的行数据。

    @return 行列表，每行三元素：(检查项, 状态文本, 说明文本)
    """
    rows: list[tuple[str, str, str]] = []

    # settings.toml
    cfg_ok, cfg_msg, cfg_path = _check_settings_toml()
    cfg_status = "[green]✓[/green]" if cfg_ok else "[red]✗[/red]"
    rows.append(("settings.toml", cfg_status, cfg_msg))

    # API Key
    key_ok, key_msg = _check_api_key(cfg_path)
    key_status = "[green]✓[/green]" if key_ok else "[red]✗[/red]"
    rows.append(("API Key", key_status, key_msg))

    # permissions.yaml
    perm_ok, perm_msg = _check_permissions_yaml()
    perm_status = "[green]✓[/green]" if perm_ok else "[red]✗[/red]"
    rows.append(("permissions.yaml", perm_status, perm_msg))

    return rows


def _render_pkg_table(console: Console, rows: list[tuple[str, str, str, str]]) -> tuple[int, int]:
    """渲染 Python 包状态表格，返回 (通过数, 总数)。

    @param console: rich Console 实例
    @param rows: 包检查行数据
    @return (通过数, 总数)
    """
    table = Table(title="📦 Python 包状态", show_lines=False, expand=True)
    table.add_column("功能", style="cyan", no_wrap=True)
    table.add_column("包名", style="white")
    table.add_column("状态", no_wrap=True)
    table.add_column("安装命令", style="yellow")

    passed = 0
    for feature, pkg, status, install_cmd in rows:
        table.add_row(feature, pkg, status, install_cmd)
        if "已安装" in status:
            passed += 1

    console.print(table)
    return passed, len(rows)


def _render_table_three_col(
    console: Console, title: str, rows: list[tuple[str, str, str]]
) -> tuple[int, int]:
    """渲染三列检查表格（检查项/状态/说明），返回 (通过数, 总数)。

    通用渲染器，用于系统级依赖表和配置状态表。

    @param console: rich Console 实例
    @param title: 表格标题
    @param rows: 行数据 (检查项, 状态, 说明)
    @return (通过数, 总数)
    """
    table = Table(title=title, show_lines=False, expand=True)
    table.add_column("检查项", style="cyan", no_wrap=True)
    table.add_column("状态", no_wrap=True)
    table.add_column("说明", style="white")

    passed = 0
    for item, status, desc in rows:
        table.add_row(item, status, desc)
        # 只有红/黄叉（✗）算"失败"；绿勾（✓）和蓝 i（ℹ 仅提示）都算通过
        # 这样麦克风权限等纯提示项不会让 doctor 永远返回非零退出码
        if "✗" not in status:
            passed += 1

    console.print(table)
    return passed, len(rows)


def run_doctor() -> int:
    """执行依赖健康检查并输出结果。

    渲染三张 rich 表格：Python 包状态、系统级依赖、配置状态，
    末尾汇总通过率。退出码：0=全部通过，1=有缺失项。

    @return 退出码（0=全通过，1=有缺失）
    """
    console = Console()

    # 顶部标题
    console.print()
    console.print("[bold blue]J.A.R.V.I.S 依赖健康检查[/bold blue]")
    console.print("[blue]═" * 60 + "[/blue]")
    console.print()

    # 1. Python 包状态
    pkg_rows = _build_pkg_rows()
    pkg_passed, pkg_total = _render_pkg_table(console, pkg_rows)

    # 2. 系统级依赖
    console.print()
    sys_rows = _build_system_rows()
    sys_passed, sys_total = _render_table_three_col(console, "🔧 系统级依赖", sys_rows)

    # 3. 配置状态
    console.print()
    cfg_rows = _build_config_rows()
    cfg_passed, cfg_total = _render_table_three_col(console, "⚙️ 配置状态", cfg_rows)

    # 汇总
    total = pkg_total + sys_total + cfg_total
    passed = pkg_passed + sys_passed + cfg_passed
    missing = total - passed

    console.print()
    if missing == 0:
        console.print(f"[bold green]✓ 总结: {passed}/{total} 项全部通过[/bold green]")
        return 0
    else:
        console.print(
            f"[bold yellow]⚠ 总结: {passed}/{total} 项通过，{missing} 项缺失或异常[/bold yellow]"
        )
        return 1


if __name__ == "__main__":
    sys.exit(run_doctor())
