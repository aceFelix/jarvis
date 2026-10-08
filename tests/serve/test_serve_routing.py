"""DesktopBridgeServer WS 指令路由测试（不起真实端口，处理器直调）。

覆盖：
- message：入引擎队列 + ok 回执；空文本 → 失败回执
- request/response 型指令：sessions/models/voices/metrics/state 正常路径
- models.add：添加自定义模型（字段校验 + 写盘隔离 + 内存同步后列表可见）
- models.edit：改内置模型（名锁定/写用户级覆盖配置）、改自定义模型（可改名且
  旧段清除、api_key 留空保持原 Key）、字段校验、改当前模型入队强制热切换
- models.remove：自定义模型删成功且列表消失；内置模型 / 不存在的名字被拒
- models.select：写盘成功入队 switch_model（引擎立即热切换）、写盘失败不入队；
  models.list 的 current 取引擎实时模型（不再停在启动快照）
- voices.*（2026-09-28 音色-模型适配）：list 全量目录+当前置顶、select dict
  回执含联动字段、add 同名 upsert/内置名拒绝、delete 仅自定义可删
- schedule.list / cost.get：右栏任务中心与用量卡数据源（hub 缺失降级空列表）
- 参数校验：sessions.open / models.select / voices.select 缺 name → 失败回执
- 未注册指令 / 缺 type 字段：回 ok=false 失败回执（不静默忽略）
- 事件泵：引擎事件 → broadcast 信封映射；stop 幂等

@author aceFelix
"""

from __future__ import annotations

import asyncio
import json
import queue
import time
from pathlib import Path

from agent.config.settings import Settings
from agent.core.daemon.deadline import Deadline
from agent.core.daemon.scheduler import ScheduleTask
from agent.serve.server import DesktopBridgeServer
from agent.ui.workbench.api import WorkbenchAPI
from agent.ui.workbench.engine import ChatEngine


class _FakeWS:
    """记录 send 内容的假 WS 客户端（处理器只依赖 ws.send）。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def _make() -> tuple[DesktopBridgeServer, WorkbenchAPI, queue.Queue, queue.Queue]:
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    server = DesktopBridgeServer(api, settings)
    return server, api, event_queue, command_queue


def _make_with_settings(settings: Settings) -> tuple[DesktopBridgeServer, WorkbenchAPI, queue.Queue]:
    """用定制 Settings 起 server（模型内置/自定义表可控的用例用）。"""
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    return DesktopBridgeServer(api, settings), api, command_queue


def _call(server: DesktopBridgeServer, cmd: str, data: dict) -> dict:
    """直调处理器表中的指令，返回唯一一条回执。"""
    ws = _FakeWS()
    payload = {"type": cmd, **data}
    asyncio.run(server._ws_handlers[cmd](ws, payload))
    assert len(ws.sent) == 1
    return ws.sent[0]


# ---- message 指令 ----

def test_message_enqueues_and_replies_ok():
    """message 文本入引擎队列，回执仅确认入队（结果走事件泵）。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {"text": "你好贾维斯"})
    assert reply["event"] == "reply"
    assert reply["data"] == {"type": "message", "ok": True, "result": None}
    cmd = command_queue.get_nowait()
    assert cmd["cmd"] == "send"
    assert cmd["text"] == "你好贾维斯"


def test_message_empty_text_rejected():
    """空白文本不入队，回执 ok=false。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {"text": "   "})
    assert reply["data"]["ok"] is False
    assert reply["data"]["error"] == "空消息"
    assert command_queue.empty()


def test_message_with_attachments_passthrough():
    """images/files 随 message 透传入引擎队列（桌面壳 📎 附件链路）。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {
        "text": "看图",
        "images": [{"data": "QUJD", "media_type": "image/png"}],
        "files": [{"name": "a.md", "content": "# x"}],
    })
    assert reply["data"]["ok"] is True
    cmd = command_queue.get_nowait()
    assert cmd["images"] == [{"data": "QUJD", "media_type": "image/png"}]
    assert cmd["files"] == [{"name": "a.md", "content": "# x"}]


def test_message_images_only_ok():
    """纯图片（空文本）也入队：引擎会补最小指令文本。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {"text": "", "images": [{"data": "QUJD"}]})
    assert reply["data"]["ok"] is True
    assert command_queue.get_nowait()["cmd"] == "send"


def test_message_too_many_images_rejected():
    """图片超 8 张：失败回执，不入队。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {"text": "x", "images": [{"data": "QQ"}] * 9})
    assert reply["data"]["ok"] is False
    assert command_queue.empty()


