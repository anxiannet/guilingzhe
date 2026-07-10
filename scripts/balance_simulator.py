#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
2–4 人整套卡牌平衡模拟器 v0.11

当前规则假设：
- 2 / 3 / 4 人共用一副 62 张测试手牌牌库。
- 所有玩家共用公共弃牌区。
- 节点牌库由 12 张测试节点牌组成。
- 开局翻开 3 张节点进入中央节点区。
- 部署到中央区无主节点后，获得节点控制权并补足中央区至 3 张。
- 已控制节点没有己方实体时，节点回流节点牌库。
- 旧节点状态机制已从当前规则和默认模拟中移除。
- N-002 / N-003 保留节点名称与系统域位置，但旧技能不进入有效节点技能池。
- 这是平衡压力测试器，不是最终规则引擎。

v0.11 调整重点：
- 不修改 CARDS / NODES 测试卡表。
- baseline 保留旧式贪心策略；tactical_v2 使用胜利导向策略。
- tactical_v2 加入：即时 Root Access 搜索、电荷预留、主攻域锁定。
- 默认最大轮数由 30 提高至 50，同时统计 30 轮内 Root Access。
- 连续 2 个整轮无节点控制权变化时，刷新中央区最早翻开的 1 座节点。
- 保留空库统计。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal, Optional
import argparse
import random
import statistics

CardType = Literal["runner", "one_shot", "equipment"]
DeckMode = Literal["shared", "separate"]
NodeZone = Literal["deck", "central", "controlled"]
AIMode = Literal["baseline", "tactical_v2"]

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
class GameStats:
    winner: Optional[int]
    turns: int
    reason: str
    player_count: int
    deck_mode: DeckMode
    ai_mode: AIMode
    played: Counter[str] = field(default_factory=Counter)
    drawn: Counter[str] = field(default_factory=Counter)
    node_skill_used: Counter[str] = field(default_factory=Counter)
    attacks: int = 0
    occupied: int = 0
    control_changes: int = 0
    central_refreshes: int = 0
    draw_fail_count: int = 0
    deck_empty_observed: bool = False
    first_empty_turn: Optional[int] = None
    root_by_30: bool = False


# 测试手牌牌组：保持原状，作为以后对照组。
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


# 测试节点牌组：保持原节点定义作为历史对照。
# N-002 / N-003 的旧技能已移出当前规则，因此默认模拟中不结算。
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

DEFAULT_COPIES: dict[str, int] = {card.code: 2 for card in CARDS}
DEFAULT_COPIES["R-001"] += 1
DEFAULT_COPIES["G-011"] += 1


