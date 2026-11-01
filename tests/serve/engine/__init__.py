"""对话引擎层（``agent.serve.engine``）测试包。

覆盖 ChatEngine / WorkbenchAPI / 指标采集 / 模型与项目管理 / 扩展面板 /
检查点 / 斜杠桥 / 跨设备桥接等纯引擎逻辑，与窗口渲染无关。

本包测试原在 ``tests/ui/``（引擎层当年寄居在 ``agent/ui/workbench/``），
2026-11 随引擎层迁入 ``agent/serve/`` 而一并归位。

@author aceFelix
"""
