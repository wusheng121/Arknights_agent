"""出怪波次解析器: level JSON → 紧凑出怪时间线文本。

从 ArknightsGameData 的 level_*.json 解析:
- waves → fragments → actions (递归累积 preDelay 得绝对时间)
- routeIndex → routes[idx] → 路线描述(上/中/下路)
- enemy key → enemy_handbook → 中文名
- enemy key → enemy_database → 属性(HP/ATK/DEF/RES)

输出紧凑文本给 DeepSeek:
  "T+3s: 源石虫×1 上路(10,5→0,3) | T+12s: 士兵×2 中路 间隔7s"
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class SpawnAction:
    """单个出怪事件。"""
    time: float = 0.0          # 绝对出怪时间(秒)
    enemy_id: str = ""         # 敌人ID (enemy_1007_slime_2)
    enemy_name: str = ""      # 中文名
    count: int = 1            # 数量
    interval: float = 1.0     # 重复间隔(秒)
    route_index: int = 0      # 路线索引
    route_desc: str = ""      # 路线描述


@dataclass
class WaveTimeline:
    """整关出怪时间线。"""
    actions: list[SpawnAction] = field(default_factory=list)
    stage_name: str = ""

    def to_description(self) -> str:
        """转紧凑文本(给 DeepSeek 喂)。"""
        if not self.actions:
            return "无出怪数据"
        lines = ["出怪波次:"]
        for a in self.actions:
            parts = []
            if a.time > 0:
                parts.append("T+%ds" % int(a.time))
            else:
                parts.append("T+0s")
            parts.append("%sx%d" % (a.enemy_name or a.enemy_id, a.count))
            if a.route_desc:
                parts.append(a.route_desc)
            if a.interval > 1.0 and a.count > 1:
                parts.append("间隔%ds" % int(a.interval))
            lines.append("  " + " ".join(parts))
        return "\n".join(lines)


def parse_level_json(
    level_path: str,
    handbook_path: str = "",
    enemy_db_path: str = "",
) -> WaveTimeline:
    """解析 level JSON → WaveTimeline。

    Args:
        level_path: level_*.json 路径
        handbook_path: enemy_handbook_table.json 路径(用于中文名)
        enemy_db_path: enemy_database.json 路径(用于属性)
    """
    with open(level_path, encoding="utf-8") as f:
        level = json.load(f)

    routes = level.get("routes", [])
    waves = level.get("waves", [])

    name_map = _load_enemy_names(handbook_path)
    enemy_stats = _load_enemy_stats(enemy_db_path)

    timeline = WaveTimeline(
        stage_name=level.get("levelId", "")
    )

    for wave in waves:
        wave_pre = float(wave.get("preDelay", 0))
        for frag in wave.get("fragments", []):
            frag_pre = float(frag.get("preDelay", 0))
            for action in frag.get("actions", []):
                act_pre = float(action.get("preDelay", 0))
                abs_time = wave_pre + frag_pre + act_pre

                enemy_id = action.get("key", "")
                if not enemy_id.startswith("enemy_"):
                    continue
                route_idx = int(action.get("routeIndex", 0))

                route_desc = _describe_route(route_idx, routes)
                enemy_name = name_map.get(enemy_id, _strip_enemy_id(enemy_id))

                sa = SpawnAction(
                    time=abs_time,
                    enemy_id=enemy_id,
                    enemy_name=enemy_name,
                    count=int(action.get("count", 1)),
                    interval=float(action.get("interval", 1.0)),
                    route_index=route_idx,
                    route_desc=route_desc,
                )
                timeline.actions.append(sa)

    timeline.actions.sort(key=lambda a: a.time)
    return timeline


def _describe_route(idx: int, routes: list) -> str:
    """将路线转为描述: 上路/中路/下路 + 起点终点。"""
    if idx < 0 or idx >= len(routes):
        return ""
    r = routes[idx]
    sp = r.get("startPosition", {})
    ep = r.get("endPosition", {})
    sr, sc = sp.get("row"), sp.get("col")
    er, ec = ep.get("row"), ep.get("col")
    if sr is None or sc is None:
        return ""
    path = _row_to_path(sr)
    if er is not None and ec is not None and (sr != er or sc != ec):
        end_path = _row_to_path(er)
        if path and end_path and path != end_path:
            return "%s→%s" % (path, end_path)
        return "%s(%d,%d→%d,%d)" % (path or "", sc, sr, ec, er)
    return path


def _row_to_path(row: int) -> str:
    """行号转路线名。"""
    if row <= 1:
        return "上路"
    elif row <= 3:
        return "中路"
    elif row >= 4:
        return "下路"
    return ""


def _strip_enemy_id(eid: str) -> str:
    """从 enemy_id 提取可读名。"""
    if not eid:
        return ""
    parts = eid.replace("enemy_", "").split("_")
    return parts[-1] if parts else eid


def _load_enemy_names(handbook_path: str) -> dict[str, str]:
    """加载 enemy_id → 中文名 映射。"""
    if not handbook_path or not os.path.exists(handbook_path):
        return {}
    with open(handbook_path, encoding="utf-8") as f:
        h = json.load(f)
    result = {}
    ed = h.get("enemyData", {})
    if isinstance(ed, dict):
        for k, v in ed.items():
            name = v.get("name", "")
            if name:
                result[k] = name
    return result


def _load_enemy_stats(db_path: str) -> dict[str, dict]:
    """加载 enemy_id → 属性 映射。"""
    if not db_path or not os.path.exists(db_path):
        return {}
    with open(db_path, encoding="utf-8") as f:
        edb = json.load(f)
    result = {}
    for e in edb.get("enemies", []):
        key = e.get("Key", "")
        values = e.get("Value", [])
        if not isinstance(values, list) or not values:
            continue
        v0 = values[0]
        ed = v0.get("enemyData", {})
        attrs = ed.get("attributes", {})
        stats = {}
        for k in ("maxHp", "atk", "def", "magicResistance", "moveSpeed"):
            val = attrs.get(k)
            if isinstance(val, dict):
                stats[k] = val.get("m_value")
            elif isinstance(val, (int, float)):
                stats[k] = val
        result[key] = stats
    return result


if __name__ == "__main__":
    base = os.path.join(os.path.dirname(__file__), "..", "..", "data", "gamedata")
    level_path = os.path.join(base, "levels", "obt", "main", "level_main_01-07.json")
    handbook_path = os.path.join(base, "excel", "enemy_handbook_table.json")
    enemy_db_path = os.path.join(base, "levels", "enemydata", "enemy_database.json")

    tl = parse_level_json(level_path, handbook_path, enemy_db_path)
    print(tl.to_description())
    print()
    print("Total spawn events:", len(tl.actions))
