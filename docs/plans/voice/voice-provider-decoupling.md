# 语音能力厂商解耦 · 阶段一（`/talk` 全双工 + `/voice` 半双工）

> 状态：**S1 完成；2026-11-24 拍板——先只做 DashScope 解耦并验证稳定性，第二厂商暂缓**
> 起因：README 跨平台能力矩阵里实时语音那一行——「（三平台均需 DashScope Key）」（README.md L180）：
> 语音是唯一还绑死单厂商的能力面
> S1 交付物：[research/voice-provider-matrix.md](../../research/voice-provider-matrix.md)（厂商协议同构度矩阵，含逐条核实状态）
> 本阶段**只搬 DashScope，行为零变化**；TTS 多厂商实现不动（理由见第 8 节）

---

## 1. 现状：四层耦合，成本差一个数量级

| 层 | 现在的绑定点 | 耦合形态 | 解耦成本 |
|---|---|---|---|
| 全双工对话 `agent/talk/realtime_*.py` | `realtime_talk.py` 的模块级 `DEFAULT_WS_URL` / `DEFAULT_VOICE`，`engine_talk.py` 写死「未配置 DashScope API Key」 | **OpenAI Realtime 同构事件流**，`ws_url` 已可配置 | 低（只需把常量换成查表） |
| 半双工识别 `agent/voice/stt/` | `create_stt()` 无条件返回 `QwenASR` | 已有工厂接缝 | 中 |
| 密钥解析 `agent/config/voice_key.py` | 三级优先里「回退主 LLM key」的条件是厂商常量 | 逻辑绑厂商 | 低 |
| 播报 TTS `tts.py`(576) / `stream_tts.py`(493) | 直接继承 SDK 抽象基类，硬编码 22050Hz，两条合成路径并存 | **Inference 协议**（`run-task`/`task-started`/`continue-task`/`finish-task`），与 realtime 事件体系无关 | 高 → 本阶段不碰 |

### 本阶段解耦范围（两套系统分别动哪一段）

> 一句话：**只解「听」与「实时会话」，不解「说」（TTS）。**

| 系统 | 本阶段动 | 本阶段不动 |
|---|---|---|
| `/talk` 全双工 | 会话协议元数据：`ws_url`、鉴权方式、模型名、音色名、`turn_detection` 能力、`session.update` 语义 → 从常量改为查表 | 状态机、响应救援、看门狗、AEC、音频 IO 鸭子类型接口 |
| `/voice` 半双工 | **只动 STT 后端选择**：`create_stt()` 由无条件 QwenASR 改为按 `(vendor, model)` 查表；采样率/端点/模型名参数化 | **TTS 合成（CosyVoice）不动**；唤醒词、静默待机、打断、落库均与厂商无关，不动 |
| 两套共用 | `voice_key` 解析去厂商硬编码（预留 `voice_stt_vendor` / `voice_tts_vendor` 覆盖位） | `voice_events` 事件协议、`realtime_const` 阈值、serve/桌面契约 |

阶段一完成后的实际能力：`/talk` 可换**同构度 ≥90%** 的厂商；`/voice` 只有「听」能换，「说」仍是 DashScope——这正是第 1.1 节说的「混用」，允许且已预留解析位。

**抽象形态复用仓库已验证的范式**：[agent/llm/provider_registry.py](../../../agent/llm/provider_registry.py) 的「表驱动 + 能力元数据」（13 项注册，新增厂商只加一行）。目标是把语音侧已存在的**隐式契约显式化**，而不是重写状态机——`realtime_engine.py` 的状态机逻辑零改动。

---

## 1.1 先回答：STT 与 TTS 能不能用不同厂商

**能，但只限于半双工 `/voice`；全双工 `/talk` 在同一会话内不能混。**

| 场景 | 能否混用 | 原因 |
|---|---|---|
| `/voice` 半双工 | **可以** | 识别与合成是两次独立调用，中间隔着 LLM；没有协议层耦合 |
| `/talk` 全双工 | **不可以** | 识别是 realtime 会话的副产品（`input_audio_transcription`），播报音频也由同一会话产生；换厂商等于换整套会话协议 |
| A 厂识别 → LLM → B 厂合成 | 可以，但属于 `/voice` 形态 | 会失去双工的打断体验，不属本阶段 |

