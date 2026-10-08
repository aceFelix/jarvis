"""settings [checkpoint] 配置节解析测试。

覆盖：默认值、项目级 TOML 映射、用户级覆盖、非法表结构容错、
settings.example.toml 模板与字段名同步。

@author aceFelix
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from agent.config.settings import Settings, load_settings


def _load(tmp_path: Path, project_toml: str, user_toml: str = ""):
    work = tmp_path / "proj"
    cfgdir = work / "configs"
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "settings.toml").write_text(project_toml, encoding="utf-8")
    home = tmp_path / "home"
    (home / ".jarvis").mkdir(parents=True, exist_ok=True)
    if user_toml:
        (home / ".jarvis" / "settings.toml").write_text(user_toml, encoding="utf-8")
    with patch.object(Path, "home", return_value=home), patch.dict("os.environ", {}, clear=True):
        return load_settings(workdir=str(work))


def test_defaults():
    s = Settings()
    assert s.checkpoint_enabled is True
    assert s.checkpoint_max_per_session == 20
    assert s.checkpoint_timeout_seconds == 10


def test_project_table_mapping(tmp_path):
    s = _load(tmp_path, '[checkpoint]\nenabled = false\nmax_per_session = 5\ntimeout_seconds = 30\n')
    assert s.checkpoint_enabled is False
    assert s.checkpoint_max_per_session == 5
    assert s.checkpoint_timeout_seconds == 30


def test_user_overrides_project(tmp_path):
    s = _load(
        tmp_path,
        '[checkpoint]\nmax_per_session = 5\n',
        '[checkpoint]\nmax_per_session = 8\n',
    )
    assert s.checkpoint_max_per_session == 8


def test_partial_and_missing_table(tmp_path):
    """只配部分键 / 完全不配：其余键保持默认。"""
    s = _load(tmp_path, '[checkpoint]\nenabled = false\n')
    assert s.checkpoint_enabled is False
    assert s.checkpoint_max_per_session == 20
    s2 = _load(tmp_path, 'provider = "dashscope"\n')
    assert s2.checkpoint_enabled is True


def test_example_toml_covers_fields():
    """settings.example.toml 的 [checkpoint] 键与字段映射同步。"""
    example = Path(__file__).resolve().parents[2] / "agent" / "configs" / "settings.example.toml"
    data = tomllib.loads(example.read_text(encoding="utf-8"))
    table = data.get("checkpoint")
    assert isinstance(table, dict)
    assert set(table) == {"enabled", "max_per_session", "timeout_seconds"}
