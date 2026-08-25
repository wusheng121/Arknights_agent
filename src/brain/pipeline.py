"""多步管道: 把单次大 prompt 拆成 4 个专注 skill。

Step 1: 选干员 (地图+敌人+干员列表 → 7-8个干员)
Step 2: 选位置 (选中干员+攻击范围+地图格子 → 位置)
Step 3: 选技能 (技能描述+敌人属性 → skill编号)
Step 4: 定部署顺序 (位置+费用+波次 → actions)
"""

from __future__ import annotations

import json
import os
from typing import Any

from src.game.copilot_schema import Action, CopilotDoc, OperSpec


PROMPT_STEP1_SELECT = """你是明日方舟战斗指挥。根据地图、波次、敌人、专家参考、策略知识和可用干员,选编队。

**学习而非照抄:**
- 如果有 expert_reference: 理解专家选这些干员的原因(角色搭配/费用节奏/应对敌人),适配用户可用干员
- 如果有 strategy_knowledge: 参考统计规律(哪些干员最常用、团队人数分布)
- 如果两者都没有: 根据敌人属性和地图信息自行推理
- 专家用低星干员通关,用户有高星干员 → 可以选更好的,但要理解专家为什么选那些干员(费用低/角色互补)

**输入字段说明:**
- map: 地图信息(红蓝门/可部署格子/敌人方向/战术建议)
- waves: 出怪波次(时间/敌人/路线)
- enemies: 敌人属性(HP/ATK/DEF/RES)
- expert_reference: 专家作业参考(干员选择/位置/技能/操作序列)
- strategy_knowledge: 1365份作业的统计规律
- available_operators: 用户可用干员(角色/阻挡/费用/范围/天赋/技能)

只输出 JSON: {"selected":[{"name":"干员名"}]}
"""

PROMPT_STEP2_POSITION = """你是明日方舟战术规划师。根据地图、敌人路径、干员特性和专家位置参考,分配位置和朝向。

**学习而非照抄:**
- 如果有 expert_positions: 理解专家为什么放在那个位置(攻击范围覆盖哪条路径?),根据用户干员的攻击范围调整
- 如果没有: 根据敌人路径和干员攻击范围自行推理最佳位置
- 核心推理: "这个干员的攻击范围从这里能覆盖几条敌人路径?"

**输入字段说明:**
- map: 地图格子(地面/高台可部署) + 红蓝门 + 战术建议
- enemy_paths: 敌人移动路径(坐标序列,从红门到蓝门)
- operators: 每个干员的角色/阻挡数/费用/攻击范围大小
- expert_positions: 专家作业的位置和方向(如果有)

地面职业只能放地面格子,高台职业只能放高台格子。同一格子不能放两个。

只输出 JSON: {"positions":[{"name":"干员名","location":[x,y],"direction":"Right"}]}
"""

PROMPT_STEP3_SKILL = """你是明日方舟技能专家。根据干员技能描述、敌人属性和策略知识,选技能。

**学习而非照抄:**
- 如果有 strategy_knowledge: 参考统计规律(如"圣聆初雪常用skill2, 246/251次")
- 如果没有: 根据技能描述和敌人属性推理(高防用法术技能,低防用物理技能)
- 理解技能描述的关键词: "回复...部署费用"=回费, "攻击力+"=增伤, "防御力+"=生存, "持续X秒"=持续时间
- skill_usage=1 表示自动开技能

**输入字段说明:**
- enemies: 敌人属性(HP/ATK/DEF/RES)
- operators: 每个干员的技能描述(name/CD/持续时间/效果关键词)
- strategy_knowledge: 统计规律(哪个干员常用几技能)

只输出 JSON: {"skills":[{"name":"干员名","skill":1,"skill_usage":1}]}
"""

