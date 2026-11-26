# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

> 本日志已按版本系列分册：主文件只保留 **[Unreleased]** 与**当前 2.6.x 系列**；
> 某系列封版后整体移入 `changelog/`，主文件只留索引。

## 历史版本索引

| 系列 | 收录版本 | 位置 |
|---|---|---|
| 2.6.x | 2.6.0 — 2.6.2 | 本文文件 |
| 2.5.x | 2.5.0 – 2.5.4 | [changelog/v2.5.md](changelog/v2.5.md) |
| 2.4.x | 2.4.0 | [changelog/v2.4.md](changelog/v2.4.md) |
| 2.3.x | 2.3.0 – 2.3.5 | [changelog/v2.3.md](changelog/v2.3.md) |
| 2.2.x | 2.2.5 | [changelog/v2.2.md](changelog/v2.2.md) |

更早的版本记录见 GitHub Releases。

## [Unreleased]

### 变更（/talk 人设改为星期五，2026-11-26）

- `/talk` 实时语音的默认人设由「贾维斯」改为**星期五（Friday）**——贾维斯之外的
  第二位智能管家，被问身份时自称星期五；`builtin` 与 `all` 两种工具模式的身份段
  逐字一致（切工具面不换人格），终端与桌面/serve 路径共用 `default_instructions()`。
- 人设仍硬编码在 `agent/voice/realtime_talk.py`，**无配置项可改**；`/voice` 与文本模式
  的人设/画像注入（`agent/prompts/system.py`）不受影响，`/voice` 唤醒词默认仍是贾维斯。
- 新增 `tests/voice/test_talk_persona.py`（9 例）：身份段一致、回退生效、显式 instructions
  优先、退出意图门与「先告别再退出」话术不丢。

## [2.6.2] — 2026-11-26

这一版把语音交互补齐成「能被打断、会好好告别」：语音厂商解耦为注册表、`/talk` 默认
全双工并实现真打断（停流再启 + 音频闸门）、`/voice` 开口即停（AEC + 本地 VAD），
说「退下」时先播一句告别再退出；同时修掉无 pyaudio 环境下打断看守静默失效的缺陷。

### 变更（语音厂商解耦阶段一，2026-11-24）

- 新增 `agent/voice/voice_providers.py` 厂商注册表与 `[voice] vendor`（默认 `dashscope`）：
  `/talk` 的端点/音色/模型、`/voice` 的识别后端改为查表；DashScope 取值原样搬运，
  行为不变；未知厂商与 `verified=false` 条目 fail-closed，不静默回落。
- `/listen` 改用统一 `voice_key` 解析（原先直接用主 LLM key，换成非 DashScope 模型时会拿错 key）。
- 同轮同步配置问答口径：262 项可问，72 项可代改（29 直接改 / 43 先确认）。

### 变更（实时语音默认全双工，2026-11-24）

- `/talk` 默认改为全双工：AI 说话期间麦克风照常上传（经 AEC 处理），开口即打断；
  外放回声大导致 AI 被自己打断时，把 `[realtime_talk] half_duplex = true` 切回半双工轮替。
- `realtime_half_duplex` 进配置真源表：可 `/config set realtime_half_duplex true|false`。

### 修复（实时语音插话被回声保护吞掉，2026-11-24）

- 有 AEC 时不再用 RMS 门槛过滤 `speech_started`：按官方文档一律停播/切轮；仅 AEC
  不可用时保留回声保护（旧实现会在 AEC 已消回声时仍把轻声插话当回声忽略）。
- 连续 `turn_detected` 取消的提示做 20 秒节流：首条展开事件链，其余只给一行短提示
  （事件链仍完整落盘 `diag.log`）。

### 变更（实时语音真打断：停流再启，2026-11-25）

- 终端 `/talk` 正常播放恢复直写（与改造前一致，保证连续）；`audio.delta` 按 40ms
  分片写、每片前检查打断代数，打断后剩余分片直接丢弃。
- 打断时 `_TerminalSpeaker.abort()` 只做 `stop_stream/start_stream`，**绝不 close**——
  实机 `close()+open()` 与录音线程并发直接 Segmentation fault（PortAudio 非线程安全）。
- 本地 VAD 前置：门槛 0.08、首帧出声满 500ms、连续 2 帧才触发；AEC 不可用或半双工
  时不启用；AEC 参考信号回到 recv 入队时喂。
- `/talk` 启动增加 `voice_state` 麦克风互斥：两个 `/talk`、或 `/talk` 与 `/voice`
  不能同时开麦（实机双引擎互相打断的根因）。
- 修复打断仍失效与误退出：本地 VAD 门槛 0.08 → 0.04（0.08 收不到“停一下”），
  `local vad sample` 改为不论高低每 2s 采样；`end_conversation` 增加退出意图门
  （最近一轮转写必须含退下/结束/再见等，否则拒绝执行），说“停一下”不再退出语音。
- 修复“叫他停还是把整段说完”：打断时置 `_discard_audio` 闸门，同一轮后续到达的
  音频帧一律不播（这些帧携带新代数，单靠 gen 检查拦不住），直到下一轮
  `response.created` 才解闸。
- 退出体验：说“退下/再见”等结束语时，Jarvis 先播一句告别再退出；工具调用轮
  不算告别，`_end_response` 等下一条非工具回复播完才结束会话（宽限 8s）。
- /voice 语音打断默认开：新增 voice_barge.VoiceBargeWatcher（AEC+本地 VAD、
  单实例 PyAudio、不 terminate），播报/推理中开口即停；TTS 播放 PCM 作 AEC 参考；
  移除死代码 _VoiceBargeInDetector，旧 _BargeInWatcher 改单实例不 terminate；
  interrupt_words 关键词打断由开口打断取代（字段保留兼容）。
- **回退**：首版「有界播放队列 + 独立 writer」实机导致断续/混乱（`underrun #1/#20/#100`、
  双引擎 `local vad level=0.500`），已删除 `agent/voice/realtime_playback.py` 及对应单测。
  设计与教训见 `docs/plans/voice/realtime-barge-in.md`。

### 修复（无 pyaudio 环境打断看守静默失效，2026-11-25）

