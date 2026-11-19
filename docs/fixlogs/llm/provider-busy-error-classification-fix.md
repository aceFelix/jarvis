# 厂商 503 引出的六处缺陷：错误分类、文案当控制流、日志不落盘、假版本号、面板定格、用例漂线程

- 日期：2026-11-03
- 起因：用户在 Git Bash 里用 `deepseek-flash`，当天反复遇到 `HTTP 503 Service is too busy`
- 类型：错误处理与可观测性（1 个厂商侧现象 + 6 个本地缺陷）
- 严重度：P1 与 P5 高——前者让多厂商备份形同虚设，后者让事故完全查不到痕迹

## 1. 问题现象

终端里反复出现这一段（三次 `request_id` 各不相同）：

```text
> 几点了现在
LLM 网络错误，1.5s 后重试一次: ProviderError("原始错误: HTTP 503: ...
'service_unavailable_error'... <- HTTPStatusError: '503 Service Temporarily Unavailable'
for url 'https://api.deepseek.com/v1/chat/completions'

[ 网络错误 ]
API 服务连接失败或超时。
请检查网络连接，或确认 base_url 配置是否正确。
如使用代理，请检查 HTTP_PROXY 环境变量。
⚠️  网络异常，自动重试中...
╭─ 💭 思考过程 ─╮
│ 用户          │
╰───────────────╯
```

用户提出的三个问题分别是：**为什么叫我查网络/代理**、**为什么今天老是出现**、**为什么
deepseek 首轮回复巨慢**。此外还有第四、第五个现象是他没注意、由排查带出来的；第六个
（用例漂线程）则是在交付验证阶段自己撞出来的。

## 2. 排查过程

### 2.1 先确定"慢"发生在哪一侧——用项目自带的分段计时

`agent/llm/openai_provider.py` 里早已埋了分段计时（`0ef3797`，2026-10-29），日志行格式是
`LLM 计时: 首包思考 Xs | 首字正文 Ys | 全程 Zs | 思考 N 字 | 正文 M 字`。从
`~/.jarvis/jarvis.log` 取出当天真实数据：

| 时刻 | 请求规模 | 首字正文 | 全程 | 备注 |
|---|---|---|---|---|
| 17:47:03 | — | **1.45s** | 2.24s | 正常水平 |
| 17:48:20 | `msgs=2 tools=1 system_chars=40179` | — | — | 发出请求 |
| 17:48:43 | 同上 | **23.63s** | 23.68s | 思考 **0 字** |
| 17:50:26 | — | — | **84.63s** | 思考 69 字、正文 **0 字** |
| 17:51:19 | — | **52.43s** | 52.49s | — |

1.45s 与 23.63s 两次**本地变量完全相同**（同一 system prompt、2 条消息、1 个工具），
且 23.63s 那次"思考 0 字"——模型一个 reasoning 分片都没返回，就是纯排队。结论：慢在厂商侧。

> 一个被自己的数据证伪的猜测：先怀疑"MCP 从 62 个工具涨到 88 个把请求撑大了"。查
> `tool_dispatch.py` 后确认 MCP 工具是 `deferred=True`，只有被 ToolSearch 发现后才带
> schema；实测日志里 `tools` 的分布是 `0`×626、`1`×128、`23`×58，最大 37，**88 个工具
> 从未进过请求**。`system_chars` 确实从历史的 35028 涨到 40179，但同一大小的 prompt 一次
> 1.45s、一次 23.63s，说明它不是当天变慢的原因。猜测作废。

### 2.2 缺陷一：503 被误分类成"网络错误"

`agent/llm/errors.py` 里两个分支互斥，而错误的那个在前：

```
L192-205  NETWORK 兜底：…  or status in (502, 503, 504)   ← 503 在这里被截走
          文案「请检查网络连接 / base_url / HTTP_PROXY」
L208-217  SERVER_ERROR：500 <= status < 600
          文案「LLM 服务暂时异常…可运行 /model 切换其他厂商」   ← 一次都没执行过
```