class Game:
    def __init__(
        self,
        rng: random.Random,
        player_count: int,
        deck_mode: DeckMode = "shared",
        max_turns: int = 50,
        opening_hand: int = 5,
        ai_mode: AIMode = "tactical_v2",
    ):
        if deck_mode == "separate":
            deck_mode = "shared"
        self.rng = rng
        self.player_count = player_count
        self.deck_mode: DeckMode = deck_mode
        self.max_turns = max_turns
        self.ai_mode: AIMode = ai_mode
        self.current_turn = 0
        self.players = [PlayerState(pid) for pid in range(player_count)]
        self.turn_order = list(range(player_count))
        self.rng.shuffle(self.turn_order)
        self.shared_deck = self.make_deck()
        self.shared_discard: list[CardDef] = []
        self.nodes = [NodeState(node) for node in NODES]
        self.node_deck = self.nodes[:]
        self.stats = GameStats(None, 0, "", player_count, deck_mode, ai_mode)
        self.next_uid = 1
        self.control_changes_this_round = 0
        self.stagnant_rounds = 0
        self.rng.shuffle(self.node_deck)
        self.replenish_central()
        for player in self.players:
            self.draw(player, opening_hand)

    def make_deck(self) -> list[CardDef]:
        by_code = {card.code: card for card in CARDS}
        deck = [by_code[code] for code, count in DEFAULT_COPIES.items() for _ in range(count)]
        assert len(deck) == 62
        self.rng.shuffle(deck)
        return deck

    def mark_deck_empty(self) -> None:
        self.stats.deck_empty_observed = True
        if self.stats.first_empty_turn is None:
            self.stats.first_empty_turn = self.current_turn

    def draw(self, player: PlayerState, n: int = 1) -> None:
        for _ in range(n):
            if not self.shared_deck:
                self.stats.draw_fail_count += 1
                self.mark_deck_empty()
                return
            card = self.shared_deck.pop(0)
            player.hand.append(card)
            self.stats.drawn[card.code] += 1
            if not self.shared_deck:
                self.mark_deck_empty()

    def discard_card(self, card: CardDef) -> None:
        self.shared_discard.append(card)

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
            node.protected_by = None
            node.controller = None
            node.entities.clear()
            node.revealed_turn = self.current_turn

    def return_node_to_deck(self, node: NodeState, shuffle: bool = True) -> None:
        node.zone = "deck"
        node.protected_by = None
        node.controller = None
        node.entities.clear()
        node.revealed_turn = 0
        if node not in self.node_deck:
            self.node_deck.append(node)
        if shuffle:
            self.rng.shuffle(self.node_deck)

    def refresh_oldest_central(self) -> bool:
        central = self.central_nodes()
        if not central:
            return False
        oldest = min(central, key=lambda n: (n.revealed_turn, n.definition.code))
        self.return_node_to_deck(oldest, shuffle=False)
        self.replenish_central()
        self.stats.central_refreshes += 1
        return True

    def normalize_nodes(self) -> None:
        changed = False
        for node in self.nodes:
            if node.zone == "central":
                node.controller = None
                node.entities.clear()
                continue
            if node.zone != "controlled":
                continue
            if node.controller is None:
                self.return_node_to_deck(node)
                self.register_control_change()
                changed = True
                continue
            node.entities = [e for e in node.entities if e.owner == node.controller]
            if not node.entities:
                self.return_node_to_deck(node)
                self.register_control_change()
                changed = True
        if changed:
            self.replenish_central()

    def check_winner(self) -> Optional[int]:
        self.normalize_nodes()
        for pid in range(self.player_count):
            counts = self.domain_count(pid)
            for domain, count in counts.items():
                if count >= ROOT_ACCESS_NODE_COUNT:
                    domain_nodes = [n for n in self.nodes if n.definition.domain == domain]
                    if len(domain_nodes) >= ROOT_ACCESS_NODE_COUNT and all(
                        n.zone == "controlled" and n.controller == pid for n in domain_nodes
                    ):
                        return pid
        return None

    def choose_focus_domain(self, pid: int) -> str:
        player = self.players[pid]
        if player.focus_domain is not None:
            return player.focus_domain
        domains = sorted({node.definition.domain for node in self.nodes})
        own = self.domain_count(pid)
        central = Counter(node.definition.domain for node in self.central_nodes())
        enemy = Counter(node.definition.domain for node in self.controlled_nodes() if node.controller != pid)
        scored = []
        for domain in domains:
            score = own[domain] * 100 + central[domain] * 20 - enemy[domain] * 5
            scored.append((score, self.rng.random(), domain))
        player.focus_domain = max(scored)[2]
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
        nodes = [n for n in self.central_nodes() if len(n.entities) < MAX_ENTITIES_PER_NODE]
        nodes.extend(n for n in self.controlled_nodes(pid) if len(n.entities) < MAX_ENTITIES_PER_NODE)
        return nodes

    def score_deploy_node(self, pid: int, node: NodeState) -> tuple[int, int, int, int, int]:
        counts = self.domain_count(pid)
        focus = self.choose_focus_domain(pid)
        progress = counts[node.definition.domain]
        completes = int(node.zone == "central" and progress >= ROOT_ACCESS_NODE_COUNT - 1)
        focus_match = int(node.definition.domain == focus)
        new_control = int(node.zone == "central")
        reinforce = progress if node.zone == "controlled" else 0
        space = MAX_ENTITIES_PER_NODE - len(node.entities)
        return (completes, new_control, focus_match, progress + reinforce, space)

    def best_node_for_deploy(self, pid: int) -> Optional[NodeState]:
        candidates = self.legal_deploy_nodes(pid)
        if not candidates:
            return None
        if self.ai_mode == "baseline":
            counts = self.domain_count(pid)
            return max(candidates, key=lambda n: (counts[n.definition.domain], int(n.zone == "central"), -len(n.entities)))
        return max(candidates, key=lambda n: self.score_deploy_node(pid, n))

    def deploy_entity(self, player: PlayerState, card: CardDef, node: NodeState) -> None:
        entity = Entity(self.next_uid, card, player.pid, self.current_turn)
        self.next_uid += 1
        node.entities.append(entity)
        if node.zone == "central":
            node.zone = "controlled"
            node.controller = player.pid
            self.stats.occupied += 1
            self.register_control_change()
            self.replenish_central()

    def remove_entity(self, node: NodeState, entity: Entity) -> None:
        if entity in node.entities:
            node.entities.remove(entity)

    def handle_destroyed_entity(self, entity: Entity) -> None:
        if entity.card.skill == "passive_return_when_destroyed":
            self.players[entity.owner].hand.append(entity.card)
        elif entity.card.skill == "passive_return_deck_bottom_when_destroyed":
            self.shared_deck.append(entity.card)
        else:
            self.discard_card(entity.card)

    def destroy_entity(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        self.handle_destroyed_entity(entity)

    def return_entity_to_hand(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        self.players[entity.owner].hand.append(entity.card)

    def return_entity_to_deck_bottom(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        self.shared_deck.append(entity.card)

    def best_enemy_entity(
        self,
        pid: int,
        card_type: Optional[CardType],
        max_cost: Optional[int] = None,
    ) -> Optional[tuple[NodeState, Entity]]:
        targets = self.enemy_entities(pid, card_type)
        if max_cost is not None:
            targets = [(node, entity) for node, entity in targets if entity.card.cost <= max_cost]
        if not targets:
            return None
        focus = self.choose_focus_domain(pid)
        return max(
            targets,
            key=lambda ne: (
                int(ne[0].definition.domain == focus),
                self.domain_count(ne[1].owner)[ne[0].definition.domain],
                ne[1].card.cost,
                ne[1].atk,
                ne[1].hp,
            ),
        )

    def pick_and_return(self, pid: int, owner_pid: Optional[int], card_type: CardType) -> bool:
        targets = self.entities_of(owner_pid, card_type) if owner_pid is not None else self.enemy_entities(pid, card_type)
        if not targets:
            return False
        node, entity = max(targets, key=lambda ne: (ne[1].card.cost, ne[1].atk, ne[1].hp))
        self.return_entity_to_hand(node, entity)
        self.normalize_nodes()
        return True

    def pick_and_destroy(self, pid: int, card_type: Optional[CardType]) -> bool:
        target = self.best_enemy_entity(pid, card_type)
        if not target:
            return False
        node, entity = target
        self.destroy_entity(node, entity)
        self.normalize_nodes()
        return True

    def resolve_card_skill(self, player: PlayerState, card: CardDef) -> bool:
        skill = card.skill
        if skill in {"look_deck_top", "look_hand"}:
            return True
        if skill in {"draw", "look_hand_then_draw"}:
            self.draw(player, 1)
            return True
        if skill == "draw_then_bottom":
            self.draw(player, 1)
            if self.shared_deck:
                self.shared_deck.append(self.shared_deck.pop(0))
            return True
        if skill == "shuffle_deck":
            self.rng.shuffle(self.shared_deck)
            return True
        if skill in {"look_and_reorder", "look_reorder_draw"}:
            top = self.shared_deck[:3]
            self.shared_deck[:3] = sorted(top, key=lambda c: c.cost, reverse=True)
            if skill == "look_reorder_draw":
                self.draw(player, 1)
            return True
        if skill == "return_own_runner_hand":
            return self.pick_and_return(player.pid, player.pid, "runner")
        if skill == "return_own_equipment_hand":
            return self.pick_and_return(player.pid, player.pid, "equipment")
        if skill == "return_enemy_runner_hand":
            return self.pick_and_return(player.pid, None, "runner")
        if skill == "return_enemy_equipment_hand":
            return self.pick_and_return(player.pid, None, "equipment")
        if skill == "return_enemy_runner_deck_bottom":
            target = self.best_enemy_entity(player.pid, "runner")
            if not target:
                return False
            node, entity = target
            self.return_entity_to_deck_bottom(node, entity)
            self.normalize_nodes()
            return True
        if skill == "multi_return_enemy_runners_hand":
            targets = self.enemy_entities(player.pid, "runner")
            if not targets:
                return False
            if card.code == "G-005":
                unique_nodes: list[NodeState] = []
                for node, _ in targets:
                    if all(node is not seen for seen in unique_nodes):
                        unique_nodes.append(node)
                best_node = max(
                    unique_nodes,
                    key=lambda n: (
                        self.domain_count(n.controller if n.controller is not None else -1)[n.definition.domain],
                        sum(1 for node, _ in targets if node is n),
                    ),
                )
                for entity in [entity for node, entity in targets if node is best_node]:
                    self.return_entity_to_hand(best_node, entity)
            else:
                for node, entity in sorted(targets, key=lambda ne: ne[1].atk, reverse=True)[:2]:
                    self.return_entity_to_hand(node, entity)
            self.normalize_nodes()
            return True
        if skill == "destroy_enemy_runner_cost_4_or_less":
            target = self.best_enemy_entity(player.pid, "runner", max_cost=4)
            if not target:
                return False
            node, entity = target
            self.destroy_entity(node, entity)
            self.normalize_nodes()
            return True
        if skill == "destroy_enemy_equipment":
            return self.pick_and_destroy(player.pid, "equipment")
        if skill == "destroy_enemy_any":
            return self.pick_and_destroy(player.pid, None)
        return True

    def card_play_score(self, player: PlayerState, card: CardDef) -> tuple[int, int, int, int, int]:
        skill_value = {
            "destroy_enemy_any": 8,
            "multi_return_enemy_runners_hand": 7,
            "destroy_enemy_runner_cost_4_or_less": 6,
            "destroy_enemy_equipment": 6,
            "return_enemy_runner_hand": 5,
            "return_enemy_equipment_hand": 5,
            "return_enemy_runner_deck_bottom": 5,
            "draw": 4,
            "look_and_reorder": 3,
            "shuffle_deck": 2,
            "look_hand": 2,
            "look_deck_top": 1,
        }.get(card.skill, 0)
        deploy = (0, 0, 0, 0, 0)
        if card.card_type in {"runner", "equipment"}:
            node = self.best_node_for_deploy(player.pid)
            if node:
                deploy = self.score_deploy_node(player.pid, node)
        deployable_bonus = 30 if card.card_type in {"runner", "equipment"} and self.central_nodes() else 0
        return (
            deploy[0] * 200 + deploy[1] * 40 + deploy[2] * 20 + deployable_bonus + skill_value,
            card.cost,
            card.atk or 0,
            card.hp or 0,
            -len(player.hand),
        )

    def playable_entities(self, player: PlayerState) -> list[CardDef]:
        return [
            card
            for card in player.hand
            if card.card_type in {"runner", "equipment"} and card.cost <= player.charge
        ]

    def immediate_root_deploy(self, player: PlayerState) -> bool:
        counts = self.domain_count(player.pid)
        winning_nodes = [
            node
            for node in self.central_nodes()
            if counts[node.definition.domain] >= ROOT_ACCESS_NODE_COUNT - 1
        ]
        if not winning_nodes:
            return False
        cards = self.playable_entities(player)
        if not cards:
            return False
        focus = self.choose_focus_domain(player.pid)
        winning_nodes.sort(
            key=lambda n: (
                int(n.definition.domain == focus),
                counts[n.definition.domain],
                -len(n.entities),
            ),
            reverse=True,
        )
        card = min(cards, key=lambda c: (c.cost, -(c.atk or 0), -(c.hp or 0)))
        node = winning_nodes[0]
        player.hand.remove(card)
        player.charge -= card.cost
        self.stats.played[card.code] += 1
        self.deploy_entity(player, card, node)
        self.resolve_card_skill(player, card)
        self.normalize_nodes()
        return True

    def play_card(self, player: PlayerState) -> bool:
        playable = [card for card in player.hand if card.cost <= player.charge]
        if not playable:
            return False
        if self.ai_mode == "baseline":
            playable.sort(key=lambda c: (c.cost, c.atk or 0, c.hp or 0), reverse=True)
        else:
            playable.sort(key=lambda c: self.card_play_score(player, c), reverse=True)
        for card in playable:
            if card.card_type in {"runner", "equipment"}:
                node = self.best_node_for_deploy(player.pid)
                if not node:
                    continue
                player.hand.remove(card)
                player.charge -= card.cost
                self.stats.played[card.code] += 1
                self.deploy_entity(player, card, node)
                self.resolve_card_skill(player, card)
                self.normalize_nodes()
                return True
            player.hand.remove(card)
            player.charge -= card.cost
            success = self.resolve_card_skill(player, card)
            self.discard_card(card)
            if success:
                self.stats.played[card.code] += 1
                return True
            return False
        return False

    def legal_attack_targets(self, pid: int) -> list[NodeState]:
        return [
            node
            for node in self.controlled_nodes()
            if node.controller != pid and node.protected_by is None and node.entities
        ]

    def available_attackers(
        self,
        pid: int,
        preserve_control: bool = True,
    ) -> list[tuple[NodeState, Entity]]:
        attackers: list[tuple[NodeState, Entity]] = []
        focus = self.choose_focus_domain(pid)
        for node in self.controlled_nodes(pid):
            eligible = [
                entity
                for entity in node.entities
                if entity.owner == pid
                and not entity.attacked_this_turn
                and entity.entered_turn < self.current_turn
                and entity.atk > 0
            ]
            if preserve_control and eligible:
                reserve = 1 if node.definition.domain == focus or len(node.entities) == 1 else 0
                eligible = eligible[: max(0, len(eligible) - reserve)]
            attackers.extend((node, entity) for entity in eligible)
        return attackers

    def attack_target_score(self, pid: int, node: NodeState) -> tuple[int, int, int, int, int, int]:
        controller = node.controller
        own_counts = self.domain_count(pid)
        enemy_counts = self.domain_count(controller) if controller is not None else Counter()
        immediate_win = int(own_counts[node.definition.domain] >= ROOT_ACCESS_NODE_COUNT - 1)
        block_enemy = int(enemy_counts[node.definition.domain] >= ROOT_ACCESS_NODE_COUNT - 1)
        focus_match = int(node.definition.domain == self.choose_focus_domain(pid))
        defender_hp = sum(entity.hp for entity in node.entities)
        defender_count = len(node.entities)
        return (
            immediate_win,
            block_enemy,
            focus_match,
            own_counts[node.definition.domain],
            -defender_hp,
            -defender_count,
        )

    def capture_plan(
        self,
        target: NodeState,
        attackers: list[tuple[NodeState, Entity]],
        max_attackers: int,
    ) -> list[tuple[NodeState, Entity]]:
        """Return the smallest high-value attacker group expected to capture with a survivor."""
        ordered = sorted(attackers, key=lambda ne: (ne[1].atk, ne[1].hp), reverse=True)
        for count in range(1, min(max_attackers, len(ordered)) + 1):
            chosen = ordered[:count]
            defenders = [[e.atk, e.hp] for e in target.entities]
            living = [[e.atk, e.hp] for _, e in chosen]
            for attacker in living[:]:
                if not defenders or attacker not in living:
                    continue
                defender = max(defenders, key=lambda value: (value[0], value[1]))
                defender[1] -= attacker[0]
                attacker[1] -= defender[0]
                if defender[1] <= 0:
                    defenders.remove(defender)
                if attacker[1] <= 0:
                    living.remove(attacker)
            if not defenders and living:
                return chosen
        return []

    def choose_attack(
        self,
        pid: int,
        winning_only: bool = False,
    ) -> Optional[tuple[NodeState, list[tuple[NodeState, Entity]]]]:
        targets = self.legal_attack_targets(pid)
        attackers = self.available_attackers(pid, preserve_control=self.ai_mode == "tactical_v2")
        max_attackers = min(len(attackers), self.players[pid].charge // ATTACK_COST_PER_ENTITY)
        if not targets or max_attackers <= 0:
            return None
        if winning_only:
            counts = self.domain_count(pid)
            targets = [n for n in targets if counts[n.definition.domain] >= ROOT_ACCESS_NODE_COUNT - 1]
            if not targets:
                return None
        if self.ai_mode == "baseline":
            counts = self.domain_count(pid)
            targets.sort(
                key=lambda n: (counts[n.definition.domain], -sum(e.hp for e in n.entities), -len(n.entities)),
                reverse=True,
            )
            attackers.sort(key=lambda ne: (ne[1].atk, ne[1].hp), reverse=True)
            return targets[0], attackers[:max_attackers]

        targets.sort(key=lambda n: self.attack_target_score(pid, n), reverse=True)
        for target in targets:
            plan = self.capture_plan(target, attackers, max_attackers)
            if plan:
                return target, plan
        return None

    def attack_node(self, player: PlayerState, winning_only: bool = False) -> bool:
        choice = self.choose_attack(player.pid, winning_only=winning_only)
        if not choice:
            return False
        target, attackers_with_sources = choice
        if not attackers_with_sources:
            return False
        player.charge -= len(attackers_with_sources) * ATTACK_COST_PER_ENTITY
        self.stats.attacks += 1
        previous_controller = target.controller
        source_by_uid = {entity.uid: source for source, entity in attackers_with_sources}
        attackers = [entity for _, entity in attackers_with_sources]
        for source, entity in attackers_with_sources:
            self.remove_entity(source, entity)
            entity.attacked_this_turn = True

        damage_to_attackers: Counter[int] = Counter()
        damage_to_defenders: Counter[int] = Counter()
        surviving = attackers[:]
        for attacker in attackers:
            if attacker not in surviving or not target.entities:
                continue
            defender = max(target.entities, key=lambda e: (e.atk, e.hp))
            damage_to_defenders[defender.uid] += attacker.atk
            damage_to_attackers[attacker.uid] += defender.atk
            if damage_to_defenders[defender.uid] >= defender.hp:
                self.destroy_entity(target, defender)
            if damage_to_attackers[attacker.uid] >= attacker.hp and attacker in surviving:
                surviving.remove(attacker)
                self.handle_destroyed_entity(attacker)

        if not target.entities and surviving:
            chosen = sorted(surviving, key=lambda e: (e.atk, e.hp), reverse=True)[:MAX_ENTITIES_PER_NODE]
            overflow = [e for e in surviving if e not in chosen]
            target.zone = "controlled"
            target.controller = player.pid
            target.entities = chosen
            for entity in overflow:
                source = source_by_uid.get(entity.uid)
                if source and source.zone == "controlled" and source.controller == player.pid and len(source.entities) < MAX_ENTITIES_PER_NODE:
                    source.entities.append(entity)
                else:
                    player.hand.append(entity.card)
            self.stats.occupied += 1
            if previous_controller != player.pid:
                self.register_control_change()
        elif target.entities:
            for entity in surviving:
                source = source_by_uid.get(entity.uid)
                if source and source.zone == "controlled" and source.controller == player.pid and len(source.entities) < MAX_ENTITIES_PER_NODE:
                    source.entities.append(entity)
                else:
                    player.hand.append(entity.card)
        else:
            target.controller = None

        self.normalize_nodes()
        return True

    def immediate_root_attack(self, player: PlayerState) -> bool:
        return self.attack_node(player, winning_only=True)

    def can_move_entity(self, pid: int, card_type: CardType) -> bool:
        sources = self.entities_of(pid, card_type)
        destinations = [n for n in self.controlled_nodes(pid) if len(n.entities) < MAX_ENTITIES_PER_NODE]
        return any(dst is not src for src, _ in sources for dst in destinations)

    def score_node_skill(self, player: PlayerState, node: NodeState) -> int:
        skill = node.definition.skill
        pid = player.pid
        if skill in REMOVED_NODE_SKILLS:
            return -999
        if skill == "protect_node":
            own = self.controlled_nodes(pid)
            return 35 + 20 * self.best_domain_count(pid) if own else -999
        if skill == "recover_runner":
            return 50 if any(c.card_type == "runner" for c in self.shared_discard) else -999
        if skill == "recover_equipment":
            return 45 if any(c.card_type == "equipment" for c in self.shared_discard) else -999
        if skill == "buff_runner_atk":
            return 35 if self.entities_of(pid, "runner") and self.legal_attack_targets(pid) else -999
        if skill == "buff_equipment_atk":
            return 32 if self.entities_of(pid, "equipment") and self.legal_attack_targets(pid) else -999
        if skill == "move_runner":
            return 28 if self.can_move_entity(pid, "runner") else -999
        if skill == "move_equipment":
            return 26 if self.can_move_entity(pid, "equipment") else -999
        if skill == "buff_runner_hp":
            return 20 if self.entities_of(pid, "runner") else -999
        if skill == "buff_equipment_hp":
            return 18 if self.entities_of(pid, "equipment") else -999
        if skill == "view_node":
            return 8
        return -999

    def use_node_skill(self, player: PlayerState, reserved_charge: int = 0) -> bool:
        if player.used_node_skill_this_turn:
            return False
        nodes = [
            node
            for node in self.controlled_nodes(player.pid)
            if node.definition.skill not in REMOVED_NODE_SKILLS
            and node.definition.skill_fee <= player.charge - reserved_charge
        ]
        if not nodes:
            return False

        if self.ai_mode == "baseline":
            priority = {
                "protect_node": 80,
                "recover_runner": 70,
                "recover_equipment": 65,
                "buff_runner_atk": 60,
                "buff_equipment_atk": 55,
                "move_runner": 45,
                "move_equipment": 40,
                "buff_runner_hp": 30,
                "buff_equipment_hp": 25,
                "view_node": 10,
            }
            nodes.sort(key=lambda n: (priority.get(n.definition.skill, 0), -n.definition.skill_fee), reverse=True)
        else:
            nodes.sort(key=lambda n: (self.score_node_skill(player, n), -n.definition.skill_fee), reverse=True)

        for node in nodes:
            if self.score_node_skill(player, node) < 0:
                continue
            if self.resolve_node_skill(player, node):
                player.charge -= node.definition.skill_fee
                player.used_node_skill_this_turn = True
                self.stats.node_skill_used[node.definition.code] += 1
                self.normalize_nodes()
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
            focus = self.choose_focus_domain(player.pid)
            targets.sort(
                key=lambda n: (
                    int(n.definition.domain == focus),
                    self.domain_count(player.pid)[n.definition.domain],
                    len(n.entities),
                ),
                reverse=True,
            )
            targets[0].protected_by = player.pid
            return True
        if skill in {"move_runner", "move_equipment"}:
            card_type: CardType = "runner" if skill == "move_runner" else "equipment"
            sources = self.entities_of(player.pid, card_type)
            destinations = [n for n in self.controlled_nodes(player.pid) if len(n.entities) < MAX_ENTITIES_PER_NODE]
            destinations.sort(key=lambda n: self.score_deploy_node(player.pid, n), reverse=True)
            for dst in destinations:
                for src, entity in sources:
                    if dst is not src:
                        self.remove_entity(src, entity)
                        dst.entities.append(entity)
                        self.normalize_nodes()
                        return True
            return False
        if skill == "buff_runner_atk":
            return self.buff_entity(player.pid, "runner", atk=1)
        if skill == "buff_runner_hp":
            return self.buff_entity(player.pid, "runner", hp=1)
        if skill == "buff_equipment_atk":
            return self.buff_entity(player.pid, "equipment", atk=1)
        if skill == "buff_equipment_hp":
            return self.buff_entity(player.pid, "equipment", hp=1)
        if skill == "recover_runner":
            return self.recover_from_discard(player, "runner")
        if skill == "recover_equipment":
            return self.recover_from_discard(player, "equipment")
        return False

    def buff_entity(self, pid: int, card_type: CardType, atk: int = 0, hp: int = 0) -> bool:
        targets = [entity for _, entity in self.entities_of(pid, card_type)]
        if not targets:
            return False
        target = max(targets, key=lambda e: (e.atk, e.hp))
        target.temp_atk += atk
        target.temp_hp += hp
        return True

    def recover_from_discard(self, player: PlayerState, card_type: CardType) -> bool:
        candidates = [card for card in self.shared_discard if card.card_type == card_type]
        if not candidates:
            return False
        card = max(candidates, key=lambda c: c.cost)
        self.shared_discard.remove(card)
        player.hand.append(card)
        return True

    def minimum_root_action_cost(self, player: PlayerState) -> int:
        counts = self.domain_count(player.pid)
        winning_central = [n for n in self.central_nodes() if counts[n.definition.domain] >= ROOT_ACCESS_NODE_COUNT - 1]
        entity_costs = [c.cost for c in player.hand if c.card_type in {"runner", "equipment"}]
        costs: list[int] = []
        if winning_central and entity_costs:
            costs.append(min(entity_costs))
        winning_targets = [
            n for n in self.legal_attack_targets(player.pid)
            if counts[n.definition.domain] >= ROOT_ACCESS_NODE_COUNT - 1
        ]
        if winning_targets:
            attackers = self.available_attackers(player.pid)
            if attackers:
                need = min(sum(e.hp for e in n.entities) for n in winning_targets)
                total = 0
                used = 0
                for _, entity in sorted(attackers, key=lambda ne: ne[1].atk, reverse=True):
                    total += entity.atk
                    used += 1
                    if total >= need:
                        costs.append(used * ATTACK_COST_PER_ENTITY)
                        break
        return min(costs, default=0)

    def cleanup_turn(self, player: PlayerState) -> None:
        for node in self.nodes:
            for entity in node.entities:
                if entity.owner == player.pid:
                    entity.temp_atk = 0
                    entity.temp_hp = 0
        while len(player.hand) > 7:
            card = min(
                player.hand,
                key=lambda c: (
                    0 if c.card_type == "one_shot" else 1,
                    c.cost,
                    c.atk or 0,
                    c.hp or 0,
                ),
            )
            player.hand.remove(card)
            self.discard_card(card)
        player.charge = 0

    def take_turn(self, pid: int, turn_number: int) -> Optional[int]:
        self.current_turn = turn_number
        player = self.players[pid]
        self.choose_focus_domain(pid)

        for node in self.nodes:
            if node.protected_by == pid:
                node.protected_by = None
        player.used_node_skill_this_turn = False
        for node in self.controlled_nodes(pid):
            for entity in node.entities:
                entity.attacked_this_turn = False

        player.charge = min(turn_number, 10)
        self.draw(player, 1)

        for _ in range(50):
            winner = self.check_winner()
            if winner is not None:
                return winner

            if self.ai_mode == "tactical_v2":
                if self.immediate_root_deploy(player):
                    continue
                if self.immediate_root_attack(player):
                    continue

            reserve = self.minimum_root_action_cost(player) if self.ai_mode == "tactical_v2" else 0

            if not player.used_node_skill_this_turn and self.use_node_skill(player, reserved_charge=reserve):
                continue

            if self.ai_mode == "tactical_v2" and self.best_domain_count(pid) >= ROOT_ACCESS_NODE_COUNT - 1:
                if self.play_card(player):
                    continue
                if self.attack_node(player):
                    continue
            else:
                if self.attack_node(player):
                    continue
                if self.play_card(player):
                    continue
            break

        self.cleanup_turn(player)
        return self.check_winner()

    def end_round(self) -> None:
        if self.control_changes_this_round == 0:
            self.stagnant_rounds += 1
        else:
            self.stagnant_rounds = 0
        self.control_changes_this_round = 0

        if self.stagnant_rounds >= STAGNATION_REFRESH_ROUNDS:
            self.refresh_oldest_central()
            for player in self.players:
                focus = player.focus_domain
                if focus is not None:
                    has_focus = self.domain_count(player.pid)[focus] > 0
                    visible_focus = any(n.definition.domain == focus for n in self.central_nodes())
                    if not has_focus and not visible_focus:
                        player.focus_domain = None
            self.stagnant_rounds = 0

    def tiebreak_winner(self) -> Optional[int]:
        scores: list[tuple[int, int, int, int, int]] = []
        for pid in range(self.player_count):
            controlled = self.controlled_nodes(pid)
            node_count = len(controlled)
            atk_sum = sum(e.atk for n in controlled for e in n.entities if e.owner == pid)
            domain_best = self.best_domain_count(pid)
            entity_count = sum(1 for n in controlled for e in n.entities if e.owner == pid)
            scores.append((node_count, domain_best, atk_sum, entity_count, pid))
        scores.sort(reverse=True)
        if len(scores) > 1 and scores[0][:4] == scores[1][:4]:
            return None
        return scores[0][4]

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

        self.stats.winner = self.tiebreak_winner()
        self.stats.turns = self.max_turns
        self.stats.reason = "tiebreak" if self.stats.winner is not None else "draw"
        return self.stats


def simulate(
    players: int,
    games: int,
    seed: int,
    max_turns: int,
    deck_mode: DeckMode,
    ai_mode: AIMode,
) -> list[GameStats]:
    results: list[GameStats] = []
    for index in range(games):
        game = Game(
            random.Random(seed + index),
            players,
            deck_mode=deck_mode,
            max_turns=max_turns,
            ai_mode=ai_mode,
        )
        results.append(game.run())
    return results


def mean_optional(values: list[Optional[int]]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return statistics.mean(present) if present else None


def report(results: list[GameStats]) -> None:
    if not results:
        return
    wins = Counter(result.winner for result in results)
    reasons = Counter(result.reason for result in results)
    turns = [result.turns for result in results]
    root_turns = [result.turns for result in results if result.reason == "root_access"]
    played: Counter[str] = Counter()
    node_skills: Counter[str] = Counter()
    for result in results:
        played.update(result.played)
        node_skills.update(result.node_skill_used)

    deck_empty_games = sum(result.deck_empty_observed for result in results)
    first_empty_avg = mean_optional([result.first_empty_turn for result in results])
    root_by_30 = sum(result.root_by_30 for result in results)

    print(
        f"players={results[0].player_count} deck_mode={results[0].deck_mode} "
        f"ai_mode={results[0].ai_mode} games={len(results)}"
    )
    print("legacy_node_state_enabled=false")
    for pid in range(results[0].player_count):
        print(f"seat_{pid}_win_rate={wins[pid] / len(results):.3f}")
    print(f"draw_rate={wins[None] / len(results):.3f}")
    print(f"root_access_rate={reasons['root_access'] / len(results):.3f}")
    print(f"root_access_by_30_rate={root_by_30 / len(results):.3f}")
    print(f"avg_turns={statistics.mean(turns):.2f}")
    print(f"median_turns={statistics.median(turns):.2f}")
    print(f"avg_root_turn={statistics.mean(root_turns):.2f}" if root_turns else "avg_root_turn=NA")
    print("reasons=" + ", ".join(f"{key}:{value}" for key, value in sorted(reasons.items())))
    print(f"avg_attacks={statistics.mean(result.attacks for result in results):.2f}")
    print(f"avg_control_changes={statistics.mean(result.control_changes for result in results):.2f}")
    print(f"avg_central_refreshes={statistics.mean(result.central_refreshes for result in results):.2f}")
    print(f"deck_empty_rate={deck_empty_games / len(results):.3f}")
    print(f"avg_first_empty_turn={first_empty_avg:.2f}" if first_empty_avg is not None else "avg_first_empty_turn=NA")
    print(f"avg_draw_fail_count={statistics.mean(result.draw_fail_count for result in results):.2f}")
    print("top_played=" + ", ".join(f"{code}:{count}" for code, count in played.most_common(12)))
    print("top_node_skills=" + ", ".join(f"{code}:{count}" for code, count in node_skills.most_common(12)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=4, choices=[2, 3, 4])
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--deck-mode", choices=["shared", "separate"], default="shared")
    parser.add_argument("--ai-mode", choices=["baseline", "tactical_v2"], default="tactical_v2")
    args = parser.parse_args()

    if args.deck_mode == "separate":
        print("warning: separate deck mode is not current standard; running shared mode instead.")

    report(
        simulate(
            args.players,
            args.games,
            args.seed,
            args.max_turns,
            args.deck_mode,
            args.ai_mode,
        )
    )


if __name__ == "__main__":
    main()
