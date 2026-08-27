"""P2 端到端冒烟测试：真实连接 acefelix MCP server，验证图谱画像注入链路。

链路：load_settings（[profile_bridge] 解析）→ MCPClient 连接 →
preload_kg_profile（get_profile 拉取）→ 渲染成 system prompt 画像段。

用法: python scripts/smoke_profile_bridge.py

@author aceFelix
"""

from __future__ import annotations

import asyncio

from agent.config.settings import load_settings
from agent.core.extensions.mcp_client import MCPClient, load_mcp_config
from agent.core.extensions.profile_bridge import kg_profile_section, preload_kg_profile


async def main() -> None:
    s = load_settings()
    print(f"[1] 配置: enabled={s.profile_bridge_enabled} "
          f"server={s.profile_bridge_server} token_limit={s.profile_bridge_token_limit}")

    cfg = load_mcp_config()
    server = s.profile_bridge_server
    assert server in cfg, f"mcp.json 未配置 {server}"

    client = MCPClient()
    conn = await client.connect(server, cfg[server])
    print(f"[2] MCP 连接 {server}: {'成功' if conn else '失败'}")
    assert conn is not None

    ok = await preload_kg_profile(client, s)
    print(f"[3] 图谱画像预加载: {'成功' if ok else '失败'}")
    section = kg_profile_section()
    print(f"[4] 渲染结果（前 500 字）:\n{section[:500]}")

    await client.disconnect_all()
    assert ok and section, "端到端冒烟失败"
    print("[OK] P2 图谱→jarvis 链路冒烟通过")


if __name__ == "__main__":
    asyncio.run(main())
