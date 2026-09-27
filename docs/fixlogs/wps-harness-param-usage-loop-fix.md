# WPS harness 传参死循环修复（subcommand 内嵌 flag + 回执无引导）

> 日期：2026-09-27 ｜ 涉及：`agent/cli_anything/runner.py`、`agent/tools/extensions/cli_anything_tool.py`、jarvis-harness-market `harnesses/wps/SKILL.md`

## 现象

用户让 jarvis「用 WPS 写一份介绍 jarvis 的文档」，模型加载 `cli_anything__wps` 后陷入死循环：

1. 传 `{"subcommand": "writer --help"}` → argparse 报 `invalid choice: 'writer --help'`
2. 传 `{"subcommand": "writer", "action": "--help"}` → argparse 报 `argument --action: expected one argument`
3. 两次失败回执都只是 argparse 原始报错，模型看不出正确格式，反复重试/换错误传法，始终没有走到 `{"subcommand": "writer", "action": "new_doc"}` 这一有效调用

WPS harness 本体（COM 链路）实测正常，问题出在「模型传参」与「失败反馈」两端。

## 根因

| 层 | 问题 |
|---|---|
| market 侧 SKILL.md（主因） | ① `action` 描述写「见命令树」，但命令树在 SKILL.md **正文**里，jarvis 只把 frontmatter 注入模型上下文，正文对模型不可见，模型看不到可用动作只能去探索 `--help`；② `examples` 是裸 CLI 字符串（`jarvis-harness-wps writer --action open_doc ...`），暗示模型「把整串命令塞进一个字符串」；③ `subcommand` 描述未声明禁止内嵌 flag |
| jarvis 侧 runner（次因） | `_build_args` 对 positional 参数原样传单 token，`"writer --help"` 必然 `invalid choice`，哪怕模型语法意图是对的 |
| jarvis 侧工具回执（次因） | 失败回执只透传 argparse 报错，不附正确传参格式，模型无法自我纠正 |

另排除一个误判：ToolSearch 拼接输出多工具 schema，终端里 `"required": ["action"]` 是别的工具的片段，wps 的 `required: ["subcommand"]` 生成无误，市场安装/加载链路本身没有 bug。

## 修复

### jarvis（`agent/cli_anything/`）

- `runner.py::_build_args`：positional 值含空格时自动拆成多个 token（无引号按空白 `split()`；含引号按「引号段/非空白段」正则切分并去引号，不用 shlex 以规避 Windows 反斜杠转义与引号保留问题）。`"writer --help"` 现在会拆成 `["writer", "--help"]` 到达 harness，argparse 正常输出该子命令用法（含 action 枚举），模型可自救。
- `cli_anything_tool.py`：新增 `_build_usage_hint()`，失败回执 stderr 命中 argparse 报错特征（`invalid choice` / `expected one argument` / `unrecognized arguments` 等）时，末尾附加「正确传参格式: WRITER | SHEET | ...；各参数需分开传」提示。非参数类错误（如 COM 业务失败）不加提示，避免误导。

### market（`harnesses/wps/SKILL.md`）

- `action` 描述内联全部三个子命令的动作枚举（不再依赖正文）
- `subcommand` 描述加防呆声明：只能传单个枚举值，禁止内嵌空格或 `--help`
- `examples` 全部改为工具调用 JSON 参数格式（`{"subcommand": "writer", "action": "new_doc"}`）
- 正文「注意事项」补充 Agent 分开传参说明
- 已同步安装副本 `~/.jarvis/cli_anything/wps/SKILL.md`

### market 仓库防回归测试

- `tests/test_harnesses.py::test_skill_md_arg_desc_self_contained`：断言所有 harness 的参数描述不得出现「见命令树/见正文」

## 测试

- `tests/cli_anything/test_runner.py`（新增 9 用例）：positional 单 token 不变 / 空格自动拆分 / 引号段切分去引号 / 非位置参数不拆 / 布尔 flag 回归 / usage hint 生成与空场景 / argparse 错误回执附提示 / 业务错误回执不附提示
- jarvis 全量：`python -m pytest tests/ -q` → **1966 passed**
- market 全量：`python -m pytest tests/ -q` → **31 passed**
- 端到端：`jarvis-harness-wps --harness-dir ... writer --help` 直接调用返回用法文本 + action 枚举，exit 0

## 边界与已知限制

- 含引号的 positional 值按正则切分，未处理转义引号（`\"`）嵌套场景；LLM 实际传参极少出现，遇到时退化为整段保留
- `_ARGPARSE_ERROR_MARKERS` 含宽泛的 `required` 关键词，理论上业务报错文本含该词也会触发提示，但提示本身无害（仅追加一行格式说明）
- market 侧 SKILL.md 改动需推送 GitHub 后，其他用户重新 install 才生效；本地已同步安装副本
