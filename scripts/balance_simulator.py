#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
2–4 人整套卡牌平衡模拟器 v0.9

运行：
    python scripts/balance_simulator.py --players 2 --games 1000 --seed 7
    python scripts/balance_simulator.py --players 3 --games 1000 --seed 7
    python scripts/balance_simulator.py --players 4 --games 1000 --seed 7

当前规则假设：
- 2 / 3 / 4 人默认 shared：所有玩家共用一副 62 张标准手牌牌库。
- 所有玩家共用 1 个公共弃牌区。
- 节点牌库由 12 张标准节点牌组成。
- 开局翻开 3 张节点进入中央节点区。
- 中央节点区节点初始为在线、无主、无实体。
- 部署到中央节点区无主节点后，获得节点控制权，并补充中央节点区至 3 张。
- 已控制节点没有己方实体时，节点变为无主，回流节点牌库并重洗。
- 每名玩家每回合最多使用 1 次节点技能。
- 每回合不限制打出手牌张数，只限制权限电荷资源。
- 每座节点最多 3 个实体。
- 刚上场的游侠 / 装备本回合不能参与攻击节点。
- 攻击目标必须是其他玩家控制的节点，不能是无主节点。
- 攻击节点由自己控制节点中的己方游侠 / 装备发起；每派出 1 个实体支付 1 电荷。
- 攻击成功后，存活攻方进入目标节点，攻击方获得节点控制权。
- 不再使用“节点上 ATK 总和最高者自动获得控制权”的旧算法。

