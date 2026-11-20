# 安全策略

## 支持的版本

| 版本 | 是否接收安全修复 |
|---|---|
| 最新发布版（当前 2.6.x） | ✅ |
| 更早的版本 | ❌ 建议先升级 |

## 上报安全漏洞

**请不要开公开 Issue、也不要在 PR / Discussions 里贴漏洞细节。**

优先使用 GitHub 的私密上报通道：

1. 打开仓库的 **Security** 标签页；
2. 点击 **Report a vulnerability**（GitHub Security Advisories）；
3. 填写复现步骤与影响范围。

如果无法使用该通道，可邮件联系 **13985465782@163.com**，标题注明 `[SECURITY]`。

### 报告里请包含

- 受影响版本（`jarvis --version`）与平台；
- 漏洞类型（命令注入 / 路径穿越 / 权限绕过 / 密钥泄露 / 远程代码执行等）；
- 最小复现步骤或 PoC；
- 影响范围与利用前提；
- 你希望的披露方式与时间。

### 请注意

- **不要**在报告里粘贴真实的 API Key、token、cookie 或个人数据；
  需要示例时请用 `sk-REDACTED` 之类的占位；
- 我们会在确认后尽快给出修复计划，并在修复发布后致谢（除非你希望匿名）；
- 请给我们合理的修复时间，在修复发布前不要公开漏洞细节。

## 安全相关的实现背景

本项目已有以下防线，供你评估问题时参考：

- 五层权限管线（`agent/permissions/`）：fail-closed，危险操作默认 ASK/DENY；
- 进程级沙箱（`agent/core/sandbox/`）：Job Objects / rlimits / sandbox-exec；
- 凭据脱敏（`agent/core/redaction.py`）：工具输出出口统一打码；
- 密钥扫描：`.github/workflows/gitleaks.yml` 对全历史与工作区扫描；
- 不可逆动作账本与延时窗口（`agent/core/actions/`）：外发类动作可撤回/可追溯。
