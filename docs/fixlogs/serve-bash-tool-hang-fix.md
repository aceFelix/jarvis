# 修复复盘：Bash 工具"卡死"——非零退出被误判为工具失败引发阻塞询问

- **日期**：2026-10-02
- **症状**：jarvis 调用 Bash 工具"老是卡死"：桌面端工具组长期停在"〈执行中…〉"，
  终端 CLI 则打印 `Bash 遇到 未知错误，1.0 秒后第 1/1 次重试...` 后阻塞在
  `是否重试一次? [y] 重试 / [n] 放弃:` 等待键盘输入，整轮回复无法收尾。
- **作者**：aceFelix

## 一、复现场景

用户让 jarvis"用火狐浏览器打开 jarvis 网站"，模型拼出组合命令：

```bash
for p in "/c/Program Files/Mozilla Firefox/firefox.exe" ...; do
  [ -f "$p" ] && echo "FOUND: $p"
done
echo "--- 站点存活 ---"
curl -s -o /dev/null -w "5174 -> HTTP %{http_code}\n" --max-time 10 http://localhost:5174
```

dev server 没起来 → `curl` 连不上返回 **exit 7**（HTTP 000），整条命令非零退出。
命令本身瞬间就跑完了——"卡死"的不是命令，而是框架后续的处理链路。

## 二、根因（三层连锁）

1. **Bash 工具把非零退出当工具失败**（`agent/tools/bash.py`）：
   `exit_code != 0` 即返回 `ToolResult(is_error=True)`。但 shell 非零退出是极常见
   的正常结果（curl 连不上=7、grep 无匹配=1、`&&` 断链等），应由 LLM 看到
   `[exit=N]` 与输出后自行决策，而不是当作工具级异常。
2. **自愈分类器认不出来 → "未知错误"**（`agent/core/error_recovery.py`）：
   分类器只对输出文本做正则匹配，`[exit=7]` + 文件列表 + `HTTP 000` 不含任何
   已知关键字 → 落入兜底 `UNKNOWN`（reason=未知错误，recoverable=False）。
3. **UNKNOWN 仍重试一次后弹阻塞式"是否重试"问句**：
   UNKNOWN 策略 `max_retries=1, ask_user_on_fail=True` → 先打印
   "Bash 遇到 未知错误，1.0 秒后第 1/1 次重试..."（同参重跑必然同样失败），
   再调 `ask_user` / `ask_user_async` 等用户应答：
   - 终端 CLI：`Prompt.ask` 硬阻塞 stdin，人不在终端前 = 冻结；
   - 桌面/serve：`WorkbenchUI.ask_user_async` 等前端应答条回填，
     用户没注意到或不应答就干等 **600 秒超时**，整轮 query 不结束，
     UI 表现为"执行中…卡死"（只能靠"停止"按钮解救）。

> 桌面端 ask_user 闭环本身是完好的（dispatcher `ask_user` 事件 →
> ChatArea 应答条 → `answer_user` 指令回填），问题在于这个问句根本不该出现——
> 它是对一条正常执行完的命令发出的"假问题"。

## 三、修复

### 1. bash.py：非零退出属正常结果（治本）

`_call_normal` 与 `_call_sandboxed` 两条路径删除 `exit_code != 0 → is_error=True`
判定，统一 `ToolResult.ok(data=header + body)` 返回——`[exit=N]` 表头与
stdout/stderr 原样回传给 LLM 自行判断。真正的工具级失败（找不到 shell、
超时、沙箱错误）仍走 error 分支。工具 description 同步声明该口径。

### 2. error_recovery.py：UNKNOWN 不再阻塞询问用户（纵深防御）

`DEFAULT_POLICIES[UNKNOWN]` 改为 `ask_user_on_fail=False`：无法分类的错误
同参重试无意义，重试一次耗尽后直接 fail-fast 把错误交回 LLM，绝不弹
"是否重试"问句。可分类的可恢复错误（网络/超时/文件缺失等）保留询问能力，
终端与桌面应答闭环均已验证可用。

## 四、验证

- `tests/test_core_tools.py`：`test_nonzero_exit_is_normal_result`（原
  `test_nonzero_exit_is_error` 反转口径）、`test_sandbox_nonzero_exit` 改断言
  `is_error is False` 且 `[exit=2]`/stderr 原样回传；
- `tests/core/test_error_recovery.py`：新增 `test_unknown_policy_never_asks_user`、
  `test_unknown_error_fails_fast_without_asking`（准备了应答也不应被消费）；
- `jarvis-desktop test/renderer/dispatcher.test.ts`：新增 `ask_user` 事件 →
  `askPrompt` 应答条状态链路测试（桌面 ask_user 闭环回归保护）；
- 全量：`uv run pytest` / `npm run typecheck` / `npx vitest run` / `npm run build` 通过。

## 五、可复用的教训

- **不要用"进程退出码非零"表达"工具执行失败"**：工具级失败（没跑起来）与
  命令级失败（跑完了但业务失败）是两个层面，后者是 LLM 决策所需的正常信息。
- **自愈链路对"认不出"的错误必须 fail-fast**：分类兜底为 UNKNOWN 时，
  重试与交互询问都缺乏判断依据；阻塞式问句在任何非纯终端宿主里都是
  定时炸弹（600s 超时等待 = 用户眼中的卡死）。
- 同类前案：`serve-toolsearch-registration-fix.md` 中 MCP 误判也走过这条
  "未知错误→重试→ask_user 卡死"链路，当时只修了上游触发条件，本次把
  链路上的两处放大环节（is_error 判定、UNKNOWN 询问）一并掐断。
