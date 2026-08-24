"""地图数据解析:tile JSON → 文字描述(给 DeepSeek 用)。

从 MAA Arknights-Tile-Pos JSON 解析:
- 红门(tile_start): 敌人来路
- 蓝门(tile_end): 己方据点
- 地面可部署(buildableType=1)
- 高台可部署(buildableType=2)
- 敌人方向(根据红门/蓝门位置推断)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class MapInfo:
    """关卡地图信息。"""
    code: str = ""
    name: str = ""
    width: int = 0
    height: int = 0
    red_doors: list[tuple[int, int]] = None  # 红门(敌人来路)
    blue_doors: list[tuple[int, int]] = None  # 蓝门(己方据点)
    melee_tiles: list[tuple[int, int]] = None  # 地面可部署
    ranged_tiles: list[tuple[int, int]] = None  # 高台可部署
    enemy_direction: str = ""  # 敌人方向

    def __post_init__(self):
        if self.red_doors is None:
            self.red_doors = []
        if self.blue_doors is None:
            self.blue_doors = []
        if self.melee_tiles is None:
            self.melee_tiles = []
        if self.ranged_tiles is None:
            self.ranged_tiles = []

    def to_description(self) -> str:
        """转文字描述(给 DeepSeek 喂)。"""
        lines = [
            f'地图 {self.code} "{self.name}" {self.width}x{self.height}',
            f"红门(敌人来路): {self.red_doors}",
            f"蓝门(己方据点): {self.blue_doors}",
            f"敌人方向: {self.enemy_direction}",
            f"地面可部署({len(self.melee_tiles)}): {self.melee_tiles}",
            f"高台可部署({len(self.ranged_tiles)}): {self.ranged_tiles}",
        ]
        return "\n".join(lines)


def parse_tile_json(path: str) -> MapInfo:
    """解析 MAA tile JSON → MapInfo。"""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    tiles = data["tiles"]
    height = data["height"]
    width = data["width"]

    info = MapInfo(
        code=data.get("code", ""),
        name=data.get("name", ""),
        width=width,
        height=height,
    )

    for row in range(height):
        for col in range(width):
            t = tiles[row][col]
            key = t["tileKey"]
            bt = t["buildableType"]
            if key == "tile_start":
                info.red_doors.append((col, row))
            elif key == "tile_end":
                info.blue_doors.append((col, row))
            elif bt == 1:
                info.melee_tiles.append((col, row))
            elif bt == 2:
                info.ranged_tiles.append((col, row))

    # 敌人方向
    if info.red_doors and info.blue_doors:
        avg_red_x = sum(d[0] for d in info.red_doors) / len(info.red_doors)
        avg_blue_x = sum(d[0] for d in info.blue_doors) / len(info.blue_doors)
        if avg_red_x > avg_blue_x:
            info.enemy_direction = "从右向左"
        elif avg_red_x < avg_blue_x:
            info.enemy_direction = "从左向右"
        else:
            info.enemy_direction = "不明"

    return info


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\resource\Arknights-Tile-Pos\main_01-07-obt-main-level_main_01-07.json"
    info = parse_tile_json(path)
    print(info.to_description())
