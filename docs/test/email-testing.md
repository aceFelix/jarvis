# SendEmail 邮件发送功能测试指南

## 测试原理

`SendEmail` 是 Jarvis 的内置工具，通过 Python `smtplib.SMTP_SSL` 连接 SMTP 服务器发送邮件。

配置来源：

1. `ToolContext.settings`（运行时传入，优先级最高）
2. `~/.jarvis/settings.toml` 中的 `[email]` 表
3. 工具参数：`to` / `subject` / `body` / `cc` / `bcc` / `attachments`

默认权限为 **ASK**，发送前需要用户确认。

## 环境准备

在 `~/.jarvis/settings.toml` 中启用并填写邮件配置（以 163 邮箱为例）：

```toml
[email]
enabled = true
smtp_host = "smtp.163.com"
smtp_port = 465
smtp_user = "your_163_email@163.com"
smtp_password = "your_authorization_code"   # 163 邮箱授权码，不是登录密码
sender = "your_163_email@163.com"
default_recipient = "13985465782@136.com"
```

> **安全提示**：`smtp_password` 是邮箱授权码，不要写成登录密码，也不要提交到 Git 仓库。

## 测试清单

| ID | 测试项 | 类型 | 验证方式 |
|---|---|---|---|
| TC-01 | 工具已注册到 ToolRegistry | 功能 | `build_default_registry()` 后查找 `SendEmail` |
| TC-02 | settings.toml `[email]` 配置解析 | 功能 | `load_settings()` 读取邮件字段 |
| TC-03 | 邮件功能关闭时拒绝发送 | 边界 | enabled=false 返回明确错误 |
| TC-04 | 缺少 smtp_user/password/sender 时拒绝 | 边界 | 配置不完整返回明确错误 |
| TC-05 | 未指定收件人且无 default_recipient 时拒绝 | 边界 | 返回明确错误 |
| TC-06 | 附件路径不存在时拒绝 | 边界 | 返回"附件不存在" |
| TC-07 | 成功发送纯文本邮件 | 功能 | 调用后收到邮件 |
| TC-08 | 成功发送带附件邮件 | 功能 | 邮件包含附件 |
| TC-09 | 指定收件人覆盖 default_recipient | 功能 | to 参数生效 |
| TC-10 | 抄送/密送生效 | 功能 | cc/bcc 收件人收到邮件 |
| TC-11 | `EmailBox` 已注册且 read/search 只读放行 | 功能 | `build_default_registry()` 后查找 `EmailBox`；`is_read_only` 为 True |
| TC-12 | 搜索返回 uid 列表（新的在前） | 功能 | 假 IMAP 断言 SEARCH 条件与序列 |
| TC-13 | 回复自动带 `In-Reply-To` / `References` | 功能 | 假 IMAP + 假 SMTP，断言报文头 |
| TC-14 | `reply` 必须 ASK 且不可逆 | 边界 | `check_permissions` 返回 ASK、`is_irreversible` 为 True |
| TC-15 | 只配 SMTP 也能收件 | 功能 | 仅填 `smtp_*` 时 `imap_host` 推导为 `imap.<域名>`，search/read 可用 |
| TC-16 | 配置缺失不重试 | 边界 | 配置类错误 `retryable=False`，日志不再出现“N 秒后第 k/N 次重试” |

## 测试用例

### TC-01 工具注册

```python
from agent.core.tool import build_default_registry

registry = build_default_registry()
assert "SendEmail" in registry
print("SendEmail 已注册")
```

**通过标准**：`"SendEmail" in registry` 为 True。

---

### TC-02 配置解析

```python
from agent.config.settings import load_settings

s = load_settings()
print(s.email_enabled)
print(s.email_smtp_host)
print(s.email_smtp_user)
print(s.email_default_recipient)
```

**通过标准**：字段值与 `~/.jarvis/settings.toml` 中 `[email]` 表一致。

---

### TC-03 功能未启用

临时把 `enabled` 设为 false：

```python
import asyncio
from agent.tools.extensions.email_tool import SendEmailTool
from agent.core.context import ToolContext
from agent.config.settings import Settings

tool = SendEmailTool()
ctx = ToolContext(workdir=".", messages=[], settings=Settings(email_enabled=False))
res = asyncio.run(tool.call({"subject": "t", "body": "b"}, ctx))
print(res.is_error, res.data)
```

**预期结果**：`is_error=True`，提示"邮件功能未启用"。

---

### TC-04 配置不完整

```python
import asyncio
from agent.tools.extensions.email_tool import SendEmailTool
from agent.core.context import ToolContext
from agent.config.settings import Settings

tool = SendEmailTool()
ctx = ToolContext(workdir=".", messages=[], settings=Settings(email_enabled=True))
res = asyncio.run(tool.call({"subject": "t", "body": "b"}, ctx))
print(res.is_error, res.data)
```

