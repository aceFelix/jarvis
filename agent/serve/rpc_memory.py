"""memory.get / memory.save（桌面「设置 → 长期记忆」面板，2026-11）。

设计口径：

- **读**：一次返回 user / project 两个范围；未选项目时 project 为 ``None``，
  面板显示“先选项目”而不是报错。
- **写**：整体覆盖，走 ``store.save_memory_scope``（原子写 + 单份 .bak）。
  保存成功后调 ``api.reload_memory`` 让引擎线程重建系统提示词，并推
  ``memory_changed``，本会话下一轮就带上新记忆。
- 模型侧的「追加一条」走 ``MemoryUpdate`` 工具，与本面板共用 store 层，
  但只允许追加，不提供覆盖/清空。

@author aceFelix
"""

from __future__ import annotations

from typing import Any

from agent.core.memory.store import MEMORY_SCOPES, read_memory_scope, save_memory_scope


class MemoryRpcMixin:
    """长期记忆面板的 RPC 处理器（混入 DesktopBridgeServer）。"""

    def _rpc_memory_get(self, data: dict[str, Any]) -> dict[str, Any]:
        """memory.get：返回两个范围的记忆快照，供设置面板渲染。"""
        workdir = self._settings.workdir
        entries = [read_memory_scope(workdir, "user")]
        if workdir:
            entries.append(read_memory_scope(workdir, "project"))
        return {"workdir": workdir, "entries": entries}

    def _rpc_memory_save(self, data: dict[str, Any]) -> dict[str, Any]:
        """memory.save：整体覆盖一个范围并让当前会话立即生效。

        校验顺序：scope 合法 → content 是字符串 → store 长度上限。
        写失败（权限/磁盘）由 store 抛 OSError，传输层回执 ok=false。
        """
        scope = str(data.get("scope", "")).strip()
        if scope not in MEMORY_SCOPES:
            raise ValueError(f"未知记忆范围: {scope}（可选 user / project）")
        content = data.get("content")
        if not isinstance(content, str):
            raise ValueError("content 必须是字符串")
        entry = save_memory_scope(self._settings.workdir, scope, content)
        # 引擎未装配时 reload_memory 只入队，下次会话装配自然读到新文件；
        # 已装配时引擎线程内重建提示词，本轮之后即生效。
        self._api.reload_memory(scope)
        return {"scope": scope, "entry": entry}
