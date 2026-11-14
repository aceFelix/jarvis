# 修复与复盘日志索引（fixlogs）

> 本目录收录 `<模块或功能>-<简短描述>-fix.md` 形式的修复 / 优化复盘，规则见项目 `bugfix-review.md`：
> 七段式（问题现象 → 排查过程 → 根因分析 → 修复方案 → 验证结果 → 涉及文件 → 经验总结）。
>
> **为什么不用子目录物理分组**：全仓大量文档以相对路径引用这里的文件，`aoci.code.txt` 也为每个文件
> 建了索引对象——搬目录会同时打断链接与认知索引（需重写全仓链接 + 重建 ~120 条 AOCI 对象），
> 代价远大于收益。因此用**分类索引**解决"找得到"的问题，文件位置保持不变。
>
> 新增一篇后，请在下方对应分类补一行（文件名 + 一句话）。

## 分类总览

| # | 分类 | 篇数 |
|---|---|---:|
| 1 | [语音系统（/voice · /talk · TTS/STT/唤醒）](#1-语音系统voice--talk--ttsstt唤醒) | 26 |
| 2 | [模型与 Provider](#2-模型与-provider) | 13 |
| 3 | [Agent 运行时与工具调用](#3-agent-运行时与工具调用) | 15 |
| 4 | [W1/W3：权限 · 撤销 · 任务脊柱 · 多 Agent](#4-w1w3权限--撤销--任务脊柱--多-agent) | 10 |
| 5 | [W2 感知与打扰预算](#5-w2-感知与打扰预算) | 5 |
| 6 | [配置与自配置层](#6-配置与自配置层) | 4 |
| 7 | [会话 · 记忆 · 上下文](#7-会话--记忆--上下文) | 5 |
| 8 | [桌面壳与 serve 桥接](#8-桌面壳与-serve-桥接) | 12 |
| 9 | [终端 · 驾驶舱 · 爬虫窗（UI）](#9-终端--驾驶舱--爬虫窗ui) | 15 |
| 10 | [扩展生态（MCP · harness · 邮箱 · 网页）](#10-扩展生态mcp--harness--邮箱--网页) | 5 |
| 11 | [CI · 测试 · 发布与文档门禁](#11-ci--测试--发布与文档门禁) | 10 |
| | **合计** | **120** |

---

## 1. 语音系统（/voice · /talk · TTS/STT/唤醒）

| 文件 | 主题 |
|---|---|
| [realtime-talk-auth-fix.md](realtime-talk-auth-fix.md) | `/talk` 鉴权失败（语音只认 DashScope key） |
| [realtime-talk-echo-guard-window.md](realtime-talk-echo-guard-window.md) | 外放回声自打断的"智能折中"方案 |
| [realtime-talk-echo-self-cancel-fix.md](realtime-talk-echo-self-cancel-fix.md) | 回声自打断导致 AI 完全不回复 |
| [realtime-talk-echo-self-talk-fix.md](realtime-talk-echo-self-talk-fix.md) | 回声自激与说话不全 |
| [realtime-talk-half-duplex.md](realtime-talk-half-duplex.md) | 外放"AI 说完不接话"的半双工方案 |
| [realtime-talk-response-done-fix.md](realtime-talk-response-done-fix.md) | `response.done` 缺失导致只能回答一句 |
| [realtime-talk-silent-turn-observability.md](realtime-talk-silent-turn-observability.md) | 静默轮排查与第一层可观测性 |
| [realtime-talk-tools-mode-default-fix.md](realtime-talk-tools-mode-default-fix.md) | `tools_mode=all` 导致语音不可用（回退 builtin） |
| [realtime-talk-voice-history-persist-fix.md](realtime-talk-voice-history-persist-fix.md) | 实时语音对话不进会话历史 |
| [realtime-talk-xiaoyun-persona-fix.md](realtime-talk-xiaoyun-persona-fix.md) | 桌面实时语音自称"小云"（人设丢失） |
| [talk-session-persist-fix.md](talk-session-persist-fix.md) | 终端 `/talk` 会话落库缺失 |
| [voice-api-key-onboarding-fix.md](voice-api-key-onboarding-fix.md) | 首启引导缺语音 Key（两套凭据） |
| [voice-exit-and-tts-drain-fix.md](voice-exit-and-tts-drain-fix.md) | Ctrl+C 退进程、TTS 尾音被误录、MCP 退出刷屏 |
| [voice-false-wake-fix.md](voice-false-wake-fix.md) | 无声音自动唤醒 / 对话幻听 |
| [voice-half-duplex-persist-fix.md](voice-half-duplex-persist-fix.md) | 半双工语音对话不进会话历史 |
| [voice-idle-standby-after-reply-fix.md](voice-idle-standby-after-reply-fix.md) | 长回答结束后立刻待机 |
| [voice-loop-tests-missing-deps-fix.md](voice-loop-tests-missing-deps-fix.md) | CI 上 voice 测试因缺 `[voice]` 依赖误败 |
| [voice-missing-deps-degrade-fix.md](voice-missing-deps-degrade-fix.md) | 缺依赖时 `/voice` 无限刷屏 |
| [voice-mode-fix.md](voice-mode-fix.md) | Voice Mode 三段式修复（听 / 想 / 说） |
| [voice-session-title-fix.md](voice-session-title-fix.md) | `/voice` 会话只有时间戳、不生成标题 |
| [voice-stop-unresponsive-fix.md](voice-stop-unresponsive-fix.md) | 桌面壳语音控制按钮无响应 |
| [voice-turn-done-fix.md](voice-turn-done-fix.md) | 语音轮缺收尾信号，气泡停不下来 |
| [voice-wake-word-homophone-fix.md](voice-wake-word-homophone-fix.md) | 唤醒词配置化 + 拼音同音容错 |
| [stt-session-updated-never-arrives-fix.md](stt-session-updated-never-arrives-fix.md) | 死等 `session.updated`，每轮录音前失败 |
| [tts-stream-lifecycle-fix.md](tts-stream-lifecycle-fix.md) | 流式 TTS：语音会断、尾音被截、新旧混播 |
| [standby-display-and-mcp-health-fix.md](standby-display-and-mcp-health-fix.md) | `<standby/>` 泄漏上屏 + MCP 就绪后健康不刷新 |

## 2. 模型与 Provider

| 文件 | 主题 |
|---|---|
| [add-zhipu-zai-provider.md](add-zhipu-zai-provider.md) | 新增智谱官方 ZhipuAi SDK Provider |
| [deepseek-anthropic-thinking-effort-passthrough-fix.md](deepseek-anthropic-thinking-effort-passthrough-fix.md) | DeepSeek 走 Anthropic 协议时思考档位不下发 |
| [deepseek-dsml-toolcall-leak-fix.md](deepseek-dsml-toolcall-leak-fix.md) | DSML 工具调用泄漏进正文的兜底 |
| [glm-tool-call-hang-fix.md](glm-tool-call-hang-fix.md) | 智谱 GLM 工具调用后无响应 |
| [provider-busy-error-classification-fix.md](provider-busy-error-classification-fix.md) | 厂商 503 引出六处缺陷（分类/日志/面板等） |
| [shared-http-pool-close-kills-chat.md](shared-http-pool-close-kills-chat.md) | 临时 provider 关掉共享连接池，第二句必挂 |
| [model-config-split.md](model-config-split.md) | 模型配置拆分到 `models.toml` |
| [model-edit-hot-reload-fix.md](model-edit-hot-reload-fix.md) | `/models` 改完仍连旧端点（同名短路） |
| [model-hot-switch.md](model-hot-switch.md) | 切换后列表仍显示旧模型为当前 |
| [model-switch-vendor-endpoint-fix.md](model-switch-vendor-endpoint-fix.md) | 切回 `base_url` 留空的自定义模型时 404 |
| [llm-models-header-missing-fix.md](llm-models-header-missing-fix.md) | 缺 `[llm.models]` 表头 + 解析失败被吞 |
| [response-optimization-round1.md](response-optimization-round1.md) | 响应优化第一轮（109 例无回归） |
| [token-stats-compact-cache-fix.md](token-stats-compact-cache-fix.md) | `/cost`、`/context`、`/compact` 在缓存命中下算错 |

## 3. Agent 运行时与工具调用

| 文件 | 主题 |
|---|---|
| [agent-loop-stall-fix.md](agent-loop-stall-fix.md) | 空转熔断 + 循环拦截（贪吃蛇 15 分钟零产出） |
| [dangling-tool-use-fix.md](dangling-tool-use-fix.md) | 悬空 `tool_use` 使会话永久失效 |
| [ambiguous-instruction-clarify-first.md](ambiguous-instruction-clarify-first.md) | 模棱两可的话先问清楚（交互行为复盘） |
| [capability-tool-name-contract-fix.md](capability-tool-name-contract-fix.md) | 能力契约把"没装"当成"工具不存在" |
| [snake-visual-claim-fix.md](snake-visual-claim-fix.md) | 声称"已用浏览器验证"但本机无内核 |
| [schedule-prompt-conditional-fix.md](schedule-prompt-conditional-fix.md) | 追着不存在的提醒工具空转（提示段未条件化） |
| [toolsearch-title-failed-step-fix.md](toolsearch-title-failed-step-fix.md) | 一次网页抓取暴露的三处体验问题 |
| [bash-background-command-wait-fix.md](bash-background-command-wait-fix.md) | MSYS2 bash 会等后台 job，卡满超时 |
| [bash-compound-background-fix.md](bash-compound-background-fix.md) | 复合后台命令仍卡满 timeout |
| [bash-timeout-retry-and-abort-gap-fix.md](bash-timeout-retry-and-abort-gap-fix.md) | 超时被当未知错误重试 + Ctrl+C 无效 |
| [operation-visibility-bash-multiline-sandbox-fix.md](operation-visibility-bash-multiline-sandbox-fix.md) | 多行后台命令误入沙箱 + 窗口标题误配 |
| [unknown-command-silent-drop.md](unknown-command-silent-drop.md) | 未注册指令被静默忽略，前端只见超时 |
| [dev-server-tool-fix.md](dev-server-tool-fix.md) | DevServer 子进程抢 stdin 导致卡死 |
| [dev-server-stop-fix.md](dev-server-stop-fix.md) | DevServer 只进不出（语音模式关不掉） |
| [query-lock-cancel-leak-fix.md](query-lock-cancel-leak-fix.md) | 三通道 query 锁"等锁窗口被取消"泄漏 |

## 4. W1/W3：权限 · 撤销 · 任务脊柱 · 多 Agent

| 文件 | 主题 |
|---|---|
| [w3-delay-window-bypass-fix.md](w3-delay-window-bypass-fix.md) | 不可逆延时窗口被等价路径（curl）绕开 |
| [w3-driver-approval-blocked-report.md](w3-driver-approval-blocked-report.md) | 被拦步骤报成"已完成"——四条断链 |
| [w3-entry-rpc-delivery.md](w3-entry-rpc-delivery.md) | 建目标三入口 + serve RPC 交付复盘 |
| [w3-journal-spine-delivery.md](w3-journal-spine-delivery.md) | 副作用账本 + 任务脊柱交付（4 个真缺陷） |
| [w3-tasks-blocked-approval-gap-fix.md](w3-tasks-blocked-approval-gap-fix.md) | 卡在"需要授权"时没有批准入口 |
| [message-rewind-checkpoint.md](message-rewind-checkpoint.md) | 消息级回溯（对话回退 + 文件回滚） |
| [desktop-checkpoint-blocks-10s.md](desktop-checkpoint-blocks-10s.md) | 同步 git 检查点让桌面每轮卡 10 秒 |
| [file-change-not-recoverable-fix.md](file-change-not-recoverable-fix.md) | 文件改错无法恢复（三条断链叠加） |
| [p0-multi-agent-collaboration-upgrade.md](p0-multi-agent-collaboration-upgrade.md) | 多 Agent 协作增强（团队/邮箱/任务列表） |
| [parallel-agent-output-interleave-fix.md](parallel-agent-output-interleave-fix.md) | 并行子代理输出表格错位 |

## 5. W2 感知与打扰预算

| 文件 | 主题 |
|---|---|
| [notify-budget-percept-relay-fix.md](notify-budget-percept-relay-fix.md) | 打扰预算被自己的冲刷路径旁路 |
| [reminder-notify-budget-bypass-fix.md](reminder-notify-budget-bypass-fix.md) | 提醒不过预算 + 静默时段写法无声失效 |
| [percept-config-set-envelope-fix.md](percept-config-set-envelope-fix.md) | 感知面板开关无反应（信封键混入白名单） |
| [percept-dedup-lost-on-hot-rebuild-fix.md](percept-dedup-lost-on-hot-rebuild-fix.md) | 热重装配丢去重账本，同一事件重播 |
| [percept-panel-history-lost-on-rebuild-fix.md](percept-panel-history-lost-on-rebuild-fix.md) | 改配置抹证据 + 静默时段绕过重要度门槛 |

## 6. 配置与自配置层

| 文件 | 主题 |
|---|---|
| [config-panel-not-refreshed-after-model-set-fix.md](config-panel-not-refreshed-after-model-set-fix.md) | 模型改完配置，面板滑杆仍是旧值 |
| [example-template-last-model.md](example-template-last-model.md) | 发布模板预置 `last_model` 导致分层失效 |
| [init-not-applied-to-session-fix.md](init-not-applied-to-session-fix.md) | `/init` 配完连不上（只写盘未进会话） |
| [serve-policy-config-not-injected-fix.md](serve-policy-config-not-injected-fix.md) | 桌面壳里 W1 预算全部不生效（漏 `init_policy`） |

## 7. 会话 · 记忆 · 上下文

| 文件 | 主题 |
|---|---|
| [session-auto-resume-removal.md](session-auto-resume-removal.md) | 下线 auto-latest 全局指针（多项目串台） |
| [session-ready-first-bubble-swallow.md](session-ready-first-bubble-swallow.md) | `session_ready` 清屏吞掉首发气泡 |
| [session-title-fix.md](session-title-fix.md) | 会话标题生成失效 |
| [profile-duplicate-memory-fix.md](profile-duplicate-memory-fix.md) | 画像记忆重复入库（存储层确定性查重） |
| [profile-refine-auth-fix.md](profile-refine-auth-fix.md) | 画像提炼鉴权失败（提炼模型 key 解析链） |

## 8. 桌面壳与 serve 桥接

| 文件 | 主题 |
|---|---|
| [jarvis-desktop-serve-realtest-fix.md](jarvis-desktop-serve-realtest-fix.md) | 桌面壳对接 serve 首轮实测五连修 |
| [desktop-cross-device-sync.md](desktop-cross-device-sync.md) | 跨设备协同接入（手机 PWA / 微信）设计 |
| [desktop-form-overflow-usage-hit-rate.md](desktop-form-overflow-usage-hit-rate.md) | 左栏表单溢出 + 用量命中率补齐 |
| [desktop-mode-thinking-controls.md](desktop-mode-thinking-controls.md) | 工作模式 + 思考强度两个选择器 |
| [desktop-slash-command-passthrough.md](desktop-slash-command-passthrough.md) | 斜杠命令透传（slash.exec + slash_bridge） |
| [desktop-thinking-selector-vendor-misjudge.md](desktop-thinking-selector-vendor-misjudge.md) | 思考选择器置灰（自定义模型厂商误判） |
| [serve-bash-hang-fix.md](serve-bash-hang-fix.md) | serve 下 Bash 卡死 + 新增停止回复 |
| [serve-bash-tool-hang-fix.md](serve-bash-tool-hang-fix.md) | 非零退出被误判为工具失败引发阻塞 |
| [serve-init-lost-before-connect.md](serve-init-lost-before-connect.md) | 启动期 init 事件丢失，面板永久离线 |
| [serve-mcp-truncation-fix.md](serve-mcp-truncation-fix.md) | 桌面缺 MCP 工具 + Anthropic 截断静默结束 |
| [serve-toolsearch-registration-fix.md](serve-toolsearch-registration-fix.md) | 有 MCP 工具却不用（ToolSearch 漏注册） |
| [workbench-gui-retired.md](workbench-gui-retired.md) | 三栏 GUI 工作台退役（只删窗口壳） |

## 9. 终端 · 驾驶舱 · 爬虫窗（UI）

| 文件 | 主题 |
|---|---|
| [cli-ask-user-bracket-eaten-fix.md](cli-ask-user-bracket-eaten-fix.md) | `[y]`/`[a]` 按键名被 Rich 标记吞掉 |
| [cli-clipboard-image-explicit-paste-fix.md](cli-clipboard-image-explicit-paste-fix.md) | 剪贴板图片改 Ctrl+V 显式粘贴 |
| [cmd-thinking-panel-legacy-console-fix.md](cmd-thinking-panel-legacy-console-fix.md) | cmd legacy 控制台思考面板不显示 |
| [thinking-live-leak-and-gui-window-close-fix.md](thinking-live-leak-and-gui-window-close-fix.md) | 思考面板 Live 残留 + 火狐被收尾关闭 |
| [thinking-panel-flood-fix.md](thinking-panel-flood-fix.md) | 思考面板刷屏（限流 + 截断） |
| [cockpit-chat-pops-fix.md](cockpit-chat-pops-fix.md) | 纯闲聊也弹任务驾驶舱 |
| [cockpit-window-close-exit-fix.md](cockpit-window-close-exit-fix.md) | 驾驶舱关闭致退出 + 无法自动重现 |
| [crawl-cdn-cgi-dedup-fix.md](crawl-cdn-cgi-dedup-fix.md) | 爬虫驾驶舱：Cloudflare 噪音与去重口径 |
| [crawl-cockpit-p35.md](crawl-cockpit-p35.md) | 爬虫驾驶舱 P3.5（滚动与拓扑交互） |
| [crawl-cockpit-split-900.md](crawl-cockpit-split-900.md) | 驾驶舱拆分（纯逻辑层独立成包） |
| [crawl-index-page-canonicalization.md](crawl-index-page-canonicalization.md) | 目录默认页归一化（`/index.html ≡ /`） |
| [crawl-topology-detach.md](crawl-topology-detach.md) | 拓扑撕出独立窗（P3.6） |
| [maximized-boot-resize-garble-fix.md](maximized-boot-resize-garble-fix.md) | 最大化启动后缩窗画面错乱 |
| [doctor-install-cmd-markup-fix.md](doctor-install-cmd-markup-fix.md) | `--doctor` 安装命令的 extras 被 Rich 吃掉 |
| [doctor-redirect-encoding-fix.md](doctor-redirect-encoding-fix.md) | `--doctor > log.txt` 崩（GBK 严格编码） |

## 10. 扩展生态（MCP · harness · 邮箱 · 网页）

| 文件 | 主题 |
|---|---|
| [mcp-prewarm-first-send.md](mcp-prewarm-first-send.md) | MCP 连接后台预热，首条消息秒进 |
| [email-box-readonly-read-reply.md](email-box-readonly-read-reply.md) | EmailBox 只读读取/检索/回复落地 |
| [websearch-multi-engine-fallback.md](websearch-multi-engine-fallback.md) | WebSearch 多引擎 fallback + 浏览器兜底 |
| [wps-harness-param-usage-loop-fix.md](wps-harness-param-usage-loop-fix.md) | WPS harness 传参死循环 |
| [wps-window-cleanup-foreground-fix.md](wps-window-cleanup-foreground-fix.md) | 任务收尾关窗失效（前台窗口误判） |

## 11. CI · 测试 · 发布与文档门禁

| 文件 | 主题 |
|---|---|
| [ci-linux-screenshot-and-yolo-fix.md](ci-linux-screenshot-and-yolo-fix.md) | Linux CI：缺 Pillow + C 盘保护域平台假设 |
| [ci-python314-and-fakegreen-fix.md](ci-python314-and-fakegreen-fix.md) | Python 3.14 口径 + 一条假绿 + 缺依赖未 skip |
| [maintenance-tests-linux-ci-fix.md](maintenance-tests-linux-ci-fix.md) | 系统维护层测试在 Linux CI 整片失败 |
| [release-contract-rulebook-linux-skip-fix.md](release-contract-rulebook-linux-skip-fix.md) | release-contract 规则库用例在 Linux 误红 |
| [md-link-checker-linux-ci-fix.md](md-link-checker-linux-ci-fix.md) | 死链扫描在 Linux CI 误报全仓 |
| [md-link-checker-untracked-link-fix.md](md-link-checker-untracked-link-fix.md) | 链接指向被 .gitignore 排除的本地文件 |
| [test-coverage-bugfixes-fix.md](test-coverage-bugfixes-fix.md) | 补测试暴露的 4 个源码 bug |
| [undefined-name-imports-fix.md](undefined-name-imports-fix.md) | `/doctor` 打崩会话（F821 全仓扫出 17 处） |
| [window-follow-throttle-uptime-fix.md](window-follow-throttle-uptime-fix.md) | 窗口跟随首动作被节流（Linux CI uptime 回归） |
| [workbench-single-instance-release-fix.md](workbench-single-instance-release-fix.md) | 单实例守卫端口释放竞态（Linux CI 失败） |

---

## 新增一篇时

1. **命名**：`<模块或功能>-<简短描述>-fix.md`（如 `dev-server-tool-fix.md`）；纯交付/设计类可用
   `-delivery` / `-report` / `-upgrade` 后缀（如 `w3-entry-rpc-delivery.md`）。
2. **结构**：按 `bugfix-review.md` 七段式写全，禁止只写"已修复"。
3. **登记**：在本文对应分类补一行（文件 + 一句话）；没有合适分类就新开一节并更新"分类总览"计数。
4. **联动**：若同时改变行为/接口/配置，按 `document-update.md` 同步 README、架构文档与
   `docs/log/CHANGELOG.md`。
5. **提交**：与代码同一次提交，commit message 里注明本复盘文件名。
