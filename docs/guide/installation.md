# 安装指南

> [← 返回 README](../../README.md) · [命令参考](commands.md) · [语音系统](voice.md) · [核心机制](concepts.md)

---

## 从 PyPI 安装（推荐）

```bash
# 一键安装全功能（语音 + GUI + daemon + MCP + 浏览器 + 摄像头/视觉 + 实时聊天窗口）
pip install "jarvis-agent[all]"

# 仅安装核心对话功能
pip install jarvis-agent
```

## 从 GitHub 安装

```bash
# 克隆仓库
git clone https://github.com/aceFelix/jarvis.git
cd jarvis

# 安装核心包（开发模式）
pip install -e .

# 开发模式全功能
pip install -e ".[all]"
```

> **注意**：`jarvis` 命令入口生成在**当前环境**的 Scripts/bin 目录。
> 在 venv 里安装后必须先激活虚拟环境，否则终端会报 `jarvis: command not found`：
> - Windows Git Bash：`source .venv/Scripts/activate`
> - Windows PowerShell：`.venv\Scripts\Activate.ps1`
> - Linux/macOS：`source .venv/bin/activate`

## 用 uv 安装（更快）

[uv](https://docs.astral.sh/uv/) 是 Rust 编写的高性能 Python 包管理器，推荐新用户尝试：

```bash
# 作为全局工具安装
uv tool install "jarvis-agent[all]"

# 之后直接用
jarvis
```

## 用 npm 安装

通过 npm 一键安装：

```bash
npm install -g @acefelix/jarvis

# 之后直接用
jarvis
```

> **环境要求**：
> - **Node.js ≥ 18**（推荐 **Node 20 LTS** 或更高版本，Node 14/16 已停止维护）
> - **Python 3.11+** 并加入 PATH
>
> npm 包会自动检测 Python 环境并通过 pip 安装 `jarvis-agent[all]`。

## 默认安装路径

安装方式决定**程序本体**的位置（跟随 Python / 包管理器），而**用户数据**统一存放在 `~/.jarvis`（与 Python 无关）。

**程序本体：**

| 安装方式 | 包（agent）位置 | 命令入口 `jarvis` |
|---|---|---|
| pip（系统 Python） | `Python安装目录\Lib\site-packages`（Windows）<br>`/usr/lib/python3.x/site-packages` 或 `~/.local/lib/python3.x/site-packages`（Linux/macOS） | `Python安装目录\Scripts\jarvis.exe`（Windows）<br>`~/.local/bin/jarvis`（Linux/macOS） |
| pip（venv 虚拟环境） | `<虚拟环境>\Lib\site-packages`（Windows）<br>`<虚拟环境>\lib\python3.x\site-packages`（Linux/macOS） | `<虚拟环境>\Scripts\jarvis.exe`（Windows）<br>`<虚拟环境>\bin\jarvis`（Linux/macOS） |
| GitHub 开发模式（`pip install -e .`） | editable 安装，`agent` 包直接指向克隆的源码目录 | 同上（Scripts/bin 下生成入口） |
| uv（`uv tool install`） | Windows: `%APPDATA%\uv\tools\jarvis-agent`<br>Linux/macOS: `~/.local/share/uv/tools/jarvis-agent`（uv 管理的隔离 venv） | `~/.local/bin/jarvis`（uv 自动链接） |
| npm（`npm install -g`） | npm 包本体在全局 node_modules（Windows: `%APPDATA%\npm\node_modules`；Linux/macOS: `/usr/lib/node_modules` 或 `~/.npm-global`）；Python 包由 install.js 装到对应 Python 的 site-packages | npm 全局 bin 目录的 `jarvis`（Windows: `%APPDATA%\npm`） |

**用户数据（所有安装方式统一，卸载/重装不丢）：**

| 内容 | 路径 |
|---|---|
| 通用配置（`settings.toml`：运行时/语音/记忆/沙箱等） | `~/.jarvis/settings.toml`（Windows: `C:\Users\<用户名>\.jarvis`） |
| 模型配置（`models.toml`：模型选择 / Base URL / **API key** / 可选模型 / 自定义模型） | `~/.jarvis/models.toml`（同上目录，2026-09 从 settings.toml 拆出） |
| daemon 日志 | `~/.jarvis/daemon.log` |
| 插件 / 技能 / 会话记忆 | `~/.jarvis/` |
| 截图临时目录 | `%TEMP%\jarvis-shots`（Windows）`/tmp/jarvis-shots`（Linux/macOS） |

> **提示**：site-packages 路径跟随"执行 pip 的那个 Python"。机器上装了多个 Python（3.11/3.12/3.13）时，用 `python -m pip install` 可强制绑定当前 `python`，用 `python -m pip show jarvis-agent` 查看实际安装位置（`Location` 字段）。

## 安装可选功能

jarvis 将不同能力拆分为可选依赖组，按需安装：

| 依赖组 | 功能 | 安装命令 |
|---|---|---|
| `gui` | 鼠标/键盘/截屏/窗口管理 | `pip install "jarvis-agent[gui]"` |
| `browser` | 浏览器自动化（Playwright） | `pip install "jarvis-agent[browser]"` |
| `mcp` | MCP 工具集成 | `pip install "jarvis-agent[mcp]"` |
| `camera` | 摄像头拍照 | `pip install "jarvis-agent[camera]"` |
| `vision` | 实时视觉监控 + OCR | `pip install "jarvis-agent[vision]"` |
| `voice` | 语音对话 `/voice` + 实时双工 `/talk`（STT+TTS+全双工） | `pip install "jarvis-agent[voice]"` |
| `daemon` | 桌面入口/热键/开机自启 | `pip install "jarvis-agent[daemon]"` |
| `realtime_ui` | 三栏工作台窗口（方舟反应炉动画，`--gui`/`--talk`） | `pip install "jarvis-agent[realtime_ui]"` |
| `all` | 上面全部 | `pip install "jarvis-agent[all]"` |

## 平台系统依赖

**Windows**: 无需额外系统依赖，直接 `pip install` 即可。

> 三栏工作台窗口需要 Edge WebView2 Runtime（Win10/11 通常已预装），如未安装请从 [Microsoft 官网](https://developer.microsoft.com/microsoft-edge/webview2/) 下载。

**macOS**:

```bash
brew install portaudio          # pyaudio 编译依赖（语音功能必需）
# 系统设置 → 隐私与安全 → 辅助功能 → 允许终端/Python（GUI 操作必需）
# 系统设置 → 隐私与安全 → 麦克风 → 允许终端/Python（语音输入必需）
```

**Linux (Ubuntu/Debian)**:

```bash
sudo apt install portaudio19-dev python3-pyaudio  # 语音功能
sudo apt install python3-tk                        # pyautogui 截屏依赖
```

**Linux (Fedora/RHEL)**:

```bash
sudo dnf install portaudio-devel gtk3-devel
```

## 依赖健康检查

安装完成后或遇到功能不可用时，运行 `--doctor` 一键诊断所有依赖状态：

```bash
jarvis --doctor
```

检查内容（用 rich 表格渲染，退出码 0=全部就绪 / 1=有缺失）：

| 类别 | 检查项 |
|---|---|
| 📦 Python 包 | 语音 / 系统监控 / GUI 工作台 / 浏览器 / 摄像头 / 视觉监控 / MCP / 实时窗口 / 微信 / LLM 核心等可选包，按 extras 组归类并给出 `pip install` 命令 |
| 🔧 系统级依赖 | Python 版本（>=3.11） / pip / uv（推荐） / Playwright 浏览器 / Edge WebView2 Runtime（Windows） / 麦克风权限提示 |
| ⚙️ 配置状态 | `~/.jarvis/settings.toml` 是否存在 / API Key 是否配置（不显示 key 内容） / `permissions.yaml` 是否就绪 |

> 包检查用 `importlib.util.find_spec` 探测，不实际 import，避免触发未安装包的副作用日志。
