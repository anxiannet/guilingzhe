#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
2–4 人平衡模拟器 v0.11

当前规则假设：
- 2 / 3 / 4 人默认共用一副测试手牌牌库。
- 所有玩家共用公共弃牌区。
- 节点牌库由12张测试节点牌组成，开局翻开3张。
- 部署到中央无主节点后获得节点控制权。
- 已控制节点没有己方实体时，节点回流节点牌库并重洗。
- 节点不具有在线或离线状态。
- 控制同一系统域的3座节点即获得 Root Access。
- 中继站与屏蔽塔旧技能已废止，在重新设计前不发动技能。
- 本脚本是平衡压力测试器，不是最终规则引擎。
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
    ai_mode: AIMode
    played: Counter[str] = field(default_factory=Counter)
    node_skill_used: Counter[str] = field(default_factory=Counter)
    attacks: int = 0
    control_changes: int = 0
    draw_fail_count: int = 0


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

DEFAULT_COPIES: dict[str, int] = {card.code: 2 for card in CARDS}
DEFAULT_COPIES["R-001"] += 1
DEFAULT_COPIES["G-011"] += 1


class Game:
    def __init__(self, rng: random.Random, player_count: int, max_turns: int, opening_hand: int, ai_mode: AIMode):
        self.rng = rng
        self.player_count = player_count
        self.max_turns = max_turns
        self.ai_mode = ai_mode
        self.current_turn = 0
        self.players = [PlayerState(pid) for pid in range(player_count)]
        self.shared_deck = self.make_deck()
        self.shared_discard: list[CardDef] = []
        self.nodes = [NodeState(node) for node in NODES]
        self.node_deck = self.nodes[:]
        self.stats = GameStats(None, 0, "", player_count, ai_mode)
        self.next_uid = 1
        self.rng.shuffle(self.node_deck)
        self.replenish_central()
        for player in self.players:
            self.draw(player, opening_hand)

    def make_deck(self) -> list[CardDef]:
        by_code = {card.code: card for card in CARDS}
        deck = [by_code[code] for code, count in DEFAULT_COPIES.items() for _ in range(count)]
        self.rng.shuffle(deck)
        return deck

    def draw(self, player: PlayerState, n: int = 1) -> None:
        for _ in range(n):
            if not self.shared_deck:
                self.stats.draw_fail_count += 1
                return
            player.hand.append(self.shared_deck.pop(0))

    def central_nodes(self) -> list[NodeState]:
        return [n for n in self.nodes if n.zone == "central"]

    def controlled_nodes(self, pid: Optional[int] = None) -> list[NodeState]:
        nodes = [n for n in self.nodes if n.zone == "controlled" and n.controller is not None]
        return nodes if pid is None else [n for n in nodes if n.controller == pid]

    def domain_count(self, pid: int) -> Counter[str]:
        return Counter(n.definition.domain for n in self.controlled_nodes(pid))

    def best_domain_count(self, pid: int) -> int:
        return max(self.domain_count(pid).values(), default=0)

    def check_winner(self) -> Optional[int]:
        for pid in range(self.player_count):
            if any(count >= ROOT_ACCESS_NODE_COUNT for count in self.domain_count(pid).values()):
                return pid
        return None

    def replenish_central(self) -> None:
        while len(self.central_nodes()) < CENTRAL_NODE_TARGET and self.node_deck:
            node = self.node_deck.pop(0)
            node.zone = "central"
            node.controller = None
            node.protected_by = None
            node.entities.clear()

    def normalize_nodes(self) -> None:
        for node in self.controlled_nodes():
            if not node.entities:
                node.zone = "deck"
                node.controller = None
                node.protected_by = None
                self.node_deck.append(node)
                self.rng.shuffle(self.node_deck)
        self.replenish_central()

    def legal_deploy_nodes(self, pid: int) -> list[NodeState]:
        nodes = [n for n in self.central_nodes() if len(n.entities) < MAX_ENTITIES_PER_NODE]
        nodes.extend(n for n in self.controlled_nodes(pid) if len(n.entities) < MAX_ENTITIES_PER_NODE)
        return nodes

    def score_node(self, pid: int, node: NodeState) -> tuple[int, int, int]:
        progress = self.domain_count(pid)[node.definition.domain]
        completes = int(node.zone == "central" and progress >= ROOT_ACCESS_NODE_COUNT - 1)
        return completes, progress, MAX_ENTITIES_PER_NODE - len(node.entities)

    def best_deploy_node(self, pid: int) -> Optional[NodeState]:
        nodes = self.legal_deploy_nodes(pid)
        return max(nodes, key=lambda n: self.score_node(pid, n)) if nodes else None

    def deploy(self, player: PlayerState, card: CardDef, node: NodeState) -> None:
        node.entities.append(Entity(self.next_uid, card, player.pid, self.current_turn))
        self.next_uid += 1
        if node.zone == "central":
            node.zone = "controlled"
            node.controller = player.pid
            self.stats.control_changes += 1
            self.replenish_central()

    def entities(self, pid: Optional[int], card_type: Optional[CardType]) -> list[tuple[NodeState, Entity]]:
        return [
            (node, entity)
            for node in self.controlled_nodes()
            for entity in node.entities
            if (pid is None or entity.owner == pid) and (card_type is None or entity.card.card_type == card_type)
        ]

    def enemy_entities(self, pid: int, card_type: Optional[CardType]) -> list[tuple[NodeState, Entity]]:
        return [(n, e) for n, e in self.entities(None, card_type) if e.owner != pid]

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

    def return_to_deck_bottom(self, node: NodeState, entity: Entity) -> None:
        self.remove_entity(node, entity)
        self.shared_deck.append(entity.card)

    def best_target(self, pid: int, card_type: Optional[CardType], max_cost: Optional[int] = None) -> Optional[tuple[NodeState, Entity]]:
        targets = self.enemy_entities(pid, card_type)
        if max_cost is not None:
            targets = [(n, e) for n, e in targets if e.card.cost <= max_cost]
        return max(targets, key=lambda ne: (ne[1].card.cost, ne[1].atk, ne[1].hp)) if targets else None

    def resolve_card_skill(self, player: PlayerState, card: CardDef) -> bool:
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
        if skill == "return_own_runner_hand":
            targets = self.entities(player.pid, "runner")
            if not targets:
                return False
            node, entity = max(targets, key=lambda ne: ne[1].card.cost)
            self.return_to_hand(node, entity)
        elif skill in {"return_enemy_runner_hand", "return_enemy_equipment_hand"}:
            target_type: CardType = "runner" if skill.endswith("runner_hand") else "equipment"
            target = self.best_target(player.pid, target_type)
            if not target:
                return False
            self.return_to_hand(*target)
        elif skill == "return_enemy_runner_deck_bottom":
            target = self.best_target(player.pid, "runner")
            if not target:
                return False
            self.return_to_deck_bottom(*target)
        elif skill == "multi_return_enemy_runners_hand":
            targets = sorted(self.enemy_entities(player.pid, "runner"), key=lambda ne: ne[1].atk, reverse=True)
            if not targets:
                return False
            for node, entity in targets[:2]:
                self.return_to_hand(node, entity)
        elif skill == "destroy_enemy_runner_cost_4_or_less":
            target = self.best_target(player.pid, "runner", 4)
            if not target:
                return False
            self.destroy_entity(*target)
        elif skill == "destroy_enemy_equipment":
            target = self.best_target(player.pid, "equipment")
            if not target:
                return False
            self.destroy_entity(*target)
        elif skill == "destroy_enemy_any":
            target = self.best_target(player.pid, None)
            if not target:
                return False
            self.destroy_entity(*target)
        self.normalize_nodes()
        return True

    def play_card(self, player: PlayerState) -> bool:
        playable = [c for c in player.hand if c.cost <= player.charge]
        playable.sort(key=lambda c: (c.cost, c.atk or 0, c.hp or 0), reverse=True)
        for card in playable:
            if card.card_type in {"runner", "equipment"}:
                node = self.best_deploy_node(player.pid)
                if node is None:
                    continue
                player.hand.remove(card)
                player.charge -= card.cost
                self.deploy(player, card, node)
                self.resolve_card_skill(player, card)
                self.stats.played[card.code] += 1
                return True
            player.hand.remove(card)
            player.charge -= card.cost
            success = self.resolve_card_skill(player, card)
            self.shared_discard.append(card)
            if success:
                self.stats.played[card.code] += 1
                return True
        return False

    def attack(self, player: PlayerState) -> bool:
        targets = [n for n in self.controlled_nodes() if n.controller != player.pid and n.protected_by is None and n.entities]
        attackers = [
            (n, e)
            for n in self.controlled_nodes(player.pid)
            for e in n.entities
            if not e.attacked_this_turn and e.entered_turn < self.current_turn and e.atk > 0
        ]
        max_count = min(len(attackers), player.charge // ATTACK_COST_PER_ENTITY)
        if not targets or max_count <= 0:
            return False
        target = max(targets, key=lambda n: (self.domain_count(n.controller or -1)[n.definition.domain], -sum(e.hp for e in n.entities)))
        chosen = sorted(attackers, key=lambda ne: (ne[1].atk, ne[1].hp), reverse=True)[:max_count]
        player.charge -= len(chosen)
        self.stats.attacks += 1
        sources = {e.uid: n for n, e in chosen}
        units = [e for _, e in chosen]
        for source, entity in chosen:
            self.remove_entity(source, entity)
            entity.attacked_this_turn = True
        survivors = units[:]
        for attacker in units:
            if not target.entities or attacker not in survivors:
                continue
            defender = max(target.entities, key=lambda e: (e.atk, e.hp))
            if attacker.atk >= defender.hp:
                self.destroy_entity(target, defender)
            if defender.atk >= attacker.hp:
                survivors.remove(attacker)
                self.shared_discard.append(attacker.card)
        if not target.entities and survivors:
            target.controller = player.pid
            target.zone = "controlled"
            target.entities = survivors[:MAX_ENTITIES_PER_NODE]
            self.stats.control_changes += 1
            for entity in survivors[MAX_ENTITIES_PER_NODE:]:
                player.hand.append(entity.card)
        else:
            for entity in survivors:
                source = sources[entity.uid]
                if len(source.entities) < MAX_ENTITIES_PER_NODE:
                    source.entities.append(entity)
                else:
                    player.hand.append(entity.card)
        self.normalize_nodes()
        return True

    def use_node_skill(self, player: PlayerState) -> bool:
        if player.used_node_skill_this_turn:
            return False
        nodes = [n for n in self.controlled_nodes(player.pid) if n.definition.skill != "pending" and n.definition.skill_fee <= player.charge]
        for node in sorted(nodes, key=lambda n: n.definition.skill_fee, reverse=True):
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
        if skill == "protect_node":
            targets = [n for n in self.controlled_nodes(player.pid) if n.protected_by is None]
            if not targets:
                return False
            targets[0].protected_by = player.pid
            return True
        if skill in {"move_runner", "move_equipment"}:
            card_type: CardType = "runner" if skill == "move_runner" else "equipment"
            sources = self.entities(player.pid, card_type)
            destinations = [n for n in self.controlled_nodes(player.pid) if len(n.entities) < MAX_ENTITIES_PER_NODE]
            for dst in destinations:
                for src, entity in sources:
                    if dst is not src:
                        self.remove_entity(src, entity)
                        dst.entities.append(entity)
                        return True
            return False
        if skill in {"buff_runner_atk", "buff_runner_hp", "buff_equipment_atk", "buff_equipment_hp"}:
            card_type: CardType = "runner" if "runner" in skill else "equipment"
            targets = [e for _, e in self.entities(player.pid, card_type)]
            if not targets:
                return False
            target = max(targets, key=lambda e: (e.atk, e.hp))
            target.temp_atk += int(skill.endswith("atk"))
            target.temp_hp += int(skill.endswith("hp"))
            return True
        if skill in {"recover_runner", "recover_equipment"}:
            card_type: CardType = "runner" if skill == "recover_runner" else "equipment"
            cards = [c for c in self.shared_discard if c.card_type == card_type]
            if not cards:
                return False
            card = max(cards, key=lambda c: c.cost)
            self.shared_discard.remove(card)
            player.hand.append(card)
            return True
        return False

    def cleanup(self, player: PlayerState) -> None:
        for node in self.nodes:
            for entity in node.entities:
                entity.temp_atk = 0
                entity.temp_hp = 0
        while len(player.hand) > 7:
            card = min(player.hand, key=lambda c: c.cost)
            player.hand.remove(card)
            self.shared_discard.append(card)
        player.charge = 0

    def take_turn(self, pid: int, turn: int) -> Optional[int]:
        self.current_turn = turn
        player = self.players[pid]
        for node in self.nodes:
            if node.protected_by == pid:
                node.protected_by = None
        player.used_node_skill_this_turn = False
        for node in self.controlled_nodes(pid):
            for entity in node.entities:
                entity.attacked_this_turn = False
        player.charge = min(turn, 10)
        self.draw(player)
        self.use_node_skill(player)
        for _ in range(40):
            winner = self.check_winner()
            if winner is not None:
                return winner
            if self.attack(player):
                continue
            if self.play_card(player):
                continue
            break
        self.cleanup(player)
        return self.check_winner()

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
            for pid in range(self.player_count):
                winner = self.take_turn(pid, turn)
                if winner is not None:
                    self.stats.winner = winner
                    self.stats.turns = turn
                    self.stats.reason = "root_access"
                    return self.stats
        self.stats.winner = self.tiebreak()
        self.stats.turns = self.max_turns
        self.stats.reason = "tiebreak" if self.stats.winner is not None else "draw"
        return self.stats


def simulate(players: int, games: int, seed: int, max_turns: int, ai_mode: AIMode) -> list[GameStats]:
    return [Game(random.Random(seed + i), players, max_turns, 5, ai_mode).run() for i in range(games)]


def report(results: list[GameStats]) -> None:
    wins = Counter(result.winner for result in results)
    turns = [result.turns for result in results]
    played = Counter()
    node_skills = Counter()
    for result in results:
        played.update(result.played)
        node_skills.update(result.node_skill_used)
    print(f"players={results[0].player_count} ai_mode={results[0].ai_mode} games={len(results)}")
    for pid in range(results[0].player_count):
        print(f"seat_{pid}_win_rate={wins[pid] / len(results):.3f}")
    print(f"draw_rate={wins[None] / len(results):.3f}")
    print(f"avg_turns={statistics.mean(turns):.2f}")
    print(f"median_turns={statistics.median(turns):.2f}")
    print("top_played=" + ", ".join(f"{code}:{count}" for code, count in played.most_common(12)))
    print("top_node_skills=" + ", ".join(f"{code}:{count}" for code, count in node_skills.most_common(12)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=4, choices=[2, 3, 4])
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--ai-mode", choices=["baseline", "tactical"], default="tactical")
    args = parser.parse_args()
    report(simulate(args.players, args.games, args.seed, args.max_turns, args.ai_mode))


if __name__ == "__main__":
    main()
