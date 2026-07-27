<div align="center">
  <img src="assets/jarvis-reactor-header.svg" alt="J.A.R.V.I.S." width="100%"/>
</div>

# J.A.R.V.I.S.

<div align="center">

<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
<a href="https://www.python.org/downloads/release/python-3130/"><img src="https://img.shields.io/badge/python-3.13-blue.svg" alt="Python 3.13" /></a>
<a href="https://pypi.org/project/jarvis-agent/"><img src="https://img.shields.io/pypi/v/jarvis-agent.svg" alt="PyPI version" /></a>
<a href="https://www.deepseek.com"><img src="https://img.shields.io/badge/DeepSeek-API-4D6BFE.svg?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xMiAyTDIgN2wxMCA1IDEwLTV6TTIgMTdsMTAgNSAxMC01TTIgMTJsMTAgNSAxMC01Ii8+PC9zdmc+" alt="DeepSeek" /></a>
<a href="https://bailian.console.aliyun.com"><img src="https://img.shields.io/badge/DashScope-%E7%99%BE%E7%82%BC-FF6A00.svg?logo=alibabacloud&logoColor=white" alt="DashScope" /></a>
<a href="#%E5%85%8D%E8%B4%A3%E5%A3%B0%E6%98%8E"><img src="https://img.shields.io/badge/status-Beta%20%E5%BC%80%E5%8F%91%E9%AA%8C%E8%AF%81%E4%B8%AD-yellow.svg" alt="Status" /></a>
<a href="#%E5%85%8D%E8%B4%A3%E5%A3%B0%E6%98%8E"><img src="https://img.shields.io/badge/%F0%9F%9B%A0%EF%B8%8F-Personal%20Project-9B59B6.svg" alt="Personal" /></a>
<a href="#%E5%BC%80%E5%8F%91%E5%8F%82%E8%80%83"><img src="https://img.shields.io/badge/%E2%9D%A4%EF%B8%8F-Inspired%20by%20Iron%20Man-E23636.svg" alt="Iron Man" /></a>
<a href="https://github.com/aceFelix/jarvis"><img src="https://img.shields.io/github/stars/aceFelix/jarvis?style=social" alt="GitHub stars" /></a>

</div>


> **J**ust **A** **R**ather **V**ery **I**ntelligent **S**ystem
>
> 「随时为您效劳，先生。」

一个为个人电脑打造的 AI Agent —— 致敬《钢铁侠》里的贾维斯。与你对话、帮你操作电脑、常驻后台听你召唤、能听会说。它把「终端原生、工具驱动、可扩展」的智能助手带到你自己的个人电脑系统中。

---

## 目录