def test_message_oversized_file_rejected():
    """单文件内容超 20 万字符：失败回执，不入队。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "message", {
        "text": "x", "files": [{"name": "b.txt", "content": "y" * 200_001}]
    })
    assert reply["data"]["ok"] is False
    assert command_queue.empty()


# ---- checkpoint.preview / checkpoint.rewind（消息级回溯） ----

def test_checkpoint_preview_passes_args_and_rejects_invalid():
    """预览直返：合法参数透传 API；非整数/小于 1 → 失败回执。"""
    server, api, _, _ = _make()
    calls: list[int] = []
    api.checkpoint_preview = lambda n: calls.append(n) or {
        "ok": True, "has_checkpoint": True, "files": [], "untracked": [], "reason": "",
    }
    reply = _call(server, "checkpoint.preview", {"user_tail_count": 2})
    assert reply["data"]["ok"] is True
    assert reply["data"]["result"]["has_checkpoint"] is True
    assert calls == [2]
    bad = _call(server, "checkpoint.preview", {"user_tail_count": 0})
    assert bad["data"]["ok"] is False and "user_tail_count" in bad["data"]["error"]
    bad = _call(server, "checkpoint.preview", {"user_tail_count": "abc"})
    assert bad["data"]["ok"] is False


def test_checkpoint_rewind_enqueues_with_defaults():
    """撤回：restore_files 默认 True；入队即返 pending，真实结果走事件。"""
    server, api, _, command_queue = _make()
    seen: list[tuple] = []
    api.checkpoint_rewind = lambda n, r: seen.append((n, r)) or {
        "ok": True, "pending": True,
    }
    reply = _call(server, "checkpoint.rewind", {"user_tail_count": 1})
    assert reply["data"]["ok"] is True and reply["data"]["result"]["pending"] is True
    assert seen == [(1, True)]
    reply = _call(server, "checkpoint.rewind", {
        "user_tail_count": 3, "restore_files": False,
    })
    assert reply["data"]["ok"] is True
    assert seen[-1] == (3, False)
    # 缺字段：默认撤回最后 1 条用户消息 + 连带回滚文件
    _call(server, "checkpoint.rewind", {})
    assert seen[-1] == (1, True)
    # 非法值：失败回执，不再透传
    bad = _call(server, "checkpoint.rewind", {"user_tail_count": -2})
    assert bad["data"]["ok"] is False
    assert seen[-1] == (1, True)


# ---- request/response 型指令 ----

def test_sessions_list_reply():
    """sessions.list 返回列表（mock 环境下可为空，但必须是 list 回执）。"""
    server, _, _, _ = _make()
    reply = _call(server, "sessions.list", {})
    assert reply["data"]["ok"] is True
    assert isinstance(reply["data"]["result"], list)


def test_sessions_open_requires_name():
    """sessions.open 缺 name → 失败回执；有 name → 指令入队。"""
    server, _, _, command_queue = _make()
    bad = _call(server, "sessions.open", {})
    assert bad["data"]["ok"] is False
    assert "name" in bad["data"]["error"]
    good = _call(server, "sessions.open", {"name": "s1"})
    assert good["data"]["ok"] is True
    assert command_queue.get_nowait()["cmd"] == "load_session"


def test_models_list_and_select():
    """models.list 返回带 current 标记的列表；select 缺 name 被拒。"""
    server, _, _, _ = _make()
    listing = _call(server, "models.list", {})
    assert listing["data"]["ok"] is True
    assert isinstance(listing["data"]["result"], list)
    bad = _call(server, "models.select", {})
    assert bad["data"]["ok"] is False
    assert "name" in bad["data"]["error"]


def test_models_select_enqueues_hot_switch(monkeypatch) -> None:
    """models.select 写盘成功 → 回执 ok 且入队 switch_model（引擎立即热切换）。

    只写 last_model 时，切换要等进程重启才生效（列表「当前」也不动）；
    现在写盘成功后追加引擎指令，切换在引擎线程内串行落地。
    """
    import agent.config.model_registry as mr

    monkeypatch.setattr(mr, "save_last_model", lambda name: True)  # 不碰真实 ~/.jarvis
    server, _, _, command_queue = _make()

    reply = _call(server, "models.select", {"name": "qwen3.8-2.4t-a95b"})

    assert reply["data"]["ok"] is True
    assert reply["data"]["result"] is True
    assert command_queue.get_nowait() == {"cmd": "switch_model", "name": "qwen3.8-2.4t-a95b"}


def test_models_select_write_failure_no_enqueue(monkeypatch) -> None:
    """写盘失败 → result=false 且不入队（不谎报已切换，引擎保持原模型）。

    回执 ok 只表示指令被处理；业务结果在 result（与 voices.select 同口径，
    前端把 result!==true 当失败并提示，引擎不收到任何切换指令）。
    """
    import agent.config.model_registry as mr

    monkeypatch.setattr(mr, "save_last_model", lambda name: False)
    server, _, _, command_queue = _make()

    reply = _call(server, "models.select", {"name": "m1"})

    assert reply["data"]["ok"] is True
    assert reply["data"]["result"] is False
    assert command_queue.empty()


def test_models_list_current_follows_engine() -> None:
    """models.list 的 current 取引擎实时模型：热切换后「当前」立刻移动。

    回归：current 曾取自启动配置快照（settings.model），于是点选新模型后
    列表仍标旧模型为「当前」—— 桌面壳表现为「选了 qwen3.8-2.4t-a95b，
    列表里当前还是 qwen3.8-flash」。
    """
    settings = Settings(
        model="qwen3.8-flash",
        models={"qwen3.8-flash": "通义千问 3.8 Flash", "qwen3.8-2.4t-a95b": "通义千问 3.8 2.4T"},
    )
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    server = DesktopBridgeServer(api, settings)

    def current_of() -> list[str]:
        listing = _call(server, "models.list", {})
        return [m["name"] for m in listing["data"]["result"] if m["current"]]

    assert current_of() == ["qwen3.8-flash"]  # 启动时：配置模型
    engine._model_override = "qwen3.8-2.4t-a95b"  # 点选后：引擎记账待落地
    assert current_of() == ["qwen3.8-2.4t-a95b"]
    engine._model = "qwen3.8-2.4t-a95b"  # 装配后：QueryLoop 正在跑的模型
    engine._session_ready = True
    engine._model_override = ""
    assert current_of() == ["qwen3.8-2.4t-a95b"]


def test_models_add_field_validation():
    """models.add 缺 name / 非法接口类型 / 非法模型类型 → 失败回执，不写盘。"""
    server, _, _, _ = _make()
    bad = _call(server, "models.add", {})
    assert bad["data"]["ok"] is False
    assert "name" in bad["data"]["error"]
    bad_fmt = _call(server, "models.add", {"name": "m1", "api_format": "grpc"})
    assert bad_fmt["data"]["ok"] is False
    assert "api_format" in bad_fmt["data"]["error"]
    bad_type = _call(server, "models.add", {"name": "m1", "model_type": "audio"})
    assert bad_type["data"]["ok"] is False
    assert "model_type" in bad_type["data"]["error"]


def test_models_add_persists_and_visible_in_list(tmp_path):
    """models.add 成功：写用户级 models.toml（含 base_url 推断）+ 内存同步，
    models.list 立即列出新模型（桌面壳左栏「添加模型」链路）。

    落盘目标用 Path.home 重定向到 tmp_path、keyring 读写 no-op ——
    不触碰真实 ~/.jarvis 与系统凭据管理器（与 tests/config/ 口径一致）。
    """
    from unittest.mock import patch

    import agent.config.keyring_store as ks

    server, _, _, _ = _make()
    (tmp_path / ".jarvis").mkdir()
    with patch.object(Path, "home", return_value=tmp_path), patch.object(
        ks, "store_api_key", lambda *a, **k: None
    ):
        reply = _call(
            server,
            "models.add",
            {
                "name": "my-model",
                "vendor": "deepseek",
                "api_format": "openai",
                "base_url": "",
                "api_key": "sk-test",
                "model_type": "multimodal",
            },
        )
        assert reply["data"]["ok"] is True
        result = reply["data"]["result"]
        assert result["name"] == "my-model"
        # base_url 留空 → 按厂商 + 接口类型推断（与 /models 添加同口径）
        assert result["base_url"] == "https://api.deepseek.com"
        # 写盘落到重定向后的用户级 models.toml
        text = (tmp_path / ".jarvis" / "models.toml").read_text(encoding="utf-8")
        assert '[llm.custom_models."my-model"]' in text

    # 内存同步：即使已出 patch 上下文，models.list 仍能列出（同进程 settings 快照）
    listing = _call(server, "models.list", {})
    names = [m["name"] for m in listing["data"]["result"]]
    assert "my-model" in names


def test_models_edit_validation_and_unknown_model():
    """models.edit：缺 name / 非法枚举 / 名字不存在 → 失败回执（均不落盘）。"""
    server, _, _, _ = _make()
    bad = _call(server, "models.edit", {})
    assert bad["data"]["ok"] is False
    assert "name" in bad["data"]["error"]
    bad_fmt = _call(server, "models.edit", {"name": "m1", "api_format": "grpc"})
    assert bad_fmt["data"]["ok"] is False
    assert "api_format" in bad_fmt["data"]["error"]
    bad_type = _call(server, "models.edit", {"name": "m1", "model_type": "audio"})
    assert bad_type["data"]["ok"] is False
    assert "model_type" in bad_type["data"]["error"]
    unknown = _call(server, "models.edit", {"name": "__no_such_model_probe__"})
    assert unknown["data"]["ok"] is False
    assert "不存在" in unknown["data"]["error"]


def test_models_edit_builtin_model_writes_override(tmp_path):
    """models.edit 改内置模型：名字锁定，写用户级覆盖配置；改的又是当前运行
    模型 → 入队带 force 的 switch_model（引擎按新配置强制重建 provider）。

    对应桌面兏「双击内置模型项 → 改 base_url/接口类型 → 保存」。
    """
    from unittest.mock import patch

    import agent.config.keyring_store as ks

    settings = Settings(model="qwen3.8-flash", models={"qwen3.8-flash": "通义千问 3.8 Flash"})
    server, _, command_queue = _make_with_settings(settings)
    (tmp_path / ".jarvis").mkdir()
    with patch.object(Path, "home", return_value=tmp_path), patch.object(
        ks, "store_api_key", lambda *a, **k: None
    ):
        reply = _call(
            server,
            "models.edit",
            {
                "name": "qwen3.8-flash",
                "api_format": "openai",
                "base_url": "https://example.com/v1",
                "api_key": "sk-new",
                "model_type": "text",
            },
        )

    assert reply["data"]["ok"] is True
    result = reply["data"]["result"]
    assert result["name"] == "qwen3.8-flash"  # 内置名字锁定
    assert result["base_url"] == "https://example.com/v1"
    assert result["model_type"] == "text"
    assert result["hot_switched"] is True
    # 写盘落到重定向后的用户级 models.toml（内置模型写的是覆盖配置）
    text = (tmp_path / ".jarvis" / "models.toml").read_text(encoding="utf-8")
    assert '[llm.custom_models."qwen3.8-flash"]' in text
    assert "https://example.com/v1" in text
    # 当前运行模型 → force 入队（跳过「同名即当前」短接，按新配置重建）
    assert command_queue.get_nowait() == {
        "cmd": "switch_model", "name": "qwen3.8-flash", "force": True,
    }


def test_models_edit_custom_rename_keeps_blank_api_key(tmp_path):
    """models.edit 改自定义模型：可改名（旧段删除），api_key 留空 → 保持原 Key。

    桌面兏不回显密钥 —— 表单 Key 留空必须是「不变」而非「清空」，否则
    用户只改个名字就会把密钥抹掉（与 REPL 预填明文的口径刻意不同）。
    改的不是当前模型 → 不入队、hot_switched=false。
    """
    from unittest.mock import patch

    import agent.config.keyring_store as ks

    settings = Settings(
        custom_models={
            "edit-probe-old": {
                "name": "edit-probe-old",
                "provider": "deepseek",
                "api_format": "openai",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-keep",
                "model_type": "text",
            },
        },
    )
    server, _, command_queue = _make_with_settings(settings)
    (tmp_path / ".jarvis").mkdir()
    with patch.object(Path, "home", return_value=tmp_path), patch.object(
        ks, "store_api_key", lambda *a, **k: None
    ):
        reply = _call(server, "models.edit", {"name": "edit-probe-old", "new_name": "edit-probe-new"})

    assert reply["data"]["ok"] is True
    result = reply["data"]["result"]
    assert result["name"] == "edit-probe-new"
    assert result["base_url"] == "https://api.deepseek.com"  # 留空沿用旧值
    assert result["hot_switched"] is False
    assert command_queue.empty()
    # 内存同步：旧名移除、密钥保留
    assert "edit-probe-old" not in settings.custom_models
    assert settings.custom_models["edit-probe-new"]["api_key"] == "sk-keep"
    # 磁盘：旧段不得残留（否则旧名成「幽灵模型」）
    text = (tmp_path / ".jarvis" / "models.toml").read_text(encoding="utf-8")
    assert '[llm.custom_models."edit-probe-new"]' in text
    assert '[llm.custom_models."edit-probe-old"]' not in text
    names = [m["name"] for m in _call(server, "models.list", {})["data"]["result"]]
    assert "edit-probe-new" in names
    assert "edit-probe-old" not in names


def test_models_edit_builtin_rename_rejected():
    """models.edit 给内置模型改名 → 失败回执（内置名固定，不能出「幽灵模型」）。"""
    settings = Settings(models={"qwen3.8-flash": "通义千问 3.8 Flash"})
    server, _, command_queue = _make_with_settings(settings)
    reply = _call(server, "models.edit", {"name": "qwen3.8-flash", "new_name": "my-flash"})
    assert reply["data"]["ok"] is False
    assert "内置模型名" in reply["data"]["error"]
    assert command_queue.empty()


def test_models_remove_custom_and_reject_builtin(tmp_path):
    """models.remove：自定义模型删成功且列表消失；内置模型 / 不存在 → 失败回执。

    内置模型来自项目级 [llm.models]，删掉用户级覆盖段也只是回退默认端点、
    列表里仍在 —— 直接拒绝（对齐 REPL 的 allow_delete=是否自定义）。
    """
    from unittest.mock import patch

    settings = Settings(
        models={"builtin-probe": "内置探针"},
        custom_models={"custom-probe": {"name": "custom-probe", "provider": "deepseek"}},
    )
    server, _, _ = _make_with_settings(settings)
    (tmp_path / ".jarvis").mkdir()
    # 先把自定义模型写进用户级 models.toml（remove 只删磁盘上真实存在的段）
    with patch.object(Path, "home", return_value=tmp_path):
        _call(
            server,
            "models.add",
            {"name": "custom-probe", "vendor": "deepseek", "api_format": "openai"},
        )
        assert '[llm.custom_models."custom-probe"]' in (
            tmp_path / ".jarvis" / "models.toml"
        ).read_text(encoding="utf-8")

        builtin = _call(server, "models.remove", {"name": "builtin-probe"})
        assert builtin["data"]["ok"] is False
        assert "内置模型不可删除" in builtin["data"]["error"]
        missing = _call(server, "models.remove", {"name": "__no_such_model_probe__"})
        assert missing["data"]["ok"] is False

        reply = _call(server, "models.remove", {"name": "custom-probe"})
        # 磁盘段已删除（列表不再「重启复活」）
        assert '[llm.custom_models."custom-probe"]' not in (
            tmp_path / ".jarvis" / "models.toml"
        ).read_text(encoding="utf-8")

    assert reply["data"]["ok"] is True
    assert reply["data"]["result"] == {"name": "custom-probe", "was_current": False}
    assert "custom-probe" not in settings.custom_models
    names = [m["name"] for m in _call(server, "models.list", {})["data"]["result"]]
    assert "custom-probe" not in names
    assert "builtin-probe" in names  # 内置模型未被误删


def test_models_remove_project_level_custom_rejected(tmp_path):
    """内置表中不存在、但用户级 models.toml 也没写的自定义模型 → 拒绝删除。

    否则删了会在重启后从项目级配置「复活」，属口径分裂；报错不让前端以为删成功。
    """
    from unittest.mock import patch

    settings = Settings(custom_models={"ghost-probe": {"name": "ghost-probe"}})
    server, _, _ = _make_with_settings(settings)
    with patch.object(Path, "home", return_value=tmp_path):
        reply = _call(server, "models.remove", {"name": "ghost-probe"})
    assert reply["data"]["ok"] is False
    assert "删除失败" in reply["data"]["error"]
    assert "ghost-probe" in settings.custom_models  # 失败不动内存状态


def test_voices_list_and_select():
    """voices.list 返回全量目录（当前置顶）；voices.select 缺 name 被拒。"""
    server, _, _, _ = _make()
    listing = _call(server, "voices.list", {})
    assert listing["data"]["ok"] is True
    result = listing["data"]["result"]
    assert isinstance(result, list) and result
    assert {"name", "voice_id", "description", "vendor", "model",
            "linked", "current", "custom"} <= set(result[0].keys())
    assert result[0]["current"] is True  # 当前音色置顶（旧版首项口径兼容）
    bad = _call(server, "voices.select", {})
    assert bad["data"]["ok"] is False


def test_voices_select_dict_reply_with_linkage(tmp_path):
    """voices.select 回执为 dict：联动时 linked_model 非空并落盘；未知音色 ok=false。"""
    from unittest.mock import patch

    settings = Settings(tts_voice="longanlang_v3", tts_model="cosyvoice-v2")
    server, _, _ = _make_with_settings(settings)
    (tmp_path / ".jarvis").mkdir()
    (tmp_path / ".jarvis" / "settings.toml").write_text(
        '[tts]\nmodel = "cosyvoice-v2"\nvoice = "longanlang_v3"\n', encoding="utf-8",
    )
    with patch.object(Path, "home", return_value=tmp_path):
        reply = _call(server, "voices.select", {"name": "longxiaochun_v3"})
        assert reply["data"]["ok"] is True
        result = reply["data"]["result"]
        assert result == {
            "ok": True, "name": "longxiaochun_v3", "voice_id": "longxiaochun_v3",
            "linked_model": "cosyvoice-v3-flash", "old_model": "cosyvoice-v2",
        }
        assert settings.tts_voice == "longxiaochun_v3"
        assert settings.tts_model == "cosyvoice-v3-flash"
        text = (tmp_path / ".jarvis" / "settings.toml").read_text(encoding="utf-8")
        assert 'voice = "longxiaochun_v3"' in text
        assert 'model = "cosyvoice-v3-flash"' in text
        # 目录外音色：指令本身被处理（data.ok=True），业务结果在 result.ok=False
        missing = _call(server, "voices.select", {"name": "不存在的音色"})
    assert missing["data"]["result"]["ok"] is False
    assert "未找到" in missing["data"]["result"]["error"]


def test_voices_add_and_delete_flow(tmp_path):
    """voices.add 写入自定义音色（upsert/校验/内置名拒绝）；voices.delete 仅自定义可删。"""
    from unittest.mock import patch

    settings = Settings(tts_voice="longanlang_v3", tts_model="cosyvoice-v3-flash")
    server, _, _ = _make_with_settings(settings)
    (tmp_path / ".jarvis").mkdir()
    (tmp_path / ".jarvis" / "settings.toml").write_text(
        '[tts]\nmodel = "cosyvoice-v3-flash"\nvoice = "longanlang_v3"\n', encoding="utf-8",
    )
    toml_path = tmp_path / ".jarvis" / "settings.toml"
    with patch.object(Path, "home", return_value=tmp_path):
        added = _call(server, "voices.add", {
            "name": "我的声音", "voice_id": "my-clone",
            "model": "cosyvoice-v3-plus", "description": "复刻",
        })
        assert added["data"]["result"] == {"ok": True, "name": "我的声音"}
        assert settings.custom_voices["我的声音"]["voice_id"] == "my-clone"
        assert '[tts.custom_voices."我的声音"]' in toml_path.read_text(encoding="utf-8")
        # 同名 upsert（编辑即重提）：覆盖不重复
        _call(server, "voices.add", {"name": "我的声音", "voice_id": "my-clone-2"})
        assert settings.custom_voices["我的声音"]["voice_id"] == "my-clone-2"
        # 缺 voice_id / 内置音色名遮蔽 → 失败回执（ValueError 由框架转 ok=false）
        bad = _call(server, "voices.add", {"name": "x"})
        assert bad["data"]["ok"] is False
        shadow = _call(server, "voices.add", {"name": "longcheng_v3", "voice_id": "y"})
        assert shadow["data"]["ok"] is False
        assert "内置音色名" in shadow["data"]["error"]
        # 删除：内置被拒；自定义删成功且磁盘段消失
        rm_builtin = _call(server, "voices.delete", {"name": "longcheng_v3"})
        assert rm_builtin["data"]["ok"] is False
        deleted = _call(server, "voices.delete", {"name": "我的声音"})
        assert deleted["data"]["result"] == {"ok": True, "name": "我的声音"}
        assert "我的声音" not in settings.custom_voices
        assert "我的声音" not in toml_path.read_text(encoding="utf-8")
    # 删后列表回到纯内置目录：内置项 custom=False（前端据此不显删除按钮）
    names = {v["name"]: v for v in _call(server, "voices.list", {})["data"]["result"]}
    assert "longcheng_v3" in names and names["longcheng_v3"]["custom"] is False
    assert "我的声音" not in names


def test_metrics_get_reply_shape():
    """metrics.get 返回 cpu/memory/disk 三键（与右栏指标面板同构）。"""
    server, _, _, _ = _make()
    reply = _call(server, "metrics.get", {})
    assert reply["data"]["ok"] is True
    result = reply["data"]["result"]
    assert {"cpu", "memory", "disk"} <= set(result.keys())


def test_state_get_reply():
    """state.get 返回引擎状态 dict（init 事件同构 payload）。"""
    server, _, _, _ = _make()
    reply = _call(server, "state.get", {})
    assert reply["data"]["ok"] is True
    assert isinstance(reply["data"]["result"], dict)


def test_state_get_includes_mcp_key():
    """state.get 带 mcp 键（右栏运行健康数据源；引擎未装配 MCP 时为 None）。"""
    server, _, _, _ = _make()
    reply = _call(server, "state.get", {})
    assert reply["data"]["ok"] is True
    assert "mcp" in reply["data"]["result"]
    assert reply["data"]["result"]["mcp"] is None


# ---- schedule.list / cost.get（右栏任务中心 + 用量卡） ----


class _StubScheduler:
    """Scheduler 替身：返回固定的待触发任务列表。"""

    def __init__(self, tasks: list[ScheduleTask]) -> None:
        self._tasks = tasks

    def list_pending(self) -> list[ScheduleTask]:
        return list(self._tasks)


class _StubDeadlineTracker:
    """DeadlineTracker 替身：返回固定的活跃截止日期列表。"""

    def __init__(self, items: list[Deadline]) -> None:
        self._items = items

    def list_active(self) -> list[Deadline]:
        return list(self._items)


class _StubHub:
    """ProactiveHub 替身：仅暴露 schedule.list 需要的两个只读属性。"""

    def __init__(self, tasks: list[ScheduleTask], items: list[Deadline]) -> None:
        self.scheduler = _StubScheduler(tasks)
        self.deadline_tracker = _StubDeadlineTracker(items)


def test_schedule_list_no_hub_returns_empty():
    """hub 未装配时返回空列表（ok=true，前端空态而非报错）。"""
    server, _, _, _ = _make()
    reply = _call(server, "schedule.list", {})
    assert reply["data"]["ok"] is True
    assert reply["data"]["result"] == {"reminders": [], "deadlines": []}


def test_schedule_list_with_hub_maps_fields():
    """hub 装配时字段映射正确（reminder 取 content/trigger_at，deadline 取 title/days_left）。"""
    settings = Settings()
    event_queue: queue.Queue = queue.Queue()
    command_queue: queue.Queue = queue.Queue()
    engine = ChatEngine(settings, event_queue, command_queue)
    api = WorkbenchAPI(event_queue, command_queue, engine, settings)
    hub = _StubHub(
        tasks=[ScheduleTask(id="t1", trigger_at="2026-09-23T09:00:00", content="开会", repeat="daily")],
        items=[Deadline(id="d1", title="Q3 交付", due_date="2099-01-01")],
    )
    server = DesktopBridgeServer(api, settings, hub=hub)
    reply = _call(server, "schedule.list", {})
    assert reply["data"]["ok"] is True
    result = reply["data"]["result"]
    assert result["reminders"] == [
        {"id": "t1", "content": "开会", "trigger_at": "2026-09-23T09:00:00", "repeat": "daily"}
    ]
    assert len(result["deadlines"]) == 1
    item = result["deadlines"][0]
    assert item["id"] == "d1"
    assert item["title"] == "Q3 交付"
    assert item["due_date"] == "2099-01-01"
    assert item["status"] == "active"
    # 到期日在未来：days_left 应为正整数
    assert isinstance(item["days_left"], int) and item["days_left"] > 0


def test_cost_get_shape_defaults_zero():
    """cost.get 返回用量统计（引擎未装配时 token/轮数/消息数全 0）。

    cache_hit_rate 为后端统一口径算好的百分数（Usage.cache_hit_rate），
    桌面壳用量卡直接展示，不在前端重算。上下文窗口占用（context_*）
    口径同 /context，引擎未装配时 used=0、窗口回退 128000 假设值。@author aceFelix
    """
    server, _, _, _ = _make()
    reply = _call(server, "cost.get", {})
    assert reply["data"]["ok"] is True
    result = reply["data"]["result"]
    assert {
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cache_hit_rate",
        "context_used",
        "context_window",
        "context_percent",
        "context_configured",
        "dialogs",
        "messages",
    } <= set(result.keys())
    assert result["input_tokens"] == 0
    assert result["cache_hit_rate"] == 0.0
    assert result["dialogs"] == 0
    assert result["messages"] == 0
    # 未装配：已用 0、回退假设窗口 128000、占比 0、未标记配置
    assert result["context_used"] == 0
    assert result["context_window"] == 128000
    assert result["context_percent"] == 0.0
    assert result["context_configured"] is False


def test_answer_user_enqueues():
    """answer_user 把回填文本入引擎队列（ask_user 弹窗闭环）。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "answer_user", {"text": "同意"})
    assert reply["data"]["ok"] is True
    cmd = command_queue.get_nowait()
    assert cmd["cmd"] == "answer_user"
    assert cmd["text"] == "同意"


