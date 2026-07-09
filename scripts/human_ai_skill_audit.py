#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归零者 / ZERO ACCESS
类人 AI 技能自动测试脚本

用途：
1. 审核标准版游侠与物品卡技能是否只由基础动作池变体或组合形成。
2. 审核卡名、技能、载体是否具有直觉关联。
3. 审核费用 / ATK / HP 是否符合当前强度分层。
4. 审核节点技能费用是否符合当前费用分层。

运行：
    python scripts/human_ai_skill_audit.py

设计原则：
- 这不是最终规则引擎。
- 这是“类人审核器”：模拟玩家读卡后的直觉判断。
- 结果分为 PASS / WARN / FAIL。
- FAIL 表示必须修改；WARN 表示建议人工复核。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Literal
import re
import sys

Severity = Literal["PASS", "WARN", "FAIL"]
CardKind = Literal["runner", "gear", "node"]
GearForm = Literal["one_shot", "equipment", "none"]


# ============================================================
# 基础规则配置
# ============================================================

BASE_ACTIONS = {
    "draw": "抽牌",
    "look_deck_top": "查看牌库顶",
    "adjust_deck_top": "调整牌库顶",
    "return_to_deck": "返回牌库",
    "look_hand": "查看手牌",
    "return_to_hand": "返回手牌",
    "destroy": "消灭",
    "shuffle_deck": "重洗牌库",  # 备选动作
}

REMOVED_ACTION_PATTERNS = {
    "弃牌": r"弃\s*\d*\s*张牌|弃牌",
    "公开手牌": r"公开.*手牌",
    "隔离": r"隔离",
    "禁用": r"禁用",
    "费用变化": r"费用[+-]|费用-\d|费用\+\d|费用减少|费用降低",
    "攻击变化": r"攻击力[+-]|攻击力\+\d|攻击力-\d|不能攻击",
    "生命变化": r"生命值[+-]|生命值\+\d|恢复\d*点生命|恢复生命",
    "伤害": r"造成\d*点伤害|伤害",
    "治疗": r"治疗|恢复生命",
    "获得电荷": r"获得\d*电荷|获得.*电荷",
    "己方返回牌库": r"己方.*返回.*牌库|你的.*返回.*牌库",
    "己方消灭": r"消灭1个己方|消灭.*己方",
}

ACTION_DETECTORS: dict[str, Callable[[str], bool]] = {
    "draw": lambda s: "抽" in s and "牌" in s,
    "look_deck_top": lambda s: "查看" in s and "牌库顶" in s,
    "adjust_deck_top": lambda s: "排序" in s or "置底" in s or "放回" in s,
    "return_to_deck": lambda s: "返回" in s and "牌库" in s,
    "look_hand": lambda s: "查看" in s and "手牌" in s and "牌库" not in s,
    "return_to_hand": lambda s: "返回" in s and "手牌" in s and "牌库" not in s,
    "destroy": lambda s: "消灭" in s,
    "shuffle_deck": lambda s: "重洗" in s,
}

NAME_INTUITION_RULES: list[tuple[str, set[str], str]] = [
    ("侦察|侦测|探针|无人机", {"look_deck_top", "look_hand"}, "侦察/侦测类名称应对应查看类技能。"),
    ("信使|抽取|广播", {"draw"}, "信使/抽取/广播类名称应对应抽牌。"),
    ("筛选|预读|算盘|洗牌|快手", {"look_deck_top", "adjust_deck_top", "shuffle_deck", "draw"}, "筛选/预读/算盘/洗牌类名称应对应牌库调整或抽牌组合。"),
    ("弹弓|甩棍|门神|推土机|束带|牵引", {"return_to_hand"}, "弹回/推回/牵引类名称应对应返回手牌。"),
    ("清除|爆破|清道夫", {"destroy"}, "清除/爆破/清道夫类名称应对应消灭。"),
    ("回滚|回收|沙盒", {"return_to_deck", "return_to_hand"}, "回滚/回收/沙盒类名称应对应返回牌库或返回手牌。"),
    ("信标|撤离|烟幕", {"return_to_hand"}, "信标/撤离/烟幕类名称应对应撤回或返回手牌。"),
    ("扒手|中间人", {"look_hand", "draw"}, "扒手/中间人应对应查看手牌或资源获取。"),
    ("不倒翁", {"return_to_hand"}, "不倒翁应对应被消灭后回到手牌。"),
    ("扳手", {"return_to_hand"}, "扳手应对应回收己方装备。"),
]

