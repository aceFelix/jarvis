"""W2 感知源测试：前台窗口 + 文件守望 + 源装配工厂 + [percept] 配置解析。

窗口源与文件源都通过注入（probe / scanner / 假时钟 / tmp 状态文件）来测，
不依赖真实前台窗口、不碰真实 ~/.jarvis，也不需要在 CI 上装 pywin32。
@author aceFelix
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.config.settings import Settings, _apply_toml
from agent.core.percept import build_sources, register_sources
from agent.core.percept.bus import BusConfig, PerceptBus
from agent.core.percept.source_file import FileSource, scan_dir
from agent.core.percept.source_window import WindowSource, redact_title


class FakeClock:
    """可控单调时钟：停留/节流类逻辑不靠 sleep 测。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_window(probe, *, clock=None, **kw):
    clock = clock or FakeClock()
    src = WindowSource(probe=probe, sleep=clock, **kw)
    return src, clock


# ---------------------------------------------------------------------------
# 前台窗口源：脱敏
# ---------------------------------------------------------------------------


def test_redact_truncates_by_default():
    long = "详细设计文档 " + "x" * 80
    out = redact_title(long)
    assert len(out) == 41 and out.endswith("…")  # 40 字 + 省略号


def test_redact_strips_credential_fragments():
    """标题里的 URL query 凭据与"密码：xxx"必须被剥掉，完整标题模式也不例外。"""
    got = redact_title("登录 https://a.com?token=SECRET123 结尾", full=True)
    assert "SECRET123" not in got and "token=" not in got
    got2 = redact_title("网银 密码：abc123 请核对", full=True)
    assert "abc123" not in got2


def test_redact_zero_chars_drops_title_entirely():
    """max_chars<=0 = 只要应用名（非 Windows 下拿不到进程名时事件会退化为只报标题）。"""
    assert redact_title("任何标题", max_chars=0) == ""
    assert redact_title("任何标题", max_chars=-5) == ""


# ---------------------------------------------------------------------------
# 前台窗口源：事件语义
# ---------------------------------------------------------------------------


def test_first_sample_baselines_without_event():
    """首采不发事件：否则一开源就播"切到 Chrome"，用户根本没切换。"""
    src, _ = make_window(lambda: ("chrome.exe", "GitHub - J.A.R.V.I.S"))
    assert src.collect() == []
    assert src.state()["process"] == "chrome.exe"


def test_focus_change_emits_once_with_prev_process():
    src, _ = make_window(lambda: ("chrome.exe", "GitHub"))
    src.collect()
    src._probe = lambda: ("Code.exe", "bus.py — jarvis")
    events = src.collect()
    assert len(events) == 1 and events[0].kind == "focus_changed"
    assert events[0].payload["prev_process"] == "chrome.exe"
    assert "Code.exe" in events[0].summary()


def test_same_app_title_change_emits_nothing():
    """同一应用内换标签页不值得打扰（进程名没变）。"""
    src, _ = make_window(lambda: ("chrome.exe", "第一个页面"))
    src.collect()
    src._probe = lambda: ("chrome.exe", "第二个页面")
    assert src.collect() == []


def test_lock_screen_or_no_hwnd_emits_nothing():
    """拿不到句柄（锁屏/服务会话）时宁可不知道，也不发"切到空应用"的假事件。"""
    src, _ = make_window(lambda: ("", ""))
    assert src.collect() == []


def test_excluded_app_is_never_reported_but_state_tracks():
    """密码管理器：零事件 + 计数留痕；切回正常应用时仍能正常报。"""
    src, _ = make_window(lambda: ("KeePass.exe", "个人银行密码"), exclude_apps=("keepass",))
    assert src.collect() == []
    assert src.state()["excluded_hits"] == 1
    src._probe = lambda: ("chrome.exe", "GitHub")
    events = src.collect()
    assert events == []  # 排除期间未建基线 → 这次仍然只是建基线
    src._probe = lambda: ("Code.exe", "bus.py")
    assert [e.kind for e in src.collect()] == ["focus_changed"]