PROMPT_STEP4_ORDER = """你是明日方舟部署调度员。根据干员位置、费用、出怪波次和专家参考,确定部署顺序。

**学习而非照抄:**
- 如果有 expert_reference: 参考专家的操作序列(谁先下/谁后下/何时撤退/何时开技能)
- 如果没有: 根据费用和波次推理(低费先下回费,高费等费用够了再下,哪路先出怪先守)
- 如果部署位可能不够,可以加 Retreat action 撤退已用完的干员
- 如果需要开技能,加 Skill action (带 kills 条件)
- 最后可以加 SkillDaemon 自动挂机

**输入字段说明:**
- waves: 出怪波次(时间/敌人/路线)
- deployments: 每个干员的 name/location/direction/cost/skill/is_ground/defends_blue_door
- expert_reference: 专家作业的操作序列(如果有)

只输出 JSON: {"actions":[{"type":"SpeedUp"},{"type":"Deploy","name":"干员名","location":[x,y],"direction":"Right","costs":10}]}
"""


def _is_ground_operator(name: str, profiles: str) -> bool:
    """从干员特性判断是否地面职业(近卫/重装/先锋/特种)。"""
    for line in (profiles.split("\n") if profiles else []):
        if line.startswith(name + " "):
            if "/WARRIOR" in line or "/TANK" in line or "/PIONEER" in line or "/SPECIAL" in line:
                return True
            return False
    return False


async def _call_deepseek(client, model: str, system_prompt: str, user_content: str) -> dict:
    """调用 DeepSeek 返回 JSON dict。"""
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    msg = resp.choices[0].message
    content = msg.content or "{}"
    import pathlib
    pathlib.Path("deepseek_content.txt").write_text(content, encoding="utf-8")
    return json.loads(content)


