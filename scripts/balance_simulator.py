#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
2–4 人整套卡牌平衡模拟器 v0.8

运行：
    python scripts/balance_simulator.py --players 2 --games 1000 --seed 7
    python scripts/balance_simulator.py --players 3 --games 1000 --seed 7
    python scripts/balance_simulator.py --players 4 --games 1000 --seed 7

当前规则假设：
- 2 人默认 separate；3 / 4 人默认 shared。
- 每名玩家每回合最多使用 1 次节点技能。
- 每回合不限制打出手牌张数，只限制权限电荷资源。
- 每座节点最多 3 个实体。
- 节点只属于一个玩家，或处于无主状态。
- 一个节点上不能同时存在多个玩家的实体。
- 无主节点上不能存在任何实体。
- 打出游侠 / 装备只能部署到无主节点或己方控制节点；部署到无主节点时获得节点控制权。
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
MAX_ENTITIES_PER_NODE = 3
ATTACK_COST_PER_ENTITY = 1


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
    def __init__(self, rng: random.Random, player_count: int, deck_mode: DeckMode, max_turns: int = 30, opening_hand: int = 5):
        self.rng = rng
        self.player_count = player_count
        self.deck_mode = deck_mode
        self.max_turns = max_turns
        self.current_turn = 0
        self.nodes = [NodeState(node) for node in NODES]
        self.players = [PlayerState(pid) for pid in range(player_count)]
        self.shared_deck: list[CardDef] = []
        self.shared_discard: list[CardDef] = []
        self.stats = GameStats(None, 0, "", player_count, deck_mode)
        self.next_uid = 1
        self.setup_decks()
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

    def entities_of(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        return [(node, entity) for node in self.nodes for entity in node.entities if entity.owner == pid and (card_type is None or entity.card.card_type == card_type)]

    def enemy_entities(self, pid: int, card_type: Optional[CardType] = None) -> list[tuple[NodeState, Entity]]:
        return [(node, entity) for node in self.nodes for entity in node.entities if entity.owner != pid and (card_type is None or entity.card.card_type == card_type)]

    def node_power(self, node: NodeState, pid: int) -> int:
        return sum(e.atk for e in node.entities if e.owner == pid)

    def check_node_invariant(self, node: NodeState) -> None:
        owners = {e.owner for e in node.entities}
        if node.controller is None:
            if node.entities:
                node.entities.clear()
            return
        node.entities = [e for e in node.entities if e.owner == node.controller]
        if not node.entities:
            node.controller = None

    def normalize_nodes(self) -> None:
        for node in self.nodes:
            self.check_node_invariant(node)

    def check_winner(self) -> Optional[int]:
        self.normalize_nodes()
        domains = sorted({node.definition.domain for node in self.nodes})
        for pid in range(self.player_count):
            for domain in domains:
                domain_nodes = [node for node in self.nodes if node.definition.domain == domain]
                if all(node.online and node.controller == pid for node in domain_nodes):
                    return pid
        return None

    def legal_deploy_nodes(self, pid: int) -> list[NodeState]:
        return [n for n in self.nodes if (n.controller is None or n.controller == pid) and len(n.entities) < MAX_ENTITIES_PER_NODE]

    def best_node_for_deploy(self, pid: int) -> Optional[NodeState]:
        candidates = self.legal_deploy_nodes(pid)
        if not candidates:
            return None
        domains = sorted({n.definition.domain for n in self.nodes})
        for domain in domains:
            if sum(1 for n in self.nodes if n.definition.domain == domain and n.controller == pid and n.online) == 2:
                domain_candidates = [n for n in candidates if n.definition.domain == domain and n.online]
                if domain_candidates:
                    return domain_candidates[0]
        unowned = [n for n in candidates if n.controller is None]
        return unowned[0] if unowned else min(candidates, key=lambda n: len(n.entities))

    def deploy_entity(self, pid: int, card: CardDef) -> Optional[int]:
        node = self.best_node_for_deploy(pid)
        if node is None:
            return None
        if node.controller is None:
            node.controller = pid
            self.stats.occupied += 1
        node.entities.append(Entity(self.next_uid, card, pid, entered_turn=self.current_turn))
        self.next_uid += 1
        return self.check_winner()

    def remove_entity(self, node: NodeState, entity: Entity, destination: str) -> None:
        if entity not in node.entities:
            return
        node.entities.remove(entity)
        entity.temp_atk = 0
        entity.temp_hp = 0
        card = entity.card
        if destination == "hand":
            self.players[entity.owner].hand.append(card)
        elif destination == "deck_bottom":
            self.return_card_to_deck_bottom(entity.owner, card)
        elif destination == "discard":
            if card.skill == "passive_return_when_destroyed":
                self.players[entity.owner].hand.append(card)
            else:
                self.discard_card(entity.owner, card)
        else:
            raise ValueError(destination)
        if node.controller == entity.owner and not node.entities:
            node.controller = None

    def choose_enemy_target(self, pid: int, card_type: Optional[CardType]) -> Optional[tuple[NodeState, Entity]]:
        targets = self.enemy_entities(pid, card_type)
        return max(targets, key=lambda item: (item[1].atk, item[1].card.cost, item[1].hp)) if targets else None

    def choose_own_target(self, pid: int, card_type: CardType) -> Optional[tuple[NodeState, Entity]]:
        targets = self.entities_of(pid, card_type)
        return max(targets, key=lambda item: (item[1].card.cost, item[1].hp)) if targets else None

    def resolve_skill(self, pid: int, card: CardDef) -> bool:
        player = self.players[pid]
        skill = card.skill
        if skill == "draw":
            self.draw(player, 1); return True
        if skill == "look_deck_top":
            return True
        if skill == "look_and_reorder":
            deck = self.active_deck(player)
            top = deck[:3]
            top.sort(key=lambda c: c.cost, reverse=True)
            deck[:len(top)] = top
            return True
        if skill == "shuffle_deck":
            self.rng.shuffle(self.active_deck(player)); return True
        if skill == "look_hand":
            return True
        if skill == "return_enemy_runner_hand": return self.apply_remove_target(pid, "runner", "hand")
        if skill == "return_enemy_equipment_hand": return self.apply_remove_target(pid, "equipment", "hand")
        if skill == "return_enemy_runner_deck_bottom": return self.apply_remove_target(pid, "runner", "deck_bottom")
        if skill == "destroy_enemy_runner": return self.apply_remove_target(pid, "runner", "discard")
        if skill == "destroy_enemy_equipment": return self.apply_remove_target(pid, "equipment", "discard")
        if skill == "destroy_enemy_any": return self.apply_remove_target(pid, None, "discard")
        if skill == "multi_return_enemy_runners_hand": return self.apply_multi_return(pid, card)
        if skill == "return_own_runner_hand": return self.apply_remove_own(pid, "runner")
        if skill == "return_own_equipment_hand": return self.apply_remove_own(pid, "equipment")
        return True

    def apply_remove_target(self, pid: int, card_type: Optional[CardType], destination: str) -> bool:
        target = self.choose_enemy_target(pid, card_type)
        if not target:
            return False
        self.remove_entity(*target, destination=destination)
        return True

    def apply_multi_return(self, pid: int, card: CardDef) -> bool:
        if card.code == "G-005":
            candidates = [n for n in self.nodes if n.controller is not None and n.controller != pid and any(e.card.card_type == "runner" for e in n.entities)]
            if not candidates:
                return False
            node = max(candidates, key=lambda n: sum(e.atk for e in n.entities if e.card.card_type == "runner"))
            targets = [(node, e) for e in list(node.entities) if e.card.card_type == "runner"]
        else:
            targets = self.enemy_entities(pid, "runner")[:2]
        for node, entity in targets:
            self.remove_entity(node, entity, "hand")
        return bool(targets)

    def apply_remove_own(self, pid: int, card_type: CardType) -> bool:
        target = self.choose_own_target(pid, card_type)
        if not target:
            return False
        self.remove_entity(*target, destination="hand")
        return True

    def card_has_legal_target(self, pid: int, card: CardDef) -> bool:
        if card.card_type in ("runner", "equipment"):
            return bool(self.legal_deploy_nodes(pid))
        if card.skill in {"return_enemy_runner_hand", "return_enemy_runner_deck_bottom", "destroy_enemy_runner", "multi_return_enemy_runners_hand"}:
            return bool(self.enemy_entities(pid, "runner"))
        if card.skill in {"return_enemy_equipment_hand", "destroy_enemy_equipment"}:
            return bool(self.enemy_entities(pid, "equipment"))
        if card.skill == "destroy_enemy_any":
            return bool(self.enemy_entities(pid, None))
        if card.skill == "return_own_runner_hand":
            return bool(self.entities_of(pid, "runner"))
        if card.skill == "return_own_equipment_hand":
            return bool(self.entities_of(pid, "equipment"))
        return True

    def play_card(self, pid: int, card: CardDef) -> Optional[int]:
        player = self.players[pid]
        if player.charge < card.cost or card not in player.hand or not self.card_has_legal_target(pid, card):
            return None
        player.hand.remove(card)
        player.charge -= card.cost
        if card.card_type in ("runner", "equipment"):
            winner = self.deploy_entity(pid, card)
            self.resolve_skill(pid, card)
        else:
            self.resolve_skill(pid, card)
            self.discard_card(pid, card)
            winner = None
        self.stats.played[card.code] += 1
        return winner or self.check_winner()

    def choose_card_to_play(self, pid: int) -> Optional[CardDef]:
        player = self.players[pid]
        playable = [c for c in player.hand if c.cost <= player.charge and self.card_has_legal_target(pid, c)]
        if not playable:
            return None
        priority = {"destroy_enemy_any": 100, "destroy_enemy_runner": 95, "destroy_enemy_equipment": 90, "multi_return_enemy_runners_hand": 85,
                    "return_enemy_runner_deck_bottom": 80, "return_enemy_runner_hand": 75, "return_enemy_equipment_hand": 70,
                    "draw": 60, "look_and_reorder": 55, "look_deck_top": 45, "look_hand": 45, "shuffle_deck": 35,
                    "return_own_runner_hand": 20, "return_own_equipment_hand": 20}
        playable.sort(key=lambda c: (priority.get(c.skill, 0), c.cost, c.atk or 0, c.hp or 0), reverse=True)
        return playable[0]

    def legal_attack_targets(self, pid: int) -> list[NodeState]:
        return [n for n in self.nodes if n.controller is not None and n.controller != pid and n.protected_by is None]

    def available_attackers(self, pid: int) -> list[tuple[NodeState, Entity]]:
        return [(n, e) for n, e in self.entities_of(pid)
                if n.controller == pid and not e.attacked_this_turn and e.entered_turn < self.current_turn and e.atk > 0]

    def should_attack(self, pid: int) -> bool:
        return self.players[pid].charge >= ATTACK_COST_PER_ENTITY and bool(self.legal_attack_targets(pid)) and bool(self.available_attackers(pid))

    def choose_attack(self, pid: int) -> Optional[tuple[NodeState, list[tuple[NodeState, Entity]]]]:
        targets = self.legal_attack_targets(pid)
        attackers = self.available_attackers(pid)
        if not targets or not attackers:
            return None
        def target_score(n: NodeState) -> tuple[int, int, int]:
            domain_have = sum(1 for x in self.nodes if x.definition.domain == n.definition.domain and x.controller == pid and x.online)
            defender_power = sum(e.atk for e in n.entities)
            return (domain_have, -defender_power, -len(n.entities))
        target = max(targets, key=target_score)
        max_count = min(self.players[pid].charge // ATTACK_COST_PER_ENTITY, len(attackers), MAX_ENTITIES_PER_NODE)
        attackers.sort(key=lambda item: (item[1].atk, item[1].hp), reverse=True)
        return target, attackers[:max_count]

    def attack_node(self, pid: int) -> Optional[int]:
        choice = self.choose_attack(pid)
        if not choice:
            return None
        target, attacker_pairs = choice
        player = self.players[pid]
        cost = len(attacker_pairs) * ATTACK_COST_PER_ENTITY
        if cost <= 0 or player.charge < cost:
            return None
        player.charge -= cost
        self.stats.attacks += 1

        attackers: list[Entity] = []
        original_sources: dict[int, NodeState] = {}
        for source, entity in attacker_pairs:
            if entity in source.entities:
                source.entities.remove(entity)
                original_sources[entity.uid] = source
                entity.attacked_this_turn = True
                attackers.append(entity)

        defenders = list(target.entities)
        damage: Counter[int] = Counter()
        for attacker in attackers[:]:
            if not defenders or attacker not in attackers:
                break
            defender = max(defenders, key=lambda e: (e.atk, e.hp))
            damage[attacker.uid] += defender.atk
            damage[defender.uid] += attacker.atk
            if damage[attacker.uid] >= attacker.hp:
                attackers.remove(attacker)
                self.discard_card(attacker.owner, attacker.card)
            if damage[defender.uid] >= defender.hp:
                defenders.remove(defender)
                if defender in target.entities:
                    target.entities.remove(defender)
                self.discard_card(defender.owner, defender.card)

        if not defenders and attackers:
            target.entities.clear()
            for attacker in attackers[:MAX_ENTITIES_PER_NODE]:
                attacker.entered_turn = self.current_turn
                attacker.temp_atk = 0
                attacker.temp_hp = 0
                target.entities.append(attacker)
            target.controller = pid
            self.stats.occupied += 1
        elif not defenders and not attackers:
            target.entities.clear()
            target.controller = None
        else:
            for attacker in attackers:
                original_sources[attacker.uid].entities.append(attacker)

        for source in set(original_sources.values()):
            if source.controller == pid and not source.entities:
                source.controller = None
        return self.check_winner()

    def use_node_skill(self, pid: int) -> Optional[int]:
        player = self.players[pid]
        if player.used_node_skill_this_turn:
            return None
        controlled = [n for n in self.nodes if n.controller == pid and n.online and n.definition.skill_fee <= player.charge]
        controlled.sort(key=lambda n: self.node_skill_priority(n), reverse=True)
        for node in controlled:
            if self.resolve_node_skill(pid, node):
                player.charge -= node.definition.skill_fee
                player.used_node_skill_this_turn = True
                self.stats.node_skill_used[node.definition.code] += 1
                return self.check_winner()
        return None

    def node_skill_priority(self, node: NodeState) -> tuple[int, int]:
        base = {"protect_node": 90, "offline_node": 80, "recover_runner": 70, "recover_equipment": 70,
                "move_runner": 60, "move_equipment": 60, "buff_runner_atk": 50, "buff_equipment_atk": 50,
                "online_node": 40, "buff_runner_hp": 40, "buff_equipment_hp": 40}.get(node.definition.skill, 10)
        return base, node.definition.skill_fee

    def resolve_node_skill(self, pid: int, node: NodeState) -> bool:
        skill = node.definition.skill
        player = self.players[pid]
        if skill == "view_node":
            return True
        if skill == "online_node":
            targets = [n for n in self.nodes if not n.online]
            if not targets: return False
            max(targets, key=lambda n: 1 if n.controller == pid else 0).online = True
            return True
        if skill == "offline_node":
            targets = [n for n in self.nodes if n.online and n.controller is not None and n.controller != pid and n.protected_by is None]
            if not targets: return False
            max(targets, key=lambda n: sum(1 for m in self.nodes if m.controller == n.controller and m.online)).online = False
            return True
        if skill == "protect_node":
            own = [n for n in self.nodes if n.controller == pid and n.protected_by is None]
            if not own: return False
            max(own, key=lambda n: sum(1 for m in self.nodes if m.definition.domain == n.definition.domain and m.controller == pid and m.online)).protected_by = pid
            return True
        if skill in ("move_runner", "move_equipment"):
            ctype: CardType = "runner" if skill == "move_runner" else "equipment"
            own = self.entities_of(pid, ctype)
            targets = [n for n in self.nodes if n.controller == pid and len(n.entities) < MAX_ENTITIES_PER_NODE]
            if not own or not targets: return False
            from_node, entity = max(own, key=lambda item: item[1].atk)
            to_node = min(targets, key=lambda n: len(n.entities))
            if from_node is to_node: return False
            from_node.entities.remove(entity)
            to_node.entities.append(entity)
            if from_node.controller == pid and not from_node.entities:
                from_node.controller = None
            return True
        if skill == "buff_runner_atk": return self.apply_buff(pid, "runner", "atk")
        if skill == "buff_runner_hp": return self.apply_buff(pid, "runner", "hp")
        if skill == "buff_equipment_atk": return self.apply_buff(pid, "equipment", "atk")
        if skill == "buff_equipment_hp": return self.apply_buff(pid, "equipment", "hp")
        if skill == "recover_runner": return self.recover_from_discard(player, "runner")
        if skill == "recover_equipment": return self.recover_from_discard(player, "equipment")
        return False

    def apply_buff(self, pid: int, card_type: CardType, stat: str) -> bool:
        own = self.entities_of(pid, card_type)
        if not own: return False
        entity = max(own, key=lambda item: item[1].atk if stat == "atk" else item[1].hp)[1]
        if stat == "atk": entity.temp_atk += 1
        else: entity.temp_hp += 1
        return True

    def recover_from_discard(self, player: PlayerState, card_type: CardType) -> bool:
        discard = self.active_discard(player)
        candidates = [c for c in discard if c.card_type == card_type]
        if not candidates: return False
        card = max(candidates, key=lambda c: c.cost)
        discard.remove(card)
        player.hand.append(card)
        return True

    def clear_temp_for_player(self, pid: int) -> None:
        for _, entity in self.entities_of(pid):
            entity.temp_atk = 0
            entity.temp_hp = 0

    def take_turn(self, turn_number: int, pid: int) -> Optional[int]:
        self.current_turn = turn_number
        player = self.players[pid]
        for node in self.nodes:
            if node.protected_by == pid:
                node.protected_by = None
        player.used_node_skill_this_turn = False
        for _, entity in self.entities_of(pid):
            entity.attacked_this_turn = False
        player.charge = min(turn_number, 10)
        self.draw(player, 1)
        winner = self.use_node_skill(pid)
        if winner is not None:
            return winner
        safety = 0
        while safety < 40 and player.charge > 0:
            safety += 1
            attack_option = self.should_attack(pid)
            card = self.choose_card_to_play(pid)
            if attack_option and (card is None or player.charge <= 3 or self.rng.random() < 0.55):
                winner = self.attack_node(pid)
            elif card is not None:
                winner = self.play_card(pid, card)
            else:
                break
            if winner is not None:
                return winner
        self.clear_temp_for_player(pid)
        player.charge = 0
        return None

    def tiebreak_winner(self) -> tuple[Optional[int], str]:
        online_counts = {pid: sum(1 for n in self.nodes if n.controller == pid and n.online) for pid in range(self.player_count)}
        best = max(online_counts.values())
        leaders = [pid for pid, value in online_counts.items() if value == best]
        if best > 0 and len(leaders) == 1:
            return leaders[0], "node_count_tiebreak"
        atk_totals = {pid: sum(self.node_power(n, pid) for n in self.nodes if n.online) for pid in range(self.player_count)}
        best_atk = max(atk_totals.values())
        leaders = [pid for pid, value in atk_totals.items() if value == best_atk]
        if best_atk > 0 and len(leaders) == 1:
            return leaders[0], "atk_tiebreak"
        return None, "draw"

    def run(self) -> GameStats:
        for turn in range(1, self.max_turns + 1):
            for pid in range(self.player_count):
                winner = self.take_turn(turn, pid)
                if winner is not None:
                    self.stats.winner = winner
                    self.stats.turns = turn
                    self.stats.reason = "root_access"
                    return self.stats
        self.stats.turns = self.max_turns
        self.stats.winner, self.stats.reason = self.tiebreak_winner()
        return self.stats


def default_deck_mode(players: int) -> DeckMode:
    return "separate" if players == 2 else "shared"


def run_many(games: int, seed: int, max_turns: int, player_count: int, deck_mode: DeckMode) -> list[GameStats]:
    root_rng = random.Random(seed)
    return [Game(random.Random(root_rng.randrange(10**12)), player_count, deck_mode, max_turns).run() for _ in range(games)]


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def report(results: list[GameStats]) -> None:
    games = len(results)
    player_count = results[0].player_count
    deck_mode = results[0].deck_mode
    wins = Counter(r.winner for r in results)
    reasons = Counter(r.reason for r in results)
    turns = [r.turns for r in results]
    played_total: Counter[str] = Counter()
    node_skill_total: Counter[str] = Counter()
    total_attacks = sum(r.attacks for r in results)
    total_occupied = sum(r.occupied for r in results)
    for r in results:
        played_total.update(r.played)
        node_skill_total.update(r.node_skill_used)
    print("归零者 / ZERO ACCESS｜2–4 人整套卡牌平衡模拟报告")
    print("=" * 72)
    print(f"玩家数：{player_count}")
    print(f"牌库模式：{deck_mode}")
    print(f"模拟局数：{games}")
    for pid in range(player_count):
        print(f"P{pid}胜率：{pct(wins[pid] / games)}")
    print(f"平局率：{pct(wins[None] / games)}")
    print(f"平均结束回合：{statistics.mean(turns):.2f}")
    print(f"中位结束回合：{statistics.median(turns):.1f}")
    print("结束原因：" + ", ".join(f"{k}={v}" for k, v in reasons.items()))
    print(f"平均攻击节点次数：{total_attacks / games:.2f}")
    print(f"平均获得节点控制权次数：{total_occupied / games:.2f}")
    print("\n使用率最高的手牌卡 Top 12")
    for code, count in played_total.most_common(12):
        card = next(c for c in CARDS if c.code == code)
        print(f"- {code} {card.name}: {count / games:.2f}/局")
    print("\n节点技能使用 Top 12")
    for code, count in node_skill_total.most_common(12):
        node = next(n for n in NODES if n.code == code)
        print(f"- {code} {node.name}: {count / games:.2f}/局")
    print("\n当前模拟假设")
    print("- 每座节点最多 3 个实体。")
    print("- 节点只属于一个玩家，或无主。")
    print("- 一个节点上不能同时存在多个玩家实体；无主节点上不能存在任何实体。")
    print("- 部署只能到无主节点或己方控制节点；部署到无主节点时获得节点控制权。")
    print("- 攻击目标必须是其他玩家控制的节点，不能是无主节点。")
    print("- 刚上场的实体本回合不能参与攻击节点。")
    print("- 攻击方可从自己控制的任意节点派出任意数量实体，受电荷限制；每个实体攻击费用 1 电荷。")
    print("- 战斗后目标节点无守方且攻方有存活实体，则攻方获得节点控制权。")


def main() -> int:
    parser = argparse.ArgumentParser(description="归零者 2–4 人整套卡牌平衡模拟器")
    parser.add_argument("--players", type=int, default=2, choices=[2, 3, 4])
    parser.add_argument("--deck-mode", choices=["shared", "separate"], default=None)
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-turns", type=int, default=30)
    args = parser.parse_args()
    deck_mode: DeckMode = args.deck_mode or default_deck_mode(args.players)  # type: ignore[assignment]
    report(run_many(args.games, args.seed, args.max_turns, args.players, deck_mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
