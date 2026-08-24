"""从 prts.wiki 爬取干员部署费用(cost)。

prts.wiki 是明日方舟中文 Wiki(MediaWiki)。
干员页 URL: https://prts.wiki/w/干员名
费用在干员页的 infobox 数据表中。

爬取后存为 cost.json: {"桃金娘": 8, "德克萨斯": 10, ...}
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

WIKI_BASE = "https://prts.wiki"
WIKI_API = f"{WIKI_BASE}/api.php"

# 常用干员费用(手动维护 + prts.wiki 补充)
# 格式: name -> cost
KNOWN_COSTS: dict[str, int] = {
    # 先锋
    "桃金娘": 8, "德克萨斯": 10, "伊内丝": 10, "伺夜": 10, "夜半": 9,
    "推进之王": 10, "风笛": 10, "嵯峨": 10, "焰尾": 10, "琴柳": 10,
    "芬": 8, "香草": 9, "翎羽": 5, "红豆": 9, "贾维": 9, "凛锋": 9,
    "清道夫": 9, "德克萨斯(剑豪)": 10, "仇白": 10,
    # 近卫
    "史尔特尔": 20, "银灰": 18, "煌": 22, "陈": 22, "棘刺": 18,
    "山": 16, "耀骑士临光": 22, "玛恩纳": 20, "号角": 22,
    "芙兰卡": 17, "幽灵鲨": 17, "布洛卡": 18, "星极": 17,
    "柏喙": 15, "刻刀": 15, "断崖": 17, "拉普兰德": 17,
    "霜叶": 15, "艾斯黛尔": 16, "慕斯": 15, "玫兰莎": 13,
    # 重装
    "塞雷娅": 18, "星熊": 20, "瑕光": 18, "泥岩": 20, "森蚺": 20,
    "古米": 12, "临光": 15, "雷蛇": 18, "坚雷": 12,
    "角峰": 15, "可颂": 16, "蛇屠箱": 16, "泡泡": 14,
    # 狙击
    "维什戴尔": 20, "能天使": 12, "蓝毒": 12, "白金": 13,
    "空弦": 12, " ash": 13, "黑": 16, "鸿雪": 12,
    "克洛丝": 7, "安德切尔": 7, "流星": 8, "梅": 9,
    "白雪": 12, "陨星": 13, "慑砂": 13, "送葬人": 14,
    # 术师
    "艾雅法拉": 20, "伊芙利特": 18, "莫斯提": 22, "刻俄柏": 18,
    "阿米娅": 20, "夜烟": 12, "远山": 12, "格雷伊": 12,
    "深靛": 11, "特米米": 18, "雪隙": 12,
    # 医疗
    "夜莺": 16, "白面鸮": 16, "闪灵": 16, "华法琳": 14,
    "嘉维尔": 12, "芙蓉": 11, "安赛尔": 11, "褐果": 11,
    # 辅助
    "安洁莉娜": 16, "铃兰": 13, "麦哲伦": 20, "傀影": 16,
    # 特种
    "红": 8, "槐琥": 10, "狮蝎": 8, "崖心": 13,
    "阿消": 8, "砾": 6, "温蒂": 16, "乌有": 8,
}


def fetch_oper_cost(name: str) -> int:
    """从 prts.wiki 爬取单个干员的部署费用。

    通过 MediaWiki API 获取干员页源码,正则提取费用。
    """
    # 优先用已知费用
    if name in KNOWN_COSTS:
        return KNOWN_COSTS[name]

    try:
        # MediaWiki API: 获取页面 wikitext
        params = urllib.parse.urlencode({
            "action": "parse",
            "page": name,
            "prop": "wikitext",
            "format": "json",
        })
        url = f"{WIKI_API}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "ArknightsAgent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        wikitext = data.get("parse", {}).get("wikitext", {}).get("*", "")

        # 正则提取部署费用
        # prts.wiki infobox 格式: |费用=8 或 |部署费用=8 或 {{干员/费用|8}}
        patterns = [
            r"\|\s*费用\s*=\s*(\d+)",
            r"\|\s*部署费用\s*=\s*(\d+)",
            r"费用.*?(\d+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, wikitext)
            if m:
                return int(m.group(1))

    except Exception:
        pass

    return -1


def crawl_costs(names: list[str], output_path: str = "cost.json", batch: int = 50) -> dict[str, int]:
    """批量爬取干员费用,存到 JSON。

    names: 要爬的干员名列表
    output_path: 输出 JSON 路径
    batch: 每 batch 个暂停(避免请求过快)
    """
    costs: dict[str, int] = {}

    # 先用已知费用
    for name in names:
        if name in KNOWN_COSTS:
            costs[name] = KNOWN_COSTS[name]

    # 剩余的从 wiki 爬
    to_crawl = [n for n in names if n not in costs]
    print(f"已知费用: {len(costs)}, 需爬取: {len(to_crawl)}")

    for i, name in enumerate(to_crawl):
        cost = fetch_oper_cost(name)
        if cost > 0:
            costs[name] = cost
            print(f"  {name}: {cost}")
        else:
            print(f"  {name}: 未获取")

        # 每 batch 个保存一次
        if (i + 1) % batch == 0:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(costs, f, ensure_ascii=False, indent=2)
            print(f"  已保存 {len(costs)} 个到 {output_path}")

    # 最终保存
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(costs, f, ensure_ascii=False, indent=2)
    print(f"完成: {len(costs)} 个费用存到 {output_path}")

    return costs


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    from src.data.oper_database import OperDatabase

    db = OperDatabase()
    n = db.load_from_maa()
    print(f"MAA 加载 {n} 个干员")

    # 爬取所有干员费用
    names = db.get_all_names()
    costs = crawl_costs(names, output_path="cost.json")

    # 加载到数据库
    loaded = db.load_cost_from_file("cost.json")
    print(f"费用加载: {loaded} 个")

    # 验证
    for name in ["桃金娘", "德克萨斯", "史尔特尔", "维什戴尔", "艾雅法拉", "夜莺", "塞雷娅"]:
        op = db.find_oper(name)
        if op:
            print(f"  {name}: role={op.role} rarity={op.rarity} cost={op.cost}")
