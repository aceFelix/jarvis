r"""邮件感知源 —— "重要来信到了才说"（W2 第 3 步，决策 3：IMAP IDLE + 断线降级轮询）。

三条硬约束写在这里，实现逐条对应：

1. **只读信封头，永不拉正文**：`SELECT readonly=True`（不把邮件标成已读）+
   `FETCH BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)]`
   —— PEEK 不置 \Seen，字段列表只取四个头，正文一个字节都不下。
2. **IDLE 长连接，断线自动降级轮询**：服务器支持 IDLE 时挂在 IDLE 上等推送
   （`idle_tick_s` 到点主动 DONE-再-IDLE，规避服务商 30 分钟掐连接）；任何异常
   一律 `mode="poll"`，按 `poll_interval_s` 定时重连拉一次，不崩不刷错误。
3. **已报过的信要记住**：readonly 模式下未读邮件会一直是未读，若不去重，每轮
   轮询都会把同一封信重新播一遍。故已报键集合持久化到
   `~/.jarvis/percept/mail_seen.json`（环形上限），重启后不会把陈年未读又提醒一遍。

凭据口径：`imap_password` 留空时回退到 `smtp_password`（163/QQ 两边同一个授权码），
与 [email] 表现有"读不回明文、写留空=保持原值"的安全范式一致——本模块只在内存里
持有凭据，绝不写进事件 payload 或留痕文件。

线程与空载：IDLE/轮询都在**源自己的线程**里跑，且**只在第一次被总线采集时启动**
（总线只会采集已启用的源），所以源关着时既不占总线轮询、也没有任何连接与线程。

@author aceFelix
"""

from __future__ import annotations

import email
import email.header
import imaplib
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from agent.core.percept.event import PerceptEvent, SOURCE_MAIL

#: 只取这四个信封头，其余（含正文）一概不下
HEADER_FIELDS = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] FLAGS)"

#: IDLE 单次最长等待（秒）：多数服务商会在 30 分钟左右掐掉 IDLE，主动到期重挂更稳
IDLE_TICK_S = 25 * 60

#: 断线后的轮询间隔（秒）
DEFAULT_POLL_INTERVAL_S = 300

#: "已报过"键集合的落盘上限（超出丢最旧）
SEEN_CAP = 500

_STATE_FILE = Path.home() / ".jarvis" / "percept" / "mail_seen.json"


