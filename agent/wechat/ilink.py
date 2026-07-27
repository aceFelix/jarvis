"""iLink Bot API 客户端 —— 微信 ClawBot 底层通信协议。

封装腾讯 iLink Bot API 的全部 HTTP 调用：
- 扫码登录（获取二维码 → 轮询状态 → 拿到 bot_token）
- 长轮询收消息（getupdates）
- 发送消息（sendmessage）
- 输入状态（sendtyping）
- 获取配置（getconfig → typing_ticket）

协议参考：@tencent-weixin/openclaw-weixin 2.x 系列。
域名 ilinkai.weixin.qq.com 为腾讯官方服务器。

@author aceFelix
"""

from __future__ import annotations

import base64
import json
import random
import time
from typing import Any, Callable
from urllib.parse import quote

try:
    import aiohttp
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]

# ---- 协议常量 ----

BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "2.4.3"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = str((2 << 16) | (4 << 8) | 3)
BOT_AGENT = "jarvis-wechat/1.0.0 (python)"

# 长轮询超时（服务端 hold 35s，客户端设 45s 留余量）
_POLL_TIMEOUT = aiohttp.ClientTimeout(total=50) if aiohttp else None
# 普通请求超时
_REQ_TIMEOUT = aiohttp.ClientTimeout(total=15) if aiohttp else None


def _make_headers(token: str | None = None) -> dict[str, str]:
    """构造 iLink API 请求头。

    每次请求 X-WECHAT-UIN 随机生成（协议要求）。
    """
    uin = str(random.randint(0, 0xFFFFFFFF))
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": base64.b64encode(uin.encode()).decode(),
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _base_info() -> dict[str, str]:
    """SDK 要求的 base_info 结构。"""
    return {
        "channel_version": CHANNEL_VERSION,
        "bot_agent": BOT_AGENT,
    }


