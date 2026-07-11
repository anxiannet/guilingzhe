#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
人类决策平衡测试器 v0.1

用途：
- 复用 balance_simulator.py 的卡表与规则结算。
- 不修改测试卡表，不创建平行规则。
- 将 AI 决策从“完美求解”改为“有限信息、有限前瞻、有限行动”的人类式策略。

人类式决策假设：
1. 只读取公开场面，不读取其他玩家手牌。
2. 明显的本回合胜利通常会发现，但保留少量漏算。
3. 重点关注下一位玩家，也会关注其他已经控制同域2节点的玩家。
4. 不穷举完整行动树；每回合只进行4至7次主要决策。
5. 不保证总是选择最低费用的阻止方案。
6. 主攻域具有惯性，但在连续受阻后可能切换。
7. 普通玩家会在扩张、防守、攻击和资源使用之间摇摆。
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from balance_simulator import Game as RulesGame
from balance_simulator import GameStats, NodeState, PlayerState, ThreatPlan


@dataclass
class HumanMetrics:
    decisions: int = 0
    visible_threats: int = 0
    threats_noticed: int = 0
    threats_ignored: int = 0
    obvious_wins_available: int = 0
    obvious_wins_taken: int = 0
    focus_switches: int = 0


class HumanDecisionGame(RulesGame):
    """RulesGame with a bounded, imperfect, public-information decision policy."""

    def __init__(
        self,
        rng: random.Random,
        player_count: int,
        max_turns: int = 50,
        notice_win_rate: float = 0.96,
        notice_next_threat_rate: float = 0.90,
        notice_other_threat_rate: float = 0.70,
    ):
        super().__init__(rng, player_count, max_turns, "tactical_v3")
        self.stats.ai_mode = "human_like"  # type: ignore[assignment]
        self.notice_win_rate = notice_win_rate
        self.notice_next_threat_rate = notice_next_threat_rate
        self.notice_other_threat_rate = notice_other_threat_rate
        self.human = HumanMetrics()
        self.focus_pressure = {pid: 0 for pid in range(player_count)}

    def turn_distance(self, current_pid: int, target_pid: int) -> int:
        current_index = self.turn_order.index(current_pid)
        target_index = self.turn_order.index(target_pid)
        return (target_index - current_index) % len(self.turn_order)

    def has_obvious_root_deploy(self, player: PlayerState) -> bool:
        counts = self.domain_count(player.pid)
        has_node = any(counts[node.definition.domain] >= 2 for node in self.central_nodes())
        has_entity = any(
            card.card_type in {"runner", "equipment"} and card.cost <= player.charge
            for card in player.hand
        )
        return has_node and has_entity

    def has_obvious_root_attack(self, player: PlayerState) -> bool:
        counts = self.domain_count(player.pid)
        for node in self.controlled_nodes():
            if node.controller == player.pid or counts[node.definition.domain] < 2:
                continue
            if self.choose_attack(player, required_node=node):
                return True
        return False

    def maybe_take_obvious_win(self, player: PlayerState) -> bool:
        available = self.has_obvious_root_deploy(player) or self.has_obvious_root_attack(player)
        if not available:
            return False
        self.human.obvious_wins_available += 1
        if self.rng.random() > self.notice_win_rate:
            return False
        if self.immediate_root_deploy(player) or self.immediate_root_attack(player):
            self.human.obvious_wins_taken += 1
            return True
        return False

    def visible_block_plans(self, player: PlayerState) -> list[ThreatPlan]:
        """Find public, one-step threats only. Opponent hands are never inspected."""
        plans: list[tuple[int, int, float, ThreatPlan]] = []
        for opponent in range(self.player_count):
            if opponent == player.pid:
                continue
            threats = self.opponent_immediate_deploy_threat(opponent)
            if not threats:
                continue
            distance = self.turn_distance(player.pid, opponent)
            for domain, _ in threats:
                nodes = [
                    node
                    for node in self.controlled_nodes(opponent)
                    if node.definition.domain == domain and node.protected_by is None
                ]
                for node in nodes:
                    card_plan = self.cheapest_block_card(player, node)
                    if card_plan:
                        plans.append((distance, card_plan.cost, self.rng.random(), card_plan))
                    attack_choice = self.choose_attack(player, required_node=node)
                    if attack_choice:
                        plan = ThreatPlan(opponent, domain, node, "attack", len(attack_choice[1]))
                        plans.append((distance, plan.cost, self.rng.random(), plan))
        plans.sort(key=lambda item: (item[0], item[1], item[2]))
        return [item[3] for item in plans]

    def maybe_block_visible_threat(self, player: PlayerState) -> bool:
        plans = self.visible_block_plans(player)
        if not plans:
            return False
        self.human.visible_threats += 1
        plan = plans[0]
        distance = self.turn_distance(player.pid, plan.opponent)
        notice_rate = self.notice_next_threat_rate if distance == 1 else self.notice_other_threat_rate

        # Human players are more likely to notice a threat when the opponent already leads the table.
        opponent_nodes = len(self.controlled_nodes(plan.opponent))
        max_nodes = max(len(self.controlled_nodes(pid)) for pid in range(self.player_count))
        if opponent_nodes >= max_nodes:
            notice_rate = min(0.98, notice_rate + 0.05)

        if self.rng.random() > notice_rate:
            self.human.threats_ignored += 1
            return False

        self.human.threats_noticed += 1

        # Humans do not always choose the mathematically cheapest plan.
        affordable = [p for p in plans[:3] if p.cost <= player.charge]
        if not affordable:
            return False
        if len(affordable) == 1 or self.rng.random() < 0.65:
            chosen = affordable[0]
        else:
            chosen = self.rng.choice(affordable)
        return self.execute_block_plan(player, chosen)

    def maybe_switch_focus(self, player: PlayerState) -> None:
        focus = self.choose_focus_domain(player.pid)
        own_progress = self.domain_count(player.pid)[focus]
        visible_focus = any(node.definition.domain == focus for node in self.central_nodes())
        enemy_focus = sum(
            1
            for node in self.controlled_nodes()
            if node.controller != player.pid and node.definition.domain == focus
        )

        if own_progress == 0 and not visible_focus:
            self.focus_pressure[player.pid] += 1
        elif enemy_focus >= 2 and own_progress <= 1:
            self.focus_pressure[player.pid] += 1
        else:
            self.focus_pressure[player.pid] = max(0, self.focus_pressure[player.pid] - 1)

        if self.focus_pressure[player.pid] >= 2 and self.rng.random() < 0.55:
            old_focus = player.focus_domain
            player.focus_domain = None
            self.choose_focus_domain(player.pid)
            self.focus_pressure[player.pid] = 0
            if player.focus_domain != old_focus:
                self.human.focus_switches += 1

    def human_action_order(self, player: PlayerState) -> list[str]:
        """Return a context-sensitive but imperfect action preference order."""
        own_best = self.best_domain_count(player.pid)
        central_progress = max(
            (self.domain_count(player.pid)[node.definition.domain] for node in self.central_nodes()),
            default=0,
        )
        has_attack = bool(self.choose_attack(player))

        weighted: list[tuple[float, str]] = []
        weighted.append((4.5 + 1.8 * central_progress, "play"))
        weighted.append((2.5 + 1.2 * own_best, "skill"))
        weighted.append((3.0 + (2.8 if has_attack else -2.0), "attack"))

        # Add small personal-style noise each decision instead of perfect deterministic sorting.
        weighted = [(score + self.rng.uniform(-1.5, 1.5), action) for score, action in weighted]
        weighted.sort(reverse=True)
        return [action for _, action in weighted]

    def take_turn(self, pid: int, turn: int) -> Optional[int]:
        self.current_turn = turn
        player = self.players[pid]
        self.choose_focus_domain(pid)
        self.maybe_switch_focus(player)

        for node in self.nodes:
            if node.protected_by == pid:
                node.protected_by = None
        player.used_node_skill_this_turn = False
        for node in self.controlled_nodes(pid):
            for entity in node.entities:
                entity.attacked_this_turn = False

        player.charge = min(turn, 10)
        self.draw(player)

        # Human players make a bounded number of major decisions per turn.
        decision_budget = self.rng.randint(4, 7)
        blocked_this_turn = False

        for _ in range(decision_budget):
            self.human.decisions += 1
            winner = self.check_winner()
            if winner is not None:
                return winner

            if self.maybe_take_obvious_win(player):
                continue

            if not blocked_this_turn and self.maybe_block_visible_threat(player):
                blocked_this_turn = True
                continue

            acted = False
            for action in self.human_action_order(player):
                if action == "skill" and self.use_node_skill(player, reserve=0):
                    acted = True
                    break
                if action == "attack" and self.attack(player, reserve=0):
                    acted = True
                    break
                if action == "play" and self.play_card(player, reserve=0):
                    acted = True
                    break
            if not acted:
                break

            # Players sometimes stop after a satisfactory action instead of exhausting every option.
            if self.rng.random() < 0.12:
                break

        self.cleanup(player)
        return self.check_winner()


