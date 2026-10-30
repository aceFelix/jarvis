<div align="center">
  <img src="assets/jarvis-reactor-header.svg" alt="J.A.R.V.I.S." width="100%"/>
</div>

# J.A.R.V.I.S.

<div align="center">

<a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0" /></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg" alt="Python 3.11-3.14" /></a>
<a href="https://pypi.org/project/jarvis-agent/"><img src="https://img.shields.io/pypi/v/jarvis-agent?logo=python&logoColor=white" alt="PyPI version" /></a>
<a href="https://www.npmjs.com/package/@acefelix/jarvis"><img src="https://img.shields.io/npm/v/@acefelix/jarvis?logo=npm&logoColor=white" alt="npm version" /></a>
<a href="https://github.com/aceFelix/jarvis/actions"><img src="https://github.com/aceFelix/jarvis/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
<a href="https://github.com/aceFelix/jarvis-desktop"><img src="https://img.shields.io/badge/desktop-jarvis--desktop-4b6fdd.svg?logo=electron&logoColor=white" alt="jarvis-desktop" /></a>
<a href="https://www.deepseek.com"><img src="https://img.shields.io/badge/DeepSeek-API-4D6BFE.svg?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xMiAyTDIgN2wxMCA1IDEwLTV6TTIgMTdsMTAgNSAxMC01TTIgMTJsMTAgNSAxMC01Ii8+PC9zdmc+" alt="DeepSeek" /></a>
<a href="https://bailian.console.aliyun.com"><img src="https://img.shields.io/badge/DashScope-%E7%99%BE%E7%82%BC-FF6A00.svg?logo=alibabacloud&logoColor=white" alt="DashScope" /></a>
<a href="#反馈声明"><img src="https://img.shields.io/badge/status-Beta%20%E5%BC%80%E5%8F%91%E9%AA%8C%E8%AF%81%E4%B8%AD-yellow.svg" alt="Status" /></a>
<a href="https://github.com/aceFelix/jarvis"><img src="https://img.shields.io/github/stars/aceFelix/jarvis?style=social" alt="GitHub stars" /></a>
<a href="https://gitcode.com/acefelix/jarvis"><img src="https://gitcode.com/acefelix/jarvis/star/badge.svg" alt="GitCode stars" /></a>
<a href="https://www.marvel.com/characters/iron-man-tony-stark"><img src="https://img.shields.io/badge/%E8%87%B4%E6%95%AC-Iron%20Man-B31B1B.svg" alt="致敬 Iron Man" /></a>

</div>

<div align="center">

🌐 简体中文 | [English](README.en.md)

</div>

> **J**ust **A** **R**ather **V**ery **I**ntelligent **S**ystem
>
> 「随时为您效劳，先生。」

**J.A.R.V.I.S. 是一个主动陪伴你电脑的 AI 智能管家**——致敬《钢铁侠》里的贾维斯。它常驻你的操作系统，能看、能听、能说、能动手：对话只是入口，**把事情办了才是目的**。你说"下周五前交项目报告"，它登记截止日期并提前提醒你；你说"退下"，它安静待机，第二天早上用一句"先生，早上好"向你播报今日简报。

> 北极星：让每个人拥有一位真正懂你、主动打理、随叫随到的电脑管家。
> 不是聊天框，不是语音助手——你还没开口，它已经把事情办了。

---

## 为什么需要 JARVIS？

| 现状痛点 | JARVIS 的回答 |
|---|---|
| AI 聊天工具很多，但**没几个能真正替你操作电脑** | 100+ 内置工具：文件、命令、浏览器、键鼠操控、截屏识图、摄像头、邮件——ReAct 循环自主规划执行，GUI 自动化像人一样点按拖拽 |
| 云端助手**不认识你的电脑**，数据也不在你手里 | 本地优先：常驻你的系统、读你的文件、操控你的软件；记忆、日志、配置全在本地 `~/.jarvis`，密钥进系统凭据管理器 |
| 大多数助手**每次都像初见**，交代过的背景全忘 | 三层记忆：会话存盘 + 长期记忆 + 画像记忆（自动提炼你的习惯偏好），越用越懂你 |

<div align="center">
  <img src="docs/assets/Jarvis_vs_Claude_Code_vs_OpenClaw.png" alt="JARVIS vs Claude Code vs OpenClaw 十维能力对比" width="640"/>
  <p><em>十维能力实测对比：JARVIS 在语音交互、电脑控制、视觉感知、主动服务上差异化领先</em></p>
</div>

## 效果展示

<div align="center">

