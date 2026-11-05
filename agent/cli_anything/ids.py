"""CLI-Anything harness id 的归一化规则（唯一实现）。

为什么单独一个模块：这一条规则同时决定三件事——registry 查询用的键、
`disabled.json` 里存的键、以及从 SKILL.md 路径反解出的 id。原先它躺在 `market.py`
里叫 `_normalize_id`，而拆出去的 `harness_state.py`（启用/禁用状态）也需要它。
两个模块各写一份的后果不是崩，而是"禁用的键和查询的键不是同一个"——静默失效，
最难查的那一类。

按仓库规则（公共纯函数一律独立成模块，不留在业务文件里），这里只放这一个纯函数。

@author aceFelix
"""

from __future__ import annotations

#: harness 仓库里 id 的规范前缀；用户输入常带也可能不带，一律去掉后再比。
_HARNESS_ID_PREFIX = "cli-anything-"


def normalize_harness_id(value: str) -> str:
    """把用户输入或路径片段归一化成 harness id（去首尾空白、转小写、去规范前缀）。

    Returns:
        归一化后的 id；输入为空或全空白时返回空串，调用方据此判"没给 id"。

    @author aceFelix
    """
    v = value.strip().lower()
    if v.startswith(_HARNESS_ID_PREFIX):
        return v[len(_HARNESS_ID_PREFIX):]
    return v
