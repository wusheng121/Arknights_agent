"""技能状态检测: 截图 → 检测干员技能状态。

三种状态:
  - not_ready: 技能没好(SP 未充满)
  - ready: 技能好了但没开(SP 满了,未激活)
  - active: 技能开启中(正在释放)

检测方式:
  1. BattleSkillReady.png 模板匹配 → 技能就绪(ready)
  2. BattleSkillStopOnClick-TopView.png → 技能开启中(active,可点击停止)
  3. 都不匹配 → 技能没好(not_ready)
"""

from __future__ import annotations

import cv2
import numpy as np
import os

MAA = r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64"
SCALE = 1.5  # MAA 1280x720 → MuMu 1920x1080


def _load_templ(name: str, subdir: str = "BattleFlag") -> np.ndarray | None:
    path = os.path.join(MAA, "resource", "template", "Battle", subdir, name)
    t = cv2.imread(path, cv2.IMREAD_COLOR)
    if t is None:
        return None
    return cv2.resize(t, (int(t.shape[1] * SCALE), int(t.shape[0] * SCALE)))


# 预加载模板
_skill_ready_templ = _load_templ("BattleSkillReady.png")
_skill_stop_templ = _load_templ("BattleSkillStopOnClick-TopView.png")
_skill_ready_click_templ = _load_templ("BattleSkillReadyOnClick-TopView.png")


def detect_skill_state(screenshot: np.ndarray, operator_screen_pos: tuple[int, int] = None) -> str:
    """检测干员技能状态。

    Args:
        screenshot: 游戏截图 (1920x1080)
        operator_screen_pos: 干员在屏幕上的位置 (x, y)
            如果为 None, 搜索整个截图

    Returns:
        "not_ready" | "ready" | "active" | "unknown"
    """
    if screenshot is None:
        return "unknown"

    # 定义搜索区域
    if operator_screen_pos:
        x, y = operator_screen_pos
        # 技能图标在干员头像下方偏右
        roi = (max(0, x - 100), max(0, y - 50), 200, 200)
        search_area = screenshot[roi[1]:roi[1]+roi[3], roi[0]:roi[0]+roi[2]]
    else:
        search_area = screenshot

    # 1. 检测技能就绪 (BattleSkillReady)
    if _skill_ready_templ is not None:
        result = cv2.matchTemplate(search_area, _skill_ready_templ, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        if max_val > 0.8:
            return "ready"

    # 2. 检测技能开启中 (BattleSkillStopOnClick - 可点击停止)
    if _skill_stop_templ is not None:
        result = cv2.matchTemplate(search_area, _skill_stop_templ, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        if max_val > 0.8:
            return "active"

    # 3. 检测技能就绪且可点击 (BattleSkillReadyOnClick)
    if _skill_ready_click_templ is not None:
        result = cv2.matchTemplate(search_area, _skill_ready_click_templ, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        if max_val > 0.8:
            return "ready"

    return "not_ready"


def should_execute_skill_action(
    action_type: str,
    skill_state: str,
    is_ammo_skill: bool = False,
) -> bool:
    """根据技能状态决定是否执行 Skill action。

    Args:
        action_type: "Skill" (激活/关闭技能)
        skill_state: detect_skill_state() 的返回值
        is_ammo_skill: 是否弹药制技能(可以提前关闭)

    Returns:
        True = 应该执行, False = 应该跳过
    """
    if skill_state == "unknown":
        return True  # 不确定时执行(保持原行为)

    if is_ammo_skill:
        # 弹药制技能: Skill action 是"关闭"技能
        if skill_state == "active":
            return True   # 技能还在开 → 关闭它 ✅
        elif skill_state == "not_ready":
            return False  # 技能已经结束了 → 不需要关闭 ❌
        elif skill_state == "ready":
            return False  # 技能好了但没开 → 不需要关闭 ❌
    else:
        # 普通技能: Skill action 是"激活"技能
        if skill_state == "ready":
            return True   # 技能好了 → 激活它 ✅
        elif skill_state == "active":
            return False  # 技能已经在开了 → 不需要再激活 ❌
        elif skill_state == "not_ready":
            return False  # 技能没好 → 等等 ❌

    return True


if __name__ == "__main__":
    # 测试: 从 ADB 截图检测技能状态
    import subprocess
    import sys

    adb = r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"
    addr = "127.0.0.1:16384"

    print("截图中...")
    r = subprocess.run([adb, "-s", addr, "exec-out", "screencap", "-p"],
                       capture_output=True, timeout=10)
    arr = np.frombuffer(r.stdout, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    if img is not None:
        print("截图成功: %dx%d" % (img.shape[1], img.shape[0]))
        state = detect_skill_state(img)
        print("技能状态(全局搜索): %s" % state)
    else:
        print("截图失败")
