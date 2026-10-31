"""/config list 与 /config set（阶段 12 P2）：终端入口的可运行契约。

为什么单独一份测试：`/config set` 与模型侧 `ConfigSet` 共用
`agent/config/config_ops.apply_config_patch`，但**入口语义不同**，差异点必须钉住：

| 差异 | 模型侧 | 终端 |
|---|---|---|
| `confirm` 项 | W1 管线弹 ASK | 两段式：先预览，再加 `--yes` |
| `forbidden` 项 | 权限层 DENY | 直接 warn + 指向手改 |
| 值来自 | JSON（原生类型） | 一行文本，要按 kind 解析 |

测试一律 `patch.object(Path, "home", ...)` 重定向并换掉副作用账本，不碰真实配置。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.commands.handlers.config_commands import handle_config_show
from agent.config.config_ops import parse_cli_value, render_cli_value
from agent.config.config_specs import CONFIG_SPECS, TIER_FORBIDDEN, get_spec
from agent.config.settings import Settings
from agent.core.actions import journal as journal_mod
from agent.core.actions.journal import configure_journal


class _FakeUI:
    """只记 info/warn 两条通道的替身（Rich 转义之后就是纯文本）。

    `_console` 是给 `/config` 无参数时那张全量自查面板留的：那里直接走
    ``ui._console.print``（表格渲染），替身给个空 shell 就行——本文件不关心
    那张表的样式，只关心它没被误当成错命令。
    """

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []
        self.printed: list[str] = []
        self._console = SimpleNamespace(print=self._record_print)

    def _record_print(self, *args: object, **kwargs: object) -> None:
        self.printed.append(" ".join(str(a) for a in args))

    def info(self, text: str) -> None:
        self.infos.append(text)

    def warn(self, text: str) -> None:
        self.warns.append(text)

    @property
    def all_text(self) -> str:
        return "\n".join(self.infos + self.warns)


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    """临时 home + 临时账本；用例结束恢复账本配置。"""
    configure_journal(path=tmp_path / "actions.jsonl")
    yield
    configure_journal(enabled=False)


@pytest.fixture
def ctx(tmp_path):
    """最小 CommandContext：settings + 假 UI + workdir。"""
    settings = Settings()
    ui = _FakeUI()
    ctx = SimpleNamespace(
        ui=ui,
        settings=settings,
        provider=None,
        mcp_client=None,
        session_name="probe",
        ctx=SimpleNamespace(workdir=str(tmp_path)),
    )
    return ctx


def _run(ctx, stripped: str) -> _FakeUI:
    """跑一条 /config 命令（home 重定向到临时目录）。"""
    with patch.object(Path, "home", return_value=Path(ctx.ctx.workdir)):
        handle_config_show(ctx, stripped)
    return ctx.ui


# ---------------------------------------------------------------------------
# 值解析 / 回显
# ---------------------------------------------------------------------------


class TestParseCliValue:
    """终端文本 → 原生值。只做类型转换，范围判断归真源表。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [("true", True), ("false", False), ("开", True), ("关", False),
         ("ON", True), ("1", True), ("0", False), ("yes", True), ("否", False)],
    )
    def test_bool_accepts_human_spellings(self, raw, expected):
        assert parse_cli_value(get_spec("window_enabled"), raw) is expected

    def test_bool_garbage_passes_through_for_one_error_source(self):
        """转不动就原样返回，让 validate_value 统一报"必须是布尔值"。"""
        assert parse_cli_value(get_spec("window_enabled"), "maybe") == "maybe"

    def test_numbers(self):
        assert parse_cli_value(get_spec("tts_volume"), "30") == 30
        assert parse_cli_value(get_spec("tts_speech_rate"), "1.25") == 1.25
        assert parse_cli_value(get_spec("tts_volume"), "abc") == "abc"

    def test_list_splits_and_strips(self):
        assert parse_cli_value(get_spec("vcs_repos"), " E:/a , E:/b ") == ["E:/a", "E:/b"]
        assert parse_cli_value(get_spec("vcs_repos"), "E:/a;E:/b") == ["E:/a", "E:/b"]
        assert parse_cli_value(get_spec("vcs_repos"), "") == []

    def test_time_and_quiet_untouched(self):
        assert parse_cli_value(get_spec("briefing_time"), "07:15") == "07:15"
        assert parse_cli_value(get_spec("quiet_hours"), "23:00-07:00") == "23:00-07:00"

    def test_render_is_the_inverse(self):
        """回显能再解析回来——预览里的"确认请再执行"才真的能照抄。"""
        for key, value in [
            ("tts_volume", 30), ("tts_speech_rate", 1.25), ("window_enabled", True),
            ("briefing_time", "07:15"), ("vcs_repos", ["E:/a", "E:/b"]),
            ("vcs_repos", []),
        ]:
            spec = get_spec(key)
            assert parse_cli_value(spec, render_cli_value(spec, value)) == value


