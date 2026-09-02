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

ADB = os.getenv("MAA_ADB_PATH", r"C:\Program Files\Netease\MuMu\nx_main\adb.exe")
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
            "home": _load_template("UiTheme/SwitchTheme/SwitchTheme@ToggleSettingsMenu.png"),
            "return": _load_template("ReturnButton/Return.png"),
            "battle_start": _load_template("Battle/StartButton/BattleStartNormal.png"),
            "hp_flag": _load_template("Battle/BattleFlag/BattleHpFlag.png"),
            "stars3": _load_template("Battle/StageDrops/StageDrops-Stars-3.png"),
            "stars2": _load_template("Battle/StageDrops/StageDrops-Stars-2.png"),
            "opers_flag": _load_template("Battle/BattleFlag/BattleOpersFlag.png"),
            "end_action": _load_template("Battle/BattleFlag/EndOfAction.png"),
        }
        # MAA home ROI: [227, 327, 159, 152] in 1280x720 → scaled for 1920x1080
        self._home_roi = (340, 490, 238, 228)  # x, y, w, h

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
        """检测当前界面类型。

        界面类型:
          home:         主界面 (有Home按钮,无部署面板)
          formation:    编队/快捷编队界面 (有部署面板,可能有关卡入口)
          battle:       战斗中 (HP flag高+部署面板)
          results:      结算界面 (Stars)
          unknown:      未知界面
        """
        if img is None:
            img = self.screenshot()
        if img is None:
            return "unknown"

        # 计算各模板分数
        scores = {}
        for name in ["stars3", "stars2", "end_action", "hp_flag", "opers_flag",
                      "battle_start", "return"]:
            t = self._templates.get(name)
            if t is not None:
                mv, _ = _match(img, t, 0.5)
                scores[name] = mv

        # Home screen: 用 MAA 的 ToggleSettingsMenu 模板 + ROI 检测
        home_t = self._templates.get("home")
        home_score = 0.0
        if home_t is not None:
            rx, ry, rw, rh = self._home_roi
            roi = img[ry:ry+rh, rx:rx+rw]
            if roi.shape[0] >= home_t.shape[0] and roi.shape[1] >= home_t.shape[1]:
                res = cv2.matchTemplate(roi, home_t, cv2.TM_CCOEFF_NORMED)
                _, home_score, _, _ = cv2.minMaxLoc(res)
        scores["home"] = home_score

        # 加载快捷编队模板
        qf_elite = _load_template("Battle/Formation/BattleQuickFormation/BattleQuickFormation-Elite1.png")
        if qf_elite is not None:
            mv_qf, _ = _match(img, qf_elite, 0.6)
            scores["quick_formation"] = mv_qf

        hp = scores.get("hp_flag", 0)
        opers = scores.get("opers_flag", 0)
        home = scores.get("home", 0)
        ret = scores.get("return", 0)
        battle_start = scores.get("battle_start", 0)
        qf = scores.get("quick_formation", 0)

        # 1. Results screen
        for name in ["stars3", "stars2", "end_action"]:
            if scores.get(name, 0) > 0.75:
                return "results"

        # 2. Home screen (优先检测,用 MAA 的 ToggleSettingsMenu 模板,阈值 0.7)
        if home > 0.7:
            return "home"

        # 3. Battle screen: HP > 0.75 AND opers > 0.7
        if hp > 0.75 and opers > 0.7:
            return "battle"

        # 4. Quick formation screen: quick_formation > 0.7 OR (opers > 0.7 AND home < 0.55)
        if qf > 0.7 or (opers > 0.7 and home < 0.55):
            return "formation"

        # 5. Formation with battle start button
        if battle_start > 0.7:
            return "formation"

        # 6. Formation with return button
        if ret > 0.8:
            return "formation"

        # 7. Opers flag (残留,可能刚关闭编队)
        if opers > 0.7:
            return "formation"

        # 8. Home with low score
        if home > 0.6:
            return "home"

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
        """从任意界面回到主界面。只用 Return 按钮和 back key(非主界面时)。"""
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
            # 非主界面 → 用 back key 回上一级 (安全:只有主界面会触发退出游戏)
            subprocess.run([ADB, "-s", ADDR, "shell", "input", "keyevent", "4"], capture_output=True, timeout=5)
            time.sleep(2)
            # 检查是否到了主界面或弹出了退出游戏对话框
            img2 = self.screenshot()
            screen2 = self.detect_screen(img2)
            if screen2 == "home":
                return True
            # 如果还在子界面,继续按 back key
            if screen2 not in ("home",):
                continue
            # 到了主界面
            return True

        return self.detect_screen() == "home"

    def esc_sub_screen(self) -> bool:
        """从子界面(快捷编队/编队)返回上一级。用 back key,安全(非主界面)。"""
        img = self.screenshot()
        screen = self.detect_screen(img)
        if screen in ("home", "battle", "results"):
            return True  # 不需要 esc
        # 非主界面 → back key 安全
        log.info("按 back key 退出子界面 (当前: %s)", screen)
        subprocess.run([ADB, "-s", ADDR, "shell", "input", "keyevent", "4"], capture_output=True, timeout=5)
        time.sleep(2)
        return True

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
