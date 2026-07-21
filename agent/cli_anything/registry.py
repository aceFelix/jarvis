"""CLI-Anything harness 注册器。

把解析好的 ``Harness`` 对象包装成 ``CliAnythingTool`` 并注册到 ToolRegistry。

@author aceFelix
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agent.cli_anything.loader import discover_harnesses
from agent.cli_anything.schema import Harness
from agent.tools.extensions.cli_anything_tool import CliAnythingTool

if TYPE_CHECKING:
    from agent.core.tool import ToolRegistry

logger = logging.getLogger(__name__)


def discover_and_register(
    registry: "ToolRegistry",
    root_dir: Path | str | None = None,
    workdir: Path | str | None = None,
) -> int:
    """扫描 harness 目录并全部注册到 ToolRegistry。

    Args:
        registry: ToolRegistry 实例。
        root_dir: 可选的自定义 harness 根目录。
        workdir: 当前工作目录，用于加载项目级 harness。

    Returns:
        成功注册的 harness 数量。
    """
    harnesses = discover_harnesses(root_dir, workdir=workdir)
    registered = 0
    for harness in harnesses:
        tool = CliAnythingTool(harness)
        if tool.name in registry:
            logger.debug("harness 已存在，跳过: %s", tool.name)
            continue
        registry.register(tool)
        registered += 1
        logger.info("已注册 cli_anything harness: %s", tool.name)
    return registered


def register_harness_tool(registry: "ToolRegistry", harness: Harness) -> None:
    """把单个 harness 注册为 Tool。"""
    registry.register(CliAnythingTool(harness))
