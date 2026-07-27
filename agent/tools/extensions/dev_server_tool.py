"""开发服务器启动工具。

让 Jarvis 能够自动识别常见前端项目（Vite / Next.js / Vue CLI / Webpack /
Create React App / Nuxt / Gatsby 等），启动开发服务器，并处理：
- Windows / Unix 路径兼容
- 端口占用自动递增
- stdout/stderr 日志重定向
- 从日志中提取访问 URL
- 返回 PID、端口、日志路径

用于用户说"帮我启动这个项目""跑一下前端""启动开发服务器"等场景。

@author aceFelix
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool


logger = logging.getLogger(__name__)

# 项目类型默认端口
_DEFAULT_PORTS: dict[str, int] = {
    "vite": 5173,
    "next": 3000,
    "nuxt": 3000,
    "cra": 3000,
    "gatsby": 8000,
    "vue-cli": 8080,
    "webpack": 8080,
    "unknown": 3000,
}

# 项目类型到默认命令模板（无 package.json scripts 时回退）
_FALLBACK_COMMANDS: dict[str, str] = {
    "vite": "npx vite --port {port}",
    "next": "npx next dev --port {port}",
    "nuxt": "npx nuxt dev --port {port}",
    "cra": "npx react-scripts start",
    "gatsby": "npx gatsby develop --port {port}",
    "vue-cli": "npx vue-cli-service serve --port {port}",
    "webpack": "npx webpack serve --port {port}",
}


class DevServerTool(Tool):
    """启动前端/Node 开发服务器。

    自动检测项目类型、package manager、可用端口，后台启动进程并监控日志。
    """

    name = "DevServer"
    description = (
        "启动前端或 Node 开发服务器。"
        "自动识别 Vite / Next.js / Vue CLI / Webpack / CRA / Nuxt / Gatsby 等项目，"
        "处理端口占用、后台进程、日志输出，并返回访问 URL。"
        "用于用户说'帮我启动这个项目'/'跑一下前端'/'启动开发服务器'等场景。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "项目目录路径。支持绝对路径或相对于当前工作目录的相对路径。默认使用当前工作目录。",
            },
            "command": {
                "type": "string",
                "description": "自定义启动命令（可选）。提供时优先使用，不再自动检测项目类型。",
            },
            "port": {
                "type": "integer",
                "description": "指定端口号（可选）。未指定时按项目类型使用默认端口；若被占用则自动递增。",
            },
            "auto_port": {
                "type": "boolean",
                "description": "端口被占用时是否自动尝试下一个端口。默认 true。",
                "default": True,
            },
            "wait_seconds": {
                "type": "integer",
                "description": "启动后等待服务器就绪的最长秒数。默认 10 秒。",
                "default": 10,
            },
        },
        "required": [],
    }
    max_result_chars = 3_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        """启动服务器会创建进程并可能写入日志，非只读。"""
        return False

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        """不同项目的服务器可并发启动，返回 True。"""
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        """启动外部进程、占用端口，默认需要用户确认。"""
        project_dir = self._resolve_project_dir(args.get("project_dir", ""), ctx)
        return PermissionResult.ask(f"启动 {project_dir} 的开发服务器")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        """展示给用户的活动描述。"""
        if args is None:
            return "启动开发服务器"
        project_dir = args.get("project_dir", "")
        return f"启动开发服务器: {project_dir or '当前目录'}"

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行开发服务器启动。

        步骤：
        1. 解析 project_dir
        2. 检测项目类型和 package manager
        3. 确定并检测端口
        4. 构建启动命令
        5. 后台启动进程，重定向日志
        6. 等待端口就绪
        7. 返回结果（PID / 端口 / URL / 日志路径）

        @author aceFelix
        """
        project_dir = self._resolve_project_dir(args.get("project_dir", ""), ctx)
        if not project_dir.is_dir():
            return ToolResult.error(f"项目目录不存在: {project_dir}")

        custom_command = (args.get("command") or "").strip()
        requested_port = args.get("port")
        auto_port = bool(args.get("auto_port", True))
        wait_seconds = int(args.get("wait_seconds", 10))

        # 1. 检测项目类型
        if custom_command:
            project_type = "custom"
            package_manager = _detect_package_manager(project_dir)
            command_str = custom_command
        else:
            project_type, package_manager, script_cmd = _detect_project(project_dir)
            if project_type == "unknown":
                return ToolResult.error(
                    f"无法识别 {project_dir} 的项目类型。"
                    f"请提供自定义命令，例如 /server --command 'npm run dev'。"
                )
            command_str = script_cmd

        # 2. 确定端口
        base_port = int(requested_port) if requested_port else _DEFAULT_PORTS.get(project_type, 3000)
        port = base_port
        if _is_port_in_use(port):
            if not auto_port:
                return ToolResult.error(f"端口 {port} 已被占用，且 auto_port=false。")
            for candidate in range(port + 1, port + 100):
                if not _is_port_in_use(candidate):
                    port = candidate
                    break
            else:
                return ToolResult.error(f"端口 {base_port} ~ {base_port + 99} 全部占用。")

        # 3. 构建完整命令（注入端口）
        final_command = self._build_command(
            command_str, project_type, package_manager, port, project_dir
        )

        # 4. 准备日志文件
        log_file = _prepare_log_file(project_dir)

        # 5. 启动进程
        try:
            proc = _start_process(final_command, project_dir, log_file)
        except Exception as e:
            return ToolResult.error(f"启动进程失败: {e}")

        # 6. 等待端口就绪
        started = _wait_for_port(port, timeout=wait_seconds)

        # 7. 尝试从日志提取 URL
        url = f"http://localhost:{port}"
        log_url = _extract_url_from_log(log_file)
        if log_url:
            url = log_url

        result = {
            "success": started,
            "project_dir": str(project_dir),
            "project_type": project_type,
            "command": final_command,
            "pid": proc.pid,
            "port": port,
            "url": url,
            "log_file": str(log_file),
            "status": "已启动" if started else "进程已启动，但端口未在预期时间内就绪",
        }

        if started:
            return ToolResult(data=json.dumps(result, ensure_ascii=False, indent=2))
        return ToolResult(
            data=json.dumps(result, ensure_ascii=False, indent=2),
            is_error=True,
        )

    def _resolve_project_dir(self, raw: str, ctx: ToolContext) -> Path:
        """解析项目目录。

        处理三种路径格式：
        - Windows 绝对路径: E:\\path\\to\\project
        - Git Bash 风格: /e/path/to/project → E:\\path\\to\\project
        - 相对路径: jarvis-website → workdir/jarvis-website

        @author aceFelix
        """
        workdir = ctx.settings.workdir if ctx.settings else os.getcwd()
        if raw:
            # 检测 Git Bash 风格路径 (/e/... → E:\...)
            raw = _normalize_gitbash_path(raw)
            p = Path(raw)
            if not p.is_absolute():
                p = Path(workdir) / p
            return p.resolve()
        return Path(workdir).resolve()

    def _build_command(
        self,
        command_str: str,
        project_type: str,
        package_manager: str,
        port: int,
        project_dir: Path,
    ) -> str:
        """把检测出的命令转换成可执行字符串，并注入端口信息。"""
        # 如果检测出来的是 npm script（形如 "npm run dev"），已经包含 package_manager
        cmd = command_str

        # 对 fallback 模板填充端口
        if project_type in _FALLBACK_COMMANDS and cmd == _FALLBACK_COMMANDS[project_type]:
            cmd = cmd.format(port=port)

        # 如果命令里包含 {port} 占位符但未填充，填充它
        cmd = cmd.replace("{port}", str(port))

        # 某些项目类型需要 PORT 环境变量（如 CRA）
        env_prefix = ""
        if project_type == "cra" and "--port" not in cmd and "PORT=" not in cmd:
            env_prefix = f"set PORT={port} && " if sys.platform == "win32" else f"PORT={port} "

        return f"{env_prefix}{cmd}"


