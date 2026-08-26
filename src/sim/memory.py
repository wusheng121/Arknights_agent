"""Tier 2: 自反思记忆 — 结构化失败/成功记录。

两层记忆:
1. stage_memory/<stage>.json — 单关尝试记录
2. principles.json — 跨关通用经验(从多次反思再蒸馏)

结构化 schema(非自由文本):
{
  "stage": "act44side_07",
  "attempt": 1,
  "deployments": [{"name":"维什戴尔","location":[8,1],"direction":"Down"}],
  "outcome": "leak"|"timeout"|"clear",
  "failure_mode": "no_healing_coverage"|"skill_not_ready"|"wrong_direction"|"concentrated_blockers"|"too_slow_kills"|...,
  "root_cause": "夜莺(2,4)治疗范围覆盖不到友方干员",
  "lesson": "医疗干员应放在友方干员附近",
  "generalizable": true,
  "timestamp": "2026-08-26T15:00:00"
}

规则:
- 成功也记录(不只失败)
- 同类失败 ≥2 次才 promote 到 principles(防误判)
- 按 failure_mode/stage/operator 检索过滤
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

MEMORY_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "memory")
PRINCIPLES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "patterns", "principles.json")


# Failure classification
FAILURE_MODES = {
    "leak": "漏怪(敌人到达蓝门)",
    "timeout": "超时(无法在规定时间内清完)",
    "no_healing_coverage": "医疗干员治疗范围覆盖不到友方",
    "skill_not_ready": "技能未就绪就执行(SP不够)",
    "skill_misused": "技能使用错误(如弹药制秒开秒关)",
    "wrong_direction": "干员朝向错误(攻击范围覆盖不到敌人)",
    "concentrated_blockers": "阻挡干员集中在一路导致其他路漏怪",
    "too_slow_kills": "击杀速度太慢(伤害不足以清完敌人)",
    "operator_useless": "干员无作用(位置/技能/范围都不对)",
    "deploy_too_late": "部署太晚(DP管理不当)",
    "clear": "通关(成功)",
}


@dataclass
class MemoryEntry:
    """单次尝试的记忆。"""
    stage: str
    attempt: int
    deployments: list[dict]  # [{"name":"维什戴尔","location":[8,1],"direction":"Down"}]
    outcome: str  # "leak"|"timeout"|"clear"
    failure_mode: str  # key from FAILURE_MODES
    root_cause: str
    lesson: str
    generalizable: bool = False
    timestamp: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "attempt": self.attempt,
            "deployments": self.deployments,
            "outcome": self.outcome,
            "failure_mode": self.failure_mode,
            "root_cause": self.root_cause,
            "lesson": self.lesson,
            "generalizable": self.generalizable,
            "timestamp": self.timestamp,
        }


class MemoryStore:
    """两层记忆存储: stage_memory + principles。"""

    def __init__(self):
        os.makedirs(MEMORY_DIR, exist_ok=True)

    def record(self, entry: MemoryEntry) -> None:
        """记录一次尝试到 stage_memory/<stage>.json。"""
        if not entry.timestamp:
            entry.timestamp = datetime.now().isoformat()

        stage_path = os.path.join(MEMORY_DIR, f"{entry.stage}.json")
        memories = []
        if os.path.exists(stage_path):
            with open(stage_path, encoding="utf-8") as f:
                memories = json.load(f)
        memories.append(entry.to_dict())

        with open(stage_path, "w", encoding="utf-8") as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)

        print(f"[memory] Recorded: {entry.stage} attempt={entry.attempt} outcome={entry.outcome} mode={entry.failure_mode}")

        # Check if should promote to principles (same failure_mode ≥ 2 times)
        if entry.failure_mode != "clear":
            self._check_promote(entry)

    def get_stage_memories(self, stage: str) -> list[dict]:
        """获取单关记忆。"""
        path = os.path.join(MEMORY_DIR, f"{stage}.json")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def get_relevant_memories(self, stage: str = "", failure_mode: str = "") -> list[dict]:
        """检索相关记忆(可按 stage/failure_mode 过滤)。"""
        results = []
        if not os.path.isdir(MEMORY_DIR):
            return results
        for fname in os.listdir(MEMORY_DIR):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(MEMORY_DIR, fname)
            with open(fpath, encoding="utf-8") as f:
                memories = json.load(f)
            for m in memories:
                if stage and m.get("stage") != stage:
                    continue
                if failure_mode and m.get("failure_mode") != failure_mode:
                    continue
                results.append(m)
        return results

    def get_lessons_for_prompt(self, stage: str = "") -> str:
        """获取记忆文本(供 LLM prompt 注入)。"""
        memories = self.get_relevant_memories(stage=stage)
        if not memories:
            return ""

        # Also get cross-stage generalizable lessons
        all_memories = self.get_relevant_memories()
        generalizable = [m for m in all_memories if m.get("generalizable") and m.get("failure_mode") != "clear"]

        lines = ["自反思记忆:"]
        
        if stage:
            stage_mems = [m for m in memories if m.get("stage") == stage]
            if stage_mems:
                lines.append(f"  本关({stage})尝试记录:")
                for m in stage_mems[-5:]:  # last 5
                    lines.append(f"    attempt {m['attempt']}: {m['outcome']} ({m['failure_mode']})")
                    if m.get("root_cause"):
                        lines.append(f"      原因: {m['root_cause'][:60]}")
                    if m.get("lesson"):
                        lines.append(f"      教训: {m['lesson'][:60]}")

        # Cross-stage lessons (generalizable)
        if generalizable:
            seen = set()
            unique = []
            for m in generalizable:
                key = m.get("lesson", "")[:20]
                if key not in seen:
                    seen.add(key)
                    unique.append(m)
            if unique:
                lines.append("  跨关通用教训:")
                for m in unique[:10]:
                    lines.append(f"    [{m['failure_mode']}] {m['lesson'][:60]}")

        return "\n".join(lines) if len(lines) > 1 else ""

    def _check_promote(self, entry: MemoryEntry) -> None:
        """检查同类失败是否 ≥2 次 → promote 到 principles。"""
        same_mode = self.get_relevant_memories(failure_mode=entry.failure_mode)
        same_mode = [m for m in same_mode if m.get("stage") == entry.stage]
        if len(same_mode) >= 2:
            # Promote: add to principles
            self._promote_to_principles(entry)

    def _promote_to_principles(self, entry: MemoryEntry) -> None:
        """将反复出现的教训提升为原则。"""
        if not os.path.exists(PRINCIPLES_PATH):
            return
        with open(PRINCIPLES_PATH, encoding="utf-8") as f:
            p_data = json.load(f)
        principles = p_data.get("principles", [])
        
        # Check if lesson already exists
        for p in principles:
            if entry.lesson[:20] in p.get("pattern", ""):
                return  # Already exists

        # Find max ID
        max_id = 0
        for p in principles:
            pid = p.get("id", "P000")
            try:
                num = int(pid[1:])
                max_id = max(max_id, num)
            except ValueError:
                pass

        new_principle = {
            "id": f"P{max_id + 1:03d}",
            "pattern": entry.lesson,
            "condition": f"Stage: {entry.stage}, Mode: {entry.failure_mode}",
            "reason": entry.root_cause,
            "confidence": 0.7,  # Lower confidence for self-reflection
            "source": "self_reflection",
        }
        principles.append(new_principle)
        p_data["principles"] = principles
        with open(PRINCIPLES_PATH, "w", encoding="utf-8") as f:
            json.dump(p_data, f, ensure_ascii=False, indent=2)
        print(f"[memory] Promoted to principle: {new_principle['id']} ({entry.lesson[:40]})")


def classify_failure(sim_result: dict, job: dict) -> MemoryEntry:
    """从 sim 结果自动分类失败模式。"""
    failure = sim_result.get("failure", {})
    events = sim_result.get("events", [])
    
    # Determine outcome
    result = sim_result.get("result", "lose")
    if result == "win":
        outcome = "clear"
        failure_mode = "clear"
    else:
        outcome = "leak" if failure.get("leaks", 0) > 0 else "timeout"
        # Classify failure mode
        failure_mode = "timeout"  # default
        if failure.get("no_healing_targets", 0) > 0:
            failure_mode = "no_healing_coverage"
        elif failure.get("skill_not_ready", 0) > 0:
            failure_mode = "skill_not_ready"
        elif failure.get("leaks", 0) > 0:
            # Check if it's concentrated blockers or too slow
            operator_deaths = failure.get("operator_deaths", 0)
            if operator_deaths > 0:
                failure_mode = "too_slow_kills"
            else:
                failure_mode = "concentrated_blockers"
        elif "timeout" in str(failure.get("root_causes", [])):
            failure_mode = "timeout"

    # Build deployments list
    deployments = []
    for a in job.get("actions", []):
        if a.get("type") == "Deploy":
            deployments.append({
                "name": a.get("name", ""),
                "location": a.get("location", []),
                "direction": a.get("direction", ""),
            })

    # Root cause from sim analysis
    root_causes = failure.get("root_causes", [])
    root_cause = "; ".join(root_causes) if root_causes else "unknown"

    # Generate lesson
    lesson = _generate_lesson(failure_mode, root_cause, deployments, events)

    # Determine if generalizable
    generalizable = failure_mode not in ("clear",) and "specific" not in root_cause.lower()

    # Count attempts
    stage = job.get("stage_name", "unknown")
    store = MemoryStore()
    existing = store.get_stage_memories(stage)
    attempt_num = len(existing) + 1

    return MemoryEntry(
        stage=stage,
        attempt=attempt_num,
        deployments=deployments,
        outcome=outcome,
        failure_mode=failure_mode,
        root_cause=root_cause,
        lesson=lesson,
        generalizable=generalizable,
    )


def _generate_lesson(failure_mode: str, root_cause: str, deployments: list, events: list) -> str:
    """根据失败模式生成教训。"""
    if failure_mode == "clear":
        return "通关成功，当前作业有效"
    
    if failure_mode == "no_healing_coverage":
        # Find medic position
        medics = [d for d in deployments if "夜莺" in d.get("name", "") or "闪灵" in d.get("name", "")]
        if medics:
            m = medics[0]
            return f"医疗{m['name']}放在{m['location']}覆盖不到友方，应放在友方干员附近"
        return "医疗干员治疗范围覆盖不到友方，应调整位置"
    
    if failure_mode == "skill_not_ready":
        skill_events = [e for e in events if e.get("event") == "skill_not_ready"]
        if skill_events:
            op = skill_events[0].get("oper", "")
            sp = skill_events[0].get("sp", 0)
            needed = skill_events[0].get("needed", 0)
            return f"{op}技能SP={sp}/{needed}未就绪，Skill action应加kills条件等SP充满"
        return "技能未就绪就执行，应加kills条件延迟"
    
    if failure_mode == "concentrated_blockers":
        return "阻挡干员集中在一路导致其他路漏怪，应分散到各蓝门"
    
    if failure_mode == "too_slow_kills":
        return "击杀速度太慢，需要更强的输出或更好的技能选择"
    
    if failure_mode == "timeout":
        return "超时未清完，可能伤害不足或部署太晚"
    
    return f"失败: {root_cause[:60]}"


if __name__ == "__main__":
    # Test with sim result
    import json
    from src.sim.game_state import run_job
    
    with open("copilot_job_llm_enhanced.json", encoding="utf-8") as f:
        job = json.load(f)
    
    result = run_job("act44side_07", job)
    
    entry = classify_failure(result, job)
    print("=== Memory Entry ===")
    print(f"Stage: {entry.stage}")
    print(f"Attempt: {entry.attempt}")
    print(f"Outcome: {entry.outcome}")
    print(f"Failure mode: {entry.failure_mode}")
    print(f"Root cause: {entry.root_cause}")
    print(f"Lesson: {entry.lesson}")
    print(f"Generalizable: {entry.generalizable}")
    print()
    
    # Record
    store = MemoryStore()
    store.record(entry)
    
    # Show memory for prompt
    print()
    print("=== Memory for prompt ===")
    print(store.get_lessons_for_prompt("act44side_07"))
