"""感知融合:MAA 结构化 + MaaAI 细粒度 + 云 VLM 语义 → GameState。

对应 REVIEW.md 第四节-3 的「语义鸿沟」:MAA CV 读的是特征点,不便直接喂 LLM;
MaaAI 补血条/技能/朝向等细粒度;云 VLM 补「敌方波次/威胁方向」等高层语义。
三路融合后喂 LLM 决策。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.game.maapy_client import MaapyEvent
from src.resilience.guarded_call import GuardedCall


@dataclass
class OperatorState:
    name: str
    location: tuple[int, int] | None = None
    hp: float = 1.0
    skill_ready: bool = False
    direction: str | None = None


@dataclass
class GameState:
    stage: str = ""
    cost: int = 0
    step: int = 0
    operators: list[OperatorState] = field(default_factory=list)
    vlm_desc: str = ""      # 云 VLM 高层语义(敌方波次/威胁方向)
    maaai_extra: str = ""   # MaaAI 细粒度补充(预留)


class Perception:
    """订阅 MAA 回调累积结构化状态;snapshot 时融合云 VLM 语义。"""

    def __init__(self, vlm: GuardedCall | None = None) -> None:
        self._struct: dict = {}
        self._stage = ""
        self._vlm = vlm

    async def update_from_maapy(self, ev: MaapyEvent) -> None:
        d = ev.details
        what = d.get("what")
        if what == "StageInfo":
            self._stage = d.get("name", self._stage)
        elif what == "BattlefieldState":
            self._struct = d

    async def snapshot(self, screenshot: Any | None = None) -> GameState:
        import re
        s = self._struct
        ops: list[OperatorState] = []
        for o in s.get("operators", []):
            loc = o.get("location")
            ops.append(
                OperatorState(
                    name=o.get("name", ""),
                    location=tuple(loc) if loc else None,
                    hp=o.get("hp", 1.0),
                    skill_ready=o.get("skill_ready", False),
                    direction=o.get("direction"),
                )
            )
        vlm_desc = ""
        if self._vlm is not None and screenshot is not None:
            vlm_desc = await self._vlm(screenshot)
        # MAA 回调无 cost 时,从 vlm_desc 解析 cost
        cost = s.get("cost", 0)
        m = re.search(r"cost[:：]\s*(\d+)", vlm_desc)
        if m:
            cost = int(m.group(1))
        return GameState(
            stage=self._stage,
            cost=cost,
            step=s.get("step", 0),
            operators=ops,
            vlm_desc=vlm_desc,
        )