def simulate(
    players: int,
    games: int,
    seed: int,
    max_turns: int,
    notice_win_rate: float,
    notice_next_threat_rate: float,
    notice_other_threat_rate: float,
) -> tuple[list[GameStats], HumanMetrics]:
    results: list[GameStats] = []
    total = HumanMetrics()
    for index in range(games):
        game = HumanDecisionGame(
            random.Random(seed + index),
            players,
            max_turns,
            notice_win_rate,
            notice_next_threat_rate,
            notice_other_threat_rate,
        )
        results.append(game.run())
        total.decisions += game.human.decisions
        total.visible_threats += game.human.visible_threats
        total.threats_noticed += game.human.threats_noticed
        total.threats_ignored += game.human.threats_ignored
        total.obvious_wins_available += game.human.obvious_wins_available
        total.obvious_wins_taken += game.human.obvious_wins_taken
        total.focus_switches += game.human.focus_switches
    return results, total


def report(results: list[GameStats], human: HumanMetrics) -> None:
    wins = Counter(result.winner for result in results)
    reasons = Counter(result.reason for result in results)
    turns = [result.turns for result in results]
    games = len(results)

    print(f"players={results[0].player_count} ai_mode=human_like games={games}")
    for pid in range(results[0].player_count):
        print(f"seat_{pid}_win_rate={wins[pid] / games:.3f}")
    print(f"draw_rate={wins[None] / games:.3f}")
    print(f"root_access_rate={reasons['root_access'] / games:.3f}")
    print(f"root_access_by_30_rate={sum(r.root_by_30 for r in results) / games:.3f}")
    print(f"avg_turns={statistics.mean(turns):.2f}")
    print(f"median_turns={statistics.median(turns):.2f}")
    print(f"avg_attacks={statistics.mean(r.attacks for r in results):.2f}")
    print(f"avg_control_changes={statistics.mean(r.control_changes for r in results):.2f}")
    print(f"avg_block_attempts={statistics.mean(r.block_attempts for r in results):.2f}")
    print(f"avg_successful_blocks={statistics.mean(r.successful_blocks for r in results):.2f}")
    print(f"deck_empty_rate={sum(r.deck_empty_observed for r in results) / games:.3f}")
    print(f"avg_decisions={human.decisions / games:.2f}")
    print(f"avg_visible_threats={human.visible_threats / games:.2f}")
    print(f"threat_notice_rate={human.threats_noticed / max(1, human.visible_threats):.3f}")
    print(f"obvious_win_take_rate={human.obvious_wins_taken / max(1, human.obvious_wins_available):.3f}")
    print(f"avg_focus_switches={human.focus_switches / games:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", type=int, default=4, choices=[2, 3, 4])
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--notice-win-rate", type=float, default=0.96)
    parser.add_argument("--notice-next-threat-rate", type=float, default=0.90)
    parser.add_argument("--notice-other-threat-rate", type=float, default=0.70)
    args = parser.parse_args()

    results, human = simulate(
        args.players,
        args.games,
        args.seed,
        args.max_turns,
        args.notice_win_rate,
        args.notice_next_threat_rate,
        args.notice_other_threat_rate,
    )
    report(results, human)


if __name__ == "__main__":
    main()
