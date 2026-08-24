"""编排调度:聊天 / 玩法优先级、抢占、排队(REVIEW.md 第四节-4)。

优先级(next 返回顺序):
1. decision(游戏暂停且有待决策)— 决策是节目核心,玩家会等
2. 被点名弹幕(@主播)— 打断,互动优先
3. 普通弹幕 — 间隙处理,不抢占决策
4. narrate(思考旁白)— 把决策等待变成节目效果
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TaskKind = Literal["chat", "decision", "narrate"]


@dataclass
class ChatMsg:
    text: str
    user: str
    mentioned: bool = False
    ts: float = 0.0


@dataclass
class Task:
    kind: TaskKind
    payload: Any = None
    priority: int = 0


class Scheduler:
    def __init__(self) -> None:
        self._chat_q: list[ChatMsg] = []
        self._game_paused = False
        self._pending_decision = False
        self._thinking = False

    def push_chat(self, msg: ChatMsg) -> None:
        self._chat_q.append(msg)

    def request_decision(self) -> None:
        self._pending_decision = True

    def pause_game(self) -> None:
        self._game_paused = True

    def resume_game(self) -> None:
        self._game_paused = False

    def begin_thinking(self) -> None:
        self._thinking = True

    def end_thinking(self) -> None:
        self._thinking = False
        self._pending_decision = False

    @property
    def thinking(self) -> bool:
        return self._thinking

    def next(self) -> Task | None:
        # 1) 决策(游戏暂停 + 有待决策)— 最高优先
        if self._game_paused and self._pending_decision:
            self._pending_decision = False
            self.begin_thinking()
            return Task("decision", None, 100)
        # 2) 被点名弹幕:打断
        mentioned = [m for m in self._chat_q if m.mentioned]
        if mentioned:
            m = mentioned[0]
            self._chat_q.remove(m)
            return Task("chat", m, 50)
        # 3) 普通弹幕:仅在非思考间隙
        if self._chat_q and not self._thinking:
            return Task("chat", self._chat_q.pop(0), 10)
        # 4) 思考旁白
        if self._thinking:
            return Task("narrate", None, 5)
        return None
