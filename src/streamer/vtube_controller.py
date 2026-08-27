"""VTube Studio 控制器 (stub)。

VTube Studio API: WebSocket, localhost:8001
功能:
- 控制虚拟形象表情/动作
- 嘴型同步 (配合 TTS 音频)
- 切换模型/服装

当前: stub 实现,不连接真实 VTube Studio
后续: 安装 VTube Studio + 启用 WebSocket API 后激活
"""

from __future__ import annotations

import logging
from typing import Any

from src.streamer.event_bus import EventBus, StreamerEvent, get_event_bus

log = logging.getLogger(__name__)


class VTubeController:
    """VTube Studio 控制器 (stub)。"""

    def __init__(self, event_bus: EventBus | None = None, ws_url: str = "ws://localhost:8001") -> None:
        self.bus = event_bus or get_event_bus()
        self.ws_url = ws_url
        self._connected = False
        self._model_name = ""

        # 订阅 TTS 事件用于嘴型同步
        self.bus.subscribe("tts_speaking", self._on_speaking)
        self.bus.subscribe("tts_finished", self._on_finished)

    async def connect(self) -> bool:
        """连接 VTube Studio WebSocket。"""
        try:
            import websockets
            # TODO: 实际连接 VTube Studio
            # self._ws = await websockets.connect(self.ws_url)
            # self._connected = True
            log.info("[VTube] stub mode (VTube Studio 未安装)")
            return False
        except Exception as e:
            log.warning("[VTube] 连接失败: %s", e)
            return False

    async def _on_speaking(self, event: StreamerEvent) -> None:
        """TTS 播放中 → 嘴型动画。"""
        if not self._connected:
            return
        # TODO: 发送嘴型同步数据到 VTube Studio

    async def _on_finished(self, event: StreamerEvent) -> None:
        """TTS 播放完成 → 停止嘴型。"""
        if not self._connected:
            return
        # TODO: 停止嘴型动画

    async def set_expression(self, expression: str) -> None:
        """设置表情。"""
        if not self._connected:
            return
        # TODO: 发送表情数据

    async def disconnect(self) -> None:
        """断开连接。"""
        self._connected = False
        log.info("[VTube] disconnected")
