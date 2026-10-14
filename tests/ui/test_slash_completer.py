"""终端斜杠命令补全器测试（agent/ui/slash_completer.py）。

该模块从 agent/ui/cli.py 拆出（RichCLI 超过单文件 800 行上限），本测试覆盖：
- tool_voice_desc 的已知/未知工具兜底
- _build_completion_list 的公共渲染（列宽、描述色、回退位置）
- SlashCompleter.get_completions 的六条子命令分派 + 默认命令/技能分支
- 配置加载失败时的兜底（补全绝不能把 REPL 拖崩）

断言只关心「候选文本 + 回退位置」这类行为契约，不锁定颜色字符串等
展示细节（除专门校验渲染参数的那两例）。

@author aceFelix
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

Document = pytest.importorskip("prompt_toolkit.document").Document

from agent.ui.slash_completer import (  # noqa: E402
    SLASH_COMMANDS,
    TOOL_VOICE_DESC,
    SlashCompleter,
    _build_completion_list,
    tool_voice_desc,
)

_COMPLETER = SlashCompleter()


def _docs(text: str) -> list[str]:
    """跑一次补全，取回候选文本列表（终端光标停在末尾）。"""
    doc = Document(text, len(text))
    return [c.text for c in _COMPLETER.get_completions(doc, None)]


# ---------- 语音描述 ----------

def test_tool_voice_desc_known_and_unknown():
    """已登记工具返回专属短语，未登记工具返回兜底短语（不能 KeyError）。"""
    assert tool_voice_desc("Bash") == TOOL_VOICE_DESC["Bash"]
    assert tool_voice_desc("NoSuchTool") == "正在处理"


def test_slash_commands_are_wellformed():
    """命令表是全 REPL 的对外契约：每条必须是 (以 / 开头的命令, 非空描述)。"""
    assert len(SLASH_COMMANDS) > 50
    for cmd, desc in SLASH_COMMANDS:
        assert cmd.startswith("/")
        assert desc.strip()


# ---------- 公共渲染 ----------

def test_build_completion_list_render_params():
    """名称按 width 左对齐补齐，描述前置两空格，回退位置抵消已输入前缀。"""
    results = _build_completion_list(
        [("plan", "只读规划")], "pl", width=10, desc_color="#aaaaaa"
    )
    assert len(results) == 1
    assert results[0].text == "plan"
    assert results[0].start_position == -2

    # display 是 FormattedText 的 (style, text) 分段
    segments = list(results[0].display)
    assert segments[0] == ("#5bc8ff", f"{'plan':<10}")
    assert segments[1] == ("#aaaaaa", "  只读规划")


def test_build_completion_list_empty_prefix_and_matches():
    """空前缀不回退（start_position=0）；空候选返回空列表而非 None。"""
    assert _build_completion_list([("yolo", "全自动")], "").pop().start_position == 0
    assert _build_completion_list([], "abc") == []


# ---------- 子命令分派 ----------

def test_non_slash_text_gets_no_completions():
    """普通对话文本不弹补全菜单。"""
    assert _docs("hello") == []
    assert _docs("  /exit") == [c for c in _docs("/exit")]  # 前导空格被 lstrip


def test_complete_modes():
    """/mode 前缀匹配权限模式，无参数时列出全部 4 种。"""
    assert _docs("/mode ") == ["default", "plan", "accept_edits", "yolo"]
    assert _docs("/mode ac") == ["accept_edits"]
    assert _docs("/mode zz") == []


def test_complete_thinking():
    """/think 档位补全与终端模糊匹配同口径（on/off + 三档强度）。"""
    assert _docs("/think ") == ["on", "off", "low", "medium", "high"]
    assert _docs("/think m") == ["medium"]


def test_default_branch_prefix_match():
    """默认分支按前缀过滤命令表。"""
    assert "/exit" in _docs("/ex")
    assert "/help" not in _docs("/ex")


def test_default_branch_includes_skills(monkeypatch):
    """技能也作为 /<skill-name> 参与补全。"""
    fake = [
        SimpleNamespace(name="pdf", description="PDF 工具"),
        SimpleNamespace(name="canvas", description=None),
    ]
    monkeypatch.setattr(
        "agent.core.extensions.skills.load_skills", lambda workdir: fake
    )
    results = _docs("/p")
    assert "/pdf" in results
    # 无描述的技能用占位文案，避免出现空描述行
    doc = Document("/pdf", 4)
    disp = list(_COMPLETER.get_completions(doc, None)[0].display)
    assert disp[1][1].strip()


def test_default_branch_survives_skill_load_error(monkeypatch):
    """技能目录读取失败时，内置命令补全必须照常返回。"""

    def _boom(workdir):
        raise RuntimeError("skills broken")

    monkeypatch.setattr("agent.core.extensions.skills.load_skills", _boom)
    assert "/exit" in _docs("/ex")


def test_complete_models(monkeypatch):
    """/model 合并内置与自定义模型，自定义项带类型标签。"""
    settings = SimpleNamespace(
        models={"claude-sonnet": "Claude sonnet"},
        custom_models={"my-gpt": {"model_type": "text", "name": "我的GPT"}},
    )
    monkeypatch.setattr(
        "agent.config.settings.load_settings", lambda *a, **k: settings
    )
    assert _docs("/model ") == ["claude-sonnet", "my-gpt"]
    assert _docs("/model my") == ["my-gpt"]


def test_complete_models_survives_config_error(monkeypatch):
    """配置加载抛错 → 返回空候选而不是让输入框崩溃。"""

    def _boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr("agent.config.settings.load_settings", _boom)
    assert _docs("/model any") == []


def test_complete_tts_voices(monkeypatch):
    """/tts-voice 同时收录音色名与不同的自定义 voice_id。"""
    voices = {
        "Cherry": {"description": "温柔女声"},
        "Ethan": {"description": "阳光男声", "voice_id": "ethan-1"},
    }
    monkeypatch.setattr("agent.config.settings.load_settings", lambda *a, **k: None)
    monkeypatch.setattr(
        "agent.voice.tts_voices.all_tts_voices", lambda settings: voices
    )
    assert _docs("/tts-voice ") == ["Cherry", "Ethan", "ethan-1"]
    assert _docs("/tts-voice et") == ["Ethan", "ethan-1"]


def test_complete_sessions_for_load_and_save(monkeypatch):
    """/load 与 /save 共用会话补全，描述含消息数与工作目录。"""
    sessions = [
        SimpleNamespace(name="demo", message_count=7, workdir="/tmp/a"),
        SimpleNamespace(name="other", message_count=1, workdir=None),
    ]
    monkeypatch.setattr(
        "agent.core.memory.store.list_sessions", lambda *a, **k: sessions
    )
    assert _docs("/load ") == ["demo", "other"]
    assert _docs("/save d") == ["demo"]
    doc = Document("/load demo", 10)
    (first,) = _COMPLETER.get_completions(doc, None)
    assert "7 条消息" in list(first.display)[1][1]


def test_complete_sessions_survives_store_error(monkeypatch):
    """会话仓储异常同样被吞掉，补全退化为空候选。"""

    def _boom(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr("agent.core.memory.store.list_sessions", _boom)
    assert _docs("/load zz") == []
