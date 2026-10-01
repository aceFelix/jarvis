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
models.add          name: str, vendor: str,     {name, vendor, api_format,
                      api_format: str,            base_url, model_type}
                      base_url: str, api_key: str,
                      model_type: str
models.edit         name: str                   {name, vendor, api_format,
                      [, new_name: str,           base_url, model_type,
                      vendor: str,                hot_switched: bool}
                      api_format: str,
                      base_url: str,
                      api_key: str,
                      model_type: str]
models.remove       name: str                   {name, was_current}
voices.list         —                           [{name, voice_id, description,
                                                 vendor, model, linked,
                                                 current, custom}]（全量目录，
                                                 当前音色置顶）
voices.select       name: str                   {ok, name, voice_id,
                                                 linked_model, old_model}
voices.add          name: str                   {ok, name}（写入自定义音色
                      voice_id: str             [tts.custom_voices]，同名
                      [, model: str,            upsert=编辑；内置名拒绝）
                      description: str,
                      vendor: str]
voices.delete       name: str                   {ok, name}（仅自定义音色
                                                 可删）
metrics.get         —                           {cpu, memory, disk}
state.get           —                           {provider, model, mcp, ...}
schedule.list       —                           {reminders: [...],
                                                 deadlines: [...]}
cost.get            —                           {model, input_tokens,
                                                 output_tokens, ...,
                                                 cache_hit_rate（百分比，
                                                 口径同 REPL /cost）}
answer_user         text: str                   null（回填 ask_user 弹窗）
talk.start          duplex?: bool（全双工           null（结果走 talk_started）
                    桥接，桌面端传 true）
talk.stop           —                           null（结果走 talk_stopped）
talk.audio          data: base64 PCM16 16k          无回执（fire-and-forget，
                    麦克风帧（全双工会话）          仅 duplex 会话消费）
voice.start         —                           null（结果走 voice_started）
voice.stop          —                           null（结果走 voice_stopped）
voice.interrupt     —                           bool（打断当前播报/推理）
proactive.ack       task_id: str                bool（提醒确认，停止升级重发）
project.set         path: str（绝对目录）       null（结果走 project_switched；
                                                非法/相对/不存在路径 ok=false）
project.get         —                           {workdir, name, persisted}
projects.list       —                           [{path, name, last_opened}]
projects.forget     path: str                   bool（是否确有移除，不删磁盘）
mode.set            mode: str                   {ok, mode}（default/plan/
                                                accept_edits/yolo；非法 ok=false）
think.set           effort: str                 {ok, effort}（off/on/low/
                                                medium/high；非法 ok=false）
==================  ==========================  =============================

事件一览（event → payload 说明）：

- 对话流：``user_message`` / ``assistant_text``（流式增量）/
  ``assistant_thinking`` / ``tool_use`` / ``tool_result`` / ``assistant_done``
- 跨设备协同：``qrcode``（连接二维码卡片） / ``remote_state``（连接态） /
  ``remote_user_message``（手机/微信入站消息，桌面渲染为带来源标记的用户气泡）
- 会话：``session_ready`` / ``session_renamed``（标题改名，前端只刷列表
  不清屏） / ``session_loaded`` / ``session_new``
- 项目工作区：``project_switched``（payload ``{workdir, name}``，project.set
  引擎侧重建完成后推一次，与 model_switched 同为「入队即返回、落地走事件」）
- 提示：``info`` / ``warn`` / ``error`` / ``status`` / ``ask_user``
- 指标：``metrics``（每 2 秒推送，与 metrics.get 同构）
- 实时语音：``talk_started`` / ``talk_stopped`` / ``volume`` /
  ``user_speaking`` / ``ai_speaking`` / ``user_transcript`` /
  ``ai_transcript`` / ``ai_transcript_delta``；全双工桥接会话另有
  ``talk_audio``（AI 语音帧，base64 PCM16 24kHz）
- 半双工语音（/voice）：``voice_started`` / ``voice_stopped`` /
  ``voice_state``（payload = listening/thinking/speaking/standby/dialog/
  exited） / ``voice_user_transcript`` / ``voice_ai_text_delta`` /
  ``voice_ai_text``（源自解耦 voice_loop 经 ServeVoiceAdapter 外抛）
- 主动播报：``proactive_notify``（payload ``{kind, title, text, task_id}``，
  kind = ``briefing`` 每日简报 / ``reminder`` 用户提醒 / ``deadline``
  截止日期；源自 ProactiveHub，见 ``agent/serve/hub.py``）
