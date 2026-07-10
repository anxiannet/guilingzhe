#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
2–4 人整套卡牌平衡模拟器 v0.10

当前规则假设：
- 2 / 3 / 4 人默认 shared：所有玩家共用一副 62 张标准手牌牌库。
- 所有玩家共用 1 个公共弃牌区。
- 节点牌库由 12 张测试节点牌组成。
- 开局翻开 3 张节点进入中央节点区。
- 部署到中央节点区无主节点后，获得节点控制权，并补充中央节点区至 3 张。
- 已控制节点没有己方实体时，节点变为无主，回流节点牌库并重洗。
- 可部署实体入场技能不得回手己方场上实体。
- 这是平衡压力测试器，不是最终规则引擎。

v0.10 调整重点：
- 不改测试牌组；当前 CARDS / NODES 继续作为以后对照组。
- 新增 ai_mode：baseline 保留旧优先级，tactical 使用局势评分。
- 新增 --disable-offline-node，用于对照有/无节点离线。
- 新增空库统计：首次空库轮数、空库局数、抽牌失败次数。
- 统一战斗离场结算入口，方便后续区分“被消灭 / 被销毁”。
- Root Access 只检查拥有至少 3 座节点的系统域，避免未来单节点候选域误判胜利。
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
AIMode = Literal["baseline", "tactical"]

MAX_ENTITIES_PER_NODE = 3
ATTACK_COST_PER_ENTITY = 1
CENTRAL_NODE_TARGET = 3
ROOT_ACCESS_NODE_COUNT = 3


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
    online: bool = True
    protected_by: Optional[int] = None
    controller: Optional[int] = None
    entities: list[Entity] = field(default_factory=list)


@dataclass
class PlayerState:
    pid: int
    hand: list[CardDef] = field(default_factory=list)
    charge: int = 0
    used_node_skill_this_turn: bool = False


@dataclass
class GameStats:
    winner: Optional[int]
    turns: int
    reason: str
    player_count: int
    deck_mode: DeckMode
    ai_mode: AIMode
    offline_node_enabled: bool
    played: Counter[str] = field(default_factory=Counter)
    drawn: Counter[str] = field(default_factory=Counter)
    node_skill_used: Counter[str] = field(default_factory=Counter)
    attacks: int = 0
    occupied: int = 0
    draw_fail_count: int = 0
    deck_empty_observed: bool = False
    first_empty_turn: Optional[int] = None


# 重要：本列表保持测试牌组原状，不在 AI 机制调整中改卡表。
CARDS: list[CardDef] = [
    CardDef("R-001", "侦察员", "runner", 1, 1, 1, "look_deck_top"),
    CardDef("R-002", "信使", "runner", 1, 1, 1, "draw"),
    CardDef("R-003", "预读者", "runner", 2, 1, 2, "look_and_reorder"),
    CardDef("R-004", "扒手", "runner", 1, 1, 1, "look_hand"),
    CardDef("R-005", "广播员", "runner", 2, 1, 2, "draw"),
    CardDef("R-007", "烟幕手", "runner", 2, 1, 2, "return_enemy_runner_hand"),
    CardDef("R-008", "洗牌客", "runner", 1, 1, 1, "shuffle_deck"),
    CardDef("R-009", "清除者", "runner", 4, 3, 2, "destroy_enemy_runner"),
    CardDef("R-010", "甩棍", "runner", 2, 2, 1, "return_enemy_runner_hand"),
    CardDef("R-013", "不倒翁", "runner", 3, 1, 3, "passive_return_when_destroyed"),
    CardDef("R-014", "筛选员", "runner", 3, 1, 2, "look_and_reorder"),
    CardDef("R-015", "扳手", "runner", 2, 1, 2, "return_enemy_equipment_hand"),
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
    CardDef("G-007", "回收地堡", "equipment", 4, 1, 5, "look_and_reorder"),
    CardDef("G-008", "牵引锚", "one_shot", 2, None, None, "return_enemy_equipment_hand"),
    CardDef("G-009", "探针", "one_shot", 1, None, None, "look_deck_top"),
    CardDef("G-010", "算盘", "one_shot", 2, None, None, "look_and_reorder"),
    CardDef("G-011", "数据抽取器", "one_shot", 1, None, None, "draw"),
    CardDef("G-012", "弹弓", "one_shot", 2, None, None, "return_enemy_runner_hand"),
]


