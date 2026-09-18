"""多步管道: 把单次大 prompt 拆成 4 个专注 skill。

Step 1: 选干员 (地图+敌人+干员列表 → 7-8个干员)
Step 2: 选位置 (选中干员+攻击范围+地图格子 → 位置)
Step 3: 选技能 (技能描述+敌人属性 → skill编号)
Step 4: 定部署顺序 (位置+费用+波次 → actions)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.game.copilot_schema import Action, CopilotDoc, OperSpec

log = logging.getLogger(__name__)


PROMPT_STEP1_SELECT = """你是明日方舟战斗指挥。根据地图、波次、敌人、专家参考、策略知识、因果原则和可用干员,选编队。

**三层信息(按优先级):**
1. principles: 因果原则(从1365份专家作业蒸馏的规律,带condition和reason)→用原则推理
2. expert_reference: 专家作业参考 → 理解专家为什么选这些干员,适配用户可用干员
3. strategy_knowledge: 统计规律 → 频率参考

**输入字段说明:**
- map: 地图信息(红蓝门/可部署格子/敌人方向/战术建议)
- waves: 出怪波次(时间/敌人/路线)
- enemies: 敌人属性(HP/ATK/DEF/RES/移速/重量)
- principles: 因果原则(pattern/condition/reason)
- expert_reference: 专家作业参考(干员选择/位置/技能/操作序列)
- strategy_knowledge: 1365份作业的统计规律
- available_operators: 结构化干员数据数组,每个含:
  - name, role(战斗角色), profession, sub_profession
  - stats: {hp, atk, def, res, block, cost, attack_time}
  - range_tiles: 攻击范围坐标数组 [[x,y],...],相对于干员位置
  - trait: 特性描述
  - talents: 天赋列表 [{name, desc}]
  - skills: 技能列表 [{skill, name, sp_type, sp_cost, sp_init, skill_type, duration, blackboard, description}]
    - skill_type: PASSIVE(被动)/AUTO(自动)/MANUAL(手动)
    - duration: -1=弹药制(可手动关闭), >0=持续秒数, 0=瞬时
    - blackboard: 技能数值效果 {atk:1.8, base_attack_time:2.9, ...}

**选人逻辑:**
- 用 stats.hp/atk/def 判断生存和输出能力
- 用 range_tiles 判断攻击范围覆盖
- 用 skills.blackboard 判断技能倍率(如 atk:1.8=攻击力+80%)
- 用 skills.skill_type 判断自动/手动触发
- 用 skills.duration=-1 识别弹药制技能

只输出 JSON: {"selected":[{"name":"干员名"}]}
"""

PROMPT_STEP2_POSITION = """你是明日方舟战术规划师。根据地图、敌人路径、干员特性和专家位置参考,分配位置和朝向。

**学习而非照抄:**
- 如果有 expert_positions: 理解专家为什么放在那个位置(攻击范围覆盖哪条路径?),根据用户干员的攻击范围调整
- 如果没有: 根据敌人路径和干员攻击范围自行推理最佳位置
- 核心推理: "这个干员的 range_tiles 从 [x,y] 朝 direction 能覆盖哪些敌人路径?"

**输入字段说明:**
- map: 地图格子(地面/高台可部署) + 红蓝门 + 战术建议
- enemy_paths: 敌人移动路径(坐标序列,从红门到蓝门)
- operators: 结构化干员数据(含 range_tiles 坐标数组)
  - range_tiles 是相对坐标 [[x,y],...],方向旋转后需调整
  - profession: WARRIOR/TANK/PIONEER/SPECIAL=地面, MEDIC/SNIPER/CASTER/SUPPORT=高台
  - stats.block: 阻挡数(1-3)
- expert_positions: 专家作业的位置和方向(如果有)

**位置推理:**
- 地面职业(profession=WARRIOR/TANK/PIONEER/SPECIAL)只能放地面格子
- 高台职业(profession=MEDIC/SNIPER/CASTER/SUPPORT)只能放高台格子
- 同一格子不能放两个
- 医疗干员放能治疗到友方的位置(range_tiles 覆盖友方)
- 阻挡型干员放敌人路径上(range_tiles 覆盖敌人路径)

只输出 JSON: {"positions":[{"name":"干员名","location":[x,y],"direction":"Right"}]}
"""

PROMPT_STEP3_SKILL = """你是明日方舟技能专家。根据干员技能数据、敌人属性和策略知识,选技能。

**学习而非照抄:**
- 如果有 strategy_knowledge: 参考统计规律(如"圣聆初雪常用skill2, 246/251次")
- 如果没有: 根据 blackboard 数值和敌人属性推理

