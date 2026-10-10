# 开发者指南

> [← 返回 README](../../README.md) · [总体架构](../architecture/01-总体架构.md) · [发布流程](../publish/PUBLISH.md)

---

## 目录结构

```
agent/
├── main.py            # 入口（REPL / --gui 工作台 / --doctor 分发）
├── bootstrap.py       # 装配工厂（provider / checker / recovery / context 构建）
├── doctor.py          # 依赖健康检查（--doctor：Python 包 / 系统级依赖 / 配置）
├── model_manager.py   # 模型切换与管理（/model /models 逻辑）
├── session_manager.py # 会话自动保存 / 标题生成
├── commands/          # 斜杠命令系统
│   ├── router.py      # 命令路由（精确匹配 + 前缀匹配 + 动态技能分发）
│   └── handlers/      # 各命令处理器（core/session/model/voice/media/plugin/collab...）
├── cli_anything/      # CLI-Anything harness 集成（包装任意软件为 CLI）
├── core/              # 核心运行时
│   ├── query_loop.py  # 对话循环（REPL 驱动 + 语音对话流程）
│   ├── layered_context.py # 分层上下文管理（冻结前缀 + 滑动窗口）
│   ├── orchestrator.py # Agent 编排器（ReAct 循环）
│   ├── tool.py        # Tool 协议定义
│   ├── context.py     # 工具上下文 + UI 协议（RealtimeTalkUI）
│   ├── message.py     # 消息/内容块类型（Message / ContentBlock）
│   ├── tool_pairing.py # 配对不变量（tool_use ↔ tool_result 补齐/去孤儿）
│   ├── team_notify.py # 多 Agent 邮箱同步注入
│   ├── result.py      # 工具调用结果（ToolResult）
│   ├── hooks.py       # 钩子系统
│   ├── diag.py        # 诊断日志
│   ├── error_recovery.py # 工具错误自愈（分类/重试/降级/询问）
│   ├── images.py     # 图片/剪贴板助手（/image /paste 加载与去重）
│   ├── logging.py    # 日志
│   ├── audit/        # 工具审计日志
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
│   ├── tool_search.py # 延迟工具搜索（ToolSearch）
│   ├── file_ops/      # 文件读写/编辑/搜索（glob/grep）
│   ├── system/        # 系统操作（鼠标/键盘/屏幕/窗口）
│   ├── web/           # 浏览器自动化 + 网络请求
│   ├── vision/        # 摄像头拍照 + 视觉监控
│   ├── collaboration/ # 多Agent协作工具（子代理/团队/任务/计划）
│   └── extensions/    # 扩展工具（LSP/市场/MCP代理/日程/邮箱/CLI-Anything）
├── llm/               # LLM 抽象层
│   ├── base.py        # 基础 Provider 接口
│   ├── thinking.py    # ThinkingConfig 配置表（思考参数策略化）
│   ├── provider_registry.py # ProviderMeta 厂商注册表（延迟导入 + URL 检测）
│   ├── openai_provider.py    # OpenAI 兼容协议
│   ├── anthropic_provider.py # Anthropic Messages API
│   ├── dashscope_provider.py # DashScope SDK 原生协议
│   ├── zai_provider.py       # 智谱 ZhipuAi SDK 原生协议
│   └── mock.py        # Mock Provider（测试用）
├── ui/                # 用户界面
│   ├── cli.py         # Rich 终端 REPL + 命令补全
│   ├── boot_animation.py # 启动动画（方舟反应炉粒子动画 + 定格帧分流）
│   ├── markdown_renderer.py # Markdown 终端渲染
│   ├── model_picker.py # 交互式模型选择器
│   ├── session_picker.py # 交互式会话选择器
│   ├── terminal_picker.py # 交互式终端选择器
│   └── workbench/     # 三栏 GUI 工作台（--gui/--talk，桌面图标宿主）
│       ├── app.py     # run_workbench() 入口（守卫→队列→装配→建窗）
│       ├── engine.py  # ChatEngine（指令分发、懒装配、会话持久化）
│       ├── api.py     # WorkbenchAPI（pywebview js_api）
│       ├── bridge.py  # UI 协议→事件适配（WorkbenchUI/WorkbenchRealtimeUI）
│       ├── metrics.py # CPU/内存/磁盘采集（2 秒推事件）
│       ├── single_instance.py # 单实例守卫（端口 47812 + 锁文件心跳）
│       └── assets/    # HTML/JS/CSS（透明反应炉波纹 + 气泡）
├── voice/             # 语音引擎
│   ├── tts.py         # CosyVoiceTTS（整段合成 + 流式 start/feed/finish + 打断）
│   ├── stt/           # STT 识别包（单一后端 QwenASR）
│   │   ├── common.py    # 音频常量 / RMS 计算 / 停止标志（跨模块共享）
│   │   ├── qwen.py      # QwenASR（OmniRealtime，服务端 VAD）
│   │   └── __init__.py  # create_stt() 工厂 + 符号 re-export
│   ├── stream_tts.py  # StreamTTSPlayer（句子级流式 TTS，逐句播放）
│   ├── realtime_engine.py # /talk 协议引擎核心（WebSocket 状态机/静音策略/响应救援，传输无关）
│   ├── realtime_talk.py # /talk 终端适配器（PyAudio 采集 + ESC + 工具装配）
│   ├── realtime_bridge_audio.py # 桌面桥接适配器（BridgeMic/BridgeSpk，serve 音频帧 ⇄ 引擎）
│   ├── realtime_audio.py # /talk 音频纯函数（RMS/衰减/静音判定）
│   ├── realtime_tools.py # /talk 工具层（内置工具 + MCP 注册表聚合 + Function Calling 执行）
│   ├── realtime_mcp.py # /talk MCP 工具装配（首个 session.update 前一次性加载）
│   ├── realtime_events.py # /talk 可观测层（环境音转写 + 事件时间线 + 轮次统计）
│   ├── voice_loop.py  # /voice 语音对话循环（听→想→说 + 对话⇄待机状态机）
│   ├── voice_config.py # 语音配置（关键词/唤醒词/待机参数/语音 system prompt）
│   ├── tts_text.py    # TTS 文本清洗（markdown/ 数学公式/工具标签剥离）
│   ├── barge_in.py    # 打断监听器（ESC 键盘 / 麦克风能量 / 打断词）
│   ├── tts_voices.py  # TTS 音色目录（/tts-voice 数据源，含音色-模型适配联动）
│   ├── audio.py       # PyAudio 全局单例（防 segfault）
│   ├── aec.py         # AEC 回声消除（WebRTC AEC3，外放防自言自语）
│   └── client_vad.py  # 客户端 VAD（静音检测/语音活动判断）
├── bridge/            # 跨设备协同（P3-1）
│   ├── server.py      # BridgeServer（HTTP 静态文件 + WebSocket 通信）
│   ├── ui.py          # BridgeUI（UIProtocol 实现，事件转发到 WS）
│   └── static/        # PWA 前端（单文件 HTML，暗色主题）
├── wechat/            # 微信 ClawBot 接入（iLink Bot API）
│   ├── ilink.py       # iLink API 客户端（扫码登录/长轮询/发消息）
│   ├── server.py      # WeChatBridge（消息循环 + 单例管理 + 24h 重连）
│   └── ui.py          # WeChatUI（UIProtocol 实现，收集回复文本）
├── daemon/            # 常驻模式
│   ├── daemon.py      # 守护进程（后台分离/托盘/热键/主动服务）
│   ├── tray.py        # 系统托盘
│   ├── hotkey.py      # 全局热键（跨平台）
│   ├── hotkey_native.py # Windows 原生 RegisterHotKey（更快响应）
│   ├── sessions.py    # 语音会话管理（stop_event 中断）
│   ├── realtime.py    # 实时聊天会话管理
│   ├── autostart.py   # 开机自启/桌面快捷方式
│   ├── terminal_spawner.py # 终端窗口生成（warm 预启动）
│   ├── voice_state.py # 语音互斥锁与开关状态
│   ├── notifications.py # 系统通知
│   └── platform_utils.py # 跨平台工具
├── config/            # 配置加载（TOML 多源合并 + 环境变量覆盖）
│   ├── settings.py    # Settings 数据类 + TOML 加载 + 字段映射
│   ├── env.py         # 环境变量覆盖（JARVIS_* → Settings）
│   ├── keyring_store.py # API Key 加密存储（系统凭据管理器）
│   ├── model_registry.py # 模型 TOML 持久化（save/load）
│   └── migrations.py  # 配置迁移
├── prompts/           # 系统提示组装（动态思维模式/语音模式）
└── utils/             # 通用工具
    └── mask.py        # API Key 脱敏

tests/                 # 测试套件（2034 个测试，覆盖 LLM/Config/Tools/Core/Voice/Daemon/权限/沙箱）
├── llm/               # Provider 注册表、思考配置、流式解析、配置加载测试
├── memory/            # 会话存盘、崩溃恢复、上下文压缩测试
├── collaboration/     # 多 Agent 协作测试
├── core/ tools/ daemon/ voice/ # 各模块单元测试
├── test_command_router.py # 命令路由集成测试
├── test_query_loop.py     # 上下文压缩/图片淘汰测试
├── _query_loop_fakes.py   # QueryLoop 测试共享替身与工厂
├── test_query_loop_run.py # QueryLoop.run 主流程/工具循环/故障转移测试
├── test_query_loop_stream.py  # 内容累积/Hooks/辅助方法/_stream_once 测试
├── test_query_loop_branches.py # 团队邮箱注入/hooks 容错/延迟工具测试
├── test_query_loop_session.py # 会话持久化/模型切换测试
├── test_orchestrator.py   # 工具编排器测试
├── test_session_manager.py# 会话标题生成/保存测试
├── test_permissions.py    # 五层权限系统测试
├── test_p23_proactive.py  # 主动感知提醒测试
└── test_p38_sandbox.py    # 安全沙箱测试

.github/workflows/     # GitHub Actions CI（自动测试 + 语法检查）
└── ci.yml             # push/PR 触发，Python 3.11-3.14 矩阵

npm/                   # npm 分发包（让 Node.js 用户通过 npm install -g 安装）
├── package.json       # npm 包定义（bin 指向 run.js）
├── install.js         # postinstall：检测 Python + pip install jarvis-agent[all]
└── run.js             # CLI 入口：转发参数给 jarvis 命令
```

