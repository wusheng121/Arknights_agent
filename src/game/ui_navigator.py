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

        # 1. Results screen (Stars) — 高阈值避免误匹配
        for name in ["stars3", "stars2", "end_action"]:
            t = self._templates.get(name)
            if t is not None:
                mv, _ = _match(img, t, 0.75)
                if mv > 0.75:
                    return "results"

        # 2. Battle screen — 需要 HP flag AND kills flag 同时匹配(避免主菜单误判)
        hp_t = self._templates.get("hp_flag")
        kills_t = self._templates.get("hp_flag")  # 用 hp_flag 做双重检查
        if hp_t is not None:
            mv_hp, _ = _match(img, hp_t, 0.75)
            if mv_hp > 0.75:
                # 额外检查: 战斗中应该有 opers_flag(部署面板)或 kills_flag
                opers_t = self._templates.get("opers_flag")
                if opers_t is not None:
                    mv_op, _ = _match(img, opers_t, 0.7)
                    if mv_op > 0.7:
                        return "battle"
                # HP flag 很高匹配但无 opers_flag → 可能是战斗中但部署区空
                if mv_hp > 0.85:
                    return "battle"

        # 3. Formation screen (Battle Start button OR Return button + no battle)
        t = self._templates.get("battle_start")
        if t is not None:
            mv, pos = _match(img, t, 0.75)
            if mv > 0.75:
                return "formation"
        # Formation sub-screen: Return button visible + opers_flag visible (not in battle)
        t_ret = self._templates.get("return")
        if t_ret is not None:
            mv_ret, _ = _match(img, t_ret, 0.8)
            if mv_ret > 0.8:
                # Return button = on a sub-screen, likely formation
                return "formation"

        # 4. Home screen — 多模板检测
        t = self._templates.get("home")
        if t is not None:
            mv, _ = _match(img, t, 0.55)
            if mv > 0.55:
                return "home"

        # 5. Opers flag only (formation without start button visible)
        t = self._templates.get("opers_flag")
        if t is not None:
            mv, _ = _match(img, t, 0.75)
            if mv > 0.75:
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
        """从任意界面回到主界面。只用 Return 按钮,不按 back key。"""
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
            # 只用 Return 按钮导航 (不按 back key,会触发退出游戏)
            t = self._templates.get("return")
            if t is not None:
                mv, pos = _match(img, t, 0.6)
                if pos:
                    self._adb_tap(*pos)
                    time.sleep(1.5)
                    continue
            # 找不到 Return 按钮,无法导航
            log.warning("找不到 Return 按钮,无法返回主界面")
            break

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
                # 点击"终端/作战"按钮进入关卡选择
                # MAA GoTerminalStopflag roi=[462, 600, 140, 120] → 中心 (532, 660) in 1280x720
                # 换算到 1920x1080 (×1.5): (798, 990)
                self._adb_tap(798, 990)
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

                # 3. 找不到关卡 → 导航交给 MAA Custom task
                log.info("未找到关卡入口,导航将交给 MAA")
                break

        log.warning("导航失败: 10 次尝试后仍未到达编队界面")
        return False

    def wait_for_game_start(self, timeout: int = 120) -> bool:
        """等待游戏启动完成: 加载→点击屏幕→开始游戏→主界面。

        处理 MAA StartUp 任务链的 ADB 等价:
        1. StartToWakeUp: roi=[520,460,240,100] → 点击屏幕跳过加载
        2. StartUpConnectingFlag: roi=[0,635,450,85] → 点击"开始游戏"按钮
        3. StartUp: roi=[227,327,159,152] → 检测到主界面

        Args:
            timeout: 最大等待秒数
        Returns:
            True 如果到达主界面
        """
        import time
        log.info("=== 等待游戏启动 ===")
        start_time = time.time()

        while time.time() - start_time < timeout:
            img = self.screenshot()
            if img is None:
                time.sleep(2)
                continue

            screen = self.detect_screen(img)
            log.info("  启动中... 当前界面: %s", screen)

            if screen == "home":
                log.info("=== 游戏启动完成(主界面) ===")
                return True

            if screen == "formation" or screen == "battle" or screen == "results":
                log.info("=== 已在游戏内(%s) ===", screen)
                return True

            # 1. StartToWakeUp: 点击屏幕中部跳过加载
            # roi=[520,460,240,100] → 中心 (640,510) in 1280x720 → (960,765) in 1920x1080
            # 检查是否有加载图标(LoadingIcon roi=[480,210,320,280])
            loading = self._match(img, _load_template("Battle/BattleFlag/BattleHpFlag.png"), 0.5)[0]
            # 尝试点击屏幕中心(跳过加载/启动画面)
            self._adb_tap(960, 540)
            time.sleep(2)

            # 再次截图检查
            img2 = self.screenshot()
            if img2 is not None:
                screen2 = self.detect_screen(img2)
                if screen2 == "home" or screen2 == "formation":
                    log.info("=== 游戏启动完成(%s) ===", screen2)
                    return True

                # 2. StartUpConnectingFlag: 点击"开始游戏"按钮
                # roi=[0,635,450,85] → 中心 (225,677) in 1280x720 → (337,1015) in 1920x1080
                # 在左下角区域点击
                self._adb_tap(337, 1015)
                time.sleep(3)

            time.sleep(2)

        log.warning("游戏启动超时(%ds)", timeout)
        return False

    def launch_game(self) -> bool:
        """启动明日方舟 app。"""
        log.info("=== 启动明日方舟 ===")
        try:
            subprocess.run(
                [ADB, "-s", ADDR, "shell", "monkey", "-p", "com.hypergryph.arknights", "1"],
                capture_output=True, timeout=15,
            )
            log.info("启动命令已发送")
            return True
        except Exception as e:
            log.error("启动游戏失败: %s", e)
            return False

    def wait_for_game_start(self, timeout: int = 120) -> bool:
        """等待游戏启动完成: 加载→点击屏幕→开始游戏→主界面。

        MAA StartUp 任务链的 ADB 等价:
        1. StartToWakeUp: roi=[520,460,240,100] → 点击屏幕跳过加载
        2. StartUpConnectingFlag: roi=[0,635,450,85] → 点击"开始游戏"按钮
        3. StartUp: roi=[227,327,159,152] → 检测到主界面
        """
        import time
        log.info("=== 等待游戏启动 ===")
        start_time = time.time()
        tapped_wake = False
        tapped_start = False

        while time.time() - start_time < timeout:
            img = self.screenshot()
            if img is None:
                time.sleep(3)
                continue

            screen = self.detect_screen(img)

            if screen in ("home", "formation", "battle", "results"):
                log.info("=== 游戏启动完成(%s) ===", screen)
                return True

            log.info("  启动中... 界面=%s (%.0fs)", screen, time.time() - start_time)

            # 1. 点击屏幕跳过加载画面 (StartToWakeUp)
            if not tapped_wake:
                self._adb_tap(960, 540)  # 屏幕中心
                time.sleep(2)
                tapped_wake = True
                continue

            # 2. 点击"开始游戏"按钮 (StartUpConnectingFlag roi=[0,635,450,85])
            # 中心 (225,677) in 1280x720 → (337,1015) in 1920x1080
            if not tapped_start:
                self._adb_tap(337, 1015)
                time.sleep(3)
                tapped_start = True
                continue

            # 3. 循环点击直到到达主界面
            self._adb_tap(960, 540)
            time.sleep(3)

        log.warning("游戏启动超时(%ds)", timeout)
        return False

    def cleanup_after_battle(self) -> None:
        """战斗结束后清理: 关闭结算 → 回到关卡选择。"""
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
