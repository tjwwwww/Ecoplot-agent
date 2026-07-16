# -*- coding: utf-8 -*-
"""
kg_builder_qilian.py
====================
祁连山森林质量诊断智能体 —— Neo4j 知识图谱全量构建脚本

用途：
- 从标准化 SQLite 数据库读取 600 个样方、全量乔木、灌木、枯死木观测。
- 从 YAML 知识注册表读取变量、指标、公式、模型、诊断规则定义。
- 构建 Neo4j 图谱，用于智能体解释、追溯和跨源关系查询。

核心原则：
1. SQLite/Parquet/Excel 是事实数据和数值计算主存储。
2. Python 工具负责密度、断面积、多样性、HDR、Hegyi 等计算。
3. YAML 注册表负责变量、指标、公式、工具、规则的定义。
4. Neo4j 负责对象、观测、指标、公式、规则、信号、任务之间的语义关系。
5. 图谱不承担复杂数值计算，不把未核验材积、碳汇、水文、病虫害结论写成正式结果。

运行示例：
python kg_builder_qilian.py --db data/qilian_forest.db --registry ontology/forestry_knowledge_registry.yaml --reset
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError as exc:
    raise SystemExit("缺少依赖 pyyaml，请先安装：pip install pyyaml") from exc

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> None:
        return None

try:
    from neo4j import GraphDatabase
except ImportError as exc:
    raise SystemExit("缺少依赖 neo4j，请先安装：pip install neo4j") from exc

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DEFAULT_PROTECTED_AREA = {
    "protected_area_id": "QILIAN_NATIONAL_PARK",
    "name_cn": "祁连山国家公园",
    "boundary_type": "国家级自然保护地",
}

DEFAULT_MONITORING_PLOT = {
    "monitoring_plot_id": "QILIAN_SIGOU_TREE_PLOT_24HA",
    "name_cn": "祁连山寺沟乔木林监测样地",
    "area_m2": 240000.0,
    "plot_type": "乔木林生态监测大样地",
}

DEFAULT_SURVEY_EVENT = {
    "survey_event_id": "EVENT_2023",
    "survey_year": 2023,
    "survey_date_text": "2023",
    "description": "2023年乔木林样地调查",
}

DEFAULT_TAXON_TRAITS: Dict[str, Dict[str, Any]] = {
    "青海云杉": {"scientific_name": "Picea crassifolia", "taxonomic_rank": "Species", "life_form": "Tree", "vegetation_layer": "TreeLayer", "shade_tolerance": "Tolerant", "taxonomy_review_status": "needs_local_backbone_confirmation"},
    "红桦": {"scientific_name": "Betula albosinensis", "taxonomic_rank": "Species", "life_form": "Tree", "vegetation_layer": "TreeLayer", "shade_tolerance": "Intolerant", "taxonomy_review_status": "needs_local_backbone_confirmation"},
    "白桦": {"scientific_name": "Betula platyphylla", "taxonomic_rank": "Species", "life_form": "Tree", "vegetation_layer": "TreeLayer", "shade_tolerance": "Very_Intolerant", "taxonomy_review_status": "needs_local_backbone_confirmation"},
    "山杨": {"scientific_name": "Populus davidiana", "taxonomic_rank": "Species", "life_form": "Tree", "vegetation_layer": "TreeLayer", "shade_tolerance": "Very_Intolerant", "taxonomy_review_status": "needs_local_backbone_confirmation"},
    "祁连圆柏": {"scientific_name": "Sabina przewalskii", "taxonomic_rank": "Species", "life_form": "Tree", "vegetation_layer": "TreeLayer", "shade_tolerance": "Moderate", "taxonomy_review_status": "needs_local_backbone_confirmation"},
    "乌柳": {"scientific_name": "Salix cheilophila", "taxonomic_rank": "Species", "life_form": "Tree", "vegetation_layer": "TreeLayer", "water_niche": "hygrophilous_candidate", "taxonomy_review_status": "needs_local_backbone_confirmation"},
    "花楸": {"scientific_name": "Sorbus pohuashanensis", "taxonomic_rank": "Species", "life_form": "Tree", "vegetation_layer": "TreeLayer", "taxonomy_review_status": "needs_local_backbone_confirmation"},
}

DEFAULT_FIELD_MAPPINGS = [
    {"field_id": "tree_observations.subplot_id", "table_name": "tree_observations", "field_name": "subplot_id", "variable_id": "V_SUBPLOT_ID", "name_cn": "样方号", "unit": "", "source_level": "tree_observation"},
    {"field_id": "tree_observations.tree_id", "table_name": "tree_observations", "field_name": "tree_id", "variable_id": "V_TREE_ID", "name_cn": "树木编号", "unit": "", "source_level": "tree_observation"},
    {"field_id": "tree_observations.species", "table_name": "tree_observations", "field_name": "species", "variable_id": "V_TAXON_NAME_CN", "name_cn": "树种中文名", "unit": "", "source_level": "tree_observation"},
    {"field_id": "tree_observations.tree_dbh_cm", "table_name": "tree_observations", "field_name": "tree_dbh_cm", "variable_id": "V_TREE_DBH_CM", "name_cn": "胸径", "unit": "cm", "source_level": "tree_observation"},
    {"field_id": "tree_observations.tree_height_m", "table_name": "tree_observations", "field_name": "tree_height_m", "variable_id": "V_TREE_HEIGHT_M", "name_cn": "树高", "unit": "m", "source_level": "tree_observation"},
    {"field_id": "tree_observations.tree_x_m", "table_name": "tree_observations", "field_name": "tree_x_m", "variable_id": "V_TREE_X_M", "name_cn": "单木局部X坐标", "unit": "m", "source_level": "tree_observation"},
    {"field_id": "tree_observations.tree_y_m", "table_name": "tree_observations", "field_name": "tree_y_m", "variable_id": "V_TREE_Y_M", "name_cn": "单木局部Y坐标", "unit": "m", "source_level": "tree_observation"},
]

DEFAULT_INDICATORS = [
    {"indicator_id": "I_TREE_COUNT", "name_cn": "样方乔木株数", "canonical_unit": "株", "level": "subplot", "definition": "样方内乔木个体数量。"},
    {"indicator_id": "I_STAND_DENSITY_PER_HA", "name_cn": "林分密度", "canonical_unit": "株/ha", "level": "subplot", "definition": "样方乔木株数折算到每公顷。"},
    {"indicator_id": "I_BASAL_AREA_PER_HA", "name_cn": "断面积", "canonical_unit": "m²/ha", "level": "subplot", "definition": "乔木胸径计算得到的断面积合计折算到每公顷。"},
    {"indicator_id": "I_MEAN_DBH_CM", "name_cn": "平均胸径", "canonical_unit": "cm", "level": "subplot", "definition": "有效胸径记录算术平均。"},
    {"indicator_id": "I_QMD_CM", "name_cn": "平方平均胸径", "canonical_unit": "cm", "level": "subplot", "definition": "sqrt(sum(D²)/n)。"},
    {"indicator_id": "I_MEAN_HEIGHT_M", "name_cn": "平均树高", "canonical_unit": "m", "level": "subplot", "definition": "有效树高记录算术平均。"},
    {"indicator_id": "I_SPECIES_RICHNESS_TREE", "name_cn": "乔木树种丰富度", "canonical_unit": "种", "level": "subplot", "definition": "样方内乔木层不同树种数。"},
    {"indicator_id": "I_SHANNON_TREE", "name_cn": "乔木Shannon多样性指数", "canonical_unit": "index", "level": "subplot", "definition": "-sum(p_i ln p_i)，基于乔木株数比例。"},
    {"indicator_id": "I_MEAN_HDR", "name_cn": "平均高径比", "canonical_unit": "index", "level": "subplot", "definition": "HDR=100*H_m/D_cm 的样方平均值。"},
    {"indicator_id": "I_HDR_ABOVE_80_RATIO_PCT", "name_cn": "HDR高于临时阈值比例", "canonical_unit": "%", "level": "subplot", "definition": "样方内 HDR>80 个体比例。仅为形态关注信号。"},
    {"indicator_id": "I_SHRUB_RICHNESS", "name_cn": "灌木物种丰富度", "canonical_unit": "种", "level": "subplot", "definition": "样方灌木观测记录中不同灌木名称数。"},
    {"indicator_id": "I_DEADWOOD_TOTAL_COUNT", "name_cn": "枯死木记录合计株数", "canonical_unit": "株", "level": "subplot", "definition": "枯死木观测记录中 total_count 合计。"},
]

DEFAULT_FORMULAS = [
    {"knowledge_id": "F_BASAL_AREA_TREE_V1", "name_cn": "单木断面积公式", "expression": "g = pi/4 * (D_cm/100)^2", "produces_indicator": "I_BASAL_AREA_PER_HA", "applicability": "胸径单位为cm。", "version": "1.0"},
    {"knowledge_id": "F_STAND_DENSITY_V1", "name_cn": "林分密度公式", "expression": "density_per_ha = tree_count / area_ha", "produces_indicator": "I_STAND_DENSITY_PER_HA", "applicability": "样方面积已知。", "version": "1.0"},
    {"knowledge_id": "F_HDR_V1", "name_cn": "高径比公式", "expression": "HDR = 100 * H_m / D_cm", "produces_indicator": "I_MEAN_HDR", "applicability": "胸径>0且树高>0。", "version": "1.0"},
    {"knowledge_id": "F_SHANNON_TREE_V1", "name_cn": "乔木Shannon多样性指数", "expression": "H' = -sum(p_i * ln(p_i))", "produces_indicator": "I_SHANNON_TREE", "applicability": "基于样方内乔木株数比例。", "version": "1.0"},
]

PROVISIONAL_DIAGNOSTIC_RULES = [
    {"knowledge_id": "R_MEAN_HDR_P90_ATTENTION", "name_cn": "样方平均高径比P90相对关注规则", "condition_expression": "I_MEAN_HDR percentile >= 90", "result_label": "相对关注信号", "evidence_level": "E_provisional_screening", "interpretation_boundary": "仅表示形态细长程度在样方间相对较高，不等于风折、死亡或病虫害概率。"},
    {"knowledge_id": "R_DENSITY_P90_ATTENTION", "name_cn": "样方密度P90相对关注规则", "condition_expression": "I_STAND_DENSITY_PER_HA percentile >= 90", "result_label": "相对关注信号", "evidence_level": "E_provisional_screening", "interpretation_boundary": "仅表示样方密度相对较高，需结合树种、径级和现场状况解释。"},
    {"knowledge_id": "R_SHANNON_P10_ATTENTION", "name_cn": "乔木多样性P10相对关注规则", "condition_expression": "I_SHANNON_TREE percentile <= 10", "result_label": "相对关注信号", "evidence_level": "E_provisional_screening", "interpretation_boundary": "仅表示样方乔木组成多样性相对较低，不等于生态系统质量低。"},
    {"knowledge_id": "R_DEADWOOD_P90_ATTENTION", "name_cn": "枯死木数量P90相对关注规则", "condition_expression": "I_DEADWOOD_TOTAL_COUNT percentile >= 90", "result_label": "相对关注信号", "evidence_level": "E_provisional_screening", "interpretation_boundary": "仅表示枯死木记录数量相对较高，需要现场核查原因和状态。"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def to_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        val = float(value)
        return val if math.isfinite(val) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        val = float(text)
        return val if math.isfinite(val) else None
    except ValueError:
        return None


def to_int_or_none(value: Any) -> Optional[int]:
    val = to_float_or_none(value)
    return int(val) if val is not None else None


def stable_key(text: str, prefix: str) -> str:
    base = clean_str(text, "UNKNOWN")
    safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", base).strip("_") or "UNKNOWN"
    return f"{prefix}_{safe}"


def parse_subplot_row_col(subplot_id: str) -> Tuple[Optional[int], Optional[int]]:
    s = clean_str(subplot_id)
    if s.isdigit() and len(s) in {3, 4}:
        s = s.zfill(4)
        return int(s[:2]), int(s[2:])
    return None, None


def chunked(items: Sequence[Dict[str, Any]], batch_size: int) -> Iterable[List[Dict[str, Any]]]:
    for i in range(0, len(items), batch_size):
        yield list(items[i:i + batch_size])


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    if not table_exists(conn, table_name):
        return []
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]


def pick_column(columns: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    col_set = set(columns)
    for candidate in candidates:
        if candidate in col_set:
            return candidate
    lower_map = {c.lower(): c for c in columns}
    for candidate in candidates:
        hit = lower_map.get(candidate.lower())
        if hit:
            return hit
    return None


def read_all_rows(conn: sqlite3.Connection, table_name: str) -> List[Dict[str, Any]]:
    if not table_exists(conn, table_name):
        return []
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table_name}").fetchall()]


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def list_to_string_list(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, list):
        return [str(v) for v in values if v is not None]
    return [str(values)]


@dataclass
class DataBundle:
    tree_rows: List[Dict[str, Any]]
    shrub_rows: List[Dict[str, Any]]
    deadwood_rows: List[Dict[str, Any]]
    subplot_ids: List[str]


class SQLiteSource:
    def __init__(self, db_path: Path, subplot_area_m2: float = 400.0) -> None:
        self.db_path = db_path
        self.subplot_area_m2 = subplot_area_m2

    def load(self) -> DataBundle:
        if not self.db_path.exists():
            raise FileNotFoundError(f"SQLite 数据库不存在：{self.db_path}")
        with sqlite3.connect(self.db_path) as conn:
            tree_rows = self._load_tree_observations(conn)
            shrub_rows = self._load_shrub_observations(conn)
            deadwood_rows = self._load_deadwood_observations(conn)
        subplot_ids = sorted({row["subplot_id"] for row in tree_rows + shrub_rows + deadwood_rows if row.get("subplot_id")})
        return DataBundle(tree_rows=tree_rows, shrub_rows=shrub_rows, deadwood_rows=deadwood_rows, subplot_ids=subplot_ids)

    def _load_tree_observations(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        table_name = "tree_observations"
        if not table_exists(conn, table_name):
            available = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            raise RuntimeError("未找到 tree_observations 表。当前表包括：" + ", ".join(available))

        columns = table_columns(conn, table_name)
        column_map = {
            "subplot_id": pick_column(columns, ["subplot_id", "样方号", "小样方号"]),
            "tree_id": pick_column(columns, ["tree_id", "树木编号", "tree_no"]),
            "species": pick_column(columns, ["species", "树种", "树名", "accepted_name_cn"]),
            "tree_dbh_cm": pick_column(columns, ["tree_dbh_cm", "dbh_cm", "胸径/cm", "胸径"]),
            "tree_height_m": pick_column(columns, ["tree_height_m", "height_m", "树高/m", "树高"]),
            "tree_x_m": pick_column(columns, ["tree_x_m", "x_m", "位置_X", "X"]),
            "tree_y_m": pick_column(columns, ["tree_y_m", "y_m", "位置_Y", "Y"]),
            "crown_width_ew_m": pick_column(columns, ["crown_width_ew_m", "冠幅/m_东西"]),
            "crown_width_ns_m": pick_column(columns, ["crown_width_ns_m", "冠幅/m_南北"]),
            "crown_width_mean_m": pick_column(columns, ["crown_width_mean_m", "冠幅/m_平均", "mean_crown_width_m"]),
            "crown_base_height_m": pick_column(columns, ["crown_base_height_m", "枝下高/m", "枝下高"]),
            "branch_count": pick_column(columns, ["branch_count", "分枝数"]),
            "health_status": pick_column(columns, ["health_status", "健康状况"]),
        }
        if column_map["subplot_id"] is None:
            raise RuntimeError("tree_observations 缺少必要字段：subplot_id/样方号")

        source_rows = read_all_rows(conn, table_name)
        normalized: List[Dict[str, Any]] = []
        for idx, row in enumerate(source_rows, start=1):
            subplot_id = clean_str(row.get(column_map["subplot_id"]), "")
            if not subplot_id:
                continue
            raw_tree_id = clean_str(row.get(column_map["tree_id"]) if column_map["tree_id"] else "", "")
            tree_id = raw_tree_id or f"TREE_{subplot_id}_{idx:05d}"
            species = clean_str(row.get(column_map["species"]) if column_map["species"] else "", "未知乔木")
            dbh = to_float_or_none(row.get(column_map["tree_dbh_cm"]) if column_map["tree_dbh_cm"] else None)
            height = to_float_or_none(row.get(column_map["tree_height_m"]) if column_map["tree_height_m"] else None)
            flags: List[str] = []
            if dbh is None:
                flags.append("missing_dbh")
            elif dbh <= 0:
                flags.append("invalid_dbh_nonpositive")
            if height is None:
                flags.append("missing_height")
            elif height <= 0:
                flags.append("invalid_height_nonpositive")
            normalized.append({
                "subplot_id": subplot_id,
                "tree_id": tree_id,
                "tree_local_number": raw_tree_id or tree_id,
                "species": species,
                "taxon_key": stable_key(species, "TAXON"),
                "tree_dbh_cm": dbh,
                "tree_height_m": height,
                "tree_x_m": to_float_or_none(row.get(column_map["tree_x_m"]) if column_map["tree_x_m"] else None),
                "tree_y_m": to_float_or_none(row.get(column_map["tree_y_m"]) if column_map["tree_y_m"] else None),
                "crown_width_ew_m": to_float_or_none(row.get(column_map["crown_width_ew_m"]) if column_map["crown_width_ew_m"] else None),
                "crown_width_ns_m": to_float_or_none(row.get(column_map["crown_width_ns_m"]) if column_map["crown_width_ns_m"] else None),
                "crown_width_mean_m": to_float_or_none(row.get(column_map["crown_width_mean_m"]) if column_map["crown_width_mean_m"] else None),
                "crown_base_height_m": to_float_or_none(row.get(column_map["crown_base_height_m"]) if column_map["crown_base_height_m"] else None),
                "branch_count": to_int_or_none(row.get(column_map["branch_count"]) if column_map["branch_count"] else None),
                "health_status": clean_str(row.get(column_map["health_status"]) if column_map["health_status"] else "", ""),
                "quality_flags": flags,
            })
        return normalized

    def _load_shrub_observations(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        table_name = next((name for name in ["shrub_observations", "shrub_observation", "林下灌木调查数据"] if table_exists(conn, name)), None)
        if not table_name:
            return []
        columns = table_columns(conn, table_name)
        col = {
            "subplot_id": pick_column(columns, ["subplot_id", "小样方号", "样方号"]),
            "species": pick_column(columns, ["species", "植物名称", "shrub_species", "accepted_name_cn"]),
            "count": pick_column(columns, ["count", "株数"]),
            "crown_width_cm": pick_column(columns, ["crown_width_cm", "平均灌丛幅（cm）", "平均冠幅cm"]),
            "height_cm": pick_column(columns, ["height_cm", "平均高度（cm）"]),
            "coverage": pick_column(columns, ["coverage", "盖度"]),
        }
        if not col["subplot_id"]:
            return []
        rows = read_all_rows(conn, table_name)
        normalized = []
        for idx, row in enumerate(rows, start=1):
            subplot_id = clean_str(row.get(col["subplot_id"]), "")
            if not subplot_id:
                continue
            species = clean_str(row.get(col["species"]) if col["species"] else "", "未知灌木")
            normalized.append({
                "observation_id": f"SHRUB_{subplot_id}_{stable_key(species, 'TX')}_{idx}",
                "subplot_id": subplot_id,
                "species": species,
                "taxon_key": stable_key(species, "TAXON"),
                "count": to_float_or_none(row.get(col["count"]) if col["count"] else None),
                "crown_width_cm": to_float_or_none(row.get(col["crown_width_cm"]) if col["crown_width_cm"] else None),
                "height_cm": to_float_or_none(row.get(col["height_cm"]) if col["height_cm"] else None),
                "coverage": to_float_or_none(row.get(col["coverage"]) if col["coverage"] else None),
                "quality_flags": [],
            })
        return normalized

    def _load_deadwood_observations(self, conn: sqlite3.Connection) -> List[Dict[str, Any]]:
        table_name = next((name for name in ["deadwood_observations", "deadwood_observation", "枯死木调查数据"] if table_exists(conn, name)), None)
        if not table_name:
            return []
        columns = table_columns(conn, table_name)
        col = {
            "deadwood_id": pick_column(columns, ["deadwood_id", "id"]),
            "subplot_id": pick_column(columns, ["subplot_id", "小样方号", "样方号"]),
            "species": pick_column(columns, ["species", "枯死木植物名称", "deadwood_species"]),
            "total_count": pick_column(columns, ["total_count", "株数"]),
            "standing_count": pick_column(columns, ["standing_count", "枯立木"]),
            "fallen_count": pick_column(columns, ["fallen_count", "枯倒木"]),
            "remarks": pick_column(columns, ["remarks", "备注"]),
        }
        if not col["subplot_id"]:
            return []
        rows = read_all_rows(conn, table_name)
        normalized = []
        for idx, row in enumerate(rows, start=1):
            subplot_id = clean_str(row.get(col["subplot_id"]), "")
            if not subplot_id:
                continue
            species = clean_str(row.get(col["species"]) if col["species"] else "", "未知枯死木")
            raw_id = clean_str(row.get(col["deadwood_id"]) if col["deadwood_id"] else "", "")
            normalized.append({
                "observation_id": raw_id or f"DEADWOOD_{subplot_id}_{idx:04d}",
                "subplot_id": subplot_id,
                "species": species,
                "taxon_key": stable_key(species, "TAXON"),
                "total_count": to_float_or_none(row.get(col["total_count"]) if col["total_count"] else None),
                "standing_count": to_float_or_none(row.get(col["standing_count"]) if col["standing_count"] else None),
                "fallen_count": to_float_or_none(row.get(col["fallen_count"]) if col["fallen_count"] else None),
                "remarks": clean_str(row.get(col["remarks"]) if col["remarks"] else "", ""),
                "quality_flags": [],
            })
        return normalized


def mean(values: Sequence[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    return sum(vals) / len(vals) if vals else None


def shannon_from_counts(counts: Counter) -> Optional[float]:
    total = sum(counts.values())
    if total <= 0:
        return None
    h = 0.0
    for c in counts.values():
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log(p)
    return h


def calculate_subplot_indicator_values(bundle: DataBundle, survey_event_id: str, subplot_area_m2: float) -> List[Dict[str, Any]]:
    trees_by_subplot = defaultdict(list)
    shrubs_by_subplot = defaultdict(list)
    deadwood_by_subplot = defaultdict(list)
    for row in bundle.tree_rows:
        trees_by_subplot[row["subplot_id"]].append(row)
    for row in bundle.shrub_rows:
        shrubs_by_subplot[row["subplot_id"]].append(row)
    for row in bundle.deadwood_rows:
        deadwood_by_subplot[row["subplot_id"]].append(row)
    area_ha = subplot_area_m2 / 10000.0
    output = []

    def add(subplot_id: str, indicator_id: str, value: Optional[float], unit: str, result_type: str = "formal_result", quality_flags: Optional[List[str]] = None, interpretation_boundary: str = "") -> None:
        if value is None:
            return
        output.append({
            "indicator_value_id": f"IV_SUBPLOT_{subplot_id}_{indicator_id}_{survey_event_id}",
            "target_type": "Subplot",
            "target_id": subplot_id,
            "indicator_id": indicator_id,
            "value": round(float(value), 6),
            "unit": unit,
            "survey_event_id": survey_event_id,
            "result_type": result_type,
            "quality_flags": quality_flags or [],
            "interpretation_boundary": interpretation_boundary,
        })

    for subplot_id in bundle.subplot_ids:
        trees = trees_by_subplot.get(subplot_id, [])
        dbhs = [r["tree_dbh_cm"] for r in trees if r.get("tree_dbh_cm") is not None and r["tree_dbh_cm"] > 0]
        heights = [r["tree_height_m"] for r in trees if r.get("tree_height_m") is not None and r["tree_height_m"] > 0]
        basal_areas = [math.pi * (d / 100.0) ** 2 / 4.0 for d in dbhs]
        hdrs = []
        for r in trees:
            d, h = r.get("tree_dbh_cm"), r.get("tree_height_m")
            if d is not None and h is not None and d > 0 and h > 0:
                hdrs.append(100.0 * h / d)
        species_counter = Counter(clean_str(r.get("species"), "未知乔木") for r in trees)
        shrub_species = {clean_str(r.get("species"), "") for r in shrubs_by_subplot.get(subplot_id, []) if clean_str(r.get("species"), "")}
        deadwood_total = sum(r.get("total_count") or 0.0 for r in deadwood_by_subplot.get(subplot_id, []))
        tree_count = len(trees)
        add(subplot_id, "I_TREE_COUNT", tree_count, "株")
        if area_ha > 0:
            add(subplot_id, "I_STAND_DENSITY_PER_HA", tree_count / area_ha, "株/ha")
            add(subplot_id, "I_BASAL_AREA_PER_HA", sum(basal_areas) / area_ha, "m²/ha")
        add(subplot_id, "I_MEAN_DBH_CM", mean(dbhs), "cm")
        if dbhs:
            add(subplot_id, "I_QMD_CM", math.sqrt(sum(d * d for d in dbhs) / len(dbhs)), "cm")
        add(subplot_id, "I_MEAN_HEIGHT_M", mean(heights), "m")
        add(subplot_id, "I_SPECIES_RICHNESS_TREE", len(species_counter), "种")
        add(subplot_id, "I_SHANNON_TREE", shannon_from_counts(species_counter), "index")
        add(subplot_id, "I_MEAN_HDR", mean(hdrs), "index", interpretation_boundary="HDR为形态细长程度指标，不等于风折、死亡或病虫害概率。")
        if hdrs:
            add(subplot_id, "I_HDR_ABOVE_80_RATIO_PCT", 100.0 * sum(1 for v in hdrs if v > 80.0) / len(hdrs), "%", result_type="relative_attention_signal", quality_flags=["provisional_threshold_hdr_80"], interpretation_boundary="HDR>80为临时筛查阈值，只能作为相对关注信号。")
        add(subplot_id, "I_SHRUB_RICHNESS", len(shrub_species), "种")
        add(subplot_id, "I_DEADWOOD_TOTAL_COUNT", deadwood_total, "株")
    return output


def percentile_rank(values: Sequence[float], value: float) -> float:
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return 50.0
    return 100.0 * sum(1 for v in vals if v <= value) / len(vals)


def build_provisional_signals(indicator_values: List[Dict[str, Any]], survey_event_id: str) -> List[Dict[str, Any]]:
    values_by_indicator = defaultdict(list)
    row_by_subplot_indicator = {}
    for row in indicator_values:
        val = row.get("value")
        if val is None:
            continue
        values_by_indicator[row["indicator_id"]].append(float(val))
        row_by_subplot_indicator[(row["target_id"], row["indicator_id"])] = row
    specs = [
        ("I_MEAN_HDR", "R_MEAN_HDR_P90_ATTENTION", "high_mean_hdr_attention", "high", 90.0),
        ("I_STAND_DENSITY_PER_HA", "R_DENSITY_P90_ATTENTION", "high_density_attention", "high", 90.0),
        ("I_SHANNON_TREE", "R_SHANNON_P10_ATTENTION", "low_tree_diversity_attention", "low", 10.0),
        ("I_DEADWOOD_TOTAL_COUNT", "R_DEADWOOD_P90_ATTENTION", "high_deadwood_count_attention", "high", 90.0),
    ]
    signals = []
    for indicator_id, rule_id, signal_type, direction, threshold in specs:
        values = values_by_indicator.get(indicator_id, [])
        if len(values) < 10:
            continue
        for (subplot_id, ind_id), iv in row_by_subplot_indicator.items():
            if ind_id != indicator_id:
                continue
            val = float(iv["value"])
            p = percentile_rank(values, val)
            attention = (p >= threshold) if direction == "high" else (p <= threshold)
            if not attention:
                continue
            signals.append({
                "signal_id": f"SIG_SUBPLOT_{subplot_id}_{signal_type}_{survey_event_id}",
                "target_type": "Subplot",
                "target_id": subplot_id,
                "signal_type": signal_type,
                "result_label": "相对关注信号",
                "severity": "attention",
                "percentile": round(p, 3),
                "indicator_value_id": iv["indicator_value_id"],
                "rule_id": rule_id,
                "explanation": f"{indicator_id} 在当前样方组中的百分位为 {p:.1f}，用于外业核查优先级排序，不等同于灾害、死亡或病虫害结论。",
                "survey_event_id": survey_event_id,
                "evidence_level": "E_provisional_screening",
            })
    return signals


class QilianKGBuilder:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j", batch_size: int = 1000) -> None:
        if not uri:
            raise ValueError("NEO4J_URI 不能为空。")
        if not user:
            raise ValueError("NEO4J_USER 不能为空。")
        if not password:
            raise ValueError("NEO4J_PASSWORD 不能为空。请不要在代码中硬编码密码。")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database
        self.batch_size = batch_size

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> List[Dict[str, Any]]:
        with self.driver.session(database=self.database) as session:
            result = session.run(cypher, **params)
            data = result.data()
            result.consume()
            return data

    def verify_connectivity(self) -> None:
        self.driver.verify_connectivity()

    def reset_graph_scope(self) -> None:
        labels = ["ProtectedArea", "MonitoringPlot", "Subplot", "TreeIndividual", "Taxon", "SurveyEvent", "TreeObservation", "ShrubObservation", "DeadwoodObservation", "TopographyContext", "ClimateStation", "ClimateMonthlySummary", "ClimateAnnualSummary", "DatabaseField", "VariableDefinition", "IndicatorDefinition", "FormulaDefinition", "ModelDefinition", "ToolDefinition", "DiagnosticRule", "CalculationRun", "IndicatorValue", "DiagnosticSignal", "InspectionTask", "FieldObservation", "ValidationResult", "EvidenceSource"]
        self.run("""
        MATCH (n)
        WHERE any(label IN labels(n) WHERE label IN $labels)
        DETACH DELETE n
        """, labels=labels)

    def create_constraints(self) -> None:
        constraints = [
            "CREATE CONSTRAINT protected_area_id IF NOT EXISTS FOR (n:ProtectedArea) REQUIRE n.protected_area_id IS UNIQUE",
            "CREATE CONSTRAINT monitoring_plot_id IF NOT EXISTS FOR (n:MonitoringPlot) REQUIRE n.monitoring_plot_id IS UNIQUE",
            "CREATE CONSTRAINT subplot_id IF NOT EXISTS FOR (n:Subplot) REQUIRE n.subplot_id IS UNIQUE",
            "CREATE CONSTRAINT tree_id IF NOT EXISTS FOR (n:TreeIndividual) REQUIRE n.tree_id IS UNIQUE",
            "CREATE CONSTRAINT taxon_key IF NOT EXISTS FOR (n:Taxon) REQUIRE n.taxon_key IS UNIQUE",
            "CREATE CONSTRAINT survey_event_id IF NOT EXISTS FOR (n:SurveyEvent) REQUIRE n.survey_event_id IS UNIQUE",
            "CREATE CONSTRAINT tree_observation_id IF NOT EXISTS FOR (n:TreeObservation) REQUIRE n.observation_id IS UNIQUE",
            "CREATE CONSTRAINT shrub_observation_id IF NOT EXISTS FOR (n:ShrubObservation) REQUIRE n.observation_id IS UNIQUE",
            "CREATE CONSTRAINT deadwood_observation_id IF NOT EXISTS FOR (n:DeadwoodObservation) REQUIRE n.observation_id IS UNIQUE",
            "CREATE CONSTRAINT database_field_id IF NOT EXISTS FOR (n:DatabaseField) REQUIRE n.field_id IS UNIQUE",
            "CREATE CONSTRAINT variable_id IF NOT EXISTS FOR (n:VariableDefinition) REQUIRE n.variable_id IS UNIQUE",
            "CREATE CONSTRAINT indicator_id IF NOT EXISTS FOR (n:IndicatorDefinition) REQUIRE n.indicator_id IS UNIQUE",
            "CREATE CONSTRAINT formula_knowledge_id IF NOT EXISTS FOR (n:FormulaDefinition) REQUIRE n.knowledge_id IS UNIQUE",
            "CREATE CONSTRAINT model_knowledge_id IF NOT EXISTS FOR (n:ModelDefinition) REQUIRE n.knowledge_id IS UNIQUE",
            "CREATE CONSTRAINT tool_name IF NOT EXISTS FOR (n:ToolDefinition) REQUIRE n.tool_name IS UNIQUE",
            "CREATE CONSTRAINT diagnostic_rule_id IF NOT EXISTS FOR (n:DiagnosticRule) REQUIRE n.knowledge_id IS UNIQUE",
            "CREATE CONSTRAINT calculation_run_id IF NOT EXISTS FOR (n:CalculationRun) REQUIRE n.run_id IS UNIQUE",
            "CREATE CONSTRAINT indicator_value_id IF NOT EXISTS FOR (n:IndicatorValue) REQUIRE n.indicator_value_id IS UNIQUE",
            "CREATE CONSTRAINT diagnostic_signal_id IF NOT EXISTS FOR (n:DiagnosticSignal) REQUIRE n.signal_id IS UNIQUE",
            "CREATE CONSTRAINT topography_context_id IF NOT EXISTS FOR (n:TopographyContext) REQUIRE n.context_id IS UNIQUE",
            "CREATE CONSTRAINT climate_station_id IF NOT EXISTS FOR (n:ClimateStation) REQUIRE n.station_id IS UNIQUE",
            "CREATE CONSTRAINT climate_monthly_summary_id IF NOT EXISTS FOR (n:ClimateMonthlySummary) REQUIRE n.summary_id IS UNIQUE",
            "CREATE CONSTRAINT climate_annual_summary_id IF NOT EXISTS FOR (n:ClimateAnnualSummary) REQUIRE n.summary_id IS UNIQUE",
        ]
        for cypher in constraints:
            self.run(cypher)

    def import_area_plot_event(self, protected_area: Dict[str, Any], monitoring_plot: Dict[str, Any], survey_event: Dict[str, Any]) -> None:
        self.run("""
        MERGE (pa:ProtectedArea {protected_area_id: $pa.protected_area_id}) SET pa += $pa
        MERGE (mp:MonitoringPlot {monitoring_plot_id: $mp.monitoring_plot_id}) SET mp += $mp
        MERGE (event:SurveyEvent {survey_event_id: $event.survey_event_id}) SET event += $event
        MERGE (pa)-[:HAS_PLOT]->(mp)
        MERGE (mp)-[:HAS_SURVEY_EVENT]->(event)
        """, pa=protected_area, mp=monitoring_plot, event=survey_event)

    def import_subplots(self, subplot_ids: Sequence[str], monitoring_plot_id: str, subplot_area_m2: float) -> None:
        rows = []
        for subplot_id in subplot_ids:
            row_idx, col_idx = parse_subplot_row_col(subplot_id)
            rows.append({
                "subplot_id": subplot_id,
                "subplot_number": subplot_id,
                "area_m2": float(subplot_area_m2),
                "area_ha": float(subplot_area_m2) / 10000.0,
                "grid_row": row_idx,
                "grid_col": col_idx,
                "grid_x_m": (col_idx - 1) * 20.0 if col_idx else None,
                "grid_y_m": (row_idx - 1) * 20.0 if row_idx else None,
                "local_x_min_m": 0.0,
                "local_x_max_m": 20.0,
                "local_y_min_m": 0.0,
                "local_y_max_m": 20.0,
            })
        for batch in chunked(rows, self.batch_size):
            self.run("""
            MATCH (mp:MonitoringPlot {monitoring_plot_id: $monitoring_plot_id})
            UNWIND $batch AS row
            MERGE (s:Subplot {subplot_id: row.subplot_id}) SET s += row
            MERGE (mp)-[:HAS_SUBPLOT]->(s)
            """, monitoring_plot_id=monitoring_plot_id, batch=batch)

    def import_taxa_from_rows(self, bundle: DataBundle) -> None:
        taxon_records: Dict[str, Dict[str, Any]] = {}
        def add_taxon(species: str, life_form: str, layer: str) -> None:
            species_name = clean_str(species, "")
            if not species_name:
                return
            key = stable_key(species_name, "TAXON")
            traits = dict(DEFAULT_TAXON_TRAITS.get(species_name, {}))
            traits.setdefault("taxonomic_rank", "Unknown")
            traits.setdefault("taxonomy_review_status", "unreviewed_source_name")
            traits["taxon_key"] = key
            traits["accepted_name_cn"] = species_name
            traits["source_name_cn"] = species_name
            traits["life_form"] = traits.get("life_form") or life_form
            traits["vegetation_layer"] = traits.get("vegetation_layer") or layer
            taxon_records[key] = traits
        for row in bundle.tree_rows:
            add_taxon(row.get("species"), "Tree", "TreeLayer")
        for row in bundle.shrub_rows:
            add_taxon(row.get("species"), "Shrub", "ShrubLayer")
        for row in bundle.deadwood_rows:
            add_taxon(row.get("species"), "Unknown", "DeadwoodLayer")
        for batch in chunked(list(taxon_records.values()), self.batch_size):
            self.run("""
            UNWIND $batch AS row
            MERGE (tx:Taxon {taxon_key: row.taxon_key}) SET tx += row
            """, batch=batch)

    def import_tree_observations(self, tree_rows: Sequence[Dict[str, Any]], survey_event_id: str) -> None:
        rows = []
        for row in tree_rows:
            obs_id = f"OBS_TREE_{row['tree_id']}_{survey_event_id}"
            obs_props = {
                "observation_id": obs_id,
                "survey_event_id": survey_event_id,
                "tree_x_m": row.get("tree_x_m"),
                "tree_y_m": row.get("tree_y_m"),
                "tree_dbh_cm": row.get("tree_dbh_cm"),
                "tree_height_m": row.get("tree_height_m"),
                "crown_width_ew_m": row.get("crown_width_ew_m"),
                "crown_width_ns_m": row.get("crown_width_ns_m"),
                "crown_width_mean_m": row.get("crown_width_mean_m"),
                "crown_base_height_m": row.get("crown_base_height_m"),
                "branch_count": row.get("branch_count"),
                "health_status": row.get("health_status"),
                "quality_flags": row.get("quality_flags") or [],
                "data_layer": "observation",
            }
            rows.append({"subplot_id": row["subplot_id"], "tree_id": row["tree_id"], "tree_local_number": row.get("tree_local_number"), "taxon_key": row["taxon_key"], "species": row["species"], "obs": obs_props})
        for batch in chunked(rows, self.batch_size):
            self.run("""
            MATCH (event:SurveyEvent {survey_event_id: $survey_event_id})
            UNWIND $batch AS row
            MATCH (s:Subplot {subplot_id: row.subplot_id})
            MERGE (tx:Taxon {taxon_key: row.taxon_key})
            SET tx.accepted_name_cn = coalesce(tx.accepted_name_cn, row.species), tx.source_name_cn = coalesce(tx.source_name_cn, row.species)
            MERGE (t:TreeIndividual {tree_id: row.tree_id})
            SET t.tree_local_number = row.tree_local_number, t.source_subplot_id = row.subplot_id
            MERGE (obs:TreeObservation {observation_id: row.obs.observation_id}) SET obs += row.obs
            MERGE (s)-[:HAS_TREE]->(t)
            MERGE (t)-[:HAS_TAXON]->(tx)
            MERGE (t)-[:HAS_OBSERVATION]->(obs)
            MERGE (obs)-[:OBSERVED_IN]->(s)
            MERGE (event)-[:RECORDED_OBSERVATION]->(obs)
            """, survey_event_id=survey_event_id, batch=batch)

    def import_shrub_observations(self, shrub_rows: Sequence[Dict[str, Any]], survey_event_id: str) -> None:
        rows = []
        for row in shrub_rows:
            rows.append({"subplot_id": row["subplot_id"], "taxon_key": row["taxon_key"], "species": row["species"], "obs": {"observation_id": f"{row['observation_id']}_{survey_event_id}", "survey_event_id": survey_event_id, "species_name": row.get("species"), "count": row.get("count"), "crown_width_cm": row.get("crown_width_cm"), "height_cm": row.get("height_cm"), "coverage": row.get("coverage"), "quality_flags": row.get("quality_flags") or [], "data_layer": "observation"}})
        for batch in chunked(rows, self.batch_size):
            self.run("""
            MATCH (event:SurveyEvent {survey_event_id: $survey_event_id})
            UNWIND $batch AS row
            MATCH (s:Subplot {subplot_id: row.subplot_id})
            MERGE (tx:Taxon {taxon_key: row.taxon_key})
            SET tx.accepted_name_cn = coalesce(tx.accepted_name_cn, row.species), tx.source_name_cn = coalesce(tx.source_name_cn, row.species), tx.life_form = coalesce(tx.life_form, "Shrub"), tx.vegetation_layer = coalesce(tx.vegetation_layer, "ShrubLayer")
            MERGE (obs:ShrubObservation {observation_id: row.obs.observation_id}) SET obs += row.obs
            MERGE (s)-[:HAS_SHRUB_OBSERVATION]->(obs)
            MERGE (obs)-[:HAS_TAXON]->(tx)
            MERGE (event)-[:RECORDED_OBSERVATION]->(obs)
            """, survey_event_id=survey_event_id, batch=batch)

    def import_deadwood_observations(self, deadwood_rows: Sequence[Dict[str, Any]], survey_event_id: str) -> None:
        rows = []
        for row in deadwood_rows:
            rows.append({"subplot_id": row["subplot_id"], "taxon_key": row["taxon_key"], "species": row["species"], "obs": {"observation_id": f"{row['observation_id']}_{survey_event_id}", "survey_event_id": survey_event_id, "species_name": row.get("species"), "total_count": row.get("total_count"), "standing_count": row.get("standing_count"), "fallen_count": row.get("fallen_count"), "remarks": row.get("remarks"), "quality_flags": row.get("quality_flags") or [], "data_layer": "observation"}})
        for batch in chunked(rows, self.batch_size):
            self.run("""
            MATCH (event:SurveyEvent {survey_event_id: $survey_event_id})
            UNWIND $batch AS row
            MATCH (s:Subplot {subplot_id: row.subplot_id})
            MERGE (tx:Taxon {taxon_key: row.taxon_key})
            SET tx.accepted_name_cn = coalesce(tx.accepted_name_cn, row.species), tx.source_name_cn = coalesce(tx.source_name_cn, row.species)
            MERGE (obs:DeadwoodObservation {observation_id: row.obs.observation_id}) SET obs += row.obs
            MERGE (s)-[:HAS_DEADWOOD_OBSERVATION]->(obs)
            MERGE (obs)-[:HAS_TAXON]->(tx)
            MERGE (event)-[:RECORDED_OBSERVATION]->(obs)
            """, survey_event_id=survey_event_id, batch=batch)

    def import_environment_contexts_from_sqlite(self, db_path: Path, monitoring_plot_id: str) -> Dict[str, int]:
        """导入环境上下文摘要。逐日气候原始值仍留在 SQLite，仅导入站点、月摘要、年摘要和样方地形摘要。"""
        counts = {"topography_contexts": 0, "climate_stations": 0, "climate_monthly_summaries": 0, "climate_annual_summaries": 0}
        if not db_path.exists():
            return counts

        def rows_from(conn: sqlite3.Connection, sql: str) -> List[Dict[str, Any]]:
            cur = conn.execute(sql)
            return [dict(row) for row in cur.fetchall()]

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            if table_exists(conn, "topography_observations"):
                rows = rows_from(conn, """
                    SELECT
                        subplot_id,
                        COUNT(*) AS observation_count,
                        AVG(elevation_m) AS mean_elevation_m,
                        MIN(elevation_m) AS min_elevation_m,
                        MAX(elevation_m) AS max_elevation_m,
                        AVG(slope_degree) AS mean_slope_degree,
                        MIN(slope_degree) AS min_slope_degree,
                        MAX(slope_degree) AS max_slope_degree,
                        AVG(aspect_degree) AS mean_aspect_degree,
                        COUNT(DISTINCT slope_position) AS slope_position_class_count
                    FROM topography_observations
                    WHERE subplot_id IS NOT NULL AND TRIM(subplot_id) <> ''
                    GROUP BY subplot_id
                """)
                topo_rows = []
                for row in rows:
                    subplot_id = clean_str(row.get("subplot_id"), "")
                    if not subplot_id:
                        continue
                    topo_rows.append({
                        **row,
                        "subplot_id": subplot_id,
                        "context_id": f"TOPO_SUBPLOT_{subplot_id}",
                        "context_level": "subplot",
                        "data_layer": "environment_context_summary",
                        "source_table": "topography_observations",
                        "interpretation_boundary": "样方地形摘要来自单木点位或样方内点位聚合，可用于背景和关联分析，不单独构成因果解释。",
                    })
                for batch in chunked(topo_rows, self.batch_size):
                    self.run("""
                    UNWIND $batch AS row
                    MATCH (s:Subplot {subplot_id: row.subplot_id})
                    MERGE (ctx:TopographyContext {context_id: row.context_id})
                    SET ctx += row
                    MERGE (s)-[:HAS_TOPOGRAPHY_CONTEXT]->(ctx)
                    """, batch=batch)
                counts["topography_contexts"] = len(topo_rows)

            if table_exists(conn, "climate_stations"):
                station_rows = rows_from(conn, "SELECT * FROM climate_stations WHERE station_id IS NOT NULL AND TRIM(station_id) <> ''")
                for row in station_rows:
                    row["data_layer"] = "climate_station_metadata"
                    row["interpretation_boundary"] = "气象站数据表示研究区气候背景，不等同于样方内微气候实测。"
                for batch in chunked(station_rows, self.batch_size):
                    self.run("""
                    MATCH (mp:MonitoringPlot {monitoring_plot_id: $monitoring_plot_id})
                    UNWIND $batch AS row
                    MERGE (st:ClimateStation {station_id: row.station_id})
                    SET st += row
                    MERGE (mp)-[:HAS_CLIMATE_BACKGROUND_STATION]->(st)
                    """, monitoring_plot_id=monitoring_plot_id, batch=batch)
                counts["climate_stations"] = len(station_rows)

            if table_exists(conn, "climate_monthly_summary"):
                monthly_rows = rows_from(conn, "SELECT * FROM climate_monthly_summary WHERE station_id IS NOT NULL AND year IS NOT NULL AND month IS NOT NULL")
                for row in monthly_rows:
                    row["summary_id"] = f"CLIMATE_MONTH_{row.get('station_id')}_{int(row.get('year'))}_{int(row.get('month')):02d}"
                    row["temporal_resolution"] = "month"
                    row["data_layer"] = "climate_summary"
                    row["source_table"] = "climate_monthly_summary"
                    row["interpretation_boundary"] = "月气候摘要由逐日站点记录聚合得到，用于气候背景描述和时间序列分析。"
                for batch in chunked(monthly_rows, self.batch_size):
                    self.run("""
                    UNWIND $batch AS row
                    MERGE (st:ClimateStation {station_id: row.station_id})
                    MERGE (m:ClimateMonthlySummary {summary_id: row.summary_id})
                    SET m += row
                    MERGE (st)-[:HAS_MONTHLY_SUMMARY]->(m)
                    """, batch=batch)
                counts["climate_monthly_summaries"] = len(monthly_rows)

            if table_exists(conn, "climate_annual_summary"):
                annual_rows = rows_from(conn, "SELECT * FROM climate_annual_summary WHERE station_id IS NOT NULL AND year IS NOT NULL")
                for row in annual_rows:
                    row["summary_id"] = f"CLIMATE_YEAR_{row.get('station_id')}_{int(row.get('year'))}"
                    row["temporal_resolution"] = "year"
                    row["data_layer"] = "climate_summary"
                    row["source_table"] = "climate_annual_summary"
                    row["interpretation_boundary"] = "年气候摘要由逐日站点记录聚合得到，用于气候背景描述；不直接代表样方内微气候。"
                for batch in chunked(annual_rows, self.batch_size):
                    self.run("""
                    UNWIND $batch AS row
                    MERGE (st:ClimateStation {station_id: row.station_id})
                    MERGE (y:ClimateAnnualSummary {summary_id: row.summary_id})
                    SET y += row
                    MERGE (st)-[:HAS_ANNUAL_SUMMARY]->(y)
                    """, batch=batch)
                counts["climate_annual_summaries"] = len(annual_rows)

        return counts

    def import_default_definitions(self) -> None:
        self.import_field_mappings(DEFAULT_FIELD_MAPPINGS)
        self.import_indicator_definitions(DEFAULT_INDICATORS)
        self.import_formula_definitions(DEFAULT_FORMULAS)
        self.import_diagnostic_rules(PROVISIONAL_DIAGNOSTIC_RULES)
        self.run("""
        MERGE (tool:ToolDefinition {tool_name: "kg_builder_python_metrics"})
        SET tool.name_cn = "知识图谱构建脚本内置确定性指标计算", tool.tool_type = "python_deterministic_calculation", tool.version = "1.0", tool.interpretation_boundary = "只计算基础结构摘要，不输出材积、碳、水文、死亡概率等未核验结论。"
        WITH tool
        OPTIONAL MATCH (f:FormulaDefinition)
        WHERE f.knowledge_id IN ["F_BASAL_AREA_TREE_V1", "F_STAND_DENSITY_V1", "F_HDR_V1", "F_SHANNON_TREE_V1"]
        MERGE (tool)-[:IMPLEMENTS_FORMULA]->(f)
        """)

    def import_field_mappings(self, rows: Sequence[Dict[str, Any]]) -> None:
        for batch in chunked(list(rows), self.batch_size):
            self.run("""
            UNWIND $batch AS row
            MERGE (field:DatabaseField {field_id: row.field_id}) SET field += row
            MERGE (v:VariableDefinition {variable_id: row.variable_id})
            SET v.name_cn = coalesce(v.name_cn, row.name_cn), v.canonical_unit = coalesce(v.canonical_unit, row.unit), v.source_level = coalesce(v.source_level, row.source_level)
            MERGE (field)-[:MAPS_TO_VARIABLE]->(v)
            """, batch=batch)

    def import_indicator_definitions(self, rows: Sequence[Dict[str, Any]]) -> None:
        for batch in chunked(list(rows), self.batch_size):
            self.run("""UNWIND $batch AS row MERGE (ind:IndicatorDefinition {indicator_id: row.indicator_id}) SET ind += row""", batch=batch)

    def import_formula_definitions(self, rows: Sequence[Dict[str, Any]]) -> None:
        for batch in chunked(list(rows), self.batch_size):
            self.run("""
            UNWIND $batch AS row
            MERGE (f:FormulaDefinition {knowledge_id: row.knowledge_id})
            SET f.name_cn = row.name_cn, f.expression = row.expression, f.version = row.version, f.applicability = row.applicability, f.raw_json = row.raw_json
            WITH f, row
            OPTIONAL MATCH (ind:IndicatorDefinition {indicator_id: row.produces_indicator})
            FOREACH (_ IN CASE WHEN ind IS NOT NULL THEN [1] ELSE [] END | MERGE (f)-[:PRODUCES_INDICATOR]->(ind))
            """, batch=[{**row, "raw_json": safe_json(row)} for row in batch])

    def import_diagnostic_rules(self, rows: Sequence[Dict[str, Any]]) -> None:
        for batch in chunked(list(rows), self.batch_size):
            self.run("""
            UNWIND $batch AS row
            MERGE (rule:DiagnosticRule {knowledge_id: row.knowledge_id})
            SET rule.name_cn = row.name_cn, rule.condition_expression = row.condition_expression, rule.result_label = row.result_label, rule.evidence_level = row.evidence_level, rule.interpretation_boundary = row.interpretation_boundary, rule.raw_json = row.raw_json
            """, batch=[{**row, "raw_json": safe_json(row)} for row in batch])

    def import_registry_yaml(self, registry_path: Path) -> None:
        if not registry_path or not registry_path.exists():
            return
        registry = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        variables = []
        for item in registry.get("variables", []) or []:
            variable_id = item.get("variable_id")
            if not variable_id:
                continue
            variables.append({"variable_id": variable_id, "name_cn": item.get("name_cn", ""), "canonical_unit": item.get("canonical_unit", ""), "level": item.get("level", ""), "source_class": item.get("source_class", ""), "property_key": item.get("property_key", ""), "definition": item.get("definition", ""), "raw_json": safe_json(item)})
        indicators = []
        for item in registry.get("indicators", []) or []:
            indicator_id = item.get("indicator_id")
            if not indicator_id:
                continue
            indicators.append({"indicator_id": indicator_id, "name_cn": item.get("name_cn", ""), "canonical_unit": item.get("canonical_unit", ""), "level": item.get("level", ""), "property_key": item.get("property_key", ""), "definition": item.get("definition", ""), "raw_json": safe_json(item)})
        formulas = []
        for item in registry.get("formulas", []) or []:
            knowledge_id = item.get("knowledge_id")
            if not knowledge_id:
                continue
            formulas.append({"knowledge_id": knowledge_id, "name_cn": item.get("name_cn", ""), "expression": item.get("expression", ""), "version": item.get("version", "1.0"), "applicability": item.get("applicability", ""), "produces_indicator": item.get("produces_indicator", ""), "consumes_inputs": list_to_string_list(item.get("consumes_inputs")), "depends_on": list_to_string_list(item.get("depends_on")), "tool_binding": item.get("tool_binding", ""), "raw_json": safe_json(item)})
        models = []
        for item in registry.get("models", []) or []:
            knowledge_id = item.get("knowledge_id")
            if not knowledge_id:
                continue
            models.append({"knowledge_id": knowledge_id, "name_cn": item.get("name_cn", ""), "expression": item.get("expression", ""), "version": item.get("version", "1.0"), "applicable_taxa": list_to_string_list(item.get("applicable_taxa")), "source_reference": item.get("source_reference", ""), "model_status": item.get("model_status", "requires_validation"), "raw_json": safe_json(item)})
        rules = []
        for item in registry.get("diagnostic_rules", []) or []:
            knowledge_id = item.get("knowledge_id")
            if not knowledge_id:
                continue
            rules.append({"knowledge_id": knowledge_id, "name_cn": item.get("name_cn", ""), "condition_expression": item.get("condition_expression", ""), "prescription_action": item.get("prescription_action", ""), "applicable_taxa": list_to_string_list(item.get("applicable_taxa")), "applicable_region": item.get("applicable_region", ""), "threshold_basis": item.get("threshold_basis", ""), "evidence_level": item.get("evidence_level", item.get("confidence_level", "unclassified")), "interpretation_boundary": item.get("interpretation_boundary", ""), "raw_json": safe_json(item)})
        if variables:
            for batch in chunked(variables, self.batch_size):
                self.run("UNWIND $batch AS row MERGE (v:VariableDefinition {variable_id: row.variable_id}) SET v += row", batch=batch)
        if indicators:
            self.import_indicator_definitions(indicators)
        if formulas:
            for batch in chunked(formulas, self.batch_size):
                self.run("""
                UNWIND $batch AS row
                MERGE (f:FormulaDefinition {knowledge_id: row.knowledge_id}) SET f += row
                WITH f, row
                OPTIONAL MATCH (out:IndicatorDefinition {indicator_id: row.produces_indicator})
                FOREACH (_ IN CASE WHEN out IS NOT NULL THEN [1] ELSE [] END | MERGE (f)-[:PRODUCES_INDICATOR]->(out))
                FOREACH (input_id IN row.consumes_inputs | MERGE (v:VariableDefinition {variable_id: input_id}) MERGE (f)-[:REQUIRES_VARIABLE]->(v))
                FOREACH (dep_id IN row.depends_on | MERGE (dep:FormulaDefinition {knowledge_id: dep_id}) MERGE (f)-[:DEPENDS_ON_FORMULA]->(dep))
                FOREACH (_ IN CASE WHEN row.tool_binding <> "" THEN [1] ELSE [] END | MERGE (tool:ToolDefinition {tool_name: row.tool_binding}) MERGE (tool)-[:IMPLEMENTS_FORMULA]->(f))
                """, batch=batch)
        if models:
            for batch in chunked(models, self.batch_size):
                self.run("UNWIND $batch AS row MERGE (m:ModelDefinition {knowledge_id: row.knowledge_id}) SET m += row", batch=batch)
        if rules:
            for batch in chunked(rules, self.batch_size):
                self.run("UNWIND $batch AS row MERGE (rule:DiagnosticRule {knowledge_id: row.knowledge_id}) SET rule += row", batch=batch)

    def import_indicator_values(self, indicator_values: Sequence[Dict[str, Any]], run_id: str, tool_name: str) -> None:
        self.run("""
        MERGE (run:CalculationRun {run_id: $run_id})
        SET run.created_at = datetime(), run.description = "基础样方指标由 kg_builder_qilian.py 计算并导入", run.result_scope = "subplot_summary_indicators", run.model_status = "deterministic_basic_metrics_only"
        MERGE (tool:ToolDefinition {tool_name: $tool_name}) SET tool.tool_type = "python_deterministic_calculation"
        MERGE (run)-[:USED_TOOL]->(tool)
        """, run_id=run_id, tool_name=tool_name)
        for batch in chunked(list(indicator_values), self.batch_size):
            self.run("""
            MATCH (run:CalculationRun {run_id: $run_id})
            UNWIND $batch AS row
            MATCH (s:Subplot {subplot_id: row.target_id})
            MERGE (ind:IndicatorDefinition {indicator_id: row.indicator_id})
            MERGE (iv:IndicatorValue {indicator_value_id: row.indicator_value_id})
            SET iv.value = row.value, iv.unit = row.unit, iv.target_type = row.target_type, iv.target_id = row.target_id, iv.survey_event_id = row.survey_event_id, iv.result_type = row.result_type, iv.quality_flags = row.quality_flags, iv.interpretation_boundary = row.interpretation_boundary, iv.created_at = datetime()
            MERGE (s)-[:HAS_INDICATOR_VALUE]->(iv)
            MERGE (iv)-[:INSTANCE_OF]->(ind)
            MERGE (iv)-[:GENERATED_BY]->(run)
            MERGE (run)-[:GENERATED]->(iv)
            """, run_id=run_id, batch=list(batch))

    def import_diagnostic_signals(self, signals: Sequence[Dict[str, Any]]) -> None:
        if not signals:
            return
        for batch in chunked(list(signals), self.batch_size):
            self.run("""
            UNWIND $batch AS row
            MATCH (s:Subplot {subplot_id: row.target_id})
            MATCH (iv:IndicatorValue {indicator_value_id: row.indicator_value_id})
            OPTIONAL MATCH (rule:DiagnosticRule {knowledge_id: row.rule_id})
            MERGE (sig:DiagnosticSignal {signal_id: row.signal_id})
            SET sig.signal_type = row.signal_type, sig.result_label = row.result_label, sig.severity = row.severity, sig.percentile = row.percentile, sig.explanation = row.explanation, sig.survey_event_id = row.survey_event_id, sig.evidence_level = row.evidence_level, sig.created_at = datetime()
            MERGE (s)-[:HAS_DIAGNOSTIC_SIGNAL]->(sig)
            MERGE (sig)-[:BASED_ON]->(iv)
            FOREACH (_ IN CASE WHEN rule IS NOT NULL THEN [1] ELSE [] END | MERGE (sig)-[:TRIGGERED_BY_RULE]->(rule))
            """, batch=list(batch))

    def validate_graph(self) -> Dict[str, Any]:
        rows = self.run("""
        RETURN
          count { MATCH (:ProtectedArea) } AS protected_areas,
          count { MATCH (:MonitoringPlot) } AS monitoring_plots,
          count { MATCH (:Subplot) } AS subplots,
          count { MATCH (:TreeIndividual) } AS trees,
          count { MATCH (:TreeObservation) } AS tree_observations,
          count { MATCH (:ShrubObservation) } AS shrub_observations,
          count { MATCH (:DeadwoodObservation) } AS deadwood_observations,
          count { MATCH (:Taxon) } AS taxa,
          count { MATCH (:IndicatorDefinition) } AS indicator_definitions,
          count { MATCH (:IndicatorValue) } AS indicator_values,
          count { MATCH (:DiagnosticSignal) } AS diagnostic_signals,
          count { MATCH (:TopographyContext) } AS topography_contexts,
          count { MATCH (:ClimateStation) } AS climate_stations,
          count { MATCH (:ClimateMonthlySummary) } AS climate_monthly_summaries,
          count { MATCH (:ClimateAnnualSummary) } AS climate_annual_summaries
        """)
        return rows[0] if rows else {}

    def print_sample_queries(self) -> None:
        print("\n可测试的 Cypher：")
        print('MATCH (s:Subplot)-[:HAS_TREE]->(t:TreeIndividual)-[:HAS_TAXON]->(tx:Taxon) RETURN s.subplot_id AS subplot, t.tree_id AS tree_id, tx.accepted_name_cn AS taxon LIMIT 20;')
        print('MATCH (s:Subplot {subplot_id:"0120"})-[:HAS_INDICATOR_VALUE]->(iv:IndicatorValue)-[:INSTANCE_OF]->(ind:IndicatorDefinition) RETURN ind.name_cn AS indicator, iv.value AS value, iv.unit AS unit, iv.result_type AS result_type, iv.quality_flags AS flags ORDER BY indicator;')
        print('MATCH (s:Subplot)-[:HAS_DIAGNOSTIC_SIGNAL]->(sig:DiagnosticSignal)-[:BASED_ON]->(iv:IndicatorValue)-[:INSTANCE_OF]->(ind:IndicatorDefinition) RETURN s.subplot_id AS subplot, sig.signal_type AS signal, sig.percentile AS percentile, ind.name_cn AS based_on, iv.value AS value ORDER BY percentile DESC LIMIT 20;')


def build_graph(args: argparse.Namespace) -> Dict[str, Any]:
    db_path = Path(args.db).resolve()
    registry_path = Path(args.registry).resolve() if args.registry else None
    source = SQLiteSource(db_path=db_path, subplot_area_m2=args.subplot_area_m2)
    bundle = source.load()
    print(f"[数据读取] 乔木记录: {len(bundle.tree_rows)}")
    print(f"[数据读取] 灌木记录: {len(bundle.shrub_rows)}")
    print(f"[数据读取] 枯死木记录: {len(bundle.deadwood_rows)}")
    print(f"[数据读取] 样方数量: {len(bundle.subplot_ids)}")
    if args.limit_subplots:
        keep = set(args.limit_subplots)
        bundle.tree_rows = [row for row in bundle.tree_rows if row["subplot_id"] in keep]
        bundle.shrub_rows = [row for row in bundle.shrub_rows if row["subplot_id"] in keep]
        bundle.deadwood_rows = [row for row in bundle.deadwood_rows if row["subplot_id"] in keep]
        bundle.subplot_ids = sorted(keep & set(bundle.subplot_ids))
        print(f"[限制导入] 仅导入样方: {', '.join(bundle.subplot_ids)}")
    indicator_values = calculate_subplot_indicator_values(bundle=bundle, survey_event_id=args.survey_event_id, subplot_area_m2=args.subplot_area_m2)
    signals = [] if args.no_signals else build_provisional_signals(indicator_values=indicator_values, survey_event_id=args.survey_event_id)
    uri = args.neo4j_uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = args.neo4j_user or os.getenv("NEO4J_USER", "neo4j")
    password = args.neo4j_password or os.getenv("NEO4J_PASSWORD", "")
    database = args.neo4j_database or os.getenv("NEO4J_DATABASE", "neo4j")
    builder = QilianKGBuilder(uri=uri, user=user, password=password, database=database, batch_size=args.batch_size)
    try:
        builder.verify_connectivity()
        print("[Neo4j] 连接成功。")
        if args.reset:
            print("[Neo4j] 正在清理本脚本管理的图谱范围...")
            builder.reset_graph_scope()
        print("[Neo4j] 创建约束...")
        builder.create_constraints()
        protected_area = dict(DEFAULT_PROTECTED_AREA)
        monitoring_plot = dict(DEFAULT_MONITORING_PLOT)
        survey_event = dict(DEFAULT_SURVEY_EVENT)
        protected_area["protected_area_id"] = args.protected_area_id
        monitoring_plot["monitoring_plot_id"] = args.monitoring_plot_id
        monitoring_plot["area_m2"] = args.plot_area_m2
        survey_event["survey_event_id"] = args.survey_event_id
        survey_event["survey_year"] = args.survey_year
        survey_event["survey_date_text"] = str(args.survey_year)
        print("[Neo4j] 导入保护地、样地和调查事件...")
        builder.import_area_plot_event(protected_area, monitoring_plot, survey_event)
        print("[Neo4j] 导入样方...")
        builder.import_subplots(bundle.subplot_ids, args.monitoring_plot_id, args.subplot_area_m2)
        print("[Neo4j] 导入 Taxon...")
        builder.import_taxa_from_rows(bundle)
        print("[Neo4j] 导入乔木单木和观测...")
        builder.import_tree_observations(bundle.tree_rows, args.survey_event_id)
        if bundle.shrub_rows:
            print("[Neo4j] 导入灌木观测...")
            builder.import_shrub_observations(bundle.shrub_rows, args.survey_event_id)
        else:
            print("[Neo4j] 未发现灌木观测表，跳过。")
        if bundle.deadwood_rows:
            print("[Neo4j] 导入枯死木观测...")
            builder.import_deadwood_observations(bundle.deadwood_rows, args.survey_event_id)
        else:
            print("[Neo4j] 未发现枯死木观测表，跳过。")
        print("[Neo4j] 导入地形和气候环境上下文摘要...")
        env_counts = builder.import_environment_contexts_from_sqlite(db_path, args.monitoring_plot_id)
        print(f"[Neo4j] 环境上下文导入: {env_counts}")
        print("[Neo4j] 导入默认字段、指标、公式和临时诊断规则定义...")
        builder.import_default_definitions()
        if registry_path and registry_path.exists() and not args.skip_registry:
            print(f"[Neo4j] 导入 YAML 知识注册表: {registry_path}")
            builder.import_registry_yaml(registry_path)
        elif registry_path and not registry_path.exists():
            print(f"[提示] YAML 知识注册表不存在，已跳过: {registry_path}")
        run_id = f"RUN_KG_BUILD_BASIC_METRICS_{args.survey_event_id}_{int(time.time())}"
        print("[Neo4j] 导入样方级指标值...")
        builder.import_indicator_values(indicator_values, run_id=run_id, tool_name="kg_builder_python_metrics")
        if signals:
            print(f"[Neo4j] 导入相对关注信号: {len(signals)} 条")
            builder.import_diagnostic_signals(signals)
        else:
            print("[Neo4j] 未生成相对关注信号。")
        summary = builder.validate_graph()
        print("\n=== 图谱构建摘要 ===")
        for key, value in summary.items():
            print(f"{key}: {value}")
        builder.print_sample_queries()
        return summary
    finally:
        builder.close()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建祁连山森林质量诊断智能体 Neo4j 知识图谱。")
    parser.add_argument("--db", default=os.getenv("FORESTRY_SQLITE_DB", str(BASE_DIR / "data" / "qilian_forest.db")), help="标准化 SQLite 数据库路径。")
    parser.add_argument("--registry", default=str(BASE_DIR / "ontology" / "forestry_knowledge_registry.yaml"), help="YAML 知识注册表路径。")
    parser.add_argument("--skip-registry", action="store_true", help="跳过 YAML 注册表导入。")
    parser.add_argument("--neo4j-uri", default=None, help="Neo4j URI。默认读取 NEO4J_URI。")
    parser.add_argument("--neo4j-user", default=None, help="Neo4j 用户名。默认读取 NEO4J_USER。")
    parser.add_argument("--neo4j-password", default=None, help="Neo4j 密码。默认读取 NEO4J_PASSWORD。")
    parser.add_argument("--neo4j-database", default=None, help="Neo4j database。默认读取 NEO4J_DATABASE。")
    parser.add_argument("--reset", action="store_true", help="清理本脚本管理的旧图谱节点后重建。")
    parser.add_argument("--batch-size", type=int, default=1000, help="Neo4j UNWIND 批量写入大小。")
    parser.add_argument("--subplot-area-m2", type=float, default=400.0, help="单个样方面积，默认20m×20m=400㎡。")
    parser.add_argument("--plot-area-m2", type=float, default=240000.0, help="监测样地面积，默认24ha=240000㎡。")
    parser.add_argument("--protected-area-id", default="QILIAN_NATIONAL_PARK", help="保护地ID。")
    parser.add_argument("--monitoring-plot-id", default="QILIAN_SIGOU_TREE_PLOT_24HA", help="监测样地ID。")
    parser.add_argument("--survey-event-id", default="EVENT_2023", help="调查事件ID。")
    parser.add_argument("--survey-year", type=int, default=2023, help="调查年份。")
    parser.add_argument("--no-signals", action="store_true", help="不生成基于百分位的临时相对关注信号。")
    parser.add_argument("--limit-subplots", nargs="*", default=None, help="仅导入指定样方，用于测试。例如 --limit-subplots 0120 3020")
    return parser


def main() -> None:
    args = make_parser().parse_args()
    summary = build_graph(args)
    print("\n[完成] 知识图谱构建完成。")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