专门为 5xx 写的正确文案与正确建议就在下面十几行。更早的 2026-10 修复已经给
`disconnected` 类做过"不要让用户查 HTTP_PROXY"的专门分支，却漏了 502/503/504。

而且 504 还有第二重误伤：它的文案常带 `Gateway Timeout`，会被前面的 `timeout` 分支先命中，
把厂商网关超时说成用户侧超时。

### 2.3 缺陷二：分支靠匹配用户可见文案

```python
# agent/core/query_loop.py L425（修复前）
if "网络错误" in error_str and not self._network_retried:
```

命中就走"同厂商重试一次"，`continue`；**下面第 9 行就是 `_try_failover()`**。所以后果不是
"永远切不过去"（重试后 `_network_retried` 已置位，第二次仍 503 时能落到 failover），
而是**被推迟一轮，白耗 1.5s 退避 + 一次注定失败的请求**。

更要紧的是这种写法本身：任何人把文案改成"网络连接异常"，重试逻辑就静默失效且测试全绿。

病根在上游一行——分类算出来了却被丢掉：

```python
# agent/llm/openai_provider.py L518（修复前）
raise ProviderError(classified.user_message) from e   # classified.category 没带下去
```

### 2.4 缺陷三：事故根本没落盘（用户"今天老是出现"却查不到）

在 `jarvis.log` 里搜 `HTTP 503` 得到 **0 条**，尽管终端里明显出现过好几次。原因：

```python
# agent/core/query_loop.py L36
logger = logging.getLogger(__name__)
```

而 handler 只挂在 `get_logger()` 那个名为 `jarvis` 的 logger 上；`agent.core.query_loop`
自己没有 handler，向 root 传播时 root 也没 handler → 只剩 Python 的 **lastResort**：把消息
原样甩到 stderr（所以用户看到的那行**没有** `[WARNING] jarvis:` 前缀），**且不写文件**。

全仓统计：**19 个模块**是同样的裸写法，包括 `core/sandbox/audit.py`（沙箱审计）、
`core/checkpoint.py`、`config/keyring_store.py`、`core/error_recovery.py`、`tools/bash.py`。

讽刺的是 `query_loop.py` 顶部那段注释写的正是"以前日志里只剩几个字符 provider_error，
异常原文和 cause 链全丢了"——当初就是为了修这个才加的 logger，结果它一直没进日志文件。

### 2.5 缺陷四：横幅里的"当前版本 2.2.2"是假数字

`pip show jarvis-agent` 显示 `Editable project location: E:\...\jarvis`，而
`python -c "import agent; print(agent.__version__)"` 是 2.3.4。`current_version()` 先读
`importlib.metadata`，editable 安装下那份 dist-info 停在装包那天，于是提示变成
"发现新版本 2.3.4（当前 2.2.2）"。

而且这条提示硬编码了"运行 /update 升级，或手动执行 pip install -U"——对 editable 环境，
后者会把安装换成 PyPI 正式版，**直接覆盖掉源码树里尚未发布的修改**。渠道判断系统里本来就有
（`upgrade_command()` 对 editable 返回 None、`run_upgrade()` 也会拒绝），只是这条提示路径
没去用它。

### 2.6 缺陷五：thinking 面板停在首帧（并撤回我的一次误判）

**我上一轮的判断是错的**：当时看到面板只有"用户"两个字，我断定"503 后重试恢复了脏上下文"。
本轮同样的 503 之后重试成功，thinking 面板**完整**显示，而它开头也正是"用户"两个字——
说明那不是数据脏，是**界面停在首帧**。

代码里的分界很清楚：面板收尾只隐含在 `assistant_text()` 的第一行（`self._end_thinking()`），
而 `_stream_once` 的 `except ProviderError` 分支调的是 `_flush_thinking()`——那只是把缓冲写进
`ThinkingContent` 数据块，**完全不碰 UI**。厂商 503 这种"正文一个字都没到"的失败轮走不到
`assistant_text`，于是面板永远停在第一帧那两个字，Live 也从未 `stop`。上面 2.1 的
`17:50:26 思考 69 字 / 正文 0 字` 正是那一轮。

