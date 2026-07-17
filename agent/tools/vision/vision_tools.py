"""视觉监控工具 —— 让贾维斯实时感知动态画面。

阶段五扩展能力。主 agent 通过这些工具启动/停止/查询摄像头实时监控。
监控由 mediapipe 在本地 CPU 跑，检测手势和人脸事件，触发回调。

工具列表:
- **VisionWatch**: 启动摄像头实时监控（手势+人脸）
- **VisionStop**: 停止监控
- **VisionStatus**: 查询监控状态（当前手势/人脸/fps）

事件触发后，daemon 会：
1. 托盘通知（"检测到点赞手势"）
2. 语音播报（待机中才播，不打断对话）
3. 可选：抓帧喂 LLM 深度理解（后续扩展）

依赖: pip install mediapipe opencv-python
"""

from __future__ import annotations

from typing import Any

from agent.core.context import ToolContext
from agent.core.result import PermissionResult, ToolResult
from agent.core.tool import JSONSchema, Tool


class VisionWatchTool(Tool):
    """启动摄像头实时视觉监控。"""

    name = "VisionWatch"
    description = (
        "启动摄像头实时视觉监控。后台用 mediapipe 持续检测手势和人脸，"
        "检测到事件（手势变化/人脸出现消失）时主动通知用户。"
        "仅当用户明确说'开启监控''打开监控''你帮我盯着...'时才调用此工具。"
        "普通'看看''这是什么'用 CameraShot 拍照即可，不要用此工具。"
        "可选参数: camera_index(默认0), enable_gesture(默认true), enable_face(默认true), fps(默认15), auto_stop_seconds(默认300=5分钟空闲自动关闭)。"
        "注意：监控会持续占用摄像头，空闲超时或用户说'关闭监控'时停止。"
    )
    input_schema: JSONSchema = {
        "type": "object",
        "properties": {
            "camera_index": {"type": "integer", "description": "摄像头索引（默认0=前置）", "default": 0},
            "enable_gesture": {"type": "boolean", "description": "启用手势识别（默认true）", "default": True},
            "enable_face": {"type": "boolean", "description": "启用人脸检测（默认true）", "default": True},
            "fps": {"type": "integer", "description": "帧率（默认15，越高越流畅但越耗CPU）", "default": 15},
            "auto_stop_seconds": {"type": "integer", "description": "空闲自动停止秒数（默认300=5分钟，0=不自动停止）", "default": 300},
        },
    }
    max_result_chars = 1000

    def __init__(self, watcher_factory=None) -> None:
        """watcher_factory: 返回 VisionWatcher 实例的可调用对象。
        daemon 注入；独立调用时为 None（工具会报错）。
        """
        self._watcher_factory = watcher_factory

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        # 持续监控摄像头涉及隐私，非 yolo 模式需确认
        mode = ctx.permission_mode
        if mode == "yolo":
            return PermissionResult.allow("yolo 模式自动放行")
        return PermissionResult.ask("启动摄像头实时监控（持续访问摄像头）")

    def activity_description(self, args: dict[str, Any] | None = None) -> str | None:
        return "启动视觉监控"

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if self._watcher_factory is None:
            return ToolResult.error("视觉监控未注入（仅 daemon 模式可用）")

        watcher = self._watcher_factory()
        if not watcher.available:
            return ToolResult.error(
                "mediapipe 未安装（pip install mediapipe opencv-python 启用）"
            )

        if watcher.running:
            status = watcher.get_status()
            return ToolResult.ok(
                f"视觉监控已在运行\n"
                f"  当前手势: {status['current_gesture']}\n"
                f"  人脸在场: {status['face_present']}\n"
                f"  帧率: {status['fps']}fps"
            )

        camera_index = args.get("camera_index", 0)
        enable_gesture = args.get("enable_gesture", True)
        enable_face = args.get("enable_face", True)
        fps = args.get("fps", 15)
        auto_stop = args.get("auto_stop_seconds", 300)

        # 重新配置 watcher（factory 每次返回同一实例，这里改参数）
        watcher._camera_index = camera_index
        watcher._enable_gesture = enable_gesture
        watcher._enable_face = enable_face
        watcher._fps = fps
        watcher._frame_interval = 1.0 / fps
        watcher._auto_stop_seconds = float(auto_stop)

        if watcher.start():
            mins = int(auto_stop // 60) if auto_stop > 0 else 0
            stop_hint = f"  空闲{mins}分钟自动关闭" if auto_stop > 0 else "  不自动关闭"
            return ToolResult.ok(
                f"✓ 视觉监控已启动\n"
                f"  摄像头: {camera_index}\n"
                f"  手势识别: {'开' if enable_gesture else '关'}\n"
                f"  人脸检测: {'开' if enable_face else '关'}\n"
                f"  帧率: {fps}fps\n"
                f"  检测到事件会主动通知用户\n"
                f"{stop_hint}\n"
                f"  说'关闭监控'或调 VisionStop 停止"
            )
        else:
            return ToolResult.error(
                "启动失败（摄像头被占用或模型加载失败，检查摄像头连接和 mediapipe 安装）"
            )


class VisionStopTool(Tool):
    """停止摄像头实时监控。"""

    name = "VisionStop"
    description = "停止摄像头实时视觉监控，释放摄像头资源。"
    input_schema: JSONSchema = {"type": "object", "properties": {}}
    max_result_chars = 500

    def __init__(self, watcher_factory=None) -> None:
        self._watcher_factory = watcher_factory

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return False

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("停止监控")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if self._watcher_factory is None:
            return ToolResult.error("视觉监控未注入")
        watcher = self._watcher_factory()
        if not watcher.running:
            return ToolResult.ok("视觉监控未在运行")
        watcher.stop()
        return ToolResult.ok("✓ 视觉监控已停止，摄像头已释放")


class VisionStatusTool(Tool):
    """查询视觉监控状态。"""

    name = "VisionStatus"
    description = (
        "查询摄像头实时视觉监控的状态：是否运行、当前手势、人脸在场、帧率等。"
        "用于用户问'监控状态怎么样''你现在看到什么手势'等。"
    )
    input_schema: JSONSchema = {"type": "object", "properties": {}}
    max_result_chars = 800

    def __init__(self, watcher_factory=None) -> None:
        self._watcher_factory = watcher_factory

    def is_read_only(self, args: dict[str, Any]) -> bool:
        return True

    def check_permissions(self, args: dict[str, Any], ctx: ToolContext) -> PermissionResult:
        return PermissionResult.allow("查询状态")

    async def call(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if self._watcher_factory is None:
            return ToolResult.error("视觉监控未注入")
        watcher = self._watcher_factory()
        status = watcher.get_status()

        if not status["available"]:
            return ToolResult.ok("视觉监控不可用（pip install mediapipe opencv-python 启用）")

        if not status["running"]:
            return ToolResult.ok("视觉监控未运行（用 VisionWatch 启动）")

        lines = [
            f"视觉监控运行中:",
            f"  摄像头: {status['camera_index']}",
            f"  帧率: {status['fps']}fps",
            f"  已处理帧数: {status['frame_count']}",
            f"  手势识别: {'开' if status['gesture_enabled'] else '关'}",
            f"  人脸检测: {'开' if status['face_enabled'] else '关'}",
            f"  当前手势: {status['current_gesture']}",
            f"  人脸在场: {'是' if status['face_present'] else '否'}",
        ]
        return ToolResult.ok("\n".join(lines))


# ---------------------------------------------------------------------------
# 注册函数
# ---------------------------------------------------------------------------


def register_vision_tools(registry, watcher_factory=None) -> int:
    """注册视觉监控工具。返回注册数。

    Args:
        registry: ToolRegistry 实例。
        watcher_factory: 返回 VisionWatcher 实例的可调用对象（daemon 注入）。
            None 时工具可注册但调用会报错（仅 daemon 模式可用）。
    """
    count = 0
    for tool_cls, factory_arg in [
        (VisionWatchTool, watcher_factory),
        (VisionStopTool, watcher_factory),
        (VisionStatusTool, watcher_factory),
    ]:
        if tool_cls.name in registry:
            continue
        try:
            registry.register(tool_cls(factory_arg))
            count += 1
        except Exception:
            pass
    return count
