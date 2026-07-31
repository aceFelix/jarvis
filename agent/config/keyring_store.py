"""API Key 安全存储 —— 系统 keyring 集成。

S-01 改进项：用系统原生凭据管理器加密存储 API Key，替代 TOML 明文。
- Windows: Windows Credential Manager (WinVaultKeyring)
- macOS: Keychain
- Linux: Secret Service / KWallet

策略：
- 存储时优先写 keyring，失败时降级到 TOML 明文
- 读取时优先查 keyring，不存在时回退到 TOML 中的 api_key 字段
- TOML 中不再写入 api_key 明文（由 keyring 接管）

@author aceFelix
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# keyring 服务名（在所有平台上作为 namespace）
_SERVICE_NAME = "jarvis-agent"

# 厂商 → keyring 用户名（用于存储多个厂商的 Key）
_VENDOR_USER_MAP: dict[str, str] = {
    "dashscope": "api_key_dashscope",
    "deepseek": "api_key_deepseek",
    "openai": "api_key_openai",
    "zhipu": "api_key_zhipu",
    "zai": "api_key_zai",
    "anthropic": "api_key_anthropic",
    "moonshot": "api_key_moonshot",
    "minimax": "api_key_minimax",
    "mimo": "api_key_mimo",
    "siliconflow": "api_key_siliconflow",
    "google": "api_key_google",
}


def _get_keyring() -> Any | None:
    """获取 keyring 模块，不可用时返回 None。"""
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def store_api_key(vendor: str, api_key: str) -> bool:
    """将 API Key 存储到系统凭据管理器。

    Args:
        vendor: 厂商名（如 dashscope / deepseek）
        api_key: API Key 明文

    Returns:
        True 表示存储成功

    @author aceFelix
    """
    if not api_key:
        return False

    kr = _get_keyring()
    if kr is None:
        logger.debug("keyring 不可用，API Key 将写入 TOML 明文")
        return False

    username = _VENDOR_USER_MAP.get(vendor, f"api_key_{vendor}")
    try:
        kr.set_password(_SERVICE_NAME, username, api_key)
        logger.debug("keyring: 已存储 %s 的 API Key", vendor)
        return True
    except Exception as e:
        logger.warning("keyring 存储失败 (%s): %s", vendor, e)
        return False


def load_api_key(vendor: str) -> str | None:
    """从系统凭据管理器读取 API Key。

    Args:
        vendor: 厂商名

    Returns:
        API Key 明文，不存在或不可用时返回 None

    @author aceFelix
    """
    kr = _get_keyring()
    if kr is None:
        return None

    username = _VENDOR_USER_MAP.get(vendor, f"api_key_{vendor}")
    try:
        key = kr.get_password(_SERVICE_NAME, username)
        if key:
            logger.debug("keyring: 已读取 %s 的 API Key", vendor)
        return key
    except Exception as e:
        logger.debug("keyring 读取失败 (%s): %s", vendor, e)
        return None


def delete_api_key(vendor: str) -> bool:
    """从系统凭据管理器删除 API Key。"""
    kr = _get_keyring()
    if kr is None:
        return False

    username = _VENDOR_USER_MAP.get(vendor, f"api_key_{vendor}")
    try:
        kr.delete_password(_SERVICE_NAME, username)
        return True
    except Exception:
        return False


def is_available() -> bool:
    """检测 keyring 是否可用。"""
    return _get_keyring() is not None
