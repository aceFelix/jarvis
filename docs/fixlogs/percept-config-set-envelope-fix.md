# 修复：感知源面板所有开关点了没反应（协议信封键 `type` 混进配置白名单校验）

- **日期**：2026-10-18
- **作者**：aceFelix
- **影响范围**：`agent/serve/rpc_percept.py` 的 `percept.config.set` —— 桌面壳「管家服务 →
  感知源」面板的**全部写操作**（总开关、逐源开关、留痕开关、采集名单表单）
- **严重级别**：高（W2 第 5、6 步交付的写侧在真实客户端下 100% 不可用；读侧正常，
  所以面板看起来"是好的"，只有点下去没反应）

## 现象

W2 第 7 步实机走查（阶段一第 8 步：在面板里拨总开关）时：

1. 点「感知层总开关」以及下面每一个逐源开关，**界面毫无变化**（灰色拨块仍是灰色）；
2. `~/.jarvis/settings.toml` 里**始终没有出现 `[percept]` 段**——写回从未落盘；
3. 唯一的现象就是"点了没反应"，没有任何报错提示。

复现步骤（最小，不依赖桌面壳）：

```bash
cd jarvis
./.venv/Scripts/python.exe -m agent.serve        # 记下 stdout 握手 JSON 里的 port 与 token
# 另开终端，用裸 WS 打两条指令
#   {"type": "percept.status.get"}                 → ok=true
#   {"type": "percept.config.set", "enabled": true} → ok=false
#                                                    error="ValueError: 不支持的感知配置项 type"
```

## 排查过程

1. **先怀疑前端**。`PerceptSourcePanel` 里开关唯一的禁用条件是 `disabled={busy}`
   （写回在飞的那一瞬间）；`perceptActions.ts::savePerceptConfig` 全程 `try/catch`
   且 `ws.ts::sendCommand` 有 15 秒超时，所以既不会抛穿、也不会永久卡在 `busy`。
   前端"点不动"的假设不成立，但它能解释"没变化"——面板刻意不做乐观更新，
   写回失败就不动本地状态（这是对的，问题不在这里）。
2. **打真实协议验证后端**。用临时 `HOME` 起一个独立 serve（不碰用户真实配置），
   裸 WS 依次发 `percept.status.get` 与 `percept.config.set`：读侧 `ok=true`，
   写侧 `ok=false`，错误是 `不支持的感知配置项 type`。**`type` 不是配置项，是指令名。**
3. **对照同框架的兄弟实现找差异**。`agent/config/email_settings.py::validate_email_config`
   对未知键是 `continue`（静默忽略），所以邮箱面板从来没踩到这个坑；而
   `percept_settings.py::validate_percept_config` 按"白名单外的键**直接报错**"实现
   ——这个设计本身是有意的（面板是机器拼的 payload，静默忽略会把"打不开的开关"
   变成"点了没反应"），但它没考虑传输层信封键。
4. **追问为什么 2783 条单测全绿却漏了它**。`tests/serve/test_serve_percept_rpc.py`
   所有用例都直接调 handler 并传**干净的业务 dict**
   （`server._rpc_percept_config_set({"enabled": True})`），完全绕过了 WS 帧结构。
   传输层这个变量在测试里根本不存在，所以缺陷不可能被测出来。

## 根因

serve 协议是**平铺帧**：请求为 `{"type": "<指令名>", ...参数}`（见 `agent/serve/protocol.py`
头部注释与桌面壳 `src/renderer/src/api/ws.ts:145` 的
`JSON.stringify({ type, ...params })`），分发时整份 dict 原样交给 handler（`server.py`
的 `result = fn(data)`）。因此**任何** RPC handler 收到的 `data` 里必然带一个 `type` 键。

`percept.config.set` 把这个 `type` 当成配置项送进了严格白名单校验 → 每条写回必抛
`ValueError` → 回执 `ok=false` → 面板按"不动本地状态"的纪律保持原样 → 用户看到的就是
"所有开关都点不动"。错误提示虽然正常渲染了，但它落在面板**底部滚动区之外**，
用户不往下滚就完全看不见，于是连"报错了"这个信息都没送达。

## 修复方案

在**传输边界**剥掉信封键，配置层的严格白名单一字未动：

```python
#: agent/serve/rpc_percept.py
TRANSPORT_ENVELOPE_KEYS = frozenset({"type", "id"})

patch = validate_percept_config(
    {k: v for k, v in data.items() if k not in TRANSPORT_ENVELOPE_KEYS}
)
```

选择这个落点而不是另外两处，理由：

- **不在 `validate_percept_config` 里忽略未知键**：那等于放弃"面板拼错键要当场报错"的
  设计意图，把这次这类缺陷重新变回静默失败；
- **不在 `server.py` 分发处统一剥 `type`**：其他 handler 依赖整帧字段（如
  `_rpc_percept_status_get` 读 `event_limit`、邮箱侧读 `subject/body/to`），改分发层
  影响面波及全部 RPC，而本缺陷只需要 percept 写侧一处收口。

剥完仍然保留三条不变量：业务键里的未知键照报（`{"type":…, "dedup_window_s":10}` →
`不支持`）、只带信封不带配置项照报（`至少需要一个有效的配置项`）、类型不符照报。

