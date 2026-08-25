"""Pass 1: per-job 标注 — 对每份专家作业标注"为什么这样部署"。

输入: 作业 JSON + 地图 + 敌人路径 + 干员数据
输出: 每个 Deploy 的因果标注(为什么这个位置/方向/干员/技能)
"""

from __future__ import annotations

import json
import os
import asyncio
import logging
from typing import Any

MAA = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"
GAMEDATA = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gamedata")
EXPERT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "expert_jobs")
ANNOTATION_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "annotations")

PROMPT_PASS1 = """你是明日方舟战术分析师。给定一份通关作业(干员+部署位置+方向+技能)、地图信息和敌人路径,标注每个部署决策的因果关系。

对每个 Deploy action,输出:
- reason_position: 为什么放在这个位置?(覆盖哪条敌人路径?守哪个蓝门?)
- reason_direction: 为什么朝这个方向?(敌人从哪个方向来?)
- reason_operator: 为什么选这个干员?(角色/费用/范围/天赋适配什么?)
- reason_skill: 为什么选这个技能?(持续/爆发/回费/治疗?)

对 Skill action:
- reason_skill_timing: 为什么在这个时机开/关技能?(kills条件意味着什么?)

对 Retreat action:
- reason_retreat: 为什么撤退?(腾部署位?已用完?)

**输入字段说明:**
- job: 作业JSON(opers + actions)
- map_info: 地图(红蓝门/可部署格子/战术建议)
- enemy_paths: 敌人路径(坐标序列)
- operator_profiles: 干员特性(角色/阻挡/费用/范围/天赋/技能描述)

只输出 JSON: {"annotations":[{"action_index":0,"type":"SpeedUp","reason":"开局加速"},
{"action_index":1,"type":"Deploy","operator":"桃金娘","location":[4,1],"direction":"Right",
"reason_position":"...","reason_direction":"...","reason_operator":"...","reason_skill":"..."}]}
"""

log = logging.getLogger(__name__)


def _load_tile_json(stage_id: str) -> dict | None:
    tile_dir = os.path.join(MAA, "resource", "Arknights-Tile-Pos")
    # Try exact match first
    for f in os.listdir(tile_dir):
        if stage_id in f:
            path = os.path.join(tile_dir, f)
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    # Try without _perm suffix (activity stages)
    short_id = stage_id.replace("_perm", "")
    for f in os.listdir(tile_dir):
        if short_id in f:
            path = os.path.join(tile_dir, f)
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return None


def _load_level_json(stage_id: str) -> dict | None:
    # Try loading from already downloaded level JSON
    # First find tile JSON to get levelId
    tile_dir = os.path.join(MAA, "resource", "Arknights-Tile-Pos")
    level_id = None
    for f in os.listdir(tile_dir):
        if stage_id in f or stage_id.replace("_perm", "") in f:
            tile_path = os.path.join(tile_dir, f)
            try:
                with open(tile_path, encoding="utf-8") as fh:
                    td = json.load(fh)
                level_id = td.get("levelId", "")
            except Exception:
                pass
            break
    
    if not level_id:
        return None
    
    # Try to find already downloaded level JSON
    local_path = os.path.join(GAMEDATA, "levels", level_id.replace("/", os.sep) + ".json")
    if os.path.exists(local_path):
        with open(local_path, encoding="utf-8") as f:
            return json.load(f)
    
    # Try to download
    from src.data.stage_util import ensure_level_json_by_tile
    result = ensure_level_json_by_tile(MAA, stage_id)
    if result and os.path.exists(result):
        with open(result, encoding="utf-8") as f:
            return json.load(f)
    
    return None


def _build_context(job: dict) -> dict:
    """为一份作业构建标注上下文。"""
    stage_id = job.get("stage_name", "")
    
    # 地图信息
    from src.data.map_info import parse_tile_json
    tile = _load_tile_json(stage_id)
    map_info = ""
    if tile:
        mi = parse_tile_json(_find_tile_path(stage_id))
        map_info = mi.to_description()
        tac = mi.to_tactical_description()
        if tac:
            map_info += "\n" + tac
    
    # 敌人路径
    from src.data.wave_parser import _extract_waypoints, _parse_paths
    level = _load_level_json(stage_id)
    enemy_paths = ""
    if level:
        routes = level.get("routes", [])
        enemy_paths = _parse_paths(routes)
    
    # 干员特性
    from src.data.oper_profile import get_operator_profile
    oper_profiles = []
    for o in job.get("opers", []):
        name = o.get("name", "")
        if name:
            p = get_operator_profile(name)
            if p:
                oper_profiles.append(p)
    
    return {
        "job": job,
        "map_info": map_info,
        "enemy_paths": enemy_paths,
        "operator_profiles": oper_profiles,
    }


