"""脱敏工具单元测试。

覆盖 mask_key / mask_url_key / mask_error_message 三个函数。

@author aceFelix
"""

import pytest
from agent.utils.mask import mask_key, mask_url_key, mask_error_message


class TestMaskKey:
    """密钥脱敏测试。"""

    def test_empty(self) -> None:
        assert mask_key("") == "(未设置)"

    def test_short_key(self) -> None:
        """少于等于 8 位：前 2 位 + ****"""
        assert mask_key("sk-abc") == "sk****"
        assert mask_key("12345678") == "12****"

    def test_long_key(self) -> None:
        """大于 8 位：前 4 位 + ... + 后 4 位"""
        result = mask_key("sk-1234567890abcdefghij")
        assert result.startswith("sk-1")
        assert result.endswith("hij")
        assert "..." in result

    def test_exact_sk_format(self) -> None:
        """DashScope Key: sk-xxxx...xxxx 格式"""
        key = "sk-1234567890abcdefghij1234567890"
        result = mask_key(key)
        assert result == "sk-1...7890"


class TestMaskUrlKey:
    """URL 中密钥脱敏测试。"""

    def test_no_key_unchanged(self) -> None:
        url = "https://api.example.com/v1/chat"
        assert mask_url_key(url) == url

    def test_api_key_param_masked(self) -> None:
        url = "https://api.example.com/v1?api_key=sk-1234567890abcdef"
        result = mask_url_key(url)
        assert "sk-****" in result
        assert "sk-1234567890abcdef" not in result

    def test_key_param_masked(self) -> None:
        url = "https://api.example.com/v1?key=my-secret-key-12345"
        result = mask_url_key(url)
        assert "****" in result
        assert "my-secret-key-12345" not in result

    def test_token_param_masked(self) -> None:
        url = "https://api.example.com/v1?token=abcdefgh12345678"
        result = mask_url_key(url)
        assert "****" in result
        assert "abcdefgh12345678" not in result

    def test_short_value_unchanged(self) -> None:
        """短值（< 8 字符）不脱敏"""
        url = "https://api.example.com/v1?key=short"
        assert mask_url_key(url) == url


class TestMaskErrorMessage:
    """错误消息脱敏测试。"""

    def test_sk_key_masked(self) -> None:
        msg = "Error: invalid api_key sk-1234567890abcdefghij12345"
        result = mask_error_message(msg)
        assert "sk-****" in result
        assert "sk-1234567890abcdefghij12345" not in result

    def test_bearer_token_masked(self) -> None:
        msg = "Authorization: Bearer sk-1234567890abcdefghij"
        result = mask_error_message(msg)
        assert "sk-****" in result
        assert "sk-1234567890abcdefghij" not in result

    def test_url_key_in_error_masked(self) -> None:
        msg = "Connection to https://api.com/v1?api_key=sk-abcdef1234567890 failed"
        result = mask_error_message(msg)
        assert "sk-****" in result

    def test_no_sensitive_unchanged(self) -> None:
        msg = "Connection timeout after 30 seconds"
        assert mask_error_message(msg) == msg