## 验证结果

- **裸 WS 探针复跑**：`config.set ok=True`、`hot_applied=True`、`enabled=True`，
  回读快照里 `window` 源 `enabled/bus_enabled` 均为 `true`（说明落盘 → 运行时 →
  重装配 → 回读整条链路通了）。
- **新增 3 条回归用例**（`tests/serve/test_serve_percept_rpc.py`）：真实平铺帧写通 /
  信封键不遮蔽未知键 / 只带信封仍报错。该文件 17 → **20 passed**。
- **全量回归**：`pytest -q` → 2785 passed + 1 failed。唯一失败是
  `tests/test_sandbox_more.py::test_run_timeout`，与本缺陷无关：它用裸 `python`
  起子进程，而本机 PATH 里没有 `python`（只有 venv），子进程 FileNotFound 导致
  超时断言假失败。已改用 `sys.executable`（路径加引号、正斜杠避开转义），
  复跑 `tests/test_sandbox_more.py` + 感知 RPC 两文件 **91 passed**，
  最终全量 **2786 passed / 0 failed**（修前基线 2783 passed + 1 环境性失败）。

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/serve/rpc_percept.py` | 新增 `TRANSPORT_ENVELOPE_KEYS` 常量；`_rpc_percept_config_set` 校验前剥掉协议信封键（`type`/`id`），并注释说明为何必须在 RPC 边界处理 |
| `tests/serve/test_serve_percept_rpc.py` | 新增 3 条走真实平铺帧的回归用例（17 → 20） |
| `tests/test_sandbox_more.py` | `test_run_timeout` 改用 `sys.executable` 替代裸 `python`，消除 PATH 依赖导致的假失败 |
| `docs/fixlogs/percept-config-set-envelope-fix.md` | 本复盘 |
| `docs/architecture/17-W2感知层.md` | 测试条目计数同步（percept_rpc 17 → 20）并链接本复盘 |

## 经验总结

- **RPC 单测至少要有一条走真实帧结构**。直传业务 dict 会把"传输层字段"这个变量从
  测试中抹掉，形成**单测全绿、真机全红**的盲区。凡是 `{type, ...params}` 平铺的协议，
  handler 的入参里就一定有 `type`——凡对入参做"未知键即报错"的严格校验，都必须先剥信封。
- **兄弟实现的差异是最省力的线索**。"邮箱面板能用、感知面板不能用"这一条对比，
  直接指向 `continue` 与 `raise` 的校验策略差异；同一框架里接入新的严格校验时，
  第一个要问的就是"信封键怎么办"。
- **错误可见性属于功能**。这次报错其实正常产生并渲染了，但落在滚动区之外，
  用户拿到的唯一信息就是"点了没反应"。写侧失败的提示必须出现在无需滚动即可看到的
  位置（面板顶部或浮层），否则"绝不静默失败"的设计在用户体验上仍然等于静默失败。
- **人工走查不可替代**。这条链路（前端 → WS → 校验 → 落盘 → 重装配 → 回读）分段单测
  全绿，第一次串起真实协议就断在第二跳。交付规则里"核心流程至少一次人工验证"
  不是形式主义，它抓到的正是这类"每段都对、合起来不通"的缺陷。

## 同批次另两项（走查中一并发现，已修）

这两项不是同一个根因，但都是同一次走查在同一个面板上抓出来的，一并记在这里。

1. **写侧错误提示落在滚动区之外**（jarvis-desktop）：`PerceptSourcePanel` 把
   错误/保存回执渲染在整个面板的最底部，而面板随外层容器整体滚动，长面板里
   用户不滚到底就看不见——本次缺陷因此被伪装成“开关坏了”。修法：新增
   `.percept-feedback` 反馈条，紧跟子返回头并 `position: sticky`（底色取
   `--qr-panel-bg`，三套皮肤各自定义，内容从下方穿过仍可读）；成功回执不再复用
   邮件表单的整块大字 `.butler-email-sent`，改用紧凑单行 `.percept-saved`。
   `percept.test.tsx` 新增一条断言：反馈条在文档序上必须排在总览之前。
2. **状态行口径误导**（两仓）：`assembled` 取的是 `hub_enabled`（宿主在不在跑），
   标签却写“总线装配：已装配”，总开关关着时与下一行“运行状态：未运行”自相
   矛盾——文案改成「后端宿主：已接线 / 未接线」，真正的采集判据仍看 `bus_running`。
   同一行的 `log_file` 也有同类问题：总线未跑时后端回空串，前端空值兜底文案是
   “未开启（关掉「本地留痕事件」后的状态）”，于是“留痕开关是绿的、文案却说已关”。
   修法分两端：后端 `status.py` 新增 `_log_file()` 按三态回值（真关→空 / 总线在跑→
   实际路径 / 开着但没跑→默认路径），前端只在真关时显「已关闭」，拿不到路径时
   显式说“总线未运行（暂无路径）”，兼容老后端。
   新增 `test_log_file_three_states`（jarvis）与「留痕开关真关时才显已关闭」
   （desktop）各一例锁住口径。
