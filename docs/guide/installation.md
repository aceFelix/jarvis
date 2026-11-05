# 安装指南

> [← 返回 README](../../README.md) · [命令参考](commands.md) · [语音系统](voice.md) · [核心机制](concepts.md)

---

## 从 PyPI 安装（推荐）

```bash
# 一键安装全功能（语音 + GUI 操控 + daemon + MCP + 浏览器 + 摄像头/视觉 + 微信接入）
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
| `daemon` | 主动感知（日历源 / 系统资源监控）与全局热键 | `pip install "jarvis-agent[daemon]"` |
| `wechat` | 微信 ClawBot 接入 | `pip install "jarvis-agent[wechat]"` |
| `all` | 上面全部 | `pip install "jarvis-agent[all]"` |

> ⚠️ **`pip install "jarvis-agent[browser]"` 装不上浏览器内核**。playwright 的 chromium
> 不在 pip 包里（另约 296MB），装完还要跑一次：
>
> ```bash
> python -m playwright install chromium
> ```
>
> 漏了这一步的表现最容易误判：包明明在、代码能 import，但“浏览器就是打不开”。
> 它属于能力自检的范畴，两个入口都会报出来：`jarvis --doctor`（启动期诊断），以及会话内的
> `/health caps browser.selfcheck`（逐条前置条件的实测依据 + 可复制补法，也可用
> `/health caps browser.selfcheck --repair` 让它代跑）。机制见
> [能力自检与自我修复](../architecture/20-能力自检与自我修复.md)。

> 原 `realtime_ui`（pywebview 三栏工作台窗口）已于 2026-10 随 `--gui` / `--talk`
> 一并退役，图形化桌面由独立仓库 **jarvis-desktop** 提供（见
> [workbench-gui-retired](../fixlogs/workbench-gui-retired.md)）。

## 平台系统依赖

**Windows**: 无需额外系统依赖，直接 `pip install` 即可。

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

> **能力级平台差异**：上表只说“跑起来需要装什么”；哪些能力在哪个系统上完整 / 受限 / 不支持，见 [README 平台支持矩阵](../../README.md#平台支持)。要点：
> - **系统维护层**（`/disk` `/clean` `/health`）依赖 PowerShell + `SecurityCenter2` + 盘符 + `cleanmgr`，**仅 Windows**（POSIX 上如实拒绝）；
> - **jarvis-desktop 桌面壳安装包**目前也只发布 Windows NSIS `.exe`；
> - GUI 的窗口管理在非 Windows 上依赖 `pygetwindow`，能力受限；
> - 语音（`/voice` · `/talk`）三平台可用，但都需 portaudio 系统库与 DashScope Key。

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
>
> `--doctor` 与会话内的 `/health caps` 是**两套互补但不同源**的判断（2026-11-05 实测：
> `agent/doctor.py` 自带 `_PKG_CHECKS` 表，不读 `agent/capabilities/`）：
>
> - `--doctor` 回答「pip 该装哪些组」：逐包给 `pip install "jarvis-agent[<组>]"` 命令，
>   额外查 Python 版本 / pip / uv / Playwright 内核目录 / WebView2 / 配置文件是否存在；
>   退出码 0=全就绪、1=有缺失（`ℹ` 纯提示项不计失败）。
> - `/health caps` 回答「这些能力现在真能用吗」：逐能力列前置条件的实测证据，还多查
>   `lsp.python`（pylsp 可执行文件）、`secrets.keyring`（keyring 包）、OCR 权重目录这些
>   `--doctor` 不查的东西，另有 `--deep` 冒烟档与 `--repair` 修复入口。
>
> 不确定缺什么时两个都跑：先看 `--doctor` 的缺失包，再看 `/health caps` 哪条能力被
> 拖到「可用（有缺项）」。彻底同源（让 `--doctor` 改读能力表）已记为后续项，见
> [能力自检与自我修复 §11](../architecture/20-能力自检与自我修复.md)。

## 版本更新

jarvis 启动时会**后台检查** PyPI 上的最新版本（非阻塞、最多 24 小时一次），发现更新时打印一行提示；不会自动改动已安装的文件。

```bash
# 方式一：会话内一键升级（自动识别 pip / uv 渠道，重启后生效）
/update

# 方式二：手动升级
pip install -U "jarvis-agent[all]"     # pip 安装的
uv tool upgrade jarvis-agent            # uv tool 安装的
npm update -g @acefelix/jarvis          # npm 安装的
```

关闭启动检查（三种方式任选）：

```bash
jarvis --no-update-check                # 本次启动
export JARVIS_UPDATE_CHECK=0            # 环境变量（Windows: set JARVIS_UPDATE_CHECK=0）
```

```toml
# ~/.jarvis/settings.toml
[update]
check = false
```

> 说明：`/update` 只在 pip / uv tool 安装下可自动升级；开发模式（`pip install -e .`）与未作为包安装的场景会跳过，仅给出指引。升级只更新主包，已安装的 extras 依赖不会被卸载。
