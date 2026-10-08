# 扩展生态与协作

> [← 返回 README](../../README.md) · [核心机制](concepts.md) · [多 Agent 协作架构](../architecture/11-多Agent协作.md) · [扩展生态架构](../architecture/10-扩展生态.md)

---

## 多 Agent 协作

Jarvis 支持派生子 Agent 并行处理复杂任务，以及团队协作模式：

- **子代理**：主 Agent 可创建子代理处理独立的子任务，结果汇总后继续
- **批量并行**：一次调用 `Agent` 工具可同时派发多个同步子任务，结果按编号聚合
- **团队模式**：创建 Agent 团队，分配不同角色和工具集
- **后台队友**：`Agent` 工具的 `run_in_background=true` 模式会创建持久 teammate，加入团队并通过邮箱持续通信
- **自动任务领取**：后台 teammate 空闲时会自动从共享 `TaskList` 领取 pending 且无阻塞的任务并执行
- **计划审批**：在 PLAN/ASK 权限模式下，teammate 执行写操作前会向 leader 发送 `plan_approval_request`，leader 审批后才继续
- **任务管理**：共享任务列表，支持依赖链、owner 分配、完成回调
- **团队状态查询**：`TeamStatus` 工具可查看成员状态、任务统计、未读邮件数
- **生命周期管理**：`TaskStop` 工具可终止后台 teammate；teammate 每 30 秒发送心跳保活
- **消息邮箱**：Agent 之间通过文件邮箱通信

管理命令：`/agents` `/tasks` `/plan`

### 典型用法

```text
> 创建 code-review 团队，分配 reviewer 和 tester
> 用 TaskCreate 创建审查任务和测试任务
> 用 Agent run_in_background=true 启动 reviewer/tester
> 队友会自动领取并执行任务，完成后通过邮箱通知 leader
> 用 TeamStatus 查看进度，用 TaskStop 终止队友
```

详见 [docs/architecture/11-多Agent协作.md](../architecture/11-多Agent协作.md)。

## 插件系统

Jarvis 有两个独立的插件市场，各自管理：

### Plugin 系统（GitHub 插件）

```bash
/plugin                       # 列出已安装插件
/plugin search [关键词]        # 搜索 Plugin 系统市场（远程 + 本地）
/plugin install <名称>        # 安装插件
/plugin uninstall <名称>      # 卸载插件
/plugin info <名称>           # 查看插件详情
/plugin update                # 检查插件更新
```

Plugin 系统默认同时搜索远程 `marketplace.json` 和本地插件市场目录。
本地市场在 `configs/settings.toml` 的 `[plugins]` 表中配置：

```toml
[plugins]
marketplace_local = "../jarvis-plugins"
```

支持两种本地目录结构：
- 扁平布局：`<marketplace_local>/<plugin>/plugin.json`
- 仓库布局：`<marketplace_local>/plugins/<plugin>/plugin.json`（与 `aceFelix/jarvis-plugins` 仓库一致）

### Plugin 通用功能

```bash
/plugin enable <名称>         # 启用被禁用的 Plugin 插件
/plugin disable <名称>        # 禁用 Plugin 插件，不卸载
/plugin create <名称>         # 创建 Plugin 插件脚手架
/plugin validate <路径>       # 校验 plugin.json 合法性
```

**启用/禁用**：禁用的 Plugin 插件 skills 会被移出 `~/.jarvis/skills/`，保留在 `~/.jarvis/plugins/disabled/<名称>/` 中，可快速重新启用。状态持久化到 `~/.jarvis/plugins/disabled.json`。

**插件创建**：`/plugin create my-tool` 生成 `plugin.json` + `skills/` 目录 + `README.md` 脚手架。

**插件校验**：`/plugin validate <路径>` 检查 `plugin.json` 是否符合规范。

## CLI-Anything 外部软件控制

Jarvis 内置 **CLI-Anything harness** 机制，可以把任意第三方软件（如 Blender、Obsidian、GIMP、Godot、WPS 等）包装成 Agent 可调用的工具。

### 安装 harness

在 `~/.jarvis/cli_anything/<软件名>/` 目录下放置：

- `SKILL.md`：描述软件能力、参数、触发场景
- `run.py`：执行入口（接收 `--<参数名>` 和 `--harness-dir`、`--workdir`）

