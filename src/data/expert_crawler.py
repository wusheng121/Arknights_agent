"""批量爬取 prts.plus 专家作业。

流程:
1. GET /copilot/query?level_keyword={stage_id} → 获取作业 ID 列表
2. GET /copilot/get/{id} → 获取完整作业 JSON
3. 筛选有 actions 的作业（过滤纯编队）
4. 存入 data/expert_jobs/{stage_id}/{job_id}.json
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

API_BASE = "https://prts.maa.plus"
EXPERT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "expert_jobs")


def query_job_ids(stage_id: str, limit: int = 20) -> list[dict]:
    """查询关卡的作业列表（返回元数据，content 可能不完整）。"""
    params = urllib.parse.urlencode({
        "level_keyword": stage_id,
        "limit": limit,
        "page": 0,
        "desc": True,
    })
    url = f"{API_BASE}/copilot/query?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "ArknightsAgent/1.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("data", {}).get("data", [])


def get_full_job(job_id: int) -> dict | None:
    """通过 ID 获取完整作业（content 含 actions）。"""
    url = f"{API_BASE}/copilot/get/{job_id}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "ArknightsAgent/1.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("data", {})
    except Exception:
        return None


def crawl_stage(stage_id: str, limit: int = 20, max_save: int = 10) -> int:
    """爬取单个关卡的专家作业。

    Returns: 保存的作业数量
    """
    # 1. 查询作业列表
    jobs_meta = query_job_ids(stage_id, limit)
    print(f"[{stage_id}] 查询到 {len(jobs_meta)} 份作业")

    # 2. 逐个获取完整内容
    saved = 0
    save_dir = os.path.join(EXPERT_DIR, stage_id)
    os.makedirs(save_dir, exist_ok=True)

    for meta in jobs_meta:
        job_id = meta.get("id")
        if job_id is None:
            continue

        # 检查是否已存在
        save_path = os.path.join(save_dir, f"{job_id}.json")
        if os.path.exists(save_path):
            saved += 1
            continue

        # 获取完整作业
        job = get_full_job(job_id)
        if not job:
            continue

        content = job.get("content", "")
        if not content:
            continue

        # 解析内容，筛选有 actions 的
        try:
            jd = json.loads(content)
        except Exception:
            continue

        actions = jd.get("actions", [])
        if len(actions) == 0:
            continue  # 跳过纯编队作业

        # 保存
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(jd, f, ensure_ascii=False, indent=2)

        opers = [o.get("name") for o in jd.get("opers", [])]
        print(f"  [{stage_id}] id={job_id} views={job.get('views',0)} actions={len(actions)} opers={opers}")
        saved += 1

        if saved >= max_save:
            break

    print(f"[{stage_id}] 保存 {saved} 份完整作业")
    return saved


def crawl_stages(stage_ids: list[str], limit: int = 20, max_save: int = 10) -> dict:
    """批量爬取多个关卡。"""
    results = {}
    for sid in stage_ids:
        count = crawl_stage(sid, limit, max_save)
        results[sid] = count
    return results


if __name__ == "__main__":
    import sys
    import time

    # 批量爬取所有主线关卡
    MAA = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"
    with open(os.path.join(MAA, "resource", "stages.json"), encoding="utf-8") as f:
        all_stages = json.load(f)

    # 主线 stage IDs
    stage_ids = [s["stageId"] for s in all_stages if s.get("stageId", "").startswith("main_")]
    stage_ids.sort()

    print("=== 批量爬取 %d 个主线关卡 ===" % len(stage_ids))
    total_saved = 0
    for i, sid in enumerate(stage_ids):
        print()
        print("[%d/%d] %s" % (i + 1, len(stage_ids), sid))
        count = crawl_stage(sid, limit=20, max_save=5)
        total_saved += count
        time.sleep(0.5)  # 避免请求过快

    print()
    print("=== 完成: %d 个关卡, %d 份作业 ===" % (len(stage_ids), total_saved))
