"""Pass 2: 跨作业归纳通用规则。

从 Pass 1 的标注中归纳因果规则:
- "范围大的干员 → 路径汇合处"
- "先锋先放 → DP 回复"
- "skill2 持续 buff 适合持续波"

输出 data/patterns/principles.json
"""

from __future__ import annotations

import asyncio
import json
import os

ANNOTATION_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "annotations")
PRINCIPLES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "patterns", "principles.json")

PROMPT_PASS2 = """你是明日方舟战术知识蒸馏器。给定多份作业的因果标注(为什么这样部署),归纳通用规则。

每条规则必须包含:
- pattern: 规律描述(简洁)
- condition: 适用条件(什么时候用)
- reason: 因果原因(为什么有效)
- confidence: 置信度 0-1(基于标注一致性)

规则应该是跨关卡通用的,不是某个关卡特定的。
从标注中提取的常见模式:
- 位置选择: 高台放哪?地面放哪?为什么?
- 方向选择: 朝哪?为什么?
- 干员选择: 什么角色?为什么?
- 技能选择: 哪个技能?为什么?
- 部署顺序: 先下谁?为什么?
- 撤退时机: 何时撤?为什么?
- 技能时机: 何时开/关?为什么?

只输出 JSON: {"principles":[
  {"id":"P001","pattern":"...","condition":"...","reason":"...","confidence":0.85},
  ...
]}
"""


def load_all_annotations() -> list[dict]:
    """加载所有 Pass 1 标注。"""
    annotations = []
    if not os.path.isdir(ANNOTATION_DIR):
        return annotations
    for stage_dir_name in os.listdir(ANNOTATION_DIR):
        stage_dir = os.path.join(ANNOTATION_DIR, stage_dir_name)
        if not os.path.isdir(stage_dir):
            continue
        for f in os.listdir(stage_dir):
            if f.endswith(".json"):
                path = os.path.join(stage_dir, f)
                try:
                    with open(path, encoding="utf-8") as fh:
                        ann = json.load(fh)
                    annotations.append(ann)
                except Exception:
                    continue
    return annotations


def format_annotations_for_llm(annotations: list[dict], max_items: int = 50) -> str:
    """把标注格式化为 LLM 可读的文本。"""
    lines = []
    for ann in annotations[:max_items]:
        stage = ann.get("stage", "?")
        job = ann.get("job_file", "?")
        lines.append(f"--- {stage}/{job} ---")
        for a in ann.get("annotations", []):
            atype = a.get("type", "")
            if atype == "Deploy":
                operator = a.get("operator", "?")
                loc = a.get("location", [])
                direction = a.get("direction", "?")
                rp = a.get("reason_position", "")
                rd = a.get("reason_direction", "")
                ro = a.get("reason_operator", "")
                rs = a.get("reason_skill", "")
                lines.append(f"  Deploy {operator}@({loc}){direction}")
                if rp:
                    lines.append(f"    位置: {rp[:80]}")
                if rd:
                    lines.append(f"    方向: {rd[:80]}")
                if ro:
                    lines.append(f"    干员: {ro[:80]}")
                if rs:
                    lines.append(f"    技能: {rs[:80]}")
            elif atype == "Skill":
                rt = a.get("reason_skill_timing", a.get("reason", ""))
                if rt:
                    lines.append(f"  Skill: {rt[:80]}")
            elif atype == "Retreat":
                rr = a.get("reason_retreat", a.get("reason", ""))
                if rr:
                    lines.append(f"  Retreat: {rr[:80]}")
            elif atype == "SkillDaemon":
                rr = a.get("reason", "")
                if rr:
                    lines.append(f"  SkillDaemon: {rr[:80]}")
            elif atype == "SpeedUp":
                rr = a.get("reason", "")
                if rr:
                    lines.append(f"  SpeedUp: {rr[:80]}")
        lines.append("")
    return "\n".join(lines)


async def run_pass2():
    """运行 Pass 2 归纳。"""
    from openai import AsyncOpenAI

    key = os.getenv("DEEPSEEK_API_KEY")
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    mdl = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    if not key:
        print("DEEPSEEK_API_KEY not set")
        return

    client = AsyncOpenAI(api_key=key, base_url=base)

    # Load annotations
    annotations = load_all_annotations()
    print("Loaded %d annotations" % len(annotations))

    if not annotations:
        print("No annotations found. Run Pass 1 first.")
        return

    # Process in batches (50 annotations per call)
    all_principles = []
    batch_size = 50

    for i in range(0, len(annotations), batch_size):
        batch = annotations[i:i + batch_size]
        formatted = format_annotations_for_llm(batch)

        print("Batch %d/%d (%d annotations)..." % (
            i // batch_size + 1,
            (len(annotations) + batch_size - 1) // batch_size,
            len(batch)))

        user_content = "以下是%d份作业的因果标注:\n\n%s\n\n请归纳通用规则。" % (len(batch), formatted)

        try:
            resp = await client.chat.completions.create(
                model=mdl,
                messages=[
                    {"role": "system", "content": PROMPT_PASS2},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                extra_body={"thinking": {"type": "disabled"}},
            )
            content = resp.choices[0].message.content or "{}"
            result = json.loads(content)
            principles = result.get("principles", [])
            all_principles.extend(principles)
            print("  Got %d principles" % len(principles))
        except Exception as e:
            print("  Error: %s" % e)

    # Deduplicate by pattern similarity (simple: same first 20 chars)
    seen = set()
    unique_principles = []
    for p in all_principles:
        pattern = p.get("pattern", "")[:20]
        if pattern not in seen:
            seen.add(pattern)
            unique_principles.append(p)

    # Assign IDs
    for i, p in enumerate(unique_principles):
        p["id"] = "P%03d" % (i + 1)

    # Save
    os.makedirs(os.path.dirname(PRINCIPLES_PATH), exist_ok=True)
    with open(PRINCIPLES_PATH, "w", encoding="utf-8") as f:
        json.dump({"principles": unique_principles}, f, ensure_ascii=False, indent=2)

    print()
    print("=== Pass 2 done: %d unique principles ===" % len(unique_principles))
    print("Saved to: %s" % PRINCIPLES_PATH)
    print()
    for p in unique_principles[:10]:
        print("  %s: %s" % (p["id"], p["pattern"][:60]))
        print("    condition: %s" % p.get("condition", "")[:60])
        print("    reason: %s" % p.get("reason", "")[:60])
        print("    confidence: %s" % p.get("confidence", "?"))
        print()


if __name__ == "__main__":
    asyncio.run(run_pass2())