跨厂商混用时必须处理三件事：

1. **音频格式桥接**：识别侧只吃 16kHz 单声道，播报侧是 22.05kHz；中间不能假设同源；
2. **密钥与计费独立**：STT 与 TTS 可能各用一家 key，`voice_key` 不能再只解析一个 `dashscope_api_key`；
3. **时序不变**：TTS 播报期间仍必须能被 STT/按键打断，换厂商不得把打断链路弄丢。

因此**配置语义**定为：`voice_vendor` 只是默认值，解析顺序为「更细粒度覆盖（`voice_stt_vendor` / `voice_tts_vendor`）> `voice_vendor` > `dashscope`」。阶段一不对外暴露两个细粒度键，只在 resolver 里预留——以后加第二个 TTS 厂商时不需要再改 `voice_vendor` 的含义。

---

## 2. 硬约束：先写回放测试，再动抽象

依据是仓库自己的事故先例：2026-10-09 仅把 `tools_mode` 默认值改为 `"all"`，实时语音即**完全不可用**（复盘见 [fixlogs/voice/realtime-talk-tools-mode-default-fix.md](../../fixlogs/voice/realtime-talk-tools-mode-default-fix.md)）。这条链路上一个默认参数、一处时序都能让它整体失效，而单测很难覆盖真实帧序。

所以交付顺序里 **S4-1/S4-2（帧序采集 + 回放测）必须在 S2 之前落地**，否则「改抽象不改行为」这句话没有证据支撑。

---

## 3. S1 的四个发现，直接改变抽象层设计

这四条比「接哪家」更重要，元数据字段按它们定形：

1. **STT 不需要独立抽象层。** `qwen3-asr-flash-realtime` 本质是 realtime 会话的「仅文本输出 + 启用 `input_audio_transcription`」模式。所以 `/voice` 的识别与 `/talk` 的对话**共用同一份 provider meta**，只差 `model` 与 `modalities`——原计划的 `stt_ws_url` / `realtime_ws_url` 双字段合并成一个 `ws_url`。
2. **meta 粒度必须是 `(vendor, series)` 而不是 `vendor`。** 同为 DashScope：Qwen-Audio 用 `smart_turn` + 扁平 `input_audio_format`，Qwen-Omni 用 `semantic_vad` + 嵌套 `audio.input.format.samplerate`。按厂商建表会立刻装不下同厂商的两种形态。
3. **`session.update` 的更新语义本身是厂商差异**（稀疏更新 vs 每次提交完整配置）。jarvis 现有的一条硬经验是「配置必须在 IDLE 期一次性完成」，对要求完整提交的厂商，任何中途更新都得重放全量配置 → meta 需要 `session_update_semantic: sparse|full`，不能假设大家都稀疏。
4. **TTS 走另一套协议，本表覆盖不到。** 阶段二解耦 TTS 时不能复用本注册表，需要独立的 inference 协议抽象。

### 元数据字段（`agent/audio/providers.py`，待建）

```python
VOICE_REGISTRY: dict[tuple[str, str], VoiceProviderMeta]   # key = (vendor, series)

VoiceProviderMeta:
    ws_url                      # 合并后的单一端点（原 stt_ws_url / realtime_ws_url）
    auth_scheme                 # bearer | query_key | custom_headers
    realtime_models / stt_models
    modalities                  # ["audio","text"] / ["text"]：STT 复用同一条目的开关
    voice_namespace
    turn_detection_supported    # 例：["smart_turn", "server_vad", "null"]
    vad_threshold_range         # 量纲声明：(-1,1) 或 (0,5000)，禁止跨厂商照搬
    session_update_semantic     # sparse | full
    input_sample_rate / output_sample_rate
    supports_hotwords / requires_sdk / key_env
    verified                    # 官方规范是否一手核实过
```

查找顺序：精确 `(vendor, series)` → 厂商默认条目 → **fail-closed 明示报错**。能力缺失一律显式告警，禁止静默回落（对应仓库既有陷阱「配置静默失效」）。

