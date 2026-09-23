"""jarvis serve 协议契约 —— 桌面壳（jarvis-desktop）与后端 API 的唯一契约来源。

传输层：WebSocket（token 认证，``ws://127.0.0.1:<port>/?token=xxx``）。
消息统一为 JSON 文本帧：

- 客户端 → 服务端（指令）::

    {"type": "<command>", ...params}

- 服务端 → 客户端（事件，与手机 PWA 桥接协议同构）::

    {"event": "<event>", "data": <payload>}

- 服务端 → 客户端（指令回执，request/response 型指令专用）::

    {"event": "reply", "data": {"type": "<command>", "ok": true, "result": ...}}
    {"event": "reply", "data": {"type": "<command>", "ok": false, "error": "..."}}

握手：serve 进程就绪后向 stdout 打印**单行** JSON（Electron 主进程逐行解析）::

    {"type": "jarvis-serve-ready", "port": <ws_port>, "http_port": <http_port>,
     "token": "<hex>", "pid": <int>}

指令一览（type → 参数 → 回执 result）：

==================  ==========================  =============================
指令                参数                        result
==================  ==========================  =============================
message             text: str                   null（结果走流式事件）
                      [, images: [{data,
                      media_type}]（≤8 张）
                      , files: [{name, content}]
                      （≤5 个文本文件）]
sessions.list       —                           [{name, updated_at, ...}]
sessions.open       name: str                   null（结果走 session_loaded）
sessions.new        —                           null（结果走 session_new）
models.list         —                           [{name, vendor, current, ...}]
models.select       name: str                   bool（是否持久化成功）
voices.list         —                           [{name, current, ...}]
voices.select       name: str                   bool
metrics.get         —                           {cpu, memory, disk}
state.get           —                           {provider, model, mcp, ...}
schedule.list       —                           {reminders: [...],
                                                 deadlines: [...]}
cost.get            —                           {model, input_tokens,
                                                 output_tokens, ...}
answer_user         text: str                   null（回填 ask_user 弹窗）
talk.start          —                           null（结果走 talk_started）
talk.stop           —                           null（结果走 talk_stopped）
voice.start         —                           null（结果走 voice_started）
voice.stop          —                           null（结果走 voice_stopped）
voice.interrupt     —                           bool（打断当前播报/推理）
proactive.ack       task_id: str                bool（提醒确认，停止升级重发）
==================  ==========================  =============================

事件一览（event → payload 说明）：

- 对话流：``user_message`` / ``assistant_text``（流式增量）/
  ``assistant_thinking`` / ``tool_use`` / ``tool_result`` / ``assistant_done``
- 会话：``session_ready`` / ``session_renamed``（标题改名，前端只刷列表
  不清屏） / ``session_loaded`` / ``session_new``
- 提示：``info`` / ``warn`` / ``error`` / ``status`` / ``ask_user``
- 指标：``metrics``（每 2 秒推送，与 metrics.get 同构）
- 实时语音：``talk_started`` / ``talk_stopped`` / ``volume`` /
  ``user_speaking`` / ``ai_speaking`` / ``user_transcript`` /
  ``ai_transcript`` / ``ai_transcript_delta``
- 半双工语音（/voice）：``voice_started`` / ``voice_stopped`` /
  ``voice_state``（payload = listening/thinking/speaking/standby/dialog/
  exited） / ``voice_user_transcript`` / ``voice_ai_text_delta`` /
  ``voice_ai_text``（源自解耦 voice_loop 经 ServeVoiceAdapter 外抛）
- 主动播报：``proactive_notify``（payload ``{kind, title, text, task_id}``，
  kind = ``briefing`` 每日简报 / ``reminder`` 用户提醒 / ``deadline``
  截止日期；源自 ProactiveHub，见 ``agent/serve/hub.py``）
- 初始化：``init``（连接建立后首推，payload 同 state.get）

@author aceFelix
"""

