"""CLI-Anything harness 集成模块。

让 J.A.R.V.I.S 能够扫描 ``~/.jarvis/cli_anything/`` 下的 harness，
把任意软件包装为可调用的 Tool。

@author aceFelix
"""

from __future__ import annotations

from agent.cli_anything.loader import discover_harnesses, parse_skill_md
from agent.cli_anything.market import install_harness, list_installed, list_market, uninstall_harness
from agent.cli_anything.registry import discover_and_register, register_harness_tool
from agent.cli_anything.runner import run_harness
from agent.cli_anything.schema import Harness, HarnessArg

__all__ = [
    "Harness",
    "HarnessArg",
    "discover_harnesses",
    "parse_skill_md",
    "run_harness",
    "register_harness_tool",
    "discover_and_register",
    "list_installed",
    "list_market",
    "install_harness",
    "uninstall_harness",
]
