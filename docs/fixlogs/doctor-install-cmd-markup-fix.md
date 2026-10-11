# 修复：`jarvis --doctor` 安装命令列的 extras 被 rich 当作样式标签吃掉

- **日期**：2026-10-11
- **作者**：aceFelix
- **影响范围**：`agent/doctor.py`（`--doctor` 依赖健康检查的输出显示）
- **严重级别**：低（仅显示问题，不影响安装本身与诊断结论）

## 现象

运行 `jarvis --doctor` 时，「📦 Python 包状态」表格的**安装命令**列丢失了 extras 后缀。

- 期望显示：`pip install "jarvis-agent[voice]"`
- 实际显示：`pip install "jarvis-agent"`

复现步骤（最小）：

```bash
# 在缺少任一 extras 包（如 mcp/webview）的环境执行
jarvis --doctor
# 观察表格「安装命令」列，方括号内的 extras 名不见了
```

影响范围：所有带 extras 的缺失项提示（`voice`/`gui`/`browser`/`camera`/`vision`/`mcp`/`realtime_ui`/`wechat` 等）。用户照着提示执行会**只装核心包、漏装 extras**，问题反而没解决。核心依赖缺失时给的 `pip install "jarvis-agent" --force-reinstall`（无方括号）不受影响。

## 排查过程

1. 定位输出代码：`agent/doctor.py` 的 `_build_pkg_rows()` 负责拼装行数据，安装命令来自模板
   `_EXTRAS_INSTALL_TEMPLATE = 'pip install "jarvis-agent[{extras}]"'`（第 69 行），
   由 `_render_pkg_table()` 通过 `table.add_row(feature, pkg, status, install_cmd)` 渲染。
2. 确认拼接结果正确：`_EXTRAS_INSTALL_TEMPLATE.format(extras="voice")` 得到
   `pip install "jarvis-agent[voice]"`，字符串本身没问题 → 说明问题出在**渲染阶段**。
3. 确认根因：rich 的 `Table.add_row()` 对**字符串单元格默认按 markup 解析**，
   `[voice]` 被识别为样式标签而被剥除（rich 中未知标签默认静默丢弃）。
   同一表格的「状态」列恰好**故意**用了 markup（`[green]✓ 已安装[/green]`），
   因此不能对整个表格 `markup=False`，否则状态列的颜色也会失效。

## 根因

`rich` 表格字符串单元格默认走 markup 解析。安装命令里的 `[extras]` 与 rich 的
`[style]` 语法**同形**，被当成未知样式标签剥掉，于是 extras 名凭空消失。
关键在于「状态」列需要 markup、「安装命令」列需要字面文本，二者语义冲突，
不能靠全局开关一刀切。

## 修复方案

只对「安装命令」单元格禁用 markup，用一个最小、局部的改法：

1. `agent/doctor.py` 引入 `from rich.text import Text`。
2. `_render_pkg_table()` 中把安装命令包成 `Text` 实例：
   `table.add_row(feature, pkg, status, Text(install_cmd))`。
   `Text` 是按字面渲染的 renderable，不走 markup；而 `status` 仍是字符串，
   继续按 markup 渲染，`[green]✓[/green]` 等颜色不受影响。

选择该方案而非「转义模板里的方括号」，是因为：
- 从渲染层根治，任何未来新增的、含 markup 特殊字符（`[`、`]`）的安装命令都不会再被误吞；
- 不需要改数据构造层，`_build_pkg_rows()` 产出的仍是干净可读的纯文本命令。

## 验证结果

- `python -m py_compile agent/doctor.py`：exit 0。
- 用项目 venv 运行源码版 `--doctor`：表格安装命令列正确显示
  `pip install "jarvis-agent[vision]"`、`pip install "jarvis-agent[realtime_ui]"`（方括号保留）；
  「状态」列 `✓ 已安装` / `✗ 未安装` 颜色正常，未出现回归。
- 退出码逻辑不变（缺项仍返回 1），汇总行 `25/29 项通过` 正常。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/doctor.py` | 引入 `rich.text.Text`；`_render_pkg_table()` 安装命令单元格改用 `Text()` 字面渲染，避免 extras 方括号被当 markup 剥除 |

## 经验总结

- **rich markup 与「看起来普通」的字符串会冲突**：凡是用户可见的**命令、路径、代码片段**，
  只要可能含 `[`/`]`（pip extras、正则、方括号索引等），都应包成 `Text` 渲染，
  不要直接以字符串塞进 rich 的表格/面板单元格。
- **同一表格中 markup 与非 markup 混用要逐列处理**：不能图省事对整个表格关闭 markup，
  否则会连带干掉有意设置的颜色标签。
- **回归检查要看同表其它列**：本次修复后特意识别「状态」列颜色仍在，确认没有引入新问题。
- **提示类输出也要保证可执行**：doctor 的安装命令是给用户直接复制执行的，
  一旦显示被截断/剥除，会把用户引向错误操作，属"看似小事、实则误导"的问题，值得认真修。