## 测试与 CI

项目配备 **2034 个单元/集成测试**，覆盖 LLM Provider、工具注册、配置加载、权限系统、上下文管理（含 tool_use ↔ tool_result 配对不变量）、会话管理、记忆持久化、安全沙箱、后台守护等核心模块。核心运行时（query_loop/orchestrator/记忆/权限/LLM Provider）覆盖率 **94%**。

```bash
# 运行全部测试
pytest tests/ -v

# 查看覆盖率
coverage run --source=agent -m pytest tests/ -q
coverage report
```

每次 push 或 PR 到 `main` 分支，**GitHub Actions 自动跑全量测试**（Python 3.11 / 3.12 / 3.13 / 3.14 矩阵），不通过不允许合并。

## 自动发布流程

Jarvis 通过 **GitHub Actions + Git Tag** 实现一键自动发布到 PyPI 和 npm，无需手动构建上传。

### 触发方式

```bash
# 1. 更新版本号（pyproject.toml 的 version 字段 + npm/package.json 的 version 字段
#    + agent/__init__.py 的 __version__，供 jarvis --version 读取）
# 2. 提交版本变更
git add pyproject.toml npm/package.json agent/__init__.py
git commit -m "chore: bump version to 2.1.1"

# 3. 打 tag 并推送（v 前缀必须）
git tag v2.1.1
git push github v2.1.1
```

