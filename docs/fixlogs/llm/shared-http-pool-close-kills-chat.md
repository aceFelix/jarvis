# 共享 HTTP 连接池被临时 provider 关闭，导致对话「第二句必挂」

- 日期：2026-10-29
- 影响版本：P-02 共享连接池（2026-07-31）之后的所有版本
- 严重程度：P0（对话完全不可持续使用，只能重启）
- 作者：aceFelix

## 一、现象

用户报告：**重启 jarvis 后第一句对话正常，之后每一次都报错**。界面显示：

```
X LLM 调用失败: 原始错误: Connection error.

[ 网络错误 ]
API 服务连接失败或超时。
请检查网络连接，或确认 base_url 配置是否正确。
如使用代理，请检查 HTTP_PROXY 环境变量。
```

按提示排查网络、代理、key、base_url 全部正常；开新会话无效（**因为是进程级状态，与会话无关**）；换哪个模型都一样。

## 二、排查过程（14 个方向被逐个排除）

| 方向 | 结论 |
|---|---|
| 网络 / DNS / TLS | 通（TLS 0.41s） |
| key / base_url / 模型名 | 正常（裸请求成功） |
| 上下文超限 | 不成立（1M 窗口，实测请求体 14065 字符） |
| 历史格式（悬空 tool_call / role 顺序） | 问题数 0 |
| 真实历史完整重放 | 成功 |
| 思考参数（thinking + reasoning_effort） | 四组对照全成功 |
| 工具 schema（全量 42 个） | 高风险 0，发送成功 |
| 共享 client 跨事件循环复用 | 三轮全成功 |
| 同 provider 连跑三轮 / 提前 break | 全成功 |
| 真实系统提示词（26051 字） | 成功 |
| 代理（用户级 / 系统级 / 端口） | 全空 |
| emoji / GBK 编码 | 全成功 |
| base_url 与模型名错配 | 返回 404，不是 Connection error |
| 配置分层错位 | 结构正确（base_url 本就是根层键） |

**关键转折**：用户提供了一条决定性观察——「重启后第一次正常，之后不行」。配置不会在两次请求之间变化，因此**所有配置类原因被一次性排除**，矛头指向进程内状态漂移。

随后在 `provider.stream()` 加入请求参数日志（model / base_url / provider_id / client_id），重启复现后日志暴露：

```
19:59:11  provider_id=2584014714400 client_id=2584029671312  base_url='api.deepseek.com/v1'      ← 正常
19:59:13  provider_id=2584047278096 client_id=2584070048016  base_url='api.deepseek.com/anthropic' ← 画像提炼，404
20:04:05  provider_id=2971259712720 client_id=2971298211280  base_url='api.deepseek.com/anthropic' ← 又一次提炼，404
20:04:43  provider_id=2971249072784 client_id=2971260273392  base_url='api.deepseek.com/v1'      ← 主对话，挂了
```

界面错误此时也已带出 cause 链（`errors.py` 的修复）：

```
Connection error. <- RuntimeError: Cannot send a request, as the client has been closed.
```

## 三、根因

两层缺陷叠加。

### 3.1 主因：`provider.close()` 关掉了全局共享连接池

P-02 之后所有 `OpenAIProvider` 共用一个进程级 `httpx.AsyncClient`：

```python
def _get_shared_http_client() -> Any:
    global _shared_http_client
    if _shared_http_client is None or _shared_http_client.is_closed:
        ...
    return _shared_http_client
```

而 `OpenAIProvider.__init__` 把它注入给 SDK：`kwargs["http_client"] = _get_shared_http_client()`。

问题在 `close()`：

```python
async def close(self) -> None:
    await self._client.close()      # AsyncOpenAI.close() 会把 http_client 一并 aclose()
```

`AsyncOpenAI.close()` 会调用底层 `httpx.AsyncClient.aclose()` —— **关的是那个共享池**。于是任何一个短命 provider 下班，全体 provider 的连接池一起死亡。

触发者是记忆画像提炼（`agent/core/memory/profile_refiner.py`）：

```python
finally:
    if provider is not None and not report.skipped:
        try:
            asyncio.run(provider.close())      # ← 每轮提炼都建临时 provider 并 close
        except Exception:
            pass
```

- 提炼默认 600 秒触发一次（`profile_refine_interval`），消息数 ≥ 7
- 每次提炼新建一个 `OpenAIProvider`（共用同一个池），用完必然 `close()`
- **提炼成功还是失败都会 close**（在 `finally` 里）
- 于是：启动后 10 分钟内必有一次提炼 → 池子死亡 → 之后每句话都挂

