"""模式提取: 从专家作业中提取通用规律。

从 data/expert_jobs/ 下的所有作业 JSON 中提取:
1. 干员选择模式 (最常用干员、团队构成比例)
2. 技能选择模式 (每个干员常用几技能、skill_usage)
3. 部署顺序模式 (action 类型的序列规律)
4. 撤退时机模式 (kills/costs 条件)
5. 位置方向模式 (Deploy 的 location/direction 统计)

输出:
- data/patterns/strategy_knowledge.txt (文本,供 LLM prompt 注入)
- data/patterns/operator_stats.json (结构化数据)
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict


EXPERT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "expert_jobs")
PATTERN_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "patterns")


def load_all_jobs() -> list[dict]:
    """加载所有专家作业。"""
    jobs = []
    if not os.path.isdir(EXPERT_DIR):
        return jobs
    for stage_dir_name in os.listdir(EXPERT_DIR):
        stage_dir = os.path.join(EXPERT_DIR, stage_dir_name)
        if not os.path.isdir(stage_dir):
            continue
        for f in os.listdir(stage_dir):
            if f.endswith(".json"):
                path = os.path.join(stage_dir, f)
                try:
                    with open(path, encoding="utf-8") as fh:
                        jd = json.load(fh)
                    if jd.get("actions"):
                        jobs.append(jd)
                except Exception:
                    continue
    return jobs


def extract_operator_patterns(jobs: list[dict]) -> dict:
    """提取干员选择模式。"""
    oper_counter = Counter()
    oper_skill = defaultdict(Counter)  # oper_name -> skill -> count
    oper_skill_usage = defaultdict(Counter)
    team_sizes = []

    for job in jobs:
        opers = job.get("opers", [])
        # 也从 groups 提取
        for g in job.get("groups", []):
            for o in g.get("opers", []):
                opers.append(o)
        team_sizes.append(len(opers))
        for o in opers:
            name = o.get("name", "")
            if not name:
                continue
            oper_counter[name] += 1
            skill = o.get("skill", 0)
            if skill:
                oper_skill[name][skill] += 1
            su = o.get("skill_usage", 0)
            oper_skill_usage[name][su] += 1

    return {
        "most_used_operators": oper_counter.most_common(30),
        "operator_skills": {name: dict(skills) for name, skills in oper_skill.items()},
        "operator_skill_usage": {name: dict(su) for name, su in oper_skill_usage.items()},
        "team_size_avg": sum(team_sizes) / len(team_sizes) if team_sizes else 0,
        "team_size_distribution": dict(Counter(team_sizes)),
    }


def extract_action_patterns(jobs: list[dict]) -> dict:
    """提取部署顺序和撤退时机模式。"""
    # action 类型序列
    action_sequences = []
    # 撤退时机
    retreat_conditions = []
    # 技能时机
    skill_conditions = []
    # Deploy 方向统计
    direction_counter = Counter()

    for job in jobs:
        actions = job.get("actions", [])
        seq = [a.get("type", "?") for a in actions]
        action_sequences.append(seq)

        for a in actions:
            atype = a.get("type", "")
            if atype == "Retreat":
                cond = {}
                if a.get("kills"):
                    cond["kills"] = a["kills"]
                if a.get("costs"):
                    cond["costs"] = a["costs"]
                if cond:
                    retreat_conditions.append(cond)
            elif atype == "Skill":
                cond = {}
                if a.get("kills"):
                    cond["kills"] = a["kills"]
                if a.get("costs"):
                    cond["costs"] = a["costs"]
                if cond:
                    skill_conditions.append(cond)
                name = a.get("name", "")
            elif atype == "Deploy":
                direction = a.get("direction", "None")
                direction_counter[direction] += 1

    # 提取常见 action 序列模式 (前 3 个 action 类型)
    first_actions = Counter()
    for seq in action_sequences:
        if seq:
            first_actions[seq[0]] += 1
        if len(seq) >= 2:
            first_actions[tuple(seq[:2])] += 1
        if len(seq) >= 3:
            first_actions[tuple(seq[:3])] += 1

    return {
        "first_action_patterns": {str(k): v for k, v in first_actions.most_common(20)},
        "retreat_kills_distribution": Counter(c.get("kills", 0) for c in retreat_conditions).most_common(10),
        "skill_kills_distribution": Counter(c.get("kills", 0) for c in skill_conditions).most_common(10),
        "direction_distribution": direction_counter.most_common(10),
        "total_jobs": len(jobs),
    }


def generate_strategy_text(oper_patterns: dict, action_patterns: dict) -> str:
    """生成策略知识库文本(供 LLM prompt 注入)。"""
    lines = ["通用策略规律(从 %d 份专家作业中提取):" % action_patterns["total_jobs"]]
    lines.append("")

    # 1. 干员选择
    lines.append("【干员选择】")
    lines.append("  平均团队人数: %.1f" % oper_patterns["team_size_avg"])
    lines.append("  团队人数分布: %s" % oper_patterns["team_size_distribution"])
    lines.append("  最常用干员(top 15):")
    for name, count in oper_patterns["most_used_operators"][:15]:
        skills = oper_patterns["operator_skills"].get(name, {})
        skill_str = ", ".join("skill%d x%d" % (s, c) for s, c in sorted(skills.items()))
        lines.append("    %s (出现%d次, %s)" % (name, count, skill_str or "无技能数据"))
    lines.append("")

    # 2. 技能选择
    lines.append("【技能选择】")
    for name in [n for n, _ in oper_patterns["most_used_operators"][:10]]:
        skills = oper_patterns["operator_skills"].get(name, {})
        su = oper_patterns["operator_skill_usage"].get(name, {})
        if skills:
            best_skill = max(skills, key=skills.get)
            lines.append("  %s: 常用 skill%d (%d/%d次), skill_usage=%s" % (
                name, best_skill, skills[best_skill], sum(skills.values()),
                dict(su)))
    lines.append("")

    # 3. 部署顺序
    lines.append("【部署顺序】")
    lines.append("  第一个 action 分布: %s" % action_patterns["first_action_patterns"])
    lines.append("")

    # 4. 撤退时机
    lines.append("【撤退时机】")
    if action_patterns["retreat_kills_distribution"]:
        lines.append("  撤退时的 kills 分布: %s" % action_patterns["retreat_kills_distribution"])
    else:
        lines.append("  无撤退数据(多数作业用 SkillDaemon 挂机)")
    lines.append("")

    # 5. 技能时机
    lines.append("【技能时机】")
    if action_patterns["skill_kills_distribution"]:
        lines.append("  开技能时的 kills 分布: %s" % action_patterns["skill_kills_distribution"])
    else:
        lines.append("  多数用 skill_usage=1 自动开技能或 SkillDaemon")
    lines.append("")

    # 6. 方向
    lines.append("【方向选择】")
    lines.append("  方向分布: %s" % action_patterns["direction_distribution"])
    lines.append("")

    return "\n".join(lines)


def main():
    os.makedirs(PATTERN_DIR, exist_ok=True)

    print("加载专家作业...")
    jobs = load_all_jobs()
    print("  共 %d 份作业" % len(jobs))

    print("提取干员模式...")
    oper_patterns = extract_operator_patterns(jobs)

    print("提取 action 模式...")
    action_patterns = extract_action_patterns(jobs)

    print("生成策略知识库...")
    strategy_text = generate_strategy_text(oper_patterns, action_patterns)

    # 保存
    text_path = os.path.join(PATTERN_DIR, "strategy_knowledge.txt")
    with open(text_path, "w", encoding="utf-8") as f:
        f.write(strategy_text)
    print("  策略知识库: %s (%d chars)" % (text_path, len(strategy_text)))

    stats_path = os.path.join(PATTERN_DIR, "operator_stats.json")
    with open(stats_path, "w", encoding="utf-8") as f:
        json.dump({"operators": oper_patterns, "actions": action_patterns}, f, ensure_ascii=False, indent=2)
    print("  结构化数据: %s" % stats_path)

    print()
    print("=== 策略知识库预览 ===")
    print(strategy_text[:1000])


if __name__ == "__main__":
    main()
