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
        """合成语音并播放(带重试)。"""
        try:
            import edge_tts
        except ImportError:
            log.warning("edge-tts 未安装,跳过语音合成")
            return

        voice = VOICES.get(self.voice_key, self.voice_key)
        # 固定文件名(避免竞争条件: 不删除,每次覆盖)
        out_path = os.path.join(TTS_DIR, "tts_latest.mp3")

        # 合成(带3次重试)
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=self.rate)
                await communicate.save(out_path)
                if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                    break
                log.warning("TTS 合成失败(attempt %d): 文件为空", attempt + 1)
            except Exception as e:
                log.warning("TTS 合成异常(attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(1)
        else:
            log.warning("TTS 合成3次重试均失败,跳过: %s", text[:30])
            return

        # 播放(同步,播完才返回)
        await self._play_audio(out_path)

    async def _play_audio(self, path: str) -> None:
        """同步播放音频文件(MCI,播完才返回)。"""
        import ctypes
        import sys

        if sys.platform != "win32":
            import subprocess
            player = "aplay" if sys.platform.startswith("linux") else "afplay"
            subprocess.run([player, path], capture_output=True, timeout=60)
            return

        # Windows MCI: open → play wait → close (同步,无竞争条件)
        winmm = ctypes.windll.winmm
        open_cmd = f'open "{path}" type mpegvideo alias tts_voice'
        play_cmd = "play tts_voice wait"
        close_cmd = "close tts_voice"

        r1 = winmm.mciSendStringW(open_cmd, None, 0, 0)
        if r1 != 0:
            buf = ctypes.create_unicode_buffer(256)
            winmm.mciGetErrorStringW(r1, buf, 256)
            log.warning("MCI open 失败(%d): %s", r1, buf.value)
            return

        winmm.mciSendStringW(play_cmd, None, 0, 0)
        winmm.mciSendStringW(close_cmd, None, 0, 0)

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
