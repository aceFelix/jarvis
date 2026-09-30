# 桌面壳左栏表单溢出与用量命中率补齐复盘

> 关键词：ModelForm、VoiceForm、settings-panel、form-scroll、flex 溢出、ProjectSection、cost.get、cache_hit_rate、Usage、缓存命中率
>
> @author aceFelix

## 1. 问题现象

### 问题一：「添加模型 / 添加音色」表单与左栏「项目」区重叠

桌面壳左栏切到模型面板 → 点「＋ 添加模型」（或音色面板点「＋ 添加音色」）后，
表单字段一多（模型表单六项：模型厂商 / 模型名 / API Key / 接口类型 / Base URL / 模型类型），
底部字段直接**压在左栏底部常驻的「项目」区上**：

- 「Base URL」的输入框与「留空按厂商自动推断」提示被项目区的「项目 / ＋ 打开文件夹」
  按钮行盖住，「模型类型」标签与下拉只露出半行；
- 「保存 / 取消」两个按钮与项目区的「最近项目」列表叠成一片重影；
- 面板没有滚动条，鼠标滚轮划不动 —— 被遮住的字段既看不见也点不到，只能靠窗口拉高
  碰运气，小窗下等于无法填完表单。

音色面板同理：字段只有四个，但 hint 文案长（音色 ID 一行、适配模型三行），
溢出量比模型表单小，重叠位置落在「适配模型」下拉与「音色描述」之间。

### 问题二：右栏「会话与用量」没有缓存命中率

用量卡只有模型 / 对话 / 输入 token / 输出 token / 缓存 token 五行。终端 `/cost`
早就有「缓存命中率」一行（评估 system prompt 缓存效果、判断费用与延迟的主要指标），
桌面壳看不到，用户只能自己拿缓存 token 除输入 token，且**不知道两种协议分母口径不同**
（Anthropic 协议的 `input_tokens` 不含命中部分，直接相除会算出大于 100% 的荒谬值）。

## 2. 根因分析

### 溢出：滚动约束只做到 `.panel` 一层，没传到表单内容容器

左栏是 flex 纵向布局，高度约束链路本应一路传到可滚动容器：

| 层 | 原样式 | 结果 |
|---|---|---|
| `#left-col`（`.glass-col`） | flex column | 高度受窗口约束 |
| `.panel` | `flex: 1; min-height: 0` | 能被压缩到剩余高度 |
| 历史/模型列表的 `.list-area` | `flex: 1; min-height: 0; overflow-y: auto` | 内容超出即滚动（所以会话列表从来没这个问题） |
| 表单的 `.settings-panel` | 只有 `display: flex; flex-direction: column; gap: 10px` | **没有 overflow，`overflow: visible`** |

`.panel` 被压缩到剩余高度后，子元素 `.settings-panel` 的内容高度超过它，
默认 `overflow: visible` 就让内容**画到面板外面**；而 `.project-section` 设了
`flex-shrink: 0` 底部常驻（这是它的设计目标），于是两者在视觉上叠在一起 ——
不是项目区跑上来，是表单溢出去撞它。

### 为什么不给 `.settings-panel` 直接加滚动

`.settings-panel` 是**共用类**：右栏 `SettingsPanel.tsx` 有四处用它做分组容器，
外层已有整体滚动。给它加 `flex: 1` 会把每个分组块拉伸填满，加 `overflow-y: auto`
则出现「外层滚动 + 分组内滚动」双滚动条。故新增修饰类 `.form-scroll`，只挂在两个
表单的字段容器上。

### 命中率缺失的深层问题：口径只写在 `/cost` 里

原命中率的协议口径判断（`cache_read > input` 视为 input 不含缓存 → 分母加上命中）
是内联在 `core_commands.handle_cost` 里的十几行代码，`cost.get` 只透传四类 token，
没有可复用的计算入口。若桌面壳自己按前端逻辑再算一遍，就成了第二套分母规则，
两处指标迟早对不上（`code-structure.md`：禁止复制粘贴复用）。

## 3. 修复方案

### 3.1 表单字段区滚动（jarvis-desktop）

- `ModelForm.tsx` / `VoiceForm.tsx`：字段容器 `className="settings-panel form-scroll"`；
- `styles/main.css`：新增 `.settings-panel.form-scroll` —— `flex: 1` + `min-height: 0`
  + `overflow-y: auto`，滚动条外观与 `.list-area` 一致（`width: 8px`、圆角、悬停加亮），
  颜色走 `var(--scroll-thumb)` / `var(--scroll-track)`，三张皮肤自动变色；
  `padding-right: 4px` 避免滑块压住输入框右边缘；
- **报错行与操作按钮移出滚动区**：`{error ? ... : null}` 与 `.model-form-actions`
  改为 `.panel` 直接子项 —— 字段滚到任意位置都能直接点保存/取消，校验失败原因
  也不会滚出视口（否则「点保存 → 报错在字段区底部 → 先找错误再滚回来」）；
- `styles/controls.css`：`.model-form-actions` 加 `flex-shrink: 0`，防止成为 flex 子项后
  被压缩。

