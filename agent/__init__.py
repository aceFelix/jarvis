"""jarvis: 个人电脑 AI Agent（J.A.R.V.I.S.）"""

# 版本号与 pyproject.toml / npm/package.json 三处双写：前两处由 publish.yml 校验，
# 第三处（本行）由 tests/test_release_contract.py 把关（旧注释说的“不在 CI 校验范围”
# 已由该测试补齐）——`jarvis --version` 与 PyPI 包版本不一致会被测试直接拦下。
# 发布提升版本时三处一并改（另加 uv.lock 里 `name = "jarvis-agent"` 那一条）。
__version__ = "2.5.3"
