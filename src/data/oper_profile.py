"""干员完整特性提取器: character_table.json → 紧凑特性描述。

从 ArknightsGameData 提取干员的完整信息,输出给 DeepSeek:
  桃金娘 PIONEER/bearer(执旗手) 天赋:浮光跃金(先锋每秒回25血)
  特性:技能发动期间阻挡数变为0
  技能1:支援号令·β型 停止攻击回费 CD31s 持续8s
  技能2:治愈之翼 停止攻击回费+治疗 CD35s 持续16s
"""

from __future__ import annotations

import json
import os
import re

# subProfessionId → 中文分支名
SUB_PROFESSION_MAP = {
    # Pioneer
    "pioneer": "冲锋手", "bearer": "执旗手", "tactician": "战术家",
    "agent": "情报官", "chainhealer": "链愈师",
    # Warrior
    "lord": "领主", "centurion": "重装手", "artsfghter": "术战者",
    "mguard": "卫士", "fighter": "斗士", "sword": "剑客",
    "fearful": "战惧者", "librator": "解放者", "reaper": "收割者",
    "groundc": "陷阱师", "traper": "布阵师", "instructor": "教官",
    # Tank
    "protector": "铁卫", "guardian": "守护者", "unyield": "不屈者",
    "duelist": "决斗者", "shifter": "位移战车",
    # Sniper
    "sniper": "神射手", "recon": "游侠", "bombarder": "投掷手",
    "hunter": "猎手", "heavyshoot": "重射手", "fastshot": "快射手",
    "siege": "攻城手", "annihilator": "歼灭者", "spreadshooter": "散射者",
    "flood": "神射炮手", "hunterrep": "重复猎手",
    # Caster
    "corecaster": "中坚术师", "splashcaster": "扩散术师",
    "funnelcaster": "驭械术师", "phalanx": "阵法术师",
    "mystic": "秘术师", "chain": "链术师", "soulchain": "驭子术师",
    "executor": "术师", "prim caster": "中坚术师",
    # Medic
    "physician": "单体医疗", "ringhealer": "群愈师",
    "wandermedic": "疗愈师", "incantationmedic": "咒言师",
    "chainhealer": "链愈师",
    # Support
    "slower": "减速师", "sluggish": "迟缓师", "bard": "吟游者",
    "ritualist": "巫术师", "summoner": "召唤师", "bounty": " bounty hunter",
    # Special
    "pusher": "推击手", "hooker": "拉击手", "geek": "伏击客",
    "merchant": "行商", "executor": "处决者", "trickmaster": "陷阱师",
    "scout": "侦察者", "soulstealer": "傀儡师",
}

_char_cache: dict = None
_skill_cache: dict = None


def _load_char_table(path: str = "") -> dict:
    global _char_cache
    if _char_cache is not None:
        return _char_cache
    if not path:
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "gamedata",
            "excel", "character_table.json"
        )
    with open(path, encoding="utf-8") as f:
        _char_cache = json.load(f)
    return _char_cache


def _load_skill_table(path: str = "") -> dict:
    global _skill_cache
    if _skill_cache is not None:
        return _skill_cache
    if not path:
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "gamedata",
            "excel", "skill_table.json"
        )
    with open(path, encoding="utf-8") as f:
        _skill_cache = json.load(f)
    return _skill_cache


def _clean_text(text: str) -> str:
    """清理富文本标签和变量占位符。"""
    if not text:
        return ""
    text = re.sub(r"<@ba\.[^>]+>|</>", "", text)
    text = re.sub(r"<\$ba\.[^>]+>", "", text)
    text = re.sub(r"\{[^}]+\}", "X", text)
    text = text.replace("\\n", " ").strip()
    return text


def _get_range_desc(operator_name: str) -> str:
    """从 MAA battle_data.json 获取攻击范围描述。"""
    try:
        import json as _json
        maa_path = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\resource\battle_data.json"
        with open(maa_path, encoding="utf-8") as f:
            bd = _json.load(f)
        # Find operator by name in chars
        chars = bd.get("chars", {})
        range_ids = None
        for k, v in chars.items():
            if v.get("name") == operator_name:
                range_ids = v.get("rangeId", [])
                break
        if not range_ids:
            return "?"
        # Use elite 2 range (index 2) or fallback
        rid = range_ids[2] if len(range_ids) > 2 else (range_ids[0] if range_ids else "")
        ranges = bd.get("ranges", {})
        r = ranges.get(rid, [])
        tiles = len(r)
        if tiles == 0:
            return "?"
        cols = [t[0] for t in r]
        max_col = max(cols) if cols else 0
        width = len(set(t[1] for t in r))
        if tiles >= 15:
            return "大(%d格%d行)" % (tiles, width)
        elif tiles >= 8:
            return "中(%d格%d行)" % (tiles, width)
        elif tiles >= 3:
            return "小(%d格)" % tiles
        else:
            return "近(%d格)" % tiles
    except Exception:
        return "?"