### 3.2 命中率口径统一（jarvis）

| 改动 | 文件 | 说明 |
|---|---|---|
| 新增 `Usage.cache_hit_rate` | `agent/llm/base.py` | 会话累计命中率的唯一实现（百分数 0~100，无输入 token 返回 0），协议口径注释随实现落地 |
| `/cost` 改为调用该属性 | `agent/commands/handlers/core_commands.py` | 删掉内联的十几行分母判断，行为不变 |
| `cost.get` 透传 | `agent/ui/workbench/engine.py::session_usage` | 四类 token 外附 `cache_hit_rate`（一位小数），引擎未装配时为 0.0 |
| 协议文档 | `agent/serve/protocol.py` | `cost.get` 返回字段补 `cache_hit_rate` |

### 3.3 用量卡展示（jarvis-desktop）

- `stores/rightStore.ts`：`CostInfo` 加可选 `cache_hit_rate?: number`（旧后端无此字段）；
- `components/RightSidebar.tsx::UsageCard`：缓存 token 行后新增「缓存命中率」行
  （`usage-cache-hit-rate`），值原样展示 `toFixed(1) + '%'`，`title` 透出
  「命中 {r} / 输入 {i} token」明细；**字段缺失时整行不渲染**，不留空白指标；
- `i18n.ts`：中英各两键（`right.cacheHitRate` / `right.cacheHitRateTitle`）。

## 4. 测试

| 测试 | 位置 | 覆盖 |
|---|---|---|
| `TestUsage.test_cache_hit_rate_openai_style` | `jarvis/tests/llm/test_llm_base_errors.py` | input 含缓存 → 分母 = input；命中与未命中持平 = 100% |
| `TestUsage.test_cache_hit_rate_anthropic_style` | 同上 | 命中 > input → 分母加命中，900/100 归一化为 90%（不是 900%） |
| `TestUsage.test_cache_hit_rate_without_input_is_zero` | 同上 | 未发生 API 调用返回 0 |
| `test_cost_get_shape_defaults_zero` | `jarvis/tests/serve/test_serve_routing.py` | `cache_hit_rate` 在返回字段集合内且默认 0.0 |
| 用量卡命中率渲染 | `jarvis-desktop/test/renderer/components.test.tsx` | 有字段显示「缓存命中率 78.5%」；无字段隐藏该行 |
| 表单滚动容器 | 同上（模型 / 音色两条既有用例内补断言） | 字段容器含 `form-scroll` 类；提交按钮不在滚动容器内 |

执行结果：

```
jarvis:         .venv\Scripts\python.exe -m pytest tests/ -q   → 2225 passed
jarvis-desktop: npx vitest run                                 → 286 passed (16 files)
jarvis-desktop: npm run typecheck && npm run build             → 通过
```

## 5. 人工走查清单（CSS 布局 jsdom 测不到）

vitest + jsdom 不做真实布局计算，`overflow` / `flex` 是否真的滚动只能实机确认：

1. 桌面壳窗口高度压到最小（或缩到半屏），左栏 → 模型 → 「＋ 添加模型」；
   预期：字段区右侧出现滚动条，可上下滑动，「Base URL」「模型类型」滚动能完整看到，
   底部「项目」区不再与表单重影。
2. 同场景滚到字段区中间，直接点「保存」→ 预期按钮始终可见可点（不随字段滚动）。
3. 模型名留空点保存 → 预期红色错误行常驻可见（不因为滚动位置而看不到）。
4. 音色面板「＋ 添加音色」重复步骤 1-3。
5. 三张皮肤（荧光绿 / 电光蓝 / 金属银）各看一次滚动条配色是否跟随。
6. 右栏「会话与用量」：对话一轮后（`assistant_done` 会刷 `cost.get`）应出现
   「缓存命中率」行，数值与终端 `/cost` 的「缓存命中率」一致。
7. 右栏设置面板（齿轮进入）滚动行为不变（未受 `.form-scroll` 影响）。

## 6. 已知边界

- **分母协议判断是启发式**：`Usage` 不带协议标识，`cache_read > input` 只是
  「input 不含缓存」的信号；OpenAI 协议下若 input 极小而 cache 极大（端点返回累计值等
  异常场景），分母会偏大一档。彻底精确需把协议标识带进 `Usage`，属后续项。
- **jsdom 无法验证滚动**：本次只断言类名与 DOM 归属，真实滚动依赖第 5 节走查。
- **旧后端兼容**：后端未重启时 `cost.get` 无 `cache_hit_rate`，用量卡隐藏该行
  （不显示 0%，避免误读为「缓存完全没生效」）。

## 7. 相关文档

- [docs/architecture/09-记忆与压缩.md](../architecture/09-记忆与压缩.md)「会话级命中率统一口径」
- [docs/architecture/07-UI层.md](../architecture/07-UI层.md)「右栏数据源」「添加模型」
- jarvis-desktop `README.md`「左栏模型面板」「右栏四区块」与
  `docs/architecture.md` 同名小节
