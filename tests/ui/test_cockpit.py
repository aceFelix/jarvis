"""任务驾驶舱（C1b 独立窗口版）状态与配置测试。

覆盖：步数/失败计数、数据流容量、run_end 最终汇总、抓取记录、
配置归一化与启用判定、文本格式化。窗口渲染在 ``cockpit_window.py``，
不在这里用 Tk（CI 无显示环境）。

@author aceFelix
"""

from __future__ import annotations

from agent.ui.cockpit import (
    CockpitState,
    _fmt_bytes,
    _fmt_ms,
    _short_url,
    cockpit_enabled,
    normalize_mode,
)
from agent.ui.cockpit_window import CockpitWindow


class _Clock:
    """可推进的假单调时钟（毫秒精度，避免依赖真实时间）。"""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0


class TestStateModel:
    def test_state_counts_steps_and_failures(self) -> None:
        """3 步、1 失败 → steps=3、failed=1。"""
        state = CockpitState(_Clock())
        state.update({"type": "run_start", "run_id": "r"})
        for i in range(3):
            state.update({"type": "tool_start", "tool": f"T{i}"})
            state.update({"type": "tool_end", "tool": f"T{i}", "ok": i != 1, "duration_ms": 100})
        assert state.steps == 3
        assert state.failed == 1
        assert state.current is None

    def test_feed_keeps_latest_eight(self) -> None:
        """12 条事件后只留最近 8 条，顺序正确。"""
        state = CockpitState(_Clock())
        state.update({"type": "run_start", "run_id": "r"})
        for i in range(12):
            state.update({"type": "tool_start", "tool": f"T{i}"})
        assert len(state.feed) == 8
        assert "T4" in list(state.feed)[0]
        assert "T11" in list(state.feed)[-1]

    def test_run_end_keeps_final_summary(self) -> None:
        """run_end 后窗口保留最终汇总：步数/失败/耗时取权威值。"""
        clock = _Clock()
        state = CockpitState(clock)
        state.update({"type": "run_start", "run_id": "r"})
        state.update({"type": "tool_start", "tool": "A"})
        clock.advance(300)
        state.update({"type": "tool_end", "tool": "A", "ok": False, "duration_ms": 300})
        clock.advance(5000)  # 墙钟继续走，但最终耗时以 run_end 为准
        state.update(
            {"type": "run_end", "steps": 1, "failed": 1, "duration_ms": 4200}
        )

        assert state.finished is True
        assert state.current is None
        assert state.display_steps() == 1
        assert state.display_failed() == 1
        assert state.elapsed_ms() == 4200.0
        # 未结束时仍用实时墙钟
        state.update({"type": "run_start", "run_id": "r2"})
        assert state.finished is False
        assert state.elapsed_ms() == 0.0

    def test_new_run_resets_previous_state(self) -> None:
        """下一轮 run_start 清空上一轮的步数、抓取与最终汇总。"""
        state = CockpitState(_Clock())
        state.update({"type": "run_start", "run_id": "r1"})
        state.update({"type": "tool_start", "tool": "A"})
        state.update({"type": "web_fetch", "url": "https://example.com", "status": "ok"})
        state.update({"type": "run_end", "steps": 1, "failed": 0, "duration_ms": 100})
        state.update({"type": "run_start", "run_id": "r2"})

        assert state.run_id == "r2"
        assert state.steps == 0
        assert state.failed == 0
        assert len(state.fetches) == 0
        assert state.final_steps is None
        assert state.finished is False

    def test_parallel_tools_are_tracked_by_id(self) -> None:
        """并行工具按 tool_use_id 保留，结束一个不影响其余。"""
        state = CockpitState(_Clock())
        state.update({"type": "run_start", "run_id": "r"})
        for index in range(3):
            state.update(
                {
                    "type": "tool_start",
                    "tool_use_id": f"t{index}",
                    "tool": f"Tool{index}",
                }
            )
        assert len(state.active) == 3
        assert state.active_tools() == ["Tool0", "Tool1", "Tool2"]
        assert state.current["tool"] == "Tool2"

        state.update({"type": "tool_end", "tool_use_id": "t0", "ok": True, "duration_ms": 100})
        assert len(state.active) == 2
        assert state.active_tools() == ["Tool1", "Tool2"]
        assert state.current["tool"] == "Tool2"

        state.update({
            "type": "tool_end",
            "tool_use_id": "t1",
            "ok": False,
            "duration_ms": 200,
            "error": "exit 1",
        })
        assert state.active_tools() == ["Tool2"]
        assert state.failed == 1
        # 失败摘要保留在步历史里，主 HUD 展开详情时展示（2026-11-09）
        failed_entry = next(e for e in state.step_history if e["tool_use_id"] == "t1")
        assert failed_entry["error"] == "exit 1"

        state.update({"type": "tool_end", "tool_use_id": "t2", "ok": True, "duration_ms": 300})
        assert state.active == {}
        assert state.current is None

    def test_web_fetch_record_keeps_fields(self) -> None:
        """web_fetch 只记有值字段；缺失字段不写假值。"""
        state = CockpitState(_Clock())
        state.update(
            {
                "type": "web_fetch",
                "url": "https://example.com/a",
                "status": "ok",
                "duration_ms": 320,
                "bytes": 12288,
            }
        )
        assert len(state.fetches) == 1
        item = state.fetches[0]
        assert item["url"] == "https://example.com/a"
        assert item["bytes"] == 12288
        assert item["duration_ms"] == 320

    def test_web_extract_record_keeps_missing(self) -> None:
        """web_extract 保留缺失字段供驾驶舱显示“缺 N”；无缺失则不写该键。"""
        state = CockpitState(_Clock())
        state.update(
            {
                "type": "web_extract",
                "url": "https://example.com/a",
                "fields": {"title": "T"},
                "missing": ["summary"],
            }
        )
        state.update(
            {
                "type": "web_extract",
                "url": "https://example.com/b",
                "fields": {"title": "B"},
            }
        )
        assert state.extracts[0]["missing"] == ["summary"]
        assert "missing" not in state.extracts[1]

    def test_web_fetch_keeps_crawl_fields(self) -> None:
        """Crawl 派生的 web_fetch 保留 parent_url/depth/kind，供 URL 清单显示层号。"""
        state = CockpitState(_Clock())
        state.update(
            {
                "type": "web_fetch",
                "url": "https://site.test/a",
                "status": "ok",
                "duration_ms": 11,
                "bytes": 6,
                "parent_url": "https://site.test/",
                "depth": 1,
                "kind": "http",
            }
        )
        item = state.fetches[0]
        assert item["parent_url"] == "https://site.test/"
        assert item["depth"] == 1
        assert item["kind"] == "http"

    def test_crawl_stats_keep_depth_and_elapsed(self) -> None:
        """crawl_stats 保留深度与耗时，供拓扑区统计行展示。"""
        state = CockpitState(_Clock())
        state.update(
            {
                "type": "crawl_stats",
                "queued": 0,
                "done": 2,
                "failed": 1,
                "dedup_hits": 3,
                "bytes": 1024,
                "elapsed_ms": 8200,
                "max_pages": 10,
                "max_depth": 2,
            }
        )
        assert state.stats["max_depth"] == 2
        assert state.stats["elapsed_ms"] == 8200

    def test_agent_tool_events_do_not_pollute_main_state(self) -> None:
        """子代理工具不计入主任务步数/失败，也不进主 HUD 数据流。"""
        state = CockpitState(_Clock())
        state.update({"type": "run_start", "run_id": "r"})
        state.update(
            {
                "type": "tool_start",
                "tool": "Grep",
                "agent_id": "sub-1",
                "agent_name": "explorer-1",
            }
        )
        state.update(
            {
                "type": "tool_end",
                "tool": "Grep",
                "ok": False,
                "agent_id": "sub-1",
                "agent_name": "explorer-1",
            }
        )

        assert state.steps == 0
        assert state.failed == 0
        assert state.current is None
        assert list(state.feed) == []


