# J.A.R.V.I.S 使用指南

> 「随时为您效劳，先生。」

---

## 我是谁

我是 **J.A.R.V.I.S**（Just A Rather Very Intelligent System）——你的私人 AI 管家。

我以漫威宇宙中托尼·斯塔克的智能管家贾维斯为蓝本，住在你的电脑里。我能：

- **对话** — 回答问题、写代码、分析文件、规划方案
- **操作电脑** — 控制鼠标键盘、截屏看屏幕、管理窗口
- **上网** — 打开浏览器、搜索信息、操作网页
- **听和说** — 语音对话、实时双工聊天、朗读文字
- **后台常驻** — 系统托盘待命、热键唤起、定时提醒、主动服务
- **团队协作** — 派生子 Agent 并行处理复杂任务
- **控制外部软件** — 通过 CLI-Anything 操作 Blender、WPS、OBS 等

我不是冷冰冰的工具。我有性格、有判断力、会主动关心你。

---

## 快速上手

### 1. 安装

```bash
# 全功能安装（推荐）
pip install "jarvis-agent[all]"

# 或用 uv（更快）
uv tool install "jarvis-agent[all]"

# 或用 npm（需已装 Python 3.11+）
npm install -g jarvis-agent
```

### 2. 配置 API Key

```bash
# Linux / macOS
export DASHSCOPE_API_KEY=sk-xxx

# Windows PowerShell
$env:DASHSCOPE_API_KEY = "sk-xxx"
```

> 默认使用阿里云 DashScope（通义千问），也支持 OpenAI / Anthropic / DeepSeek 等。

### 3. 启动

```bash
jarvis              # 进入 REPL 对话
jarvis --daemon     # 后台常驻模式（贾维斯形态）
jarvis --talk       # 直接启动实时语音对话
jarvis --quick      # 快速启动（跳过动画和可选初始化）
```

---

## 日常使用

### 对话就像聊天

启动后直接输入自然语言，我会自动判断需要什么工具来完成任务：

```
> 帮我看看桌面上有什么文件
> 把 report.docx 里的内容总结一下
> 写一个 Python 脚本，批量重命名当前目录的图片
> 帮我搜一下最近 React 19 有什么新特性
> 打开 Chrome 帮我看看 GitHub 上有没有新通知
```

### 高效使用技巧

| 技巧 | 说明 |
|------|------|
| **直接说目标** | 不用告诉我用什么工具，我会自己选择最优方案 |
| **给上下文** | "把昨天那个项目的 README 改一下" 比 "改文件" 好 |
| **用 yolo 模式** | 信任我时切到 `/mode yolo`，省去反复确认 |
| **图片输入** | `/paste` 粘贴截图让我看，比文字描述更直观 |
| **语音交互** | 手忙时 `/voice` 直接说，不用打字 |
| **后台常驻** | `--daemon` 模式随时热键唤起，不用反复启动 |
| **会话恢复** | 我会自动保存对话，下次启动自动恢复 |

### 中断与退出

| 操作 | 效果 |
|------|------|
| `Ctrl+C` | 中断当前回答（不会退出） |
| `/exit` 或 `/quit` | 退出 REPL |
| `Ctrl+D` | 退出（Linux/macOS） |

---

## 命令速查

输入 `/` 弹出命令列表，`Tab` 键自动补全。

### 常用命令

| 命令 | 说明 |
|------|------|
| `/help` | 查看所有命令 |
| `/model <前缀>` | 切换模型（如 `/model qwen3.6`） |
| `/models` | 交互式模型管理（↑↓选择） |
| `/mode yolo` | 全自动模式（不再逐次确认） |
| `/mode default` | 恢复默认确认模式 |
| `/think on/off` | 开关深度思考（显示推理过程） |
| `/compact` | 手动压缩上下文（对话太长时用） |
| `/reset` | 清空对话重新开始 |
| `/verbose` | 开关详细输出（token 统计、缓存命中等） |

### 会话管理

| 命令 | 说明 |
|------|------|
| `/save [名称]` | 保存当前对话 |
| `/load <前缀>` | 加载已保存的对话 |
| `/sessions` | 交互式选择历史会话 |

### 图片输入

| 命令 | 说明 |
|------|------|
| `/image <路径>` | 添加图片到下一条消息 |
| `/paste` | 粘贴剪贴板图片 |

> 图片需要多模态模型（默认 qwen3.7-plus 已支持）。

### 语音命令

| 命令 | 说明 |
|------|------|
| `/voice` | 语音对话模式（听→想→说 循环） |
| `/talk` | 实时双工对话（边说边听，可打断） |
| `/say <文本>` | 朗读文字 |
| `/listen` | 录音转文字 |

### 扩展命令

