# 模型切换：切回 base_url 留空的自定义模型时端点停在上一个厂商（404）

- 日期：2026-11-03
- 影响版本：v2.2.2 ～ v2.3.4（判据自 `4b77351` 2026-07-30 起就存在，**升级不解决**）
- 类型：功能缺陷（多厂商往返切换）
- 严重度：高——用户会认定"厂商切不回去了"，且报错文案把方向指向"模型不存在"

## 1. 问题现象

真机操作序列（DeepSeek 当时正在过载）：

```text
provider deepseek   model deepseek-flash       # 启动时正常
> /model qwen3.7-flash
模型已切换为: qwen3.7-flash（通义千问 3.7 Flash）
> jarvis            →  正常回答               # DashScope 这条线是好的
> /model deepseek-flash
模型已切换为: deepseek-flash（多模态（deepseek-flash））
> 明天天气如何
LLM 调用失败：HTTP 404 - The model `deepseek-flash` does not exist or you do not
have access to it.   <-  for url 'https://dashscope.aliyuncs.com/compatible-mode/v1/...'
```

决定性线索在最后那个 URL：**要求的是 DeepSeek 的模型，请求却打到了 DashScope 的域名**。
DeepSeek 全程没有参与这次失败，所以"DeepSeek 服务有问题"这个直观结论对 503 成立、对 404 不成立。

导火索确实是外部因素：DeepSeek 当时返回 `503 Service is too busy`，用户因此第一次做出
"切走再切回"的动作，把潜伏了近三个月的切换缺陷引爆了。

## 2. 排查过程

### 2.1 先排除代码变更

`agent/llm/errors.py`、`agent/llm/openai_provider.py`、`agent/core/query_loop.py` 最后
改动都在 2026-10-29，期间三次提交（文档订正 / 发布契约测试 / 画像查重）都没碰 LLM 链路。
说明 503 不是我们引入的。

### 2.2 一个被自己数据证伪的猜测

注意到两次启动 MCP 从 `5/7、62 个工具` 变成 `7/7、88 个工具`，怀疑"工具 schema 变多 →
请求变重 → 厂商更容易拒"。但**第一次 503 就发生在 62 个工具时**，变量不成立，猜测作废。

### 2.3 离线复现（不需要任何真实 key）

还原"运行中 provider 已在 DashScope → 切回 deepseek-flash"，只改自定义条目写法：

| case | 自定义条目 | 切换后端点 | 复用旧 provider |
|---|---|---|---|
| A | `base_url` 留空 | `…dashscope.aliyuncs.com/compatible-mode/v1` | **True**（← bug） |
| B | 显式写 `base_url` | `https://api.deepseek.com/v1` | False（正确） |

触发条件坐实：**条目未显式写 base_url**。A 的展示文案 `多模态（deepseek-flash）`
与用户终端一字不差。

### 2.4 第一版修复被既有测试拉回来

我先按"cfg 应有端点与当前端点字符串不等 → 重建"来写，结果**三条既有测试直接红**：

- `test_custom_reuse_keeps_provider_and_snapshot`
- `test_baseline_same_endpoint_still_reuses`
- `test_custom_reuse_provider`

原因是既有 fixture 里当前端点是 `https://api.deepseek.com`（**少 `/v1`**），而厂商表里是
`https://api.deepseek.com/v1`——同一个厂商，两种写法都比"不等"，于是同厂商切模型也被判成
需要重建。

同时意识到一个更危险的场景：用户把 `base_url` 指向自建网关（`http://localhost:8000/v1`）时，
按厂商推断会拿到官方地址并把流量**从代理上拐走**。而 [init-not-applied-to-session-fix.md](init-not-applied-to-session-fix.md)
里已经为桌面壳定过同族原则："编辑模型时 base_url 留空，先沿用原值，原本也没有才推断"。

结论：**判据不该是"地址字符串变了"，而该是"厂商归属变了"**。既有测试的失败是正确信号，
不是要改的测试。

## 3. 根因

`_build_switched_provider` 用"自定义条目与当前 provider 端点有无差异"决定是否重建，
三条判据是 `api_format` / `base_url` / `api_key`。而**后两条在真实配置下永远为空**：

- `/models` 添加模型时 base_url 留空即按厂商推断，不落盘；
- `keyring_store` 的策略明写"TOML 中不再写入 api_key 明文（由 keyring 接管）"。

于是 `cfg.get("base_url") and …` 与 `cfg.get("api_key") and …` 双双短路，`api_format`
两边又都是 `openai`（DashScope 兼容模式与 DeepSeek 同协议）——三条判据全部失效，
provider 被复用，只有模型名换了。

讽刺的是正确答案一直在同一个文件里没被调用：`_infer_model_vendor("deepseek-flash")`
能从前缀判出 `deepseek`，`_infer_base_url` 能从厂商表拿到真实地址，两者与那三条判据
是**同一个提交（`4b77351`）一起进来的**，只是切换路径从来没用上。

## 4. 修复方案

