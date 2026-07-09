#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
2–4 人整套卡牌平衡模拟器 v0.2

用途：
1. 模拟 2 到 4 人对战流程。
2. 粗测整套游侠、物品卡、节点技能费用的平衡性。
3. 输出各座位胜率、平均结束回合、卡牌使用率、节点技能使用率、疑似强卡 / 弱卡。

运行：
    python scripts/balance_simulator.py --players 2 --games 1000 --seed 7
    python scripts/balance_simulator.py --players 3 --games 1000 --seed 7
    python scripts/balance_simulator.py --players 4 --games 1000 --seed 7

重要说明：
- 这是平衡压力测试器，不是最终规则引擎。
- 当前用启发式 AI 模拟玩家行为，不代表真人最优解。
- 当前复制数是临时测试复制数：30 张不同手牌卡默认各 2 张，侦察员和数据抽取器额外各 1 张，总计 62 张。
- 每名玩家使用一副相同的 62 张临时测试牌库。
- 当前节点控制模型采用“节点上某玩家 ATK 总和最高且大于 0，即控制该节点”。并列时控制权不变。
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
    player_count: int
    played: Counter[str] = field(default_factory=Counter)
    drawn: Counter[str] = field(default_factory=Counter)
    node_skill_used: Counter[str] = field(default_factory=Counter)


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


