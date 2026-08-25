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


def post_process_job(job: dict, map_info: MapInfo, db: OperDatabase, has_expert: bool = False) -> dict:
    """后处理作业 JSON:修正方向 + 验证位置 + 类型匹配 + 去重。"""
    actions = job.get("actions", [])
    opers = job.get("opers", [])

    # 0. 过滤不适合站场的干员类型 (仅无专家参考时)
    if not has_expert:
        from src.data.oper_profile import _load_char_table, _detect_combat_role
        char_data = _load_char_table()
        skill_data = None
        try:
            from src.data.oper_profile import _load_skill_table
            skill_data = _load_skill_table()
        except Exception:
            pass
        filtered_opers = []
        for o in opers:
            name = o.get("name", "")
            if not name:
                continue
            prof = ""
            sub = ""
            char_entry = None
            for k, v in char_data.items():
                if v.get("name") == name:
                    prof = v.get("profession", "")
                    sub = v.get("subProfessionId", "")
                    char_entry = v
                    break
            if prof and sub and char_entry:
                trait = char_entry.get("trait", {})
                trait_desc = ""
                if isinstance(trait, dict):
                    cands = trait.get("candidates", [])
                    if cands:
                        trait_desc = cands[-1].get("description", "")
                role = _detect_combat_role(prof, sub, char_entry.get("skills", []), skill_data or {}, char_entry.get("tagList", []), trait_desc)
                if role in ("utility", "burst_only"):
                    continue
            filtered_opers.append(o)
        job["opers"] = filtered_opers
        opers = filtered_opers

    # 1. 方向: 根据敌人路径自动计算每个位置朝向
    for action in actions:
        if action.get("type") == "Deploy" and action.get("location"):
            loc = action.get("location")
            if isinstance(loc, (list, tuple)) and len(loc) >= 2:
                pos = (int(loc[0]), int(loc[1]))
                auto_dir = _calc_direction_from_paths(pos, map_info)
                if auto_dir:
                    action["direction"] = auto_dir

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

        # 如果无法从 db 获取 deploy_type,从 MAA battle_data 兜底
        if not deploy_type:
            from src.data.oper_profile import _load_char_table
            char_data = _load_char_table()
            for k, v in char_data.items():
                if v.get("name") == name:
                    pos_field = v.get("position", "")
                    deploy_type = "Melee" if pos_field == "MELEE" else ("Ranged" if pos_field == "RANGED" else "")
                    break

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

    # 3. 补充 costs 条件(用真实费用,从 character_table 读取)
    from src.data.oper_profile import _load_char_table
    char_data = _load_char_table()
    for action in valid_actions:
        if action.get("type") == "Deploy":
            name = action.get("name", "")
            real_cost = _get_real_cost(name, char_data, db)
            action["costs"] = real_cost
        elif action.get("type") == "SpeedUp":
            action["costs"] = 0

    # 4. 自动撤先锋: 如果 Deploy 数量 > 部署上限,插入 Retreat
    deploy_actions = [a for a in valid_actions if a.get("type") == "Deploy"]
    deploy_limit = 7  # 默认部署上限
    pioneer_names = []
    for a in deploy_actions:
        name = a.get("name", "")
        oper = db.find_oper(name)
        if oper and oper.role in ("Pioneer",):
            pioneer_names.append(name)
        elif not oper:
            # fallback: 从 char_table 查
            for k, v in char_data.items():
                if v.get("name") == name and v.get("profession") == "PIONEER":
                    pioneer_names.append(name)
                    break
    if len(deploy_actions) > deploy_limit and pioneer_names:
            new_actions = []
            deploy_count = 0
            for action in valid_actions:
                new_actions.append(action)
                if action.get("type") == "Deploy":
                    deploy_count += 1
                    if deploy_count == deploy_limit - 1:
                        for pn in pioneer_names:
                            new_actions.append({"type": "Retreat", "name": pn, "costs": 30})
            valid_actions = new_actions

    # 5. 蓝门覆盖验证: 每个蓝门 3 格内必须有地面干员(排除会被撤退的先锋)
    if map_info.blue_doors and map_info.melee_tiles:
        # 找出会被撤退的先锋名
        retreated_names = set()
        for a in valid_actions:
            if a.get("type") == "Retreat":
                retreated_names.add(a.get("name", ""))
        _ensure_blue_door_coverage(valid_actions, map_info, db, char_data, retreated_names)

    job["actions"] = valid_actions
    return job


