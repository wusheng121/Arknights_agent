"""数据层:干员属性(职业/稀有度/位置/范围)+ 部署费用。

优先从 MAA 本地 battle_data.json 获取(职业/位置/稀有度/范围/分支),
MAA 没有的(部署费用)从 prts.wiki 补。

数据结构参考 MAA AsstBattleDef.h::OperProps:
  id, name, name_en/jp/kr/tw, role(职业), ranges[3], rarity, location_type, tokens
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

MAA_RES = os.getenv("MAA_RESOURCE_PATH", r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\resource")


@dataclass
class OperData:
    """干员属性数据。"""
    id: str = ""
    name: str = ""
    name_en: str = ""
    role: str = ""          # Pioneer/Warrior/Tank/Sniper/Caster/Medic/Support/Special/Drone
    rarity: int = 0         # 1-6
    position: str = ""      # MELEE/RANGED
    location_type: str = "" # Melee/Ranged
    ranges: list[str] = field(default_factory=list)  # 攻击范围 ID
    sub_profession: str = ""# 分支 ID
    cost: int = -1          # 部署费用(MAA 无,从 prts.wiki 补)
    skill: int = 0         # 技能序号
    skill_usage: int = 0   # 技能用法


PROFESSION_TO_ROLE = {
    "PIONEER": "Pioneer", "WARRIOR": "Warrior", "TANK": "Tank",
    "SNIPER": "Sniper", "CASTER": "Caster", "MEDIC": "Medic",
    "SUPPORT": "Support", "SPECIAL": "Special", "DRONE": "Drone",
}

POSITION_TO_LOCATION = {
    "MELEE": "Melee", "RANGED": "Ranged",
}


class OperDatabase:
    """干员数据库(从 MAA battle_data.json 加载 + prts.wiki 补费用)。"""

    def __init__(self) -> None:
        self._opers: dict[str, OperData] = {}  # name -> OperData
        self._opers_by_id: dict[str, OperData] = {}  # id -> OperData
        self._loaded = False

    def load_from_maa(self, battle_data_path: str | None = None) -> int:
        """从 MAA battle_data.json 加载干员数据。

        Returns: 加载的干员数量。
        """
        if battle_data_path is None:
            battle_data_path = os.path.join(MAA_RES, "battle_data.json")

        if not os.path.exists(battle_data_path):
            return 0

        with open(battle_data_path, encoding="utf-8") as f:
            data = json.load(f)

        chars = data.get("chars", {})
        for char_id, char_data in chars.items():
            name = char_data.get("name", "")
            if not name:
                continue

            profession = char_data.get("profession", "")
            role = PROFESSION_TO_ROLE.get(profession, "Unknown")
            position = char_data.get("position", "")
            location_type = POSITION_TO_LOCATION.get(position, "None")

            oper = OperData(
                id=char_id,
                name=name,
                name_en=char_data.get("name_en", ""),
                role=role,
                rarity=char_data.get("rarity", 0),
                position=position,
                location_type=location_type,
                ranges=char_data.get("rangeId", []),
                sub_profession=char_data.get("subProfessionId", ""),
                cost=-1,  # MAA 无费用
            )

            self._opers[name] = oper
            self._opers_by_id[char_id] = oper

        self._loaded = True
        return len(self._opers)

    def load_cost_from_file(self, cost_path: str) -> int:
        """从本地费用文件加载部署费用(prts.wiki 爬取后存)。

        文件格式: {"桃金娘": 8, "德克萨斯": 10, ...}
        """
        if not os.path.exists(cost_path):
            return 0

        with open(cost_path, encoding="utf-8") as f:
            cost_data = json.load(f)

        count = 0
        for name, cost in cost_data.items():
            if name in self._opers:
                self._opers[name].cost = int(cost)
                count += 1

        return count

    def find_oper(self, name: str) -> OperData | None:
        """按名查找干员。"""
        return self._opers.get(name)

    def find_oper_by_id(self, char_id: str) -> OperData | None:
        """按 ID 查找干员。"""
        return self._opers_by_id.get(char_id)

    def get_role(self, name: str) -> str:
        """获取干员职业。"""
        op = self._opers.get(name)
        return op.role if op else "Unknown"

    def get_rarity(self, name: str) -> int:
        """获取干员稀有度。"""
        op = self._opers.get(name)
        return op.rarity if op else 0

    def get_cost(self, name: str) -> int:
        """获取部署费用。"""
        op = self._opers.get(name)
        return op.cost if op else -1

    def get_location_type(self, name: str) -> str:
        """获取部署位置类型(Melee/Ranged)。"""
        op = self._opers.get(name)
        return op.location_type if op else "None"

    def get_all_names(self) -> list[str]:
        """获取所有干员名。"""
        return list(self._opers.keys())

    def get_opers_by_role(self, role: str) -> list[OperData]:
        """按职业筛选干员。"""
        return [op for op in self._opers.values() if op.role == role]

    def get_opers_by_rarity(self, rarity: int) -> list[OperData]:
        """按稀有度筛选干员。"""
        return [op for op in self._opers.values() if op.rarity == rarity]

    def to_dict(self, name: str) -> dict:
        """转 dict(给 DeepSeek 用)。"""
        op = self._opers.get(name)
        if op is None:
            return {}
        return {
            "name": op.name,
            "role": op.role,
            "rarity": op.rarity,
            "location_type": op.location_type,
            "cost": op.cost,
            "sub_profession": op.sub_profession,
        }

    def filter_for_llm(self, oper_names: list[str] | None = None, limit: int = 40) -> list[dict]:
        """筛选干员列表给 LLM(按稀有度+名筛选,限制数量)。

        oper_names: 若提供,只包含这些干员(如用户拥有的);否则全部。
        limit: 最多返回数量。
        """
        if oper_names:
            opers = [self._opers[n] for n in oper_names if n in self._opers]
        else:
            opers = list(self._opers.values())

        # 按稀有度降序
        opers.sort(key=lambda o: (-o.rarity, o.name))

        return [
            {"name": o.name, "role": o.role, "rarity": o.rarity,
             "location_type": o.location_type, "cost": o.cost}
            for o in opers[:limit]
        ]


# 全局单例
_db: OperDatabase | None = None


def get_database() -> OperDatabase:
    """获取全局干员数据库单例。"""
    global _db
    if _db is None:
        _db = OperDatabase()
        _db.load_from_maa()
    return _db


if __name__ == "__main__":
    db = OperDatabase()
    n = db.load_from_maa()
    print(f"从 MAA 加载 {n} 个干员")

    # 测试查询
    for name in ["桃金娘", "德克萨斯", "史尔特尔", "维什戴尔", "艾雅法拉", "夜莺", "塞雷娅"]:
        op = db.find_oper(name)
        if op:
            print(f"  {name}: role={op.role} rarity={op.rarity} loc={op.location_type} cost={op.cost} sub={op.sub_profession}")
        else:
            print(f"  {name}: 未找到")

    # 统计
    print(f"\n按职业统计:")
    for role in ["Pioneer", "Warrior", "Tank", "Sniper", "Caster", "Medic", "Support", "Special", "Drone"]:
        opers = db.get_opers_by_role(role)
        print(f"  {role}: {len(opers)} 个")
