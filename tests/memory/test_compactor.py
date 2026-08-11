"""agent/core/memory/compactor.py 单元测试。

覆盖 token 估算、压缩触发判断、compact_messages（用 mock provider 模拟流式摘要）、
压缩后文件回灌 restore_recent_files、会话记忆 update_session_memory 等逻辑。

已知源码 bug（不改源码，测试绕过）:
- compactor.py 模块顶部缺少 `import os`，而 `update_session_memory` 内部直接使用
  `os.path.exists`，运行时抛 `NameError: name 'os' is not defined` 并被 except 吞掉
  → 会话记忆写入功能实际永远返回 None。本测试通过 monkeypatch 向模块注入 `os`
  全局名来验证其正常逻辑。

@author aceFelix
"""

import types

import pytest

from agent.core.message import (
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
)
from agent.core.memory import compactor
from agent.core.memory.compactor import (
    CompactResult,
    estimate_tokens,
    load_session_memory,
    restore_recent_files,
    should_compact,
    track_file_access,
    update_session_memory,
)
from agent.llm.base import ProviderError, Stop, TextDelta


class FakeProvider:
    """流式返回预设事件序列的 mock provider。"""

    def __init__(self, events=()):
        self.events = list(events)
        self.last_kwargs = None

    async def stream(self, **kwargs):
        self.last_kwargs = kwargs
        for e in self.events:
            yield e


class ErrorProvider:
    """流式迭代时抛异常的 provider（async generator，body 在迭代时执行）。"""

    def __init__(self, exc):
        self.exc = exc

    async def stream(self, **kwargs):
        raise self.exc
        yield  # pragma: no cover - 使函数成为 async generator


def _text_messages(n: int) -> list[Message]:
    """构造 n 条纯文本 user 消息。"""
    return [Message.user_text(f"消息{i}") for i in range(n)]


class TestEstimateTokens:
    """token 估算。"""

    def test_text_content(self) -> None:
        # "abcdefgh" 8 字符 // 4 = 2
        msg = Message(role="user", content=[TextContent(text="abcdefgh")])
        assert estimate_tokens([msg]) == 2

    def test_empty_text_min_one(self) -> None:
        msg = Message(role="user", content=[TextContent(text="")])
        assert estimate_tokens([msg]) == 1

    def test_tool_use_content(self) -> None:
        block = ToolUseContent(id="t1", name="read_file", input={"path": "a.py"})
        msg = Message(role="assistant", content=[block])
        assert estimate_tokens([msg]) >= 1

    def test_tool_result_with_images(self) -> None:
        block = ToolResultContent(
            tool_use_id="t1",
            content="short",
            images=[ImageContent(data="x"), ImageContent(data="y")],
        )
        msg = Message(role="user", content=[block])
        # 文本部分 ≥1，每张图片 1500
        assert estimate_tokens([msg]) >= 3000

    def test_image_content_fixed(self) -> None:
        msg = Message(role="user", content=[ImageContent(data="data")])
        assert estimate_tokens([msg]) == 1500

    def test_image_tokens_overridable(self) -> None:
        """图片 token 估算可按 provider 覆盖（不同视觉 token 算法）。"""
        msg = Message(role="user", content=[ImageContent(data="data")])
        assert estimate_tokens([msg], image_tokens_per_image=1000) == 1000
        assert estimate_tokens([msg], image_tokens_per_image=2000) == 2000

    def test_multiple_messages_accumulate(self) -> None:
        msgs = [Message.user_text("abcd"), Message.assistant_text("efgh")]
        assert estimate_tokens(msgs) == 2


class TestShouldCompact:
    """压缩触发判断。"""

    def test_too_few_messages(self) -> None:
        assert should_compact(_text_messages(5), threshold=1) is False

    def test_below_threshold(self) -> None:
        # 10 条短消息，每条 token 很少，低于阈值
        assert should_compact(_text_messages(10), threshold=10_000_000) is False

    def test_trigger(self) -> None:
        assert should_compact(_text_messages(10), threshold=1) is True


