"""前台窗口感知源 —— "你现在在哪个应用里"（W2 第 2 步）。

采集方式：Windows 下用标准库 `ctypes` 调 `GetForegroundWindow` +
`GetWindowThreadProcessId` 拿 pid，再用已有依赖 `psutil` 反查进程名，
`GetWindowTextW` 拿标题——**零新增依赖**（与 pyproject 排期表口径一致）。
非 Windows 或 ctypes 不可用时退回 `pygetwindow`（已是既有依赖），只拿得到
标题拿不到进程名；两条路都不通就抛错交给总线的故障隔离（源挂了不影响其他源）。

为什么默认不保留完整标题（隐私口径，PLAN 第 8 节决策 2）：
- 窗口标题常含文档名、邮件主题、联系人姓名、URL（带 query 参数时甚至含 token）；
- 主动提醒的话术只需要"在哪个应用 + 大致在做什么"，40 字足够；
- 因此默认截断到 `title_max_chars` 并按正则剥掉凭据类片段，完整标题必须由用户
  显式开 `window_title_full_enabled`；
- 密码管理器等敏感应用直接进排除名单，命中即**一个字节都不发**。

事件语义：
- `focus_changed`：切到另一个应用（按进程名判定，同一应用内换标签页不算）；
  去重键 `focus:<进程名>`，所以"来回切同一个应用"5 分钟内只算一次。
- `focus_linger`：在同一应用连续停留超过 `linger_minutes`（默认 0=不发），
  用于"你在浏览器泡了 40 分钟"这类健康/专注提醒，也是 §3.3 验收构造的第四类事件。

@author aceFelix
"""

from __future__ import annotations

import platform
import re
import time
from typing import Any, Callable

from agent.core.percept.event import PerceptEvent, SOURCE_WINDOW

#: 默认排除应用（小写子串匹配进程名）：密码/凭据管理类，命中即不采
DEFAULT_EXCLUDE_APPS: tuple[str, ...] = (
    "1password", "keepass", "bitwarden", "lastpass", "dashlane",
    "nordpass", "keeweb", "密码", "密钥",
)

#: 标题脱敏正则：剥掉 URL query 里的凭据参数与"密码/验证码"后面的值
_REDACT_PATTERN = re.compile(
    r"(?i)(?:[?&](?:token|key|password|passwd|pwd|auth|access_token|secret)=)[^\s&]+"
    r"|(?:密码|验证码|动态码|授权码)\s*[:：]?\s*\S+"
)

# 停留事件的 monotonic 基准由源自己持有，总线时基不参与（两者语义不同：
# 总线管"投递频率"，源管"用户在同一个应用待了多久"）