**预期结果**：`is_error=True`，提示缺少 `smtp_user`。

---

### TC-05 收件人缺失

```python
import asyncio
from agent.tools.extensions.email_tool import SendEmailTool
from agent.core.context import ToolContext
from agent.config.settings import Settings

tool = SendEmailTool()
ctx = ToolContext(
    workdir=".",
    messages=[],
    settings=Settings(
        email_enabled=True,
        email_smtp_user="a@163.com",
        email_smtp_password="pwd",
        email_sender="a@163.com",
    ),
)
res = asyncio.run(tool.call({"subject": "t", "body": "b"}, ctx))
print(res.is_error, res.data)
```

**预期结果**：`is_error=True`，提示未指定收件人且无默认收件人。

---

### TC-06 附件不存在

```python
import asyncio
from agent.tools.extensions.email_tool import SendEmailTool
from agent.core.context import ToolContext
from agent.config.settings import Settings

tool = SendEmailTool()
ctx = ToolContext(
    workdir=".",
    messages=[],
    settings=Settings(
        email_enabled=True,
        email_smtp_user="a@163.com",
        email_smtp_password="pwd",
        email_sender="a@163.com",
        email_default_recipient="b@136.com",
    ),
)
res = asyncio.run(tool.call({"subject": "t", "body": "b", "attachments": ["not_exist.txt"]}, ctx))
print(res.is_error, res.data)
```

**预期结果**：`is_error=True`，提示"附件不存在"。

---

### TC-07 发送纯文本邮件

在 REPL 中输入：

```text
> 发邮件提醒我今晚8点开会
```

或在代码中直接调用（需已配置真实邮箱）：

```python
import asyncio
from agent.tools.extensions.email_tool import SendEmailTool
from agent.core.context import ToolContext
from agent.config.settings import load_settings

tool = SendEmailTool()
ctx = ToolContext(workdir=".", messages=[], settings=load_settings())
res = asyncio.run(tool.call({
    "subject": "测试邮件",
    "body": "这是 Jarvis 发送的测试邮件。",
}, ctx))
print(res.data)
```

**通过标准**：目标收件箱收到邮件，主题和内容正确。

---

### TC-08 发送带附件邮件

```text
> 把 test.txt 作为附件发邮件给我，主题测试附件
```

或代码调用：

```python
res = asyncio.run(tool.call({
    "subject": "测试附件",
    "body": "请查收附件。",
    "attachments": ["test.txt"],
}, ctx))
```

**通过标准**：收件箱收到邮件，且包含 `test.txt` 附件。

---

### TC-09 指定收件人覆盖默认值

```python
res = asyncio.run(tool.call({
    "to": "another@example.com",
    "subject": "覆盖默认收件人",
    "body": "测试 to 参数是否覆盖 default_recipient。",
}, ctx))
```

**通过标准**：`another@example.com` 收到邮件，而 `default_recipient` 未收到。

---

### TC-10 抄送/密送

```python
res = asyncio.run(tool.call({
    "subject": "测试抄送密送",
    "body": "cc 和 bcc 测试。",
    "cc": "cc1@example.com,cc2@example.com",
    "bcc": "bcc@example.com",
}, ctx))
```

**通过标准**：主收件人、`cc`、`bcc` 均收到邮件。

---

### TC-11 收件箱工具注册与权限

```python
from agent.core.tool import build_default_registry

reg = build_default_registry()
assert "EmailBox" in reg
box = reg.get("EmailBox")
print(box.is_read_only({"action": "read"}), box.is_read_only({"action": "reply"}))
```

**通过标准**：`read` / `search` 为 True（只读，免确认）；`reply` 为 False 且
`check_permissions` 返回 `ASK`、`is_irreversible` 为 True。

---

### TC-12 搜索最新未读

```python
await box.call({"action": "search", "unseen": True, "limit": 3}, ctx)
```

**通过标准**：返回最多 3 条、**新的在前**；每条带 `uid`；结果里提示可再
`action="read"` 读正文。对应“刚好听到播报但不知道是哪封”的场景。

---

### TC-13 回复带会话线程头

```python
await box.call({"action": "reply", "uid": "4821", "body": "收到，今天下午给您。"}, ctx)
```

**通过标准**：发给原发件人；主题为 `Re: <原主题>`；报文带 `In-Reply-To: <原Message-ID>`
与 `References:`；在本机邮箱客户端里与来信**归于同一会话**。

已自动化：`tests/tools/test_email_box.py`（假 IMAP + 假 SMTP，不联网）。

## 故障排查

