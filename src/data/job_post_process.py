"""作业后处理:修正 DeepSeek 生成的作业 JSON。

修正:
1. 方向: 根据敌人方向自动覆盖(从右向左→Right, 从左向右→Left)
2. 位置: 检查 location 是否在可部署列表,越界/不在则跳过该 action
3. 类型: 检查干员职业(地面/高台)与格子类型(buildableType)是否匹配,不匹配则跳过
4. 去重: 同一格子不能部署两次
"""

from __future__ import annotations

import json
from src.data.map_info import MapInfo, parse_tile_json
from src.data.oper_database import OperDatabase, PROFESSION_TO_ROLE


# 职业→部署类型
ROLE_TO_DEPLOY = {
    "Pioneer": "Melee", "Warrior": "Melee", "Tank": "Melee",
    "Special": "Melee", "Drone": "Melee",
    "Sniper": "Ranged", "Caster": "Ranged",
    "Medic": "Ranged", "Support": "Ranged",
}


def post_process_job(job: dict, map_info: MapInfo, db: OperDatabase) -> dict:
    """后处理作业 JSON:修正方向 + 验证位置 + 类型匹配 + 去重。"""
    actions = job.get("actions", [])
    opers = job.get("opers", [])

    # 1. 修正方向
    correct_dir = _get_correct_direction(map_info)
    for action in actions:
        if action.get("type") == "Deploy" and action.get("direction"):
            action["direction"] = correct_dir

    # 2. 验证位置 + 类型 + 去重
    melee_set = set(map_info.melee_tiles)
    ranged_set = set(map_info.ranged_tiles)
    used_tiles = set()
    valid_actions = []

    for action in actions:
        if action.get("type") != "Deploy":
            valid_actions.append(action)
            continue

        name = action.get("name", "")
        loc = action.get("location", [])
        if len(loc) < 2:
            continue
        col, row = int(loc[0]), int(loc[1])
        tile = (col, row)

        # 检查位置在可部署列表 — 不在则找最近的同类型格子
        oper = db.find_oper(name)
        role = oper.role if oper else ""
        deploy_type = ROLE_TO_DEPLOY.get(role, "")

        if tile not in melee_set and tile not in ranged_set:
            # 位置不可部署 → 找最近的正确类型格子
            target_set = melee_set if deploy_type == "Melee" else ranged_set
            new_tile = _find_nearest(tile, target_set, used_tiles)
            if new_tile is None:
                continue
            tile = new_tile
            action["location"] = [tile[0], tile[1]]
        elif deploy_type == "Melee" and tile not in melee_set:
            # 地面职业放高台 → 找最近的地面格子
            new_tile = _find_nearest(tile, melee_set, used_tiles)
            if new_tile is None:
                continue
            tile = new_tile
            action["location"] = [tile[0], tile[1]]
        elif deploy_type == "Ranged" and tile not in ranged_set:
            # 高台职业放地面 → 找最近的高台格子
            new_tile = _find_nearest(tile, ranged_set, used_tiles)
            if new_tile is None:
                continue
            tile = new_tile
            action["location"] = [tile[0], tile[1]]

        # 检查去重
        if tile in used_tiles:
            # 已占用 → 找最近的同类型格子
            target_set = melee_set if deploy_type == "Melee" else ranged_set
            new_tile = _find_nearest(tile, target_set, used_tiles)
            if new_tile is None:
                continue
            tile = new_tile
            action["location"] = [tile[0], tile[1]]

        used_tiles.add(tile)
        valid_actions.append(action)

    # 3. 补充 costs 条件(让 MAA 等费用够了再部署)
    for action in valid_actions:
        if action.get("type") == "Deploy" and "costs" not in action:
            name = action.get("name", "")
            oper = db.find_oper(name)
            cost = oper.cost if oper and oper.cost else 0
            if cost > 0:
                action["costs"] = cost
            else:
                action["costs"] = 10  # 未知 cost 默认 10

    job["actions"] = valid_actions
    return job


def _find_nearest(tile: tuple[int, int], candidates: set, used: set) -> tuple[int, int] | None:
    """找最近的未占用格子(曼哈顿距离)。"""
    best = None
    best_dist = 9999
    for c in candidates:
        if c in used:
            continue
        dist = abs(c[0] - tile[0]) + abs(c[1] - tile[1])
        if dist < best_dist:
            best_dist = dist
            best = c
    return best


def _get_correct_direction(map_info: MapInfo) -> str:
    """根据敌人方向返回正确朝向。"""
    if map_info.enemy_direction == "从右向左":
        return "Right"
    elif map_info.enemy_direction == "从左向右":
        return "Left"
    return "Right"


if __name__ == "__main__":
    from src.data.map_info import parse_tile_json
    from src.data.oper_database import OperDatabase

    mi = parse_tile_json(r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\resource\Arknights-Tile-Pos\main_01-07-obt-main-level_main_01-07.json")
    db = OperDatabase()
    db.load_from_maa()
    db.load_cost_from_file("cost.json")

    with open("copilot_job.json", encoding="utf-8") as f:
        job = json.load(f)

    print("修正前 actions:", len(job["actions"]))
    job = post_process_job(job, mi, db)
    print("修正后 actions:", len(job["actions"]))
    print("敌人方向:", mi.enemy_direction, "→ 朝向:", _get_correct_direction(mi))
    for a in job["actions"]:
        if a["type"] == "Deploy":
            print(f"  {a['name']} {a['location']} dir={a['direction']}")