# 技能强度建议费用区间。这里只做粗粒度人工审核，不做精确平衡。
COST_BANDS = {
    "light_info": (1, 1),
    "standard_info_combo": (2, 3),
    "standard_return": (2, 3),
    "return_to_deck": (3, 3),
    "destroy_single": (4, 4),
    "destroy_flexible": (5, 5),
    "multi_return": (4, 5),
    "equipment_light": (1, 2),
    "equipment_heavy": (4, 5),
}


# ============================================================
# 数据结构
# ============================================================

@dataclass(frozen=True)
class Card:
    card_id: str
    name: str
    english_name: str
    kind: CardKind
    cost: int
    atk: int | None
    hp: int | None
    text: str
    carrier: str
    gear_form: GearForm = "none"


@dataclass(frozen=True)
class Node:
    node_id: str
    name: str
    domain: str
    skill_fee: int
    text: str
    function: str


@dataclass
class Finding:
    severity: Severity
    subject: str
    message: str


@dataclass
class AuditContext:
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: Severity, subject: str, message: str) -> None:
        self.findings.append(Finding(severity, subject, message))

    def pass_(self, subject: str, message: str) -> None:
        self.add("PASS", subject, message)

    def warn(self, subject: str, message: str) -> None:
        self.add("WARN", subject, message)

    def fail(self, subject: str, message: str) -> None:
        self.add("FAIL", subject, message)


# ============================================================
# 当前卡表数据
# ============================================================

RUNNERS: list[Card] = [
    Card("R-001", "侦察员", "Scout", "runner", 1, 1, 1, "查看手牌牌库顶1张。", "游侠"),
    Card("R-002", "信使", "Courier", "runner", 1, 1, 1, "抽1张牌。", "游侠"),
    Card("R-003", "预读者", "Pre-Reader", "runner", 2, 1, 2, "查看手牌牌库顶2张，任意排序后放回。", "游侠"),
    Card("R-004", "扒手", "Pickpocket", "runner", 1, 1, 1, "查看1名对手的手牌。", "游侠"),
    Card("R-005", "广播员", "Broadcaster", "runner", 2, 1, 2, "选择1名玩家，其抽1张牌。", "游侠"),
    Card("R-007", "烟幕手", "Smoke Runner", "runner", 2, 1, 2, "将1个己方场上游侠返回你的手牌。", "游侠"),
    Card("R-008", "洗牌客", "Shuffler", "runner", 1, 1, 1, "重洗手牌牌库。", "游侠"),
    Card("R-009", "清除者", "Eliminator", "runner", 4, 3, 2, "消灭1个对手场上游侠。", "游侠"),
    Card("R-010", "甩棍", "Baton", "runner", 2, 2, 1, "将1个对手场上游侠返回其控制者手牌。", "游侠"),
    Card("R-013", "不倒翁", "Tumbler", "runner", 3, 1, 3, "此牌被消灭时，返回你的手牌。", "游侠"),
    Card("R-014", "筛选员", "Screener", "runner", 3, 1, 2, "查看手牌牌库顶2张，任意排序后放回。然后抽1张牌。", "游侠"),
    Card("R-015", "扳手", "Wrench", "runner", 2, 1, 2, "将1个己方场上装备返回你的手牌。", "游侠"),
    Card("R-016", "推土机", "Bulldozer", "runner", 4, 3, 3, "将至多2个对手场上游侠返回各自控制者手牌。", "游侠"),
    Card("R-017", "爆破兵", "Demolitionist", "runner", 4, 3, 2, "消灭1个对手场上装备。", "游侠"),
    Card("R-018", "门神", "Gatekeeper", "runner", 3, 1, 4, "将1个对手场上游侠返回其控制者手牌。", "游侠"),
    Card("R-020", "快手", "Quickhand", "runner", 2, 2, 1, "抽1张牌，然后将手牌牌库顶1张置底。", "游侠"),
    Card("R-021", "中间人", "Broker", "runner", 3, 1, 2, "查看1名对手的手牌。然后抽1张牌。", "游侠"),
    Card("R-023", "清道夫", "Scavenger", "runner", 5, 3, 3, "消灭1个对手场上游侠或装备。", "游侠"),
]

