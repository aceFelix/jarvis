"""agent/core/memory/store.py 单元测试。

覆盖会话 JSON 存盘/加载/列表/删除/路径穿越防护，以及长期记忆文件的读取与优先级。
所有涉及 ~/.jarvis 的路径函数都通过 monkeypatch 把 `store.user_jarvis_dir`
重定向到 pytest 的 tmp_path，避免污染真实用户目录。

@author aceFelix
"""

import json

import pytest

from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.core.memory import store
from agent.core.memory.store import (
    _message_from_dict,
    _message_to_dict,
    _session_path,
    delete_session,
    get_memory_files,
    latest_session_name,
    list_sessions,
    load_long_term_memory,
    load_session,
    memory_section,
    project_memory_path,
    save_session,
    user_memory_path,
)


@pytest.fixture
def jarvis_home(tmp_path, monkeypatch):
    """把 store 模块的 user_jarvis_dir 重定向到 tmp_path 下隔离的 .jarvis 目录。"""
    home = tmp_path / ".jarvis"
    monkeypatch.setattr(store, "user_jarvis_dir", lambda: home)
    return home


def _make_messages() -> list[Message]:
    """构造覆盖全部 block 类型的消息列表。"""
    return [
        Message(role="user", content=[TextContent(text="你好，贾维斯！")]),
        Message(
            role="assistant",
            content=[
                TextContent(text="我在。"),
                ToolUseContent(id="tu-1", name="read_file", input={"path": "a.py"}),
            ],
        ),
        Message(
            role="user",
            content=[
                ToolResultContent(
                    tool_use_id="tu-1",
                    content="文件内容",
                    is_error=False,
                    images=[ImageContent(data="base64xxx", media_type="image/png")],
                )
            ],
        ),
        Message(role="user", content=[ImageContent(data="imgdata", media_type="image/jpeg")]),
    ]


class TestMessageSerialization:
    """消息序列化往返。"""

    def test_message_dict_roundtrip(self) -> None:
        """_message_to_dict / _message_from_dict 各类 block 往返一致。"""
        msgs = _make_messages()
        for msg in msgs:
            restored = _message_from_dict(_message_to_dict(msg))
            assert restored.role == msg.role
            assert restored.id == msg.id
            assert len(restored.content) == len(msg.content)

        # 逐 block 校验关键字段
        restored = _message_from_dict(_message_to_dict(msgs[1]))
        assert isinstance(restored.content[1], ToolUseContent)
        assert restored.content[1].name == "read_file"
        assert restored.content[1].input == {"path": "a.py"}

        restored3 = _message_from_dict(_message_to_dict(msgs[2]))
        assert isinstance(restored3.content[0], ToolResultContent)
        assert restored3.content[0].tool_use_id == "tu-1"
        assert restored3.content[0].is_error is False
        assert len(restored3.content[0].images) == 1
        assert restored3.content[0].images[0].data == "base64xxx"
        assert restored3.content[0].images[0].media_type == "image/png"

        restored4 = _message_from_dict(_message_to_dict(msgs[3]))
        assert isinstance(restored4.content[0], ImageContent)
        assert restored4.content[0].data == "imgdata"

    def test_block_from_dict_unknown_type_fallback(self) -> None:
        """未知 block 类型降级为空文本，不抛异常。"""
        block = store._block_from_dict({"type": "weird", "text": "x"})
        assert isinstance(block, TextContent)
        assert block.text == ""


