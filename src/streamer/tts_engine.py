"""TTS 引擎: edge-tts 语音合成 + winsound 播放。

edge-tts: 微软免费在线 TTS,支持中文多音色
winsound: Windows 内置音频播放

语音选择:
- zh-CN-YunxiNeural: 男声,年轻活泼(适合主播)
- zh-CN-XiaoxiaoNeural: 女声,温柔
- zh-CN-YunjianNeural: 男声,沉稳

使用方式:
  tts = TTSEngine()
  await tts.speak("开始打1-7了!")  # 合成+播放
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from typing import Any

from src.streamer.event_bus import EventBus, StreamerEvent, get_event_bus

log = logging.getLogger(__name__)

# TTS 临时文件目录
TTS_DIR = os.path.join(tempfile.gettempdir(), "arknights_tts")

# 可用语音
VOICES = {
    "male_young": "zh-CN-YunxiNeural",      # 男声,年轻活泼
    "female_gentle": "zh-CN-XiaoxiaoNeural", # 女声,温柔
    "male_calm": "zh-CN-YunjianNeural",      # 男声,沉稳
    "female_lively": "zh-CN-XiaoyiNeural",   # 女声,活泼
}


class TTSEngine:
    """edge-tts 语音合成 + winsound 播放。"""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        voice: str = "male_young",
        rate: str = "+0%",
    ) -> None:
        self.bus = event_bus or get_event_bus()
        self.voice_key = voice
        self.rate = rate
        self._speaking = False
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._enabled = True

        os.makedirs(TTS_DIR, exist_ok=True)

        # 订阅解说事件
        self.bus.subscribe("commentary_ready", self._on_commentary)

    async def start(self) -> None:
        """启动 TTS 播放循环。"""
        if self._task:
            return
        self._task = asyncio.create_task(self._playback_loop())
        log.info("[TTS] started, voice=%s", VOICES.get(self.voice_key, self.voice_key))

    async def stop(self) -> None:
        """停止 TTS。"""
        self._enabled = False
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        log.info("[TTS] stopped")

    async def speak(self, text: str) -> None:
        """合成并播放语音。"""
        if not self._enabled or not text.strip():
            return
        await self._queue.put(text)

    async def _on_commentary(self, event: StreamerEvent) -> None:
        """收到解说文本 → 入队播放。"""
        text = event.message
        if text:
            await self.speak(text)

    async def _playback_loop(self) -> None:
        """播放循环: 从队列取文本 → 合成 → 播放。"""
        while self._enabled:
            try:
                text = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if not text.strip():
                continue

            try:
                self._speaking = True
                self.bus.publish_now("tts_speaking", text, {"text": text})
                await self._synthesize_and_play(text)
            except Exception as e:
                log.warning("TTS 播放失败: %s", e)
            finally:
                self._speaking = False
                self.bus.publish_now("tts_finished", "", {"text": text})

    async def _synthesize_and_play(self, text: str) -> None:
        """合成语音并播放。"""
        try:
            import edge_tts
        except ImportError:
            log.warning("edge-tts 未安装,跳过语音合成")
            return

        voice = VOICES.get(self.voice_key, self.voice_key)
        # 生成文件名(用时间戳避免冲突)
        ts = int(time.time() * 1000)
        out_path = os.path.join(TTS_DIR, f"tts_{ts}.mp3")

        # 合成
        communicate = edge_tts.Communicate(text, voice, rate=self.rate)
        await communicate.save(out_path)

        if not os.path.exists(out_path):
            log.warning("TTS 文件未生成: %s", out_path)
            return

        # 播放 (winsound 不支持 mp3,用 subprocess 调用)
        await self._play_audio(out_path)

        # 清理
        try:
            os.remove(out_path)
        except Exception:
            pass

    async def _play_audio(self, path: str) -> None:
        """播放音频文件。"""
        import subprocess
        import sys

        if sys.platform == "win32":
            # Windows: 用 start 命令播放 (等待播放完成)
            # /wait 让进程等待播放结束
            subprocess.run(
                ["cmd", "/c", "start", "/wait", "", path],
                capture_output=True,
                timeout=30,
            )
        else:
            # Linux/Mac: 用 aplay/afplay
            player = "aplay" if sys.platform.startswith("linux") else "afplay"
            subprocess.run([player, path], capture_output=True, timeout=30)

    def is_speaking(self) -> bool:
        return self._speaking


if __name__ == "__main__":
    async def test():
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        bus = get_event_bus()
        tts = TTSEngine(voice="male_young")
        await bus.start()
        await tts.start()

        # 测试语音
        print("测试1: 短句")
        await tts.speak("开始打1-7了，今天用煌单核！")
        await asyncio.sleep(5)

        print("测试2: 中句")
        await tts.speak("已经击杀十个了，煌清怪效率不错！")
        await asyncio.sleep(5)

        print("测试3: 长句")
        await tts.speak("不好，煌血量危险了！技能好了但没开，让我点一下技能！")
        await asyncio.sleep(8)

        await tts.stop()
        await bus.stop()
        print("测试完成")

    asyncio.run(test())