def _detect_combat_role(profession: str, sub_profession: str, skills: list, skill_data: dict, tags: list, trait_desc: str) -> str:
    """检测干员战斗角色: lane_holder(站场) / burst_only(仅爆发) / utility(工具人) / support(支援)."""
    
    # 解放者: 常态不攻击不阻挡 → burst_only (如玛恩纳)
    if sub_profession == "librator":
        return "burst_only"
    
    # 处决者/行商: 快速复活/费用递减 → utility (如砾/红/槐琥)
    if sub_profession in ("executor", "merchant"):
        return "utility"
    
    # 术战者: 检查是否有"逐渐流失生命"或"强制退出" → burst_only (如史尔特尔黄昏)
    # 但不开黄昏的史尔特尔也能站场,所以标 burst_only 不完全准确
    # 改为: 检查技能是否有撤退机制
    has_retreat_skill = False
    for s_ref in skills:
        sid = s_ref.get("skillId", "")
        if sid in skill_data:
            levels = skill_data[sid].get("levels", [])
            if levels:
                desc = _clean_text(levels[0].get("description", ""))
                if "逐渐流失生命" in desc or "强制退出" in desc:
                    has_retreat_skill = True
                    break
    
    # 标签含"爆发"且无持续输出能力 → burst_only
    if has_retreat_skill:
        # 有撤退技能: skill_usage=1 会自动开,打完会撤退 → 不能长期站场
        return "burst_only"
    
    # 重装 → tank
    if profession == "TANK":
        return "tank"
    
    # 医疗 → support
    if profession == "MEDIC":
        return "support"
    
    # 先锋 → support (回费)
    if profession == "PIONEER":
        # 但情报官/情报官可以站场侦察
        return "support"
    
    # 辅助 → support
    if profession == "SUPPORT":
        return "support"
    
    # 特种(非处决者) → utility
    if profession == "SPECIAL":
        return "utility"
    
    # 标签含"爆发"但有持续普攻 → lane_holder (如维什戴尔)
    # 其余近卫/狙击/术师 → lane_holder (有持续普攻,能站场)
    return "lane_holder"


def get_operator_profile(name: str, char_path: str = "", skill_path: str = "") -> str:
    """获取单个干员的完整特性描述。

    Returns:
        "桃金娘 PIONEER/执旗手 天赋:浮光跃金(先锋每秒回25血) 特性:技能期间阻挡数变0
         技能1:支援号令·β型 停止攻击回费 CD31s 持续8s
         技能2:治愈之翼 停止攻击回费+治疗 CD35s 持续16s"
    """
    char_data = _load_char_table(char_path)
    skill_data = _load_skill_table(skill_path)

    char_id = None
    for k, v in char_data.items():
        if v.get("name") == name:
            char_id = k
            break
    if not char_id:
        return ""

    char = char_data[char_id]
    prof = char.get("profession", "")
    sub = char.get("subProfessionId", "")
    sub_cn = SUB_PROFESSION_MAP.get(sub, sub)
    tags = char.get("tagList", [])

    # 检测战斗角色
    trait_desc_text = ""
    trait = char.get("trait", {})
    if isinstance(trait, dict):
        candidates = trait.get("candidates", [])
        if candidates:
            trait_desc_text = _clean_text(candidates[-1].get("description", ""))
    combat_role = _detect_combat_role(prof, sub, char.get("skills", []), skill_data, tags, trait_desc_text)

    # 获取阻挡数和部署费用(从 phases 精二数据)
    block = 0
    deploy_cost = 0
    phases = char.get("phases", [])
    if len(phases) >= 3:
        attrs = phases[2].get("attributesKeyFrames", [])
        if attrs:
            data = attrs[-1].get("data", {})
            block = data.get("blockCnt", 0) or 0
            deploy_cost = data.get("cost", 0) or 0
    elif phases:
        attrs = phases[0].get("attributesKeyFrames", [])
        if attrs:
            data = attrs[-1].get("data", {})
            block = data.get("blockCnt", 0) or 0
            deploy_cost = data.get("cost", 0) or 0

    parts = [name + " [" + combat_role + "] " + prof + "/" + sub_cn + " 阻挡" + str(block) + " 费用" + str(deploy_cost) + " 范围" + _get_range_desc(name)]

    # 特性 (trait)
    trait = char.get("trait", {})
    if isinstance(trait, dict):
        candidates = trait.get("candidates", [])
        if candidates:
            best = candidates[-1]
            trait_desc = _clean_text(best.get("description", ""))
            if trait_desc:
                parts.append("特性:" + trait_desc)

    # 天赋 (talents) - 精二阶段
    for t in char.get("talents", []):
        for c in t.get("candidates", []):
            if c.get("unlockCondition", {}).get("phase") == "PHASE_2":
                tname = c.get("name", "")
                tdesc = _clean_text(c.get("description", ""))
                if tname and tdesc:
                    parts.append("天赋:" + tname + "(" + tdesc[:60] + ")")
                break

    # 标签
    tags = char.get("tagList", [])
    if tags:
        parts.append("标签:" + "/".join(tags))

    # 技能
    for i, s_ref in enumerate(char.get("skills", [])):
        skill_id = s_ref.get("skillId", "")
        if not skill_id or skill_id not in skill_data:
            continue
        s_data = skill_data[skill_id]
        levels = s_data.get("levels", [])
        if not levels:
            continue
        lv = levels[0]
        sname = lv.get("name", "")
        sdesc = _clean_text(lv.get("description", ""))
        sp = lv.get("spData", {})
        sp_cost = sp.get("spCost", 0) or 0
        dur = lv.get("duration", 0) or 0
        skill_str = "技能" + str(i + 1) + ":" + sname
        if sdesc:
            skill_str += " " + sdesc[:50]
        if sp_cost:
            skill_str += " CD" + str(sp_cost) + "s"
        if dur:
            skill_str += " 持续" + str(int(dur)) + "s"
        parts.append(skill_str)

    return " ".join(parts)


def get_profiles_batch(names: list[str], char_path: str = "", skill_path: str = "") -> str:
    """批量获取多个干员的特性描述,输出为紧凑文本。"""
    parts = []
    for name in names:
        profile = get_operator_profile(name, char_path, skill_path)
        if profile:
            parts.append(profile)
    return "\n".join(parts)


if __name__ == "__main__":
    for name in ["桃金娘", "维什戴尔", "史尔特尔", "银灰", "塞雷娅", "夜莺", "玛恩纳"]:
        print(get_operator_profile(name))
        print()