- `VoiceBargeWatcher._run()` 为取 `paInt16` 而 `import pyaudio`，在未安装 pyaudio 的 CI
  runner 上抛 ImportError 被外层 except 吞掉，看守从不启动、语音打断静默失效（本地装了
  pyaudio 所以不复现）；改为模块级 `_PA_INT16 = getattr(pyaudio, "paInt16", 8)`。
- 新增 `tests/voice/test_voice_barge.py::test_runs_without_pyaudio_module`：把
  `sys.modules["pyaudio"]` 置 None 后 reload，断言常量兜底为 8 且看守仍能触发，锁死这类
  「本地绿、CI 红」问题。
- 配置口径：`[voice] barge_in` 代码默认改为 `true`，但用户/项目 `settings.toml` 里显式
  写 `barge_in = false` 仍会覆盖默认（实测「打断不生效」的真正原因），需手动改为 `true`。

## [2.6.1] — 2026-11-20

这一版集中修“目录不对就无法使用”的一类问题：从系统目录/只读目录启动不再崩溃或卡死，
并引入默认工作区；同时补齐 cmd 下的终端降级、人设卡失败的可观测性，以及 npm 发布
鉴权配置。

### 修复（系统目录启动崩溃与首轮卡死，2026-11-20）

- **检查点**：workdir 命中系统目录/盘根时禁用文件回滚并首屏提示；`git` 子进程改用
  Popen + 进程树终止，超时真正生效；超时后本会话停用；REPL 改为后台建卡，不再阻塞
  发送（`--workdir` 与普通项目目录不受影响）。
- **会话记忆**：workdir 不可写时回退到 `~/.jarvis/memory/projects/<名-哈希>`，启动
  不再 traceback；两处都不可用才按“无记忆”处理。
- **默认工作区**：显式指定 > 安全 cwd > `~/.jarvis/workspace`（自动创建）；修复
  `workdir` 漏在 TOML 白名单外导致配置写了不生效。
- 复盘：`docs/fixlogs/checkpoint-systemdir-block-fix.md`、
  `docs/fixlogs/default-workspace-fallback-fix.md`、
  `docs/fixlogs/workdir-unwritable-session-memory-crash-fix.md`。

### 修复（终端展示与可观测性，2026-11-20）

- 命令输出复用 REPL 的 120 列封顶 Console（修掉 `_global_console` 幽灵引用）；
- legacy 控制台（cmd）改用 ASCII 表格/面板边框与 `[!]/[x]/[?]/[提醒]` 标记；
- 人设卡失败分支带具体原因，失败后 10 分钟退避，不再每次退出重复告警；
- `jarvis --init` 生成全注释的 `settings.toml` 骨架并打印配置指引。
- 复盘：`docs/fixlogs/terminal-ui-cmd-render-and-mintty-resize-fix.md`、
  `docs/fixlogs/profile-persona-build-silent-failure-fix.md`、
  `docs/fixlogs/init-settings-toml-absent-fix.md`。

### 修复（npm 发布鉴权配置，2026-11-19）

- 2.6.0 的 npm 发布 404 根因是 `NPM_TOKEN` 失效（registry 上包与 scope 均正常，
  2.5.4 仍在）；补发走 Actions 的 **Re-run failed jobs** 即可，tag 固化的是旧 workflow。
- 删除 `publish-npm` 步骤里冗余的 `~/.npmrc` 写入：认证由 setup-node(`registry-url`)
  生成的 `NPM_CONFIG_USERCONFIG` 临时文件负责，写默认路径既不生效又误导排查。
- `npm/package.json` 的 `repository.url` 补 `git+` 前缀，消除 `npm pkg fix` 警告。

### 修复（CI 跨平台断言，2026-11-20）

- Windows 路径判定用例在 Linux CI 上跳过；盘根判定同时覆盖 POSIX `/`；
- 会话记忆回退语义同步既有用例（两处都不可写才返回 None）。

## [2.6.0] — 2026-11-19

这一版把画像记忆从“能记事实”推进到“一直懂我”：提炼触发收敛为每 7 条 + 终态、
衰减改为只降权不删除、补上事件轨与按需检索，并新增常驻人设卡；同时修掉清空画像
时旧人设卡与事件轨残留的缺陷。驾驶舱的「耗时」也拆成模型与工具两段。

### 修复（/memory clear 未清人设卡与事件轨，2026-11-19）

- 代码核对发现：`/memory clear yes` 只清 `profile.json`；旧 `persona.json` 仍被注入，
  `/memory events` 仍能看到“已清空”的旧行程。更糟的是空库会让 `build_persona` 返回 None、
  `startup_check_async` 对空库直接返回，旧卡**永久残留**且无法用 rebuild 覆盖。
- 新增 `profile_persona.discard()`：删除卡片与 pending，并刷新注入缓存；重建线程正在跑时
  拒绝删除并如实提示（删了也会被 worker 写回）。
- 新增 `profile_events.clear_events()`：整轨清空并返回删除条数；与 append 共用进程锁与
  跨进程文件锁，文件不存在 / IO 失败静默返回 0。
- `/memory clear yes` 一并调用两者，输出“已丢弃常驻人设卡 / 已清空事件轨（N 条）”。
- 测试：discard 3 例、clear_events 3 例、命令级 clear 断言扩展；全量 5383 passed。
- 复盘见 `docs/fixlogs/session-memory/profile-clear-orphan-persona-fix.md`。

### 新增（P6：常驻人设卡 + 全量分页 + 跨记忆边界，2026-11-18）

- **常驻人设卡**：新增 `agent/core/memory/profile_persona.py`，把全量条目蒸馏成
  600–800 tokens 的“他是谁”（固定 7 节）；`persona.json` + `persona_state.json`；
  每条 bullet 必须带 `sources` 接地，查不到来源即丢弃；超限按 current_focus→…→identity 裁剪。
- **只在退出重建**：正常退出（REPL / 桌面 `_shutdown`）在提炼后重建，最多等 15 秒；
  异常退出靠 `pending` + 下次启动补偿；新建/切会话不重建，会话内靠 L1 最近 5 条变化；
  `/memory persona [rebuild]` 查看或手动重建；首次无卡且有条目时启动首建一次。