GEARS: list[Card] = [
    Card("G-001", "回收沙盒", "Recovery Sandbox", "gear", 3, None, None, "将1个对手场上游侠返回其控制者手牌牌库底。", "一次性物品 / 设备 / 回收", "one_shot"),
    Card("G-002", "侦测芯片", "Scan Chip", "gear", 1, None, None, "查看1名对手的手牌。", "一次性物品 / 程序芯片 / 侦测", "one_shot"),
    Card("G-003", "权限束带", "Access Bind", "gear", 2, None, None, "将1个对手场上游侠返回其控制者手牌。", "一次性物品 / 工具 / 束缚", "one_shot"),
    Card("G-004", "撤离信标", "Evac Beacon", "gear", 2, None, None, "将1个己方场上游侠返回你的手牌。", "一次性物品 / 设备 / 撤离", "one_shot"),
    Card("G-005", "强制回滚扇区", "Forced Rollback", "gear", 5, None, None, "将1座节点中的所有对手游侠返回各自控制者手牌。", "一次性物品 / 一次性回滚指令", "one_shot"),
    Card("G-006", "蜂鸟无人机", "Hummingbird Drone", "gear", 1, 0, 1, "查看任意手牌牌库顶1张。", "装备 / 设备 / 侦察", "equipment"),
    Card("G-007", "回收地堡", "Recovery Bunker", "gear", 4, 1, 5, "将1个己方场上装备返回你的手牌。", "装备 / 设备 / 回收工事", "equipment"),
    Card("G-008", "牵引锚", "Pull Anchor", "gear", 2, None, None, "将1个对手场上装备返回其控制者手牌。", "一次性物品 / 工具 / 牵引", "one_shot"),
    Card("G-009", "探针", "Probe", "gear", 1, None, None, "查看手牌牌库顶2张。", "一次性物品 / 工具 / 侦察", "one_shot"),
    Card("G-010", "算盘", "Abacus", "gear", 2, None, None, "查看手牌牌库顶3张，任意排序后放回。", "一次性物品 / 工具 / 演算", "one_shot"),
    Card("G-011", "数据抽取器", "Data Extractor", "gear", 1, None, None, "抽1张牌。", "一次性物品 / 工具 / 抽取", "one_shot"),
    Card("G-012", "弹弓", "Slingshot", "gear", 2, None, None, "将1个对手场上游侠返回其控制者手牌。", "一次性物品 / 武器 / 牵引", "one_shot"),
]

NODES: list[Node] = [
    Node("N-001", "档案馆", "行政域", 1, "查看1个节点的信息。", "查看节点信息"),
    Node("N-002", "中继站", "行政域", 2, "使1个节点在线。", "使节点在线"),
    Node("N-003", "屏蔽塔", "行政域", 2, "使1个节点离线。", "使节点离线"),
    Node("N-004", "防火墙", "安防域", 2, "保护1个节点1回合。", "保护节点"),
    Node("N-005", "传送站", "安防域", 1, "移动1个己方游侠到1个节点。", "移动己方游侠"),
    Node("N-006", "货运站", "安防域", 1, "移动1个己方装备到1个节点。", "移动己方装备"),
    Node("N-007", "蜂鸟巢", "生化域", 1, "选择1个己方游侠，本回合攻击力+1。", "强化游侠攻击"),
    Node("N-008", "纳米诊所", "生化域", 1, "选择1个己方游侠，本回合生命值+1。", "强化游侠生命"),
    Node("N-009", "复苏舱", "生化域", 2, "从弃牌区将1张游侠加入手牌。", "回收游侠"),
    Node("N-010", "武研所", "科研域", 1, "选择1个己方装备，本回合攻击力+1。", "强化装备攻击"),
    Node("N-011", "装研所", "科研域", 1, "选择1个己方装备，本回合生命值+1。", "强化装备生命"),
    Node("N-012", "回收中心", "科研域", 2, "从弃牌区将1张装备加入手牌。", "回收装备"),
]


# ============================================================
# 审核函数
# ============================================================

def detect_actions(text: str) -> set[str]:
    return {action for action, detector in ACTION_DETECTORS.items() if detector(text)}


