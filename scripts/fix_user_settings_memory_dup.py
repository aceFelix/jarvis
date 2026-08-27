"""一次性修复脚本：合并 ~/.jarvis/settings.toml 中重复的 [memory] 表。

问题：文件里有两个 [memory] 表（第 101 行会话持久化段 + 第 228 行画像记忆段），
TOML 规范禁止重复表名，tomllib 解析报错后被 _read_toml 静默吞掉，
导致整份用户级配置（voice/tts/email/custom_models 等）从未生效。

修复：删除第二个 [memory] 块，其四个键以解析器识别的 profile_* 前缀并入第一个块；
[memory.refine_model] 表名/字段名同步修正为解析器识别的 [memory.refine] 标准格式。
先备份到 settings.toml.bak-p2，修复后用 tomllib 校验。

@author aceFelix
"""

from __future__ import annotations

import re
import shutil
import sys
import tomllib
from pathlib import Path

p = Path.home() / ".jarvis" / "settings.toml"
bak = p.with_suffix(".toml.bak-p2")
shutil.copy2(p, bak)
c = p.read_text(encoding="utf-8")

# 1. 删除第二个（重复的）[memory] 块
dup = re.compile(
    r"(?m)^\[memory\]\r?\nenabled = true[^\r\n]*\r?\n"
    r"max_entries = \d+[^\r\n]*\r?\n"
    r"inject_token_limit = \d+[^\r\n]*\r?\n"
    r"refine_min_messages = \d+[^\r\n]*\r?\n"
)
if not dup.search(c):
    sys.exit("ERR: duplicate [memory] block not found")
c = dup.sub("", c, count=1)

# 2. 在第一个 [memory] 块尾部（long_term_memory 行后）并入 profile_* 字段（值沿用原重复块）
merge = (
    "# 画像记忆（原文件后段重复的 [memory] 块合并至此——TOML 禁止重复表名，\n"
    "# 重复会导致整份配置解析失败全部失效；字段名改为解析器识别的 profile_* 前缀）\n"
    "profile_enabled = true            # 画像总开关，关闭后不提炼也不注入\n"
    "profile_max_entries = 200         # 条目上限\n"
    "profile_inject_token_limit = 500  # 注入限额（token）\n"
    "profile_refine_min_messages = 6   # 会话至少多少条消息才触发提炼"
)
anchor = re.compile(r"(?m)^(long_term_memory = true[^\r\n]*)")
if not anchor.search(c):
    sys.exit("ERR: anchor long_term_memory not found")
c = anchor.sub(lambda m: m.group(1) + "\n" + merge, c, count=1)

# 3. [memory.refine_model] 表名与 provider 字段不被解析器识别，
#    改为标准 [memory.refine] + api_format（model/api_key 行原样保留，不触碰密钥）
refine = re.compile(
    r"(?m)^\[memory\.refine_model\][^\r\n]*\r?\nprovider = \"dashscope\"[^\r\n]*\r?\n"
)
if not refine.search(c):
    sys.exit("ERR: refine_model block not found")
c = refine.sub(
    "[memory.refine]\n"
    "# 独立提炼模型（原表名 refine_model / provider 字段不被解析器识别，已修正为标准字段）\n"
    'api_format = "dashscope"\n',
    c,
    count=1,
)

p.write_text(c, encoding="utf-8")

# 4. 校验：能解析 + 关键表都在
with p.open("rb") as f:
    data = tomllib.load(f)
print("profile_bridge =", data.get("profile_bridge"))
print("memory keys    =", sorted(data.get("memory", {}).keys()))
print("refine model   =", data.get("memory", {}).get("refine", {}).get("model"))
print("OK: fixed & validated, backup at", bak)