**输入字段说明:**
- enemies: 敌人属性(HP/ATK/DEF/RES)
- operators: 结构化干员数据,含完整 skills 数组:
  - skill: 技能编号(1/2/3)
  - name: 技能名
  - sp_type: INCREASE_WITH_TIME(每秒充能)/INCREASE_WHEN_ATTACK(攻击时充能)
  - sp_cost: SP需求量, sp_init: 初始SP
  - skill_type: PASSIVE(被动)/AUTO(自动触发)/MANUAL(手动触发)
  - duration: -1=弹药制(可手动关闭,有max_cnt限制), >0=持续N秒, 0=瞬时
  - blackboard: 数值效果,如 {atk:1.8}=攻击力+80%, {base_attack_time:2.9}=攻速变2.9倍
- strategy_knowledge: 统计规律(哪个干员常用几技能)

**技能选择逻辑:**
- 弹药制(duration=-1, blackboard含max_cnt): 适合 skill_usage=1 自动开启
- AUTO skill_type: SP满自动触发,适合 skill_usage=1
- MANUAL skill_type: 需手动 Skill action 触发,加 kills 条件等SP充满
- 高防敌人(DEF>500)选法术技能(blackboard含magic_resistance降低或arts伤害)
- 低防敌人选物理技能(blackboard含atk_scale倍率)

只输出 JSON: {"skills":[{"name":"干员名","skill":1,"skill_usage":1}]}
"""

PROMPT_STEP4_ORDER = """你是明日方舟部署调度员。根据干员位置、费用、出怪波次和专家参考,确定部署顺序。

**条件化执行(核心规则):**
不要用固定时间(pre_delay),用游戏状态条件触发动作。MAA 支持以下条件:
- kills: 等击杀数达到 N 才执行(如 Skill action 等击杀够了再开)
- costs: 等费用达到 N 才执行(如 Deploy 等费用够了再下)
- cost_changes: 等费用变化量达到 N(从上一个 action 起算)
- cooling: 等 CD 中干员数达到 N
- elapsed_time: 等时间达到 N 毫秒

**条件化 vs 固定时间:**
- 好的写法: {"type":"Deploy","name":"维什戴尔","location":[8,1],"direction":"Down","costs":40}
  → 费用到 40 才部署,适应不同游戏速度
- 好的写法: {"type":"Skill","name":"维什戴尔","kills":15}
  → 击杀到 15 才开技能,确保 SP 已充满
- 坏的写法: {"type":"Deploy","name":"维什戴尔","location":[8,1],"direction":"Down","pre_delay":5000}
  → 固定等 5 秒,游戏速度变化会出错

**部署逻辑:**
- 低费先锋先下回费(costs 用实际费用,先锋一般 6-10)
- 高费输出等费用够了再下(costs=实际费用)
- 需要撤退时加 Retreat action
- 需要开技能时加 Skill action,用 kills 条件等 SP 充满
- 弹药制技能(如维什戴尔3技能)用 skill_usage=1 自动开,不要手动 Skill
- 最后加 SkillDaemon 挂机

**doc 字段(可选):** 每个 action 可加 doc 描述,如 "doc":"下维什戴尔清高台"

**输入字段说明:**
- waves: 出怪波次(时间/敌人/路线)
- deployments: 每个干员的 name/location/direction/cost/skill/is_ground/defends_blue_door
- expert_reference: 专家作业的操作序列(如果有)
- principles: 因果原则

