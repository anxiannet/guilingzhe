#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
整套卡牌平衡模拟器 v0.1

用途：
1. 模拟双人对战流程。
2. 粗测整套游侠、物品卡、节点技能费用的平衡性。
3. 输出胜率、平均结束回合、卡牌使用率、疑似强卡 / 弱卡。

运行：
    python scripts/balance_simulator.py --games 1000 --seed 7

重要说明：
- 这是第一版“平衡压力测试器”，不是最终规则引擎。
- 当前用启发式 AI 模拟玩家行为，不代表真人最优解。
- 当前复制数是临时测试复制数：30 张不同手牌卡默认各 2 张，侦察员和数据抽取器额外各 1 张，总计 62 张。
- 当前节点控制模型采用“节点上己方 ATK 总和 > 对方 ATK 总和即控制”的临时规则。
- 当前不模拟完整战斗伤害，因为标准版手牌技能已移除伤害 / 治疗类动作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
from collections import Counter, defaultdict
import argparse
import random
import statistics

CardType = Literal["runner", "one_shot", "equipment"]
SkillKind = Literal[
    "draw",
    "look_deck_top",
    "look_and_reorder",
    "shuffle_deck",
    "look_hand",
    "return_own_runner_hand",
    "return_own_equipment_hand",
    "return_enemy_runner_hand",
    "return_enemy_equipment_hand",
    "return_enemy_runner_deck_bottom",
    "destroy_enemy_runner",
    "destroy_enemy_equipment",
    "destroy_enemy_any",
    "multi_return_enemy_runners_hand",
    "passive_return_when_destroyed",
]

NodeSkill = Literal[
    "view_node",
    "online_node",
    "offline_node",
    "protect_node",
    "move_runner",
    "move_equipment",
    "buff_runner_atk",
    "buff_runner_hp",
    "recover_runner",
    "buff_equipment_atk",
    "buff_equipment_hp",
    "recover_equipment",
]


@dataclass(frozen=True)
class CardDef:
    code: str
    name: str
    card_type: CardType
    cost: int
    atk: Optional[int]
    hp: Optional[int]
    skill: SkillKind


@dataclass(frozen=True)
class NodeDef:
    code: str
    name: str
    domain: str
    skill_fee: int
    skill: NodeSkill


@dataclass
class Entity:
    uid: int
    card: CardDef
    owner: int
    atk_bonus: int = 0
    hp_bonus: int = 0

    @property
    def atk(self) -> int:
        return (self.card.atk or 0) + self.atk_bonus

    @property
    def hp(self) -> int:
        return (self.card.hp or 0) + self.hp_bonus


@dataclass
class NodeState:
    definition: NodeDef
    online: bool = True
    protected_by: Optional[int] = None
    controller: Optional[int] = None
    entities: list[Entity] = field(default_factory=list)


@dataclass
class PlayerState:
    pid: int
    deck: list[CardDef]
    hand: list[CardDef] = field(default_factory=list)
    discard: list[CardDef] = field(default_factory=list)
    charge: int = 0
    used_node_skill_this_turn: bool = False


@dataclass
class GameStats:
    winner: Optional[int]
    turns: int
    reason: str
    played: Counter[str] = field(default_factory=Counter)
    drawn: Counter[str] = field(default_factory=Counter)
    node_skill_used: Counter[str] = field(default_factory=Counter)


# ============================================================
# 当前卡表数据
# ============================================================

