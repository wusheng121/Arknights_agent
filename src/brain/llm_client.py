"""DeepSeek 大脑:输入 GameState → 输出符合 copilot-schema 的 Action。

用 openai SDK(兼容 DeepSeek)。无 ``DEEPSEEK_API_KEY`` 时 primary 抛错触发降级 fallback(用预设作业)。
真实接入:在 ``.env`` 填 ``DEEPSEEK_API_KEY`` 即生效。可开 thinking 模式做游戏决策推理。
"""

from __future__ import annotations

import json
import os
from typing import Any

from src.game.copilot_schema import Action, CopilotDoc, GroupSpec, OperSpec
from src.game.perception import GameState
from src.resilience.guarded_call import GuardedCall

SYSTEM_PROMPT = """你是明日方舟 AI 主播的战斗决策模块。给定当前战局状态,输出「下一个动作」的 JSON,须严格符合 MAA copilot-schema 的单个 action:
{"type":"Deploy"|"Skill"|"Retreat"|"SpeedUp"|"BulletTime"|"SkillUsage","name":<干员名>,"location":[x,y],"direction":"Left"|"Right"|"Up"|"Down"|"None","kills":<int>,"costs":<int>}
- Deploy 必填 name/location/direction;Skill/Retreat 填 name;SpeedUp 无需其他字段。
- 条件化执行: 用 kills(等击杀数)和 costs(等费用)条件触发,不要用 pre_delay 固定时间。
  例: Deploy 用 costs=实际费用; Skill 用 kills=N 等 SP 充满。
- 坐标用 MAA 坐标(见 map.ark-nights.com,设置「坐标展示」选 MAA)。
- 从 available_operators 选干员,禁止编造。
- 只输出 JSON 对象,不要任何解释或代码块标记。"""

_ACTION_FIELDS = {
    "type", "name", "location", "direction", "kills", "costs", "cost_changes",
    "cooling", "time_elapsed", "pre_delay", "post_delay", "skill_usage",
    "skill_times", "distance", "doc",
}


def _state_to_user(state: GameState, planned: Action | None = None, operators: list[dict] | None = None) -> str:
    # 取练度前 20 个可用干员,聚焦强干员
    top: list[dict] = []
    if operators:
        ranked = sorted(operators, key=lambda o: (o.get("elite") or 0, o.get("level") or 0, o.get("rarity") or 0), reverse=True)[:20]
        top = [{"name": o.get("name"), "rarity": o.get("rarity"), "elite": o.get("elite"), "level": o.get("level")} for o in ranked]
    return json.dumps(
        {
            "stage": state.stage,
            "cost": state.cost,
            "step": state.step,
            "operators_on_field": [
                {"name": o.name, "location": list(o.location) if o.location else None, "hp": o.hp, "skill_ready": o.skill_ready, "direction": o.direction}
                for o in state.operators
            ],
            "vlm_desc": state.vlm_desc,
            "available_operators": top,
            "hint": (planned.doc if planned else ""),
        },
        ensure_ascii=False,
    )


def _coerce(data: dict) -> Action:
    loc = data.get("location")
    if isinstance(loc, list):
        loc = tuple(loc)
    kwargs = {k: v for k, v in data.items() if k in _ACTION_FIELDS}
    if loc is not None:
        kwargs["location"] = loc
    # SpeedUp / SkillDaemon / Output 无需 name/location/direction
    if kwargs.get("type") in ("SpeedUp", "SkillDaemon", "Output"):
        for k in ("name", "location", "direction"):
            kwargs.pop(k, None)
    return Action(**kwargs)


