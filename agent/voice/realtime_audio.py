"""实时语音的音频处理纯函数与可调常量。

从 realtime_talk.py 拆出（拆分前主文件逼近 800 行上限）：RMS 计算、PCM 增益
衰减等无状态纯函数，以及回声抑制 / 半双工相关系数。realtime_talk 通过 import
引用，保持 `rt_mod.ECHO_GUARD_RMS` 等既有访问方式不变。

@author aceFelix
"""
from __future__ import annotations

import math
import struct

# AI 说话时麦克风衰减系数（0~1），防止 AEC 失效时扬声器回声被送回服务器导致自说自话。
# 0.1：免提外放下 AEC3 仍会残留可触发服务端 VAD 的回声，需较深压低；
# 代价是用户插话需说得更响才能打断（与“回声保护窗口”配合使用，仅全双工模式生效）。
ECHO_SUPPRESS_FACTOR = 0.1
# 回声保护门限：AI 说话期间，衰减后麦克风的实时 RMS 低于此值时，
# 认为本次 speech_started 是扬声器回声被服务端误触发（而非用户真实插话），
# 直接忽略、不取消回复。真实插话需说够响使衰减后 RMS 超过此门限。
# 0.012 ≈ -38dBFS：足以滤掉深衰减后的回声残余，又不至于要求用户喊话。
ECHO_GUARD_RMS = 0.012
# 半双工：AI 说完后继续静音麦克风的“回声尾迹窗口”（秒）。
# 免提外放时扬声器余音与房间混响会在 AI 说完后短暂持续，若立刻恢复上传，
# smart_turn 会把它当成“用户还在说话”而迟迟不开始下一轮回复。留一段尾迹
# 静默期让回声散尽，再恢复拾取用户语音。
MIC_TAIL_SECONDS = 0.6
# 半双工：单次响应静音的兜底上限（秒）。
# 正常响应由 response.done 解除静音；若状态异常悬挂（事件丢失等），超过
# 此上限强制恢复上传，避免麦克风永久哑掉（表现为“AI 再也听不到你说话”）。
MIC_MUTE_MAX_SECONDS = 30.0
# 半双工：轮末静音窗口（秒）——speech_stopped（语义判停）到 response.created
# 之间的保护期。2026-09-28 实机定性（aceFelix）：smart_turn 语义判停后立即提交
# 轮次并创建响应，但客户端半双工静音要到 response.created 后的下一个发送循环
# 才生效，中间的 ~100ms 里仍在直播真实麦克风——用户的尾音/换气声被服务端 VAD
# 重新检出为新轮次，刚出生的响应被 response.done[cancelled, reason=turn_detected]
# 掐死（链上无 speech_started、客户端未发 cancel、无 error）。此窗口从判停时刻
# 起发等长静音帧堵住尾音；response.created 后由响应期静音（mic_muted ①）接管，
# response.done 后被回声尾迹窗口覆盖。用户若真在继续说话（RMS 超门限），
# voice_gap 立即清掉本窗口恢复上传，不吞话。
MIC_TURN_END_MUTE_SECONDS = 3.0
# 注（2026-09-28 复盘，推翻 2026-09 第五次修正）：第五次修正依据的
# “静音在 response.created 之前会让服务端立刻取消响应”，其观测全部来自
# MCP 中途 session.update 毒化会话（彼时每轮响应本来就活不过 ~100ms，且
# 签名完全相同），属混淆变量归因错误。无中途 update 的会话在 response.created
# 后才静音仍出现 turn_detected 取消，且取消早于静音生效——真实成因是判停后
# 直播尾音被重新检出，即 MIC_TURN_END_MUTE_SECONDS 针对的问题。
# 本地语音活动门限：麦克风 RMS 高于此值视为“用户正在说话”。
# 0.02 ≈ -34dBFS，略高于回声保护门限 ECHO_GUARD_RMS(0.012)，避免房间底噪误判。
VOICE_RMS = 0.02
# 本地判“说完”：麦克风持续低于 VOICE_RMS 这么久即认为这一轮说完（秒）。
# 取 1.0s：句内自然停顿通常 <0.8s，再短会截断长句。该判定仅用于**提前复位**
# “正在说话”界面指示，不再触发任何静音（2026-09 第五次实机修正，见 voice_gap）。
SPEECH_GAP_SECONDS = 1.0


def mic_muted(
    half_duplex: bool,
    response_active: bool,
    ai_speaking: bool,
    response_begin_ts: float | None,
    mic_resume_at: float | None,
    now: float,
) -> tuple[bool, float | None]:
    """半双工下是否应暂停上传麦克风，并返回更新后的静音恢复时刻。

    两块静音期，任一命中即静音：

    1. **响应进行中**（`response_active` 或 `ai_speaking`）。关键在于静音必须从响应
       一开始就生效，而不是等 AI 出声：`response.created` → 首个
       `response.audio.delta` 之间存在空窗期（模型思考 + Function Calling 工具执行，
       需工具时长达数秒）。此前只在 `ai_speaking` 时静音，空窗期内麦克风仍在上传，
       用户尾音/环境噪声会被服务端当成“新的用户轮次”而取消本轮响应
       （回复被打断取消，需工具的提问最易复现）。
    2. **静音窗口**（`mic_resume_at`），三类来源：
       - **轮末静音**（`MIC_TURN_END_MUTE_SECONDS`）：smart_turn 语义判停后、
         `response.created` 前 ~100ms 直播尾音被重检为新轮次，由引擎在
         `speech_stopped` 分支设置（仅 smart_turn，server_vad 天然免疫）；
       - **工具轮之后**（`output` 全为 function_call）等待下一轮最终回复；
       - **普通回复结束后**的回声尾迹，待扬声器余音/混响散尽再恢复拾取。
       用户若真在继续说话（RMS 超门限），`voice_gap` 立即清窗口，不吞话。

    > 旧注曾声明“静音只能从 `response.created` 之后开始”（第五次实机修正），
    > 该结论已被 2026-09-28 复盘推翻（真因是 MCP 中途 session.update 毒化会话，
    > 见本文件第 42 行注与 fixlogs/realtime-talk-half-duplex.md 第六次修正）。

    悬挂保护：响应状态异常持续超过 `MIC_MUTE_MAX_SECONDS` 时解除静音并清空恢复
    时刻，保证麦克风不会永久哑掉（表现为“AI 再也听不到你说话”）。

    @author aceFelix
    """
    if not half_duplex:
        return False, mic_resume_at
    # ① 响应进行中（含模型思考、工具调用的空窗期）
    if response_active or ai_speaking:
        if response_begin_ts is not None and now - response_begin_ts > MIC_MUTE_MAX_SECONDS:
            return False, None  # 悬挂保护：状态异常，强制恢复上传
        return True, mic_resume_at
    # ② 说完后的回声尾迹窗口
    if mic_resume_at is not None:
        if now < mic_resume_at:
            return True, mic_resume_at
        return False, None  # 尾迹期已过，恢复上传
    return False, mic_resume_at