### 2.7 一个既有缺陷顺手修掉

`set_debug(False)` 用 `isinstance(handler, logging.StreamHandler)` 挑控制台 handler，而
`FileHandler` 正是 `StreamHandler` 的子类——于是关 DEBUG 时会把**文件** handler 一起降到
WARNING，此后 INFO/DEBUG 静默不再落盘。`LLM 计时` 这类 INFO 行恰好是事后定位慢请求
唯一的实测数据来源。

### 2.8 缺陷六：用例漂出后台线程（交付验证时撞到的偶发独红）

P0-P7 改完后第一次全量：`1 failed, 4195 passed`，红的正是
`tests/core/test_profile_memory.py::TestRefineSessionE2E::test_full_pipeline`；单跑该用例
0.30s 必绿，再连跑三次全量又全绿——典型的**时序竞态**，不能用“重跑绿了”交差，必须把
机制挖清。两条直接证据：

1. **看门狗插件**（每个用例 setup 时扫一次活动线程）：单跑该文件就统计到
   `profile-refiner` 后台线程漂进了**后续 13 个用例**的执行期；
2. **耗时探针**：默认 `Settings()` 的 `api_format` 是 `openai`（不是 mock），用例里
   `maybe_refine_async` 起的线程会真去 `agent.bootstrap._build_provider` 建 `AsyncOpenAI`，
   实测**一次 1624 ms**——这就是那条线程漂出用例的寿命。

危害链：`refine_session` 是在**调用时**才从模块全局取 `_build_refine_provider` 的，只要
线程启动被 GIL 延迟到下一个用例装好 `monkeypatch` 之后，它就会拿到**那个用例**的 fake。
把窗口撑开（在 `_collect_dialog_text` 里睡 1s）后稳定复现：泄漏线程单独就把
`fake.thinking_state` 撑成 `[False, True]` 两项，叠上用例自己的两项后
`assert fake.thinking_state == [False, True]` 必红——现象与全量那次完全对得上。

附带危害（不报错但更严）：线程里 `refine_session` 未传 store → 默认 `ProfileStore()` 指向
用户真实画像库（实测 `C:\Users\…\.jarvis\memory\profile.json`，38 条真实条目）；在配了
`OPENAI_API_KEY` 的机器上，还会从单测里发出真实请求、扣真实额度。

**与本轮改动的关系（归因要诚实）**：这个竞态早就存在，不是 P0-P7 制造的——但 P5 让 19 个
模块的日志真正落盘，新增了文件 I/O 与 GIL 竞争，抬高了整体负载，把一个潜伏的窄窗口推到了
能撞上的临界点。换个角度说：P5 之前全量绿也不是“没这个坑”，只是概率低到没撞。这类
“改了 A 才暴露 B”必须查清并修掉，不能归为偶发忽略。

## 3. 根因分析

| # | 根因 | 为什么原来会这样 |
|---|---|---|
| 1 | 5xx 状态码判断被塞进 NETWORK 兜底分支，抢在 SERVER_ERROR 之前 | 两处逻辑不同时期加入，先写的用 `or status in (…)` 兜了个"顺手" |
| 2 | `ProviderError` 只携带文案，不携带分类 | 分类最初只为"给用户看"设计，后来恢复策略出现时沿用了文案匹配 |
| 3 | handler 只挂在 `jarvis` 这一个 logger 上，而多数模块用 `getLogger(__name__)` | 统一日志模块按"单例 logger"设计，没有覆盖命名空间传播这条路 |
| 4 | `current_version()` 优先信任包元数据 | 写的时候假设"元数据 == 安装的代码"，editable 打破了它 |
| 5 | 面板收尾隐含在“正文第一条增量”路径里 | 流式 UI 的正常路径掩盖了“只有 thinking 没有 text”的失败路径 |
| 6 | 用例不隔离真 LLM 工厂，且 `maybe_refine_async` 起的 daemon 线程没有 join 入口 | 节流测试只断言返回值，没人管它顺手起的那个线程去哪了、活多久 |

