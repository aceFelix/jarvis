"""语音分层导入守卫（2026-11-26）。

锁定 agent/audio ← agent/voice + agent/talk 的单向依赖，防止后续开发把两条链路
重新耦合回去；同时防止已删除的旧 voice/realtime_* 与共享模块路径回潮。

@author aceFelix
@since 2026-11-26
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = REPO_ROOT / "agent"


def _imports(path: Path) -> set[str]:
    """提取一个 Python 文件的绝对导入模块名（含 from-import 的模块部分）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _package_imports(package: str) -> set[str]:
    """汇总一个包内全部模块的导入（相对导入按包内模块处理，不跨包）。"""
    names: set[str] = set()
    for path in (AGENT / package).rglob("*.py"):
        for name in _imports(path):
            if name.startswith("agent."):
                names.add(name)
    return names


def test_audio_does_not_import_voice_or_talk() -> None:
    """基础设施层不能反向依赖两条业务链路。"""
    imports = _package_imports("audio")
    assert not any(n.startswith(("agent.voice", "agent.talk")) for n in imports)


def test_voice_does_not_import_talk() -> None:
    """/voice 半双工管线不能依赖 /talk 实时链路。"""
    imports = _package_imports("voice")
    assert not any(n.startswith("agent.talk") for n in imports)


def test_talk_does_not_import_voice() -> None:
    """/talk 实时链路不能依赖 /voice 半双工管线。"""
    imports = _package_imports("talk")
    assert not any(n.startswith("agent.voice") for n in imports)


@pytest.mark.parametrize(
    "name",
    [
        "audio.py",
        "aec.py",
        "voice_state.py",
        "voice_providers.py",
        "client_vad.py",
        "realtime_engine.py",
        "realtime_talk.py",
        "talk_history.py",
    ],
)
def test_old_voice_paths_are_gone(name: str) -> None:
    """已迁移/删除的旧路径不得残留，避免出现两份实现。"""
    assert not (AGENT / "voice" / name).exists()


def test_new_package_markers_exist() -> None:
    """两个新包必须有 __init__.py，否则 setuptools/PyInstaller 可能漏收。"""
    assert (AGENT / "audio" / "__init__.py").is_file()
    assert (AGENT / "talk" / "__init__.py").is_file()
