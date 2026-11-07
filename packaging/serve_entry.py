"""PyInstaller 冻结入口：把 `python -m agent.serve` 打成交互式可执行 jarvis-serve。

桌面壳（jarvis-desktop）打包态不再依赖本机 Python 与 jarvis 源码仓库，而是
spawn 本模块冻结出的 `jarvis-serve.exe`。逻辑与 `agent/serve/__main__.py` 完全
一致：加载 settings → 据 projects.toml 的 last_active 恢复上次项目目录 → run_serve
（run_serve 内部打单行握手 JSON 到 stdout，Electron 逐行解析）。

2026-11-07 新增：

- ``--caps``：冻结包能力门禁。打印 JSON 并返回 0/1，供 build_serve.ps1 与
  发布流水线断言“八组 extras 与双浏览器内核真的在包里”。
- 冻结态把 ``PLAYWRIGHT_BROWSERS_PATH`` 指向随包的 ``ms-playwright``，
  浏览器工具离线可用，不再依赖用户另外跑 `playwright install`。

之所以单独建入口而非直接冻结 `agent/serve/__main__.py`：PyInstaller 需要一个明确的
脚本文件作为 Analysis 入口，且 `__main__.py` 的 `if __name__ == "__main__"` 守卫在
被作为模块收集时不会执行，这里用一个显式 main() 供 spec 调用。

@author aceFelix
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _configure_frozen_paths() -> None:
    """冻结态把浏览器内核路径指向随包目录（幂等、可被用户环境变量覆盖）。"""
    if not getattr(sys, "frozen", False):
        return
    root = Path(sys.executable).parent / "ms-playwright"
    if root.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(root))


def _caps_gate() -> int:
    """``--caps``：打印打包能力门禁 JSON；全部就绪返回 0，否则返回 1。"""
    from agent.capabilities import gate, probe

    reports = probe.check_all(force=True)
    result = gate.evaluate(reports)
    # 冻结态 stdout 在 Windows 控制台默认是 GBK；显式 UTF-8，避免中文结论乱码
    # （GitHub Actions 的 pwsh 与本地终端都能正常显示）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def main() -> int:
    """等价于 `python -m agent.serve`；``--caps`` 走冻结包能力门禁。"""
    _configure_frozen_paths()
    if "--caps" in sys.argv[1:]:
        return _caps_gate()

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