def test_probe_error_propagates_for_bus_isolation():
    """探测失败要如实抛出——总线靠它做连败统计与自动停用，吞掉就失去保护。"""
    def boom():
        raise OSError("pygetwindow 未安装")

    src, _ = make_window(boom)
    with pytest.raises(OSError):
        src.collect()


def test_linger_fires_once_then_silent():
    """停留超时：到点发一次，继续待着不重复催；切走再回来才重置。"""
    clock = FakeClock()
    src, clock = make_window(lambda: ("chrome.exe", "YouTube"), linger_minutes=30, clock=clock)
    src.collect()                      # 建基线，t=1000
    clock.advance(29 * 60)
    assert src.collect() == []         # 29 分钟不到点
    clock.advance(2 * 60)
    events = src.collect()
    assert [e.kind for e in events] == ["focus_linger"]
    assert "31 分钟" in events[0].summary()
    clock.advance(60 * 60)
    assert src.collect() == []         # 已催过一次，不再重复
    src._probe = lambda: ("Code.exe", "bus.py")
    assert [e.kind for e in src.collect()] == ["focus_changed"]
    src._probe = lambda: ("Code.exe", "bus.py")
    clock.advance(31 * 60)
    assert [e.kind for e in src.collect()] == ["focus_linger"]  # 换应用后重新计时


def test_window_events_dedup_by_process_through_bus(tmp_path):
    """来回切同一个应用，总线 5 分钟窗口内只算一件事（验收"各产出恰好一条"）。"""
    bus = PerceptBus(BusConfig(dedup_window_s=300), log_path=None)
    seen = []
    bus.subscribe(lambda e: seen.append(e.summary()))
    src, _ = make_window(lambda: ("chrome.exe", "GitHub"), exclude_apps=())
    bus.register_source(src, enabled=True, interval_s=5)

    bus.tick(now=1000.0)               # 建基线
    src._probe = lambda: ("Code.exe", "bus.py")
    bus.tick(now=1010.0)               # 切到 Code → 投递
    src._probe = lambda: ("chrome.exe", "GitHub")
    bus.tick(now=1020.0)               # 切回 Chrome → 投递
    src._probe = lambda: ("Code.exe", "bus.py")
    bus.tick(now=1030.0)               # 又切回 Code → 同键命中去重
    assert len(seen) == 2
    assert bus.stats()["counters"]["dedup"] == 1


# ---------------------------------------------------------------------------
# 文件守望源
# ---------------------------------------------------------------------------


