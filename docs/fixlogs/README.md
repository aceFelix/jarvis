# 修复与复盘日志索引（fixlogs）

> 本目录收录 `<模块或功能>-<简短描述>-fix.md` 形式的修复 / 优化复盘，规则见项目 `bugfix-review.md`：
> 七段式（问题现象 → 排查过程 → 根因分析 → 修复方案 → 验证结果 → 涉及文件 → 经验总结）。
>
> **按主题分 11 个子目录**，每个子目录自带索引（本页只做总览）。
> 新增一篇时放进对应子目录，并在**该子目录的 README** 登记一行。

## 分类总览

| # | 分类 | 篇数 | 目录 |
|---|---|---:|---|
| 1 | 语音系统 | 26 | [voice/](voice/README.md) |
| 2 | 模型与 Provider | 13 | [llm/](llm/README.md) |
| 3 | Agent 运行时与工具调用 | 15 | [agent-runtime/](agent-runtime/README.md) |
| 4 | 权限 · 撤销 · 任务脊柱 · 多 Agent | 10 | [w1-w3/](w1-w3/README.md) |
| 5 | W2 感知与打扰预算 | 5 | [percept/](percept/README.md) |
| 6 | 配置与自配置层 | 4 | [config/](config/README.md) |
| 7 | 会话 · 记忆 · 上下文 | 10 | [session-memory/](session-memory/README.md) |
| 8 | 桌面壳与 serve 桥接 | 12 | [desktop-serve/](desktop-serve/README.md) |
| 9 | 终端 · 驾驶舱 · 爬虫窗 | 16 | [ui/](ui/README.md) |
| 10 | 扩展生态 | 5 | [ecosystem/](ecosystem/README.md) |
| 11 | CI · 测试 · 发布与文档门禁 | 10 | [ci-release/](ci-release/README.md) |
| | **合计** | **126** | |

---

## 新增一篇时

1. **命名**：`<模块或功能>-<简短描述>-fix.md`；纯交付/设计类可用 `-delivery` / `-report` / `-upgrade` 后缀。
2. **结构**：按 `bugfix-review.md` 七段式写全，禁止只写「已修复」。
3. **归档**：放进对应子目录；在该子目录 `README.md` 补一行（文件 + 一句话）；
   没有合适分类就新建目录，并在本文「分类总览」加一行。
4. **联动**：若同时改变行为/接口/配置，按 `document-update.md` 同步 README、架构文档与
   `docs/log/CHANGELOG.md`。
5. **提交**：与代码同一次提交，commit message 里注明本复盘文件名。
