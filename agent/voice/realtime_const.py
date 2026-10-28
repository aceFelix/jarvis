"""实时语音引擎的音频参数与时序常量（单一来源）。

这些常量原先写在 ``realtime_engine.py`` 顶部。协议接收循环拆到 ``realtime_recv.py``、
响应救援与看门狗拆到 ``realtime_rescue.py`` 之后，三处都要用同一份数值；若各自
复制一份就会漂移（本项目已在厂商 base_url 上踩过「两处副本各写一遍」的坑），
故抽成本模块：数值只此一份。

消费方均直接从这里导入（``realtime_engine`` / ``realtime_recv`` / ``realtime_rescue`` /
``realtime_talk``），不再经 ``realtime_engine`` 转手再导出；要调阈值只改这一处。

@author aceFelix
"""

from __future__ import annotations

# ---- 音频参数 ----
INPUT_RATE = 16000    # 麦克风：16kHz
OUTPUT_RATE = 24000   # 扬声器：24kHz
CHUNK_BYTES = 3200    # 每次读取 3200 字节（~100ms @ 16kHz mono 16bit）
SEND_INTERVAL = 0.02  # 发送间隔 20ms
VOLUME_REPORT_INTERVAL = 8  # 每 8 个 chunk 上报一次音量（~160ms）
# 单条 AI 回复最大持续时长（秒），超过后主动截断，避免服务端 1006 断连
MAX_RESPONSE_SECONDS = 90.0
# 响应救援时延（秒）：turn_detected 取消后服务端不再补答的等待；
# 轮次提交后服务端未自动创建响应的等待；两次救援最小间隔
RESCUE_AFTER_CANCEL_SECONDS = 1.2
RESCUE_AFTER_COMMIT_SECONDS = 1.8
RESCUE_MIN_INTERVAL_SECONDS = 2.0
# 救援后强制静音保持（秒）：补发 response.create 到 response.created 之间
# 用户任何声响都会被服务端当成新轮次掐死响应，故救援后立即重置静音窗口
RESCUE_HOLD_SECONDS = 1.0
