#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
2–4 人整套卡牌平衡模拟器 v0.12

当前规则：
- 所有玩家共用一副 62 张测试手牌牌库与公共弃牌区。
- 节点牌库由 12 张测试节点牌组成，中央区保持 3 张无主节点。
- 节点只有“无主 / 受控”两种归属，不记录旧节点状态。
- 控制同一系统域的 3 座节点，立即获得 Root Access。
- N-002 / N-003 保留测试节点位置，旧技能不进入有效技能池。
- CARDS / NODES 保持测试卡表原状，作为以后对照组。

v0.12：
- baseline：旧式贪心。
- tactical_v3：即时获胜搜索、主攻域锁定、全局威胁扫描。
- tactical_v3 为“自己获胜”或“阻止下一位玩家获胜”锁定行动路线。
- 预留电荷覆盖节点技能、普通出牌与普通攻击。
- 默认 50 轮，并统计 30 轮内 Root Access。
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Optional

CardType = Literal["runner", "one_shot", "equipment"]
NodeZone = Literal["deck", "central", "controlled"]
AIMode = Literal["baseline", "tactical_v3"]

MAX_ENTITIES_PER_NODE = 3
ATTACK_COST_PER_ENTITY = 1
CENTRAL_NODE_TARGET = 3
ROOT_ACCESS_NODE_COUNT = 3
OBSERVATION_TURN = 30
STAGNATION_REFRESH_ROUNDS = 2
REMOVED_NODE_SKILLS = {"pending"}


@dataclass(frozen=True)
class CardDef:
    code: str
    name: str
    card_type: CardType
    cost: int
    atk: Optional[int]
    hp: Optional[int]
    skill: str


@dataclass(frozen=True)
class NodeDef:
    code: str
    name: str
    domain: str
    skill_fee: int
    skill: str


@dataclass
class Entity:
    uid: int
    card: CardDef
    owner: int
    entered_turn: int
    temp_atk: int = 0
    temp_hp: int = 0
    attacked_this_turn: bool = False

    @property
    def atk(self) -> int:
        return (self.card.atk or 0) + self.temp_atk

    @property
    def hp(self) -> int:
        return (self.card.hp or 0) + self.temp_hp


@dataclass
class NodeState:
    definition: NodeDef
    zone: NodeZone = "deck"
    protected_by: Optional[int] = None
    controller: Optional[int] = None
    entities: list[Entity] = field(default_factory=list)
    revealed_turn: int = 0


@dataclass
class PlayerState:
    pid: int
    hand: list[CardDef] = field(default_factory=list)
    charge: int = 0
    used_node_skill_this_turn: bool = False
    focus_domain: Optional[str] = None


@dataclass
class ThreatPlan:
    opponent: int
    domain: str
    node: NodeState
    method: Literal["attack", "return_runner", "return_equipment", "destroy_runner", "destroy_equipment", "destroy_any"]
    cost: int
    card: Optional[CardDef] = None


@dataclass
class GameStats:
    winner: Optional[int]
    turns: int
    reason: str
    player_count: int
    ai_mode: AIMode
    played: Counter[str] = field(default_factory=Counter)
    node_skill_used: Counter[str] = field(default_factory=Counter)
    attacks: int = 0
    control_changes: int = 0
    central_refreshes: int = 0
    block_attempts: int = 0
    successful_blocks: int = 0
    draw_fail_count: int = 0
    deck_empty_observed: bool = False
    first_empty_turn: Optional[int] = None
    root_by_30: bool = False