| 命令 | 说明 |
|------|------|
| `/tools` | 列出所有可用工具 |
| `/skills` | 查看已加载技能包 |
| `/memory` | 查看长期记忆 |
| `/mcp` | 查看 MCP 工具连接状态 |
| `/doctor` | 系统诊断（自愈统计、配置检查） |
| `/server [目录]` | 一键启动开发服务器 |
| `/connect-phone` | 跨设备协同（手机扫码连接当前会话） |
| `/connect-wechat` | 微信扫码连接 JARVIS（通过 ClawBot 对话） |
| `/disconnect-wechat` | 断开微信 ClawBot 连接 |
| `/agents` | 查看多 Agent 团队状态 |
| `/tasks` | 查看共享任务列表 |
| `/plan` | 切换规划模式 |
| `/plugin` | 插件管理 |
| `/cli_anything` | CLI-Anything harness 管理 |

---

## 语音交互

### 语音对话 `/voice`

进入后形成 **听→想→说** 闭环：

```
🎤 你说话 → 语音识别 → 我思考回答 → 语音朗读 → 🎤 继续听...
```

- **退出**：说"退下"或按 `ESC`
- **打断**：我说话时按 `ESC` 立即停止朗读
- **适用**：做饭时、开车时、懒得打字时

### 实时双工 `/talk`

更自然的对话体验——像打电话一样：

- **全双工**：你说话的同时我能听到，不用等我说完
- **随时打断**：开口说话就自动打断我的回复
- **方舟反应炉窗口**：安装 `realtime_ui` 后弹出炫酷的动画窗口
- **退出**：说"退下"或按 `ESC`

### 语音配置建议

```toml
# ~/.jarvis/settings.toml
[stt]
model = "qwen3-asr-flash-realtime"   # 推荐：质量最高，中英混合强

[tts]
model = "cosyvoice-v3-flash"         # 快速合成
voice = "longanlang_v3"              # 音色
speech_rate = 1.0                    # 语速（1.2 稍快，0.8 稍慢）
```

---

## 电脑操作

我能直接控制你的电脑 GUI——截屏看、鼠标点、键盘打、窗口管理。

### 使用场景

```
> 帮我截个屏看看现在桌面什么样
> 打开记事本写一段话然后保存
> 帮我把浏览器窗口移到左边，编辑器移到右边
> 点击屏幕右上角的通知图标
> 帮我在 Photoshop 里把这张图裁剪成 16:9
```

### 工作原理

1. 我先 **截屏** 看清屏幕布局
2. 根据看到的内容 **定位** 目标位置
3. 执行 **点击/输入/拖拽** 操作
4. 再截屏 **验证** 结果

### 平台权限

| 平台 | 需要 |
|------|------|
| Windows | 无需额外配置 |
| macOS | 系统设置 → 隐私与安全 → 辅助功能 → 勾选终端；屏幕录制 → 勾选终端 |
| Linux | 需要 X11 桌面环境（DISPLAY 变量），Wayland 暂不支持 |

---

## 常驻模式（推荐）

```bash
jarvis --daemon
```

这是"真正的贾维斯"形态——后台待命，随叫随到。

### 功能

- **系统托盘**：蓝色同心圆图标，右键菜单操作
- **全局热键**：`Ctrl+Shift+J`（可配置）一键唤起语音
- **文本对话**：托盘菜单弹出独立终端窗口
- **实时聊天**：托盘开关方舟反应炉语音窗口
- **主动服务**：
  - 每日简报（08:30 自动播报）
  - 截止日期追踪（"下周五之前交报告"）
  - 系统资源监控（CPU/内存/磁盘告警）
  - 定时提醒（"明天3点提醒我开会"）
  - 节假日提醒
  - 连续工作提醒（2小时提醒休息）

### 开机自启

```bash
python -m agent.daemon.autostart install    # 安装
python -m agent.daemon.autostart status     # 查看状态
python -m agent.daemon.autostart desktop    # 创建桌面快捷方式
```

### 跨平台行为

| 平台 | 行为 |
|------|------|
| Windows | 无窗口后台进程，关闭终端不影响 |
| macOS | 新会话脱离终端，关闭 Terminal 不影响 |
| Linux | 前台运行（终端不能关），功能完整 |

---

## 权限模式

我内置五层安全防护，不会越权操作。

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `default` | 写操作逐次确认 | 日常使用 |
| `plan` | 只读，拒绝所有写操作 | 让我先出方案 |
| `accept_edits` | 文件编辑自动通过，命令仍确认 | 写代码时 |
| `yolo` | 全自动，不再确认 | 完全信任我时 |

切换：`/mode yolo`

> 即使在 yolo 模式，`.ssh`/`.aws` 等敏感目录和 `rm -rf /` 等危险命令仍被硬拦截。

---

## 模型管理

### 切换模型

