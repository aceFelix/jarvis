"""IP 地理定位工具。

通过多个 IP 定位 API 获取当前设备的近似地理位置（城市级精度）。
无需 API Key，无需 GPS/定位服务，只要能上网即可。
多 provider 自动容错：一个挂了就试下一个。

@author aceFelix
"""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult, ValidationResult
from agent.core.tool import JSONSchema, Tool

_TIMEOUT = 8.0

# 按优先级排列：国内 API 优先（不被墙），国际 API 备用
_PROVIDERS = [
    # PConline（太平洋网络，国内可靠）
    {
        "name": "PConline",
        "url": "https://whois.pconline.com.cn/ipJson.jsp?json=true",
        "headers": {"User-Agent": "Mozilla/5.0"},
        "parser": "_parse_pconline",
    },
    # ip-api（国际，国内可能被墙）
    {
        "name": "ip-api",
        "url": "http://ip-api.com/json/",
        "headers": {"User-Agent": "jarvis-agent/1.0"},
        "parser": "_parse_ipapi",
    },
]


def _do_request(url: str, headers: dict[str, str]) -> str:
    req = Request(url, headers=headers)
    with urlopen(req, timeout=_TIMEOUT) as resp:
        raw = resp.read()
        # PConline 等国内 API 返回 GBK，先尝试 UTF-8 再回退 GBK
        for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")


# ---- PConline parser ----

def _parse_pconline(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    return {
        "ip": data.get("ip", ""),
        "country": data.get("addr", "").split(" ")[0] if data.get("addr") else "",
        "countryCode": data.get("proCode", ""),
        "regionName": data.get("pro", ""),
        "city": data.get("city", ""),
        "isp": data.get("addr", ""),
    }


# ---- ip-api parser ----

def _parse_ipapi(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if data.get("status") == "fail":
        raise RuntimeError(data.get("message", "ip-api 查询失败"))
    return {
        "ip": data.get("query", ""),
        "country": data.get("country", ""),
        "countryCode": data.get("countryCode", ""),
        "regionName": data.get("regionName", ""),
        "city": data.get("city", ""),
        "lat": data.get("lat", ""),
        "lon": data.get("lon", ""),
        "timezone": data.get("timezone", ""),
        "isp": data.get("isp", ""),
    }


class LocationTool(Tool):
    name = "Location"
    description = (
        "通过 IP 地址获取当前设备的近似地理位置（城市级精度）。"
        "无需 GPS 或定位权限，基于公网 IP 反查。"
        "返回城市、地区、国家、经纬度、ISP 等信息。"
        "也可传入 IP 地址查询指定 IP 的位置。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "ip": {
                "type": "string",
                "description": "要查询的 IP 地址（可选，留空则查询当前设备 IP 的位置）",
            },
        },
        "required": [],
    }
    max_result_chars = 2_000

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def is_concurrency_safe(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("只读网络访问（IP 定位）")

    def validate_input(self, args: dict[str, Any], ctx: ToolContext) -> ValidationResult:
        ip = args.get("ip", "").strip()
        if ip:
            parts = ip.split(".")
            if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
                return ValidationResult.fail(f"无效 IP 地址: {ip}")
        return ValidationResult.pass_()

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import asyncio

        ip = args.get("ip", "").strip()
        query_label = ip if ip else "当前设备"

        errors: list[str] = []
        for provider in _PROVIDERS:
            if ctx.ui:
                ctx.ui.info(f"定位中: {query_label}（{provider['name']}）")

            # 如果有指定 IP，拼到 URL 里
            url = provider["url"]
            if ip and "ip-api" in url:
                url = url.rstrip("/") + "/" + ip

            try:
                loop = asyncio.get_event_loop()
                parser = globals()[provider["parser"]]
                raw = await loop.run_in_executor(
                    None, _do_request, url, provider["headers"]
                )
                data = await loop.run_in_executor(None, parser, raw)
            except Exception as e:
                errors.append(f"{provider['name']}: {e}")
                continue

            lines = [
                f"位置信息（{query_label}，数据来源: {provider['name']}）",
                "",
                f"  IP        | {data.get('ip', 'N/A')}",
                f"  国家/地区 | {data.get('country', 'N/A')} ({data.get('countryCode', 'N/A')})",
                f"  省份/州   | {data.get('regionName', 'N/A')}",
                f"  城市       | {data.get('city', 'N/A')}",
            ]
            if data.get("lat"):
                lines.append(f"  经纬度     | {data['lat']}, {data['lon']}")
            if data.get("timezone"):
                lines.append(f"  时区       | {data['timezone']}")
            lines.append(f"  运营商     | {data.get('isp', 'N/A')}")
            return ToolResult(data="\n".join(lines))

        return ToolResult(
            data=f"定位失败（已尝试 {len(_PROVIDERS)} 个数据源）:\n"
            + "\n".join(f"  - {e}" for e in errors),
            is_error=True,
        )