# 测试卡表保持原状。
CARDS: list[CardDef] = [
    CardDef("R-001", "侦察员", "runner", 1, 1, 1, "look_deck_top"),
    CardDef("R-002", "信使", "runner", 1, 1, 1, "draw"),
    CardDef("R-003", "预读者", "runner", 2, 1, 2, "look_and_reorder"),
    CardDef("R-004", "扒手", "runner", 1, 1, 1, "look_hand"),
    CardDef("R-005", "广播员", "runner", 2, 1, 2, "draw"),
    CardDef("R-007", "烟幕手", "runner", 2, 1, 2, "return_enemy_runner_hand"),
    CardDef("R-008", "洗牌客", "runner", 1, 1, 1, "shuffle_deck"),
    CardDef("R-009", "鹰眼", "runner", 5, 3, 2, "destroy_enemy_runner_cost_4_or_less"),
    CardDef("R-010", "甩棍", "runner", 2, 2, 1, "return_enemy_runner_hand"),
    CardDef("R-013", "不倒翁", "runner", 3, 1, 3, "passive_return_when_destroyed"),
    CardDef("R-014", "筛选员", "runner", 3, 1, 2, "look_reorder_draw"),
    CardDef("R-015", "扳手", "runner", 2, 1, 2, "return_enemy_equipment_hand"),
    CardDef("R-016", "推土机", "runner", 4, 3, 3, "multi_return_enemy_runners_hand"),
    CardDef("R-017", "爆破兵", "runner", 4, 3, 2, "destroy_enemy_equipment"),
    CardDef("R-018", "门神", "runner", 3, 1, 4, "return_enemy_runner_hand"),
    CardDef("R-020", "快手", "runner", 2, 2, 1, "draw_then_bottom"),
    CardDef("R-021", "中间人", "runner", 3, 1, 2, "look_hand_then_draw"),
    CardDef("R-023", "清道夫", "runner", 5, 3, 3, "destroy_enemy_any"),
    CardDef("G-001", "回收沙盒", "one_shot", 3, None, None, "return_enemy_runner_deck_bottom"),
    CardDef("G-002", "侦测芯片", "one_shot", 1, None, None, "look_hand"),
    CardDef("G-003", "权限束带", "one_shot", 2, None, None, "return_enemy_runner_hand"),
    CardDef("G-004", "撤离信标", "one_shot", 2, None, None, "return_own_runner_hand"),
    CardDef("G-005", "强制回滚扇区", "one_shot", 5, None, None, "multi_return_enemy_runners_hand"),
    CardDef("G-006", "蜂鸟无人机", "equipment", 1, 0, 1, "look_deck_top"),
    CardDef("G-007", "回收地堡", "equipment", 4, 1, 5, "look_and_reorder"),
    CardDef("G-008", "牵引锚", "one_shot", 2, None, None, "return_enemy_equipment_hand"),
    CardDef("G-009", "探针", "one_shot", 1, None, None, "look_deck_top"),
    CardDef("G-010", "算盘", "one_shot", 2, None, None, "look_and_reorder"),
    CardDef("G-011", "数据抽取器", "one_shot", 1, None, None, "draw"),
    CardDef("G-012", "弹弓", "one_shot", 2, None, None, "return_enemy_runner_hand"),
]

