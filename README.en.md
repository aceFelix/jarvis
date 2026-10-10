<div align="center">
  <img src="assets/jarvis-reactor-header.svg" alt="J.A.R.V.I.S." width="100%"/>
</div>

# J.A.R.V.I.S.

<div align="center">

<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg" alt="Python 3.11-3.14" /></a>
<a href="https://pypi.org/project/jarvis-agent/"><img src="https://img.shields.io/pypi/v/jarvis-agent?logo=python&logoColor=white" alt="PyPI version" /></a>
<a href="https://www.npmjs.com/package/@acefelix/jarvis"><img src="https://img.shields.io/npm/v/@acefelix/jarvis?logo=npm&logoColor=white" alt="npm version" /></a>
<a href="https://github.com/aceFelix/jarvis/actions"><img src="https://github.com/aceFelix/jarvis/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
<a href="https://github.com/aceFelix/jarvis-desktop"><img src="https://img.shields.io/badge/desktop-jarvis--desktop-4b6fdd.svg?logo=electron&logoColor=white" alt="jarvis-desktop" /></a>
<a href="https://www.deepseek.com"><img src="https://img.shields.io/badge/DeepSeek-API-4D6BFE.svg?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xMiAyTDIgN2wxMCA1IDEwLTV6TTIgMTdsMTAgNSAxMC01TTIgMTJsMTAgNSAxMC01Ii8+PC9zdmc+" alt="DeepSeek" /></a>
<a href="https://bailian.console.aliyun.com"><img src="https://img.shields.io/badge/DashScope-Bailian-FF6A00.svg?logo=alibabacloud&logoColor=white" alt="DashScope" /></a>
<a href="#disclaimer"><img src="https://img.shields.io/badge/status-Beta%20In%20Development-yellow.svg" alt="Status" /></a>
<a href="https://github.com/aceFelix/jarvis"><img src="https://img.shields.io/github/stars/aceFelix/jarvis?style=social" alt="GitHub stars" /></a>
<a href="https://www.marvel.com/characters/iron-man-tony-stark"><img src="https://img.shields.io/badge/In%20tribute%20to-Iron%20Man-B31B1B.svg" alt="In tribute to Iron Man" /></a>

</div>

<div align="center">

🌐 [简体中文](README.md) | English

</div>

> **J**ust **A** **R**ather **V**ery **I**ntelligent **S**ystem
>
> "At your service, sir."

**J.A.R.V.I.S. is a proactive AI butler that lives on your personal computer** — a tribute to JARVIS from *Iron Man*. It resides in your operating system and can see, listen, speak and act: conversation is just the entry point, **getting things done is the goal**. Say "submit the project report by next Friday" and it registers the deadline with tiered reminders; say "stand down" and it waits quietly, greeting you the next morning with a daily briefing: "Good morning, sir."

> North star: give everyone a computer butler that truly knows you, proactively takes care of things, and is always at your service. Not a chat box, not a voice assistant — it gets things done before you even ask.

---

## Why JARVIS?

| The pain today | JARVIS's answer |
|---|---|
| Many AI chat tools, but few can **actually operate your computer** | 100+ built-in tools: files, commands, browser, mouse/keyboard control, screenshot reading, camera, email — a ReAct loop plans and executes autonomously, GUI automation clicks and drags like a human |
| Cloud assistants **don't know your machine**, and your data isn't yours | Local-first: it lives in your OS, reads your files, drives your apps; memory, logs and configs stay in local `~/.jarvis`, keys go into the OS credential manager |
| Most assistants **forget you every session** | Three-layer memory: session persistence + long-term memory + profile distillation (auto-learns your habits) — it knows you better the longer you use it |

<div align="center">
  <img src="docs/assets/Jarvis_vs_Claude_Code_vs_OpenClaw.png" alt="JARVIS vs Claude Code vs OpenClaw ten-dimension capability comparison" width="640"/>
  <p><em>Measured ten-dimension comparison: JARVIS leads on voice interaction, computer control, visual perception and proactive service</em></p>
</div>

## Showcase

<div align="center">

**jarvis-desktop workbench** — three theme skins · three columns (left: console / center: chat / right: task center · usage · system status)

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/desktop-workbench0.png" width="100%"/><br/><sub>Fluorescent Green · task center & system status</sub></td>
  <td width="33%"><img src="assets/screenshots/desktop-workbench1.png" width="100%"/><br/><sub>Electric Blue · settings panel</sub></td>
  <td width="33%"><img src="assets/screenshots/desktop-workbench2.png" width="100%"/><br/><sub>Metal Silver · settings panel</sub></td>
</tr>
</table>

**Terminal REPL** — a conversation about memory: "Jarvis, how much do you know about me?" (skill loading → MCP knowledge graph → profile memory)

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/repl-chat0.png" width="100%"/><br/><sub>Boot: Arc Reactor + MCP/LSP ready</sub></td>
  <td width="33%"><img src="assets/screenshots/repl-chat1.png" width="100%"/><br/><sub>Thinking: planning multi-source lookup</sub></td>
  <td width="33%"><img src="assets/screenshots/repl-chat2.png" width="100%"/><br/><sub>LoadSkill: loading a skill pack</sub></td>
