<div align="center">
  <img src="https://raw.githubusercontent.com/aceFelix/jarvis/master/assets/jarvis-reactor-header.svg" alt="J.A.R.V.I.S." width="100%"/>
</div>

# J.A.R.V.I.S.

<div align="center">

<a href="https://github.com/aceFelix/jarvis/blob/master/LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg" alt="Python 3.11-3.14" /></a>
<a href="https://www.npmjs.com/package/@acefelix/jarvis"><img src="https://img.shields.io/npm/v/@acefelix/jarvis?logo=npm&logoColor=white" alt="npm version" /></a>
<a href="https://pypi.org/project/jarvis-agent/"><img src="https://img.shields.io/pypi/v/jarvis-agent?logo=python&logoColor=white" alt="PyPI version" /></a>
<a href="https://github.com/aceFelix/jarvis/actions"><img src="https://github.com/aceFelix/jarvis/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
<a href="https://github.com/aceFelix/jarvis"><img src="https://img.shields.io/github/stars/aceFelix/jarvis?style=social" alt="GitHub stars" /></a>

</div>

> **J**ust **A** **R**ather **V**ery **I**ntelligent **S**ystem
>
> 「随时为您效劳，先生。」

**J.A.R.V.I.S. 是一个主动陪伴你电脑的 AI 智能管家**——致敬《钢铁侠》里的贾维斯。它常驻你的操作系统，能看、能听、能说、能动手：对话只是入口，**把事情办了才是目的**。你说"下周五前交项目报告"，它登记截止日期并提前提醒你；你说"退下"，它安静待机，第二天早上用一句"先生，早上好"向你播报今日简报。

> 不是聊天框，不是语音助手——你还没开口，它已经把事情办了。

---

## 安装

```bash
npm install -g @acefelix/jarvis
```

安装完成后即可运行：

```bash
jarvis
```

**环境要求**：Node.js 18+ 与 Python 3.11+（Jarvis 核心为 Python 实现，npm 包负责自动装好它）。
安装时会弹出**功能选装菜单**，可选装语音、GUI 操控、浏览器自动化、摄像头、MCP 等能力；
直接回车即全装，输入序号用逗号分隔（如 `1,3,5`）按需安装，`0` 只装核心。

> 也可以用 Python 直接安装：`pip install "jarvis-agent[all]"` 或 `uv tool install "jarvis-agent[all]"`（更快）。
> 详细安装方式与各平台系统依赖见 [安装指南](https://github.com/aceFelix/jarvis/blob/master/docs/guide/installation.md)。

## 快速开始

```bash
jarvis --init    # 交互式配置：选厂商 → 确认模型 → 输 Key → 自动测试连接 → 保存
jarvis           # 开聊：自然语言直接说，输入 "/" 弹命令面板，Tab 补全
```

- 默认接阿里云 DashScope（`qwen3.7-plus` 多模态），也可 `export DASHSCOPE_API_KEY=sk-xxx` 后直接启动
- 所有配置在 `~/.jarvis/`（settings.toml + models.toml），`/mode` 切换权限模式，`/think` 调深度思考
- 遇到功能不可用？`jarvis --doctor` 一键诊断依赖与配置

## 为什么需要 JARVIS？

| 现状痛点 | JARVIS 的回答 |
|---|---|
| AI 聊天工具很多，但**没几个能真正替你操作电脑** | 100+ 内置工具：文件、命令、浏览器、键鼠操控、截屏识图、摄像头、邮件——ReAct 循环自主规划执行，GUI 自动化像人一样点按拖拽 |
| 云端助手**不认识你的电脑**，数据也不在你手里 | 本地优先：常驻你的系统、读你的文件、操控你的软件；记忆、日志、配置全在本地 `~/.jarvis`，密钥进系统凭据管理器 |
| 大多数助手**每次都像初见**，交代过的背景全忘 | 三层记忆：会话存盘 + 长期记忆 + 画像记忆（自动提炼你的习惯偏好），越用越懂你 |

## 核心能力

| 能力 | 一句话 |
|---|---|
| 🖥️ **终端原生 Agent** | Rich 终端 REPL + 100+ 工具 + ReAct 编排，Ctrl+C 任意阶段打断 |
| 🎙️ **能听会说** | `/voice` 逐轮语音对话，`/talk` 全双工实时聊天（说话即打断），TTS 播报主动提醒 |
| 🖱️ **操控电脑** | 鼠标/键盘/截屏/窗口管理 + 视觉模板定位，像人一样操作任意软件 |
| 🗓️ **主动管家** | 每日简报、定时提醒、截止日期追踪、系统监控——不用叫，它自己看着办 |
| 🧠 **三层记忆** | 会话存盘 + 长期记忆 + 画像提炼，越用越懂你 |
| 📱 **多端同会话** | jarvis-desktop 桌面壳 / 手机扫码 / 微信 ClawBot，三端共享同一会话 |
| 🧩 **开放生态** | MCP 接入、插件市场、Skill 技能包、CLI-Anything 把任意软件变工具、多 Agent 协作 |
| 🔐 **安全底线** | 五层权限 + 四级沙箱 + 操作审计 + keyring 加密存储，危险操作硬阻断 |

**接入自由**：支持 11 家厂商（阿里云 DashScope / DeepSeek / OpenAI / 智谱 / Anthropic / Kimi / MiniMax / SiliconFlow / 小米 MiMo / Google Gemini / 自定义兼容服务），`/models` 双击即改、运行中热切换。

## 效果展示

<div align="center">

<img src="https://raw.githubusercontent.com/aceFelix/jarvis/master/assets/screenshots/desktop-workbench0.png" alt="jarvis-desktop 工作台" width="640"/>

<img src="https://raw.githubusercontent.com/aceFelix/jarvis/master/assets/screenshots/repl-chat0.png" alt="终端 REPL" width="640"/>

<em>桌面工作台与终端 REPL · 更多截图见 GitHub 仓库</em>

</div>

## 了解更多

- 📦 项目主页与完整文档：<https://github.com/aceFelix/jarvis>
- 🚀 新手上手指南：[USER_GUIDE.md](https://github.com/aceFelix/jarvis/blob/master/USER_GUIDE.md)
- 🖥️ 桌面应用：[jarvis-desktop](https://github.com/aceFelix/jarvis-desktop)

> 本 npm 包是 J.A.R.V.I.S 的官方安装入口，安装时会自动从 PyPI 拉取对应的 Python 核心包
> `jarvis-agent` 并暴露 `jarvis` 命令；核心代码与文档均在上方 GitHub 仓库中开源。

## 反馈

J.A.R.V.I.S. 仍处于**开发与验证阶段**，使用中如遇问题欢迎反馈：**13985465782@163.com**。

## 许可证

MIT © aceFelix
