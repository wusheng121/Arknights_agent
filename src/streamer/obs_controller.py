"""OBS 控制器 (stub)。

OBS WebSocket: ws://localhost:4455
功能:
- 切换场景 (游戏/VTuber/全屏)
- 控制来源 (显示/隐藏游戏画面/头像/弹幕)
- 开始/停止直播

当前: stub 实现
后续: 安装 OBS + obs-websocket 插件后激活
"""

from __future__ import annotations

import logging
from typing import Any

from src.streamer.event_bus import EventBus, StreamerEvent, get_event_bus

log = logging.getLogger(__name__)


class OBSController:
    """OBS 控制器 (stub)。"""

    def __init__(self, event_bus: EventBus | None = None, ws_url: str = "ws://localhost:4455") -> None:
        self.bus = event_bus or get_event_bus()
        self.ws_url = ws_url
        self._connected = False
        self._current_scene = ""

        # 订阅事件
        self.bus.subscribe("battle_start", self._on_battle_start)
        self.bus.subscribe("battle_end", self._on_battle_end)

    async def connect(self, password: str = "") -> bool:
        """连接 OBS WebSocket。"""
        try:
            # TODO: 安装 obs-websocket-py 后实现
            # import obswebsocket
            # self._client = obswebsocket.obsws(host="localhost", port=4455, password=password)
            # self._client.connect()
            # self._connected = True
            log.info("[OBS] stub mode (OBS 未安装)")
            return False
        except Exception as e:
            log.warning("[OBS] 连接失败: %s", e)
            return False

    async def switch_scene(self, scene_name: str) -> None:
        """切换场景。"""
        if not self._connected:
            return
        # TODO: self._client.call(obswebsocket.call.SetCurrentScene(scene_name=scene_name))
        self._current_scene = scene_name
        log.info("[OBS] 切换场景: %s", scene_name)

    async def _on_battle_start(self, event: StreamerEvent) -> None:
        """战斗开始 → 切到游戏场景。"""
        await self.switch_scene("游戏")

    async def _on_battle_end(self, event: StreamerEvent) -> None:
        """战斗结束 → 切到 VTuber 场景。"""
        await self.switch_scene("VTuber")

    async def start_streaming(self) -> None:
        """开始直播。"""
        if not self._connected:
            return
        # TODO: self._client.call(obswebsocket.call.StartStreaming())

    async def stop_streaming(self) -> None:
        """停止直播。"""
        if not self._connected:
            return
        # TODO: self._client.call(obswebsocket.call.StopStreaming())

    async def disconnect(self) -> None:
        """断开连接。"""
        self._connected = False
        log.info("[OBS] disconnected")