# ---------------------------------------------------------------------------
# /config list
# ---------------------------------------------------------------------------


class TestConfigList:
    def test_lists_writable_items_only(self, ctx):
        """/config list 与模型侧同一份渲染：可写项全列、禁改项零出现。

        数量断言从真源表派生而不是写死数字：写死的代价是每加一条配置就得改测试，
        而真正要守的不变量是“列出的集合 == 真源表里可写的集合”，这条断言恰好把
        它守住了（少渲染一条也会抱）。
        """
        _run(ctx, "/config list")
        text = ctx.ui.all_text
        writable = [s for s in CONFIG_SPECS if s.tier != TIER_FORBIDDEN]
        assert f"可调整的配置（{len(writable)} 项）" in text
        for spec in writable:
            assert spec.key in text, f"{spec.key} 该出现在清单里"
        assert "tts_volume" in text and "daily_max" in text
        # 禁改项（沙箱/凭据/留痕）连名字都不给
        for key in ("sandbox_enabled", "api_key", "log_events"):
            assert key not in text

    def test_marks_confirm_items(self, ctx):
        _run(ctx, "/config list")
        line = next(x for x in ctx.ui.infos[0].splitlines() if "daily_max" in x)
        assert "需确认" in line

    def test_mentions_how_to_set(self, ctx):
        _run(ctx, "/config list")
        assert "/config set" in ctx.ui.all_text


# ---------------------------------------------------------------------------
# /config set：auto 档直接改
# ---------------------------------------------------------------------------


class TestConfigSetAuto:
    def test_changes_value_persists_and_records(self, ctx, tmp_path):
        ui = _run(ctx, "/config set tts_volume 30")
        assert ctx.settings.tts_volume == 30
        assert "已改 1 项" in ui.infos[-1] and "50 → 30" in ui.infos[-1]
        # 落盘
        toml_text = (Path(ctx.ctx.workdir) / ".jarvis" / "settings.toml").read_text(encoding="utf-8")
        assert "volume = 30" in toml_text
        # 进账本且可回退
        rec = journal_mod.get_journal().recent(1)[0]
        assert rec.tool == "ConfigSet"
        assert rec.args.get("entry") == "terminal"
        assert rec.undo_spec["before"] == {"tts_volume": 50}

    def test_noop_says_so_and_does_not_record(self, ctx):
        _run(ctx, "/config set tts_volume 30")
        before = len(journal_mod.get_journal().recent(20))
        ui = _run(ctx, "/config set tts_volume 30")
        assert "本来就是这个值" in ui.infos[-1]
        assert len(journal_mod.get_journal().recent(20)) == before

    def test_out_of_range_is_rejected_before_writing(self, ctx):
        ui = _run(ctx, "/config set tts_volume 999")
        assert ui.warns and "不得大于 100" in ui.warns[-1]
        assert ctx.settings.tts_volume == 50
        assert not (Path(ctx.ctx.workdir) / ".jarvis" / "settings.toml").exists()

    def test_unknown_key(self, ctx):
        ui = _run(ctx, "/config set nonsense 1")
        assert "没有 nonsense 这个配置项" in ui.warns[0]
        assert "/config list" in ui.all_text

    def test_existing_but_unmanaged_key_points_to_file(self, ctx):
        """存在但没进真源表的项：必须说“存在、只能改文件”，不是“不认识”。

        这是两类完全不同的实情。旧实现一律回“不认识的配置项”，用户会以为自己
        拼错了，然后去两百多行 TOML 里白找一场。
        """
        ui = _run(ctx, "/config set smtp_host smtp.163.com")
        text = ui.all_text
        assert "不认识的配置项" not in text
        assert "smtp_host" in text
        assert "[email]" in text  # 指出它在哪个段
        assert "settings.toml" in text  # 指出去哪个文件改
        assert not ui.warns  # 不是错误，是说明

    def test_truly_absent_key_is_reported_as_absent(self, ctx):
        """目录里也没有的名字，仍然要如实说“没有”——不能因为加了目录就什么都不算错。"""
        ui = _run(ctx, "/config set zzz_not_a_real_knob 1")
        assert "没有 zzz_not_a_real_knob 这个配置项" in ui.all_text

    def test_unknown_subcommand_shows_usage(self, ctx):
        ui = _run(ctx, "/config bogus")
        assert "不认识的子命令 bogus" in ui.warns[0]
        assert "/config set <键> <值>" in ui.all_text

    def test_bare_config_still_shows_full_panel(self, ctx):
        """/config（无参数）保持老行为：全量自查面板而不是报错。"""
        ui = _run(ctx, "/config")
        assert ui.warns == []                       # 没当成错命令
        assert any("当前生效配置" in line for line in ui.printed)

    def test_show_alias_behaves_the_same(self, ctx):
        for cmd in ("/config", "/config show"):
            assert _run(ctx, cmd).warns == []


# ---------------------------------------------------------------------------
# /config set：confirm 档两段式
# ---------------------------------------------------------------------------


