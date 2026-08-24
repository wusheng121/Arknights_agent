"""ADB 直接控制:截图 + tap/swipe(绕过 MAA SingleStep)。

用于 SingleStep 实时决策的执行层:截图→VLM 看战局(含干员头像位置+格子屏幕坐标)→DeepSeek 决策→adb 拖拽部署。
"""

from __future__ import annotations

import os
import subprocess


class AdbController:
    def __init__(self, adb_path: str, address: str) -> None:
        self.adb = adb_path
        self.addr = address

    def screencap(self, out_path: str) -> bool:
        r = subprocess.run(
            [self.adb, "-s", self.addr, "exec-out", "screencap", "-p"],
            stdout=open(out_path, "wb"),
            stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0 and os.path.exists(out_path)

    def tap(self, x: int, y: int) -> None:
        subprocess.run([self.adb, "-s", self.addr, "shell", "input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 400) -> None:
        subprocess.run(
            [self.adb, "-s", self.addr, "shell", "input", "swipe",
             str(x1), str(y1), str(x2), str(y2), str(ms)]
        )

    def deploy(self, oper_screen: tuple[int, int], tile_screen: tuple[int, int]) -> None:
        """从待部署区拖拽干员头像到格子位置。"""
        self.swipe(oper_screen[0], oper_screen[1], tile_screen[0], tile_screen[1])