**jarvis-desktop 桌面工作台** — 三主题皮肤 · 三栏布局（左：控制台 / 中：对话流 / 右：任务中心 · 用量 · 系统状态）

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/desktop-workbench0.png" width="100%"/><br/><sub>荧光绿 · 任务中心与系统状态</sub></td>
  <td width="33%"><img src="assets/screenshots/desktop-workbench1.png" width="100%"/><br/><sub>电光蓝 · 设置面板</sub></td>
  <td width="33%"><img src="assets/screenshots/desktop-workbench2.png" width="100%"/><br/><sub>金属银 · 设置面板</sub></td>
</tr>
</table>

**终端 REPL** — 一轮关于记忆的对话：「贾维斯你对我了解多少？」（技能加载 → MCP 知识图谱 → 画像记忆）

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/repl-chat0.png" width="100%"/><br/><sub>启动：方舟反应炉 + MCP/LSP 就绪</sub></td>
  <td width="33%"><img src="assets/screenshots/repl-chat1.png" width="100%"/><br/><sub>思考过程：规划多源信息</sub></td>
  <td width="33%"><img src="assets/screenshots/repl-chat2.png" width="100%"/><br/><sub>LoadSkill 技能加载</sub></td>
</tr>
<tr>
  <td width="33%"><img src="assets/screenshots/repl-chat3.png" width="100%"/><br/><sub>ToolSearch 检索 MCP 工具</sub></td>
  <td width="33%"><img src="assets/screenshots/repl-chat4.png" width="100%"/><br/><sub>查询知识图谱画像</sub></td>
  <td width="33%"><img src="assets/screenshots/repl-chat5.png" width="100%"/><br/><sub>思考汇总：整合画像数据</sub></td>
</tr>
<tr>
  <td width="33%"><img src="assets/screenshots/repl-chat6.png" width="100%"/><br/><sub>回答：三层记忆 · 越用越懂你</sub></td>
  <td width="33%"><img src="assets/screenshots/model-manage.png" width="100%"/><br/><sub>/models 模型管理（11 厂商）</sub></td>
  <td width="33%"><img src="assets/screenshots/voice-manage.png" width="100%"/><br/><sub>/tts-voice 音色管理</sub></td>
</tr>
</table>

**语音交互** — `/voice` 逐轮对话 · `/talk` 实时双工（说话即打断）

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/realtime-talk0.png" width="100%"/><br/><sub>/voice：聆听→查时间→贴心提醒→打断</sub></td>
  <td width="33%"><img src="assets/screenshots/realtime-talk1.png" width="100%"/><br/><sub>/talk：启动横幅（server_vad + AEC）</sub></td>
  <td width="33%"><img src="assets/screenshots/realtime-talk2.png" width="100%"/><br/><sub>/talk：语音讲个程序员笑话</sub></td>
</tr>
</table>

**跨设备协同** — 手机扫码接管同一会话（/connect-phone）

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/phone-connect0.png" width="100%"/><br/><sub>终端生成二维码 + 消息双端同步</sub></td>
  <td width="33%"><img src="assets/screenshots/phone-connect1.png" width="100%"/><br/><sub>手机端：思考过程可视</sub></td>
  <td width="33%"><img src="assets/screenshots/phone-connect2.png" width="100%"/><br/><sub>手机端：MCP 工具查天气</sub></td>
</tr>
</table>

**微信 ClawBot** — 在微信里随时使唤管家（/connect-wechat）

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/wechat-clawbot0.png" width="100%"/><br/><sub>终端扫码连接（24h 有效）</sub></td>
  <td width="33%"><img src="assets/screenshots/wechat-clawbot1.png" width="100%"/><br/><sub>微信发消息即可对话</sub></td>
  <td width="33%"><img src="assets/screenshots/wechat-clawbot2.png" width="100%"/><br/><sub>完整工具能力随身携带</sub></td>
</tr>
</table>

<!-- proactive-briefing（主动简报播报「先生，提醒您：」）截图待补，补齐后在此追加一行 -->

</div>

## 核心能力

