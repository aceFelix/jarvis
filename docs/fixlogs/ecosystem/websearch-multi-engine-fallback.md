# WebSearch 多引擎 fallback 与浏览器兜底改造

> 2026-09 · 网络工具链 · @author aceFelix

## 背景（问题现象）

WebSearch 此前只有一条通道：POST `https://html.duckduckgo.com/html/`，用
requests/urllib 直连。国内环境 DuckDuckGo 不可达时，error_recovery 框架按
指数退避重试 3 次（只读工具 +1 次）后仍然全挂，用户侧表现为：

```
WebSearch 遇到 执行超时，1.0 秒后第 1/3 次重试...
...
❓ 工具 WebSearch 执行失败: 执行超时
搜索失败: ConnectTimeout: ... host='html.duckduckgo.com' ...
```

模型拿不到任何搜索结果，只能凭"气候常识"之类的先验知识作答，且失败回执里
没有任何可行的下一步指引。

## 根因

单点依赖：唯一引擎 + 唯一端点，无降级链。DDG 的 HTML 端点在需要代理的网络
环境下必然 ConnectTimeout，而代码里没有第二个可达的免 key 搜索源。

## 修复方案

三层降级链，全部失败后再给模型可操作的引导：

| 层 | 实现 | 说明 |
|---|---|---|
| 1. HTTP 引擎轮询 | [search_engine.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/web/search_engine.py) `_SEARCH_ENGINES` | 顺序：**Bing**（国内可直连，GET `bing.com/search` 静态 HTML）→ **DuckDuckGo HTML**（原实现迁入）→ **DDG lite**（备胎端点）。单引擎超时 10s；抛异常与「解析到 0 结果」都算失效，降级下一个引擎 |
| 2. 本地浏览器兜底 | [browser.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/web/browser.py) `search_via_browser()` | 复用 BrowserManager 懒启动无头 Chromium，但**开临时 page 搜索、用完即关**：不导航、不改变模型正在操作的共享页面；浏览器若是本次自启的，搜索完整体释放。真实渲染可穿过 JS 反爬 |
| 3. 全挂引导 | [web.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/agent/tools/web/web.py) `WebSearchTool.call()` 失败回执 | 回执列出每个通道的失败原因 + 明确建议：「若已注册带搜索能力的 MCP 工具（Exa/Tavily/Bocha 等）请改调用该工具；否则提示用户检查网络/代理」。MCP 工具名因服务器而异，框架不盲目自动调，交由模型按引导决策 |

### 关键实现点

- **引擎与解析分离**：`parse_bing_html` / `parse_ddg_html` /
  `parse_ddg_lite_html` 为纯函数，单测用本地 HTML 样例覆盖，不触网。
- **HTTP 层策略不变**：requests 优先、ImportError 降级 urllib，线程池执行。
- **0 结果也算失效**：引擎改版导致解析不出结果时同样降级，而不是把空结果
  直接回给模型。
- **来源标注**：走浏览器兜底成功的结果，输出头部带 `[兜底通道]` 说明，让
  模型知道数据来自降级链路。
- **playwright 未装**：`_browser_search_fallback` 捕获 ImportError，返回
  「不可用」说明并继续走第 3 层引导，不影响基础功能。

## 测试

新增 [tests/tools/test_web_search.py](file:///e:/2.MyProjects/MyAgentChat/J.A.R.V.I.S/jarvis/tests/tools/test_web_search.py)（15 用例）：

- 三种结果页 HTML 解析（含 uddg 重定向还原、max_results 截断、引擎顺序断言）
- 轮询链：首引擎异常降级、0 结果降级、浏览器兜底成功标注、全挂时 MCP 引导
- `search_via_browser`：元素提取/去重/临时页关闭/自启浏览器释放/共享页不被导航污染

全部 mock，不依赖网络与 playwright。验证：

```powershell
python -m pytest tests/tools/test_web_search.py -q   # 15 passed
python -m pytest tests/ -q                            # 1940 passed
```

## 遗留与边界

- Bing 若未来对裸 requests 加 JS 校验返回 0 结果，会自动落到 DDG 链/浏览器
  兜底，行为可控。
- 走代理环境下各引擎均可达，本改造不影响 `HTTPS_PROXY` 生效路径。
- MCP 搜索工具自动发现调用（而非引导）可作为后续增强，需要定义「搜索类
  工具」的识别约定。
