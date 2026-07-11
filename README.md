<div align="center">
  <pre>
   ██ ▄████▄ █████▄  ██  ██ ██ ▄█████
   ██ ██▄▄██ ██▄▄██▄ ██▄▄██ ██ ▀▀▀▄▄▄
████▀ ██  ██ ██   ██  ▀██▀  ██ █████▀
  </pre>
</div>

# J.A.R.V.I.S

> **J**ust **A** **R**ather **V**ery **I**ntelligent **S**ystem
>
> 「随时为您效劳，先生。」

这是一个为个人电脑打造的 AI Agent —— 能像《钢铁侠》里的贾维斯一样与你对话、帮你操作电脑、常驻后台听你召唤、能听会说。它的工具协议、权限系统、编排模型、MCP 集成、上下文压缩等核心思想有着现代 AI Agent 的最佳实践，实现语言、交互方式和产品形态都围绕「个人电脑助手」设计，把「终端原生、工具驱动、可扩展」的智能助手带到你自己的个人电脑系统中。

## 平台支持

| 功能 | Windows | macOS | Linux |
|---|---|---|---|
| REPL 对话 + 文件/命令工具 | ✅ | ✅ | ✅ |
| LLM Provider（OpenAI / Anthropic / DashScope） | ✅ | ✅ | ✅ |
| MCP 集成 / 会话记忆 / 上下文压缩 | ✅ | ✅ | ✅ |
| Rich 终端 UI + 启动动画 | ✅ | ✅ | ✅ |
| 语音 TTS / STT（`/voice`） | ✅ | ✅ | ✅ |
| 鼠标 / 键盘 / 截屏（pyautogui） | ✅ | ✅¹ | ✅² |
| 摄像头 / 视觉监控 | ✅ | ✅ | ✅ |
| `--daemon` 后台常驻模式 | ✅ | ✅ | ⚠️ 前台运行³ |
| 开机自启 | ✅ Startup | ✅ LaunchAgent | ❌ 手动 systemd |
| 桌面快捷方式 | ✅ .lnk | ✅ .command | ✅ .desktop |
| 全局热键 | ✅ | ❌ | ⚠️ 需 root |

> ¹ macOS 需在「系统设置 → 隐私与安全 → 辅助功能」中授权终端/Python
> ² Linux 鼠标键盘操作需 DISPLAY 环境变量（X11/Wayland 桌面环境）
> ³ Linux 上 `--daemon` 会以前台模式运行（无法后台分离），功能完整

## 安装

### 从 PyPI 安装（推荐）

```bash
# 一键安装全功能（语音 + GUI + daemon + MCP + 浏览器 + 摄像头/视觉）
pip install "jarvis-agent[all]"

# 仅安装核心对话功能
pip install jarvis-agent
```

### 从 GitHub 安装

```bash
# 克隆仓库
git clone https://github.com/yourname/J.A.R.V.I.S.git
cd J.A.R.V.I.S/jarvis

# 安装核心包（开发模式）
pip install -e .

# 开发模式全功能
pip install -e ".[all]"
```

### 用 uv 安装（更快）

