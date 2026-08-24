"""RAG 统一检索器: wiki 攻略 + 作业 JSON 双源检索。

给定关卡代码,检索:
1. prts.wiki 攻略文本 (自然语言:推荐干员/位置/策略)
2. MAA 作业 JSON (结构化:精确位置/朝向/时机)
3. 自建缓存作业 (已验证通关)

输出紧凑文本,喂给 LLM 作为"专家知识"。
"""

from __future__ import annotations

import os

from src.data.rag_wiki import get_stage_guide
from src.data.rag_jobs import get_expert_jobs_context


def retrieve_context(
    stage_code: str,
    stage_id: str = "",
    maa_path: str = "",
    cache_dir: str = "",
) -> str:
    """检索关卡攻略+作业,返回紧凑上下文文本。

    Args:
        stage_code: 关卡显示码 (如 "1-7", "AT-7")
        stage_id: 关卡内部 ID (如 "main_01-07", "act44side_07")
        maa_path: MAA 安装路径
        cache_dir: 作业缓存目录

    Returns:
        "wiki攻略: ... | 专家作业: ..."
    """
    parts = []

    # 1. wiki 攻略
    wiki_guide = get_stage_guide(stage_code)
    if wiki_guide:
        parts.append("wiki攻略: " + wiki_guide)

    # 2. 专家作业 (按 stage_id 和 stage_code 都搜)
    expert_context = ""
    if stage_id:
        expert_context = get_expert_jobs_context(stage_id, maa_path, cache_dir)
    if not expert_context:
        expert_context = get_expert_jobs_context(stage_code, maa_path, cache_dir)
    if expert_context:
        parts.append(expert_context)

    return "\n".join(parts) if parts else ""


if __name__ == "__main__":
    MAA = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"
    cache = os.path.join(os.path.dirname(__file__), "..", "..", "job_cache")

    for stage in ["1-7", "AT-7"]:
        print(f"=== {stage} RAG 检索 ===")
        ctx = retrieve_context(stage, "", MAA, cache)
        print(ctx[:300] if ctx else "(无结果)")
        print()
