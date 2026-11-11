"""模型计价 —— 把 token 用量按官方价目换算成人民币。

为什么单独成模块：价格是持续变动的外部事实，必须集中、带来源、带核对日期，
并且**查不到就明说查不到**。散落在各 provider 里各自算钱，迟早出现三处口径不一致；
用「折扣系数」近似则更糟——缓存命中价不是未命中的某个固定比例，而是差 30~50 倍。

数据来源与核对日期见 ``SOURCE`` / ``VERIFIED_AT``；
``tests/llm/test_pricing.py`` 钉住全部数字，官方调价时测试会红，提醒更新，
而不是悄悄按旧价算出一笔看着合理、实际错几十倍的钱。

峰谷口径（官方中文页原文）：北京时间周一至周五 9:00-12:00、14:00-18:00 为高峰，
其余时段（含周末与中国法定节假日全天）为空闲，空闲价 = 高峰价的一半。

时区处理用固定 UTC+8 而非 zoneinfo：中国无夏令时，固定偏移即精确；而 Windows 上的
``zoneinfo`` 需要额外 tzdata 包，为一个偏移量引入依赖不划算。

@author aceFelix
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

#: 计价所依据的官方文档页（中文页，价格与峰谷时段均以此为准）
SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing"
#: 价目最后核对日期。调价后必须改这里并重跑测试，否则等于用旧价记账。
VERIFIED_AT = "2026-11-11"

#: 北京时间固定偏移（UTC+8，中国无夏令时）
BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")

#: 高峰时段（北京时间，小时左闭右开）
PEAK_WINDOWS: tuple[tuple[int, int], ...] = ((9, 12), (14, 18))


@dataclass(frozen=True)
class PriceRow:
    """单个模型的单价（元 / 百万 tokens，**高峰价**）。

    空闲时段一律取其一半，因为官方口径就是「空闲价 = 高峰价的一半」，
    不单独维护两套数字，避免两处不一致。
    """

    hit: float
    miss: float
    output: float


# ═══════════════════════════════════════════════════════════════
# 价目表 —— 只收录有官方页可核的模型；新增前先找到官方价目页
# ═══════════════════════════════════════════════════════════════

PRICES: dict[str, PriceRow] = {
    # deepseek-flash（DeepSeek-V4.1-Flash-1017）：0.04 / 2 / 8
    "deepseek-flash": PriceRow(hit=0.04, miss=2.0, output=8.0),
    # deepseek-v4-pro（DeepSeek-V4-Pro-0813）：0.30 / 9 / 27
    "deepseek-v4-pro": PriceRow(hit=0.30, miss=9.0, output=27.0),
}

#: 旧模型名 → 现行计费名。官方说明：旧名仍可调用，但由新模型服务并按新价计费。
ALIASES: dict[str, str] = {
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-flash",
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-flash-vision-exp": "deepseek-flash",
}


def resolve(model: str) -> PriceRow | None:
    """按模型名查单价。未收录返回 None —— 调用方必须据此显示「无法计价」。"""
    if not model:
        return None
    key = model.strip().lower()
    key = ALIASES.get(key, key)
    return PRICES.get(key)


def _is_holiday(d: date) -> bool:
    """中国法定节假日判定；数据缺失或出错一律按「不是节假日」处理。

    为什么 fail-open：holidays 表只覆盖有数据的年份，跨年后它会自然失效。
    节假日报错只影响峰谷判定的金额（相差一倍），不该让整个计费链路崩掉。
    """
    try:
        from agent.core.daemon.holidays import is_holiday

        # 传 ISO 字符串：holidays.is_holiday 的签名是 str | datetime，不收 date
        return bool(is_holiday(d.isoformat()))
    except Exception:  # noqa: BLE001 - 计价的辅助判定不得拖垮主流程
        return False


def is_peak(at: datetime | None = None) -> bool:
    """该时刻是否处于高峰时段（北京时间，周一至周五，非节假日，9-12 或 14-18 点）。"""
    now = at or datetime.now(BEIJING)
    local = now.astimezone(BEIJING)
    if local.weekday() >= 5:  # 5=周六 6=周日
        return False
    try:
        on_holiday = _is_holiday(local.date())
    except Exception:  # noqa: BLE001 - 节假日判定再坏一层也不能让计价崩掉
        on_holiday = False
    if on_holiday:
        return False
    return any(start <= local.hour < end for start, end in PEAK_WINDOWS)


def period_label(at: datetime | None = None) -> str:
    """峰谷的可读标签，给 /cost 用。"""
    return "高峰" if is_peak(at) else "空闲"


@dataclass(frozen=True)
class CostResult:
    """一次结算的结果。

    ``priced=False`` 表示该模型没有收录价目：这时 ``cost_cny`` 无意义，
    调用方必须把 token 数照实展示、把金额标成「无法计价」，不能给一个假数字。
    """

    cost_cny: float
    priced: bool
    peak: bool
    hit_tokens: int
    miss_tokens: int
    output_tokens: int


def compute_cost(
    model: str,
    *,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    output_tokens: int,
    at: datetime | None = None,
) -> CostResult:
    """按模型、峰谷与实际 token 结构结算一次请求。

    Args:
        model: 模型名（旧名会自动映射到现行计费名）
        cache_hit_tokens: 命中缓存的输入 token（按 hit 价）
        cache_miss_tokens: 未命中缓存的输入 token（按 miss 价）
        output_tokens: 输出 token
        at: 请求发生时刻；缺省用当前时间

    Returns:
        CostResult；``priced=False`` 时不要使用 ``cost_cny``
    """
    hit = max(0, int(cache_hit_tokens or 0))
    miss = max(0, int(cache_miss_tokens or 0))
    out = max(0, int(output_tokens or 0))
    peak = is_peak(at)

    row = resolve(model)
    if row is None:
        return CostResult(0.0, False, peak, hit, miss, out)

    factor = 1.0 if peak else 0.5
    cost = (
        hit * row.hit
        + miss * row.miss
        + out * row.output
    ) * factor / 1_000_000
    return CostResult(cost, True, peak, hit, miss, out)


def summarize(model: str, *, input_tokens: int, cache_hit_tokens: int, output_tokens: int,
              input_includes_cache: bool = True, at: datetime | None = None) -> CostResult:
    """从会话级累计口径结算（inputs 已含缓存时自动拆出未命中部分）。

    ``input_includes_cache``：
    - True （OpenAI / DashScope 兼容协议）：input_tokens 含缓存 → miss = input - hit
    - False（Anthropic 协议）：input_tokens 只含未命中 → miss = input
    """
    hit = max(0, int(cache_hit_tokens or 0))
    total_in = max(0, int(input_tokens or 0))
    miss = max(0, total_in - hit) if input_includes_cache else total_in
    return compute_cost(
        model,
        cache_hit_tokens=hit,
        cache_miss_tokens=miss,
        output_tokens=output_tokens,
        at=at,
    )