def touch(root: Path, rel: str, content: str = "x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def make_file(tmp_path, dirs=None, **kw):
    kw.setdefault("state_path", tmp_path / "percept" / "file_state.json")
    return FileSource(watch_dirs=dirs if dirs is not None else [tmp_path / "Downloads"],
                      **kw)


def test_first_scan_only_baselines(tmp_path):
    """首扫不报现存文件：否则开源瞬间就是"新增 800 个文件"的假堆积。"""
    src = make_file(tmp_path)
    touch(src._dirs[0], "已有.pdf")
    assert src.collect() == []
    assert src.state()["baselined"][str(src._dirs[0])] is True


def test_single_new_file_is_named(tmp_path):
    src = make_file(tmp_path, grow_threshold=5)
    src.collect()
    touch(src._dirs[0], "report.docx", "hello")
    events = src.collect()
    assert [e.kind for e in events] == ["file_added"]
    assert events[0].payload["samples"] == ["report.docx"]
    assert events[0].payload["added"] == 1


def test_many_new_files_are_aggregated(tmp_path):
    src = make_file(tmp_path, grow_threshold=3)
    src.collect()
    for i in range(7):
        touch(src._dirs[0], f"img_{i}.png")
    events = src.collect()
    assert [e.kind for e in events] == ["dir_grew"]
    assert events[0].payload["added"] == 7
    assert "新增 7 个文件" in events[0].summary()


def test_deletion_and_modification_are_quiet(tmp_path):
    """删除与修改是用户自己的动作，不该被管家提醒。"""
    src = make_file(tmp_path)
    src.collect()
    p = touch(src._dirs[0], "note.md")
    src.collect()                        # 一次新增（该报）
    p.write_text("changed", encoding="utf-8")
    assert src.collect() == []           # 修改：静默
    p.unlink()
    assert src.collect() == []           # 删除：静默


def test_no_watch_dirs_is_configured_not_broken(tmp_path):
    """开了源但没填目录=配置未完成，返回空而不是抛错（否则面板会显示"源坏了"）。"""
    src = FileSource(watch_dirs=[], state_path=tmp_path / "st.json")
    assert src.collect() == []


def test_missing_directory_is_tolerated(tmp_path):
    src = make_file(tmp_path, dirs=[tmp_path / "不存在的目录"])
    assert src.collect() == []


def test_snapshot_survives_restart_and_reports_offline_growth(tmp_path):
    """核心价值：jarvis 关着期间攒下的新文件，重启后作为一次真实堆积报出来。"""
    first = make_file(tmp_path)
    first.collect()
    for i in range(6):
        touch(first._dirs[0], f"a{i}.log")

    second = make_file(tmp_path)         # 同一 state 文件的新实例=进程重启
    events = second.collect()
    assert [e.kind for e in events] == ["dir_grew"]
    assert events[0].payload["added"] == 6


def test_corrupt_state_is_treated_as_no_baseline(tmp_path):
    state = tmp_path / "percept" / "file_state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{这不是合法 JSON", encoding="utf-8")
    src = FileSource(watch_dirs=[tmp_path / "d"], state_path=state)
    touch(Path(src._dirs[0]), "x.txt")
    assert src.collect() == []           # 当作没基线：静默建基线而不是抛


def test_state_file_is_written_and_readable(tmp_path):
    src = make_file(tmp_path)
    src.collect()
    payload = json.loads(Path(src.state()["state_path"]).read_text(encoding="utf-8"))
    assert payload["version"] == 1 and str(src._dirs[0]) in payload["dirs"]


def test_machine_dirs_hidden_and_partial_files_are_skipped(tmp_path):
    root = tmp_path / "proj"
    touch(root, "src/main.py")
    touch(root, "node_modules/left-pad/index.js")
    touch(root, ".git/config")
    touch(root, ".DS_Store")
    touch(root, "big.iso.part")
    snap, truncated = scan_dir(root, max_entries=100)
    assert list(snap) == ["src/main.py"]
    assert truncated is False


def test_recursive_paths_use_forward_slash_and_flag_truncation(tmp_path):
    root = tmp_path / "deep"
    touch(root, "a/b/c/deep.txt")
    snap, truncated = scan_dir(root, max_entries=100)
    assert list(snap) == ["a/b/c/deep.txt"]     # 反斜杠已归一为 /
    assert truncated is False
    _, truncated2 = scan_dir(root, max_entries=0)
    assert truncated2 is True                    # 命中上限即标记快照不完整


def test_truncated_still_emits_event_with_flag(tmp_path):
    src = make_file(tmp_path, max_entries=2, grow_threshold=2)
    src.collect()
    d = src._dirs[0]
    for i in range(5):
        touch(d, f"f{i}.txt")
    events = src.collect()
    assert events and events[0].payload["truncated"] is True


def test_dir_grew_dedups_through_bus(tmp_path):
    """同一目录 5 分钟内反复堆积只提醒一条（验收"不多不少"的目录侧）。"""
    bus = PerceptBus(BusConfig(dedup_window_s=300), log_path=None)
    seen = []
    bus.subscribe(lambda e: seen.append(e.payload["added"]))
    root = tmp_path / "Downloads"
    src = make_file(tmp_path, grow_threshold=2)
    bus.register_source(src, enabled=True, interval_s=30)
    bus.tick(now=1000.0)                              # 建基线
    for i in range(4):
        touch(root, f"n{i}.bin")
    bus.tick(now=1030.0)                              # 4 个新增 → 投递
    for i in range(4, 8):
        touch(root, f"n{i}.bin")
    bus.tick(now=1060.0)                              # 又 4 个 → 同档去重
    assert seen == [4]
    assert bus.stats()["counters"]["dedup"] == 1