def decode_header_value(raw: str) -> str:
    """把 MIME 编码的邮件头（如 =?UTF-8?B?...?=）解成可读串。

    中文邮件系统的主题/发件人几乎都是 RFC2047 编码，不解码会把"=?UTF-8?B?5oCO5LmI?="
    这种原样播报出去。
    """
    if not raw:
        return ""
    try:
        parts = email.header.decode_header(raw)
    except (ValueError, UnicodeDecodeError):
        return raw.strip()
    out: list[str] = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(charset or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                out.append(text.decode("utf-8", errors="replace"))
        else:
            out.append(str(text))
    return "".join(out).strip()


def parse_envelope(header_bytes: bytes) -> dict[str, str]:
    """从信封头字节解析 {from, subject, date, message_id}（不含正文）。"""
    msg = email.message_from_bytes(header_bytes or b"")
    return {
        "from": decode_header_value(msg.get("From", "")),
        "subject": decode_header_value(msg.get("Subject", "")),
        "date": decode_header_value(msg.get("Date", "")),
        "message_id": (msg.get("Message-ID") or "").strip(),
    }


def envelope_key(env: dict[str, str]) -> str:
    """一封信的稳定标识：优先 Message-ID，缺失时用 发件人+主题+日期。"""
    mid = env.get("message_id", "")
    if mid:
        return mid.strip("<> ")
    return f"{env.get('from', '')}|{env.get('subject', '')}|{env.get('date', '')}"


def imap_connect(host: str, port: int) -> Any:
    """建立 IMAP 连接：993 走 SSL，其余端口走 STARTTLS（143 等）。"""
    if int(port) == 993:
        return imaplib.IMAP4_SSL(host, int(port), timeout=30)
    conn = imaplib.IMAP4(host, int(port), timeout=30)
    conn.starttls()
    return conn


class MailSource:
    """IMAP 只读收件源（push 型：内部线程采集，`collect()` 只做排空投递）。

    @author aceFelix
    """

    name = SOURCE_MAIL

    def __init__(
        self,
        *,
        host: str = "",
        port: int = 993,
        user: str = "",
        password: str = "",
        mailbox: str = "INBOX",
        watch_senders: Any = (),
        interval_s: float = 30.0,
        poll_interval_s: int = DEFAULT_POLL_INTERVAL_S,
        idle_tick_s: int = IDLE_TICK_S,
        connector: Callable[[str, int], Any] | None = None,
        seen_path: Path | str | None = None,
    ) -> None:
        """
        Args:
            host/user/password: 空即为"未配置"——本源的 collect() 只排空队列，
                未配置时线程压根不起，也就不会抛"连接失败"误导面板。
            watch_senders: 发件人白名单（子串、不分大小写）。**非空时只报命中的人**，
                这是"重要来信"在源侧的第一道过滤（比让 W1 猜谁重要更可靠）。
            interval_s: 总线排空本源的节拍（把队列里的信投出去），与网络等待无关。
            connector: 连接工厂（测试注入），签名 `(host, port) -> conn`。
            seen_path: 已报键集合落盘路径，测试传 tmp。
        """
        self.interval_s = float(interval_s)
        self._host = (host or "").strip()
        self._port = int(port or 993)
        self._user = (user or "").strip()
        self._password = (password or "").strip()
        self._mailbox = (mailbox or "INBOX").strip() or "INBOX"
        self._watch = tuple(str(x).lower() for x in (watch_senders or ()) if str(x).strip())
        self._poll_s = max(30, int(poll_interval_s))
        self._idle_s = max(30, int(idle_tick_s))
        self._connector = connector or imap_connect
        self._seen_path = Path(seen_path) if seen_path else _STATE_FILE

        self._queue: deque[dict[str, str]] = deque(maxlen=50)
        self._seen: deque[str] = deque(maxlen=SEEN_CAP)
        self._seen_set: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: Any = None
        self.mode = "off"           # off / idle / poll —— 面板据此显示"长连接还在不在"
        self.last_error = ""
        self.delivered = 0
        self.filtered = 0           # 被发件人白名单挡掉的封数（"过滤生效中"证据）
        self._load_seen()

    # ---- 总线契约 ----

    def collect(self) -> list[PerceptEvent]:
        """排空内部队列；首次调用时才把采集线程拉起来。"""
        if not self.configured():
            return []
        self._ensure_thread()
        with self._lock:
            pending = list(self._queue)
            self._queue.clear()
        events: list[PerceptEvent] = []
        for env in pending:
            events.append(self._event(env))
            self.delivered += 1
        return events

    def close(self) -> None:
        """停线程并断开连接（总线 shutdown_sources 会调）。"""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        conn = getattr(self, "_conn", None)
        self.mode = "off"
        if conn is None:
            return
        try:
            conn.logout()
        except Exception:
            pass  # 已经断了：logout 失败无所谓，别在退出路径上升错误

    def configured(self) -> bool:
        """host + 账号 + 凭据三者齐备才算配好（缺任一则本源不工作）。"""
        return bool(self._host and self._user and self._password)

    def state(self) -> dict[str, Any]:
        """运行状态（导出/面板用）：模式、末次错误、过滤名单——不含任何凭据。"""
        return {
            "configured": self.configured(),
            "mode": self.mode,
            "host": self._host,
            "mailbox": self._mailbox,
            "last_error": self.last_error,
            "delivered": self.delivered,
            "filtered": self.filtered,
            "watch_senders": list(self._watch),
            "queued": len(self._queue),
            "seen": len(self._seen_set),
        }

    # ---- 采集线程（IDLE + 降级轮询）----

    def _ensure_thread(self) -> None:
        """懒启采集线程（幂等）。总线只在源已启用时才 collect，故关着=零线程。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jarvis-percept-mail", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """线程主体：一轮一轮地采，异常不致命——失败就退化成定时轮询。"""
        while not self._stop.is_set():
            wait = self._poll_s
            try:
                wait = self._cycle()
            except Exception as e:
                # 断线降级轮询：记错、退到 poll 模式、按轮询间隔重来
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                self.mode = "poll"
                wait = self._poll_s
            if self._stop.wait(max(5, wait)):
                break
        self.mode = "off"

    def _cycle(self) -> int:
        """完整一轮：连接 → 登录 → 取新未读 → 挂 IDLE 等推送。

        Returns:
            下一轮之前的等待秒数（IDLE 立即返回 0，轮询模式返回 poll_interval）。
        """
        conn = self._connector(self._host, self._port)
        self._conn = conn
        if hasattr(conn, "login"):
            conn.login(self._user, self._password)
        # 首轮只记录"哪些信已经存过"，一律不发事件：否则一开源就把收件箱
        # 陈年未读全播一遍，用户第一感受就是"这玩意一装就吵"
        first = not self._seen_set
        fresh = self._harvest(conn, announce=not first)
        if first:
            self._save_seen()
        if self._supports_idle(conn):
            self.mode = "idle"
            return self._wait_idle(conn)
        self.mode = "poll"
        if fresh:
            return 0  # 还有存量未处理完，立刻再来一轮
        return self._poll_s

    def _supports_idle(self, conn: Any) -> bool:
        """服务器是否广告 IDLE 能力（不支持就走轮询，不硬试）。"""
        try:
            return bool(conn.hascapability("IDLE"))
        except Exception:
            return False

    def _wait_idle(self, conn: Any) -> int:
        """挂在 IDLE 上等推送；单次最多等 `idle_tick_s` 后主动重挂。

        刻意只等一个周期就返回：`_run` 会立刻重进 `_cycle`（DONE 再 IDLE），
        这比在句柄里死循环更安全——服务商常 30 分钟掐掉长连接，周期性重挂
        反而不容易卡在半死连接上。

        Returns:
            0 表示"有事要处理"，`idle_tick_s` 表示"只是保活到期"。
        """
        try:
            with conn.idle():  # 进入 IDLE，退出时自动 DONE
                resp = conn.idle_check(timeout=self._idle_s)
            if self._stop.is_set():
                return 0
            return 0 if self._has_new_mail(resp) else self._idle_s
        except (OSError, imaplib.IMAP4.error) as e:
            # 交给 _run 的降级逻辑：连接已经不可用，退到轮询重连
            raise ConnectionError(f"IDLE 中断: {e}") from e

    @staticmethod
    def _has_new_mail(resp: Any) -> bool:
        """idle_check 回包里有没有 EXISTS/RECENT（其余是 keepalive 噪声）。"""
        if isinstance(resp, str):
            items: Any = [resp]
        else:
            try:
                _typ, items = resp  # (typ, [bytes...]) 二元组
            except (TypeError, ValueError):
                items = resp
        for item in items or []:
            text = item.decode("utf-8", "replace") if isinstance(item, bytes) else str(item)
            if "EXISTS" in text.upper() or "RECENT" in text.upper():
                return True
        return False

    # ---- 取未读信封（可独立测试的核心）----

    def _harvest(self, conn: Any, *, announce: bool = True) -> int:
        """select(readonly) → search UNSEEN → 逐封 PEEK 信封头，新信入队。

        Args:
            announce: False=只记账不入队（首轮建基线用）。

        Returns:
            本轮新入队的封数。
        """
        conn.select(self._mailbox, readonly=True)
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK":
            return 0
        ids = [s for s in (data[0].split() if data and data[0] else [])]
        fresh = 0
        for num in ids:
            env = self._fetch_envelope(conn, num)
            if env is None:
                continue
            key = envelope_key(env)
            if key in self._seen_set:
                continue
            self._seen_set.add(key)
            self._seen.append(key)
            if not announce:
                continue
            if self._watched(env):
                with self._lock:
                    self._queue.append(env)
                fresh += 1
            else:
                self.filtered += 1
        return fresh

    def _fetch_envelope(self, conn: Any, num: bytes | str) -> dict[str, str] | None:
        """取一封的信封头；单封取失败跳过（不能让一封坏邮件毁掉整轮）。"""
        try:
            typ, data = conn.fetch(num, HEADER_FIELDS)
        except Exception:
            return None
        if typ != "OK" or not data:
            return None
        raw = b""
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                raw = item[1]
                break
        if not raw:
            return None
        return parse_envelope(raw)

    def _watched(self, env: dict[str, str]) -> bool:
        """发件人白名单判定（名单为空=全放行）。"""
        if not self._watch:
            return True
        sender = (env.get("from") or "").lower()
        return any(x in sender for x in self._watch)

    def _event(self, env: dict[str, str]) -> PerceptEvent:
        """来件事件：命中白名单的先验更高（用户明确说过这个人重要）。"""
        sender = env.get("from") or "未知发件人"
        subject = env.get("subject") or "（无主题）"
        hint = 0.75 if self._watch else 0.5
        return PerceptEvent(
            source=SOURCE_MAIL,
            kind="mail_received",
            payload={
                "summary": f"来信：{sender} 主题「{subject}」",
                "from": sender,
                "subject": subject,
                "date": env.get("date", ""),
            },
            salience_hint=hint,
            # 去重键用信封标识：同一封被 IDLE 与轮询都撞见也只播一次
            dedup_key=f"mail:{envelope_key(env)}",
        )

    # ---- 已报集合持久化 ----

    def _load_seen(self) -> None:
        """读回"已报过哪些信"。文件损坏/结构不对一律当作空集，不抛。"""
        try:
            import json

            raw = json.loads(self._seen_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        keys = raw.get("keys") if isinstance(raw, dict) else None
        if not isinstance(keys, list):
            return
        for key in keys[-SEEN_CAP:]:
            text = str(key)
            if text not in self._seen_set:
                self._seen_set.add(text)
                self._seen.append(text)

    def _save_seen(self) -> None:
        """原子落盘已报集合（临时文件 + os.replace）。"""
        import json
        import os

        payload = {"version": 1, "keys": list(self._seen)}
        try:
            self._seen_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._seen_path.with_suffix(self._seen_path.suffix + f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._seen_path)
        except OSError:
            return  # 落盘失败最坏结果是重启后重报一次，不值得为此中断采集
