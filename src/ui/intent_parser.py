"""自然语言意图解析: 用户输入 → pipeline 参数。

支持的指令格式:
  "打1-7"           → {action: "run", stage: "1-7"}
  "用维什戴尔打CE-5" → {action: "run", stage: "CE-5", preferred_opers: ["维什戴尔"]}
  "换山为煌"         → {action: "modify", replace: {"山": "煌"}}
  "再打一次"         → {action: "rerun"}
  "显示记忆"         → {action: "show_memory"}
  "显示原则"         → {action: "show_principles"}
  "截图"             → {action: "screenshot"}
  "列表"             → {action: "list_stages"}
"""

from __future__ import annotations

import re
import logging

log = logging.getLogger(__name__)


def parse_intent(text: str) -> dict:
    """解析用户自然语言输入,返回 pipeline 参数。

    Returns:
        {"action": "run"|"modify"|"rerun"|"show_memory"|"show_principles"|"screenshot"|"list_stages"|"unknown",
         "stage": str (optional),
         "preferred_opers": list[str] (optional),
         "replace": dict (optional)}
    """
    text = text.strip()
    if not text:
        return {"action": "unknown", "raw": text}

    # 显示记忆/原则
    if "记忆" in text or "历史" in text:
        return {"action": "show_memory"}
    if "原则" in text or "规律" in text:
        return {"action": "show_principles"}

    # 截图
    if "截图" in text or "画面" in text or "看看" in text:
        return {"action": "screenshot"}

    # 列出关卡
    if "列表" in text or "有哪些" in text or "关卡" in text and "打" not in text:
        return {"action": "list_stages"}

    # 再打一次
    if "再打" in text or "重试" in text or "再来" in text:
        return {"action": "rerun"}

    # 换干员: "换山为煌" / "把山换成煌"
    m = re.search(r"换(.{1,6})为(.{1,6})|把(.{1,6})换成(.{1,6})", text)
    if m:
        old = (m.group(1) or m.group(3)).strip()
        new = (m.group(2) or m.group(4)).strip()
        return {"action": "modify", "replace": {old: new}}

    # 用X打Y: "用维什戴尔打CE-5"
    m = re.search(r"用(.{1,20}?)打(.{2,10})", text)
    if m:
        opers_str = m.group(1).strip()
        stage = m.group(2).strip()
        preferred_opers = [o.strip() for o in re.split(r"[,，、]", opers_str) if o.strip()]
        return {"action": "run", "stage": stage, "preferred_opers": preferred_opers}

    # 打X: "打1-7"
    m = re.search(r"打\s*([a-zA-Z0-9\-]+)", text)
    if m:
        stage = m.group(1).strip()
        return {"action": "run", "stage": stage}

    # 直接是关卡代码: "1-7", "CE-5"
    if re.match(r"^[a-zA-Z0-9\-]+$", text):
        return {"action": "run", "stage": text}

    return {"action": "unknown", "raw": text}


def format_intent_response(intent: dict) -> str:
    """把解析结果格式化为给用户的回复。"""
    action = intent.get("action", "unknown")
    if action == "unknown":
        return "无法理解指令。试试:\n• 打1-7\n• 用维什戴尔打CE-5\n• 换山为煌\n• 再打一次\n• 显示记忆"

    if action == "run":
        stage = intent.get("stage", "?")
        opers = intent.get("preferred_opers", [])
        if opers:
            return "收到: 打 {} (优先用 {})".format(stage, ", ".join(opers))
        return "收到: 打 {}".format(stage)

    if action == "modify":
        replace = intent.get("replace", {})
        parts = ["{} → {}".format(k, v) for k, v in replace.items()]
        return "收到: 换人 {}".format(", ".join(parts))

    if action == "rerun":
        return "收到: 再打一次"

    if action == "show_memory":
        return "显示记忆..."

    if action == "show_principles":
        return "显示原则..."

    if action == "screenshot":
        return "截图中..."

    if action == "list_stages":
        return "列出可用关卡..."

    return ""


if __name__ == "__main__":
    tests = [
        "打1-7",
        "用维什戴尔打CE-5",
        "用维什戴尔、山打1-7",
        "换山为煌",
        "把山换成煌",
        "再打一次",
        "显示记忆",
        "截图",
        "CE-5",
        "有哪些关卡",
    ]
    for t in tests:
        intent = parse_intent(t)
        print("{:20s} → {} | {}".format(t, intent, format_intent_response(intent)))
