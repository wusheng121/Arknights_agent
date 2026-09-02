"""自主控制 game_loop:Copilot 作业 + ADB 执行(混合)。

流程:
1. 读 copilot_job.json(opers + actions)
2. MAA Copilot formation 编队(MAA,已验证)
3. ADB 找开始作战按钮 → tap(绕开 MAA BattleStartAll 20000)
4. 轮询等 check_in_battle == True
5. 按 actions 顺序执行(CV 感知 + tile_calc + ADB):
   - 每个 action 前检查在战斗中
   - Deploy: available 为空重试 3 次
   - SpeedUp: 模板匹配找按钮
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys

import cv2
import numpy as np

from src.game.adb_control import AdbController
from src.game.cv_perception import CVPerception, DeploymentOper, _match_template_multi, _load_template
from src.game.tile_calc import TileCalc

log = logging.getLogger(__name__)

MAA_RES = os.getenv("MAA_RESOURCE_PATH", r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\resource")
MAA_ROOT = os.path.dirname(MAA_RES)
ADB_PATH = os.getenv("MAA_ADB_PATH", r"C:\Program Files\Netease\MuMu\nx_main\adb.exe")
ADB_ADDR = os.getenv("MAA_ADDRESS", "127.0.0.1:16384")
TILE_PATH = os.path.join(MAA_RES, "Arknights-Tile-Pos",
                         "main_01-07-obt-main-level_main_01-07.json")
JOB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "copilot_job.json")

# 开始作战按钮模板
_START_TEMPL = None
_SPEED_UP_TEMPL = None


def _init_templates():
    global _START_TEMPL, _SPEED_UP_TEMPL
    if _START_TEMPL is None:
        t = _load_template("BattleStartNormal.png", "StartButton")
        if t is not None:
            _START_TEMPL = cv2.resize(t, (int(t.shape[1] * 1.5), int(t.shape[0] * 1.5)))
    if _SPEED_UP_TEMPL is None:
        t = _load_template("BattleSpeedUp.png", "BattleFlag")
        if t is not None:
            _SPEED_UP_TEMPL = cv2.resize(t, (int(t.shape[1] * 1.5), int(t.shape[0] * 1.5)))


async def game_loop(steps: int | None = None) -> None:
    """Copilot 作业 + ADB 执行主循环。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _init_templates()

    adb = AdbController(ADB_PATH, ADB_ADDR)
    cv = CVPerception(scale=1.5)
    tc = TileCalc(TILE_PATH)

    # 1. 读作业
    if not os.path.exists(JOB_PATH):
        log.error("无 copilot_job.json,先跑 --llm 生成作业")
        return
    with open(JOB_PATH, encoding="utf-8") as f:
        job = json.load(f)
    opers = job.get("opers", [])
    actions = job.get("actions", [])
    log.info("作业: stage=%s opers=%d actions=%d", job.get("stage_name"), len(opers), len(actions))

    if steps is None:
        steps = len(actions)

    # 2. MAA Copilot formation 编队(只编队,不点开始作战)
    log.info("=== 阶段1: MAA Copilot 编队 ===")
    await _copilot_formation(adb, job)

    # 3. ADB 找开始作战按钮 → tap(含快速编队确认)
    log.info("=== 阶段2: ADB 点开始作战 ===")
    await _tap_start_battle(adb, cv)

    # 4. 等进战斗
    log.info("=== 阶段3: 等进战斗 ===")
    entered = await _wait_in_battle(adb, cv, timeout=40)
    if not entered:
        log.error("40 秒未进入战斗,退出")
        return

    # 额外等 5 秒让待部署区完全加载
    log.info("  等 5 秒让待部署区加载...")
    await asyncio.sleep(5)

    # 5. 按 actions 顺序执行
    log.info("=== 阶段4: 执行 actions (ADB 自主控制) ===")
    for i, action in enumerate(actions[:steps]):
        log.info("--- action %d/%d: %s ---", i + 1, len(actions),
                 json.dumps(action, ensure_ascii=False))

        atype = action.get("type", "Deploy")

        if atype == "SpeedUp":
            await _do_speedup(adb, cv)
            continue

        if atype == "SkillDaemon":
            log.info("  SkillDaemon: 等待")
            await asyncio.sleep(5)
            continue

        # 其他 action 前先检查在战斗中
        img = _screencap(adb)
        if img is None or not cv.check_in_battle(img):
            log.warning("  不在战斗中,等 3s")
            await asyncio.sleep(3)
            img = _screencap(adb)
            if img is None or not cv.check_in_battle(img):
                log.error("  仍不在战斗中,跳过")
                continue

        if atype == "Deploy":
            await _do_deploy(adb, cv, tc, action)
        elif atype == "Skill":
            await _do_skill(adb, cv, tc, action)
        elif atype == "Retreat":
            await _do_retreat(adb, cv, tc, action)
        else:
            log.info("  未支持: %s", atype)

    log.info("=== actions 执行完成 ===")


