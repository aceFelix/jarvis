# 实时双工语音 `/talk` 鉴权失败修复记录

## 问题现象

在桌面托盘 daemon 中启动「实时聊天」窗口可以正常弹出，但在 CLI 中输入 `/talk` 启动实时双工语音对话时，窗口弹出后立即报错：

```
⚠ 实时对话鉴权失败（HTTP 401/403）。请检查：
1) DASHSCOPE_API_KEY 是否配置且有效；
2) 是否已开通 DashScope 实时语音/多模态服务；
3) [realtime_talk] 中的 ws_url 是否与你的业务空间一致。
```

窗口中的系统气泡也重复出现多条连接提示，随后自动退出实时语音对话。

---

## 排查与修复过程

### 第一阶段：检查 API Key 配置

**时间**：初始排查  
**操作**：
- 检查环境变量 `DASHSCOPE_API_KEY`，确认存在且有效。
- 检查 `~/.jarvis/settings.toml`，发现文件中有多个 `api_key` 字段：
  - 顶层 LLM 配置：`api_key = "sk-your-dashscope-key-1"`
  - `[realtime_talk]` 段经补充后有：`api_key = "sk-your-dashscope-key-2"`
- 检查 `Settings` 类发现，`[realtime_talk].api_key` 没有被映射到 `dashscope_api_key` 字段。

**修复**：
- 在 `agent/config/settings.py` 的 `_apply_toml()` 中，为 `[realtime_talk]` 表增加 `api_key → dashscope_api_key` 映射。

**结果**：问题未解决，CLI `/talk` 仍然报 401。

---

### 第二阶段：统一 Python 环境

**时间**：排查环境不一致  
**发现**：
- `which jarvis` 指向 Python 3.13 的 Scripts 目录。
- `which python` 却指向 Python 3.14。
- 项目源码修改后，`jarvis` 命令可能调用的是 Python 3.13 中已安装的旧包，而不是当前项目源码。

**修复**：
- 卸载 Python 3.14，统一使用 Python 3.13。
- 在项目目录重新执行 `pip install -e .` 确保源码生效。

**结果**：问题未解决，`python -m agent.main` 直接启动后 `/talk` 仍然 401。

---

### 第三阶段：验证 Key 与网络

**时间**：排除 Key 本身问题  
**操作**：
1. 用 `curl` 直接测试 DashScope 实时语音 WebSocket 端点：
   - `sk-your-dashscope-key-1` → `InvalidApiKey`（无效）
   - `sk-your-dashscope-key-2` → `missing upgrade`（非 InvalidApiKey，说明 Key 有效）
2. 检查 `websockets` 版本为 `16.1`，支持 `additional_headers` 参数。
3. 编写最小测试脚本 `test_ws.py`，用同样的 Key 和 `websockets.connect()` 直接连接：

```python
async with websockets.connect(url, additional_headers=headers) as ws:
    print("✅ WebSocket 连接成功（鉴权通过）")
```

**结果**：最小测试脚本成功，说明 Key、网络、`websockets` 库均正常。

---

### 第四阶段：定位根因（关键转折）

**时间**：加调试打印追踪  
**操作**：在 `RealtimeTalk.run()` 中加入临时 DEBUG 输出：

```python
print(f"[DEBUG] RealtimeTalk api_key: {self._api_key[:15]}...")
print(f"[DEBUG] ws_url: {self._ws_url}")
print(f"[DEBUG] model: {self._model}")
```

**发现**：运行 `/talk` 后终端输出两条 DEBUG 信息：

```
[DEBUG] RealtimeTalk api_key: sk-your-dashscope-key-2...
[DEBUG] ws_url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime
[DEBUG] model: qwen-audio-3.0-realtime-flash
[DEBUG] RealtimeTalk api_key: ...
[DEBUG] ws_url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime
[DEBUG] model: qwen-audio-3.0-realtime-flash
```

**关键结论**：
- `/talk` 被触发了 **两次**。
- 第一次 api_key 正确，第二次 api_key 为空字符串。
- 第二次的空 Key 连接导致 401，覆盖了第一次的正常连接。

---

### 第五阶段：分析双重启动原因

**时间**：深入代码结构  
**发现**：
- CLI `/talk` 使用 `RealtimeTalkWindow(standalone=True)`。
- `RealtimeTalkWindow` 内部通过 `multiprocessing.spawn` 启动一个子进程运行 pywebview 窗口。
- 当 `standalone=True` 时，**子进程会自己启动一个 `RealtimeTalk`**（见 `process.py` 的 `_frontend_process_main`）。
- 但父进程的 `_realtime_talk()` 函数里也创建并启动了一个 `RealtimeTalk`。
- 更关键的是：`RealtimeTalkWindow` 默认 `_config = {}`，`_realtime_talk()` 在创建窗口后**没有调用 `set_config()` 把 api_key 传过去**。