只输出 JSON: {"actions":[{"type":"SpeedUp"},{"type":"Deploy","name":"干员名","location":[x,y],"direction":"Right","costs":10,"doc":"下先锋回费"},{"type":"Skill","name":"干员名","kills":15,"doc":"击杀够了开技能"},{"type":"SkillDaemon"}]}
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
    pathlib.Path(os.path.join(os.path.dirname(__file__), "..", "..", "tmp", "deepseek_content.txt")).write_text(content, encoding="utf-8")
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
    principles: str = "",
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

    # ===== 获取结构化干员数据 (完整 stats + range_tiles + skills + blackboard) =====
    from src.data.oper_profile import get_operator_structured
    top_names = [o.get("name", "") for o in top if o.get("name")]
    ops_structured = [get_operator_structured(n) for n in top_names]
    ops_structured = [d for d in ops_structured if d]

    # ===== Step 1: 选干员 =====
    # 传完整结构化数据: stats(HP/ATK/DEF/RES/block/cost) + range_tiles + skills(sp_cost/skill_type/duration/blackboard)
    # 让 LLM 能计算: 谁抗得住(HP/DEF) / 谁打得动(ATK vs 敌人DEF) / 技能类型(弹药制/自动触发)
    step1_user = json.dumps({
        "stage": stage,
        "map": map_info,
        "waves": wave_desc,
        "enemies": enemy_stats_desc,
        "expert_reference": rag_context,
        "strategy_knowledge": strategy_knowledge,
        "principles": principles,
        "available_operators": ops_structured,
    }, ensure_ascii=False)

    log.info("[Step1] 选干员... (%d structured operators)", len(ops_structured))
    step1_result = await _call_deepseek(client, mdl, PROMPT_STEP1_SELECT, step1_user)
    selected_names = [o["name"] for o in step1_result.get("selected", [])]
    log.info("[Step1] 选中: %s", ", ".join(selected_names))

    # ===== Step 2: 选位置 =====
    # 传 range_tiles 坐标 + 地图可部署格子,LLM 可计算覆盖范围
    selected_ops = [d for d in ops_structured if d["name"] in selected_names]
    step2_user = json.dumps({
        "stage": stage,
        "map": map_info,
        "enemy_paths": paths_desc,
        "operators": selected_ops,
        "expert_positions": rag_context,
        "principles": principles,
    }, ensure_ascii=False)

    log.info("[Step2] 选位置...")
    step2_result = await _call_deepseek(client, mdl, PROMPT_STEP2_POSITION, step2_user)
    positions = step2_result.get("positions", [])
    log.info("[Step2] 位置: %s", json.dumps(positions, ensure_ascii=False)[:200])

    # ===== Step 3: 选技能 =====
    # 传完整 skill 数据: sp_type/sp_cost/sp_init/skill_type/duration/blackboard
    # 让 LLM 能判断: 弹药制(duration=-1, max_cnt) / 自动触发(AUTO) / SP 充能速度
    skill_profiles = []
    for name in selected_names:
        for d in selected_ops:
            if d["name"] == name:
                skill_profiles.append(d)
                break

    step3_user = json.dumps({
        "stage": stage,
        "enemies": enemy_stats_desc,
        "operators": skill_profiles,
        "strategy_knowledge": strategy_knowledge,
        "principles": principles,
    }, ensure_ascii=False)

    log.info("[Step3] 选技能...")
    step3_result = await _call_deepseek(client, mdl, PROMPT_STEP3_SKILL, step3_user)
    skills = step3_result.get("skills", [])
    log.info("[Step3] 技能: %s", json.dumps(skills, ensure_ascii=False)[:200])

    # ===== Step 4: 定部署顺序 =====
    pos_map = {p["name"]: p for p in positions}
    skill_map = {s["name"]: s for s in skills}

    # 从结构化数据获取 cost (不再用 regex 解析)
    cost_map = {}
    for d in ops_structured:
        if d["name"] in selected_names:
            cost_map[d["name"]] = d["stats"]["cost"]

    deploy_list = []
    for name in selected_names:
        pos = pos_map.get(name, {})
        sk = skill_map.get(name, {})
        blue_door = "?"
        if pos.get("location") and blue_doors:
            loc = pos["location"]
            distances = [(abs(int(loc[0])-bd[0])+abs(int(loc[1])-bd[1]), bd) for bd in blue_doors]
            if distances:
                blue_door = str(min(distances)[1])
        # 从结构化数据判断地面/高台
        is_ground = any(d["name"] == name and d["profession"] in ("WARRIOR", "TANK", "PIONEER", "SPECIAL")
                        for d in ops_structured)
        deploy_list.append({
            "name": name,
            "location": pos.get("location", [0, 0]),
            "direction": pos.get("direction", "Right"),
            "cost": cost_map.get(name, 10),
            "skill": sk.get("skill", 1),
            "skill_usage": sk.get("skill_usage", 1),
            "defends_blue_door": blue_door,
            "is_ground": is_ground,
        })

    step4_user = json.dumps({
        "stage": stage,
        "waves": wave_desc,
        "deployments": deploy_list,
        "expert_reference": rag_context,
        "principles": principles,
    }, ensure_ascii=False)

    log.info("[Step4] 定部署顺序...")
    step4_result = await _call_deepseek(client, mdl, PROMPT_STEP4_ORDER, step4_user)
    actions_raw = step4_result.get("actions", [])
    log.info("[Step4] actions: %d", len(actions_raw))

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
            kills=int(a.get("kills", 0)) if a.get("kills") else 0,
            costs=int(a.get("costs", 0)) if a.get("costs") else 0,
            cost_changes=int(a.get("cost_changes", 0)) if a.get("cost_changes") else 0,
            cooling=int(a.get("cooling", -1)) if a.get("cooling") is not None else -1,
            pre_delay=int(a.get("pre_delay", 0)) if a.get("pre_delay") else 0,
            post_delay=int(a.get("post_delay", 0)) if a.get("post_delay") else 0,
            skill_usage=int(a.get("skill_usage")) if a.get("skill_usage") is not None else None,
            skill_times=int(a.get("skill_times", 1)) if a.get("skill_times") else 1,
            doc=a.get("doc"),
        ))

    doc = CopilotDoc(
        stage_name=stage,
        opers=opers,
        actions=actions,
        minimum_required="v6.7.0",
    )
    log.info("[Pipeline] 完成: %d opers, %d actions", len(doc.opers), len(doc.actions))
    return doc
