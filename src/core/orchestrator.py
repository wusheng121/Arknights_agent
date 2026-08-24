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


def _make_brain() -> GuardedCall:
    # 真实接入见 src/brain/llm_client.py(DeepSeek V4 + thinking);无 key 自动降级
    return make_brain()


def _make_vlm() -> GuardedCall:
    # 真实接入见 src/game/vlm_client.py(云 VLM,openai 兼容);无 key 自动降级
    return make_vlm()


async def game_loop(client: MockMaapyClient | None = None, steps: int = 5) -> None:
    import json as _json
    import re
    client = client or create_client(mock=True)
    perc = Perception(vlm=_make_vlm())
    brain = _make_brain()

    # 读 copilot_job.json 的 opers 作为编队
    job_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "copilot_job.json"))
    operators: list = []
    step_path = None
    if os.path.exists(job_path):
        with open(job_path, encoding="utf-8") as f:
            job = _json.load(f)
        operators = [{"name": o.get("name"), "rarity": 6, "elite": 2, "level": 90} for o in job.get("opers", [])]
        step_job = {"stage_name": "1-7", "opers": job.get("opers", []),
                    "actions": [{"type": "SpeedUp"}], "minimum_required": "v6.7.0"}
        step_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "copilot_step.json"))
        with open(step_path, "w", encoding="utf-8") as f:
            _json.dump(step_job, f, ensure_ascii=False)
        log.info("载入编队 %d 干员: %s", len(operators), [o["name"] for o in operators])
    else:
        log.error("无 copilot_job.json,先跑 --llm 生成作业")
        return

    from src.game.adb_control import AdbController
    adb = AdbController(os.getenv("MAA_ADB_PATH", ""), os.getenv("MAA_ADDRESS", ""))
    shot_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "shot.png"))

    client.add_handler(perc.update_from_maapy)

    # Copilot formation 自动编队 + 开始作战 + SpeedUp(进战斗)
    await client.append("Copilot", {"filename": step_path, "formation": True, "formation_index": 0})
    await client.start()
    log.info("Copilot 编队+开始作战中...")
    await client.wait_done(timeout=90)
    log.info("进战斗,等渲染...")
    await asyncio.sleep(5)
    log.info("切 ADB 实时 Deploy 循环")

    for i in range(steps):
        adb.screencap(shot_path)
        with open(shot_path, "rb") as f:
            shot = f.read()
        state = await perc.snapshot(shot)
        log.info("step=%d cost=%d vlm=%s", state.step, state.cost, (state.vlm_desc or "-")[:120])
        action = await brain(state, operators)
        log.info("  action: %s %s loc=%s", action.type, action.name or "", action.location)
        if action.type == "Deploy" and action.name:
            # 从 vlm_desc 解析干员头像坐标 + 建议格子坐标
            m = re.search(r"%s\((\d+)[,，](\d+)\)" % re.escape(action.name), state.vlm_desc or "")
            gm = re.search(r"(?:格子|Deploy)[^0-9]{0,6}(\d+)[,，](\d+)", state.vlm_desc or "")
            if m and gm:
                oper_pos = (int(m.group(1)), int(m.group(2)))
                tile_pos = (int(gm.group(1)), int(gm.group(2)))
                log.info("  adb deploy %s %s->%s", action.name, oper_pos, tile_pos)
                adb.deploy(oper_pos, tile_pos)
                await asyncio.sleep(2)
            else:
                log.warning("  VLM 坐标解析失败,跳过")

    log.info("ADB 实时循环完成(%d steps)", steps)


def handle_task(t: Task | None) -> None:
    if t is None:
        return
    if t.kind == "chat":
        log.info("  [chat@%d] %s: %s", t.priority, t.payload.user, t.payload.text)
    elif t.kind == "narrate":
        log.info("  [narrate] 思考中…")