- 初始化：``init``（连接建立后首推，payload 同 state.get）
- 运行健康：``mcp_ready``（MCP 后台连接落定时推一次，payload 即 state.get 的
  mcp 快照 ``{connected, failed, tools}``；桌面 init 时快照常为 None——MCP 约 9s
  后台预热才连上，故需本事件驱动右栏补刷）

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
CMD_SESSIONS_RENAME = "sessions.rename"
CMD_SESSIONS_DELETE = "sessions.delete"
CMD_MODELS_LIST = "models.list"
CMD_MODELS_SELECT = "models.select"
# models.add（2026-09）：桌面壳左栏「添加模型」表单提交 —— 写用户级 models.toml
# 的 [llm.custom_models."<name>"]（与 /models 添加其他模型同口径）。
CMD_MODELS_ADD = "models.add"
# models.edit / models.remove（2026-09）：桌面壳左栏模型面板的配置管理 ——
# 双击模型项进编辑表单（models.edit）、右键显删除按钮（models.remove，
# 仅自定义模型可删），与 /models 的「修改配置 / 删除模型」同口径。
CMD_MODELS_EDIT = "models.edit"
CMD_MODELS_REMOVE = "models.remove"
CMD_VOICES_LIST = "voices.list"
CMD_VOICES_SELECT = "voices.select"
# voices.add / voices.delete（2026-09-28 音色-模型适配桌面接入）：桌面壳左栏
# 音色面板的自定义音色管理 —— 添加/编辑表单提交（voices.add，同名 upsert）、
# 删除自定义音色（voices.delete，仅 custom 可删），与 /tts-voice 表单同口径。
CMD_VOICES_ADD = "voices.add"
CMD_VOICES_DELETE = "voices.delete"
CMD_METRICS_GET = "metrics.get"
CMD_STATE_GET = "state.get"
CMD_SCHEDULE_LIST = "schedule.list"
CMD_COST_GET = "cost.get"
CMD_ANSWER_USER = "answer_user"
CMD_REPLY_ABORT = "reply.abort"
CMD_TALK_START = "talk.start"
CMD_TALK_STOP = "talk.stop"
# talk.audio（2026-09-28 桌面全双工）：渲染进程 → 服务端的麦克风音频帧
#（base64 PCM16 16kHz 单声道，100ms/帧），仅 talk.start 带 duplex=true 的
# 会话消费；无回执（fire-and-forget，客户端用 send 而非 sendCommand）。
CMD_TALK_AUDIO = "talk.audio"
CMD_VOICE_START = "voice.start"
CMD_VOICE_STOP = "voice.stop"
CMD_VOICE_INTERRUPT = "voice.interrupt"
CMD_PROACTIVE_ACK = "proactive.ack"
CMD_SETTINGS_GET = "settings.get"
CMD_SETTINGS_SET = "settings.set"
# project.*（桌面项目工作区，2026）：桌面壳"选择文件夹作为项目 → 在其中
# 聊天开发"。project.set 切当前 workdir（结果走 project_switched 事件）；
# project.get / projects.list / projects.forget 读写当前项目与最近项目列表
#（~/.jarvis/projects.toml）。后端只接收并校验绝对目录路径，绝不弹框。
CMD_PROJECT_SET = "project.set"
CMD_PROJECT_GET = "project.get"
CMD_PROJECTS_LIST = "projects.list"
CMD_PROJECTS_FORGET = "projects.forget"
# mode.set / think.set（工作模式 / 思考强度，2026-09）：桌面输入区两个选择器。
# mode.set 切权限模式（default/plan/accept_edits/yolo，与终端 /mode 同口径）；
# think.set 切思考强度（off/on/low/medium/high，后端按厂商翻译成各参数）。
# 均为 request/response 型，校验在 api 侧，落地入引擎队列串行执行。
CMD_MODE_SET = "mode.set"
CMD_THINK_SET = "think.set"
# phone.* / wechat.*（跨设备协同桌面接入，2026-10）：把终端 /connect-phone、
# /connect-wechat 的能力接入桌面壳。手机/微信与桌面共享同一会话，靠引擎侧唯一
# 共享 query 锁串行（两端都能发、绝不同时发）。connect 为「入队即返回、二维码走
# qrcode 事件」的异步型；status 供重开桌面回填连接态；wechat.pairing 回填手机端
# 显示的数字配对码（终端是 input()，桌面改内联输入）。@author aceFelix
CMD_PHONE_CONNECT = "phone.connect"
CMD_PHONE_DISCONNECT = "phone.disconnect"
CMD_PHONE_STATUS = "phone.status"
CMD_WECHAT_CONNECT = "wechat.connect"
CMD_WECHAT_DISCONNECT = "wechat.disconnect"
CMD_WECHAT_STATUS = "wechat.status"
CMD_WECHAT_PAIRING = "wechat.pairing"

