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
    # 桌面壳 spawn 本模块时无 --workdir 概念：启动据 projects.toml 的 last_active
    # 恢复上次项目目录（"重开续用最近项目"）；无记录/目录已失效则回退进程默认。
    # @author aceFelix
    from agent.config.projects_registry import get_last_active_existing

    _settings = load_settings()
    _restore = get_last_active_existing()
    if _restore:
        _settings = _settings.with_overrides(workdir=_restore)
    sys.exit(run_serve(_settings))