class TestSessionSaveLoad:
    """会话存盘与加载。"""

    def test_save_load_roundtrip_all_blocks(self, jarvis_home) -> None:
        """save_session/load_session 往返，各类 block 完整还原。"""
        msgs = _make_messages()
        path = save_session("test-session", msgs, workdir="/tmp/wd", model="qwen", provider="dashscope")
        assert path.exists()
        assert path.parent == jarvis_home / "sessions"

        data = load_session("test-session")
        assert data is not None
        assert data.meta.name == "test-session"
        assert data.meta.workdir == "/tmp/wd"
        assert data.meta.model == "qwen"
        assert data.meta.provider == "dashscope"
        assert data.meta.message_count == len(msgs)
        assert len(data.messages) == len(msgs)

        # 校验各类 block 内容还原
        assert data.messages[0].content[0].text == "你好，贾维斯！"
        assert isinstance(data.messages[1].content[1], ToolUseContent)
        assert data.messages[1].content[1].id == "tu-1"
        assert data.messages[2].content[0].images[0].data == "base64xxx"

    def test_save_keeps_created_at(self, jarvis_home) -> None:
        """已存在会话再次保存时保留 created_at。"""
        msgs = [Message.user_text("hello")]
        save_session("keep-ct", msgs)
        first = load_session("keep-ct")
        assert first is not None

        save_session("keep-ct", msgs + [Message.assistant_text("hi")])
        second = load_session("keep-ct")
        assert second is not None
        assert second.meta.created_at == first.meta.created_at
        # updated_at 不早于 created_at
        assert second.meta.updated_at >= second.meta.created_at
        assert second.meta.message_count == 2

    def test_save_existing_corrupt_file_uses_new_created_at(self, jarvis_home) -> None:
        """旧文件损坏时读取失败，使用当前时间作为 created_at（不抛异常）。"""
        path = store.sessions_dir() / "corrupt.json"
        path.write_text("{bad json", encoding="utf-8")
        save_session("corrupt", [Message.user_text("hi")])
        data = load_session("corrupt")
        assert data is not None
        assert data.meta.created_at > 0

    def test_load_missing_returns_none(self, jarvis_home) -> None:
        """加载不存在的会话返回 None。"""
        assert load_session("not-exist") is None

    def test_load_corrupt_json_returns_none(self, jarvis_home) -> None:
        """损坏 JSON 返回 None。"""
        path = store.sessions_dir() / "bad.json"
        path.write_text("{invalid json", encoding="utf-8")
        assert load_session("bad") is None


