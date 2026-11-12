"""文件层禁改：拦"绕开 ConfigSet 直接改配置文件"。

2026-11 实测的缝：禁改清单只挂在 ConfigSet 上，模型改用 FileEdit 直接改
`models.toml` 时**没有任何一层**看它改的是哪个键。它当时自觉没绕，但那是规矩不是护栏。

判据：目标必须是配置文件，**且**要写的键命中禁改清单（与 config_forbidden 同源）。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.core.result import PermissionBehavior, ToolResult
from agent.core.tool import Tool
from agent.permissions.checker import PermissionChecker
from agent.permissions.modes import PermissionMode


class _Tool(Tool):
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, name: str = "FileEdit", irreversible: bool = True) -> None:
        self.name = name
        self.description = name
        self._irr = irreversible

    def is_irreversible(self, args: dict[str, Any]) -> bool:
        return self._irr

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    async def call(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        return ToolResult.ok("noop")


def _ctx(tmp_path: Path, perm_file: str = "") -> Any:
    return SimpleNamespace(
        workdir=str(tmp_path),
        permission_mode="yolo",
        extra={},
        settings=SimpleNamespace(
            permissions_file=perm_file,
            permissions_yolo_guard_roots=["c:\\"],
            permissions_yolo_hard_deny=[],
        ),
    )


def _check(tool: _Tool, args: dict[str, Any], ctx: Any, mode: PermissionMode) -> Any:
    return PermissionChecker(mode=mode).check(tool, args, ctx)


class TestConfigFileWrites:
    def test_yolo_cannot_edit_api_key_via_fileedit(self, tmp_path: Path):
        """这条是核心：yolo 也不许**绕过 ConfigSet** 改凭据。"""
        target = tmp_path / "models.toml"
        r = _check(
            _Tool("FileEdit"),
            {"file_path": str(target), "new_string": 'api_key = "sk-newvalue123456"\n'},
            _ctx(tmp_path),
            PermissionMode.YOLO,
        )
        assert r.behavior == PermissionBehavior.DENY
        assert "api_key" in r.reason

    def test_yolo_cannot_edit_permissions_guard_roots(self, tmp_path: Path):
        r = _check(
            _Tool("FileEdit"),
            {"file_path": str(tmp_path / "settings.toml"),
             "new_string": "permissions_yolo_guard_roots = []\n"},
            _ctx(tmp_path),
            PermissionMode.YOLO,
        )
        assert r.behavior == PermissionBehavior.DENY

    def test_yolo_cannot_write_sandbox_keys(self, tmp_path: Path):
        r = _check(
            _Tool("FileWrite"),
            {"file_path": str(tmp_path / "settings.toml"),
             "content": "[sandbox]\nenabled = false\n"},
            _ctx(tmp_path),
            PermissionMode.YOLO,
        )
        assert r.behavior == PermissionBehavior.DENY

    def test_yolo_cannot_touch_permissions_yaml(self, tmp_path: Path):
        yaml = tmp_path / "permissions.yaml"
        r = _check(
            _Tool("FileWrite"),
            {"file_path": str(yaml), "content": "rules: []\n"},
            _ctx(tmp_path, perm_file=str(yaml)),
            PermissionMode.YOLO,
        )
        assert r.behavior == PermissionBehavior.DENY
        assert "权限规则文件" in r.reason

    def test_yolo_cannot_shell_rewrite_config(self, tmp_path: Path):
        r = _check(
            _Tool("Bash"),
            {"command": "sed -i 's/x/y/' ~/.jarvis/settings.toml"},
            _ctx(tmp_path),
            PermissionMode.YOLO,
        )
        assert r.behavior == PermissionBehavior.DENY


class TestStillAllowsOrdinaryEdits:
    """护栏不能宽到把正常干活也拦住——那会逼模型去绕。"""

    def test_editing_non_config_file_is_untouched(self, tmp_path: Path):
        r = _check(
            _Tool("FileEdit"),
            {"file_path": str(tmp_path / "app.py"), "new_string": "api_key = 'x'\n"},
            _ctx(tmp_path),
            PermissionMode.YOLO,
        )
        assert r.behavior == PermissionBehavior.ALLOW

    def test_editing_harmless_config_key_is_untouched(self, tmp_path: Path):
        """settings.toml 里的非护栏项（音量）照常可改。"""
        r = _check(
            _Tool("FileEdit"),
            {"file_path": str(tmp_path / "settings.toml"), "new_string": "[tts]\nvolume = 40\n"},
            _ctx(tmp_path),
            PermissionMode.YOLO,
        )
        assert r.behavior == PermissionBehavior.ALLOW

    def test_reading_config_is_untouched(self, tmp_path: Path):
        r = _check(
            _Tool("FileRead"),
            {"file_path": str(tmp_path / "models.toml")},
            _ctx(tmp_path),
            PermissionMode.YOLO,
        )
        assert r.behavior == PermissionBehavior.ALLOW