class TestStripImages:
    """图片剥离。"""

    def test_image_block_replaced_with_marker(self) -> None:
        msgs = [Message(role="user", content=[ImageContent(data="d")])]
        stripped = compactor._strip_images(msgs)
        block = stripped[0].content[0]
        assert isinstance(block, TextContent)
        assert block.text == "[图片]"

    def test_tool_result_images_dropped_text_kept(self) -> None:
        tr = ToolResultContent(
            tool_use_id="t1", content="保留文本", images=[ImageContent(data="d")]
        )
        msgs = [Message(role="user", content=[tr])]
        stripped = compactor._strip_images(msgs)
        block = stripped[0].content[0]
        assert isinstance(block, ToolResultContent)
        assert block.images == []
        assert "[附图已省略]" in block.content

    def test_no_images_returns_same_objects(self) -> None:
        msgs = [Message.user_text("hello")]
        stripped = compactor._strip_images(msgs)
        assert stripped[0] is msgs[0]


class TestFormatHistory:
    """摘要请求的历史格式化。"""

    def test_blocks_formatted(self) -> None:
        msgs = [
            Message(role="user", content=[TextContent(text="帮我写代码")]),
            Message(
                role="assistant",
                content=[
                    TextContent(text="好的"),
                    ToolUseContent(id="t1", name="write_file", input={}),
                ],
            ),
            Message(
                role="user",
                content=[ToolResultContent(tool_use_id="t1", content="完成")],
            ),
        ]
        text = compactor._format_history_for_summary(msgs)
        assert "用户: 帮我写代码" in text
        assert "助手: 好的" in text
        assert "[调用工具 write_file]" in text
        assert "[工具结果: 完成]" in text

    def test_tool_result_truncated_to_200(self) -> None:
        long_result = "长" * 300
        msgs = [Message(role="user", content=[ToolResultContent(tool_use_id="t", content=long_result)])]
        text = compactor._format_history_for_summary(msgs)
        assert "长" * 200 in text
        assert "长" * 201 not in text
        assert "..." in text

    def test_single_message_truncated_to_500(self) -> None:
        long_msg = Message.user_text("啊" * 600)
        text = compactor._format_history_for_summary([long_msg])
        assert "啊" * 500 in text
        assert "啊" * 501 not in text

    def test_thinking_only_message_skipped(self) -> None:
        # 只有 ThinkingContent 的消息无 parts → 不输出行
        msgs = [Message(role="assistant", content=[ThinkingContent(text="思考中")])]
        text = compactor._format_history_for_summary(msgs)
        assert text == ""


