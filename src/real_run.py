r"""真实接入 smoke:连 MuMu + MAA,跑 StartUp 唤醒,验证真实链路。

需游戏在主菜单附近(StartUp 会打开/回到游戏主界面)。
MAA 路径 / adb / address 默认填好,可用环境变量覆盖。

运行:
  $env:PYTHONPATH = "C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\Python"
  $env:PYTHONIOENCODING="utf-8"
  python -m src.real_run            # StartUp 唤醒
  python -m src.real_run --copilot  # SingleStep 打 1-7(需游戏在 1-7 编队准备界面)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
load_dotenv()

MAA = os.getenv("MAA_RESOURCE_PATH", r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64")
sys.path.insert(0, os.path.join(MAA, "Python"))

from src.brain.llm_client import make_copilot_brain
from src.core.orchestrator import game_loop
from src.game.maapy_client import MSG_SUB_TASK_EXTRA_INFO, MaapyClient
ADB = os.getenv("MAA_ADB_PATH", r"C:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe")
ADDR = os.getenv("MAA_ADDRESS", "127.0.0.1:16384")

log = logging.getLogger(__name__)


async def _verify_and_fallback_deploy(adb, addr, maa_path, job_data, tile_calc, cv2, np, sp):
    """部署验证: 检查干员是否在场上,失败则 ADB 手动部署。

    Returns: True=部署成功/已在场上, False=部署失败
    """
    # 1. 截图检查是否还在战斗中
    try:
        r = sp.run([adb, "-s", addr, "exec-out", "screencap", "-p"], capture_output=True, timeout=10)
        arr = np.frombuffer(r.stdout, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            log.warning("部署验证: 截图失败")
            return True  # 无法验证,假设成功
    except Exception:
        return True

    # 2. 检查是否在战斗中 (HP flag)
    hp_path = os.path.join(maa_path, "resource", "template", "Battle", "BattleFlag", "BattleHpFlag.png")
    hp_templ = cv2.imread(hp_path)
    if hp_templ is None:
        return True
    hp_ts = cv2.resize(hp_templ, (int(hp_templ.shape[1]*1.5), int(hp_templ.shape[0]*1.5)))
    hp_res = cv2.matchTemplate(img, hp_ts, cv2.TM_CCOEFF_NORMED)
    _, hp_mv, _, _ = cv2.minMaxLoc(hp_res)
    in_battle = hp_mv > 0.6

    if not in_battle:
        log.info("部署验证: 已不在战斗中(HP=%.2f),无需验证", hp_mv)
        return True

    # 3. 在战斗中 → 检查部署面板是否有可部署干员
    flag_path = os.path.join(maa_path, "resource", "template", "Battle", "BattleFlag", "BattleOpersFlag.png")
    flag_templ = cv2.imread(flag_path)
    if flag_templ is None:
        return True
    flag_ts = cv2.resize(flag_templ, (int(flag_templ.shape[1]*1.5), int(flag_templ.shape[0]*1.5)))
    flag_res = cv2.matchTemplate(img, flag_ts, cv2.TM_CCOEFF_NORMED)
    _, flag_mv, _, _ = cv2.minMaxLoc(flag_res)

    # 如果部署面板 score < 0.5,可能已经在战斗深处(面板消失),干员应该已部署
    if flag_mv < 0.5:
        log.info("部署验证: 部署面板不可见(%.2f),干员可能已部署", flag_mv)
        return True

    # 4. 部署面板可见 → 检查是否有干员待部署 (可用=未部署)
    from src.game.cv_perception import CVPerception
    cv = CVPerception(scale=1.5)
    opers = cv.update_deployment(img)
    available_opers = [o for o in opers if o.available and not o.cooling]

    if not available_opers:
        log.info("部署验证: 无可用干员(可能已全部署或冷却中)")
        return True

    # 5. 有可用干员 → 可能部署失败 → ADB 手动部署
    log.warning("部署验证: 发现 %d 个可用干员,可能部署失败! 尝试 ADB 手动部署", len(available_opers))

    if not tile_calc:
        log.warning("无 tile_calc,无法手动部署")
        return False

    # 从 job 获取部署信息
    for action in job_data.get("actions", []):
        if action.get("type") != "Deploy" or not action.get("name"):
            continue

        loc = action.get("location", [2, 3])
        direction = action.get("direction", "Left")
        name = action.get("name")

        # 获取目标位置屏幕坐标
        tx, ty = tile_calc.get_tile_screen_pos(loc[1], loc[0])

        # 找第一个可用干员的屏幕位置
        if available_opers:
            oper = available_opers[0]
            ox = oper.rect[0] + oper.rect[2] // 2
            oy = oper.rect[1] + oper.rect[3] // 2

            log.info("ADB 手动部署 %s: (%d,%d) → (%d,%d) 方向=%s",
                     name, ox, oy, tx, ty, direction)

            # 拖拽部署
            sp.run([adb, "-s", addr, "shell", "input", "swipe",
                    str(ox), str(oy), str(tx), str(ty), "500"])
            import asyncio
            await asyncio.sleep(2)

            # 方向选择: 部署后出现方向箭头,点对应方向
            # 方向按钮在干员周围,偏移约 100-150px
            dir_offset = {"Left": (-120, 0), "Right": (120, 0), "Up": (0, -120), "Down": (0, 120)}
            dx, dy = dir_offset.get(direction, (0, 0))
            dir_x = tx + dx
            dir_y = ty + dy
            log.info("ADB 方向选择 %s: (%d,%d)", direction, dir_x, dir_y)
            sp.run([adb, "-s", addr, "shell", "input", "tap", str(dir_x), str(dir_y)])
            await asyncio.sleep(1)

            log.info("手动部署完成")
            return True

    return False


async def _ev_printer(ev):
    import json
    m = ev.msg
    name = {3: "AllTasksCompleted", 10001: "ChainStart", 10002: "ChainCompleted",
            20001: "SubTaskStart", 20002: "SubTaskCompleted", 20003: "SubTaskExtraInfo"}.get(m, str(m))
    what = ev.details.get("what", "")
    if what == "OperBoxInfo":
        inner = ev.details.get("details") or {}
        log.info("[cb] %s OperBoxInfo done=%s", name, inner.get("done"))
        return
    extra = ""
    if m == 20000 or what:
        extra = json.dumps(ev.details, ensure_ascii=False)[:300]
    log.info("[cb] %s %s %s", name, what, extra)


async def smoke_start() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("MAA=%s ADB=%s ADDR=%s", MAA, ADB, ADDR)
    client = MaapyClient(resource_path=MAA)
    client.add_handler(_ev_printer)
    ok = await client.connect(ADB, ADDR)
    log.info("connect = %s", ok)
    if not ok:
        return
    await client.append("StartUp", {"client_type": "Official", "enable": True})
    await client.start()
    await client.wait_done(timeout=180)
    log.info("StartUp 完成")


async def smoke_copilot(steps: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("MAA=%s ADB=%s ADDR=%s", MAA, ADB, ADDR)
    from src.game.copilot_schema import Action
    client = MaapyClient(resource_path=MAA)
    client.add_handler(_ev_printer)
    ok = await client.connect(ADB, ADDR)
    if not ok:
        log.error("connect 失败")
        return
    log.info("SingleStep: set_stage 1-7")
    await client.set_stage("1-7")
    log.info("SingleStep: start_battle")
    await client.start_battle()
    await client.start()
    for i in range(steps):
        await asyncio.sleep(5)
        act = Action(type="SpeedUp", doc=f"step{i+1} 切换倍速(验证单步喂入)")
        log.info("SingleStep: do_action %s", act.type)
        await client.do_action(act)
    await asyncio.sleep(3)
    client.stop()
    log.info("SingleStep 真实验证完成")


async def smoke_copilot_doc(stage: str = "1-7", fresh: bool = False) -> None:
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("MAA=%s ADB=%s ADDR=%s stage=%s", MAA, ADB, ADDR, stage)
    if not os.getenv("DEEPSEEK_API_KEY", ""):
        log.error("DEEPSEEK_API_KEY 未填,请在 .env 填写")
        return
    ops_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "operators.json"))
    if not os.path.exists(ops_path):
        log.error("无 operators.json,先跑 --operbox")
        return
    with open(ops_path, encoding="utf-8") as f:
        operators = json.load(f)
    log.info("可用干员 %d 个", len(operators))

    from src.data.stage_util import stage_code_to_id, get_tile_json_path, get_level_json_path, get_cache_path, ensure_level_json, list_available_stages
    sid = stage_code_to_id(stage, MAA)

    # MAA copilot 的 stage_name: 优先用 stageId (MAA 内部用 stageId 找 tile 数据)
    stage_name_for_maa = sid

    # 检查 tile 数据是否存在
    tile_path = get_tile_json_path(MAA, stage)
    if not os.path.exists(tile_path):
        available = list_available_stages(MAA)
        log.error("关卡 %s 的 tile 数据不存在! 可用关卡: %s", stage, available[:20])
        return

    # 检查作业缓存(有缓存直接用,跳过 LLM)
    # 注意: --fresh 跳过缓存执行,但 RAG 仍可读取已缓存作业作为专家参考
    _cache_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "job_cache"))
    _cache_path = get_cache_path(_cache_dir, stage, MAA)
    if not fresh and os.path.exists(_cache_path):
        with open(_cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("actions"):
            log.info("=== 使用缓存作业: %s ===", _cache_path)
            job_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "copilot_job.json"))
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(cached, f, ensure_ascii=False, indent=2)
            job_data = cached
            await _run_maa_copilot(job_path, job_data)
            return

    # 加载地图信息
    from src.data.map_info import parse_tile_json
    from src.data.oper_database import OperDatabase as _ODB
    tile_path = get_tile_json_path(MAA, stage)
    mi = None
    map_info = ""
    if os.path.exists(tile_path):
        mi = parse_tile_json(tile_path)
        map_info = mi.to_description()
        if mi.to_tactical_description():
            map_info += "\n" + mi.to_tactical_description()
        log.info("地图信息:\n%s", map_info)
    else:
        log.warning("未找到 tile 数据: %s", tile_path)
    _db = _ODB()
    _db.load_from_maa()
    _db.load_cost_from_file("cost.json")

    # 加载出怪波次 + 敌人属性
    from src.data.wave_parser import parse_level_json
    from src.data.enemy_lookup import to_compact_description as _enemy_desc
    gamedata = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "gamedata"))
    level_path = get_level_json_path(gamedata, stage, MAA)
    handbook_path = os.path.join(gamedata, "excel", "enemy_handbook_table.json")
    enemy_db_path = os.path.join(gamedata, "levels", "enemydata", "enemy_database.json")
    wave_desc = ""
    enemy_ids = []
    # 自动下载缺失的 level JSON
    if not os.path.exists(level_path):
        log.info("level JSON 不存在,自动下载...")
        level_path = ensure_level_json(gamedata, stage, MAA) or level_path
    if os.path.exists(level_path):
        tl = parse_level_json(level_path, handbook_path, enemy_db_path)
        wave_desc = tl.to_description()
        enemy_ids = list(set(a.enemy_id for a in tl.actions))
        # 更新初始费用
        if mi and tl.initial_cost:
            mi.initial_cost = tl.initial_cost
        if mi and tl.runes_desc:
            mi.runes = [{"desc": tl.runes_desc}]
        if mi and tl.route_waypoints:
            mi.route_waypoints = tl.route_waypoints
        # 路径描述
        paths_desc = tl.paths_desc
        log.info("敌人路径:\n%s", paths_desc[:300] if paths_desc else "无")
        log.info("出怪波次:\n%s", wave_desc)
    else:
        log.warning("未找到 level JSON: %s", level_path)

    # 加载敌人属性
    enemy_stats_desc = ""
    if enemy_ids:
        enemy_stats_desc = _enemy_desc(enemy_ids, enemy_db_path, handbook_path)
        log.info("敌人属性: %s", enemy_stats_desc)

    # 加载干员完整特性
    from src.data.oper_profile import get_profiles_batch
    top_names = [o["name"] for o in sorted(
        operators,
        key=lambda o: (o.get("elite") or 0, o.get("level") or 0, o.get("rarity") or 0),
        reverse=True
    )[:40]]
    oper_profiles = get_profiles_batch(top_names)
    log.info("干员特性: %d 个", len(oper_profiles.split("\n")))

    # 加载策略知识库 + 因果原则
    strategy_knowledge = ""
    pattern_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "patterns", "strategy_knowledge.txt"))
    if os.path.exists(pattern_path):
        with open(pattern_path, encoding="utf-8") as f:
            strategy_knowledge = f.read()
        log.info("策略知识库: %d chars", len(strategy_knowledge))
    principles = ""
    principles_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "patterns", "principles.json"))
    if os.path.exists(principles_path):
        with open(principles_path, encoding="utf-8") as f:
            principles_data = json.load(f)
        # 转为紧凑文本
        lines = []
        for p in principles_data.get("principles", []):
            lines.append("[%s] %s (confidence:%s)" % (p.get("id",""), p.get("pattern",""), p.get("confidence","?")))
            if p.get("condition"):
                lines.append("  condition: %s" % p["condition"])
            if p.get("reason"):
                lines.append("  reason: %s" % p["reason"])
        principles = "\n".join(lines)
        log.info("因果原则: %d 条, %d chars", len(principles_data.get("principles", [])), len(principles))

    # 加载自反思记忆
    from src.sim.memory import MemoryStore
    memory_store = MemoryStore()
    memory_text = memory_store.get_lessons_for_prompt(stage_name_for_maa)
    if memory_text:
        log.info("自反思记忆:\n%s", memory_text[:300])

    # RAG 检索: wiki 攻略 + 专家作业
    from src.data.rag_retriever import retrieve_context
    from src.data.rag_jobs import search_expert_jobs_by_stage
    expert_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "expert_jobs"))
    rag_context = retrieve_context(stage, stage_name_for_maa, MAA,
                                   os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "job_cache")),
                                   expert_dir)
    if rag_context:
        log.info("RAG 检索结果:\n%s", rag_context[:300])
    else:
        log.info("RAG: 无检索结果(新关卡,走通用推理)")

    # 检查专家作业: 按练度筛选 + 选动作最少的
    user_oper_map = {o.get("name", ""): o for o in operators}
    expert_jobs = search_expert_jobs_by_stage(stage_name_for_maa, expert_dir)
    valid_experts = []  # 满足练度要求的作业
    for ej in expert_jobs:
        ej_names = set(o.get("name", "") for o in ej.opers)
        if not ej_names:
            continue
        # 检查干员匹配 + 练度
        all_ok = True
        missing = []
        for eo in ej.opers:
            name = eo.get("name", "")
            user_op = user_oper_map.get(name)
            if not user_op:
                missing.append(name)
                all_ok = False
                break
            # 检查 requirements (从专家作业原始文件读)
            # ej.file_path 指向原始 JSON
            req = eo.get("requirements", {})
            if req:
                user_elite = user_op.get("elite", 0) or 0
                user_level = user_op.get("level", 0) or 0
                req_elite = req.get("elite", 0)
                req_level = req.get("level", 0)
                if user_elite < req_elite or user_level < req_level:
                    all_ok = False
                    log.info("  %s: %s 练度不足 (elite %d<%d / level %d<%d)" % (
                        os.path.basename(ej.file_path), name,
                        user_elite, req_elite, user_level, req_level))
                    break
        if all_ok and not missing:
            valid_experts.append(ej)
            log.info("  %s: 匹配 OK (opers=%s actions=%d)" % (
                os.path.basename(ej.file_path), list(ej_names), len(ej.actions)))

    # 从满足练度的作业中选动作最少的
    best_expert = None
    if valid_experts:
        valid_experts.sort(key=lambda j: len(j.actions))
        best_expert = valid_experts[0]
        best_match = 1.0
        log.info("=== 使用专家作业(练度满足,动作最少): %s ===" % best_expert.file_path)
    elif expert_jobs:
        # 有专家作业但不满足练度 → 用 LLM 适配
        best_match = 0.5
        best_expert = None
        log.info("专家作业存在但练度不满足,走 LLM 适配")
    else:
        best_match = 0
        best_expert = None
    if best_expert and best_match >= 1.0:
        # 有满足练度的专家作业 → 直接使用,跳过 LLM 和后处理
        log.info("=== 使用专家作业(练度满足): %s ===" % best_expert.file_path)
        with open(best_expert.file_path, encoding="utf-8") as f:
            expert_json = json.load(f)
        job_data = expert_json
        job_data["stage_name"] = stage_name_for_maa
        # 注意: groups 不解析,让 MAA 自己从组里选能识别的干员
        # (MAA 可能无法识别较新干员,保留组让 MAA 回退到其他候选)
        job_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "copilot_job.json"))
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job_data, f, ensure_ascii=False, indent=2)
        log.info("专家作业直接使用: opers=%d actions=%d (跳过后处理)" % (
            len(job_data.get("opers", [])), len(job_data.get("actions", []))))
    elif best_expert and best_match > 0:
        # 部分匹配 → 告诉 LLM 哪些干员需要替代
        log.info("专家作业匹配度 %.0f%%,需要 LLM 适配替代干员" % (best_match * 100))
        doc = await generate_job_pipeline(
            operators, stage_name_for_maa, map_info, wave_desc, enemy_stats_desc, oper_profiles, paths_desc,
            mi.blue_doors if mi else None,
            rag_context=rag_context,
            strategy_knowledge=strategy_knowledge,
            principles=principles,
        )
        job_data = doc.to_maa()
        job_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "copilot_job.json"))
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job_data, f, ensure_ascii=False, indent=2)
        log.info("LLM 适配生成: opers=%d actions=%d (跳过后处理)" % (len(doc.opers), len(doc.actions)))
    else:
        # 无专家作业 → 走完整管道 + 后处理 + sim 验证
        log.info("无匹配专家作业,走 LLM 管道 + 后处理 + sim 验证")
        from src.brain.pipeline import generate_job_pipeline
        doc = await generate_job_pipeline(
            operators, stage_name_for_maa, map_info, wave_desc, enemy_stats_desc, oper_profiles, paths_desc,
            mi.blue_doors if mi else None,
            rag_context=rag_context,
            strategy_knowledge=strategy_knowledge,
            principles=principles,
        )
        job_data = doc.to_maa()
        job_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "copilot_job.json"))
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job_data, f, ensure_ascii=False, indent=2)

        # 后处理(仅无专家作业时)
        from src.data.job_post_process import post_process_job
        try:
            if mi is not None:
                job_data = post_process_job(job_data, mi, _db, has_expert=False)
                with open(job_path, "w", encoding="utf-8") as f:
                    json.dump(job_data, f, ensure_ascii=False, indent=2)
                log.info("后处理完成: actions=%d" % len(job_data.get("actions", [])))
        except Exception as e:
            log.error("后处理失败: %s", e)

        # sim 验证 + LLM 修正 + 记忆记录
        try:
            from src.sim.validate import validate_and_fix
            from src.sim.memory import MemoryStore, classify_failure
            from src.sim.game_state import run_job as sim_run_job
            from openai import AsyncOpenAI
            sim_client = AsyncOpenAI(
                api_key=os.getenv("DEEPSEEK_API_KEY"),
                base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            )
            sim_model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
            job_data = await validate_and_fix(job_data, stage_name_for_maa, sim_client, sim_model, max_fixes=2)
            # 写回修正后的作业
            with open(job_path, "w", encoding="utf-8") as f:
                json.dump(job_data, f, ensure_ascii=False, indent=2)
            # 记录 sim 结果到记忆
            sim_result = sim_run_job(stage_name_for_maa, job_data)
            entry = classify_failure(sim_result, job_data)
            MemoryStore().record(entry)
            log.info("sim 记忆: %s %s (%s)", entry.outcome, entry.failure_mode, entry.lesson[:40])
        except Exception as e:
            log.warning("sim 验证跳过: %s", e)

    # MAA Copilot 一体化: formation + 开始作战 + actions(全给 MAA) + 安全网监控
    await _run_maa_copilot(job_path, job_data, stage)


async def _run_maa_copilot(job_path: str, job_data: dict, stage: str = "1-7") -> None:
    """MAA Copilot 一体化执行: formation + 开始作战 + actions + 胜负检测 + 安全网。"""
    import json as _json
    import subprocess as _sp
    import cv2 as _cv2
    import numpy as _np

    client = MaapyClient(resource_path=MAA)
    client.add_handler(_ev_printer)

    # 检测 CopilotAction 回调 → 战斗已开始
    _battle_started = False
    _deploy_action_count = 0
    async def _action_detector(ev):
        nonlocal _battle_started, _deploy_action_count
        if ev.msg == 20003 and ev.details.get("what") == "CopilotAction":
            _battle_started = True
            action = ev.details.get("details", {}).get("action", "")
            if action == "Deploy":
                _deploy_action_count += 1
                log.info("[Deploy] CopilotAction #%d: %s", _deploy_action_count,
                         ev.details.get("details", {}).get("target", ""))
    client.add_handler(_action_detector)

    # 安全网监控器
    from src.game.battle_monitor import BattleMonitor
    _tile_calc = None
    try:
        from src.game.tile_calc import TileCalc
        from src.data.stage_util import get_cache_path as _get_tile_path
        _tile_dir = os.path.join(MAA, "resource", "Arknights-Tile-Pos")
        _tile_path = _get_tile_path(_tile_dir, stage, MAA)
        if _tile_path and os.path.exists(_tile_path):
            _tile_calc = TileCalc(_tile_path)
    except Exception:
        pass
    _monitor = BattleMonitor(
        tile_calc=_tile_calc,
        job_actions=job_data.get("actions", []),
        check_interval=3.0,
    )
    _monitor_events: list = []

    # BattleMonitor 事件 → Streamer 事件总线 (streamer 在后面创建)
    _streamer = None
    _streamer_started = False
    def _monitor_to_streamer(ev):
        if _streamer:
            _streamer.on_battle_event(ev)
        _monitor_events.append(ev)
    _monitor.add_event_handler(_monitor_to_streamer)

    _monitor_task: asyncio.Task | None = None

    ok = await client.connect(ADB, ADDR)
    if not ok:
        log.error("connect 失败")
        return

    # === UI 导航: 检测游戏状态,必要时启动+导航 ===
    from src.game.ui_navigator import UINavigator
    _navigator = UINavigator()
    _nav_info = _navigator.get_screen_info()
    log.info("当前界面: %s", _nav_info["screen"])

    # 如果游戏没在运行(截图失败),启动游戏
    if not _nav_info.get("has_image"):
        log.info("=== 游戏未运行,启动游戏 ===")
        _navigator.launch_game()
        if not _navigator.wait_for_game_start(timeout=120):
            log.error("游戏启动失败")
            return

    # 如果在主界面或未知界面,导航到编队
    _nav_info = _navigator.get_screen_info()
    _screen = _nav_info["screen"]
    if _screen not in ("formation", "battle", "results"):
        if _screen in ("home", "unknown"):
            log.info("=== 自主导航到编队界面 ===")
            if not _navigator.navigate_to_formation(stage_code=stage):
                log.warning("导航失败,尝试直接启动 MAA Copilot")
    elif _screen == "results":
        log.info("=== 关闭结算界面 ===")
        _navigator.dismiss_results()

    log.info("=== MAA Copilot 一体化(formation + actions) ===")
    await client.append("Copilot", {"filename": job_path, "formation": True, "formation_index": 0})

    # AI 主播 (暂时关闭,专注修部署问题)
    _ENABLE_STREAMER = False
    _streamer = None
    _streamer_started = False
    _streamer_task = None
    if _ENABLE_STREAMER:
        from src.streamer.streamer import Streamer as _Streamer
        _streamer = _Streamer(mock_danmaku=True, danmaku_interval=20.0)
        _streamer_started = False
        _monitor_to_streamer = lambda ev: (_streamer.on_battle_event(ev), _monitor_events.append(ev))
        _monitor.add_event_handler(_monitor_to_streamer)

        async def _start_streamer_async():
            """异步启动 AI 主播(不阻塞 MAA)。"""
            nonlocal _streamer_started
            await _streamer.start()
            _streamer_started = True
            _streamer.on_battle_start(stage, oper_count=len(job_data.get("opers", [])))
            log.info("=== AI 主播已启动(异步) ===")

        _streamer_task = asyncio.create_task(_start_streamer_async())

    # 异步监控: 18 秒后 ADB tap 开始作战(足够编队完成)
    _adb_tapped = False
    _deploy_verified = False
    import time as _time
    _start_time = _time.time()

    while True:
        await asyncio.sleep(0.5)
        if not client.running():
            break
        if _battle_started:
            # 战斗已开始 → 启动安全网监控
            if _monitor_task is None:
                log.info("=== 安全网监控启动 ===")
                _monitor_task = asyncio.create_task(_monitor.monitor_loop(timeout=300))
            # 部署验证: Deploy 后 3 秒检查干员是否在场上
            if _deploy_action_count > 0 and not _deploy_verified:
                _deploy_verified = True
                log.info("=== 部署验证(%d个Deploy action) ===", _deploy_action_count)
                await asyncio.sleep(3)
                # 截图检查是否还在战斗中
                _deploy_ok = await _verify_and_fallback_deploy(
                    ADB, ADDR, MAA, job_data, _tile_calc, _cv2, _np, _sp)
                if _deploy_ok:
                    log.info("部署验证通过")
                else:
                    log.warning("部署验证失败,已尝试 ADB fallback")
        # 18 秒后 ADB tap (编队通常 10-15 秒完成)
        if not _adb_tapped and _time.time() - _start_time > 25:
            log.info("编队后 10 秒未进战斗, ADB tap 开始作战...")
            _t = _cv2.imread(os.path.join(MAA, "resource", "template", "Battle", "StartButton", "BattleStartNormal.png"))
            if _t is not None:
                _ts = _cv2.resize(_t, (int(_t.shape[1]*1.5), int(_t.shape[0]*1.5)))
                _r = _sp.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"], capture_output=True, timeout=10)
                _arr = _np.frombuffer(_r.stdout, dtype=_np.uint8)
                _img = _cv2.imdecode(_arr, _cv2.IMREAD_COLOR)
                if _img is not None:
                    _res = _cv2.matchTemplate(_img, _ts, _cv2.TM_CCOEFF_NORMED)
                    _, _mv, _, _ml = _cv2.minMaxLoc(_res)
                    if _mv > 0.8:
                        _cx = _ml[0] + _ts.shape[1] // 2
                        _cy = _ml[1] + _ts.shape[0] // 2
                        log.info("  ADB tap 开始作战(%d,%d) score=%.3f", _cx, _cy, _mv)
                        _sp.run([ADB, "-s", ADDR, "shell", "input", "tap", str(_cx), str(_cy)])
                        _adb_tapped = True
                        await asyncio.sleep(5)
                    else:
                        log.info("  未找到开始作战按钮, 等待 MAA 自己处理")
                        _adb_tapped = True  # 不再重试

    await client.wait_done(timeout=300)
    log.info("Copilot 完成")

    # 如果 MAA 完成快(部署后立即结束),需要在这里也启动 AI 主播
    if _ENABLE_STREAMER and _deploy_action_count > 0 and not _streamer_started:
        log.info("=== AI 主播启动(延迟) ===")
        await _streamer.start()
        _streamer_started = True
        _streamer.on_battle_start(stage, oper_count=len(job_data.get("opers", [])))
        if _monitor_task is None:
            log.info("=== 安全网监控启动(延迟) ===")
            _monitor_task = asyncio.create_task(_monitor.monitor_loop(timeout=300))

    # 停止安全网监控
    if _monitor_task is not None:
        _monitor.stop()
        try:
            await asyncio.wait_for(_monitor_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            _monitor_task.cancel()
        _monitor_result = _monitor.get_result()
        log.info("安全网结果: %s", _monitor_result)
        if _monitor_events:
            log.info("安全网事件(%d条):", len(_monitor_events))
            for ev in _monitor_events:
                log.info("  [%s] %s", ev.event_type, ev.message)

    # 停止 AI 主播
    if _ENABLE_STREAMER and _streamer_started:
        # 报告战斗结果
        try:
            _stars = _detect_battle_result(ADB, ADDR, MAA)
            _result_str = "win" if _stars >= 2 else "lose"
            _streamer.on_battle_end(_result_str)
        except Exception:
            _streamer.on_battle_end("unknown")
        await asyncio.sleep(3)
        await _streamer.stop()

    # 如果 MAA 没进战斗(BattleStartAll 失败),ADB tap 开始作战
    if not _battle_started:
        log.info("MAA 未能开始战斗,ADB tap fallback...")
        _t = _cv2.imread(os.path.join(MAA, "resource", "template", "Battle", "StartButton", "BattleStartNormal.png"))
        if _t is not None:
            _ts = _cv2.resize(_t, (int(_t.shape[1]*1.5), int(_t.shape[0]*1.5)))
            for _retry in range(5):
                _r = _sp.run([ADB, "-s", ADDR, "exec-out", "screencap", "-p"], capture_output=True, timeout=10)
                _arr = _np.frombuffer(_r.stdout, dtype=_np.uint8)
                _img = _cv2.imdecode(_arr, _cv2.IMREAD_COLOR)
                if _img is None:
                    await asyncio.sleep(2)
                    continue
                _res = _cv2.matchTemplate(_img, _ts, _cv2.TM_CCOEFF_NORMED)
                _, _mv, _, _ml = _cv2.minMaxLoc(_res)
                if _mv > 0.8:
                    _cx = _ml[0] + _ts.shape[1] // 2
                    _cy = _ml[1] + _ts.shape[0] // 2
                    log.info("  ADB tap 开始作战(%d,%d) score=%.3f", _cx, _cy, _mv)
                    _sp.run([ADB, "-s", ADDR, "shell", "input", "tap", str(_cx), str(_cy)])
                    await asyncio.sleep(5)
                    # 重新提交 Copilot 任务执行 actions
                    await client.append("Copilot", {"filename": job_path, "formation": False, "formation_index": 0})
                    await client.start()
                    break
                log.info("  未找到开始作战按钮(%d/5)", _retry+1)
                await asyncio.sleep(2)

    # 胜负检测: 截图匹配 Stars 模板
    await asyncio.sleep(3)
    _battle_result = "unknown"
    _battle_stars = 0
    try:
        _stars = _detect_battle_result(ADB, ADDR, MAA)
        _battle_stars = _stars
        if _stars >= 3:
            _battle_result = "win"
            log.info("=== 通关! Stars=%d ===", _stars)
            from src.data.stage_util import get_cache_path
            _cache_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "job_cache"))
            os.makedirs(_cache_dir, exist_ok=True)
            _cache_path = get_cache_path(_cache_dir, stage, MAA)
            with open(_cache_path, "w", encoding="utf-8") as f:
                _json.dump(job_data, f, ensure_ascii=False, indent=2)
            log.info("作业已缓存: %s", _cache_path)
        elif _stars == 0:
            _battle_result = "lose"
            log.warning("=== 失败(0星), 可能漏怪 ===")
        else:
            log.info("=== 无法判断胜负(可能未结算) ===")
    except Exception as e:
        log.error("胜负检测失败: %s", e)

    # === P3: 真机结果记录到记忆 ===
    try:
        from src.sim.memory import MemoryStore, MemoryEntry
        _mem_store = MemoryStore()
        _oper_names = [o.get("name", "") for o in job_data.get("opers", [])]
        _deploy_actions = [
            {"name": a.get("name", ""), "location": a.get("location", []),
             "direction": a.get("direction", "")}
            for a in job_data.get("actions", []) if a.get("type") == "Deploy"
        ]
        # 从 BattleMonitor 事件提取异常
        _anomaly_types = []
        for ev in _monitor_events:
            if ev.event_type == "anomaly":
                _anomaly_types.append(ev.data.get("type", "unknown"))

        # 构建记忆条目
        if _battle_result == "win":
            _failure_mode = "clear"
            _root_cause = ""
            _lesson = "通关成功，当前作业有效"
        elif _battle_result == "lose":
            _failure_mode = "leak" if _anomaly_types else "timeout"
            _root_cause = "; ".join(_anomaly_types) if _anomaly_types else "unknown"
            _lesson = f"失败: {_root_cause[:60]}" if _root_cause else "战斗失败，原因不明"
        else:
            _failure_mode = "unknown"
            _root_cause = "无法判断胜负"
            _lesson = "战斗结果未知"

        _existing = _mem_store.get_stage_memories(stage)
        _entry = MemoryEntry(
            stage=stage,
            attempt=len(_existing) + 1,
            deployments=_deploy_actions,
            outcome=_battle_result,
            failure_mode=_failure_mode,
            root_cause=_root_cause,
            lesson=_lesson,
            generalizable=True,
        )
        _mem_store.record(_entry)
        log.info("=== P3 记忆记录: %s %s (%s) ===", _battle_result, _failure_mode, _lesson[:40])
    except Exception as e:
        log.warning("P3 记忆记录失败: %s", e)

    # 清理: 关闭结算界面
    try:
        _navigator.cleanup_after_battle()
    except Exception:
        pass


async def smoke_operbox() -> None:
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("MAA=%s ADB=%s ADDR=%s", MAA, ADB, ADDR)
    own: dict = {"opers": []}

    async def h(ev):
        if ev.msg == MSG_SUB_TASK_EXTRA_INFO and ev.details.get("what") == "OperBoxInfo":
            d = ev.details
            inner = d.get("details") or {}
            if own.get("_first") is None:
                own["_first"] = True
                all_ops0 = inner.get("all_opers") or []
                own0 = sum(1 for o in all_ops0 if o.get("own"))
                print(f"first cb: all_opers={len(all_ops0)} own_true={own0} done={inner.get('done')}")
            ops = inner.get("own_opers") or inner.get("all_opers") or []
            existing = {o["name"] for o in own["opers"]}
            for o in ops:
                if o.get("own") and o.get("name") and o["name"] not in existing:
                    own["opers"].append({
                        "name": o["name"],
                        "rarity": o.get("rarity"),
                        "elite": o.get("elite"),
                        "level": o.get("level"),
                        "potential": o.get("potential"),
                    })
                    existing.add(o["name"])
            if inner.get("done"):
                all_ops_d = inner.get("all_opers") or []
                own_d = sum(1 for o in all_ops_d if o.get("own"))
                print(f"done cb: all_opers={len(all_ops_d)} own_true={own_d} accumulated={len(own['opers'])}")

    client = MaapyClient(resource_path=MAA)
    client.add_handler(_ev_printer)
    client.add_handler(h)
    ok = await client.connect(ADB, ADDR)
    if not ok:
        log.error("connect 失败")
        return
    await client.append("OperBox", {"enable": True})
    await client.start()
    await client.wait_done(timeout=300)
    out = os.path.join(os.path.dirname(__file__), "..", "operators.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(own["opers"], f, ensure_ascii=False, indent=2)
    log.info("保存 %d 个干员到 operators.json", len(own["opers"]))
    print(json.dumps(own["opers"][:40], ensure_ascii=False))


async def smoke_singlestep(steps: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("MAA=%s ADB=%s ADDR=%s", MAA, ADB, ADDR)
    if not os.getenv("DEEPSEEK_API_KEY", ""):
        log.warning("DEEPSEEK_API_KEY 未填(本次用缓存作业,不需 LLM)")
    log.info("SingleStep 实时部署测试 (patched MaaCore.dll + update_deployment)")
    from src.core.orchestrator import game_loop
    await game_loop(steps=steps)


def _detect_battle_result(adb: str, addr: str, maa: str) -> int:
    """截图检测战斗结果: 0=失败, 2=2星, 3=3星, -1=无法判断。"""
    import subprocess as sp
    import cv2
    import numpy as np

    r = sp.run([adb, "-s", addr, "exec-out", "screencap", "-p"], capture_output=True, timeout=10)
    arr = np.frombuffer(r.stdout, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return -1

    scale = 1.5  # MAA 基准 1280x720 → 1920x1080
    best_stars = 0
    best_score = 0.0

    for stars, name in [(3, "StageDrops-Stars-3"), (2, "StageDrops-Stars-2")]:
        path = os.path.join(maa, "resource", "template", "Battle", "StageDrops", name + ".png")
        if not os.path.exists(path):
            continue
        templ = cv2.imread(path)
        if templ is None:
            continue
        templ_s = cv2.resize(templ, (int(templ.shape[1] * scale), int(templ.shape[0] * scale)))
        res = cv2.matchTemplate(img, templ_s, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        if max_val > 0.8 and max_val > best_score:
            best_stars = stars
            best_score = max_val

    # 失败检测
    fail_path = os.path.join(maa, "resource", "template", "Battle", "StageDrops", "MissionFailedFlag.png")
    if not os.path.exists(fail_path):
        fail_path = os.path.join(maa, "resource", "template", "Roguelike", "MissionFailedFlag.png")
    if os.path.exists(fail_path):
        templ = cv2.imread(fail_path)
        if templ is not None:
            templ_s = cv2.resize(templ, (int(templ.shape[1] * scale), int(templ.shape[0] * scale)))
            res = cv2.matchTemplate(img, templ_s, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            if max_val > 0.8:
                return 0

    return best_stars if best_stars > 0 else -1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--copilot", action="store_true", help="SingleStep SpeedUp 验证(不需 key)")
    ap.add_argument("--llm", action="store_true", help="真实 DeepSeek 决策(需 .env DEEPSEEK_API_KEY + 游戏在编队界面)")
    ap.add_argument("--stage", type=str, default="1-7", help="关卡代码 (如 1-7, 2-1, 3-4)")
    ap.add_argument("--fresh", action="store_true", help="强制重新生成作业(跳过缓存)")
    ap.add_argument("--list-stages", action="store_true", help="列出所有可用关卡")
    ap.add_argument("--operbox", action="store_true", help="识别账号全干员列表存 operators.json(需游戏在主菜单)")
    ap.add_argument("--singlestep", action="store_true", help="SingleStep 实时部署测试(patched MaaCore.dll, 需游戏在 1-7 编队界面)")
    ap.add_argument("--steps", type=int, default=8)
    args = ap.parse_args()
    if args.list_stages:
        from src.data.stage_util import list_available_stages
        stages = list_available_stages(MAA)
        print(f"可用主线关卡 ({len(stages)} 个):")
        for s in stages:
            print(f"  {s}")
    elif args.operbox:
        asyncio.run(smoke_operbox())
    elif args.singlestep:
        asyncio.run(smoke_singlestep(args.steps))
    elif args.llm:
        asyncio.run(smoke_copilot_doc(args.stage, args.fresh))
    elif args.copilot:
        asyncio.run(smoke_copilot(args.steps))
    else:
        asyncio.run(smoke_start())


if __name__ == "__main__":
    main()