# ---------------------------------------------------------------------------
# 工厂与配置
# ---------------------------------------------------------------------------


def test_build_sources_none_settings():
    assert build_sources(None) == []


def test_default_settings_registers_sources_all_disabled():
    """默认配置（出厂口径）：源都登记，但一个都不启用。"""
    bus = PerceptBus(BusConfig(), log_path=None)
    status = register_sources(bus, Settings())
    assert status == {"window": False, "file": False, "vcs": False, "mail": False,
                      "vision": False}
    st = bus.stats()["sources"]
    assert st["window"]["enabled"] is False and st["file"]["enabled"] is False
    assert st["vcs"]["enabled"] is False and st["mail"]["enabled"] is False
    assert st["vision"]["enabled"] is False
    # 一个源都没开 → 总线不该起轮询线程（空载 CPU 增量为 0 的依据）
    assert bus.start() is False and bus.running is False


def test_master_switch_off_disables_every_source():
    """总开关关时逐源“各自开着”也必须不算开（第 4 步接线时补上的漏采口子）。"""
    settings = Settings(
        percept_enabled=False,
        percept_window_enabled=True, percept_vcs_enabled=True,
        percept_mail_enabled=True, percept_vision_enabled=True,
    )
    bus = PerceptBus(BusConfig(), log_path=None)
    assert all(v is False for v in register_sources(bus, settings).values())
    assert bus.start() is False                     # 宿主一句 start 也不会开采


def test_register_is_idempotent_and_takes_new_params():
    bus = PerceptBus(BusConfig(), log_path=None)
    settings = Settings(percept_enabled=True, percept_window_enabled=True,
                        percept_window_interval_s=9)
    register_sources(bus, settings)
    register_sources(bus, settings)          # 同名替换，不产生幽灵源
    assert sorted(bus.source_names()) == ["file", "mail", "vcs", "vision", "window"]
    assert bus.stats()["sources"]["window"]["interval_s"] == 9.0
    assert bus.stats()["sources"]["window"]["enabled"] is True


def test_percept_toml_table_maps_to_settings():
    data = {
        "percept": {
            "enabled": True,
            "window_enabled": True,
            "file_watch_dirs": ["E:/Downloads"],
            "file_min_interval_s": 45,
            "dedup_window_s": 60,
            "min_salience": 0.2,
            "log_events": False,
            "mail_watch_senders": ["boss@", "妈妈"],
            "已废弃键": 1,
        }
    }
    s = _apply_toml(Settings(), data)
    assert s.percept_enabled is True and s.percept_window_enabled is True
    assert s.percept_file_watch_dirs == ["E:/Downloads"]
    assert s.percept_dedup_window_s == 60 and s.percept_min_salience == 0.2
    assert s.percept_log_events is False
    assert s.percept_file_enabled is False   # 未出现的键保持默认（全关）
    # 第 3 步新增的两个字段也要能被解析（总线节流与邮件白名单）
    assert s.percept_file_min_interval_s == 45
    assert s.percept_mail_watch_senders == ["boss@", "妈妈"]


def test_percept_absent_table_keeps_everything_off():
    """旧 settings.toml 没有 [percept] 表 → 全部保持安全缺省。"""
    s = _apply_toml(Settings(), {"monitor": {"enabled": True}})
    assert s.percept_enabled is False and s.percept_window_enabled is False
    assert s.percept_file_watch_dirs == [] and s.percept_vcs_repos == []
    assert s.percept_mail_watch_senders == [] and s.percept_vision_enabled is False
    assert s.percept_vision_fps == 2                 # 视觉默认低帧率（常驻成本口径）


def test_watch_dirs_list_is_copied_not_aliased():
    """数组要复制：否则两个 Settings 实例共享同一个可变列表，改一个影响另一个。"""
    shared = ["E:/Downloads"]
    a = _apply_toml(Settings(), {"percept": {"file_watch_dirs": shared}})
    shared.append("E:/other")
    assert a.percept_file_watch_dirs == ["E:/Downloads"]