class TestMode:
    def test_normalize_mode(self) -> None:
        """window/off 归一化；历史值 auto、terminal 都按窗口模式。"""
        assert normalize_mode("off") == "off"
        assert normalize_mode("OFF") == "off"
        assert normalize_mode("window") == "window"
        assert normalize_mode("auto") == "window"
        assert normalize_mode("terminal") == "window"
        assert normalize_mode("") == "window"

    def test_normalize_tool_cards(self) -> None:
        """浮窗模式只认 auto/always/off，未知值降级 auto。"""
        from agent.ui.cockpit import normalize_tool_cards

        assert normalize_tool_cards("always") == "always"
        assert normalize_tool_cards("OFF") == "off"
        assert normalize_tool_cards("sometimes") == "auto"
        assert normalize_tool_cards("") == "auto"

    def test_visual_options_clamp_and_normalize(self) -> None:
        """数值越界钳制、枚举归一、总开关生效。"""
        from agent.ui.cockpit import VisualOptions, visual_enabled

        opts = VisualOptions.from_values(
            enabled=True,
            task_cockpit="off",
            tool_cards="auto",
            card_min_ms=-1,
            card_max=99,
            card_hold_ms=99999,
            effects="weird",
        )
        assert opts.task_cockpit == "off"
        assert opts.card_min_ms == 0
        assert opts.card_max == 6
        assert opts.card_hold_ms == 3000
        assert opts.effects == "subtle"
        assert opts.cards_on() is True
        assert opts.cockpit_on() is False
        assert opts.any_enabled() is True
        assert visual_enabled(opts, voice_mode=False) is True
        # 2026-11-09 用户要求：/voice 语音模式也保留操作可视化
        assert visual_enabled(opts, voice_mode=True) is True

        disabled = VisualOptions.from_values(enabled=False)
        assert disabled.any_enabled() is False

    def test_cockpit_enabled(self) -> None:
        """off 不启用；window 启用；语音模式不再抑制（/voice 也要可视化）。"""
        assert cockpit_enabled("window", voice_mode=False) is True
        assert cockpit_enabled("off", voice_mode=False) is False
        assert cockpit_enabled("window", voice_mode=True) is True
        assert cockpit_enabled("off", voice_mode=True) is False

    def test_agent_card_options_normalize_and_clamp(self) -> None:
        """代理卡配置：枚举归一、数值钳制、总开关联动。"""
        from agent.ui.cockpit import VisualOptions

        opts = VisualOptions.from_values(
            agent_cards="weird",
            agent_card_max=99,
            agent_card_hold_ms=-1,
            agent_card_min_ms=99999,
            agent_card_keep_idle=False,
        )
        assert opts.agent_cards == "auto"
        assert opts.agent_card_max == 6
        assert opts.agent_card_hold_ms == 0
        assert opts.agent_card_min_ms == 5000
        assert opts.agent_card_keep_idle is False
        assert opts.agent_cards_on() is True

        off = VisualOptions.from_values(agent_cards="off")
        assert off.agent_cards_on() is False
        # 只开代理卡也算“有可视化窗口”，窗口需要被拉起
        assert off.any_enabled() is True

        disabled = VisualOptions.from_values(enabled=False, agent_cards="auto")
        assert disabled.agent_cards_on() is False
        assert disabled.any_enabled() is False