def _win_api() -> Any:
    """懒加载并**一次性声明签名**的 user32 句柄。

    必需因为：ctypes 默认 restype 是 c_int，64 位下 `GetForegroundWindow()`
    返回的 HWND 会被**静默截断**（高位丢弃），进而所有窗口文本查询拿到
    错误句柄却不报错——典型的难查脏数据。故必须显式设 restype/argtypes。
    """
    import ctypes  # 标准库：无新增依赖
    from ctypes import wintypes

    if _win_api.cached is not None:  # type: ignore[attr-defined]
        return _win_api.cached  # type: ignore[attr-defined]
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = wintypes.INT
    user32.GetWindowTextW.argtypes = (wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    _win_api.cached = user32  # type: ignore[attr-defined]
    return user32


#: 懒加载缓存槽（带显式 restype/argtypes 的 user32 句柄）
_win_api.cached = None  # type: ignore[attr-defined]


def _probe_windows() -> tuple[str, str]:
    """Windows 原生探测：(进程名, 窗口标题)。非 Windows 或依赖缺失回 pygetwindow。

    @raise OSError 两条路都不通（无 pygetwindow 且非 Windows）时抛出，
    由总线故障隔离计入连败、达阈值自动停用。无 GUI 会话（锁屏/服务/SSH）
    拿到空句柄则返回 ("", "") 表示"确实不知道"，源据此不发事件。
    """
    if platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = _win_api()
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return "", ""
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name = ""
            try:
                import psutil

                name = psutil.Process(pid.value).name() or ""
            except Exception:
                name = ""  # 进程可能已退出：只丢进程名，标题仍可用
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return name, buf.value or ""
        except Exception:
            pass  # 落到 pygetwindow 兜底
    try:
        import pygetwindow as gw  # type: ignore[import-untyped]

        active = gw.getActiveWindow()
        if active is None:
            return "", ""
        title = getattr(active, "title", "") or ""
        # 非 Windows 下 pygetwindow 拿不到进程名，只能给空串
        return "", title
    except Exception as e:
        raise OSError(f"前台窗口探测不可用: {e}") from e


def redact_title(title: str, *, max_chars: int = 40, full: bool = False) -> str:
    """标题脱敏：先剥凭据片段，再按长度截断。

    Args:
        title: 原始窗口标题。
        max_chars: 保留字数上限；`<=0` 表示"只要应用名、标题整体丢弃"（返回空串）。
        full: True 表示用户显式同意保留完整标题（不做长度截断，但仍剥凭据）。
    """
    text = _REDACT_PATTERN.sub("", title or "").strip()
    if full:
        return text
    if max_chars <= 0:
        return ""
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


class WindowSource:
    """前台窗口采集器（实现总线的 `name` / `interval_s` / `collect()` 契约）。

    @author aceFelix
    """

    name = SOURCE_WINDOW

    def __init__(
        self,
        *,
        interval_s: float = 5.0,
        title_max_chars: int = 40,
        title_full_enabled: bool = False,
        exclude_apps: Any = DEFAULT_EXCLUDE_APPS,
        linger_minutes: int = 0,
        probe: Callable[[], tuple[str, str]] | None = None,
        sleep: Callable[[float], float] = time.monotonic,
    ) -> None:
        """
        Args:
            probe: 注入的窗口探测函数（返回 (进程名, 标题)），测试与替代实现用；
                None 用 `_probe_windows`。
            exclude_apps: 排除名单（子串、不分大小写）。
            linger_minutes: >0 时启用"在同一应用待太久"事件。
            sleep: 单调时钟函数（默认 time.monotonic，测试可注入假时钟）。
        """
        self.interval_s = float(interval_s)
        self._title_max = int(title_max_chars)
        self._title_full = bool(title_full_enabled)
        self._exclude = tuple(str(x).lower() for x in (exclude_apps or ()) if str(x).strip())
        self._linger_s = max(0, int(linger_minutes)) * 60.0
        self._probe = probe or _probe_windows
        self._clock = sleep
        self._process = ""        # 当前认定用户所在的应用进程名
        self._title = ""          # 当前脱敏标题
        self._since = self._clock()
        self._baselined = False   # 首采只建基线不发事件（见 collect）
        self._linger_announced = False
        self.skipped_excluded = 0  # 被排除名单挡掉的采样次数（面板"隐私生效中"证据）

    # ---- 总线契约 ----

    def collect(self) -> list[PerceptEvent]:
        """采一次前台窗口；只在"应用变了"或"停留超时"时产事件。"""
        process, title = self._probe()
        process = (process or "").strip()
        title = (title or "").strip()
        if not process and not title:
            return []  # 无前台窗口（锁屏/服务会话）：不知道就是不知道，不发假事件

        events: list[PerceptEvent] = []
        now = self._clock()

        if self._is_excluded(process):
            # 敏感应用：状态照更新（否则切回来会被当成"新应用"漏报），事件一条不发
            self.skipped_excluded += 1
            self._process, self._title, self._since = process, title, now
            self._linger_announced = False
            return []

        redacted = redact_title(title, max_chars=self._title_max, full=self._title_full)
        if not process and not redacted:
            # 脱敏后什么都没下来（非 Windows 拿不到进程名 + max_chars=0 丢了标题）：
            # 发一条"切到 "的空话不如不发，也不能把原始标题塞回去。
            return []
        if not self._baselined:
            # 首采只建基线：否则 daemon 一启动（或用户刚打开这个源）就播一条
            # "切到 Chrome"，而用户根本没切换——属于无意义打扰，且会白吃一条日额度
            self._baselined = True
            self._process, self._title, self._since = process, redacted, now
            return []
        if process != self._process:
            events.append(self._focus_event(process, redacted))
            self._process, self._title, self._since = process, redacted, now
            self._linger_announced = False
            return events

        # 同一应用内标题变化（如换网页）：值得记账但不值得打扰，故不发事件——
        # 总线去重键按进程名，发出去也会被 W1 当同源合并，不如这里就省掉。
        self._title = redacted

        if self._linger_s and not self._linger_announced and now - self._since >= self._linger_s:
            self._linger_announced = True
            events.append(self._linger_event(process, now - self._since))
        return events

    # ---- 内部 ----

    def _is_excluded(self, process: str) -> bool:
        """进程名命中排除名单（子串、不分大小写）。空进程名不视为排除。"""
        if not process or not self._exclude:
            return False
        low = process.lower()
        return any(x in low for x in self._exclude)

    def _focus_event(self, process: str, title: str) -> PerceptEvent:
        """切应用事件。先验低（0.3）：多数切换不值得打扰，靠 W1 与 linger 规则裁。"""
        label = process or title
        summary = f"切到 {label}" + (f"：{title}" if title and process else "")
        return PerceptEvent(
            source=SOURCE_WINDOW,
            kind="focus_changed",
            payload={
                "summary": summary,
                "process": process,
                "title": title,
                "prev_process": self._process,
            },
            salience_hint=0.3,
            dedup_key=f"focus:{label.lower()}",
        )

    def _linger_event(self, process: str, stayed_s: float) -> PerceptEvent:
        """停留超时事件（"在浏览器泡了 40 分钟"），去重键按应用避免重复催。

        先验 0.65 比 `focus_changed` 的 0.3 高一大截，因为停留阈值是**用户
        显式设的**（`window_linger_minutes > 0` 才会发）——他既然开了这个旋钮，
        就是在说“这件事该告诉我”。预算侧 `percept:window` 权重 0.5，
        0.5×0.65=0.325 刚好过 0.3 门槛；而普通切换 0.5×0.3=0.15 永远只留痕。
        """
        minutes = int(stayed_s // 60)
        label = process or self._title
        return PerceptEvent(
            source=SOURCE_WINDOW,
            kind="focus_linger",
            payload={
                "summary": f"在 {label} 连续待了 {minutes} 分钟",
                "process": process,
                "title": self._title,
                "minutes": minutes,
            },
            salience_hint=0.65,
            dedup_key=f"linger:{label.lower()}",
        )

    def state(self) -> dict[str, Any]:
        """当前观察状态（面板"正在观察什么"与导出用）。"""
        return {
            "process": self._process,
            "title": self._title,
            "baselined": self._baselined,
            "stayed_s": round(self._clock() - self._since),
            "excluded_hits": self.skipped_excluded,
            "title_full_enabled": self._title_full,
            "exclude_apps": list(self._exclude),
        }