## 4. 修复方案

| 项 | 位置 | 改动 |
|---|---|---|
| P0 | `agent/llm/errors.py` | 新增厂商 5xx/过载分支，**放在关键词表之前**（状态码是强证据，同时避开 504 的 `timeout` 与关键词表里 `token` 的误伤）；`NETWORK` 兜底摘掉 `status in (502,503,504)`；新文案明确"这不是你的网络/base_url/代理"，并给出 `/model` 与 `vendor_fallback` 两条可操作路径 |
| P1 | `agent/llm/base.py` | `ProviderError` 新增 `category` 字段（空串表示未分类） |
| P1 | `agent/llm/errors.py` | 新增 `to_provider_error()` 工厂（四个 Provider 的抛出点统一改走它）与 `effective_category()`（category 优先，缺失时回退文案匹配，保持外部裸构造行为不变） |
| P1 | `agent/core/query_loop.py` | 网络重试分支改用 `effective_category(e) == ErrorCategory.NETWORK.value`；厂商 5xx 因此不再占用这轮，直接落到 `_try_failover()` |
| P5 | `agent/core/logging.py` | 新增 `_attach_agent_namespace()`：把同一对 handler 挂到 `agent` 命名空间根（幂等、`propagate=False`），一处修好 19 个模块且保留各模块 logger name；handler 共享对象使 `set_debug` 依然同时生效 |
| P5+ | `agent/core/logging.py` | `set_debug` 排除 `FileHandler`，关 DEBUG 不再误降文件 handler |
| P6 | `agent/update.py` | `current_version()` 改为优先 `agent.__version__`；`start_background_check()` 按 `upgrade_command()` 的结果分渠道出文案，editable 下改为"请用 git pull"，不再喊 `/update`；新增 `format_command()` 给含空格路径加引号 |
| P7 | `agent/ui/cli.py`、`agent/core/context.py` | 新增公开收尾入口 `RichCLI.end_thinking()`（幂等），并在 `UIProtocol` 里登记为可选方法 |
| P7 | `agent/core/query_loop.py` | `_stream_once` 的 `finally` 里收尾（异常/中断/取消都收口；收尾失败被吞掉，不掩盖真正的 `ProviderError`/`CancelledError`） |
| P7 | `agent/bridge/ui.py`、`agent/wechat/ui.py` | 委托型 UI 转发 `end_thinking`（用 `getattr` 保护） |
| P8 | `tests/core/test_profile_memory.py` | 两道自收闸：`_no_real_llm_client`（把 `agent.bootstrap._build_provider` 钉成“调了就记名、收尾就红”，避开 `refine_session` 吃掉异常造成静默）与 `_no_refine_worker_leak`（用例收尾等 `profile-refiner` 线程退出，超 5s 直接 fail）；节流用例补上 provider 隔离，`_last_refine_ts` 改走 `monkeypatch.setattr` 自动还原；新增 `test_worker_thread_does_not_outlive_test` 把不变量直接写成用例 |

## 5. 验证

### 5.1 新增用例

本轮共新增 **27 个用例函数**（`test def` 计数逐文件对过，不包含上一轮 404 修复那 6 个尚未
提交的用例；两者合起来相对 HEAD +33）：