class TestCompactMessages:
    """压缩主流程。"""

    async def test_too_few_messages_returns_original(self) -> None:
        """消息数 ≤ keep_recent 时原样返回，不调用 provider。"""
        provider = FakeProvider()
        msgs = _text_messages(3)
        result = await compactor.compact_messages(provider, "m", msgs, keep_recent=4)
        assert result.new_messages == msgs
        assert result.messages_summarized == 0
        assert result.messages_kept == 3
        assert result.summary == ""
        assert provider.last_kwargs is None  # provider 未被调用

    async def test_basic_flow(self) -> None:
        """正常流程：摘要头部 + 保留尾部，统计字段正确。"""
        provider = FakeProvider([TextDelta(text="用户想优化代码性能"), Stop()])
        msgs = _text_messages(10)
        result = await compactor.compact_messages(provider, "qwen-max", msgs, keep_recent=4)

        assert isinstance(result, CompactResult)
        assert result.summary == "用户想优化代码性能"
        assert result.messages_summarized == 6
        assert result.messages_kept == 4
        assert result.pre_compact_tokens > 0
        assert result.post_compact_tokens > 0

        # 新消息 = [摘要] + 原尾部 4 条
        assert len(result.new_messages) == 5
        first = result.new_messages[0]
        assert first.role == "user"
        assert isinstance(first.content[0], TextContent)
        assert "用户想优化代码性能" in first.content[0].text
        assert result.new_messages[1:] == msgs[-4:]

        # 请求参数校验
        assert provider.last_kwargs["model"] == "qwen-max"
        assert provider.last_kwargs["tools"] == []
        assert provider.last_kwargs["temperature"] == 0.0
        assert provider.last_kwargs["max_tokens"] == compactor.COMPACT_MAX_OUTPUT_TOKENS
        assert provider.last_kwargs["system"] == compactor._COMPACT_SYSTEM

    async def test_empty_summary_falls_back_to_original(self) -> None:
        """LLM 未产出任何文本时回退：不压缩，原样返回。"""
        provider = FakeProvider([Stop()])
        msgs = _text_messages(10)
        result = await compactor.compact_messages(provider, "m", msgs, keep_recent=4)
        assert result.new_messages == msgs
        assert result.summary == ""
        assert result.messages_summarized == 0

    async def test_provider_error_propagates(self) -> None:
        """ProviderError 原样上抛。"""
        provider = ErrorProvider(ProviderError("网络错误"))
        with pytest.raises(ProviderError):
            await compactor.compact_messages(provider, "m", _text_messages(10), keep_recent=4)

    async def test_other_exception_wrapped_in_provider_error(self) -> None:
        """其他异常包装成 ProviderError。"""
        provider = ErrorProvider(ValueError("boom"))
        with pytest.raises(ProviderError):
            await compactor.compact_messages(provider, "m", _text_messages(10), keep_recent=4)

    async def test_on_progress_callback(self) -> None:
        """进度回调在开始和结束时各调用一次。"""
        provider = FakeProvider([TextDelta(text="摘要"), Stop()])
        calls: list[str] = []
        await compactor.compact_messages(
            provider, "m", _text_messages(10), keep_recent=4, on_progress=calls.append
        )
        assert len(calls) == 2
        assert "压缩上下文" in calls[0]
        assert "压缩完成" in calls[1]

    async def test_task_budget_injected(self) -> None:
        """task_budget_remaining > 0 时在摘要消息中注入预算提示。"""
        provider = FakeProvider([TextDelta(text="摘要"), Stop()])
        result = await compactor.compact_messages(
            provider, "m", _text_messages(10), keep_recent=4, task_budget_remaining=5000
        )
        text = result.new_messages[0].content[0].text
        assert "[任务预算]" in text
        assert "5000" in text


class TestTrackFileAccess:
    """文件访问记录。"""

    def test_track_and_dedup(self) -> None:
        ctx = types.SimpleNamespace(extra={})
        track_file_access(ctx, "a.py")
        track_file_access(ctx, "b.py")
        track_file_access(ctx, "a.py")  # 再次访问 a.py → 移除旧记录移到末尾
        files = ctx.extra["_recent_files"]
        assert [p for p, _, _ in files] == ["b.py", "a.py"]

    def test_cap_at_20(self) -> None:
        ctx = types.SimpleNamespace(extra={})
        for i in range(25):
            track_file_access(ctx, f"f{i}.py")
        assert len(ctx.extra["_recent_files"]) == 20
        assert ctx.extra["_recent_files"][0][0] == "f5.py"

    def test_no_extra_ignored(self) -> None:
        ctx = types.SimpleNamespace()
        track_file_access(ctx, "a.py")  # 不抛异常


