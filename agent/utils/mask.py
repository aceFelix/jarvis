"""敏感字段脱敏工具。

S-03 改进项：统一脱敏显示 API Key、密码等敏感字段。
从 config_commands.py 提取为共享模块，供 doctor、错误提示等所有展示场景使用。

@author aceFelix
"""

from __future__ import annotations

import re


def mask_key(key: str) -> str:
    """脱敏密钥：只显示首 4 + 尾 4 位，中间用 * 替代。

    少于 8 位的短 Key 只显示前 2 位 + ****。
    空字符串返回 "(未设置)"。

    @author aceFelix
    """
    if not key:
        return "(未设置)"
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:4]}...{key[-4:]}"


def mask_url_key(url: str) -> str:
    """从 URL 中脱敏 api_key/sk- 等查询参数。

    匹配 ?api_key=xxx 或 ?key=sk-xxx 或 Authorization header 中的 Bearer token。

    @author aceFelix
    """
    # api_key/sk- 查询参数
    url = re.sub(r'([?&](?:api_key|key|token|secret)=)sk-[^&\s]+', r'\1sk-****', url)
    url = re.sub(r'([?&](?:api_key|key|token|secret)=)[^&\s]{8,}', r'\1****', url)
    return url


def mask_error_message(msg: str) -> str:
    """从错误消息中脱敏所有敏感信息。

    处理：URL 中的 api_key、Bearer token、sk- 开头的密钥。

    @author aceFelix
    """
    # Bearer token
    msg = re.sub(r'(Bearer\s+)sk-[^\s,;]+', r'\1sk-****', msg)
    msg = re.sub(r'(Bearer\s+)[^\s,;]{20,}', r'\1****', msg)
    # sk- 密钥
    msg = re.sub(r'sk-[a-zA-Z0-9]{20,}', 'sk-****', msg)
    # URL 中的 key
    msg = mask_url_key(msg)
    return msg