重要说明：这是平衡压力测试器，不是最终规则引擎。
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
MAX_ENTITIES_PER_NODE = 3
ATTACK_COST_PER_ENTITY = 1
CENTRAL_NODE_TARGET = 3


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
    deck: list[CardDef] = field(default_factory=list)
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
    deck_mode: DeckMode
    played: Counter[str] = field(default_factory=Counter)
    drawn: Counter[str] = field(default_factory=Counter)
    node_skill_used: Counter[str] = field(default_factory=Counter)
    attacks: int = 0
    occupied: int = 0


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
    def __init__(self, rng: random.Random, player_count: int, deck_mode: DeckMode = "shared", max_turns: int = 30, opening_hand: int = 5):
        self.rng = rng
        self.player_count = player_count
        self.deck_mode = deck_mode
        self.max_turns = max_turns
        self.current_turn = 0
        self.players = [PlayerState(pid) for pid in range(player_count)]
        self.shared_deck: list[CardDef] = []
        self.shared_discard: list[CardDef] = []
        self.node_deck: list[NodeState] = []
        self.nodes = [NodeState(node) for node in NODES]
        self.stats = GameStats(None, 0, "", player_count, deck_mode)
        self.next_uid = 1
        self.setup_decks()
        self.setup_nodes()
        for player in self.players:
            self.draw(player, opening_hand)

    def make_deck(self) -> list[CardDef]:
        by_code = {card.code: card for card in CARDS}
        deck = [by_code[code] for code, count in DEFAULT_COPIES.items() for _ in range(count)]
        assert len(deck) == 62
        self.rng.shuffle(deck)
        return deck

    def setup_decks(self) -> None:
        if self.deck_mode == "shared":
            self.shared_deck = self.make_deck()
        else:
            for player in self.players:
                player.deck = self.make_deck()

    def setup_nodes(self) -> None:
        self.node_deck = self.nodes[:]
        self.rng.shuffle(self.node_deck)
        self.replenish_central()

    def active_deck(self, player: PlayerState) -> list[CardDef]:
        return self.shared_deck if self.deck_mode == "shared" else player.deck

    def active_discard(self, player: PlayerState) -> list[CardDef]:
        return self.shared_discard if self.deck_mode == "shared" else player.discard

    def draw(self, player: PlayerState, n: int = 1) -> None:
        deck = self.active_deck(player)
        for _ in range(n):
            if not deck:
                return
            card = deck.pop(0)
            player.hand.append(card)
            self.stats.drawn[card.code] += 1

    def discard_card(self, owner_pid: int, card: CardDef) -> None:
        self.active_discard(self.players[owner_pid]).append(card)

    def return_card_to_deck_bottom(self, owner_pid: int, card: CardDef) -> None:
        self.active_deck(self.players[owner_pid]).append(card)

    def central_nodes(self) -> list[NodeState]:
        return [node for node in self.nodes if node.zone == "central"]

    def controlled_nodes(self, pid: Optional[int] = None) -> list[NodeState]:
        nodes = [node for node in self.nodes if node.zone == "controlled" and node.controller is not None]
        return nodes if pid is None else [node for node in nodes if node.controller == pid]

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
        for node in list(self.nodes):
            owners = {entity.owner for entity in node.entities}
            if node.zone == "central":
                node.controller = None
                if node.entities:
                    node.entities.clear()
                continue
            if node.zone != "controlled":
                continue
            if node.controller is None:
                if node.entities:
                    node.entities.clear()
                self.return_node_to_deck(node)
                changed = True
                continue
            node.entities = [entity for entity in node.entities if entity.owner == node.controller]
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
                if all(node.zone == "controlled" and node.online and node.controller == pid for node in domain_nodes):
                    return pid
        return None

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
        nodes = [node for node in self.central_nodes() if len(node.entities) < MAX_ENTITIES_PER_NODE]
        nodes += [node for node in self.controlled_nodes(pid) if len(node.entities) < MAX_ENTITIES_PER_NODE]
        return nodes

    def best_node_for_deploy(self, pid: int) -> Optional[NodeState]:
        candidates = self.legal_deploy_nodes(pid)
        if not candidates:
            return None
        domain_count = Counter(node.definition.domain for node in self.controlled_nodes(pid) if node.online)
        candidates.sort(
            key=lambda node: (
                domain_count[node.definition.domain],
                1 if node.zone == "central" else 0,
                -len(node.entities),
            ),
            reverse=True,
        )
        return candidates[0]

    def deploy_entity(self, player: PlayerState, card: CardDef, node: NodeState) -> Entity:
        entity = Entity(self.next_uid, card, player.pid, self.current_turn)
        self.next_uid += 1
        node.entities.append(entity)
        if node.zone == "central":
            node.zone = "controlled"
            node.controller = player.pid
            self.stats.occupied += 1
            self.replenish_central()
        return entity

    def remove_entity(self, node: NodeState, entity: Entity) -> None:
        if entity in node.entities:
            node.entities.remove(entity)

    def destroy_entity(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        if entity.card.skill == "passive_return_when_destroyed":
            self.players[entity.owner].hand.append(entity.card)
        else:
            self.discard_card(entity.owner, entity.card)

    def return_entity_to_hand(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        self.players[entity.owner].hand.append(entity.card)

    def return_entity_to_deck_bottom(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        self.return_card_to_deck_bottom(entity.owner, entity.card)

    def resolve_card_skill(self, player: PlayerState, card: CardDef) -> bool:
        skill = card.skill
        if skill in {"look_deck_top", "look_hand"}:
            return True
        if skill == "draw":
            self.draw(player, 1)
            return True
        if skill == "shuffle_deck":
            self.rng.shuffle(self.active_deck(player))
            return True
        if skill == "look_and_reorder":
            deck = self.active_deck(player)
            top = deck[:3]
            deck[:3] = sorted(top, key=lambda c: c.cost, reverse=True)
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
            targets = [(node, e) for node, e in self.enemy_entities(player.pid, "runner")]
            if not targets:
                return False
            # G-005 当前按1座节点中的所有对手游侠；R-016 近似为最多2个对手游侠。
            if card.code == "G-005":
                best_node = max({node for node, _ in targets}, key=lambda n: sum(1 for _, e in targets if _ is n))
                for entity in [e for n, e in targets if n is best_node]:
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

    def best_enemy_entity(self, pid: int, card_type: Optional[CardType]) -> Optional[tuple[NodeState, Entity]]:
        targets = self.enemy_entities(pid, card_type)
        if not targets:
            return None
        return max(targets, key=lambda ne: (ne[1].cost if hasattr(ne[1], "cost") else ne[1].card.cost, ne[1].atk, ne[1].hp))

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

    def play_card(self, player: PlayerState) -> bool:
        playable = [card for card in player.hand if card.cost <= player.charge]
        if not playable:
            return False
        playable.sort(key=lambda c: (c.cost, c.atk or 0, c.hp or 0), reverse=True)
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
            if card.card_type == "one_shot":
                # 信息 / 抽牌类可以直接打；目标类先尝试结算。
                player.hand.remove(card)
                player.charge -= card.cost
                success = self.resolve_card_skill(player, card)
                self.discard_card(player.pid, card)
                if success:
                    self.stats.played[card.code] += 1
                    return True
                return False
        return False

    def legal_attack_targets(self, pid: int) -> list[NodeState]:
        return [
            node for node in self.controlled_nodes()
            if node.controller != pid and node.protected_by is None and node.entities
        ]

    def available_attackers(self, pid: int) -> list[tuple[NodeState, Entity]]:
        return [
            (node, entity)
            for node in self.controlled_nodes(pid)
            for entity in node.entities
            if entity.owner == pid
            and not entity.attacked_this_turn
            and entity.entered_turn < self.current_turn
            and entity.atk > 0
        ]

    def choose_attack(self, pid: int) -> Optional[tuple[NodeState, list[tuple[NodeState, Entity]]]]:
        targets = self.legal_attack_targets(pid)
        attackers = self.available_attackers(pid)
        if not targets or not attackers:
            return None
        max_attackers = min(len(attackers), self.players[pid].charge // ATTACK_COST_PER_ENTITY)
        if max_attackers <= 0:
            return None
        domain_count = Counter(node.definition.domain for node in self.controlled_nodes(pid) if node.online)
        targets.sort(key=lambda n: (domain_count[n.definition.domain], -sum(e.hp for e in n.entities), -len(n.entities)), reverse=True)
        attackers.sort(key=lambda ne: (ne[1].atk, ne[1].hp), reverse=True)
        return targets[0], attackers[:max_attackers]

    def attack_node(self, player: PlayerState) -> bool:
        choice = self.choose_attack(player.pid)
        if not choice:
            return False
        target, attackers_with_sources = choice
        cost = len(attackers_with_sources) * ATTACK_COST_PER_ENTITY
        if player.charge < cost:
            return False
        player.charge -= cost
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
            if damage_to_attackers[attacker.uid] >= attacker.hp:
                if attacker in surviving_attackers:
                    surviving_attackers.remove(attacker)
                self.discard_card(attacker.owner, attacker.card)
        if not target.entities and surviving_attackers:
            chosen = sorted(surviving_attackers, key=lambda e: (e.atk, e.hp), reverse=True)[:MAX_ENTITIES_PER_NODE]
            overflow = [e for e in surviving_attackers if e not in chosen]
            target.entities.clear()
            target.zone = "controlled"
            target.controller = player.pid
            for entity in chosen:
                target.entities.append(entity)
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

    def use_node_skill(self, player: PlayerState) -> bool:
        if player.used_node_skill_this_turn:
            return False
        nodes = [node for node in self.controlled_nodes(player.pid) if node.online and node.definition.skill_fee <= player.charge]
        if not nodes:
            return False
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
        for node in nodes:
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
            targets[0].online = True
            return True
        if skill == "offline_node":
            targets = [n for n in self.controlled_nodes() if n.controller != player.pid and n.online and n.protected_by is None]
            if not targets:
                return False
            targets[0].online = False
            return True
        if skill == "protect_node":
            targets = [n for n in self.controlled_nodes(player.pid) if n.protected_by is None]
            if not targets:
                return False
            targets[0].protected_by = player.pid
            return True
        if skill in {"move_runner", "move_equipment"}:
            card_type: CardType = "runner" if skill == "move_runner" else "equipment"
            sources = self.entities_of(player.pid, card_type)
            destinations = [n for n in self.controlled_nodes(player.pid) if len(n.entities) < MAX_ENTITIES_PER_NODE]
            for src, entity in sources:
                for dst in destinations:
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
        discard = self.active_discard(player)
        candidates = [card for card in discard if card.card_type == card_type]
        if not candidates:
            return False
        card = max(candidates, key=lambda c: c.cost)
        discard.remove(card)
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
            self.discard_card(player.pid, card)
        player.charge = 0

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
            acted = False
            if self.attack_node(player):
                acted = True
            elif self.play_card(player):
                acted = True
            if not acted:
                break
        self.cleanup_turn(player)
        return self.check_winner()

    def tiebreak_winner(self) -> Optional[int]:
        scores: list[tuple[int, int, int, int, int]] = []
        for pid in range(self.player_count):
            online_nodes = [n for n in self.controlled_nodes(pid) if n.online]
            online_count = len(online_nodes)
            atk_sum = sum(entity.atk for n in online_nodes for entity in n.entities if entity.owner == pid)
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


def simulate(players: int, games: int, seed: int, max_turns: int, deck_mode: DeckMode) -> list[GameStats]:
    results: list[GameStats] = []
    for index in range(games):
        rng = random.Random(seed + index)
        game = Game(rng, players, deck_mode=deck_mode, max_turns=max_turns)
        results.append(game.run())
    return results


def report(results: list[GameStats]) -> None:
    if not results:
        return
    player_count = results[0].player_count
    deck_mode = results[0].deck_mode
    wins = Counter(result.winner for result in results)
    turns = [result.turns for result in results]
    reasons = Counter(result.reason for result in results)
    played = Counter()
    node_skills = Counter()
    attacks = []
    occupied = []
    for result in results:
        played.update(result.played)
        node_skills.update(result.node_skill_used)
        attacks.append(result.attacks)
        occupied.append(result.occupied)
    print(f"players={player_count} deck_mode={deck_mode} games={len(results)}")
    for pid in range(player_count):
        print(f"seat_{pid}_win_rate={wins[pid] / len(results):.3f}")
    print(f"draw_rate={wins[None] / len(results):.3f}")
    print(f"avg_turns={statistics.mean(turns):.2f}")
    print(f"median_turns={statistics.median(turns):.2f}")
    print("reasons=" + ", ".join(f"{k}:{v}" for k, v in sorted(reasons.items())))
    print(f"avg_attacks={statistics.mean(attacks):.2f}")
    print(f"avg_control_changes={statistics.mean(occupied):.2f}")
    print("top_played=" + ", ".join(f"{code}:{count}" for code, count in played.most_common(12)))
    print("top_node_skills=" + ", ".join(f"{code}:{count}" for code, count in node_skills.most_common(12)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=4, choices=[2, 3, 4])
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--deck-mode", choices=["shared", "separate"], default="shared")
    args = parser.parse_args()
    if args.deck_mode == "separate":
        print("warning: separate deck mode is an experimental future variant; current standard rule uses shared mode.")
    results = simulate(args.players, args.games, args.seed, args.max_turns, args.deck_mode)
    report(results)


if __name__ == "__main__":
    main()