`dashscope` 条目从现有常量**原样搬运**（`DEFAULT_WS_URL`、`DEFAULT_VOICE="longanqian"`、`smart_turn`/`server_vad`、`qwen-audio-3.0-realtime-flash`、`qwen3-asr-flash-realtime`）——先搬运不改行为，用第 6 节的回放测证明等价。

---

## 4. 接入门槛（写死，避免临场摇摆）

- 事件流同构度 **≥ 90%**（只有 URL、鉴权头、模型名、音色名不同）→ 阶段一直接接；
- 需要**事件映射层**的厂商 → 阶段一不接，只在 meta 预留字段，不为未验证厂商写代码；
- **至少一家非 DashScope 走完端到端**，本阶段才算成功——不能只有抽象、没有第二实现；
- 本地零厂商路线（`faster-whisper`）单独评估，结论见下一小节。

### S1 判定结果

| 档位 | 厂商 | 处置 |
|---|---|---|
| A（同族，只差 URL/鉴权/取值） | 百度千帆 audio-realtime、Azure Voice Live | 候选，但百度只有摘要级文档、Azure 是 preview 且要开资源 |
| B（同族但能力是子集 + 量纲差异） | **StepFun stepaudio-realtime** | **建议首批** |
| C（需事件映射层） | 豆包端到端、Azure GPT-Live、智谱 GLM-Realtime | 只留 meta 字段，不写代码 |
| 基线未核实 | **OpenAI Realtime** | 留条目 + `verified=false`；官方文档本次直连 403，未一手核实前不得启用 |

> **2026-11-24 拍板**：阶段一只做 DashScope 解耦并验证稳定性，本表与「建议首批 StepFun」整体保留为**二期名单**；S3（第二厂商）顺延，前置核实清单不删除。入选门槛与档位不变。

### 为什么首批建议 StepFun

1. 协议文档最完整可得（客户端事件全文已拿到），是三家里唯一拿到全量一手规范的；
2. 鉴权形态与现状完全一致（`?model=` + `Authorization: Bearer`），建连代码几乎不用改形状；
3. 能力有增量：`tools.type=retrieval`（知识库检索）是 DashScope 这条线没有的，`response.thinking.*` 正好对得上已有的 reasoning 通道；
4. 代价明确且小：只有两处真实差异——`threshold` 量纲是 **0–5000 能量值**（不是 -1~1），以及没有 `smart_turn`。两者都由 meta 的能力门 + 量纲声明解决，不需要映射层。

若你想更省：百度千帆（A 档，逐字一致）；若要更广：StepFun + Azure Voice Live。**这是本计划唯一等你决定的事。**

### 本地零厂商路线：本轮不建议

`faster-whisper` 是离线批处理库，只能替掉「听」这一半（`/talk` 全双工与 `/voice` 的播报都没有本地等价物），却要常驻约 1.5GB 内存并引入 `ctranslate2` 二进制 wheel；拿它承担「零厂商」定位，收益不匹配。建议归入**阶段二（TTS 解耦）**一并评估——那时才有 STT + TTS 两侧的本地方案组合。详细实测数据与判断依据在 S1 文档第五节。

---

## 5. 改动点清单

