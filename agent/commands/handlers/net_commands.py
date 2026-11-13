"""`/net` 网络诊断：出口、引擎可达性、关键站点可达性。

## 为什么需要这条命令

2026-11 一次"查某人底细"的语音会话烧掉 5 次失败、246 秒。复盘出三个独立故障：

1. 搜索引擎表里没有国内源，唯一能通的 Bing 把中文人名拆成单字；
2. `WebFetch` 用 requests、**不读 Windows 系统代理**，而 `Crawl` 用 Chromium、
   能读——同一个 URL 两套栈两种结果；
3. 同一个不可达域名每次都重走满三轮退避。

修完之后用户仍需要一个**一眼能看出来的入口**：到底是墙的问题、代理没开、还是
引擎挂了。没有它，每次都得重新推理一遍。

只读：只发探测请求，不改设置、不落盘。

@author aceFelix
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from agent.tools.web import net
from agent.tools.web.search_engine import _PROBE_URLS

if TYPE_CHECKING:  # router 在运行时导入本模块，注解只能单向引用
    from agent.commands.router import CommandContext

# 体检站点：国内基准 / 通用基础设施 / 被墙代表 / 自己的 API。
# 选这四个能一次分清"墙、代理、服务端"三种情况。
_SITES: tuple[tuple[str, str], ...] = (
    ("baidu.com", "国内基准"),
    ("github.com", "开发基础设施"),
    ("zh.wikipedia.org", "被墙代表"),
    ("api.deepseek.com", "当前使用的 API"),
)

_TIMEOUT = 1.5


def handle_net(ctx: "CommandContext", stripped: str) -> bool:
    """处理 /net：网络出口与可达性体检。

    引擎与站点**一次全部提交**再逐一取结果：两批串行会把耗时从 ~1.5s 抬到 ~3s，
    而这个命令的用途就是"一眼看出来"，慢一倍就变成负担了。
    """
    ui = ctx.ui
    # 出口只报一次，用必然要访问的地址做样本
    ui.info(f"出口：{net.describe_egress('https://www.baidu.com')}")

    engine_items = list(_PROBE_URLS.items())
    site_urls = [f"https://{host}" for host, _desc in _SITES]

    with ThreadPoolExecutor(max_workers=10) as pool:
        engine_futures = [
            pool.submit(net.probe, url, timeout=_TIMEOUT) for _n, url in engine_items
        ]
        site_futures = [
            pool.submit(net.probe, url, timeout=_TIMEOUT) for url in site_urls
        ]

        ui.info("引擎可达性（按此顺序选引擎；不可达的 5 分钟内自动跳过）")
        for (name, _url), fut in zip(engine_items, engine_futures):
            ok, ms = fut.result()
            mark = "✓" if ok else "✗"
            detail = f"{ms:.0f}ms" if ok else "不可达"
            ui.info(f"  {mark} {name}：{detail}")

        ui.info("站点可达性")
        for (host, desc), fut in zip(_SITES, site_futures):
            ok, ms = fut.result()
            mark = "✓" if ok else "✗"
            detail = f"{ms:.0f}ms" if ok else "不可达"
            ui.info(f"  {mark} {host}：{detail}　{desc}")

    ui.info(
        "说明：引擎顺序按实测可达性自动排，不需要手改；"
        "国外站点不通时，在代理软件里打开「系统代理」即可让 jarvis 也走它。"
    )
    return True
