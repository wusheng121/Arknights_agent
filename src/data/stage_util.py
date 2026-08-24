"""关卡工具: stage code → 文件路径解析 + 自动下载。

主线: "1-7" → stageId "main_01-07"
活动: "OF-F1" → stages.json → "a003_f01_perm"
"""

from __future__ import annotations

import json
import os
import urllib.request

# ArknightsGameData CDN
_GAMEDATA_BASE = "https://cdn.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata"

# stages.json 缓存
_stages_cache: dict[str, str] | None = None


def _load_stages_map(maa_path: str) -> dict[str, str]:
    """加载 stages.json → {stage_code: stageId}。"""
    global _stages_cache
    if _stages_cache is not None:
        return _stages_cache
    path = os.path.join(maa_path, "resource", "stages.json")
    if not os.path.exists(path):
        _stages_cache = {}
        return _stages_cache
    with open(path, encoding="utf-8") as f:
        stages = json.load(f)
    _stages_cache = {s["code"]: s["stageId"] for s in stages if s.get("code") and s.get("stageId")}
    return _stages_cache


def stage_code_to_id(code: str, maa_path: str = "") -> str:
    """'1-7' → 'main_01-07', 'OF-F1' → stages.json 查找, 'AT-7' → 'act44side_07'。"""
    # 主线: 数字-数字
    parts = code.split("-")
    if len(parts) == 2:
        try:
            chapter, stage = int(parts[0]), int(parts[1])
            return f"main_{chapter:02d}-{stage:02d}"
        except ValueError:
            pass
    # 活动关卡: 查 stages.json
    if maa_path:
        sm = _load_stages_map(maa_path)
        if code in sm:
            return sm[code]
    # 活动别名映射 (显示码 → stageId)
    _ALIAS = {
        # 墟 (act44side)
        "AT-1": "act44side_01", "AT-2": "act44side_02", "AT-3": "act44side_03",
        "AT-4": "act44side_04", "AT-5": "act44side_05", "AT-6": "act44side_06",
        "AT-7": "act44side_07", "AT-8": "act44side_08",
    }
    if code in _ALIAS:
        return _ALIAS[code]
    return code


def get_tile_json_path(maa_path: str, stage_code: str) -> str:
    """MAA tile JSON 路径。"""
    sid = stage_code_to_id(stage_code, maa_path)
    tile_dir = os.path.join(maa_path, "resource", "Arknights-Tile-Pos")
    # 主线: {sid}-obt-main-level_{sid}.json
    main_path = os.path.join(tile_dir, f"{sid}-obt-main-level_{sid}.json")
    if os.path.exists(main_path):
        return main_path
    # 活动: 搜索以 sid 开头的文件(去掉 _perm 后缀)
    sid_clean = sid.replace("_perm", "")
    if os.path.isdir(tile_dir):
        for f in os.listdir(tile_dir):
            if f.startswith(sid_clean):
                return os.path.join(tile_dir, f)
    # fallback: 返回主线格式路径
    return main_path


def get_level_json_path(gamedata_path: str, stage_code: str, maa_path: str = "") -> str:
    """ArknightsGameData level JSON 路径。
    
    主线: levels/obt/main/level_main_XX-YY.json
    活动: 从 tile JSON 的 levelId 字段获取路径
    """
    sid = stage_code_to_id(stage_code, maa_path)
    # 主线
    if sid.startswith("main_"):
        return os.path.join(gamedata_path, "levels", "obt", "main", f"level_{sid}.json")
    # 活动: 从 tile JSON 读 levelId
    tile_path = get_tile_json_path(maa_path, stage_code)
    if os.path.exists(tile_path):
        import json
        with open(tile_path, encoding="utf-8") as f:
            tile_data = json.load(f)
        level_id = tile_data.get("levelId", "")
        if level_id:
            return os.path.join(gamedata_path, "levels", f"{level_id}.json")
    # fallback
    sid_clean = sid.replace("_perm", "")
    prefix = sid_clean.split("_")[0]
    return os.path.join(gamedata_path, "levels", "activities", prefix, f"level_{sid_clean}.json")


def get_cache_path(cache_dir: str, stage_code: str, maa_path: str = "") -> str:
    """作业缓存路径。"""
    sid = stage_code_to_id(stage_code, maa_path)
    return os.path.join(cache_dir, f"{sid}.json")


def ensure_level_json(gamedata_path: str, stage_code: str, maa_path: str = "") -> str | None:
    """确保 level JSON 存在,不存在则自动下载。返回路径或 None。"""
    level_path = get_level_json_path(gamedata_path, stage_code, maa_path)
    if os.path.exists(level_path):
        return level_path

    sid = stage_code_to_id(stage_code, maa_path)
    sid_clean = sid.replace("_perm", "")

    # 构造下载 URL
    if sid.startswith("main_"):
        url = f"{_GAMEDATA_BASE}/levels/obt/main/level_{sid}.json"
    else:
        # 从 level_path 提取 levelId 路径
        rel_path = os.path.relpath(level_path, gamedata_path).replace("\\", "/")
        url = f"{_GAMEDATA_BASE}/{rel_path}"

    print(f"[stage_util] 下载 level JSON: {url}")
    os.makedirs(os.path.dirname(level_path), exist_ok=True)
    # 先试 jsDelivr, 失败则用 raw.githubusercontent.com
    urls = [url]
    rel_path = os.path.relpath(level_path, gamedata_path).replace("\\", "/")
    urls.append(f"https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/{rel_path}")
    for try_url in urls:
        try:
            req = urllib.request.Request(try_url, headers={"User-Agent": "ArknightsAgent/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            with open(level_path, "wb") as f:
                f.write(data)
            print(f"[stage_util] 已下载 {len(data)} bytes → {level_path}")
            return level_path
        except Exception as e:
            print(f"[stage_util] 尝试 {try_url.split('/')[2]} 失败: {e}")
    print("[stage_util] 所有下载源失败")
    return None


def list_available_stages(maa_path: str) -> list[str]:
    """列出 MAA 中有 tile 数据的关卡代码(主线+活动)。"""
    sm = _load_stages_map(maa_path)
    # 返回所有在 stages.json 里有 tile 文件的关卡
    tile_dir = os.path.join(maa_path, "resource", "Arknights-Tile-Pos")
    if not os.path.isdir(tile_dir):
        return []
    tile_files = set(os.listdir(tile_dir))
    codes = []
    for code, sid in sm.items():
        sid_clean = sid.replace("_perm", "")
        # 检查 tile 文件是否存在
        found = any(f.startswith(sid_clean) for f in tile_files)
        if found:
            codes.append(code)
    codes.sort()
    return codes


if __name__ == "__main__":
    # 列出可用关卡
    maa = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"
    stages = list_available_stages(maa)
    print(f"MAA 有 {len(stages)} 个主线关卡 tile 数据")
    print("前 15 个:", stages[:15])
    print()
    # 测试自动下载
    for code in ["1-7", "2-1"]:
        sid = stage_code_to_id(code)
        print(f"{code} → {sid}")
        print(f"  tile: {get_tile_json_path(maa, code)}")
        print(f"  level: {get_level_json_path('data/gamedata', code)}")
        print()
