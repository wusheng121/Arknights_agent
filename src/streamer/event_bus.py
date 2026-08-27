"""事件总线: 统一 pub/sub,连接所有 AI 主播组件。

事件类型:
  # 游戏事件 (from BattleMonitor)
  battle_start / battle_end / kills_update / anomaly / intervene

  # 解说事件 (from commentator)
  commentary_ready  # 解说文本生成完成
  tts_speaking      # 正在播放语音
  tts_finished      # 语音播放完成

  # 弹幕事件 (from danmaku_reader)
  danmaku_received  # 收到弹幕
  danmaku_reply     # LLM 回复弹幕

  # 系统事件
  streamer_start / streamer_stop
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class StreamerEvent:
    """AI 主播事件。"""
    event_type: str
    message: str = ""
    data: dict = field(default_factory=dict)
    timestamp: float = 0.0

    def __repr__(self) -> str:
        return f"[{self.event_type}] {self.message}"


EventHandler = Callable[[StreamerEvent], Any]


class EventBus:
    """异步事件总线: 发布/订阅模式。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._global_handlers: list[EventHandler] = []
        self._queue: asyncio.Queue[StreamerEvent] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task | None = None

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """订阅特定事件类型。"""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """订阅所有事件。"""
        self._global_handlers.append(handler)

    def publish(self, event: StreamerEvent) -> None:
        """发布事件(非阻塞,放入队列)。"""
        import time
        if not event.timestamp:
            event.timestamp = time.time()
        self._queue.put_nowait(event)

    def publish_now(self, event_type: str, message: str = "", data: dict | None = None) -> None:
        """快捷发布。"""
        self.publish(StreamerEvent(
            event_type=event_type,
            message=message,
            data=data or {},
        ))

    async def start(self) -> None:
        """启动事件循环。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("[EventBus] started")

    async def stop(self) -> None:
        """停止事件循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        log.info("[EventBus] stopped")

    async def _loop(self) -> None:
        """事件分发循环。"""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # 分发给特定类型订阅者
            handlers = self._handlers.get(event.event_type, [])
            for h in handlers + self._global_handlers:
                try:
                    result = h(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    log.exception("event handler error: %s", event.event_type)

            self._queue.task_done()


# 全局单例
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """获取全局事件总线实例。"""
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


if __name__ == "__main__":
    async def test():
        bus = EventBus()

        # 订阅
        bus.subscribe("battle_start", lambda e: print(f"[handler] {e}"))
        bus.subscribe("commentary_ready", lambda e: print(f"[TTS] {e.message}"))
        bus.subscribe_all(lambda e: print(f"[global] {e.event_type}"))

        await bus.start()

        # 发布事件
        bus.publish_now("battle_start", "开始打1-7了！", {"stage": "1-7"})
        bus.publish_now("commentary_ready", "煌单核果然稳！", {})
        bus.publish_now("battle_end", "通关了！", {"result": "win"})

        await asyncio.sleep(1)
        await bus.stop()

    asyncio.run(test())