class TestSessionList:
    """会话列表。"""

    @staticmethod
    def _write_session(path, name: str, updated_at: float) -> None:
        """手工写一个会话文件，控制 updated_at。"""
        data = {
            "meta": {
                "name": name,
                "created_at": 1.0,
                "updated_at": updated_at,
                "message_count": 3,
                "workdir": "",
                "model": "",
                "provider": "",
            },
            "messages": [],
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_list_sessions_sorted_by_updated_at_desc(self, jarvis_home) -> None:
        """list_sessions 按 updated_at 倒序排列。"""
        d = store.sessions_dir()
        self._write_session(d / "old.json", "old", updated_at=100.0)
        self._write_session(d / "new.json", "new", updated_at=300.0)
        self._write_session(d / "mid.json", "mid", updated_at=200.0)

        sessions = list_sessions()
        assert [s.name for s in sessions] == ["new", "mid", "old"]
        assert sessions[0].message_count == 3

    def test_list_sessions_skips_corrupt_file(self, jarvis_home) -> None:
        """损坏的 JSON 文件被跳过。"""
        d = store.sessions_dir()
        self._write_session(d / "good.json", "good", updated_at=100.0)
        (d / "bad.json").write_text("not json at all", encoding="utf-8")

        sessions = list_sessions()
        assert [s.name for s in sessions] == ["good"]

    def test_list_sessions_empty(self, jarvis_home) -> None:
        """无会话时返回空列表。"""
        assert list_sessions() == []

    def test_latest_session_name(self, jarvis_home) -> None:
        """latest_session_name 返回最近更新的会话名。"""
        d = store.sessions_dir()
        self._write_session(d / "a.json", "a", updated_at=100.0)
        self._write_session(d / "b.json", "b", updated_at=500.0)
        assert latest_session_name() == "b"

    def test_latest_session_name_none(self, jarvis_home) -> None:
        """无会话时 latest_session_name 返回 None。"""
        assert latest_session_name() is None


class TestDeleteSession:
    """会话删除。"""

    def test_delete_existing(self, jarvis_home) -> None:
        save_session("del-me", [Message.user_text("x")])
        assert delete_session("del-me") is True
        assert load_session("del-me") is None

    def test_delete_missing_returns_false(self, jarvis_home) -> None:
        assert delete_session("never-existed") is False


class TestSessionPath:
    """路径穿越防护。"""

    def test_path_traversal_sanitized(self, jarvis_home) -> None:
        """路径穿越字符被清洗，文件始终落在 sessions 目录内。"""
        path = _session_path("../../etc/passwd")
        assert path.parent == store.sessions_dir()
        assert path.name == "etcpasswd.json"

    def test_special_chars_removed(self, jarvis_home) -> None:
        """空格和标点被移除，中文/字母数字/横线下划线保留。"""
        path = _session_path("my session 01!?")
        assert path.name == "mysession01.json"

    def test_unicode_name_kept(self, jarvis_home) -> None:
        """中文会话名合法保留。"""
        path = _session_path("我的会话-1")
        assert path.name == "我的会话-1.json"

    def test_all_invalid_falls_back_to_default(self, jarvis_home) -> None:
        """全部字符非法时回退为 default。"""
        assert _session_path("...") == store.sessions_dir() / "default.json"
        assert _session_path("") == store.sessions_dir() / "default.json"


class TestLongTermMemory:
    """长期记忆读取与优先级。"""

    def test_load_both_user_and_project(self, jarvis_home, tmp_path) -> None:
        """用户级 + 项目级记忆都存在时同时注入，项目级在后。"""
        # 注意: workdir 必须是与 ~/.jarvis 无关的独立目录，
        # 否则 project_memory_path(workdir) 会与用户级记忆指向同一文件。
        workdir = str(tmp_path / "proj")
        user_mem = user_memory_path()
        user_mem.parent.mkdir(parents=True, exist_ok=True)
        user_mem.write_text("用户喜欢简洁的回答", encoding="utf-8")

        proj_mem = project_memory_path(workdir)
        proj_mem.parent.mkdir(parents=True, exist_ok=True)
        proj_mem.write_text("项目使用 FastAPI", encoding="utf-8")

        text = load_long_term_memory(workdir)
        assert "用户级记忆" in text
        assert "项目级记忆" in text
        assert text.index("用户级记忆") < text.index("项目级记忆")
        assert "用户喜欢简洁的回答" in text
        assert "项目使用 FastAPI" in text

    def test_load_user_only(self, jarvis_home, tmp_path) -> None:
        """只有用户级记忆。"""
        user_memory_path().parent.mkdir(parents=True, exist_ok=True)
        user_memory_path().write_text("记忆A", encoding="utf-8")
        text = load_long_term_memory(str(tmp_path / "proj"))
        assert "用户级记忆" in text
        assert "项目级记忆" not in text

    def test_load_project_only(self, jarvis_home, tmp_path) -> None:
        """只有项目级记忆。"""
        workdir = str(tmp_path / "proj")
        proj_mem = project_memory_path(workdir)
        proj_mem.parent.mkdir(parents=True, exist_ok=True)
        proj_mem.write_text("记忆B", encoding="utf-8")
        text = load_long_term_memory(workdir)
        assert "项目级记忆" in text
        assert "用户级记忆" not in text

    def test_load_none_returns_empty(self, jarvis_home, tmp_path) -> None:
        """都不存在时返回空字符串。"""
        assert load_long_term_memory(str(tmp_path / "proj")) == ""

    def test_load_blank_files_returns_empty(self, jarvis_home, tmp_path) -> None:
        """文件存在但内容为空白时返回空字符串。"""
        user_memory_path().parent.mkdir(parents=True, exist_ok=True)
        user_memory_path().write_text("   \n  ", encoding="utf-8")
        assert load_long_term_memory(str(tmp_path / "proj")) == ""

    def test_load_unreadable_user_file_ignored(self, jarvis_home, tmp_path) -> None:
        """用户记忆文件损坏（非法 UTF-8）时静默跳过，项目记忆仍注入。"""
        user_memory_path().parent.mkdir(parents=True, exist_ok=True)
        user_memory_path().write_bytes(b"\xff\xfe\x80\x81 broken")
        workdir = str(tmp_path / "proj")
        proj_mem = project_memory_path(workdir)
        proj_mem.parent.mkdir(parents=True, exist_ok=True)
        proj_mem.write_text("项目记忆", encoding="utf-8")

        text = load_long_term_memory(workdir)
        assert "项目级记忆" in text
        assert "用户级记忆" not in text

    def test_memory_section(self, jarvis_home, tmp_path) -> None:
        """memory_section 有记忆时追加换行，无记忆时为空。"""
        user_memory_path().parent.mkdir(parents=True, exist_ok=True)
        user_memory_path().write_text("记忆", encoding="utf-8")
        section = memory_section(str(tmp_path / "proj"))
        assert section.endswith("\n")
        assert section.startswith("# 长期记忆")

        # 清空用户记忆文件，此时只剩项目级（也不存在）→ 空
        user_memory_path().unlink()
        assert memory_section(str(tmp_path / "proj")) == ""

    def test_get_memory_files(self, jarvis_home, tmp_path) -> None:
        """get_memory_files 返回用户级/项目级记忆文件路径。"""
        workdir = str(tmp_path / "proj")
        files = get_memory_files(workdir)
        assert files["user"] == user_memory_path()
        assert files["project"] == project_memory_path(workdir)
        # 路径不含真实用户主目录
        assert str(files["user"]).startswith(str(jarvis_home))
