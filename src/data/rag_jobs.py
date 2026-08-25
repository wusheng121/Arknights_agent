"""RAG: MAA 作业 JSON 索引 + prts.plus API 检索。

索引来源:
1. MAA 内置 copilot 作业 (resource/copilot/)
2. 自建通关缓存 (job_cache/)
3. prts.plus 社区作业 API (https://prts.maa.plus/copilot/query)

检索方式: 按 stage_name 精确匹配 + API 搜索
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass


PRTS_PLUS_API = "https://prts.maa.plus/copilot/query"


@dataclass
class IndexedJob:
    """索引的作业。"""
    file_path: str
    stage_name: str
    opers: list[dict]
    actions: list[dict]
    source: str  # "maa_builtin" / "cached" / "prts_plus"
    strategy_text: str = ""  # 攻略文本(来自 doc.details)


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

    if job.strategy_text:
        parts.append(f"攻略: {job.strategy_text[:200]}")

    return " | ".join(parts)


def index_expert_jobs(expert_dir: str = "") -> list[IndexedJob]:
    """索引 prts.plus 专家作业（本地文件）。"""
    if not expert_dir:
        expert_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "expert_jobs"
        )
    jobs = []
    if not os.path.isdir(expert_dir):
        return jobs
    for stage_dir_name in os.listdir(expert_dir):
        stage_dir = os.path.join(expert_dir, stage_dir_name)
        if not os.path.isdir(stage_dir):
            continue
        for f in os.listdir(stage_dir):
            if f.endswith(".json"):
                path = os.path.join(stage_dir, f)
                job = _parse_job_file(path, "expert")
                if job:
                    jobs.append(job)
    return jobs


def search_expert_jobs_by_stage(stage_name: str, expert_dir: str = "") -> list[IndexedJob]:
    """从专家作业库搜索指定关卡的作业。"""
    if not expert_dir:
        expert_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "expert_jobs"
        )
    results = []
    stage_dir = os.path.join(expert_dir, stage_name)
    if os.path.isdir(stage_dir):
        for f in os.listdir(stage_dir):
            if f.endswith(".json"):
                path = os.path.join(stage_dir, f)
                job = _parse_job_file(path, "expert")
                if job:
                    results.append(job)
    return results


def get_expert_jobs_context(stage_name: str, maa_path: str = "", cache_dir: str = "",
                            expert_dir: str = "") -> str:
    """获取专家作业上下文(供 RAG 检索用)。

    优先级:
    1. 本地专家作业 (prts.plus 爬取)
    2. 本地缓存/内置作业
    3. prts.plus API 实时搜索
    """
    parts = []

    # 1. 本地专家作业（最可靠，有完整 actions）
    expert_jobs = search_expert_jobs_by_stage(stage_name, expert_dir)
    for i, job in enumerate(expert_jobs[:5]):
        ctx = job_to_context(job)
        if ctx:
            parts.append(f"  专家作业{i+1}: {ctx}")

    # 2. 本地缓存/内置作业
    jobs = search_jobs_by_stage(stage_name, maa_path, cache_dir)
    for i, job in enumerate(jobs[:3]):
        ctx = job_to_context(job)
        if ctx:
            parts.append(f"  缓存作业{i+1}: {ctx}")

    # 3. prts.plus API 实时搜索（补充）
    if not expert_jobs:
        prts_jobs = search_prts_plus(stage_name)
        for i, job in enumerate(prts_jobs[:3]):
            ctx = job_to_context(job)
            if ctx:
                parts.append(f"  社区作业{i+1}: {ctx}")

    if parts:
        return "专家作业参考:\n" + "\n".join(parts)
    return ""


def search_prts_plus(stage_name: str, limit: int = 5) -> list[IndexedJob]:
    """从 prts.plus API 搜索社区作业。"""
    jobs = []
    try:
        params = urllib.parse.urlencode({
            "level_keyword": stage_name,
            "limit": limit,
            "page": 0,
            "desc": True,
        })
        url = f"{PRTS_PLUS_API}?{params}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "ArknightsAgent/1.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        raw_jobs = data.get("data", {}).get("data", [])
        for rj in raw_jobs:
            content = rj.get("content", "")
            if not content:
                continue
            try:
                jd = json.loads(content)
                jobs.append(IndexedJob(
                    file_path="",
                    stage_name=jd.get("stage_name", stage_name),
                    opers=jd.get("opers", []),
                    actions=jd.get("actions", []),
                    source="prts_plus",
                    strategy_text=jd.get("doc", {}).get("details", ""),
                ))
            except Exception:
                continue
    except Exception:
        pass
    return jobs


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