def should_attenuate(
    ai_speaking: bool, has_aec: bool, echo_suppress_with_aec: bool
) -> bool:
    """AI 说话期间是否需要二次压低麦克风增益。

    把音量衰减到 `ECHO_SUPPRESS_FACTOR` 后，残留回声被服务端重新识别为用户语音的
    概率大幅降低。AEC 有效时默认不再压低（避免用户插话被压没），可用
    `echo_suppress_with_aec` 关闭该行为。

    仅供全双工（耳机）模式使用：半双工期间根本不上传麦克风，无需压低。

    @author aceFelix
    """
    if not ai_speaking:
        return False
    if has_aec and not echo_suppress_with_aec:
        return False
    return True


def _rms(data: bytes) -> float:
    """计算 16bit PCM 音频数据的 RMS 音量，返回 0.0 ~ 1.0。

    @author aceFelix
    """
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"{count}h", data[: count * 2])
    mean_square = sum(s * s for s in samples) / count
    return min(1.0, math.sqrt(mean_square) / 32768.0)


def voice_gap(
    rms: float,
    last_voice_ts: float,
    mic_resume_at: float | None,
    now: float,
    *,
    muted: bool,
) -> tuple[float, float | None]:
    """本地判“说完”的时刻跟踪，供界面指示复位与提前解除静音。

    本函数自身**不设置任何静音窗口**（设置方是引擎的 speech_stopped 分支与
    `_end_response`）；它只保留两项能力：

    - 持续低声超过 ``SPEECH_GAP_SECONDS`` → 清零 ``last_voice_ts``（调用方据此复位
      “正在说话”指示；服务端虽会下发 `speech_stopped`，但界面复位不必等它）
    - 静音期间出现语音 → 立即清掉 ``mic_resume_at``（用户续话不被尾迹窗口吞掉）

    Args:
        rms: 本帧麦克风电平（0.0~1.0）。
        last_voice_ts: 上次检测到说话的时刻，0 表示当前不在说话。
        mic_resume_at: 静音截止时刻，None 表示未处于等待窗口。
        now: 当前单调时钟。
        muted: 本帧是否处于静音（AI 说话中或等待窗口内）。

    Returns:
        新的 ``(last_voice_ts, mic_resume_at)``。

    @author aceFelix
    """
    if muted:
        # 静音期的能量可能来自扬声器回声，不参与“说完”判定；但若明显高于门限，
        # 更可能是用户真的在说话，立即恢复上传而不是干等窗口到期。
        if rms >= VOICE_RMS:
            return last_voice_ts, None
        return last_voice_ts, mic_resume_at
    if rms >= VOICE_RMS:
        return now, mic_resume_at
    if last_voice_ts and now - last_voice_ts >= SPEECH_GAP_SECONDS:
        # 判“说完”：只清零活动时刻（供界面复位），不再设置静音窗口
        return 0.0, mic_resume_at
    return last_voice_ts, mic_resume_at


def silence_like(data: bytes) -> bytes:
    """返回与输入等长的静音 PCM（半双工静音帧）。

    半双工不能用“停止上传”来实现：DashScope 要求客户端**持续发送**麦克风音频流，
    输入一旦中断，服务端的判轮状态机就卡住并中止本轮响应（实机 2026-09 验证：
    静音=停止上传时服务端不再下发后续事件，恢复上传前会话处于悬死状态）。

    发送静音帧既保持流连续，又无声、不触发 VAD、不回传扬声器回声。

    @author aceFelix
    """
    return b"\x00" * len(data)


def _attenuate_pcm(data: bytes, factor: float) -> bytes:
    """按比例衰减 16bit PCM 音频，factor 在 0~1 之间。

    用于 AI 说话时临时压低麦克风增益，减少扬声器回声被 VAD 误判。
    采用 numpy 向量运算，未安装 numpy 时回退标准库 array。

    @author aceFelix
    """
    if not data or factor <= 0:
        return b"\x00" * len(data)
    if factor >= 1:
        return data
    try:
        import numpy as np
        arr = np.frombuffer(data, dtype=np.int16)
        scaled = (arr.astype(np.float32) * factor).astype(np.int16)
        return scaled.tobytes()
    except Exception:
        import array
        samples = array.array("h", data)
        scaled = array.array("h", (max(-32768, min(32767, int(s * factor))) for s in samples))
        return scaled.tobytes()
