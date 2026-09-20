"""异步桥接: pipeline → UI 事件流。

把 smoke_copilot_doc() 的各阶段包装为 AsyncGenerator,
在每个关键步骤 yield 一个事件 dict,供 Gradio 实时更新。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import AsyncGenerator

log = logging.getLogger(__name__)

MAA = os.getenv("MAA_RESOURCE_PATH", r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64")
ADB = os.getenv("MAA_ADB_PATH", r"C:\Program Files\Netease\MuMu\nx_main\adb.exe")
ADDR = os.getenv("MAA_ADDRESS", "127.0.0.1:16384")


def _env() -> None:
    """加载 .env 环境变量。"""
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ.setdefault(k, v)


def take_screenshot() -> str | None:
    """ADB 截图,返回文件路径。"""
    tmp_dir = os.path.join(os.path.dirname(__file__), "..", "..", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    shot_path = os.path.join(tmp_dir, "screenshot.png")
    try:
        subprocess.run(
            [ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
            stdout=open(shot_path, "wb"),
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if os.path.exists(shot_path) and os.path.getsize(shot_path) > 1000:
            return shot_path
    except Exception as e:
        log.error("截图失败: %s", e)
    return None


def get_memory_text() -> str:
    """获取记忆内容。"""
    try:
        from src.sim.memory import MemoryStore
        store = MemoryStore()
        import glob
        mem_files = glob.glob(os.path.join(store.memory_dir, "*.json"))
        if not mem_files:
            return "暂无记忆记录"
        lines = ["=== 战斗记忆 ==="]
        for mf in sorted(mem_files, key=os.path.getmtime, reverse=True)[:10]:
            stage = os.path.basename(mf).replace(".json", "")
            with open(mf, encoding="utf-8") as f:
                entries = json.load(f)
            for m in entries[-3:]:
                lines.append("  {} attempt={} {} {}".format(
                    stage, m.get("attempt", "?"), m.get("outcome", "?"),
                    m.get("lesson", "")[:60]))
        return "\n".join(lines)
    except Exception as e:
        return "读取记忆失败: {}".format(e)


def get_principles_text() -> str:
    """获取原则列表。"""
    try:
        path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "patterns", "principles.json")
        if not os.path.exists(path):
            return "暂无原则 (principles.json 不存在)"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        principles = data.get("principles", [])
        if not principles:
            return "暂无原则"
        lines = ["=== 因果原则 ({} 条) ===".format(len(principles))]
        for p in principles:
            lines.append("  [{}] {} (confidence:{})".format(
                p.get("id", "?"), p.get("pattern", "")[:60], p.get("confidence", "?")))
        return "\n".join(lines)
    except Exception as e:
        return "读取原则失败: {}".format(e)


def list_stages() -> str:
    """列出可用关卡。"""
    try:
        from src.data.stage_util import list_available_stages
        stages = list_available_stages(MAA)
        return "可用关卡 ({} 个):\n{}".format(
            len(stages), ", ".join(stages[:50])
        )
    except Exception as e:
        return "列出关卡失败: {}".format(e)


async def run_pipeline_stream(
    stage: str = "1-7",
    fresh: bool = True,
    preferred_opers: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """异步执行 pipeline,在各阶段 yield 事件。

    事件格式:
        {"stage": "rag"|"llm"|"sim"|"maa"|"done",
         "status": "running"|"done"|"error",
         "detail": "...",
         "data": dict (optional)}
    """
    _env()

    # 检查 API key
    if not os.getenv("DEEPSEEK_API_KEY"):
        yield {"stage": "error", "status": "error", "detail": "DEEPSEEK_API_KEY 未配置,请检查 .env"}
        return

    # 加载 operators.json
    operbox_path = os.path.join(os.path.dirname(__file__), "..", "..", "operators.json")
    if not os.path.exists(operbox_path):
        yield {"stage": "error", "status": "error", "detail": "operators.json 不存在,请先运行 --operbox"}
        return

    with open(operbox_path, encoding="utf-8") as f:
        operators = json.load(f)

    if preferred_opers:
        # 把优先干员排到前面
        operators = sorted(operators, key=lambda o: o.get("name", "") not in preferred_opers)

    yield {"stage": "rag", "status": "running", "detail": "检索专家作业 + RAG..."}

    try:
        from src.real_run import smoke_copilot_doc
        yield {"stage": "llm", "status": "running", "detail": "LLM 4步管道启动 (结构化数据)..."}

        # 用 monkeypatch 在 pipeline 各步打 yield 点
        # 简单方案: 直接跑 smoke_copilot_doc,通过 log handler 捕获
        import io
        import logging as _logging

        log_capture = []

        class _UIHandler(_logging.Handler):
            def emit(self, record):
                log_capture.append(record.getMessage())
                if len(log_capture) > 200:
                    log_capture.pop(0)

        handler = _UIHandler()
        handler.setLevel(_logging.INFO)
        _logging.getLogger().addHandler(handler)

        # 在后台任务跑 pipeline
        task = asyncio.create_task(smoke_copilot_doc(stage, fresh))
        last_yield_count = 0

        while not task.done():
            # 吐出新日志
            new_logs = log_capture[last_yield_count:]
            last_yield_count = len(log_capture)
            for line in new_logs:
                if "Step1" in line or "选中" in line:
                    yield {"stage": "llm", "status": "step1", "detail": line}
                elif "Step2" in line or "位置" in line:
                    yield {"stage": "llm", "status": "step2", "detail": line}
                elif "Step3" in line or "技能" in line:
                    yield {"stage": "llm", "status": "step3", "detail": line}
                elif "Step4" in line or "actions" in line:
                    yield {"stage": "llm", "status": "step4", "detail": line}
                elif "sim" in line.lower() or "PASSED" in line or "FAILED" in line:
                    yield {"stage": "sim", "status": "running", "detail": line}
                elif "MAA" in line or "Fight" in line or "Copilot" in line or "ChainStart" in line:
                    yield {"stage": "maa", "status": "running", "detail": line}
                elif "通关" in line or "Stars" in line:
                    yield {"stage": "done", "status": "win", "detail": line}
                elif "失败" in line or "LOSE" in line:
                    yield {"stage": "done", "status": "lose", "detail": line}

            await asyncio.sleep(1)

        _logging.getLogger().removeHandler(handler)

        # 检查结果
        if task.exception():
            yield {"stage": "error", "status": "error", "detail": str(task.exception())}
        else:
            # 吐出剩余日志
            new_logs = log_capture[last_yield_count:]
            for line in new_logs:
                if "通关" in line or "Stars" in line or "win" in line.lower():
                    yield {"stage": "done", "status": "win", "detail": line}
                elif "失败" in line or "lose" in line.lower():
                    yield {"stage": "done", "status": "lose", "detail": line}
                elif "记忆" in line or "P3" in line:
                    yield {"stage": "memory", "status": "done", "detail": line}

            yield {"stage": "done", "status": "done", "detail": "流程结束"}

    except Exception as e:
        yield {"stage": "error", "status": "error", "detail": str(e)}
        return

    # 读取最终作业 JSON
    job_path = os.path.join(os.path.dirname(__file__), "..", "..", "tmp", "copilot_job_runtime.json")
    if os.path.exists(job_path):
        with open(job_path, encoding="utf-8") as f:
            job = json.load(f)
        yield {"stage": "job", "status": "done", "detail": "作业已生成", "data": job}