class TestStepRows:
    """执行序列展示行：最近 4 步 + 失败步固定置顶（不因被挤出而“看不见失败”）。"""

    @staticmethod
    def _entry(ok, name: str = "tool") -> dict:
        return {"tool": name, "ok": ok}

    def test_empty(self) -> None:
        from agent.ui.cockpit import step_rows

        assert step_rows([]) == []

    def test_keeps_last_four_without_failure(self) -> None:
        from agent.ui.cockpit import step_rows

        history = [self._entry(True, str(index)) for index in range(6)]
        rows = step_rows(history)
        assert [row["tool"] for row in rows] == ["2", "3", "4", "5"]

    def test_pins_failure_outside_window(self) -> None:
        """8 步任务里第 1 步失败：仍要出现在第一行，总行数保持 4。"""
        from agent.ui.cockpit import step_rows

        history = [self._entry(False, "failed")] + [
            self._entry(True, str(index)) for index in range(7)
        ]
        rows = step_rows(history)
        assert len(rows) == 4
        assert rows[0]["tool"] == "failed"
        assert [row["tool"] for row in rows[1:]] == ["4", "5", "6"]

    def test_failure_inside_window_not_duplicated(self) -> None:
        from agent.ui.cockpit import step_rows

        history = [self._entry(True, "a")] * 5 + [self._entry(False, "b")]
        rows = step_rows(history)
        assert [row["tool"] for row in rows] == ["a", "a", "a", "b"]