这解释了全部现象：

| 现象 | 解释 |
|---|---|
| 重启后第一句正常 | 新进程，池子是干净的 |
| 之后每次必挂 | 某次提炼把池子关了，且不可逆 |
| 开新会话无效 | 池子是进程级资源，与会话无关 |
| 换模型无效 | 所有 OpenAIProvider 共用同一个池 |
| 只能重启恢复 | 只有新进程才会重建池子 |

### 3.1.1 影响范围：终端同样中招，且入口更多

`_get_shared_http_client()` 是**模块级全局**，CLI 与桌面壳共用同一份代码。
全仓扫描后，会关闭 provider 的入口共四处：

| 入口 | 位置 | 桌面壳 | 终端 | 修复方式 |
|---|---|---|---|---|
| 画像提炼 | `profile_refiner.py` `asyncio.run(provider.close())` | 有 | 有（含 `/memory refine`） | 已改**独占池** |
| `/model` 切换 | `model_switch.py` `await old_provider.close()` | 有 | 终端走 `_switch_model` 重建 QueryLoop，不关旧 provider | `close()` 不再碰共享池 |
| `/init` 连通性测试 | `init_command.py` `await provider.close()` | — | 有 | 已改**独占池** |
| 进程退出 | `main.py` / `engine.py` | 有 | 有 | 保留，并补 `close_shared_http_client()` |

语音模块（`voice/`）里的 `close()` 全部是 WebSocket / 音频流 / TTS 句柄，
不碰 provider，不受影响。

`model_commands.py` 第 106 行早就记着这个现象：「提示写着『配置已更新』，下一句
话却仍打旧端点，表现为 Connection error（2026-10 真机实测）」——当时的归因是
「端点没重建」，实际根因是**共享连接池被关掉了**。

### 3.2 次因：`[memory.refine]` 的协议键被静默忽略

`~/.jarvis/settings.toml` 里写的是：

```toml
[memory.refine]
api_format = "anthropic"
model = "deepseek-flash"
base_url = "https://api.deepseek.com/anthropic"
```

但 `settings_domains.py` 的映射只认 `provider` 这个键名：

```python
("memory", "refine", (
    ("model", "profile_refine_model"),
    ("provider", "profile_refine_provider"),     # ← 只有 provider
    ("base_url", "profile_refine_base_url"),
    ...
))
```

于是 `api_format = "anthropic"` 被丢弃，协议回退到 openai，最终请求变成
`https://api.deepseek.com/anthropic/chat/completions`（OpenAI 兼容路径）→ 404。

**它不直接导致主对话失败，但它保证每次提炼都走失败路径**，把 3.1 的缺陷稳定触发。

修复时实测确认：`https://api.deepseek.com/anthropic` 这个 Anthropic 兼容端点是
真实可用的（`messages.create` 返回 `ok`），环境变量 `DEEPSEEK_API_KEY` 也有效。
也就是说**用户配置本身完全正确**，404 纯粹是协议键被丢弃导致路径拼错。

## 四、修复

### 4.1 `agent/llm/openai_provider.py`

**两层修复**：一层是「不许关别人的池」，另一层是「干脆各用各的池」。

第二层才是根治：即使 `close()` 不再关共享池，提炼（后台线程）与主对话仍泡在
同一个连接池里，后台的流量、取消、占用仍可能影响前台可用连接。物理隔离之后，
后台任务退场时关的是自己的池，主对话完全不受影响。

```python
# 新增：独占池参数
if dedicated_client:
    self._own_pool = _new_http_pool(max_keepalive=4, max_connections=8)
    kwargs["http_client"] = self._own_pool
else:
    self._own_pool = None
    kwargs["http_client"] = _get_shared_http_client()

async def close(self) -> None:
    """释放 provider 自身（**不会关闭全局共享连接池**）。

    当前规则：只有独占池（dedicated_client=True）才真正关闭；
    共享池由 close_shared_http_client() 在进程退出时统一处理。
    """
    own_pool = getattr(self, "_own_pool", None)
    if own_pool is not None and not own_pool.is_closed:
        await own_pool.aclose()

async def close_shared_http_client() -> None:
    """关闭进程级共享连接池（**唯一**允许关它的地方）。"""
```

配套改动：