| 文件 | 改动 |
|---|---|
| `agent/audio/providers.py`（新建） | `VOICE_REGISTRY` + `VoiceProviderMeta`；搬运 dashscope 条目 |
| `agent/talk/realtime_talk.py` | 不再直接引用模块级 `DEFAULT_WS_URL` / `DEFAULT_VOICE`，默认值改由 meta 提供 |
| `agent/talk/realtime_engine.py` | 仅 `session.update` 组装处取 meta（`turn_detection.type`、`voice`、`modalities`）；**状态机逻辑零改动** |
| `agent/serve/engine/engine_talk.py` | key 校验与报错文案去厂商硬编码；`model` 默认值取自 meta |
| `agent/config/voice_key.py` | `resolve(settings, vendor="dashscope")`；保留三级优先，把「回退主 LLM key」的条件从厂商常量改为 `主 LLM 厂商 == 语音厂商` |
| `agent/voice/stt/__init__.py` | `create_stt()` 由「无条件返回 QwenASR」改为按 `(vendor, model 前缀)` 查表分派 |
| `agent/voice/stt/qwen.py` | 采样率、端点、模型名从入参/meta 取，去掉模块级常量依赖 |
| `agent/commands/handlers/voice_commands.py` | `/listen` 与 `/talk` 改走 `voice_key` + `voice_providers`；`/listen` 不再用主 LLM key；`turn_detection` 过能力门 |
| `agent/config/settings.py` + 目录 | 新增 `voice_vendor`（默认 `dashscope`，老用户零迁移）；`dashscope_api_key` **保留继续生效**并标注 deprecated |
| `agent/config/catalog.py` | `dashscope_api_key`「实时语音专用的 DashScope 密钥」与 `realtime_talk`「DashScope 双向流」两条描述改写为厂商中立 |

`/voice` 的 `listen()` 阻塞语义与 `_request_stop()` 那条**跨模块停止契约不得复制到各后端**——`stt/common.py` 已注明只能有一个实例，各后端只实现「怎么收帧」，不实现「怎么停」。

---

## 6. 测试（离线回放为基线）

1. **帧序采集**：从 `~/.jarvis/logs/diag.log` 与 `realtime_events.py` 的时间线日志导出至少两轮真实会话（须含：打断、响应救援、`response.done[cancelled]`、error 分支），落 `tests/fixtures/talk/*.jsonl`。
2. **`tests/talk/test_realtime_replay.py`**：假 WebSocket 逐帧喂入，断言客户端**动作序列**（发出的帧、UI 事件、静音窗口时刻、救援触发）与基线一致——这是「改抽象不改行为」的回归护栏。
3. **能力门单测**：不支持 `smart_turn` 的厂商必须回落 `server_vad` **并告警**；缺 key 必须报「未配置 `<厂商>` 语音密钥」而非硬编码文案；`vad_threshold_range` 声明被正确使用（拿 -1~1 的值喂 0–5000 的厂商要报错而不是静默生效）。
4. **注册表完整性测**：每条 meta 必填字段齐全、models 非空、音色命名空间存在、`turn_detection_supported` 非空、`verified=false` 的条目不得被 `/talk` 选中——与 [tests/test_release_contract.py](../../../tests/test_release_contract.py) 的「目录覆盖每个 `Settings` 字段」同一精神。
5. **CI 约束**：新增用例不得依赖 key、麦克风或端口；厂商实机相关一律按现有 `_WINDOWS_ONLY` 同口径打平台/依赖门（不得出现「本地绿 CI 红」，也不得用整条 skip 换绿灯）。
6. **人工走查清单**：S3 每接一家补一份 `docs/test/voice-provider-<vendor>.md`（含打断、音色切换、纯识别三条主路径）。

---

## 7. 文档同步（按 document-update 纪律）

- [research/voice-provider-matrix.md](../../research/voice-provider-matrix.md)：第四节结论段在拍板后更新为最终决定；
- [architecture/06-语音系统.md](../../architecture/06-语音系统.md)：**只加一节指针**指向本文档与 S1 矩阵——该文件已 840 余行，仓库单文件上限 900 行，不能靠它承载调研内容；
- `README.md` / `README.en.md`：语音能力行与平台矩阵里「（三平台均需 DashScope Key）」那一行按实际改写；
- **对外数字联动**：本阶段新增 `Settings` 字段（`voice_vendor`）会让「配置可问总数 / 直接改 / 改前先确认」三个数字各 +1，必须同轮更新 `README.md`、`config-docs/configuration.md`、`docs/guide/concepts.md`、`docs/test/config-self-service-testing.md`，并跑链接门；
- `CHANGELOG.md` 记在下一个版本条目下。

---

## 8. 明确不做（本阶段）

