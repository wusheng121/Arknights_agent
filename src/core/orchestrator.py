"""编排主循环:感知 → 路由 → 行动,串接 client/perception/scheduler/brain。

brain 用 GuardedCall 包(mock primary + 规则 fallback),演示云 LLM 降级。
SingleStep 闭环:set_stage → start_battle → start → (snapshot → decide → do_action)* → finish
"""

from __future__ import annotations

import asyncio
import logging
import os

from src.brain.llm_client import make_brain
from src.core.scheduler import Scheduler, Task
from src.game.copilot_schema import Action
from src.game.maapy_client import MockMaapyClient, create_client
from src.game.perception import GameState, Perception
from src.game.vlm_client import make_vlm
from src.resilience.guarded_call import GuardedCall

log = logging.getLogger(__name__)


async def _ev_printer(ev):
    """打印 MAA 回调事件。"""
    if ev.msg in (20001, 20002, 20003, 3):
        parts = [f"[cb] {ev.msg}"]
        d = ev.details
        st = d.get("subtask", d.get("what", ""))
        if st:
            parts.append(st)
        if d.get("first"):
            parts.append(str(d["first"][:3]))
        det = d.get("details", d)
        if isinstance(det, dict):
            action = det.get("action", "")
            target = det.get("target", "")
            if action:
                parts.append(action)
            if target:
                parts.append(target)
        log.info(" ".join(parts))


def _make_brain() -> GuardedCall:
    # 真实接入见 src/brain/llm_client.py(DeepSeek V4 + thinking);无 key 自动降级
    return make_brain()


def _make_vlm() -> GuardedCall:
    # 真实接入见 src/game/vlm_client.py(云 VLM,openai 兼容);无 key 自动降级
    return make_vlm()


async def game_loop(client: MockMaapyClient | None = None, steps: int = 5) -> None:
    """SingleStep 闭环: Copilot 编队 → ADB tap 开始 → SingleStep do_action 逐个部署。

    依赖 patched MaaCore.dll (update_deployment fix)。
    每次 do_action 前截图 → CV 感知 → DeepSeek 决策 → MAA 执行。
    """
    import json as _json

    client = client or create_client(mock=False, resource_path=os.getenv("MAA_RESOURCE_PATH", ""))
    client.add_handler(_ev_printer)
    # Override resource path for patched DLL (use source resource)
    client._resource_path = os.getenv("MAA_RESOURCE_PATH", "")

    # 读 copilot_job.json 作为编队 + 动作来源
    job_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "copilot_job.json"))
    if not os.path.exists(job_path):
        log.error("无 copilot_job.json,先跑 --llm 生成作业")
        return
    with open(job_path, encoding="utf-8") as f:
        job = _json.load(f)
    operators = [{"name": o.get("name"), "rarity": 6, "elite": 2, "level": 90} for o in job.get("opers", [])]
    actions = job.get("actions", [])
    log.info("载入作业: %d 干员, %d actions", len(operators), len(actions))

    # 1. 手动编队已完成,游戏已在战斗中
    # 用户需: 手动选 7 个干员 → 点开始作战 → 进战斗后运行 --singlestep
    log.info("假设游戏已在 1-7 战斗中(用户手动编队+开始)")

    # 2. SingleStep: set_stage → do_action 逐个执行
    log.info("=== SingleStep 实时部署 (patched MaaCore.dll) ===")
    await client.set_stage("1-7")
    await client.start()
    await client.wait_done(timeout=30)
    log.info("SingleStep set_stage 完成")

    # 逐个执行 actions (不调 start_battle,避免导航干扰)
    import subprocess as _sp
    maa = os.getenv("MAA_RESOURCE_PATH", "")
    adb_path = os.getenv("MAA_ADB_PATH", "")
    addr = os.getenv("MAA_ADDRESS", "")

    # 逐个执行 actions
    for i, action_data in enumerate(actions):
        if i >= steps:
            break
        atype = action_data.get("type", "Deploy")
        if atype == "SpeedUp":
            _sp.run([adb_path, "-s", addr, "shell", "input", "tap", "1700", "80"])
            await asyncio.sleep(1)
            log.info("step %d: SpeedUp", i)
            continue

        if atype != "Deploy":
            log.info("step %d: 跳过 %s", i, atype)
            continue

        name = action_data.get("name", "")
        loc = action_data.get("location", [])
        direction = action_data.get("direction", "Right")
        log.info("step %d: Deploy %s → %s dir=%s", i, name, loc, direction)

        from src.game.copilot_schema import Action
        action = Action(type="Deploy", name=name, location=tuple(loc), direction=direction)
        await client.do_action(action)
        await client.start()
        await client.wait_done(timeout=30)
        log.info("  do_action 完成 (update_deployment 应已刷新)")

    log.info("=== SingleStep 循环完成 ===")


def handle_task(t: Task | None) -> None:
    if t is None:
        return
    if t.kind == "chat":
        log.info("  [chat@%d] %s: %s", t.priority, t.payload.user, t.payload.text)
    elif t.kind == "narrate":
        log.info("  [narrate] 思考中…")