async def _maa_silent_handler(ev) -> None:
    """静默 MAA 回调(不处理)。"""
    pass


async def _copilot_formation(adb: AdbController, job: dict) -> None:
    """MAA Copilot formation 编队(只编队,不开始作战)。"""
    step_job = {
        "stage_name": job.get("stage_name", "1-7"),
        "opers": job.get("opers", []),
        "actions": [],
        "minimum_required": job.get("minimum_required", "v6.7.0"),
    }
    step_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "copilot_step.json"))
    with open(step_path, "w", encoding="utf-8") as f:
        json.dump(step_job, f, ensure_ascii=False)

    try:
        maa_python = os.path.join(MAA_ROOT, "Python")
        if maa_python not in sys.path:
            sys.path.insert(0, maa_python)
        from src.game.maapy_client import MaapyClient
        client = MaapyClient(resource_path=MAA_ROOT)
        client.add_handler(_maa_silent_handler)
        await client.connect(ADB_PATH, ADB_ADDR)
        await client.append("Copilot", {"filename": step_path, "formation": True, "formation_index": 0})
        await client.start()
        await client.wait_done(timeout=120)
        log.info("MAA 编队完成")
    except Exception as e:
        log.error("MAA 编队失败: %s, 请手动编队", e)
        log.warning("等待 15 秒(手动编队时间)")
        await asyncio.sleep(15)


async def _tap_start_battle(adb: AdbController, cv: CVPerception) -> None:
    """ADB 找开始作战按钮 → tap(含快速编队确认)。"""
    for retry in range(8):
        img = _screencap(adb)
        if img is None:
            await asyncio.sleep(1)
            continue

        # 0. 已经在战斗中
        if cv.check_in_battle(img):
            log.info("  已经在战斗中,跳过")
            return

        # 1. 找开始作战按钮 BattleStartNormal
        if _START_TEMPL is not None:
            matches = _match_template_multi(img, _START_TEMPL, 0.8)
            if matches:
                m = matches[0]
                cx = m[0] + m[2] // 2
                cy = m[1] + m[3] // 2
                log.info("  找到开始作战按钮(%d,%d) score=%.3f → tap", cx, cy, m[4])
                adb.tap(cx, cy)
                await asyncio.sleep(3)
                return

        # 2. 可能在快速编队界面, 找 Confirm 按钮
        confirm_templ = _load_template("BattleQuickFormationConfirm.png",
                                       os.path.join("Formation", "BattleQuickFormation"))
        if confirm_templ is not None:
            ts = cv2.resize(confirm_templ,
                (int(confirm_templ.shape[1] * 1.5), int(confirm_templ.shape[0] * 1.5)))
            matches = _match_template_multi(img, ts, 0.8)
            if matches:
                m = matches[0]
                cx = m[0] + m[2] // 2
                cy = m[1] + m[3] // 2
                log.info("  找到快速编队确认按钮(%d,%d) → tap", cx, cy)
                adb.tap(cx, cy)
                await asyncio.sleep(3)
                continue  # 确认后可能还要点开始作战

        log.info("  未找到按钮,等 2s 重试(%d/8)", retry + 1)
        await asyncio.sleep(2)

    log.warning("  8 次未找到按钮,请手动操作")


