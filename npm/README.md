# jarvis-agent (npm wrapper)

这是 **J.A.R.V.I.S** 的 npm 安装入口。它本身不包含 Python 代码，
而是在 `postinstall` 阶段自动通过 pip / uv 安装 `jarvis-agent` Python 包，
并把 `jarvis` 命令暴露给 npm/npx 用户。

## 安装要求

- Node.js 14+
- Python 3.11+（Jarvis 核心为 Python 项目）
- pip（随 Python 一起安装）或 [uv](https://docs.astral.sh/uv/)（推荐，安装速度快 5-10x）

## 安装

```bash
npm install -g jarvis-agent
```

安装完成后即可运行：

```bash
jarvis
```

## 工作原理

1. `npm install jarvis-agent` 触发 `postinstall` 脚本 `install.js`
2. `install.js` 检测系统中的 Python，以及安装工具 uv / pip（**uv 优先**）
3. 检测到 Python 后弹出**交互式功能选装菜单**（非 TTY / CI 环境自动装 `[all]`）
4. 根据用户选择调用 `uv pip install` 或 `pip install` 安装对应 extras 的 `jarvis-agent`
5. 安装成功后根据所选功能提示对应的**系统级依赖**（如 PortAudio、playwright 浏览器、摄像头驱动等）
6. `run.js` 作为 `jarvis` 命令入口，转发参数给 Python 端的 `jarvis`

### 交互式功能选装菜单

```
J.A.R.V.I.S 功能选装菜单：

  [x] 核心功能（对话/工具/模型切换）—— 必装
  [ ] 1. 语音对话（TTS/STT，需要 PortAudio）
  [ ] 2. 系统托盘（常驻后台/主动提醒，需要 psutil）
  [ ] 3. GUI 视觉操作（鼠标键盘控制/截屏）
  [ ] 4. 浏览器自动化（需要 playwright install）
  [ ] 5. 摄像头/OCR（OpenCV + PaddleOCR）
  [ ] 6. MCP 生态接入
  [ ] 7. 微信接入
  [ ] 8. 实时聊天窗口（独立语音对话窗口）

  回车 = 全装[all] | 输入序号用逗号分隔（如 1,3,5）| 0 = 仅核心
```

| 输入 | 行为 | 安装命令示例 |
|---|---|---|
| 回车 | 安装全部功能 `[all]` | `pip install "jarvis-agent[all]==<version>"` |
| `0` | 仅装核心（无 extras） | `pip install "jarvis-agent==<version>"` |
| `1,3,5` | 按序号拼 extras | `pip install "jarvis-agent[voice,gui,camera,vision]==<version>"` |

序号与 extras 的对应关系：

| 序号 | 功能 | extras |
|---|---|---|
| 1 | 语音对话 | `voice` |
| 2 | 系统托盘 | `daemon` |
| 3 | GUI 视觉操作 | `gui` |
| 4 | 浏览器自动化 | `browser` |
| 5 | 摄像头/OCR | `camera,vision`（vision 含 mediapipe + paddleocr，体积较大） |
| 6 | MCP 生态接入 | `mcp` |
| 7 | 微信接入 | `wechat` |
| 8 | 实时聊天窗口 | `realtime_ui` |

### uv 优先策略

安装前会先执行 `uv --version` 检测：

- 检测到 uv → 使用 `uv pip install` 加速安装，并提示 `✓ 检测到 uv，使用 uv 加速安装（快 5-10x）`
- 未检测到 uv → 退回原有 `pip install` 逻辑

### 系统级依赖提示

`pip install` 成功后，只会针对用户**实际选装**的功能打印对应的系统级依赖提示，例如：

```
✅ Python 依赖安装成功！

📋 系统级依赖检查：
  ✓ 语音：需要 PortAudio
    - Windows: 通常已内置
    - macOS: brew install portaudio
    - Linux: sudo apt install portaudio19-dev
  ✓ 浏览器：需要下载浏览器二进制
    - 运行: playwright install
  ✓ 视觉监控：需要摄像头驱动
    - Windows: 通常已内置
    - macOS: 需要摄像头权限
```

## 本地开发

```bash
cd jarvis/npm
node install.js   # 手动触发 pip / uv 安装（会进入交互菜单）
node run.js       # 启动 jarvis
```

CI / 非交互环境下，`install.js` 会跳过选装菜单直接安装 `[all]`：

```bash
node install.js < /dev/null   # 模拟非 TTY，自动装 [all]
```

## 许可证

MIT © aceFelix
