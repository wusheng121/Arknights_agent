"""安全网: 战斗实时监控 + 应急干预。

架构:
  MAA 执行 Copilot 作业（主流程）
       ↓ 同时
  BattleMonitor.monitor_loop():
    1. ADB 截图
    2. CV 感知 (check_in_battle / skill_ready / kills / costs / deployment)
    3. 异常检测 (干员血量低 / 技能没开 / 击杀太慢 / 敌人接近蓝门)
    4. 应急干预 (ADB tap 撤退/技能/部署)
    5. 事件发射 (供 AI 主播解说生成器消费)

事件类型:
  - battle_start: 战斗开始
  - battle_end: 战斗结束 (win/lose)
  - deploy: 部署干员
  - skill_ready: 技能就绪
  - skill_used: 技能使用
  - hp_low: 干员血量低
  - kills_update: 击杀数更新
  - anomaly: 检测到异常
  - intervene: 应急干预执行
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import cv2
import numpy as np

from src.game.cv_perception import CVPerception
from src.game.skill_detector import detect_skill_state

log = logging.getLogger(__name__)

ADB_PATH = os.getenv("MAA_ADB_PATH", r"C:\Program Files\Netease\MuMu\nx_main\adb.exe")
MAA_ADDRESS = os.getenv("MAA_ADDRESS", "127.0.0.1:16384")
MAA_RES = os.getenv("MAA_RESOURCE_PATH", r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\resource")


@dataclass
class BattleState:
    """某一时刻的战斗状态快照。"""
    in_battle: bool = False
    kills: int = -1
    costs: int = -1
    skill_states: dict[str, str] = field(default_factory=dict)  # {oper_name: "not_ready"|"ready"|"active"}
    deployment_count: int = 0
    timestamp: float = 0.0


@dataclass
class Anomaly:
    """检测到的异常。"""
    type: str  # hp_low | skill_not_used | kills_too_low | not_in_battle
    severity: str  # critical | warning | info
    message: str
    data: dict = field(default_factory=dict)


@dataclass
class BattleEvent:
    """战斗事件(供 AI 主播解说)。"""
    event_type: str
    message: str
    data: dict = field(default_factory=dict)
    timestamp: float = 0.0


class BattleMonitor:
    """安全网: 战斗实时监控 + 应急干预。

    用法:
        monitor = BattleMonitor(tile_calc, job_actions)
        await monitor.start_battle()
        # monitor_loop 在后台跑
        await monitor.wait_battle_end(timeout=300)
        result = monitor.get_result()
    """

    def __init__(
        self,
        tile_calc=None,
        job_actions: list[dict] | None = None,
        check_interval: float = 3.0,
    ) -> None:
        self.tile_calc = tile_calc
        self.job_actions = job_actions or []
        self.check_interval = check_interval

        self.cv = CVPerception(scale=1.5)
        self._running = False
        self._battle_active = False
        self._result: str | None = None
        self._events: list[BattleEvent] = []
        self._event_handlers: list[Callable[[BattleEvent], Any]] = []
        self._state: BattleState | None = None
        self._last_kills: int = -1
        self._expected_kills: int = 0  # 从 job_actions 估算
        self._battle_start_time: float = 0.0
        self._last_screenshot: np.ndarray | None = None

        # 估算预期击杀进度(从 job_actions 的 kills 条件)
        for a in self.job_actions:
            k = a.get("kills", 0)
            if k > self._expected_kills:
                self._expected_kills = k

    def add_event_handler(self, handler: Callable[[BattleEvent], Any]) -> None:
        """注册事件处理器(供 AI 主播解说生成器)。"""
        self._event_handlers.append(handler)

    def _emit(self, event_type: str, message: str, data: dict | None = None) -> None:
        """发射事件。"""
        ev = BattleEvent(
            event_type=event_type,
            message=message,
            data=data or {},
            timestamp=time.time(),
        )
        self._events.append(ev)
        for h in self._event_handlers:
            try:
                h(ev)
            except Exception:
                log.exception("event handler error: %s", event_type)

    def _capture_screenshot(self) -> np.ndarray | None:
        """ADB 截图。"""
        try:
            r = subprocess.run(
                [ADB_PATH, "-s", MAA_ADDRESS, "exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=10,
            )
            arr = np.frombuffer(r.stdout, dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            log.warning("截图失败: %s", e)
            return None

    def _check_battle_end(self, img: np.ndarray) -> str | None:
        """检测战斗是否结束。"""
        templ_dir = os.path.join(MAA_RES, "template", "Battle", "StageDrops")
        for name, result in [("EndOfAction.png", "win"), ("StageDrops-Stars-3.png", "win")]:
            path = os.path.join(templ_dir, name)
            if os.path.exists(path):
                t = cv2.imread(path)
                if t is not None:
                    ts = cv2.resize(t, (int(t.shape[1] * 1.5), int(t.shape[0] * 1.5)))
                    res = cv2.matchTemplate(img, ts, cv2.TM_CCOEFF_NORMED)
                    _, mv, _, _ = cv2.minMaxLoc(res)
                    if mv > 0.7:
                        return result
        # 检测失败画面
        for name in ["BattleFailed.png", "BattleDefeat.png"]:
            path = os.path.join(MAA_RES, "template", "Battle", name)
            if os.path.exists(path):
                t = cv2.imread(path)
                if t is not None:
                    ts = cv2.resize(t, (int(t.shape[1] * 1.5), int(t.shape[0] * 1.5)))
                    res = cv2.matchTemplate(img, ts, cv2.TM_CCOEFF_NORMED)
                    _, mv, _, _ = cv2.minMaxLoc(res)
                    if mv > 0.7:
                        return "lose"
        return None

    def _detect_kills(self, img: np.ndarray) -> int:
        """检测击杀数。"""
        kills_flag = os.path.join(MAA_RES, "template", "Battle", "BattleFlag", "BattleKillsFlag.png")
        if not os.path.exists(kills_flag):
            return -1
        t = cv2.imread(kills_flag)
        if t is None:
            return -1
        ts = cv2.resize(t, (int(t.shape[1] * 1.5), int(t.shape[0] * 1.5)))
        res = cv2.matchTemplate(img, ts, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if mv < 0.6:
            return -1
        # kills 数字在 flag 右侧
        fx, fy = ml[0], ml[1]
        cw, ch = int(60 * 1.5), int(25 * 1.5)
        roi = img[fy:fy + ch, fx + int(ts.shape[1]):fx + int(ts.shape[1]) + cw]
        return self.cv._ocr_digits(roi)

    def _perceive(self, img: np.ndarray) -> BattleState:
        """CV 感知 → BattleState。"""
        if img is None:
            return BattleState(in_battle=False)

        in_battle = self.cv.check_in_battle(img)
        kills = self._detect_kills(img) if in_battle else -1
        costs = self.cv.update_cost(img) if in_battle else -1
        deployment = self.cv.update_deployment(img) if in_battle else []

        # 技能状态(全局搜索)
        skill_state = "unknown"
        if in_battle:
            skill_state = detect_skill_state(img)

        return BattleState(
            in_battle=in_battle,
            kills=kills,
            costs=costs,
            skill_states={"_global": skill_state} if skill_state != "unknown" else {},
            deployment_count=len(deployment),
            timestamp=time.time(),
        )

    def _detect_anomalies(self, state: BattleState, img: np.ndarray) -> list[Anomaly]:
        """检测异常。"""
        anomalies: list[Anomaly] = []

        # 1. 不在战斗中(战斗可能已结束或未开始)
        if not state.in_battle:
            if self._battle_active:
                # 检测战斗是否结束
                end_result = self._check_battle_end(img)
                if end_result:
                    anomalies.append(Anomaly(
                        type="battle_end",
                        severity="info",
                        message=f"战斗结束: {end_result}",
                        data={"result": end_result},
                    ))
                else:
                    anomalies.append(Anomaly(
                        type="not_in_battle",
                        severity="warning",
                        message="不在战斗中(可能已结束或未开始)",
                    ))
            return anomalies

        # 2. 技能就绪但没开
        for name, sstate in state.skill_states.items():
            if sstate == "ready":
                anomalies.append(Anomaly(
                    type="skill_not_used",
                    severity="warning",
                    message=f"技能就绪但未使用({name})",
                    data={"oper": name, "skill_state": sstate},
                ))

        # 3. 击杀数进度检查(如果有预期)
        if self._expected_kills > 0 and state.kills >= 0:
            elapsed = time.time() - self._battle_start_time
            if elapsed > 30 and state.kills < self._expected_kills * 0.3:
                anomalies.append(Anomaly(
                    type="kills_too_low",
                    severity="warning",
                    message=f"击杀进度过慢: {state.kills}/{self._expected_kills} (用时{elapsed:.0f}s)",
                    data={"kills": state.kills, "expected": self._expected_kills, "elapsed": elapsed},
                ))

        # 4. 击杀数更新事件
        if state.kills >= 0 and state.kills != self._last_kills:
            if self._last_kills >= 0:
                delta = state.kills - self._last_kills
                self._emit("kills_update", f"击杀数: {state.kills}", {"kills": state.kills, "delta": delta})
            self._last_kills = state.kills

        return anomalies

    async def _intervene(self, anomaly: Anomaly) -> None:
        """应急干预。"""
        if anomaly.type == "skill_not_used":
            # 技能就绪 → 点技能按钮
            if self.tile_calc:
                try:
                    sx, sy = self.tile_calc.get_skill_screen_pos()
                    self._adb_tap(sx, sy)
                    self._emit("intervene", f"应急开技能: ADB tap ({sx},{sy})", {"action": "skill", "pos": [sx, sy]})
                    log.info("[safety] 应急开技能: (%d,%d)", sx, sy)
                except Exception as e:
                    log.warning("[safety] 开技能失败: %s", e)

        elif anomaly.type == "battle_end":
            self._result = anomaly.data.get("result", "unknown")
            self._battle_active = False
            self._emit("battle_end", f"战斗结束: {self._result}", {"result": self._result})

    def _adb_tap(self, x: int, y: int) -> None:
        """ADB 点击。"""
        subprocess.run(
            [ADB_PATH, "-s", MAA_ADDRESS, "shell", "input", "tap", str(x), str(y)],
            capture_output=True,
            timeout=5,
        )

    async def monitor_loop(self, timeout: float = 300.0) -> None:
        """安全网监控循环(在后台跑)。"""
        self._battle_start_time = time.time()
        self._battle_active = True
        self._running = True
        self._emit("battle_start", "战斗开始", {"actions": len(self.job_actions)})

        deadline = time.time() + timeout
        while self._running and self._battle_active and time.time() < deadline:
            img = self._capture_screenshot()
            self._last_screenshot = img

            if img is not None:
                state = self._perceive(img)
                self._state = state

                anomalies = self._detect_anomalies(state, img)
                for a in anomalies:
                    self._emit("anomaly", a.message, {"type": a.type, "severity": a.severity, "data": a.data})
                    if a.severity in ("critical", "warning"):
                        await self._intervene(a)

            await asyncio.sleep(self.check_interval)

        self._running = False
        if self._result is None:
            self._result = "timeout"
            self._emit("battle_end", "战斗超时", {"result": "timeout"})

    def get_result(self) -> str | None:
        """获取战斗结果。"""
        return self._result

    def get_events(self) -> list[BattleEvent]:
        """获取所有事件(供 AI 主播解说)。"""
        return self._events

    def get_last_state(self) -> BattleState | None:
        """获取最后感知状态。"""
        return self._state

    def get_last_screenshot(self) -> np.ndarray | None:
        """获取最后截图。"""
        return self._last_screenshot

    def stop(self) -> None:
        """停止监控。"""
        self._running = False
        self._battle_active = False


if __name__ == "__main__":
    # 测试: 截图 + 感知
    monitor = BattleMonitor()

    img = monitor._capture_screenshot()
    if img is not None:
        print("截图: %dx%d" % (img.shape[1], img.shape[0]))
        state = monitor._perceive(img)
        print("在战斗中: %s" % state.in_battle)
        print("击杀数: %d" % state.kills)
        print("费用: %d" % state.costs)
        print("技能状态: %s" % state.skill_states)
        print("待部署数: %d" % state.deployment_count)
    else:
        print("截图失败")
