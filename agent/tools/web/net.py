"""网络出口策略：代理解析 + 不可达域名记忆。

## 为什么必须单独一个模块

WebFetch / WebSearch 与 Crawl / Browser* 走**两条不同的网络栈**：

- Crawl / Browser* 用 Chromium，它会读 Windows 的 INET 系统代理设置；
- WebFetch / WebSearch 用 `requests`，只读 `HTTP(S)_PROXY` 环境变量，**不读系统代理**。

于是同一件事在用户机器上出现诡异反差：`Crawl` 抓 `bing.com` 成功（6.5s），
`WebFetch` 抓 `zh.wikipedia.org` 必然 ConnectTimeout（15s × 3 轮重试，60s+）。
2026-11 一次"查某人底细"的会话就这么白烧了 5 次失败、246 秒——不是超时参数
太小，是**两条栈的出口不一样**。

本模块给 python 侧补上"读系统代理"，并记住哪些域名在本机根本不可达，避免
每次都白等满三轮退避。

## 三条约束

1. **开关关着就不注入**：`ProxyEnable=0` 时视为用户明确选择直连，不偷偷套代理。
2. **socks 但没有 PySocks 时按 http 语义试同一端口**：Clash 一类客户端的 mixed
   端口两种协议共用，实测能通就不用逼用户装依赖。
3. **不可达只记 5 分钟**：别把一次网络抖动记成永久结论。

@author aceFelix
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlsplit

# 不可达记忆的存活秒数。够短到网络恢复后能自愈，够长到一次任务里不重复撞墙。
_DEAD_TTL_S = 300.0

# host -> 过期时间戳（monotonic）
_DEAD: dict[str, float] = {}


def host_of(url: str) -> str:
    """取 URL 的主机名（小写）。解析失败返回空串。"""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _system_proxy() -> str | None:
    """读 Windows INET 系统代理，返回可用于 requests 的代理 URL。

    只在 `ProxyEnable=1` 时返回；关闭状态返回 None（尊重用户选择）。
    非 Windows 或读取失败也返回 None。
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            try:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except OSError:
                return None
            if not enabled:
                return None
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except OSError:
                return None
    except OSError:
        return None

    raw = str(server or "").strip()
    if not raw:
        return None

    # 形态一：127.0.0.1:7890（单一代理，两种协议共用）
    if "=" not in raw:
        return f"http://{raw}"

    # 形态二：http=1.2.3.4:8080;https=1.2.3.4:8080;socks=127.0.0.1:40008
    parts: dict[str, str] = {}
    for chunk in raw.split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            parts[k.strip().lower()] = v.strip()
    for key in ("https", "http"):
        if parts.get(key):
            return f"http://{parts[key]}"
    # 只剩 socks：没有 PySocks 时 requests 会拒收 socks://，按 http 语义试同端口
    if parts.get("sock") or parts.get("socks"):
        addr = parts.get("socks") or parts.get("sock")
        return f"http://{addr}"
    return None


def proxies_for(url: str) -> dict[str, str] | None:
    """返回该 URL 应使用的 requests proxies 字典；无需代理返回 None。

    优先级：环境变量（显式最高）→ 系统代理 → 直连。
    `NO_PROXY` 命中的主机直连。
    """
    host = host_of(url)
    if not host:
        return None

    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    if no_proxy:
        for item in no_proxy.split(","):
            item = item.strip().lower()
            if item and (host == item or host.endswith("." + item.lstrip("."))):
                return None

    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        val = os.environ.get(name)
        if val:
            scheme = "https" if name.lower().startswith("https") else "http"
            return {scheme: val, "http": val, "https": val}

    sys_proxy = _system_proxy()
    if sys_proxy:
        return {"http": sys_proxy, "https": sys_proxy}
    return None


def note_unreachable(url: str) -> None:
    """记一次连接层不可达（超时/拒连）。"""
    host = host_of(url)
    if host:
        _DEAD[host] = time.monotonic() + _DEAD_TTL_S


def is_unreachable(url: str) -> bool:
    """该主机是否在近期被判定不可达。"""
    host = host_of(url)
    if not host:
        return False
    deadline = _DEAD.get(host)
    if deadline is None:
        return False
    if time.monotonic() >= deadline:
        _DEAD.pop(host, None)
        return False
    return True


def forget(url: str) -> None:
    """连接成功后清除该主机的不可达记录。"""
    _DEAD.pop(host_of(url), None)


def describe_egress(url: str = "") -> str:
    """一句话说明当前出口，用于失败回执——让人一眼知道是"没代理"还是"代理没开"。"""
    if url and is_unreachable(url):
        return f"{host_of(url)} 近期被判不可达（已跳过，5 分钟后自动重试）"
    p = proxies_for(url) if url else None
    if p:
        return f"经代理 {p.get('http') or p.get('https')}"
    try:
        import winreg  # noqa: F401

        if os.name == "nt":
            sys_p = _system_proxy()
            if sys_p is None:
                return "直连（系统代理未开启；若已装代理软件，请在它里面打开「系统代理」）"
    except ImportError:
        pass
    return "直连（未检测到 HTTP(S)_PROXY 环境变量）"