class Game:
    def __init__(self, rng: random.Random, player_count: int, max_turns: int = 30, opening_hand: int = 5):
        if player_count not in (2, 3, 4):
            raise ValueError("player_count 只支持 2、3、4")
        self.rng = rng
        self.player_count = player_count
        self.max_turns = max_turns
        self.opening_hand = opening_hand
        self.nodes = [NodeState(node) for node in NODES]
        self.players = [PlayerState(pid, self.make_deck()) for pid in range(player_count)]
        self.stats = GameStats(winner=None, turns=0, reason="", player_count=player_count)
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

    def enemies(self, pid: int) -> list[int]:
        return [p.pid for p in self.players if p.pid != pid]

    def entities_of(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        result: list[tuple[NodeState, Entity]] = []
        for node in self.nodes:
            for entity in node.entities:
                if entity.owner == pid and (card_type is None or entity.card.card_type == card_type):
                    result.append((node, entity))
        return result

    def enemy_entities(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        result: list[tuple[NodeState, Entity]] = []
        for enemy_pid in self.enemies(pid):
            result.extend(self.entities_of(enemy_pid, card_type))
        return result

    def node_power(self, node: NodeState, pid: int) -> int:
        return sum(entity.atk for entity in node.entities if entity.owner == pid)

    def strongest_enemy_pid_on_node(self, node: NodeState, pid: int) -> Optional[int]:
        enemies = self.enemies(pid)
        if not enemies:
            return None
        return max(enemies, key=lambda enemy: self.node_power(node, enemy))

    def update_control(self) -> None:
        for node in self.nodes:
            if node.protected_by is not None:
                continue
            powers = {player.pid: self.node_power(node, player.pid) for player in self.players}
            best_power = max(powers.values())
            if best_power <= 0:
                continue
            leaders = [pid for pid, power in powers.items() if power == best_power]
            if len(leaders) == 1:
                node.controller = leaders[0]
            # 并列时控制权不变。

    def check_winner(self) -> Optional[int]:
        domains = sorted({node.definition.domain for node in self.nodes})
        for pid in range(self.player_count):
            for domain in domains:
                domain_nodes = [node for node in self.nodes if node.definition.domain == domain]
                if all(node.online and node.controller == pid for node in domain_nodes):
                    return pid
        return None

    def best_node_for_deploy(self, pid: int) -> NodeState:
        domains = sorted({node.definition.domain for node in self.nodes})
        # 先补齐自己已经控制 2 座的系统域。
        for domain in domains:
            domain_nodes = [n for n in self.nodes if n.definition.domain == domain and n.online]
            if sum(1 for n in domain_nodes if n.controller == pid) == 2:
                candidates = [n for n in domain_nodes if n.controller != pid]
                if candidates:
                    return min(candidates, key=lambda n: self.node_power(n, pid) - max(self.node_power(n, e) for e in self.enemies(pid)))
        # 再阻止任意对手补齐 3 座。
        for enemy_pid in self.enemies(pid):
            for domain in domains:
                domain_nodes = [n for n in self.nodes if n.definition.domain == domain and n.online]
                if sum(1 for n in domain_nodes if n.controller == enemy_pid) == 2:
                    candidates = [n for n in domain_nodes if n.controller == enemy_pid]
                    if candidates:
                        return min(candidates, key=lambda n: self.node_power(n, pid) - self.node_power(n, enemy_pid))
        # 最后选择最容易翻盘或空置的节点。
        return min(
            self.nodes,
            key=lambda n: (
                self.node_power(n, pid) - max([self.node_power(n, e) for e in self.enemies(pid)] or [0]),
                len(n.entities),
            ),
        )

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
        # 多人局中优先打击当前节点控制者、接近胜利者、高 ATK / 高费用目标。
        def score(item: tuple[NodeState, Entity]) -> tuple[int, int, int, int, int]:
            node, entity = item
            owner_controls_node = 1 if node.controller == entity.owner else 0
            owner_controlled_online = sum(1 for n in self.nodes if n.controller == entity.owner and n.online)
            return (owner_controlled_online, owner_controls_node, entity.atk, entity.card.cost, entity.hp)
        return max(targets, key=score)

    def choose_own_target(self, pid: int, card_type: Optional[CardType] = None) -> Optional[tuple[NodeState, Entity]]:
        targets = self.entities_of(pid, card_type)
        if not targets:
            return None
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
            top = player.deck[:3]
            top.sort(key=lambda c: c.cost, reverse=True)
            player.deck[: len(top)] = top
            return True
        if skill == "shuffle_deck":
            self.rng.shuffle(player.deck)
            return True
        if skill == "look_hand":
            return bool(self.enemies(pid))
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
            node, entity = target
            strongest_enemy = max([self.node_power(node, e) for e in self.enemies(pid)] or [0])
            if node.controller == pid and self.node_power(node, pid) - entity.atk <= strongest_enemy:
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
        target_required = {
            "return_enemy_runner_hand", "return_enemy_equipment_hand", "return_enemy_runner_deck_bottom",
            "destroy_enemy_runner", "destroy_enemy_equipment", "destroy_enemy_any", "multi_return_enemy_runners_hand",
            "return_own_runner_hand", "return_own_equipment_hand",
        }
        if card.skill in target_required:
            if not self.resolve_skill(pid, card):
                return False
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
        controlled = [n for n in self.nodes if n.controller == pid and n.online and n.definition.skill_fee <= player.charge]
        if not controlled:
            return
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
            target = max(offline, key=lambda n: 1 if n.controller == pid else 0)
            target.online = True
            return True
        if skill == "offline_node":
            targets = [n for n in self.nodes if n.online and n.controller in self.enemies(pid)]
            if not targets:
                return False
            target = max(targets, key=lambda n: sum(1 for m in self.nodes if m.controller == n.controller and m.online))
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
            ctype: CardType = "runner" if skill == "move_runner" else "equipment"
            own = self.entities_of(pid, ctype)
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
                break
            actions += 1
        self.update_control()

    def run(self) -> GameStats:
        for turn in range(1, self.max_turns + 1):
            self.reset_turn_flags()
            for pid in range(self.player_count):
                self.take_turn(turn, pid)
                winner = self.check_winner()
                if winner is not None:
                    self.stats.winner = winner
                    self.stats.turns = turn
                    self.stats.reason = "root_access"
                    return self.stats
        self.stats.turns = self.max_turns
        online_counts = {pid: sum(1 for n in self.nodes if n.controller == pid and n.online) for pid in range(self.player_count)}
        best = max(online_counts.values())
        leaders = [pid for pid, count in online_counts.items() if count == best]
        if best > 0 and len(leaders) == 1:
            self.stats.winner = leaders[0]
            self.stats.reason = "node_count_tiebreak"
        else:
            self.stats.winner = None
            self.stats.reason = "draw"
        return self.stats


def run_many(games: int, seed: int, max_turns: int, player_count: int) -> list[GameStats]:
    root_rng = random.Random(seed)
    results: list[GameStats] = []
    for _ in range(games):
        game = Game(random.Random(root_rng.randrange(10**12)), player_count=player_count, max_turns=max_turns)
        results.append(game.run())
    return results


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def report(results: list[GameStats]) -> None:
    games = len(results)
    player_count = results[0].player_count if results else 0
    wins = Counter(r.winner for r in results)
    reasons = Counter(r.reason for r in results)
    turns = [r.turns for r in results]
    played_total: Counter[str] = Counter()
    node_skill_total: Counter[str] = Counter()
    for r in results:
        played_total.update(r.played)
        node_skill_total.update(r.node_skill_used)

    print("归零者 / ZERO ACCESS｜2–4 人整套卡牌平衡模拟报告")
    print("=" * 72)
    print(f"玩家数：{player_count}")
    print(f"模拟局数：{games}")
    for pid in range(player_count):
        print(f"P{pid}胜率：{pct(wins[pid] / games)}")
    print(f"平局率：{pct(wins[None] / games)}")
    print(f"平均结束回合：{statistics.mean(turns):.2f}")
    print(f"中位结束回合：{statistics.median(turns):.1f}")
    print("结束原因：" + ", ".join(f"{k}={v}" for k, v in reasons.items()))

    print("\n使用率最高的手牌卡 Top 12")
    for code, count in played_total.most_common(12):
        card = next(c for c in CARDS if c.code == code)
        print(f"- {code} {card.name}: 使用 {count} 次，平均每局 {count / games:.2f}")

    print("\n使用率最低的手牌卡 Bottom 12")
    for code in sorted([c.code for c in CARDS], key=lambda c: played_total[c])[:12]:
        card = next(c for c in CARDS if c.code == code)
        print(f"- {code} {card.name}: 使用 {played_total[code]} 次，平均每局 {played_total[code] / games:.2f}")

    print("\n节点技能使用 Top 12")
    for code, count in node_skill_total.most_common(12):
        node = next(n for n in NODES if n.code == code)
        print(f"- {code} {node.name}: 使用 {count} 次，平均每局 {count / games:.2f}")

    print("\n疑似平衡问题")
    suspicious = []
    expected_win = 1 / player_count if player_count else 0
    for pid in range(player_count):
        win_rate = wins[pid] / games
        if abs(win_rate - expected_win) > 0.08:
            suspicious.append(f"P{pid} 胜率偏离均值：{pct(win_rate)}，理论均值约 {pct(expected_win)}。可能存在座位优势或回合顺序优势。")
    for card in CARDS:
        used = played_total[card.code]
        if used / games > 1.2 * player_count / 2:
            suspicious.append(f"{card.code} {card.name} 使用率过高：{used / games:.2f}/局，可能过强或过便宜。")
        if used / games < 0.03:
            suspicious.append(f"{card.code} {card.name} 使用率过低：{used / games:.2f}/局，可能太贵、目标太少或 AI 不会用。")
    if suspicious:
        for line in suspicious[:40]:
            print(f"- {line}")
    else:
        print("- 未发现明显异常。")

    print("\n当前模拟假设")
    print("- 支持 2、3、4 人。")
    print("- 每名玩家使用相同 62 张临时复制数牌库。")
    print("- 节点全部开局在线且无主。")
    print("- 节点控制采用各玩家 ATK 总和比较，最高者控制；并列时控制权不变。")
    print("- 控制同一系统域 3 座在线节点立即获胜。")
    print("- 不模拟完整战斗伤害；消灭 / 返回类技能直接改变场面。")
    print("- AI 是启发式，不代表真人最优打法。")


def main() -> int:
    parser = argparse.ArgumentParser(description="归零者 2–4 人整套卡牌平衡模拟器")
    parser.add_argument("--players", type=int, default=2, choices=[2, 3, 4], help="玩家数：2、3、4，默认2")
    parser.add_argument("--games", type=int, default=1000, help="模拟局数，默认1000")
    parser.add_argument("--seed", type=int, default=7, help="随机种子，默认7")
    parser.add_argument("--max-turns", type=int, default=30, help="最大回合数，默认30")
    args = parser.parse_args()
    if args.games <= 0:
        raise SystemExit("--games 必须大于0")
    results = run_many(args.games, args.seed, args.max_turns, args.players)
    report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
