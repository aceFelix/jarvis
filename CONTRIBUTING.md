# 参与贡献 J.A.R.V.I.S.

感谢你愿意参与！这份文档说明「怎么提、怎么改、怎么验、怎么合」。
**提交任何内容前，请先读一遍本文；有疑问就先开 Issue 问，不要闷头做完再提。**

---

## 1. 你可以贡献什么

不只是写代码。以下都算有效贡献：

| 类型 | 说明 |
|---|---|
| 报 Bug | 附版本、平台、复现步骤、脱敏日志 |
| 提功能建议 | 先描述场景与痛点，再谈方案 |
| 修 Bug / 加功能 | 见 §4–§7 |
| 写文档 / 翻译 | README、`docs/`、错误提示措辞都欢迎 |
| 补测试 | 尤其是跨平台（Windows / Linux）用例 |
| 复现与验证 | 在别的系统/模型/终端上验证问题并反馈结论 |

---

## 2. 开始之前

1. 搜一下已有 [Issues](../../issues) 和 PR，避免重复；
2. **较大的改动（新功能、重构、接口变更）请先开 Issue 讨论方向**，
   确认后再动手；小修复（错别字、明显 bug、补测试）可以直接提 PR；
3. 读一下 [README](README.md) 与 [docs/architecture/](docs/architecture/)，了解模块边界；
4. 安全漏洞**不要开公开 Issue**，见 [SECURITY.md](SECURITY.md)。

> `AGENTS.md` 是 AI 编码工具托管文件（含 `aoci:begin … aoci:end` 区块），
> 手工修改会被工具覆写。不要把它计入你的改动。

---

## 3. 开发环境

要求：**Python 3.11 – 3.14**，Windows / macOS / Linux 均可。

```bash
git clone https://github.com/<你的账号>/jarvis.git
cd jarvis

# 方式 A：pip（推荐新手）
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS / Linux
pip install -e ".[all]"

# 方式 B：uv（更快）
uv sync --all-extras
```

可选依赖说明（都在 `pyproject.toml` 的 extras 里）：
`gui` / `browser` / `mcp` / `camera` / `vision` / `voice` / `daemon` / `wechat`。
只跑测试不必装 `[all]`，核心依赖即可。

浏览器能力还需要单独装内核（不在 pip 依赖里）：

```bash
python -m playwright install chromium
```

跑起来验证：

```bash
jarvis --version
jarvis --doctor      # 依赖健康检查
```

---

## 4. 跑测试（提 PR 前必须过）

```bash
# 全量（CI 就是这个）
pytest tests/ -q

# 单个文件
pytest tests/core/test_checkpoint.py -q

# 单个用例
pytest tests/core/test_checkpoint.py::TestCreateRestore::test_create_returns_id_and_restore_reverts -v

# 只跑与改动相关的目录（开发时更快）
pytest tests/config tests/core -q
```

CI 还会跑两道额外门禁，本地也可以先自查：

```bash
python -m compileall -q agent
ruff check --select F821 agent          # 未定义名门禁
```

**跨平台注意**：CI 在 Linux 上跑，本地多在 Windows。
涉及路径大小写、盘符、权限、终端能力的用例，必须做平台分流
（`pytest.mark.skipif(os.name != "nt", ...)` 或写成跨平台等价断言），
否则会出现「本地绿、CI 红」。

---

## 5. 代码约定

| 约定 | 要求 |
|---|---|
| 单文件行数 | **≤ 900 行**；接近上限先拆分再写 |
| 注释 | 公共函数/类写清用途、参数、返回值；复杂逻辑写「为什么」 |
| 分层 | 视图/命令层不写业务逻辑；工具函数进 `utils/` |
| 配置项 | 新增配置必须五处同步：`config_specs.py`、`settings_domains.py`、两份 `*.example.toml`、架构文档 |
| 新命令 | 三处登记：`router.py`、`/help` 文案、`slash_completer.py` |
| 新工具 | 注册进 `core/tool.py` 的注册器，并更新工具文档 |
| 错误处理 | 旁路能力（日志/缓存/记忆）失败不得打断主流程；不要用裸 `except: pass` 吞掉原因 |
| 密钥 | 任何 `api_key` / token / 本机绝对路径都不得进代码、测试与文档 |

