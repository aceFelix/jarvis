"""``python -m agent.serve`` 入口 —— 桌面壳 spawn 子进程的调用形式。

Electron 主进程（jarvis-desktop backend.ts）以 ``python -m agent.serve``
拉起本模块，经 stdout 握手 JSON 获取端口与 token。

@author aceFelix
"""

from __future__ import annotations

import sys

from agent.config.settings import load_settings
from agent.serve.app import run_serve

if __name__ == "__main__":
    sys.exit(run_serve(load_settings()))