| 文件 | 新增 | 覆盖点 |
|---|---|---|
| `tests/llm/test_llm_base_errors.py` | +7 | 503/502/504 均归 `SERVER_ERROR`、504 不被 `timeout` 抢走、无状态码的 busy 关键词仍算厂商、500 保持原文案、不得出现祈使式本地排查指引（但允许“查 HTTP_PROXY 没有意义”这句澄清）、`to_provider_error` 带 category、`effective_category` 的优先级与回退 |
| `tests/test_query_loop_run.py` | +4 | 改文案不静默关掉重试（P1 核心价值）、厂商过载直接 failover（断言 `stream_calls == 1`，即不白跑同厂商重试）、失败轮仍收尾思考面板、`end_thinking` 缺失时不报错 |
| `tests/core/test_logging.py` | +4 | 裸 `getLogger(__name__)` 的 WARNING **与 INFO** 真的落盘、logger name 保留、重复挂载幂等且不再向 root 传播、`set_debug` 不降文件 handler |
| `tests/ui/test_cli_thinking.py`（新文件） | +4 | 只有思考增量时收尾仍输出完整内容、收尾幂等、正常路径不重复打印、纯空白不产生空面板 |
| `tests/test_update.py` | +5 | `current_version()` 采信运行时代码、editable 下升级提示不再喊 `/update`、包安装渠道文案分流、`format_command` 给含空格路径加引号、既有提示类用例固定渠道 |
| `tests/serve/engine/test_remote_bridge.py` | +2 | 委托型 UI 的 `end_thinking` 转发（`WeChatUI` 调一次 / 宿主无方法 no-op / 宿主报错静默；`BroadcastUI` 只转终端不 `_broadcast`） |
| `tests/core/test_profile_memory.py` | +1 | `test_worker_thread_does_not_outlive_test`：把“提炼后台线程不得漂出用例”写成显式不变量 |

### 5.2 全量回归（按真实时间线记录，不要把结果洗成“一开始就绿”）

| 阶段 | 结果 |
|---|---|
| 本轮改动前（404 修复交付时） | 4172 passed / 0 failed |
| ① P0-P7 落地后首次全量 | **1 failed**, 4195 passed / 2 skipped（红的就是 §2.8 那条竞态，单跑必绿） |
| ② 为取证再连跑 3 次全量 | 3/3 clean——说明触发率约 1/4，是时序竞态而非必现 |
| ③ 撑开窗口 + 看门狗插件拿到直接证据（§2.8） | 机制定性完成，不是“重跑绿了算” |
| ④ 修好竞态后首次全量 | 4196 passed / **1 failed**（红的是链接门禁：新文件 `tests/ui/test_cli_thinking.py` 未 `git add`） |
| ⑤ `git add` 后连跑 2 次全量 | 2/2 clean，4197 passed / 0 failed / 2 skipped（130.07s / 127.49s） |
| ⑥ ruff 对照逼出修改、补上委托型 UI 转发用例后的全量 | **4199 passed / 0 failed / 2 skipped**（131.99s） |
| 链接门禁 | `tests/test_md_link_checker.py` 17 passed（该门只承认进过 git 索引的链接目标） |
| ruff | 逐文件与 HEAD 同口径对比（20 个改动 .py 文件）：**19 个净新增 0**，`agent/update.py` 反而少了 1 条 |

唯一残留：`tests/test_query_loop_run.py` +4 条 **F811**——与本文件既有约定一致：21 条存量用例全部
靠同名的 `registry` fixture 参数拿装配好的注册表（HEAD 就是 21 条 F811），新增 4 条同一写法。
不改：为了这 4 条单独偏离文件约定（改名或逐行 `# noqa`）反而伤一致性。

中途被这项对照逼出来的真实缺陷（已修）：`to_provider_error` 的 `-> "ProviderError"` 引发
**F821 未定义名字 + UP037**——运行时不报错（注解被当作字符串），但静态分析完全认不出这个返回类型，
现在用 `if TYPE_CHECKING: from agent.llm.base import ProviderError` 解；本轮新写的三处防御式
`try/except Exception: pass`（`query_loop` 收尾、`WeChatUI` 转发、`update.current_version`）
改用仓内已有的 `contextlib.suppress(Exception)` 写法（`imap.py` 的 logout、`bash.py` 的
transport close 同一口径），`current_version` 则收窄到 `except ImportError`——这样既不新增
S110/BLE001，也比盲捕 `Exception` 更符合本仓“不静默吞异常”的口径。