def _ensure_blue_door_coverage(actions: list, map_info: MapInfo, db, char_data: dict, retreated_names: set = None) -> None:
    """确保每个蓝门 3 格内有地面阻挡干员,没有就移动最近的地面干员过来。
    
    retreated_names 中的干员会被撤退,不计算为蓝门防守。
    """
    if retreated_names is None:
        retreated_names = set()
    from src.data.oper_profile import _load_char_table
    melee_set = set(map_info.melee_tiles)
    ranged_set = set(map_info.ranged_tiles)

    for bd in map_info.blue_doors:
        # 找该蓝门 3 格内的地面 Deploy (排除会被撤退的)
        nearby_ground = []
        for a in actions:
            if a.get("type") != "Deploy":
                continue
            name = a.get("name", "")
            if name in retreated_names:
                continue  # 会被撤退,不算
            loc = a.get("location", [])
            if len(loc) < 2:
                continue
            pos = (int(loc[0]), int(loc[1]))
            dist = abs(pos[0] - bd[0]) + abs(pos[1] - bd[1])
            if dist <= 3 and pos in melee_set:
                nearby_ground.append((dist, a))

        if nearby_ground:
            continue  # 已有地面干员守此蓝门

        # 没有地面干员,找最近的地面 Deploy 移过来
        used_tiles = set()
        for a in actions:
            if a.get("type") == "Deploy":
                loc = a.get("location", [])
                if len(loc) >= 2:
                    used_tiles.add((int(loc[0]), int(loc[1])))

        best_action = None
        best_dist = 999
        for a in actions:
            if a.get("type") != "Deploy":
                continue
            name = a.get("name", "")
            # 检查是否地面职业
            is_ground = False
            oper = db.find_oper(name)
            if oper and oper.role in ("Pioneer", "Warrior", "Tank", "Special"):
                is_ground = True
            else:
                for k, v in char_data.items():
                    if v.get("name") == name and v.get("position") == "MELEE":
                        is_ground = True
                        break
            if not is_ground:
                continue
            loc = a.get("location", [])
            if len(loc) < 2:
                continue
            pos = (int(loc[0]), int(loc[1]))
            if pos not in melee_set:
                continue  # 不在地面格子,跳过
            dist = abs(pos[0] - bd[0]) + abs(pos[1] - bd[1])
            if dist < best_dist:
                best_dist = dist
                best_action = a

        if best_action:
            # 找蓝门附近最近的可用地面格子
            best_tile = None
            best_tile_dist = 999
            for tile in melee_set:
                if tile in used_tiles:
                    continue
                dist = abs(tile[0] - bd[0]) + abs(tile[1] - bd[1])
                if dist <= 3 and dist < best_tile_dist:
                    best_tile_dist = dist
                    best_tile = tile
            if best_tile:
                old_loc = best_action.get("location", [])
                used_tiles.discard((int(old_loc[0]), int(old_loc[1])))
                best_action["location"] = [best_tile[0], best_tile[1]]
                used_tiles.add(best_tile)


def _get_real_cost(name: str, char_data: dict, db) -> int:
    """从 character_table 获取真实部署费用。"""
    for k, v in char_data.items():
        if v.get("name") == name:
            phases = v.get("phases", [])
            if len(phases) >= 3:
                attrs = phases[2].get("attributesKeyFrames", [])
                if attrs:
                    data = attrs[-1].get("data", {})
                    cost = data.get("cost", 0)
                    if cost:
                        return cost
            elif phases:
                attrs = phases[0].get("attributesKeyFrames", [])
                if attrs:
                    data = attrs[-1].get("data", {})
                    cost = data.get("cost", 0)
                    if cost:
                        return cost
            break
    # fallback to oper_database
    oper = db.find_oper(name)
    if oper and oper.cost and oper.cost > 0:
        return oper.cost
    return 10


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
    """根据敌人方向返回正确朝向。'不明'时返回空。"""
    d = map_info.enemy_direction
    if d == "从右向左":
        return "Right"
    elif d == "从左向右":
        return "Left"
    elif d == "从上向下":
        return "Up"
    elif d == "从下向上":
        return "Down"
    return ""