- [平台支持](#平台支持)
- [安装](#安装)
- [快速开始](#快速开始)
- [配置指南](#配置指南)
- [核心概念](#核心概念)
  - [五层权限系统](#五层权限系统)
  - [上下文压缩](#上下文压缩)
  - [记忆系统](#记忆系统)
  - [Skill 技能包](#skill-技能包)
  - [MCP 集成](#mcp-集成)
- [REPL 命令参考](#repl-命令参考)
- [模型管理](#模型管理)
- [深度思考模式](#深度思考模式)
- [语音功能](#语音功能)
  - [语音对话 `/voice`](#语音对话-voice)
  - [实时双工 `/talk`](#实时双工-talk)
  - [TTS 朗读 `/say`](#tts-朗读-say)
  - [录音识别 `/listen`](#录音识别-listen)
- [图片输入](#图片输入)
- [GUI 自动化](#gui-自动化)
- [常驻模式（贾维斯形态）](#常驻模式贾维斯形态)
- [多 Agent 协作](#多-agent-协作)
- [插件系统](#插件系统)
- [CLI-Anything 外部软件控制](#cli-anything-外部软件控制)
- [邮件发送](#邮件发送)
- [开发服务器](#开发服务器)
- [工具错误自愈](#工具错误自愈)
- [目录结构](#目录结构)
- [开发路线](#开发路线)
- [许可证](#许可证)

---

## 平台支持

| 功能 | Windows | macOS | Linux |
|---|---|---|---|
| REPL 对话 + 文件/命令工具 | ✅ | ✅ | ✅ |
| LLM Provider（OpenAI / Anthropic / DashScope） | ✅ | ✅ | ✅ |
| MCP 集成 / 会话记忆 / 上下文压缩 | ✅ | ✅ | ✅ |
| Rich 终端 UI + 启动动画 | ✅ | ✅ | ✅ |
| 语音对话 `/voice`（STT + TTS） | ✅ | ✅ | ✅ |
| 实时双工语音 `/talk`（全双工） | ✅ | ✅ | ✅ |
| 实时聊天窗口（方舟反应炉动画） | ✅ | ✅ | ✅ |
| 鼠标 / 键盘 / 截屏（pyautogui） | ✅ | ✅¹ | ✅² |
| 摄像头 / 视觉监控 | ✅ | ✅ | ✅ |
| `--daemon` 后台常驻模式 | ✅ | ✅ | ⚠️ 前台运行³ |
| 开机自启 | ✅ Startup | ✅ LaunchAgent | ❌ 手动 systemd |
| 桌面快捷方式 | ✅ .lnk | ✅ .command | ✅ .desktop |
| 全局热键 | ✅ | ❌ | ⚠️ 需 root |

> ¹ macOS 需在「系统设置 → 隐私与安全 → 辅助功能」中授权终端/Python
> ² Linux 鼠标键盘操作需 DISPLAY 环境变量（X11/Wayland 桌面环境）
> ³ Linux 上 `--daemon` 会以前台模式运行（无法后台分离），功能完整

> ⚠️ **重要提示**：本项目在 **Windows** 上完成全部功能开发与实机验证。macOS 和 Linux 仅做了代码层面的适配，**未经过完整实机测试**，可能存在未发现的兼容性问题。建议优先在 Windows 上使用 J.A.R.V.I.S 以获得最佳体验。

---

## 安装

### 从 PyPI 安装（推荐）

```bash
# 一键安装全功能（语音 + GUI + daemon + MCP + 浏览器 + 摄像头/视觉 + 实时聊天窗口）
pip install "jarvis-agent[all]"

# 仅安装核心对话功能
pip install jarvis-agent
```

### 从 GitHub 安装

```bash
# 克隆仓库
git clone https://github.com/aceFelix/jarvis.git
cd jarvis

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

### 用 npm 安装

如果你习惯 Node.js 生态，也可以通过 npm 一键安装（需要系统已安装 Python 3.11+）：

```bash
npm install -g jarvis-agent

# 之后直接用
jarvis
```

> **环境要求**：
> - **Node.js ≥ 18**（推荐 **Node 20 LTS** 或更高版本，Node 14/16 已停止维护）
> - **Python 3.11+** 并加入 PATH
>
> npm 包会自动检测 Python 环境并通过 pip 安装 `jarvis-agent[all]`。

### 安装可选功能

jarvis 将不同能力拆分为可选依赖组，按需安装：

| 依赖组 | 功能 | 安装命令 |
|---|---|---|
| `gui` | 鼠标/键盘/截屏/窗口管理 | `pip install "jarvis-agent[gui]"` |
| `browser` | 浏览器自动化（Playwright） | `pip install "jarvis-agent[browser]"` |
| `mcp` | MCP 工具集成 | `pip install "jarvis-agent[mcp]"` |
| `camera` | 摄像头拍照 | `pip install "jarvis-agent[camera]"` |
| `vision` | 实时视觉监控 + OCR | `pip install "jarvis-agent[vision]"` |
| `voice` | 语音对话 `/voice` + 实时双工 `/talk`（STT+TTS+全双工） | `pip install "jarvis-agent[voice]"` |
| `daemon` | 后台常驻/托盘/热键/开机自启 | `pip install "jarvis-agent[daemon]"` |
| `realtime_ui` | 实时聊天独立窗口（方舟反应炉动画，`/talk` 可视化） | `pip install "jarvis-agent[realtime_ui]"` |
| `all` | 上面全部 | `pip install "jarvis-agent[all]"` |

### 平台系统依赖

**Windows**: 无需额外系统依赖，直接 `pip install` 即可。

> 实时聊天窗口需要 Edge WebView2 Runtime（Win10/11 通常已预装），如未安装请从 [Microsoft 官网](https://developer.microsoft.com/microsoft-edge/webview2/) 下载。

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
```

**Linux (Fedora/RHEL)**:

```bash
sudo dnf install portaudio-devel gtk3-devel
```

---

## 快速开始

```bash
# 默认接阿里云 DashScope（qwen3.7-plus，多模态视觉模型）
export DASHSCOPE_API_KEY=sk-xxx
jarvis
```

> Windows PowerShell 用 `$env:DASHSCOPE_API_KEY = "sk-xxx"` 设置环境变量。

默认配置在 `configs/settings.toml`，环境变量 `JARVIS_*` 和 CLI 参数可覆盖。
API Key 识别优先级：`DASHSCOPE_API_KEY` > `ANTHROPIC_API_KEY` > `OPENAI_API_KEY` > `JARVIS_API_KEY`。

启动后进入 REPL 终端界面，输入问题即可与 AI 对话：
- 直接输入自然语言，AI 会自动调用工具完成任务
- 输入 `/` 弹出命令列表，Tab 键自动补全
- `Shift+Enter` 换行（Windows 终端自动转换）
- `Ctrl+C` 中断当前回答

> **桌面快捷方式**：安装后不会自动创建。如需桌面图标，运行：
> ```bash
> python -m agent.daemon.autostart desktop
> ```

---

## 配置指南

Jarvis 使用三层配置合并：

1. **项目默认配置** — `configs/settings.toml`（随项目分发）
2. **用户级覆盖** — `~/.jarvis/settings.toml`（自动创建，持久化个人设置）
3. **环境变量覆盖** — `JARVIS_*` 前缀的环境变量（优先级最高）

### 核心配置项

```toml
# ---- LLM ----
provider = "dashscope"          # 模型提供商
api_format = "openai"           # 协议格式（openai / anthropic）
model = "qwen3.7-plus"          # 默认模型（多模态视觉）
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
max_tokens = 20480              # 单次输出最大 Token

# ---- 运行时 ----
workdir = "E:\\J.A.R.V.I.S_Work" # 默认工作目录
permission_mode = "yolo"         # 权限模式（default / plan / accept_edits / yolo）
max_iterations = 50              # 单轮最大工具调用次数

# ---- 语音 ----
[tts]
model = "cosyvoice-v3-flash"     # TTS 模型（v3-flash/v3-plus/v3.5-plus）
voice = "longanlang_v3"          # 音色
volume = 50                      # 音量 0-100
speech_rate = 1.0                # 语速 0.5-2.0

[stt]
# 三后端自动适配（根据 model 名）：
#   qwen3-asr-*        → QwenASR（OmniRealtimeConversation，服务端 VAD，质量最高）
#   paraformer-*       → ParaformerSTT（Recognition WebSocket，客户端 VAD，轻量快）
#   fun-asr-realtime   → ParaformerSTT（同为 Recognition 实时识别后端）
#   fun-asr-flash-*    → FunASRFlashSTT（HTTP POST 文件上传，非实时，/voice 体验差）
model = "qwen3-asr-flash-realtime"
max_seconds = 15                  # 单次录音最长秒数
silence_seconds = 1.5             # 静音检测秒数

# ---- 实时双工语音 ----
[realtime_talk]
model = "qwen-audio-3.0-realtime-flash"  # DashScope 实时语音模型
voice = "longanqian"                      # 音色
auto_start = false                        # daemon 启动时自动进入

# ---- 上下文压缩 ----
[context]
compaction = true
compaction_threshold = 8000       # Token 阈值（超此值触发压缩）
keep_recent_messages = 6          # 压缩时保留最近 N 条消息

# ---- 常驻模式 ----
[daemon]
hotkey = "ctrl+shift+j"           # 全局热键
tray = true                       # 系统托盘图标
```

> 完整配置项参见 `configs/settings.toml`。

---

## 核心概念

### 五层权限系统

Jarvis 拥有多层安全防护，确保 AI 不会越权操作你的电脑：

| 层级 | 说明 |
|---|---|
| **L1 硬阻断** | `.ssh`/`.aws`/`.gnupg` 等敏感目录永久拒绝访问；`rm -rf /` 等危险命令永久拦截 |
| **L2 路径守护** | 限制 AI 的文件操作范围，防止读写关键系统目录 |
| **L3 命令分类** | 将命令分为安全/危险/敏感三级，危险命令需确认 |
| **L4 权限模式** | `default` 逐次确认 / `plan` 只读规划 / `accept_edits` 编辑自动通过 / `yolo` 全自动 |
| **L5 用户确认** | 关键操作（删除文件、执行脚本）弹窗确认 |

切换权限模式：`/mode yolo`

### 上下文压缩

当对话历史 Tokens 超过阈值（默认 8000）时，自动将旧消息摘要压缩，保留最近 N 条原始消息：

- **水位策略**：≥30% 保留最近 2 条，≥80% 保留最近 6 条
- **图片驱逐**：旧图片替换为文字占位符释放 Token
- **工具结果折叠**：只保留最近 4 个工具结果
- **容错重试**：遇到 Context Too Long 错误自动压缩后重试
- 手动触发：`/compact`

### 记忆系统

Jarvis 支持多层记忆持久化：

- **会话记忆**：自动保存/恢复对话历史。`/save` `/load` `/sessions` 管理
- **长期记忆**：`~/.jarvis/MEMORY.md`（用户级）+ `<workdir>/.jarvis/MEMORY.md`（项目级），启动时注入系统提示
- **自动恢复**：异常退出后下次启动自动提示恢复

### Skill 技能包

通过 Skill 文件为 AI 注入专业知识和工作流程：

```
~/.jarvis/skills/<name>/SKILL.md        # 用户级技能包
<workdir>/.jarvis/skills/<name>/SKILL.md # 项目级技能包
```

SKILL.md 包含：
- **Frontmatter**：name / description / when_to_use / trigger_words
- **正文**：Markdown 格式的专业知识指令

查看已加载技能：`/skills`

### MCP 集成

支持 [Model Context Protocol](https://modelcontextprotocol.io/) 接入外部工具：

- 配置文件：`~/.jarvis/mcp.json`
- 工具命名：`mcp__<server>__<tool>` 格式注册
- 默认 ASK 权限（外部进程），yolo 模式可放宽
- 查看状态：`/mcp`

---

## REPL 命令参考

启动后输入 `/` 弹出命令列表，Tab 键自动补全：

### 对话控制

| 命令 | 说明 |
|---|---|
| `/help` `/h` | 查看所有命令帮助 |
| `/exit` `/quit` | 退出贾维斯 |
| `/reset` `/clear` | 清空对话历史，重新开始 |
| `/compact` | 手动压缩上下文（摘要旧消息节省 Token） |

### 模型管理

| 命令 | 说明 |
|---|---|
| `/model <前缀>` | 前缀匹配切换模型（支持模糊输入） |
| `/models` | 交互式模型管理（↑↓选择、Enter切换、空格编辑配置） |
| `/think` | 开关深度思考模式（`/think on` / `/think off`） |

### 权限控制

| 命令 | 说明 |
|---|---|
| `/mode <模式>` | 切换权限模式（default / plan / accept_edits / yolo） |
| `/tools` | 列出所有可用工具 |

### 会话管理

| 命令 | 说明 |
|---|---|
| `/save [名称]` | 保存当前会话 |
| `/load <前缀>` | 前缀匹配加载已保存会话 |
| `/sessions` `/loads` | 列出并交互选择已保存会话 |

### 记忆与知识

| 命令 | 说明 |
|---|---|
| `/memory` | 查看长期记忆文件内容 |
| `/skills` | 列出已加载的技能包 |

### 语音功能

| 命令 | 说明 |
|---|---|
| `/voice` | 进入语音对话模式（连续 STT→LLM→TTS 循环） |
| `/talk` | 进入实时双工语音对话（全双工，说话即可打断） |
| `/say <文本>` | TTS 朗读指定文字 |
| `/listen` `/mic` | 录音并识别为文字 |

### 图片输入

| 命令 | 说明 |
|---|---|
| `/image <路径>` `/img <路径>` | 添加本地图片到待发送列表 |
| `/paste` `/p` | 添加剪贴板图片到待发送列表 |

> 图片在下次发送消息时自动附带。支持格式：PNG / JPG / WEBP / BMP。自动缩放到最长边 1280px。

### 多 Agent 与插件

| 命令 | 说明 |
|---|---|
| `/agents` | 查看多 Agent 团队状态与成员 |
| `/tasks` | 查看共享任务列表进度 |
| `/plan` | 切换规划模式（进入/退出只读规划） |
| `/plugin` | 列出已安装插件（Plugin 系统） |
| `/plugin search [关键词]` | 搜索 Plugin 系统市场 |
| `/plugin install <名称>` | 安装 Plugin 系统的插件 |
| `/plugin uninstall <名称>` | 卸载 Plugin 系统的插件 |
| `/plugin info <名称>` | 查看 Plugin 插件详情 |
| `/plugin update` | 检查 Plugin 插件更新 |
| `/plugin enable <名称>` | 启用被禁用的 Plugin 插件 |
| `/plugin disable <名称>` | 禁用 Plugin 插件，不卸载 |
| `/plugin create <名称>` | 创建 Plugin 插件脚手架 |
| `/plugin validate <路径>` | 校验 plugin.json 合法性 |
| `/cli_anything` | 列出已安装 CLI-Anything harness |
| `/cli_anything market` | 列出市场可用 harness |
| `/cli_anything install <id>` | 安装指定 harness |
| `/cli_anything uninstall <id>` | 卸载指定 harness |
| `/cli_anything enable <id>` | 启用被禁用的 harness |
| `/cli_anything disable <id>` | 禁用 harness，不卸载 |
| `/cli_anything create <id>` | 创建 harness 脚手架 |
| `/cli_anything validate <路径>` | 校验 SKILL.md 合法性 |

### MCP 工具

| 命令 | 说明 |
|---|---|
| `/mcp` | 查看 MCP server 连接状态与工具列表 |

### 系统与诊断

| 命令 | 说明 |
|---|---|
| `/doctor` | 查看自愈统计与系统诊断 |
| `/server [目录]` | 一键启动前端开发服务器 |
| `/connect-phone` | 跨设备协同（手机扫码连接当前会话） |
| `/connect-wechat` | 微信扫码连接 JARVIS（通过 ClawBot 在微信中对话） |
| `/disconnect-wechat` | 断开微信 ClawBot 连接 |
| `/verbose` | 开关详细输出（token 统计、缓存命中等） |

---

## 模型管理

### 内置模型

开箱即用，接入阿里云 DashScope：

- `qwen3.7-plus` — 通义千问 3.7 Plus（默认，多模态视觉）
- `qwen3.6-plus` — 通义千问 3.6 Plus
- `qwen3.6-flash` — 通义千问 3.6 Flash（快速响应）
- `qwen3.5-plus` — 通义千问 3.5 Plus
- `qwen3.5-flash` — 通义千问 3.5 Flash（快速响应）

### 添加自定义模型

通过 `/models` 命令交互式添加自定义模型，支持三种接口类型：

| 接口类型 | 适用模型 | 说明 |
|---|---|---|
| **OpenAI 兼容** | DeepSeek / GPT-4o / 各类兼容服务 | 标准 OpenAI API 格式 |
| **Anthropic 兼容** | Claude 系列 | Anthropic Messages API 格式 |
| **DashScope SDK** | qwen 系列原生协议 | 支持 MultiModalConversation 和 Generation 双端点 |

配置会自动保存到 `~/.jarvis/settings.toml` 的 `[llm.custom_models]` 中，重启后保持。

### 自定义模型配置示例

```toml
[llm.custom_models."deepseek-v4"]
api_format = "openai"
base_url = "https://api.deepseek.com/v1"
api_key = "sk-your-deepseek-key"
model_type = "text"              # "text" 纯文本 / "vision" 多模态
```

---

## 深度思考模式

启用后，模型在每次回复前先输出 `reasoning_content`（思考过程），形成完整的 **Think → Act → Observe** ReAct 循环。

- **视觉效果**：思考内容在终端显示为暗色面板「💭 思考过程」
- **运行中开关**：`/think on` / `/think off`（无需重启）
- **配置项**：
  ```toml
  enable_thinking = true
  thinking_budget = 800  # 思考过程 Token 上限
  ```
- **环境变量**：`JARVIS_ENABLE_THINKING=0` 关闭
- **模型适配**：
  - Qwen 系列：通过 `enable_thinking` 参数控制
  - DeepSeek 系列：通过 `thinking.type` 参数控制，支持 `reasoning_effort` 可调
  - `/think off` 时自动过滤 `reasoning_content`，净化输出

---

## 语音功能

Jarvis 提供两套独立的语音系统：

| 模式 | 技术路线 | 特点 |
|---|---|---|
| **`/voice` 语音对话** | STT → LLM → TTS 管线 | 识别→思考→朗读，逐轮对话 |
| **`/talk` 实时聊天** | 全双工 WebSocket 直连 | 边说边听，AI 说话时可打断 |

> 两套系统独立运行，但共用麦克风硬件。同时开启可能导致 PyAudio 设备冲突。

### 语音对话 `/voice`

进入语音对话模式后，形成 **听 → 想 → 说** 闭环：

```
🎤 聆听 → STT 识别 → LLM 思考回答 → TTS 朗读 → 🎤 聆听 → ...
```

- **语音输入**：三种 STT 后端可选，修改 `settings.toml` 中 `[stt].model` 切换：

  | 配置 model | 后端类 | 协议 | 特点 |
  |---|---|---|---|
  | `qwen3-asr-*` | **QwenASR** | WebSocket（OmniRealtimeConversation） | 服务端 VAD，质量最高，中英混合强 |
  | `paraformer-*` | **ParaformerSTT** | WebSocket（Recognition） | 客户端 VAD，轻量快速 |
  | `fun-asr-realtime` | **ParaformerSTT** | WebSocket（Recognition） | 实时识别，与 paraformer 同后端 |
  | `fun-asr-flash-*` | **FunASRFlashSTT** | HTTP POST（文件上传） | 非实时，/voice 循环体验差，不推荐 |

- **语音输出**：两种 TTS 模式
  - **CosyVoiceTTS**：整段合成播放（`cosyvoice-v3-flash` / `v3-plus` / `v3.5-plus`）
  - **StreamTTSPlayer**：WebSocket 流式合成，LLM 逐句输出 → 即时合成播放，首句延迟 ~500ms
  - 默认音色 `longanlang_v3`
- **打断机制**：ESC 键打断当前 AI 播报，或说"退下"退出语音模式
- **思考隔离**：思考过程只显示在终端面板，不进入 TTS
- **内容清洗**：自动过滤代码块、表格、链接等不适合朗读的内容

### 实时双工 `/talk`

基于 DashScope 实时语音 WebSocket 服务（`qwen-audio-3.0-realtime-flash`）：

- **全双工通信**：麦克风音频流实时送入模型，同时接收 AI 语音输出
- **smart_turn 轮次检测**：融合声学感知与语义理解判断说话边界，无意义附和声不会打断对话
- **AEC 回声消除**：基于 WebRTC AEC3，消除扬声器回声，外放不戴耳机也不会自言自语，同时保留开口打断能力
- **Function Calling**：模型可自主调用工具获取实时信息。内置时间查询工具，并自动接入 ToolRegistry 全部工具（文件读写、Bash、Glob、Grep、WebSearch、SendEmail 等）。模型根据 instructions 自主判断高风险操作，先用语音询问用户确认后再执行
- **独立窗口 UI**：安装 `realtime_ui` 后，弹出专用对话窗口
  - 黑色无边框设计，窗口自动最大化
  - **方舟反应炉粒子动画**：背景实时波动，随语音音量改变
  - AI 说话时反应炉核心变色发光，脉冲波纹扩散
  - 对话气泡实时显示用户和 AI 的语音转录文本
- **终端模式**：未安装 `realtime_ui` 时在终端中运行，同样支持打断
- 退出方式：ESC 键或说"退下"

> **AEC 依赖**：实时聊天回声消除依赖 `aec-audio-processing`（WebRTC AEC3 Python 绑定）和 `numpy`，已包含在 `[voice]` 可选依赖组中。未安装时自动降级为仅 smart_turn 语义防回声模式。

### TTS 朗读 `/say`

```bash
/say 你好，我是贾维斯
```

将文字转为语音朗读。使用 DashScope CosyVoice 引擎。

### 录音识别 `/listen`

```bash
/listen      # 录音并输出识别文本
/mic         # 别名
```

---

## 图片输入

Jarvis 支持在对话中附带图片（需要多模态视觉模型，如 `qwen3.7-plus`）：

```bash
/image C:\Users\me\photo.png   # 添加本地图片
/img C:\Users\me\photo.png     # 别名
/paste                          # 添加剪贴板中的图片
/p                              # 别名
```

- 图片加入待发送列表，下次发送消息时自动附带
- 支持 PNG / JPG / WEBP / BMP 格式
- 自动缩放到最长边 1280px，JPEG 质量 85
- 剪贴板图片会自动检测并去重（MD5 判断）

---

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

---

## 常驻模式（贾维斯形态）

```bash
jarvis --daemon          # 后台启动
```

### 跨平台行为

| 平台 | 后台分离方式 | 说明 |
|---|---|---|
| Windows | `pythonw.exe` + `DETACHED_PROCESS` | 无窗口进程，关闭终端不影响 |
| macOS | `start_new_session=True` | 新会话脱离终端 |
| Linux | 不支持后台分离 | 以前台模式运行 |

启动后系统托盘出现蓝色同心圆图标。

### 托盘菜单（右键）

| 菜单项 | 行为 |
|---|---|
| **语音对话** | 唤起语音对话模式 |
| **文本对话** | 弹出终端运行完整 REPL（自动恢复上次会话） |
| **实时聊天** | 开关实时双工语音对话（勾选=开启），弹出方舟反应炉窗口 |
| **退出贾维斯** | 立即终止守护进程 |

> 实时聊天窗口：daemon 生命周期内保持单例，重复点击不会新建窗口，仅唤起已有窗口。窗口随 daemon 退出而销毁。

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

### 实时双工配置

在 `~/.jarvis/settings.toml` 中配置：

```toml
[realtime_talk]
api_key = "sk-xxx"              # DashScope API Key（实时语音必需）
model = "qwen-audio-3.0-realtime-flash"
voice = "longanqian"
auto_start = false              # daemon 启动时是否自动进入实时聊天
```

> `api_key` 用于 `/talk` 实时双工语音鉴权。不配置时回退到 `DASHSCOPE_API_KEY` 环境变量。
>
> daemon 启动时自动进入实时聊天模式。托盘菜单可随时开关。

### 更快的热键响应（P1-2）

daemon 模式默认使用 Windows 原生 `RegisterHotKey` 监听全局热键，比键盘钩子响应更快。可在 `~/.jarvis/settings.toml` 中微调：

```toml
[daemon]
hotkey = "ctrl+shift+j"        # 全局热键
hotkey_native = true            # Windows 优先使用 RegisterHotKey（更快）
hotkey_debounce_ms = 200        # 去抖毫秒，防止一次按下触发多次
```

如果希望热键按下后文本窗口能立即输入，可开启 warm 预启动（常驻一个隐藏终端进程）：

```toml
[daemon]
text_terminal_warm = true       # 预启动隐藏文本终端，唤起到可输入 < 500ms（但常驻内存）
```

文本终端也可以用 `--quick` 快速启动，跳过开机动画、MCP、LSP 等可选初始化，首次调用相关命令时再懒加载：

```bash
jarvis --quick                # REPL 快速启动
jarvis --daemon --quick       # daemon 快速启动（弹出终端自动带 --quick）
```

### 系统资源监控

daemon 模式下自动监控 CPU / 内存 / 磁盘：

```toml
[monitor]
enabled = true
cpu_threshold = 85.0       # CPU 超 85% 持续 30s 告警
memory_threshold = 90.0    # 内存超 90% 告警
disk_threshold = 10.0      # 磁盘剩余低于 10% 告警
check_interval = 10        # 检查间隔（秒）
alert_cooldown = 600       # 同类告警冷却（10 分钟）
# P2-3 增强
disk_trend_days = 7        # 磁盘趋势预测：预测几天后将满
high_cpu_duration = 600    # 异常进程：CPU > 50% 持续多少秒通知
work_break_interval = 7200 # 连续工作 2 小时提醒休息
```

### 主动提醒系统（P2-3）

daemon 模式下，贾维斯具备主动感知能力，不需要用户提问就能主动服务：

**每日简报**：每天 08:30 自动播报今日概览（待触发提醒、节假日、系统状态、截止日期、日历事件）。

**截止日期追踪**：对贾维斯说“下周五之前交项目报告”，自动注册截止日期，分级提醒（提前 7/3/1/0 天 + 逾期每天）。

**提醒升级**：提醒触发后未确认会自动重复通知（5→10→20 分钟，最多 3 次），说“知道了”即可确认。

**日历集成**（可选）：读取 Outlook/ICS 日历事件，在简报中展示 + 提前 30 分钟提醒。

```toml
[daemon]
briefing_enabled = true
briefing_time = "08:30"    # 每日简报时间

[deadline]
enabled = true
check_time = "09:00"       # 每日检查截止日期的时间

[calendar]
enabled = false            # 日历集成（需配置 Outlook 或 ICS）
backend = "auto"           # auto / outlook / ics
ics_path = ""              # 本地 .ics 文件路径
ics_url = ""               # 远程 .ics 订阅 URL
remind_minutes_before = 30
```

Agent 工具：

| 工具 | 说明 |
|------|------|
| `ScheduleReminder` | 安排定时提醒（“明天 3 点提醒我开会”） |
| `AddDeadline` | 注册截止日期（“下周五之前交报告”） |
| `ListDeadlines` | 查看活跃截止日期 |
| `CompleteDeadline` | 标记截止日期完成 |
| `AcknowledgeReminder` | 确认提醒（停止升级重复通知） |

### 跨设备协同（P3-1）

在终端输入 `/connect-phone`，电脑端会显示一个二维码，手机扫码即可连接当前 JARVIS 会话，出门在外也能远程操控电脑。

```toml
[bridge]
http_port = 8765               # PWA 页面端口
ws_port = 8766                 # WebSocket 通信端口
token = ""                     # 认证 token，留空自动生成
```

**使用方式**：
1. 在 JARVIS 终端输入 `/connect-phone`
2. 终端显示二维码和访问地址
3. 手机和电脑连同一局域网 Wi-Fi
4. 手机扫码或手动访问 URL 开始对话

终端效果示例：

```
🌐 跨设备协同已启动
   手机访问: http://192.168.1.100:8765/?token=a1b2c3d4e5f6g7h8
   手机和电脑需在同一局域网（Wi-Fi）

████  ████  █  ████  ████
█  █  █  █  █  █     █  █
...

提示: 手机扫码或手动访问上方 URL 即可开始对话
      输入 /connect-phone 可重新生成二维码
```

**核心特性**：
- **共享会话**：手机端与电脑端共享同一对话历史，手机上发的消息会同步到电脑终端
- **权限隔离**：手机端默认 PLAN 模式（只读），写操作需手机端确认
- **流式输出**：JARVIS 回复实时推送到手机端，支持 Markdown 渲染
- **工具调用可视化**：手机端可查看工具调用过程和结果
- **Token 认证**：每次 `/connect-phone` 自动生成 token，防止未授权访问
- **中断支持**：手机端可随时中断 JARVIS 的回复
- **终端会话生命周期**：随当前 JARVIS 终端退出而关闭

> 外网访问需配合内网穿透（如 frp、Cloudflare Tunnel）。

### 微信 ClawBot 接入

在终端输入 `/connect-wechat`，扫码连接微信 ClawBot，之后在微信中发消息即可与 JARVIS 对话（含完整工具调用能力）。

**使用方式**：
1. 在 JARVIS 终端输入 `/connect-wechat`
2. 终端显示二维码（或扫码链接）
3. 手机微信扫码并确认连接
4. 在微信中找到 ClawBot 发消息即可对话

**核心特性**：
- **官方接口**：基于腾讯 iLink Bot API，安全合规不封号
- **完整能力**：微信端可使用 JARVIS 全部工具（文件、命令、搜索等）
- **共享会话**：微信对话与电脑终端共享同一对话历史
- **24h 续期**：连接有效期 24 小时，到期前终端提醒重新扫码
- **长消息分段**：超过 2000 字自动分段发送

**依赖**：`pip install "jarvis-agent[wechat]"`（aiohttp + qrcode）

> 需微信版本 ≥ 8.0.70，设置 → 插件中可看到 ClawBot。

### 安全沙箱执行（P3-8）

高风险操作在隔离环境中运行，防止误操作破坏系统。跨平台支持：

| 平台 | 沙箱机制 | 说明 |
|------|------|------|
| Windows | Job Object | 内存/进程数限制，KILL_ON_JOB_CLOSE 终止进程树 |
| Linux | resource.setrlimit | RLIMIT_AS/CPU/NPROC 资源限制 |
| macOS | sandbox-exec + rlimit | Apple Sandbox 命令包装 + 资源限制 |

**四级风险分类**：

| 风险等级 | 策略 | 示例命令 |
|------|------|------|
| LOW | 直接放行 | ls, cat, git status |
| MEDIUM | 沙箱开启时自动放行 | npm install, git commit, python script.py |
| HIGH | 强制沙箱 + 文件快照 | rm, del, git push --force |
| CRITICAL | 沙箱 + 快照 + 用户确认 | rm -rf, sudo, format, reg delete |

**文件快照保护**：高风险操作前自动备份目标文件，操作失败可回滚（`~/.jarvis/sandbox_snapshots/`）。

**审计日志**：所有沙箱操作记录到 `~/.jarvis/sandbox_audit.jsonl`，支持统计查询。

```toml
[sandbox]
enabled = false              # 总开关
max_memory_mb = 512          # 沙箱内最大内存（MB）
max_cpu_seconds = 60         # 最大 CPU 时间（秒）
max_processes = 10           # 最大子进程数（防 fork bomb）
timeout = 120                # 命令总超时（秒）
block_network = false        # 是否阻断网络
auto_allow_medium = true     # 沙箱开启时自动放行中等风险
audit = true                 # 记录审计日志
max_snapshots = 20           # 文件快照最大保留数
excluded_commands = []       # 不走沙箱的命令（如 ["docker", "wsl"]）
```

---

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

---

## 工具错误自愈

Jarvis 内置 **Tool Self-Healing**，工具调用失败时不会立刻把错误抛给 LLM，而是先自动分类、重试、降级或询问用户：

- **错误分类**：网络抖动、API 限流、超时、文件缺失、权限不足、依赖缺失、配置错误等
- **自动重试**：临时网络错误 / 限流按指数退避重试
- **自动修复**：文件缺失时自动创建父目录；超时时自动延长 `timeout`
- **用户询问**：重试耗尽后询问用户是否再试一次
- **遥测统计**：`/doctor` 可查看自愈配置、错误分布、最近事件

### 配置

在 `configs/settings.toml` 或 `~/.jarvis/settings.toml` 中配置：

```toml
[self_healing]
enable_tool_self_healing = true
tool_retry_max = 3
tool_retry_backoff_base = 1.0
tool_retry_backoff_max = 30.0
```

### 命令

```bash
/doctor              # 查看自愈统计与系统诊断
```

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

详见 [docs/architecture/11-多Agent协作.md](docs/architecture/11-多Agent协作.md)。

---

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

### CLI-Anything harness（CLI 工具封装）

```bash
/cli_anything                 # 列出已安装 harness
/cli_anything market          # 列出市场可用 harness
/cli_anything install <id>    # 安装指定 harness
/cli_anything uninstall <id>  # 卸载指定 harness
```

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

### CLI-Anything 通用功能

```bash
/cli_anything enable <id>         # 启用被禁用的 harness
/cli_anything disable <id>        # 禁用 harness，不卸载
/cli_anything create <id>         # 创建 harness 脚手架
/cli_anything validate <路径>      # 校验 SKILL.md 合法性
```

**启用/禁用**：禁用的 harness 不会被加载，保留文件。状态持久化到 `~/.jarvis/cli_anything/disabled.json`。

**harness 创建**：`/cli_anything create my-tool` 生成 `SKILL.md` + `README.md` 脚手架。

**harness 校验**：`/cli_anything validate <路径>` 检查 `SKILL.md` 是否符合规范。

详见 [docs/architecture/10-扩展生态.md](docs/architecture/10-扩展生态.md)。

---

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

---

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

---

## 目录结构

```
agent/
├── main.py            # 入口（REPL / daemon / --talk 分发）
├── acp.py             # Agent Communication Protocol
├── cli_anything/      # CLI-Anything harness 集成（包装任意软件为 CLI）
├── core/              # 核心运行时
│   ├── query_loop.py  # 对话循环（REPL 驱动 + 语音对话流程）
│   ├── orchestrator.py # Agent 编排器（ReAct 循环）
│   ├── tool.py        # Tool 协议定义
│   ├── context.py     # 工具上下文 + UI 协议（RealtimeTalkUI）
│   ├── message.py     # 消息/内容块类型（Message / ContentBlock）
│   ├── result.py      # 工具调用结果（ToolResult）
│   ├── hooks.py       # 钩子系统
│   ├── diag.py        # 诊断日志
│   ├── error_recovery.py # 工具错误自愈（分类/重试/降级/询问）
│   ├── daemon/        # 后台主动感知（调度器/监控/视觉守望/节假日/截止日期/日历）
│   ├── extensions/    # 外部扩展机制（MCP客户端/插件/Skill加载）
│   ├── memory/        # 记忆持久化（上下文压缩/恢复/文件状态/存储）
│   └── sandbox/       # 安全沙箱（风险评分/隔离执行/文件守护/审计日志）
├── collaboration/     # 多 Agent 协作框架
│   ├── subagent.py    # 子代理定义与运行
│   ├── team.py        # Agent 团队管理
│   ├── teammate.py    # 团队成员
│   ├── teammate_registry.py # 队友注册表（全局生命周期管理）
│   ├── mailbox.py     # Agent 间消息邮箱
│   └── task_list.py   # 共享任务列表
├── lsp/               # LSP 代码智能
│   ├── client.py      # LSP 客户端
│   └── manager.py     # 多语言 LSP Server 管理
├── permissions/       # 五层权限系统
│   ├── rules.py       # 权限规则定义
│   ├── checker.py     # 权限校验器
│   ├── path_guard.py  # 路径安全守护
│   ├── shell_classifier.py # Shell 命令危险分级
│   └── modes.py       # 权限模式（default/plan/accept_edits/yolo）
├── tools/             # 内置工具（30+）
│   ├── base.py        # 基础工具执行器
│   ├── bash.py        # 命令执行
│   ├── ask_user.py    # 向用户提问
│   ├── location.py    # IP 定位
│   ├── todo.py        # 任务计划
│   ├── file_ops/      # 文件读写/编辑/搜索（glob/grep）
│   ├── system/        # 系统操作（鼠标/键盘/屏幕/窗口）
│   ├── web/           # 浏览器自动化 + 网络请求
│   ├── vision/        # 摄像头拍照 + 视觉监控
│   ├── collaboration/ # 多Agent协作工具（子代理/团队/任务）
│   └── extensions/    # 扩展工具（LSP/市场/MCP代理/日程）
├── llm/               # LLM 抽象层
│   ├── base.py        # 基础 Provider 接口
│   ├── openai_provider.py    # OpenAI 兼容协议
│   ├── anthropic_provider.py # Anthropic Messages API
│   ├── dashscope_provider.py # DashScope SDK 原生协议
│   └── mock.py        # Mock Provider（测试用）
├── ui/                # 用户界面
│   ├── cli.py         # Rich 终端 REPL + 命令补全
│   ├── boot_animation.py # 启动动画（方舟反应炉像素粒子）
│   ├── markdown_renderer.py # Markdown 终端渲染
│   ├── model_picker.py # 交互式模型选择器
│   ├── session_picker.py # 交互式会话选择器
│   ├── terminal_picker.py # 交互式终端选择器
│   └── realtime_window/ # 实时聊天独立窗口
│       ├── window.py  # 父进程窗口控制器（单例 + 子进程管理）
│       ├── process.py # 子进程入口 + 前端窗口 + JSBridge
│       ├── bridge.py  # Webview ↔ RealtimeTalk 桥接（UI 协议实现）
│       └── assets/    # HTML/JS/CSS（方舟反应炉动画 + 对话气泡）
├── voice/             # 语音引擎
│   ├── tts.py         # CosyVoiceTTS（DashScope CosyVoice 合成）
│   ├── stt.py         # STT 三后端（QwenASR / ParaformerSTT / FunASRFlashSTT）
│   ├── stream_tts.py  # StreamTTSPlayer（WebSocket 流式 TTS，逐句播放）
│   ├── audio.py       # 音频工具（PyAudio 管理/音量计算）
│   ├── aec.py         # AEC 回声消除（WebRTC AEC3，外放防自言自语）
│   ├── client_vad.py  # 客户端 VAD（静音检测/语音活动判断）
│   ├── voice_loop.py  # /voice 语音对话循环（STT→LLM→TTS，含流式 TTS）
│   └── realtime_talk.py # /talk 全双工实时语音（WebSocket 直连 DashScope）
├── bridge/            # 跨设备协同（P3-1）
│   ├── server.py      # BridgeServer（HTTP 静态文件 + WebSocket 通信）
│   ├── ui.py          # BridgeUI（UIProtocol 实现，事件转发到 WS）
│   └── static/        # PWA 前端（单文件 HTML，暗色主题）
├── wechat/            # 微信 ClawBot 接入（iLink Bot API）
│   ├── ilink.py       # iLink API 客户端（扫码登录/长轮询/发消息）
│   ├── server.py      # WeChatBridge（消息循环 + 单例管理 + 24h 重连）
│   └── ui.py          # WeChatUI（UIProtocol 实现，收集回复文本）
├── daemon/            # 常驻模式
│   ├── daemon.py      # 守护进程（后台分离/托盘/热键）
│   ├── autostart.py   # 开机自启/桌面快捷方式
│   ├── hotkey_native.py # Windows 原生 RegisterHotKey（更快响应）
│   ├── terminal_spawner.py # 终端窗口生成（warm 预启动）
│   └── voice_state.py # 语音状态管理
├── config/            # 配置加载（TOML 多源合并 + 环境变量覆盖）
└── prompts/           # 系统提示组装（动态思维模式/语音模式）

npm/                   # npm 分发包（让 Node.js 用户通过 npm install -g 安装）
├── package.json       # npm 包定义（bin 指向 run.js）
├── install.js         # postinstall：检测 Python + pip install jarvis-agent[all]
└── run.js             # CLI 入口：转发参数给 jarvis 命令
```

---

## 开发路线

- [x] **阶段 1**：最小可用 Agent（对话 + 文件 + 命令 + 五层权限）
- [x] **阶段 2**：电脑操作能力（GUI + 多模态视觉 + 浏览器自动化 + 摄像头拍照）
- [x] **阶段 3**：实时语音（TTS + STT + `/voice` 闭环 + `/talk` 全双工）
- [x] **阶段 4**：记忆与生态（会话持久化/长期记忆/MCP接入/上下文压缩/Skill系统）
- [x] **阶段 5**：贾维斯形态（daemon常驻+全局热键+系统托盘+开机自启+子代理+主动感知+视觉监控+主动提醒系统）
- [x] **阶段 6**：跨平台适配（Windows / macOS / Linux）
- [x] **阶段 7**：实时聊天 UI（方舟反应炉动画窗口 + 全双工打断 + 单例管理）

---

## 许可证

本项目采用 [MIT License](LICENSE) 许可协议。

> 本项目借鉴了 ClaudeCode 等优秀工具的设计思想。作者保留创作署名权，但不对使用方式做任何限制。

详细条款请参阅 [LICENSE](LICENSE) 文件。

---

## 反馈声明

J.A.R.V.I.S. 现阶段仍处于**开发与验证阶段**，功能尚未完全稳定。使用过程中可能会出现一些小 Bug，纯属本人疏忽未能验证完全，对此深表歉意。

如您在体验过程中遇到任何问题或体验不佳，欢迎通过以下方式反馈：

- **邮箱**：13985465782@163.com

您的每一条反馈都是我改进的动力，感谢支持与包容！

---

## 开发参考

J.A.R.V.I.S. 的设计与实现参考了以下优秀项目和资源：

| 项目 / 资源 | 说明 |
|---|---|
| [ClaudeCode (BasicProtein)](https://github.com/BasicProtein/ClaudeCode) | 核心架构参考，Agent 循环与工具调用设计 |
| [claude-code (Anthropic)](https://github.com/anthropics/claude-code) | 官方 Claude Code 实现，交互范式与权限模型参考 |
| [OpenClaw](https://github.com/openclaw/openclaw) | 多渠道 AI Agent 框架，插件体系与 Channel 抽象参考 |
| [weixin-ClawBot-API](https://github.com/SiverKing/weixin-ClawBot-API) | 微信 ClawBot iLink Bot API 协议实现参考 |
| [CLI-Anything](https://github.com/HKUDS/CLI-Anything) | CLI 工具集成框架，技能扩展机制参考 |
| [DeepSeek API 文档](https://api-docs.deepseek.com) | 大模型推理接口文档 |
| [阿里云百炼平台](https://bailian.console.aliyun.com) | 实时语音对话 API（通义千问）服务端 |

---
---

## 感谢支持


<div align="center">

**感谢您使用 J.A.R.V.I.S.！**

</div>

<div align="center">

**「J.A.R.V.I.S. ——— 随时为您效劳，先生。」**

</div>


<div>
| 微信 | X (Twitter) | 抖音 |
|:---:|:---:|:---:|
| <img src="assets/wechat-qr.png" alt="微信" width="70"/> | <img src="assets/x-qr.png" alt="X" width="70"/> | 
<img src="assets/tiktok-qr.png" alt="抖音" width="70"/> |
</div>