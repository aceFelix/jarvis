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
| AI 聊天工具很多，但**没几个能真正替你操作电脑** | 70+ 内置工具（接入 MCP / harness 可扩展至 100+）：文件、命令、浏览器、键鼠操控、截屏识图、摄像头、邮件——ReAct 循环自主规划执行，GUI 自动化像人一样点按拖拽 |
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

**语音交互** — `/voice` 逐轮对话（空格打断、10 秒无话自动待机） · `/talk` 实时双工（说话即打断）

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
| 🖥️ **终端原生 Agent** | Rich 终端 REPL + 70+ 工具（可扩展至 100+）+ ReAct 编排，Ctrl+C 任意阶段打断 | [核心机制](docs/guide/concepts.md) · [命令参考](docs/guide/commands.md) |
| 🎙️ **能听会说** | `/voice` 逐轮语音对话（空格打断、10 秒无话自动待机等唤醒），`/talk` 全双工实时聊天（说话即打断）；TTS 播报主动提醒 | [语音系统](docs/guide/voice.md) |
| 🖱️ **操控电脑** | 鼠标/键盘/截屏/窗口管理 + 视觉模板定位，像人一样操作任意软件 | [GUI 自动化](docs/guide/ecosystem.md#gui-自动化) |
| 👁️ **看得见它在干什么** | 终端逐条打出操作时间线（工具/目标/耗时/失败原因），被操作的窗口自动置顶可见但**不抢键鼠**；**任何工具开新窗都会跟随**（通用观察器，不按工具名登记）；任务收尾自动关闭中间产物窗口（最终结果、前台与未保存窗口保留）；`/trace` 回看最近一轮，轨迹只记路径与窗口标题、不记文件内容 | [操作可见性](docs/plans/operation-visibility.md) |
| 🛰️ **任务驾驶舱 + 网页抓取** | 干活时弹出一块置顶面板（纯闲聊不弹，点 ✕ 只隐藏），一眼看清做到第几步、哪一步失败、花了多久，点任意一步可展开细节；每个运行中的工具、每个子代理各有一张小卡片。最左边另有「爬虫驾驶舱」：抓过的网页按层级列出、抽到的字段排成表、页面关系画成图，能拖出来单独放大看。`Crawl` 会按顺序翻页（自己限页数、深度与同域，遵守 robots.txt），遇到 JS 空壳页自动改用浏览器渲染，反爬提示只如实报告、不绕过。全部可视化都能用 `[visualization]` 总开关关掉 | [驾驶舱方案](docs/plans/terminal-cockpit.md) · [多任务浮窗](docs/plans/cockpit-multicards.md) · [子代理卡](docs/plans/agent-cards.md) |
| 🖼️ **图片驾驶舱** | 截图、拍照、网页截图会同时显示在一块独立图片驾驶舱里（默认停屏幕左上角、可拖动，位置记住）：大图看原图、缩略条翻历史、元信息带来源/尺寸/路径。终端此前只能把图送给模型、人却看不到——这个窗口补上「给人看」那一半，模型说「我看了截图」时可以当场核对它看的是不是同一张。它**不改变**送给模型的图片，也不改变模型侧回执口径（附上 ≠ 看见） | [图片驾驶舱方案](docs/plans/image-window.md) |
| 🗓️ **主动管家** | 每日简报、定时提醒（桌面与终端 REPL 都能对话安排）、截止日期追踪、系统监控——不用叫，它自己看着办 | [主动提醒系统](docs/guide/desktop.md#主动提醒系统-p2-3已接线) |
| 👀 **工作现场感知** | 前台窗口 / 目录堆积 / 仓库状况 / 重要来信 / 摄像头在场 / 日程临近 / 系统资源七类事件进统一总线，经打扰预算后只在该说话时开口（**逐源默认关**，从未表过态时连接后告知一次，采了什么可导出自查） | [感知源](docs/guide/desktop.md#感知源阶段-9--w2已接线) |
| 🛎️ **管家服务** | jarvis-desktop 标题栏入口：邮箱服务（查看/修改当前邮箱 + 配置 SMTP + 发送邮件）直连后端工具（后续可扩展更多服务） | [桌面与多端](docs/guide/desktop.md) |
| 🧰 **扩展服务** | jarvis-desktop 标题栏入口：右栏面板集中管理 MCP 服务（增删改+启停）、内置工具、技能、插件与 harness 工具（市场搜索/安装/卸载/启停） | [桌面与多端](docs/guide/desktop.md) |
| 🧑‍🤝‍🧑 **代理团队** | jarvis-desktop 右栏新区块：复杂任务自动拆给子代理/队友并行做，实时显示每个代理的**状态与当前动作**，可单独停止、可给队友追加指令；终端 /task /agents 同源 | [多 Agent 协作](docs/guide/ecosystem.md#多-agent-协作) |
| 🧭 **先规划后执行** | 模型可自主进入规划模式（只读调研 → 提交方案 → 恢复写权限）：方案落盘 `.jarvis/PLAN.md`，桌面以系统消息显示摘要与预览；也可在输入区下拉手动选「规划」 | [桌面指南 · 规划模式](docs/guide/desktop.md#规划模式桌面2026-11) |
| 🧠 **三层记忆** | 会话存盘（逐轮即时落盘，`/sessions` 与 `/load` 会显示**创建/更新时间**）+ 长期记忆 + 画像提炼，越用越懂你；长期记忆在桌面「设置 → 长期记忆」里**看得见、改得动**，也可以直接对它说「记住…」让模型自己写 | [记忆系统](docs/guide/concepts.md#记忆系统) |
| 📱 **多端同会话** | jarvis-desktop 桌面壳 / 手机扫码 / 微信 ClawBot，三端共享同一会话 | [桌面与多端](docs/guide/desktop.md) |
| 🧭 **管家运行时层** | permissions.yaml 路径级免确认 + 授权租约免打断 + 打扰预算削废话 + /away 无人值守待批清单（静默时段里你亲口设的提醒改为**上屏不出声**，可配同时发邮件） | [W1 运行时层](docs/architecture/16-W1判断层.md) |
| 🧾 **敢放手的一层** | 发出去的邮件先进 30 秒待发窗口（`/undo cancel` 可拦，窗口未结案期间 `curl` 这类外发命令也会临时改问人），**替你改的文件在写入前自动留底，`/undo` 一条命令还原文（只动改过的那几个文件，不影响同批其它改动）**；"替我盯着这件事，条件到了再往下做"有跨会话任务脊柱承载，**一句话就能建目标**（`/tasks add …` 或对贾维斯说，等待期间一次 LLM 都不调） | [W3 兑现层](docs/architecture/18-W3兑现层.md) |
| ⚙️ **配置不用翻文件** | 207 个配置项全能问：不知道某项干什么、在哪改，直接说「CPU 阈值太高了」——它查完告诉你这段在干什么、现在是多少、**要我改还是你自己改**（62 项能代改：29 项直接改、33 项改前先确认；其余 144 项只给文件与段名）；`/config explain <关键词>` 同口径可自查。撞到只能手改的项会说清「这项**存在**，只是我不能代改」，不再含糊成「不认识」 | [配置系统](docs/architecture/12-配置系统.md) |
| 🧩 **开放生态** | MCP 接入、插件市场、Skill 技能包、CLI-Anything 把任意软件变工具、多 Agent 协作 | [扩展生态](docs/guide/ecosystem.md) |
| 🧹 **清理 / 查毒 / 体检** | `/disk` 出可回收清单且能回答**“空间到底被谁占了”**（`--top` 目录体积排行）；`/clean` 走**隔离区**（移走而非复制，随时 `/clean --restore` 还原），其中**崩溃转储与错误上报队列删了完全无感，开了即可无人值守自动清**（`/clean --auto-status` 看名单，`/clean --auto` 立即跑）；`/health` 七区块体检（资源/磁盘/安全/进程/启动项/电源/温度）。杀软判定看 `SecurityCenter2` 而不只是 Defender 自身字段——**装了第三方杀软不会被误报“没有实时防护”**；非管理员读不到的区块**如实标注需提权**，不显 0 | [系统维护层](docs/architecture/19-系统维护层.md) |
| 🩺 **知道自己缺什么** | `/health caps` 把 13 项能力实测自查（包 / 二进制 / **浏览器内核** / 凭据；`--deep` 真起一次浏览器、真截一帧屏拿证据）。缺件的工具**在调用前就拦住**并说清缺哪一条、实测依据、怎么补，回执里硬写「本次调用没有执行，禁止当成已验证」；`/repair` 让它自己补回缺件（四个白名单动作 + 六道门：超 200MB 就算 yolo 也要问、`/away` 一律升回待批、**修完必须复测才算成**）；模型无视觉时提示词改给替代手段降级链，截图回执只说「附上」不说「看见」 | [能力自检层](docs/architecture/20-能力自检与自我修复.md) |
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
- 遇到功能不可用？`jarvis --doctor` 一键诊断依赖与配置；想知道它现在**到底能干什么、缺什么**，开聊后打 `/health caps`（加 `--deep` 让它真起一次浏览器给你看证据），缺的内核可以 `/repair` 让它自己装回来
- 只装了核心依赖时，GUI / 浏览器 / 摄像头 / 语音这些可选能力按 8 个 extras 组安装（`gui` `browser` `mcp` `camera` `vision` `voice` `daemon` `wechat`）：`pip install "jarvis-agent[browser,gui,voice]"`，全部分组见 [安装指南](docs/guide/installation.md)；**playwright 的浏览器内核不在 pip 依赖里**，装完包还要 `python -m playwright install chromium`，或直接让 `/health caps browser.selfcheck --repair` 代跑
- 启动后台检查新版本，发现更新提示 `/update` 一键升级（`--no-update-check` 可关闭）
- 本仓库在 [GitCode](https://gitcode.com/acefelix/jarvis) 与 [GitHub](https://github.com/aceFelix/jarvis) 双平台同步托管，每次提交两边一并推送

> 安装路径、可选依赖组、各平台系统依赖、`--serve` 后端入口等详见 [安装指南](docs/guide/installation.md)。
> 想一句话理解这个项目要去哪，读 [蓝图文档](docs/blueprint/BLUEPRINT.md)。

## 平台支持

> 图例：✅ 已在该系统实机验证 ｜ 🟡 代码层适配、依赖到位即可用但未在该系统跑完整实机 ｜ ⚠️ 部分能力可用（已注明受限项）｜ ⛔ 不支持 / 暂未提供

| 能力 | Windows | macOS | Linux |
|---|:---:|:---:|:---:|
| 对话 + ReAct 工具编排 + 三层记忆 | ✅ | 🟡 | 🟡 |
| 文件 / 命令 / Web / 邮件 内置工具 | ✅ | 🟡 | 🟡 |
| 浏览器自动化（Playwright） | ✅ | 🟡 | 🟡 |
| GUI 自动化（键鼠 / 截屏 / 窗口） | ✅ | 🟡 | ⚠️ 窗口管理受限（pygetwindow） |
| 摄像头拍照 / 视觉手势（mediapipe） | ✅ | 🟡 | 🟡 |
| 实时语音 `/voice` · `/talk`（STT+TTS+AEC） | ✅ | 🟡 需 portaudio | 🟡 需 `portaudio19-dev`（三平台均需 DashScope Key） |
| 主动管家（简报 / 提醒 / 截止日期 / 系统监控） | ✅ | 🟡 | 🟡 |
| W2 感知层事件总线 | ✅ | 🟡 | 🟡 |
| 前台窗口感知源 | ✅ | 🟡 取不到进程名 | ⚠️ 依赖 pygetwindow，受限 |
| 日历源（日程临近提醒） | ✅ Outlook COM / ICS | 🟡 仅 ICS（无 Outlook COM） | 🟡 仅 ICS |
| 安全沙箱执行 | ✅ Job Object | 🟡 sandbox-exec | 🟡 rlimits |
| MCP / Skill / Plugin / harness / 多 Agent / serve 后端 | ✅ | 🟡 | 🟡 |
| 多端同会话（手机 PWA / 微信 ClawBot） | ✅ | 🟡 | 🟡 |
| 能力自检 `/health caps` · 自我修复 `/repair` | ✅ | 🟡 未跑完整实机 | 🟡 未跑完整实机（截屏冒烟依赖 X） |
| 系统维护 `/disk` `/clean` `/health` | ✅ | ⛔ 仅 Windows（PowerShell + SecurityCenter2 + 盘符 + cleanmgr） | ⛔ 仅 Windows |
| jarvis-desktop 桌面壳安装包 | ✅ NSIS `.exe` | ⛔ 暂未提供 | ⛔ 暂未提供 |

> ⚠️ 本项目在 **Windows** 上完成全部开发与实机验证；macOS / Linux 为代码层适配、未跑完整实机，建议优先 Windows。**系统维护层（清理/查毒/体检）与桌面壳安装包目前仅 Windows**；上表逐项受限/不支持处已标出，依赖细节见 [安装指南](docs/guide/installation.md#平台系统依赖)。“仅 Windows、POSIX 如实拒绝”不只是文字声明：CI（Linux）会逐条对账内置清理规则库，验证它在 POSIX 上确实只拒掉那些 Windows 语义路径、其余校验闸照旧生效（`tests/test_release_contract.py`）。

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
| 清理磁盘 / 查毒 / 性能体检（`/disk` `/clean` `/health`） | [docs/architecture/19-系统维护层.md](docs/architecture/19-系统维护层.md) |
| 能力自查 / 缺件拦截 / 自我修复（`/health caps` `/repair`） | [docs/architecture/20-能力自检与自我修复.md](docs/architecture/20-能力自检与自我修复.md) |
| 让它自己改配置（说了就改 / 能改什么 / 为什么要确认） | [docs/architecture/12-配置系统.md](docs/architecture/12-配置系统.md) |
| 多 Agent / 插件 / CLI-Anything / 邮件 | [docs/guide/ecosystem.md](docs/guide/ecosystem.md) |
| 配置项 / 厂商接入 / 常见问题 | [config-docs/](config-docs/configuration.md) |
| 架构设计（20 篇） | [docs/architecture/](docs/architecture/00-索引.md) |
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
- [x] **阶段 10**：W3 兑现层（副作用账本 + 不可逆动作延时窗口 + `/undo`；跨会话任务脊柱 + 后台驱动器 + `/tasks`）——已随 v2.3.0 发布
- [x] **阶段 11**：感知层收编（休眠的 `CalendarSource` / `SystemMonitor` 接进事件总线）——已随 v2.3.0 发布
- [x] **阶段 12**：系统维护切片（清理 / 查毒 / 性能诊断）——已随 v2.3.2 发布，设计与实测数据见 [19-系统维护层](docs/architecture/19-系统维护层.md)
- [x] **阶段 13**：能力自检与自我修复（13 项能力实测 + 调用前能力门 + `/health caps` + `/repair`，另补模型能力面三值化）——已随 v2.4.0 发布，设计与取舍见 [20-能力自检与自我修复](docs/architecture/20-能力自检与自我修复.md)，逐段实做记录见 [capability-selfcheck.md](docs/plans/capability-selfcheck.md)

> 阶段 8–11（运行时层 / 感知层 / 兑现层 / 体验补齐）与全部能力条目见 [蓝图文档](docs/blueprint/BLUEPRINT.md)，执行级细节见 [docs/roadmap/](docs/roadmap/)。
> 阶段 12 / 13 的**编号口径**（本文与蓝图曾各指一件事）见蓝图 §9.4 说明。

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
