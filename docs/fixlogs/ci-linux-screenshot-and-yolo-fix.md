# CI 在 Linux/3.14 上的截图与 yolo 用例修复（ci-linux-screenshot-and-yolo-fix）

> 日期：2026-11-14 ｜ 作者：aceFelix ｜ 影响范围：`pyproject.toml`（`[dev]` extra）、`tests/permissions/test_yolo_scope.py`

## 一、问题现象

GitHub Actions 在 `ubuntu-latest` + **Python 3.14.10** 上跑全量 `pytest tests/ -v --tb=short`：
**5 failed / 5066 passed / 87 skipped**。失败分两组，且与业务断言无关：

```
FAILED tests/permissions/test_yolo_scope.py::TestYoloScope::test_explicit_empty_hard_deny_disables_only_that_list
  assert <PermissionBehavior.ALLOW> == <PermissionBehavior.ASK>

FAILED tests/tools/test_screenshot_image_ref.py::TestScreenshotTraceMeta::test_ok_factory_carries_image_ref
FAILED tests/tools/test_screenshot_image_ref.py::TestScreenshotTraceMeta::test_trace_meta_carries_no_base64
FAILED tests/tools/test_screenshot_image_ref.py::TestScreenshotTraceMeta::test_receipt_keeps_see_clause
FAILED tests/tools/test_screenshot_image_ref.py::TestScreenshotTraceMeta::test_file_is_really_written
  ToolResult(data="pyautogui 未安装: No module named 'PIL'", is_error=True)
```

同一份代码在本地 Windows 上**全绿**，说明不是逻辑回归。

## 二、排查过程

1. **先排除本次改动**：本次提交只新增/Markdown 两个文件，不触碰 Python，失败文件与本次改动无关。
2. **对齐环境差异**：CI 用 `pip install ".[dev]"`（`ci.yml` §Install dependencies），本地开发环境装了 `[gui]/[camera]`。
3. **第一组（截图）**：读 `test_screenshot_image_ref.py` 第 31–39 行——测试自己的假 pyautogui 里
   `from PIL import Image`。`pyproject.toml` 中 `pillow>=10.0.0` 只出现在 `[gui]` 与 `[camera]`，
   **`[dev]` 里没有**。于是 `_import_pyautogui()` 抛 `No module named 'PIL'`，被工具侧
   `except ImportError` 兜成"pyautogui 未安装"，四条断言全线崩。
4. **第二组（yolo）**：读用例——目标取 `Path.home()/".ssh"/"id_rsa"`，`_ctx()` 默认保护根是
   `("c:\\",)`。Windows 上 home 在 `C:\Users\...`（落在保护域内 → ASK）；**Linux 上 home 是
   `/home/runner`，不在 C 盘**（→ 落到"非受保护即放行" → ALLOW）。
5. **本地复现环境假设**：`python -m pytest tests/tools/test_screenshot_image_ref.py tests/permissions/test_yolo_scope.py -q`
   → 20 passed（本地 PIL 12.2.0），进一步确认是环境假设而非代码缺陷。
6. **验证 Linux 路径归一化**：直接调 `yolo_gate._norm/under_any`，`/home/runner` →
   `\home\runner`，`under_any(target, ["c:\\"])` 返回 `None`——与 CI 的 ALLOW 完全对应。

## 三、根因分析

两个**独立的测试环境假设**，被同一个事件同时引爆：

- **依赖归属错误**：截图/图片窗口用例的意图是"用假 pyautogui 把 `call()` 真正跑到底、真编码真落盘"
  （2026-11-11 `ToolResult.ok(trace_meta=...)` 事故后加的钉子）。它需要**真 PIL**，
  但 Pillow 只声明在功能 extras 里，测试安装集 `[dev]` 没带上。
- **平台假设**：保护根默认值是 `c:\`（Windows 语义），用例却用 `Path.home()` 取目标，
  在 POSIX 上两者天然不相交。这条不变量（"两条清单互不顶替"）本身与平台无关，
  是测试把平台细节写死进了夹具。

**为什么以前没红**：这 27 个提交此前只存在于本地与 gitcode，GitHub 侧 CI 从未跑到；
本次推送后 GitHub CI 首次完整执行这条代码路径，问题才暴露。

## 四、修复方案

1. **`pyproject.toml` → `[dev]` 增加 `pillow>=10.0.0`**（带注释说明原因）。
   刻意**不**改成 `pip install ".[dev,gui]"`：`pyautogui` 在无头 Linux 还需要 X11/scrot，
   为跑测试引入整套 GUI 栈不划算；测试只需要 Pillow 这一个真编码器。
2. **`tests/permissions/test_yolo_scope.py`**：保护根显式传当前用户 home
   `_ctx(roots=(str(Path.home()),), hard=())`，让"硬禁止清单写空、但保护域仍在"这条不变量
   在 Windows 与 Linux 上是同一个断言。默认 C 盘根不丢覆盖——由
   `test_guard_roots_default_is_c_drive` 单独钉住。

两者都是**测试/测试依赖**修正，不改任何运行时行为。

## 五、验证结果

| 验证项 | 结果 |
|---|---|
| 本地两个失败文件 | **20 passed** |
| `tests/permissions` + `tests/tools` + `test_image_state.py` | **355 passed** |
| `[dev]` 解析 | `['pytest>=8.0.0', 'pytest-asyncio>=0.23.0', 'ruff>=0.5.0', 'pillow>=10.0.0']` |
| Linux 路径模拟 | `_norm('/home/runner')='\home\runner'`，`under_any(target,[root])` 命中 → 会得到 ASK |
| CI 复核 | 待本次推送后由 GitHub Actions 在 3.11/3.12/3.13/3.14 四档确认 |

未新增/未修改任何生产代码路径，无运行时回归面。

## 六、涉及文件

| 文件 | 改动说明 |
|---|---|
| `pyproject.toml` | `[dev]` 增加 `pillow>=10.0.0`，附原因注释 |
| `tests/permissions/test_yolo_scope.py` | `test_explicit_empty_hard_deny_disables_only_that_list` 显式传 home 作保护根，平台无关 |
| `docs/fixlogs/ci-linux-screenshot-and-yolo-fix.md` | 本复盘 |

## 七、经验总结

1. **测试所需的依赖必须进 `[dev]`**：只要用例真的 import 某个可选依赖（哪怕只在测试的假件里），
   它就不再是"可选"，CI 的安装集必须覆盖。判定标准是"这条用例在干净环境能不能跑"，不是"我机器上装了"。
2. **默认值里的平台语义别进断言**：`c:\` 是 Windows 语义，`Path.home()` 是跨平台 API，两者组合必然在
   POSIX 上错位。凡是"某平台默认值 + 跨平台取路径"的用例，要么显式传平台无关的配置，要么标平台门禁。
3. **优先让用例平台无关，而不是 skip**：本条的语义（两条清单互不顶替）与平台无关，显式传 roots
   就能在两边都跑；只有"能力本身是平台专属"（如 PowerShell 体检）才该 skip。
4. **双平台要定期同步主干**：这类问题不是新引入的，而是 GitHub 侧 CI 长期没跑到新代码造成的"CI 腐化"。
   一个特性分支只在单平台合 PR 后，必须及时把主干推到另一侧，否则红灯会在最不合适的时刻集中爆发。
5. **报错文案会骗人**："pyautogui 未安装" 的真因是缺 Pillow——`except ImportError` 把两类缺失
   兜成同一句话。排查时不要停在文案上，要回到 `pyproject` 看依赖到底声明在哪一层。
