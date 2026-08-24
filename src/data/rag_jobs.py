"""RAG: MAA 作业 JSON 索引 + 缓存作业检索。

索引来源:
1. MAA 内置 copilot 作业 (resource/copilot/)
2. 自建通关缓存 (job_cache/)

检索方式: 按 stage_name 精确匹配 + 内容相似度
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class IndexedJob:
    """索引的作业。"""
    file_path: str
    stage_name: str
    opers: list[dict]
    actions: list[dict]
    source: str  # "maa_builtin" / "cached"


def _parse_job_file(path: str, source: str) -> IndexedJob | None:
    """解析单个作业 JSON 文件。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        stage = data.get("stage_name", "")
        opers = data.get("opers", [])
        actions = data.get("actions", [])
        if not stage:
            return None
        return IndexedJob(
            file_path=path,
            stage_name=stage,
            opers=opers,
            actions=actions,
            source=source,
        )
    except Exception:
        return None


def index_maa_builtin(maa_path: str) -> list[IndexedJob]:
    """索引 MAA 内置 copilot 作业。"""
    copilot_dir = os.path.join(maa_path, "resource", "copilot")
    jobs = []
    if not os.path.isdir(copilot_dir):
        return jobs
    for root, dirs, files in os.walk(copilot_dir):
        for f in files:
            if f.endswith(".json"):
                path = os.path.join(root, f)
                job = _parse_job_file(path, "maa_builtin")
                if job:
                    jobs.append(job)
    return jobs


def index_cached_jobs(cache_dir: str) -> list[IndexedJob]:
    """索引缓存通关作业。"""
    jobs = []
    if not os.path.isdir(cache_dir):
        return jobs
    for f in os.listdir(cache_dir):
        if f.endswith(".json"):
            path = os.path.join(cache_dir, f)
            job = _parse_job_file(path, "cached")
            if job:
                jobs.append(job)
    return jobs


def search_jobs_by_stage(stage_name: str, maa_path: str = "", cache_dir: str = "") -> list[IndexedJob]:
    """按 stage_name 搜索作业。"""
    results = []
    # MAA 内置
    if maa_path:
        builtin = index_maa_builtin(maa_path)
        for job in builtin:
            if stage_name.lower() in job.stage_name.lower() or job.stage_name.lower() in stage_name.lower():
                results.append(job)
    # 缓存
    if cache_dir:
        cached = index_cached_jobs(cache_dir)
        for job in cached:
            if stage_name.lower() in job.stage_name.lower() or job.stage_name.lower() in stage_name.lower():
                results.append(job)
    return results


def job_to_context(job: IndexedJob) -> str:
    """把作业转为紧凑文本(供 LLM 参考)。"""
    parts = []
    parts.append(f"来源: {job.source}")
    parts.append(f"关卡: {job.stage_name}")

    if job.opers:
        oper_names = [o.get("name", "?") for o in job.opers]
        parts.append(f"干员: {', '.join(oper_names)}")

    if job.actions:
        action_summary = []
        for a in job.actions:
            if a.get("type") == "Deploy":
                name = a.get("name", "?")
                loc = a.get("location", [])
                direction = a.get("direction", "?")
                costs = a.get("costs", 0)
                action_summary.append(f"{name}@({loc[0]},{loc[1]}){direction}费用{costs}")
            elif a.get("type") == "Retreat":
                action_summary.append(f"撤退{a.get('name','?')}")
            elif a.get("type") == "SpeedUp":
                action_summary.append("加速")
            elif a.get("type") == "Skill":
                action_summary.append(f"技能{a.get('name','?')}")
        if action_summary:
            parts.append("操作: " + " → ".join(action_summary[:15]))

    return " | ".join(parts)


def get_expert_jobs_context(stage_name: str, maa_path: str = "", cache_dir: str = "") -> str:
    """获取专家作业上下文(供 RAG 检索用)。"""
    jobs = search_jobs_by_stage(stage_name, maa_path, cache_dir)
    if not jobs:
        return ""

    parts = ["专家作业参考:"]
    for i, job in enumerate(jobs[:3]):  # 最多取 3 份
        parts.append(f"  作业{i+1}: {job_to_context(job)}")
    return "\n".join(parts)


if __name__ == "__main__":
    MAA = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"
    cache = os.path.join(os.path.dirname(__file__), "..", "..", "job_cache")

    print("=== MAA 内置作业 ===")
    builtin = index_maa_builtin(MAA)
    print(f"共 {len(builtin)} 份")
    for job in builtin[:5]:
        print(f"  {job.stage_name} ({job.source})")

    print()
    print("=== 缓存作业 ===")
    cached = index_cached_jobs(cache)
    print(f"共 {len(cached)} 份")
    for job in cached:
        print(f"  {job.stage_name} ({job.source})")

    print()
    print("=== 搜索 1-7 ===")
    results = search_jobs_by_stage("1-7", MAA, cache)
    for job in results:
        print(f"  {job.stage_name} ({job.source})")
        print(f"  {job_to_context(job)[:120]}")