class TestFormatting:
    def test_fmt_ms(self) -> None:
        """毫秒格式化：缺失为空串，<1s 用 ms，≥1s 用秒。"""
        assert _fmt_ms(None) == ""
        assert _fmt_ms(320) == "320ms"
        assert _fmt_ms(4200) == "4.2s"

    def test_fmt_bytes(self) -> None:
        """字节格式化：缺失为空串，KB/MB 自动换算。"""
        assert _fmt_bytes(None) == ""
        assert _fmt_bytes(900) == "900B"
        assert _fmt_bytes(12288) == "12KB"

    def test_short_url(self) -> None:
        """URL 去掉协议前缀并截断。"""
        assert _short_url("https://example.com/a") == "example.com/a"
        long_url = "https://example.com/" + "x" * 60
        assert _short_url(long_url).endswith("…")


class TestWindowChatOnlyTurns:
    """闲聊轮次（无工具调用）不得弹出任务驾驶舱（2026-11-09 用户实测反馈）。

    实际表现：纯聊天也会弹出一张“共 0 步 · 0 工具”的 HUD，标题还写着
    「任务完成」。修复后窗口只在首个工具/抓取事件到达时才创建。
    """

    def _window(self) -> "CockpitWindow":
        win = CockpitWindow()
        counter = {"count": 0}

        def _fake_start() -> None:
            # 测试里不创建真实 Tk：只记一次启动并伪造线程对象
            counter["count"] += 1
            win._thread = object()

        win._ensure_started = _fake_start  # type: ignore[method-assign]
        win._started = counter
        return win

    def test_chat_only_turn_never_starts_window(self) -> None:
        """run_start → run_end 且没有任何工具：既不启动窗口也不入队。"""
        win = self._window()
        win.feed({"type": "run_start", "run_id": "r1"})
        win.feed(
            {
                "type": "run_end",
                "run_id": "r1",
                "steps": 0,
                "failed": 0,
                "duration_ms": 2200,
            }
        )
        assert win._started["count"] == 0
        assert win._thread is None
        assert win._events.empty()

    def test_first_tool_start_replays_run_start(self) -> None:
        """首个工具才拉起窗口，并先补投 run_start 让耗时从任务开始算。"""
        win = self._window()
        win.feed({"type": "run_start", "run_id": "r1"})
        win.feed({"type": "tool_start", "run_id": "r1", "tool": "Bash"})

        queued = []
        while not win._events.empty():
            queued.append(win._events.get_nowait())
        assert [item["type"] for item in queued] == ["run_start", "tool_start"]
        assert queued[0]["run_id"] == "r1"

    def test_chat_turn_keeps_previous_summary(self) -> None:
        """窗口已开：闲聊轮次的 run_start/run_end 被丢弃，不冲成 0 步。"""
        win = self._window()
        win._thread = object()  # 上一轮任务已创建窗口
        win._run_id = "r1"

        win.feed({"type": "run_start", "run_id": "r2"})
        win.feed(
            {
                "type": "run_end",
                "run_id": "r2",
                "steps": 0,
                "failed": 0,
                "duration_ms": 2200,
            }
        )
        assert win._events.empty()
        assert win._pending_run_start is None

        # 下一轮真任务仍然正常：首个工具补投 run_start
        win.feed({"type": "run_start", "run_id": "r3"})
        win.feed({"type": "tool_start", "run_id": "r3", "tool": "Read"})
        queued = []
        while not win._events.empty():
            queued.append(win._events.get_nowait()["type"])
        assert queued == ["run_start", "tool_start"]


