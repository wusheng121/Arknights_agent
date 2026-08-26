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
        # 无专家作业 → 走完整管道 + 后处理
        log.info("无匹配专家作业,走 LLM 管道 + 后处理")
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

    # MAA Copilot 一体化: formation + 开始作战 + actions(全给 MAA)
    await _run_maa_copilot(job_path, job_data, stage)


async def _run_maa_copilot(job_path: str, job_data: dict, stage: str = "1-7") -> None:
    """MAA Copilot 一体化执行: formation + 开始作战 + actions + 胜负检测。"""
    import json as _json
    import subprocess as _sp
    import cv2 as _cv2
    import numpy as _np

    client = MaapyClient(resource_path=MAA)
    client.add_handler(_ev_printer)

    # 检测 CopilotAction 回调 → 战斗已开始
    _battle_started = False
    async def _action_detector(ev):
        nonlocal _battle_started
        if ev.msg == 20003 and ev.details.get("what") == "CopilotAction":
            _battle_started = True
    client.add_handler(_action_detector)

    ok = await client.connect(ADB, ADDR)
    if not ok:
        log.error("connect 失败")
        return

    log.info("=== MAA Copilot 一体化(formation + actions) ===")
    await client.append("Copilot", {"filename": job_path, "formation": True, "formation_index": 0})
    await client.start()

    # 异步监控: 18 秒后 ADB tap 开始作战(足够编队完成)
    _adb_tapped = False
    import time as _time
    _start_time = _time.time()

    while True:
        await asyncio.sleep(0.5)
        if not client.running():
            break
        if _battle_started:
            continue
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
    try:
        _stars = _detect_battle_result(ADB, ADDR, MAA)
        if _stars >= 3:
            log.info("=== 通关! Stars=%d ===", _stars)
            from src.data.stage_util import get_cache_path
            _cache_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "job_cache"))
            os.makedirs(_cache_dir, exist_ok=True)
            _cache_path = get_cache_path(_cache_dir, stage, MAA)
            with open(_cache_path, "w", encoding="utf-8") as f:
                _json.dump(job_data, f, ensure_ascii=False, indent=2)
            log.info("作业已缓存: %s", _cache_path)
        elif _stars == 0:
            log.warning("=== 失败(0星), 可能漏怪 ===")
        else:
            log.info("=== 无法判断胜负(可能未结算) ===")
    except Exception as e:
        log.error("胜负检测失败: %s", e)


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