def test_unknown_command_not_registered():
    """未注册指令不在处理器表中。

    分发层对这种情况回 ok=false 失败回执（见 test_unregistered_command_replies_error），
    不是静默忽略。
    """
    server, _, _, _ = _make()
    assert "not.a.command" not in server._ws_handlers


class _FakeConn:
    """按序吐出预设 JSON 帧的假 WS 连接（直驱 _handle_ws 主循环）。"""

    def __init__(self, frames: list[dict]) -> None:
        self._raw = [json.dumps(f, ensure_ascii=False) for f in frames]
        self.sent: list[dict] = []

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for raw in self._raw:
            yield raw

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


def test_unregistered_command_replies_error():
    """未注册指令 / 缺 type 字段：回 ok=false 失败回执（不回则客户端干等到超时）。

    旧行为是静默忽略：前端（桌面壳 15s 超时 / 手机端）按回执等待，静默丢弃只会
    表现为「指令 X 回执超时」且看不到原因——典型触发是前端已热更新、后端进程
    仍是旧代码（python -m agent.serve 不热重载）。
    """
    server, _, _, _ = _make()
    conn = _FakeConn([{"type": "not.a.command"}, {"type": ""}])
    asyncio.run(server._handle_ws(conn, f"/?token={server._token}"))
    replies = [f for f in conn.sent if f.get("event") == "reply"]
    assert [r["data"]["type"] for r in replies] == ["not.a.command", ""]
    assert all(r["data"]["ok"] is False for r in replies)
    assert "不支持指令 not.a.command" in replies[0]["data"]["error"]
    assert "重启后端" in replies[0]["data"]["error"]
    assert replies[1]["data"]["error"] == "指令缺少 type 字段"


