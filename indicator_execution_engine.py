# -*- coding: utf-8 -*-
"""
indicator_execution_engine.py
=============================

统一指标执行层。

这个模块的职责不是重新定义指标，而是把已经登记的 indicator_id
落到真实数据库字段和确定性计算函数上。它支持两种调用方式：

1. 指定 indicator_ids：只计算用户明确需要的指标；
2. 指定 indicator_group：批量计算一组常用指标。

所有输出只表达观测值、确定性计算值和数据质量标记，不生成伪置信度。
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections import Counter
from functools import lru_cache
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "qilian_forest.db"
SUBPLOT_AREA_HA = 0.04


REGISTRY_PATH = BASE_DIR / "ontology" / "forestry_knowledge_registry.yaml"


@lru_cache(maxsize=1)
def _load_indicator_contracts() -> Dict[str, Dict[str, Any]]:
    """读取指标注册表中的 calculation 绑定。"""
    if not REGISTRY_PATH.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    indicators = data.get("indicators") or data.get("derived_indicators") or []
    contracts: Dict[str, Dict[str, Any]] = {}
    for item in indicators:
        indicator_id = item.get("indicator_id") or item.get("knowledge_id")
        if not indicator_id:
            continue
        contracts[indicator_id] = {
            "indicator_id": indicator_id,
            "name_cn": item.get("name_cn"),
            "level": item.get("level"),
            "canonical_unit": item.get("canonical_unit"),
            "calculation": item.get("calculation") or {},
        }
    return contracts


TREE_MORPHOLOGY_INDICATORS = {
    "TREE_HEIGHT_DIAMETER_RATIO",
    "TREE_BASAL_AREA",
    "TREE_MEAN_CROWN_WIDTH",
    "TREE_CROWN_AREA",
    "TREE_CROWN_ASPECT_RATIO",
    "TREE_CROWN_ASYMMETRY_RATIO",
    "TREE_CROWN_LENGTH",
    "TREE_LIVE_CROWN_RATIO",
    "TREE_DBH_PERCENTILE_WITHIN_REFERENCE_GROUP",
    "TREE_HEIGHT_PERCENTILE_WITHIN_REFERENCE_GROUP",
    "TREE_CROWN_WIDTH_PERCENTILE_WITHIN_REFERENCE_GROUP",
    "TREE_VOLUME",
}

TREE_COMPETITION_INDICATORS = {
    "TREE_NEAREST_NEIGHBOR_DISTANCE",
    "TREE_NEIGHBOR_COUNT_WITHIN_RADIUS",
    "TREE_LOCAL_DENSITY_WITHIN_RADIUS",
    "TREE_HEGYI_COMPETITION_INDEX",
    "TREE_LARGER_NEIGHBOR_COUNT",
    "TREE_LARGER_NEIGHBOR_BASAL_AREA",
    "TREE_NEIGHBOR_MEAN_DBH",
    "TREE_NEIGHBOR_MEAN_HEIGHT",
    "TREE_CONSPECIFIC_NEIGHBOR_RATIO",
    "TREE_HETEROSPECIFIC_NEIGHBOR_RATIO",
    "TREE_NEIGHBOR_BASAL_AREA_WITHIN_RADIUS",
}

SUBPLOT_STRUCTURE_INDICATORS = {
    "SUBPLOT_TREE_COUNT",
    "SUBPLOT_SPECIES_COUNT",
    "SUBPLOT_STAND_DENSITY",
    "SUBPLOT_MEAN_DBH",
    "SUBPLOT_MEAN_HEIGHT",
    "SUBPLOT_QUADRATIC_MEAN_DBH",
    "SUBPLOT_TOTAL_BASAL_AREA",
    "SUBPLOT_BASAL_AREA_PER_HA",
    "SUBPLOT_DIAMETER_CLASS_DISTRIBUTION",
    "SUBPLOT_SPECIES_COMPOSITION",
    "SUBPLOT_SHANNON_INDEX",
    "SUBPLOT_SIMPSON_DIVERSITY_INDEX",
    "SUBPLOT_PIELOU_EVENNESS",
    "SUBPLOT_DBH_COEFFICIENT_OF_VARIATION",
    "SUBPLOT_MIXING_RATIO",
    "SUBPLOT_HIGH_HDR_TREE_RATIO",
    "SUBPLOT_HIGH_COMPETITION_TREE_RATIO",
}

TOPOGRAPHY_INDICATORS = {
    "TOPOGRAPHY_NORTHNESS",
    "TOPOGRAPHY_EASTNESS",
    "TOPOGRAPHY_ASPECT_CLASS",
    "TOPOGRAPHY_SLOPE_CLASS",
    "TOPOGRAPHY_ELEVATION_BAND",
}

CLIMATE_INDICATORS = {
    "CLIMATE_ANNUAL_MEAN_TEMPERATURE",
    "CLIMATE_MONTHLY_MEAN_TEMPERATURE",
    "CLIMATE_ANNUAL_PRECIPITATION",
    "CLIMATE_MONTHLY_PRECIPITATION",
    "CLIMATE_GROWING_SEASON_MEAN_TEMPERATURE",
    "CLIMATE_GROWING_SEASON_PRECIPITATION",
    "CLIMATE_TEMPERATURE_ANOMALY",
    "CLIMATE_PRECIPITATION_ANOMALY",
    "CLIMATE_PRECIPITATION_ANOMALY_PERCENT",
    "CLIMATE_HEAT_DAYS",
    "CLIMATE_FROST_DAYS",
    "CLIMATE_HEAVY_PRECIPITATION_DAYS",
    "CLIMATE_MAX_CONSECUTIVE_DRY_DAYS",
    "CLIMATE_EXTREME_COLD_DAYS",
    "CLIMATE_STRONG_WIND_DAYS",
    "CLIMATE_ANNUAL_MAX_WIND_SPEED",
}

INDICATOR_GROUPS = {
    "tree_morphology": TREE_MORPHOLOGY_INDICATORS,
    "tree_competition": TREE_COMPETITION_INDICATORS,
    "subplot_stand_structure": SUBPLOT_STRUCTURE_INDICATORS,
    "topography_derived": TOPOGRAPHY_INDICATORS,
    "climate_background": CLIMATE_INDICATORS,
}


def compute_registered_indicators(
    target_type: str,
    target_id: str = "",
    indicator_ids: Optional[Sequence[str]] = None,
    indicator_group: str = "",
    parameters: Optional[Dict[str, Any]] = None,
    db_path: Optional[str | os.PathLike[str]] = None,
) -> Dict[str, Any]:
    """按 indicator_id 计算已实现指标。"""

    parameters = parameters or {}
    db_file = Path(db_path or DEFAULT_DB_PATH)
    if not db_file.exists():
        return {
            "status": "failed",
            "error_code": "DB_NOT_FOUND",
            "message": f"数据库不存在：{db_file}",
        }

    requested = _resolve_requested_indicators(indicator_ids, indicator_group, target_type)
    if not requested:
        return {
            "status": "failed",
            "error_code": "NO_INDICATORS_REQUESTED",
            "message": "没有指定 indicator_ids 或可识别的 indicator_group。",
        }

    contracts = _load_indicator_contracts()
    registered_requested = [indicator_id for indicator_id in requested if indicator_id in contracts]
    unregistered_requested = sorted(set(requested) - set(registered_requested))
    if contracts and not registered_requested:
        return {
            "status": "failed",
            "error_code": "INDICATORS_NOT_REGISTERED",
            "requested_indicator_ids": sorted(requested),
            "unregistered_indicator_ids": unregistered_requested,
        }
    if registered_requested:
        requested = registered_requested

    target_type_norm = _normalize_target_type(target_type)
    results: Dict[str, Any] = {}
    quality_flags: List[str] = []
    executed_groups: List[str] = []

    with _connect(db_file) as conn:
        group_plan = _partition_indicators(requested, contracts)

        if group_plan["tree_morphology"]:
            data = compute_tree_morphology_indicators(conn, target_type_norm, target_id, group_plan["tree_morphology"])
            results.update(data["indicator_values"])
            quality_flags.extend(data["quality_flags"])
            executed_groups.append("tree_morphology")

        if group_plan["tree_competition"]:
            radius_m = float(parameters.get("radius_m", 6.0))
            data = compute_tree_competition_indicators(conn, target_type_norm, target_id, group_plan["tree_competition"], radius_m)
            results.update(data["indicator_values"])
            quality_flags.extend(data["quality_flags"])
            executed_groups.append("tree_competition")

        if group_plan["subplot_stand_structure"]:
            data = compute_subplot_structure_indicators(conn, target_type_norm, target_id, group_plan["subplot_stand_structure"])
            results.update(data["indicator_values"])
            quality_flags.extend(data["quality_flags"])
            executed_groups.append("subplot_stand_structure")

        if group_plan["topography_derived"]:
            data = compute_topography_indicators(conn, target_type_norm, target_id, group_plan["topography_derived"], parameters)
            results.update(data["indicator_values"])
            quality_flags.extend(data["quality_flags"])
            executed_groups.append("topography_derived")

        if group_plan["climate_background"]:
            data = compute_climate_indicators(conn, group_plan["climate_background"], parameters)
            results.update(data["indicator_values"])
            quality_flags.extend(data["quality_flags"])
            executed_groups.append("climate_background")

    computed_requested = sorted(set(requested) & set(results.keys()))
    unsupported = sorted(set(requested) - set(results.keys()))
    if 'unregistered_requested' in locals():
        unsupported = sorted(set(unsupported) | set(unregistered_requested))
    status = "success" if computed_requested else "not_found"
    calculation_contracts = {
        indicator_id: contracts.get(indicator_id, {}).get("calculation", {})
        for indicator_id in sorted(set(requested))
        if contracts.get(indicator_id)
    }
    return {
        "status": status,
        "target": {"type": target_type_norm, "id": target_id},
        "requested_indicator_ids": sorted(requested),
        "computed_indicator_ids": computed_requested,
        "computed_indicator_count": len(computed_requested),
        "executed_groups": executed_groups,
        "calculation_contracts": calculation_contracts,
        "indicator_values": results,
        "unsupported_or_unavailable_indicator_ids": unsupported,
        "quality_flags": sorted(set(quality_flags)),
        "parameters": parameters,
        "result_boundary": "本结果为数据库观测值和确定性公式计算结果；统计关联、机制解释和经营建议需要单独分析并说明证据边界。",
    }


def compute_tree_morphology_indicators(
    conn: sqlite3.Connection,
    target_type: str,
    target_id: str,
    requested: Iterable[str],
) -> Dict[str, Any]:
    rows = _load_tree_rows(conn, target_type, target_id)
    quality_flags: List[str] = []
    if not rows:
        return {"indicator_values": {}, "quality_flags": ["NO_TREE_OBSERVATIONS"]}

    values: Dict[str, Any] = {}
    requested_set = set(requested)
    reference_cache: Dict[tuple[str, str], List[float]] = {}
    records = []
    for row in rows:
        tree = dict(row)
        metrics = _tree_morphology_for_row(tree)
        if requested_set & {
            "TREE_DBH_PERCENTILE_WITHIN_REFERENCE_GROUP",
            "TREE_HEIGHT_PERCENTILE_WITHIN_REFERENCE_GROUP",
            "TREE_CROWN_WIDTH_PERCENTILE_WITHIN_REFERENCE_GROUP",
        }:
            species = str(tree.get("species") or "").strip()
            if species:
                for field, indicator_id in [
                    ("tree_dbh_cm", "TREE_DBH_PERCENTILE_WITHIN_REFERENCE_GROUP"),
                    ("tree_height_m", "TREE_HEIGHT_PERCENTILE_WITHIN_REFERENCE_GROUP"),
                    ("crown_width_mean_m", "TREE_CROWN_WIDTH_PERCENTILE_WITHIN_REFERENCE_GROUP"),
                ]:
                    if indicator_id not in requested_set:
                        continue
                    key = (species, field)
                    if key not in reference_cache:
                        reference_cache[key] = _load_species_reference_values(conn, species, field)
                    metrics[indicator_id] = _percentile_rank(tree.get(field), reference_cache[key])
            else:
                quality_flags.append("SPECIES_MISSING_FOR_PERCENTILE_REFERENCE")
        records.append({"tree_id": tree.get("tree_id"), "subplot_id": tree.get("subplot_id"), "species": tree.get("species"), **metrics})

    for indicator_id in requested_set:
        indicator_values = [r[indicator_id] for r in records if indicator_id in r and r[indicator_id] is not None]
        if not indicator_values:
            continue
        if target_type == "tree" and len(records) == 1:
            values[indicator_id] = indicator_values[0]
        else:
            values[indicator_id] = _numeric_summary(indicator_values)
    values["TREE_MORPHOLOGY_RECORDS"] = records[:50]
    return {"indicator_values": values, "quality_flags": quality_flags}


def compute_tree_competition_indicators(
    conn: sqlite3.Connection,
    target_type: str,
    target_id: str,
    requested: Iterable[str],
    radius_m: float,
) -> Dict[str, Any]:
    if target_type == "tree":
        target_row = conn.execute("SELECT subplot_id FROM tree_observations WHERE tree_id=? LIMIT 1", (target_id,)).fetchone()
        if target_row is None:
            return {"indicator_values": {}, "quality_flags": ["TARGET_TREE_NOT_FOUND"]}
        subplot_id = target_row["subplot_id"]
    else:
        subplot_id = target_id

    rows = conn.execute(
        """
        SELECT tree_id, subplot_id, tree_x_m, tree_y_m, species, tree_dbh_cm, tree_height_m
        FROM tree_observations
        WHERE subplot_id=? AND tree_x_m IS NOT NULL AND tree_y_m IS NOT NULL AND tree_dbh_cm IS NOT NULL AND tree_dbh_cm > 0
        """,
        (subplot_id,),
    ).fetchall()
    if not rows:
        return {"indicator_values": {}, "quality_flags": ["NO_SPATIAL_TREE_OBSERVATIONS"]}

    trees = [dict(r) for r in rows]
    records = []
    for tree in trees:
        if target_type == "tree" and tree["tree_id"] != target_id:
            continue
        records.append(_competition_for_tree(tree, trees, radius_m))

    requested_set = set(requested)
    values: Dict[str, Any] = {}
    for indicator_id in requested_set:
        indicator_values = [r[indicator_id] for r in records if indicator_id in r and r[indicator_id] is not None]
        if not indicator_values:
            continue
        values[indicator_id] = indicator_values[0] if target_type == "tree" and len(records) == 1 else _numeric_summary(indicator_values)
    values["TREE_COMPETITION_RECORDS"] = records[:50]
    return {"indicator_values": values, "quality_flags": []}


def compute_subplot_structure_indicators(
    conn: sqlite3.Connection,
    target_type: str,
    target_id: str,
    requested: Iterable[str],
) -> Dict[str, Any]:
    subplot_id = target_id if target_type == "subplot" else _subplot_for_tree(conn, target_id)
    if not subplot_id:
        return {"indicator_values": {}, "quality_flags": ["SUBPLOT_NOT_RESOLVED"]}

    rows = conn.execute(
        """
        SELECT tree_id, species, tree_dbh_cm, tree_height_m
        FROM tree_observations
        WHERE subplot_id=? AND tree_dbh_cm IS NOT NULL AND tree_dbh_cm > 0
        """,
        (subplot_id,),
    ).fetchall()
    if not rows:
        return {"indicator_values": {}, "quality_flags": ["NO_TREE_OBSERVATIONS"]}

    trees = [dict(r) for r in rows]
    dbhs = [_float(t.get("tree_dbh_cm")) for t in trees if _float(t.get("tree_dbh_cm")) is not None and _float(t.get("tree_dbh_cm")) > 0]
    heights = [_float(t.get("tree_height_m")) for t in trees if _float(t.get("tree_height_m")) is not None and _float(t.get("tree_height_m")) > 0]
    species_counts = Counter(str(t.get("species") or "").strip() or "未明确乔木" for t in trees)
    requested_set = set(requested)

    basal_areas = [_basal_area_m2(d) for d in dbhs]
    values: Dict[str, Any] = {}
    n = len(trees)
    species_count = len(species_counts)
    total_ba = sum(basal_areas)
    proportions = [c / n for c in species_counts.values()] if n else []

    candidates = {
        "SUBPLOT_TREE_COUNT": n,
        "SUBPLOT_SPECIES_COUNT": species_count,
        "SUBPLOT_STAND_DENSITY": round(n / SUBPLOT_AREA_HA, 3),
        "SUBPLOT_MEAN_DBH": _mean(dbhs),
        "SUBPLOT_MEAN_HEIGHT": _mean(heights),
        "SUBPLOT_QUADRATIC_MEAN_DBH": round(math.sqrt(sum(d * d for d in dbhs) / len(dbhs)), 3) if dbhs else None,
        "SUBPLOT_TOTAL_BASAL_AREA": round(total_ba, 6),
        "SUBPLOT_BASAL_AREA_PER_HA": round(total_ba / SUBPLOT_AREA_HA, 6),
        "SUBPLOT_DIAMETER_CLASS_DISTRIBUTION": _diameter_class_distribution(dbhs),
        "SUBPLOT_SPECIES_COMPOSITION": dict(species_counts),
        "SUBPLOT_SHANNON_INDEX": round(-sum(p * math.log(p) for p in proportions if p > 0), 6) if proportions else None,
        "SUBPLOT_SIMPSON_DIVERSITY_INDEX": round(1.0 - sum(p * p for p in proportions), 6) if proportions else None,
        "SUBPLOT_PIELOU_EVENNESS": None,
        "SUBPLOT_DBH_COEFFICIENT_OF_VARIATION": _coefficient_of_variation(dbhs),
        "SUBPLOT_MIXING_RATIO": round(1.0 - (max(species_counts.values()) / n), 6) if n else None,
        "SUBPLOT_HIGH_HDR_TREE_RATIO": _high_hdr_ratio(trees),
    }
    if candidates["SUBPLOT_SHANNON_INDEX"] is not None and species_count > 1:
        candidates["SUBPLOT_PIELOU_EVENNESS"] = round(candidates["SUBPLOT_SHANNON_INDEX"] / math.log(species_count), 6)

    for indicator_id in requested_set:
        if indicator_id in candidates and candidates[indicator_id] is not None:
            values[indicator_id] = candidates[indicator_id]
    return {"indicator_values": values, "quality_flags": []}


def compute_topography_indicators(
    conn: sqlite3.Connection,
    target_type: str,
    target_id: str,
    requested: Iterable[str],
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    if target_type == "tree":
        rows = conn.execute("SELECT * FROM topography_observations WHERE tree_id=?", (target_id,)).fetchall()
    elif target_type == "subplot":
        rows = conn.execute("SELECT * FROM topography_observations WHERE subplot_id=?", (target_id,)).fetchall()
    else:
        limit = int(parameters.get("limit", 10000))
        rows = conn.execute("SELECT * FROM topography_observations LIMIT ?", (limit,)).fetchall()
    if not rows:
        return {"indicator_values": {}, "quality_flags": ["NO_TOPOGRAPHY_OBSERVATIONS"]}

    records = []
    for row in rows:
        rec = dict(row)
        aspect = _float(rec.get("aspect_degree"))
        slope = _float(rec.get("slope_degree"))
        elevation = _float(rec.get("elevation_m"))
        records.append({
            "tree_id": rec.get("tree_id"),
            "subplot_id": rec.get("subplot_id"),
            "TOPOGRAPHY_NORTHNESS": round(math.cos(math.radians(aspect)), 6) if aspect is not None else None,
            "TOPOGRAPHY_EASTNESS": round(math.sin(math.radians(aspect)), 6) if aspect is not None else None,
            "TOPOGRAPHY_ASPECT_CLASS": _aspect_class(aspect),
            "TOPOGRAPHY_SLOPE_CLASS": _slope_class(slope),
            "TOPOGRAPHY_ELEVATION_BAND": _elevation_band(elevation, int(parameters.get("elevation_band_width_m", 100))),
        })

    requested_set = set(requested)
    values: Dict[str, Any] = {}
    for indicator_id in requested_set:
        vals = [r[indicator_id] for r in records if indicator_id in r and r[indicator_id] is not None]
        if not vals:
            continue
        if target_type == "tree" and len(records) == 1:
            values[indicator_id] = vals[0]
        elif all(isinstance(v, (int, float)) for v in vals):
            values[indicator_id] = _numeric_summary(vals)
        else:
            values[indicator_id] = dict(Counter(vals))
    values["TOPOGRAPHY_DERIVED_RECORDS"] = records[:50]
    return {"indicator_values": values, "quality_flags": []}


def compute_climate_indicators(
    conn: sqlite3.Connection,
    requested: Iterable[str],
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    station_id = str(parameters.get("station_id") or "").strip()
    start_year = parameters.get("start_year")
    end_year = parameters.get("end_year")
    months = _parse_months(parameters.get("months"))
    if parameters.get("growing_season", False) and not months:
        months = [5, 6, 7, 8, 9]

    rows = _load_climate_daily_rows(conn, station_id, start_year, end_year, months)
    if not rows:
        return {"indicator_values": {}, "quality_flags": ["NO_CLIMATE_DAILY_OBSERVATIONS"]}

    requested_set = set(requested)
    records = [dict(r) for r in rows]
    by_year: Dict[int, List[Dict[str, Any]]] = {}
    for rec in records:
        year = int(str(rec["observation_date"])[:4])
        by_year.setdefault(year, []).append(rec)

    baseline_start = int(parameters.get("baseline_start", 1991))
    baseline_end = int(parameters.get("baseline_end", 2020))
    baseline_rows = [r for r in records if baseline_start <= int(str(r["observation_date"])[:4]) <= baseline_end]
    baseline_temp = _mean([_float(r.get("mean_temperature_c")) for r in baseline_rows])
    baseline_precip = _sum([_float(r.get("precipitation_mm")) for r in baseline_rows])
    baseline_year_count = len({int(str(r["observation_date"])[:4]) for r in baseline_rows})
    baseline_precip_per_year = baseline_precip / baseline_year_count if baseline_year_count else None

    heat_threshold_c = float(parameters.get("heat_threshold_c", 30.0))
    frost_threshold_c = float(parameters.get("frost_threshold_c", 0.0))
    heavy_precip_threshold_mm = float(parameters.get("heavy_precip_threshold_mm", 25.0))
    dry_precip_threshold_mm = float(parameters.get("dry_precip_threshold_mm", 0.1))
    extreme_cold_threshold_c = float(parameters.get("extreme_cold_threshold_c", -10.0))
    strong_wind_threshold_m_s = float(parameters.get("strong_wind_threshold_m_s", 10.8))
    suspicious_precip_threshold_mm = float(parameters.get("suspicious_precip_threshold_mm", 500.0))
    min_run_days = parameters.get("min_run_days")
    min_run_days = int(min_run_days) if min_run_days not in (None, "", 0, "0") else None

    annual_records = []
    quality_flags: List[str] = []
    for year, year_rows in sorted(by_year.items()):
        mean_temp = _mean([_float(r.get("mean_temperature_c")) for r in year_rows])
        total_precip = _sum([_float(r.get("precipitation_mm")) for r in year_rows])
        temp_anomaly = mean_temp - baseline_temp if mean_temp is not None and baseline_temp is not None else None
        precip_anomaly = total_precip - baseline_precip_per_year if baseline_precip_per_year is not None else None
        suspicious_days = sum(1 for r in year_rows if (_float(r.get("precipitation_mm")) or 0) > suspicious_precip_threshold_mm)
        dry_periods = _dry_periods(year_rows, dry_precip_threshold_mm, min_run_days or 1)
        if suspicious_days:
            quality_flags.append("SUSPICIOUS_DAILY_PRECIPITATION_PRESENT")
        annual_records.append({
            "year": year,
            "record_count": len(year_rows),
            "CLIMATE_ANNUAL_MEAN_TEMPERATURE": _round(mean_temp),
            "CLIMATE_ANNUAL_PRECIPITATION": _round(total_precip),
            "CLIMATE_GROWING_SEASON_MEAN_TEMPERATURE": _round(mean_temp),
            "CLIMATE_GROWING_SEASON_PRECIPITATION": _round(total_precip),
            "CLIMATE_TEMPERATURE_ANOMALY": _round(temp_anomaly),
            "CLIMATE_PRECIPITATION_ANOMALY": _round(precip_anomaly),
            "CLIMATE_PRECIPITATION_ANOMALY_PERCENT": _round(precip_anomaly / baseline_precip_per_year * 100.0) if baseline_precip_per_year else None,
            "CLIMATE_HEAT_DAYS": _count_days(year_rows, "max_temperature_c", lambda v: v >= heat_threshold_c),
            "CLIMATE_FROST_DAYS": _count_days(year_rows, "min_temperature_c", lambda v: v < frost_threshold_c),
            "CLIMATE_HEAVY_PRECIPITATION_DAYS": _count_days(year_rows, "precipitation_mm", lambda v: v >= heavy_precip_threshold_mm),
            "CLIMATE_MAX_CONSECUTIVE_DRY_DAYS": _max_consecutive_dry_days(year_rows, dry_precip_threshold_mm),
            "dry_periods_ge_min_run": dry_periods if min_run_days else [],
            "matches_min_run_days": bool(min_run_days and dry_periods),
            "CLIMATE_EXTREME_COLD_DAYS": _count_days(year_rows, "min_temperature_c", lambda v: v <= extreme_cold_threshold_c),
            "CLIMATE_STRONG_WIND_DAYS": _count_strong_wind_days(year_rows, strong_wind_threshold_m_s),
            "CLIMATE_ANNUAL_MAX_WIND_SPEED": _round(max([_float(r.get("max_wind_speed_m_s")) or _float(r.get("wind_gust_m_s")) or 0 for r in year_rows] or [0])),
            "suspicious_precipitation_days": suspicious_days,
        })

    values: Dict[str, Any] = {}
    for indicator_id in requested_set:
        if indicator_id in {"CLIMATE_MONTHLY_MEAN_TEMPERATURE", "CLIMATE_MONTHLY_PRECIPITATION"}:
            values[indicator_id] = _monthly_climate_summary(records, indicator_id)
            continue
        vals = [r[indicator_id] for r in annual_records if indicator_id in r and r[indicator_id] is not None]
        if vals:
            values[indicator_id] = annual_records if len(annual_records) > 1 else vals[0]

    values["CLIMATE_ANNUAL_RECORDS"] = annual_records
    if min_run_days:
        values["CLIMATE_DRY_RUN_YEARS_MATCHING_MIN_RUN"] = [
            {"year": r["year"], "max_consecutive_dry_days": r["CLIMATE_MAX_CONSECUTIVE_DRY_DAYS"], "periods": r["dry_periods_ge_min_run"]}
            for r in annual_records
            if r.get("matches_min_run_days")
        ]
    values["CLIMATE_THRESHOLDS"] = {
        "heat_threshold_c": heat_threshold_c,
        "frost_threshold_c": frost_threshold_c,
        "heavy_precip_threshold_mm": heavy_precip_threshold_mm,
        "dry_precip_threshold_mm": dry_precip_threshold_mm,
        "extreme_cold_threshold_c": extreme_cold_threshold_c,
        "strong_wind_threshold_m_s": strong_wind_threshold_m_s,
        "suspicious_precip_threshold_mm": suspicious_precip_threshold_mm,
        "suspicious_precip_policy": "mark_only_not_excluded",
        "min_run_days": min_run_days,
    }
    return {"indicator_values": values, "quality_flags": quality_flags}


def _resolve_requested_indicators(indicator_ids: Optional[Sequence[str]], indicator_group: str, target_type: str) -> List[str]:
    ids = [str(x).strip() for x in (indicator_ids or []) if str(x).strip()]
    if ids:
        return ids
    group = str(indicator_group or "").strip()
    if group in INDICATOR_GROUPS:
        return sorted(INDICATOR_GROUPS[group])
    target = _normalize_target_type(target_type)
    if target == "tree":
        return sorted(TREE_MORPHOLOGY_INDICATORS | TREE_COMPETITION_INDICATORS | TOPOGRAPHY_INDICATORS)
    if target == "subplot":
        return sorted(SUBPLOT_STRUCTURE_INDICATORS | TOPOGRAPHY_INDICATORS)
    if target == "climate":
        return sorted(CLIMATE_INDICATORS)
    return []


def _partition_indicators(indicator_ids: Sequence[str], contracts: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, set[str]]:
    requested = set(indicator_ids)
    if not contracts:
        return {
            "tree_morphology": requested & TREE_MORPHOLOGY_INDICATORS,
            "tree_competition": requested & TREE_COMPETITION_INDICATORS,
            "subplot_stand_structure": requested & SUBPLOT_STRUCTURE_INDICATORS,
            "topography_derived": requested & TOPOGRAPHY_INDICATORS,
            "climate_background": requested & CLIMATE_INDICATORS,
        }

    plan = {
        "tree_morphology": set(),
        "tree_competition": set(),
        "subplot_stand_structure": set(),
        "topography_derived": set(),
        "climate_background": set(),
    }
    for indicator_id in requested:
        contract = contracts.get(indicator_id, {})
        calc = contract.get("calculation") or {}
        calc_type = calc.get("type")
        level = contract.get("level")
        if level == "station_year" or indicator_id.startswith("CLIMATE_") or calc_type in {"timeseries_aggregation", "timeseries_run_length"}:
            plan["climate_background"].add(indicator_id)
        elif level == "topography" or indicator_id.startswith("TOPOGRAPHY_") or calc_type == "classification":
            plan["topography_derived"].add(indicator_id)
        elif calc_type == "spatial_neighborhood" or indicator_id in TREE_COMPETITION_INDICATORS:
            plan["tree_competition"].add(indicator_id)
        elif level == "subplot" or indicator_id.startswith(("SUBPLOT_", "ID_SHANNON", "ID_SIMPSON", "ID_PIELOU", "ID_STAND", "ID_QUADRATIC", "ID_SUBPLOT")):
            plan["subplot_stand_structure"].add(indicator_id)
        elif level == "tree" or indicator_id.startswith(("TREE_", "ID_HEIGHT", "ID_TREE")):
            plan["tree_morphology"].add(indicator_id)
    return plan


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_target_type(target_type: str) -> str:
    value = str(target_type or "").strip().lower()
    if value in {"tree", "treeindividual", "single_tree", "单木"}:
        return "tree"
    if value in {"subplot", "样方", "quadrat"}:
        return "subplot"
    if value in {"plot", "monitoring_plot", "样地", "all"}:
        return "plot"
    if value in {"climate", "station", "气候"}:
        return "climate"
    return value or "unknown"


def _load_tree_rows(conn: sqlite3.Connection, target_type: str, target_id: str) -> List[sqlite3.Row]:
    if target_type == "tree":
        return conn.execute("SELECT * FROM tree_observations WHERE tree_id=?", (target_id,)).fetchall()
    if target_type == "subplot":
        return conn.execute("SELECT * FROM tree_observations WHERE subplot_id=?", (target_id,)).fetchall()
    return conn.execute("SELECT * FROM tree_observations LIMIT 10000").fetchall()


def _load_species_reference_values(conn: sqlite3.Connection, species: str, field: str) -> List[float]:
    rows = conn.execute(
        f"SELECT {field} AS value FROM tree_observations WHERE species=? AND {field} IS NOT NULL",
        (species,),
    ).fetchall()
    return [float(r["value"]) for r in rows if r["value"] is not None]


def _tree_morphology_for_row(tree: Dict[str, Any]) -> Dict[str, Any]:
    dbh = _float(tree.get("tree_dbh_cm"))
    height = _float(tree.get("tree_height_m"))
    crown_ew = _float(tree.get("crown_width_ew_m"))
    crown_ns = _float(tree.get("crown_width_ns_m"))
    crown_mean = _float(tree.get("crown_width_mean_m"))
    crown_base = _float(tree.get("crown_base_height_m"))
    if crown_mean is None and crown_ew is not None and crown_ns is not None:
        crown_mean = (crown_ew + crown_ns) / 2.0
    crown_length = height - crown_base if height is not None and crown_base is not None else None
    return {
        "TREE_HEIGHT_DIAMETER_RATIO": _round(100.0 * height / dbh) if dbh and height is not None else None,
        "TREE_BASAL_AREA": _round(_basal_area_m2(dbh)) if dbh else None,
        "TREE_MEAN_CROWN_WIDTH": _round(crown_mean),
        "TREE_CROWN_AREA": _round(math.pi * (crown_mean / 2.0) ** 2) if crown_mean is not None else None,
        "TREE_CROWN_ASPECT_RATIO": _round(max(crown_ew, crown_ns) / min(crown_ew, crown_ns)) if crown_ew and crown_ns and min(crown_ew, crown_ns) > 0 else None,
        "TREE_CROWN_ASYMMETRY_RATIO": _round(abs(crown_ew - crown_ns) / ((crown_ew + crown_ns) / 2.0)) if crown_ew and crown_ns and (crown_ew + crown_ns) > 0 else None,
        "TREE_CROWN_LENGTH": _round(crown_length),
        "TREE_LIVE_CROWN_RATIO": _round(crown_length / height) if crown_length is not None and height and height > 0 else None,
        "TREE_VOLUME": _round(_float(tree.get("volume_m3"))),
    }


def _competition_for_tree(tree: Dict[str, Any], all_trees: List[Dict[str, Any]], radius_m: float) -> Dict[str, Any]:
    x = float(tree["tree_x_m"])
    y = float(tree["tree_y_m"])
    dbh = float(tree["tree_dbh_cm"])
    species = str(tree.get("species") or "")
    neighbors = []
    for other in all_trees:
        if other["tree_id"] == tree["tree_id"]:
            continue
        ox = _float(other.get("tree_x_m"))
        oy = _float(other.get("tree_y_m"))
        other_dbh = _float(other.get("tree_dbh_cm"))
        if ox is None or oy is None or other_dbh is None:
            continue
        distance = math.hypot(x - ox, y - oy)
        if distance <= 0:
            continue
        if distance <= radius_m:
            neighbors.append((other, distance))

    nearest = min((d for _, d in neighbors), default=None)
    larger_neighbors = [(n, d) for n, d in neighbors if (_float(n.get("tree_dbh_cm")) or 0) > dbh]
    hegyi = sum(((_float(n.get("tree_dbh_cm")) or 0) / dbh) * (1.0 / d) for n, d in neighbors) if dbh > 0 else None
    neighbor_dbhs = [_float(n.get("tree_dbh_cm")) for n, _ in neighbors]
    neighbor_heights = [_float(n.get("tree_height_m")) for n, _ in neighbors]
    conspecific = sum(1 for n, _ in neighbors if str(n.get("species") or "") == species)
    neighbor_count = len(neighbors)
    return {
        "tree_id": tree.get("tree_id"),
        "subplot_id": tree.get("subplot_id"),
        "species": species,
        "TREE_NEAREST_NEIGHBOR_DISTANCE": _round(nearest),
        "TREE_NEIGHBOR_COUNT_WITHIN_RADIUS": neighbor_count,
        "TREE_LOCAL_DENSITY_WITHIN_RADIUS": _round(neighbor_count / (math.pi * radius_m * radius_m)),
        "TREE_HEGYI_COMPETITION_INDEX": _round(hegyi),
        "TREE_LARGER_NEIGHBOR_COUNT": len(larger_neighbors),
        "TREE_LARGER_NEIGHBOR_BASAL_AREA": _round(sum(_basal_area_m2(_float(n.get("tree_dbh_cm")) or 0) for n, _ in larger_neighbors)),
        "TREE_NEIGHBOR_MEAN_DBH": _mean(neighbor_dbhs),
        "TREE_NEIGHBOR_MEAN_HEIGHT": _mean(neighbor_heights),
        "TREE_CONSPECIFIC_NEIGHBOR_RATIO": _round(conspecific / neighbor_count) if neighbor_count else None,
        "TREE_HETEROSPECIFIC_NEIGHBOR_RATIO": _round((neighbor_count - conspecific) / neighbor_count) if neighbor_count else None,
        "TREE_NEIGHBOR_BASAL_AREA_WITHIN_RADIUS": _round(sum(_basal_area_m2(_float(n.get("tree_dbh_cm")) or 0) for n, _ in neighbors)),
    }


def _load_climate_daily_rows(
    conn: sqlite3.Connection,
    station_id: str,
    start_year: Any,
    end_year: Any,
    months: List[int],
) -> List[sqlite3.Row]:
    clauses = []
    params: List[Any] = []
    if station_id:
        clauses.append("station_id=?")
        params.append(station_id)
    if start_year:
        clauses.append("CAST(substr(observation_date,1,4) AS INT) >= ?")
        params.append(int(start_year))
    if end_year:
        clauses.append("CAST(substr(observation_date,1,4) AS INT) <= ?")
        params.append(int(end_year))
    if months:
        clauses.append(f"CAST(substr(observation_date,6,2) AS INT) IN ({','.join('?' for _ in months)})")
        params.extend(months)
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    return conn.execute(f"SELECT * FROM climate_daily_normalized {where_sql} ORDER BY observation_date", params).fetchall()


def _monthly_climate_summary(records: List[Dict[str, Any]], indicator_id: str) -> List[Dict[str, Any]]:
    grouped: Dict[tuple[int, int], List[Dict[str, Any]]] = {}
    for rec in records:
        d = str(rec["observation_date"])
        grouped.setdefault((int(d[:4]), int(d[5:7])), []).append(rec)
    output = []
    for (year, month), rows in sorted(grouped.items()):
        if indicator_id == "CLIMATE_MONTHLY_MEAN_TEMPERATURE":
            value = _mean([_float(r.get("mean_temperature_c")) for r in rows])
        else:
            value = _sum([_float(r.get("precipitation_mm")) for r in rows])
        output.append({"year": year, "month": month, "value": _round(value), "record_count": len(rows)})
    return output


def _parse_months(value: Any) -> List[int]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [int(x.strip()) for x in value.split(",") if x.strip()]
    return [int(x) for x in value]


def _count_days(rows: List[Dict[str, Any]], field: str, predicate) -> int:
    count = 0
    for row in rows:
        value = _float(row.get(field))
        if value is not None and predicate(value):
            count += 1
    return count


def _count_strong_wind_days(rows: List[Dict[str, Any]], threshold: float) -> int:
    count = 0
    for row in rows:
        values = [_float(row.get("max_wind_speed_m_s")), _float(row.get("wind_gust_m_s")), _float(row.get("wind_speed_m_s"))]
        if max([v for v in values if v is not None] or [0.0]) >= threshold:
            count += 1
    return count




def _dry_periods(rows: List[Dict[str, Any]], threshold: float, min_run_days: int) -> List[Dict[str, Any]]:
    periods: List[Dict[str, Any]] = []
    current_start: Optional[str] = None
    current_end: Optional[str] = None
    current_len = 0
    for row in sorted(rows, key=lambda r: str(r.get("observation_date"))):
        obs_date = str(row.get("observation_date"))
        precip = _float(row.get("precipitation_mm"))
        if precip is not None and precip < threshold:
            if current_start is None:
                current_start = obs_date
            current_end = obs_date
            current_len += 1
        else:
            if current_len >= min_run_days and current_start and current_end:
                periods.append({"start_date": current_start, "end_date": current_end, "days": current_len})
            current_start = None
            current_end = None
            current_len = 0
    if current_len >= min_run_days and current_start and current_end:
        periods.append({"start_date": current_start, "end_date": current_end, "days": current_len})
    return periods

def _max_consecutive_dry_days(rows: List[Dict[str, Any]], threshold: float) -> int:
    max_run = 0
    current = 0
    for row in sorted(rows, key=lambda r: str(r.get("observation_date"))):
        precip = _float(row.get("precipitation_mm"))
        if precip is not None and precip < threshold:
            current += 1
            max_run = max(max_run, current)
        else:
            current = 0
    return max_run


def _subplot_for_tree(conn: sqlite3.Connection, tree_id: str) -> Optional[str]:
    row = conn.execute("SELECT subplot_id FROM tree_observations WHERE tree_id=? LIMIT 1", (tree_id,)).fetchone()
    return row["subplot_id"] if row else None


def _float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: Optional[float], digits: int = 6) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    clean = [float(v) for v in values if v is not None]
    return round(sum(clean) / len(clean), 6) if clean else None


def _sum(values: Iterable[Optional[float]]) -> float:
    return round(sum(float(v) for v in values if v is not None), 6)


def _basal_area_m2(dbh_cm: float) -> float:
    return math.pi * (dbh_cm ** 2) / 40000.0


def _coefficient_of_variation(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return None
    mean_value = sum(values) / len(values)
    if mean_value == 0:
        return None
    variance = sum((v - mean_value) ** 2 for v in values) / (len(values) - 1)
    return round(math.sqrt(variance) / mean_value * 100.0, 6)


def _numeric_summary(values: List[Any]) -> Dict[str, Any]:
    clean = [float(v) for v in values if isinstance(v, (int, float))]
    if not clean:
        return {"count": len(values), "values": values[:20]}
    return {
        "count": len(clean),
        "mean": round(sum(clean) / len(clean), 6),
        "min": round(min(clean), 6),
        "max": round(max(clean), 6),
    }


def _percentile_rank(value: Any, reference_values: List[float]) -> Optional[float]:
    val = _float(value)
    if val is None or not reference_values:
        return None
    less_equal = sum(1 for ref in reference_values if ref <= val)
    return round(less_equal / len(reference_values) * 100.0, 3)


def _diameter_class_distribution(dbhs: List[float], bin_width: int = 5) -> Dict[str, int]:
    bins: Dict[str, int] = {}
    for dbh in dbhs:
        lower = int(dbh // bin_width) * bin_width
        upper = lower + bin_width
        bins[f"{lower}-{upper}"] = bins.get(f"{lower}-{upper}", 0) + 1
    return dict(sorted(bins.items(), key=lambda item: int(item[0].split("-")[0])))


def _high_hdr_ratio(trees: List[Dict[str, Any]], threshold: float = 80.0) -> Optional[float]:
    valid = 0
    high = 0
    for tree in trees:
        dbh = _float(tree.get("tree_dbh_cm"))
        height = _float(tree.get("tree_height_m"))
        if dbh and height is not None:
            valid += 1
            if 100.0 * height / dbh > threshold:
                high += 1
    return round(high / valid * 100.0, 6) if valid else None


def _aspect_class(aspect: Optional[float]) -> Optional[str]:
    if aspect is None:
        return None
    angle = aspect % 360
    classes = [
        ("北", 337.5, 360.0), ("北", 0.0, 22.5), ("东北", 22.5, 67.5),
        ("东", 67.5, 112.5), ("东南", 112.5, 157.5), ("南", 157.5, 202.5),
        ("西南", 202.5, 247.5), ("西", 247.5, 292.5), ("西北", 292.5, 337.5),
    ]
    for name, low, high in classes:
        if low <= angle < high:
            return name
    return "北"


def _slope_class(slope: Optional[float]) -> Optional[str]:
    if slope is None:
        return None
    if slope < 5:
        return "平坡"
    if slope < 15:
        return "缓坡"
    if slope < 25:
        return "斜坡"
    if slope < 35:
        return "陡坡"
    return "急陡坡"


def _elevation_band(elevation: Optional[float], width_m: int) -> Optional[str]:
    if elevation is None or width_m <= 0:
        return None
    lower = int(elevation // width_m) * width_m
    return f"{lower}-{lower + width_m}m"


def tool_compute_registered_indicators(
    target_type: str,
    target_id: str = "",
    indicator_ids_json: str = "[]",
    indicator_group: str = "",
    parameters_json: str = "{}",
) -> str:
    """OpenAI tool 兼容包装：返回 JSON 字符串。"""

    try:
        indicator_ids = json.loads(indicator_ids_json) if indicator_ids_json else []
        parameters = json.loads(parameters_json) if parameters_json else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"status": "failed", "error_code": "INVALID_JSON", "message": str(exc)}, ensure_ascii=False)

    result = compute_registered_indicators(
        target_type=target_type,
        target_id=target_id,
        indicator_ids=indicator_ids,
        indicator_group=indicator_group,
        parameters=parameters,
    )
    return json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    print(tool_compute_registered_indicators("climate", "", '["CLIMATE_FROST_DAYS","CLIMATE_HEAT_DAYS"]', "", '{"start_year": 2024, "end_year": 2024}'))
