"""中国法定节假日数据与提醒 —— 让贾维斯记得放假。

阶段五第三刀（主动感知）。内置 2026 年中国法定节假日数据（含调休），
daemon 启动时检查"明天是否节假日"，是则当天提醒用户"明天 XX 放假"。

数据来源: 国务院办公厅发布的节假日安排。每年年底发布次年安排，
届时更新 HOLIDAYS 字典即可。

数据结构:
    HOLIDAYS[日期字符串] = 节假日名称
    WORKDAYS[日期字符串] = True  # 调休上班的周末

    日期字符串格式: "MM-DD"（同每年重复的节日）或 "YYYY-MM-DD"（特定日期）

用法:
    from agent.core.holidays import is_holiday, is_workday, check_tomorrow_holiday
    if is_holiday("2026-01-01"):
        print("元旦放假")
    reminder = check_tomorrow_holiday()  # 返回提醒文本或 None
"""

from __future__ import annotations

from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# 2026 年中国法定节假日
# 数据来源: 国务院办公厅关于2026年部分节假日安排的通知
# 格式: { "YYYY-MM-DD": "节假日名称" }
# ---------------------------------------------------------------------------

HOLIDAYS_2026: dict[str, str] = {
    # 元旦 1月1日-1月3日（周五-周日，连休3天，无需调休）
    "2026-01-01": "元旦",
    "2026-01-02": "元旦",
    "2026-01-03": "元旦",

    # 春节 2月17日(除夕)-2月23日（周二-周一，连休7天）
    # 调休: 2月14日(周六)、2月15日(周日)上班
    "2026-02-17": "春节（除夕）",
    "2026-02-18": "春节（正月初一）",
    "2026-02-19": "春节",
    "2026-02-20": "春节",
    "2026-02-21": "春节",
    "2026-02-22": "春节",
    "2026-02-23": "春节",

    # 清明节 4月4日-4月6日（周六-周一，连休3天，无需调休）
    "2026-04-04": "清明节",
    "2026-04-05": "清明节",
    "2026-04-06": "清明节",

    # 劳动节 5月1日-5月5日（周五-周二，连休5天）
    # 调休: 4月26日(周日)上班
    "2026-05-01": "劳动节",
    "2026-05-02": "劳动节",
    "2026-05-03": "劳动节",
    "2026-05-04": "劳动节",
    "2026-05-05": "劳动节",

    # 端午节 6月19日-6月21日（周五-周日，连休3天，无需调休）
    "2026-06-19": "端午节",
    "2026-06-20": "端午节",
    "2026-06-21": "端午节",

    # 中秋节 9月25日-9月27日（周五-周日，连休3天，无需调休）
    "2026-09-25": "中秋节",
    "2026-09-26": "中秋节",
    "2026-09-27": "中秋节",

    # 国庆节 10月1日-10月7日（周四-周三，连休7天）
    # 调休: 9月27日(周日)已算中秋，10月10日(周六)上班
    "2026-10-01": "国庆节",
    "2026-10-02": "国庆节",
    "2026-10-03": "国庆节",
    "2026-10-04": "国庆节",
    "2026-10-05": "国庆节",
    "2026-10-06": "国庆节",
    "2026-10-07": "国庆节",
}

# 调休上班日（本是周末但需上班）
WORKDAYS_2026: dict[str, bool] = {
    "2026-02-14": True,  # 春节调休
    "2026-02-15": True,  # 春节调休
    "2026-04-26": True,  # 劳动节调休
    "2026-10-10": True,  # 国庆节调休
}


# ---------------------------------------------------------------------------
# 查询接口
# ---------------------------------------------------------------------------


def _get_holidays_for_year(year: int) -> dict[str, str]:
    """获取指定年份的节假日数据。目前只有 2026。"""
    if year == 2026:
        return HOLIDAYS_2026
    # 其他年份暂无数据，返回空（后续按需补充）
    return {}


def _get_workdays_for_year(year: int) -> dict[str, bool]:
    """获取指定年份的调休上班日数据。"""
    if year == 2026:
        return WORKDAYS_2026
    return {}