class TestRestoreRecentFiles:
    """压缩后文件回灌。"""

    def test_restore_reads_files(self, tmp_path) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("print('hi')", encoding="utf-8")
        f2 = tmp_path / "b.md"
        f2.write_text("# docs", encoding="utf-8")

        ctx = types.SimpleNamespace(extra={}, messages=[])
        ctx.extra["_recent_files"] = [
            (str(f1), 1.0, "read"),
            (str(f2), 2.0, "write"),
        ]
        count = restore_recent_files(ctx)
        assert count == 2
        # 最新的（write 的 b.md）优先回灌
        assert "[压缩后文件回灌: 已修改]" in ctx.messages[0].content[0].text
        assert "b.md" in ctx.messages[0].content[0].text
        assert "[压缩后文件回灌: 已读取]" in ctx.messages[1].content[0].text
        # 回灌后清空记录
        assert ctx.extra["_recent_files"] == []

    def test_missing_file_skipped(self, tmp_path) -> None:
        ctx = types.SimpleNamespace(extra={}, messages=[])
        ctx.extra["_recent_files"] = [
            (str(tmp_path / "ghost.py"), 1.0, "read"),
        ]
        assert restore_recent_files(ctx) == 0
        assert ctx.messages == []
        assert ctx.extra["_recent_files"] == []

    def test_long_file_truncated(self, tmp_path) -> None:
        big = tmp_path / "big.txt"
        big.write_text("x" * 6000, encoding="utf-8")
        ctx = types.SimpleNamespace(extra={}, messages=[])
        ctx.extra["_recent_files"] = [(str(big), 1.0, "edit")]
        assert restore_recent_files(ctx) == 1
        text = ctx.messages[0].content[0].text
        assert "文件过长，已截断" in text
        assert len(text) < 6000

    def test_no_extra_returns_zero(self) -> None:
        ctx = types.SimpleNamespace()
        assert restore_recent_files(ctx) == 0

    def test_empty_recent_files_returns_zero(self) -> None:
        ctx = types.SimpleNamespace(extra={}, messages=[])
        assert restore_recent_files(ctx) == 0

    def test_at_most_5_files_restored(self, tmp_path) -> None:
        files = []
        for i in range(8):
            p = tmp_path / f"f{i}.txt"
            p.write_text(f"content{i}", encoding="utf-8")
            files.append((str(p), float(i), "read"))
        ctx = types.SimpleNamespace(extra={}, messages=[])
        ctx.extra["_recent_files"] = files
        assert restore_recent_files(ctx) == 5


class TestUpdateSessionMemory:
    """会话记忆更新。"""

    @pytest.fixture(autouse=True)
    def _inject_missing_os(self, monkeypatch):
        """绕过源码 bug: compactor.py 顶部缺少 import os。

        `update_session_memory` 内部直接使用 `os.path.exists`，模块级没有 os，
        实际运行会抛 NameError 被 except 吞掉返回 None（会话记忆永远写不进去）。
        这里向模块注入 os 全局名，让测试能够覆盖其正常写入逻辑。
        """
        import os

        # raising=False: 模块本来没有 os 属性，这里作为新增全局名注入
        monkeypatch.setattr(compactor, "os", os, raising=False)

    def test_empty_summary_returns_none(self, tmp_path) -> None:
        result = CompactResult(
            new_messages=[], summary="", pre_compact_tokens=0,
            post_compact_tokens=0, messages_summarized=0, messages_kept=0,
        )
        assert update_session_memory(str(tmp_path), result) is None

    def test_writes_memory_file(self, tmp_path) -> None:
        result = CompactResult(
            new_messages=[], summary="用户偏好：代码要加注释", pre_compact_tokens=100,
            post_compact_tokens=10, messages_summarized=8, messages_kept=4,
        )
        path = update_session_memory(str(tmp_path), result)
        assert path is not None
        # Windows 路径分隔符是反斜杠，归一化后断言
        assert path.replace("\\", "/").endswith(".jarvis/SESSION_MEMORY.md")
        content = (tmp_path / ".jarvis" / "SESSION_MEMORY.md").read_text(encoding="utf-8")
        assert "会话自动记忆" in content  # 头部
        assert "用户偏好：代码要加注释" in content
        assert "压缩了 8 条消息" in content
        assert "100 → 10 tokens" in content

    def test_appends_on_second_call(self, tmp_path) -> None:
        result = CompactResult(
            new_messages=[], summary="第一条摘要", pre_compact_tokens=1,
            post_compact_tokens=1, messages_summarized=1, messages_kept=1,
        )
        update_session_memory(str(tmp_path), result)
        result2 = CompactResult(
            new_messages=[], summary="第二条摘要", pre_compact_tokens=1,
            post_compact_tokens=1, messages_summarized=1, messages_kept=1,
        )
        update_session_memory(str(tmp_path), result2)
        content = (tmp_path / ".jarvis" / "SESSION_MEMORY.md").read_text(encoding="utf-8")
        assert content.count("## ") >= 2
        assert "第一条摘要" in content
        assert "第二条摘要" in content

    def test_long_summary_truncated_to_2000(self, tmp_path) -> None:
        result = CompactResult(
            new_messages=[], summary="长" * 2500, pre_compact_tokens=1,
            post_compact_tokens=1, messages_summarized=1, messages_kept=1,
        )
        path = update_session_memory(str(tmp_path), result)
        content = (tmp_path / ".jarvis" / "SESSION_MEMORY.md").read_text(encoding="utf-8")
        assert "完整摘要省略" in content
        assert path is not None

    def test_failure_returns_none(self, tmp_path) -> None:
        """workdir 是一个文件路径时，_session_memory_path 创建目录失败 → 返回 None。"""
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("i am a file", encoding="utf-8")
        result = CompactResult(
            new_messages=[], summary="摘要", pre_compact_tokens=1,
            post_compact_tokens=1, messages_summarized=1, messages_kept=1,
        )
        assert update_session_memory(str(blocker), result) is None