# 全部桌面指令集合（测试与文档一致性校验用）
DESKTOP_COMMANDS: frozenset[str] = frozenset({
    CMD_MESSAGE,
    CMD_SESSIONS_LIST,
    CMD_SESSIONS_OPEN,
    CMD_SESSIONS_NEW,
    CMD_SESSIONS_RENAME,
    CMD_SESSIONS_DELETE,
    CMD_MODELS_LIST,
    CMD_MODELS_SELECT,
    CMD_MODELS_ADD,
    CMD_MODELS_EDIT,
    CMD_MODELS_REMOVE,
    CMD_VOICES_LIST,
    CMD_VOICES_SELECT,
    CMD_VOICES_ADD,
    CMD_VOICES_DELETE,
    CMD_METRICS_GET,
    CMD_STATE_GET,
    CMD_SCHEDULE_LIST,
    CMD_COST_GET,
    CMD_ANSWER_USER,
    CMD_REPLY_ABORT,
    CMD_TALK_START,
    CMD_TALK_STOP,
    CMD_TALK_AUDIO,
    CMD_VOICE_START,
    CMD_VOICE_STOP,
    CMD_VOICE_INTERRUPT,
    CMD_PROACTIVE_ACK,
    CMD_SETTINGS_GET,
    CMD_SETTINGS_SET,
    CMD_PROJECT_SET,
    CMD_PROJECT_GET,
    CMD_PROJECTS_LIST,
    CMD_PROJECTS_FORGET,
    CMD_MODE_SET,
    CMD_THINK_SET,
    CMD_PHONE_CONNECT,
    CMD_PHONE_DISCONNECT,
    CMD_PHONE_STATUS,
    CMD_WECHAT_CONNECT,
    CMD_WECHAT_DISCONNECT,
    CMD_WECHAT_STATUS,
    CMD_WECHAT_PAIRING,
})

# ---- 事件名常量（服务端 → 客户端） ----
EVT_REPLY = "reply"
EVT_INIT = "init"
EVT_METRICS = "metrics"
# MCP 连接结果落定事件：payload = state.get 的 mcp 快照（{connected,failed,tools}），
# 引擎后台预热连完 MCP 后推一次，桌面壳据此刷新右栏运行健康。@author aceFelix
EVT_MCP_READY = "mcp_ready"
# 项目切换落地事件：payload = {workdir, name}。project.set 入队后由引擎线程
# 串行重建（换 workdir → 重生提示词/重挂 harness/开新会话）完成后推一次；
# 与 model_switched 同构（入队即返回，落地走事件）。@author aceFelix
EVT_PROJECT_SWITCHED = "project_switched"
EVT_PROACTIVE_NOTIFY = "proactive_notify"
EVT_VOICE_STARTED = "voice_started"
EVT_VOICE_STOPPED = "voice_stopped"
# talk_audio（2026-09-28 桌面全双工）：服务端 → 渲染进程的 AI 语音帧
#（base64 PCM16 24kHz 单声道，随 response.audio.delta 逐帧广播）；打断时
# 远端应清空播放队列（引擎本地 spk.stop_stream 与该事件语义对齐）。
EVT_TALK_AUDIO = "talk_audio"
EVT_VOICE_STATE = "voice_state"
EVT_VOICE_USER_TRANSCRIPT = "voice_user_transcript"
EVT_VOICE_AI_TEXT_DELTA = "voice_ai_text_delta"
EVT_VOICE_AI_TEXT = "voice_ai_text"
# qrcode（跨设备协同，2026-10）：手机/微信连接二维码就绪事件，payload =
# {"channel": "phone"|"wechat", "url": <扫码地址字符串>, "fresh": bool}。桌面壳在
# 中间聊天区内联渲染成二维码卡片（前端 qrcode 库画 canvas）；fresh=True 表示一次
# 新连接（前端在底部新建卡片、清理旧未连接卡片），fresh=False/缺省表示同一连接内
# 二维码过期重生成（就地刷新最后一张未连接卡片，不堆叠）；微信配对码走既有
# ask_user 内联条（answer_user 通道），不新增交互组件。@author aceFelix
EVT_QRCODE = "qrcode"
# 跨设备协同连接状态事件：payload = {"channel", "connected": bool}，
# 连接成功/断开时各推一次，桌面下拉按钮据此切换文案与置灰。
EVT_REMOTE_STATE = "remote_state"
# 跨设备协同远端用户消息：payload = {"channel": "phone"|"wechat", "text": str}。
# 手机/微信入站消息经此事件上桌面，前端按普通用户气泡渲染并标注来源（区别于
# 本地 user_message 被跳过、也区别于早先的居中 info 系统提示）。
EVT_REMOTE_USER_MESSAGE = "remote_user_message"


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
