# -*- coding: utf-8 -*-
"""
semantic_planner.py
===================
把开放自然语言问题归一为有限 SemanticPlan。

核心思想：
- 不穷举用户话术；
- 把 ontology_retriever 召回的候选实体/指标/工具给 LLM 看；
- 让 LLM 自主判断目标对象、任务意图、范围和所需能力；
- 后端再做 plan_validator 校验，避免乱调用工具。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

try:
    from provider import chat_with_tools
except Exception:  # pragma: no cover
    chat_with_tools = None  # type: ignore

BASE_DIR = Path(__file__).resolve().parent

PLAN_SCHEMA_VERSION = "0.1.0"

ALLOWED_ROUTES = {
    "direct_answer",
    "ontology_explanation",
    "domain_analysis",
    "visualization",
    "report_generation",
    "clarify",
}
ALLOWED_INTENTS = {
    "general_chat",
    "concept_explanation",
    "indicator_explanation",
    "object_status_analysis",
    "object_comparison",
    "field_inspection_planning",
    "visualization_generation",
    "evidence_trace",
    "report_generation",
    "unsupported_or_not_evaluated",
}
ALLOWED_TARGET_TYPES = {
    "General",
    "MonitoringPlot",
    "Subplot",
    "TreeIndividual",
    "Taxon",
    "IndicatorDefinition",
    "FormulaDefinition",
    "VariableDefinition",
    "DiagnosticRule",
    "DiagnosticSignal",
    "ClimateExposure",
    "SiteCondition",
}


def _json_dumps(value: Any, max_chars: int = 9000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    return text if len(text) <= max_chars else text[:max_chars] + "\n...（已截断）"


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except Exception:
            return None
    return None


def _load_capabilities() -> Dict[str, Any]:
    path = BASE_DIR / "capability_registry.yaml"
    if yaml is None or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_simple_general_question(question: str) -> bool:
    q = str(question or "").strip()
    if not q:
        return True
    simple_patterns = [
        "你是谁", "你能做什么", "怎么用", "这个系统", "本体是什么", "知识图谱是什么",
        "为什么不能", "有什么区别", "是否可以", "应该怎么", "路线", "架构",
    ]
    # 包含具体实体/指标时，不直接回答
    concrete_patterns = [r"\d{4}", r"QLS\d+", "样方", "单木", "乌柳", "青海云杉", "红桦", "白桦", "山杨"]
    if any(re.search(p, q, flags=re.I) for p in concrete_patterns):
        return False
    return any(p in q for p in simple_patterns) and not any(w in q for w in ["画", "图", "报告", "清单", "核查"])


def _fallback_plan(question: str, context: Optional[Dict[str, Any]], candidates: Dict[str, Any]) -> Dict[str, Any]:
    q = str(question or "")
    context = dict(context or {})
    entity_candidates = candidates.get("entity_candidates") or []
    indicator_candidates = candidates.get("indicator_candidates") or []
    formula_candidates = candidates.get("formula_candidates") or []

    route = "domain_analysis"
    intent = "object_status_analysis"
    target = {"type": "MonitoringPlot", "name": "祁连山24公顷大样地", "id": "QILIAN_24HA", "confidence": 0.45}
    scope = {"type": "MonitoringPlot", "id": "QILIAN_24HA", "reason": "默认全样地范围"}
    dimensions = ["overview", "structure", "relative_signals", "inspection_priority"]

    if _is_simple_general_question(q):
        route = "direct_answer"
        intent = "general_chat"
        target = {"type": "General", "name": "general", "id": None, "confidence": 0.7}
        dimensions = []

    if any(w in q for w in ["报告", "文档", "汇报", "导出", "任务单"]):
        route = "report_generation"
        intent = "report_generation"
    elif any(w in q for w in ["画", "图", "分布", "箱线", "散点", "空间"]):
        route = "visualization"
        intent = "visualization_generation"

    if any(w in q for w in ["公式", "怎么算", "含义", "是什么", "代表", "指标", "HDR", "Hegyi", "高径比"]):
        if indicator_candidates:
            c = indicator_candidates[0]
            target = {"type": c.get("type", "IndicatorDefinition"), "name": c.get("name"), "id": c.get("id"), "confidence": c.get("score", 0.7)}
            route = "ontology_explanation"
            intent = "indicator_explanation"
            dimensions = ["definition", "formula", "inputs", "boundary"]
        elif formula_candidates:
            c = formula_candidates[0]
            target = {"type": "FormulaDefinition", "name": c.get("name"), "id": c.get("id"), "confidence": c.get("score", 0.7)}
            route = "ontology_explanation"
            intent = "indicator_explanation"
            dimensions = ["expression", "inputs", "outputs", "tool_binding"]

    # 优先使用用户文本中明确召回的实体；page_context 只是 ambient，不强制覆盖
    explicit_entities = [c for c in entity_candidates if c.get("source") != "page_context"]
    if explicit_entities and route not in {"direct_answer"}:
        c = explicit_entities[0]
        t = c.get("type") or "General"
        target = {"type": t, "name": c.get("name"), "id": c.get("id"), "confidence": c.get("score", 0.8)}
        if t == "Subplot":
            scope = {"type": "Subplot", "id": c.get("id") or c.get("name"), "reason": "用户明确提到样方"}
            dimensions = ["stand_structure", "species_composition", "morphology", "competition", "site_context", "inspection_priority"]
        elif t == "TreeIndividual":
            scope = {"type": "TreeIndividual", "id": c.get("id") or c.get("name"), "reason": "用户明确提到单木"}
            dimensions = ["history_record", "morphology", "competition", "peer_percentile", "field_checklist"]
        elif t == "Taxon":
            scope = {"type": "MonitoringPlot", "id": "QILIAN_24HA", "reason": "用户未指定样方，默认全样地树种分析"}
            dimensions = ["abundance", "distribution_across_subplots", "size_structure", "morphology", "site_context", "inspection_priority"]

    if any(w in q for w in ["外业", "现场", "复测", "核查", "看什么", "候选"]):
        intent = "field_inspection_planning"
        if route == "domain_analysis":
            dimensions.append("field_checklist")

    return make_default_plan(question=q, route=route, intent=intent, target=target, scope=scope, dimensions=dimensions)


def make_default_plan(question: str, route: str, intent: str, target: Dict[str, Any], scope: Dict[str, Any], dimensions: List[str]) -> Dict[str, Any]:
    need_chart = route == "visualization"
    need_report = route == "report_generation"
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "route": route,
        "intent": intent,
        "target": target,
        "scope": scope,
        "dimensions": dimensions,
        "output": {
            "answer_type": "chart_answer" if need_chart else "report" if need_report else "diagnostic_answer" if route == "domain_analysis" else "chat_answer",
            "need_chart": need_chart,
            "need_report": need_report,
            "chart_request": {},
        },
        "evidence_needs": {
            "ontology": route in {"ontology_explanation", "domain_analysis", "visualization", "report_generation"},
            "database": route in {"domain_analysis", "visualization", "report_generation"},
            "formula": route in {"ontology_explanation", "domain_analysis", "visualization", "report_generation"},
            "kg": "optional",
            "climate": "optional",
            "literature": "optional",
        },
        "missing_context": [],
        "boundary_level": "relative_attention_signal",
        "planner_note": "fallback_rule_plan",
    }


class SemanticPlanner:
    def __init__(self) -> None:
        self.capabilities = _load_capabilities()

    def plan(self, question: str, context: Optional[Dict[str, Any]], candidates: Dict[str, Any], history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        fallback = _fallback_plan(question, context, candidates)
        # 简单问题直接返回，不增加LLM开销
        if fallback.get("route") == "direct_answer":
            return validate_plan(fallback, candidates=candidates, context=context)

        if chat_with_tools is None:
            return validate_plan(fallback, candidates=candidates, context=context)

        system = (
            "你是森林大样地野外调查智能体的语义规划器。"
            "你的任务不是回答用户，而是把自然语言问题归一为固定 JSON SemanticPlan。"
            "不要穷举用户问法；请根据本体候选、数据库实体候选和能力列表判断目标对象、任务意图和分析范围。"
            "页面上下文只是 ambient context，只有用户说‘这个样方/这里/它/里面’等指代词时才优先使用。"
            "只输出 JSON，不要输出解释。"
        )
        user_payload = {
            "question": question,
            "page_context": context or {},
            "retrieved_candidates": candidates,
            "capability_registry": self.capabilities,
            "allowed_routes": sorted(ALLOWED_ROUTES),
            "allowed_intents": sorted(ALLOWED_INTENTS),
            "allowed_target_types": sorted(ALLOWED_TARGET_TYPES),
            "required_json_shape": {
                "schema_version": PLAN_SCHEMA_VERSION,
                "route": "direct_answer | ontology_explanation | domain_analysis | visualization | report_generation | clarify",
                "intent": "general_chat | concept_explanation | indicator_explanation | object_status_analysis | object_comparison | field_inspection_planning | visualization_generation | evidence_trace | report_generation | unsupported_or_not_evaluated",
                "target": {"type": "本体对象类型", "name": "对象名", "id": "可空", "confidence": 0.0},
                "scope": {"type": "MonitoringPlot/Subplot/TreeIndividual/...", "id": "可空", "reason": "范围判断依据"},
                "dimensions": ["需要分析的维度"],
                "output": {"answer_type": "chat_answer/diagnostic_answer/chart_answer/report", "need_chart": False, "need_report": False, "chart_request": {}},
                "evidence_needs": {"ontology": True, "database": True, "formula": "optional", "kg": "optional", "climate": "optional", "literature": "optional"},
                "missing_context": [],
                "boundary_level": "formal_result | relative_attention_signal | heuristic | not_evaluated",
            },
        }
        try:
            response = chat_with_tools(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": _json_dumps(user_payload)},
                ],
                tools=[],
            )
            content = ""
            if isinstance(response, dict):
                content = str(response.get("content") or response.get("message", {}).get("content") or "")
            else:
                try:
                    content = str(response.choices[0].message.content or "")
                except Exception:
                    content = str(response or "")
            parsed = _extract_json(content)
            if parsed:
                parsed.setdefault("planner_note", "llm_plan")
                return validate_plan(parsed, candidates=candidates, context=context, fallback=fallback)
        except Exception as exc:
            fallback["planner_warning"] = f"LLM规划失败，使用规则回退：{exc}"
        return validate_plan(fallback, candidates=candidates, context=context)


def validate_plan(
    plan: Dict[str, Any],
    candidates: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """后端校验计划，避免不在本体/能力边界中的路线和对象类型。"""
    base = fallback or _fallback_plan("", context, candidates or {})
    if not isinstance(plan, dict):
        plan = base
    plan.setdefault("schema_version", PLAN_SCHEMA_VERSION)
    if plan.get("route") not in ALLOWED_ROUTES:
        plan["route"] = base.get("route", "direct_answer")
    if plan.get("intent") not in ALLOWED_INTENTS:
        plan["intent"] = base.get("intent", "general_chat")
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    if target.get("type") not in ALLOWED_TARGET_TYPES:
        target["type"] = base.get("target", {}).get("type", "General")
    target.setdefault("name", base.get("target", {}).get("name", "general"))
    target.setdefault("id", base.get("target", {}).get("id"))
    try:
        target["confidence"] = float(target.get("confidence", base.get("target", {}).get("confidence", 0.5)))
    except Exception:
        target["confidence"] = 0.5
    plan["target"] = target

    scope = plan.get("scope") if isinstance(plan.get("scope"), dict) else {}
    scope.setdefault("type", base.get("scope", {}).get("type", "MonitoringPlot"))
    scope.setdefault("id", base.get("scope", {}).get("id", "QILIAN_24HA"))
    scope.setdefault("reason", base.get("scope", {}).get("reason", "默认范围"))
    plan["scope"] = scope

    if not isinstance(plan.get("dimensions"), list):
        plan["dimensions"] = base.get("dimensions", [])
    output = plan.get("output") if isinstance(plan.get("output"), dict) else {}
    output.setdefault("need_chart", plan.get("route") == "visualization")
    output.setdefault("need_report", plan.get("route") == "report_generation")
    output.setdefault("answer_type", "chart_answer" if output["need_chart"] else "report" if output["need_report"] else "diagnostic_answer" if plan.get("route") == "domain_analysis" else "chat_answer")
    output.setdefault("chart_request", {})
    plan["output"] = output
    if not isinstance(plan.get("evidence_needs"), dict):
        plan["evidence_needs"] = base.get("evidence_needs", {})
    if not isinstance(plan.get("missing_context"), list):
        plan["missing_context"] = []
    plan.setdefault("boundary_level", "relative_attention_signal")
    return plan


_DEFAULT_PLANNER: Optional[SemanticPlanner] = None


def get_default_planner() -> SemanticPlanner:
    global _DEFAULT_PLANNER
    if _DEFAULT_PLANNER is None:
        _DEFAULT_PLANNER = SemanticPlanner()
    return _DEFAULT_PLANNER


def create_semantic_plan(question: str, context: Optional[Dict[str, Any]], candidates: Dict[str, Any], history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return get_default_planner().plan(question, context=context, candidates=candidates, history=history)
