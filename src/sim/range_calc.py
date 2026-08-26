"""Arknights mini simulator - range tile calculation.

从 MAA battle_data.json 的 ranges[rangeId] 获取相对坐标,
根据干员位置和朝向计算实际覆盖的 tile 坐标。
"""

from __future__ import annotations

import json
import os

MAA = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"

# 朝向旋转矩阵: (dx, dy) → 旋转后的 (dx, dy)
# Right: 不旋转
# Left: x轴翻转
# Up: 90度逆时针 (dx,dy) → (dy,-dx)
# Down: 90度顺时针 (dx,dy) → (-dy,dx)
_FACING_ROTATION = {
    "Right": lambda dx, dy: (dx, dy),
    "Left": lambda dx, dy: (-dx, dy),
    "Up": lambda dx, dy: (dy, -dx),
    "Down": lambda dx, dy: (-dy, dx),
    "None": lambda dx, dy: (dx, dy),
}


def get_range_tiles(operator_name: str) -> list[tuple[int, int]]:
    """获取干员的攻击范围相对坐标列表。

    Returns: [(dx, dy), ...] 相对于干员位置的偏移。
    """
    bd_path = os.path.join(MAA, "resource", "battle_data.json")
    with open(bd_path, encoding="utf-8") as f:
        bd = json.load(f)

    chars = bd.get("chars", {})
    ranges = bd.get("ranges", {})

    for k, v in chars.items():
        if v.get("name") == operator_name:
            rids = v.get("rangeId", [])
            if rids:
                rid = rids[-1]  # 精二范围
                raw = ranges.get(rid, [])
                return [(int(t[0]), int(t[1])) for t in raw]
            break
    return []


def calc_range_tiles(
    pos: tuple[int, int],
    facing: str,
    range_shape: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """计算干员从 pos 朝 facing 方向,覆盖哪些 tile 坐标。

    Args:
        pos: (col, row) 干员位置
        facing: "Right"|"Left"|"Up"|"Down"|"None"
        range_shape: [(dx, dy), ...] 相对坐标

    Returns: [(col, row), ...] 绝对坐标列表
    """
    rot = _FACING_ROTATION.get(facing, _FACING_ROTATION["None"])
    col, row = pos
    result = []
    for dx, dy in range_shape:
        rdx, rdy = rot(dx, dy)
        result.append((col + rdx, row + rdy))
    return result


def tiles_in_range(
    pos: tuple[int, int],
    facing: str,
    operator_name: str,
    valid_tiles: set[tuple[int, int]] = None,
) -> list[tuple[int, int]]:
    """计算干员攻击范围内的有效 tile。

    Args:
        pos: 干员位置
        facing: 朝向
        operator_name: 干员名(用于查范围)
        valid_tiles: 如果提供,只返回在此集合内的 tile

    Returns: 覆盖的 tile 坐标列表
    """
    shape = get_range_tiles(operator_name)
    if not shape:
        return []
    absolute = calc_range_tiles(pos, facing, shape)
    if valid_tiles is not None:
        absolute = [t for t in absolute if t in valid_tiles]
    return absolute


if __name__ == "__main__":
    # Test: 维什戴尔 at (8,1) facing Down
    shape = get_range_tiles("维什戴尔")
    print("维什戴尔 range shape:", shape)
    print()
    
    for facing in ["Right", "Left", "Up", "Down"]:
        tiles = calc_range_tiles((8, 1), facing, shape)
        print("  (8,1) %s: %s" % (facing, tiles))
    
    print()
    # Test: 夜莺 at (2,4) facing Right
    shape2 = get_range_tiles("夜莺")
    print("夜莺 range shape:", shape2)
    tiles2 = calc_range_tiles((2, 4), "Right", shape2)
    print("  (2,4) Right:", tiles2)
    print("  ↑ check if covers (5,4) and (6,4):", 
          (5,4) in tiles2, (6,4) in tiles2)
