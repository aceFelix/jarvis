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

## 故障排查

| 现象 | 可能原因 | 解决方式 |
|---|---|---|
| 邮件功能未启用 | `[email].enabled = false` | 改为 `true` |
| 缺少 smtp_user | 配置未填写发件账号 | 填写 `smtp_user` |
| 认证失败 | `smtp_password` 错误或用了登录密码 | 使用邮箱授权码 |
| 连接超时 | SMTP 服务器地址/端口错误 | 检查 `smtp_host` 和 `smtp_port` |
| 收件人为空 | 未传 `to` 且无 `default_recipient` | 补全配置或参数 |
| 附件不存在 | 路径错误或文件缺失 | 使用相对 workdir 或绝对路径 |

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