async def generate_job_pipeline(
    operators: list[dict],
    stage: str,
    map_info: str,
    wave_desc: str,
    enemy_stats_desc: str,
    oper_profiles_full: str,
    paths_desc: str = "",
    blue_doors: list[tuple[int, int]] = None,
    rag_context: str = "",
    strategy_knowledge: str = "",
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> CopilotDoc:
    """多步管道生成整关作业。"""

    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    base = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    mdl = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=key, base_url=base)

    # 精简干员列表(只 name + role + cost)
    from src.data.oper_database import get_database
    db = get_database()
    top = sorted(
        operators,
        key=lambda o: (o.get("elite") or 0, o.get("level") or 0, o.get("rarity") or 0),
        reverse=True,
    )[:40]

    # ===== Step 1: 选干员 =====
    ops_brief = []
    for o in top:
        name = o.get("name", "")
        od = db.find_oper(name)
        role = od.location_type if od else "?"
        ops_brief.append({"name": name, "rarity": o.get("rarity"), "elite": o.get("elite"), "level": o.get("level"), "deploy_type": role})

    # 从 oper_profiles 提取精简特性 (name + role + cost + range)
    import re
    ops_with_roles = []
    for line in oper_profiles_full.split("\n") if oper_profiles_full else []:
        m = re.match(r"(.+?)\s*\[(\w+)\].*?阻挡(\d+).*?费用(\d+).*?范围(.+?)(?:\s|天|$)", line)
        if m:
            ops_with_roles.append({
                "name": m.group(1).strip(),
                "role": m.group(2),
                "block": int(m.group(3)),
                "cost": int(m.group(4)),
                "range": m.group(5).strip(),
            })

    step1_user = json.dumps({
        "stage": stage,
        "map": map_info,
        "waves": wave_desc,
        "enemies": enemy_stats_desc,
        "expert_reference": rag_context,
        "strategy_knowledge": strategy_knowledge,
        "available_operators": ops_with_roles if ops_with_roles else ops_brief,
    }, ensure_ascii=False)

    print("[Step1] 选干员...")
    step1_result = await _call_deepseek(client, mdl, PROMPT_STEP1_SELECT, step1_user)
    selected_names = [o["name"] for o in step1_result.get("selected", [])]
    print("[Step1] 选中: %s" % ", ".join(selected_names))

    # ===== Step 2: 选位置 =====
    # 精简干员信息: 只保留角色/阻挡/费用/范围,去掉天赋/技能描述(减少噪音)
    import re as _re2
    position_profiles = []
    for name in selected_names:
        for line in (oper_profiles_full.split("\n") if oper_profiles_full else []):
            if line.startswith(name + " "):
                m = _re2.search(r'\[(\w+)\].*?阻挡(\d+).*?费用(\d+).*?范围(.+?)(?:\s|天|$)', line)
                if m:
                    position_profiles.append({
                        "name": name,
                        "role": m.group(1),
                        "block": int(m.group(2)),
                        "cost": int(m.group(3)),
                        "range": m.group(4).strip(),
                    })
                break

    step2_user = json.dumps({
        "stage": stage,
        "map": map_info,
        "enemy_paths": paths_desc,
        "operators": position_profiles,
        "expert_positions": rag_context,  # 专家作业的位置作为参考
    }, ensure_ascii=False)

    print("[Step2] 选位置...")
    step2_result = await _call_deepseek(client, mdl, PROMPT_STEP2_POSITION, step2_user)
    positions = step2_result.get("positions", [])
    print("[Step2] 位置: %s" % json.dumps(positions, ensure_ascii=False)[:200])

    # ===== Step 3: 选技能 =====
    # 给选中干员的技能描述
    from src.data.oper_profile import get_operator_profile
    skill_profiles = []
    for name in selected_names:
        p = get_operator_profile(name)
        if p:
            skill_profiles.append(p)

    step3_user = json.dumps({
        "stage": stage,
        "enemies": enemy_stats_desc,
        "operators": skill_profiles,
        "strategy_knowledge": strategy_knowledge,  # 统计规律:"圣聆初雪常用skill2"
    }, ensure_ascii=False)

    print("[Step3] 选技能...")
    step3_result = await _call_deepseek(client, mdl, PROMPT_STEP3_SKILL, step3_user)
    skills = step3_result.get("skills", [])
    print("[Step3] 技能: %s" % json.dumps(skills, ensure_ascii=False)[:200])

    # ===== Step 4: 定部署顺序 =====
    # 合并位置 + 费用 + 技能
    pos_map = {p["name"]: p for p in positions}
    skill_map = {s["name"]: s for s in skills}

    # 从 oper_profile 提取费用
    cost_map = {}
    for line in (oper_profiles_full.split("\n") if oper_profiles_full else []):
        for name in selected_names:
            if line.startswith(name + " "):
                cm = re.search(r"费用(\d+)", line)
                if cm:
                    cost_map[name] = int(cm.group(1))
                break

    deploy_list = []
    for name in selected_names:
        pos = pos_map.get(name, {})
        sk = skill_map.get(name, {})
        # 确定守哪个蓝门
        blue_door = "?"
        if pos.get("location") and blue_doors:
            loc = pos["location"]
            distances = [(abs(int(loc[0])-bd[0])+abs(int(loc[1])-bd[1]), bd) for bd in blue_doors]
            if distances:
                blue_door = str(min(distances)[1])
        deploy_list.append({
            "name": name,
            "location": pos.get("location", [0, 0]),
            "direction": pos.get("direction", "Right"),
            "cost": cost_map.get(name, 10),
            "skill": sk.get("skill", 1),
            "skill_usage": sk.get("skill_usage", 1),
            "defends_blue_door": blue_door,
            "is_ground": _is_ground_operator(name, oper_profiles_full),
        })

    step4_user = json.dumps({
        "stage": stage,
        "waves": wave_desc,
        "deployments": deploy_list,
    }, ensure_ascii=False)

    print("[Step4] 定部署顺序...")
    step4_result = await _call_deepseek(client, mdl, PROMPT_STEP4_ORDER, step4_user)
    actions_raw = step4_result.get("actions", [])
    print("[Step4] actions: %d" % len(actions_raw))

    # ===== Step 5: 组装 CopilotDoc =====
    opers = []
    for name in selected_names:
        sk = skill_map.get(name, {})
        opers.append(OperSpec(
            name=name,
            skill=int(sk.get("skill", 1)),
            skill_usage=int(sk.get("skill_usage", 1)),
        ))

    actions = []
    for a in actions_raw:
        loc = a.get("location", [])
        loc_tuple = tuple(loc) if isinstance(loc, list) else None
        actions.append(Action(
            type=a.get("type", "Deploy"),
            name=a.get("name"),
            location=loc_tuple,
            direction=a.get("direction", "Right"),
            costs=int(a.get("costs", 0)) if a.get("costs") else 0,
        ))

    doc = CopilotDoc(
        stage_name=stage,
        opers=opers,
        actions=actions,
        minimum_required="v6.7.0",
    )
    print("[Pipeline] 完成: %d opers, %d actions" % (len(doc.opers), len(doc.actions)))
    return doc
