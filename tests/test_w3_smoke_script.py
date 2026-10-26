"""W3 一键走查脚本自身的测试（阶段 10 收尾切片）。

脚本是给人实机看的东西，一旦它自己坏了，走查清单就会指着假象说"通过"。所以按
`docs/../testing` 规则里 CLI/脚本的最低要求做**命令执行测试**：真起子进程跑一遍，
看退出码与结论行。全程 ~2 秒（serve 段只起一台本机 WS 服务器，不调 LLM）。

刻意跑真子进程而不是 import 函数：脚本的隔离方式（把台账/账本指向临时目录、
结束复位单例）只有在干净进程里才算验到。

@author aceFelix
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "smoke_w3_spine.py"


def _run(*extra_args: str) -> str:
    """跑脚本并返回 stdout+stderr；非 0 退出码直接把输出甩进断言消息。"""
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT), *extra_args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, cwd=str(REPO_ROOT),
    )
    blob = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"退出码 {proc.returncode}\n{blob}"
    return blob


def test_smoke_script_full_run_passes():
    """默认全跑（含 serve WS 端到端）：结论行必须是「全部通过」且一行失败都没有。"""
    blob = _run()
    assert "全部通过" in blob, blob[-600:]
    assert "[失败]" not in blob, blob


def test_smoke_script_covers_three_entrances():
    """三段入口都要真的出现在输出里，缺一段说明脚本被改窄了。"""
    blob = _run()
    for marker in ("入口一：终端 /tasks add", "入口二：TrackGoal 工具", "入口三：serve tasks.exec"):
        assert marker in blob, marker
    # W3.2 的两条不变量也必须在这一次跑里被看见（用户最关心的"敢放手"）
    assert "延时窗口：不可逆动作被扣住" in blob
    assert "绕道硬拦：窗口未结案时" in blob


def test_smoke_script_no_serve_mode():
    """``--no-serve``：只跑进程内部分也要全绿（缺 websockets 的机器靠这条路）。"""
    blob = _run("--no-serve")
    assert "全部通过" in blob, blob[-600:]
    assert "入口三：serve tasks.exec" not in blob


def test_smoke_script_runs_in_temp_ledger_only():
    """脚本只写临时目录，跑完把临时目录删掉：不许碰用户真实 ~/.jarvis/goals。"""
    blob = _run()
    match = re.search(r"临时台账目录：(.+)", blob)
    assert match, "首行必须打出临时台账目录，便于排查"
    assert not Path(match.group(1).strip()).exists(), "临时目录应随脚本结束清理"