示例：

```
~/.jarvis/cli_anything/
├── blender/
│   ├── SKILL.md
│   └── run.py
└── wps/
    └── SKILL.md       # pip 型 harness 只需 SKILL.md（全局命令已安装）
```

### SKILL.md 示例

```markdown
---
name: Blender
id: blender
description: 通过 CLI 控制 Blender 3D 建模软件
when_to_use: 用户需要创建/修改 3D 模型、渲染场景时
trigger_words: [blender, 3d, 建模, 渲染]
command: python
args:
  - name: operation
    type: string
    enum: [create_mesh, render, export, info]
    required: true
    description: 操作类型
  - name: prompt
    type: string
    required: false
    description: 自然语言描述要执行的操作
examples:
  - "用 Blender 创建一个立方体"
---
```

### 市场命令

Jarvis 支持 **CLI-Anything官方市场**（CLI-Anything GitHub 仓库）和 **jarvis自定义市场**（如 jarvis-harness-market）两个来源：

```text
/cli_anything market              # 查看市场可用 harness（官方 + 自定义）
/cli_anything install blender     # 从官方仓库安装 Blender harness
/cli_anything install wps         # 从自定义市场安装 WPS harness（自动 pip install）
/cli_anything uninstall blender   # 卸载已安装 harness
/cli_anything list                # 列出本地已安装 harness
```

网络不可用时，命令会自动回退到本地 `../CLI-Anything-main` 仓库（如果存在）。

### jarvis自定义 Harness 市场

