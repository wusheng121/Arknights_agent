"""MAA Python binding 封装(SingleStep 动态喂入 + 回调闭环)。

真实环境:MAA 主仓库 ``src/Python/asst`` 包 + ``MaaCore.dll``(见 README)。
无 asst 时 ``create_client()`` 自动降级到 ``MockMaapyClient``(事件驱动,模拟执行 + 战局状态回调)。
真实分支 API 已按 ``src/Python/asst.py`` + ``sample.py`` 校准。
真实 SingleStep 执行/等待模型需在真实环境验证(见 README「真实接入」)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.game.copilot_schema import Action

log = logging.getLogger(__name__)

# MAA 回调 msg 常量(见 callback-schema)
MSG_ALL_TASKS_COMPLETED = 3
MSG_SUB_TASK_START = 20001
MSG_SUB_TASK_COMPLETED = 20002
MSG_SUB_TASK_EXTRA_INFO = 20003


@dataclass
class MaapyEvent:
    msg: int
    details: dict
    taskchain: str = ""


Handler = Callable[["MaapyEvent"], Awaitable[None]]


def _single_step_params(subtask: str, details: dict) -> dict:
    # 注意:源码 SingleStepTask::set_params 读 "subtype"(非文档的 "subtask")
    return {"type": "copilot", "subtype": subtask, "details": details}


class MockMaapyClient:
    """无真实 MAA 时的 mock:do_action 事件驱动,触发战局状态回调。"""

    def __init__(self) -> None:
        self._handlers: list[Handler] = []
        self._started = False
        self._step_count = 0
        self._stage = ""
        self._done = asyncio.Event()

    def add_handler(self, h: Handler) -> None:
        self._handlers.append(h)

    async def connect(self, *a: Any, **k: Any) -> bool:
        return True

    async def set_stage(self, stage: str) -> int:
        self._stage = stage
        return 1

    async def start_battle(self) -> int:
        return 2

    async def start(self) -> bool:
        self._started = True
        self._done.clear()
        await self._emit(MSG_SUB_TASK_START, {"subtask": "SingleStep:stage"})
        await self._emit(
            MSG_SUB_TASK_COMPLETED,
            {"subtask": "SingleStep:stage", "details": {"stage": self._stage}},
        )
        await self._emit(MSG_SUB_TASK_EXTRA_INFO, {"what": "StageInfo", "name": self._stage})
        await self._emit(MSG_SUB_TASK_START, {"subtask": "SingleStep:start"})
        await self._emit(MSG_SUB_TASK_COMPLETED, {"subtask": "SingleStep:start"})
        return True

    async def do_action(self, action: Action) -> int:
        if not self._started:
            raise RuntimeError("not started")
        self._step_count += 1
        d = action.to_maa()
        await self._emit(MSG_SUB_TASK_START, {"subtask": "SingleStep:action", "details": d})
        await asyncio.sleep(0)
        await self._emit(
            MSG_SUB_TASK_EXTRA_INFO,
            {
                "what": "BattlefieldState",
                "step": self._step_count,
                "cost": 20 + self._step_count * 2,
                "operators": [
                    {
                        "name": d.get("name"),
                        "location": d.get("location"),
                        "hp": round(1.0 - 0.1 * self._step_count, 2),
                        "skill_ready": self._step_count % 2 == 0,
                        "direction": d.get("direction"),
                    }
                ],
                "enemies": [],
            },
        )
        await self._emit(MSG_SUB_TASK_COMPLETED, {"subtask": "SingleStep:action", "details": d})
        return self._step_count

    async def finish(self) -> None:
        await self._emit(MSG_ALL_TASKS_COMPLETED, {})
        self._done.set()

    async def wait_done(self, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self._done.wait(), timeout)

    def stop(self) -> None:
        self._started = False

    async def get_image(self) -> bytes | None:
        # mock:返回占位 bytes(真实环境由 MaapyClient.get_image 截屏)
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

    async def _emit(self, msg: int, details: dict) -> None:
        ev = MaapyEvent(msg=msg, details=details, taskchain=details.get("taskchain", ""))
        for h in self._handlers:
            try:
                await h(ev)
            except Exception:
                log.exception("maapy handler error: msg=%s", msg)


class MaapyClient:
    """真实 MAA 封装(API 已按 asst.py / sample.py 校准)。"""

    def __init__(self, resource_path: str, touch_mode: str = "minitouch") -> None:
        # Add patched DLL directory to search path BEFORE importing asst
        import ctypes
        patched_dir = os.path.join(os.path.dirname(resource_path), "MAA-v6.16.8-win-x64-patched")
        if os.path.isdir(patched_dir):
            os.add_dll_directory(patched_dir)
        try:
            from asst.asst import Asst  # type: ignore
            from asst.utils import InstanceOptionType  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "未找到 asst 模块:需将 MAA src/Python/asst 包与 MaaCore.dll 放入 "
                "PYTHONPATH/工作目录,并传 resource_path(见 README)"
            ) from e
        if not Asst.load(path=resource_path):
            raise RuntimeError(f"Asst.load 失败,检查路径: {resource_path}")
        self._Asst = Asst
        self._loop = asyncio.get_event_loop()
        self._handlers: list[Handler] = []
        self._inst = Asst(callback=Asst.CallBackType(self._on_callback))
        self._inst.set_instance_option(InstanceOptionType.touch_type, touch_mode)
        self._done = asyncio.Event()

    def add_handler(self, h: Handler) -> None:
        self._handlers.append(h)

    def _on_callback(self, msg: int, details_bytes: bytes, arg: Any) -> None:
        # MAA 线程回调 → asyncio 桥接
        try:
            details = (
                json.loads(details_bytes.decode("utf-8")) if details_bytes else {}
            )
        except Exception:
            details = {}
        ev = MaapyEvent(
            msg=int(msg), details=details, taskchain=details.get("taskchain", "")
        )
        try:
            self._loop.call_soon_threadsafe(asyncio.ensure_future, self._dispatch(ev))
        except RuntimeError:
            # loop 已关闭(进程退出),丢弃回调
            pass

    async def _dispatch(self, ev: MaapyEvent) -> None:
        for h in self._handlers:
            try:
                await h(ev)
            except Exception:
                log.exception("maapy handler error: msg=%s", ev.msg)
        if ev.msg == MSG_ALL_TASKS_COMPLETED:
            self._done.set()

    async def connect(self, adb_path: str | None = None, address: str | None = None, config: str = "General") -> bool:
        adb_path = adb_path or os.getenv("MAA_ADB_PATH")
        address = address or os.getenv("MAA_ADDRESS")
        return await asyncio.to_thread(self._inst.connect, adb_path, address, config)

    async def append(self, task_type: str, params: dict) -> int:
        return await asyncio.to_thread(self._inst.append_task, task_type, params)

    async def set_stage(self, stage: str) -> int:
        # details 用 "stage_name"(非文档的 "stage")
        return await self.append("SingleStep", _single_step_params("stage", {"stage_name": stage}))

    async def start_battle(self) -> int:
        return await self.append("SingleStep", _single_step_params("start", {}))

    async def do_action(self, action: Action) -> int:
        # parse_actions 读 details["actions"] 数组
        return await self.append("SingleStep", _single_step_params("action", {"actions": [action.to_maa()]}))

    async def start(self) -> bool:
        self._done.clear()
        return await asyncio.to_thread(self._inst.start)

    def stop(self) -> bool:
        return self._inst.stop()

    def running(self) -> bool:
        return self._inst.running()

    async def get_image(self) -> bytes | None:
        # 1920x1080x3 截图
        return await asyncio.to_thread(self._inst.get_image, 1920 * 1080 * 3)

    async def finish(self) -> None:
        # 真实 MAA 任务链跑完自然 AllTasksCompleted;循环结束 stop
        self.stop()

    async def wait_done(self, timeout: float = 300.0) -> None:
        async def _poll():
            while self._inst.running() and not self._done.is_set():
                await asyncio.sleep(0.1)
            self._done.set()

        try:
            await asyncio.wait_for(
                asyncio.gather(self._done.wait(), _poll()), timeout=timeout
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.stop()
            raise


def create_client(
    mock: bool | None = None, resource_path: str | None = None
) -> "MockMaapyClient | MaapyClient":
    if mock is True:
        return MockMaapyClient()
    if mock is False:
        if not resource_path:
            raise ValueError("真实分支需 resource_path(MAA dll+resource 目录)")
        return MaapyClient(resource_path)
    try:
        import asst  # noqa: F401
        if not resource_path:
            return MockMaapyClient()
        return MaapyClient(resource_path)
    except ImportError:
        return MockMaapyClient()
