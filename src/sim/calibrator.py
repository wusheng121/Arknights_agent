"""P2: sim-to-real 校准 — 对比 sim 结果 vs 真机结果。

流程:
1. 在 sim 中跑作业 → sim_result (kills/lives/events 时间线)
2. 在真机中跑同一作业 → real_result (MAA 执行 + BattleMonitor 感知)
3. 对比 → gap 报告
4. 根据 gap 建议 sim 参数调整

对比指标:
- 胜负匹配: sim 预测 win/lose vs 真机 win/lose
- 击杀时间线: sim 各 tick 的 kills vs 真机各时刻的 kills
- 漏怪数: sim leaks vs 真机 lives_left 差值
- 事件匹配: sim deploy/skill 事件 vs 真机 MAA CopilotAction 回调

输出:
- gap_report.json: 详细对比数据
- 建议: sim 参数调整方向
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

CALIBRATION_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "calibration")


@dataclass
class SimResult:
    """sim 执行结果。"""
    result: str  # win/lose
    ticks: int
    lives_left: int
    kills_timeline: list[tuple[int, int]] = field(default_factory=list)  # [(tick, kills)]
    events: list[dict] = field(default_factory=list)
    failure_analysis: dict = field(default_factory=dict)


@dataclass
class RealResult:
    """真机执行结果。"""
    result: str  # win/lose/unknown
    stars: int  # 0-3
    lives_left: int = -1
    kills_timeline: list[tuple[float, int]] = field(default_factory=list)  # [(timestamp, kills)]
    events: list[dict] = field(default_factory=list)
    battle_duration: float = 0.0


@dataclass
class GapReport:
    """sim-to-real gap 报告。"""
    stage: str
    job_name: str
    sim_result: str
    real_result: str
    result_match: bool  # 胜负是否匹配
    sim_ticks: int
    real_duration: float
    sim_lives: int
    real_lives: int
    sim_kills_final: int
    real_kills_final: int
    sim_events: list[dict]
    real_events: list[dict]
    gaps: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "job_name": self.job_name,
            "sim_result": self.sim_result,
            "real_result": self.real_result,
            "result_match": self.result_match,
            "sim_ticks": self.sim_ticks,
            "real_duration_s": round(self.real_duration, 1),
            "sim_lives_left": self.sim_lives,
            "real_lives_left": self.real_lives,
            "sim_kills_final": self.sim_kills_final,
            "real_kills_final": self.real_kills_final,
            "sim_events": self.sim_events,
            "real_events": self.real_events,
            "gaps": self.gaps,
            "suggestions": self.suggestions,
        }


def run_sim(stage_id: str, job: dict) -> SimResult:
    """在 sim 中跑作业。"""
    from src.sim.game_state import run_job

    result = run_job(stage_id, job)

    # 构建 kills 时间线
    kills_timeline = []
    kills = 0
    for e in result.get("events", []):
        if e.get("event") == "enemy_killed":
            kills += 1
        kills_timeline.append((e.get("tick", 0), kills))

    # 提取关键事件
    key_events = []
    for e in result.get("events", []):
        evt = e.get("event", "")
        if evt in ("deploy", "deploy_failed", "skill_used", "skill_not_ready",
                    "enemy_killed", "enemy_leaked", "operator_died", "warning"):
            key_events.append({
                "tick": e.get("tick", 0),
                "event": evt,
                "details": e.get("details", {}),
            })

    return SimResult(
        result=result["result"],
        ticks=result["ticks"],
        lives_left=result["lives_left"],
        kills_timeline=kills_timeline,
        events=key_events,
        failure_analysis=result.get("failure", {}),
    )


async def run_real(job_path: str, stage: str, job_data: dict, maa_path: str, adb: str, addr: str) -> RealResult:
    """在真机中跑同一作业 (调用 _run_maa_copilot + BattleMonitor)。"""
    from src.game.battle_monitor import BattleMonitor
    from src.game.maapy_client import MaapyClient, MSG_SUB_TASK_EXTRA_INFO
    import subprocess
    import cv2
    import numpy as np

    client = MaapyClient(resource_path=maa_path)

    # BattleMonitor for real-time perception
    monitor = BattleMonitor(
        job_actions=job_data.get("actions", []),
        check_interval=3.0,
    )
    monitor_events: list = []
    monitor.add_event_handler(lambda ev: monitor_events.append(ev))

    battle_started = False
    copilot_actions: list[dict] = []

    async def action_detector(ev):
        nonlocal battle_started
        if ev.msg == 20003 and ev.details.get("what") == "CopilotAction":
            battle_started = True
            copilot_actions.append(ev.details.get("details", {}))

    client.add_handler(action_detector)

    ok = await client.connect(adb, addr)
    if not ok:
        return RealResult(result="error", stars=0)

    await client.append("Copilot", {"filename": job_path, "formation": True, "formation_index": 0})
    await client.start()

    start_time = time.time()
    monitor_task = None

    while True:
        await asyncio.sleep(0.5)
        if not client.running():
            break
        if battle_started and monitor_task is None:
            monitor_task = asyncio.create_task(monitor.monitor_loop(timeout=300))

    await client.wait_done(timeout=300)
    if monitor_task:
        monitor.stop()
        try:
            await asyncio.wait_for(monitor_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            monitor_task.cancel()

    duration = time.time() - start_time

    # 胜负检测
    stars = 0
    try:
        r = subprocess.run([adb, "-s", addr, "exec-out", "screencap", "-p"], capture_output=True, timeout=10)
        arr = np.frombuffer(r.stdout, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            for name, s in [("StageDrops-Stars-3.png", 3), ("StageDrops-Stars-2.png", 2)]:
                path = os.path.join(maa_path, "resource", "template", "Battle", "StageDrops", name)
                if os.path.exists(path):
                    t = cv2.imread(path)
                    if t is not None:
                        ts = cv2.resize(t, (int(t.shape[1]*1.5), int(t.shape[0]*1.5)))
                        res = cv2.matchTemplate(img, ts, cv2.TM_CCOEFF_NORMED)
                        _, mv, _, _ = cv2.minMaxLoc(res)
                        if mv > 0.7:
                            stars = s
                            break
    except Exception:
        pass

    result = "win" if stars >= 2 else ("lose" if stars == 0 else "unknown")

    # 提取 kills 时间线 from monitor events
    kills_timeline = []
    for ev in monitor_events:
        if ev.event_type == "kills_update":
            kills_timeline.append((ev.timestamp - start_time, ev.data.get("kills", 0)))

    # 提取关键事件
    real_events = []
    for ev in monitor_events:
        real_events.append({
            "timestamp": round(ev.timestamp - start_time, 1),
            "event": ev.event_type,
            "message": ev.message,
            "data": ev.data,
        })

    return RealResult(
        result=result,
        stars=stars,
        kills_timeline=kills_timeline,
        events=real_events,
        battle_duration=duration,
    )


def compare_results(sim: SimResult, real: RealResult, stage: str, job_name: str) -> GapReport:
    """对比 sim vs real 结果。"""
    gaps: list[str] = []
    suggestions: list[str] = []

    # 1. 胜负匹配
    result_match = sim.result == real.result
    if not result_match:
        gaps.append(f"胜负不匹配: sim={sim.result} vs real={real.result}")
        if sim.result == "win" and real.result == "lose":
            suggestions.append("sim 过于乐观: sim 预测通关但真机失败。可能原因: 技能效果不全/敌人伤害更高/路径有差异")
        elif sim.result == "lose" and real.result == "win":
            suggestions.append("sim 过于保守: sim 预测失败但真机通关。可能原因: sim 敌人属性偏高/技能效果低估")

    # 2. 漏怪对比
    sim_lives = sim.lives_left
    real_lives = real.lives_left
    if real_lives >= 0 and sim_lives != real_lives:
        gaps.append(f"剩余生命不匹配: sim={sim_lives} vs real={real_lives}")

    # 3. 击杀数对比
    sim_kills = sim.kills_timeline[-1][1] if sim.kills_timeline else 0
    real_kills = real.kills_timeline[-1][1] if real.kills_timeline else -1
    if real_kills >= 0 and abs(sim_kills - real_kills) > 2:
        gaps.append(f"最终击杀数不匹配: sim={sim_kills} vs real={real_kills}")

    # 4. 时间对比
    sim_duration = sim.ticks * 0.1  # sim 0.1s/tick
    real_duration = real.battle_duration
    if real_duration > 0:
        ratio = real_duration / sim_duration if sim_duration > 0 else 0
        if ratio > 2 or ratio < 0.5:
            gaps.append(f"时间差异大: sim={sim_duration:.1f}s vs real={real_duration:.1f}s (ratio={ratio:.2f})")
            suggestions.append(f"时间比例={ratio:.2f}: 考虑调整 sim tick_interval 或 DP 回复速度")

    # 5. 事件对比
    sim_deploy_count = sum(1 for e in sim.events if e.get("event") == "deploy")
    sim_skill_count = sum(1 for e in sim.events if e.get("event") == "skill_used")
    sim_leak_count = sum(1 for e in sim.events if e.get("event") == "enemy_leaked")
    sim_death_count = sum(1 for e in sim.events if e.get("event") == "operator_died")

    real_action_count = len(real.events)
    gaps.append(f"sim 事件: deploy={sim_deploy_count} skill={sim_skill_count} leak={sim_leak_count} death={sim_death_count}")
    gaps.append(f"real 事件: {real_action_count} 条 (kills_timeline={len(real.kills_timeline)})")

    # 6. 漏怪根因
    if sim_leak_count > 0 and real.result == "win":
        gaps.append(f"sim 预测漏怪{sim_leak_count}次但真机通关: sim 阻挡/伤害计算可能不准确")
        suggestions.append("检查 sim 阻挡逻辑: 干员 block 值是否正确? 多个敌人同时到达是否正确处理?")

    if sim_death_count > 0 and real.result == "win":
        gaps.append(f"sim 预测干员死亡{sim_death_count}次但真机通关: sim 伤害计算可能偏高")
        suggestions.append("检查 sim 伤害公式: atk-def 还是 max(atk-def, atk*0.05)? 敌人 ATK 是否准确?")

    return GapReport(
        stage=stage,
        job_name=job_name,
        sim_result=sim.result,
        real_result=real.result,
        result_match=result_match,
        sim_ticks=sim.ticks,
        real_duration=real.battle_duration,
        sim_lives=sim_lives,
        real_lives=real_lives,
        sim_kills_final=sim_kills,
        real_kills_final=real_kills,
        sim_events=sim.events[-20:],
        real_events=real.events[-20:],
        gaps=gaps,
        suggestions=suggestions,
    )


async def calibrate(
    stage_id: str,
    job_path: str,
    job_data: dict,
    maa_path: str = "",
    adb: str = "",
    addr: str = "",
) -> GapReport:
    """完整校准流程: sim → real → 对比 → 报告。"""
    os.makedirs(CALIBRATION_DIR, exist_ok=True)

    job_name = os.path.basename(job_path)

    # 1. Run sim
    log.info("[calibrate] === Step 1: Run sim ===")
    sim = run_sim(stage_id, job_data)
    log.info("[calibrate] sim result: %s, ticks=%d (%.1fs), lives=%d, kills=%d",
             sim.result, sim.ticks, sim.ticks * 0.1, sim.lives_left,
             sim.kills_timeline[-1][1] if sim.kills_timeline else 0)

    # 2. Run real
    log.info("[calibrate] === Step 2: Run real ===")
    real = await run_real(job_path, stage_id, job_data, maa_path, adb, addr)
    log.info("[calibrate] real result: %s, stars=%d, duration=%.1fs",
             real.result, real.stars, real.battle_duration)

    # 3. Compare
    log.info("[calibrate] === Step 3: Compare ===")
    report = compare_results(sim, real, stage_id, job_name)

    # 4. Save report
    report_path = os.path.join(CALIBRATION_DIR, f"{stage_id}_{job_name}.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    log.info("[calibrate] Report saved: %s", report_path)

    # 5. Print summary
    print()
    print("=" * 60)
    print("  sim-to-real 校准报告")
    print("=" * 60)
    print(f"  关卡: {stage_id}")
    print(f"  作业: {job_name}")
    print(f"  sim 结果: {report.sim_result} (ticks={report.sim_ticks}, {report.sim_ticks*0.1:.1f}s)")
    print(f"  real 结果: {report.real_result} (stars={real.stars}, {report.real_duration:.1f}s)")
    print(f"  胜负匹配: {'✅' if report.result_match else '❌'}")
    print(f"  sim lives: {report.sim_lives} | real lives: {report.real_lives}")
    print(f"  sim kills: {report.sim_kills_final} | real kills: {report.real_kills_final}")
    print()
    print("  Gaps:")
    for g in report.gaps:
        print(f"    - {g}")
    print()
    if report.suggestions:
        print("  建议:")
        for s in report.suggestions:
            print(f"    → {s}")
    print("=" * 60)

    return report


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Load .env
    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k] = v

    stage = sys.argv[1] if len(sys.argv) > 1 else "1-7"

    # Resolve stage
    from src.data.stage_util import resolve_stage
    stage_id, _ = resolve_stage(stage)

    # Find expert job
    from src.data.rag_jobs import find_expert_job
    job_path, job_data, _ = find_expert_job(stage_id)

    if not job_path:
        # Use copilot_job.json
        job_path = os.path.join(os.path.dirname(__file__), "..", "..", "copilot_job.json")
        with open(job_path, encoding="utf-8") as f:
            job_data = json.load(f)

    maa = os.getenv("MAA_RESOURCE_PATH", r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64")
    adb = os.getenv("MAA_ADB_PATH", r"C:\Program Files\Netease\MuMu\nx_main\adb.exe")
    addr = os.getenv("MAA_ADDRESS", "127.0.0.1:16384")

    asyncio.run(calibrate(stage_id, job_path, job_data, maa, adb, addr))