def _find_tile_path(stage_id: str) -> str:
    tile_dir = os.path.join(MAA, "resource", "Arknights-Tile-Pos")
    for f in os.listdir(tile_dir):
        if stage_id in f:
            return os.path.join(tile_dir, f)
    return ""


async def _call_deepseek(client, model: str, system_prompt: str, user_content: str) -> dict:
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


async def annotate_job(job_path: str, client, model: str) -> dict | None:
    """标注单份作业。"""
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    
    stage_id = job.get("stage_name", "")
    if not stage_id:
        return None
    
    # Check if has map + enemy paths
    context = _build_context(job)
    if not context["map_info"] or not context["enemy_paths"]:
        return None
    
    # Check if has 1+ Deploy actions
    deploys = [a for a in job.get("actions", []) if a.get("type") == "Deploy"]
    if len(deploys) < 1:
        return None
    
    # Call LLM
    user_content = json.dumps(context, ensure_ascii=False)
    try:
        result = await _call_deepseek(client, model, PROMPT_PASS1, user_content)
    except Exception as e:
        log.error("Annotation failed for %s: %s", job_path, e)
        return None
    
    result["stage"] = stage_id
    result["job_file"] = os.path.basename(job_path)
    return result


async def run_pass1(max_jobs: int = 20, batch_delay: float = 0.5):
    """运行 Pass 1 标注。"""
    import os
    from openai import AsyncOpenAI
    
    key = os.getenv("DEEPSEEK_API_KEY")
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    mdl = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    
    if not key:
        print("DEEPSEEK_API_KEY not set")
        return
    
    client = AsyncOpenAI(api_key=key, base_url=base)
    os.makedirs(ANNOTATION_DIR, exist_ok=True)
    
    # Collect jobs to annotate
    jobs_to_annotate = []
    for stage_dir_name in os.listdir(EXPERT_DIR):
        stage_dir = os.path.join(EXPERT_DIR, stage_dir_name)
        if not os.path.isdir(stage_dir):
            continue
        for f in os.listdir(stage_dir):
            if f.endswith(".json"):
                job_path = os.path.join(stage_dir, f)
                # Check if already annotated
                annotation_path = os.path.join(ANNOTATION_DIR, stage_dir_name, f)
                if os.path.exists(annotation_path):
                    continue
                jobs_to_annotate.append((stage_dir_name, f, job_path))
    
    print("Jobs to annotate: %d (limiting to %d)" % (len(jobs_to_annotate), max_jobs))
    
    success = 0
    fail = 0
    
    for i, (stage, fname, job_path) in enumerate(jobs_to_annotate[:max_jobs]):
        print("[%d/%d] %s/%s..." % (i + 1, min(max_jobs, len(jobs_to_annotate)), stage, fname), end="")
        
        result = await annotate_job(job_path, client, mdl)
        
        if result:
            stage_ann_dir = os.path.join(ANNOTATION_DIR, stage)
            os.makedirs(stage_ann_dir, exist_ok=True)
            out_path = os.path.join(stage_ann_dir, fname)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            success += 1
            print(" OK (%d annotations)" % len(result.get("annotations", [])))
        else:
            fail += 1
            print(" SKIP")
        
        await asyncio.sleep(batch_delay)
    
    print()
    print("Pass 1 done: success=%d fail=%d" % (success, fail))
    
    # Show sample annotation
    if success > 0:
        for stage_dir_name in os.listdir(ANNOTATION_DIR):
            stage_ann_dir = os.path.join(ANNOTATION_DIR, stage_dir_name)
            if os.path.isdir(stage_ann_dir):
                for f in os.listdir(stage_ann_dir):
                    if f.endswith(".json"):
                        path = os.path.join(stage_ann_dir, f)
                        with open(path, encoding="utf-8") as fh:
                            sample = json.load(fh)
                        print()
                        print("=== Sample annotation: %s/%s ===" % (stage_dir_name, f))
                        for ann in sample.get("annotations", [])[:3]:
                            print("  %s" % json.dumps(ann, ensure_ascii=False)[:200])
                        break
                break


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import sys
    max_jobs = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    asyncio.run(run_pass1(max_jobs=max_jobs))