### 5.3 变异矩阵（逐条退回旧行为，看对应用例会不会红）

方法：备份目标文件 → 注入变异 → 只跑相关用例 → 按 sha256 还原并校验。基线（未变异）：
五份文件合计 **108 passed**。

| # | 变异（退回旧行为） | 结果 | 红掉的用例 |
|---|---|---|---|
| V1 | `errors.py` 厂商分支摘掉 `status in (502,503,504)`，只留关键词 | ✅ 抓到（1 failed） | `test_upstream_busy_503_is_not_blamed_on_user_network`——**只有文案用例红、category 类全绿**，实证了状态码与关键词是互补双路 |
| V2 | 厂商分支的 `category` 退回 `NETWORK` | ✅ 抓到（5 failed） | 503 文案、5xx 归类、504 不被抢走、无状态码 busy、`to_provider_error` 带 category |
| V3 | `to_provider_error` 不携带 category | ✅ 抓到（1 failed） | `test_to_provider_error_carries_category` |
| V4 | `query_loop` 重试判据退回 `"网络错误" in str(e)` | ✅ 抓到（1 failed） | `test_renamed_wording_does_not_silently_kill_retry`（P1 要守的就是这条） |
| V5 | 不挂 `agent` 命名空间（退回 P5 前） | ✅ 抓到（3 failed） | 裸 logger 落盘、命名空间只挂一次不传播、`set_debug` 与命名空间共享 handler |
| V6 | `set_debug` 不排除 `FileHandler` | ✅ 抓到（1 failed） | `test_set_debug_off_keeps_file_handler_debug` |
| V7 | `_stream_once` 的 `finally` 不调 `end_thinking()` | ✅ 抓到（1 failed） | `test_provider_error_still_ends_thinking_panel` |
| V8 | `RichCLI.end_thinking()` 改空操作 | ✅ 抓到（2 failed） | 只有思考增量时补印完整内容、收尾幂等 |
| V9 | `current_version()` 不采信运行时 `__version__` | ✅ 抓到（2 failed） | 版本号非空、优先运行时代码 |

§2.8 那两道测试闸另外单独做了变异（同样备份/还原）：

| # | 变异 | 结果 |
|---|---|---|
| M-A | 去掉节流用例里的 `_build_refine_provider` 隔离（退回真工厂） | ✅ 被 `_no_real_llm_client` 抓到：`51 passed, 1 error`（收尾断言“测试禁止构造真 LLM provider”） |
| M-B | 让后台 worker 睡 8s（模拟线程不退出） | ✅ 被 drain 闸抓到：`test_worker_thread_does_not_outlive_test` FAILED，`Failed: profile-refiner 后台线程未在 5s 内退出` |

### 5.4 现场数据与人工走查

- 厂商侧慢：同一 `system_chars=40179 / tools=1 / msgs=2` 下 1.45s vs 23.63s vs 52.43s
  首字正文（§2.1），结论不依赖本地代码。
- 落盘效果（P5）实机确认：修复后 `~/.jarvis/jarvis.log` 里能搜到 `HTTP 503`（修复前 0 条）。
- 面板定格（P7）：失败轮思考面板不再停在首帧（用户侧需重启会话验证；代码侧已有
  `tests/ui/test_cli_thinking.py` 与 `test_provider_error_still_ends_thinking_panel` 钉住）。

