"""Arknights mini simulator - data loader.

从 ArknightsGameData + MAA battle_data 加载:
- 关卡: 网格/路径/波次
- 干员: 属性/范围/技能
- 敌人: 属性
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

GAMEDATA = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gamedata")
MAA = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"


@dataclass
class TileInfo:
    col: int
    row: int
    tile_type: str  # road/wall/start/end/floor/forbidden
    buildable: str  # melee/ranged/none


@dataclass
class EnemySpawn:
    time: float  # seconds
    enemy_id: str
    route_index: int
    count: int = 1
    interval: float = 1.0


@dataclass
class Route:
    waypoints: list[tuple[int, int]]  # (col, row) sequence
    index: int


@dataclass
class EnemyData:
    enemy_id: str
    name: str
    hp: int
    atk: int
    defense: int
    res: float
    move_speed: float
    mass_level: int
    atk_time: float  # attack interval
    life_point_reduce: int  # how many lives lost when leaking


@dataclass
class SkillData:
    skill_index: int
    name: str
    sp_cost: int
    sp_init: int
    sp_type: str  # INCREASE_WITH_TIME / INCREASE_WHEN_ATTACK
    duration: float  # seconds, -1 = infinite (ammo)
    description: str
    blackboard: dict = None  # key→value (e.g. {"atk": 1.8, "base_attack_time": 2.9})


@dataclass
class OperatorData:
    char_id: str
    name: str
    profession: str  # PIONEER/WARRIOR/TANK/SNIPER/CASTER/MEDIC/SUPPORT/SPECIAL
    hp: int
    atk: int
    defense: int
    res: float
    block: int
    cost: int
    attack_time: float
    range_tiles: list[tuple[int, int]]  # relative coordinates
    skills: list[SkillData]


def load_stage(stage_id: str) -> dict:
    """加载关卡数据: 网格/路径/波次/敌人。"""
    # Find tile JSON
    tile_dir = os.path.join(MAA, "resource", "Arknights-Tile-Pos")
    tile_path = None
    for f in os.listdir(tile_dir):
        if stage_id in f or stage_id.replace("_perm", "") in f:
            tile_path = os.path.join(tile_dir, f)
            break
    if not tile_path:
        raise FileNotFoundError(f"Tile JSON not found for {stage_id}")

    with open(tile_path, encoding="utf-8") as f:
        tile_data = json.load(f)

    # Find level JSON
    level_id = tile_data.get("levelId", "")
    level_path = os.path.join(GAMEDATA, "levels", level_id.replace("/", os.sep) + ".json")
    if not os.path.exists(level_path):
        # Try download
        from src.data.stage_util import ensure_level_json_by_tile
        result = ensure_level_json_by_tile(MAA, stage_id)
        if result:
            level_path = result
        else:
            raise FileNotFoundError(f"Level JSON not found: {level_id}")

    with open(level_path, encoding="utf-8") as f:
        level_data = json.load(f)

    # Parse tiles
    tiles = []
    tile_grid = tile_data["tiles"]
    for row in range(tile_data["height"]):
        for col in range(tile_data["width"]):
            t = tile_grid[row][col]
            bt = t.get("buildableType", 0)
            buildable = "none"
            if bt == 1:
                buildable = "melee"
            elif bt == 2:
                buildable = "ranged"
            tiles.append(TileInfo(
                col=col, row=row,
                tile_type=t.get("tileKey", "tile_forbidden"),
                buildable=buildable,
            ))

    # Parse routes
    routes = []
    for i, r in enumerate(level_data.get("routes", [])):
        sp = r.get("startPosition", {})
        ep = r.get("endPosition", {})
        waypoints = []
        if sp.get("col") is not None:
            waypoints.append((int(sp["col"]), int(sp["row"])))
        for cp in (r.get("checkpoints") or []):
            if cp.get("type") == "MOVE":
                pos = cp.get("position", {})
                if pos.get("col") is not None:
                    waypoints.append((int(pos["col"]), int(pos["row"])))
        if ep.get("col") is not None:
            waypoints.append((int(ep["col"]), int(ep["row"])))
        # Deduplicate consecutive
        deduped = [waypoints[0]] if waypoints else []
        for wp in waypoints[1:]:
            if wp != deduped[-1]:
                deduped.append(wp)
        routes.append(Route(waypoints=deduped, index=i))

    # Parse waves → enemy spawns
    spawns = []
    enemy_db_path = os.path.join(GAMEDATA, "levels", "enemydata", "enemy_database.json")
    with open(enemy_db_path, encoding="utf-8") as f:
        enemy_db = json.load(f)
    
    # Build enemy lookup
    enemy_lookup = {}
    for e in enemy_db.get("enemies", []):
        key = e.get("Key", "")
        values = e.get("Value", [])
        if isinstance(values, list) and values:
            v0 = values[0]
            ed = v0.get("enemyData", {})
            attrs = ed.get("attributes", {})
            enemy_lookup[key] = EnemyData(
                enemy_id=key,
                name=ed.get("name", {}).get("m_value", key) if isinstance(ed.get("name"), dict) else key,
                hp=int(_get_val(attrs.get("maxHp"))),
                atk=int(_get_val(attrs.get("atk"))),
                defense=int(_get_val(attrs.get("def"))),
                res=float(_get_val(attrs.get("magicResistance"))),
                move_speed=float(_get_val(attrs.get("moveSpeed"))),
                mass_level=int(_get_val(attrs.get("massLevel"))),
                atk_time=float(_get_val(attrs.get("baseAttackTime"))),
                life_point_reduce=int(_get_val(ed.get("lifePointReduce")) or 1),
            )

    # Parse waves
    for wave in level_data.get("waves", []):
        wave_pre = float(wave.get("preDelay", 0))
        for frag in wave.get("fragments", []):
            frag_pre = float(frag.get("preDelay", 0))
            for action in frag.get("actions", []):
                act_pre = float(action.get("preDelay", 0))
                abs_time = wave_pre + frag_pre + act_pre
                enemy_id = action.get("key", "")
                if not enemy_id.startswith("enemy_"):
                    continue
                count = int(action.get("count", 1))
                interval = float(action.get("interval", 1.0))
                route_idx = int(action.get("routeIndex", 0))
                spawns.append(EnemySpawn(
                    time=abs_time,
                    enemy_id=enemy_id,
                    route_index=route_idx,
                    count=count,
                    interval=interval,
                ))

    spawns.sort(key=lambda s: s.time)

    # Red/blue doors
    red_doors = []
    blue_doors = []
    for t in tiles:
        if t.tile_type == "tile_start":
            red_doors.append((t.col, t.row))
        elif t.tile_type == "tile_end":
            blue_doors.append((t.col, t.row))

    # Initial cost
    options = level_data.get("options", {})
    initial_cost = int(options.get("initialCost", 10) or 10)

    return {
        "stage_id": stage_id,
        "width": tile_data["width"],
        "height": tile_data["height"],
        "tiles": tiles,
        "routes": routes,
        "spawns": spawns,
        "enemy_lookup": enemy_lookup,
        "red_doors": red_doors,
        "blue_doors": blue_doors,
        "initial_cost": initial_cost,
        "runes": level_data.get("runes", []),
    }


def load_operator(name: str) -> OperatorData:
    """加载单个干员的完整数据。"""
    char_path = os.path.join(GAMEDATA, "excel", "character_table.json")
    skill_path = os.path.join(GAMEDATA, "excel", "skill_table.json")

    with open(char_path, encoding="utf-8") as f:
        chars = json.load(f)
    with open(skill_path, encoding="utf-8") as f:
        skills = json.load(f)

    char_id = None
    char = None
    for k, v in chars.items():
        if v.get("name") == name:
            char_id = k
            char = v
            break
    if not char:
        raise ValueError(f"Operator not found: {name}")

    # Stats from elite 2
    phases = char.get("phases", [])
    hp = atk = defense = res = 0
    block = cost = 0
    attack_time = 0.0
    if len(phases) >= 3:
        frames = phases[2].get("attributesKeyFrames", [])
        if frames:
            data = frames[-1].get("data", {})
            hp = int(_get_val(data.get("maxHp")))
            atk = int(_get_val(data.get("atk")))
            defense = int(_get_val(data.get("def")))
            res = float(_get_val(data.get("magicResistance")))
            block = int(_get_val(data.get("blockCnt")))
            cost = int(_get_val(data.get("cost")))
            attack_time = float(_get_val(data.get("baseAttackTime")))

    # Range tiles
    from src.sim.range_calc import get_range_tiles
    range_tiles = get_range_tiles(name)

    # Skills
    skill_list = []
    for i, s_ref in enumerate(char.get("skills", [])):
        sid = s_ref.get("skillId", "")
        if sid not in skills:
            continue
        s_data = skills[sid]
        levels = s_data.get("levels", [])
        if not levels:
            continue
        lv = levels[-1] if len(levels) >= 7 else levels[0]
        sp = lv.get("spData", {})
        dur = lv.get("duration", 0) or 0
        # Parse blackboard (skill effect values)
        bb_raw = lv.get("blackboard", [])
        bb = {}
        if isinstance(bb_raw, list):
            for b in bb_raw:
                key = b.get("key", "")
                val = b.get("value", 0)
                if key and val:
                    bb[key] = val
        elif isinstance(bb_raw, dict):
            bb = {k: v for k, v in bb_raw.items() if v}

        skill_list.append(SkillData(
            skill_index=i + 1,
            name=lv.get("name", ""),
            sp_cost=int(sp.get("spCost", 0) or 0),
            sp_init=int(sp.get("initSp", 0) or 0),
            sp_type=sp.get("spType", ""),
            duration=float(dur),
            description=lv.get("description", "")[:80],
            blackboard=bb,
        ))

    return OperatorData(
        char_id=char_id,
        name=name,
        profession=char.get("profession", ""),
        hp=hp, atk=atk, defense=defense, res=res,
        block=block, cost=cost, attack_time=attack_time,
        range_tiles=range_tiles,
        skills=skill_list,
    )


def _get_val(val) -> float:
    if isinstance(val, dict):
        return val.get("m_value", 0) or 0
    return val or 0


if __name__ == "__main__":
    # Test load AT-7
    stage = load_stage("act44side_07")
    print("Stage: %s %dx%d" % (stage["stage_id"], stage["width"], stage["height"]))
    print("Red doors:", stage["red_doors"])
    print("Blue doors:", stage["blue_doors"])
    print("Routes: %d" % len(stage["routes"]))
    print("Spawns: %d" % len(stage["spawns"]))
    print("Enemies: %d" % len(stage["enemy_lookup"]))
    print("Initial cost: %d" % stage["initial_cost"])
    print()
    
    # Test load operators
    for name in ["维什戴尔", "夜莺", "塞雷娅"]:
        op = load_operator(name)
        print("=== %s ===" % op.name)
        print("  HP=%d ATK=%d DEF=%d RES=%d block=%d cost=%d atk_time=%.1f" % (
            op.hp, op.atk, op.defense, op.res, op.block, op.cost, op.attack_time))
        print("  range: %d tiles" % len(op.range_tiles))
        for s in op.skills:
            print("  skill%d: %s spCost=%d spInit=%d %s dur=%.1f" % (
                s.skill_index, s.name, s.sp_cost, s.sp_init, s.sp_type, s.duration))
        print()
