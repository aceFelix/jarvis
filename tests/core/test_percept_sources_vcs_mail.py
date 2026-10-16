"""W2 第 3 步：仓库/CI 源与邮件源测试。

vcs 源注入假命令执行器（不真跑 git，避免 CI 上仓库状态不确定），
mail 源注入假 IMAP 连接（不联网、不碰真实邮箱）。两者的核心行为——
"只在跨过提醒线的那一次发事件"与"只读信封头"——都在这里锁死。
@author aceFelix
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.core.percept.source_mail import (
    MailSource,
    decode_header_value,
    envelope_key,
    parse_envelope,
)
from agent.core.percept.source_vcs import VcsSource, parse_stash_count, parse_status


# ---------------------------------------------------------------------------
# git status 解析
# ---------------------------------------------------------------------------


def test_parse_status_plain_branch():
    out = parse_status("## main\n?? a.py\nM  b.py\n")
    assert out == {"branch": "main", "ahead": 0, "behind": 0, "dirty": 2}


def test_parse_status_ahead_behind():
    out = parse_status("## master...origin/master [ahead 3, behind 2]\n")
    assert (out["branch"], out["ahead"], out["behind"], out["dirty"]) == ("master", 3, 2, 0)


def test_parse_status_ahead_only():
    out = parse_status("## feat/x...origin/feat/x [ahead 1]\n?? new.txt\n")
    assert (out["ahead"], out["behind"], out["dirty"]) == (1, 0, 1)


def test_parse_status_detached_and_empty():
    assert parse_status("## HEAD (no branch)\n")["dirty"] == 0
    assert parse_status("") == {"branch": "", "ahead": 0, "behind": 0, "dirty": 0}


def test_parse_stash_count():
    assert parse_stash_count("stash@{0}: WIP\nstash@{1}: On main\n") == 2
    assert parse_stash_count("") == 0


# ---------------------------------------------------------------------------
# vcs 源：只在跨过提醒线时发事件
# ---------------------------------------------------------------------------


class FakeGit:
    """按命令返回预设输出；记录调用次数以证明"每个仓库一轮两次命令"。"""

    def __init__(self, status: str = "## main\n", code: int = 0, stash: str = "") -> None:
        self.status, self.code, self.stash = status, code, stash
        self.calls = 0
        self.raise_missing = False

    def __call__(self, argv, timeout):
        self.calls += 1
        if self.raise_missing:
            raise FileNotFoundError("git 未安装")
        if "stash" in argv:
            return 0, self.stash
        return self.code, self.status


def make_vcs(tmp_path, runner, **kw) -> VcsSource:
    kw.setdefault("repos", [tmp_path / "jarvis"])
    kw.setdefault("dirty_threshold", 3)
    (tmp_path / "jarvis").mkdir(exist_ok=True)   # 目录必须存在，否则按"不可用"处理
    return VcsSource(runner=runner, **kw)


def test_vcs_first_sample_baselines(tmp_path):
    src = make_vcs(tmp_path, FakeGit(status="## main\n?? a\n?? b\n?? c\n"))
    assert src.collect() == []                   # 一开源就把现状当"变化"报是错的
    assert src.state()["snapshot"][str(tmp_path / "jarvis")]["dirty"] == 3


def test_vcs_dirty_crossing_threshold_fires_once(tmp_path):
    src = make_vcs(tmp_path, FakeGit(status="## main\n"))
    src.collect()                                # 基线：干净
    src._runner = FakeGit(status="## main\n?? a\n?? b\n?? c\n")
    kinds = [e.kind for e in src.collect()]
    assert kinds == ["repo_dirty_grew"]
    src._runner = FakeGit(status="## main\n?? a\n?? b\n?? c\n?? d\n")
    assert src.collect() == []                   # 已经很脏了，不再重复催


def test_vcs_unpushed_and_behind_fire_only_on_zero_to_positive(tmp_path):
    src = make_vcs(tmp_path, FakeGit(status="## main...origin/main\n"))
    src.collect()
    src._runner = FakeGit(status="## main...origin/main [ahead 2, behind 1]\n")
    kinds = [e.kind for e in src.collect()]
    assert sorted(kinds) == ["repo_behind", "repo_unpushed"]
    src._runner = FakeGit(status="## main...origin/main [ahead 5, behind 3]\n")
    assert src.collect() == []                   # 两条线都已跨过，不重复


def test_vcs_stash_piles_up(tmp_path):
    runner = FakeGit(status="## main\n", stash="")
    src = make_vcs(tmp_path, runner, stash_threshold=2)
    src.collect()
    runner.stash = "stash@{0}: a\nstash@{1}: b\n"
    events = src.collect()
    assert [e.kind for e in events] == ["repo_stash_piled"]
    assert "2 个 stash" in events[0].summary()


def test_vcs_missing_dir_is_unavailable_not_fatal(tmp_path):
    src = VcsSource(repos=[tmp_path / "nope"], runner=FakeGit(), dirty_threshold=1)
    assert src.collect() == []
    assert src.state()["unavailable"][str(tmp_path / "nope")] == "目录不存在"
    assert src.state()["fatal"] == ""


def test_vcs_bad_repo_doesnt_break_others(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
    runner = FakeGit(status="## main\n?? x\n?? y\n?? z\n")
    src = VcsSource(repos=[a, b], runner=runner, dirty_threshold=3)
    src._last[str(b)] = {"branch": "main", "dirty": 0, "ahead": 0, "behind": 0, "stash": 0}
    src._last[str(a)] = {"branch": "main", "dirty": 0, "ahead": 0, "behind": 0, "stash": 0}
    # 让 a 变成"不是仓库"（退出码非 0），b 仍正常
    original = runner

    def flaky(argv, timeout):
        if str(a) in argv:
            return 128, "fatal: not a git repository"
        return original(argv, timeout)

    src._runner = flaky
    events = src.collect()
    assert [e.payload["repo_name"] for e in events] == ["b"]
    assert "不是可用的 git 仓库" in src.state()["unavailable"][str(a)]


def test_vcs_git_absent_stops_polling(tmp_path):
    """git 没装=环境问题，置 fatal 后不再空转，也不抛错让面板显示"源坏了"。"""
    runner = FakeGit()
    runner.raise_missing = True
    src = make_vcs(tmp_path, runner)
    assert src.collect() == []
    assert src.state()["fatal"] == "git 命令不可用"
    calls_before = runner.calls
    assert src.collect() == []                   # 第二轮直接短路，不再发命令
    assert runner.calls == calls_before


def test_vcs_dedups_same_kind_through_repeated_ticks(tmp_path):
    """仓库一直脏着，总线窗口内同种事件只算一条（验收"不多不少"）。"""
    from agent.core.percept.bus import BusConfig, PerceptBus

    bus = PerceptBus(BusConfig(dedup_window_s=300), log_path=None)
    seen = []
    bus.subscribe(lambda e: seen.append(e.kind))
    src = make_vcs(tmp_path, FakeGit(status="## main\n"), dirty_threshold=2)
    bus.register_source(src, enabled=True, interval_s=60)
    bus.tick(now=1000.0)
    src._runner = FakeGit(status="## main\n?? a\n?? b\n")
    bus.tick(now=1060.0)                         # 跨过线 → 投递
    src._runner = FakeGit(status="## main\n?? a\n?? b\n?? c\n")
    bus.tick(now=1120.0)                         # 脏得更多，但 kind+仓库 同键 → 去重
    assert seen == ["repo_dirty_grew"]


# ---------------------------------------------------------------------------
# 邮件源：信封头解析
# ---------------------------------------------------------------------------


def test_decode_rfc2047_chinese_subject():
    raw = "From: =?UTF-8?B?5byg5LiJ?=<zhang@example.com>\r\nSubject: =?UTF-8?B?5L2g5aW9?=\r\n"
    env = parse_envelope(raw.encode("utf-8"))
    assert env["from"].startswith("张三")
    assert env["subject"] == "你好"


def test_decode_header_tolerates_garbage():
    assert decode_header_value("=?bogus?X?abc?=") != ""  # 解不动也不抛


def test_envelope_key_prefers_message_id():
    assert envelope_key({"message_id": "<a@b>", "from": "x", "subject": "y", "date": "z"}) == "a@b"
    assert envelope_key({"message_id": "", "from": "x", "subject": "y", "date": "z"}) == "x|y|z"


class FakeIMAP:
    """最小可用的假 IMAP：只实现本源用到的方法，并记录调用参数以便断言。"""

    def __init__(self, unseen: list[bytes], headers: dict[bytes, bytes], *, idle=True) -> None:
        self.unseen, self.headers, self._idle = unseen, headers, idle
        self.select_args: list[tuple[str, bool]] = []
        self.fetch_calls: list[bytes] = []
        self.fetch_specs: list[str] = []        # 记录 FETCH 规格，锁死“只取信封头”
        self.logged_out = False
        self.idle_resp: list = [b"2 EXISTS (uidnext 3)"]

    def login(self, user, password):
        return "OK", [""]

    def select(self, mailbox, readonly=False):
        self.select_args.append((mailbox, readonly))
        return "OK", [str(len(self.unseen)).encode()]

    def search(self, charset, criterion):
        return "OK", [b" ".join(self.unseen)]

    def fetch(self, num, spec):
        self.fetch_calls.append(num)
        self.fetch_specs.append(spec)
        return "OK", [(b"1 (FLAGS ())", self.headers[num])]

    def hascapability(self, cap):
        return cap == "IDLE" and self._idle

    def idle(self):
        outer = self

        class _Ctx:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Ctx()

    def idle_check(self, timeout=0.5):
        return "OK", list(self.idle_resp)

    def logout(self):
        self.logged_out = True
        return "BYE", [""]


def header_of(frm: str, subj: str, mid: str) -> bytes:
    return f"From: {frm}\r\nSubject: {subj}\r\nDate: Tue, 16 Oct 2026 09:00:00 +0800\r\nMessage-ID: {mid}\r\n\r\n".encode()


class _OneShotStop:
    """假停止位：第一圈不拦（让 _run 真的跑完一轮），之后拦（立即退出）。

    用替身而不是真 threading.Event + 真 sleep，是为了把异常降级行为测得
    确定且不耗时（降级后的等待时长是百秒量级，不能真等）；`probe` 在
    即将休眠前回调，供断言“循环里那一瞬的模式与时长”（退出后 mode 会变回 off）。
    """

    def __init__(self, probe=None) -> None:
        self.hits = 0
        self._probe = probe

    def is_set(self) -> bool:
        return self.hits > 0

    def wait(self, timeout) -> bool:
        if self._probe is not None:
            self._probe(timeout)
        self.hits += 1
        return True

    def set(self) -> None:
        self.hits += 1


def make_mail(tmp_path, conn, **kw) -> MailSource:
    kw.setdefault("host", "imap.example.com")
    kw.setdefault("user", "me@example.com")
    kw.setdefault("password", "auth-code")
    kw.setdefault("seen_path", tmp_path / "percept" / "mail_seen.json")
    src = MailSource(connector=lambda h, p: conn, **kw)
    # 测试不拉后台采集线程（线程会并发改队列，断言就不可靠了）；
    # “collect 会懒起线程”本身由下面两个专用用例验证。
    src._ensure_thread = lambda: None
    return src


def test_mail_select_is_readonly_and_peek_only_headers(tmp_path):
    """只读 + PEEK：不能把邮件标成已读，也不能碰正文——这是决策 3 的硬约束。"""
    conn = FakeIMAP([b"1"], {b"1": header_of("张三 <z@x.com>", "季度报表", "<m1@x>")})
    src = make_mail(tmp_path, conn)
    src._harvest(conn)
    assert conn.select_args == [(src._mailbox, True)]     # readonly=True：不置 \Seen
    assert conn.fetch_calls == [b"1"]
    # 断言真正用到的 FETCH 规格（防止以后被误改成 RFC822/正文拉取）
    spec = conn.fetch_specs[0]
    assert "BODY.PEEK[HEADER.FIELDS" in spec
    assert "BODY[" not in spec.replace("BODY.PEEK[", "")   # 没有非 PEEK 的取法
    assert "HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)" in spec


def test_mail_event_contains_envelope_only(tmp_path):
    conn = FakeIMAP([b"1"], {b"1": header_of("李四 <l@x.com>", "会议邀请", "<m2@x>")})
    src = make_mail(tmp_path, conn)
    src._harvest(conn)
    events = src.collect()
    assert len(events) == 1 and events[0].kind == "mail_received"
    assert events[0].payload["from"] == "李四 <l@x.com>"
    assert "会议邀请" in events[0].summary()
    assert events[0].dedup_key == "mail:m2@x"        # 去重键用 Message-ID（去尖括号）
    assert "auth-code" not in json.dumps(events[0].to_dict(), ensure_ascii=False)  # 凭据不入事件


def test_mail_first_round_is_silent_baseline(tmp_path):
    """首轮只记账不播报：否则一开源就把陈年未读全播一遍。"""
    conn = FakeIMAP([b"1", b"2"], {
        b"1": header_of("a@x.com", "旧邮件一", "<o1@x>"),
        b"2": header_of("b@x.com", "旧邮件二", "<o2@x>"),
    })
    src = make_mail(tmp_path, conn)
    src._cycle()                                  # first=True
    assert len(src._queue) == 0
    assert src.state()["seen"] == 2


def test_mail_new_arrival_after_baseline_is_reported(tmp_path):
    conn = FakeIMAP([b"1"], {b"1": header_of("a@x.com", "旧邮件", "<o1@x>")})
    src = make_mail(tmp_path, conn)
    src._cycle()                                  # 基线
    conn.unseen.append(b"2")
    conn.headers[b"2"] = header_of("boss@x.com", "马上要看", "<n1@x>")
    src._cycle()
    assert len(src._queue) == 1
    assert src.collect()[0].payload["subject"] == "马上要看"


def test_mail_seen_survives_restart(tmp_path):
    """readonly 下未读邮件一直是未读，去重必须落盘，否则每轮轮询重播同一封。"""
    conn = FakeIMAP([b"1"], {b"1": header_of("a@x.com", "同一封", "<same@x>")})
    first = make_mail(tmp_path, conn)
    first._harvest(conn)
    first._save_seen()
    assert first.collect()[0].payload["subject"] == "同一封"
    assert first.collect() == []                  # 队列已空

    second = make_mail(tmp_path, conn)            # 新实例=进程重启
    assert second.state()["seen"] == 1
    assert second._harvest(conn) == 0             # 同一封不再入队


def test_mail_watch_senders_filters_others(tmp_path):
    conn = FakeIMAP([b"1", b"2"], {
        b"1": header_of("boss@x.com", "重要的事", "<w1@x>"),
        b"2": header_of("newsletter@spam.com", "订阅推广", "<w2@x>"),
    })
    src = make_mail(tmp_path, conn, watch_senders=("boss@",))
    src._harvest(conn)
    events = src.collect()
    assert [e.payload["from"] for e in events] == ["boss@x.com"]
    assert events[0].salience_hint == 0.75        # 命中白名单的先验更高
    assert src.state()["filtered"] == 1


def test_mail_degrades_to_poll_on_error(tmp_path):
    """连接异常不能崩线程：记下末次错误、退到定时轮询、按轮询间隔重试。"""
    def boom(host, port):
        raise OSError("连接被重置")

    src = MailSource(host="imap.x.com", user="u@x.com", password="p", connector=boom,
                     seen_path=tmp_path / "s.json")
    seen: dict = {}
    src._stop = _OneShotStop(probe=lambda wait: seen.update(
        mode=src.mode, wait=wait, error=src.last_error))
    src._run()                               # 只跑一轮就退出，不拉真线程/真时钟
    assert seen["mode"] == "poll"            # 降级：不再是 idle
    assert seen["wait"] == src._poll_s       # 降级：按轮询间隔重试（不是 0 也不是永久退出）
    assert seen["error"].startswith("OSError")
    assert src.configured() is True          # 配置是齐的，只是网络挂了


def test_mail_poll_mode_when_server_lacks_idle(tmp_path):
    conn = FakeIMAP([], {}, idle=False)
    src = make_mail(tmp_path, conn, poll_interval_s=300)
    src._cycle()
    assert src.mode == "poll"


def test_mail_idle_wait_reports_new_mail(tmp_path):
    conn = FakeIMAP([], {})
    src = make_mail(tmp_path, conn, idle_tick_s=60)
    conn.idle_resp = [b"3 EXISTS"]
    assert src._wait_idle(conn) == 0
    conn.idle_resp = [b"still here"]
    assert src._wait_idle(conn) == 60


def test_mail_unconfigured_starts_nothing(tmp_path):
    """没配邮箱时：collect 只返回空、线程不起、也不报错（"开了但没配齐"口径）。"""
    src = MailSource(seen_path=tmp_path / "s.json")
    assert src.collect() == []
    assert src._thread is None and src.mode == "off"
    assert src.state()["configured"] is False


def test_mail_close_logs_out(tmp_path):
    conn = FakeIMAP([], {})
    src = make_mail(tmp_path, conn)
    src._conn = conn
    src._thread = None                        # 让 close 不去 join 一个不存在的线程
    src.close()
    assert conn.logged_out is True
    assert src.mode == "off"


def test_mail_collect_lazily_starts_one_thread(tmp_path):
    """懒起线程是本源的“零空载”前提：配齐了、第一次被采集才起，且只起一个。"""
    def boom(host, port):
        raise OSError("慢连接")               # 采集线程里失败也不影响断言

    src = MailSource(host="imap.x.com", user="u@x.com", password="p", connector=boom,
                     seen_path=tmp_path / "s.json")
    try:
        assert src.collect() == []
        first = src._thread
        assert first is not None and first.is_alive()
        assert src.collect() == []
        assert src._thread is first           # 幂等：不会每轮新起一个
    finally:
        src.close()
    assert src._thread is None


# ---------------------------------------------------------------------------
# 工厂接线（vcs/mail 两源的新配置字段要真的传到源实例）
# ---------------------------------------------------------------------------


def test_factory_passes_vcs_and_mail_params(tmp_path):
    """percept_vcs_* / email_imap_* 必须流到源实例，否则配置面板是假的。"""
    from agent.config.settings import Settings
    from agent.core.percept import build_sources

    repo = tmp_path / "jarvis"
    repo.mkdir()
    settings = Settings(
        percept_vcs_enabled=True,
        percept_vcs_repos=[str(repo)],
        percept_vcs_interval_s=90,
        percept_mail_enabled=True,
        percept_mail_watch_senders=["boss@", "妈妈"],
        email_imap_host="imap.x.com",
        email_imap_port=143,
        email_imap_user="me@x.com",
        email_imap_password="code",
        email_imap_mailbox="INBOX.New",
    )
    pairs = build_sources(settings)
    vcs = next(s for s, _ in pairs if s.name == "vcs")
    mail = next(s for s, _ in pairs if s.name == "mail")
    assert vcs.state()["repos"] == [str(repo)]
    assert vcs.interval_s == 90.0
    assert mail.state()["host"] == "imap.x.com"
    assert mail.state()["mailbox"] == "INBOX.New"
    assert mail.state()["watch_senders"] == ["boss@", "妈妈"]   # 白名单也得从配置流进来
    assert mail.configured() is True
    # 逐源开关必须原样反映配置（工厂不擅自放宽）
    assert dict((s.name, e) for s, e in pairs)["vcs"] is True


def test_factory_mail_credentials_fall_back_to_smtp(tmp_path):
    """imap_user/imap_password 留空时沿用 SMTP 同一套（163/QQ 就是一个授权码两边用）。"""
    from agent.config.settings import Settings
    from agent.core.percept import build_sources

    settings = Settings(
        percept_mail_enabled=True,
        email_imap_host="imap.163.com",
        email_smtp_user="me@163.com",
        email_smtp_password="same-code",
    )
    sources = build_sources(settings)
    mail = next(s for s, _ in sources if s.name == "mail")
    assert mail.configured() is True
    assert mail.state()["host"] == "imap.163.com"
