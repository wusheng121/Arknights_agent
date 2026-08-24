"""三路云调用统一降级 / 熔断框架(REVIEW.md 第八节-4 落地)。

每路云调用(LLM / TTS / VLM)封装为 ``GuardedCall``:
超时 + 重试 + 降级 fallback + 熔断。fallback 必须永不抛错(本地 / 规则逻辑)。

熔断状态机:
    OK  --连续失败达阈值-->  DOWN(冷却期内直接走 fallback)
    DOWN --冷却到期-->  DEGRADED(半开,试探一次 primary)
    DEGRADED --primary 成功-->  OK  --primary 失败-->  DOWN
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable


class Level(Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


@dataclass
class Circuit:
    fails: int = 0
    opened_at: float | None = None
    level: Level = Level.OK


class GuardedCall:
    def __init__(
        self,
        name: str,
        primary: Callable[..., Awaitable[Any]],
        fallback: Callable[..., Awaitable[Any]],
        *,
        timeout: float = 8.0,
        retries: int = 2,
        fail_threshold: int = 3,
        cool: float = 60.0,
        now: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.name = name
        self.primary = primary
        self.fallback = fallback
        self.timeout = timeout
        self.retries = retries
        self.fail_threshold = fail_threshold
        self.cool = cool
        self.now = now or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self.cb = Circuit()

    async def __call__(self, *a: Any, **k: Any) -> Any:
        # 1) 熔断冷却期内:直接 fallback,不再请求主路径
        if (
            self.cb.level == Level.DOWN
            and self.cb.opened_at is not None
            and self.now() - self.cb.opened_at < self.cool
        ):
            return await self.fallback(*a, **k)
        # 2) 冷却到期:半开放行,试探一次主路径
        if self.cb.level == Level.DOWN:
            self.cb.level = Level.DEGRADED
        # 3) 主路径重试(超时计入失败)
        for attempt in range(self.retries + 1):
            try:
                r = await asyncio.wait_for(self.primary(*a, **k), self.timeout)
                self._on_ok()
                return r
            except Exception:
                if attempt < self.retries:
                    await self._sleep(0.3 * (attempt + 1))
                    continue
        # 4) 主路径耗尽:走 fallback 并累计失败 / 触发熔断
        self._on_fail()
        return await self.fallback(*a, **k)

    def _on_ok(self) -> None:
        self.cb = Circuit()

    def _on_fail(self) -> None:
        self.cb.fails += 1
        if self.cb.fails >= self.fail_threshold:
            self.cb.level = Level.DOWN
            self.cb.opened_at = self.now()
        else:
            self.cb.level = Level.DEGRADED
