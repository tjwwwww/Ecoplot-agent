# -*- coding: utf-8 -*-
"""
domain_analysis_engine.py
=========================
按 SemanticPlan 执行领域分析。

定位：
- LLM 负责理解和表达；
- 本模块负责稳定调用数据库、公式/指标工具、诊断规则和图表生成；
- 不把所有 legacy tools 直接暴露给模型，而是根据 plan 有边界地执行。
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import forestry_spatial_tools as fst

BASE_DIR = Path(__file__).resolve().parent
VISUALIZATION_DIR = BASE_DIR / "visualizations"
VISUALIZATION_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_SUBPLOT_AREA_HA = float(os.getenv("FORESTRY_SUBPLOT_AREA_HA", "0.04"))


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except Exception:
        return str(value)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    return v if math.isfinite(v) else None


def _summary(values: Sequence[Any]) -> Dict[str, Any]:
    vals = sorted(v for v in (_safe_float(x) for x in values) if v is not None)
    if not vals:
        return {"count": 0, "min": None, "p25": None, "median": None, "mean": None, "p75": None, "max": None}
    def q(p: float) -> float:
        if len(vals) == 1:
            return vals[0]
        pos = (len(vals) - 1) * p
        lo = int(math.floor(pos)); hi = int(math.ceil(pos))
        if lo == hi:
            return vals[lo]
        return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)
    return {
        "count": len(vals),
        "min": round(vals[0], 4),
        "p25": round(q(0.25), 4),
        "median": round(q(0.5), 4),
        "mean": round(sum(vals) / len(vals), 4),
        "p75": round(q(0.75), 4),
        "max": round(vals[-1], 4),
    }


def _percentile_rank(value: Optional[float], population: Sequence[Any]) -> Optional[float]:
    if value is None:
        return None
    vals = sorted(v for v in (_safe_float(x) for x in population) if v is not None)
    if not vals:
        return None
    less = sum(v < value for v in vals)
    equal = sum(v == value for v in vals)
    return round(100.0 * (less + 0.5 * equal) / len(vals), 1)


def _get_db_path() -> Path:
    env_path = os.getenv("FORESTRY_SQLITE_DB")
    if env_path:
        return Path(env_path)
    try:
        return Path(getattr(fst, "DB_PATH", BASE_DIR / "data" / "qilian_forest.db"))
    except Exception:
        return BASE_DIR / "data" / "qilian_forest.db"


def _connect() -> sqlite3.Connection:
    path = _get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    if not _table_exists(conn, table):
        return []
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _rows_to_dicts(rows: Sequence[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def _calc_hdr(row: Dict[str, Any]) -> Optional[float]:
    dbh = _safe_float(row.get("tree_dbh_cm"))
    h = _safe_float(row.get("tree_height_m"))
    if dbh and h and dbh > 0:
        return 100.0 * h / dbh
    return None


def _call_legacy_tool(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """按名称安全调用 legacy agent 的已注册工具，不把整个工具列表交给 LLM。"""
    try:
        import agent as legacy_agent  # type: ignore
        registry = getattr(legacy_agent, "TOOL_REGISTRY", {})
        spec = registry.get(tool_name)
        if spec is None:
            return {"status": "not_available", "tool_id": tool_name, "error": f"legacy工具不存在：{tool_name}"}
        if getattr(spec, "enabled", True) is False:
            return {"status": "disabled", "tool_id": tool_name, "error": getattr(spec, "disabled_reason", "工具已禁用")}
        result = spec.handler(args)
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                result = {"raw": result}
        if isinstance(result, dict):
            result.setdefault("status", result.get("status", "success"))
            result.setdefault("tool_id", tool_name)
            return result
        return {"status": "success", "tool_id": tool_name, "data": result}
    except Exception as exc:
        return {"status": "failed", "tool_id": tool_name, "error": str(exc)}


def _species_composition(conn: sqlite3.Connection, where: str = "1=1", params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    cols = _table_columns(conn, "tree_observations")
    if "species" not in cols:
        return []
    rows = conn.execute(
        f"SELECT species, COUNT(*) AS tree_count, AVG(tree_dbh_cm) AS mean_dbh_cm, AVG(tree_height_m) AS mean_height_m "
        f"FROM tree_observations WHERE {where} AND species IS NOT NULL AND TRIM(species)<>'' "
        f"GROUP BY species ORDER BY tree_count DESC LIMIT 50",
        params,
    ).fetchall()
    return [
        {
            "species": str(r["species"]),
            "tree_count": int(r["tree_count"] or 0),
            "mean_dbh_cm": round(float(r["mean_dbh_cm"] or 0), 3),
            "mean_height_m": round(float(r["mean_height_m"] or 0), 3),
        }
        for r in rows
    ]


def _infer_chart_metric(question: str, plan: Dict[str, Any]) -> str:
    q = str(question or "")
    chart_req = plan.get("output", {}).get("chart_request") or {}
    metric = str(chart_req.get("metric") or chart_req.get("variable") or "").strip()
    if metric:
        return metric
    if any(w in q for w in ["HDR", "高径比", "细长"]):
        return "hdr"
    if "树高" in q or "高度" in q:
        return "tree_height_m"
    if "胸径" in q or "DBH" in q.upper():
        return "tree_dbh_cm"
    if "Hegyi" in q or "竞争" in q:
        return "hegyi_ci"
    return "hdr"


def _fetch_tree_rows(conn: sqlite3.Connection, where: str = "1=1", params: Tuple[Any, ...] = (), limit: int = 200000) -> List[Dict[str, Any]]:
    cols = _table_columns(conn, "tree_observations")
    wanted = [c for c in [
        "tree_id", "subplot_id", "species", "tree_dbh_cm", "tree_height_m",
        "tree_x_m", "tree_y_m", "crown_width_mean_m", "crown_base_height_m",
        "health_status", "remarks",
    ] if c in cols]
    if not wanted:
        return []
    rows = conn.execute(f"SELECT {', '.join(wanted)} FROM tree_observations WHERE {where} LIMIT ?", (*params, limit)).fetchall()
    out = _rows_to_dicts(rows)
    for row in out:
        hdr = _calc_hdr(row)
        row["hdr"] = round(hdr, 4) if hdr is not None else None
        h = _safe_float(row.get("tree_height_m"))
        cbh = _safe_float(row.get("crown_base_height_m"))
        row["lcr"] = round((h - cbh) / h, 4) if h and cbh is not None and 0 <= cbh <= h else None
    return out


def _load_subplot_topography_context(conn: sqlite3.Connection, subplot_ids: Sequence[str]) -> Dict[str, Any]:
    subplot_ids = [str(x).strip() for x in subplot_ids if str(x).strip()]
    if not subplot_ids:
        return {"status": "not_available", "message": "\u672a\u63d0\u4f9b\u6837\u65b9\u7f16\u53f7"}
    results: List[Dict[str, Any]] = []
    for sid in subplot_ids[:200]:
        try:
            tool_result = json.loads(fst.tool_get_subplot_topography_summary(sid))
        except Exception as exc:
            tool_result = {"status": "failed", "subplot_id": sid, "error": str(exc)}
        results.append(tool_result)
    success = [r for r in results if r.get("status") == "success"]
    return {
        "status": "success" if success else "not_available",
        "source": "tool_get_subplot_topography_summary",
        "records": success,
        "record_count": len(success),
        "attempted_count": len(results),
    }

def _load_climate_background_context(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        payload = json.loads(fst.tool_get_climate_background_summary())
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    return payload


def _load_tree_topography_context(conn: sqlite3.Connection, tree_id: str) -> Dict[str, Any]:
    try:
        payload = json.loads(fst.tool_get_tree_topography_context(tree_id))
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}
    return payload


def analyze_taxon(plan: Dict[str, Any], candidates: Dict[str, Any]) -> Dict[str, Any]:
    name = str(plan.get("target", {}).get("name") or "").strip()
    if not name:
        return {"status": "failed", "error": "Taxon 名称为空。"}
    with _connect() as conn:
        if not _table_exists(conn, "tree_observations"):
            return {"status": "failed", "error": "数据库缺少 tree_observations 表。"}
        rows = _fetch_tree_rows(conn, "species=?", (name,))
        if not rows:
            return {"status": "not_found", "error": f"没有找到树种记录：{name}"}
        subplot_ids = sorted({str(r.get("subplot_id")) for r in rows if r.get("subplot_id") is not None})
        dbhs = [r.get("tree_dbh_cm") for r in rows]
        heights = [r.get("tree_height_m") for r in rows]
        hdrs = [r.get("hdr") for r in rows]
        lcrs = [r.get("lcr") for r in rows]
        top_subplots = conn.execute(
            "SELECT subplot_id, COUNT(*) AS n, AVG(tree_dbh_cm) AS mean_dbh_cm, AVG(tree_height_m) AS mean_height_m "
            "FROM tree_observations WHERE species=? GROUP BY subplot_id ORDER BY n DESC LIMIT 15",
            (name,),
        ).fetchall()
        status_counts: List[Dict[str, Any]] = []
        cols = _table_columns(conn, "tree_observations")
        if "health_status" in cols:
            status_counts = [dict(r) for r in conn.execute(
                "SELECT COALESCE(health_status,'未记录') AS health_status, COUNT(*) AS n FROM tree_observations WHERE species=? GROUP BY health_status ORDER BY n DESC",
                (name,),
            ).fetchall()]
        high_hdr = [r for r in rows if _safe_float(r.get("hdr")) is not None and float(r["hdr"]) >= 80]
        # 相对候选树：按 HDR 排序，不等于风险概率
        candidate_trees = sorted(
            [r for r in rows if _safe_float(r.get("hdr")) is not None],
            key=lambda x: float(x.get("hdr") or 0),
            reverse=True,
        )[:10]
        topography_context = _load_subplot_topography_context(conn, subplot_ids)
        climate_context = _load_climate_background_context(conn)
        return {
            "status": "success",
            "target_type": "Taxon",
            "taxon": name,
            "scope": "MonitoringPlot" if plan.get("scope", {}).get("type") == "MonitoringPlot" else plan.get("scope"),
            "tree_count": len(rows),
            "subplot_count": len(subplot_ids),
            "subplots_preview": subplot_ids[:30],
            "top_subplots_by_count": [dict(r) for r in top_subplots],
            "size_structure": {
                "dbh_cm": _summary(dbhs),
                "height_m": _summary(heights),
            },
            "morphology": {
                "hdr": _summary(hdrs),
                "lcr": _summary(lcrs),
                "hdr_ge_80_count": len(high_hdr),
                "hdr_ge_80_ratio_pct": round(100.0 * len(high_hdr) / len(rows), 2) if rows else None,
            },
            "health_status_counts": status_counts,
            "candidate_trees_by_relative_attention": [
                {
                    "tree_id": r.get("tree_id"), "subplot_id": r.get("subplot_id"), "dbh_cm": r.get("tree_dbh_cm"),
                    "height_m": r.get("tree_height_m"), "hdr": r.get("hdr"),
                    "attention_reason": "该树种内HDR排序较高，建议作为形态核查候选；不是风折或死亡概率。",
                }
                for r in candidate_trees
            ],
            "topography_context": topography_context,
            "site_context": topography_context,
            "climate_context": climate_context,
            "field_suggestions": [
                "优先查看该树种集中分布的样方，确认是否存在地形或竞争背景差异。",
                "对HDR较高个体，现场记录倾斜、断梢、冠枯、被压、虫孔/腐朽等证据。",
                "对比同样方内其他树种，避免把树种自身形态差异误判为健康问题。",
            ],
            "interpretation_boundary": "这是基于2023年单期调查数据的树种状态画像和相对核查建议，不等于死亡概率、病虫害确诊或经营处方。",
        }


def analyze_subplot(plan: Dict[str, Any], candidates: Dict[str, Any]) -> Dict[str, Any]:
    sid = str(plan.get("target", {}).get("id") or plan.get("target", {}).get("name") or plan.get("scope", {}).get("id") or "").strip()
    if not sid:
        return {"status": "failed", "error": "样方编号为空。"}
    with _connect() as conn:
        rows = _fetch_tree_rows(conn, "subplot_id=?", (sid,))
        if not rows:
            return {"status": "not_found", "error": f"没有找到样方：{sid}"}
        species_comp = _species_composition(conn, "subplot_id=?", (sid,))
        dbhs = [r.get("tree_dbh_cm") for r in rows]
        heights = [r.get("tree_height_m") for r in rows]
        hdrs = [r.get("hdr") for r in rows]
        topography_context = _load_subplot_topography_context(conn, [sid])
        legacy_tools = {
            "stand_structure": _call_legacy_tool("tool_calc_stand_structure_metrics", {"subplot_id": sid}),
            "species_diversity": _call_legacy_tool("tool_calc_species_diversity_metrics", {"subplot_id": sid}),
            "tree_morphology": _call_legacy_tool("tool_calc_tree_morphology_metrics", {"subplot_id": sid}),
            "hegyi_competition": _call_legacy_tool("tool_calc_hegyi_competition", {"subplot_id": sid, "radius_m": 6.0}),
            "deadwood": _call_legacy_tool("tool_calc_deadwood_metrics", {"subplot_id": sid}),
            "shrub_layer": _call_legacy_tool("tool_calc_shrub_metrics", {"subplot_id": sid}),
        }
        candidate_trees = sorted(
            [r for r in rows if _safe_float(r.get("hdr")) is not None],
            key=lambda x: float(x.get("hdr") or 0),
            reverse=True,
        )[:10]
        return {
            "status": "success",
            "target_type": "Subplot",
            "subplot_id": sid,
            "tree_count": len(rows),
            "density_per_ha_simple": round(len(rows) / DEFAULT_SUBPLOT_AREA_HA, 2),
            "species_composition": species_comp,
            "structure_summary": {"dbh_cm": _summary(dbhs), "height_m": _summary(heights), "hdr": _summary(hdrs)},
            "legacy_metric_tools": legacy_tools,
            "topography_context": topography_context,
            "site_context": topography_context,
            "candidate_trees_by_hdr": [
                {"tree_id": r.get("tree_id"), "species": r.get("species"), "dbh_cm": r.get("tree_dbh_cm"), "height_m": r.get("tree_height_m"), "hdr": r.get("hdr")}
                for r in candidate_trees
            ],
            "field_suggestions": [
                "沿20m×20m样方边界确认树号和坐标匹配。",
                "重点观察高HDR或被压个体的倾斜、断梢、冠枯、机械损伤。",
                "同步记录枯死木、新增死亡、林下更新和灌木层情况。",
            ],
            "interpretation_boundary": "样方诊断为相对筛查和现场核查优先级，不是死亡、风折、病虫害或经营处方结论。",
        }


def analyze_tree(plan: Dict[str, Any], candidates: Dict[str, Any]) -> Dict[str, Any]:
    tid = str(plan.get("target", {}).get("id") or plan.get("target", {}).get("name") or plan.get("scope", {}).get("id") or "").strip()
    if not tid:
        return {"status": "failed", "error": "单木编号为空。"}
    with _connect() as conn:
        rows = _fetch_tree_rows(conn, "tree_id=?", (tid,), limit=5)
        if not rows:
            return {"status": "not_found", "error": f"没有找到单木：{tid}"}
        row = rows[0]
        species = str(row.get("species") or "")
        dbh = _safe_float(row.get("tree_dbh_cm"))
        height = _safe_float(row.get("tree_height_m"))
        hdr = _safe_float(row.get("hdr"))
        peer_rows = _fetch_tree_rows(conn, "species=?", (species,), limit=200000) if species else []
        return {
            "status": "success",
            "target_type": "TreeIndividual",
            "tree_id": tid,
            "record": row,
            "peer_percentiles": {
                "reference_group": f"同树种 {species}" if species else "未确定树种",
                "reference_count": len(peer_rows),
                "dbh_percentile": _percentile_rank(dbh, [r.get("tree_dbh_cm") for r in peer_rows]),
                "height_percentile": _percentile_rank(height, [r.get("tree_height_m") for r in peer_rows]),
                "hdr_percentile": _percentile_rank(hdr, [r.get("hdr") for r in peer_rows]),
            },
            "legacy_tools": {
                "tree_morphology": _call_legacy_tool("tool_calc_tree_morphology_metrics", {"subplot_id": row.get("subplot_id"), "target_tree_id": tid}),
                "hegyi_competition": _call_legacy_tool("tool_calc_hegyi_competition", {"subplot_id": row.get("subplot_id"), "target_tree_id": tid, "radius_m": 6.0}),
            },
            "topography_context": _load_tree_topography_context(conn, tid),
            "climate_context": _load_climate_background_context(conn),
            "field_suggestions": [
                "核对树号、坐标和树种，排除匹配错误。",
                "记录是否存活、是否倾斜、断梢、冠枯、被压或有虫孔腐朽。",
                "拍摄整株、树冠、树干基部和周边竞争环境照片。",
            ],
            "interpretation_boundary": "单木结果仅为历史记录、形态指标和同类相对比较，不等于死亡原因或未来风险概率。",
        }


def analyze_plot(plan: Dict[str, Any], candidates: Dict[str, Any]) -> Dict[str, Any]:
    with _connect() as conn:
        if not _table_exists(conn, "tree_observations"):
            return {"status": "failed", "error": "数据库缺少 tree_observations 表。"}
        total, subplot_count = conn.execute("SELECT COUNT(*), COUNT(DISTINCT subplot_id) FROM tree_observations").fetchone()
        species_comp = _species_composition(conn)
        rows = conn.execute(
            "SELECT subplot_id, COUNT(*) AS n, AVG(tree_dbh_cm) AS mean_dbh_cm, AVG(tree_height_m) AS mean_height_m "
            "FROM tree_observations GROUP BY subplot_id ORDER BY n DESC LIMIT 20"
        ).fetchall()
        return {
            "status": "success",
            "target_type": "MonitoringPlot",
            "plot_id": "QILIAN_24HA",
            "tree_count": int(total or 0),
            "subplot_count": int(subplot_count or 0),
            "nominal_area_ha": round(int(subplot_count or 0) * DEFAULT_SUBPLOT_AREA_HA, 3),
            "species_composition_top": species_comp[:20],
            "top_subplots_by_tree_count": [dict(r) for r in rows],
            "legacy_scan_summary": _call_legacy_tool("tool_scan_subplots_risk_summary", {"start_subplot_id": "0101", "end_subplot_id": "3020"}),
            "climate_context": _load_climate_background_context(conn),
            "field_suggestions": [
                "先用全样地概览确定优先核查样方，再进入样方级候选树清单。",
                "现场复查应同时设置高关注对象和对照对象，避免只看异常样方。",
            ],
            "interpretation_boundary": "全样地概览用于宏观结构认识和优先核查排序，不等于生态系统功能完整评价。",
        }


def explain_ontology_item(plan: Dict[str, Any], candidates: Dict[str, Any]) -> Dict[str, Any]:
    target = plan.get("target", {})
    target_id = target.get("id")
    target_name = target.get("name")
    pools = []
    for key in ["indicator_candidates", "formula_candidates", "diagnostic_rule_candidates", "ontology_candidates", "tool_candidates", "all_candidates_preview"]:
        pools.extend(candidates.get(key) or [])
    matched = []
    for item in pools:
        if target_id and str(item.get("id")) == str(target_id):
            matched.append(item)
        elif target_name and str(target_name) in str(item.get("name")):
            matched.append(item)
    if not matched and pools:
        matched = pools[:5]
    return {
        "status": "success" if matched else "not_found",
        "target_type": target.get("type"),
        "target_name": target_name,
        "matched_items": matched[:10],
        "interpretation_boundary": "这是本体/知识注册表解释；具体数值仍应通过数据库和公式工具计算。",
    }


def _chart_for_plan(question: str, plan: Dict[str, Any], analysis: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        return [{"type": "error", "message": f"matplotlib不可用，无法生成图片：{exc}"}]

    metric = _infer_chart_metric(question, plan)
    target = plan.get("target", {})
    target_type = target.get("type")
    target_name = str(target.get("name") or "")
    artifacts: List[Dict[str, Any]] = []
    with _connect() as conn:
        if target_type == "Taxon" and target_name:
            rows = _fetch_tree_rows(conn, "species=?", (target_name,))
            title_scope = target_name
        elif target_type == "Subplot":
            sid = str(target.get("id") or target_name or plan.get("scope", {}).get("id"))
            rows = _fetch_tree_rows(conn, "subplot_id=?", (sid,))
            title_scope = f"样方{sid}"
        else:
            rows = _fetch_tree_rows(conn)
            title_scope = "全样地"
    vals = [float(v) for v in (_safe_float(r.get(metric)) for r in rows) if v is not None]
    if not vals:
        return [{"type": "error", "message": f"没有可用于绘图的 {metric} 数据。"}]
    fig = plt.figure(figsize=(7.5, 4.8))
    ax = fig.add_subplot(111)
    if any(w in question for w in ["箱线", "box"]):
        ax.boxplot(vals, vert=True)
        ax.set_xticklabels([title_scope])
        chart_type = "boxplot"
    else:
        ax.hist(vals, bins=min(20, max(8, int(math.sqrt(len(vals))))))
        chart_type = "histogram"
    ax.set_title(f"{title_scope} - {metric} {chart_type}")
    ax.set_ylabel("count" if chart_type == "histogram" else metric)
    ax.set_xlabel(metric)
    fig.tight_layout()
    safe_scope = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]", "_", title_scope)[:40]
    filename = f"agent_chart_{safe_scope}_{metric}_{int(time.time())}.png"
    path = VISUALIZATION_DIR / filename
    fig.savefig(path, dpi=160)
    plt.close(fig)
    artifacts.append({
        "type": "image",
        "path": str(path),
        "relative_url": f"/visualizations/{filename}",
        "artifact_url": f"/visualizations/{filename}",
        "name": filename,
        "metric": metric,
        "chart_type": chart_type,
    })
    return artifacts


def execute_plan(question: str, plan: Dict[str, Any], candidates: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    started = time.perf_counter()
    route = plan.get("route")
    target_type = plan.get("target", {}).get("type")
    result: Dict[str, Any]
    warnings: List[str] = []
    artifacts: List[Dict[str, Any]] = []
    tools_used: List[Dict[str, Any]] = []

    if route == "direct_answer":
        result = {"status": "no_tool_needed", "message": "该问题可直接由对话模型回答。"}
    elif route == "ontology_explanation" or target_type in {"IndicatorDefinition", "FormulaDefinition", "VariableDefinition", "DiagnosticRule"}:
        result = explain_ontology_item(plan, candidates)
        tools_used.append({"tool": "ontology_registry_lookup", "status": result.get("status", "success"), "arguments": {"target": plan.get("target")}})
    elif target_type == "Taxon":
        result = analyze_taxon(plan, candidates)
        tools_used.append({"tool": "domain_analysis:analyze_taxon", "status": result.get("status", "success"), "arguments": {"target": plan.get("target")}})
    elif target_type == "Subplot":
        result = analyze_subplot(plan, candidates)
        tools_used.append({"tool": "domain_analysis:analyze_subplot", "status": result.get("status", "success"), "arguments": {"target": plan.get("target")}})
    elif target_type == "TreeIndividual":
        result = analyze_tree(plan, candidates)
        tools_used.append({"tool": "domain_analysis:analyze_tree", "status": result.get("status", "success"), "arguments": {"target": plan.get("target")}})
    else:
        result = analyze_plot(plan, candidates)
        tools_used.append({"tool": "domain_analysis:analyze_plot", "status": result.get("status", "success"), "arguments": {"target": plan.get("target")}})

    need_chart = bool(plan.get("output", {}).get("need_chart")) or route == "visualization"
    if need_chart:
        chart_artifacts = _chart_for_plan(question, plan, result)
        artifacts.extend(chart_artifacts)
        status = "success" if not any(a.get("type") == "error" for a in chart_artifacts) else "failed"
        tools_used.append({"tool": "visualization:matplotlib_chart", "status": status, "arguments": plan.get("output", {}).get("chart_request", {})})
        if status == "failed":
            warnings.extend(str(a.get("message")) for a in chart_artifacts if a.get("type") == "error")

    elapsed = round(time.perf_counter() - started, 4)
    return {
        "status": result.get("status", "success") if isinstance(result, dict) else "success",
        "semantic_plan": plan,
        "analysis_result": _json_safe(result),
        "artifacts": artifacts,
        "used_tools": tools_used,
        "warnings": warnings,
        "duration_s": elapsed,
        "answer_boundary": "请区分正式观测/确定性公式结果、相对关注信号、启发式解释和待验证假设。",
    }