class TestLoadSessionMemory:
    """会话记忆加载。"""

    def test_missing_returns_empty(self, tmp_path) -> None:
        assert load_session_memory(str(tmp_path / "empty")) == ""

    def test_loads_content(self, tmp_path) -> None:
        mem_dir = tmp_path / ".jarvis"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "SESSION_MEMORY.md").write_text("# 会话自动记忆\n\n内容", encoding="utf-8")
        assert "内容" in load_session_memory(str(tmp_path))

    def test_truncated_to_4000(self, tmp_path) -> None:
        mem_dir = tmp_path / ".jarvis"
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "SESSION_MEMORY.md").write_text("x" * 5000, encoding="utf-8")
        content = load_session_memory(str(tmp_path))
        assert "早期记忆已省略" in content
        assert len(content) <= 4100

    def test_important_sections_kept_over_recent(self, tmp_path) -> None:
        """超长时优先保留含重要段（错误修复/用户反馈）的条目，而非按时间取尾。"""
        mem_dir = tmp_path / ".jarvis"
        mem_dir.mkdir(parents=True, exist_ok=True)
        header = "# 会话自动记忆\n\n> 说明文字\n\n"
        important_old = (
            "## 2026-01-01 10:00\n\n"
            "### 4. 错误与修复\n"
            "- 错误: compactor 缺 import os\n"
            "- 修复: 补全导入\n"
            "\n### 5. 用户反馈\n- 用户偏好：代码要加注释\n"
        )
        recent_noisy = "## 2026-01-02 10:00\n\n" + "普通闲聊内容。" * 60
        content = header + important_old + "\n" + recent_noisy
        (mem_dir / "SESSION_MEMORY.md").write_text(content, encoding="utf-8")

        loaded = load_session_memory(str(tmp_path))
        # 重要段保留（即使它比普通段更早）
        assert "错误与修复" in loaded
        assert "补全导入" in loaded
        assert "代码要加注释" in loaded
        # 不超过预算
        assert len(loaded) <= 4000

    def test_within_budget_returns_full(self, tmp_path) -> None:
        """内容未超预算时原样返回，不做截断。"""
        mem_dir = tmp_path / ".jarvis"
        mem_dir.mkdir(parents=True, exist_ok=True)
        content = "# 会话自动记忆\n\n## 2026-01-01 10:00\n\n少量内容"
        (mem_dir / "SESSION_MEMORY.md").write_text(content, encoding="utf-8")
        assert load_session_memory(str(tmp_path)) == content