**根因**：
1. 父进程启动 `RealtimeTalk` 时传入了正确的 `api_key`（第一次 DEBUG）。
2. 子进程启动 `RealtimeTalk` 时 `config` 为空，导致 `api_key=""`（第二次 DEBUG）。
3. 子进程的 401 错误通过窗口气泡显示出来。

---

## 最终修复方案

### 修复 1：把配置正确传给窗口子进程

**文件**：`agent/main.py`

在 `_realtime_talk()` 中：
- 把 `api_key / model / voice / ws_url` 打包成 `config` 字典。
- 创建 `RealtimeTalkWindow` 时显式指定 `standalone=True`。
- 调用 `window.set_config(config)` 把配置传给子进程。

```python
config = {
    "api_key": api_key,
    "model": getattr(settings, "realtime_model", "qwen-audio-3.0-realtime-flash"),
    "voice": getattr(settings, "realtime_voice", "longanqian"),
    "ws_url": getattr(settings, "realtime_ws_url", "") or DEFAULT_WS_URL,
}

window = RealtimeTalkWindow(on_close=lambda: None, standalone=True)
window.set_config(config)
window.show()
```

### 修复 2：避免父进程重复启动 RealtimeTalk

CLI `/talk` 使用 `standalone=True` 模式时，父进程只负责窗口生命周期，子进程自己运行 `RealtimeTalk`。父进程不再创建并运行自己的 `RealtimeTalk`。

```python
if has_window and window is not None:
    # standalone=True 时 RealtimeTalk 在子进程中运行，父进程只需等待窗口关闭。
    while window.is_open:
        await asyncio.sleep(0.2)
else:
    rt = RealtimeTalk(**config)
    await rt.run(ui)
```

### 修复 3：让「结束」按钮真正关闭会话

**文件**：`agent/ui/realtime_window/process.py`

原来的「结束」按钮只把 `__close_session__` 事件放入队列，没有实际关闭窗口或停止 `RealtimeTalk`。

修改内容：
1. `JSBridge` 持有窗口引用。
2. `close_session()` 调用 `window.destroy()` 关闭窗口。
3. 窗口关闭回调中设置 `rt._running = False`，优雅停止 `RealtimeTalk`。
4. `finally` 块中再次停止并 join RealtimeTalk 线程，确保资源释放。

```python
def close_session(self) -> None:
    """用户点击"结束"按钮：关闭窗口以停止会话。"""
    if self._window is not None:
        try:
            self._window.destroy()
        except Exception:
            pass
```

---

## 涉及文件

| 文件 | 改动说明 |
|---|---|
| `agent/config/settings.py` | `[realtime_talk].api_key` 映射到 `dashscope_api_key` |
| `agent/main.py` | `_realtime_talk()` 使用 `set_config()` 传配置；父进程不再重复启动 RealtimeTalk |
| `agent/ui/realtime_window/process.py` | `JSBridge.close_session()` 关闭窗口；窗口关闭时停止 RealtimeTalk |
| `agent/voice/realtime_talk.py` | 临时 DEBUG 打印（已移除） |

---

## 验证结果

- `python -m agent.main` 启动后输入 `/talk`：
  - 窗口正常弹出，核反应炉动画显示。
  - 不再报 401/403 鉴权失败。
  - 只建立一次 WebSocket 连接。
- 点击右下角「结束」按钮：
  - 窗口关闭。
  - CLI 回到命令提示符。
  - 无残留进程或音频设备占用。

---

## 经验教训

1. **多进程架构下，配置必须显式传递**。子进程不会自动继承父进程的局部变量，`multiprocessing.spawn` 只会传递可序列化的参数。
2. **不要依赖单例对象保存运行时配置**。`RealtimeTalkWindow` 是单例，但它的 `_config` 默认空，调用方必须主动 `set_config()`。
3. **调试双重调用时，日志比猜测更有效**。通过临时 DEBUG 打印才发现 `/talk` 被触发了两次，并迅速定位到子进程空 Key 问题。
4. **统一 Python 环境**。多个 Python 版本混用会导致"改代码不生效"的假象，优先保证 `python` 与 `jarvis` 命令使用同一解释器。
5. **前端按钮必须有明确的终止动作**。`close_session()` 不能只发事件，必须触发窗口销毁或会话停止，否则用户体验上"点了没反应"。