| 文件 | 改动 |
|---|---|
| `agent/bootstrap.py` | `_build_provider()` 新增 `dedicated_client` 关键字参数并透传 |
| `agent/llm/provider_registry.py` | OpenAI 系 8 个 provider 的 `init_keys` 加 `dedicated_client`（DashScope 原生 SDK / Zai 不接） |
| `agent/core/memory/profile_refiner.py` | 提炼 provider 一律 `dedicated_client=True`（含回退主 LLM 分支） |

### 4.2 `agent/main.py`

进程退出时显式关闭共享池（此前依赖 `provider.close()` 顺带关闭）：

```python
await provider.close()
try:
    from agent.llm.openai_provider import close_shared_http_client
    await close_shared_http_client()
except Exception:
    pass
```

### 4.3 可观测性（三处，缺一不可）

| 文件 | 改动 |
|---|---|
| `agent/llm/errors.py` | `_extract_raw()` 顺着 `__cause__` 链提取，界面直接显示 `Connection error. <- 真实原因` |
| `agent/core/query_loop.py` | 此前**完全没有 logger**；现补 `logger.error(..., exc_info=True)` 与重试前的 `logger.warning` |
| `agent/llm/openai_provider.py` | 每次请求记录 `model / base_url / key前6位 / provider_id / client_id / msgs / tools / system_chars` |

**没有这几处，这个问题将永远无法定位**：`APIConnectionError` 在 SDK 里是
`except Exception: raise APIConnectionError(...)` 的万能兜底，任何异常都会变成
"Connection error"，而当时日志里只留了一个 `provider_error` 字样。

### 4.4 回归测试

新增 `tests/llm/test_shared_http_pool_close.py`（4 例）：

- `provider.close()` 后共享池仍存活，且仍是同一个对象
- 多个 provider 共用同一池，关掉其中一个不影响其他
- `close_shared_http_client()` 才是真正关闭的地方，关闭后再取会自动重建
- `dedicated_client=True` 的 provider 用独占池，关它不伤共享池

同时修正 `tests/core/test_refine_provider.py` 的工厂替身（用 `**kwargs` 收尾，
避免工厂新增构造开关时打爆测试）。

### 4.5 配置键名别名（消除静默失效）

`settings_domains.py` 的 `[memory.refine]` 子表映射补上 `api_format`，与 `provider` 等价：

```python
("memory", "refine", (
    ("model",       "profile_refine_model"),
    ("provider",    "profile_refine_provider"),
    ("api_format",  "profile_refine_provider"),   # 新增别名
    ("base_url",    "profile_refine_base_url"),
    ("api_key",     "profile_refine_api_key"),
    ("interval",    "profile_refine_interval"),
)),
```

`apply_domain_tables()` 只处理「表内已有的键」，所以只写一个时各自生效，
两个都写时按声明顺序后者覆盖。同时 `[memory]` 扁平表也加了
`profile_refine_api_format` 别名。

修复后实测：用户原有的 `api_format = "anthropic"` 被正确读成
`profile_refine_provider = "anthropic"`，提炼走回 Anthropic 协议 + 正确路径。

## 五、验证

**单元测试**：3 passed；相关子集 725 passed。

**端到端复现**（主对话 → 临时提炼 provider 404 → close → 主对话继续）：

```
[1] 主对话第一句: 成功 -> '收到'
[2] 临时 provider（故意 404）+ close()
[3] 主对话第2句: 成功 -> '收到'
    主对话第3句: 成功 -> '收到'
=== 修复生效：临时 provider 下班不再拖死共享连接池 ===
```

## 六、教训

1. **模块级单例的 close 必须区分「自身」与「共享资源」**。`close()` 的语义是"我不用了"，不是"大家都别用了"。凡是把全局对象注入给 SDK 的地方，都要确认 SDK 的 `close()` 会不会顺手关掉它。
2. **`finally` 里无条件 close 是危险模式**，尤其失败路径。提炼失败 → 仍然 close → 附带把别人的资源关了。
3. **错误信息不带 cause 链等于没有错误信息**。`Connection error` 四个字让排查绕了十几个方向；带上 `<- RuntimeError: client has been closed` 之后一秒定位。
4. **用户观察比仪器更值钱**。"重启后第一句正常"这一句，直接把 14 个方向砍掉，且指明了"进程内状态漂移"这个正确方向。前面十几轮我在自己搭的环境里测，而我的探针每次新建 client、每轮显式关流，恰好避开了真实路径。
5. **配置键名不一致会让配置静默失效**。`profile_refine_provider` 的语义是 api_format，用户按直觉写 `api_format` 却没人报错。
