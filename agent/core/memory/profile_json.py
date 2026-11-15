"""画像提炼的 JSON 容错解析（抽取段 / 裁决段共用）。

小模型经常把 JSON 包在 ```json 围栏里、前后带一句话，或直接吐半个对象。
这里按"直接解析 → 剥围栏 → 截首个 { 到末个 }"三级降级，失败一律返回 {},
由调用方当作"本轮无可提取内容"处理——绝不抛异常打断提炼。

@author aceFelix
"""

from __future__ import annotations

import json


def parse_json_object(raw: str) -> dict:
    """把模型输出解析成 dict；失败返回空 dict。"""
    text = (raw or "").strip()
    if not text:
        return {}
    # 剥 markdown 围栏
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[1] if len(parts) > 1 else ""
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    # 截取 {...}
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}
