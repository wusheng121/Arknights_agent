"""Arknights mini simulator - GameState + step().

核心模拟:
- 网格/tile 系统
- 敌人移动/阻挡/攻击/死亡
- 干员攻击/技能SP/部署/撤退
- DP 回复/扣费
- 波次刷怪
- 胜负判定

V1 简化: 不做技能效果(只做普攻+阻挡+移动+DP+波次+胜负)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from src.sim.data_loader import (
    TileInfo, EnemySpawn, Route, EnemyData, OperatorData, SkillData,
    load_stage, load_operator,
)
from src.sim.range_calc import calc_range_tiles


@dataclass
class SimOperator:
    """战场上的干员。"""
    name: str
    col: int
    row: int
    facing: str
    hp: int
    max_hp: int
    atk: int
    defense: int
    res: float
    block: int
    attack_time: float
    range_tiles: list[tuple[int, int]]  # absolute tiles
    profession: str
    sub_profession: str
    is_medic: bool
    skill: SkillData
    sp: int = 0
    skill_active: bool = False
    skill_duration_left: float = 0
    skill_usage: int = 0  # 0=manual, 1=auto-use, 2=auto-use N times
    skill_times: int = 1
    skill_times_used: int = 0
    attack_cooldown: float = 0
    alive: bool = True


@dataclass
class SimEnemy:
    """战场上的敌人。"""
    enemy_id: str
    name: str
    col: float  # float for smooth movement
    row: float
    hp: int
    max_hp: int
    atk: int
    defense: int
    res: float
    move_speed: float
    mass_level: int
    atk_time: float
    life_point_reduce: int
    route: Route
    route_progress: int = 0  # index in waypoints
    segment_progress: float = 0.0  # 0-1 between waypoints
    blocked_by: Optional[str] = None  # operator name
    attack_cooldown: float = 0
    alive: bool = True


@dataclass
class SimEvent:
    """模拟事件。"""
    tick: float
    event: str
    details: dict = field(default_factory=dict)


class GameState:
    """Arknights mini simulator state."""

    def __init__(self, stage_data: dict):
        self.width = stage_data["width"]
        self.height = stage_data["height"]
        self.tiles = stage_data["tiles"]
        self.routes = stage_data["routes"]
        self.spawns = stage_data["spawns"]
        self.enemy_lookup = stage_data["enemy_lookup"]
        self.red_doors = stage_data["red_doors"]
        self.blue_doors = stage_data["blue_doors"]
        self.initial_cost = stage_data["initial_cost"]
        self.max_lives = stage_data.get("max_life_points", 3)
        self.move_multiplier = stage_data.get("move_multiplier", 1.0)
        self.cost_increase_time = stage_data.get("cost_increase_time", 1.0)

        # Build tile lookup
        self.tile_map = {}
        for t in self.tiles:
            self.tile_map[(t.col, t.row)] = t

        # State
        self.tick = 0.0
        self.dp = self.initial_cost
        self.lives = self.max_lives
        self.operators: list[SimOperator] = []
        self.enemies: list[SimEnemy] = []
        self.events: list[SimEvent] = []
        self.spawn_index = 0
        self.game_over = False
        self.won = False

        # Track deployed positions
        self.deployed_positions: set[tuple[int, int]] = set()

        # DP regen
        self.dp_regen_timer = 0.0

        # Game speed (1x or 2x with SpeedUp)
        self.speed_multiplier = 1

    def deploy(self, name: str, col: int, row: int, facing: str, skill_index: int = 1) -> bool:
        """部署干员。返回是否成功。"""
        pos = (col, row)
        if pos in self.deployed_positions:
            self._log("deploy_failed", oper=name, reason="position_occupied", pos=pos)
            return False

        op_data = load_operator(name)
        if self.dp < op_data.cost:
            self._log("deploy_failed", oper=name, reason="not_enough_dp", dp=self.dp, cost=op_data.cost)
            return False

        # Check tile buildable
        tile = self.tile_map.get(pos)
        if tile and tile.buildable == "none":
            self._log("deploy_failed", oper=name, reason="tile_not_buildable", pos=pos)
            return False

        # Check profession vs tile type
        is_ground = op_data.profession in ("PIONEER", "WARRIOR", "TANK", "SPECIAL", "DRONE")
        if tile and is_ground and tile.buildable != "melee":
            self._log("deploy_failed", oper=name, reason="ground_on_ranged_tile", pos=pos)
            return False
        if tile and not is_ground and tile.buildable != "ranged":
            self._log("deploy_failed", oper=name, reason="ranged_on_ground_tile", pos=pos)
            return False

        # Find skill (skill_index=0 means use skill 1)
        skill = None
        for s in op_data.skills:
            if s.skill_index == skill_index or (skill_index == 0 and s.skill_index == 1):
                skill = s
                break
        if not skill and op_data.skills:
            skill = op_data.skills[0]

        # Calculate range tiles
        range_abs = calc_range_tiles((col, row), facing, op_data.range_tiles)

        self.dp -= op_data.cost
        self.deployed_positions.add(pos)

        sim_op = SimOperator(
            name=name,
            col=col, row=row, facing=facing,
            hp=op_data.hp, max_hp=op_data.hp,
            atk=op_data.atk, defense=op_data.defense, res=op_data.res,
            block=op_data.block, attack_time=op_data.attack_time,
            range_tiles=range_abs,
            profession=op_data.profession,
            sub_profession=op_data.sub_profession,
            is_medic=(op_data.profession == "MEDIC"),
            skill=skill or op_data.skills[0] if op_data.skills else None,
            sp=skill.sp_init if skill else 0,
        )
        self.operators.append(sim_op)
        self._log("deploy", oper=name, pos=pos, facing=facing, dp_left=self.dp)

        # Check healing targets (for medics)
        if sim_op.is_medic:
            allies_in_range = self._allies_in_range(sim_op)
            if not allies_in_range:
                self._log("warning", oper=name, reason="no_healing_targets", pos=pos,
                         range_tiles=range_abs)
        return True

    def retreat(self, name: str) -> bool:
        """撤退干员。"""
        for op in self.operators:
            if op.name == name and op.alive:
                op.alive = False
                self.deployed_positions.discard((op.col, op.row))
                self.dp += 5  # simplified refund
                self._log("retreat", oper=name, dp_after=self.dp)
                # Unenblock enemies
                for e in self.enemies:
                    if e.blocked_by == name:
                        e.blocked_by = None
                return True
        return False

    def use_skill(self, name: str) -> bool:
        """使用技能。"""
        for op in self.operators:
            if op.name == name and op.alive:
                return self._activate_skill(op)
        return False

    def _activate_skill(self, op: SimOperator) -> bool:
        """激活干员技能(内部方法)。"""
        if not op.skill:
            return False
        if op.sp < op.skill.sp_cost:
            self._log("skill_not_ready", oper=op.name,
                     sp=op.sp, needed=op.skill.sp_cost)
            return False
        op.sp = 0
        op.skill_active = True
        op.skill_times_used += 1
        if op.skill.duration > 0:
            op.skill_duration_left = op.skill.duration
        elif op.skill.duration == -1:
            op.skill_duration_left = float('inf')
        self._log("skill_activated", oper=op.name, skill=op.skill.name,
                  usage=op.skill_usage, times_used=op.skill_times_used)
        return True

    def step(self, dt: float = 0.1):
        """推进一个时间步。"""
        if self.game_over:
            return

        self.tick += dt

        # 1. DP regen
        self.dp_regen_timer += dt
        if self.dp_regen_timer >= self.cost_increase_time:
            self.dp_regen_timer -= self.cost_increase_time
            self.dp = min(self.dp + 1, 99)

        # 2. Spawn enemies
        while self.spawn_index < len(self.spawns):
            spawn = self.spawns[self.spawn_index]
            if spawn.time <= self.tick:
                for _ in range(spawn.count):
                    self._spawn_enemy(spawn)
                self.spawn_index += 1
            else:
                break

        # 3. Enemy movement
        for enemy in self.enemies:
            if not enemy.alive or enemy.blocked_by:
                continue
            self._move_enemy(enemy, dt)

        # 4. Operator attacks
        for op in self.operators:
            if not op.alive:
                continue
            op.attack_cooldown -= dt
            if op.attack_cooldown <= 0:
                op.attack_cooldown = op.attack_time
                self._operator_attack(op)

        # 5. Skill SP regen + auto skill activation
        for op in self.operators:
            if not op.alive or not op.skill:
                continue
            if not op.skill_active:
                if op.skill.sp_type == "INCREASE_WITH_TIME":
                    op.sp = min(op.sp + int(dt * 10) / 10, op.skill.sp_cost)
                elif op.skill.sp_type == "INCREASE_WHEN_ATTACK":
                    pass  # regen on attack, handled in _operator_attack

                # Auto skill activation (skill_usage=1 or 2)
                if op.skill_usage in (1, 2) and op.sp >= op.skill.sp_cost:
                    if op.skill_usage == 2 and op.skill_times_used >= op.skill_times:
                        continue  # Already used all times
                    self._activate_skill(op)

            # Skill duration
            if op.skill_active:
                if op.skill_duration_left != float('inf'):
                    op.skill_duration_left -= dt
                    if op.skill_duration_left <= 0:
                        op.skill_active = False
                        op.skill_duration_left = 0
                        self._log("skill_ended", oper=op.name)

        # 6. Enemy attacks (blocked enemies attack operators)
        for enemy in self.enemies:
            if not enemy.alive or not enemy.blocked_by:
                continue
            enemy.attack_cooldown -= dt
            if enemy.attack_cooldown <= 0:
                enemy.attack_cooldown = enemy.atk_time
                blocker = self._find_operator(enemy.blocked_by)
                if blocker and blocker.alive:
                    damage = max(enemy.atk - blocker.defense, 1)
                    blocker.hp -= damage
                    if blocker.hp <= 0:
                        blocker.alive = False
                        self.deployed_positions.discard((blocker.col, blocker.row))
                        self._log("operator_died", oper=blocker.name, killed_by=enemy.name)
                        # Unenblock all enemies blocked by this operator
                        for e in self.enemies:
                            if e.blocked_by == blocker.name:
                                e.blocked_by = None

        # 7. Check deaths
        self.enemies = [e for e in self.enemies if e.alive and e.hp > 0]

        # 8. Check win/lose
        if self.lives <= 0:
            self.game_over = True
            self.won = False
            self._log("game_over", result="lose", reason="lives_depleted")
        elif self.spawn_index >= len(self.spawns) and not self.enemies:
            self.game_over = True
            self.won = True
            self._log("game_over", result="win")

    def _spawn_enemy(self, spawn: EnemySpawn):
        enemy_data = self.enemy_lookup.get(spawn.enemy_id)
        if not enemy_data:
            return
        route = self.routes[spawn.route_index] if spawn.route_index < len(self.routes) else self.routes[0]
        waypoints = route.waypoints
        if not waypoints:
            return
        start = waypoints[0]
        enemy = SimEnemy(
            enemy_id=spawn.enemy_id,
            name=enemy_data.name,
            col=float(start[0]),
            row=float(start[1]),
            hp=enemy_data.hp, max_hp=enemy_data.hp,
            atk=enemy_data.atk, defense=enemy_data.defense,
            res=enemy_data.res,
            move_speed=enemy_data.move_speed,
            mass_level=enemy_data.mass_level,
            atk_time=enemy_data.atk_time,
            life_point_reduce=enemy_data.life_point_reduce,
            route=route,
        )
        self.enemies.append(enemy)
        self._log("enemy_spawn", enemy=enemy.name, pos=start, route=spawn.route_index)

    def _move_enemy(self, enemy: SimEnemy, dt: float):
        """沿路径移动敌人。"""
        waypoints = enemy.route.waypoints
        if enemy.route_progress >= len(waypoints) - 1:
            # Reached end of path → blue door
            end_pos = waypoints[-1]
            if (int(end_pos[0]), int(end_pos[1])) in self.blue_doors:
                self.lives -= enemy.life_point_reduce
                enemy.alive = False
                self._log("enemy_leaked", enemy=enemy.name, lives_left=self.lives)
            return

        # Move along current segment
        current = waypoints[enemy.route_progress]
        next_wp = waypoints[enemy.route_progress + 1]

        dx = next_wp[0] - current[0]
        dy = next_wp[1] - current[1]
        dist = math.sqrt(dx * dx + dy * dy)
        if dist == 0:
            enemy.route_progress += 1
            return

        move = enemy.move_speed * self.move_multiplier * dt
        enemy.segment_progress += move / dist

        if enemy.segment_progress >= 1.0:
            enemy.segment_progress = 0.0
            enemy.route_progress += 1
            enemy.col = float(next_wp[0])
            enemy.row = float(next_wp[1])
            # Check if next tile has an operator to block
            self._check_block(enemy)
        else:
            enemy.col = current[0] + dx * enemy.segment_progress
            enemy.row = current[1] + dy * enemy.segment_progress

    def _check_block(self, enemy: SimEnemy):
        """检查敌人是否被干员阻挡。"""
        if enemy.blocked_by:
            return
        for op in self.operators:
            if not op.alive:
                continue
            if op.col == round(enemy.col) and op.row == round(enemy.row):
                # Count how many enemies this operator is blocking
                blocked_count = sum(1 for e in self.enemies if e.blocked_by == op.name and e.alive)
                if blocked_count < op.block:
                    enemy.blocked_by = op.name
                    self._log("enemy_blocked", enemy=enemy.name, by=op.name,
                             blocked_count=blocked_count + 1, block_max=op.block)
                    return

    def _operator_attack(self, op: SimOperator):
        """干员攻击范围内的敌人(或治疗友方)。"""
        # Calculate effective stats with skill effects
        atk = op.atk
        attack_time = op.attack_time
        max_targets = 1

        # Centurion guards (群攻卫) attack all blocked enemies
        if op.sub_profession == "centurion":
            max_targets = op.block

        if op.skill_active and op.skill and op.skill.blackboard:
            bb = op.skill.blackboard
            # ATK bonus (e.g. {"atk": 1.8} means ATK * (1 + 1.8) = 2.8x)
            if "atk" in bb:
                atk = int(op.atk * (1 + bb["atk"]))
            # Attack speed change
            if "base_attack_time" in bb:
                attack_time = max(0.1, op.attack_time + bb["base_attack_time"])
            # Multi-target
            if "attack@max_target" in bb:
                max_targets = max(max_targets, int(bb["attack@max_target"]))
            if "max_target" in bb:
                max_targets = max(max_targets, int(bb["max_target"]))

        op.attack_cooldown = attack_time

        if op.is_medic:
            # Heal lowest HP ally in range
            allies = self._allies_in_range(op)
            if allies:
                target = min(allies, key=lambda a: a.hp / a.max_hp)
                heal_scale = 1.0
                if op.skill_active and op.skill and op.skill.blackboard:
                    bb = op.skill.blackboard
                    if "atk" in bb:
                        heal_scale = 1 + bb["atk"]
                    if "heal_scale" in bb:
                        heal_scale = bb["heal_scale"]
                    if "attack@heal_scale" in bb:
                        heal_scale = bb["attack@heal_scale"]
                heal = int(atk * heal_scale)
                target.hp = min(target.hp + heal, target.max_hp)
        else:
            # Attack enemies in range
            targets = [e for e in self.enemies if e.alive and (round(e.col), round(e.row)) in op.range_tiles]
            if targets:
                # Attack up to max_targets
                for target in targets[:max_targets]:
                    # Damage: physical (atk - def) or depends on profession
                    if op.profession in ("CASTER",):
                        # Caster: magic damage (atk - res, but res is percentage)
                        damage = max(int(atk * (1 - target.res / 100)), 1)
                    else:
                        damage = max(atk - target.defense, 1)
                    target.hp -= damage
                    if target.hp <= 0:
                        target.alive = False
                        self._log("enemy_killed", enemy=target.name, by=op.name)
                    # SP regen on attack
                    if op.skill and op.skill.sp_type == "INCREASE_WHEN_ATTACK" and not op.skill_active:
                        op.sp = min(op.sp + 1, op.skill.sp_cost)

    def _allies_in_range(self, op: SimOperator) -> list[SimOperator]:
        """找范围内的友方干员(非自己)。"""
        result = []
        for ally in self.operators:
            if ally.alive and ally != op:
                if (ally.col, ally.row) in [(int(t[0]), int(t[1])) for t in op.range_tiles]:
                    result.append(ally)
        return result

    def _find_operator(self, name: str) -> Optional[SimOperator]:
        for op in self.operators:
            if op.name == name:
                return op
        return None

    def _log(self, event: str, **kwargs):
        self.events.append(SimEvent(tick=self.tick, event=event, details=kwargs))

    def get_snapshot(self) -> dict:
        """结构化快照 for LLM."""
        return {
            "tick": round(self.tick, 1),
            "dp": self.dp,
            "lives": self.lives,
            "enemies": [{
                "name": e.name, "pos": [round(e.col, 1), round(e.row, 1)],
                "hp": e.hp, "blocked": e.blocked_by is not None,
                "dist_to_blue": self._dist_to_blue(e)
            } for e in self.enemies if e.alive],
            "operators": [{
                "name": op.name, "pos": [op.col, op.row], "facing": op.facing,
                "hp": op.hp, "alive": op.alive,
                "skill_sp": round(op.sp, 1),
                "skill_max_sp": op.skill.sp_cost if op.skill else 0,
                "skill_ready": op.sp >= (op.skill.sp_cost if op.skill else 0),
                "skill_active": op.skill_active,
                "targets_in_range": len([e for e in self.enemies if e.alive and (round(e.col), round(e.row)) in op.range_tiles]),
                "healing_targets": len(self._allies_in_range(op)) if op.is_medic else 0,
            } for op in self.operators],
        }

    def get_event_log(self) -> list[dict]:
        """关键事件列表。"""
        important = ["deploy", "deploy_failed", "skill_activated", "skill_not_ready",
                     "skill_ended", "enemy_spawn", "enemy_blocked", "enemy_killed",
                     "enemy_leaked", "operator_died", "game_over", "warning"]
        return [{"tick": round(e.tick, 1), "event": e.event, **e.details}
                for e in self.events if e.event in important]

    def get_failure_analysis(self) -> dict:
        """自动根因分析。"""
        if not self.game_over:
            return {"result": "ongoing"}

        leaks = [e for e in self.events if e.event == "enemy_leaked"]
        deaths = [e for e in self.events if e.event == "operator_died"]
        skill_issues = [e for e in self.events if e.event == "skill_not_ready"]
        no_heal = [e for e in self.events if e.event == "warning" and e.details.get("reason") == "no_healing_targets"]

        causes = []
        if leaks:
            causes.append(f"漏怪{len(leaks)}次")
        if deaths:
            causes.append(f"干员死亡{len(deaths)}次: {', '.join(d.details.get('oper','') for d in deaths)}")
        if skill_issues:
            causes.append(f"技能未就绪{len(skill_issues)}次: {', '.join(s.details.get('oper','') for s in skill_issues)}")
        if no_heal:
            causes.append(f"医疗无目标{len(no_heal)}次: {', '.join(n.details.get('oper','') for n in no_heal)}")

        return {
            "result": "win" if self.won else "lose",
            "lives_left": self.lives,
            "leaks": len(leaks),
            "operator_deaths": len(deaths),
            "skill_not_ready": len(skill_issues),
            "no_healing_targets": len(no_heal),
            "root_causes": causes,
        }

    def _dist_to_blue(self, enemy: SimEnemy) -> int:
        """敌人到最近蓝门的曼哈顿距离。"""
        if not self.blue_doors:
            return 999
        return min(abs(round(enemy.col) - bd[0]) + abs(round(enemy.row) - bd[1]) for bd in self.blue_doors)


def check_condition_feasibility(stage_id: str, job: dict) -> list[str]:
    """预检条件可行性:在 sim 运行前,检查 actions 里的条件是否可能达成。

    返回问题列表(空=没问题)。
    """
    issues = []
    actions = job.get("actions", [])

    # Load stage to get total enemy count
    try:
        stage_data = load_stage(stage_id)
        total_enemies = len(stage_data["spawns"])
    except Exception:
        total_enemies = 999

    for i, a in enumerate(actions):
        atype = a.get("type", "")
        kills = a.get("kills", 0)
        costs = a.get("costs", 0)

        # Check kills condition feasibility
        if kills and kills > total_enemies:
            issues.append(f"action[{i}] {atype}: kills={kills} > total_enemies={total_enemies}, 条件不可能达成")

        # Check costs condition feasibility (costs should be reasonable, not > 99)
        if costs and costs > 99:
            issues.append(f"action[{i}] {atype}: costs={costs} 过高, DP 不可能达到")

        # Check kills condition on non-Skill actions (kills mainly for Skill/Retreat)
        if kills and atype == "Deploy":
            issues.append(f"action[{i}] Deploy: 用 kills={kills} 条件不合理, Deploy 应该用 costs 条件")

        # Check costs condition on Skill actions (costs mainly for Deploy)
        if costs and atype == "Skill":
            issues.append(f"action[{i}] Skill: 用 costs={costs} 条件不合理, Skill 应该用 kills 条件")

    return issues


def run_job(stage_id: str, job: dict, max_ticks: int = 5000) -> dict:
    """运行一份作业,返回结果。"""
    stage_data = load_stage(stage_id)
    state = GameState(stage_data)

    # Execute actions
    actions = job.get("actions", [])
    action_index = 0
    tick_interval = 0.1
    dp_at_last_action = state.dp  # for cost_changes tracking
    last_action_tick = 0.0  # for elapsed_time tracking

    for tick_num in range(max_ticks):
        # Execute actions whose conditions are met
        while action_index < len(actions):
            action = actions[action_index]
            atype = action.get("type", "")

            if atype == "SpeedUp":
                state.speed_multiplier = 2 if state.speed_multiplier == 1 else 1
                action_index += 1
                continue
            elif atype == "SkillDaemon":
                action_index += 1
                continue
            elif atype == "Deploy":
                name = action.get("name", "")
                loc = action.get("location", [0, 0])
                direction = action.get("direction", "Right")
                # costs condition
                costs = action.get("costs", 0)
                if not costs:
                    try:
                        op_data = load_operator(name)
                        costs = op_data.cost
                    except Exception:
                        costs = 0
                if costs and state.dp < costs:
                    break  # wait for DP
                # cost_changes condition
                cost_changes = action.get("cost_changes", 0)
                if cost_changes:
                    dp_change = state.dp - dp_at_last_action
                    if dp_change < cost_changes:
                        break
                # cooling condition
                cooling = action.get("cooling", -1)
                if cooling >= 0:
                    cd_count = sum(1 for op in state.operators if not op.alive)
                    if cd_count < cooling:
                        break
                # elapsed_time condition
                elapsed_time = action.get("elapsed_time", 0) or action.get("time_elapsed", 0)
                if elapsed_time:
                    elapsed_ms = (state.tick - last_action_tick) * 1000
                    if elapsed_ms < elapsed_time:
                        break
                # pre_delay
                pre_delay = action.get("pre_delay", 0)
                if pre_delay:
                    pre_steps = int((pre_delay / 1000.0) / tick_interval)
                    for _ in range(pre_steps):
                        state.step(tick_interval)
                        if state.game_over:
                            break
                skill_idx = 1
                for o in job.get("opers", []):
                    if o.get("name") == name:
                        skill_idx = o.get("skill", 1)
                        break
                if state.deploy(name, int(loc[0]), int(loc[1]), direction, skill_idx):
                    # Set skill_usage and skill_times from job
                    for o in job.get("opers", []):
                        if o.get("name") == name:
                            sim_op = state.operators[-1] if state.operators else None
                            if sim_op and sim_op.name == name:
                                sim_op.skill_usage = int(o.get("skill_usage", 0) or 0)
                                sim_op.skill_times = int(o.get("skill_times", 1) or 1)
                            break
                    # post_delay
                    post_delay = action.get("post_delay", 0)
                    if post_delay:
                        post_steps = int((post_delay / 1000.0) / tick_interval)
                        for _ in range(post_steps):
                            state.step(tick_interval)
                            if state.game_over:
                                break
                    dp_at_last_action = state.dp
                    last_action_tick = state.tick
                    action_index += 1
                else:
                    break
            elif atype == "Skill":
                name = action.get("name", "")
                # kills condition
                kills = action.get("kills", 0)
                if kills:
                    killed = sum(1 for e in state.events if e.event == "enemy_killed")
                    if killed < kills:
                        break
                # cost_changes condition
                cost_changes = action.get("cost_changes", 0)
                if cost_changes:
                    dp_change = state.dp - dp_at_last_action
                    if dp_change < cost_changes:
                        break
                # cooling condition
                cooling = action.get("cooling", -1)
                if cooling >= 0:
                    cd_count = sum(1 for op in state.operators if not op.alive)
                    if cd_count < cooling:
                        break
                # elapsed_time condition
                elapsed_time = action.get("elapsed_time", 0) or action.get("time_elapsed", 0)
                if elapsed_time:
                    elapsed_ms = (state.tick - last_action_tick) * 1000
                    if elapsed_ms < elapsed_time:
                        break
                # pre_delay for Skill
                pre_delay = action.get("pre_delay", 0)
                if pre_delay:
                    pre_steps = int((pre_delay / 1000.0) / tick_interval)
                    for _ in range(pre_steps):
                        state.step(tick_interval)
                        if state.game_over:
                            break
                state.use_skill(name)
                # post_delay for Skill
                post_delay = action.get("post_delay", 0)
                if post_delay:
                    post_steps = int((post_delay / 1000.0) / tick_interval)
                    for _ in range(post_steps):
                        state.step(tick_interval)
                        if state.game_over:
                            break
                dp_at_last_action = state.dp
                last_action_tick = state.tick
                action_index += 1
            elif atype == "Retreat":
                name = action.get("name", "")
                # kills condition
                kills = action.get("kills", 0)
                if kills:
                    killed = sum(1 for e in state.events if e.event == "enemy_killed")
                    if killed < kills:
                        break
                # pre_delay for Retreat
                pre_delay = action.get("pre_delay", 0)
                if pre_delay:
                    pre_steps = int((pre_delay / 1000.0) / tick_interval)
                    for _ in range(pre_steps):
                        state.step(tick_interval)
                        if state.game_over:
                            break
                state.retreat(name)
                dp_at_last_action = state.dp
                last_action_tick = state.tick
                action_index += 1
            elif atype == "ResetStopwatch":
                last_action_tick = state.tick
                action_index += 1
            else:
                action_index += 1

        state.step(tick_interval)

        if state.speed_multiplier > 1:
            state.step(tick_interval)

        if state.game_over:
            break

    # Timeout
    if not state.game_over:
        state.game_over = True
        state.won = False
        state._log("game_over", result="lose", reason="timeout")

    return {
        "result": "win" if state.won else "lose",
        "ticks": tick_num,
        "lives_left": state.lives,
        "failure": state.get_failure_analysis(),
        "events": state.get_event_log()[-50:],
        "snapshot": state.get_snapshot(),
    }


if __name__ == "__main__":
    import json

    # Test with the expert job (100885: 予愿安洁莉娜 single core)
    with open("data/expert_jobs/act44side_07/100885.json", encoding="utf-8") as f:
        job = json.load(f)

    print("=== Simulating expert job 100885 ===")
    print("opers:", [o.get("name") for o in job.get("opers", [])])
    print("actions:")
    for a in job.get("actions", []):
        print("  %s" % json.dumps(a, ensure_ascii=False)[:80])

    result = run_job("act44side_07", job)

    print()
    print("Result: %s" % result["result"])
    print("Ticks: %d (%.1fs)" % (result["ticks"], result["ticks"] * 0.1))
    print("Lives left: %d" % result["lives_left"])
    print()
    print("Failure analysis:")
    fa = result["failure"]
    for k, v in fa.items():
        print("  %s: %s" % (k, v))
    print()
    print("Last 20 events:")
    for e in result["events"][-20:]:
        print("  t=%.1f %s %s" % (e["tick"], e["event"], {k:v for k,v in e.items() if k not in ("tick","event")}))