通过配置 `market_url` / `market_local` 接入自定义市场（如 [jarvis-harness-market](https://github.com/aceFelix/jarvis-harness-market)）：

```toml
# ~/.jarvis/settings.toml
[cli_anything]
market_url = "https://raw.githubusercontent.com/aceFelix/jarvis-harness-market/main"
market_local = "path/to/jarvis-harness-market"   # 本地回退路径
```

自定义市场的 harness 支持两种安装模式：

| 模式 | 说明 | 安装行为 |
|------|------|----------|
| **pip 型**（推荐） | harness 是标准 Python 包，有 `setup.py` + `install_cmd` | 自动 `pip install` + 迁移 SKILL.md |
| **目录型** | harness 是自包含目录，无 `install_cmd` | 整目录复制到 `~/.jarvis/cli_anything/<id>/` |

pip 型 harness 安装后提供全局命令（如 `jarvis-harness-wps`），与官方 CLI-Anything harness 行为一致。

### CLI-Anything 通用功能

```bash
/cli_anything enable <id>         # 启用被禁用的 harness
/cli_anything disable <id>        # 禁用 harness，不卸载
/cli_anything create <id>         # 创建 harness 脚手架
/cli_anything validate <路径>      # 校验 SKILL.md 合法性
```

**启用/禁用**：禁用的 harness 不会被加载，保留文件。状态持久化到 `~/.jarvis/cli_anything/disabled.json`。

### 使用

启动 Jarvis 后，harness 会自动注册为工具 `cli_anything__<id>`。例如：

```
> 用 Blender 创建一个立方体
```

Jarvis 会调用 `cli_anything__blender`，并在执行前询问你确认（默认 ASK 权限）。

### 安全说明

- 所有 harness 工具默认 **ASK** 权限，执行前需要确认。
- 不通过 shell 执行，避免命令注入。
- 支持超时和强制终止（默认 120 秒）。

## GUI 自动化

Jarvis 可以直接控制鼠标、键盘、窗口和屏幕，像人一样操作电脑 GUI。安装 `gui` 依赖组后自动启用：

```bash
pip install "jarvis-agent[gui]"
```

### 基础操作

| 工具 | 能力 |
|---|---|
| **GetScreenSize** | 查询屏幕分辨率 |
| **ScreenShot** | 全屏/局部截图，图片直接回传给模型 |
| **MouseClick** | 在屏幕绝对坐标点击（支持左/右/中键、双击） |
| **MouseDrag** | 从一个坐标拖拽到另一个坐标（文件、滑块、调整大小） |
| **MouseMove** | 移动光标 |
| **MouseScroll** | 滚轮滚动 |
| **TypeText** | 输入文字（ASCII 打字，中文走剪贴板粘贴） |
| **KeyTap** | 按键/组合键（如 `["ctrl","s"]`） |

### 多窗口协调

操作具体应用窗口时，建议先聚焦窗口，再用窗口相对坐标操作：

```text
1. WindowFocus(title="Chrome")      # 激活窗口
2. WindowRect(title="Chrome")       # 获取窗口屏幕绝对坐标
3. WindowClick(title="Chrome", x=100, y=50)  # 在窗口内相对坐标点击
```

这样即使窗口被移动过，`WindowClick` 仍能通过相对坐标准确点击。

### 等待与视觉定位

| 工具 | 能力 |
|---|---|
| **WaitFor** | 等待屏幕/区域出现目标图片，或等待画面发生变化 |
| **VisualClick** | 用模板匹配找图标/按钮并自动点击 |

视觉定位适合按钮/图标位置不固定的场景：传入目标小图，Jarvis 会自动在屏幕上找到匹配位置并点击，避免写死坐标的脆弱性。

### 右键菜单

`MouseClick` 支持 `button=right`。右键弹出菜单后，可配合 `KeyTap` 用方向键选择菜单项并按 Enter 确认。

### 使用原则

1. **先看再动**：操作前先用 `ScreenShot` 看清屏幕，不要盲点坐标。
2. **小步验证**：完成一步后截图确认结果，再执行下一步。
3. **危险操作需确认**：点击、输入、关窗口等会改状态的操作默认需要用户确认（yolo 模式可关闭）。

## 邮件发送

Jarvis 可以通过 `SendEmail` 工具主动给用户发邮件，适用于提醒、摘要、报告转发等场景。

### 配置

在 `~/.jarvis/settings.toml` 中添加 `[email]` 表：

```toml
[email]
enabled = true
smtp_host = "smtp.163.com"
smtp_port = 465
smtp_user = "your_163_email@163.com"
smtp_password = "your_authorization_code"   # 163 邮箱授权码，不是登录密码
sender = "your_163_email@163.com"
default_recipient = "13985465782@136.com"   # 用户未指定收件人时的默认地址
```

### 使用

直接用自然语言告诉 Jarvis：

```text
> 发邮件提醒我今晚8点开会
> 把这份总结发到我的邮箱，主题是今日工作摘要
```

Jarvis 会调用 `SendEmail`，并在发送前询问确认。支持指定收件人、抄送、密送和本地附件。

## 开发服务器

Jarvis 内置 `/server` 命令和 `DevServer` 工具，用于一键启动前端/Node 开发服务器：

```bash
/server                                  # 启动当前目录项目
/server jarvis-website                   # 启动指定目录项目
/server --port 3000                      # 指定端口（被占用时自动递增）
/server --command "pnpm run dev"         # 自定义启动命令
/server jarvis-website --port 3000 --wait 15
```

支持自动识别的项目类型：

| 项目类型 | 检测依据 | 默认命令 |
|---|---|---|
| Vite | `vite.config.*` 或依赖 `vite` | `npm run dev` / `npx vite --port {port}` |
| Next.js | `next.config.*` 或依赖 `next` | `npm run dev` / `npx next dev --port {port}` |
| Nuxt | `nuxt.config.*` 或依赖 `nuxt` | `npm run dev` / `npx nuxt dev --port {port}` |
| Vue CLI | `vue.config.*` 或依赖 `@vue/cli-service` | `npm run dev` / `npx vue-cli-service serve --port {port}` |
| Webpack | `webpack.config.*` 或依赖 `webpack` | `npm run dev` / `npx webpack serve --port {port}` |
| Create React App | 依赖 `react-scripts` | `npm start`（自动注入 `PORT`） |
| Gatsby | `gatsby-config.*` 或依赖 `gatsby` | `npx gatsby develop --port {port}` |

特性：

- **自动检测 package manager**：根据 `pnpm-lock.yaml` / `yarn.lock` 选择 `pnpm` / `yarn` / `npm`
- **端口占用自动递增**：默认端口被占用时自动找下一个可用端口
- **日志重定向**：stdout/stderr 写入 `~/.jarvis/dev_server_logs/<项目名>_<时间戳>.log`
- **URL 提取**：从日志中自动提取 `http://localhost:port` 返回

AI 工具：`DevServer(project_dir=..., port=..., command=...)`
