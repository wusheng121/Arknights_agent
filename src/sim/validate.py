"""Sim validation + LLM fix loop.

流程:
1. LLM 生成作业
2. sim 验证 → 如果失败 → 给 LLM 根因 + 事件 + 快照
3. LLM 修正作业
4. 再 sim 验证 → 通过后用于真机执行
"""

from __future__ import annotations

import json
import os
import logging

log = logging.getLogger(__name__)

PROMPT_FIX = """你是明日方舟战术修正师。一份作业在模拟器中失败了,根据失败分析修正作业。

**失败信息:**
- failure_analysis: 根因分析(漏怪/干员死亡/技能未就绪/医疗无目标)
- events: 关键事件时间线
- snapshot: 最后状态快照
- current_job: 当前作业(operators + actions)

**修正原则:**
- 根据根因修正: 如果是"医疗无目标"→移动医疗位置; 如果是"技能未就绪"→加kills条件或换技能
- 只修改有问题的部分,不要全部重写
- 保持能用的部分不变

只输出修正后的完整 JSON:
{"opers":[{"name":"干员名","skill":1,"skill_usage":1}],"actions":[...]}
"""


async def validate_and_fix(
    job_data: dict,
    stage_id: str,
    client,
    model: str,
    max_fixes: int = 2,
) -> dict:
    """验证作业 → 如果失败 → LLM 修正 → 再验证。

    Args:
        job_data: 作业 JSON
        stage_id: 关卡 ID (如 "act44side_07")
        client: openai AsyncOpenAI
        model: DeepSeek model name
        max_fixes: 最多修正次数

    Returns:
        修正后的作业 JSON
    """
    from src.sim.game_state import run_job

    current_job = job_data

    for fix_round in range(max_fixes + 1):
        # Resolve groups
        job_to_sim = json.loads(json.dumps(current_job))
        groups = job_to_sim.get("groups", [])
        if groups:
            group_map = {}
            for g in groups:
                gname = g.get("name", "")
                candidates = g.get("opers", [])
                if candidates:
                    group_map[gname] = candidates[0].get("name", "")
            for o in job_to_sim.get("opers", []):
                if o.get("name") in group_map:
                    o["name"] = group_map[o["name"]]
            for a in job_to_sim.get("actions", []):
                if a.get("name") in group_map:
                    a["name"] = group_map[a["name"]]
            job_to_sim["groups"] = []

        # Run simulation
        log.info("[sim] Round %d: simulating job (%d opers, %d actions)...",
                 fix_round, len(job_to_sim.get("opers", [])), len(job_to_sim.get("actions", [])))
        result = run_job(stage_id, job_to_sim)

        if result["result"] == "win":
            log.info("[sim] ✅ PASSED (ticks=%d, lives=%d)", result["ticks"], result["lives_left"])
            return current_job

        if fix_round >= max_fixes:
            log.warning("[sim] ❌ FAILED after %d fixes. Using last job.", max_fixes)
            log.warning("[sim] Root causes: %s", result["failure"].get("root_causes", []))
            return current_job

        # Build fix prompt
        log.info("[sim] ❌ FAILED. Root causes: %s", result["failure"].get("root_causes", []))
        log.info("[sim] Asking LLM to fix...")

        # Get skill mapping from opers
        oper_skills = {o.get("name", ""): o.get("skill", 1) for o in current_job.get("opers", [])}

        fix_input = json.dumps({
            "failure_analysis": result["failure"],
            "events": result["events"][-30:],
            "snapshot": result["snapshot"],
            "current_job": {
                "opers": current_job.get("opers", []),
                "actions": current_job.get("actions", []),
            },
            "oper_skills": oper_skills,
        }, ensure_ascii=False)

        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PROMPT_FIX},
                {"role": "user", "content": fix_input},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            extra_body={"thinking": {"type": "disabled"}},
        )

        content = resp.choices[0].message.content or "{}"
        fix_result = json.loads(content)

        # Merge: keep original opers, update actions from fix
        if "opers" in fix_result:
            current_job["opers"] = fix_result["opers"]
        if "actions" in fix_result:
            current_job["actions"] = fix_result["actions"]

        log.info("[sim] Fix round %d: updated %d opers, %d actions",
                 fix_round + 1, len(current_job.get("opers", [])),
                 len(current_job.get("actions", [])))

    return current_job
