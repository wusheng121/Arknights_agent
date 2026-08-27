"""B站弹幕读取器 (stub)。

B站直播弹幕 WebSocket:
- URL: wss://broadcast-msg.chat.bilibili.com:{port}/sub
- 需要房间号 + cookies

功能:
- 实时读取直播间弹幕
- 发布 danmaku_received 事件到事件总线
- 可选: LLM 回复弹幕

当前: stub 实现 + 模拟弹幕
后续: 配置 B站直播间信息后激活
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from src.streamer.event_bus import EventBus, StreamerEvent, get_event_bus

log = logging.getLogger(__name__)

# 模拟弹幕 (用于测试)
MOCK_DANMAKU = [
    "这干员什么练度？",
    "煌单核可以的",
    "主播技术不错",
    "这个关卡我打了好几遍",
    "6666",
    "技能什么时候开？",
    "这波稳了",
    "加油！",
    "煌yyds",
    "求作业JSON",
]


class DanmakuReader:
    """B站弹幕读取器 (stub + 模拟模式)。"""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        room_id: str = "",
        mock: bool = True,
        mock_interval: float = 15.0,
    ) -> None:
        self.bus = event_bus or get_event_bus()
        self.room_id = room_id
        self.mock = mock
        self.mock_interval = mock_interval
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """启动弹幕读取。"""
        if self._running:
            return
        self._running = True
        if self.mock:
            self._task = asyncio.create_task(self._mock_loop())
            log.info("[Danmaku] mock mode started (interval=%.0fs)", self.mock_interval)
        else:
            # TODO: 连接 B站 WebSocket
            log.info("[Danmaku] real mode (not implemented yet)")

    async def stop(self) -> None:
        """停止弹幕读取。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        log.info("[Danmaku] stopped")

    async def _mock_loop(self) -> None:
        """模拟弹幕循环。"""
        while self._running:
            await asyncio.sleep(self.mock_interval)
            if not self._running:
                break
            # 随机弹幕
            text = random.choice(MOCK_DANMAKU)
            user = f"观众{random.randint(1000, 9999)}"
            self.bus.publish_now(
                "danmaku_received",
                text,
                {"user": user, "text": text},
            )
            log.info("[Danmaku] %s: %s", user, text)

    async def _connect_bilibili(self) -> None:
        """连接 B站直播弹幕 WebSocket (TODO)。"""
        import websockets
        # TODO: 实现 B站弹幕 WebSocket 协议
        # 1. 获取 room_id → real_room_id
        # 2. 获取 danmaku server 地址
        # 3. WebSocket 连接
        # 4. 发送 auth packet
        # 5. 循环接收弹幕
        pass


if __name__ == "__main__":
    async def test():
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        bus = get_event_bus()
        danmaku = DanmakuReader(mock=True, mock_interval=5.0)
        await bus.start()
        await danmaku.start()

        # 注册处理器
        bus.subscribe("danmaku_received", lambda e: print(f"[弹幕] {e.data.get('user')}: {e.message}"))

        await asyncio.sleep(20)
        await danmaku.stop()
        await bus.stop()

    asyncio.run(test())
