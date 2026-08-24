"""TileCalc2 Python 复现:格子(row,col)→屏幕坐标(x,y)。

按 MAA 源码 TileCalc2.hpp 的 3D 投影算法:
1. get_tile_world_pos(row, col) → 世界坐标
2. camera_pos(view, side) → 相机位置
3. camera_euler_angles(side) → 欧拉角
4. camera_matrix(pos, euler, ratio) → 4×4 矩阵(translate + rotateY + rotateX + projection)
5. world_to_screen(world_pos) → 屏幕(1280×720 基准)
6. 缩放 ×1.5 → 1920×1080

用法:
    from src.game.tile_calc import TileCalc
    tc = TileCalc("path/to/main_01-07-...json")
    x, y = tc.get_tile_screen_pos(4, 5)  # 桃金娘格子→屏幕坐标
    rx, ry = tc.get_retreat_screen_pos()  # 撤退按钮
    sx, sy = tc.get_skill_screen_pos()   # 技能按钮
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any


@dataclass
class Tile:
    heightType: int = 0
    buildableType: int = 0
    tileKey: str = ""


class Level:
    def __init__(self, data: dict) -> None:
        self.stageId: str = data.get("stageId", "")
        self.code: str = data.get("code", "")
        self.levelId: str = data.get("levelId", "")
        self.name: str = data.get("name", "")
        self.height: int = data.get("height", 0)
        self.width: int = data.get("width", 0)
        self.view: list[list[float]] = data.get("view", [])
        self.tiles: list[list[Tile]] = []
        for row in data.get("tiles", []):
            self.tiles.append([
                Tile(
                    heightType=t.get("heightType", 0),
                    buildableType=t.get("buildableType", 0),
                    tileKey=t.get("tileKey", ""),
                )
                for t in row
            ])

    def get_width(self) -> int:
        return self.width

    def get_height(self) -> int:
        return self.height

    def get_item(self, y: int, x: int) -> Tile:
        return self.tiles[y][x]


class TileCalc:
    DEGREE = math.pi / 180.0
    # 基准分辨率(MAA 算法用 1280x720)
    BASE_W = 1280
    BASE_H = 720
    # MuMu 1920x1080 = 1.5x
    SCALE_X = 1.5
    SCALE_Y = 1.5

    def __init__(self, tile_json_path: str, scale_x: float = 1.5, scale_y: float = 1.5) -> None:
        with open(tile_json_path, encoding="utf-8") as f:
            data = json.load(f)
        self.level = Level(data)
        self.SCALE_X = scale_x
        self.SCALE_Y = scale_y

    def _camera_pos(self, side: bool = False) -> list[float]:
        view = self.level.view[1 if side else 0]
        x, y, z = view[0], view[1], view[2]
        ratio = self.BASE_H / self.BASE_W
        from_ratio = 9.0 / 16.0
        to_ratio = 3.0 / 4.0
        t = (from_ratio - ratio) / (from_ratio - to_ratio)
        return [x + (-1.4 * t), y + (-2.8 * t), z + 0]

    def _camera_euler(self, side: bool = False) -> list[float]:
        if side:
            return [10 * self.DEGREE, 30 * self.DEGREE, 0]
        return [0, 30 * self.DEGREE, 0]

    def _camera_matrix(self, pos: list[float], euler: list[float], ratio: float) -> list[list[float]]:
        cos_y = math.cos(euler[0])
        sin_y = math.sin(euler[0])
        cos_x = math.cos(euler[1])
        sin_x = math.sin(euler[1])
        tan_f = math.tan(20 * self.DEGREE)
        far_c = 1000.0
        near_c = 0.3

        # translate
        tr = [
            [1, 0, 0, -pos[0]],
            [0, 1, 0, -pos[1]],
            [0, 0, 1, -pos[2]],
            [0, 0, 0, 1],
        ]
        # rotateY
        my = [
            [cos_y,  0, sin_y, 0],
            [0,      1, 0,     0],
            [-sin_y, 0, cos_y, 0],
            [0,      0, 0,     1],
        ]
        # rotateX
        mx = [
            [1, 0,      0,      0],
            [0, cos_x,  -sin_x, 0],
            [0, -sin_x, -cos_x, 0],
            [0, 0,      0,      1],
        ]
        # projection
        proj = [
            [ratio / tan_f, 0,         0, 0],
            [0,             1 / tan_f, 0, 0],
            [0,             0,         -(far_c + near_c) / (far_c - near_c), -(far_c * near_c * 2) / (far_c - near_c)],
            [0,             0,         -1, 0],
        ]
        # matrix = proj * mx * my * tr
        result = _mat_mul(proj, _mat_mul(mx, _mat_mul(my, tr)))
        return result

    def _world_to_screen(self, world_pos: list[float], side: bool = False) -> tuple[int, int]:
        pos_cam = self._camera_pos(side)
        euler = self._camera_euler(side)
        ratio = self.BASE_H / self.BASE_W
        matrix = self._camera_matrix(pos_cam, euler, ratio)
        # result = matrix * [x, y, z, 1]
        result = _mat_vec_mul(matrix, [world_pos[0], world_pos[1], world_pos[2], 1])
        # 归一化
        w = result[3]
        result = [r / w for r in result]
        result = [(r + 1) / 2 for r in result]
        x = round(result[0] * self.BASE_W) * self.SCALE_X
        y = round((1 - result[1]) * self.BASE_H) * self.SCALE_Y
        return int(x), int(y)

    def _get_tile_world_pos(self, tile_y: int, tile_x: int) -> list[float]:
        w = self.level.get_width()
        h = self.level.get_height()
        tile = self.level.get_item(tile_y, tile_x)
        return [
            tile_x - (w - 1) / 2.0,
            (h - 1) / 2.0 - tile_y,
            tile.heightType * -0.4,
        ]

    def get_tile_screen_pos(self, row: int, col: int, side: bool = False) -> tuple[int, int]:
        """格子(row, col)→屏幕坐标(x, y),已缩放到 1920x1080。"""
        world = self._get_tile_world_pos(row, col)
        return self._world_to_screen(world, side)

    # 撤退/技能按钮的相对位置(从 TileCalc2.hpp)
    REL_POS_X = 1.3143386840820312
    REL_POS_Y = 1.314337134361267
    REL_POS_Z = -0.3967874050140381

    def get_retreat_screen_pos(self, has_multi_stages: bool = False) -> tuple[int, int]:
        rel = [-self.REL_POS_X + (self.level.view[0][0] if has_multi_stages else 0), self.REL_POS_Y, self.REL_POS_Z]
        return self._world_to_screen(rel, side=True)

    def get_skill_screen_pos(self, has_multi_stages: bool = False) -> tuple[int, int]:
        rel = [self.REL_POS_X + (self.level.view[0][0] if has_multi_stages else 0), -self.REL_POS_Y, self.REL_POS_Z]
        return self._world_to_screen(rel, side=True)

    def get_deployable_tiles(self) -> list[tuple[int, int, Tile]]:
        """返回所有可部署格子 [(row, col, Tile)],buildableType 1=地面 2=高台。"""
        result = []
        for y in range(self.level.get_height()):
            for x in range(self.level.get_width()):
                t = self.level.get_item(y, x)
                if t.buildableType in (1, 2):
                    result.append((y, x, t))
        return result


def _mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """4×4 矩阵乘法。"""
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _mat_vec_mul(m: list[list[float]], v: list[float]) -> list[float]:
    """4×4 矩阵 × 4 向量。"""
    return [sum(m[i][j] * v[j] for j in range(4)) for i in range(4)]


if __name__ == "__main__":
    import sys
    tile_path = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\resource\Arknights-Tile-Pos\main_01-07-obt-main-level_main_01-07.json"
    tc = TileCalc(tile_path)
    print(f"关卡: {tc.level.code} ({tc.level.name}), {tc.level.width}x{tc.level.height}")
    print(f"view: {tc.level.view}")
    print(f"\n可部署格子({len(tc.get_deployable_tiles())}个):")
    for row, col, tile in tc.get_deployable_tiles():
        x, y = tc.get_tile_screen_pos(row, col)
        print(f"  ({col},{row}) {tile.tileKey} build={tile.buildableType} → 屏幕({x},{y})")
    rx, ry = tc.get_retreat_screen_pos()
    sx, sy = tc.get_skill_screen_pos()
    print(f"\n撤退按钮: ({rx},{ry})")
    print(f"技能按钮: ({sx},{sy})")
