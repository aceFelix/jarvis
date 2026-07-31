"""Keyring 存储单元测试。

覆盖 store_api_key / load_api_key / delete_api_key / is_available。

@author aceFelix
"""

from unittest.mock import MagicMock, patch

import pytest
from agent.config.keyring_store import (
    is_available,
    store_api_key,
    load_api_key,
    delete_api_key,
)


class TestKeyringStore:
    """Keyring 存储功能测试（mock keyring）。"""

    @pytest.fixture
    def mock_keyring(self) -> MagicMock:
        """mock keyring 模块。"""
        with patch("agent.config.keyring_store._get_keyring") as mock_get:
            mock_kr = MagicMock()
            mock_get.return_value = mock_kr
            yield mock_kr

    def test_is_available_true(self, mock_keyring: MagicMock) -> None:
        assert is_available() is True

    def test_is_available_false(self) -> None:
        with patch("agent.config.keyring_store._get_keyring", return_value=None):
            assert is_available() is False

    def test_store_empty_key_returns_false(self, mock_keyring: MagicMock) -> None:
        assert store_api_key("dashscope", "") is False

    def test_store_api_key_success(self, mock_keyring: MagicMock) -> None:
        assert store_api_key("dashscope", "sk-test-key") is True
        mock_keyring.set_password.assert_called_once()

    def test_store_api_key_unknown_vendor(self, mock_keyring: MagicMock) -> None:
        """未知厂商使用 api_key_{vendor} 作为用户名。"""
        assert store_api_key("unknown_vendor", "sk-test") is True
        args = mock_keyring.set_password.call_args
        assert "api_key_unknown_vendor" in args[0]

    def test_store_api_key_exception(self, mock_keyring: MagicMock) -> None:
        mock_keyring.set_password.side_effect = RuntimeError("vault locked")
        assert store_api_key("dashscope", "sk-test") is False

    def test_load_api_key_success(self, mock_keyring: MagicMock) -> None:
        mock_keyring.get_password.return_value = "sk-loaded-key"
        result = load_api_key("deepseek")
        assert result == "sk-loaded-key"

    def test_load_api_key_not_found(self, mock_keyring: MagicMock) -> None:
        mock_keyring.get_password.return_value = None
        result = load_api_key("openai")
        assert result is None

    def test_load_api_key_exception(self, mock_keyring: MagicMock) -> None:
        mock_keyring.get_password.side_effect = RuntimeError("vault error")
        result = load_api_key("dashscope")
        assert result is None

    def test_load_api_key_unavailable(self) -> None:
        with patch("agent.config.keyring_store._get_keyring", return_value=None):
            assert load_api_key("dashscope") is None

    def test_delete_api_key_success(self, mock_keyring: MagicMock) -> None:
        assert delete_api_key("dashscope") is True
        mock_keyring.delete_password.assert_called_once()

    def test_delete_api_key_exception(self, mock_keyring: MagicMock) -> None:
        mock_keyring.delete_password.side_effect = RuntimeError("vault locked")
        assert delete_api_key("dashscope") is False

    def test_store_unavailable(self) -> None:
        with patch("agent.config.keyring_store._get_keyring", return_value=None):
            assert store_api_key("dashscope", "sk-test") is False

    def test_all_vendors_in_map(self) -> None:
        """所有内置厂商都有映射。"""
        from agent.config.keyring_store import _VENDOR_USER_MAP
        expected = {
            "dashscope", "deepseek", "openai", "zhipu", "zai",
            "anthropic", "moonshot", "minimax", "mimo",
            "siliconflow", "google",
        }
        assert set(_VENDOR_USER_MAP.keys()) == expected
