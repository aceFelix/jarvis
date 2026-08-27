"""冒烟测试：画像提炼 provider 真实链路。

链路：load_settings（[memory.refine] 解析）→ _build_refine_provider
（key 解析链：环境变量优先）→ 真实调用提炼模型一句话，确认鉴权通过。

用法: python scripts/smoke_refine_provider.py

@author aceFelix
"""

from __future__ import annotations

import asyncio
import sys

from agent.config.settings import load_settings
from agent.core.memory.profile_refiner import _build_refine_provider, _call_llm


async def main() -> int:
    s = load_settings()
    print(f"[1] refine 配置: model={s.profile_refine_model} "
          f"api_format={s.profile_refine_provider or '(继承)'} "
          f"api_key={'(留空→走解析链)' if not s.profile_refine_api_key else '(显式配置)'}")

    provider = _build_refine_provider(s)
    print(f"[2] provider 构建: {type(provider).__name__}")
    assert provider is not None

    try:
        out = await _call_llm(provider, s.profile_refine_model, "只回复两个字：正常")
        print(f"[3] 模型响应: {out.strip()[:80]!r}")
        assert out.strip(), "模型返回空内容"
        print("[OK] 提炼链路鉴权通过，deepseek-v4-flash 可用")
        return 0
    finally:
        await provider.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
