"""RAG: prts.wiki 攻略爬取 + 索引。

从 prts.wiki 爬取关卡攻略页面,提取:
- 敌方情报(敌人名/数量/级别)
- 关卡元信息(部署上限/初始费用/目标点耐久)
- 攻略文本(如果有)

存储为 data/wiki/<stage_code>.txt,供 RAG 检索用。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

WIKI_API = "https://prts.wiki/api.php"
WIKI_BASE = "https://prts.wiki"

# 缓存目录
WIKI_CACHE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "wiki"
)


def fetch_stage_page(stage_code: str) -> str:
    """从 prts.wiki 爬取关卡页面 wikitext。

    stage_code: 关卡代码如 "1-7", "AT-7"
    """
    # 先尝试直接用 stage code 作为页面名
    params = urllib.parse.urlencode({
        "action": "parse",
        "page": stage_code,
        "prop": "wikitext",
        "format": "json",
        "redirects": "1",
    })
    url = f"{WIKI_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "ArknightsAgent/1.0"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception:
        return ""

    wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")
    if not wikitext:
        return ""

    # 如果是重定向,跟随
    if wikitext.startswith("#redirect") or wikitext.startswith("#重定向"):
        m = re.search(r"\[\[(.+?)[\]\]|]", wikitext)
        if m:
            redirect_target = m.group(1).strip()
            return fetch_stage_page(redirect_target)

    return wikitext


def parse_wikitext(wikitext: str) -> dict:
    """解析 wikitext,提取有用信息。"""
    result = {
        "enemies": [],
        "stage_info": {},
        "guide_text": "",
    }

    # 提取敌方情报
    enemy_match = re.search(r"\{\{敌方情报(.+?)\}\}", wikitext, re.DOTALL)
    if enemy_match:
        enemy_block = enemy_match.group(1)
        enemies = re.findall(r"\|敌人(\d+)=(.+?)\|敌人\1数量=(\d+)", enemy_block)
        for _, name, count in enemies:
            result["enemies"].append({"name": name.strip(), "count": int(count)})

    # 提取关卡信息
    info_match = re.search(r"\{\{(?:普通|突袭)关卡信息(.+?)\}\}", wikitext, re.DOTALL)
    if info_match:
        info_block = info_match.group(1)
        for field in ["关卡id", "敌人数量", "地图大小", "最短用时", "部署上限", "初始COST", "目标点耐久"]:
            m = re.search(rf"\|{field}=(.+?)(?:\||\}})", info_block)
            if m:
                result["stage_info"][field] = m.group(1).strip()

    # 提取攻略文本(如果有攻略模板)
    guide_match = re.search(r"\{\{攻略(.+?)\}\}", wikitext, re.DOTALL)
    if guide_match:
        result["guide_text"] = guide_match.group(1)[:500]

    # 如果没有专门攻略模板,提取清理后的文本
    if not result["guide_text"]:
        text = wikitext
        # 清理 wikitext 标记
        text = re.sub(r"\{\{[^}]*\}\}", " ", text)  # 模板
        text = re.sub(r"\[\[(.+?)[\]\|].*?\]\]", r"\1", text)  # 链接
        text = re.sub(r"\{\||\|\}|\|-|\|\+", " ", text)  # 表格
        text = re.sub(r"==+([^=]+)==+", r"\n[\1]", text)  # 标题
        text = re.sub(r"<[^>]+>", " ", text)  # HTML
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()
        if text:
            result["guide_text"] = text[:800]

    return result


def crawl_stage(stage_code: str) -> dict | None:
    """爬取单个关卡攻略,缓存到本地。"""
    # 检查缓存
    cache_path = os.path.join(WIKI_CACHE_DIR, f"{stage_code}.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    # 爬取
    wikitext = fetch_stage_page(stage_code)
    if not wikitext:
        return None

    parsed = parse_wikitext(wikitext)
    parsed["stage_code"] = stage_code
    parsed["wikitext_length"] = len(wikitext)

    # 缓存
    os.makedirs(WIKI_CACHE_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    return parsed


def crawl_stages_batch(stage_codes: list[str]) -> dict[str, dict]:
    """批量爬取多个关卡。"""
    results = {}
    for code in stage_codes:
        data = crawl_stage(code)
        if data:
            results[code] = data
            enemies = len(data.get("enemies", []))
            print(f"  {code}: {enemies} enemies, guide={len(data.get('guide_text', ''))} chars")
        else:
            print(f"  {code}: not found")
    return results


def get_stage_guide(stage_code: str) -> str:
    """获取关卡的攻略文本(供 RAG 检索用)。

    返回紧凑文本:
    "1-7 攻略: 敌人: 源石虫×23/暴徒×4/士兵×7... 部署上限8 初始费用10..."
    """
    data = crawl_stage(stage_code)
    if not data:
        return ""

    parts = []
    # 敌人信息
    enemies = data.get("enemies", [])
    if enemies:
        enemy_str = "敌人: " + ", ".join(f"{e['name']}×{e['count']}" for e in enemies)
        parts.append(enemy_str)

    # 关卡信息
    info = data.get("stage_info", {})
    if info:
        info_str = " / ".join(f"{k}={v}" for k, v in info.items())
        parts.append(info_str)

    # 攻略文本
    guide = data.get("guide_text", "")
    if guide:
        parts.append("攻略: " + guide[:300])

    return " | ".join(parts) if parts else ""


if __name__ == "__main__":
    # 测试爬取常用关卡
    stages = ["1-7", "1-1", "AT-7"]
    print("爬取 prts.wiki 攻略:")
    results = crawl_stages_batch(stages)
    print()
    for code, data in results.items():
        guide = get_stage_guide(code)
        print(f"{code}: {guide[:150]}")
