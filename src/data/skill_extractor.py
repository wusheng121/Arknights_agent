"""干员技能提取器: character_table.json + skill_table.json → 紧凑技能描述。

从 ArknightsGameData 提取干员技能信息,输出给 DeepSeek:
  "桃金娘: 技能1'治愈之翼' 停止攻击 回费12点 CD35s 持续16s"
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class SkillInfo:
    """单个技能信息。"""
    skill_id: str = ""
    name: str = ""
    description: str = ""
    sp_cost: int = 0
    sp_init: int = 0
    duration: float = 0.0


@dataclass
class OperatorSkills:
    """干员技能集合。"""
    char_id: str = ""
    name: str = ""
    profession: str = ""
    sub_profession: str = ""
    skills: list[SkillInfo] = field(default_factory=list)

    def to_description(self) -> str:
        """转紧凑文本。"""
        if not self.skills:
            return ""
        parts = [self.name + ":"]
        for i, s in enumerate(self.skills):
            desc = s.description or ""
            desc = _clean_desc(desc)
            sp_info = "CD%ds" % s.sp_cost if s.sp_cost else ""
            dur_info = "持续%ds" % int(s.duration) if s.duration > 0 else ""
            parts.append("  技能%d'%s' %s %s %s" % (
                i + 1, s.name or "?", desc, sp_info, dur_info))
        return "\n".join(p for p in parts if p.strip())


def extract_skills(
    operator_name: str,
    char_table_path: str = "",
    skill_table_path: str = "",
) -> OperatorSkills | None:
    """提取单个干员的技能信息。

    Args:
        operator_name: 干员中文名
        char_table_path: character_table.json 路径
        skill_table_path: skill_table.json 路径
    """
    char_data = _load_char_table(char_table_path)
    skill_data = _load_skill_table(skill_table_path)

    char_id = None
    for k, v in char_data.items():
        if v.get("name") == operator_name:
            char_id = k
            break
    if not char_id:
        return None

    char = char_data[char_id]
    op = OperatorSkills(
        char_id=char_id,
        name=char.get("name", operator_name),
        profession=char.get("profession", ""),
        sub_profession=char.get("subProfessionId", ""),
    )

    for s_ref in char.get("skills", []):
        skill_id = s_ref.get("skillId", "")
        if not skill_id or skill_id not in skill_data:
            continue
        s_data = skill_data[skill_id]
        levels = s_data.get("levels", [])
        if not levels:
            continue
        lv0 = levels[0]
        sp_data = lv0.get("spData", {})
        si = SkillInfo(
            skill_id=skill_id,
            name=lv0.get("name", ""),
            description=lv0.get("description", ""),
            sp_cost=int(sp_data.get("spCost", 0) or 0),
            sp_init=int(sp_data.get("initSp", 0) or 0),
            duration=float(lv0.get("duration", 0) or 0),
        )
        op.skills.append(si)

    return op


def extract_skills_batch(
    names: list[str],
    char_table_path: str = "",
    skill_table_path: str = "",
) -> list[OperatorSkills]:
    """批量提取多个干员的技能信息。"""
    char_data = _load_char_table(char_table_path)
    skill_data = _load_skill_table(skill_table_path)
    result = []
    for name in names:
        op = extract_skills(name, "", "")
        if op:
            result.append(op)
    return result


def to_compact_description(names: list[str], char_table_path: str = "", skill_table_path: str = "") -> str:
    """批量提取并输出紧凑文本。"""
    parts = []
    for name in names:
        op = extract_skills(name, char_table_path, skill_table_path)
        if op:
            parts.append(op.to_description())
    return "\n".join(parts)


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


def _clean_desc(desc: str) -> str:
    """清理技能描述中的富文本标签。"""
    import re
    desc = re.sub(r"<@ba\.[^>]+>|</>", "", desc)
    desc = re.sub(r"\{[^}]+\}", "X", desc)
    return desc.strip()


if __name__ == "__main__":
    for name in ["桃金娘", "维什戴尔", "史尔特尔", "银灰", "塞雷娅", "夜莺"]:
        op = extract_skills(name)
        if op:
            print(op.to_description())
            print()