</tr>
<tr>
  <td width="33%"><img src="assets/screenshots/repl-chat3.png" width="100%"/><br/><sub>ToolSearch: discovering MCP tools</sub></td>
  <td width="33%"><img src="assets/screenshots/repl-chat4.png" width="100%"/><br/><sub>Querying the knowledge-graph profile</sub></td>
  <td width="33%"><img src="assets/screenshots/repl-chat5.png" width="100%"/><br/><sub>Thinking: consolidating profile data</sub></td>
</tr>
<tr>
  <td width="33%"><img src="assets/screenshots/repl-chat6.png" width="100%"/><br/><sub>Answer: three-layer memory, knows you better</sub></td>
  <td width="33%"><img src="assets/screenshots/model-manage.png" width="100%"/><br/><sub>/models management (11 vendors)</sub></td>
  <td width="33%"><img src="assets/screenshots/voice-manage.png" width="100%"/><br/><sub>/tts-voice management</sub></td>
</tr>
</table>

**Voice** — `/voice` turn-by-turn chat · `/talk` realtime duplex (speak to interrupt)

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/realtime-talk0.png" width="100%"/><br/><sub>/voice: listen → check time → gentle reminder → barge-in</sub></td>
  <td width="33%"><img src="assets/screenshots/realtime-talk1.png" width="100%"/><br/><sub>/talk: startup banner (server_vad + AEC)</sub></td>
  <td width="33%"><img src="assets/screenshots/realtime-talk2.png" width="100%"/><br/><sub>/talk: telling a programmer joke</sub></td>
</tr>
</table>

**Cross-device** — scan a QR code to take over the same session (/connect-phone)

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/phone-connect0.png" width="100%"/><br/><sub>Terminal QR + two-way message sync</sub></td>
  <td width="33%"><img src="assets/screenshots/phone-connect1.png" width="100%"/><br/><sub>Phone: visible thinking process</sub></td>
  <td width="33%"><img src="assets/screenshots/phone-connect2.png" width="100%"/><br/><sub>Phone: MCP tool calling for weather</sub></td>
</tr>
</table>

**WeChat ClawBot** — boss your butler from WeChat anytime (/connect-wechat)

<table>
<tr>
  <td width="33%"><img src="assets/screenshots/wechat-clawbot0.png" width="100%"/><br/><sub>Terminal QR pairing (24h valid)</sub></td>
  <td width="33%"><img src="assets/screenshots/wechat-clawbot1.png" width="100%"/><br/><sub>Chat right inside WeChat</sub></td>
  <td width="33%"><img src="assets/screenshots/wechat-clawbot2.png" width="100%"/><br/><sub>Full tool capabilities on the go</sub></td>
</tr>
</table>

<!-- proactive-briefing ("Sir, a reminder:") screenshot pending; append a row here once provided -->

</div>

## Core Capabilities