class TestConfigSetConfirm:
    def test_first_pass_only_previews(self, ctx):
        ui = _run(ctx, "/config set daily_max 20")
        assert ctx.settings.notify_daily_max == 10          # 一步都没动
        assert "需要确认" in ui.infos[-1]
        assert "10 → 20" in ui.infos[-1]
        assert "为什么要问" in ui.infos[-1]
        assert "/config set daily_max 20 --yes" in ui.infos[-1]

    def test_second_pass_with_yes_applies(self, ctx):
        _run(ctx, "/config set daily_max 20")
        ui = _run(ctx, "/config set daily_max 20 --yes")
        assert ctx.settings.notify_daily_max == 20
        assert "已改 1 项" in ui.infos[-1]

    def test_short_flag_works_too(self, ctx):
        _run(ctx, "/config set daily_max 20 -y")
        assert ctx.settings.notify_daily_max == 20

    def test_preview_echoes_normalized_value(self, ctx):
        """预览里回显的是规范化后的值，不是脏原始串（照抄回去要干净）。"""
        ui = _run(ctx, "/config set window_exclude_apps A, B ,A")
        assert "/config set window_exclude_apps A, B --yes" in ui.infos[-1]

    def test_list_value_applies_after_confirmation(self, ctx):
        _run(ctx, "/config set window_exclude_apps 1Password, KeePass --yes")
        assert ctx.settings.percept_window_exclude_apps == ["1Password", "KeePass"]


# ---------------------------------------------------------------------------
# /config set：禁改与护栏
# ---------------------------------------------------------------------------


class TestConfigSetForbidden:
    def test_forbidden_is_refused_with_reason(self, ctx):
        ctx.settings.sandbox_enabled = True          # 出厂是 False，先置上才看得出"没被动"
        ui = _run(ctx, "/config set sandbox_enabled false")
        assert ctx.settings.sandbox_enabled is True
        assert "禁改项" in ui.warns[0] and "提权" in ui.warns[0]
        assert "settings.toml" in ui.all_text

    def test_forbidden_ignores_yes_flag(self, ctx):
        """护栏是配置本身的属性：加 --yes 也不能绕过去。"""
        ctx.settings.sandbox_enabled = True
        _run(ctx, "/config set sandbox_enabled false --yes")
        assert ctx.settings.sandbox_enabled is True

    def test_log_events_is_refused_though_panel_allows_it(self, ctx):
        """`log_events` 面板能改、命令行与模型都不能改（审计线）。"""
        ui = _run(ctx, "/config set log_events false --yes")
        assert ctx.settings.percept_log_events is True
        assert "禁改项" in ui.warns[0]


# ---------------------------------------------------------------------------
# /config set：用法与边界
# ---------------------------------------------------------------------------


class TestConfigSetUsage:
    def test_missing_value_shows_usage(self, ctx):
        ui = _run(ctx, "/config set tts_volume")
        assert "/config set <键> <值>" in ui.all_text

    def test_list_needs_explicit_empty_marker(self, ctx):
        """裸写一个键不能就把名单清空——太容易误伤。"""
        ui = _run(ctx, "/config set vcs_repos")
        assert '要清空清单请写' in ui.infos[-1]
        assert ctx.settings.percept_vcs_repos == []

    def test_explicit_empty_marker_clears(self, ctx):
        _run(ctx, "/config set vcs_repos E:/repo --yes")
        assert ctx.settings.percept_vcs_repos == ["E:/repo"]
        _run(ctx, '/config set vcs_repos "" --yes')
        assert ctx.settings.percept_vcs_repos == []

    def test_empty_body_prints_usage(self, ctx):
        ui = _run(ctx, "/config set")
        assert "/config list" in ui.all_text


# ---------------------------------------------------------------------------
# 两个入口共用同一条路径
# ---------------------------------------------------------------------------


def test_both_entries_share_one_write_path(ctx):
    """模型工具与终端命令改同一项，落盘/账本/广播形状完全一致。

    这条挡的是"两处各写一份"的老路：以前谁新增一步（比如广播）就得记得改两遍。
    """
    import asyncio

    from agent.tools.config_tool import ConfigSetTool

    tool_ctx = SimpleNamespace(
        settings=ctx.settings, workdir=str(ctx.ctx.workdir),
        permission_mode="default", session_name="probe", extra={},
    )
    async def _call():
        return await ConfigSetTool().call({"patch": {"tts_volume": 70}}, tool_ctx)

    res = asyncio.run(_call())
    assert res.is_error is False and "60 → 70" not in res.data  # 当前是 50
    assert "50 → 70" in res.data

    ui = _run(ctx, "/config set tts_voice_junk 1")  # 一个必然失败的调用
    assert ui.warns  # 只是确认终端路径也走同一套校验

    rec = journal_mod.get_journal().recent(1)[0]
    assert rec.args.get("entry") == "model"     # 最近一条来自工具
    assert rec.undo_spec["before"] == {"tts_volume": 50}
