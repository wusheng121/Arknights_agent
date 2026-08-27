"""AI 主播主控: 统一调度所有组件。

初始化流程:
1. 创建事件总线
2. 创建各组件 (解说/TTS/VTube/OBS/弹幕)
3. 启动所有组件
4. 接收 BattleMonitor 事件 → 发布到事件总线 → 各组件响应

使用方式:
    streamer = Streamer(mock_danmaku=True)
    await streamer.start()
    # 战斗时:
    streamer.on_battle_event(battle_monitor_event)
    # 战斗后:
    await streamer.stop()
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from src.streamer.event_bus import EventBus, StreamerEvent, get_event_bus
from src.streamer.commentator import Commentator
from src.streamer.tts_engine import TTSEngine
from src.streamer.vtube_controller import VTubeController
from src.streamer.obs_controller import OBSController
from src.streamer.danmaku_reader import DanmakuReader

log = logging.getLogger(__name__)


class Streamer:
    """AI 主播主控。"""

    def __init__(
        self,
        voice: str = "male_young",
        mock_danmaku: bool = True,
        danmaku_interval: float = 15.0,
        enable_vtube: bool = False,
        enable_obs: bool = False,
    ) -> None:
        self.bus = get_event_bus()

        # 组件
        self.commentator = Commentator()
        self.tts = TTSEngine(voice=voice)
        self.vtube = VTubeController()
        self.obs = OBSController()
        self.danmaku = DanmakuReader(
            mock=mock_danmaku,
            mock_interval=danmaku_interval,
        )

        self._running = False
        self._enable_vtube = enable_vtube
        self._enable_obs = enable_obs

        # 全局事件日志
        self.bus.subscribe_all(self._log_event)

    async def start(self) -> None:
        """启动 AI 主播。"""
        if self._running:
            return
        self._running = True

        log.info("=== AI 主播启动 ===")

        # 启动事件总线
        await self.bus.start()

        # 启动各组件
        await self.tts.start()
        await self.danmaku.start()

        if self._enable_vtube:
            await self.vtube.connect()
        if self._enable_obs:
            await self.obs.connect()

        # 主播开场白
        self.bus.publish_now("streamer_start", "AI 主播已上线")
        await asyncio.sleep(0.5)
        self.bus.publish_now(
            "commentary_ready",
            "大家好,我是AI主播,今天来打明日方舟!",
            {"event_type": "streamer_start"},
        )

        log.info("=== AI 主播已启动 ===")

    async def stop(self) -> None:
        """停止 AI 主播。"""
        if not self._running:
            return
        self._running = False

        log.info("=== AI 主播关闭中 ===")

        # 结束语
        self.bus.publish_now(
            "commentary_ready",
            "今天的直播就到这里,感谢观看!",
            {"event_type": "streamer_stop"},
        )
        await asyncio.sleep(3)

        # 停止各组件
        await self.danmaku.stop()
        await self.tts.stop()
        await self.vtube.disconnect()
        await self.obs.disconnect()
        await self.bus.stop()

        log.info("=== AI 主播已关闭 ===")

    def on_battle_event(self, battle_event: Any) -> None:
        """接收 BattleMonitor 事件 → 转发到事件总线。

        battle_event 可以是:
        - BattleEvent (from battle_monitor.py)
        - dict (手动构造)
        """
        # BattleEvent dataclass → StreamerEvent
        if hasattr(battle_event, "event_type"):
            self.bus.publish_now(
                battle_event.event_type,
                battle_event.message,
                battle_event.data,
            )
        elif isinstance(battle_event, dict):
            self.bus.publish_now(
                battle_event.get("event_type", "unknown"),
                battle_event.get("message", ""),
                battle_event.get("data", {}),
            )

    def on_battle_start(self, stage: str, oper_count: int = 0) -> None:
        """快捷方法: 战斗开始。"""
        self.bus.publish_now("battle_start", f"战斗开始: {stage}", {
            "stage": stage, "oper_count": oper_count,
        })

    def on_battle_end(self, result: str) -> None:
        """快捷方法: 战斗结束。"""
        self.bus.publish_now("battle_end", f"战斗结束: {result}", {"result": result})

    def on_kills_update(self, kills: int, delta: int = 1) -> None:
        """快捷方法: 击杀更新。"""
        self.bus.publish_now("kills_update", f"击杀: {kills}", {"kills": kills, "delta": delta})

    def on_anomaly(self, anomaly_type: str, message: str, severity: str = "warning") -> None:
        """快捷方法: 异常检测。"""
        self.bus.publish_now("anomaly", message, {
            "type": anomaly_type, "severity": severity,
        })

    async def _log_event(self, event: StreamerEvent) -> None:
        """全局事件日志。"""
        log.info("[Event] %s: %s", event.event_type, event.message[:50])


async def main():
    """测试: 模拟一场战斗的完整直播流程。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    streamer = Streamer(mock_danmaku=True, danmaku_interval=10.0)
    await streamer.start()

    # 模拟战斗
    await asyncio.sleep(5)
    streamer.on_battle_start("1-7", oper_count=1)

    await asyncio.sleep(8)
    streamer.on_kills_update(5, 5)

    await asyncio.sleep(8)
    streamer.on_kills_update(10, 5)

    await asyncio.sleep(8)
    streamer.on_anomaly("skill_not_used", "技能好了但没开", "warning")

    await asyncio.sleep(8)
    streamer.on_kills_update(20, 10)

    await asyncio.sleep(8)
    streamer.on_battle_end("win")

    await asyncio.sleep(10)
    await streamer.stop()


if __name__ == "__main__":
    asyncio.run(main())