def _detect_package_manager(project_dir: Path) -> str:
    """检测项目使用的包管理器（npm / yarn / pnpm）。"""
    if (project_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (project_dir / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _detect_project(project_dir: Path) -> tuple[str, str, str]:
    """检测项目类型并返回 (project_type, package_manager, start_command)。"""
    pkg_file = project_dir / "package.json"
    package_manager = _detect_package_manager(project_dir)

    # 根据配置文件判断类型
    project_type = _detect_by_config(project_dir)

    # 读取 package.json
    scripts: dict[str, str] = {}
    deps: set[str] = set()
    dev_deps: set[str] = set()
    if pkg_file.is_file():
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            deps = set(pkg.get("dependencies", {}).keys())
            dev_deps = set(pkg.get("devDependencies", {}).keys())
        except Exception as e:
            logger.warning("解析 package.json 失败 %s: %s", project_dir, e)

    # 如果配置文件没识别出来，根据依赖判断
    if project_type == "unknown":
        project_type = _detect_by_dependencies(deps | dev_deps)

    # 选择启动命令
    script_cmd = _select_start_command(project_type, scripts, package_manager, project_dir)

    return project_type, package_manager, script_cmd


def _detect_by_config(project_dir: Path) -> str:
    """根据配置文件判断项目类型。"""
    patterns = [
        ("vite", ["vite.config.*"]),
        ("next", ["next.config.*"]),
        ("nuxt", ["nuxt.config.*"]),
        ("vue-cli", ["vue.config.*"]),
        ("webpack", ["webpack.config.*"]),
        ("gatsby", ["gatsby-config.*"]),
    ]
    for ptype, globs in patterns:
        for g in globs:
            if list(project_dir.glob(g)):
                return ptype
    return "unknown"


def _detect_by_dependencies(deps: set[str]) -> str:
    """根据依赖判断项目类型。"""
    if "vite" in deps:
        return "vite"
    if "next" in deps:
        return "next"
    if "nuxt" in deps:
        return "nuxt"
    if "@vue/cli-service" in deps:
        return "vue-cli"
    if "react-scripts" in deps:
        return "cra"
    if "webpack" in deps or "webpack-dev-server" in deps:
        return "webpack"
    if "gatsby" in deps:
        return "gatsby"
    return "unknown"


def _select_start_command(
    project_type: str,
    scripts: dict[str, str],
    package_manager: str,
    project_dir: Path,
) -> str:
    """选择最佳启动命令。"""
    # 优先使用 package.json 中的 scripts
    for script_name in ("dev", "start", "serve"):
        if script_name in scripts:
            return f"{package_manager} run {script_name}"

    # 没有 script 时按项目类型回退
    fallback = _FALLBACK_COMMANDS.get(project_type)
    if fallback:
        return fallback

    # 兜底：尝试 npx serve 或 npm start
    if "start" in scripts:
        return f"{package_manager} start"
    return "npx serve ."


def _is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """检查端口是否被占用。

    同时尝试 IPv4 (127.0.0.1) 和 IPv6 (::1)，因为部分开发服务器
    （如 Vite）可能仅绑定到 IPv6 环回地址，导致 IPv4 检测不到。

    @author aceFelix
    """
    for h in (host, "::1"):
        try:
            with socket.create_connection((h, port), timeout=0.5):
                return True
        except OSError:
            continue
    return False


def _prepare_log_file(project_dir: Path) -> Path:
    """准备日志文件路径。"""
    log_dir = Path.home() / ".jarvis" / "dev_server_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = project_dir.name or "project"
    return log_dir / f"{name}_{timestamp}.log"


def _start_process(command: str, cwd: Path, log_file: Path) -> subprocess.Popen:
    """后台启动开发服务器进程。

    关键：stdin 必须设为 DEVNULL，否则子进程（npm/vite）会继承父进程的 stdin，
    Vite 在运行时监听键盘输入（h+Enter 显示帮助等），会抢走 jarvis 的键盘输入，
    导致父进程收不到用户消息，表现为"jarvis 卡住不回复"。

    @author aceFelix
    """
    full_cmd = f'{command} > "{log_file}" 2>&1'

    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "shell": True,
        "stdin": subprocess.DEVNULL,  # 关键：断开子进程 stdin，防止抢键盘输入
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    return subprocess.Popen(full_cmd, **kwargs)


def _wait_for_port(port: int, timeout: int = 10) -> bool:
    """等待端口就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _is_port_in_use(port):
            time.sleep(0.5)
            continue
        return True
    return False


def _extract_url_from_log(log_file: Path) -> str | None:
    """从日志中提取最常见的本地访问 URL。"""
    if not log_file.exists():
        return None
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    # 匹配 http://localhost:port 或 http://127.0.0.1:port
    matches = re.findall(r"https?://(?:localhost|127\.0\.0\.1):\d+", text)
    if matches:
        return matches[-1]

    # Vite 的 Network: 行
    m = re.search(r"Network:\s+(https?://\S+)", text)
    if m:
        return m.group(1)

    return None


def _normalize_gitbash_path(raw: str) -> str:
    """将 Git Bash 风格路径转为 Windows 绝对路径。

    Git Bash 路径如 /e/J.A.R.V.I.S_Work/...，在 Windows 上 Path() 无法正确解析。
    此函数检测 /<drive_letter>/ 模式，转为 Windows 绝对路径。

    @author aceFelix
    """
    if sys.platform != "win32":
        return raw

    # 匹配 /<单字母>/rest → 转为 <字母>:\rest
    m = re.match(r"^/([a-zA-Z])/(.*)", raw)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2).replace("/", "\\")
        return f"{drive}:\\{rest}"

    return raw
