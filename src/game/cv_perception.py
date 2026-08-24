"""CV 感知层:识别待部署区干员 + 费用 + 战斗状态。

参考 MAA BattlefieldMatcher/BattleHelper,用 opencv(CPU)实现:
- update_deployment: 模板匹配 BattleOpersFlag → 定位干员槽位
  → 混合策略(no-resize + templ-to-roi 取 max)识别职业
  → HSV 判断可用/冷却
  → 截取头像(后续匹配干员名)
- update_cost: 找 BattleCostFlag → OCR 数字(待接)
- check_in_battle: 找 HP/Kills flag

不依赖 MAA 框架,纯 Python(opencv + numpy)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

MAA_RES = os.getenv("MAA_RESOURCE_PATH", r"C:\Users\slient\Downloads\MAA-v6.16.8-win-x64\resource")
TEMPLATE_DIR = os.path.join(MAA_RES, "template", "Battle")

# MAA tasks.json rect_move (1280x720 基准)
RECT_MOVE_CLICK = (-45, 6, 75, 120)      # 干员可点击区域
RECT_MOVE_ROLE = (-41, 6, 31, 25)        # 职业图标区域
RECT_MOVE_AVATAR = (7, 32, 60, 60)       # 头像区域
RECT_MOVE_AVAILABLE = (0, 0, 10, 10)    # 可用判断
RECT_MOVE_COOLING = (-68, 124, 114, 4)   # 冷却判断
RECT_MOVE_COST = (-10, 12, 30, 17)       # 费用数字区域

ROLES = ["Pioneer", "Warrior", "Tank", "Sniper", "Caster", "Medic", "Support", "Special", "Drone"]
ROLE_TO_LOCATION = {
    "Pioneer": "Melee", "Warrior": "Melee", "Tank": "Melee", "Special": "Melee", "Drone": "Melee",
    "Sniper": "Ranged", "Caster": "Ranged", "Medic": "Ranged", "Support": "Ranged",
}


@dataclass
class DeploymentOper:
    index: int = 0
    role: str = "Unknown"
    cost: int = -1
    available: bool = False
    cooling: bool = False
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)
    avatar: np.ndarray | None = None
    name: str = ""
    location_type: str = "None"


def _load_template(name: str, sub: str = "BattleFlag") -> np.ndarray | None:
    p = os.path.join(TEMPLATE_DIR, sub, name)
    if os.path.exists(p):
        return cv2.imread(p, cv2.IMREAD_COLOR)
    return None


def _nms(boxes: list[tuple[int, int, int, int, float]], threshold: float = 0.3) -> list[tuple[int, int, int, int, float]]:
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: -b[4])
    keep = []
    for b in boxes:
        x1, y1, w, h = b[0], b[1], b[2], b[3]
        overlap = False
        for k in keep:
            kx, ky, kw, kh = k[0], k[1], k[2], k[3]
            ix1 = max(x1, kx); iy1 = max(y1, ky)
            ix2 = min(x1 + w, kx + kw); iy2 = min(y1 + h, ky + kh)
            if ix2 > ix1 and iy2 > iy1:
                inter = (ix2 - ix1) * (iy2 - iy1)
                if inter / min(w * h, kw * kh) > threshold:
                    overlap = True
                    break
        if not overlap:
            keep.append(b)
    return keep


def _match_template_multi(image: np.ndarray, templ: np.ndarray, threshold: float = 0.7) -> list[tuple[int, int, int, int, float]]:
    if image is None or templ is None:
        return []
    if len(image.shape) == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if len(templ.shape) == 2:
        templ = cv2.cvtColor(templ, cv2.COLOR_GRAY2BGR)
    res = cv2.matchTemplate(image, templ, cv2.TM_CCOEFF_NORMED)
    locs = np.where(res >= threshold)
    h, w = templ.shape[:2]
    results = [(int(pt[0]), int(pt[1]), w, h, float(res[pt[1], pt[0]])) for pt in zip(*locs[::-1])]
    return _nms(results)


def _move_rect(base: tuple[int, int, int, int], move: tuple[int, int, int, int], scale: float) -> tuple[int, int, int, int]:
    """base 已是目标分辨率坐标, move 是 1280x720 基准偏移(需缩放)。"""
    return (base[0] + int(move[0] * scale), base[1] + int(move[1] * scale),
            int(move[2] * scale), int(move[3] * scale))


def _correct_rect(rect: tuple[int, int, int, int], shape: tuple) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    x, y, rw, rh = rect
    x = max(0, min(x, w - 1)); y = max(0, min(y, h - 1))
    rw = min(rw, w - x); rh = min(rh, h - y)
    return (x, y, rw, rh)


class CVPerception:
    def __init__(self, scale: float = 1.5) -> None:
        self.scale = scale
        self._flag_templ = _load_template("BattleOpersFlag.png")
        self._hp_templ = _load_template("BattleHpFlag.png")
        self._kills_templ = _load_template("BattleKillsFlag.png")
        self._cost_templ = _load_template("BattleCostFlag.png")
        self._role_temps = {r: _load_template(f"BattleOperRole{r}.png", "OperRole") for r in ROLES}
        # 缩放 flag 模板到实际分辨率
        if self._flag_templ is not None:
            self._flag_templ_scaled = cv2.resize(self._flag_templ,
                (int(self._flag_templ.shape[1] * scale), int(self._flag_templ.shape[0] * scale)))
        else:
            self._flag_templ_scaled = None

    def check_in_battle(self, image: np.ndarray) -> bool:
        if image is None:
            return False
        for templ in [self._hp_templ, self._kills_templ]:
            if templ is not None:
                ts = cv2.resize(templ, (int(templ.shape[1] * self.scale), int(templ.shape[0] * self.scale)))
                if _match_template_multi(image, ts, 0.6):
                    return True
        return False

    def update_deployment(self, image: np.ndarray, need_cost: bool = False) -> list[DeploymentOper]:
        if image is None or self._flag_templ_scaled is None:
            return []

        flags = _match_template_multi(image, self._flag_templ_scaled, 0.7)
        if not flags:
            return []
        # 按水平排序 + 过滤非待部署区(y 过小的是费用区/地图)
        flags = sorted(flags, key=lambda b: b[0])
        # 待部署区在屏幕底部(1280x720 基准 y>588 → 1920x1080 y>882)
        flags = [f for f in flags if f[1] > int(588 * self.scale)]
        if not flags:
            return []

        opers: list[DeploymentOper] = []
        for idx, flag in enumerate(flags):
            fx, fy, fw, fh, _ = flag

            # 职业图标(混合策略:no-resize + templ-to-roi 取 max)
            role_rect = _correct_rect(_move_rect((fx, fy, fw, fh), RECT_MOVE_ROLE, self.scale), image.shape)
            role = self._role_analyze(image, role_rect)

            # 可用
            avail_rect = _correct_rect(_move_rect((fx, fy, fw, fh), RECT_MOVE_AVAILABLE, self.scale), image.shape)
            available = self._available_analyze(image, avail_rect)

            # 冷却
            cool_rect = _correct_rect(_move_rect((fx, fy, fw, fh), RECT_MOVE_COOLING, self.scale), image.shape)
            cooling = self._cooling_analyze(image, cool_rect)

            # 头像
            avatar_rect = _correct_rect(_move_rect((fx, fy, fw, fh), RECT_MOVE_AVATAR, self.scale), image.shape)
            ax, ay, aw, ah = avatar_rect
            avatar = image[ay:ay + ah, ax:ax + aw].copy() if aw > 0 and ah > 0 else None

            # 可点击区域
            click_rect = _correct_rect(_move_rect((fx, fy, fw, fh), RECT_MOVE_CLICK, self.scale), image.shape)

            # 费用
            cost = self._ocr_cost_from_oper(image, (fx, fy, fw, fh))

            opers.append(DeploymentOper(
                index=idx, role=role, cost=cost, available=available, cooling=cooling,
                rect=click_rect, avatar=avatar,
                location_type=ROLE_TO_LOCATION.get(role, "None"),
            ))

        return opers

    def _role_analyze(self, image: np.ndarray, roi: tuple[int, int, int, int]) -> str:
        """混合策略职业识别:no-resize(模板原尺寸匹配 ROI)+ templ-to-roi(模板缩放到 ROI),取 max。"""
        x, y, w, h = roi
        if w <= 0 or h <= 0:
            return "Unknown"
        roi_img = image[y:y + h, x:x + w]
        if roi_img.size == 0 or roi_img.shape[0] < 5 or roi_img.shape[1] < 5:
            return "Unknown"

        best_role = "Unknown"
        best_score = 0.5  # MAA 阈值

        for role, templ in self._role_temps.items():
            if templ is None:
                continue
            scores = []
            # 方式1: no-resize(ROI > 模板, 直接匹配)
            if roi_img.shape[0] >= templ.shape[0] and roi_img.shape[1] >= templ.shape[1]:
                res1 = cv2.matchTemplate(roi_img, templ, cv2.TM_CCOEFF_NORMED)
                _, mv1, _, _ = cv2.minMaxLoc(res1)
                scores.append(mv1)
            # 方式2: templ-to-roi(模板缩放到 ROI 大小)
            ts = cv2.resize(templ, (w, h))
            res2 = cv2.matchTemplate(roi_img, ts, cv2.TM_CCOEFF_NORMED)
            _, mv2, _, _ = cv2.minMaxLoc(res2)
            scores.append(mv2)
            # 取 max
            mv = max(scores)
            if mv > best_score:
                best_score = mv
                best_role = role

        return best_role

    def _available_analyze(self, image: np.ndarray, roi: tuple[int, int, int, int]) -> bool:
        x, y, w, h = roi
        if w <= 0 or h <= 0:
            return False
        roi_img = image[y:y + h, x:x + w]
        if roi_img.size == 0:
            return False
        hsv = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)
        avg = cv2.mean(hsv)
        return avg[2] > 100

    def _cooling_analyze(self, image: np.ndarray, roi: tuple[int, int, int, int]) -> bool:
        x, y, w, h = roi
        if w <= 0 or h <= 0:
            return False
        roi_img = image[y:y + h, x:x + w]
        if roi_img.size == 0:
            return False
        hsv = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)
        # MAA: HSV [0,100,0]~[20,255,150] → 冷却色(红橙色)
        lower = np.array([0, 100, 0])
        upper = np.array([20, 255, 150])
        mask = cv2.inRange(hsv, lower, upper)
        count = cv2.countNonZero(mask)
        return count > 300  # MAA specialParams[0]=300

    def update_cost(self, image: np.ndarray) -> int:
        """识别总费用(屏幕左下角 cost)。

        用 BattleCostFlag 定位 → ROI 数字模板匹配。
        """
        if image is None or self._cost_templ is None:
            return -1
        ts = cv2.resize(self._cost_templ,
            (int(self._cost_templ.shape[1] * self.scale), int(self._cost_templ.shape[0] * self.scale)))
        matches = _match_template_multi(image, ts, 0.6)
        if not matches:
            return -1
        # cost flag 右侧是数字
        fx, fy = matches[0][0], matches[0][1]
        cw, ch = int(40 * self.scale), int(20 * self.scale)
        roi = image[fy:fy + ch, fx + int(ts.shape[1]):fx + int(ts.shape[1]) + cw]
        return self._ocr_digits(roi)

    def _ocr_cost_from_oper(self, image: np.ndarray, flag_pos: tuple[int, int, int, int]) -> int:
        """从干员 flag 位置 OCR 该干员的费用。

        flag_pos: (x, y, w, h) flag 的位置(已缩放到目标分辨率)。
        用 rectMove=[-10, 12, 30, 17] 定位 cost ROI。
        """
        fx, fy, fw, fh = flag_pos
        cx = int(fx + (-10) * self.scale)
        cy = int(fy + 12 * self.scale)
        cw = int(30 * self.scale)
        ch = int(17 * self.scale)
        cx = max(0, cx); cy = max(0, cy)
        roi = image[cy:cy + ch, cx:cx + cw]
        if roi.size == 0:
            return -1
        return self._ocr_digits(roi)

    def _ocr_digits(self, roi: np.ndarray) -> int:
        """数字模板匹配 OCR。

        用 digit_lib/ 下的数字模板(0-9),自动积累。
        匹配不上的存为 unknown_N.png 待标注。
        """
        if roi is None or roi.size == 0:
            return -1
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        _, bin_img = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(bin_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rects = sorted(
            [cv2.boundingRect(c) for c in contours
             if cv2.boundingRect(c)[2] > 3 and cv2.boundingRect(c)[3] > 8],
            key=lambda r: r[0],
        )
        if not rects:
            return -1

        digit_lib = os.path.join(os.path.dirname(__file__), "..", "..", "digit_lib")
        os.makedirs(digit_lib, exist_ok=True)
        templates = {}
        for f in os.listdir(digit_lib):
            if f.endswith(".png") and len(f) == 5:
                d = f[0]
                t = cv2.imread(os.path.join(digit_lib, f), cv2.IMREAD_GRAYSCALE)
                if t is not None:
                    templates[d] = t

        cost_str = ""
        for dx, dy, dw, dh in rects:
            digit_roi = bin_img[dy:dy + dh, dx:dx + dw]
            best_d = "?"
            best_s = 0.5
            for d, templ in templates.items():
                if templ.shape[0] != dh or templ.shape[1] != dw:
                    templ_r = cv2.resize(templ, (dw, dh))
                else:
                    templ_r = templ
                res = cv2.matchTemplate(digit_roi, templ_r, cv2.TM_CCOEFF_NORMED)
                _, mv, _, _ = cv2.minMaxLoc(res)
                if mv > best_s:
                    best_s = mv
                    best_d = d
            if best_d == "?" and templates:
                idx = 0
                while os.path.exists(os.path.join(digit_lib, f"unknown_{idx}.png")):
                    idx += 1
                cv2.imwrite(os.path.join(digit_lib, f"unknown_{idx}.png"), digit_roi)
            cost_str += best_d

        if "?" in cost_str or not cost_str:
            return -1
        try:
            return int(cost_str)
        except ValueError:
            return -1

    def check_pause_button(self, image: np.ndarray) -> bool:
        """检查暂停按钮。"""
        templ = _load_template("BattleOfficiallyBegin.png")
        if templ is not None:
            ts = cv2.resize(templ, (int(templ.shape[1] * self.scale), int(templ.shape[0] * self.scale)))
            return bool(_match_template_multi(image, ts, 0.6))
        return False

    def check_speed_up(self, image: np.ndarray) -> bool:
        templ = _load_template("BattleSpeedUp.png")
        if templ is not None:
            ts = cv2.resize(templ, (int(templ.shape[1] * self.scale), int(templ.shape[0] * self.scale)))
            return bool(_match_template_multi(image, ts, 0.6))
        return False

    def identify_oper_name(self, avatar: np.ndarray, avatar_cache: dict[str, np.ndarray] | None = None) -> str:
        """头像模板匹配干员名(需 avatar_cache)。"""
        if avatar is None or not avatar_cache:
            return ""
        best_name = ""
        best_score = 0.5
        for name, templ in avatar_cache.items():
            if templ is None:
                continue
            if templ.shape[:2] != avatar.shape[:2]:
                templ_r = cv2.resize(templ, (avatar.shape[1], avatar.shape[0]))
            else:
                templ_r = templ
            res = cv2.matchTemplate(avatar, templ_r, cv2.TM_CCOEFF_NORMED)
            _, mv, _, _ = cv2.minMaxLoc(res)
            if mv > best_score:
                best_score = mv
                best_name = name
        return best_name


if __name__ == "__main__":
    import subprocess
    import sys

    adb = os.getenv("MAA_ADB_PATH", r"C:\Program Files\Netease\MuMu\nx_device\15.0\shell\adb.exe")
    addr = os.getenv("MAA_ADDRESS", "127.0.0.1:16384")
    subprocess.run([adb, "-s", addr, "connect", addr], stderr=subprocess.DEVNULL)
    subprocess.run([adb, "-s", addr, "exec-out", "screencap", "-p"], stdout=open("shot_cv.png", "wb"), stderr=subprocess.DEVNULL)

    img = cv2.imread("shot_cv.png")
    print(f"截图: {img.shape}")

    cv = CVPerception(scale=1.5)
    print(f"在战斗中: {cv.check_in_battle(img)}")

    opers = cv.update_deployment(img)
    print(f"待部署区干员({len(opers)}个):")
    for op in opers:
        print(f"  [{op.index}] role={op.role} available={op.available} cooling={op.cooling} rect={op.rect}")