def _calc_direction_from_paths(pos: tuple[int, int], map_info: MapInfo) -> str:
    """根据干员位置和敌人路径,计算最佳朝向。

    优先用攻击范围覆盖最大化: 对每个方向,算攻击范围覆盖多少路径格子,选覆盖最多的。
    无路径数据时用整体方向。
    """
    if not map_info.route_waypoints:
        return _get_correct_direction(map_info) or "Right"

    # 1. 提取所有路径格子(插值)
    path_tiles = _extract_path_tiles(map_info.route_waypoints)
    if not path_tiles:
        return _get_correct_direction(map_info) or "Right"

    # 2. 尝试用攻击范围覆盖最大化
    name = None  # 由调用方传入
    col, row = pos
    best_dir = ""
    best_count = -1

    # 3. 对每个方向,计算覆盖的路径格子数
    # 如果没有攻击范围数据,用简单的"最近路径段"方法
    for direction, count in _count_coverage_all_directions(pos, path_tiles, map_info):
        if count > best_count:
            best_count = count
            best_dir = direction

    if best_count > 0:
        return best_dir

    # fallback: 最近路径段方法
    return _calc_direction_nearest_segment(pos, map_info)


def _count_coverage_all_directions(pos: tuple[int, int], path_tiles: set, map_info: MapInfo) -> list[tuple[str, int]]:
    """对每个方向计算覆盖的路径格子数。
    
    平手时偏向迎敌方向(朝向敌人来的方向,能更早开始攻击)。
    """
    col, row = pos
    results = []
    for direction, offsets in [
        ("Right", [(1,0),(2,0),(3,0),(0,-1),(0,1)]),
        ("Left", [(-1,0),(-2,0),(-3,0),(0,-1),(0,1)]),
        ("Up", [(0,-1),(0,-2),(0,-3),(-1,0),(1,0)]),
        ("Down", [(0,1),(0,2),(0,3),(-1,0),(1,0)]),
    ]:
        count = 0
        for dx, dy in offsets:
            if (col+dx, row+dy) in path_tiles:
                count += 1
        results.append((direction, count))

    # 平手时偏向迎敌方向:找最近路径段的方向
    best = max(results, key=lambda x: x[1])
    tied = [r for r in results if r[1] == best[1]]
    if len(tied) > 1:
        # 平手,用最近路径段方向决定
        nearest_dir = _calc_direction_nearest_segment(pos, map_info)
        for d, c in tied:
            if d == nearest_dir:
                return [(d, c)] + [r for r in results if r[0] != d]
    return results


def _extract_path_tiles(waypoints_list: list[list[tuple[int,int]]]) -> set:
    """从路径坐标列表提取所有路径格子(水平/垂直插值)。"""
    tiles = set()
    for waypoints in waypoints_list:
        for i in range(len(waypoints) - 1):
            w1, w2 = waypoints[i], waypoints[i+1]
            if w1[1] == w2[1]:  # 水平段
                for c in range(min(w1[0], w2[0]), max(w1[0], w2[0]) + 1):
                    tiles.add((c, w1[1]))
            elif w1[0] == w2[0]:  # 垂直段
                for r in range(min(w1[1], w2[1]), max(w1[1], w2[1]) + 1):
                    tiles.add((w1[0], r))
    return tiles


def _calc_direction_nearest_segment(pos: tuple[int, int], map_info: MapInfo) -> str:
    """找最近的路径段,根据段方向计算朝向(fallback 方法)。"""
    col, row = pos
    best_dir = ""
    best_score = -1

    for waypoints in map_info.route_waypoints:
        for i in range(len(waypoints) - 1):
            w1 = waypoints[i]
            w2 = waypoints[i + 1]
            dx = w2[0] - w1[0]
            dy = w2[1] - w1[1]
            seg_len = abs(dx) + abs(dy)
            if seg_len == 0:
                continue

            mid = ((w1[0] + w2[0]) / 2, (w1[1] + w2[1]) / 2)
            dist = abs(mid[0] - col) + abs(mid[1] - row)
            score = (10000 + seg_len - dist)
            if score > best_score:
                best_score = score
                if abs(dx) > abs(dy):
                    best_dir = "Left" if dx > 0 else "Right"
                else:
                    best_dir = "Up" if dy > 0 else "Down"

    return best_dir or _get_correct_direction(map_info) or "Right"


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