```
/model qwen3.6       # 前缀匹配，切到 qwen3.6-plus
/model flash         # 匹配到 qwen3.5-flash
/models              # 交互式选择（↑↓ + Enter）
```

### 添加自定义模型

通过 `/models` 交互式添加，或手动编辑 `~/.jarvis/settings.toml`：

```toml
[llm.custom_models."deepseek-v4"]
api_format = "openai"
base_url = "https://api.deepseek.com/v1"
api_key = "sk-your-key"
model_type = "text"
```

支持的接口：OpenAI 兼容 / Anthropic / DashScope SDK。

### 深度思考

```
/think on     # 开启（默认）
/think off    # 关闭（更快响应）
```

开启后我会先输出思考过程（暗色面板），再给正式回复。适合复杂推理任务。

---

## 文件系统操作

我可以直接读写你工作目录下的文件：

```
> 看看当前目录有什么文件
> 读一下 main.py 的内容
> 帮我在 utils.py 里加一个日期格式化函数
> 把所有 .log 文件删掉
> 创建一个 React 项目
```

### 内置工具

| 工具 | 能力 |
|------|------|
| FileRead | 读文件（支持分段读大文件） |
| FileEdit | 精确替换文件内容 |
| FileWrite | 创建/覆写文件 |
| Glob | 按通配符搜索文件 |
| Grep | 按正则搜索文件内容 |
| Bash | 执行任意 Shell 命令 |

---

## 浏览器操作

```
> 打开百度搜索 "Python 3.13 新特性"
> 帮我看看这个网页的内容：https://example.com
> 在 GitHub 上搜索 star 最多的 AI Agent 项目
```

我能打开网页、截图看页面、点击元素、输入文字、提取文本。

---

## 多 Agent 协作

复杂任务可以派生子 Agent 并行处理：

```
> 帮我同时调研 React、Vue、Svelte 的最新状态，然后汇总对比
> 创建一个代码审查团队，一个看后端一个看前端
```

- **同步子代理**：一次性任务，完成后汇报
- **后台队友**：持久运行，通过邮箱通信
- **任务依赖**：B 等 A 完成才开始
- **自动领取**：空闲队友自动认领待办任务

管理：`/agents` 看团队，`/tasks` 看任务进度。

---

## 外部软件控制（CLI-Anything）

通过 harness 机制，我可以操作第三方软件：

```
> 用 Blender 创建一个旋转的立方体
> 帮我在 WPS 里新建一个表格
> 用 OBS 开始录屏
```

### 管理 harness

```
/cli_anything              # 已安装列表
/cli_anything market       # 市场可用
/cli_anything install wps  # 安装
/cli_anything uninstall wps # 卸载
```

---

## 插件与技能

### Skill 技能包

给我注入专业知识：

```
~/.jarvis/skills/<name>/SKILL.md        # 用户级
<workdir>/.jarvis/skills/<name>/SKILL.md # 项目级
```

查看：`/skills`

### Plugin 插件

```
/plugin search <关键词>     # 搜索市场
/plugin install <名称>      # 安装
/plugin uninstall <名称>    # 卸载
```

### MCP 工具

接入外部 API 服务（天气、地图、航班、GitHub 等）：

配置文件：`~/.jarvis/mcp.json`

查看状态：`/mcp`

---

## 配置详解

### 配置文件位置

| 优先级 | 位置 | 说明 |
|--------|------|------|
| 最高 | 环境变量 `JARVIS_*` | 临时覆盖 |
| 中 | `~/.jarvis/settings.toml` | 个人持久配置 |
| 最低 | `configs/settings.toml` | 项目默认 |

### 核心配置项

```toml
# LLM
provider = "dashscope"
model = "qwen3.7-plus"
max_tokens = 20480
enable_thinking = true

# 运行时
workdir = "./workspace"           # 工作目录
permission_mode = "default"       # 权限模式
max_iterations = 50               # 单轮最大工具调用

# 上下文压缩
[context]
compaction = true
compaction_threshold = 8000
keep_recent_messages = 6

# 常驻模式
[daemon]
hotkey = "ctrl+shift+j"
briefing_enabled = true
briefing_time = "08:30"

# 安全沙箱
[sandbox]
enabled = false
max_memory_mb = 512
max_cpu_seconds = 60
```

---

## 命令行参数

```bash
jarvis [选项]

选项：
  --workdir <路径>       指定工作目录
  --model <名称>         指定模型
  --mode <模式>          权限模式（default/plan/accept_edits/yolo）
  --verbose              详细输出（token 统计、缓存命中）
  --debug                调试模式（打印异常栈）
  --no-boot              跳过启动动画
  --quick                快速启动（跳过可选初始化）
  --daemon               常驻模式
  --talk                 直接启动实时语音
  --with-tray            前台 REPL + 托盘图标
  --headless             无界面模式（供外部桥接）
  --acp                  ACP 模式（JSON-RPC stdio，供 cc-connect 桥接）
  --version              显示版本
```

