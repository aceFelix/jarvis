"""图片窗口纯逻辑测试：``agent/ui/image_cache.py``。

这些函数决定「图会不会挤爆窗口」「同名文件改了还显不显示旧图」「默认停在
屏幕左上角且不会被拖出屏幕」。不碰 Tk、不碰 PIL，可在无显示环境跑。

@author aceFelix
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.ui import image_cache as geo


class TestFitSize:
    def test_scales_down_keeping_ratio(self) -> None:
        assert geo.fit_size(2560, 1440, 436, 292) == (436, 245)

    def test_never_upscales(self) -> None:
        """小图不放大：放大只会变糊，还会误导用户以为原图很大。"""
        assert geo.fit_size(40, 20, 400, 300) == (40, 20)

    @pytest.mark.parametrize(
        ("width", "height", "max_w", "max_h"),
        [(0, 100, 100, 100), (100, 0, 100, 100), (100, 100, 0, 100), ("x", 1, 1, 1)],
    )
    def test_invalid_returns_zero(self, width: object, height: object, max_w: object, max_h: object) -> None:
        assert geo.fit_size(width, height, max_w, max_h) == (0, 0)

    def test_main_size_fits_main_area(self) -> None:
        """大图区适配：不超过窗口内区宽高。"""
        width, height = geo.main_size(4000, 3000)
        assert width <= geo.WINDOW_W - 2 * geo.PAD
        assert height <= geo.MAIN_BOTTOM - geo.MAIN_TOP

    def test_thumb_size_uses_limit_on_long_edge(self) -> None:
        width, height = geo.thumb_size(1600, 800, 88)
        assert width == 88
        assert height == 44


class TestCacheKey:
    def test_mtime_is_part_of_key(self) -> None:
        """同路径不同 mtime 必须是两个键，否则覆盖后仍显示旧图。"""
        first = geo.cache_key("C:/a.png", 111, 88)
        second = geo.cache_key("C:/a.png", 222, 88)
        assert first != second

    def test_size_is_part_of_key(self) -> None:
        assert geo.cache_key("C:/a.png", 111, 88) != geo.cache_key("C:/a.png", 111, 120)


class TestFileProbe:
    def test_alive_and_mtime(self, tmp_path: Path) -> None:
        target = tmp_path / "a.png"
        target.write_bytes(b"x")
        assert geo.file_alive(target) is True
        assert geo.file_mtime_ns(target) > 0

    def test_missing_file(self, tmp_path: Path) -> None:
        """ScreenShot 落系统临时目录，被清理后要能安静地判成不存在。"""
        missing = tmp_path / "gone.png"
        assert geo.file_alive(missing) is False
        assert geo.file_mtime_ns(missing) == 0

    def test_none_path_is_not_alive(self) -> None:
        assert geo.file_alive(None) is False


class TestWindowOrigin:
    def test_default_is_top_left(self) -> None:
        """默认停靠屏幕左上角（先生 2026-11-11 指定）。"""
        assert geo.window_origin(1920, 1080, None) == (geo.DEFAULT_X, geo.DEFAULT_Y)
        assert (geo.DEFAULT_X, geo.DEFAULT_Y) == (24, 24)

    def test_restores_saved_position(self) -> None:
        assert geo.window_origin(1920, 1080, (300, 200)) == (300, 200)

    def test_clamps_into_screen(self) -> None:
        """显示分辨率变小后，旧位置要被钳进屏幕内。"""
        assert geo.window_origin(1920, 1080, (9999, 9999)) == (
            1920 - geo.WINDOW_W,
            1080 - geo.WINDOW_H,
        )
        assert geo.window_origin(1920, 1080, (-50, -50)) == (0, 0)

    @pytest.mark.parametrize("saved", [(None,), (), ("x", "y"), {"x": 1}])
    def test_dirty_saved_falls_back(self, saved: object) -> None:
        assert geo.window_origin(1920, 1080, saved) == (geo.DEFAULT_X, geo.DEFAULT_Y)


class TestStripLayout:
    def test_positions_step_by_thumb_plus_gap(self) -> None:
        assert geo.strip_positions(3, 88, gap=8, left=10) == [10, 106, 202]

    def test_zero_count(self) -> None:
        assert geo.strip_positions(0, 88) == []

    def test_tiny_thumb_is_clamped(self) -> None:
        """thumb_px 传 0 也不能退化成叠在一起。"""
        assert geo.strip_positions(2, 0, gap=0, left=0) == [0, 16]


class TestClampIndex:
    def test_bounds(self) -> None:
        assert geo.clamp_index(5, 3) == 2
        assert geo.clamp_index(-1, 3) == 0
        assert geo.clamp_index("x", 3) == 0
        assert geo.clamp_index(0, 0) == 0


class TestHumanBytes:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(None, ""), (0, ""), (-1, ""), (512, "512B"), (2048, "2KB"), (3 * 1024 * 1024, "3.0MB")],
    )
    def test_format(self, value: object, expected: str) -> None:
        assert geo.human_bytes(value) == expected


class TestDescribeMeta:
    def test_three_lines(self) -> None:
        lines = geo.describe_meta(
            {
                "path": "C:/shots/a.png",
                "source": "ScreenShot",
                "width": 2560,
                "height": 1440,
                "media_type": "image/jpeg",
                "bytes": 2048,
                "ts": "2026-11-11T14:00:00+08:00",
            }
        )
        assert lines[0] == "ScreenShot · 2560×1440 · JPEG · 2KB"
        assert lines[1] == "2026-11-11T14:00:00+08:00"
        assert lines[2] == "C:/shots/a.png"

    def test_missing_fields_are_omitted_not_faked(self) -> None:
        """缺字段就省略，不写「未知尺寸」「0B」这种假信息。"""
        lines = geo.describe_meta({"path": "C:/a.png"})
        assert lines[0] == "未知来源"
        assert lines[1] == ""