# 重要：本列表保持测试节点原状，不在 AI 机制调整中改卡表。
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
    def __init__(
        self,
        rng: random.Random,
        player_count: int,
        deck_mode: DeckMode = "shared",
        max_turns: int = 30,
        opening_hand: int = 5,
        ai_mode: AIMode = "tactical",
        disable_offline_node: bool = False,
    ):
        if deck_mode == "separate":
            deck_mode = "shared"
        self.rng = rng
        self.player_count = player_count
        self.deck_mode: DeckMode = deck_mode
        self.max_turns = max_turns
        self.ai_mode: AIMode = ai_mode
        self.disable_offline_node = disable_offline_node
        self.current_turn = 0
        self.players = [PlayerState(pid) for pid in range(player_count)]
        self.shared_deck = self.make_deck()
        self.shared_discard: list[CardDef] = []
        self.nodes = [NodeState(node) for node in NODES]
        self.node_deck = self.nodes[:]
        self.stats = GameStats(
            None,
            0,
            "",
            player_count,
            deck_mode,
            ai_mode,
            not disable_offline_node,
        )
        self.next_uid = 1
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

    def online_domain_count(self, pid: int) -> Counter[str]:
        return Counter(n.definition.domain for n in self.controlled_nodes(pid) if n.online)

    def best_domain_count(self, pid: int) -> int:
        counts = self.online_domain_count(pid)
        return max(counts.values(), default=0)

    def replenish_central(self) -> None:
        while len(self.central_nodes()) < CENTRAL_NODE_TARGET and self.node_deck:
            node = self.node_deck.pop(0)
            node.zone = "central"
            node.online = True
            node.protected_by = None
            node.controller = None
            node.entities.clear()

    def return_node_to_deck(self, node: NodeState) -> None:
        node.zone = "deck"
        node.online = True
        node.protected_by = None
        node.controller = None
        node.entities.clear()
        if node not in self.node_deck:
            self.node_deck.append(node)
        self.rng.shuffle(self.node_deck)

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
                changed = True
                continue
            node.entities = [e for e in node.entities if e.owner == node.controller]
            if not node.entities:
                self.return_node_to_deck(node)
                changed = True
        if changed:
            self.replenish_central()

    def check_winner(self) -> Optional[int]:
        self.normalize_nodes()
        domains = sorted({node.definition.domain for node in self.nodes})
        for pid in range(self.player_count):
            for domain in domains:
                domain_nodes = [node for node in self.nodes if node.definition.domain == domain]
                if len(domain_nodes) < ROOT_ACCESS_NODE_COUNT:
                    continue
                if all(node.zone == "controlled" and node.online and node.controller == pid for node in domain_nodes):
                    return pid
        return None

    def entities_of(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        return [
            (node, e)
            for node in self.controlled_nodes(pid)
            for e in node.entities
            if e.owner == pid and (card_type is None or e.card.card_type == card_type)
        ]

    def enemy_entities(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        return [
            (node, e)
            for node in self.controlled_nodes()
            for e in node.entities
            if e.owner != pid and (card_type is None or e.card.card_type == card_type)
        ]

    def legal_deploy_nodes(self, pid: int) -> list[NodeState]:
        nodes = [n for n in self.central_nodes() if len(n.entities) < MAX_ENTITIES_PER_NODE]
        nodes.extend(n for n in self.controlled_nodes(pid) if len(n.entities) < MAX_ENTITIES_PER_NODE)
        return nodes

    def score_deploy_node(self, pid: int, node: NodeState) -> tuple[int, int, int, int]:
        counts = self.online_domain_count(pid)
        domain_progress = counts[node.definition.domain]
        completes_domain = 1 if node.zone == "central" and domain_progress >= ROOT_ACCESS_NODE_COUNT - 1 else 0
        advances_domain = 1 if node.zone == "central" else 0
        reinforces_good_domain = domain_progress if node.zone == "controlled" else 0
        space_pressure = MAX_ENTITIES_PER_NODE - len(node.entities)
        return (completes_domain, domain_progress + advances_domain, reinforces_good_domain, space_pressure)

    def best_node_for_deploy(self, pid: int) -> Optional[NodeState]:
        candidates = self.legal_deploy_nodes(pid)
        if not candidates:
            return None
        if self.ai_mode == "baseline":
            domain_count = Counter(n.definition.domain for n in self.controlled_nodes(pid) if n.online)
            candidates.sort(
                key=lambda n: (domain_count[n.definition.domain], 1 if n.zone == "central" else 0, -len(n.entities)),
                reverse=True,
            )
            return candidates[0]
        return max(candidates, key=lambda n: self.score_deploy_node(pid, n))

    def deploy_entity(self, player: PlayerState, card: CardDef, node: NodeState) -> None:
        entity = Entity(self.next_uid, card, player.pid, self.current_turn)
        self.next_uid += 1
        node.entities.append(entity)
        if node.zone == "central":
            node.zone = "controlled"
            node.controller = player.pid
            self.stats.occupied += 1
            self.replenish_central()

    def remove_entity(self, node: NodeState, entity: Entity) -> None:
        if entity in node.entities:
            node.entities.remove(entity)

    def handle_destroyed_entity(self, entity: Entity) -> None:
        # 规则术语：游侠为“被消灭”，装备为“被销毁”。此处统一处理离场去向。
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

    def best_enemy_entity(self, pid: int, card_type: Optional[CardType]) -> Optional[tuple[NodeState, Entity]]:
        targets = self.enemy_entities(pid, card_type)
        if not targets:
            return None
        return max(targets, key=lambda ne: (ne[1].card.cost, ne[1].atk, ne[1].hp))

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
        if skill == "draw":
            self.draw(player, 1)
            return True
        if skill == "shuffle_deck":
            self.rng.shuffle(self.shared_deck)
            return True
        if skill == "look_and_reorder":
            top = self.shared_deck[:3]
            self.shared_deck[:3] = sorted(top, key=lambda c: c.cost, reverse=True)
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
                best_node = max(unique_nodes, key=lambda n: sum(1 for node, _ in targets if node is n))
                for entity in [entity for node, entity in targets if node is best_node]:
                    self.return_entity_to_hand(best_node, entity)
            else:
                for node, entity in sorted(targets, key=lambda ne: ne[1].atk, reverse=True)[:2]:
                    self.return_entity_to_hand(node, entity)
            self.normalize_nodes()
            return True
        if skill == "destroy_enemy_runner":
            return self.pick_and_destroy(player.pid, "runner")
        if skill == "destroy_enemy_equipment":
            return self.pick_and_destroy(player.pid, "equipment")
        if skill == "destroy_enemy_any":
            return self.pick_and_destroy(player.pid, None)
        return True

    def card_play_score(self, player: PlayerState, card: CardDef) -> tuple[int, int, int, int]:
        skill_value = {
            "destroy_enemy_any": 8,
            "multi_return_enemy_runners_hand": 7,
            "destroy_enemy_runner": 6,
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
        deploy_bonus = 0
        if card.card_type in {"runner", "equipment"}:
            node = self.best_node_for_deploy(player.pid)
            if node:
                deploy_bonus = 5 * self.score_deploy_node(player.pid, node)[0] + 2 * self.score_deploy_node(player.pid, node)[1]
        return (deploy_bonus + skill_value, card.cost, card.atk or 0, card.hp or 0)

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
        return [n for n in self.controlled_nodes() if n.controller != pid and n.protected_by is None and n.entities]

    def available_attackers(self, pid: int) -> list[tuple[NodeState, Entity]]:
        return [
            (node, e)
            for node in self.controlled_nodes(pid)
            for e in node.entities
            if e.owner == pid and not e.attacked_this_turn and e.entered_turn < self.current_turn and e.atk > 0
        ]

    def attack_target_score(self, pid: int, node: NodeState) -> tuple[int, int, int, int, int]:
        controller = node.controller
        enemy_threat = 0
        if controller is not None:
            enemy_counts = self.online_domain_count(controller)
            enemy_threat = enemy_counts[node.definition.domain]
        own_counts = self.online_domain_count(pid)
        own_progress = own_counts[node.definition.domain]
        defender_hp = sum(e.hp for e in node.entities)
        defender_count = len(node.entities)
        online = 1 if node.online else 0
        return (enemy_threat, own_progress, online, -defender_hp, -defender_count)

    def choose_attack(self, pid: int) -> Optional[tuple[NodeState, list[tuple[NodeState, Entity]]]]:
        targets = self.legal_attack_targets(pid)
        attackers = self.available_attackers(pid)
        max_attackers = min(len(attackers), self.players[pid].charge // ATTACK_COST_PER_ENTITY)
        if not targets or max_attackers <= 0:
            return None
        if self.ai_mode == "baseline":
            domain_count = Counter(n.definition.domain for n in self.controlled_nodes(pid) if n.online)
            targets.sort(
                key=lambda n: (domain_count[n.definition.domain], -sum(e.hp for e in n.entities), -len(n.entities)),
                reverse=True,
            )
        else:
            targets.sort(key=lambda n: self.attack_target_score(pid, n), reverse=True)
        attackers.sort(key=lambda ne: (ne[1].atk, ne[1].hp), reverse=True)
        return targets[0], attackers[:max_attackers]

    def attack_node(self, player: PlayerState) -> bool:
        choice = self.choose_attack(player.pid)
        if not choice:
            return False
        target, attackers_with_sources = choice
        player.charge -= len(attackers_with_sources) * ATTACK_COST_PER_ENTITY
        self.stats.attacks += 1
        source_by_uid = {entity.uid: source for source, entity in attackers_with_sources}
        attackers = [entity for _, entity in attackers_with_sources]
        for source, entity in attackers_with_sources:
            self.remove_entity(source, entity)
            entity.attacked_this_turn = True

        damage_to_attackers: Counter[int] = Counter()
        damage_to_defenders: Counter[int] = Counter()
        surviving_attackers = attackers[:]
        for attacker in attackers:
            if attacker not in surviving_attackers or not target.entities:
                continue
            defender = max(target.entities, key=lambda e: (e.atk, e.hp))
            damage_to_defenders[defender.uid] += attacker.atk
            damage_to_attackers[attacker.uid] += defender.atk
            if damage_to_defenders[defender.uid] >= defender.hp:
                self.destroy_entity(target, defender)
            if damage_to_attackers[attacker.uid] >= attacker.hp and attacker in surviving_attackers:
                surviving_attackers.remove(attacker)
                self.handle_destroyed_entity(attacker)

        if not target.entities and surviving_attackers:
            chosen = sorted(surviving_attackers, key=lambda e: (e.atk, e.hp), reverse=True)[:MAX_ENTITIES_PER_NODE]
            overflow = [e for e in surviving_attackers if e not in chosen]
            target.entities.clear()
            target.zone = "controlled"
            target.controller = player.pid
            target.entities.extend(chosen)
            for entity in overflow:
                source = source_by_uid.get(entity.uid)
                if source and source.zone == "controlled" and source.controller == player.pid and len(source.entities) < MAX_ENTITIES_PER_NODE:
                    source.entities.append(entity)
                else:
                    player.hand.append(entity.card)
            self.stats.occupied += 1
        elif target.entities:
            for entity in surviving_attackers:
                source = source_by_uid.get(entity.uid)
                if source and source.zone == "controlled" and source.controller == player.pid and len(source.entities) < MAX_ENTITIES_PER_NODE:
                    source.entities.append(entity)
                else:
                    player.hand.append(entity.card)
        else:
            target.controller = None

        self.normalize_nodes()
        return True

    def score_node_skill(self, player: PlayerState, node: NodeState) -> int:
        skill = node.definition.skill
        pid = player.pid
        if skill == "offline_node":
            if self.disable_offline_node:
                return -999
            targets = [n for n in self.controlled_nodes() if n.controller != pid and n.online and n.protected_by is None]
            if not targets:
                return -999
            best = max(targets, key=lambda n: self.online_domain_count(n.controller or -1)[n.definition.domain])
            enemy_progress = self.online_domain_count(best.controller or -1)[best.definition.domain]
            return 35 + 25 * enemy_progress
        if skill == "protect_node":
            own = self.controlled_nodes(pid)
            if not own:
                return -999
            best_progress = self.best_domain_count(pid)
            return 35 + 20 * best_progress
        if skill == "online_node":
            own_offline = [n for n in self.controlled_nodes(pid) if not n.online]
            any_offline = [n for n in self.controlled_nodes() if not n.online]
            if own_offline:
                return 70 + 20 * self.best_domain_count(pid)
            if any_offline:
                return 20
            return -999
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
        return 0

    def can_move_entity(self, pid: int, card_type: CardType) -> bool:
        sources = self.entities_of(pid, card_type)
        destinations = [n for n in self.controlled_nodes(pid) if len(n.entities) < MAX_ENTITIES_PER_NODE]
        return any(dst is not src for src, _ in sources for dst in destinations)

    def use_node_skill(self, player: PlayerState) -> bool:
        if player.used_node_skill_this_turn:
            return False
        nodes = [n for n in self.controlled_nodes(player.pid) if n.online and n.definition.skill_fee <= player.charge]
        if not nodes:
            return False

        if self.ai_mode == "baseline":
            priority = {
                "offline_node": 90,
                "protect_node": 80,
                "recover_runner": 70,
                "recover_equipment": 65,
                "buff_runner_atk": 60,
                "buff_equipment_atk": 55,
                "move_runner": 45,
                "move_equipment": 40,
                "online_node": 35,
                "buff_runner_hp": 30,
                "buff_equipment_hp": 25,
                "view_node": 10,
            }
            nodes.sort(key=lambda n: (priority.get(n.definition.skill, 0), -n.definition.skill_fee), reverse=True)
        else:
            nodes.sort(key=lambda n: (self.score_node_skill(player, n), -n.definition.skill_fee), reverse=True)

        for node in nodes:
            if self.ai_mode == "tactical" and self.score_node_skill(player, node) < 0:
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
        if skill == "view_node":
            return True
        if skill == "online_node":
            targets = [n for n in self.controlled_nodes() if not n.online]
            if not targets:
                return False
            targets.sort(
                key=lambda n: (
                    1 if n.controller == player.pid else 0,
                    self.online_domain_count(n.controller or player.pid)[n.definition.domain],
                ),
                reverse=True,
            )
            targets[0].online = True
            return True
        if skill == "offline_node":
            if self.disable_offline_node:
                return False
            targets = [n for n in self.controlled_nodes() if n.controller != player.pid and n.online and n.protected_by is None]
            if not targets:
                return False
            if self.ai_mode == "baseline":
                targets[0].online = False
                return True
            target = max(targets, key=lambda n: self.attack_target_score(player.pid, n))
            target.online = False
            return True
        if skill == "protect_node":
            targets = [n for n in self.controlled_nodes(player.pid) if n.protected_by is None]
            if not targets:
                return False
            targets.sort(key=lambda n: self.score_deploy_node(player.pid, n), reverse=True)
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

    def cleanup_turn(self, player: PlayerState) -> None:
        for node in self.nodes:
            for entity in node.entities:
                if entity.owner == player.pid:
                    entity.temp_atk = 0
                    entity.temp_hp = 0
        while len(player.hand) > 7:
            card = min(player.hand, key=lambda c: c.cost)
            player.hand.remove(card)
            self.discard_card(card)
        player.charge = 0

    def should_play_before_attack(self, player: PlayerState) -> bool:
        if self.ai_mode == "baseline":
            return False
        if not any(card.cost <= player.charge for card in player.hand):
            return False
        if not self.available_attackers(player.pid):
            return True
        if self.best_domain_count(player.pid) >= ROOT_ACCESS_NODE_COUNT - 1:
            return True
        return False

    def take_turn(self, pid: int, turn_number: int) -> Optional[int]:
        self.current_turn = turn_number
        player = self.players[pid]
        for node in self.nodes:
            if node.protected_by == pid:
                node.protected_by = None
        player.used_node_skill_this_turn = False
        for node in self.controlled_nodes(pid):
            for entity in node.entities:
                entity.attacked_this_turn = False
        player.charge = min(turn_number, 10)
        self.draw(player, 1)
        self.use_node_skill(player)
        for _ in range(40):
            winner = self.check_winner()
            if winner is not None:
                return winner
            if self.should_play_before_attack(player) and self.play_card(player):
                continue
            if self.attack_node(player):
                continue
            if self.play_card(player):
                continue
            break
        self.cleanup_turn(player)
        return self.check_winner()

    def tiebreak_winner(self) -> Optional[int]:
        scores: list[tuple[int, int, int, int, int]] = []
        for pid in range(self.player_count):
            online_nodes = [n for n in self.controlled_nodes(pid) if n.online]
            online_count = len(online_nodes)
            atk_sum = sum(e.atk for n in online_nodes for e in n.entities if e.owner == pid)
            domain_best = 0
            for domain in {n.definition.domain for n in self.nodes}:
                domain_best = max(domain_best, sum(1 for n in online_nodes if n.definition.domain == domain))
            entity_count = sum(1 for n in self.controlled_nodes(pid) for e in n.entities if e.owner == pid)
            scores.append((online_count, atk_sum, domain_best, entity_count, pid))
        scores.sort(reverse=True)
        if len(scores) > 1 and scores[0][:4] == scores[1][:4]:
            return None
        return scores[0][4]

    def run(self) -> GameStats:
        for turn in range(1, self.max_turns + 1):
            for pid in range(self.player_count):
                winner = self.take_turn(pid, turn)
                if winner is not None:
                    self.stats.winner = winner
                    self.stats.turns = turn
                    self.stats.reason = "root_access"
                    return self.stats
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
    disable_offline_node: bool,
) -> list[GameStats]:
    results: list[GameStats] = []
    for index in range(games):
        rng = random.Random(seed + index)
        game = Game(
            rng,
            players,
            deck_mode=deck_mode,
            max_turns=max_turns,
            ai_mode=ai_mode,
            disable_offline_node=disable_offline_node,
        )
        results.append(game.run())
    return results


def mean_optional(values: list[Optional[int]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return statistics.mean(present)


def report(results: list[GameStats]) -> None:
    if not results:
        return
    wins = Counter(result.winner for result in results)
    turns = [result.turns for result in results]
    reasons = Counter(result.reason for result in results)
    played = Counter()
    node_skills = Counter()
    attacks = []
    occupied = []
    draw_fails = []
    first_empty_turns: list[Optional[int]] = []
    player_count = results[0].player_count
    deck_mode = results[0].deck_mode
    ai_mode = results[0].ai_mode
    offline_node_enabled = results[0].offline_node_enabled
    for result in results:
        played.update(result.played)
        node_skills.update(result.node_skill_used)
        attacks.append(result.attacks)
        occupied.append(result.occupied)
        draw_fails.append(result.draw_fail_count)
        first_empty_turns.append(result.first_empty_turn)
    deck_empty_games = sum(1 for result in results if result.deck_empty_observed)
    first_empty_avg = mean_optional(first_empty_turns)

    print(f"players={player_count} deck_mode={deck_mode} ai_mode={ai_mode} games={len(results)}")
    print(f"offline_node_enabled={str(offline_node_enabled).lower()}")
    for pid in range(player_count):
        print(f"seat_{pid}_win_rate={wins[pid] / len(results):.3f}")
    print(f"draw_rate={wins[None] / len(results):.3f}")
    print(f"avg_turns={statistics.mean(turns):.2f}")
    print(f"median_turns={statistics.median(turns):.2f}")
    print("reasons=" + ", ".join(f"{k}:{v}" for k, v in sorted(reasons.items())))
    print(f"avg_attacks={statistics.mean(attacks):.2f}")
    print(f"avg_control_changes={statistics.mean(occupied):.2f}")
    print(f"deck_empty_rate={deck_empty_games / len(results):.3f}")
    print(f"avg_first_empty_turn={first_empty_avg:.2f}" if first_empty_avg is not None else "avg_first_empty_turn=NA")
    print(f"avg_draw_fail_count={statistics.mean(draw_fails):.2f}")
    print("top_played=" + ", ".join(f"{code}:{count}" for code, count in played.most_common(12)))
    print("top_node_skills=" + ", ".join(f"{code}:{count}" for code, count in node_skills.most_common(12)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=4, choices=[2, 3, 4])
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--deck-mode", choices=["shared", "separate"], default="shared")
    parser.add_argument("--ai-mode", choices=["baseline", "tactical"], default="tactical")
    parser.add_argument("--disable-offline-node", action="store_true")
    args = parser.parse_args()
    if args.deck_mode == "separate":
        print("warning: separate deck mode is not current standard; running shared mode instead.")
    results = simulate(
        args.players,
        args.games,
        args.seed,
        args.max_turns,
        args.deck_mode,
        args.ai_mode,
        args.disable_offline_node,
    )
    report(results)


if __name__ == "__main__":
    main()