| Capability | In one sentence | Docs |
|---|---|---|
| 🖥️ **Terminal-native Agent** | Rich terminal REPL + 100+ tools + ReAct orchestration, Ctrl+C interrupts at any stage | [Concepts](docs/guide/concepts.md) · [Commands](docs/guide/commands.md) |
| 🎙️ **Listens & speaks** | `/voice` turn-by-turn voice chat, `/talk` full-duplex realtime (speak to interrupt), TTS announces proactive reminders | [Voice](docs/guide/voice.md) |
| 🖱️ **Operates your PC** | Mouse/keyboard/screenshot/window tools + visual template locating, drives any software like a human | [GUI Automation](docs/guide/ecosystem.md#gui-自动化) |
| 🗓️ **Proactive butler** | Daily briefing, timed reminders, deadline tracking, system monitoring — it acts without being asked | [Proactive System](docs/guide/desktop.md#主动提醒系统-p2-3已接线) |
| 🧠 **Three-layer memory** | Session persistence + long-term memory + profile distillation, knows you better over time | [Memory](docs/guide/concepts.md#记忆系统) |
| 📱 **One session, many ends** | jarvis-desktop shell / phone QR / WeChat ClawBot share the same session | [Desktop & Remote](docs/guide/desktop.md) |
| 🧩 **Open ecosystem** | MCP, plugin marketplace, Skill packs, CLI-Anything (turn any software into a tool), multi-agent collaboration | [Ecosystem](docs/guide/ecosystem.md) |
| 🔐 **Security baseline** | Five-layer permissions + four-level sandbox + operation audit + keyring encryption, hard-blocks dangerous ops | [Concepts](docs/guide/concepts.md#五层权限系统) |

**Provider freedom**: 11 vendors supported (Alibaba DashScope / DeepSeek / OpenAI / Zhipu / Anthropic / Kimi / MiniMax / SiliconFlow / Xiaomi MiMo / Google Gemini / custom-compatible services); double-click to edit in `/models`, hot-switch while running.

> 📖 The detailed guides under `docs/guide/` are currently written in Simplified Chinese; the architecture docs and code comments follow the same language.

## Quick Start

```bash
# Install (pick one, Python 3.11+)
pip install "jarvis-agent[all]"          # PyPI, full features
uv tool install "jarvis-agent[all]"      # uv (faster)
npm install -g @acefelix/jarvis          # npm (needs Node 18+ / Python 3.11+)

jarvis --init    # Interactive setup: pick vendor → confirm model → enter key → auto-test → save
jarvis           # Chat away: natural language directly, "/" for command palette, Tab to complete
```

- Defaults to Alibaba DashScope (`qwen3.7-plus`, multimodal); or just `export DASHSCOPE_API_KEY=sk-xxx` and launch
- All config lives in `~/.jarvis/` (settings.toml + models.toml); `/mode` switches permission mode, `/think` tunes deep thinking
- Something not working? `jarvis --doctor` diagnoses dependencies and config in one shot

> Install paths, optional dependency groups, per-OS system dependencies and the `--gui` workbench entry: see the [Installation Guide](docs/guide/installation.md) (Chinese).
> To grasp where this project is heading in one read: the [Blueprint](docs/BLUEPRINT.md).

## Platform Support

| | Windows | macOS | Linux |
|---|---|---|---|
| Full features (chat / voice / workbench / desktop shell / GUI control / autostart) | ✅ **Fully developed & verified** | ✅ Code-adapted | ✅ Code-adapted |

> ⚠️ All development and real-machine verification happens on Windows; macOS / Linux are code-level adaptations without full real-machine testing. Windows is recommended first. Per-feature differences: [Installation Guide](docs/guide/installation.md#平台系统依赖) (Chinese).

## Documentation Map

| Topic | Doc |
|---|---|
| **Start here**: the butler's own onboarding guide (persona-style) | [USER_GUIDE.md](USER_GUIDE.md) (中文) |
| Install / upgrade / platform deps | [docs/guide/installation.md](docs/guide/installation.md) (中文) |
| All commands / model management / deep thinking | [docs/guide/commands.md](docs/guide/commands.md) (中文) |
| Voice chat / realtime duplex / voices | [docs/guide/voice.md](docs/guide/voice.md) (中文) |
| Desktop shell / proactive reminders / phone / WeChat / serve protocol | [docs/guide/desktop.md](docs/guide/desktop.md) (中文) |
| Permissions / compaction / memory / sandbox / self-healing | [docs/guide/concepts.md](docs/guide/concepts.md) (中文) |
| Multi-agent / plugins / CLI-Anything / email | [docs/guide/ecosystem.md](docs/guide/ecosystem.md) (中文) |
| Config reference / vendor setup / FAQ | [config-docs/](config-docs/configuration.md) (中文) |
| Architecture (15 chapters) | [docs/architecture/](docs/architecture/00-索引.md) (中文) |
| Testing / CI / publishing / directory layout | [docs/guide/development.md](docs/guide/development.md) (中文) |
| Blueprint (vision · capability model · roadmap) | [docs/BLUEPRINT.md](docs/BLUEPRINT.md) |

## Development Roadmap

- [x] **Phase 1**: Minimum viable Agent (chat + files + commands + five-layer permissions)
- [x] **Phase 2**: Computer operation (GUI + multimodal vision + browser automation + camera)
- [x] **Phase 3**: Realtime voice (TTS + STT + `/voice` loop + `/talk` full-duplex)
- [x] **Phase 4**: Memory & ecosystem (session persistence / long-term memory / MCP / context compaction / Skill system)
- [x] **Phase 5**: The JARVIS form (proactive perception + subagents + proactive reminders + jarvis-desktop shell)
- [x] **Phase 6**: Cross-platform (Windows / macOS / Linux)
- [x] **Phase 7**: Realtime chat UI (Arc Reactor workbench + full-duplex interrupt + desktop app)

> Stages 8–11 (policy / perception / execution / experience) and the full capability list: [Blueprint](docs/BLUEPRINT.md); execution-level detail: [docs/roadmap/](docs/roadmap/).

## Disclaimer

J.A.R.V.I.S. is still in **development and validation**; features are not yet fully stable. If you hit problems or rough edges, feedback is welcome at **13985465782@163.com**. Every piece of feedback drives improvement — thanks for your support and tolerance!

## Thank You for Your Support

<div align="center">

**Thank you for using J.A.R.V.I.S.!**

**"J.A.R.V.I.S. — At your service, sir."**

</div>

<div align="center">

<table>
<tr>
  <td align="center">WeChat</td>
  <td align="center">X (Twitter)</td>
  <td align="center">Douyin</td>
  <td align="center">Bilibili</td>
</tr>
<tr>
  <td><img src="assets/wechat-qr.png" alt="WeChat" width="55"/></td>
  <td><img src="assets/x-qr.png" alt="X" width="55"/></td>
  <td><img src="assets/tiktok-qr.png" alt="Douyin" width="55"/></td>
  <td><img src="assets/bilibili-qr.png" alt="Bilibili" width="55"/></td>
</tr>
</table>

</div>
