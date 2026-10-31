"""按配置装配感知源 —— 宿主的唯一接线点（W2 第 2、3、4 步）。

宿主（serve 的 ProactiveHub、CLI、以后的桌面面板）只需要一句
``register_sources(bus, settings)``，不必知道有几个源、各要什么参数。
"配置 → 源实例"的映射集中在这里，是为了让**逐源默认关**这件事只有一处实现：
`enabled` = 总开关 `percept_enabled` **与** 逐源 `percept_*_enabled`，这里不擅自放宽。

> ❗ 总开关必须参与 AND（第 4 步接线时补上的漏采口子）：早期只取逐源字段，
> 于是“`enabled=false` 但 `window_enabled=true`”这份配置会把窗口源登记成启用，
> 宿主一句 `bus.start()` 就在用户以为全关的情况下采了前台窗口。
> `source_enabled_map()` 早就按“总开关关→全 False”的口径实现，源侧必须与它一致。

两个刻意的设计：

- **登记全部源，启用状态由配置决定**。面板要能列出"可以开哪些源"，
  所以未启用的源也照样注册（`enabled=False`），总线不会采它（有单测锁死）。
- **"开了但没配齐"不发事件、也不报错**。例如开了 file 源却没填白名单目录、
  开了 mail 源却没配邮箱、开了 vision 源但本机没装 mediapipe：这类是用户配置
  未完成或环境不支持，不是运行时故障。若让它抛，总线的连败自动停用会把源标成
  "坏了"，面板显示一个误导人的错误。故这类源的 `collect()` 返回空列表，
  能不能用由 `state()` / 面板另行提示。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.percept.bus import PerceptBus
from agent.core.percept.event import (
    SOURCE_CALENDAR,
    SOURCE_FILE,
    SOURCE_MAIL,
    SOURCE_SCHEDULE,
    SOURCE_SYSTEM,
    SOURCE_VCS,
    SOURCE_VISION,
    SOURCE_WINDOW,
)
from agent.core.percept.source_calendar import (
    DEFAULT_INTERVAL_S as CALENDAR_INTERVAL_S,
    CalendarSource,
)
from agent.core.percept.source_file import FileSource
from agent.core.percept.source_mail import MailSource
from agent.core.percept.source_system import (
    DEFAULT_INTERVAL_S as SYSTEM_INTERVAL_S,
    SystemSource,
)
from agent.core.percept.source_vcs import VcsSource
from agent.core.percept.source_vision import DEFAULT_FPS, VisionSource
from agent.core.percept.source_window import DEFAULT_EXCLUDE_APPS, WindowSource

#: 邮件源的队列排空节拍（秒）。IDLE 信号到了之后由采集线程即时入队，
#: 总线最多隔这么久把事件投出去；它不是轮询间隔（轮询间隔是 MailSource.poll_interval_s）。
MAIL_DRAIN_INTERVAL_S = 30


def build_sources(settings: Any) -> list[tuple[Any, bool]]:
    """按 Settings 构造源实例，返回 ``[(source, enabled), ...]``。

    只构造**已实现**的源；schedule 收编位仍不在此出现，避免"面板有个开关、
    背后没人采"的假可用（理由见本模块顶部与架构文档第七节）。
    """
    out: list[tuple[Any, bool]] = []
    if settings is None:
        return out

    # 总开关参与每一个逐源判定：关了就是全关，不留“某个单源单独越权”的口子
    master = bool(getattr(settings, "percept_enabled", False))

    def _flag(field: str) -> bool:
        """逐源开关：总开关与本源开关都开才算开。"""
        return master and bool(getattr(settings, field, False))

    out.append((
        WindowSource(
            interval_s=int(getattr(settings, "percept_window_interval_s", 5) or 5),
            title_max_chars=int(getattr(settings, "percept_window_title_max_chars", 40) or 40),
            title_full_enabled=bool(getattr(settings, "percept_window_title_full_enabled", False)),
            exclude_apps=tuple(getattr(settings, "percept_window_exclude_apps", ()) or DEFAULT_EXCLUDE_APPS),
            linger_minutes=int(getattr(settings, "percept_window_linger_minutes", 0) or 0),
        ),
        _flag("percept_window_enabled"),
    ))

    out.append((
        FileSource(
            watch_dirs=getattr(settings, "percept_file_watch_dirs", ()) or (),
            interval_s=int(getattr(settings, "percept_file_interval_s", 30) or 30),
            max_entries=int(getattr(settings, "percept_file_max_entries", 2000) or 2000),
            grow_threshold=int(getattr(settings, "percept_file_grow_threshold", 5) or 5),
            ignore_suffixes=getattr(settings, "percept_file_ignore_suffixes", ()) or (),
        ),
        _flag("percept_file_enabled"),
    ))

    out.append((
        VcsSource(
            repos=getattr(settings, "percept_vcs_repos", ()) or (),
            interval_s=int(getattr(settings, "percept_vcs_interval_s", 120) or 120),
        ),
        _flag("percept_vcs_enabled"),
    ))

    # IMAP 凭据回退口径：imap_user/imap_password 留空时沿用 SMTP 同一套（163/QQ
    # 就是一个授权码两边用）；imap_host 留空时按 smtp_host 推断（同域前缀互换）。
    # 没配齐时 MailSource.configured() 为 False，线程压根不起——这是"开了但
    # 没配齐"的标准处理（见本模块顶部）。
    # 账号解析只有 resolve_account 一份：以前这里手写了一遍等价逻辑，
    # 加上主机名推导后两处必然漂移（一处能收、一处不能），故收归到 core/mail。
    # @author aceFelix
    from agent.core.mail.imap import resolve_account

    _mail = resolve_account(settings)
    out.append((
        MailSource(
            host=_mail.host,
            port=_mail.port,
            user=_mail.user,
            password=_mail.password,
            mailbox=_mail.mailbox,
            # 发件人白名单：非空则只报命中的人（源侧第一道过滤，见 source_mail）
            watch_senders=getattr(settings, "percept_mail_watch_senders", ()) or (),
            interval_s=MAIL_DRAIN_INTERVAL_S,   # 排空节拍固定，真正影响延迟的是 IDLE/轮询
        ),
        _flag("percept_mail_enabled"),
    ))

    # 视觉在场源（第 4 步收编）：唯一会碰摄像头的源，默认关 + 低帧率，
    # 依赖缺失时不抛不重试（见 source_vision 模块顶部）。
    out.append((
        VisionSource(
            camera_index=int(getattr(settings, "percept_vision_camera_index", 0) or 0),
            fps=int(getattr(settings, "percept_vision_fps", DEFAULT_FPS) or DEFAULT_FPS),
            enable_gesture=bool(getattr(settings, "percept_vision_gesture_enabled", True)),
            enable_face=True,   # 在场判定是本源的存在理由，不给开关
            auto_stop_minutes=int(getattr(settings, "percept_vision_auto_stop_minutes", 0) or 0),
            interval_s=int(getattr(settings, "percept_vision_interval_s", 5) or 5),
        ),
        _flag("percept_vision_enabled"),
    ))

    # 日历源（阶段 11 收编）：后台能力 ``daemon/calendar_source.py`` 早已写好但一直休眠，
    # 本源只做总线适配（拉取型，不自己起线程）；提前量从配置来，默认 30 分钟。
    out.append((
        CalendarSource(
            lead_minutes=int(getattr(settings, "calendar_remind_minutes_before", 30) or 30),
            interval_s=CALENDAR_INTERVAL_S,
            backend=str(getattr(settings, "calendar_backend", "auto") or "auto"),
            ics_path=str(getattr(settings, "calendar_ics_path", "") or ""),
            ics_url=str(getattr(settings, "calendar_ics_url", "") or ""),
        ),
        _flag("percept_calendar_enabled"),
    ))

    # 系统资源源（阶段 11 收编）：阈值直接吃 ``monitor_*`` 那一组，本源只用
    # check_now() 拉快照，不起第二条采样线程。
    #
    # 采样间隔以前写死常量 60s，导致 ``[monitor] check_interval`` 改了没反应
    # （字段存在、模板也有注释，就是没人读）。现在真读它；``source_system`` 里
    # 的 DEFAULT_INTERVAL_S 仍作为“这个字段没配”时的兜底值，两处默认必须同为 60。
    #
    # ``work_break_interval`` 同理必传：久坐提醒的判定在 ``_do_check`` 里，
    # 而本源的 ``check_now`` 是它唯一的驱动点，漏了这一行字段就永远改不动。
    out.append((
        SystemSource(
            cpu_threshold=float(getattr(settings, "monitor_cpu_threshold", 85.0) or 85.0),
            memory_threshold=float(getattr(settings, "monitor_memory_threshold", 90.0) or 90.0),
            disk_threshold=float(getattr(settings, "monitor_disk_threshold", 10.0) or 10.0),
            alert_cooldown=int(getattr(settings, "monitor_alert_cooldown", 600) or 600),
            disk_trend_days=int(getattr(settings, "monitor_disk_trend_days", 7) or 7),
            work_break_interval=int(
                getattr(settings, "monitor_work_break_interval", 7200) or 7200
            ),
            interval_s=int(getattr(settings, "monitor_check_interval", SYSTEM_INTERVAL_S)
                           or SYSTEM_INTERVAL_S),
        ),
        _flag("percept_system_enabled"),
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


#: 本期已实现的源名（面板据此区分"已可用"与"待接入"）
IMPLEMENTED_SOURCES: tuple[str, ...] = (
    SOURCE_WINDOW, SOURCE_FILE, SOURCE_VCS, SOURCE_MAIL, SOURCE_VISION,
    SOURCE_CALENDAR, SOURCE_SYSTEM,
)

#: 最后的收编位（schedule）。理由已在 2026-10-26 更新：
#:
#: Scheduler 的提醒**已经会过 W1 打扰预算闸门**（阶段 10 把提醒从直投 ``_emit``
#: 改成过闸 + 静默时段降级投递），所以“提醒被静默时段压掉”这个老问题不存在了。
#: 现在不收编它的原因是**会重复播报**：提醒到点已经由提醒通道播了一次（降级或正常），
#: 再往总线投一条 ``schedule_fired``，W1 会当成第二件独立的事再播一次。
#: 真要把两者统一，得把提醒的投递路径整体挪到总线上，那是另一次改动。
PENDING_SOURCES: tuple[str, ...] = (SOURCE_SCHEDULE,)