NODES: list[NodeDef] = [
    NodeDef("N-001", "档案馆", "行政域", 1, "view_node"),
    NodeDef("N-002", "中继站", "行政域", 0, "pending"),
    NodeDef("N-003", "屏蔽塔", "行政域", 0, "pending"),
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

DEFAULT_COPIES = {card.code: 2 for card in CARDS}
DEFAULT_COPIES["R-001"] += 1
DEFAULT_COPIES["G-011"] += 1


class Game:
    def __init__(self, rng: random.Random, player_count: int, max_turns: int = 50, ai_mode: AIMode = "tactical_v3"):
        self.rng = rng
        self.player_count = player_count
        self.max_turns = max_turns
        self.ai_mode = ai_mode
        self.current_turn = 0
        self.players = [PlayerState(pid) for pid in range(player_count)]
        self.turn_order = list(range(player_count))
        self.rng.shuffle(self.turn_order)
        self.shared_deck = self.make_deck()
        self.shared_discard: list[CardDef] = []
        self.nodes = [NodeState(node) for node in NODES]
        self.node_deck = self.nodes[:]
        self.stats = GameStats(None, 0, "", player_count, ai_mode)
        self.next_uid = 1
        self.control_changes_this_round = 0
        self.stagnant_rounds = 0
        self.rng.shuffle(self.node_deck)
        self.replenish_central()
        for player in self.players:
            self.draw(player, 5)

    def make_deck(self) -> list[CardDef]:
        by_code = {card.code: card for card in CARDS}
        deck = [by_code[code] for code, count in DEFAULT_COPIES.items() for _ in range(count)]
        assert len(deck) == 62
        self.rng.shuffle(deck)
        return deck

    def draw(self, player: PlayerState, n: int = 1) -> None:
        for _ in range(n):
            if not self.shared_deck:
                self.stats.draw_fail_count += 1
                self.stats.deck_empty_observed = True
                if self.stats.first_empty_turn is None:
                    self.stats.first_empty_turn = self.current_turn
                return
            player.hand.append(self.shared_deck.pop(0))
            if not self.shared_deck:
                self.stats.deck_empty_observed = True
                if self.stats.first_empty_turn is None:
                    self.stats.first_empty_turn = self.current_turn

    def central_nodes(self) -> list[NodeState]:
        return [node for node in self.nodes if node.zone == "central"]

    def controlled_nodes(self, pid: Optional[int] = None) -> list[NodeState]:
        nodes = [node for node in self.nodes if node.zone == "controlled" and node.controller is not None]
        return nodes if pid is None else [node for node in nodes if node.controller == pid]

    def domain_count(self, pid: int) -> Counter[str]:
        return Counter(node.definition.domain for node in self.controlled_nodes(pid))

    def best_domain_count(self, pid: int) -> int:
        return max(self.domain_count(pid).values(), default=0)

    def register_control_change(self) -> None:
        self.stats.control_changes += 1
        self.control_changes_this_round += 1

    def replenish_central(self) -> None:
        while len(self.central_nodes()) < CENTRAL_NODE_TARGET and self.node_deck:
            node = self.node_deck.pop(0)
            node.zone = "central"
            node.controller = None
            node.protected_by = None
            node.entities.clear()
            node.revealed_turn = self.current_turn

    def return_node_to_deck(self, node: NodeState, shuffle: bool = True) -> None:
        node.zone = "deck"
        node.controller = None
        node.protected_by = None
        node.entities.clear()
        node.revealed_turn = 0
        if node not in self.node_deck:
            self.node_deck.append(node)
        if shuffle:
            self.rng.shuffle(self.node_deck)

    def normalize_nodes(self) -> None:
        changed = False
        for node in self.nodes:
            if node.zone != "controlled":
                continue
            node.entities = [entity for entity in node.entities if entity.owner == node.controller]
            if not node.entities:
                self.return_node_to_deck(node)
                self.register_control_change()
                changed = True
        if changed:
            self.replenish_central()

    def refresh_oldest_central(self) -> None:
        central = self.central_nodes()
        if not central:
            return
        oldest = min(central, key=lambda node: (node.revealed_turn, node.definition.code))
        self.return_node_to_deck(oldest, shuffle=False)
        self.replenish_central()
        self.stats.central_refreshes += 1

    def check_winner(self) -> Optional[int]:
        self.normalize_nodes()
        for pid in range(self.player_count):
            if any(count >= ROOT_ACCESS_NODE_COUNT for count in self.domain_count(pid).values()):
                return pid
        return None

    def next_player_after(self, pid: int) -> int:
        index = self.turn_order.index(pid)
        return self.turn_order[(index + 1) % len(self.turn_order)]

    def choose_focus_domain(self, pid: int) -> str:
        player = self.players[pid]
        if player.focus_domain:
            return player.focus_domain
        own = self.domain_count(pid)
        central = Counter(node.definition.domain for node in self.central_nodes())
        enemy = Counter(node.definition.domain for node in self.controlled_nodes() if node.controller != pid)
        choices = []
        for domain in sorted({node.definition.domain for node in self.nodes}):
            score = own[domain] * 100 + central[domain] * 20 - enemy[domain] * 5
            choices.append((score, self.rng.random(), domain))
        player.focus_domain = max(choices)[2]
        return player.focus_domain

    def entities_of(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        return [
            (node, entity)
            for node in self.controlled_nodes(pid)
            for entity in node.entities
            if entity.owner == pid and (card_type is None or entity.card.card_type == card_type)
        ]

    def enemy_entities(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        return [
            (node, entity)
            for node in self.controlled_nodes()
            for entity in node.entities
            if entity.owner != pid and (card_type is None or entity.card.card_type == card_type)
        ]

    def legal_deploy_nodes(self, pid: int) -> list[NodeState]:
        return self.central_nodes() + [
            node for node in self.controlled_nodes(pid) if len(node.entities) < MAX_ENTITIES_PER_NODE
        ]

    def deploy_score(self, pid: int, node: NodeState) -> tuple[int, int, int, int]:
        progress = self.domain_count(pid)[node.definition.domain]
        return (
            int(node.zone == "central" and progress >= 2),
            int(node.zone == "central"),
            int(node.definition.domain == self.choose_focus_domain(pid)),
            progress,
        )

    def best_deploy_node(self, pid: int) -> Optional[NodeState]:
        nodes = self.legal_deploy_nodes(pid)
        return max(nodes, key=lambda node: self.deploy_score(pid, node)) if nodes else None

    def deploy(self, player: PlayerState, card: CardDef, node: NodeState) -> None:
        node.entities.append(Entity(self.next_uid, card, player.pid, self.current_turn))
        self.next_uid += 1
        if node.zone == "central":
            node.zone = "controlled"
            node.controller = player.pid
            self.register_control_change()
            self.replenish_central()

    def remove_entity(self, node: NodeState, entity: Entity) -> None:
        if entity in node.entities:
            node.entities.remove(entity)

    def destroy_entity(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        if entity.card.skill == "passive_return_when_destroyed":
            self.players[entity.owner].hand.append(entity.card)
        else:
            self.shared_discard.append(entity.card)

    def return_to_hand(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        self.players[entity.owner].hand.append(entity.card)

    def best_target(self, pid: int, card_type: Optional[CardType], max_cost: Optional[int] = None, required_node: Optional[NodeState] = None) -> Optional[tuple[NodeState, Entity]]:
        targets = self.enemy_entities(pid, card_type)
        if max_cost is not None:
            targets = [(node, entity) for node, entity in targets if entity.card.cost <= max_cost]
        if required_node is not None:
            targets = [(node, entity) for node, entity in targets if node is required_node]
        return max(targets, key=lambda ne: (ne[1].card.cost, ne[1].atk, ne[1].hp)) if targets else None

    def resolve_card_skill(self, player: PlayerState, card: CardDef, forced_node: Optional[NodeState] = None) -> bool:
        skill = card.skill
        if skill in {"look_deck_top", "look_hand"}:
            return True
        if skill in {"draw", "look_hand_then_draw"}:
            self.draw(player)
            return True
        if skill == "draw_then_bottom":
            self.draw(player)
            if self.shared_deck:
                self.shared_deck.append(self.shared_deck.pop(0))
            return True
        if skill == "shuffle_deck":
            self.rng.shuffle(self.shared_deck)
            return True
        if skill in {"look_and_reorder", "look_reorder_draw"}:
            top = sorted(self.shared_deck[:3], key=lambda c: c.cost, reverse=True)
            self.shared_deck[: len(top)] = top
            if skill == "look_reorder_draw":
                self.draw(player)
            return True

        mapping = {
            "return_enemy_runner_hand": ("runner", None, "return"),
            "return_enemy_equipment_hand": ("equipment", None, "return"),
            "destroy_enemy_runner_cost_4_or_less": ("runner", 4, "destroy"),
            "destroy_enemy_equipment": ("equipment", None, "destroy"),
            "destroy_enemy_any": (None, None, "destroy"),
        }
        if skill in mapping:
            card_type, max_cost, action = mapping[skill]
            target = self.best_target(player.pid, card_type, max_cost, forced_node)
            if not target:
                return False
            if action == "return":
                self.return_to_hand(*target)
            else:
                self.destroy_entity(*target)
            self.normalize_nodes()
            return True

        if skill == "return_own_runner_hand":
            targets = self.entities_of(player.pid, "runner")
            if not targets:
                return False
            self.return_to_hand(*max(targets, key=lambda ne: ne[1].card.cost))
            self.normalize_nodes()
            return True

        if skill == "return_enemy_runner_deck_bottom":
            target = self.best_target(player.pid, "runner", required_node=forced_node)
            if not target:
                return False
            node, entity = target
            self.remove_entity(node, entity)
            self.shared_deck.append(entity.card)
            self.normalize_nodes()
            return True

        if skill == "multi_return_enemy_runners_hand":
            targets = [(n, e) for n, e in self.enemy_entities(player.pid, "runner") if forced_node is None or n is forced_node]
            if not targets:
                return False
            for node, entity in sorted(targets, key=lambda ne: ne[1].atk, reverse=True)[:2]:
                self.return_to_hand(node, entity)
            self.normalize_nodes()
            return True
        return True

    def play_specific_card(self, player: PlayerState, card: CardDef, forced_node: Optional[NodeState] = None) -> bool:
        if card not in player.hand or card.cost > player.charge:
            return False
        if card.card_type in {"runner", "equipment"}:
            node = forced_node or self.best_deploy_node(player.pid)
            if not node:
                return False
            player.hand.remove(card)
            player.charge -= card.cost
            self.deploy(player, card, node)
            self.resolve_card_skill(player, card)
            self.stats.played[card.code] += 1
            return True
        player.hand.remove(card)
        player.charge -= card.cost
        success = self.resolve_card_skill(player, card, forced_node)
        self.shared_discard.append(card)
        if success:
            self.stats.played[card.code] += 1
        return success

    def play_card(self, player: PlayerState, reserve: int = 0) -> bool:
        playable = [card for card in player.hand if card.cost <= player.charge - reserve]
        if not playable:
            return False
        playable.sort(key=lambda c: (int(c.card_type in {"runner", "equipment"}), c.cost, c.atk or 0, c.hp or 0), reverse=True)
        for card in playable:
            if self.play_specific_card(player, card):
                return True
        return False

    def available_attackers(self, pid: int, preserve_control: bool = True) -> list[tuple[NodeState, Entity]]:
        result = []
        focus = self.choose_focus_domain(pid)
        for node in self.controlled_nodes(pid):
            eligible = [entity for entity in node.entities if entity.entered_turn < self.current_turn and not entity.attacked_this_turn and entity.atk > 0]
            if preserve_control and eligible and (len(node.entities) == 1 or node.definition.domain == focus):
                eligible = eligible[:-1]
            result.extend((node, entity) for entity in eligible)
        return result

    def capture_plan(self, target: NodeState, attackers: list[tuple[NodeState, Entity]], max_count: int) -> list[tuple[NodeState, Entity]]:
        ordered = sorted(attackers, key=lambda ne: (ne[1].atk, ne[1].hp), reverse=True)
        for count in range(1, min(max_count, len(ordered)) + 1):
            chosen = ordered[:count]
            defenders = [[e.atk, e.hp] for e in target.entities]
            living = [[e.atk, e.hp] for _, e in chosen]
            for attacker in living[:]:
                if not defenders:
                    break
                defender = max(defenders, key=lambda x: (x[0], x[1]))
                defender[1] -= attacker[0]
                attacker[1] -= defender[0]
                if defender[1] <= 0:
                    defenders.remove(defender)
                if attacker[1] <= 0 and attacker in living:
                    living.remove(attacker)
            if not defenders and living:
                return chosen
        return []

    def choose_attack(self, player: PlayerState, reserve: int = 0, required_node: Optional[NodeState] = None) -> Optional[tuple[NodeState, list[tuple[NodeState, Entity]]]]:
        targets = [node for node in self.controlled_nodes() if node.controller != player.pid and node.protected_by is None and node.entities]
        if required_node is not None:
            targets = [node for node in targets if node is required_node]
        attackers = self.available_attackers(player.pid, preserve_control=self.ai_mode == "tactical_v3")
        max_count = min(len(attackers), max(0, player.charge - reserve))
        if not targets or max_count <= 0:
            return None
        own = self.domain_count(player.pid)
        targets.sort(key=lambda node: (int(own[node.definition.domain] >= 2), int(self.domain_count(node.controller or -1)[node.definition.domain] >= 2), int(node.definition.domain == self.choose_focus_domain(player.pid)), -sum(entity.hp for entity in node.entities)), reverse=True)
        for target in targets:
            plan = self.capture_plan(target, attackers, max_count)
            if plan:
                return target, plan
        return None

    def attack(self, player: PlayerState, reserve: int = 0, required_node: Optional[NodeState] = None) -> bool:
        choice = self.choose_attack(player, reserve, required_node)
        if not choice:
            return False
        target, chosen = choice
        player.charge -= len(chosen)
        self.stats.attacks += 1
        previous_controller = target.controller
        sources = {entity.uid: node for node, entity in chosen}
        attackers = [entity for _, entity in chosen]
        for source, entity in chosen:
            self.remove_entity(source, entity)
            entity.attacked_this_turn = True
        survivors = attackers[:]
        for attacker in attackers:
            if not target.entities or attacker not in survivors:
                continue
            defender = max(target.entities, key=lambda entity: (entity.atk, entity.hp))
            if attacker.atk >= defender.hp:
                self.destroy_entity(target, defender)
            if defender.atk >= attacker.hp:
                survivors.remove(attacker)
                self.shared_discard.append(attacker.card)
        if not target.entities and survivors:
            target.zone = "controlled"
            target.controller = player.pid
            target.entities = survivors[:MAX_ENTITIES_PER_NODE]
            if previous_controller != player.pid:
                self.register_control_change()
            for entity in survivors[MAX_ENTITIES_PER_NODE:]:
                player.hand.append(entity.card)
        else:
            for entity in survivors:
                source = sources[entity.uid]
                if source.zone == "controlled" and source.controller == player.pid and len(source.entities) < 3:
                    source.entities.append(entity)
                else:
                    player.hand.append(entity.card)
        self.normalize_nodes()
        return True

    def use_node_skill(self, player: PlayerState, reserve: int = 0) -> bool:
        if player.used_node_skill_this_turn:
            return False
        nodes = [node for node in self.controlled_nodes(player.pid) if node.definition.skill not in REMOVED_NODE_SKILLS and node.definition.skill_fee <= player.charge - reserve]
        for node in sorted(nodes, key=lambda n: n.definition.skill_fee, reverse=True):
            if self.resolve_node_skill(player, node):
                player.charge -= node.definition.skill_fee
                player.used_node_skill_this_turn = True
                self.stats.node_skill_used[node.definition.code] += 1
                return True
        return False

    def resolve_node_skill(self, player: PlayerState, node: NodeState) -> bool:
        skill = node.definition.skill
        if skill in REMOVED_NODE_SKILLS:
            return False
        if skill == "view_node":
            return True
        if skill == "protect_node":
            targets = [n for n in self.controlled_nodes(player.pid) if n.protected_by is None]
            if not targets:
                return False
            targets[0].protected_by = player.pid
            return True
        if skill in {"move_runner", "move_equipment"}:
            card_type = "runner" if skill == "move_runner" else "equipment"
            sources = self.entities_of(player.pid, card_type)
            destinations = [n for n in self.controlled_nodes(player.pid) if len(n.entities) < 3]
            for destination in destinations:
                for source, entity in sources:
                    if destination is not source:
                        self.remove_entity(source, entity)
                        destination.entities.append(entity)
                        self.normalize_nodes()
                        return True
            return False
        if skill in {"buff_runner_atk", "buff_runner_hp", "buff_equipment_atk", "buff_equipment_hp"}:
            card_type = "runner" if "runner" in skill else "equipment"
            targets = [entity for _, entity in self.entities_of(player.pid, card_type)]
            if not targets:
                return False
            target = max(targets, key=lambda e: (e.atk, e.hp))
            target.temp_atk += int(skill.endswith("atk"))
            target.temp_hp += int(skill.endswith("hp"))
            return True
        if skill in {"recover_runner", "recover_equipment"}:
            card_type = "runner" if skill == "recover_runner" else "equipment"
            cards = [card for card in self.shared_discard if card.card_type == card_type]
            if not cards:
                return False
            card = max(cards, key=lambda c: c.cost)
            self.shared_discard.remove(card)
            player.hand.append(card)
            return True
        return False

    def immediate_root_deploy(self, player: PlayerState) -> bool:
        counts = self.domain_count(player.pid)
        nodes = [node for node in self.central_nodes() if counts[node.definition.domain] >= 2]
        cards = [card for card in player.hand if card.card_type in {"runner", "equipment"} and card.cost <= player.charge]
        if not nodes or not cards:
            return False
        card = min(cards, key=lambda c: (c.cost, -(c.atk or 0), -(c.hp or 0)))
        return self.play_specific_card(player, card, max(nodes, key=lambda n: self.deploy_score(player.pid, n)))

    def immediate_root_attack(self, player: PlayerState) -> bool:
        winning_nodes = [node for node in self.controlled_nodes() if node.controller != player.pid and self.domain_count(player.pid)[node.definition.domain] >= 2]
        for node in winning_nodes:
            if self.attack(player, required_node=node):
                return True
        return False

    def opponent_immediate_deploy_threat(self, opponent: int) -> list[tuple[str, NodeState]]:
        counts = self.domain_count(opponent)
        return [(node.definition.domain, node) for node in self.central_nodes() if counts[node.definition.domain] >= 2]

    def cheapest_block_card(self, player: PlayerState, node: NodeState) -> Optional[ThreatPlan]:
        if len(node.entities) != 1:
            return None
        entity = node.entities[0]
        candidates: list[ThreatPlan] = []
        for card in player.hand:
            if card.cost > player.charge:
                continue
            if entity.card.card_type == "runner":
                if card.skill == "return_enemy_runner_hand":
                    candidates.append(ThreatPlan(entity.owner, node.definition.domain, node, "return_runner", card.cost, card))
                elif card.skill == "destroy_enemy_runner_cost_4_or_less" and entity.card.cost <= 4:
                    candidates.append(ThreatPlan(entity.owner, node.definition.domain, node, "destroy_runner", card.cost, card))
            if entity.card.card_type == "equipment":
                if card.skill == "return_enemy_equipment_hand":
                    candidates.append(ThreatPlan(entity.owner, node.definition.domain, node, "return_equipment", card.cost, card))
                elif card.skill == "destroy_enemy_equipment":
                    candidates.append(ThreatPlan(entity.owner, node.definition.domain, node, "destroy_equipment", card.cost, card))
            if card.skill == "destroy_enemy_any":
                candidates.append(ThreatPlan(entity.owner, node.definition.domain, node, "destroy_any", card.cost, card))
        return min(candidates, key=lambda p: p.cost) if candidates else None

    def minimum_block_plan(self, player: PlayerState) -> Optional[ThreatPlan]:
        next_pid = self.next_player_after(player.pid)
        threats = self.opponent_immediate_deploy_threat(next_pid)
        plans: list[ThreatPlan] = []
        for domain, _ in threats:
            controlled = [node for node in self.controlled_nodes(next_pid) if node.definition.domain == domain and node.protected_by is None]
            for node in controlled:
                card_plan = self.cheapest_block_card(player, node)
                if card_plan:
                    plans.append(card_plan)
                choice = self.choose_attack(player, required_node=node)
                if choice:
                    plans.append(ThreatPlan(next_pid, domain, node, "attack", len(choice[1])))
        return min(plans, key=lambda plan: plan.cost) if plans else None

    def execute_block_plan(self, player: PlayerState, plan: ThreatPlan) -> bool:
        self.stats.block_attempts += 1
        before = self.domain_count(plan.opponent)[plan.domain]
        if plan.method == "attack":
            success = self.attack(player, required_node=plan.node)
        else:
            success = bool(plan.card and self.play_specific_card(player, plan.card, plan.node))
        after = self.domain_count(plan.opponent)[plan.domain]
        if success and after < before:
            self.stats.successful_blocks += 1
            return True
        return success

    def minimum_root_cost(self, player: PlayerState) -> int:
        counts = self.domain_count(player.pid)
        costs = [card.cost for card in player.hand if card.card_type in {"runner", "equipment"} and any(counts[node.definition.domain] >= 2 for node in self.central_nodes())]
        for node in self.controlled_nodes():
            if node.controller == player.pid or counts[node.definition.domain] < 2:
                continue
            choice = self.choose_attack(player, required_node=node)
            if choice:
                costs.append(len(choice[1]))
        return min(costs, default=0)

    def cleanup(self, player: PlayerState) -> None:
        for node in self.nodes:
            for entity in node.entities:
                if entity.owner == player.pid:
                    entity.temp_atk = 0
                    entity.temp_hp = 0
        while len(player.hand) > 7:
            card = min(player.hand, key=lambda c: (0 if c.card_type == "one_shot" else 1, c.cost))
            player.hand.remove(card)
            self.shared_discard.append(card)
        player.charge = 0

    def take_turn(self, pid: int, turn: int) -> Optional[int]:
        self.current_turn = turn
        player = self.players[pid]
        self.choose_focus_domain(pid)
        for node in self.nodes:
            if node.protected_by == pid:
                node.protected_by = None
        player.used_node_skill_this_turn = False
        for node in self.controlled_nodes(pid):
            for entity in node.entities:
                entity.attacked_this_turn = False
        player.charge = min(turn, 10)
        self.draw(player)

        for _ in range(50):
            winner = self.check_winner()
            if winner is not None:
                return winner

            if self.ai_mode == "tactical_v3":
                if self.immediate_root_deploy(player) or self.immediate_root_attack(player):
                    continue
                block_plan = self.minimum_block_plan(player)
                if block_plan and block_plan.cost <= player.charge:
                    self.execute_block_plan(player, block_plan)
                    continue
                reserve = self.minimum_root_cost(player)
                if reserve == 0 and block_plan:
                    reserve = block_plan.cost
            else:
                reserve = 0

            if self.use_node_skill(player, reserve):
                continue
            if self.attack(player, reserve):
                continue
            if self.play_card(player, reserve):
                continue
            break

        self.cleanup(player)
        return self.check_winner()

    def end_round(self) -> None:
        if self.control_changes_this_round == 0:
            self.stagnant_rounds += 1
        else:
            self.stagnant_rounds = 0
        self.control_changes_this_round = 0
        if self.stagnant_rounds >= STAGNATION_REFRESH_ROUNDS:
            self.refresh_oldest_central()
            self.stagnant_rounds = 0

    def tiebreak(self) -> Optional[int]:
        scores = []
        for pid in range(self.player_count):
            nodes = self.controlled_nodes(pid)
            scores.append((len(nodes), self.best_domain_count(pid), sum(e.atk for n in nodes for e in n.entities), pid))
        scores.sort(reverse=True)
        if len(scores) > 1 and scores[0][:-1] == scores[1][:-1]:
            return None
        return scores[0][-1]

    def run(self) -> GameStats:
        for turn in range(1, self.max_turns + 1):
            for pid in self.turn_order:
                winner = self.take_turn(pid, turn)
                if winner is not None:
                    self.stats.winner = winner
                    self.stats.turns = turn
                    self.stats.reason = "root_access"
                    self.stats.root_by_30 = turn <= OBSERVATION_TURN
                    return self.stats
            self.end_round()
        self.stats.winner = self.tiebreak()
        self.stats.turns = self.max_turns
        self.stats.reason = "tiebreak" if self.stats.winner is not None else "draw"
        return self.stats


def simulate(players: int, games: int, seed: int, max_turns: int, ai_mode: AIMode) -> list[GameStats]:
    return [Game(random.Random(seed + i), players, max_turns, ai_mode).run() for i in range(games)]


def report(results: list[GameStats]) -> None:
    wins = Counter(result.winner for result in results)
    reasons = Counter(result.reason for result in results)
    turns = [result.turns for result in results]
    root_turns = [result.turns for result in results if result.reason == "root_access"]
    print(f"players={results[0].player_count} ai_mode={results[0].ai_mode} games={len(results)}")
    for pid in range(results[0].player_count):
        print(f"seat_{pid}_win_rate={wins[pid] / len(results):.3f}")
    print(f"draw_rate={wins[None] / len(results):.3f}")
    print(f"root_access_rate={reasons['root_access'] / len(results):.3f}")
    print(f"root_access_by_30_rate={sum(r.root_by_30 for r in results) / len(results):.3f}")
    print(f"avg_turns={statistics.mean(turns):.2f}")
    print(f"median_turns={statistics.median(turns):.2f}")
    print(f"avg_root_turn={statistics.mean(root_turns):.2f}" if root_turns else "avg_root_turn=NA")
    print(f"avg_attacks={statistics.mean(r.attacks for r in results):.2f}")
    print(f"avg_control_changes={statistics.mean(r.control_changes for r in results):.2f}")
    print(f"avg_block_attempts={statistics.mean(r.block_attempts for r in results):.2f}")
    print(f"avg_successful_blocks={statistics.mean(r.successful_blocks for r in results):.2f}")
    print(f"avg_central_refreshes={statistics.mean(r.central_refreshes for r in results):.2f}")
    print(f"deck_empty_rate={sum(r.deck_empty_observed for r in results) / len(results):.3f}")
    print(f"avg_draw_fail_count={statistics.mean(r.draw_fail_count for r in results):.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=4, choices=[2, 3, 4])
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--ai-mode", choices=["baseline", "tactical_v3"], default="tactical_v3")
    args = parser.parse_args()
    report(simulate(args.players, args.games, args.seed, args.max_turns, args.ai_mode))


if __name__ == "__main__":
    main()
