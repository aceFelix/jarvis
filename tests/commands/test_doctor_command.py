"""`/doctor` 命令处理器的回归测。

存在的理由很具体：2026-11-05 用户在 REPL 里打 `/doctor`，直接 NameError 把整个会话
打崩——`core_commands._doctor()` 用了 `Path`，而这个模块从来没导入过它（main 拆分那次
重构把导入弄丢了）。查下来真正的缺口不是那行代码，而是**全仓没有任何用例执行过
`_doctor` 的函数体**：`tests/serve/engine/test_slash.py` 只断言了「/doctor 会被派发到
handle_doctor」，派发完里面炸什么都不管。

所以本文件的立场是：把"函数体真跑一遍、而且跑到最后一张表"变成契约。以后谁再动这个
函数（包括删导入、改字段名、加新的渲染分支），这里当场红。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.commands.handlers import core_commands
from agent.config.settings import Settings


class _RecordingUI:
    """只记录 info 文本的假 UI（真 RichCLI 会往终端画东西，测里不需要）。"""

    def __init__(self) -> None:
        self.infos: list[str] = []

    def info(self, msg: object) -> None:
        self.infos.append(str(msg))


class _FakeProvider:
    """够用的 provider 替身：`_doctor` 只问思考模式开没开。"""

    def is_thinking_enabled(self) -> bool:
        return True


@pytest.fixture()
def rendered(monkeypatch: pytest.MonkeyPatch):
    """把 render_table / render_panel 换成记录器。

    @return (标题 → 行列表, 面板标题列表)
    """
    tables: dict[str, list] = {}
    panels: list[str] = []

    def _fake_table(rows, headers=None, title=""):  # noqa: ANN001
        tables[title] = rows

    def _fake_panel(content, title=""):  # noqa: ANN001
        panels.append(title)

    monkeypatch.setattr(core_commands, "render_table", _fake_table)
    monkeypatch.setattr(core_commands, "render_panel", _fake_panel)
    return tables, panels


def _run_doctor(ui: _RecordingUI) -> None:
    """按生产入口的调用形状跑一遍 `_doctor`。"""
    core_commands._doctor(ui, Settings(), _FakeProvider(), "test-model", [])


def test_doctor_reaches_the_config_table(rendered) -> None:
    """崩溃点就卡在「配置」这张表之前：能出这张表，说明 Path 那几行真的执行过了。

    这条是本次事故的最小复现——它红，等价于 `/doctor` 又在 REPL 里崩。
    """
    tables, _panels = rendered
    _run_doctor(_RecordingUI())

    assert "配置" in tables, f"没渲染到配置表（说明函数体中途炸了），实际渲染：{sorted(tables)}"
    assert [row[0] for row in tables["配置"]] == ["用户配置", "模型配置", "API key", "Base URL"]


def test_doctor_renders_every_section_in_order(rendered) -> None:
    """六张表都要出：环境信息 / Provider 状态 / 配置 / 当前会话，外加自愈统计。

    顺序也钉住——诊断面板的价值在于稳定可读，两次输出对不上就没人再看它。
    """
    tables, _panels = rendered
    _run_doctor(_RecordingUI())

    for title in ("环境信息", "Provider 状态", "配置", "当前会话"):
        assert title in tables, f"缺了「{title}」这一节"


def test_doctor_env_rows_report_the_running_model(rendered) -> None:
    """模型与 workdir 取自调用现场而不是配置默认值：切过型之后这里必须是新模型。"""
    tables, _panels = rendered
    core_commands._doctor(_RecordingUI(), Settings(), _FakeProvider(), "deepseek-flash", [])

    env = {row[0]: row[1] for row in tables["环境信息"]}
    assert env["Model"] == "deepseek-flash"
    assert env["Provider"] == "mock"  # Settings() 默认 provider
    assert env["Debug"] == "off"


def test_doctor_provider_row_names_the_real_class(rendered) -> None:
    """「Provider 类」念的是真实类型名，排查"到底装配成了哪一家"全靠这一行。"""
    tables, _panels = rendered
    _run_doctor(_RecordingUI())

    prov = {row[0]: row[1] for row in tables["Provider 状态"]}
    assert prov["Provider 类"] == "_FakeProvider"
    assert prov["思考模式"] == "on"


def test_doctor_survives_empty_messages_and_records_session_size(rendered) -> None:
    """没有历史消息时也要正常收尾，并把消息数如实报成 0。"""
    tables, _panels = rendered
    _run_doctor(_RecordingUI())

    sess = {row[0]: row[1] for row in tables["当前会话"]}
    assert sess["消息数"] == "0"


def test_handle_doctor_entry_does_not_raise(rendered) -> None:
    """走 handle_doctor 这条真实入口（REPL 与 serve 都经它），异常一律不许外抛。"""
    ctx = SimpleNamespace(
        ui=_RecordingUI(),
        settings=Settings(),
        provider=_FakeProvider(),
        model="test-model",
        messages=[],
    )
    assert core_commands.handle_doctor(ctx, "/doctor") is True


def test_module_imports_path_at_top_level() -> None:
    """结构约束：`Path` 必须是模块级导入，不许再退回"用的时候再说"。

    这条测看着像凑数，其实是本次根因的直接对偶——局部导入/忘记导入之所以能潜伏，
    就是因为没人检查模块级命名空间。CI 的 `ruff check --select F821` 也覆盖同一件事，
    这里再钉一层，是为了让本地跑测的人不看 CI 也能发现。
    """
    import agent.commands.handlers.core_commands as mod

    assert mod.Path is not None, "Path 不再是模块级名字，_doctor 会重新 NameError"