- **第二厂商接入**：本阶段只搬 DashScope，StepFun / 百度千帆 / Azure / OpenAI 均不写代码、不实机；S1 矩阵保留作二期名单；
- **TTS 多厂商**：含 `_PlaybackCallback` 与 SDK `ResultCallback` 解绑、`tts.py`(576) 与 `stream_tts.py`(493) 两条合成路径合并、`VOICE_CATALOG` 分厂商 —— 独立立项（阶段二）；
- 需要事件映射层的厂商（豆包、Azure GPT-Live、智谱）；
- WebRTC / AOQ 通道；
- `jarvis-desktop` 侧协议改动：桌面契约（`DESKTOP_COMMANDS`）不因语音厂商变化而变。

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 实时语音行为脆弱（有改一个默认值即全废的先例） | 第 6 节第 2 条回放测**先落地**再动 S2 |
| 跨厂商参数照搬（`threshold` 量纲不同） | meta 声明 `vad_threshold_range` + 能力门单测正向断言 |
| `session.update` 语义不同导致配置不生效 | `session_update_semantic` 字段 + full 语义下重放全量配置 |
| 无 key 无法实机验证新厂商 | 回放测兜底 + 人工走查清单；平台/依赖门而非新增失败 |
| 能力静默失效 | meta 显式声明 + fail-closed 告警 |
| 对外数字漂移（新增配置项破三个数字） | 同轮更新 README 与 config-docs，跑链接门 |
| 基线本身未一手核实（OpenAI 直连 403） | `verified=false` 锁住条目；接入前必须回头核实官方事件全集 |

---

## 10. 交付顺序与当前进度

```
S1 调研 ─► 拍板（DashScope-only）─► S4-1 帧序采集 ─► S2 抽象（搬 dashscope）─► S4-2 回放回归 + 实机走查 ─► 稳定性签收 ─► （二期）第二厂商
 ✅ 完成            ✅ 2026-11-24                ⬜ 下一步                  🟡 代码已落地·待验证              ⬜                          ⬜                      ⏸ 暂缓
```

- [x] **S1** 厂商协议同构度矩阵（含核实状态分级、门槛判定、四个设计发现、本地路线评估、S3 前置核实清单）
- [x] **拍板**：先只做 DashScope 解耦；第二厂商（StepFun 等）暂缓到解耦验证通过之后
- [ ] **S4-1 帧序采集**：从 diag 日志与 `realtime_events.py` 时间线导出至少两轮真实会话（打断、救援、`response.done[cancelled]`、error），落 `tests/fixtures/talk/*.jsonl`
- [ ] **S2 抽象层**：新建 `voice_providers.py`，**先原样搬运 dashscope 条目**（`DEFAULT_WS_URL`、`DEFAULT_VOICE="longanqian"`、`smart_turn`/`server_vad`、两个模型名）；`voice_vendor` 默认 `dashscope`，老用户零迁移；状态机零改动
  - 2026-11-24 已落地：`agent/audio/providers.py`（`(vendor, series)` 注册表 + fail-closed 解析）、`[voice] vendor`（Settings/领域映射/目录/两份示例 TOML）、`realtime_talk` 默认值改从 meta 取、`create_stt` 按表分派、`engine_talk`/`voice_commands` 去厂商硬编码；新增 `tests/voice/test_voice_providers.py` 15 例；`tests/commands + tests/voice + tests/config` 共 1093 passed。回放/实机验证尚未做。
- [ ] **S4-2 回归**：回放测断言客户端动作序列与基线一致；`/voice`、`/talk` 各三条主路径实机走查
- [ ] **稳定性签收**：DashScope 解耦后行为不变、失败路径文案不回归；签收通过才解冻第二厂商
- [ ] **文档与对外数字同步**

### 本阶段完成定义（DoD）

1. 未接第二厂商，但 `voice_providers.py` 已存在且 `dashscope` 条目是从现有常量搬运、非重写；
2. 无 `voice_vendor` 的老配置按 `dashscope` 运行，Key 报错文案与 SDK 缺失提示与改造前一致；
3. 回放测逐项一致（帧、UI 事件、静音窗口、救援时刻），且用例不依赖 key / 麦克风 / 端口；
4. `/voice` 与 `/talk` 实机主路径全部通过；
5. 解耦后 DashScope 连续使用无新增故障，才进入二期选型。