- **注入**：有卡时注入人设卡 + L1 最近变化，无卡回退 P1c 核心注入；KG 画像存在时不重复注入；
  提示词声明“画像为自动观察，冲突时以长期记忆为准”。
- **全量浏览**：`RecallProfile` 新增 `mode="list"`（按类别+权重分页，20/页、最多 50，
  含休眠标记与分类统计），回答“你了解我多少”时可翻完全库。
- **跨记忆边界（P6a）**：`MemoryUpdate` 收紧为“仅用户显式要求记住时使用”；
  提炼 prompt 遇“记住/以后都”不再记成画像；写入前与 MEMORY.md 条目比对
  （≥0.85 跳过并计 `memory_md_skip`，0.6–0.85 计 `conflict` 但照写）；
  **画像永不改写 MEMORY.md**；仲裁顺序 MEMORY.md > KG 画像 > 本地画像/人设卡 > 会话记忆。
- 配置：`profile_persona_enabled=true`、`profile_persona_token_limit=800`；两份模板同步；
  `Settings` 261 字段。
- 测试：人设卡 16 例、golden 4 例、list 7 例、跨记忆 6 例；全量 5383 passed。
- 文档：`research/always-on-profile-memory.md`、`plans/profile-memory/profile-memory-p6-persona-impl.md`、
  09 架构、concepts、commands、README、plans 索引。

### 变更（驾驶舱耗时拆成模型/工具，2026-11-18）

- **HUD 与终端的「耗时」拆成两段**：原先只写「耗时 ELAPSED 7.6s」，而执行序列里两条
  命令是 467ms + 151ms，用户拿两者对比会以为统计在造假——实际是两个口径：总耗时含
  等模型往返与逐字生成，分步明细只有工具本身。
- `run_end` 新增 `tool_ms`：工具占用的墙钟**区间并集**（并行工具不重复计），与
  `duration_ms` 同口径可直接相减；HUD 完成带显示 `7.6s（模型 7.0s · 工具 0.6s）`，
  终端完成行同源。旧 trace 文件没有该字段时保持原句，不伪造 `模型 0ms`。
- 测试：区间并集 4 组（串行/重叠/包含/乱序零长）、`run_end` 必带 `tool_ms`、空闲
  不计入工具段、拆分与 clamp、终端渲染 3 例。复盘见
  [cockpit-elapsed-breakdown-fix.md](../fixlogs/ui/cockpit-elapsed-breakdown-fix.md)。

### 变更（提炼触发精简：去掉空闲触发，改为“每 7 条 + 终态”，2026-11-17）

- **空闲触发整体下线**（`profile_idle.py` 删除、`profile_refine_idle_minutes` 配置移除）：
  它只增加一条计时器路径与一个配置键，而退出/切会话已经覆盖主要尾巴。
- **默认每 7 条新消息提炼一次**（`profile_refine_min_delta` 6 → **7**）。
- **成功不再按时间节流**：水位 + 条数已限住频率；`profile_refine_interval` 仅用于**失败退避**，
  避免 provider 报错时每轮重试。
- **退出真正收干**：REPL 退出与桌面引擎 `_shutdown` 均把终态提炼等待最多 **15 秒**
  （daemon 线程否则会被进程退出直接带走）；切会话/新建仍只触发不等待。
- 异常退出（崩溃/强杀）无法在进程内触发，但水位不推进，尾巴保留并在下次同会话提炼时补上。
- `Settings` 字段实测 **259 → 258**（删除 idle 键；以 `dataclasses.fields` 实测为准——
  此前条目里的 205/207/209/211 为记账漂移，本次一并纠正口径）；两份 `settings.example.toml` 同步；
  台账去掉 `idle_sessions`。
- 测试：删除空闲触发 10 例；新增“成功不节流 / 失败退避”2 例；台账/ golden 用例同步改口径。

### 修复（提炼把推断当事实，2026-11-17）

- 实机发现“关注贵阳天气，可能与出差或探亲有关”被当成画像事实入库：抽取 prompt 规则写着
  “从行为推断取低”（等于允许写推断），且“一次性事件不记”与 P2c 的 episodic 互相矛盾。
- 抽取 prompt：改为“不要从行为反推性格或意图”+“禁止推测/替用户补因果”；
  “不是什么”列表改为允许**带明确期限的未来安排**（与 episodic 对齐）。
- 裁决 prompt：content 只写用户明确陈述部分（例：“下周五可能去贵阳出差” → 只写“下周五有出差安排”）。
- 解析硬闸：非 `episodic` 的 add/update 含“可能/也许/或许/大概/恐怕/应该是/不确定/猜测”
  直接拒收并计入 `rejected` 遥测。
- 测试：`TestSpeculationGate` 4 例；复盘见 `docs/fixlogs/session-memory/profile-refine-speculation-fix.md`。

### 修复（update 漏写类别把身份降级成 other，2026-11-15）

- 实机验证发现：裁决 prompt 的 `update` 示例没有 `category`，模型不写 → 解析默认 `other` →
  落盘直接建 `other/stateful` 新条目，`identity/permanent` 被降级成 `other/stateful`。
- `update` 缺省类别改为**继承目标旧条目**（`Decision.category=""`），lifecycle 同样继承
  （permanent 不被降级）；prompt 示例补 `category` 并写明继承规则。
- episodic 收紧：必须给 `expire_days`（否则回退为按类别派生）；“近期/最近”这类模糊说法不算临时事项。
- 测试：`TestUpdateCategoryInheritance` 6 例；复盘见 `docs/fixlogs/session-memory/profile-update-category-downgrade-fix.md`。

### 新增（P4 首批：检索 golden 集，2026-11-15）

- 新增 `tests/memory/test_profile_recall_golden.py`：15 组「提问 → 期望条目」，
  别名 + bigram 两级召回要求 **top-3 命中 ≥12**（实测 15/15）。
- 另断言关掉别名后“命中数不增加且不报错”，防止后续调参把降级路径弄崩。
- 定位：后续调半衰期 / 别名权重 / 注入预算的回归安全网，改召回参数先跑这批。

### 新增（画像按需检索 RecallProfile 工具，2026-11-15）

- 新增 `agent/tools/memory/recall_profile.py`：`RecallProfile(query, category?, top_k?)` 只读工具，
  `deferred=True` 经 ToolSearch 按需发现（不占每轮工具 schema）。