CARDS: list[CardDef] = [
    CardDef("R-001", "侦察员", "runner", 1, 1, 1, "look_deck_top"),
    CardDef("R-002", "信使", "runner", 1, 1, 1, "draw"),
    CardDef("R-003", "预读者", "runner", 2, 1, 2, "look_and_reorder"),
    CardDef("R-004", "扒手", "runner", 1, 1, 1, "look_hand"),
    CardDef("R-005", "广播员", "runner", 2, 1, 2, "draw"),
    CardDef("R-007", "烟幕手", "runner", 2, 1, 2, "return_own_runner_hand"),
    CardDef("R-008", "洗牌客", "runner", 1, 1, 1, "shuffle_deck"),
    CardDef("R-009", "清除者", "runner", 4, 3, 2, "destroy_enemy_runner"),
    CardDef("R-010", "甩棍", "runner", 2, 2, 1, "return_enemy_runner_hand"),
    CardDef("R-013", "不倒翁", "runner", 3, 1, 3, "passive_return_when_destroyed"),
    CardDef("R-014", "筛选员", "runner", 3, 1, 2, "look_and_reorder"),
    CardDef("R-015", "扳手", "runner", 2, 1, 2, "return_own_equipment_hand"),
    CardDef("R-016", "推土机", "runner", 4, 3, 3, "multi_return_enemy_runners_hand"),
    CardDef("R-017", "爆破兵", "runner", 4, 3, 2, "destroy_enemy_equipment"),
    CardDef("R-018", "门神", "runner", 3, 1, 4, "return_enemy_runner_hand"),
    CardDef("R-020", "快手", "runner", 2, 2, 1, "draw"),
    CardDef("R-021", "中间人", "runner", 3, 1, 2, "look_hand"),
    CardDef("R-023", "清道夫", "runner", 5, 3, 3, "destroy_enemy_any"),
    CardDef("G-001", "回收沙盒", "one_shot", 3, None, None, "return_enemy_runner_deck_bottom"),
    CardDef("G-002", "侦测芯片", "one_shot", 1, None, None, "look_hand"),
    CardDef("G-003", "权限束带", "one_shot", 2, None, None, "return_enemy_runner_hand"),
    CardDef("G-004", "撤离信标", "one_shot", 2, None, None, "return_own_runner_hand"),
    CardDef("G-005", "强制回滚扇区", "one_shot", 5, None, None, "multi_return_enemy_runners_hand"),
    CardDef("G-006", "蜂鸟无人机", "equipment", 1, 0, 1, "look_deck_top"),
    CardDef("G-007", "回收地堡", "equipment", 4, 1, 5, "return_own_equipment_hand"),
    CardDef("G-008", "牵引锚", "one_shot", 2, None, None, "return_enemy_equipment_hand"),
    CardDef("G-009", "探针", "one_shot", 1, None, None, "look_deck_top"),
    CardDef("G-010", "算盘", "one_shot", 2, None, None, "look_and_reorder"),
    CardDef("G-011", "数据抽取器", "one_shot", 1, None, None, "draw"),
    CardDef("G-012", "弹弓", "one_shot", 2, None, None, "return_enemy_runner_hand"),
]

NODES: list[NodeDef] = [
    NodeDef("N-001", "档案馆", "行政域", 1, "view_node"),
    NodeDef("N-002", "中继站", "行政域", 2, "online_node"),
    NodeDef("N-003", "屏蔽塔", "行政域", 2, "offline_node"),
    NodeDef("N-004", "防火墙", "安防域", 2, "protect_node"),
    NodeDef("N-005", "传送站", "安防域", 1, "move_runner"),
    NodeDef("N-006", "货运站", "安防域", 1, "move_equipment"),
    NodeDef("N-007", "蜂鸟巢", "生化域", 1, "buff_runner_atk"),
    NodeDef("N-008", "纳米诊所", "生化域", 1, "buff_runner_hp"),
    NodeDef("N-009", "复苏舱", "生化域", 2, "recover_runner"),
    NodeDef("N-010", "武研所", "科研域", 1, "buff_equipment_atk"),
    NodeDef("N-011", "装研所", "科研域", 1, "buff_equipment_hp"),
    NodeDef("N-012", "回收中心", "科研域", 2, "recover_equipment"),
]

