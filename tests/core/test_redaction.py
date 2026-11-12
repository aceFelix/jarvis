"""密钥脱敏：读的时候糊掉，而不是靠模型自觉不回显。

背景（2026-11 实测）：用户问某个模型的 api_key 在哪，jarvis `grep` 了 models.toml，
两把真 key 原样进了工具结果 → 上下文 → 会话日志。它事后知道"回复里别复述"，但工具
输出已经进去了。所以这条必须在**读取端**做，不能靠礼貌。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.core.redaction import has_secret, redact_secrets


class TestUnit:
    @pytest.mark.parametrize("text", [
        'api_key = "sk-163c60988b024357b9307ee3fedd00f1"',
        '"token": "ghp_abcdefghijklmnopqrstuvwxyz0123"',
        "default_api_key = \"sk-163c60988b024357b9307ee3fedd00f1\"",   # 前缀下划线
        "password = 'hunter2'",
        'api_key: sk-abcdefghijklmno',
    ])
    def test_masks_real_looking_secrets(self, text: str):
        out = redact_secrets(text)
        assert "***" in out
        assert has_secret(text)

    @pytest.mark.parametrize("text", [
        'api_key = "test"',           # 占位
        'api_key = "已设置"',          # 中文说明
        'api_key = ""',               # 空
        "见 ~/.jarvis/models.toml 顶端，api_key 在 88 行",   # 散文
        "token_count = 12345",        # 不是凭据键
        "",
    ])
    def test_leaves_non_secrets_alone(self, text: str):
        assert redact_secrets(text) == text

    def test_idempotent(self):
        once = redact_secrets('api_key = "sk-abcdefghijklmno"')
        assert redact_secrets(once) == once

    def test_keeps_key_name_and_length(self):
        """要保住"设过了没有、多长"这两个判断依据，否则模型没法排查配置。"""
        out = redact_secrets('api_key = "sk-163c60988b024357b9307ee3fedd00f1"')
        assert "api_key" in out
        assert "35 位" in out


class TestToolIntegration:
    """真实工具的输出必须已经是糊过的——这是用户实际看到的那一层。"""

    def _ctx(self, tmp_path: Path):
        from agent.core.context import ToolContext

        return ToolContext(workdir=str(tmp_path), messages=[])

    def test_file_read_masks_secret(self, tmp_path: Path):
        from agent.tools.file_ops.file_read import FileReadTool

        f = tmp_path / "models.toml"
        f.write_text('api_key = "sk-abcdefghijklmnopqrst"\n', encoding="utf-8")
        res = asyncio.run(FileReadTool().call({"file_path": str(f)}, self._ctx(tmp_path)))
        text = str(res.data)
        assert "sk-abcdefghijklmnopqrst" not in text
        assert "***" in text

    def test_grep_masks_secret(self, tmp_path: Path):
        from agent.tools.file_ops.grep import GrepTool

        f = tmp_path / "a.toml"
        f.write_text('api_key = "sk-abcdefghijklmnopqrst"\n', encoding="utf-8")
        res = asyncio.run(
            GrepTool().call({"pattern": "api_key", "path": str(tmp_path)}, self._ctx(tmp_path))
        )
        text = str(res.data)
        assert "sk-abcdefghijklmnopqrst" not in text
        assert "***" in text