- 检索走纯本地 `recall_similar`（bigram + 别名 + 同类别），**不引向量、不发网络**；
  休眠条目也会被检索到（用户主动问起就该捕回来）。
- 提示词只在存在未注入条目（`dormant_count() > 0`）时补一句“可调用 RecallProfile”，小库不推销工具。
- 配置：`profile_recall_tool_top_k`（默认 8，夹 1–20）；`Settings` 210 → **211**，两份模板同步。
- 测试：新增 `tests/tools/test_recall_profile.py`（13 例）+ 提示词条件出现 1 例。

### 新增（事件轨：临时事项独立留痕，2026-11-15）

- 新增 `agent/core/memory/profile_events.py`：`~/.jarvis/memory/events.jsonl` append-only 事件轨，
  提供 append / load / recent / prune；坏行跳过、IO 失败静默（旁路绝不打断主流程）。
- 提炼裁决段新增可选契约：`"lifecycle": "episodic"` + `"expire_days"`（1–365，从今天算）；
  非 episodic 的候选会被**强制清空**这两个字段。
- episodic 事实入库时算好 `expire_at`（到期由 `expire_episodic` 出清），同时追写事件轨；
  其他类型不写事件（事件轨只服务临时事项）。
- 事件轨默认**不注入** system prompt；新增 `/memory events [n]` 查看最近未过期事件（默认 5、最多 50）。
- 每日维护按 `profile_event_retention_days`（默认 90，0 = 只删到期项）清理“到期或过老”记录。
- `Settings` 209 → **210**；两份 `settings.example.toml` 同步。
- 测试：新增 `tests/core/test_profile_events.py`（16 例）+ 裁决解析/落盘 4 例 + 命令 2 例。

### 变更（画像衰减改为降权：不删条目，只调权重，2026-11-15）

- **不再按半衰期删条目**：`decay()` 拆为 `expire_episodic()`（只出清显式到期的 episodic）
  与 `audit_freshness()`（只统计休眠数，**不改数据**）；confidence 不再被时间反复乘。
- **派生权重**：`weight = confidence × 0.5^(距最近确认天数/30)`（读时计算，不落盘）；
  低于 `DORMANT_WEIGHT_FLOOR=0.10` → 休眠：不注入、不占预算，但**留在库里**；
  再被提及（noop/合并）刷新 `last_referenced_at` → 权重自动回到 confidence（复活）。
- **注入**只用 `active_entries()`；**召回**不排除休眠条目（仍是 update 的合法目标），权重作同分排序；
  **超容量淘汰**改为按权重最低先淘汰（唯一“容量兜底才删”）。
- `/memory` 改为按权重排序并展示「权重 0.32 | 休眠」，头部提示休眠条数；休眠条目仍可见。
- 删除路径收敛为三条：episodic 显式到期、超 200 条容量淘汰、用户/模型显式 `delete`。
- 测试：TestFreshnessWeight 6 例（权重/休眠/注入跳过/容量淘汰/不删除）+ 生命周期用例改口径。
- 半衰期 30 天与休眠阈值 0.10 仍为经验值，待 P4 评测集校准（本轮不动数值）。

### 修复（画像衰减依据是死字段，2026-11-15）

- 核对发现 `last_referenced_at` / `ref_count` **从未被写入**：`decay()` 声称“被引用/被再次提及就续命”，
  实际只看 `updated_at`，stateful 事实约 90 天必被删。
- 新增 `ProfileStore.touch_reference()`：提炼裁决为 `noop`（新消息里又说到同一事实）时调用，
  刷新 `last_referenced_at` 并 `ref_count+1`；`merge_or_add` 命中重复也算一次再确认（+1）。
- `/memory` 列表在 `ref_count>0` 时展示“提及 N 次”，续命依据对用户可见。
- 测试：`TestReferenceWiring` 4 例 + `TestNoopRenewal` 2 例 + 列表展示 1 例。
- 复盘见 `docs/fixlogs/session-memory/profile-decay-reference-wiring-fix.md`；半衰期 30 天/0.15 仍为经验值，待评测集校准。

### 新增（画像软删除：update 不再抹掉旧值，2026-11-15）

- `ProfileEntry` 新增 `invalidated_at` / `superseded_by`；`update` 落盘改为
  “建新条目 + 旧条目打失效标记并用 `superseded_by` 指向新条目”，历史可追。
- 失效条目**不再注入、不召回、不参与去重/衰减/展示**；`entries()` 默认只返有效条目
  （`include_invalidated=True` 取历史），`get()` 同理；`len(store)` 只算有效条目。
- `merge_or_add` 跳过失效条目（不把旧值“复活”）；`dedup_all` 只归并有效条目并保留历史；
  `prune_over_limit` 先淘汰失效条目；`decay` 跳过失效条目。
- `delete`（模型明确要求删）仍物理删除；`/memory` 末尾提示“另有 N 条已被新事实取代”。
- 测试：新增 `TestSoftDelete`（8 例）+ `TestSoftDeleteOnUpdate`（3 例）+ 命令 1 例。

### 新增（画像生命周期分层：permanent 不再被半衰期磨，2026-11-15）

- `ProfileEntry` 新增 `lifecycle`（permanent / stateful / episodic）与 `expire_at`；
  `profile.json` version 2 → **3**（老库缺字段读取时按类别派生，不写盘迁移）。
- **类别派生**（确定性）：identity / relationship / aspiration → permanent；
  其余（偏好/作息/工作习惯/心思/工具/在做的事）→ stateful；episodic 留给带到期时间的事实。
- **decay 分流**：permanent 不动；episodic 到期直接出清（未设 expire_at 则按 stateful 走）；
  其余仍 30 天半衰期，<0.15 清除；manual 条目继续完全豁免。
- **合并防护**：merge_or_add / dedup_all 取更稳的 lifecycle（permanent 不被 stateful 降级），
  `expire_at` 取大；新增 `decay(now=)` 注入点供测试。
- 测试：新增 `TestLifecycle`（9 例：派生/兼容读/三类 decay/合并与归并）。

### 新增（画像提炼遥测：`/memory` 看得见上轮做了什么，2026-11-15）

