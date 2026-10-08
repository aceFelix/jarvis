"""PyInstaller 冻结入口：把 `python -m agent.serve` 打成交互式可执行 jarvis-serve。

桌面壳（jarvis-desktop）打包态不再依赖本机 Python 与 jarvis 源码仓库，而是
spawn 本模块冻结出的 `jarvis-serve.exe`。逻辑与 `agent/serve/__main__.py` 完全
一致：加载 settings → 据 projects.toml 的 last_active 恢复上次项目目录 → run_serve
（run_serve 内部打单行握手 JSON 到 stdout，Electron 逐行解析）。

之所以单独建入口而非直接冻结 `agent/serve/__main__.py`：PyInstaller 需要一个明确的
脚本文件作为 Analysis 入口，且 `__main__.py` 的 `if __name__ == "__main__"` 守卫在
被作为模块收集时不会执行，这里用一个显式 main() 供 spec 调用。

@author aceFelix
"""

from __future__ import annotations

import sys


def main() -> int:
    """等价于 `python -m agent.serve`：恢复上次项目目录后启动 headless serve。"""
    from agent.config.projects_registry import get_last_active_existing
    from agent.config.settings import load_settings
    from agent.serve.app import run_serve

    settings = load_settings()
    restore = get_last_active_existing()
    if restore:
        # 桌面壳 spawn 无 --workdir：据 last_active 续用最近项目（目录失效则回退默认）。
        settings = settings.with_overrides(workdir=restore)
    return run_serve(settings)


if __name__ == "__main__":
    sys.exit(main())