class TestWindowHideReopen:
    """驾驶舱窗口“关闭不退出、下一轮自动重现”的行为（不创建真实 Tk）。

    2026-11-08 实机：点 ✕ 关闭窗口导致 jarvis 整个进程退出（Tcl 报
    ``alloc: invalid block``）。改为关闭只 withdraw 隐藏，不销毁 Tk。
    """

    def _window(self) -> "CockpitWindow":
        win = CockpitWindow()
        win._thread = object()  # 非 None，避免 feed 真的拉起 Tk 线程
        return win

    def test_user_close_hides_without_ending_session(self) -> None:
        """点 ✕ 只隐藏，不置 _closed（进程与后续对话继续）。"""
        win = self._window()
        win._run_id = "r1"
        win._close_from_ui()
        assert win._hidden is True
        assert win._hidden_run_id == "r1"
        assert win._closed.is_set() is False

    def test_same_run_stays_hidden_after_close(self) -> None:
        """任务中途关闭：同一 run 后续工具不再弹出（避免关掉又立刻重现）。"""
        win = self._window()
        win._run_id = "r1"
        win._close_from_ui()
        win.feed({"type": "tool_start", "run_id": "r1", "tool": "Bash"})
        assert win._hidden is True

    def test_next_run_tool_start_reopens(self) -> None:
        """下一轮首个 tool_start 自动重新显示，并投递 __show__ 指令。"""
        win = self._window()
        win._run_id = "r1"
        win._close_from_ui()

        win.feed({"type": "run_start", "run_id": "r2"})
        win.feed({"type": "tool_start", "run_id": "r2", "tool": "Bash"})

        assert win._hidden is False
        queued = []
        while not win._events.empty():
            queued.append(win._events.get_nowait()["type"])
        assert "__show__" in queued
        assert "tool_start" in queued

    def test_closed_while_idle_reopens_on_first_tool(self) -> None:
        """空闲时关闭（无 run_id）→ 下一个任务的首个工具就重新显示。"""
        win = self._window()
        win._close_from_ui()
        win.feed({"type": "tool_start", "run_id": "r1", "tool": "Bash"})
        assert win._hidden is False


class TestWindowFormatting:
    def test_display_width_counts_cjk_as_two(self) -> None:
        """CJK 按 2 个显示单元，ASCII 按 1 个。"""
        from agent.ui.cockpit_window import _display_width

        assert _display_width("abc") == 3
        assert _display_width("中文") == 4

    def test_clip_truncates_by_display_width(self) -> None:
        """按显示宽度截断，超长补省略号；短文本原样返回。"""
        from agent.ui.cockpit_window import _clip

        assert _clip("abc", 10) == "abc"
        clipped = _clip("中" * 10, 10)
        assert clipped.endswith("…")
        assert len(clipped) < 10

    def test_fmt_elapsed(self) -> None:
        """总耗时：<1s 用 ms，<60s 用秒，其余用分秒。"""
        from agent.ui.cockpit_window import _fmt_elapsed

        assert _fmt_elapsed(320) == "320ms"
        assert _fmt_elapsed(4200) == "4.2s"
        assert _fmt_elapsed(62000) == "1m02s"