def is_holiday(date_str: str | datetime | None = None) -> bool:
    """判断某天是否节假日。

    Args:
        date_str: 日期。字符串 "YYYY-MM-DD" 或 datetime 对象。None=今天。

    Returns: True=节假日, False=非节假日。
    """
    if date_str is None:
        date_str = datetime.now()
    if isinstance(date_str, datetime):
        date_str = date_str.strftime("%Y-%m-%d")
        year = date_obj.year if (date_obj := datetime.strptime(date_str, "%Y-%m-%d")) else datetime.now().year
    else:
        year = int(date_str.split("-")[0])

    holidays = _get_holidays_for_year(year)
    return date_str in holidays


def is_workday(date_str: str | datetime | None = None) -> bool:
    """判断某天是否工作日（含调休上班的周末）。

    逻辑:
    - 调休上班日(WORKDAYS) → True
    - 节假日(HOLIDAYS) → False
    - 周一至周五 → True
    - 周六周日 → False
    """
    if date_str is None:
        date_obj = datetime.now()
    elif isinstance(date_str, str):
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        date_obj = date_str

    date_str = date_obj.strftime("%Y-%m-%d")
    year = date_obj.year

    workdays = _get_workdays_for_year(year)
    holidays = _get_holidays_for_year(year)

    if date_str in workdays:
        return True  # 调休上班
    if date_str in holidays:
        return False  # 节假日
    return date_obj.weekday() < 5  # 周一至周五


def get_holiday_name(date_str: str | datetime | None = None) -> str | None:
    """获取节假日的名称。非节假日返回 None。"""
    if date_str is None:
        date_obj = datetime.now()
    elif isinstance(date_str, str):
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        date_obj = date_str

    date_str = date_obj.strftime("%Y-%m-%d")
    holidays = _get_holidays_for_year(date_obj.year)
    return holidays.get(date_str)


# ---------------------------------------------------------------------------
# 节假日提醒（daemon 启动时调用）
# ---------------------------------------------------------------------------


def check_tomorrow_holiday() -> str | None:
    """检查明天是否节假日，返回提醒文本。

    如果明天是节假日第一天（今天是工作日），返回"明天 XX 放假"提醒。
    如果明天不是节假日或明天已在假期中，返回 None（不打扰）。

    Returns: 提醒文本（如"先生，明天是元旦节，记得放假"），或 None。
    """
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")
    today_str = now.strftime("%Y-%m-%d")

    tomorrow_holiday = get_holiday_name(tomorrow_str)
    if tomorrow_holiday is None:
        return None  # 明天不是节假日

    # 明天是节假日。检查今天是否也是节假日（已在假期中则不提醒）
    today_holiday = get_holiday_name(today_str)
    if today_holiday is not None:
        return None  # 今天已经在放假了，不重复提醒

    # 检查是否节假日的"第一天"（昨天不是这个节假日）
    yesterday = now - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    yesterday_holiday = get_holiday_name(yesterday_str)

    # 明天是节假日第一天（昨天不是同一个节假日）
    is_first_day = yesterday_holiday != tomorrow_holiday

    if is_first_day:
        # 判断是放假前一天（工作日晚上提醒）还是假期前最后一天
        if is_workday(today_str):
            return f"先生，明天开始放{tomorrow_holiday}假，记得安排好手头的事。"
        else:
            return f"先生，明天是{tomorrow_holiday}，祝您假期愉快。"

    return None


def get_upcoming_holidays(days: int = 30) -> list[tuple[str, str]]:
    """获取未来 N 天内的节假日列表。

    Args:
        days: 查询未来多少天。

    Returns: [(日期字符串, 节假日名称), ...] 列表，按日期排序。
    """
    now = datetime.now()
    result = []
    for i in range(days + 1):
        d = now + timedelta(days=i)
        d_str = d.strftime("%Y-%m-%d")
        name = get_holiday_name(d_str)
        if name:
            result.append((d_str, name))
    return result
