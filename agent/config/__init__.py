"""配置层。

拆分后的模块:
- settings.py: Settings 数据类 + TOML 加载 + 字段映射（loader）
- env.py: 环境变量覆盖逻辑
- model_registry.py: 模型 TOML 持久化（保存/恢复）
"""

from agent.config.settings import Settings, load_settings
from agent.config.model_registry import save_custom_model, save_last_model, save_realtime_talk_auto_start

__all__ = [
    "Settings",
    "load_settings",
    "save_custom_model",
    "save_last_model",
    "save_realtime_talk_auto_start",
]
