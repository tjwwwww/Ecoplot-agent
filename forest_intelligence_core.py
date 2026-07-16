# -*- coding: utf-8 -*-
"""
forest_intelligence_core.py
===========================
林业智能体通用能力层。

该模块不面向某一个固定问题，而是提供可扩展的“任务状态 + 数据检查 + 分析协议”
接口。首批内置协议用于验证数据质量、林分结构、地形关联、气候背景和外业核查。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "qilian_forest.db"
SUBPLOT_AREA_HA = 0.04


PROTOCOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "data_quality_diagnosis": {
        "name_cn": "数据质量诊断",
        "claim_level": "data_quality",
        "description": "检查缺失、重复、越界、树高胸径异常、坐标异常和物种名称异常。",
    },
    "stand_structure_analysis": {
        "name_cn": "林分结构分析",
        "claim_level": "descriptive_statistics",
        "description": "分析树种组成、密度、平均胸径树高、断面积、径级结构和空间分布。",
    },
    "topography_association_analysis": {
        "name_cn": "地形关联分析",
        "claim_level": "association_only",
        "description": "分析海拔、坡度、坡向、坡位与物种分布和林分结构的统计关联，不给因果结论。",
    },
    "climate_background_analysis": {
        "name_cn": "气候背景分析",
        "claim_level": "background_context",
        "description": "分析多年平均、年际变化、极端年份、生长季温度降水和调查年份位置。",
    },
    "field_inspection_suggestion": {
        "name_cn": "外业核查与补采建议",
        "claim_level": "field_action_suggestion",
        "description": "根据异常树、异常区域、缺失字段和不可回答问题生成现场核查建议。",
    },
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(_json_safe(payload), ensure_ascii=False, default=str)


def _json_safe(value: Any) -> Any:
    if isinstance(value, sqlite3.Row):
        return {k: _json_safe(value[k]) for k in value.keys()}
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    return value


def _rows(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    if not _table_exists(conn, table):
        return []
    return [str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})")]


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _pearson(xs: Sequence[Any], ys: Sequence[Any]) -> Optional[float]:
    pairs = [(_safe_float(x), _safe_float(y)) for x, y in zip(xs, ys)]
    pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    x_vals = [p[0] for p in pairs]
    y_vals = [p[1] for p in pairs]
    mx = mean(x_vals)
    my = mean(y_vals)
    sx = sum((x - mx) ** 2 for x in x_vals)
    sy = sum((y - my) ** 2 for y in y_vals)
    if sx <= 0 or sy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(sx * sy)


def _extract_subplot_ids(text: str) -> List[str]:
    return sorted(set(re.findall(r"(?<!\d)(\d{4})(?!\d)", text or "")))


def _canonical_species(conn: sqlite3.Connection, name: str) -> str:
    name = str(name or "").strip()
    if not name:
        return ""
    exact = conn.execute("SELECT species FROM tree_observations WHERE species=? LIMIT 1", (name,)).fetchone()
    if exact:
        return str(exact["species"])
    contains = conn.execute(
        "SELECT species, COUNT(*) AS n FROM tree_observations WHERE species LIKE ? GROUP BY species ORDER BY n DESC LIMIT 1",
        (f"%{name}%",),
    ).fetchone()
    if contains:
        return str(contains["species"])
    reverse = conn.execute(
        "SELECT species, COUNT(*) AS n FROM tree_observations GROUP BY species ORDER BY n DESC"
    ).fetchall()
    for row in reverse:
        species = str(row["species"] or "")
        if species and species in name:
            return species
    return name


def _match_species(conn: sqlite3.Connection, text: str) -> List[Dict[str, Any]]:
    species_rows = conn.execute(
        "SELECT species, COUNT(*) AS n, COUNT(DISTINCT subplot_id) AS subplot_count "
        "FROM tree_observations WHERE species IS NOT NULL GROUP BY species ORDER BY n DESC"
    ).fetchall()
    text = str(text or "")
    matched = []
    for row in species_rows:
        name = str(row["species"])
        if name and (name in text or text in name):
            matched.append(dict(row))
    return matched[:20]


def tool_resolve_forest_question(question: str, context_json: str = "{}") -> str:
    """把自然语言问题初步落地为林业任务状态。"""
    question = str(question or "").strip()
    try:
        context = json.loads(context_json) if context_json else {}
    except Exception:
        context = {}
    with _connect() as conn:
        species_candidates = _match_species(conn, question)
        subplot_ids = _extract_subplot_ids(question)
        variables = []
        variable_terms = {
            "海拔": "elevation_m",
            "坡度": "slope_degree",
            "坡向": "aspect_degree",
            "坡位": "slope_position",
            "胸径": "tree_dbh_cm",
            "树高": "tree_height_m",
            "密度": "stand_density",
            "断面积": "basal_area_m2",
            "气温": "mean_temperature_c",
            "温度": "mean_temperature_c",
            "降水": "precipitation_mm",
            "坐标": "tree_x_m/tree_y_m",
        }
        for term, field in variable_terms.items():
            if term in question:
                variables.append({"term": term, "field": field})
    intent = "general_chat"
    if any(w in question for w in ["缺失", "重复", "异常", "越界", "质量", "错误"]):
        intent = "data_quality_diagnosis"
    elif any(w in question for w in ["海拔", "坡度", "坡向", "坡位", "地形"]):
        intent = "topography_association_analysis"
    elif any(w in question for w in ["气候", "降水", "温度", "气温", "生长季", "极端年份"]):
        intent = "climate_background_analysis"
    elif any(w in question for w in ["外业", "复测", "核查", "补采", "现场"]):
        intent = "field_inspection_suggestion"
    elif any(w in question for w in ["结构", "组成", "密度", "径级", "断面积", "空间分布", "平均胸径", "平均树高"]):
        intent = "stand_structure_analysis"

    target = None
    if species_candidates:
        target = {"type": "Taxon", "name": species_candidates[0]["species"]}
    elif subplot_ids:
        target = {"type": "Subplot", "id": subplot_ids[0]}
    elif context.get("current_subplot_id"):
        target = {"type": "Subplot", "id": context.get("current_subplot_id"), "source": "page_context"}

    return _json({
        "status": "success",
        "task_state": {
            "question": question,
            "intent": intent,
            "target": target,
            "subplot_ids": subplot_ids,
            "species_candidates": species_candidates,
            "variables": variables,
            "available_protocols": list(PROTOCOL_REGISTRY.keys()),
            "note": "这是初步语义落地结果，后续仍需数据适用性检查。",
        },
    })


def tool_inspect_forest_data(
    target_type: str = "",
    target_name: str = "",
    variables_json: str = "[]",
    scope_json: str = "{}",
) -> str:
    """检查本地森林数据库是否具备回答问题所需的数据、字段和关联关系。"""
    try:
        variables = json.loads(variables_json) if variables_json else []
    except Exception:
        variables = []
    try:
        scope = json.loads(scope_json) if scope_json else {}
    except Exception:
        scope = {}
    with _connect() as conn:
        tables = {
            name: {
                "exists": _table_exists(conn, name),
                "columns": _columns(conn, name),
            }
            for name in [
                "tree_observations",
                "shrub_observations",
                "deadwood_observations",
                "topography_observations",
                "climate_daily_normalized",
                "climate_monthly_summary",
                "climate_annual_summary",
                "climate_stations",
            ]
        }
        tree_summary = dict(conn.execute(
            "SELECT COUNT(*) AS tree_count, COUNT(DISTINCT subplot_id) AS subplot_count, COUNT(DISTINCT species) AS species_count "
            "FROM tree_observations"
        ).fetchone())
        topo_summary = dict(conn.execute(
            "SELECT COUNT(*) AS topo_count, COUNT(DISTINCT tree_id) AS tree_count, COUNT(DISTINCT subplot_id) AS subplot_count "
            "FROM topography_observations"
        ).fetchone())
        climate_summary = dict(conn.execute(
            "SELECT COUNT(*) AS daily_count, MIN(observation_date) AS start_date, MAX(observation_date) AS end_date "
            "FROM climate_daily_normalized"
        ).fetchone())
        target_check: Dict[str, Any] = {}
        if target_type.lower() in {"taxon", "species", "??"} and target_name:
            canonical_name = _canonical_species(conn, target_name)
            target_check = dict(conn.execute(
                "SELECT species, COUNT(*) AS tree_count, COUNT(DISTINCT subplot_id) AS subplot_count "
                "FROM tree_observations WHERE species=? GROUP BY species",
                (canonical_name,),
            ).fetchone() or {})
            if target_check:
                target_check["input_name"] = target_name
                target_check["canonical_name"] = canonical_name
        elif target_type.lower() in {"subplot", "样方"} and target_name:
            target_check = dict(conn.execute(
                "SELECT subplot_id, COUNT(*) AS tree_count, COUNT(DISTINCT species) AS species_count "
                "FROM tree_observations WHERE subplot_id=? GROUP BY subplot_id",
                (target_name,),
            ).fetchone() or {})
        joins = {
            "tree_to_topography": {
                "join_key": "tree_id",
                "available": bool(conn.execute(
                    "SELECT 1 FROM tree_observations t JOIN topography_observations g ON t.tree_id=g.tree_id LIMIT 1"
                ).fetchone()),
            },
            "subplot_to_tree": {"join_key": "subplot_id", "available": True},
            "climate_to_plot": {
                "join_key": "station_id/background_context",
                "available": _table_exists(conn, "climate_daily_normalized"),
                "boundary": "气候为附近气象站背景，不直接解释样地内单木微环境差异。",
            },
        }
    return _json({
        "status": "success",
        "target_check": target_check,
        "requested_variables": variables,
        "scope": scope,
        "data_catalog": tables,
        "coverage_summary": {
            "tree_observations": tree_summary,
            "topography_observations": topo_summary,
            "climate_daily_normalized": climate_summary,
        },
        "join_capabilities": joins,
    })


def _where_for_target(target_type: str, target_name: str, scope: Dict[str, Any]) -> Tuple[str, List[Any]]:
    clauses = ["1=1"]
    params: List[Any] = []
    if target_type.lower() in {"taxon", "species", "树种"} and target_name:
        clauses.append("species=?")
        params.append(target_name)
    if target_type.lower() in {"subplot", "样方"} and target_name:
        clauses.append("subplot_id=?")
        params.append(target_name)
    subplot_id = scope.get("subplot_id") or scope.get("id") if isinstance(scope, dict) else None
    if subplot_id:
        clauses.append("subplot_id=?")
        params.append(str(subplot_id))
    return " AND ".join(clauses), params


def _where_for_tree_alias(target_type: str, target_name: str, scope: Dict[str, Any], alias: str = "t") -> Tuple[str, List[Any]]:
    clauses = ["1=1"]
    params: List[Any] = []
    if target_type.lower() in {"taxon", "species", "??"} and target_name:
        clauses.append(f"{alias}.species=?")
        params.append(target_name)
    if target_type.lower() in {"subplot", "??"} and target_name:
        clauses.append(f"{alias}.subplot_id=?")
        params.append(target_name)
    subplot_id = scope.get("subplot_id") or scope.get("id") if isinstance(scope, dict) else None
    if subplot_id:
        clauses.append(f"{alias}.subplot_id=?")
        params.append(str(subplot_id))
    return " AND ".join(clauses), params


def _stand_structure(conn: sqlite3.Connection, target_type: str, target_name: str, scope: Dict[str, Any]) -> Dict[str, Any]:
    if target_type.lower() in {"taxon", "species", "??"}:
        target_name = _canonical_species(conn, target_name)
    where_sql, params = _where_for_target(target_type, target_name, scope)
    row = dict(conn.execute(
        f"""
        SELECT COUNT(*) AS tree_count, COUNT(DISTINCT subplot_id) AS subplot_count,
               AVG(tree_dbh_cm) AS mean_dbh_cm, AVG(tree_height_m) AS mean_height_m,
               SUM(3.141592653589793 * (tree_dbh_cm/200.0) * (tree_dbh_cm/200.0)) AS basal_area_m2
        FROM tree_observations
        WHERE {where_sql} AND tree_dbh_cm IS NOT NULL AND tree_dbh_cm > 0
        """,
        params,
    ).fetchone())
    subplot_count = int(row.get("subplot_count") or 0)
    area_ha = subplot_count * SUBPLOT_AREA_HA if subplot_count else None
    tree_count = int(row.get("tree_count") or 0)
    species = _rows(conn.execute(
        f"""
        SELECT species, COUNT(*) AS tree_count,
               ROUND(100.0 * COUNT(*) / NULLIF((SELECT COUNT(*) FROM tree_observations WHERE {where_sql}), 0), 2) AS proportion_pct,
               AVG(tree_dbh_cm) AS mean_dbh_cm, AVG(tree_height_m) AS mean_height_m
        FROM tree_observations
        WHERE {where_sql} AND species IS NOT NULL
        GROUP BY species ORDER BY tree_count DESC LIMIT 20
        """,
        params * 2,
    ))
    dbh_bins = _rows(conn.execute(
        f"""
        SELECT CAST(tree_dbh_cm/5 AS INTEGER)*5 AS dbh_bin_cm, COUNT(*) AS tree_count
        FROM tree_observations
        WHERE {where_sql} AND tree_dbh_cm IS NOT NULL AND tree_dbh_cm > 0
        GROUP BY dbh_bin_cm ORDER BY dbh_bin_cm
        """,
        params,
    ))
    return {
        "status": "success" if tree_count else "not_found",
        "protocol": "stand_structure_analysis",
        "claim_level": PROTOCOL_REGISTRY["stand_structure_analysis"]["claim_level"],
        "scope": {"target_type": target_type, "target_name": target_name, **scope},
        "stand_summary": {
            **row,
            "estimated_area_ha": area_ha,
            "stem_density_per_ha": (tree_count / area_ha) if area_ha else None,
            "basal_area_m2_per_ha": (row.get("basal_area_m2") / area_ha) if area_ha and row.get("basal_area_m2") is not None else None,
        },
        "species_composition": species,
        "dbh_size_class": dbh_bins,
        "boundary": "这是基于现有乔木观测表的描述性林分结构结果。",
    }


def _data_quality(conn: sqlite3.Connection) -> Dict[str, Any]:
    duplicate_tree_ids = _rows(conn.execute(
        "SELECT tree_id, COUNT(*) AS n FROM tree_observations WHERE tree_id IS NOT NULL GROUP BY tree_id HAVING n>1 LIMIT 30"
    ))
    missing = dict(conn.execute(
        """
        SELECT
          SUM(CASE WHEN species IS NULL OR TRIM(species)='' THEN 1 ELSE 0 END) AS missing_species,
          SUM(CASE WHEN tree_dbh_cm IS NULL THEN 1 ELSE 0 END) AS missing_dbh,
          SUM(CASE WHEN tree_height_m IS NULL THEN 1 ELSE 0 END) AS missing_height,
          SUM(CASE WHEN tree_x_m IS NULL OR tree_y_m IS NULL THEN 1 ELSE 0 END) AS missing_coordinates
        FROM tree_observations
        """
    ).fetchone())
    outliers = {
        "dbh_non_positive": conn.execute("SELECT COUNT(*) FROM tree_observations WHERE tree_dbh_cm IS NOT NULL AND tree_dbh_cm<=0").fetchone()[0],
        "height_non_positive": conn.execute("SELECT COUNT(*) FROM tree_observations WHERE tree_height_m IS NOT NULL AND tree_height_m<=0").fetchone()[0],
        "height_over_80m": conn.execute("SELECT COUNT(*) FROM tree_observations WHERE tree_height_m>80").fetchone()[0],
        "dbh_over_300cm": conn.execute("SELECT COUNT(*) FROM tree_observations WHERE tree_dbh_cm>300").fetchone()[0],
        "coordinate_outside_20m_subplot": conn.execute(
            "SELECT COUNT(*) FROM tree_observations WHERE tree_x_m<0 OR tree_x_m>20 OR tree_y_m<0 OR tree_y_m>20"
        ).fetchone()[0],
        "hdr_over_120": conn.execute(
            "SELECT COUNT(*) FROM tree_observations WHERE tree_dbh_cm>0 AND tree_height_m IS NOT NULL AND (tree_height_m*100.0/tree_dbh_cm)>120"
        ).fetchone()[0],
    }
    species_names = _rows(conn.execute(
        "SELECT species, COUNT(*) AS n FROM tree_observations GROUP BY species ORDER BY n DESC"
    ))
    return {
        "status": "success",
        "protocol": "data_quality_diagnosis",
        "claim_level": PROTOCOL_REGISTRY["data_quality_diagnosis"]["claim_level"],
        "record_summary": dict(conn.execute(
            "SELECT COUNT(*) AS tree_records, COUNT(DISTINCT tree_id) AS unique_tree_ids, COUNT(DISTINCT subplot_id) AS subplot_count FROM tree_observations"
        ).fetchone()),
        "missing_fields": missing,
        "duplicate_tree_ids_preview": duplicate_tree_ids,
        "outlier_counts": outliers,
        "species_name_distribution": species_names,
        "boundary": "数据质量诊断只标记可疑记录，是否为真实异常需结合原始调查表或现场核查。",
    }


def _topography_association(conn: sqlite3.Connection, target_type: str, target_name: str, scope: Dict[str, Any]) -> Dict[str, Any]:
    if target_type.lower() in {"taxon", "species", "??"}:
        target_name = _canonical_species(conn, target_name)
    where_sql, params = _where_for_tree_alias(target_type, target_name, scope, "t")
    rows = conn.execute(
        f"""
        SELECT t.subplot_id, t.tree_id, t.species, t.tree_dbh_cm, t.tree_height_m,
               g.elevation_m, g.slope_degree, g.aspect_degree, g.slope_position
        FROM tree_observations t
        JOIN topography_observations g ON t.tree_id=g.tree_id
        WHERE {where_sql}
        """,
        params,
    ).fetchall()
    if not rows:
        return {
            "status": "not_found",
            "protocol": "topography_association_analysis",
            "message": "未查询到可关联的乔木—地形记录。",
        }
    elevation_bins = _rows(conn.execute(
        f"""
        SELECT CAST(g.elevation_m/100 AS INTEGER)*100 AS elevation_bin_m,
               COUNT(*) AS tree_count, COUNT(DISTINCT t.subplot_id) AS subplot_count,
               AVG(t.tree_dbh_cm) AS mean_dbh_cm, AVG(t.tree_height_m) AS mean_height_m
        FROM tree_observations t
        JOIN topography_observations g ON t.tree_id=g.tree_id
        WHERE {where_sql} AND g.elevation_m IS NOT NULL
        GROUP BY elevation_bin_m ORDER BY elevation_bin_m
        """,
        params,
    ))
    slope_bins = _rows(conn.execute(
        f"""
        SELECT CAST(g.slope_degree/10 AS INTEGER)*10 AS slope_bin_degree,
               COUNT(*) AS tree_count, COUNT(DISTINCT t.subplot_id) AS subplot_count,
               AVG(t.tree_dbh_cm) AS mean_dbh_cm, AVG(t.tree_height_m) AS mean_height_m
        FROM tree_observations t
        JOIN topography_observations g ON t.tree_id=g.tree_id
        WHERE {where_sql} AND g.slope_degree IS NOT NULL
        GROUP BY slope_bin_degree ORDER BY slope_bin_degree
        """,
        params,
    ))
    xs = [r["elevation_m"] for r in rows]
    dbh = [r["tree_dbh_cm"] for r in rows]
    height = [r["tree_height_m"] for r in rows]
    return {
        "status": "success",
        "protocol": "topography_association_analysis",
        "claim_level": PROTOCOL_REGISTRY["topography_association_analysis"]["claim_level"],
        "sample_size": len(rows),
        "subplot_count": len({r["subplot_id"] for r in rows}),
        "elevation_range_m": {
            "min": min(_safe_float(x) for x in xs if _safe_float(x) is not None),
            "max": max(_safe_float(x) for x in xs if _safe_float(x) is not None),
        },
        "elevation_bins": elevation_bins,
        "slope_bins": slope_bins,
        "correlation": {
            "elevation_vs_dbh_pearson": _pearson(xs, dbh),
            "elevation_vs_height_pearson": _pearson(xs, height),
        },
        "boundary": "该协议只支持描述统计和关联分析，不能证明海拔、坡度等地形因子对生长的因果影响。",
    }


def _climate_background(conn: sqlite3.Connection, options: Dict[str, Any]) -> Dict[str, Any]:
    survey_year = int(options.get("survey_year") or 2023)
    annual = _rows(conn.execute(
        "SELECT * FROM climate_annual_summary ORDER BY year"
    ))
    if not annual:
        return {"status": "not_found", "protocol": "climate_background_analysis", "message": "未找到逐年气候摘要表。"}
    long_term = dict(conn.execute(
        """
        SELECT COUNT(*) AS year_count,
               MIN(year) AS start_year, MAX(year) AS end_year,
               AVG(mean_temperature_c) AS long_term_mean_temperature_c,
               AVG(total_precipitation_mm) AS long_term_mean_annual_precipitation_mm,
               MIN(mean_temperature_c) AS coldest_annual_mean_temperature_c,
               MAX(mean_temperature_c) AS warmest_annual_mean_temperature_c,
               MIN(total_precipitation_mm) AS driest_annual_precipitation_mm,
               MAX(total_precipitation_mm) AS wettest_annual_precipitation_mm
        FROM climate_annual_summary
        """
    ).fetchone())
    extreme_years = {
        "warmest_years": _rows(conn.execute("SELECT year, mean_temperature_c FROM climate_annual_summary ORDER BY mean_temperature_c DESC LIMIT 5")),
        "coldest_years": _rows(conn.execute("SELECT year, mean_temperature_c FROM climate_annual_summary ORDER BY mean_temperature_c ASC LIMIT 5")),
        "wettest_years": _rows(conn.execute("SELECT year, total_precipitation_mm FROM climate_annual_summary ORDER BY total_precipitation_mm DESC LIMIT 5")),
        "driest_years": _rows(conn.execute("SELECT year, total_precipitation_mm FROM climate_annual_summary ORDER BY total_precipitation_mm ASC LIMIT 5")),
    }
    growing = dict(conn.execute(
        """
        SELECT AVG(mean_temperature_c) AS growing_season_mean_temperature_c,
               AVG(total_precipitation_mm) AS growing_season_mean_precipitation_mm
        FROM (
            SELECT year, AVG(mean_temperature_c) AS mean_temperature_c, SUM(total_precipitation_mm) AS total_precipitation_mm
            FROM climate_monthly_summary
            WHERE month BETWEEN 5 AND 9
            GROUP BY year
        )
        """
    ).fetchone())
    survey = dict(conn.execute(
        "SELECT year, mean_temperature_c, total_precipitation_mm FROM climate_annual_summary WHERE year=? LIMIT 1",
        (survey_year,),
    ).fetchone() or {})
    if survey:
        survey["temperature_departure_from_long_term_c"] = survey.get("mean_temperature_c") - long_term.get("long_term_mean_temperature_c")
        survey["precipitation_departure_from_long_term_mm"] = survey.get("total_precipitation_mm") - long_term.get("long_term_mean_annual_precipitation_mm")
    return {
        "status": "success",
        "protocol": "climate_background_analysis",
        "claim_level": PROTOCOL_REGISTRY["climate_background_analysis"]["claim_level"],
        "long_term_summary": long_term,
        "growing_season_summary_may_to_sep": growing,
        "extreme_years": extreme_years,
        "survey_year_position": survey,
        "boundary": "当前气候数据代表附近气象站长期背景，不直接用于解释样地内单木尺度差异。",
    }


def _field_suggestions(conn: sqlite3.Connection, options: Dict[str, Any]) -> Dict[str, Any]:
    high_hdr = _rows(conn.execute(
        """
        SELECT subplot_id, tree_id, species, tree_dbh_cm, tree_height_m,
               ROUND(tree_height_m*100.0/tree_dbh_cm, 2) AS hdr
        FROM tree_observations
        WHERE tree_dbh_cm>0 AND tree_height_m IS NOT NULL
        ORDER BY hdr DESC LIMIT 20
        """
    ))
    missing_coord_subplots = _rows(conn.execute(
        """
        SELECT subplot_id, COUNT(*) AS missing_coordinate_count
        FROM tree_observations
        WHERE tree_x_m IS NULL OR tree_y_m IS NULL
        GROUP BY subplot_id ORDER BY missing_coordinate_count DESC LIMIT 20
        """
    ))
    sparse_topography = _rows(conn.execute(
        """
        SELECT t.subplot_id, COUNT(*) AS tree_count,
               SUM(CASE WHEN g.tree_id IS NULL THEN 1 ELSE 0 END) AS missing_topography_count
        FROM tree_observations t
        LEFT JOIN topography_observations g ON t.tree_id=g.tree_id
        GROUP BY t.subplot_id
        HAVING missing_topography_count>0
        ORDER BY missing_topography_count DESC LIMIT 20
        """
    ))
    return {
        "status": "success",
        "protocol": "field_inspection_suggestion",
        "claim_level": PROTOCOL_REGISTRY["field_inspection_suggestion"]["claim_level"],
        "candidate_checks": {
            "high_hdr_tree_preview": high_hdr,
            "subplots_with_missing_coordinates": missing_coord_subplots,
            "subplots_with_missing_topography_link": sparse_topography,
        },
        "recommended_field_tasks": [
            "核对高 HDR 单木的胸径、树高、树冠形态和是否存在测量误差。",
            "补测缺失或异常坐标，尤其是坐标超出 20m×20m 样方范围的记录。",
            "核查缺失地形关联的单木编号，确认 tree_id 是否一致。",
            "对当前工具无法回答的问题，补充相应字段后再进入分析。",
        ],
        "boundary": "外业建议是基于数据异常和相对关注信号生成的核查清单，不等同于经营处方。",
    }


def tool_run_forest_analysis_protocol(
    protocol: str,
    target_type: str = "",
    target_name: str = "",
    scope_json: str = "{}",
    options_json: str = "{}",
) -> str:
    """运行一个可扩展的林业分析协议。"""
    try:
        scope = json.loads(scope_json) if scope_json else {}
    except Exception:
        scope = {}
    try:
        options = json.loads(options_json) if options_json else {}
    except Exception:
        options = {}
    protocol = str(protocol or "").strip()
    if protocol not in PROTOCOL_REGISTRY:
        return _json({
            "status": "unsupported_protocol",
            "protocol": protocol,
            "available_protocols": PROTOCOL_REGISTRY,
        })
    with _connect() as conn:
        if protocol == "data_quality_diagnosis":
            result = _data_quality(conn)
        elif protocol == "stand_structure_analysis":
            result = _stand_structure(conn, target_type, target_name, scope)
        elif protocol == "topography_association_analysis":
            result = _topography_association(conn, target_type, target_name, scope)
        elif protocol == "climate_background_analysis":
            result = _climate_background(conn, options)
        elif protocol == "field_inspection_suggestion":
            result = _field_suggestions(conn, options)
        else:
            result = {"status": "unsupported_protocol", "protocol": protocol}
    result.setdefault("protocol_definition", PROTOCOL_REGISTRY.get(protocol))
    return _json(result)


FOREST_INTELLIGENCE_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "tool_resolve_forest_question",
            "description": "通用语义落地工具：把用户自然语言初步解析为林业任务状态，包括意图、对象、样方、树种、变量和候选分析协议。适合在不确定如何查数前先调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用户原始问题。"},
                    "context_json": {"type": "string", "description": "可选页面上下文 JSON。"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_inspect_forest_data",
            "description": "通用数据适用性检查工具：检查森林调查、地形、气候数据是否存在，字段和关联键是否可用。复杂数据问题应先用它确认数据能力。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_type": {"type": "string", "description": "对象类型，如 Taxon、Subplot、TreeIndividual、Plot。"},
                    "target_name": {"type": "string", "description": "对象名称或编号，如 青海云杉、3018。"},
                    "variables_json": {"type": "string", "description": "需要检查的变量 JSON 数组。"},
                    "scope_json": {"type": "string", "description": "范围 JSON，如 {\"subplot_id\":\"3018\"}。"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_run_forest_analysis_protocol",
            "description": "通用分析协议执行工具：在统一架构下运行数据质量、林分结构、地形关联、气候背景、外业核查等可扩展分析协议。协议只给其证据等级允许的结论。",
            "parameters": {
                "type": "object",
                "properties": {
                    "protocol": {
                        "type": "string",
                        "description": "协议名：data_quality_diagnosis、stand_structure_analysis、topography_association_analysis、climate_background_analysis、field_inspection_suggestion。",
                    },
                    "target_type": {"type": "string", "description": "对象类型，如 Taxon、Subplot。"},
                    "target_name": {"type": "string", "description": "对象名称或编号，如 青海云杉、3018。"},
                    "scope_json": {"type": "string", "description": "范围 JSON。"},
                    "options_json": {"type": "string", "description": "可选参数 JSON，如 {\"survey_year\":2023}。"},
                },
                "required": ["protocol"],
            },
        },
    },
]