注释署名：新代码请用你自己的 GitHub ID（或留空），**不要**照抄历史文件的作者名。

---

## 6. 提交规范

使用 Conventional Commits，一条提交做一件事：

```
fix(checkpoint): 系统目录下启用检查点导致首轮卡死
feat(tools): 新增 WebExtract 的字段缺失统计
docs(guide): 补充默认工作区的说明
test(workspace): 覆盖 POSIX 盘根判定
refactor(config): 拆分 settings_domains 映射表
chore(ci): 缓存 pip 下载目录
```

- 标题 ≤ 72 字符，正文说明「为什么」与「影响面」；
- 关联 issue 用 `Fixes #123`；
- 不提交调试代码、临时文件、IDE 配置、`.env`；
- 仓库启用 gitleaks（密钥扫描），提交带密钥会直接失败。

---

## 7. 分支与 PR

```bash
git checkout main
git fetch upstream && git pull --ff-only upstream main   # 有 upstream 时
git checkout -b fix/checkpoint-systemdir

# 改代码 + 测试
git add -p
git commit -m "fix(checkpoint): ..."
git push -u origin fix/checkpoint-systemdir
```

然后在 GitHub 上开 PR，填写模板里的四项（改了什么 / 为什么 / 怎么验证 / 影响面）。

PR 要求：

- [ ] 一个 PR 只解决一个问题；
- [ ] CI 全绿（语法门禁 + 全量 pytest + gitleaks + Markdown 链接检查）；
- [ ] 新增或修改的行为有对应测试；
- [ ] 相关文档同步更新（README / `docs/` / `CHANGELOG.md` 的 `[Unreleased]`）；
- [ ] 不含密钥、本机路径、调试输出。

Review 期间请在**同一分支追加提交**（便于看增量），不要强推别人的分支。
如果项目要求压缩提交，维护者会在合并时处理。

---

## 8. 仓库与远程

| 平台 | 角色 |
|---|---|
| GitHub（`aceFelix/jarvis`） | 主仓库，Issue / PR / CI / Release 都在这里 |
| GitCode（`acefelix/jarvis`） | 只读镜像，方便国内访问 |

**PR 统一提到 GitHub**。GitCode 上的 fork 不会被自动同步，请不要在那边开 PR。
推送时如需同时更新两个远程，可给同一个 remote 配多个 push URL：

```bash
git remote set-url --add --push github https://gitcode.com/acefelix/jarvis.git
```

---

## 9. 发布流程（维护者）

贡献者**不需要**发版。维护者按以下顺序操作：

1. 新增 `release/vX.Y.Z` 分支，四处同步版本号：
   `pyproject.toml`、`agent/__init__.py`、`npm/package.json`、`uv.lock`；
2. 更新 `docs/log/CHANGELOG.md`（把 `[Unreleased]` 归档到新版本）；
3. 跑 `pytest tests/test_release_contract.py`（版本一致性门禁）与全量测试；
4. `python -m build --sdist --wheel` 并校验产物元数据；
5. 打 tag `vX.Y.Z` 并推送 → 触发 `.github/workflows/publish.yml`
   （PyPI → npm → GitHub Release）；
6. 发布成功后把 `release/vX.Y.Z` 合并回 `main`。

> **tag 一旦触发发布就不能重发**（PyPI / npm 版本不可覆盖），
> 所以上述第 3、4 步必须在打 tag 前完成。

---

## 10. 遇到问题

- 用法问题：先看 [USER_GUIDE.md](USER_GUIDE.md) 与 [docs/guide/](docs/guide/)；
- 报错排查：`jarvis --doctor`、`/health caps`、`~/.jarvis/logs/diag.log`；
- 仍然卡住：开 Issue，附 `jarvis --version`、平台、命令、**脱敏后**的日志片段。

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