| 位置 | 改动 |
|---|---|
| `agent/config/vendors.py` | 新增 `host_of(url)`（取 netloc，小写）与 `is_known_vendor_host(url)`（host 是否命中厂商表）。**只比 host 不比全串**：同厂商地址写法差异是常态 |
| `agent/model_manager.py` | 新增 `_switch_target_changed_vendor(...)`：目标厂商可推断 + 该厂商端点非 SDK 自管 + 当前 host 在厂商表内，三者齐备才判"厂商归属变更" |
| `agent/model_manager.py` | 新增 `_vendor_api_key(vendor)`：按目标厂商查 keyring；`other/custom` 直接返回空；keyring 不可用时降级为空串，由调用方继承 `settings.api_key` |
| `agent/model_manager.py` | 重建判据补上厂商变更一路；`if-elif` 链改为并列布尔（消除 ruff SIM114，同时体现四判据的"或"语义）；重建时端点/密钥用"显式配置 > 按厂商推断 > 继承当前" |

三条**保守不复用**的边界（都经过测试固定）：

1. 厂商推不出来（模型名不带厂商前缀）→ 维持旧行为，不新增"切不过去"的失败；
2. 该厂商端点由 SDK 自管（`_infer_base_url` 返回空串）→ 无 host 可比，不折腾；
3. 当前 host 不在厂商表里（自建网关/内网端点）→ **绝不重建**，不把流量从代理上拐走。

## 5. 验证

- 新增 6 条用例（`tests/llm/test_model_manager_extra.py::TestSwitchVendorEndpointNotSticky`）：
  跨厂商切回、同厂商少 `/v1`、自建网关、厂商推不出、keyring key 跟随、keyring 异常降级。
- 全文件加模块级 autouse fixture 隔离 keyring。**隔离点选在底层 `load_api_key`（外部 I/O）
  而不是 `_vendor_api_key`（被测逻辑）**——否则同一份代码在存过 deepseek 凭据的机器上会把
  "复用"断言判成"重建"，属测试依赖本地环境。
- 该文件 62 passed（56 既有 + 6 新增）；切换相关三个文件 101 passed；全量 pytest 见下。
- `ruff` 对两个源文件：改前 7 条 → 改后 5 条，净新增 0（顺带消掉该处既有 2 条 SIM114）。
- 变异测试 4 类，全部按预期被抓：

  | 变异 | 结果 |
  |---|---|
  | M1 厂商判据恒 False（等于没修） | 抓到 2 条 |
  | M2 去掉自建网关保守判断 | 精确抓到 |
  | M3 host 比对退化回全串 | 抓到预期用例，**并额外抓到 3 条既有测试**——反证 host 比对是既有契约的要求，不是过度设计 |
  | M4 不再按厂商查 keyring 取 key | 精确抓到 |

  M1 下 `keyring_key_follows_target_vendor` 仍通过：因为 `differs_key` 判据独立成立，
  两条判据互补救场——这是我为该用例标注的预期集合有误，不是断言漏洞；key 判据的专属
  变异是 M4，它被抓到了。

## 6. 涉及文件

| 文件 | 改动 |
|---|---|
| `agent/config/vendors.py` | 新增 `host_of` / `is_known_vendor_host` |
| `agent/model_manager.py` | 新增 `_vendor_api_key` / `_switch_target_changed_vendor`，重建判据与端点/密钥解析改造 |
| `tests/llm/test_model_manager_extra.py` | 模块级 keyring 隔离 + 6 条新用例 |
| `docs/fixlogs/model-switch-vendor-endpoint-fix.md` | 本复盘 |
| `docs/architecture/12-配置系统.md`、`docs/architecture/07-UI层.md` | 同步"重建还是复用"的判据描述 |
| `CHANGELOG.md` | 未发布段新增修复条目 |

## 7. 经验总结

1. **空值语义必须显式设计**。`cfg.get("base_url") or settings.base_url` 把"未配置"实现成
   "继承上一个"，与"按厂商推断"是三种不同语义，混在一行里就没人能看出选了哪种。差异
   判据依赖一个"正常就是空的"字段，等于判据永不生效。
2. **同厂商的 URL 不要比全串**。`api.deepseek.com` 与 `…/v1`、带不带尾斜杠，都是常态差异；
   要比 host。厂商表已经存在时，反查用 host 而不是再维护一份映射。
3. **既有测试红灯优先怀疑自己的方案**。这次三条既有测试把我从"比字符串"拽回"比厂商归属"，
   顺带暴露了自建网关会被拐流——如果当时直接改测试，就交付了一个比原 bug 更糟的行为变更。
4. **用户说"某厂商坏了"先看请求 URL 落在哪家**。503 落在 `api.deepseek.com` 是厂商侧；
   404 落在 `dashscope.aliyuncs.com` 是我们发错了地址。同一个用户 complaint 里可能混着
   两个性质完全不同的故障。
5. **测试隔离外部 I/O，而不是隔离自己的函数**。patch `load_api_key` 既避免碰真实凭据，
   又保留"按厂商取 key"这条逻辑本身可测（否则降级分支永远测不到）。
