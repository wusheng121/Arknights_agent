"""解说生成器: 接收游戏事件 → LLM 生成解说文本 → 发布到事件总线。

策略:
1. 关键事件(battle_start/end, anomaly, intervene) → 立即解说
2. 击杀里程碑(kills=5/10/15...) → 简短解说
3. 冷却期(无事件3秒) → 随机闲聊(策略知识/干员介绍/弹幕回复)
4. 弹幕 → 高优先级回复

LLM prompt 设计:
- 角色: 明日方舟 AI 主播,风格活泼专业
- 输入: 事件类型 + 数据 + 最近历史
- 输出: 一句话解说(口语化,适合 TTS)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from src.streamer.event_bus import EventBus, StreamerEvent, get_event_bus

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是明日方舟 AI 主播的解说模块。接收游戏事件,生成一句话口语化解说。

要求:
- 像主播说话,自然口语,不要太正式
- 一句话,15-30字,适合语音合成
- 可以带情绪(激动/紧张/高兴)
- 不要用"我"开头,直接说内容

示例:
- 事件: battle_start, 关卡: 1-7 → "开始打1-7了,今天用煌单核,应该稳!"
- 事件: kills_update, kills: 10 → "已经击杀十个了,煌清怪效率不错!"
- 事件: anomaly, type: skill_not_used → "技能好了但没开,等等,让我点一下!"
- 事件: battle_end, result: win → "通关了!煌单核果然稳!"
- 事件: battle_end, result: lose → "翻车了...这关有点难,我们再来一次"
- 事件: danmaku, text: "这干员什么练度" → "这位问练度,煌精二60级,够用了"

只输出解说文本,不要任何标记或解释。"""

FALLBACK_COMMENTARY = {
    "battle_start": "战斗开始!",
    "battle_end": "战斗结束。",
    "kills_update": "击杀更新。",
    "anomaly": "注意,有情况!",
    "intervene": "紧急操作!",
    "danmaku_received": "感谢弹幕!",
    "idle": "正在挂机中...",
}


class Commentator:
    """LLM 解说生成器。"""

    def __init__(
        self,
        event_bus: EventBus | None = None,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
    ) -> None:
        self.bus = event_bus or get_event_bus()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self._client = None
        self._history: list[str] = []  # 最近5条解说
        self._last_event_time = time.time()
        self._kill_milestones = {5, 10, 15, 20, 25, 30, 50}
        self._last_kills = 0

        # 订阅事件
        self.bus.subscribe("battle_start", self._on_battle_start)
        self.bus.subscribe("battle_end", self._on_battle_end)
        self.bus.subscribe("kills_update", self._on_kills_update)
        self.bus.subscribe("anomaly", self._on_anomaly)
        self.bus.subscribe("intervene", self._on_intervene)
        self.bus.subscribe("danmaku_received", self._on_danmaku)

    async def _init_client(self) -> None:
        if self._client or not self.api_key:
            return
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def _generate(self, event_desc: str, context: dict | None = None) -> str:
        """调用 LLM 生成解说文本。"""
        await self._init_client()
        if not self._client:
            return FALLBACK_COMMENTARY.get(context.get("event_type", ""), "...")

        # 构建上下文
        history_str = " | ".join(self._history[-3:]) if self._history else "无"
        user_msg = f"事件: {event_desc}\n上下文: {json.dumps(context or {}, ensure_ascii=False)}\n最近解说: {history_str}"

        try:
            resp = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                max_tokens=60,
                temperature=0.7,
                extra_body={"thinking": {"type": "disabled"}},
            )
            text = (resp.choices[0].message.content or "").strip()
            if len(text) > 80:
                text = text[:80]
            self._history.append(text)
            if len(self._history) > 10:
                self._history.pop(0)
            self._last_event_time = time.time()
            return text
        except Exception as e:
            log.warning("解说生成失败: %s", e)
            return FALLBACK_COMMENTARY.get(context.get("event_type", ""), "...")

    async def _on_battle_start(self, event: StreamerEvent) -> None:
        stage = event.data.get("stage", "?")
        opers = event.data.get("oper_count", 0)
        text = await self._generate(
            f"战斗开始, 关卡={stage}, 干员数={opers}",
            {"event_type": "battle_start", "stage": stage, "oper_count": opers},
        )
        self.bus.publish_now("commentary_ready", text, {"event_type": "battle_start"})

    async def _on_battle_end(self, event: StreamerEvent) -> None:
        result = event.data.get("result", "unknown")
        text = await self._generate(
            f"战斗结束, 结果={result}",
            {"event_type": "battle_end", "result": result},
        )
        self.bus.publish_now("commentary_ready", text, {"event_type": "battle_end", "result": result})

    async def _on_kills_update(self, event: StreamerEvent) -> None:
        kills = event.data.get("kills", 0)
        delta = event.data.get("delta", 0)
        # 只在里程碑解说
        if kills in self._kill_milestones or kills - self._last_kills >= 5:
            text = await self._generate(
                f"击杀数更新: {kills} (本次+{delta})",
                {"event_type": "kills_update", "kills": kills, "delta": delta},
            )
            self.bus.publish_now("commentary_ready", text, {"event_type": "kills_update", "kills": kills})
        self._last_kills = kills

    async def _on_anomaly(self, event: StreamerEvent) -> None:
        atype = event.data.get("type", "unknown")
        severity = event.data.get("severity", "warning")
        text = await self._generate(
            f"异常检测: 类型={atype}, 严重度={severity}, 描述={event.message}",
            {"event_type": "anomaly", "anomaly_type": atype, "severity": severity},
        )
        self.bus.publish_now("commentary_ready", text, {"event_type": "anomaly", "anomaly_type": atype})

    async def _on_intervene(self, event: StreamerEvent) -> None:
        action = event.data.get("action", "unknown")
        text = await self._generate(
            f"应急干预: 动作={action}, 描述={event.message}",
            {"event_type": "intervene", "action": action},
        )
        self.bus.publish_now("commentary_ready", text, {"event_type": "intervene", "action": action})

    async def _on_danmaku(self, event: StreamerEvent) -> None:
        user = event.data.get("user", "观众")
        text = event.data.get("text", "")
        reply = await self._generate(
            f"收到弹幕: 用户={user}, 内容={text}",
            {"event_type": "danmaku_received", "user": user, "text": text},
        )
        self.bus.publish_now("commentary_ready", reply, {"event_type": "danmaku_reply", "user": user})
        self.bus.publish_now("danmaku_reply", reply, {"user": user, "reply": reply})


if __name__ == "__main__":
    async def test():
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        bus = get_event_bus()
        commentator = Commentator()
        await bus.start()

        # 模拟事件
        bus.publish_now("battle_start", "战斗开始", {"stage": "1-7", "oper_count": 1})
        await asyncio.sleep(3)

        bus.publish_now("kills_update", "击杀5", {"kills": 5, "delta": 5})
        await asyncio.sleep(3)

        bus.publish_now("anomaly", "技能未使用", {"type": "skill_not_used", "severity": "warning"})
        await asyncio.sleep(3)

        bus.publish_now("battle_end", "通关", {"result": "win"})
        await asyncio.sleep(3)

        await bus.stop()

    asyncio.run(test())
