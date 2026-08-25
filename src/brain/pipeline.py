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


PROMPT_STEP1_SELECT = """你是明日方舟战斗指挥。根据地图信息、出怪波次和可选的专家参考,从可用干员中选出 10-12 个组成编队。

**如果提供了 expert_reference(专家攻略/作业),必须优先使用其中的干员组合!**
- 专家作业的干员组合是已验证可通关的,不要自己换
- 只有当专家作业中的干员用户没有时,才选相似角色替代
- 替代规则:银灰→拉普兰德,塞雷娅→星熊/黍,维什戴尔→澄闪(同为高台输出)

选择原则:
- 2 个先锋(回费):执旗手或冲锋手,低费优先(cost≤14)
- 4-6 个输出:优先 [lane_holder] 站场型,攻击范围大的优先
- **[burst_only] 和 [utility] 型禁止选!** (玛恩纳/史尔特尔/砾等不适合站场)
- 1 个重装 [tank] 阻挡3
- 1-2 个医疗 [support]
- 高防敌人用法术,低防用物理

只输出 JSON: {"selected":[{"name":"干员名"}]}
"""

PROMPT_STEP2_POSITION = """你是明日方舟战术规划师。给定选中干员特性、地图格子和敌人路径,为每个干员分配位置和朝向。

**蓝门防守原则:**
- 数一下地图有几个蓝门,每个蓝门 3 格内必须有 1 个地面阻挡(近卫/重装)。
- 不要在同一个路径(同一行)放两个地面阻挡!分散到不同行/不同蓝门。

**高台分布原则:**
- 高台干员不要全放一边!左右两侧都要有高台覆盖敌人路径。
- 看敌人路径,高台放能覆盖最多路径格子的位置。

**干员位置原则:**
- 阻挡位(近卫/重装 阻挡≥2):放蓝门附近地面格子,每个蓝门一个,不要重复。
- 输出位(高台):放能覆盖多条敌人路径的位置,左右分散。
- 先锋:放能打到敌人的位置(情报官特性是攻击回费,必须能打到怪才有用!)。
- 医疗:放后排,覆盖己方干员。

**朝向原则:**
- 看干员所在位置的敌人路径走向,朝向覆盖最多路径格子的方向。
- 不同位置方向可能不同,不要统一方向。

地面职业只能放「地面可部署」,高台职业只能放「高台可部署」。同一格子不能放两个。

只输出 JSON: {"positions":[{"name":"干员名","location":[x,y],"direction":"Right"}]}
"""

PROMPT_STEP3_SKILL = """你是明日方舟技能专家。根据干员的技能描述和战斗角色,选择最合适的技能编号。

**技能选择依据(按优先级):**
1. 先锋(执旗手):选CD最短的纯回费技能(通常是技能1)。不要选有附加效果(如治疗)的技能,因为CD更长。
2. 先锋(情报官):选能攻击回费的技能(看描述含"获得...部署费用"且需要攻击)。
3. 站场输出:选持续时间最长的持续输出技能(非一次性爆发)。
4. 重装:选生存/防御技能(看描述含"防御力+"或"每秒回复")。
5. 医疗:选持续治疗技能(如夜莺技能2法术护盾,不要选技能3圣域因为CD太长)。
6. burst_only 型:选爆发最高的技能。

skill_usage=1 会自动开技能。有"逐渐流失生命"/"强制退出"的技能会自动触发撤退。

只输出 JSON: {"skills":[{"name":"干员名","skill":1,"skill_usage":1}]}
"""

PROMPT_STEP4_ORDER = """你是明日方舟部署调度员。根据干员的位置、费用、类型和出怪波次,确定部署顺序。

**最重要的原则: 平衡各蓝门防守 + 阻挡优先!**
- 看每个干员的位置和 defends_blue_door,确定他守哪个蓝门。
- **不要把同一个蓝门的干员连续部署!** 交替部署不同蓝门的干员。
- **每个蓝门先下地面阻挡位(近卫/重装/先锋),再下高台输出!** 阻挡位防漏怪,输出位打伤害。
- 先下低费(cost≤14)干员,确保每个蓝门都有人守,再补充高费输出。
- 如果出怪从 T+0 就开始,立刻部署低费干员守各路。
- costs 条件 = 该干员的部署费用。
- **部署位不够时,加 Retreat action 撤掉先锋(已完成回费),腾位给阻挡/输出。**

部署顺序示例(3个蓝门):
SpeedUp → Deploy先锋A(蓝门1,cost10) → Deploy先锋B(蓝门2,cost10) → Deploy阻挡C(蓝门3,cost13) → Deploy阻挡D(蓝门1,cost16) → Deploy输出E(蓝门2,cost20) → ...

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
        "expert_reference": rag_context,  # RAG 检索的攻略+作业
        "available_operators": ops_with_roles if ops_with_roles else ops_brief,
    }, ensure_ascii=False)

    print("[Step1] 选干员...")
    step1_result = await _call_deepseek(client, mdl, PROMPT_STEP1_SELECT, step1_user)
    selected_names = [o["name"] for o in step1_result.get("selected", [])]
    print("[Step1] 选中: %s" % ", ".join(selected_names))

    # ===== Step 2: 选位置 =====
    # 只给选中干员的完整特性
    selected_profiles = []
    for line in (oper_profiles_full.split("\n") if oper_profiles_full else []):
        for name in selected_names:
            if line.startswith(name + " "):
                selected_profiles.append(line)
                break

    step2_user = json.dumps({
        "stage": stage,
        "map": map_info,
        "enemy_paths": paths_desc,
        "selected_operators": selected_profiles,
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