- 台账 `RefineState` 新增 `last_report`：抽取 / 新增 / 更新 / 删除 / 合并 / 跳过（noop）/ 拒绝
  七项计数，随 `advance()` **同一次锁+写**落盘（不多一次 IO）；白名单 + 非零值过滤，
  脏值静默丢弃，不影响水位读写。
- 提炼 worker 成功后写入该计数；失败轮次只记 `last_error`，不污染上轮遥测。
- `/memory` 头部新增一行「上次提炼 mm-dd HH:mm：新增 2 / 跳过 3 / 拒绝 1」；
  库为空时也展示，能看出“上轮全被硬闸拦了”这类情况。
- 测试：台账 4 例（白名单/保留旧值/脏值）+ golden 2 例（写入与失败不写）+ 命令 2 例。

### 新增（画像注入侧：主题优先 + 两轮填充 + 预算口径，2026-11-15）

- **渲染拆出** `agent/core/memory/profile_inject.py`：`ProfileStore.render_for_prompt` 只负责取快照，
  排序与预算裁剪进新模块（局部导入避免与存储层成环）。
- **主题优先**：`profile_inject_prefer_topics` 命中的类别置顶（空列表＝保持旧排序），
  无论用户问什么，注入的不再永远是“高置信那批”。
- **两轮填充**：主题命中 → 短条目（长度 ≤ 中位数）→ 长条目，逐条试装、放不下就跳过；
  一条 40 字事实不再挤掉 4–5 条短事实。
- **预算口径可切换**：`profile_inject_token_budget_mode` = `pseudo`（默认，`len/4` 兼容现状）/
  `weighted`（中英分权真 token，中文不再低估六成）。**默认不变**；切 weighted 需同步下调
  `profile_inject_token_limit`，具体值待评测集实测定，不凭估算改。
- **空白条目**：内容全空白的条目不再渲染（原先会输出一行空横杠白占预算）。
- **配置**：新增 2 键；`Settings` 字段 207 → **209**；两份 `settings.example.toml` 同步。
- **测试**：新增 `tests/core/test_profile_inject.py`（23 例）；画像相关合计 **154 例通过**。

### 新增（画像提炼两段式 + 四操作裁决 + 别名，2026-11-15）

- **两段式**：① 抽取（第 1 次 LLM，只读增量对话，**不带旧画像**）→ ② 逐条召回（纯本地 top-K）
  → ③ 裁决（第 2 次 LLM，候选 + 各自近邻）→ ④ 落盘。候选与裁决不再互相污染。
- **四操作**：`add` / `update` / `delete` / `noop`。`noop` 是“不记”的正式出口，
  承接“看着像重复但不值得改”的全部情况——只给 add/update 时，重复是协议逼出来的。
- **硬闸**：空内容、>40 字、非法 op 直接拒绝并计 `rejected`；类别白名单；置信度 clamp 0.3~0.95。
- **别名**：`add`/`update` 生成 2–5 个（每个 2–6 字），合并取并集≤ 8；`profile.json`
  version 1 → **2**（旧文件缺字段按 `[]` 读，不迁移不备份）。
- **召回打分**：`ProfileStore.recall_similar` = bigram 相似度 + 别名命中 0.30 + 同类别 0.15。
- **配置**：`profile_recall_top_k`（默认 5）、`profile_aliases_enabled`（默认 true）；
  `Settings` 字段 205 → **207**。
- **成本**：每次提炼从 1 次 LLM 变 2 次（裁决段多带候选与近邻，别名 +~50 token）；
  由于 P0 已干掉整窗重喂，总成本仍低于改造前。
- **测试**：新增 `tests/core/test_profile_resolve.py`（19 例）；画像相关共 **800 passed**。
- 注入侧改造当时未做（`profile_inject_prefer_topics` 未进配置表，避免死配置），
  已在下一刀 P1c 补齐（见上一条）。

### 新增（画像提炼改为水位增量，不再重喂整窗，2026-11-14）

- 新增 `agent/core/memory/profile_ledger.py`：按会话记录 `refined_until`（已提炼到第几条），
  提炼只喂 `messages[refined_until:]` 的增量。根治“每 600 秒把最近 40 条重喂一遍、
  同一事实被反复发现”的重复源头。
- 节流依据从“最近一次成功”改为“最近一次尝试”（`last_attempt_ts`）：provider 持续报错时
  不会每轮重试（每次 `_auto_save` 都起线程）。
- 新增终态触发：退出 / 切换会话时 `final=True` 无视节流，把最后一段增量收干。
- 新增空闲触发：60 分钟无新消息（且仍有 ≥`profile_refine_min_delta` 条增量）时补提炼一次。
  用**每会话一次性 Timer**而非宿主 Scheduler——后者会把内部任务持久化进 `schedule.json`
  并出现在 `/schedule` 列表里。
- 新增配置：`profile_refine_min_delta`（默认 6）、`profile_refine_on_exit`（默认 true）、
  `profile_refine_idle_minutes`（默认 60）；`Settings` 字段 202 → **205**
  （记忆提炼参数不进真源表，与现有 `profile_*` 一致）。
- **画像边界校正（2026-11-15）**：提炼 prompt 改为"只记关于本人"的正面清单 + 排除清单
  （助手工作流、命令/端口/路径、技术实现细节、环境状态、一次性事件），类别新增
  `personality`（性格）/ `aspiration`（愿望抱负）/ `mindset`（心思）；判断口径固定为
  "这条 5 年后还成立吗？描述的是人还是事/系统？"；细碎事实归长期记忆 `MEMORY.md`。
- 台账健壮性：50 会话 LRU；损坏当空表；`/rewind`、`/compact` 导致消息变少时自动下调水位；
  读-改-写全程持跨进程锁，拿不到锁只更新内存（不阻提炼）——Windows 下读写与 `os.replace`
  撞车时加短重试，不再把水位误读成 0。
- **修复（2026-11-15 实机测试）**：短尾巴（1–5 条增量）原先被两处门槛同时挡住——
  主路径 `delta < min_delta` 提前返回且不排空闲，空闲判定也用 `min_delta`。现改为：
  增量不足时也排空闲兜底，空闲门槛降到 **delta ≥ 1**（已按小时限流）；
  `/memory refine` 改走 force 增量路径（不再整窗重喂、来源不再是 `manual-refine`）。
  另修测试 worker 晚于 fixture 写回真实台账的问题，并清理已写入的 `s1` 脏数据。
  复盘见 `docs/fixlogs/session-memory/profile-refine-tail-lost-fix.md`。
