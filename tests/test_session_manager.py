"""agent/session_manager.py 单元测试。

覆盖会话标题生成（_sanitize_title / _rename_session_file / _generate_title_*）、
自动保存、手动保存、加载、列表展示等会话生命周期管理逻辑。
涉及 ~/.jarvis 的路径函数统一 monkeypatch 到 tmp_path；LLM 标题生成用
FakeTitleProvider 模拟流式事件序列。

@author aceFelix
"""

import json
import re
from datetime import datetime

import pytest

from agent.core.message import (
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.core.memory import store as store_mod
from agent.core.memory.store import SessionData, SessionMeta
from agent.session_manager import (
    _auto_save,
    _generate_session_title,
    _generate_title_from_first_user,
    _list_sessions,
    _load_by_name,
    _load_by_picker,
    _load_session,
    _rename_session_file,
    _render_session,
    _sanitize_title,
    _save_session,
    advance_session,
)


@pytest.fixture
def jarvis_home(tmp_path, monkeypatch):
    """把 store 模块的 user_jarvis_dir 重定向到 tmp_path，隔离真实用户目录。"""
    home = tmp_path / ".jarvis"
    monkeypatch.setattr(store_mod, "user_jarvis_dir", lambda: home)
    return home


class StubUI:
    """RichCLI 的简化替身：记录 info/warn/error 及渲染调用。"""

    def __init__(self):
        self.calls: list[tuple] = []
        self._console = None

    def info(self, text):
        self.calls.append(("info", text))

    def warn(self, text):
        self.calls.append(("warn", text))

    def error(self, text):
        self.calls.append(("error", text))

    def tool_result(self, name, tool_use_id, content, is_error=False):
        self.calls.append(("tool_result", name, tool_use_id, content, is_error))

    def assistant_thinking(self, text):
        self.calls.append(("thinking", text))

    def _end_thinking(self):
        self.calls.append(("end_thinking",))

    def tool_use(self, name, input_, tool_use_id):
        self.calls.append(("tool_use", name, input_, tool_use_id))


class FakeTitleProvider:
    """模拟 LLM 标题生成的 provider：按预设事件流式返回。"""

    def __init__(self, events=(), thinking=False):
        self._events = list(events)
        self._thinking = thinking
        self.thinking_changes: list[bool] = []

    def is_thinking_enabled(self):
        return self._thinking

    def set_thinking_enabled(self, enabled):
        self.thinking_changes.append(enabled)
        self._thinking = enabled

    async def stream(self, **kwargs):
        for e in self._events:
            yield e


class ErrorTitleProvider:
    """stream 时抛异常的 provider。"""

    async def stream(self, **kwargs):
        raise RuntimeError("provider down")


class TestSanitizeTitle:
    """标题文件名安全化。"""

    def test_removes_punctuation(self) -> None:
        assert _sanitize_title("Hello, World!") == "Hello-World"

    def test_chinese_punctuation_removed(self) -> None:
        assert _sanitize_title("你好，世界！") == "你好世界"

    def test_truncated_to_15_chars(self) -> None:
        title = _sanitize_title("一二三四五六七八九十一二三四五六七八九十")
        assert len(title) == 15

    def test_only_punctuation_returns_empty(self) -> None:
        assert _sanitize_title("!!!？？") == ""

    def test_whitespace_collapsed_to_hyphen(self) -> None:
        assert _sanitize_title("a   b   c") == "a-b-c"

    def test_keeps_letters_digits_underscore(self) -> None:
        assert _sanitize_title("my_session_01") == "my_session_01"


class TestRenameSessionFile:
    """会话文件重命名。"""

    def test_rename_to_new_name(self, jarvis_home) -> None:
        (jarvis_home / "sessions").mkdir(parents=True, exist_ok=True)
        old = jarvis_home / "sessions" / "old.json"
        old.write_text("{}", encoding="utf-8")

        result = _rename_session_file("old", "新标题！")
        assert result == "新标题"
        assert not old.exists()
        assert (jarvis_home / "sessions" / "新标题.json").exists()

    def test_old_file_missing_returns_title(self, jarvis_home) -> None:
        """旧文件未落盘：直接用新标题（不创建文件）。"""
        result = _rename_session_file("never-saved", "新标题")
        assert result == "新标题"
        assert not (jarvis_home / "sessions" / "新标题.json").exists()

    def test_target_exists_appends_number(self, jarvis_home) -> None:
        """目标名已存在 → 追加 -2。"""
        d = jarvis_home / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        old = d / "old.json"
        old.write_text("{}", encoding="utf-8")
        (d / "标题.json").write_text("{}", encoding="utf-8")

        result = _rename_session_file("old", "标题")
        assert result == "标题-2"
        assert (d / "标题-2.json").exists()

    def test_target_and_suffix_exist_appends_higher(self, jarvis_home) -> None:
        """-2 也被占用 → 用 -3。"""
        d = jarvis_home / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        (d / "old.json").write_text("{}", encoding="utf-8")
        (d / "标题.json").write_text("{}", encoding="utf-8")
        (d / "标题-2.json").write_text("{}", encoding="utf-8")

        result = _rename_session_file("old", "标题")
        assert result == "标题-3"
        assert (d / "标题-3.json").exists()

    def test_empty_title_returns_old_name(self, jarvis_home) -> None:
        assert _rename_session_file("old", "!!!") == "old"

    def test_same_title_returns_title(self, jarvis_home) -> None:
        assert _rename_session_file("same", "same") == "same"

    def test_meta_name_synced_after_rename(self, jarvis_home) -> None:
        """重命名后回写文件内 meta.name，保证列表上报名=文件名（可按名加载）。

        回归背景：只 rename 文件不同步 meta 时，sessions.list 上报旧名，
        按旧名 load_session 找不到文件报"会话不存在或为空"。

        @author aceFelix
        """
        d = jarvis_home / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        old = d / "old.json"
        old.write_text(
            json.dumps({"meta": {"name": "old"}, "messages": []}),
            encoding="utf-8",
        )

        result = _rename_session_file("old", "新标题")
        assert result == "新标题"
        data = json.loads((d / f"{result}.json").read_text(encoding="utf-8"))
        assert data["meta"]["name"] == result

    def test_meta_name_synced_with_suffix(self, jarvis_home) -> None:
        """追加 -2 序号的重命名同样回写 meta.name 为最终名。"""
        d = jarvis_home / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        (d / "old.json").write_text(
            json.dumps({"meta": {"name": "old"}, "messages": []}),
            encoding="utf-8",
        )
        (d / "标题.json").write_text("{}", encoding="utf-8")

        result = _rename_session_file("old", "标题")
        assert result == "标题-2"
        data = json.loads((d / "标题-2.json").read_text(encoding="utf-8"))
        assert data["meta"]["name"] == "标题-2"

    def test_rename_moves_file_without_copy(self, jarvis_home) -> None:
        """改名是移动而非复制：旧文件不再残留。

        历史：auto-latest 恢复指针时代对 auto-latest 名特例走复制，
        指针特性已下线，现在所有会话统一直接改名。@author aceFelix
        """
        d = jarvis_home / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        old = d / "auto-latest.json"
        old.write_text(
            json.dumps({"meta": {"name": "auto-latest"}, "messages": []}),
            encoding="utf-8",
        )

        result = _rename_session_file("auto-latest", "新标题")
        assert result == "新标题"
        assert not old.exists()  # 旧文件已被移走
        copy_data = json.loads((d / "新标题.json").read_text(encoding="utf-8"))
        assert copy_data["meta"]["name"] == "新标题"


class TestGenerateTitleFromFirstUser:
    """首条用户消息标题生成。"""

    async def test_takes_first_user_text(self, jarvis_home) -> None:
        ui = StubUI()
        messages = [
            Message.assistant_text("你好，有什么可以帮你？"),
            Message.user_text("请帮我优化一下登录页面的性能问题，谢谢！"),
        ]
        result = await _generate_title_from_first_user(ui, messages, "session-old")
        # 前 15 字符（去掉标点后）
        assert result == "请帮我优化一下登录页面的性能问"
        assert ui.calls and ui.calls[0][0] == "info"

    async def test_no_user_message_returns_old_name(self, jarvis_home) -> None:
        ui = StubUI()
        messages = [Message.assistant_text("只有助手消息")]
        result = await _generate_title_from_first_user(ui, messages, "session-old")
        assert result == "session-old"
        assert ui.calls == []

    async def test_empty_user_text_skipped(self, jarvis_home) -> None:
        """首条 user 消息无文本（如只有工具结果）时继续找下一条。"""
        ui = StubUI()
        messages = [
            Message(role="user", content=[ToolResultContent(tool_use_id="t", content="")]),
            Message.user_text("第二条才是正文"),
        ]
        result = await _generate_title_from_first_user(ui, messages, "old")
        assert result == "第二条才是正文"

    async def test_url_stripped_before_truncation(self, jarvis_home) -> None:
        """先剥 URL 再截 15 字：实机标题曾被切成「jarvsi帮我抓取一下htt」。"""
        ui = StubUI()
        messages = [
            Message.user_text("jarvsi帮我抓取一下https://ailhz.top/这个网页内容"),
        ]
        result = await _generate_title_from_first_user(ui, messages, "session-old")
        assert "http" not in result
        assert result.startswith("jarvsi")

    async def test_url_only_falls_back_to_raw_text(self, jarvis_home) -> None:
        """用户只发了一个链接：清洗后为空 → 回退原文，不能生成空标题。"""
        ui = StubUI()
        result = await _generate_title_from_first_user(
            ui, [Message.user_text("https://example.com/abc")], "session-old"
        )
        assert result
        assert result != "session-old"

    async def test_exception_returns_old_name(self, jarvis_home, monkeypatch) -> None:
        """内部异常时回退旧名。"""
        ui = StubUI()
        monkeypatch.setattr(
            store_mod, "sessions_dir", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        result = await _generate_title_from_first_user(ui, [Message.user_text("x")], "old")
        assert result == "old"


class TestGenerateSessionTitle:
    """LLM 标题生成。"""

    async def test_normal_generation(self, jarvis_home) -> None:
        from agent.llm.base import TextDelta, Stop

        ui = StubUI()
        provider = FakeTitleProvider([TextDelta(text="我的项目标题"), Stop()])
        messages = [Message.user_text("帮我写一个 Python 爬虫"), Message.assistant_text("好的")]
        result = await _generate_session_title(ui, provider, "qwen", messages, "auto-latest")
        assert result == "我的项目标题"
        # 思考模式被临时关闭后恢复
        assert provider.thinking_changes == [False, False]
        assert ui.calls[0][0] == "info"

    async def test_prefix_stripped(self, jarvis_home) -> None:
        from agent.llm.base import Stop, TextDelta

        ui = StubUI()
        provider = FakeTitleProvider([TextDelta(text="标题：测试标题"), Stop()])
        messages = [Message.user_text("hi")]
        result = await _generate_session_title(ui, provider, "qwen", messages, "auto-latest")
        assert result == "测试标题"

    async def test_quotes_stripped(self, jarvis_home) -> None:
        from agent.llm.base import Stop, TextDelta

        ui = StubUI()
        provider = FakeTitleProvider([TextDelta(text='"引号标题"'), Stop()])
        messages = [Message.user_text("hi")]
        result = await _generate_session_title(ui, provider, "qwen", messages, "auto-latest")
        assert result == "引号标题"

    async def test_long_output_truncated(self, jarvis_home) -> None:
        from agent.llm.base import Stop, TextDelta

        ui = StubUI()
        provider = FakeTitleProvider([TextDelta(text="这是一个非常非常长的会话标题超过十五个字符了"), Stop()])
        messages = [Message.user_text("hi")]
        result = await _generate_session_title(ui, provider, "qwen", messages, "auto-latest")
        assert len(result) <= 15

    async def test_empty_output_returns_old_name(self, jarvis_home) -> None:
        from agent.llm.base import Stop

        ui = StubUI()
        provider = FakeTitleProvider([Stop()])
        messages = [Message.user_text("hi")]
        result = await _generate_session_title(ui, provider, "qwen", messages, "auto-latest")
        assert result == "auto-latest"
        assert ui.calls == []

    async def test_provider_error_returns_old_name(self, jarvis_home) -> None:
        ui = StubUI()
        provider = ErrorTitleProvider()
        messages = [Message.user_text("hi")]
        result = await _generate_session_title(ui, provider, "qwen", messages, "auto-latest")
        assert result == "auto-latest"

    async def test_no_dialog_lines_returns_old_name(self, jarvis_home) -> None:
        """全是工具消息时无对话内容 → 返回旧名。"""
        from agent.llm.base import Stop, TextDelta

        ui = StubUI()
        provider = FakeTitleProvider([TextDelta(text="x"), Stop()])
        messages = [
            Message(role="user", content=[ToolResultContent(tool_use_id="t", content="ok")]),
        ]
        result = await _generate_session_title(ui, provider, "qwen", messages, "auto-latest")
        assert result == "auto-latest"

    async def test_only_first_4_dialog_lines_used(self, jarvis_home) -> None:
        """只取前 4 条 user/assistant 消息。"""
        from agent.llm.base import Stop, TextDelta

        ui = StubUI()
        provider = FakeTitleProvider([TextDelta(text="标题"), Stop()])
        messages = [Message.user_text(f"对话内容{i}") for i in range(8)]
        result = await _generate_session_title(ui, provider, "qwen", messages, "auto-latest")
        assert result == "标题"


class TestAutoSave:
    """自动保存。"""

    def test_empty_messages_noop(self, jarvis_home, monkeypatch) -> None:
        save_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        monkeypatch.setattr(store_mod, "save_session", save_mock)
        ui = StubUI()
        _auto_save(ui, [], session_name="x")
        save_mock.assert_not_called()

    def test_saves_only_named_session(self, jarvis_home, monkeypatch) -> None:
        """只写指定会话文件一份，不再写 auto-latest 恢复指针。@author aceFelix"""
        save_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        monkeypatch.setattr(store_mod, "save_session", save_mock)
        ui = StubUI()
        messages = [Message.user_text("hello")]
        _auto_save(ui, messages, workdir="/wd", model="qwen", provider="ds", session_name="s1")
        assert save_mock.call_count == 1
        names = [c.args[0] for c in save_mock.call_args_list]
        assert names == ["s1"]
        assert ui.calls[0][0] == "info"

    def test_verbose_false_no_info(self, jarvis_home, monkeypatch) -> None:
        save_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        monkeypatch.setattr(store_mod, "save_session", save_mock)
        ui = StubUI()
        _auto_save(ui, [Message.user_text("x")], session_name="s1", verbose=False)
        assert ui.calls == []

    def test_exception_silent(self, jarvis_home, monkeypatch) -> None:
        """保存异常静默（不打断主流程）。"""
        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(store_mod, "save_session", _boom)
        ui = StubUI()
        _auto_save(ui, [Message.user_text("x")], session_name="s1")  # 不抛异常
        assert ui.calls == []


class TestAdvanceSession:
    """统一会话收尾 advance_session（文本 REPL 与 /voice 共用）。

    2026-11-09 用户反馈：语音会话只留时间戳名字、从不生成标题。
    收尾逻辑收敛到 advance_session 后，两条路径生成 15 字以内标题的规则一致。
    """

    async def test_first_turn_generates_title_without_llm(self, jarvis_home) -> None:
        """第 1 轮：取用户首句前 15 字做标题，不调用 LLM。"""
        ui = StubUI()
        messages = [
            Message.user_text("帮我看看电脑为什么这么卡，风扇一直响"),
            Message.assistant_text("好的，我先看一下进程。"),
        ]
        name, count, titled = await advance_session(
            ui,
            messages,
            session_name="session-20261109-164502",
            dialog_count=0,
            title_generated=False,
            settings=None,
        )
        assert count == 1
        assert titled is False
        # 先截前 15 字再清标点（逗号被去掉，标题共 15 字）
        assert name == "帮我看看电脑为什么这么卡风扇"
        assert len(name) <= 15
        assert (jarvis_home / "sessions" / f"{name}.json").exists()

    async def test_second_turn_uses_llm_title(self, jarvis_home) -> None:
        """第 2 轮：用前两轮对话让 LLM 生成标题。"""
        from agent.llm.base import Stop, TextDelta

        ui = StubUI()
        provider = FakeTitleProvider([TextDelta(text="网络排障"), Stop()])
        messages = [
            Message.user_text("帮我看看为什么连不上网"),
            Message.assistant_text("先检查网卡状态"),
            Message.user_text("网卡显示已连接"),
            Message.assistant_text("那可能是 DNS"),
        ]
        name, count, titled = await advance_session(
            ui,
            messages,
            session_name="session-old",
            dialog_count=1,
            title_generated=False,
            provider=provider,
            model="qwen",
            settings=None,
        )
        assert count == 2
        assert titled is True
        assert name == "网络排障"

    async def test_existing_title_is_kept(self, jarvis_home) -> None:
        """已有标题（/load 恢复或用户改名）不再生成。"""
        ui = StubUI()
        messages = [Message.user_text("随便聊聊"), Message.assistant_text("好的")]
        name, count, titled = await advance_session(
            ui,
            messages,
            session_name="已有标题",
            dialog_count=0,
            title_generated=True,
            settings=None,
        )
        assert name == "已有标题"
        assert count == 1
        assert titled is True

    async def test_empty_messages_noop(self, jarvis_home, monkeypatch) -> None:
        """没有消息时不动轮数、不落盘。"""
        save_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        monkeypatch.setattr(store_mod, "save_session", save_mock)
        ui = StubUI()
        name, count, titled = await advance_session(
            ui,
            [],
            session_name="s1",
            dialog_count=3,
            title_generated=False,
        )
        assert (name, count, titled) == ("s1", 3, False)
        save_mock.assert_not_called()

    async def test_created_at_survives_rename(self, jarvis_home) -> None:
        """标题改名后创建时间不变（时间属性不能因改名丢失）。"""
        ui = StubUI()
        messages = [Message.user_text("第一次提问"), Message.assistant_text("回答")]
        name, _count, _titled = await advance_session(
            ui,
            messages,
            session_name="session-20260101-000000",
            dialog_count=0,
            title_generated=False,
            settings=None,
        )
        first = json.loads((jarvis_home / "sessions" / f"{name}.json").read_text(encoding="utf-8"))
        created = first["meta"]["created_at"]
        assert created > 0

        # 第二轮再次保存（同名文件），created_at 必须保留
        messages.append(Message.user_text("第二次提问"))
        await advance_session(
            ui,
            messages,
            session_name=name,
            dialog_count=1,
            title_generated=False,
            settings=None,
        )
        second = json.loads((jarvis_home / "sessions" / f"{name}.json").read_text(encoding="utf-8"))
        assert second["meta"]["created_at"] == created


class TestSaveSession:
    """手动保存。"""

    def test_save_with_name(self, jarvis_home, monkeypatch) -> None:
        save_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(return_value="/p/s1.json")
        monkeypatch.setattr(store_mod, "save_session", save_mock)
        settings = __import__("types", fromlist=["SimpleNamespace"]).SimpleNamespace(
            workdir="/wd", model="qwen", provider="ds"
        )
        ui = StubUI()
        _save_session(ui, settings, "/save myname", [Message.user_text("hi")])
        assert save_mock.call_args.args[0] == "myname"
        assert ui.calls[0] == ("info", "会话已保存: myname（1 条消息）→ /p/s1.json")

    def test_save_without_name_uses_timestamp(self, jarvis_home, monkeypatch) -> None:
        save_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(return_value="/p")
        monkeypatch.setattr(store_mod, "save_session", save_mock)
        settings = __import__("types", fromlist=["SimpleNamespace"]).SimpleNamespace(
            workdir="", model="", provider=""
        )
        ui = StubUI()
        _save_session(ui, settings, "/save", [Message.user_text("hi")])
        name = save_mock.call_args.args[0]
        assert re.match(r"auto-\d{8}-\d{6}", name) is not None
        assert "会话已保存" in ui.calls[0][1]

    def test_empty_messages_warns(self, jarvis_home, monkeypatch) -> None:
        save_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        monkeypatch.setattr(store_mod, "save_session", save_mock)
        settings = __import__("types", fromlist=["SimpleNamespace"]).SimpleNamespace(
            workdir="", model="", provider=""
        )
        ui = StubUI()
        _save_session(ui, settings, "/save x", [])
        assert ui.calls[0][0] == "warn"
        save_mock.assert_not_called()

    def test_name_with_spaces_kept(self, jarvis_home, monkeypatch) -> None:
        save_mock = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock(return_value="/p")
        monkeypatch.setattr(store_mod, "save_session", save_mock)
        settings = __import__("types", fromlist=["SimpleNamespace"]).SimpleNamespace(
            workdir="", model="", provider=""
        )
        ui = StubUI()
        _save_session(ui, settings, "/save 带空格 的名字", [Message.user_text("hi")])
        assert save_mock.call_args.args[0] == "带空格 的名字"


class TestLoadSession:
    """会话加载。"""

    def test_load_session_is_deprecated_noop(self, jarvis_home) -> None:
        """_load_session 已废弃：调用无副作用。"""
        ui = StubUI()
        _load_session(ui, None, "/load", [])
        assert ui.calls == []

    def test_load_by_name_missing(self, jarvis_home, monkeypatch) -> None:
        monkeypatch.setattr(store_mod, "load_session", lambda name: None)
        ui = StubUI()
        settings = __import__("types", fromlist=["SimpleNamespace"]).SimpleNamespace()
        messages: list[Message] = []
        _load_by_name(ui, settings, "ghost", messages)
        assert ui.calls[0][0] == "error"
        assert "ghost" in ui.calls[0][1]

    def test_load_by_name_success(self, jarvis_home, monkeypatch) -> None:
        msgs = [Message.user_text("恢复的对话"), Message.assistant_text("好的")]
        data = SessionData(
            meta=SessionMeta(name="s1", workdir="/wd", message_count=2),
            messages=msgs,
        )
        monkeypatch.setattr(store_mod, "load_session", lambda name: data)
        ui = StubUI()
        settings = __import__("types", fromlist=["SimpleNamespace"]).SimpleNamespace()
        messages: list[Message] = [Message.user_text("旧的")]
        _load_by_name(ui, settings, "s1", messages)
        # 原消息被清空替换
        assert len(messages) == 2
        assert messages[0].content[0].text == "恢复的对话"
        assert ui.calls[0][0] == "info"
        # 渲染了用户消息
        assert any(c[0] == "info" and "恢复的对话" in c[1] for c in ui.calls)

    def test_load_by_picker_no_sessions(self, jarvis_home, monkeypatch) -> None:
        monkeypatch.setattr(store_mod, "list_sessions", lambda: [])
        ui = StubUI()
        _load_by_picker(ui, [])
        assert ui.calls[0][0] == "info"
        assert "没有已保存的会话" in ui.calls[0][1]

    def test_load_by_picker_picked(self, jarvis_home, monkeypatch) -> None:
        msgs = [Message.user_text("被选择的会话")]
        data = SessionData(
            meta=SessionMeta(name="s2", workdir="", message_count=1),
            messages=msgs,
        )
        monkeypatch.setattr(store_mod, "list_sessions", lambda: [SessionMeta(name="s2", message_count=1, updated_at=100)])
        monkeypatch.setattr(store_mod, "load_session", lambda name: data)

        import agent.ui.terminal_picker as tp
        monkeypatch.setattr(tp, "pick_from_list", lambda items, **kw: "s2")
        ui = StubUI()
        messages: list[Message] = []
        _load_by_picker(ui, messages)
        assert len(messages) == 1
        assert messages[0].content[0].text == "被选择的会话"

    def test_load_by_picker_cancelled(self, jarvis_home, monkeypatch) -> None:
        monkeypatch.setattr(store_mod, "list_sessions", lambda: [SessionMeta(name="s2", message_count=1, updated_at=100)])
        import agent.ui.terminal_picker as tp
        monkeypatch.setattr(tp, "pick_from_list", lambda items, **kw: None)
        ui = StubUI()
        messages: list[Message] = [Message.user_text("原内容")]
        _load_by_picker(ui, messages)
        # 取消后消息不被清空
        assert len(messages) == 1
        assert messages[0].content[0].text == "原内容"


class TestRenderSession:
    """会话回放渲染。"""

    def test_user_text_and_tool_result(self) -> None:
        ui = StubUI()
        msgs = [
            Message(role="user", content=[TextContent(text="帮我看看")]),
            Message(
                role="user",
                content=[ToolResultContent(tool_use_id="tu12345678", content="结果", is_error=True)],
            ),
        ]
        _render_session(ui, msgs)
        kinds = [c[0] for c in ui.calls]
        assert "info" in kinds
        assert "tool_result" in kinds
        # 用户文本消息
        assert any(c[0] == "info" and "帮我看看" in c[1] for c in ui.calls)

    def test_assistant_thinking_text_tool_use(self) -> None:
        ui = StubUI()
        msgs = [
            Message(
                role="assistant",
                content=[
                    ThinkingContent(text="思考过程"),
                    TextContent(text="正式回答"),
                    ToolUseContent(id="tu00000001", name="read_file", input={"p": "a"}),
                ],
            ),
        ]
        _render_session(ui, msgs)
        kinds = [c[0] for c in ui.calls]
        assert "thinking" in kinds
        assert "end_thinking" in kinds
        assert "tool_use" in kinds
        assert any(c[0] == "info" and "正式回答" in c[1] for c in ui.calls)

    def test_blank_text_not_rendered(self) -> None:
        ui = StubUI()
        msgs = [Message(role="user", content=[TextContent(text="   ")])]
        _render_session(ui, msgs)
        assert ui.calls == []


class TestListSessions:
    """会话列表展示。"""

    def test_no_sessions_info(self, jarvis_home, monkeypatch) -> None:
        monkeypatch.setattr(store_mod, "list_sessions", lambda: [])
        ui = StubUI()
        _list_sessions(ui)
        assert ui.calls[0][0] == "info"
        assert "没有已保存的会话" in ui.calls[0][1]

    def test_sessions_printed_without_console(self, jarvis_home, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            store_mod, "list_sessions",
            lambda: [SessionMeta(name="s1", message_count=3, updated_at=datetime(2026, 7, 1, 10, 0).timestamp())],
        )
        ui = StubUI()
        _list_sessions(ui)
        out = capsys.readouterr().out
        assert "s1" in out
        assert "3" in out

    def test_sessions_table_with_console(self, jarvis_home, monkeypatch) -> None:
        printed: list = []
        fake_console = type("Console", (), {"print": lambda self, *a, **k: printed.append(a)})()

        monkeypatch.setattr(
            store_mod, "list_sessions",
            lambda: [SessionMeta(name="s1", message_count=3, updated_at=datetime(2026, 7, 1, 10, 0).timestamp())],
        )
        ui = StubUI()
        ui._console = fake_console
        _list_sessions(ui)
        assert printed, "应通过 rich Table 打印"