---

## 跨设备协同

在终端输入 `/connect-phone`，电脑端会显示一个二维码，手机扫码即可连接当前 JARVIS 会话，出门在外也能远程操控电脑。

**使用方式**：
1. 在 JARVIS 终端输入 `/connect-phone`
2. 终端显示二维码和访问地址
3. 手机和电脑连同一局域网 Wi-Fi
4. 手机扫码或手动访问 URL 开始对话

**核心特性**：
- 共享会话：手机端与电脑端共享同一对话历史
- 流式输出：JARVIS 回复实时推送到手机端
- Token 认证：每次自动生成 token，防止未授权访问
- 中断支持：手机端可随时中断 JARVIS 的回复

> 外网访问需配合内网穿透（如 frp、Cloudflare Tunnel）。

---

## 微信 ClawBot 接入

在终端输入 `/connect-wechat`，扫码连接微信 ClawBot，之后在微信中发消息即可与 JARVIS 对话。

**使用方式**：
1. 在 JARVIS 终端输入 `/connect-wechat`
2. 终端显示二维码，手机微信扫码并确认
3. 在微信中找到 ClawBot 发消息即可对话
4. 断开连接：输入 `/disconnect-wechat`

**核心特性**：
- 官方接口：基于腾讯 iLink Bot API，不封号
- 完整能力：微信端可使用 JARVIS 全部工具
- 共享会话：微信对话与电脑终端同步
- 24h 续期：到期前终端提醒重新扫码

**依赖**：`pip install "jarvis-agent[wechat]"`

> 需微信版本 ≥ 8.0.70，设置 → 插件中可看到 ClawBot。

---

## 常见问题

### 语音不工作？

1. 确认安装了语音依赖：`pip install "jarvis-agent[voice]"`
2. macOS 需安装 portaudio：`brew install portaudio`
3. 检查麦克风权限（macOS：隐私 → 麦克风 → 勾选终端）
4. 确认 `DASHSCOPE_API_KEY` 已配置

### GUI 操作失败？

- **macOS**：系统设置 → 隐私与安全 → 辅助功能 → 勾选 Terminal/Python
- **macOS 截图黑屏**：隐私与安全 → 屏幕录制 → 勾选终端
- **Linux**：确认有 DISPLAY 环境变量（X11 桌面）

### 如何让我更快？

1. `/think off` — 关闭深度思考，减少推理时间
2. `/mode yolo` — 跳过确认环节
3. `--quick` — 跳过启动动画和可选初始化
4. 用 flash 模型 — `/model flash`（更快但能力稍弱）

### 对话太长变慢？

- `/compact` — 手动压缩上下文
- 默认超过 8000 token 自动压缩
- `/reset` — 彻底清空重新开始

### 如何记住我的偏好？

编辑长期记忆文件：

```
~/.jarvis/MEMORY.md          # 全局记忆（所有项目）
<workdir>/.jarvis/MEMORY.md  # 项目级记忆
```

写入你的偏好（如"回复用中文""代码风格用 4 空格缩进"），我每次启动都会读取。

---

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Tab` | 命令/路径自动补全 |
| `↑` / `↓` | 浏览历史输入 |
| `Shift+Enter` | 换行（多行输入） |
| `Ctrl+C` | 中断当前回答 |
| `Ctrl+D` | 退出（Linux/macOS） |
| `ESC` | 语音模式：打断/退出 |
| `Ctrl+Shift+J` | daemon 模式：全局唤起语音 |

---

## 最佳实践

### 让我发挥最大价值的方式

1. **把我当管家，不是搜索引擎**
   - ❌ "Python 怎么读文件"
   - ✅ "帮我把 data.csv 里销售额大于 100 万的行提取出来存到新文件"

2. **复杂任务先让我规划**
   - "帮我重构这个项目的目录结构，先出个方案"
   - 我会进入 Plan 模式，只读分析后给出方案，你确认再执行

3. **善用多模态**
   - 截图给我看 → `/paste` + "这个报错怎么解决"
   - 拍照给我看 → 摄像头工具直接识别

4. **后台常驻 + 主动服务**
   - `jarvis --daemon` 让我常驻后台
   - "每天早上8点给我发简报"
   - "下周五之前提醒我交报告"
   - 我会主动服务，不用你时刻想着我

5. **信任但验证**
   - 日常用 `default` 模式，关键操作我会确认
   - 熟悉后切 `yolo`，效率翻倍
   - 敏感操作（删除、格式化）我永远会确认

---

<div align="center">

**「J.A.R.V.I.S — 随时为您效劳，先生。」**

</div>