async def _wait_in_battle(adb: AdbController, cv: CVPerception, timeout: int = 30) -> bool:
    """轮询等进战斗。"""
    for i in range(timeout // 2):
        img = _screencap(adb)
        if img is not None and cv.check_in_battle(img):
            log.info("  进战斗成功(等了 %d 秒)", i * 2)
            # 额外等 3 秒让待部署区加载
            await asyncio.sleep(3)
            return True
        await asyncio.sleep(2)
    return False


async def _do_speedup(adb: AdbController, cv: CVPerception) -> None:
    """SpeedUp: 模板匹配找二倍速按钮 → tap。"""
    img = _screencap(adb)
    if img is None:
        log.warning("  SpeedUp 截图失败")
        return

    if _SPEED_UP_TEMPL is not None:
        matches = _match_template_multi(img, _SPEED_UP_TEMPL, 0.6)
        if matches:
            m = matches[0]
            cx = m[0] + m[2] // 2
            cy = m[1] + m[3] // 2
            log.info("  SpeedUp tap(%d,%d) score=%.3f", cx, cy, m[4])
            adb.tap(cx, cy)
            await asyncio.sleep(1)
            return

    # fallback 硬编码
    log.info("  SpeedUp fallback tap(1700,80)")
    adb.tap(1700, 80)
    await asyncio.sleep(1)


async def _do_deploy(adb: AdbController, cv: CVPerception, tc: TileCalc,
                     action: dict) -> None:
    """Deploy:CV 找可用干员 → tile_calc 算格子 → ADB 拖拽(重试 3 次)。"""
    name = action.get("name", "")
    location = action.get("location", [])
    direction = action.get("direction", "None")

    if not location or len(location) < 2:
        log.warning("  Deploy 缺少 location")
        return

    col, row = int(location[0]), int(location[1])
    target_x, target_y = tc.get_tile_screen_pos(row, col)
    log.info("  Deploy %s → 格子(%d,%d) 屏幕(%d,%d) dir=%s",
             name, col, row, target_x, target_y, direction)

    # 重试 3 次找可用干员
    for retry in range(3):
        img = _screencap(adb)
        if img is None:
            await asyncio.sleep(2)
            continue

        opers = cv.update_deployment(img, need_cost=True)
        available = [op for op in opers if op.available and not op.cooling]
        log.info("  CV 识别(%d/3): 可用干员(%d): %s",
                 retry + 1, len(available),
                 [(op.role, op.cost) for op in available])

        if available:
            # 按 cost 低的优先
            available.sort(key=lambda o: o.cost if o.cost > 0 else 999)
            oper = available[0]
            oper_x = oper.rect[0] + oper.rect[2] // 2
            oper_y = oper.rect[1] + oper.rect[3] // 2
            log.info("  选中: [%d] %s cost=%d 头像(%d,%d)",
                     oper.index, oper.role, oper.cost, oper_x, oper_y)

            # ADB 拖拽
            dist = abs(target_x - oper_x) + abs(target_y - oper_y)
            duration = max(300, min(800, dist // 3))
            log.info("  swipe (%d,%d)→(%d,%d) %dms", oper_x, oper_y, target_x, target_y, duration)
            adb.swipe(oper_x, oper_y, target_x, target_y, duration)
            await asyncio.sleep(1)

            # 方向
            if direction and direction != "None":
                dir_map = {
                    "Right": (target_x + 100, target_y),
                    "Left": (target_x - 100, target_y),
                    "Up": (target_x, target_y - 100),
                    "Down": (target_x, target_y + 100),
                }
                end = dir_map.get(direction)
                if end:
                    log.info("  方向 %s → swipe(%d,%d)", direction, end[0], end[1])
                    adb.swipe(target_x, target_y, end[0], end[1], 200)
                    await asyncio.sleep(0.5)
            return

        # 没有可用干员,等 2s 重试
        log.info("  没有可用干员,等 2s 重试")
        await asyncio.sleep(2)

    log.warning("  3 次重试无可用干员,跳过 %s", name)


async def _do_skill(adb: AdbController, cv: CVPerception, tc: TileCalc,
                    action: dict) -> None:
    """Skill:tile_calc 算格子 → tap 选中 → tap 技能按钮。"""
    name = action.get("name", "")
    location = action.get("location", [])

    if location and len(location) >= 2:
        col, row = int(location[0]), int(location[1])
        x, y = tc.get_tile_screen_pos(row, col)
        log.info("  Skill %s → tap(%d,%d)", name, x, y)
        adb.tap(x, y)
        await asyncio.sleep(0.5)
        sx, sy = tc.get_skill_screen_pos()
        log.info("  技能按钮 → tap(%d,%d)", sx, sy)
        adb.tap(sx, sy)
        await asyncio.sleep(0.5)
    elif name:
        # 按 CV 找场上干员
        img = _screencap(adb)
        if img is not None:
            opers = cv.update_deployment(img)
            log.info("  Skill by name %s (场上干员 %d 个)", name, len(opers))
        log.warning("  Skill 缺少 location,按 name 找干员暂未实现")


async def _do_retreat(adb: AdbController, cv: CVPerception, tc: TileCalc,
                      action: dict) -> None:
    """Retreat:tile_calc 算格子 → tap 选中 → tap 撤退按钮。"""
    location = action.get("location", [])
    if location and len(location) >= 2:
        col, row = int(location[0]), int(location[1])
        x, y = tc.get_tile_screen_pos(row, col)
        log.info("  Retreat → tap(%d,%d)", x, y)
        adb.tap(x, y)
        await asyncio.sleep(0.5)
        rx, ry = tc.get_retreat_screen_pos()
        log.info("  撤退按钮 → tap(%d,%d)", rx, ry)
        adb.tap(rx, ry)
        await asyncio.sleep(0.5)


def _screencap(adb: AdbController) -> np.ndarray | None:
    """adb 截图 → numpy array (BGR)。"""
    try:
        r = subprocess.run(
            [adb.adb, "-s", adb.addr, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0 or not r.stdout:
            return None
        arr = np.frombuffer(r.stdout, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


if __name__ == "__main__":
    asyncio.run(game_loop())