# ---- 每连接首帧 ----

def test_init_pushed_per_client_connect():
    """init 首帧按连接推送：新连接立即收到 init 信封（payload 同 get_state）。

    启动期一次性 broadcast 在无客户端时会被丢弃，首帧只能走每连接钩子；
    桌面壳首屏七路刷新（含设置面板 settings.get 回填）全挂在该事件上。

    @author aceFelix
    """
    server, api, _, _ = _make()
    ws = _FakeWS()
    asyncio.run(server._on_client_connected(ws))
    assert len(ws.sent) == 1
    assert ws.sent[0]["event"] == "init"
    assert ws.sent[0]["data"] == api.get_state()


# ---- 事件泵 ----

def test_event_pump_broadcasts_and_stops():
    """事件泵消费引擎队列并 broadcast；stop 幂等。"""
    server, _, event_queue, _ = _make()
    received: list[tuple[str, object]] = []
    server.broadcast = lambda event, data: received.append((event, data))  # type: ignore[method-assign]
    server.start_event_pump(event_queue)
    event_queue.put_nowait({"type": "assistant_text", "payload": "你好"})
    event_queue.put_nowait({"type": "metrics", "payload": {"cpu": 1}})
    event_queue.put_nowait("非 dict 项应被跳过")
    deadline = time.time() + 2
    while len(received) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert received == [("assistant_text", "你好"), ("metrics", {"cpu": 1})]
    server.stop_event_pump()
    server.stop_event_pump()  # 幂等：二次调用不抛异常
    assert server._pump_thread is None


