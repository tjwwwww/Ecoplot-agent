# -*- coding: utf-8 -*-
"""
interpretive_rule_engine.py
==========================

文献解释规则检索引擎。

规则来源当前为 rule.md。这里的规则不是确定性计算公式，也不是自动诊断处方，
而是用于辅助解释的文献经验规则。智能体应先调用指标/数据工具获得事实，
再用本工具检索适用规则作为解释依据和边界说明。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RULE_PATH = BASE_DIR / "rule.md"


@dataclass
class InterpretiveRule:
    rule_id: str
    category: str
    name: str
    if_text: str
    then_text: str
    description: str
    applicable_taxa: str
    applicable_region: str
    threshold: str
    evidence_text: str
    rule_type: str = "literature_heuristic"
    evidence_level: str = "literature_reference"
    formal_result_available: bool = False


def retrieve_interpretive_rules(
    query_text: str = "",
    species: str = "",
    rule_category: str = "",
    target_context: Optional[Dict[str, Any]] = None,
    limit: int = 5,
    rule_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """检索与问题/对象上下文相关的解释规则。"""

    target_context = target_context or {}
    rules = load_interpretive_rules(rule_path)
    if not rules:
        return {
            "status": "not_found",
            "message": "未找到可用解释规则。请确认 rule.md 存在且包含规则编号。",
        }

    scored = []
    for rule in rules:
        score, reasons = _score_rule(rule, query_text, species, rule_category, target_context)
        if score > 0:
            payload = asdict(rule)
            payload["match_score"] = score
            payload["match_reasons"] = reasons
            payload["contextual_applicability"] = _assess_contextual_applicability(rule, target_context)
            scored.append(payload)

    scored.sort(key=lambda item: item["match_score"], reverse=True)
    return {
        "status": "success" if scored else "not_found",
        "query_text": query_text,
        "species": species or target_context.get("species") or "",
        "rule_category": rule_category,
        "matched_rule_count": len(scored),
        "rules": scored[: max(1, int(limit or 5))],
        "result_boundary": "这些规则是文献经验解释规则，不是确定性计算结果；应与实测指标、地形/气候数据和现场核查一起使用，不能单独推出因果或处方结论。",
    }


def tool_retrieve_interpretive_rules(
    query_text: str = "",
    species: str = "",
    rule_category: str = "",
    target_context_json: str = "{}",
    limit: int = 5,
) -> str:
    """OpenAI tool 兼容包装。"""

    try:
        target_context = json.loads(target_context_json) if target_context_json else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"status": "failed", "error_code": "INVALID_JSON", "message": str(exc)}, ensure_ascii=False)
    result = retrieve_interpretive_rules(query_text, species, rule_category, target_context, limit)
    return json.dumps(result, ensure_ascii=False)


@lru_cache(maxsize=4)
def load_interpretive_rules(rule_path: Optional[str | Path] = None) -> List[InterpretiveRule]:
    path = Path(rule_path or DEFAULT_RULE_PATH)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return parse_rule_markdown(text)


def parse_rule_markdown(text: str) -> List[InterpretiveRule]:
    blocks = re.split(r"(?=规则编号：)", text)
    rules: List[InterpretiveRule] = []
    seen_rule_ids: set[str] = set()
    for block in blocks:
        if not block.strip().startswith("规则编号："):
            continue
        rule_id = _extract_line(block, "规则编号")
        if not rule_id or rule_id in seen_rule_ids:
            continue
        seen_rule_ids.add(rule_id)
        category = _extract_line(block, "规则类别")
        name = _extract_line(block, "规则名称")
        description = _extract_line(block, "规则说明")
        taxa = _extract_line(block, "适用树种")
        region = _extract_line(block, "适用区域")
        threshold = _extract_line(block, "阈值")
        evidence = _extract_line(block, "证据原文")
        if_text = _extract_section(block, ["IF", "IF（条件1）"], ["THEN", "规则说明", "IF（条件2）"])
        then_text = _extract_section(block, ["THEN"], ["规则说明", "适用树种", "阈值", "证据原文"])
        rules.append(InterpretiveRule(
            rule_id=rule_id,
            category=category,
            name=name,
            if_text=if_text,
            then_text=then_text,
            description=description,
            applicable_taxa=taxa,
            applicable_region=region,
            threshold=threshold,
            evidence_text=evidence,
        ))
    return rules


def _extract_line(block: str, label: str) -> str:
    pattern = rf"^{re.escape(label)}：\s*(.+?)\s*$"
    match = re.search(pattern, block, flags=re.MULTILINE)
    return match.group(1).strip().strip('"') if match else ""


def _extract_section(block: str, starts: List[str], stops: List[str]) -> str:
    start_pattern = "|".join(re.escape(s) for s in starts)
    stop_pattern = "|".join(re.escape(s) for s in stops)
    pattern = rf"(?:{start_pattern})：?\s*\n(?P<body>.*?)(?=^(?:{stop_pattern})：?|\Z)"
    match = re.search(pattern, block, flags=re.MULTILINE | re.DOTALL)
    return match.group("body").strip() if match else ""


def _score_rule(rule: InterpretiveRule, query_text: str, species: str, category: str, context: Dict[str, Any]) -> tuple[int, List[str]]:
    haystack = "\n".join([
        rule.rule_id,
        rule.category,
        rule.name,
        rule.if_text,
        rule.then_text,
        rule.description,
        rule.applicable_taxa,
        rule.applicable_region,
        rule.threshold,
    ])
    query = query_text or ""
    target_species = species or str(context.get("species") or context.get("taxon") or "")
    score = 0
    reasons: List[str] = []

    if target_species and target_species in haystack:
        score += 5
        reasons.append(f"树种匹配：{target_species}")
    if category and category in rule.category:
        score += 4
        reasons.append(f"规则类别匹配：{category}")

    keyword_map = {
        "气候": ["气候", "温度", "降水", "干旱", "SPEI", "生长季", "变暖"],
        "地形": ["地形", "海拔", "坡度", "坡向", "高海拔", "低海拔"],
        "竞争": ["竞争", "竞争指数", "胸径", "密度", "冠幅"],
        "生长": ["生长", "径向生长", "树轮", "衰退", "恢复力", "抵抗力"],
    }
    for reason_name, keywords in keyword_map.items():
        if any(k in query for k in keywords) and any(k in haystack for k in keywords):
            score += 2
            reasons.append(f"主题匹配：{reason_name}")

    for token in _query_tokens(query):
        if len(token) >= 2 and token in haystack:
            score += 1

    dbh = _float(context.get("tree_dbh_cm") or context.get("dbh_cm"))
    if rule.rule_id == "TR-003" and dbh is not None:
        if dbh < 12 or dbh > 20:
            score += 6
            reasons.append("胸径阈值匹配 TR-003")
    elevation = _float(context.get("elevation_m"))
    if elevation is not None and "3000" in rule.threshold:
        if ("< 3000" in rule.threshold or "3000m以下" in rule.applicable_region) and elevation < 3000:
            score += 3
            reasons.append("海拔阈值匹配：<3000m")
        if (">= 3000" in rule.threshold or "3000m以上" in rule.applicable_region) and elevation >= 3000:
            score += 3
            reasons.append("海拔阈值匹配：>=3000m")

    return score, reasons


def _assess_contextual_applicability(rule: InterpretiveRule, context: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[str] = []
    flags: List[str] = []
    species = str(context.get("species") or context.get("taxon") or "")
    if species:
        checks.append("species_matched" if species in rule.applicable_taxa or species in rule.if_text else "species_not_explicitly_matched")
    dbh = _float(context.get("tree_dbh_cm") or context.get("dbh_cm"))
    if rule.rule_id == "TR-003" and dbh is not None:
        if dbh < 12:
            checks.append("dbh_lt_12_small_tree_case")
        elif dbh > 20:
            checks.append("dbh_gt_20_large_tree_case")
        else:
            flags.append("DBH_BETWEEN_RULE_THRESHOLDS")
    if not checks:
        flags.append("CONTEXT_INSUFFICIENT_FOR_RULE_CONDITION_CHECK")
    return {
        "applicability_status": "conditional" if flags else "matched_or_relevant",
        "checks": checks,
        "quality_flags": flags,
    }


def _query_tokens(text: str) -> List[str]:
    return re.findall(r"[\u4e00-\u9fa5A-Za-z0-9_.-]+", text or "")


def _float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    print(tool_retrieve_interpretive_rules("青海云杉小径木竞争压力", "青海云杉", "树木规则", '{"tree_dbh_cm": 10}', 3))
