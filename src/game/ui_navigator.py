"""UI 导航器: 自主完成所有界面跳转。

检测当前界面 → 导航到目标界面。

界面类型:
  home:         主界面
  stage_select: 关卡选择界面
  formation:    编队/战斗准备界面 (有开始作战按钮)
  battle:       战斗中 (有 HP flag)
  results:      结算界面 (有 Stars)
  unknown:      未知界面

导航路径:
  home → stage_select: tap "作战" 按钮
  stage_select → formation: tap stage → tap "进入作战"
  formation → battle: MAA Copilot 处理
  battle → results: 等待战斗结束
  results → stage_select: tap 关闭结算
  any → home: tap 返回按钮
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Literal

import cv2
import numpy as np

log = logging.getLogger(__name__)

ADB = os.getenv("MAA_ADB_PATH", r"C:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe")
ADDR = os.getenv("MAA_ADDRESS", "127.0.0.1:16384")
MAA = os.getenv("MAA_RESOURCE_PATH", r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64")
TEMPLATE_DIR = os.path.join(MAA, "resource", "template")
SCALE = 1.5  # MAA 1280x720 → MuMu 1920x1080

ScreenType = Literal["home", "stage_select", "formation", "battle", "results", "unknown"]


def _load_template(rel_path: str) -> np.ndarray | None:
    """加载模板并缩放到目标分辨率。"""
    path = os.path.join(TEMPLATE_DIR, rel_path)
    if not os.path.exists(path):
        return None
    t = cv2.imread(path)
    if t is None:
        return None
    return cv2.resize(t, (int(t.shape[1] * SCALE), int(t.shape[0] * SCALE)))


def _match(image: np.ndarray, template: np.ndarray, threshold: float = 0.7) -> tuple[float, tuple[int, int] | None]:
    """模板匹配，返回 (score, center_pos)。"""
    if image is None or template is None:
        return 0.0, None
    res = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, mv, _, ml = cv2.minMaxLoc(res)
    if mv >= threshold:
        cx = ml[0] + template.shape[1] // 2
        cy = ml[1] + template.shape[0] // 2
        return mv, (cx, cy)
    return mv, None


class UINavigator:
    """UI 导航器: 自主完成界面跳转。"""

    def __init__(self) -> None:
        self._templates = {
            "home": _load_template("QuickSwitch/Home.png"),
            "return": _load_template("ReturnButton/Return.png"),
            "battle_start": _load_template("Battle/StartButton/BattleStartNormal.png"),
            "hp_flag": _load_template("Battle/BattleFlag/BattleHpFlag.png"),
            "stars3": _load_template("Battle/StageDrops/StageDrops-Stars-3.png"),
            "stars2": _load_template("Battle/StageDrops/StageDrops-Stars-2.png"),
            "opers_flag": _load_template("Battle/BattleFlag/BattleOpersFlag.png"),
            "end_action": _load_template("Battle/BattleFlag/EndOfAction.png"),
        }

    def screenshot(self) -> np.ndarray | None:
        """ADB 截图。"""
        try:
            r = subprocess.run(
                [ADB, "-s", ADDR, "exec-out", "screencap", "-p"],
                capture_output=True, timeout=10,
            )
            arr = np.frombuffer(r.stdout, dtype=np.uint8)
            return cv2.imdecode(arr, cv2.IMREAD_COLOR)
        except Exception as e:
            log.warning("截图失败: %s", e)
            return None

    def _adb_tap(self, x: int, y: int) -> None:
        """ADB 点击。"""
        subprocess.run(
            [ADB, "-s", ADDR, "shell", "input", "tap", str(x), str(y)],
            capture_output=True, timeout=5,
        )
        log.info("ADB tap: (%d, %d)", x, y)

    def _adb_swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> None:
        """ADB 滑动。"""
        subprocess.run(
            [ADB, "-s", ADDR, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms)],
            capture_output=True, timeout=5,
        )

    def detect_screen(self, img: np.ndarray | None = None) -> ScreenType:
        """检测当前界面类型。"""
        if img is None:
            img = self.screenshot()
        if img is None:
            return "unknown"

        # 1. Results screen (Stars)
        for name in ["stars3", "stars2", "end_action"]:
            t = self._templates.get(name)
            if t is not None:
                mv, _ = _match(img, t, 0.65)
                if mv > 0.65:
                    return "results"

        # 2. Battle screen (HP flag)
        t = self._templates.get("hp_flag")
        if t is not None:
            mv, _ = _match(img, t, 0.6)
            if mv > 0.6:
                return "battle"

        # 3. Formation screen (Battle Start button)
        t = self._templates.get("battle_start")
        if t is not None:
            mv, pos = _match(img, t, 0.7)
            if mv > 0.7:
                return "formation"

        # 4. Home screen (Home button visible)
        t = self._templates.get("home")
        if t is not None:
            mv, _ = _match(img, t, 0.6)
            if mv > 0.6:
                return "home"

        # 5. Check for opers_flag (also indicates formation/battle prep)
        t = self._templates.get("opers_flag")
        if t is not None:
            mv, _ = _match(img, t, 0.6)
            if mv > 0.6:
                return "formation"

        return "unknown"

    def dismiss_results(self, img: np.ndarray | None = None) -> bool:
        """关闭结算界面。"""
        if img is None:
            img = self.screenshot()
        if img is None:
            return False

        screen = self.detect_screen(img)
        if screen != "results":
            return True  # Already dismissed

        # Tap center to dismiss results
        self._adb_tap(960, 540)
        time.sleep(1.5)

        # Verify
        img2 = self.screenshot()
        screen2 = self.detect_screen(img2)
        return screen2 != "results"

    def return_to_home(self) -> bool:
        """从任意界面回到主界面。"""
        for _ in range(5):
            img = self.screenshot()
            screen = self.detect_screen(img)
            if screen == "home":
                return True
            if screen == "results":
                self.dismiss_results(img)
                continue
            if screen == "battle":
                log.warning("在战斗中,无法返回主界面")
                return False
            # Tap return button or back
            t = self._templates.get("return")
            if t is not None:
                mv, pos = _match(img, t, 0.6)
                if pos:
                    self._adb_tap(*pos)
                    time.sleep(1.5)
                    continue
            # Try pressing back key
            subprocess.run([ADB, "-s", ADDR, "shell", "input", "keyevent", "4"], capture_output=True, timeout=5)
            time.sleep(1.5)

        return self.detect_screen() == "home"

    def navigate_to_formation(self, stage_code: str = "") -> bool:
        """从任意界面导航到编队/战斗准备界面。

        Args:
            stage_code: 关卡代码 (如 "1-7"),用于选择关卡
        Returns:
            True 如果成功到达编队界面
        """
        log.info("=== 导航到编队界面 (stage=%s) ===", stage_code)

        for attempt in range(10):
            img = self.screenshot()
            screen = self.detect_screen(img)
            log.info("  [attempt %d] 当前界面: %s", attempt + 1, screen)

            if screen == "formation":
                log.info("已到达编队界面!")
                return True

            if screen == "results":
                log.info("关闭结算界面...")
                self.dismiss_results(img)
                time.sleep(1)
                continue

            if screen == "battle":
                log.warning("在战斗中,等待战斗结束...")
                time.sleep(5)
                continue

            if screen == "home":
                # 点击"作战"按钮进入关卡选择
                # 作战按钮通常在主界面左下角
                # 先尝试用 StartButton1 模板匹配
                t = self._templates.get("battle_start")
                if t is not None:
                    mv, pos = _match(img, t, 0.5)
                    if pos:
                        self._adb_tap(*pos)
                        time.sleep(2)
                        continue
                # Fallback: 点击主界面"作战"按钮的固定位置
                # 1280x720 基准: (156, 564) → 1920x1080: (234, 846)
                self._adb_tap(234, 846)
                time.sleep(2)
                continue

            if screen == "stage_select" or screen == "unknown":
                # 尝试找到关卡并进入
                # 1. 尝试匹配 BattleStartNormal (如果当前有关卡选中)
                t = self._templates.get("battle_start")
                if t is not None:
                    mv, pos = _match(img, t, 0.6)
                    if pos:
                        log.info("找到开始作战按钮,点击...")
                        self._adb_tap(*pos)
                        time.sleep(2)
                        continue

                # 2. 尝试用 StartButton1 (关卡入口按钮)
                t1 = _load_template("Battle/StartButton/StartButton1.png")
                if t1 is not None:
                    mv, pos = _match(img, t1, 0.6)
                    if pos:
                        log.info("找到关卡入口按钮,点击...")
                        self._adb_tap(*pos)
                        time.sleep(2)
                        continue

                # 3. 如果知道关卡代码,尝试搜索
                if stage_code:
                    # 点击搜索框 → 输入关卡代码 → 选择
                    # 搜索按钮通常在右上角
                    self._adb_tap(1750, 150)  # 搜索按钮位置
                    time.sleep(1)
                    # 输入关卡代码
                    subprocess.run(
                        [ADB, "-s", ADDR, "shell", "input", "text", stage_code.replace("-", "")],
                        capture_output=True, timeout=5,
                    )
                    time.sleep(1)
                    # 点击搜索结果
                    self._adb_tap(960, 400)
                    time.sleep(2)
                    # 点击进入作战
                    t = self._templates.get("battle_start")
                    if t is not None:
                        img2 = self.screenshot()
                        mv, pos = _match(img2, t, 0.6)
                        if pos:
                            self._adb_tap(*pos)
                            time.sleep(2)
                            continue
                    continue

                # 4. Fallback: 点击屏幕中央(可能有关卡列表)
                self._adb_tap(960, 540)
                time.sleep(2)
                continue

        log.warning("导航失败: 10 次尝试后仍未到达编队界面")
        return False

    def cleanup_after_battle(self) -> None:
        """战斗结束后清理: 关闭结算 → 回到关卡选择。"""
        img = self.screenshot()
        screen = self.detect_screen(img)

        if screen == "results":
            log.info("关闭结算界面...")
            self.dismiss_results(img)
            time.sleep(1)

        # 如果还在战斗或未知界面,等待
        if screen == "battle":
            log.info("仍在战斗中,等待...")
            time.sleep(5)

    def get_screen_info(self) -> dict:
        """获取当前界面信息(用于日志)。"""
        img = self.screenshot()
        screen = self.detect_screen(img)
        return {"screen": screen, "has_image": img is not None}


if __name__ == "__main__":
    nav = UINavigator()
    info = nav.get_screen_info()
    print("Current screen: %s" % info["screen"])