from __future__ import annotations

# ---- 握手标记（stdout 单行 JSON 的 type 字段，Electron 据此识别就绪行） ----
SERVE_READY_MARKER = "jarvis-serve-ready"

# ---- 指令 type 常量（客户端 → 服务端） ----
CMD_MESSAGE = "message"
CMD_SESSIONS_LIST = "sessions.list"
CMD_SESSIONS_OPEN = "sessions.open"
CMD_SESSIONS_NEW = "sessions.new"
CMD_MODELS_LIST = "models.list"
CMD_MODELS_SELECT = "models.select"
CMD_VOICES_LIST = "voices.list"
CMD_VOICES_SELECT = "voices.select"
CMD_METRICS_GET = "metrics.get"
CMD_STATE_GET = "state.get"
CMD_SCHEDULE_LIST = "schedule.list"
CMD_COST_GET = "cost.get"
CMD_ANSWER_USER = "answer_user"
CMD_REPLY_ABORT = "reply.abort"
CMD_TALK_START = "talk.start"
CMD_TALK_STOP = "talk.stop"
CMD_VOICE_START = "voice.start"
CMD_VOICE_STOP = "voice.stop"
CMD_VOICE_INTERRUPT = "voice.interrupt"
CMD_PROACTIVE_ACK = "proactive.ack"

# 全部桌面指令集合（测试与文档一致性校验用）
DESKTOP_COMMANDS: frozenset[str] = frozenset({
    CMD_MESSAGE,
    CMD_SESSIONS_LIST,
    CMD_SESSIONS_OPEN,
    CMD_SESSIONS_NEW,
    CMD_MODELS_LIST,
    CMD_MODELS_SELECT,
    CMD_VOICES_LIST,
    CMD_VOICES_SELECT,
    CMD_METRICS_GET,
    CMD_STATE_GET,
    CMD_SCHEDULE_LIST,
    CMD_COST_GET,
    CMD_ANSWER_USER,
    CMD_REPLY_ABORT,
    CMD_TALK_START,
    CMD_TALK_STOP,
    CMD_VOICE_START,
    CMD_VOICE_STOP,
    CMD_VOICE_INTERRUPT,
    CMD_PROACTIVE_ACK,
})

# ---- 事件名常量（服务端 → 客户端） ----
EVT_REPLY = "reply"
EVT_INIT = "init"
EVT_METRICS = "metrics"
EVT_PROACTIVE_NOTIFY = "proactive_notify"
EVT_VOICE_STARTED = "voice_started"
EVT_VOICE_STOPPED = "voice_stopped"
EVT_VOICE_STATE = "voice_state"
EVT_VOICE_USER_TRANSCRIPT = "voice_user_transcript"
EVT_VOICE_AI_TEXT_DELTA = "voice_ai_text_delta"
EVT_VOICE_AI_TEXT = "voice_ai_text"


def build_reply(cmd_type: str, *, ok: bool, result: object = None, error: str = "") -> dict:
    """构造指令回执消息体（{"event": "reply", ...} 信封的 data 部分由调用方组装）。

    Args:
        cmd_type: 原始指令 type。
        ok: 是否成功。
        result: 成功时的结果（可 JSON 序列化）。
        error: 失败时的错误描述。

    Returns:
        完整回执消息 dict，可直接 json.dumps 后经 WS 发送。

    @author aceFelix
    """
    data: dict = {"type": cmd_type, "ok": ok}
    if ok:
        data["result"] = result
    else:
        data["error"] = error or "未知错误"
    return {"event": EVT_REPLY, "data": data}


def build_handshake(ws_port: int, http_port: int, token: str, pid: int) -> dict:
    """构造 stdout 握手 JSON（单行打印，Electron 主进程解析）。

    @author aceFelix
    """
    return {
        "type": SERVE_READY_MARKER,
        "port": ws_port,
        "http_port": http_port,
        "token": token,
        "pid": pid,
    }