class ILinkClient:
    """微信 ClawBot iLink API 客户端。

    生命周期：创建时传入 bot_token（登录后），通过 aiohttp.ClientSession 发起请求。
    所有方法均为 async，需在 asyncio loop 中调用。

    @author aceFelix
    """

    def __init__(self, bot_token: str = "", base_url: str = "") -> None:
        self._bot_token = bot_token
        self._base_url = (base_url or BASE_URL).rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    @property
    def bot_token(self) -> str:
        return self._bot_token

    @bot_token.setter
    def bot_token(self, value: str) -> None:
        self._bot_token = value

    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, value: str) -> None:
        self._base_url = (value or BASE_URL).rstrip("/")

    async def _ensure_session(self) -> "aiohttp.ClientSession":
        """懒创建 aiohttp session。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """关闭 HTTP session。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def reset_session(self) -> None:
        """强制丢弃当前 session（跨 event loop 时调用）。

        aiohttp.ClientSession 绑定创建时的 event loop，跨线程/loop 使用会报
        'attached to a different loop'。此方法直接丢弃旧 session 引用，
        下次请求时 _ensure_session() 会在当前 loop 中重新创建。
        注意：此方法为同步方法，不会关闭旧 session（旧 loop 可能已不可用）。
        """
        self._session = None

    # ---- 内部请求方法 ----

    async def _get(self, path: str, token: str | None = None) -> dict[str, Any]:
        """GET 请求，返回 JSON dict。"""
        session = await self._ensure_session()
        url = f"{self._base_url}/{path}"
        async with session.get(
            url, headers=_make_headers(token), timeout=_REQ_TIMEOUT
        ) as resp:
            text = await resp.text()
            try:
                return json.loads(text)
            except Exception:
                return {}

    async def _post(
        self, path: str, body: dict, token: str | None = None, timeout: Any = None
    ) -> dict[str, Any]:
        """POST 请求，返回 JSON dict。"""
        session = await self._ensure_session()
        url = f"{self._base_url}/{path}"
        async with session.post(
            url, json=body, headers=_make_headers(token), timeout=timeout or _REQ_TIMEOUT
        ) as resp:
            text = await resp.text()
            try:
                return json.loads(text)
            except Exception:
                return {}

    # ---- 登录流程 ----

    async def fetch_login_qrcode(
        self, local_token_list: list[str] | None = None
    ) -> dict[str, Any]:
        """获取登录二维码。

        Returns:
            {"qrcode": "...", "qrcode_img_content": "..."}
        """
        body = {"local_token_list": local_token_list or []}
        data = await self._post("ilink/bot/get_bot_qrcode?bot_type=3", body)
        if data.get("qrcode"):
            return data
        # 兼容旧版 GET 流程
        return await self._get("ilink/bot/get_bot_qrcode?bot_type=3")

    async def poll_login_status(
        self, qrcode: str, verify_code: str | None = None
    ) -> dict[str, Any]:
        """轮询扫码状态。

        Returns 可能的 key：
            bot_token, baseurl, already_connected, expired,
            scanned, need_verifycode, verify_code_blocked, redirect_base
        """
        endpoint = f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode, safe='')}"
        if verify_code:
            endpoint += f"&verify_code={quote(verify_code, safe='')}"
        status = await self._get(endpoint)
        state = status.get("status", "")

        if state == "confirmed" or status.get("bot_token"):
            return {
                "bot_token": status.get("bot_token"),
                "baseurl": status.get("baseurl") or status.get("base_url") or self._base_url,
                "ilink_bot_id": status.get("ilink_bot_id"),
                "ilink_user_id": status.get("ilink_user_id"),
            }
        if state == "binded_redirect" or status.get("binded_redirect"):
            return {"already_connected": True}
        if state == "expired":
            return {"expired": True}
        if state == "scaned_but_redirect":
            redirect_host = status.get("redirect_host")
            if redirect_host:
                return {"redirect_base": f"https://{redirect_host}"}
            return {}
        if state == "scaned":
            return {"scanned": True, "verify_code_accepted": bool(verify_code)}
        if state in ("need_verifycode", "verify_code_blocked") or status.get("need_verifycode"):
            if state == "verify_code_blocked":
                return {"verify_code_blocked": True}
            return {"need_verifycode": True, "retry_verifycode": bool(verify_code)}
        return {}

    async def wait_login_confirmation(
        self,
        qrcode: str,
        *,
        timeout_seconds: float = 600,
        verify_callback: Callable[[bool], str] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """等待扫码确认，轮询直到拿到 bot_token 或超时。

        Args:
            qrcode: 二维码标识符。
            timeout_seconds: 最大等待时间。
            verify_callback: 需要配对码时调用，参数为是否重试，返回用户输入的配对码。
                             为 None 时跳过配对码（返回失败）。
            status_callback: 状态变化通知（如 "scanned", "expired"）。

        Returns:
            {"bot_token": ..., "baseurl": ...} 或 {"timeout": True} / {"expired": True}
        """
        deadline = time.time() + timeout_seconds
        current_base_url = self._base_url
        pending_verify_code: str | None = None
        scanned_printed = False

        while True:
            if time.time() >= deadline:
                return {"timeout": True}

            try:
                # 临时切换 base_url（redirect 场景）
                old_base = self._base_url
                self._base_url = current_base_url
                result = await self.poll_login_status(qrcode, pending_verify_code)
                self._base_url = old_base
            except Exception:
                await _sleep(1)
                continue

            if result.get("bot_token"):
                # 更新 base_url
                if result.get("baseurl"):
                    current_base_url = result["baseurl"]
                return result
            if result.get("already_connected"):
                return result
            if result.get("expired"):
                if status_callback:
                    status_callback("expired")
                return result
            if result.get("verify_code_blocked"):
                return result
            if result.get("redirect_base"):
                current_base_url = result["redirect_base"]
                continue
            if result.get("scanned"):
                if pending_verify_code and result.get("verify_code_accepted"):
                    pending_verify_code = None
                if not scanned_printed:
                    scanned_printed = True
                    if status_callback:
                        status_callback("scanned")
            if result.get("need_verifycode"):
                if verify_callback is None:
                    return {"verify_code_blocked": True}
                retry = result.get("retry_verifycode", False)
                pending_verify_code = verify_callback(retry)
                if not pending_verify_code:
                    return {"verify_code_blocked": True}
                continue

            await _sleep(1)

    # ---- 消息收发 ----

    async def get_updates(self, get_updates_buf: str = "") -> dict[str, Any]:
        """长轮询获取新消息（服务端 hold ~35s）。

        Returns:
            {"msgs": [...], "get_updates_buf": "..."}
        """
        body = {"get_updates_buf": get_updates_buf, "base_info": _base_info()}
        return await self._post(
            "ilink/bot/getupdates", body, token=self._bot_token, timeout=_POLL_TIMEOUT
        )

    async def send_message(self, to_id: str, context_token: str, text: str) -> dict[str, Any]:
        """发送文本消息给微信用户。

        必须包含完整 msg 结构 + base_info，否则消息静默丢失。
        """
        client_id = f"openclaw-weixin-{random.randint(0, 0xFFFFFFFF):08x}"
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_id,
                "client_id": client_id,
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
            "base_info": _base_info(),
        }
        return await self._post("ilink/bot/sendmessage", body, token=self._bot_token)

    async def send_typing(self, user_id: str, typing_ticket: str, status: int) -> None:
        """发送输入状态。status=1 正在输入，status=2 取消。"""
        if not typing_ticket:
            return
        body = {
            "ilink_user_id": user_id,
            "typing_ticket": typing_ticket,
            "status": status,
            "base_info": _base_info(),
        }
        try:
            await self._post("ilink/bot/sendtyping", body, token=self._bot_token)
        except Exception:
            pass

    async def get_config(self, user_id: str, context_token: str) -> dict[str, Any]:
        """获取用户配置（含 typing_ticket）。"""
        body = {
            "ilink_user_id": user_id,
            "context_token": context_token,
            "base_info": _base_info(),
        }
        return await self._post("ilink/bot/getconfig", body, token=self._bot_token)


async def _sleep(seconds: float) -> None:
    """asyncio.sleep 的封装，便于统一处理。"""
    import asyncio
    await asyncio.sleep(seconds)