def make_brain(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> GuardedCall:
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    base = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    mdl = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    has_key = bool(key)
    client = None
    if has_key:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key, base_url=base)

    async def primary(state: GameState, operators: list[dict] | None = None) -> Action:
        if not has_key or client is None:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置")
        user = _state_to_user(state, None, operators)
        resp = await client.chat.completions.create(
            model=mdl,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content or "{}"
        act = _coerce(json.loads(content))
        print("[brain primary]", act.type, act.name, act.location, act.direction)
        return act

    async def fallback(state: GameState, operators: list[dict] | None = None) -> Action:
        if operators:
            ranked = sorted(operators, key=lambda o: (o.get("elite") or 0, o.get("level") or 0), reverse=True)
            for o in ranked:
                if o.get("name"):
                    act = Action(type="Deploy", name=o["name"], location=(4, 5), direction="Right", doc="先锋回费")
                    print("[brain fallback]", act.type, act.name, act.location)
                    return act
        print("[brain fallback] SpeedUp")
        return Action(type="SpeedUp")

    return GuardedCall("llm", primary, fallback, timeout=12.0, retries=1, fail_threshold=3, cool=60.0)


SYSTEM_PROMPT_COPILOT = """你是明日方舟 AI 主播的战斗决策模块。给定地图信息(含战术建议)、出怪波次、敌人属性、干员列表(含完整特性),输出整关作业 JSON,须严格符合 MAA copilot-schema:
{"stage_name":<关卡>,"opers":[{"name":<干员名>,"skill":<1-3>,"skill_usage":<0-3>}],"actions":[{"type":"Deploy"|"Skill"|"Retreat"|"SpeedUp","name":<干员名>,"location":[x,y],"direction":"Left"|"Right"|"Up"|"Down"|"None","kills":<int>,"costs":<int>}],"minimum_required":"v6.7.0"}
规则:
- 只能使用「可用干员列表」里的干员,禁止编造不在列表的干员。
- 练度优先于稀有度:优先选 elite 高(精二)+ level 高(练满)的。
- 选 7-12 个干员:2 个先锋(回费)+ 4-6 个输出 + 1-2 医疗 + 1-2 重装。简单关卡 7-8 个够,复杂关卡(多路/多门)选 10-12 个。

- 干员特性(关键!):
  - 「干员特性」提供了每个干员的战斗角色[lane_holder/burst_only/utility/tank/support]、阻挡数、部署费用、攻击范围、职业分支、天赋、技能描述。
  - **战斗角色**:
    - [lane_holder]站场型:普攻有持续输出,适合放路口长期站场。
    - [burst_only]纯爆发:skill_usage=1会自动开技能,打完撤退或无法攻击,不适合站场,尽量不选。
    - [utility]工具人:快速复活,临时接怪,不适合长期站场。
    - [tank]重装:放前排阻挡,保护后排。
    - [support]支援:回费/治疗,放后排。
  - **攻击范围(关键!)**:
    - 范围大(≥15格)的高台优先放,一个位置覆盖多路汇合处。
    - 范围中(8-12格)放各路口。范围近(≤2格)只能打身边,放阻挡位。
  - **阻挡数**:
    - 阻挡数=能同时挡几个敌人。
    - **执旗手分支特性:技能发动期间阻挡数变为0**→不能接敌,只回费。
    - 阻挡数高的(阻挡3)适合放多怪路口。
  - **部署费用**:看地图信息的初始费用,优先选低费(cost≤14)。
  - **位置策略**:
    - 看地图信息中的红门蓝门位置和战术建议!
    - **近卫/重装(阻挡位)放蓝门附近**,防止怪进门。
    - **高台输出放能覆盖最多路径的位置**。
    - **先锋放路口附近**,回费同时接敌(非执旗手)。
  - **部署顺序**:先下先锋(低费)回费,费用够了再下近卫/重装做阻挡,再下高台输出,最后医疗。

- 出怪时序(关键!):
  - 根据「出怪波次」决定部署顺序!哪路先出怪就先守哪路。
  - 根据敌人属性选对策:高防(DEF≥100)用法术,低防用物理。

- 部署位置规则:
  - 地面职业(Pioneer/Warrior/Tank/Special)只能放「地面可部署」格子。
  - 高台职业(Sniper/Caster/Medic/Support)只能放「高台可部署」格子。
  - 坐标从 0 开始。direction 看地图信息的敌人方向。

- Retreat + 二次部署:
  - 可以先 Deploy,战斗中 Retreat(撤退),再 Deploy 同名干员(二次部署)。

- actions 规则:
  1. 第一个 action 是 SpeedUp。
  2. skill_usage=1(自动开技能)。
  3. 先下先锋回费,再按出怪顺序部署,最后医疗。
  4. Deploy 必填 name/location/direction,加 costs 条件。

- 只输出 JSON 对象,不要解释或代码块。"""


def _coerce_doc(data: dict) -> CopilotDoc:
    opers = [
        OperSpec(name=o.get("name", ""), skill=o.get("skill", 0), skill_usage=o.get("skill_usage", 0))
        for o in data.get("opers", [])
        if o.get("name")
    ]
    groups = [
        GroupSpec(name=g.get("name", ""), opers=[OperSpec(name=o.get("name", "")) for o in g.get("opers", [])])
        for g in data.get("groups", [])
    ]
    actions = [_coerce(a) for a in data.get("actions", [])]
    return CopilotDoc(
        stage_name=data.get("stage_name", ""),
        opers=opers,
        groups=groups,
        actions=actions,
        minimum_required=data.get("minimum_required", "v6.7.0"),
    )


def make_copilot_brain(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> GuardedCall:
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    base = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    mdl = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    has_key = bool(key)
    client = None
    if has_key:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key, base_url=base)

    async def primary(operators: list[dict], stage: str, map_info: str = "") -> CopilotDoc:
        if not has_key or client is None:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置")
        # 按练度(elite/level/rarity)降序取前 40,聚焦强干员(避免 379 个淹没 + 省 token)
        top = sorted(
            operators,
            key=lambda o: (o.get("elite") or 0, o.get("level") or 0, o.get("rarity") or 0),
            reverse=True,
        )[:40]
        # 标注每个干员的部署类型(Melee=地面/Ranged=高台)
        from src.data.oper_database import get_database
        db = get_database()
        for o in top:
            name = o.get("name", "")
            od = db.find_oper(name)
            if od:
                o["deploy_type"] = od.location_type  # Melee or Ranged
        # 构造 user message:地图信息 + 可用干员
        user_data = {
            "stage": stage,
            "map": map_info,
            "available_operators": top,
        }
        user = json.dumps(user_data, ensure_ascii=False)
        resp = await client.chat.completions.create(
            model=mdl,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT_COPILOT},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        msg = resp.choices[0].message
        content = msg.content or ""
        rc = getattr(msg, "reasoning_content", "") or ""
        import pathlib
        pathlib.Path("deepseek_content.txt").write_text(
            f"=== content ===\n{content}\n=== reasoning_content ===\n{rc}\n", encoding="utf-8")
        print("[DeepSeek] content len=%d reasoning len=%d" % (len(content), len(rc)))
        if not content.strip():
            raise RuntimeError("DeepSeek content 为空(可能答案在 reasoning_content)")
        return _coerce_doc(json.loads(content))

    async def fallback(operators: list[dict], stage: str, map_info: str = "") -> CopilotDoc:
        # 无 key:按 rarity/elite/level 降序选前 4 个
        ranked = sorted(
            operators,
            key=lambda o: (o.get("elite") or 0, o.get("level") or 0, o.get("rarity") or 0),
            reverse=True,
        )
        names = [o["name"] for o in ranked[:4] if o.get("name")]
        ops = [OperSpec(name=n, skill=1) for n in names]
        acts: list[Action] = []
        locs = [(6, 3), (6, 4), (5, 4), (5, 3)]
        for i, n in enumerate(names):
            acts.append(Action(type="Deploy", name=n, location=locs[i % len(locs)], direction="Right", doc=f"部署{n}"))
        acts.append(Action(type="SpeedUp"))
        return CopilotDoc(stage_name=stage, opers=ops, actions=acts)

    return GuardedCall("copilot_llm", primary, fallback, timeout=60.0, retries=1, fail_threshold=3, cool=60.0)
