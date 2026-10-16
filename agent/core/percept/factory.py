"""按配置装配感知源 —— 宿主的唯一接线点（W2 第 2、3 步）。

宿主（serve 的 ProactiveHub、CLI、以后的桌面面板）只需要一句
``register_sources(bus, settings)``，不必知道有几个源、各要什么参数。
"配置 → 源实例"的映射集中在这里，是为了让**逐源默认关**这件事只有一处实现：
`enabled` 一律取自 Settings 的 `percept_*_enabled`，这里不擅自放宽。

两个刻意的设计：

- **登记全部源，启用状态由配置决定**。面板要能列出"可以开哪些源"，
  所以未启用的源也照样注册（`enabled=False`），总线不会采它（有单测锁死）。
- **"开了但没配齐"不发事件、也不报错**。例如开了 file 源却没填白名单目录、
  开了 mail 源却没配邮箱：这类是用户配置未完成，不是运行时故障。若让它抛，
  总线的连败自动停用会把源标成"坏了"，面板显示一个误导人的错误。故这类源
  的 `collect()` 返回空列表，配置是否齐备由 `state()` / 面板另行提示。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.percept.bus import PerceptBus
from agent.core.percept.event import (
    SOURCE_FILE,
    SOURCE_MAIL,
    SOURCE_VCS,
    SOURCE_WINDOW,
)
from agent.core.percept.source_file import FileSource
from agent.core.percept.source_mail import MailSource
from agent.core.percept.source_vcs import VcsSource
from agent.core.percept.source_window import DEFAULT_EXCLUDE_APPS, WindowSource

#: 邮件源的队列排空节拍（秒）。IDLE 信号到了之后由采集线程即时入队，
#: 总线最多隔这么久把事件投出去；它不是轮询间隔（轮询间隔是 MailSource.poll_interval_s）。
MAIL_DRAIN_INTERVAL_S = 30


def build_sources(settings: Any) -> list[tuple[Any, bool]]:
    """按 Settings 构造源实例，返回 ``[(source, enabled), ...]``。

    只构造**已实现**的源；收编类源（schedule/calendar/system/vision）在第 4 步
    接线后才出现，避免"面板有个开关、背后没人采"的假可用。
    """
    out: list[tuple[Any, bool]] = []
    if settings is None:
        return out

    out.append((
        WindowSource(
            interval_s=int(getattr(settings, "percept_window_interval_s", 5) or 5),
            title_max_chars=int(getattr(settings, "percept_window_title_max_chars", 40) or 40),
            title_full_enabled=bool(getattr(settings, "percept_window_title_full_enabled", False)),
            exclude_apps=tuple(getattr(settings, "percept_window_exclude_apps", ()) or DEFAULT_EXCLUDE_APPS),
            linger_minutes=int(getattr(settings, "percept_window_linger_minutes", 0) or 0),
        ),
        bool(getattr(settings, "percept_window_enabled", False)),
    ))

    out.append((
        FileSource(
            watch_dirs=getattr(settings, "percept_file_watch_dirs", ()) or (),
            interval_s=int(getattr(settings, "percept_file_interval_s", 30) or 30),
            max_entries=int(getattr(settings, "percept_file_max_entries", 2000) or 2000),
            grow_threshold=int(getattr(settings, "percept_file_grow_threshold", 5) or 5),
            ignore_suffixes=getattr(settings, "percept_file_ignore_suffixes", ()) or (),
        ),
        bool(getattr(settings, "percept_file_enabled", False)),
    ))

    out.append((
        VcsSource(
            repos=getattr(settings, "percept_vcs_repos", ()) or (),
            interval_s=int(getattr(settings, "percept_vcs_interval_s", 120) or 120),
        ),
        bool(getattr(settings, "percept_vcs_enabled", False)),
    ))

    # IMAP 凭据回退口径：imap_user/imap_password 留空时沿用 SMTP 同一套（163/QQ
    # 就是一个授权码两边用）；没配齐时 MailSource.configured() 为 False，
    # 线程压根不起——这是"开了但没配齐"的标准处理（见本模块顶部）。
    out.append((
        MailSource(
            host=str(getattr(settings, "email_imap_host", "") or ""),
            port=int(getattr(settings, "email_imap_port", 993) or 993),
            user=str(getattr(settings, "email_imap_user", "")
                     or getattr(settings, "email_smtp_user", "") or ""),
            password=str(getattr(settings, "email_imap_password", "")
                         or getattr(settings, "email_smtp_password", "") or ""),
            mailbox=str(getattr(settings, "email_imap_mailbox", "INBOX") or "INBOX"),
            # 发件人白名单：非空则只报命中的人（源侧第一道过滤，见 source_mail）
            watch_senders=getattr(settings, "percept_mail_watch_senders", ()) or (),
            interval_s=MAIL_DRAIN_INTERVAL_S,   # 排空节拍固定，真正影响延迟的是 IDLE/轮询
        ),
        bool(getattr(settings, "percept_mail_enabled", False)),
    ))
    return out


def register_sources(bus: PerceptBus, settings: Any) -> dict[str, bool]:
    """构造并登记全部可用源，返回 ``{源名: 启用状态}``（面板与日志直接可用）。

    重复调用是幂等的：`PerceptBus.register_source` 同名即替换，所以热更新
    配置后重跑一次就能把新参数（采样间隔、白名单目录）带进去。
    """
    status: dict[str, bool] = {}
    for source, enabled in build_sources(settings):
        bus.register_source(source, enabled=enabled)
        status[source.name] = enabled
    return status


#: 本期已实现的源名（收编类源在第 4 步接线，面板据此区分"已可用"与"待接入"）
IMPLEMENTED_SOURCES: tuple[str, ...] = (SOURCE_WINDOW, SOURCE_FILE, SOURCE_VCS, SOURCE_MAIL)

#: 待后续切片接线的源名（用于文档与面板占位，避免误以为已可用）
PENDING_SOURCES: tuple[str, ...] = ()