推送 `v*` tag 后，[publish.yml](../../.github/workflows/publish.yml) 自动执行：
1. **测试** — 跑全量 pytest，失败则中止发布
2. **版本一致性校验** — tag 版本号必须与 `pyproject.toml` / `npm/package.json` 一致，否则报错（`agent/__init__.py` 不在 CI 校验范围内，需手动同步）
3. **构建** — `python -m build` 生成 wheel + sdist
4. **发布 PyPI** — 通过 Trusted Publisher（OIDC 无凭证）上传
5. **发布 npm** — 通过 `NPM_TOKEN` 上传 npm wrapper 包
6. **创建 GitHub Release** — 自动附带 wheel/sdist 下载，从 commit 提取 changelog

### 首次配置（仅做一次）

#### PyPI Trusted Publisher（无 API Token）

1. 登录 [pypi.org](https://pypi.org) → Account settings → Publishing
2. Add a new pending publisher，填入：
   - **PyPI Project Name**: `jarvis-agent`
   - **Owner**: `aceFelix`
   - **Repository name**: `jarvis`
   - **Workflow name**: `publish.yml`
   - **Environment name**: `pypi`
3. 第一次发布后，publisher 自动激活。后续版本无需再次配置。

#### npm Token

1. 登录 [npmjs.com](https://www.npmjs.com) → Access Tokens → Generate New Token → **Automation**（绕过 2FA 限制）
2. 在 GitHub repo → Settings → Secrets and variables → Actions → New repository secret
   - **Name**: `NPM_TOKEN`
   - **Value**: 上一步生成的 token

> 配置完成后，每次推送 `v*` tag 即可全自动发布。无需本地装 twine、无需手动 `npm publish`、无需管理 API token 轮换。

更多发布细节见 [docs/publish/PUBLISH.md](../publish/PUBLISH.md)。
