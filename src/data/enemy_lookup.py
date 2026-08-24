"""敌人属性查询: enemy_database.json + enemy_handbook_table.json → 敌人属性摘要。

从 ArknightsGameData 提取敌人属性,输出给 DeepSeek:
  "源石虫: HP1050 ATK185 DEF0 RES0 | 士兵: HP1000 ATK130 DEF50"
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class EnemyInfo:
    """敌人属性。"""
    enemy_id: str = ""
    name: str = ""
    hp: int = 0
    atk: int = 0
    defense: int = 0
    res: float = 0.0
    move_speed: float = 0.0
    attack_speed: float = 0.0
    mass_level: int = 0


_enemy_cache: dict = None
_name_cache: dict = None


def lookup_enemy(enemy_id: str, db_path: str = "", handbook_path: str = "") -> EnemyInfo | None:
    """查询单个敌人属性。"""
    stats = _load_enemy_stats(db_path)
    names = _load_enemy_names(handbook_path)

    if enemy_id not in stats:
        return None

    s = stats[enemy_id]
    return EnemyInfo(
        enemy_id=enemy_id,
        name=names.get(enemy_id, ""),
        hp=int(s.get("maxHp", 0) or 0),
        atk=int(s.get("atk", 0) or 0),
        defense=int(s.get("def", 0) or 0),
        res=float(s.get("magicResistance", 0) or 0),
        move_speed=float(s.get("moveSpeed", 0) or 0),
        attack_speed=float(s.get("attackSpeed", 0) or 0),
        mass_level=int(s.get("massLevel", 0) or 0),
    )


def lookup_enemies(enemy_ids: list[str], db_path: str = "", handbook_path: str = "") -> list[EnemyInfo]:
    """批量查询敌人属性。"""
    result = []
    for eid in enemy_ids:
        info = lookup_enemy(eid, db_path, handbook_path)
        if info:
            result.append(info)
    return result


def to_compact_description(enemy_ids: list[str], db_path: str = "", handbook_path: str = "") -> str:
    """批量查询并输出紧凑文本。"""
    parts = []
    seen = set()
    for eid in enemy_ids:
        if eid in seen:
            continue
        seen.add(eid)
        info = lookup_enemy(eid, db_path, handbook_path)
        if info and info.name:
            extras = []
            if info.move_speed:
                extras.append("移速%.1f" % info.move_speed)
            if info.mass_level:
                extras.append("重量%d" % info.mass_level)
            extra_str = " " + " ".join(extras) if extras else ""
            parts.append("%s: HP%d ATK%d DEF%d RES%d%s" % (
                info.name, info.hp, info.atk, info.defense, int(info.res), extra_str))
    return " | ".join(parts)


def _load_enemy_stats(db_path: str = "") -> dict:
    global _enemy_cache
    if _enemy_cache is not None:
        return _enemy_cache
    if not db_path:
        db_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "gamedata",
            "levels", "enemydata", "enemy_database.json"
        )
    with open(db_path, encoding="utf-8") as f:
        edb = json.load(f)
    _enemy_cache = {}
    for e in edb.get("enemies", []):
        key = e.get("Key", "")
        values = e.get("Value", [])
        if not isinstance(values, list) or not values:
            continue
        v0 = values[0]
        ed = v0.get("enemyData", {})
        attrs = ed.get("attributes", {})
        stats = {}
        for k in ("maxHp", "atk", "def", "magicResistance", "moveSpeed", "attackSpeed", "massLevel"):
            val = attrs.get(k)
            if isinstance(val, dict):
                stats[k] = val.get("m_value")
            elif isinstance(val, (int, float)):
                stats[k] = val
        _enemy_cache[key] = stats
    return _enemy_cache


def _load_enemy_names(handbook_path: str = "") -> dict:
    global _name_cache
    if _name_cache is not None:
        return _name_cache
    if not handbook_path:
        handbook_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "gamedata",
            "excel", "enemy_handbook_table.json"
        )
    with open(handbook_path, encoding="utf-8") as f:
        h = json.load(f)
    _name_cache = {}
    ed = h.get("enemyData", {})
    if isinstance(ed, dict):
        for k, v in ed.items():
            name = v.get("name", "")
            if name:
                _name_cache[k] = name
    return _name_cache


if __name__ == "__main__":
    ids = ["enemy_1007_slime_2", "enemy_1027_mob", "enemy_1002_nsabr",
           "enemy_1029_shdsbr", "enemy_1028_mocock"]
    desc = to_compact_description(ids)
    print(desc)
