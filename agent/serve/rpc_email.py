"""email.* 桌面指令（管家服务 → 邮件发送 / 邮箱服务）—— RPC 混入。

从 `agent/serve/server.py` 拆出（aceFelix，2026-10）：`DesktopBridgeServer` 承载
全部桌面指令后已突破 800 行上限，按「一族指令一个混入文件」拆分，指令语义与
回执口径**一字不改**，只是换了个存放位置。

为什么单成一族：邮件发送是阻塞 SMTP IO（要 `asyncio.to_thread`），邮箱配置是
快速本地 IO（同步 `_register_rpc` 即可），两者的失败语义也不同（前者错误原文
来自 SMTP，后者是校验/落盘失败），与对话链路毫无共享。

依赖宿主类（`DesktopBridgeServer`）提供：``_api`` / ``_settings`` /
``_send_json``，注册在宿主 `_register_desktop_handlers` 里完成。

@author aceFelix
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agent.config.email_settings import (
    apply_email_config,
    read_email_config,
    save_email_config,
    validate_email_config,
)
from agent.serve import protocol

if TYPE_CHECKING:  # 仅为 IDE/类型检查声明宿主属性，运行期不引入循环导入
    from agent.config.settings import Settings
    from agent.ui.workbench.api import WorkbenchAPI


class EmailRpcMixin:
    """email.send / email.config.get / email.config.set 三个指令的处理器。

    @author aceFelix
    """

    if TYPE_CHECKING:
        _api: "WorkbenchAPI"
        _settings: "Settings"

        async def _send_json(self, ws: Any, msg: dict) -> None: ...  # pragma: no cover

    async def _cmd_email_send(self, ws: Any, data: dict) -> None:
        """email.send 指令：管家服务「邮件发送」表单落地（异步，避免阻塞事件循环）。

        校验 subject/body 非空（快速失败，与其它 _rpc_* 抛 ValueError 同口径）后，
        把实际的 SMTP 发送放进 asyncio.to_thread 的工作线程执行——api.send_email
        是同步阻塞 IO（timeout 30s），直接 await 会冻住事件循环、掐断流式事件。
        回执 result 为 {"ok": true, "message": ...}；失败 ok=false + 错误原文
        （邮件未启用 / 缺配置 / 认证失败等，前端弹窗内联显示）。@author aceFelix
        """
        try:
            subject = str(data.get("subject") or "").strip()
            body = str(data.get("body") or "").strip()
            if not subject:
                raise ValueError("邮件主题不能为空")
            if not body:
                raise ValueError("邮件正文不能为空")
            result = await asyncio.to_thread(
                self._api.send_email,
                {
                    "to": str(data.get("to") or ""),
                    "cc": str(data.get("cc") or ""),
                    "bcc": str(data.get("bcc") or ""),
                    "subject": subject,
                    "body": body,
                },
            )
            msg = protocol.build_reply(protocol.CMD_EMAIL_SEND, ok=True, result=result)
        except Exception as e:  # noqa: BLE001 - 回执携带错误，连接不中断
            msg = protocol.build_reply(
                protocol.CMD_EMAIL_SEND, ok=False, error=f"{type(e).__name__}: {e}"
            )
        await self._send_json(ws, msg)

    def _rpc_email_config_get(self, data: dict) -> dict:
        """email.config.get：邮箱服务面板数据源（当前发件邮箱与 SMTP 服务信息）。

        授权码**不回传明文**，只回 ``has_password`` 布尔——桌面壳据此把密码框渲染为
        「已配置，留空即不修改」。字段口径见 agent/config/email_settings.py。

        @author aceFelix
        """
        return read_email_config(self._settings)

    def _rpc_email_config_set(self, data: dict) -> dict:
        """email.config.set：修改邮箱服务配置（校验 → 落盘 → 运行时生效 → 回读）。

        顺序与 settings.set 一致：先落盘 settings.toml 的 [email] 表，再改运行时
        Settings（SendEmailTool 每次现读，立即生效）；落盘失败则回执报错且不动运行时。
        授权码留空 = 保持原值（不回传、不覆盖）。回执 result 为落盘后的完整配置快照。

        @author aceFelix
        """
        patch = validate_email_config(data)
        if not patch:
            raise ValueError("email.config.set 至少需要一个有效的配置项")
        if not save_email_config(patch):
            raise RuntimeError("settings.toml 落盘失败，请检查磁盘写权限")
        apply_email_config(self._settings, patch)
        return read_email_config(self._settings)
