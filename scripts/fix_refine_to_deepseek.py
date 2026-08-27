"""一次性脚本：把 [memory.refine] 从失效的 DashScope 提炼模型切换为
deepseek-v4-flash（anthropic 协议），api_key 留空走 DEEPSEEK_API_KEY 环境变量。

背景：原 [memory.refine] 配了 qwen3.7-flash + 硬编码 DashScope key，
该 key 失效后画像提炼报 "Authentication Fails ... ****Yftw is invalid"。

做法：备份 → 整块替换 [memory.refine] → tomllib 校验。

@author aceFelix
"""

from __future__ import annotations

import re
import shutil
import sys
import tomllib
from pathlib import Path

TARGET = Path.home() / ".jarvis" / "settings.toml"

NEW_BLOCK = '''[memory.refine]
# 独立提炼模型：复用自定义模型 deepseek-v4-flash（anthropic 协议）
# api_key 留空 → refiner 按解析链回退：自定义模型配置 → DEEPSEEK_API_KEY 环境变量
api_format = "anthropic"
model = "deepseek-v4-flash"
base_url = "https://api.deepseek.com/anthropic"
api_key = ""
'''


def main() -> int:
    if not TARGET.exists():
        print(f"[ERR] 找不到 {TARGET}")
        return 1

    text = TARGET.read_text(encoding="utf-8")

    # 定位 [memory.refine] 块：从表头到下一个顶层 [xxx] 表头之前
    pattern = re.compile(
        r"^\[memory\.refine\][^\[]*",
        re.MULTILINE,
    )
    if not pattern.search(text):
        print("[ERR] 未找到 [memory.refine] 表")
        return 1

    # 备份
    backup = TARGET.with_suffix(".toml.bak-refine")
    shutil.copy2(TARGET, backup)
    print(f"[OK] 备份: {backup}")

    new_text = pattern.sub(NEW_BLOCK + "\n", text, count=1)

    # tomllib 校验后再写回
    try:
        data = tomllib.loads(new_text)
    except Exception as e:
        print(f"[ERR] 修改后 TOML 解析失败，未写回: {e}")
        return 1

    refine = data.get("memory", {}).get("refine", {})
    print(f"[OK] 新 [memory.refine]: {refine}")

    TARGET.write_text(new_text, encoding="utf-8")
    print("[OK] 已写回")
    return 0


if __name__ == "__main__":
    sys.exit(main())