DEFAULT_COPIES: dict[str, int] = {card.code: 2 for card in CARDS}
DEFAULT_COPIES["R-001"] += 1
DEFAULT_COPIES["G-011"] += 1


# ============================================================
# 游戏模拟
# ============================================================

class Game:
    def __init__(self, rng: random.Random, max_turns: int = 30, opening_hand: int = 5):
        self.rng = rng
        self.max_turns = max_turns
        self.opening_hand = opening_hand
        self.nodes = [NodeState(node) for node in NODES]
        self.players = [PlayerState(0, self.make_deck()), PlayerState(1, self.make_deck())]
        self.stats = GameStats(winner=None, turns=0, reason="")
        self.next_uid = 1
        for player in self.players:
            self.rng.shuffle(player.deck)
            self.draw(player, opening_hand)

    def make_deck(self) -> list[CardDef]:
        by_code = {card.code: card for card in CARDS}
        deck: list[CardDef] = []
        for code, count in DEFAULT_COPIES.items():
            deck.extend([by_code[code]] * count)
        assert len(deck) == 62, f"临时测试牌库必须为62张，当前={len(deck)}"
        return deck

    def draw(self, player: PlayerState, n: int = 1) -> None:
        for _ in range(n):
            if not player.deck:
                if player.discard:
                    player.deck = player.discard[:]
                    player.discard.clear()
                    self.rng.shuffle(player.deck)
                else:
                    return
            card = player.deck.pop(0)
            player.hand.append(card)
            self.stats.drawn[card.code] += 1

    def opponent(self, pid: int) -> int:
        return 1 - pid

    def entities_of(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        result = []
        for node in self.nodes:
            for entity in node.entities:
                if entity.owner == pid and (card_type is None or entity.card.card_type == card_type):
                    result.append((node, entity))
        return result

    def enemy_entities(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        return self.entities_of(self.opponent(pid), card_type)

    def node_power(self, node: NodeState, pid: int) -> int:
        return sum(entity.atk for entity in node.entities if entity.owner == pid)

    def update_control(self) -> None:
        for node in self.nodes:
            if node.protected_by is not None:
                # 保护节点不能被接管，但当前控制者仍保留。
                continue
            p0 = self.node_power(node, 0)
            p1 = self.node_power(node, 1)
            if p0 > p1 and p0 > 0:
                node.controller = 0
            elif p1 > p0 and p1 > 0:
                node.controller = 1

    def check_winner(self) -> Optional[int]:
        domains = sorted({node.definition.domain for node in self.nodes})
        for pid in [0, 1]:
            for domain in domains:
                domain_nodes = [node for node in self.nodes if node.definition.domain == domain]
                if all(node.online and node.controller == pid for node in domain_nodes):
                    return pid
        return None

    def best_node_for_deploy(self, pid: int) -> NodeState:
        # 类人启发式：优先补齐自己已控制2座的系统域，其次争夺对方控制2座的系统域，再选己方差距最小节点。
        domains = sorted({node.definition.domain for node in self.nodes})
        for domain in domains:
            domain_nodes = [n for n in self.nodes if n.definition.domain == domain and n.online]
            if sum(1 for n in domain_nodes if n.controller == pid) == 2:
                candidates = [n for n in domain_nodes if n.controller != pid]
                if candidates:
                    return min(candidates, key=lambda n: self.node_power(n, pid) - self.node_power(n, self.opponent(pid)))
        for domain in domains:
            domain_nodes = [n for n in self.nodes if n.definition.domain == domain and n.online]
            if sum(1 for n in domain_nodes if n.controller == self.opponent(pid)) == 2:
                candidates = [n for n in domain_nodes if n.controller == self.opponent(pid)]
                if candidates:
                    return min(candidates, key=lambda n: self.node_power(n, pid) - self.node_power(n, self.opponent(pid)))
        return min(self.nodes, key=lambda n: (self.node_power(n, pid) - self.node_power(n, self.opponent(pid)), len(n.entities)))

    def remove_entity(self, node: NodeState, entity: Entity, destination: str) -> None:
        node.entities.remove(entity)
        owner = self.players[entity.owner]
        card = entity.card
        if destination == "hand":
            owner.hand.append(card)
        elif destination == "deck_bottom":
            owner.deck.append(card)
        elif destination == "discard":
            if card.skill == "passive_return_when_destroyed":
                owner.hand.append(card)
            else:
                owner.discard.append(card)
        else:
            raise ValueError(f"unknown destination: {destination}")

    def choose_enemy_target(self, pid: int, card_type: Optional[CardType] = None) -> Optional[tuple[NodeState, Entity]]:
        targets = self.enemy_entities(pid, card_type)
        if not targets:
            return None
        # 优先打击对方高 ATK / 高费用目标。
        return max(targets, key=lambda item: (item[1].atk, item[1].card.cost, item[1].hp))

    def choose_own_target(self, pid: int, card_type: Optional[CardType] = None) -> Optional[tuple[NodeState, Entity]]:
        targets = self.entities_of(pid, card_type)
        if not targets:
            return None
        # 只有在目标有较高费用时才值得主动撤回。
        return max(targets, key=lambda item: (item[1].card.cost, item[1].hp))

    def resolve_skill(self, pid: int, card: CardDef) -> bool:
        player = self.players[pid]
        skill = card.skill

        if skill == "draw":
            self.draw(player, 1)
            return True
        if skill == "look_deck_top":
            return True
        if skill == "look_and_reorder":
            # 类人启发式：把最高费用牌放到最前，模拟筛选资源。
            top = player.deck[:3]
            top.sort(key=lambda c: c.cost, reverse=True)
            player.deck[: len(top)] = top
            return True
        if skill == "shuffle_deck":
            self.rng.shuffle(player.deck)
            return True
        if skill == "look_hand":
            return True
        if skill == "return_enemy_runner_hand":
            target = self.choose_enemy_target(pid, "runner")
            if not target:
                return False
            self.remove_entity(*target, destination="hand")
            return True
        if skill == "return_enemy_equipment_hand":
            target = self.choose_enemy_target(pid, "equipment")
            if not target:
                return False
            self.remove_entity(*target, destination="hand")
            return True
        if skill == "return_enemy_runner_deck_bottom":
            target = self.choose_enemy_target(pid, "runner")
            if not target:
                return False
            self.remove_entity(*target, destination="deck_bottom")
            return True
        if skill == "destroy_enemy_runner":
            target = self.choose_enemy_target(pid, "runner")
            if not target:
                return False
            self.remove_entity(*target, destination="discard")
            return True
        if skill == "destroy_enemy_equipment":
            target = self.choose_enemy_target(pid, "equipment")
            if not target:
                return False
            self.remove_entity(*target, destination="discard")
            return True
        if skill == "destroy_enemy_any":
            target = self.choose_enemy_target(pid, None)
            if not target:
                return False
            self.remove_entity(*target, destination="discard")
            return True
        if skill == "multi_return_enemy_runners_hand":
            targets = self.enemy_entities(pid, "runner")
            if not targets:
                return False
            targets.sort(key=lambda item: (item[1].atk, item[1].card.cost), reverse=True)
            count = 99 if card.code == "G-005" else 2
            for node, entity in targets[:count]:
                if entity in node.entities:
                    self.remove_entity(node, entity, destination="hand")
            return True
        if skill == "return_own_runner_hand":
            target = self.choose_own_target(pid, "runner")
            if not target:
                return False
            # 如果撤回会丢掉即将胜利的节点，不用。
            node, entity = target
            if node.controller == pid and self.node_power(node, pid) - entity.atk <= self.node_power(node, self.opponent(pid)):
                return False
            self.remove_entity(node, entity, destination="hand")
            return True
        if skill == "return_own_equipment_hand":
            target = self.choose_own_target(pid, "equipment")
            if not target:
                return False
            self.remove_entity(*target, destination="hand")
            return True
        if skill == "passive_return_when_destroyed":
            return True
        raise ValueError(f"unknown skill: {skill}")

    def play_card(self, pid: int, card: CardDef) -> bool:
        player = self.players[pid]
        if player.charge < card.cost or card not in player.hand:
            return False

        # 对于需要目标的强交互牌，没有目标就不打。
        target_required = {
            "return_enemy_runner_hand", "return_enemy_equipment_hand", "return_enemy_runner_deck_bottom",
            "destroy_enemy_runner", "destroy_enemy_equipment", "destroy_enemy_any", "multi_return_enemy_runners_hand",
            "return_own_runner_hand", "return_own_equipment_hand",
        }
        if card.skill in target_required:
            possible = self.resolve_skill(pid, card)
            if not possible:
                return False
            # resolve_skill 已执行，下面只负责费用、移除手牌、部署。
            player.hand.remove(card)
            player.charge -= card.cost
        else:
            player.hand.remove(card)
            player.charge -= card.cost
            self.resolve_skill(pid, card)

        if card.card_type in ("runner", "equipment"):
            node = self.best_node_for_deploy(pid)
            node.entities.append(Entity(self.next_uid, card, pid))
            self.next_uid += 1
        else:
            player.discard.append(card)

        self.stats.played[card.code] += 1
        self.update_control()
        return True

    def choose_card_to_play(self, pid: int) -> Optional[CardDef]:
        player = self.players[pid]
        playable = [card for card in player.hand if card.cost <= player.charge]
        if not playable:
            return None

        priority = {
            "destroy_enemy_any": 100,
            "destroy_enemy_runner": 95,
            "destroy_enemy_equipment": 90,
            "multi_return_enemy_runners_hand": 85,
            "return_enemy_runner_deck_bottom": 80,
            "return_enemy_runner_hand": 75,
            "return_enemy_equipment_hand": 70,
            "draw": 60,
            "look_and_reorder": 55,
            "look_deck_top": 45,
            "look_hand": 45,
            "shuffle_deck": 35,
            "passive_return_when_destroyed": 30,
            "return_own_runner_hand": 20,
            "return_own_equipment_hand": 20,
        }
        playable.sort(key=lambda c: (priority.get(c.skill, 0), c.cost, c.atk or 0, c.hp or 0), reverse=True)
        return playable[0]

    def use_node_skill(self, pid: int) -> None:
        player = self.players[pid]
        if player.used_node_skill_this_turn:
            return
        controlled = [node for node in self.nodes if node.controller == pid and node.online and node.definition.skill_fee <= player.charge]
        if not controlled:
            return
        # 优先保护即将完成胜利域的节点，其次使用资源回收或移动。
        controlled.sort(key=lambda n: n.definition.skill_fee, reverse=True)
        for node in controlled:
            if self.resolve_node_skill(pid, node):
                player.charge -= node.definition.skill_fee
                player.used_node_skill_this_turn = True
                self.stats.node_skill_used[node.definition.code] += 1
                self.update_control()
                return

    def resolve_node_skill(self, pid: int, node: NodeState) -> bool:
        skill = node.definition.skill
        player = self.players[pid]
        if skill == "view_node":
            return True
        if skill == "online_node":
            offline = [n for n in self.nodes if not n.online]
            if not offline:
                return False
            # 优先上线自己控制的节点。
            target = max(offline, key=lambda n: 1 if n.controller == pid else 0)
            target.online = True
            return True
        if skill == "offline_node":
            targets = [n for n in self.nodes if n.online and n.controller == self.opponent(pid)]
            if not targets:
                return False
            target = self.rng.choice(targets)
            target.online = False
            return True
        if skill == "protect_node":
            own = [n for n in self.nodes if n.controller == pid and n.protected_by is None]
            if not own:
                return False
            target = self.rng.choice(own)
            target.protected_by = pid
            return True
        if skill in ("move_runner", "move_equipment"):
            ctype = "runner" if skill == "move_runner" else "equipment"
            own = self.entities_of(pid, ctype)  # type: ignore[arg-type]
            if not own:
                return False
            from_node, entity = max(own, key=lambda item: item[1].atk)
            to_node = self.best_node_for_deploy(pid)
            if from_node is to_node:
                return False
            from_node.entities.remove(entity)
            to_node.entities.append(entity)
            return True
        if skill == "buff_runner_atk":
            own = self.entities_of(pid, "runner")
            if not own:
                return False
            _, entity = max(own, key=lambda item: item[1].atk)
            entity.atk_bonus += 1
            return True
        if skill == "buff_runner_hp":
            own = self.entities_of(pid, "runner")
            if not own:
                return False
            _, entity = max(own, key=lambda item: item[1].hp)
            entity.hp_bonus += 1
            return True
        if skill == "recover_runner":
            candidates = [c for c in player.discard if c.card_type == "runner"]
            if not candidates:
                return False
            card = max(candidates, key=lambda c: c.cost)
            player.discard.remove(card)
            player.hand.append(card)
            return True
        if skill == "buff_equipment_atk":
            own = self.entities_of(pid, "equipment")
            if not own:
                return False
            _, entity = max(own, key=lambda item: item[1].atk)
            entity.atk_bonus += 1
            return True
        if skill == "buff_equipment_hp":
            own = self.entities_of(pid, "equipment")
            if not own:
                return False
            _, entity = max(own, key=lambda item: item[1].hp)
            entity.hp_bonus += 1
            return True
        if skill == "recover_equipment":
            candidates = [c for c in player.discard if c.card_type == "equipment"]
            if not candidates:
                return False
            card = max(candidates, key=lambda c: c.cost)
            player.discard.remove(card)
            player.hand.append(card)
            return True
        return False

    def reset_turn_flags(self) -> None:
        for player in self.players:
            player.used_node_skill_this_turn = False
        for node in self.nodes:
            if node.protected_by is not None:
                # 简化：保护持续到保护者下回合开始；这里在每个完整回合末清一次。
                node.protected_by = None

    def take_turn(self, turn_number: int, pid: int) -> None:
        player = self.players[pid]
        player.charge = min(turn_number, 10)
        self.draw(player, 1)
        self.use_node_skill(pid)
        actions = 0
        while actions < 8:
            card = self.choose_card_to_play(pid)
            if card is None:
                break
            if not self.play_card(pid, card):
                # 防止 AI 卡在无法使用的最高优先级牌上。
                player.hand.remove(card)
                player.hand.append(card)
                break
            actions += 1
        self.update_control()

    def run(self) -> GameStats:
        for turn in range(1, self.max_turns + 1):
            self.reset_turn_flags()
            for pid in [0, 1]:
                self.take_turn(turn, pid)
                winner = self.check_winner()
                if winner is not None:
                    self.stats.winner = winner
                    self.stats.turns = turn
                    self.stats.reason = "root_access"
                    return self.stats
        self.stats.turns = self.max_turns
        p0_nodes = sum(1 for node in self.nodes if node.controller == 0 and node.online)
        p1_nodes = sum(1 for node in self.nodes if node.controller == 1 and node.online)
        if p0_nodes > p1_nodes:
            self.stats.winner = 0
            self.stats.reason = "node_count_tiebreak"
        elif p1_nodes > p0_nodes:
            self.stats.winner = 1
            self.stats.reason = "node_count_tiebreak"
        else:
            self.stats.winner = None
            self.stats.reason = "draw"
        return self.stats


# ============================================================
# 批量模拟与报告
# ============================================================

def run_many(games: int, seed: int, max_turns: int) -> list[GameStats]:
    root_rng = random.Random(seed)
    results: list[GameStats] = []
    for _ in range(games):
        game = Game(random.Random(root_rng.randrange(10**12)), max_turns=max_turns)
        results.append(game.run())
    return results


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def report(results: list[GameStats]) -> None:
    games = len(results)
    wins = Counter(r.winner for r in results)
    reasons = Counter(r.reason for r in results)
    turns = [r.turns for r in results]
    played_total: Counter[str] = Counter()
    drawn_total: Counter[str] = Counter()
    node_skill_total: Counter[str] = Counter()
    winner_played: dict[str, Counter[int]] = defaultdict(Counter)

    for r in results:
        played_total.update(r.played)
        drawn_total.update(r.drawn)
        node_skill_total.update(r.node_skill_used)
        for code, count in r.played.items():
            if r.winner is not None:
                winner_played[code][r.winner] += count

    print("归零者 / ZERO ACCESS｜整套卡牌平衡模拟报告")
    print("=" * 72)
    print(f"模拟局数：{games}")
    print(f"P0胜率：{pct(wins[0] / games)}")
    print(f"P1胜率：{pct(wins[1] / games)}")
    print(f"平局率：{pct(wins[None] / games)}")
    print(f"平均结束回合：{statistics.mean(turns):.2f}")
    print(f"中位结束回合：{statistics.median(turns):.1f}")
    print("结束原因：" + ", ".join(f"{k}={v}" for k, v in reasons.items()))

    print("\n使用率最高的手牌卡 Top 12")
    for code, count in played_total.most_common(12):
        card = next(c for c in CARDS if c.code == code)
        print(f"- {code} {card.name}: 使用 {count} 次，平均每局 {count / games:.2f}")

    print("\n使用率最低的手牌卡 Bottom 12")
    all_codes = [c.code for c in CARDS]
    for code in sorted(all_codes, key=lambda c: played_total[c])[:12]:
        card = next(c for c in CARDS if c.code == code)
        print(f"- {code} {card.name}: 使用 {played_total[code]} 次，平均每局 {played_total[code] / games:.2f}")

    print("\n节点技能使用 Top 12")
    for code, count in node_skill_total.most_common(12):
        node = next(n for n in NODES if n.code == code)
        print(f"- {code} {node.name}: 使用 {count} 次，平均每局 {count / games:.2f}")

    print("\n疑似平衡问题")
    suspicious = []
    for card in CARDS:
        used = played_total[card.code]
        if used / games > 1.2:
            suspicious.append(f"{card.code} {card.name} 使用率过高：{used / games:.2f}/局，可能过强或过便宜。")
        if used / games < 0.05:
            suspicious.append(f"{card.code} {card.name} 使用率过低：{used / games:.2f}/局，可能太贵、目标太少或 AI 不会用。")
    if suspicious:
        for line in suspicious[:30]:
            print(f"- {line}")
    else:
        print("- 未发现明显异常。")

    print("\n当前模拟假设")
    print("- 双人对战，双方使用相同 62 张临时复制数牌库。")
    print("- 节点全部开局在线且无主。")
    print("- 节点控制采用 ATK 总和比较，较高者控制。")
    print("- 控制同一系统域 3 座在线节点立即获胜。")
    print("- 不模拟完整战斗伤害；消灭 / 返回类技能直接改变场面。")
    print("- AI 是启发式，不代表真人最优打法。")


def main() -> int:
    parser = argparse.ArgumentParser(description="归零者整套卡牌平衡模拟器")
    parser.add_argument("--games", type=int, default=1000, help="模拟局数，默认1000")
    parser.add_argument("--seed", type=int, default=7, help="随机种子，默认7")
    parser.add_argument("--max-turns", type=int, default=30, help="最大回合数，默认30")
    args = parser.parse_args()

    if args.games <= 0:
        raise SystemExit("--games 必须大于0")
    results = run_many(args.games, args.seed, args.max_turns)
    report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