def classify_strength(card: Card) -> str:
    text = card.text
    actions = detect_actions(text)
    if "消灭" in text and "或" in text:
        return "destroy_flexible"
    if "消灭" in text:
        return "destroy_single"
    if "所有" in text or "至多2个" in text:
        return "multi_return"
    if "返回" in text and "牌库" in text:
        return "return_to_deck"
    if "返回" in text and "手牌" in text:
        return "standard_return"
    if "抽" in text and ("查看" in text or "排序" in text or "置底" in text):
        return "standard_info_combo"
    if "查看" in text or "抽" in text or "重洗" in text:
        return "light_info" if card.cost <= 1 else "standard_info_combo"
    if card.gear_form == "equipment" and card.cost >= 4:
        return "equipment_heavy"
    return "standard_info_combo"


def audit_removed_actions(card: Card, ctx: AuditContext) -> None:
    for label, pattern in REMOVED_ACTION_PATTERNS.items():
        if re.search(pattern, card.text):
            ctx.fail(card.card_id, f"技能含已移除动作：{label}。技能：{card.text}")
            return
    ctx.pass_(card.card_id, "未发现已移除动作。")


def audit_base_actions(card: Card, ctx: AuditContext) -> None:
    actions = detect_actions(card.text)
    if not actions:
        ctx.fail(card.card_id, f"技能无法拆解为基础动作：{card.text}")
        return
    unknown_fragments = []
    # 允许的非基础限制词：目标、数量、区域、时机。
    allowed_terms = ["选择", "1名", "1个", "1张", "至多", "所有", "对手", "己方", "场上", "控制者", "你的", "节点", "回合", "此牌", "被"]
    if "使" in card.text and not any(term in card.text for term in allowed_terms):
        unknown_fragments.append("可能存在未拆解动作：使")
    if unknown_fragments:
        ctx.warn(card.card_id, "；".join(unknown_fragments))
    ctx.pass_(card.card_id, f"可拆解为基础动作：{', '.join(BASE_ACTIONS[a] for a in sorted(actions))}。")


def audit_name_intuition(card: Card, ctx: AuditContext) -> None:
    actions = detect_actions(card.text)
    matched_any = False
    for pattern, expected_actions, explanation in NAME_INTUITION_RULES:
        if re.search(pattern, card.name):
            matched_any = True
            if actions & expected_actions:
                ctx.pass_(card.card_id, f"卡名与技能直觉匹配：{explanation}")
            else:
                ctx.fail(card.card_id, f"卡名与技能不匹配：{explanation} 当前动作={actions or '未识别'}，技能={card.text}")
            return
    if not matched_any:
        ctx.warn(card.card_id, f"没有命中卡名直觉规则，需要人工复核：{card.name} / {card.text}")


def audit_carrier(card: Card, ctx: AuditContext) -> None:
    text = card.text
    if card.kind == "runner":
        if card.gear_form != "none":
            ctx.fail(card.card_id, "游侠不应带 gear_form。")
        if any(word in card.name for word in ["芯片", "沙盒", "锚", "无人机", "地堡", "信标", "算盘", "抽取器", "弹弓"]):
            ctx.warn(card.card_id, "卡名可能更像物品，需人工确认是否仍应为游侠。")
        else:
            ctx.pass_(card.card_id, "游侠载体基本合理。")
    elif card.kind == "gear":
        if card.gear_form == "one_shot":
            if card.atk is not None or card.hp is not None:
                ctx.fail(card.card_id, "一次性物品不应有 ATK / HP。")
            elif any(word in card.name for word in ["地堡", "无人机"]):
                ctx.warn(card.card_id, "一次性物品名称像可部署装备，需人工确认。")
            else:
                ctx.pass_(card.card_id, "一次性物品载体合理。")
        elif card.gear_form == "equipment":
            if card.atk is None or card.hp is None:
                ctx.fail(card.card_id, "装备必须有 ATK / HP。")
            elif "返回你的手牌" in text and "装备" in text and "回收" not in card.name:
                ctx.warn(card.card_id, "装备回收技能建议卡名包含回收、维修、仓库、地堡等直觉词。")
            else:
                ctx.pass_(card.card_id, "装备载体合理。")


