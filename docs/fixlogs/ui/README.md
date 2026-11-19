# 终端 · 驾驶舱 · 爬虫窗

> [修复与复盘日志索引](../README.md) 的第 9 类，共 16 篇。
> 新增一篇时在本表补一行；文件名与路径保持 `<主题>-<简短描述>-fix.md`。

| 文件 | 主题 |
|---|---|
| [cli-ask-user-bracket-eaten-fix.md](cli-ask-user-bracket-eaten-fix.md) | `[y]`/`[a]` 按键名被 Rich 标记吞掉 |
| [cli-clipboard-image-explicit-paste-fix.md](cli-clipboard-image-explicit-paste-fix.md) | 剪贴板图片改 Ctrl+V 显式粘贴 |
| [cmd-thinking-panel-legacy-console-fix.md](cmd-thinking-panel-legacy-console-fix.md) | cmd legacy 控制台思考面板不显示 |
| [thinking-live-leak-and-gui-window-close-fix.md](thinking-live-leak-and-gui-window-close-fix.md) | 思考面板 Live 残留 + 火狐被收尾关闭 |
| [thinking-panel-flood-fix.md](thinking-panel-flood-fix.md) | 思考面板刷屏（限流 + 截断） |
| [cockpit-chat-pops-fix.md](cockpit-chat-pops-fix.md) | 纯闲聊也弹任务驾驶舱 |
| [cockpit-window-close-exit-fix.md](cockpit-window-close-exit-fix.md) | 驾驶舱关闭致退出 + 无法自动重现 |
| [cockpit-elapsed-breakdown-fix.md](cockpit-elapsed-breakdown-fix.md) | 驾驶舱耗时拆成模型/工具（两个口径同屏） |
| [crawl-cdn-cgi-dedup-fix.md](crawl-cdn-cgi-dedup-fix.md) | 爬虫驾驶舱：Cloudflare 噪音与去重口径 |
| [crawl-cockpit-p35.md](crawl-cockpit-p35.md) | 爬虫驾驶舱 P3.5（滚动与拓扑交互） |
| [crawl-cockpit-split-900.md](crawl-cockpit-split-900.md) | 驾驶舱拆分（纯逻辑层独立成包） |
| [crawl-index-page-canonicalization.md](crawl-index-page-canonicalization.md) | 目录默认页归一化（`/index.html ≡ /`） |
| [crawl-topology-detach.md](crawl-topology-detach.md) | 拓扑撕出独立窗（P3.6） |
| [maximized-boot-resize-garble-fix.md](maximized-boot-resize-garble-fix.md) | 最大化启动后缩窗画面错乱 |
| [doctor-install-cmd-markup-fix.md](doctor-install-cmd-markup-fix.md) | `--doctor` 安装命令的 extras 被 Rich 吃掉 |
| [doctor-redirect-encoding-fix.md](doctor-redirect-encoding-fix.md) | `--doctor > log.txt` 崩（GBK 严格编码） |
