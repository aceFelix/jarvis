# J.A.R.V.I.S. 故障排查

> 常见错误和解决方案。

---

## LLM API 错误

J.A.R.V.I.S. 会自动分类 API 错误并显示中文提示。以下是各类错误的排查方向：

### 鉴权失败（401）

```
症状：API Key 无效、已过期或未配置
```

1. 检查环境变量是否设置：`echo $DASHSCOPE_API_KEY`（Linux/macOS）或 `echo $env:DASHSCOPE_API_KEY`（PowerShell）
2. 运行 `jarvis --config-show` 确认 `api_key` 不是 "(未设置)"
3. 到对应厂商控制台检查 Key 是否有效、是否过期
4. 运行 `jarvis --init` 重新配置

### 请求限流（429）

```
症状：API 调用频率超限或配额用尽
```

1. 稍等 30 秒后重试
2. 到厂商控制台检查额度余额
3. 输入 `/model <名称>` 切换到其他可用模型

### 模型不存在（404）

```
症状：当前模型不存在或无权访问
```

1. 运行 `/models` 查看可用模型列表
2. 输入 `/model <名称>` 切换到有效模型
3. 如果使用自定义模型，检查 `provider_type` 是否匹配厂商

### 网络错误

```
症状：API 服务连接失败或超时
```

1. 检查网络连接是否正常
2. 检查 `base_url` 配置是否正确：`jarvis --config-show`
3. 如使用代理，检查 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量
4. J.A.R.V.I.S. 会自动重试一次（1.5s 退避），无需手动操作

### 上下文过长

```
症状：对话历史超过模型上下文窗口
```

1. J.A.R.V.I.S. 会自动压缩后重试
2. 如持续报错，运行 `/compact` 手动压缩
3. 或运行 `/clear` 清空对话历史重新开始

---

## 安装与启动

### `pip install jarvis-agent` 失败

```bash
# 需要 Python 3.11+
python --version

# 使用 uv（推荐）
pip install uv
uv pip install jarvis-agent
```

### `ModuleNotFoundError: No module named 'openai'`

```bash
pip install openai
```

### 启动后显示 provider = mock

未配置有效的 API Key。运行：

```bash
jarvis --init
```

---

## 模型切换

### /model 切换后报错

```
症状：切换到自定义模型后无法使用
```

1. 运行 `jarvis --config-show` 查看当前模型配置
2. 检查自定义模型的 `api_key` / `base_url` / `provider_type` 是否正确
3. 如果 `api_key` 为空但你有环境变量，说明 key 已从环境变量读取（正常）

### 智谱模型 405 报错

```
症状：智谱 API 返回 HTML 而非 JSON
```

可能原因：
1. 用了 OpenAI 兼容接口但模型名不对 → 检查模型名是否在智谱控制台已开通
2. 用了原生 SDK（zai）但 `base_url` 填了 OpenAI 格式 → 原生 SDK 不需要 base_url

---

## 语音相关

### 实时语音（/talk）无法启动

1. 确认 `dashscope_api_key` 已配置（DashScope 专属 Key）
2. 确认麦克风和扬声器正常工作
3. 运行 `python -c "import pyaudio; print('OK')"` 确认 PyAudio 已安装

### 回声 / 自言自语

```
症状：外放扬声器场景下 Jarvis 被自己的声音触发
```

1. 安装 AEC 库：`pip install aec-audio-processing`
2. 或使用耳机（AEC 自动优化外放路径延迟）

### TTS 音色不满意

修改 `configs/settings.toml`：

```toml
tts_voice = "longxiaochun"   # 可选音色见 DashScope 文档
```

---

## 配置相关

### 配置文件在哪

| 层级 | 路径 |
|---|---|
| 项目级 | `configs/settings.toml` |
| 用户级 | `~/.jarvis/settings.toml`（Windows: `C:\Users\用户名\.jarvis\settings.toml`） |
| MCP | `~/.jarvis/mcp.json` |
| 记忆 | `~/.jarvis/MEMORY.md` |
| 日志 | `~/.jarvis/jarvis.log` |

### 配置文件被覆盖

`jarvis --init` 合并写入（不会覆盖已有配置）。如果配置丢失，检查 `~/.jarvis/settings.toml` 是否为 TOML 格式错误导致解析失败。

### 自定义模型不见了

1. 运行 `jarvis --config-show` 确认 `custom_models` 不为空
2. 如果为空，检查 `~/.jarvis/settings.toml` 中 `[llm.custom_models]` 节是否存在且格式正确
3. TOML 格式错误会导致整个用户配置被丢弃（静默回退到项目配置）

---

## 性能

### 启动慢

1. MCP server 多 → 正常现象（已并行连接，7 个 server ≈ max(单个耗时)）
2. 网络问题 → MCP server 连接超时
3. 运行 `jarvis --quick` 跳过 MCP/LSP 快速启动

### 回复越来越慢

1. 对话历史过长 → `/compact` 压缩
2. 工具定义膨胀 → 已启用延迟加载（14 核心工具始终携带，其余按需）
3. 切换模型 → 共享 HTTP 连接池，不重复握手

---

## 诊断命令

| 命令 | 说明 |
|---|---|
| `/doctor` | 系统诊断（环境/配置/日志/自愈统计） |
| `/config show` | 查看当前生效的完整配置 |
| `/cost` | 查看 token 用量和成本估算 |
| `/context` | 查看上下文窗口使用情况 |
| `/mcp` | 查看 MCP server 连接状态 |