- 测试：新增 `tests/core/test_profile_ledger.py`（15 例）、
  `tests/core/test_profile_refine_golden.py`（12 例）与 `tests/core/test_profile_idle.py`（10 例）；
  画像相关合计 **88 例通过**。

### 文档（更新日志分册化，2026-11-14）

- `docs/log/CHANGELOG.md` 1420 行 → **604 行**：主文件只留 `[Unreleased]` 与当前 **2.5.x**，
  历史按 minor 系列分册：`changelog/v2.4.md`（167 行）/ `v2.3.md`（666 行）/ `v2.2.md`（13 行），
  单文件均低于 900 行门禁；主文件顶部新增「历史版本索引」。
- 册子内原日志的相对链接统一补一级（`../fixlogs/...` → `../../fixlogs/...`），
  头部回指主文件的链接保持 `../CHANGELOG.md`；markdown 链接门 0 死链。
- 切分由一次性脚本完成并自检：**14 个版本段逐行一致**（按 `## [x]` 分段对比）+ 版本段总数守恒；
  脚本用后即删，已发布内容一字未改。

### 修复（CI 环境：Linux / Python 3.14，2026-11-14）

- **5 条用例在 ubuntu CI 变红，本地 Windows 全绿**，均为测试环境假设，不涉及运行时改动：
  - `tests/tools/test_screenshot_image_ref.py` 4 条：用例的假 pyautogui 里真的 `from PIL import Image`，
    而 `pillow` 只声明在 `[gui]` / `[camera]`，CI 只装 `.[dev]` → `No module named 'PIL'` 被工具侧的
    `except ImportError` 兜成“pyautogui 未安装”。修法：`[dev]` 补 `pillow>=10.0.0`
    （刻意不装 `[gui]`：无头 Linux 的 pyautogui 还要 X11/scrot，不值得为测试引入完整 GUI 栈）。
  - `tests/permissions/test_yolo_scope.py::test_explicit_empty_hard_deny_disables_only_that_list` 1 条：
    目标取 `Path.home()/.ssh/id_rsa`，而保护根默认是 `c:\\`；POSIX 上 home 不在 C 盘，于是从 ASK 落成 ALLOW。
    修法：保护根显式传当前用户 home，让“两条清单互不顶替”这条不变量平台无关；默认 C 盘根由
    `test_guard_roots_default_is_c_drive` 单独钉住，覆盖不丢。
  - 复盘：新增 `docs/fixlogs/ci-release/ci-linux-screenshot-and-yolo-fix.md`。

### 新增（图片驾驶舱 P0：终端终于能看见图，2026-11-11）

- **背景**：终端此前只有图片**入站**通道——`ScreenShot` / `CameraShot` / `BrowserScreenshot`
  把图编码成 base64 送给模型，人却看不见。模型说「我看了截图，按钮在右下角」时，用户无法
  核对它看的是不是同一张图；`operation-visibility` 计划里「失败截图未做」也卡在同一处。
- **数据面**：新增 `ImageRef`（`agent/core/images.py`）——只带路径与元信息、**不含字节**，
  与 provider 契约的 `ImageContent` 彻底分工：前者给人看（原图 + 屏幕适配），后者给模型看
  （≤1280 / JPEG85），两条尺寸链不混用。工具把引用放进 `ToolResult.trace_meta["image"]`
  （`tool_after` 的 payload 压根不含 images），`trace` 派生 `image_show` 事件时**用 `ImageRef`
  白名单重建**：直接 `**image` 展开会把上游误塞的 base64 一路落进 JSONL 与事件流。
- **展示面**：新增 `agent/ui/cockpit_image.py`（`ImageWindow` + `ImageManager`）与
  `agent/ui/image_cache.py`（缩放尺寸/缓存键/存活判定/布局纯函数）。480×600 独立窗，
  **默认屏幕左上角、整面可拖、位置记 `ui_state.json` 的 `image` 段**；大图按窗宽适配，
  缩略条点击与滚轮切换，元信息显示来源/尺寸/格式/体积/时间/路径；文件被清理时显示
  「图已过期」并保留元信息。
- **生命周期**：与抓取舱同构——有图才弹、结束保留、✕ 只隐藏、下轮自动重现、主 HUD 隐藏时
  同步隐藏、终端前台时让位置顶（`TerminalAwareTopmost` 新增 `peers`）。
- **落点**：`CameraShot` 复用 `~/.jarvis/cam`；`BrowserScreenshot` **新增落盘**
  `~/.jarvis/media/browser_*.png`（此前只在内存里编码，没有路径就没法展示）；
  `ScreenShot` 落点不变（系统临时目录）。
- **配置**：`[visualization]` 新增 `image_window`（auto/off）、`image_max`（1–24，默认 12）、
  `image_thumb_px`（48–160，默认 88），四处同步（settings / settings_domains / catalog / 模板）。
- **`ui_state.json` 改分段读-改-写**：原先 `save_position` 整文件覆盖、只写 `cockpit` 段；
  新增 `image` 段后两个窗口会互相冲掉对方位置。写入统一走 `_write_section`。
- **文案边界**：窗口标题写明「不改变模型的图片回执」，`image_receipt` 的「附上不等于看见」
  一字未改；工具回执里禁止出现「已为你显示 / 已确认」。
- **重构**：`cockpit_window.py` 已顶到 900 行门禁，先把 `_draw_feed` / `_draw_fetches` 与
  `_section` 的坐标收进 `cockpit_detail.py`（`draw_feed` / `draw_fetches` / `draw_section`），
  900 → 894 后再接线。
- **测试**：新增 `tests/ui/test_cockpit_image.py`（纯逻辑）、`tests/ui/test_image_state.py`
  （状态与生命周期，假窗口注入）、`tests/core/test_trace_images.py`（派生、脏值、无 base64）；
  `tests/ui/test_cli_cockpit.py` 的「全 off」用例同步补上图片驾驶舱，并新增「只开图片驾驶舱仍建根窗口」。
  全量 **4970 passed / 2 skipped**。
