# npm 安装器被 PATH 中的坏 Python shim 带偏（npm-uv-python-selection-fix）

> 日期：2026-11-27 ｜ 作者：aceFelix ｜ 影响范围：`npm/install.js`（npm postinstall）

## 一、问题现象

用户在干净的 Git Bash 环境执行官方安装命令：

```bash
npm install -g @acefelix/jarvis
```

npm 包本身下载、解包、执行 postinstall 都正常，但安装 Python 依赖时失败：

```text
[jarvis] ✓ Python 3.13.14 (python)
[jarvis] ✓ 检测到 uv，使用 uv 加速安装（快 5-10x）
[jarvis] 执行: uv pip install "jarvis-agent[all]==2.6.2"
uv error: Failed to inspect Python interpreter from first executable in the search path
at `C:\ProgramData\chocolatey\bin\python3.14.exe`
Cannot find file at 'c:\python314\python.exe'
```

即：脚本已经找到可用 Python 3.13.14，uv 却另行选中了 PATH 里坏掉的 Chocolatey Python 3.14 shim。

## 二、排查过程

1. 确认不是 npm 包缺失：日志显示 postinstall 已启动，版本号也正确；
2. 确认不是 Python 3.13 不可用：`findPython()` 明确输出 `Python 3.13.14 (python)`；
3. 对比两条安装路径：
   - pip 回退：`python -m pip install ...`，绑定的是已校验解释器；
   - uv 路径：裸 `uv pip install ...`，没有 `--python`，uv 会重新扫描 PATH；
4. 实机环境同时存在：
   - 可用：`D:\Program\Python\Python313\python.exe`；
   - 坏 shim：`C:\ProgramData\chocolatey\bin\python3.14.exe` → `C:\python314\python.exe`（不存在）；
5. 根因确认：**uv 的解释器选择与 `findPython()` 的选择不是同一个来源**。

## 三、根因分析

`npm/install.js` 先用 `findPython()` 验证并选中一个 Python 命令，但检测到 uv 后只生成：

```text
uv pip install "<target>"
```

uv 默认从 PATH 中自行寻找解释器，导致“验证的是 A，安装的是 B”。在 Windows 上，Chocolatey 的版本管理 shim 很常见；只要某个 shim 指向已移动/删除的 Python，npm 安装就会失败。

## 四、修复方案

| 文件 | 改动 |
|---|---|
| `npm/install.js` | 新增 `resolvePythonExecutable()`：用已校验的 Python 执行 `sys.executable`，拿到绝对路径；新增 `buildUvInstallPrefix()`：生成 `uv pip install --python "<绝对路径>"`；uv 分支改为使用该前缀 |
| `npm/install.test.js` | 新增 3 例：解析绝对路径、查询失败回退原命令、带空格路径正确加引号 |
| `npm/package.json` | 新增 `test` 脚本：`node --test install.test.js` |
| `pyproject.toml` / `agent/__init__.py` / `npm/package.json` / `uv.lock` | 版本 2.6.2 → 2.6.3，保持发布契约四处一致 |

设计取舍：

- **不用 `--system`**：那会改变安装目标语义，且仍不保证选中正确解释器；
- **不在 PATH 上做文章**：用户机器可能有多个 Python，包装器不应替用户删改环境；
- **pip 回退不动**：`python -m pip` 本来就绑定已校验解释器。

## 五、验证结果

- `node --check npm/install.js`：通过；
- `node --test npm/install.test.js`：**3 passed**；
- 实机解析当前解释器：`D:\Program\Python\Python313\python.exe`；
- 生成命令：`uv pip install --python "D:\Program\Python\Python313\python.exe" "jarvis-agent[all]==2.6.3"`；
- `npm pack ./npm --dry-run --json`：发布包仅含 `README.md`、`install.js`、`package.json`、`run.js`，测试文件未进入发布包；
- 发布契约与全量回归在打 tag 前执行。

## 六、涉及文件

| 文件 | 改动说明 |
|---|---|
| `npm/install.js` | uv 安装显式绑定已校验解释器 |
| `npm/install.test.js` | 新增安装器回归测试 |
| `npm/package.json` | 新增 test 脚本并升版本 |
| `pyproject.toml` | 版本升 2.6.3 |
| `agent/__init__.py` | `__version__` 升 2.6.3 |
| `uv.lock` | jarvis-agent 自身版本升 2.6.3 |
| `docs/log/CHANGELOG.md` | 记录 2.6.3 修复 |
| `docs/fixlogs/ci-release/README.md`、`docs/fixlogs/README.md` | 复盘索引登记 |

## 七、经验总结

1. **“验证过的解释器”必须一路传递**：包装器检测到 Python 后，后续 pip/uv 不能重新自行选择，否则检测与安装会落在不同解释器上。
2. **Windows 的 Python shim 是常态环境**：Chocolatey、py launcher、Store alias 都可能让 PATH 里的第一个 Python 不可用；安装器要对解释器身份显式绑定。
3. **安装器也需要回归测试**：npm 包装器不是“一次性脚本”，它面向所有用户环境，关键选择逻辑必须用可注入命令执行器锁住。
4. **错误日志本身就是证据**：本次日志同时给出“已选 Python 3.13”和“uv 检查 python3.14 失败”，根因无需猜测。
