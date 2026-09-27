"""cli_anything runner / 工具封装的回归测试。

覆盖场景（WPS harness 传参死循环复盘）：
- 位置参数值含空格时自动拆成多个 token（"writer --help" → ["writer", "--help"]）
- 含引号的位置参数值用 shlex 解析，保留带空格的单个值
- argparse 类失败回执附加「正确传参格式」提示，引导模型自我纠正

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.cli_anything.runner import _build_args
from agent.cli_anything.schema import Harness, HarnessArg
from agent.core.context import ToolContext
from agent.tools.extensions.cli_anything_tool import (
    CliAnythingTool,
    _build_usage_hint,
)


def _wps_like_harness() -> Harness:
    """构造一个与 wps SKILL.md 等价的 Harness：subcommand 位置参数 + action 等普通参数。"""
    return Harness(
        id="wps",
        name="WPS Office",
        description="COM 自动化控制 WPS",
        command="jarvis-harness-wps",
        args=[
            HarnessArg(
                name="subcommand",
                type="string",
                description="子命令",
                required=True,
                enum=["writer", "sheet", "slide", "status", "version"],
                positional=True,
            ),
            HarnessArg(name="action", type="string", description="操作类型"),
            HarnessArg(name="target", type="string", description="操作对象"),
            HarnessArg(name="headless", type="boolean", description="后台操作"),
        ],
        dir_path=Path("C:/fake/wps"),
    )


class TestBuildArgsPositionalSplit:
    """位置参数含空格时的自动拆分行为。"""

    def test_single_token_positional_unchanged(self) -> None:
        """正常传法：subcommand="writer" 原样传单 token。"""
        h = _wps_like_harness()
        result = _build_args(h, {"subcommand": "writer", "action": "new_doc"})
        assert result[0] == "writer"
        assert result[1:] == ["--action", "new_doc"]

    def test_space_separated_positional_split(self) -> None:
        """LLM 把整串命令塞进 subcommand：无引号时按空白拆分。"""
        h = _wps_like_harness()
        result = _build_args(h, {"subcommand": "writer --help"})
        assert result == ["writer", "--help"]

    def test_quoted_positional_value_preserved(self) -> None:
        """含引号的值按引号段切分并去引号，引号内的空格不拆（文件路径场景）。"""
        h = _wps_like_harness()
        result = _build_args(h, {"subcommand": 'writer "C:\\my docs\\a.docx"'})
        assert result == ["writer", "C:\\my docs\\a.docx"]

    def test_non_positional_value_not_split(self) -> None:
        """非位置参数（如 target 路径含空格）保持单 token，不受拆分影响。"""
        h = _wps_like_harness()
        result = _build_args(h, {"subcommand": "writer", "target": "C:\\my docs\\a.docx"})
        assert result == ["writer", "--target", "C:\\my docs\\a.docx"]

    def test_boolean_flag_still_works(self) -> None:
        """布尔参数行为回归：true 传 flag，false 忽略。"""
        h = _wps_like_harness()
        assert "--headless" in _build_args(h, {"subcommand": "status", "headless": True})
        assert "--headless" not in _build_args(h, {"subcommand": "status", "headless": False})


class TestUsageHint:
    """失败回执的正确传参格式提示生成。"""

    def test_hint_contains_enum_and_separator_note(self) -> None:
        """提示应包含位置参数枚举与「分开传」说明。"""
        h = _wps_like_harness()
        hint = _build_usage_hint(h)
        assert "writer | sheet | slide" in hint
        assert "不要拼进位置参数字符串" in hint

    def test_hint_empty_without_positional(self) -> None:
        """没有位置参数的 harness 不生成提示（无此痛点）。"""
        h = Harness(
            id="x", name="X", description="", command="x",
            args=[HarnessArg(name="action", type="string", description="")],
        )
        assert _build_usage_hint(h) == ""


class TestToolErrorHint:
    """工具执行失败时回执附加提示。"""

    @pytest.mark.asyncio
    async def test_argparse_error_appends_hint(self, monkeypatch) -> None:
        """argparse invalid choice 类报错：回执末尾附正确传参格式。"""
        from agent.tools.extensions import cli_anything_tool as mod

        async def fake_run(harness, kwargs, *, timeout, workdir=""):
            return {
                "stdout": "",
                "stderr": "jarvis-harness-wps: error: argument command: invalid choice: 'writer --help'",
                "exit_code": 2,
                "error": "",
            }

        monkeypatch.setattr(mod, "run_harness", fake_run)
        tool = CliAnythingTool(_wps_like_harness())
        ctx = ToolContext(workdir=".", messages=[])
        result = await tool.call({"subcommand": "writer --help"}, ctx)
        assert result.is_error
        assert "正确传参格式" in result.data
        assert "writer | sheet | slide" in result.data

    @pytest.mark.asyncio
    async def test_business_error_no_hint(self, monkeypatch) -> None:
        """非参数类错误（COM 业务失败）不附加传参提示，避免误导。"""
        from agent.tools.extensions import cli_anything_tool as mod

        async def fake_run(harness, kwargs, *, timeout, workdir=""):
            return {
                "stdout": '{"status": "error", "message": "COM 调用失败"}',
                "stderr": "",
                "exit_code": 0,
                "error": "",
            }

        monkeypatch.setattr(mod, "run_harness", fake_run)
        tool = CliAnythingTool(_wps_like_harness())
        ctx = ToolContext(workdir=".", messages=[])
        result = await tool.call({"subcommand": "writer", "action": "info"}, ctx)
        assert not result.is_error
        assert "正确传参格式" not in result.data