- **本地预览**：`python -m agent.ui.image_demo` 生成三张不同尺寸的测试图并只弹图片驾驶舱，
  不跑真实对话、不需要模型与 pyautogui，用来快速看形态（`agent/ui/image_demo.py`）。

### 修复（图片驾驶舱把三个截图工具打挂，2026-11-11）

- **现象**：真实会话里 `ScreenShot` 连试三次全挂，报
  `TypeError: ToolResult.ok() got an unexpected keyword argument 'trace_meta'`；
  同一路径的 `CameraShot` / `BrowserScreenshot` 一起挂（`Crawl` / `WebExtract` 不受影响，
  它们用 `ToolResult(data=..., trace_meta=...)` 直接构造）。
- **根因**：`ToolResult` 这个 dataclass **早就有** `trace_meta` 字段，但 `ok()` 工厂方法
  从来没透传它。图片驾驶舱那三处写成 `ToolResult.ok(..., trace_meta=...)`，于是每次调用都在
  构造结果时抛 `TypeError`——典型的「加字段忘了改 classmethod」。
- **修法**：`ok()` 增加 `trace_meta` 参数并透传（`error()` 不收该参数，守卫明确禁止绕过）。
- **为什么测试没拦住**：全仓此前**没有任何用例真的跑过 `call()`**（需要 pyautogui / 摄像头 /
  浏览器），图片驾驶舱的测试都停在 `trace_meta` dict 的派生逻辑上。已补两道门禁：
  - `tests/core/test_tool_result_contract.py`：AST 扫 `agent/` 下所有 `ToolResult.ok/error(...)`
    调用，把 keyword 名与签名（唯一真源）逐个比对，命中即报**文件:行号**。反向变异验证过：
    把 `trace_meta` 从签名里去掉，守卫立刻抓到 `screen.py:205` / `camera.py:213` /
    `browser.py:328` 三处；
  - `tests/tools/test_screenshot_image_ref.py`：假 pyautogui 真跑 `ScreenShotTool.call()`，
    断言 `trace_meta["image"]` 完整、尺寸取**原图**（不是回传模型的缩放值）、不含 base64、
    回执仍带「附上不等于看见」。

### 修复（多模态模型看不到工具截图，2026-11-12）