| 能力 | 一句话 | 文档 |
|---|---|---|
| 🖥️ **终端原生 Agent** | Rich 终端 REPL + 100+ 工具 + ReAct 编排，Ctrl+C 任意阶段打断 | [核心机制](docs/guide/concepts.md) · [命令参考](docs/guide/commands.md) |
| 🎙️ **能听会说** | `/voice` 逐轮语音对话，`/talk` 全双工实时聊天（说话即打断），TTS 播报主动提醒 | [语音系统](docs/guide/voice.md) |
| 🖱️ **操控电脑** | 鼠标/键盘/截屏/窗口管理 + 视觉模板定位，像人一样操作任意软件 | [GUI 自动化](docs/guide/ecosystem.md#gui-自动化) |
| 🗓️ **主动管家** | 每日简报、定时提醒（桌面与终端 REPL 都能对话安排）、截止日期追踪、系统监控——不用叫，它自己看着办 | [主动提醒系统](docs/guide/desktop.md#主动提醒系统-p2-3已接线) |
| 👀 **工作现场感知** | 前台窗口 / 目录堆积 / 仓库状况 / 重要来信 / 摄像头在场 / 日程临近 / 系统资源七类事件进统一总线，经打扰预算后只在该说话时开口（**逐源默认关**，从未表过态时连接后告知一次，采了什么可导出自查） | [感知源](docs/guide/desktop.md#感知源阶段-9--w2已接线) |
| 🛎️ **管家服务** | jarvis-desktop 标题栏入口：邮箱服务（查看/修改当前邮箱 + 配置 SMTP + 发送邮件）直连后端工具（后续可扩展更多服务） | [桌面与多端](docs/guide/desktop.md) |
| 🧰 **扩展服务** | jarvis-desktop 标题栏入口：右栏面板集中管理 MCP 服务（增删改+启停）、内置工具、技能、插件与 harness 工具（市场搜索/安装/卸载/启停） | [桌面与多端](docs/guide/desktop.md) |
| 🧠 **三层记忆** | 会话存盘 + 长期记忆 + 画像提炼，越用越懂你 | [记忆系统](docs/guide/concepts.md#记忆系统) |
| 📱 **多端同会话** | jarvis-desktop 桌面壳 / 手机扫码 / 微信 ClawBot，三端共享同一会话 | [桌面与多端](docs/guide/desktop.md) |
| 🧭 **管家运行时层** | permissions.yaml 路径级免确认 + 授权租约免打断 + 打扰预算削废话 + /away 无人值守待批清单（静默时段里你亲口设的提醒改为**上屏不出声**，可配同时发邮件） | [W1 运行时层](docs/architecture/16-W1判断层.md) |
| 🧾 **敢放手的一层** | 发出去的邮件先进 30 秒待发窗口（`/undo cancel` 可拦，窗口未结案期间 `curl` 这类外发命令也会临时改问人），**替你改的文件在写入前自动留底，`/undo` 一条命令还原文（只动改过的那几个文件，不影响同批其它改动）**；"替我盯着这件事，条件到了再往下做"有跨会话任务脊柱承载，**一句话就能建目标**（`/tasks add …` 或对贾维斯说，等待期间一次 LLM 都不调） | [W3 兑现层](docs/architecture/18-W3兑现层.md) |
| 🧩 **开放生态** | MCP 接入、插件市场、Skill 技能包、CLI-Anything 把任意软件变工具、多 Agent 协作 | [扩展生态](docs/guide/ecosystem.md) |
| 🔐 **安全底线** | 五层权限 + 四级沙箱 + 操作审计 + keyring 加密存储，危险操作硬阻断 | [核心机制](docs/guide/concepts.md#五层权限系统) |

**接入自由**：支持 11 家厂商（阿里云 DashScope / DeepSeek / OpenAI / 智谱 / Anthropic / Kimi / MiniMax / SiliconFlow / 小米 MiMo / Google Gemini / 自定义兼容服务），`/models` 双击即改、运行中热切换。

## 快速开始

```bash
# 三选一安装（Python 3.11+）
pip install "jarvis-agent[all]"          # PyPI 全功能
uv tool install "jarvis-agent[all]"      # uv（更快）
npm install -g @acefelix/jarvis          # npm（需 Node 18+ / Python 3.11+）

jarvis --init    # 交互式配置：选厂商 → 确认模型 → 输 Key → 自动测试连接 → 保存
jarvis           # 开聊：自然语言直接说，"/" 弹命令面板，Tab 补全
```

- 默认接阿里云 DashScope（`qwen3.7-plus` 多模态），也可 `export DASHSCOPE_API_KEY=sk-xxx` 后直接启动
- 不想装 Python、只想用图形界面？直接下载 [jarvis-desktop 安装包](https://github.com/aceFelix/jarvis-desktop/releases/latest)（Windows，免装 Python 双击即用）
- 所有配置在 `~/.jarvis/`（settings.toml + models.toml），`/mode` 切换权限模式，`/think` 调深度思考
- **懒得改配置就直接说**：“太吵了”“每天 7:15 给我出简报”“把我切窗口的记录关掉”——它自己改配置并当场生效，回执含原值多少、怎么 `/undo` 回退；沙箱开关与凭据一律拒绝（那是它该守的护栏）
- 遇到功能不可用？`jarvis --doctor` 一键诊断依赖与配置
- 启动后台检查新版本，发现更新提示 `/update` 一键升级（`--no-update-check` 可关闭）
- 本仓库在 [GitCode](https://gitcode.com/acefelix/jarvis) 与 [GitHub](https://github.com/aceFelix/jarvis) 双平台同步托管，每次提交两边一并推送

> 安装路径、可选依赖组、各平台系统依赖、`--serve` 后端入口等详见 [安装指南](docs/guide/installation.md)。
> 想一句话理解这个项目要去哪，读 [蓝图文档](docs/blueprint/BLUEPRINT.md)。

## 平台支持

| | Windows | macOS | Linux |
|---|---|---|---|
| 全部功能（对话/语音/GUI 操控/serve 后端/jarvis-desktop 桌面壳） | ✅ **全功能实机验证** | ✅ 代码适配 | ✅ 代码适配 |

> ⚠️ 本项目在 Windows 上完成全部开发与实机验证，macOS / Linux 仅代码层适配、未完整实机测试，建议优先 Windows。逐项差异见 [安装指南](docs/guide/installation.md#平台系统依赖)。

## 文档地图

| 想了解 | 文档 |
|---|---|
| **新手第一站**：贾维斯亲自带你上手（人设化指南） | [USER_GUIDE.md](USER_GUIDE.md) |
| 安装 / 升级 / 平台依赖 | [docs/guide/installation.md](docs/guide/installation.md) |
| 全部命令 / 模型管理 / 深度思考 | [docs/guide/commands.md](docs/guide/commands.md) |
| 语音对话 / 实时双工 / 音色 | [docs/guide/voice.md](docs/guide/voice.md) |
| 桌面壳 / 主动提醒 / 手机 / 微信 / serve 协议 | [docs/guide/desktop.md](docs/guide/desktop.md) |
| 权限 / 压缩 / 记忆 / 沙箱 / 自愈 | [docs/guide/concepts.md](docs/guide/concepts.md) |
| 授权租约 / 打扰预算 / 无人值守 | [docs/architecture/16-W1判断层.md](docs/architecture/16-W1判断层.md) |
| 撤销行动（`/undo`）/ 跨会话任务脊柱 | [docs/architecture/18-W3兑现层.md](docs/architecture/18-W3兑现层.md) |
| 让它自己改配置（说了就改 / 能改什么 / 为什么要确认） | [docs/architecture/12-配置系统.md](docs/architecture/12-配置系统.md) |
| 多 Agent / 插件 / CLI-Anything / 邮件 | [docs/guide/ecosystem.md](docs/guide/ecosystem.md) |
| 配置项 / 厂商接入 / 常见问题 | [config-docs/](config-docs/configuration.md) |
| 架构设计（18 篇） | [docs/architecture/](docs/architecture/00-索引.md) |
| 测试 / CI / 发布 / 目录结构 | [docs/guide/development.md](docs/guide/development.md) |
| 蓝图（愿景 · 能力模型 · 演进路线） | [docs/blueprint/BLUEPRINT.md](docs/blueprint/BLUEPRINT.md) |

## 开发路线

- [x] **阶段 1**：最小可用 Agent（对话 + 文件 + 命令 + 五层权限）
- [x] **阶段 2**：电脑操作能力（GUI + 多模态视觉 + 浏览器自动化 + 摄像头拍照）
- [x] **阶段 3**：实时语音（TTS + STT + `/voice` 闭环 + `/talk` 全双工）
- [x] **阶段 4**：记忆与生态（会话持久化 / 长期记忆 / MCP 接入 / 上下文压缩 / Skill 系统）
- [x] **阶段 5**：贾维斯形态（主动感知 + 子代理 + 主动提醒 + jarvis-desktop 桌面壳）
- [x] **阶段 6**：跨平台适配（Windows / macOS / Linux）
- [x] **阶段 7**：实时聊天 UI（方舟反应炉工作台 + 全双工打断 + 桌面应用）
- [x] **阶段 8**：W1 运行时层（授权租约 + 打扰预算 + 无人值守；声明式规则并入五层管线）——已随 v2.2.3 发布
- [x] **阶段 9**：W2 感知层（统一事件总线 + 窗口/目录/仓库/来信/在场五类事件源 + 逐源默认关 + 首次启动询问 + 桌面「感知源」面板）——已随 v2.2.5 发布
- [x] **阶段 10**：W3 兑现层（副作用账本 + 不可逆动作延时窗口 + `/undo`；跨会话任务脊柱 + 后台驱动器 + `/tasks`）——**后端已交付（374 例单测，全量 3205 例）**，含目标入口三件套（`/tasks add|show|approve|pause|resume|cancel|advance|delete` 子命令、`TrackGoal` 工具、serve `tasks.exec`/`actions.exec` RPC，共用 `core/tasks/spine_ops.py`）；两条一键实机命令：“不经模型”那一半 `python scripts/smoke_w3_spine.py`（40 项），**“到点真的自己把活干了并且真的能撤”那一半 `python scripts/livecheck_w3.py`（12 项，真 LLM）** —— 后者已于 2026-10-22 全绿跑穿，当场发现并修掉四条断链（子代理不继承宿主权限规则、受阻却谎报已完成、后台改动不入账、`[actions]` 对后台无效）；延时窗口一段则于 2026-10-21 跑穿（修了两处：等价通路绕道、`/undo --task` 归属断链）；同日补上「批准并重试」的真通路（`tasks.exec approve`：按本步被拒工具 + 目标目录 + 30 分钟签限定范围租约），并在 jarvis-desktop 仓接完右栏「跨会话目标」与「副作用账本」两个区块（那边 524 例 vitest 全绿）；尚未发布，只剩桌面实机渲染需人工走查
- [x] **阶段 11**：感知层收编（把休眠的 `CalendarSource` / `SystemMonitor` 接进事件总线：日程临近每条只报一次、资源阈值告警复用 monitor 冷却；面板同步放开这两个开关，唯一仍未收编的 `schedule` 因会重复播报而刻意不接）

> 阶段 8–11（运行时层 / 感知层 / 兑现层 / 体验补齐）与全部能力条目见 [蓝图文档](docs/blueprint/BLUEPRINT.md)，执行级细节见 [docs/roadmap/](docs/roadmap/)。

## 反馈声明

J.A.R.V.I.S. 现阶段仍处于**开发与验证阶段**，功能尚未完全稳定。使用中如遇问题或体验不佳，欢迎反馈：**13985465782@163.com**。您的每一条反馈都是我改进的动力，感谢支持与包容！

## 感谢支持

<div align="center">

**感谢您使用 J.A.R.V.I.S.！**

**「J.A.R.V.I.S. ——— 随时为您效劳，先生。」**

</div>

<div align="center">

<table>
<tr>
  <td align="center">微信</td>
  <td align="center">X (Twitter)</td>
  <td align="center">抖音</td>
  <td align="center">哔哩哔哩</td>
</tr>
<tr>
  <td><img src="assets/wechat-qr.png" alt="微信" width="55"/></td>
  <td><img src="assets/x-qr.png" alt="X" width="55"/></td>
  <td><img src="assets/tiktok-qr.png" alt="抖音" width="55"/></td>
  <td><img src="assets/bilibili-qr.png" alt="哔哩哔哩" width="55"/></td>
</tr>
</table>

</div>

### 贾维斯 Agent 应用开发知识交流群

<div align="center">

扫码加入，和其他Agent应用伙伴一起交流 Agent 应用开发经验、插件及各种AI玩法。

<table>
<tr>
  <td align="center">微信群「Hello Jarvis.」</td>
  <td align="center">QQ 群「Hello Jarvis.」<br/>（群号 1109055256）</td>
  <td align="center">抖音群「Hello Jarvis.」<br/>（群号 777346526350）</td>
</tr>
<tr>
  <td><img src="assets/communication/discussion-group.png" alt="微信群 Hello Jarvis." width="150"/></td>
  <td><img src="assets/communication/discussion-group2.jpg" alt="QQ 群 Hello Jarvis." width="150"/></td>
  <td><img src="assets/communication/discussion-group3.jpg" alt="抖音群 Hello Jarvis." width="150"/></td>
</tr>
</table>

> 微信群二维码 7 天内有效，过期后可从 QQ 群获取最新入群方式；QQ 群与抖音群可直接在 App 内搜索群号加入。

</div>

### 赞赏支持

<div align="center">

如果 J.A.R.V.I.S. 对你有帮助，欢迎请我喝杯咖啡——你的支持是项目持续迭代的动力，🦀🦀。

<img src="assets/communication/thanks-for-the-tip.png" alt="微信赞赏码" width="200"/>

</div>
