"""明日方舟战斗流程协议(copilot-schema)数据结构。

对应 MAA ``docs.maa.plus/zh-cn/protocol/copilot-schema.html``。
``SingleStep`` 任务以单个 ``Action`` 作为 details 喂入(见 maapy_client)。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ActionType = Literal[
    "Deploy",
    "Skill",
    "Retreat",
    "SpeedUp",
    "BulletTime",
    "SkillUsage",
    "Output",
    "SkillDaemon",
    "MoveCamera",
    "ResetStopwatch",
]
Direction = Literal["Left", "Right", "Up", "Down", "None"]

_DEFAULT_DROP: dict[str, Any] = {
    "kills": 0,
    "costs": 0,
    "cost_changes": 0,
    "time_elapsed": 0,
    "pre_delay": 0,
    "post_delay": 0,
    "cooling": -1,
    "skill_times": 1,
}


@dataclass
class Action:
    """单个战斗动作。SingleStep 喂入的最小单元。"""

    type: ActionType = "Deploy"
    name: str | None = None
    location: tuple[int, int] | None = None
    direction: Direction | None = None
    kills: int = 0
    costs: int = 0
    cost_changes: int = 0
    cooling: int = -1
    time_elapsed: int = 0
    pre_delay: int = 0
    post_delay: int = 0
    skill_usage: int | None = None
    skill_times: int = 1
    distance: tuple[float, float] | None = None
    doc: str | None = None

    def to_maa(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("location", "distance"):
            if d.get(k) is not None:
                d[k] = list(d[k])
        for k, df in _DEFAULT_DROP.items():
            if d.get(k) == df:
                d.pop(k, None)
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class OperSpec:
    name: str
    skill: int = 0
    skill_usage: int = 0
    skill_times: int = 1


@dataclass
class GroupSpec:
    name: str
    opers: list[OperSpec] = field(default_factory=list)


@dataclass
class CopilotDoc:
    """整关作业(备选方案 a:一次生成整关再交 MAA 跑)。"""

    stage_name: str
    opers: list[OperSpec] = field(default_factory=list)
    groups: list[GroupSpec] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    minimum_required: str = "v6.7.0"

    def to_maa(self) -> dict[str, Any]:
        return {
            "stage_name": self.stage_name,
            "opers": [asdict(o) for o in self.opers],
            "groups": [
                {"name": g.name, "opers": [asdict(o) for o in g.opers]}
                for g in self.groups
            ],
            "actions": [a.to_maa() for a in self.actions],
            "minimum_required": self.minimum_required,
        }