- **现象**：`deepseek-flash` 官方已是**多模态**（[Vision 文档](https://api-docs.deepseek.com/guides/vision)：
  能读截图里的文字），但 jarvis 截图后模型反复回报「我拿不到画面内容」，还写了十几秒
  内心戏。图片驾驶舱里人类看得见，模型看不见——两边各自以为对方知道。
- **根因两层**：
  1. `_messages_to_openai` 里 `role="tool"` 的 content **只能是字符串**，图片塞不进去。
     代码只在末尾拼一句 `[附带 N 张图片]`，注释写着「后续如需视觉 tool_result 再追加
     独立 user 消息」——**从未实现**，图等于被丢掉；
  2. DeepSeek 明文规定**图片只允许出现在 user 消息**（出现在 system / assistant 里
     直接 400），所以正确姿势就是另起一条 user 消息，这正是上面那步没做完的事。
- **修法**：新增 `_tool_images_message()`，把工具结果里的图（取最近 `_MAX_TOOL_IMAGES=4`
  张，远低于单请求 48MiB 上限）转成一条 `user` 消息带 `image_url`（base64 data URL，
  与用户直接贴图同构）；tool 消息末尾改写成「见下一条消息」。
- **文案配套**：`image_receipt()` 增加 `visible` 参数，判据由工具经 `vision_hint(ctx)`
  取到（唯一真源仍是 `model_capability`）：已知有视觉就直说「你可以看到这张图」，
  已知无视觉就直说「你只拿到路径，不要描述画面内容」，只有未确认时才保留旧的条件式
  提醒。模型不再拿「附上: 是」误判自己看得见。
- **实测**：造一张含 `JARVIS VISION CHECK / CODE 7391-ALPHA` 的图，走
  `_messages_to_openai` → 真发 `deepseek-flash`，返回 **`7391-ALPHA`**（思考里逐行读出
  了图上三行文字）。全量 **5007 passed / 2 skipped**。
- **顺带记录**：`max_tokens` 太小 + 思考模式会让视觉任务返回空 `content`
  （首次验证 `max_tokens=60` 全被 `reasoning_tokens` 吃掉）；依赖看图的任务要给够
  输出预算。

### 重构（三舱视觉：三色纪律 + 共享材质，2026-11-12）

- **依据**：`posters/jarvis-cyberpunk-design-philosophy.md`（Neon Doctrine）。先生指定三舱
  主色：任务驾驶舱电光青 `#00d4ff`、爬虫驾驶舱品红电流 `#ff2d78`、图片驾驶舱荧光绿 `#39ff88`。
- **`hud_theme` 重写为色板模型**：`HudTheme` + `THEME_TASK/CRAWL/IMAGE` 三个实例；
  **任务舱是全局默认**，旧常量名（`CYAN`/`GRID`…）继续指向它，因此工具卡、代理卡、
  详情浮层三个子组件零改动即换新色。
- **材质三舱共用一份**：`draw_background`（深渊黑 + 4px 扫描线 + 28px 网格 + 固定种子
  96 粒噪点 + 左右侧刻度）与 `draw_frame`（外框 + 四角 HUD 括号），替掉三个舱各自手写
  的网格/角标；`glow_text` 统一「先辉光后亮字」，支持 `fill` 覆盖让成/败数字也能发光。
- **语义色跨舱统一**：成 `#33f0c0` / 败 `#ff4d6a` / 警告 `#ffb347`，只有主色随舱变；
  品红舱的失败色刻意偏橙红，避免与主色糊成一片导致成/败不可辨。
- **改名**：「图片窗口」→「图片驾驶舱」，类 `ImageWindow` → `ImageCockpit`，标题
  `IMAGE COCKPIT // 图片驾驶舱`；爬虫舱标题补全为 `CRAWL COCKPIT // 爬虫驾驶舱`。
  配置键 `image_window` 与文件名 `image-window.md` 刻意保留（改名会破坏既有配置与链接）。
- **新增预览**：`python -m agent.ui.hud_preview` 一次弹三舱对比配色（不跑模型、不需要 key）。
- 行数：`cockpit_window` 894 → 884、`cockpit_crawl` 857 → 838（删掉重复的网格/角标实现），
  `hud_theme` 89 → 约 340。全量 **5007 passed / 2 skipped**。

### 修复（三舱可读性：文字看不清、线条不清晰，2026-11-12）

- **现象**：先生实测后反馈「文字看不太清，线条不够清晰」。
- **原因两条，都在我自己上一版里**：
  1. 为了「暗部必须保留材质」，我把 `dim` 压到 45–55% 白度，又铺了 **4px 间距的扫描线**
     ——小字压在横纹上必然糊；噪点 96 粒进一步压字。
  2. 结构线（外框、分区线、面板边、✕ 圆圈）用的是 `accent_dim`（如 `#0b4d66`），
     太暗，读者分辨不出区块边界。
- **修法**：
  - `HudTheme` 新增 **`rule`**（结构线色，介于主色与辉光之间：TASK `#1b9fc4`、
    CRAWL `#c2195c`、IMAGE `#1fbf6b`）；`draw_frame` 外框、三舱的汇总格/面板边/
    分区延长线/✕ 圆圈共 10 处全部改用它；
  - 文字提亮：`text` 提到 ~93% 白度、`dim` 提到 ~65%（三舱各自带主色色相）；
  - 纹理退后：扫描线间距 4px → **6px**、噪点 96 → **64** 粒、`grid` 各降一档
    ——单舱绘制项 344 → **262**。
- **刻意不动字号**：试过整体 +1，但各舱文本是按字符数 `clip()` 的（`clip(56)`、
  `clip(62)`…），字号一涨就会顶出面板，需同步重算所有截断宽度。本轮先靠对比度解决；
  若仍嫌小，下一轮统一调字号并一并重算 clip。

### 修复（三舱可读性 II：文字与结构线再提一档 + 爬虫舱收窄，2026-11-12）

- **第一轮修完仍看不清**。先生三张实测截图指出两处根因（与字号无关）：
  1. **分区标题用了 `accent_dim`**（如品红 `#6d1035`）——「抽取 // EXTRACT」
     「拓扑 // TOPOLOGY」几乎不可见。改为 `rule`，与结构线同级可见。
  2. **`dim` 作为信息色太暗**：耗时列、数据流、路径、说明全用它，在深渊黑上
     「背景与字体分不清」。三舱 `dim` 统一提到约 80% 白度
     （TASK `#bcd9e6` / CRAWL `#e0b9c7` / IMAGE `#c2e8d6`）。
- **结构线再亮一档**：`rule` 提到接近主色（TASK `#46cdef` / CRAWL `#ff5c9a` /
  IMAGE `#59ffab`）；任务舱的 ✕ 圆圈与两条分隔线、图片舱 footer 线一并改用它。
- **爬虫驾驶舱收窄**：`CRAWL_W` 560 → **500**；两处长行截断同步收紧
  （统计行 84 → 70、拓扑统计 88 → 72），否则会顶出面板。
- `HUD v1` 角标从 `CYAN_DIM` 改 `DIM`。
- 字号仍未动：各舱按字符数 `clip()`，需统一重算；本轮纯靠亮度层次解决。

### 修复（任务驾驶舱只剩背景和标题，2026-11-12）

- **现象**：先生截图显示任务驾驶舱只剩背景网格与标题，正文全无。
- **根因**：上一轮把 `CYAN_DIM` 换成 `RULE` 时**漏了 import**，`_draw_header` 画完
  标题后抛 `NameError`，被 `_pump` 的 `except` 记进 diag 吞掉——于是只画到标题为止。
- **为什么三道检查全绿**：`py_compile` 只查语法（`NameError` 是运行时）；`tests/ui`
  257 条用假窗口替换 `CockpitWindow`，从未执行 `_Hud.draw()`；人工冒烟只验「不崩」，
  异常被吞后进程照样活着。
- **修法**：补 import；新增 `tests/ui/test_hud_render_guard.py` 两道闸——
  ① ruff `F821` 扫 `agent/`（漏 import 即红，CI 有效）；② 真 Tk 渲染守卫（有显示
  环境才跑），断言 `draw()` 后 canvas item > 200 且文字 ≥ 10 段，"只剩背景"立刻红。
- 真机复核：任务驾驶舱 **309 items / 31 段文字**；全量 **5009 passed / 2 skipped**。

### 修复（熔断的 MCP 工具被反复调用，2026-11-12）

- **现象**：`tyc-mcp` 熔断后，模型在一次任务里**连调 4 次** `search_companies`，
  每次都拿到「已熔断 (3 次失败，156s 后重试)」，白费 4 步与近半分钟。
- **根因三条，都不是模型"记不住"**：
  1. 熔断状态只出现在**调用之后**的结果里；工具列表里那个工具看起来完全正常——
     模型在"选工具"那一刻没有任何依据；
  2. 错误文案只陈述事实，没有「别再试」的指令；
  3. `{e}` 直接插值 `KeyError`，文案还带一对引号，像底层异常而不是明确裁决。
- **修法**：
  - `MCPToolWrapper.description` 改为**动态 property**：熔断期间追加
    「`[不可用]` 该 server 已熔断（连续失败 N 次），约 R 秒后自动恢复……」；
    未熔断时逐字节返回原描述（不破请求前缀缓存）；冷却到期或未达阈值都不谎报。
    文案刻意用 GBK 可编码字符（emoji `⛔` 会让 `/tools` 一类打印在 cp936 控制台上
    直接抛 `UnicodeEncodeError`）；
  - `call()` 的未连接/熔断错误标 **`retryable=False`**（自愈层不再重试），文案写明
    「不要再调用本工具：熔断解除前每次调用都会立即失败。请改用其他来源，或直接向
    用户说明该数据暂不可得。」；顺带修掉 KeyError 的引号。
  - `MCPClient` 新增 `breaker_remaining()`（失败次数 + 剩余秒数），作为唯一状态源。
- 测试：`tests/tools/test_mcp_tool_breaker.py` 7 条（描述三态、过期不谎报、
  错误不可重试、无引号）。全量 **5016 passed / 2 skipped**。