## 6. 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/llm/errors.py` | 厂商 5xx 分支、`to_provider_error`、`effective_category` |
| `agent/llm/base.py` | `ProviderError.category` |
| `agent/llm/openai_provider.py`、`anthropic_provider.py`、`dashscope_provider.py`、`zai_provider.py` | 抛出点统一走工厂 |
| `agent/core/query_loop.py` | 重试判据改分类；`finally` 收尾思考面板 |
| `agent/core/logging.py` | `agent` 命名空间挂载；`set_debug` 排除文件 handler |
| `agent/update.py` | 版本号取运行代码；分渠道提示文案；`format_command` |
| `agent/ui/cli.py`、`agent/core/context.py` | `end_thinking` 公开入口与协议登记 |
| `agent/bridge/ui.py`、`agent/wechat/ui.py` | 转发 `end_thinking` |
| `tests/llm/test_llm_base_errors.py`、`tests/test_query_loop_run.py`、`tests/core/test_logging.py`、`tests/ui/test_cli_thinking.py`、`tests/test_update.py`、`tests/_query_loop_fakes.py`、`tests/serve/engine/test_remote_bridge.py` | 新增用例、FakeUI 收尾计数、委托型 UI 转发覆盖 |
| `tests/core/test_profile_memory.py` | 两道自收 fixture（真 LLM 工厂守卫、后台线程 drain）+ provider 隔离 + 新不变量用例 |
| `docs/architecture/03-LLMProvider层.md`、`02-Agent运行时.md`、`07-UI层.md` | 同步分类口径、恢复策略、面板收尾 |
| `CHANGELOG.md` | 修复条目 |

## 7. 经验总结

1. **别把用户可见文案当控制流开关**。文案是给读的，控制流要有结构化依据（这里是
   `category`）。这次两个方向都踩了：误分类让 503 走了网络分支；而网络分支本身靠中文子串匹配。
2. **状态码优先于关键词**。504 带 `timeout`、5xx 消息里可能带 `token`，关键词表先命中就会把
   厂商故障重写成用户侧问题。判断顺序应按证据强度排。
3. **统一日志要覆盖"命名空间传播"这条路**。只给一个自造名字（`jarvis`）的 logger 挂 handler，
   等于让所有 `getLogger(__name__)` 的模块静默丢日志——而且失败方式极隐蔽：终端能看到
   （lastResort 写 stderr），文件里没有，于是"有现象无痕迹"。挂到命名空间根上一处修好全部。
4. **区分"UI 数据层 flush"与"UI 呈现层收尾"**。`_flush_thinking()` 写数据块不等于面板收口，
   失败路径必须显式收口——放 `finally` 而不是放正常结束处，且实现要幂等。
5. **`editable` 安装下包元数据不是"当前版本"**。运行时版本应以被执行代码为准；提示与命令
   必须按安装渠道生成，否则会引导用户执行一个覆盖源码的操作。
6. **用实测数据代替推断，并敢于撤回**。本轮撤回了一次自己给出的"脏上下文"结论——同一缺陷
   现象在新证据下有更好解释（面板停在首帧）。分段计时、日志计数这类现场数据是唯一能证伪
   自己假设的东西；上次的"分段计时"正是靠它把 1.45s 与 23.63s 摆在一起，才确认慢在厂商侧。
7. **断言要允许否定形式的文案**。`assert "HTTP_PROXY" not in msg` 会误杀“查 HTTP_PROXY 没有
   意义”这种真正帮到用户的澄清——不变量应写成“不得出现祈使式的本地排查指引”。
8. **谁起的后台线程谁收**。`maybe_refine_async` 这类“触发完就返回”的接口在测试里等于
   交付一个无 join 入口的线程；用例必须自己 drain，否则它读到的是**后续用例**的
   `monkeypatch`（模块级名字是在调用时才取的）。另外，把真外部构造（`_build_provider`）
   钉成守卫时，**不能只靠抛异常**——`refine_session` 会把异常吃掉成 `report.error`，子线程
   里没人看见；得“记名 + 收尾断言”。
9. **“重跑绿了”不是结论**。间歇性独红要么复现出机制、要么留下检测手段。本轮靠两件工具拿到
   直接证据：一个看门狗插件（每个 setup 扫一次活动线程，量化“漂了几个用例”）与一个撑开
   竞态窗口的探针（把 1/4 概率变成 100%，才敢下结论）。
10. **性能/并发类改动会抬高既有脆弱点的触发率**。P5 多写日志本身没错，但它把一条潜伏
    几个月的测试竞态推到了能撞上的频率。改完全量多出一条不相关的红，仍要查清归因。
