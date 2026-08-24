"""SingleStep 最小 demo(mock):验证 MAA 单步喂 action + 回调闭环走得通。

运行:python -m src.game.singlestep_demo
"""

from __future__ import annotations

import asyncio
import logging

from src.core.orchestrator import game_loop
from src.game.maapy_client import create_client


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = create_client(mock=True)
    await game_loop(client, steps=5)
    print("\n[OK] SingleStep 闭环验证通过(mock) — 命门解除见 REVIEW.md 第七节-1")


if __name__ == "__main__":
    asyncio.run(main())
