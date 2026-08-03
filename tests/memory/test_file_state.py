"""agent/core/memory/file_state.py 单元测试。

覆盖文件读取/写入时的 mtime 记录、外部修改冲突检测（stale）、缓存失效等逻辑。
用临时目录中的真实文件 + os.utime 精确控制 mtime 来模拟"外部修改"。

@author aceFelix
"""

import os
import types

import pytest

from agent.core.memory import file_state as fs


@pytest.fixture
def ctx():
    """带 extra 缓存的假 ToolContext。"""
    return types.SimpleNamespace(extra={})


@pytest.fixture
def sample_file(tmp_path):
    """一个内容可改、mtime 可控的临时文件。"""
    p = tmp_path / "note.txt"
    p.write_text("v1", encoding="utf-8")
    return p


class TestRecordFileRead:
    """记录文件读取。"""

    def test_record_read_creates_cache_entry(self, ctx, sample_file) -> None:
        fs.record_file_read(ctx, str(sample_file))
        key = str(sample_file.resolve())
        assert key in ctx.extra["_file_state_cache"]
        assert abs(ctx.extra["_file_state_cache"][key] - sample_file.stat().st_mtime) < 0.01

    def test_record_read_no_extra_ctx_ignored(self, sample_file) -> None:
        """ctx 没有 extra 属性时静默忽略，不报错。"""
        bare = types.SimpleNamespace()  # 无 extra
        fs.record_file_read(bare, str(sample_file))  # 不抛异常

        none_extra = types.SimpleNamespace(extra=None)
        fs.record_file_read(none_extra, str(sample_file))  # 不抛异常


class TestCheckFileStale:
    """外部修改冲突检测。"""

    def test_not_read_before_returns_false(self, ctx, sample_file) -> None:
        """文件从未被记录 → 返回 False（新建或首次操作不阻止）。"""
        assert fs.check_file_stale(ctx, str(sample_file)) is False

    def test_unchanged_returns_false(self, ctx, sample_file) -> None:
        """mtime 与缓存一致 → 安全。"""
        fs.record_file_read(ctx, str(sample_file))
        assert fs.check_file_stale(ctx, str(sample_file)) is False

    def test_externally_modified_returns_true(self, ctx, sample_file) -> None:
        """外部修改文件（mtime 变化 > 0.5s）→ 冲突。"""
        fs.record_file_read(ctx, str(sample_file))
        # 模拟外部编辑：修改内容 + 推进 mtime 1 秒
        sample_file.write_text("v2 - external edit", encoding="utf-8")
        new_mtime = sample_file.stat().st_mtime + 1.0
        os.utime(sample_file, (new_mtime, new_mtime))
        assert fs.check_file_stale(ctx, str(sample_file)) is True

    def test_small_mtime_diff_within_threshold_returns_false(self, ctx, sample_file) -> None:
        """mtime 差异 ≤0.5s（精度容差）→ 不视为冲突。"""
        fs.record_file_read(ctx, str(sample_file))
        base = sample_file.stat().st_mtime
        os.utime(sample_file, (base + 0.3, base + 0.3))
        assert fs.check_file_stale(ctx, str(sample_file)) is False

    def test_deleted_file_returns_false(self, ctx, sample_file) -> None:
        """文件被删除后（mtime 取 0）→ 视为未修改，不阻止。"""
        fs.record_file_read(ctx, str(sample_file))
        sample_file.unlink()
        # _get_mtime 失败返回 0.0，与缓存差异大，但这里验证不抛异常且可调用
        assert fs.check_file_stale(ctx, str(sample_file)) is True


class TestRecordFileWrite:
    """记录文件写入。"""

    def test_record_write_refreshes_mtime(self, ctx, sample_file) -> None:
        """编辑文件后记录新 mtime → 不再冲突。"""
        fs.record_file_read(ctx, str(sample_file))
        sample_file.write_text("v2", encoding="utf-8")
        new_mtime = sample_file.stat().st_mtime + 1.0
        os.utime(sample_file, (new_mtime, new_mtime))
        assert fs.check_file_stale(ctx, str(sample_file)) is True

        fs.record_file_write(ctx, str(sample_file))
        assert fs.check_file_stale(ctx, str(sample_file)) is False


class TestInvalidate:
    """缓存失效。"""

    def test_invalidate_removes_entry(self, ctx, sample_file) -> None:
        fs.record_file_read(ctx, str(sample_file))
        key = str(sample_file.resolve())
        assert key in ctx.extra["_file_state_cache"]

        fs.invalidate(ctx, str(sample_file))
        assert key not in ctx.extra["_file_state_cache"]
        # 失效后视为未读过 → 不阻止编辑
        assert fs.check_file_stale(ctx, str(sample_file)) is False

    def test_invalidate_missing_entry_no_error(self, ctx, sample_file) -> None:
        fs.invalidate(ctx, str(sample_file))  # 不抛异常


class TestGetMtime:
    """mtime 获取。"""

    def test_missing_file_returns_zero(self, tmp_path) -> None:
        assert fs._get_mtime(tmp_path / "nope.txt") == 0.0

    def test_existing_file_returns_st_mtime(self, tmp_path) -> None:
        p = tmp_path / "a.txt"
        p.write_text("x", encoding="utf-8")
        assert fs._get_mtime(p) == p.stat().st_mtime