def audit_stats(card: Card, ctx: AuditContext) -> None:
    if card.cost < 0:
        ctx.fail(card.card_id, "费用不能为负数。")
        return
    if card.kind == "runner":
        if card.atk is None or card.hp is None:
            ctx.fail(card.card_id, "游侠必须有 ATK / HP。")
            return
        if card.atk < 0 or card.hp <= 0:
            ctx.fail(card.card_id, "游侠 ATK 不能为负，HP 必须大于0。")
            return
    if card.gear_form == "equipment":
        if card.atk is None or card.hp is None:
            ctx.fail(card.card_id, "装备必须有 ATK / HP。")
            return
        if card.atk < 0 or card.hp <= 0:
            ctx.fail(card.card_id, "装备 ATK 不能为负，HP 必须大于0。")
            return
    strength = classify_strength(card)
    min_cost, max_cost = COST_BANDS.get(strength, (1, 5))
    if not (min_cost <= card.cost <= max_cost):
        ctx.warn(card.card_id, f"费用可能不符合强度层级：强度={strength}，建议={min_cost}-{max_cost}，当前={card.cost}。")
    else:
        ctx.pass_(card.card_id, f"费用符合强度层级：{strength}。")


def audit_wording(card: Card, ctx: AuditContext) -> None:
    text = card.text
    if "处理" in text:
        ctx.fail(card.card_id, "卡面不使用旧词“处理”，应使用“消灭”。")
    if "吹回" in text or "脱离接入" in text or "权限驱逐" in text:
        ctx.fail(card.card_id, "卡面不使用吹回 / 脱离接入 / 权限驱逐，规则文本应写返回手牌或返回牌库。")
    if "牌库" in text and "手牌牌库" not in text and "控制者手牌牌库" not in text:
        ctx.warn(card.card_id, "牌库表述可能不够精确，建议明确“手牌牌库”或“控制者手牌牌库”。")
    if "然后" in text:
        ctx.warn(card.card_id, "组合技能含“然后”，需确认卡面空间和结算顺序清晰。")
    else:
        ctx.pass_(card.card_id, "卡面文案无明显旧术语。")


def audit_node_fee(node: Node, ctx: AuditContext) -> None:
    text = node.text
    expected = 1
    reason = "默认轻量技能。"
    if "在线" in text or "离线" in text or "保护" in text or "弃牌区" in text:
        expected = 2
        reason = "在线 / 离线 / 保护 / 回收类节点技能建议 2 电荷。"
    if "直接获胜" in text or "Root Access" in text:
        expected = 3
        reason = "直接影响胜利条件的技能建议 3 电荷以上。"
    if node.skill_fee != expected:
        ctx.warn(node.node_id, f"节点费用可能不匹配：当前={node.skill_fee}，建议={expected}。{reason}")
    else:
        ctx.pass_(node.node_id, f"节点费用合理：{node.skill_fee}电荷。{reason}")


def audit_card(card: Card, ctx: AuditContext) -> None:
    audit_removed_actions(card, ctx)
    audit_base_actions(card, ctx)
    audit_name_intuition(card, ctx)
    audit_carrier(card, ctx)
    audit_stats(card, ctx)
    audit_wording(card, ctx)


def audit_all(cards: Iterable[Card], nodes: Iterable[Node]) -> AuditContext:
    ctx = AuditContext()
    for card in cards:
        audit_card(card, ctx)
    for node in nodes:
        audit_node_fee(node, ctx)
    return ctx


# ============================================================
# 报告输出
# ============================================================

def print_report(ctx: AuditContext) -> int:
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for finding in ctx.findings:
        counts[finding.severity] += 1

    print("归零者 / ZERO ACCESS｜类人 AI 技能自动测试报告")
    print("=" * 64)
    print(f"PASS: {counts['PASS']}  WARN: {counts['WARN']}  FAIL: {counts['FAIL']}")
    print("=" * 64)

    for severity in ["FAIL", "WARN", "PASS"]:
        group = [f for f in ctx.findings if f.severity == severity]
        if not group:
            continue
        print(f"\n[{severity}]")
        for finding in group:
            print(f"- {finding.subject}: {finding.message}")

    return 1 if counts["FAIL"] else 0


def main() -> int:
    cards = [*RUNNERS, *GEARS]
    ctx = audit_all(cards, NODES)
    return print_report(ctx)


if __name__ == "__main__":
    sys.exit(main())
