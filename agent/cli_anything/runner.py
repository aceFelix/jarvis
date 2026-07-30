"""CLI-Anything harness 执行器。

负责把 Harness + 用户传入的参数转换为安全子进程命令，并收集执行结果。

设计要点：
- 不使用 shell，避免注入风险。
- 命令参数按位置拼接，额外注入 ``--harness-dir`` 和 ``--workdir``。
- 带超时和强制终止，防止 harness 卡死。
- 返回结构化 dict，方便上层 Tool 处理。

@author aceFelix
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from pathlib import Path
from typing import Any

from agent.cli_anything.schema import Harness, HarnessArg

logger = logging.getLogger(__name__)

# 默认 harness 执行超时（秒）
_DEFAULT_TIMEOUT = 120.0

# 允许作为 command 的白名单前缀（降低命令注入风险）
# cli-anything-* 是由 CLI-Anything 生态安装的 harness 入口
# jarvis-harness-* 是由 Jarvis 自定义市场安装的 harness 入口
_ALLOWED_COMMAND_PREFIXES = (
    "python", "node", "python3", "node.exe", "npm", "npx",
    "cli-anything-", "jarvis-harness-"
)

# 解释器类命令，如果只写了解释器，会自动拼接 harness 目录下的默认脚本
_INTERPRETERS = ("python", "python3", "node", "node.exe")


def _validate_command(command: str) -> list[str]:
    """把 command 字符串解析为参数列表，并做简单安全校验。

    只接受白名单前缀的命令，拒绝裸 shell 命令（如 ``bash -c``）。
    """
    parts = shlex.split(command, posix=False)
    if not parts:
        raise ValueError("harness command 为空")
    first = parts[0].lower()
    # 允许绝对路径的 python / node
    if Path(first).name.lower() in ("python", "python3", "node", "node.exe", "npm", "npx"):
        return parts
    if not any(first.startswith(prefix) for prefix in _ALLOWED_COMMAND_PREFIXES):
        raise ValueError(f"harness command 不被允许: {command}")
    return parts


def _build_args(harness: Harness, kwargs: dict[str, Any]) -> list[str]:
    """根据 HarnessArg 定义把 kwargs 转换为命令行参数。

    规则：
    - 必填参数缺失时报错。
    - 参数名转换为 ``--<name>`` 形式。
    - 布尔值 true 时只传标志（如 ``--force``），false 时忽略。
    - 列表用逗号分隔（后续可扩展为多次传入同一参数）。
    - 字符串参数中的 Git Bash 路径（/e/...）自动转为 Windows 路径（E:\\...）。
    """
    result: list[str] = []
    provided = set()

    for arg in harness.args:
        value = kwargs.get(arg.name)
        if value is None and arg.default is not None:
            value = arg.default

        if arg.required and value is None:
            raise ValueError(f"缺少必填参数: {arg.name}")

        if value is None:
            continue

        provided.add(arg.name)

        # 布尔值特殊处理：true 传标志，false 不传
        if arg.type == "boolean":
            if value:
                flag = arg.name.replace("_", "-")
                result.append(f"--{flag}")
            continue

        # 列表值用逗号连接
        if isinstance(value, list):
            value = ",".join(str(v) for v in value)

        # 字符串值：自动转换 Git Bash 路径 → Windows 路径
        if isinstance(value, str):
            value = _normalize_path(value)

        # 位置参数：只传值，不加 --name 前缀
        if arg.positional:
            result.append(str(value))
            continue

        # CLI 惯例：flag 用连字符，arg 名用下划线。如 output_path → --output-path
        flag_name = arg.name.replace("_", "-")
        result.append(f"--{flag_name}")
        result.append(str(value))

    # 未知参数不报错，仅记录日志——LLM 可能多传参数，不应导致执行失败
    unknown = set(kwargs.keys()) - provided - {"harness_dir", "workdir"}
    if unknown:
        import logging
        logging.getLogger(__name__).warning("忽略未知参数: %s", sorted(unknown))

    return result


def _normalize_path(value: str) -> str:
    """把 Git Bash 路径（/e/...）转为 Windows 路径（E:\\...）。

    harness CLI 是 Windows 子进程，不认 Git Bash 路径。
    LLM 在 system prompt 里被教用 /e/... 格式，传给 harness 时需要转换。
    """
    import re
    # 匹配 /e/... /c/Users/... 等 Git Bash 路径
    m = re.match(r'^/([a-zA-Z])/(.*)$', value)
    if m:
        drive = m.group(1).upper()
        rest = m.group(2)
        rest_replaced = rest.replace('/', '\\')
        return f"{drive}:\\{rest_replaced}"
    return value


async def run_harness(
    harness: Harness,
    kwargs: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    workdir: str = "",
) -> dict[str, Any]:
    """执行 harness 命令。

    Args:
        harness: 要执行的 harness。
        kwargs: 用户传入的参数名值对。
        timeout: 超时时间（秒）。
        workdir: 当前工作目录，会作为 ``--workdir`` 传给 harness。

    Returns:
        dict，包含 stdout / stderr / exit_code / error。
    """
    try:
        base_cmd = _validate_command(harness.command)
        harness_args = _build_args(harness, kwargs)
    except ValueError as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "error": str(e)}

    cmd = list(base_cmd)

    # 如果 command 只写了 python / node 等解释器，自动拼接 harness 目录下的默认脚本
    if (
        harness.dir_path is not None
        and len(cmd) == 1
        and Path(cmd[0]).name.lower() in _INTERPRETERS
    ):
        default_script = harness.dir_path / "run.py"
        if default_script.exists():
            cmd.append(str(default_script))

    # --harness-dir / --workdir 放在 harness_args 前面：
    # harness CLI（如 jarvis-harness-wps）的 argparse 期望可选参数在子命令之前。
    if harness.dir_path is not None:
        cmd.append("--harness-dir")
        cmd.append(str(harness.dir_path))

    if workdir:
        cmd.append("--workdir")
        cmd.append(workdir)

    cmd.extend(harness_args)

    logger.debug("执行 harness 命令: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir or None,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "stdout": "",
                "stderr": f"harness 执行超时（>{timeout}s）",
                "exit_code": -1,
                "error": "timeout",
            }

        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode or 0,
            "error": "",
        }
    except Exception as e:
        logger.exception("harness 执行异常")
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "error": str(e)}