| 现象 | 可能原因 | 解决方式 |
|---|---|---|
| 邮件功能未启用 | `[email].enabled = false` | 改为 `true` |
| 缺少 smtp_user | 配置未填写发件账号 | 填写 `smtp_user` |
| 认证失败 | `smtp_password` 错误或用了登录密码 | 使用邮箱授权码 |
| 连接超时 | SMTP 服务器地址/端口错误 | 检查 `smtp_host` 和 `smtp_port` |
| 收件人为空 | 未传 `to` 且无 `default_recipient` | 补全配置或参数 |
| 附件不存在 | 路径错误或文件缺失 | 使用相对 workdir 或绝对路径 |
| 读信报“收件需要 IMAP” | `[email]` 里没有 `imap_*`（只有 SMTP 那几项） | **发件配好 ≠ 收件配好**：两套协议。`imap_host` 可从 `smtp_host` 推断（`smtp.163.com` → `imap.163.com`），账号/授权码也会回退，所以只配 SMTP 通常就够了；自定义域名必须手写 |
| 读信报“当前缺：imap_host” | `smtp_host` 不是 `smtp.<域名>` 形态（如 `mail.mycorp.com`） | 显式写 `imap_host`（问邮箱商要） |
| 报 `EXAMINE Unsafe Login` | **不是授权码错**：网易 163/126 要求客户端在 `LOGIN` 后发 `IMAP ID` 命令，不发就拒绝 `SELECT`（代码已在 `send_client_id` 里处理；若仍出现，确认这条链路没被绕过） | 不用改配置；查日志确认 login 后确实发了 `ID` |
| 搜索中文关键字搜不到 | 服务器不支持 UTF-8 SEARCH charset | 用 `from` / `subject` 英文片段，或缩短关键字再本地过滤 |
| 回复后对方客户端里不成会话 | 原信缺 `Message-ID`（工具会直接报错而不是默默发） | 用 `SendEmail` 发新邮件 |

## 使用示例

### 自然语言触发

```text
> 发邮件给我，主题今日摘要，内容今天完成了 cli-anything 市场远程读取功能。
> 把 /e/report.pdf 发到我的邮箱
> 每天晚上10点发一封今日总结邮件
```

### 作为工具调用

```python
await tool.call({
    "to": "13985465782@136.com",
    "subject": "Jarvis 测试",
    "body": "邮件功能已就绪。",
    "attachments": ["/path/to/file.txt"],
}, ctx)
```

## 相关：收件（阶段 9 W2 感知层 + EmailBox 工具，2026-10）

本节主要覆盖**发件**（`SendEmailTool` 走 SMTP）。同一张 `[email]` 表上另有一套
`imap_*` 字段，接了两块**只读收件**能力（标准库 `imaplib`，零新增依赖）：

1. **`mail` 感知源（事件，W2 阶段 9）**：只取信封头，永不下正文，来信时播报一句话；
2. **`EmailBox` 工具（模型主动调用）**：`search` / `read` / `reply`，同样全程只读
   （`select(readonly=True)` + `BODY.PEEK`），读完邮件在邮箱里仍是未读。

两者共用 `agent/core/mail/` 的底层（`parse.py` 纯函数 + `imap.py` 只读连接），
解码口径只有一份。

- 感知源设计与隐私约束（readonly + `BODY.PEEK[HEADER.FIELDS …]` + 已报键落盘）见
  [17-W2 感知层](../architecture/17-W2感知层.md) §6.5；
- 工具口径与安全边界（正文是不可信输入）见
  [05-工具系统](../architecture/05-工具系统.md)「邮件工具」节；
- 测试见 `tests/core/test_percept_sources_vcs_mail.py`（假 IMAP，不联网）、
  `tests/core/test_mail_parse.py`（纯函数）与 `tests/tools/test_email_box.py`；
- ✅ 当前状态：宿主接线（serve 侧订阅总线）已于 W2 第 4 步完成，逐源开关与
  名单也能走 `percept.config.set`（第 5 步），并已接上桌面「管家服务 → 感知源」
  面板（第 6 步）。自动化用例全用假 IMAP，**尚未跑过真邮箱**；人工验证清单：
  1. 配好 `[email]` 的 `imap_host/imap_user/imap_password`（留空则回退 SMTP 同名值）；
  2. 开 `[percept]` 的 `enabled` 与 `mail_enabled`：在桌面「感知源」面板上拨开关
     （当场重装配，不必重启），或改 `settings.toml` 后重启桌面壳 / `--serve`；
  3. 用另一个邮箱发一封信，预期：只弹一条「贾维斯观察」，不弹第二条；
  4. 查 `~/.jarvis/percept/events.jsonl` 与 `mail_seen.json`，预期：只有信封头，
     无正文、无凭据；到邮箱后台确认该信**仍是未读**（`BODY.PEEK` 不置已读）；
  5. 填 `mail_watch_senders`（面板「采集名单」里每行一项）后重复步骤 3，预期：
     名单外的人来信不弹。
