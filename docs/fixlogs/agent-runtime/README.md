# Agent 运行时与工具调用

> [修复与复盘日志索引](../README.md) 的第 3 类，共 15 篇。
> 新增一篇时在本表补一行；文件名与路径保持 `<主题>-<简短描述>-fix.md`。

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