[uv](https://docs.astral.sh/uv/) 是 Rust 编写的高性能 Python 包管理器，推荐新用户尝试：

```bash
# 作为全局工具安装
uv tool install "jarvis-agent[all]"

# 之后直接用
jarvis
```

### 安装可选功能

jarvis 将不同能力拆分为可选依赖组，按需安装：

| 依赖组 | 功能 |
|---|---|
| `gui` | 鼠标/键盘/截屏/窗口管理 |
| `browser` | 浏览器自动化（Playwright） |
| `mcp` | MCP 工具集成 |
| `camera` | 摄像头拍照 |
| `vision` | 实时视觉监控 + OCR |
| `voice` | 语音对话（TTS + STT） |
| `daemon` | 后台常驻/托盘/热键/开机自启 |
| `all` | 上面全部 |

```bash
# 一键安装全部功能（推荐）
pip install "jarvis-agent[all]"

# 或按需安装
pip install "jarvis-agent[voice]"                    # 语音对话
pip install "jarvis-agent[gui]"                      # 鼠标/键盘/截屏
pip install "jarvis-agent[daemon]"                   # 后台常驻模式
pip install "jarvis-agent[mcp]"                      # MCP 工具集成
pip install "jarvis-agent[camera,vision]"            # 摄像头/视觉监控
pip install "jarvis-agent[browser]"                  # 浏览器自动化
pip install playwright && playwright install chromium  # 浏览器需要额外安装 Chromium
```

### 平台系统依赖

**macOS**:

```bash
brew install portaudio          # pyaudio 编译依赖（语音功能必需）
# 系统设置 → 隐私与安全 → 辅助功能 → 允许终端/Python（GUI 操作必需）
# 系统设置 → 隐私与安全 → 麦克风 → 允许终端/Python（语音输入必需）
```

**Linux (Ubuntu/Debian)**:

```bash
sudo apt install portaudio19-dev python3-pyaudio  # 语音功能
sudo apt install libgtk-3-dev libnotify-dev        # 系统托盘（pystray）
sudo apt install python3-tk                        # pyautogui 截屏依赖
# 全局热键需要 root 权限: sudo jarvis --daemon
```

**Linux (Fedora/RHEL)**:

```bash
sudo dnf install portaudio-devel gtk3-devel
```

**Windows**: 无需额外系统依赖，直接 `pip install` 即可。

## 快速开始

```bash
# 默认接阿里云 DashScope（qwen3.7-plus，多模态视觉模型）
export DASHSCOPE_API_KEY=sk-xxx
jarvis
```

> Windows PowerShell 用 `$env:DASHSCOPE_API_KEY = "sk-xxx"` 设置环境变量。

默认配置在 `configs/settings.toml`，环境变量 `JARVIS_*` 和 CLI 参数可覆盖。
API Key 识别优先级：`DASHSCOPE_API_KEY` > `ANTHROPIC_API_KEY` > `OPENAI_API_KEY` > `JARVIS_API_KEY`。

### 模型管理（`/models`）

内置 qwen 系列模型开箱即用，也支持添加自定义模型（DeepSeek / Claude / GPT 等）：

- `/models` — 交互式模型列表，↑↓ 选择、Enter 切换、空格管理配置
- `/model <name>` — 快速切换到指定模型
- 内置模型可按空格键修改接口类型 / API Key / 模型类型（多模态 or 纯文本），保存为自定义覆盖配置
- 自定义模型支持编辑和删除，配置持久化到 `~/.jarvis/settings.toml`
- 接口类型支持：**OpenAI 兼容** / **Anthropic 兼容** / **DashScope SDK**（qwen 原生协议，支持 `MultiModalConversation` 和 `Generation` 双端点）

## 目录结构

```
agent/
├── core/           # 核心：Tool 协议、QueryLoop、Orchestrator、上下文压缩、
│                   #   会话记忆、Skill 系统、MCP 客户端、子代理、调度器
├── permissions/    # 五层权限系统：规则、校验、路径守护、命令分类
├── tools/          # 内置工具（30+）：bash / 文件 / glob / grep / todo / ask_user +
│                   #   GUI（鼠标/键盘/屏幕/窗口/浏览器）+ 摄像头 / 视觉监控 +
│                   #   MCP 代理 / 子代理 / 日程 / 系统监控
├── llm/            # LLM 抽象层：base + openai_provider + anthropic_provider + dashscope_provider
├── ui/             # Rich 终端 UI + 启动动画 + prompt_toolkit 补全
├── prompts/        # 系统提示组装
├── config/         # 配置加载（TOML 多源合并 + 环境变量覆盖 + 用户级覆盖）
├── voice/          # 语音引擎：TTS（CosyVoice）+ STT（Qwen3-ASR / Paraformer 双后端）+ /voice 闭环
├── daemon/         # 常驻模式：后台守护 + 全局热键 + 系统托盘 + 开机自启
└── memory/         # 会话 / 记忆持久化
```

## REPL 命令

启动后输入 `/` 自动弹出命令列表（Tab 补全）：

| 命令 | 说明 |
|---|---|
| `/help` `/h` | 查看所有命令帮助 |
| `/exit` `/quit` | 退出贾维斯 |
| `/mode <m>` | 切换权限模式（default / plan / accept_edits / yolo）|
| `/model` | 交互式模型选择 |
| `/model <name>` | 直接切换到指定模型 |
| `/models` | 管理模型列表（添加 / 编辑 / 删除） |
| `/think` | 开关深度思考模式（ReAct 思维链） |
| `/voice` `/talk` | 进入语音对话模式 |
| `/say <text>` | TTS 语音朗读 |
| `/listen` `/mic` | 录音识别成文字 |
| `/save [name]` | 保存当前会话 |
| `/load <name>` `/resume <n>` | 加载已保存会话 |
| `/sessions` | 列出所有已保存会话 |
| `/memory` | 查看长期记忆文件 |
| `/skills` | 列出已加载技能包 |
| `/mcp` | 查看 MCP server 连接状态 |
| `/tools` | 列出所有可用工具 |
| `/reset` `/clear` | 清空对话历史 |
| `/compact` | 手动压缩上下文 |

## 深度思考（ReAct 思维链）

启用后，模型在每次回复前先输出 `reasoning_content`（思考过程），再进行正式回复或工具调用，形成完整的 **Think → Act → Observe** ReAct 循环。

- 思考内容在终端显示为「💭 思考过程」暗色面板，不干扰正式回复输出
- 配置：`configs/settings.toml` 中 `enable_thinking = true` / `thinking_budget = 2000`
- 运行时开关：`/think on` / `/think off`
- 环境变量关闭：`JARVIS_ENABLE_THINKING=0`

## 语音对话（`/voice`）

进入语音对话模式后，形成 **STT → LLM → TTS** 闭环：

- **语音输入**：Qwen3-ASR 流式识别（WebSocket），支持中英文
- **语音输出**：CosyVoice TTS 自然语音合成
- **打断机制**：ESC 打断当前回复，LLM 意图识别退出（说"退下"）
- **思考模式**：语音模式下思考内容显示在终端面板，不进入 TTS 朗读
- **markdown 清洗**：自动过滤代码块 / 表格 / 链接等不适合朗读的内容

## 常驻模式（贾维斯形态）

```bash
jarvis --daemon          # 后台启动，自动进入语音对话
```

**跨平台行为**：

| 平台 | 后台分离方式 | 说明 |
|---|---|---|
| Windows | `pythonw.exe` + `DETACHED_PROCESS` | 无窗口进程，关闭终端不影响 |
| macOS | `start_new_session=True` | 新会话脱离终端，关闭 Terminal.app 不影响 |
| Linux | 不支持后台分离 | 以前台模式运行（功能完整，关闭终端即退出） |

启动后系统托盘出现蓝色同心圆图标。默认自动进入语音对话模式——说「退下」进待机，说「贾维斯」唤醒。

**托盘菜单（右键）：**

| 菜单项 | 行为 |
|---|---|
| 语音对话 | 唤起语音对话模式 |
| 文本对话 | 弹出终端窗口运行完整 REPL（自动恢复上次会话） |
| 退出贾维斯 | 立即终止守护进程 |

> 文本对话弹窗：Windows 用 Git Bash / CMD，macOS 用 Terminal.app，Linux 用 gnome-terminal / xterm。

### 开机自启 / 桌面快捷方式

```bash
python -m agent.daemon.autostart install            # 安装开机自启
python -m agent.daemon.autostart uninstall          # 卸载开机自启
python -m agent.daemon.autostart status             # 查看状态

python -m agent.daemon.autostart desktop            # 创建桌面快捷方式
python -m agent.daemon.autostart desktop-uninstall  # 删除桌面快捷方式
```

| 平台 | 开机自启 | 桌面快捷方式 |
|---|---|---|
| Windows | Startup 文件夹 .lnk | .lnk（指向 VBS 无窗口启动） |
| macOS | LaunchAgent plist（`launchctl load`） | .command（Terminal.app 打开） |
| Linux | 不支持（提示手动 systemd） | .desktop 文件 |

### 其他启动方式

| 方式 | 说明 |
|---|---|
| `jarvis` | 前台 REPL 模式（带启动动画） |
| `jarvis --with-tray` | 前台 REPL + 托盘图标 |
| `jarvis --daemon` | 后台常驻模式（Windows/macOS 后台分离，Linux 前台运行） |

### 会话恢复

异常退出时下次启动自动提示恢复上次会话（对话历史 + 工作目录）。文本对话窗口也会自动恢复。

## 可选依赖

GUI 工具（鼠标 / 键盘 / 屏幕 / 窗口）未安装时自动跳过，不影响基础功能：

```bash
pip install -e ".[gui]"                              # 鼠标/键盘/截屏
pip install -e ".[browser]" && playwright install chromium  # 浏览器自动化
```

## 开发路线

- [x] 阶段 1：最小可用 Agent（对话 + 文件 + 命令 + 五层权限）
- [x] 阶段 2：电脑操作能力（GUI + 多模态视觉 + 浏览器自动化 + 摄像头拍照）
- [x] 阶段 3：实时语音（TTS + STT + /voice 闭环 + 打断）
- [x] 阶段 4：记忆与生态（会话持久化 / 长期记忆 / MCP 接入 / 上下文压缩 / Skill 系统）
- [x] 阶段 5：贾维斯形态（daemon 常驻 + 全局热键 + 系统托盘 + 开机自启 + 子代理 + 主动感知 + 视觉监控）
- [x] 阶段 6：跨平台适配（Windows / macOS / Linux）