# ---- mode.set / think.set（工作模式 + 思考强度，2026-09 桌面输入区两选择器）----

def test_mode_set_enqueues_on_valid_mode():
    """mode.set 合法模式 → 业务结果 result.ok=True 且入队 set_mode（引擎热重建 orchestrator）。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "mode.set", {"mode": "plan"})
    assert reply["data"]["ok"] is True  # 传输层：指令被处理
    assert reply["data"]["result"] == {"ok": True, "mode": "plan"}  # 业务结果
    assert command_queue.get_nowait() == {"cmd": "set_mode", "mode": "plan"}


def test_mode_set_invalid_rejected_no_enqueue():
    """未知模式名 → result.ok=False，不入队（不谎报已切换）。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "mode.set", {"mode": "nope"})
    assert reply["data"]["result"]["ok"] is False
    assert command_queue.empty()


def test_mode_set_missing_mode_rejected():
    """缺 mode 字段（归一为空串）→ result.ok=False，不入队。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "mode.set", {})
    assert reply["data"]["result"]["ok"] is False
    assert command_queue.empty()


def test_think_set_enqueues_on_valid_effort():
    """think.set 合法档位（off/on/low/medium/high）→ 入队 set_thinking。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "think.set", {"effort": "low"})
    assert reply["data"]["result"] == {"ok": True, "effort": "low"}
    assert command_queue.get_nowait() == {"cmd": "set_thinking", "effort": "low"}


def test_think_set_invalid_rejected_no_enqueue():
    """非法思考档位 → result.ok=False，不入队。"""
    server, _, _, command_queue = _make()
    reply = _call(server, "think.set", {"effort": "extreme"})
    assert reply["data"]["result"]["ok"] is False
    assert command_queue.empty()


def test_mode_think_registered_in_handlers():
    """mode.set / think.set 已注册到 _ws_handlers（协议与服务器接线到位）。"""
    server, _, _, _ = _make()
    assert "mode.set" in server._ws_handlers
    assert "think.set" in server._ws_handlers
