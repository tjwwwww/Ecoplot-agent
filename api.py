# -*- coding: utf-8 -*-
"""
api.py
======
祁连山森林质量诊断智能体后端 API（前后端联调版）

功能：
- 服务健康检查、全样地总览
- 样方列表、样方详情、六维指标
- 单木列表、单木完整详情、同类树百分位
- 公式执行、指标预计算和版本化缓存
- 八类绘图接口及静态文件访问
- 支持全局/样方/单木/多对象比较的智能体问答
- 生成样方级现场核查候选清单

重要边界：
- 材积模型未明确标记“正式结果可用”时，不向前端推荐正式展示。
- 高径比和Hegyi仅作为相对关注信号，不等同于死亡、风折或病虫害确诊。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from pyproj import CRS, Transformer
    PYPROJ_AVAILABLE = True
    PYPROJ_IMPORT_ERROR = ""
except Exception as exc:
    CRS = None  # type: ignore[assignment]
    Transformer = None  # type: ignore[assignment]
    PYPROJ_AVAILABLE = False
    PYPROJ_IMPORT_ERROR = str(exc)

import forestry_spatial_tools as fst
import survey_module as svm

try:
    from forestry_visualization_engine import ForestryDataRepository
    from forestry_visualization_engine import (
        plot_climate_time_series,
        plot_group_comparison_boxplot,
        plot_size_class_distribution,
        plot_species_composition,
        plot_subplot_grid_heatmap,
        plot_subplot_percentile_profile,
        plot_tree_relationship_scatter,
        plot_tree_spatial_map,
    )
    VIS_ENGINE_AVAILABLE = True
    VIS_ENGINE_IMPORT_ERROR = ""
except Exception as exc:
    ForestryDataRepository = None  # type: ignore[assignment]
    VIS_ENGINE_AVAILABLE = False
    VIS_ENGINE_IMPORT_ERROR = str(exc)


# =============================================================================
# 配置
# =============================================================================

API_VERSION = "0.3.0"
CACHE_SCHEMA_VERSION = "3"
DEFAULT_SUBPLOT_AREA_HA = float(os.getenv("FORESTRY_SUBPLOT_AREA_HA", "0.04"))
CGCS2000_GK102_PROJ4 = os.getenv(
    "FORESTRY_GK102_PROJ4",
    "+proj=tmerc +lat_0=0 +lon_0=102 +k=1 +x_0=500000 +y_0=0 +ellps=GRS80 +units=m +no_defs",
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
VISUALIZATION_DIR = BASE_DIR / "visualizations"
REPORT_DIR = BASE_DIR / "reports"
WEB_DIR = BASE_DIR / "web"

for directory in (DATA_DIR, CACHE_DIR, VISUALIZATION_DIR, REPORT_DIR, WEB_DIR):
    directory.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(getattr(fst, "DB_PATH", DATA_DIR / "qilian_forest.db"))

app = FastAPI(
    title="ForestryAgent API",
    description="祁连山森林样地、样方、单木、指标、图表、公式、智能体与现场核查接口。",
    version=API_VERSION,
)

app.mount(
    "/visualizations",
    StaticFiles(directory=str(VISUALIZATION_DIR)),
    name="visualizations",
)

# 测试网页目录：访问 http://127.0.0.1:8000/web/chat.html
app.mount(
    "/web",
    StaticFiles(directory=str(WEB_DIR), html=True),
    name="web",
)

cors_raw = os.getenv("FORESTRY_CORS_ALLOWED", "*")
ALLOWED_ORIGINS = [x.strip() for x in cors_raw.split(",") if x.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False if ALLOWED_ORIGINS == ["*"] else True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("FORESTRY_API_KEY", "")


ENVIRONMENT_VIEW_SQL: Dict[str, str] = {
    "vw_tree_topography_context": """
        CREATE VIEW vw_tree_topography_context AS
        SELECT
            tree_id,
            elevation_m,
            slope_degree,
            aspect_degree,
            slope_position,
            NULL AS terrain_position,
            source_file AS data_source,
            imported_at,
            NULL AS quality_flag
        FROM topography_observations
    """,
    "vw_climate_daily_normalized": """
        CREATE VIEW vw_climate_daily_normalized AS
        SELECT
            n.station_id,
            s.station_name,
            s.latitude,
            s.longitude,
            s.elevation_m,
            n.observation_date,
            n.mean_temperature_c,
            n.min_temperature_c,
            n.max_temperature_c,
            n.precipitation_mm,
            n.dew_point_c,
            n.wind_speed_m_s,
            n.wind_gust_m_s,
            n.visibility_km,
            n.sea_level_pressure_hpa AS pressure_hpa,
            n.snow_depth_cm,
            n.raw_weather_flags,
            n.data_source,
            n.imported_at,
            n.weather_type,
            n.quality_flag
        FROM climate_daily_normalized n
        LEFT JOIN climate_stations s ON s.station_id = n.station_id
    """,
}


def _bootstrap_environment_views() -> None:
    if not DB_PATH.exists():
        return
    with sqlite3.connect(DB_PATH) as conn:
        for view_name, sql in ENVIRONMENT_VIEW_SQL.items():
            conn.execute(f"DROP VIEW IF EXISTS {view_name}")
            conn.execute(sql)
        conn.commit()


@app.on_event("startup")
def _startup_bootstrap_environment_views() -> None:
    try:
        _bootstrap_environment_views()
    except Exception as exc:
        print(f"[WARN] failed to bootstrap environment views: {exc}")


def verify_api_key(x_api_key: Optional[str] = Header(default=None)) -> bool:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API Key")
    return True


# =============================================================================
# 请求模型
# =============================================================================

class FormulaExecuteRequest(BaseModel):
    knowledge_id: str = Field(..., min_length=1, description="公式ID，例如 F_STAND_DENSITY")
    context_json: Dict[str, Any] = Field(default_factory=dict, description="公式输入变量")


class MapPositionConvertRequest(BaseModel):
    lng: float = Field(..., description="手机定位经度，通常为 WGS84")
    lat: float = Field(..., description="手机定位纬度，通常为 WGS84")
    from_crs: str = Field(default="WGS84", description="输入经纬度坐标系，第一版支持 WGS84")


class AgentPageContext(BaseModel):
    current_plot_id: Optional[str] = None
    current_subplot_id: Optional[str] = None
    current_tree_id: Optional[str] = None

    # 兼容前端或旧接口常用写法
    subplot_id: Optional[str] = None
    tree_id: Optional[str] = None

    selected_subplot_ids: List[str] = Field(default_factory=list)
    selected_tree_ids: List[str] = Field(default_factory=list)
    current_page: Optional[str] = None
    page_title: Optional[str] = None
    context_policy: Optional[str] = Field(default="auto", description="auto 表示当前页面上下文只作为背景，不强制限定全局问题。")

    class Config:
        extra = "allow"


class AgentChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    context: Optional[AgentPageContext] = None

    # 新版本体驱动 agent2.py 需要 session_id 支持连续对话
    session_id: Optional[str] = None

    # chat: 自然对话；report: 报告模式
    mode: str = Field(default="chat", description="chat or report")

    # 工具/规划轮次
    max_rounds: int = Field(default=6, ge=1, le=30)

    # 保存报告/导出
    save_report: bool = False
    report_file: Optional[str] = None

    # 兼容旧前端把 current_page 放在顶层
    current_page: Optional[str] = None

    # 是否返回中间理解结果，前端工作区可显示
    include_plan: bool = False
    include_candidates: bool = False
    include_debug: bool = False


class PrecomputeRequest(BaseModel):
    subplot_ids: List[str] = Field(default_factory=list)
    force: bool = True


class InspectionTaskRequest(BaseModel):
    subplot_id: str
    top_n: int = Field(default=10, ge=1, le=100)
    include_controls: bool = True
    control_count: int = Field(default=5, ge=0, le=30)
    radius_m: float = Field(default=6.0, gt=0, le=50)


# =============================================================================
# 指标和图表元数据
# =============================================================================

INDICATOR_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "IND_STAND_DENSITY": {
        "indicator_id": "IND_STAND_DENSITY", "name_zh": "林分密度",
        "target_type": "Subplot", "unit": "株/ha", "formula": "N = n / A",
        "description": "单位面积内乔木株数。",
        "interpretation_boundary": "高密度不自动等于质量差，需结合林型、径级和竞争比较。",
    },
    "IND_MEAN_DBH": {
        "indicator_id": "IND_MEAN_DBH", "name_zh": "算术平均胸径",
        "target_type": "Subplot", "unit": "cm", "formula": "D̄ = ΣDᵢ / n",
        "description": "样方内有效乔木胸径的算术平均值。",
        "interpretation_boundary": "应与平方平均胸径及径级结构联合解释。",
    },
    "IND_QUADRATIC_MEAN_DBH": {
        "indicator_id": "IND_QUADRATIC_MEAN_DBH", "name_zh": "平方平均胸径",
        "target_type": "Subplot", "unit": "cm", "formula": "Dq = sqrt(ΣDᵢ² / n)",
        "description": "与林分断面积相联系的平均胸径指标。",
        "interpretation_boundary": "不能单独代表林分质量或年龄。",
    },
    "IND_BASAL_AREA_HA": {
        "indicator_id": "IND_BASAL_AREA_HA", "name_zh": "单位面积断面积",
        "target_type": "Subplot", "unit": "m²/ha",
        "formula": "G = Σ[πDᵢ²/40000] / A",
        "description": "样方单木胸高断面积之和换算到每公顷。",
        "interpretation_boundary": "不等同于生物量、蓄积或健康状态。",
    },
    "IND_TREE_HDR": {
        "indicator_id": "IND_TREE_HDR", "name_zh": "高径比",
        "target_type": "TreeIndividual", "unit": "无量纲",
        "formula": "HDR = 100H(m) / D(cm)",
        "description": "树高相对于胸径的细长程度。",
        "interpretation_boundary": "仅作为形态细长性信号，不能单独判定风折或死亡风险。",
    },
    "IND_TREE_LCR": {
        "indicator_id": "IND_TREE_LCR", "name_zh": "活冠率",
        "target_type": "TreeIndividual", "unit": "比例",
        "formula": "LCR = (H - Hcb) / H",
        "description": "活冠长度占树高比例。",
        "interpretation_boundary": "冠基高缺失或异常时不应计算。",
    },
    "IND_HEGYI_CI": {
        "indicator_id": "IND_HEGYI_CI", "name_zh": "Hegyi竞争指数",
        "target_type": "TreeIndividual", "unit": "1/m",
        "formula": "Cᵢ = Σ[(Dⱼ/Dᵢ)/Lᵢⱼ]",
        "description": "基于邻木大小和距离的局部竞争指标。",
        "interpretation_boundary": "受搜索半径、边缘效应和坐标完整性影响，应优先做同类相对比较。",
    },
    "IND_SHANNON_TREE": {
        "indicator_id": "IND_SHANNON_TREE", "name_zh": "乔木层Shannon指数",
        "target_type": "Subplot", "unit": "无量纲", "formula": "H' = -Σpᵢ ln(pᵢ)",
        "description": "基于乔木株数相对多度计算。",
        "interpretation_boundary": "天然单优势林的低值不自动等于质量差。",
    },
    "IND_DEADWOOD_DENSITY": {
        "indicator_id": "IND_DEADWOOD_DENSITY", "name_zh": "枯死木密度",
        "target_type": "Subplot", "unit": "株/ha", "formula": "Ndead = ndead / A",
        "description": "单位面积枯死木数量。",
        "interpretation_boundary": "表示历史死亡或干扰背景，不等同于当前死亡率。",
    },
    "IND_SHRUB_RICHNESS": {
        "indicator_id": "IND_SHRUB_RICHNESS", "name_zh": "灌木物种丰富度",
        "target_type": "Subplot", "unit": "种", "formula": "S = unique(taxon)",
        "description": "样方内唯一灌木分类单元数量。",
        "interpretation_boundary": "受调查鉴定精度和重复记录合并规则影响。",
    },
    "IND_TREE_VOLUME": {
        "indicator_id": "IND_TREE_VOLUME", "name_zh": "单木材积",
        "target_type": "TreeIndividual", "unit": "m³/株", "formula": "V = f_species(D,H)",
        "description": "由经核验的树种或树种组二元材积模型得到。",
        "interpretation_boundary": "模型、单位和适用范围未核验前不得作为正式结果。",
    },
}

PLOT_TOOL_REGISTRY = [
    {"tool_id": "plot_subplot_grid_heatmap", "endpoint": "/api/plot/grid", "name_zh": "样方格网热力图"},
    {"tool_id": "plot_size_class_distribution", "endpoint": "/api/plot/size_class/{subplot_id}", "name_zh": "径级结构图"},
    {"tool_id": "plot_species_composition", "endpoint": "/api/plot/species/{subplot_id}", "name_zh": "树种组成图"},
    {"tool_id": "plot_tree_relationship_scatter", "endpoint": "/api/plot/scatter/{subplot_id}", "name_zh": "单木变量关系散点图"},
    {"tool_id": "plot_group_comparison_boxplot", "endpoint": "/api/plot/boxplot", "name_zh": "分组箱线图"},
    {"tool_id": "plot_tree_spatial_map", "endpoint": "/api/plot/spatial/{subplot_id}", "name_zh": "单木空间分布图"},
    {"tool_id": "plot_subplot_percentile_profile", "endpoint": "/api/plot/percentile/{subplot_id}", "name_zh": "样方百分位画像"},
    {"tool_id": "plot_climate_time_series", "endpoint": "/api/plot/climate", "name_zh": "气候时间序列图"},
]


# =============================================================================
# 工具函数
# =============================================================================

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_tool_result(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except json.JSONDecodeError:
            return {"raw": result}
    return {"result": result}


def _call_tool(function_name: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    function = getattr(fst, function_name, None)
    if function is None:
        return {"status": "not_available", "tool_id": function_name, "message": f"底层工具不存在：{function_name}"}
    try:
        return _parse_tool_result(function(*args, **kwargs))
    except TypeError:
        try:
            return _parse_tool_result(function(*args))
        except Exception as exc:
            return {"status": "failed", "tool_id": function_name, "error": str(exc)}
    except Exception as exc:
        return {"status": "failed", "tool_id": function_name, "error": str(exc)}


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> List[str]:
    if not _table_exists(conn, table_name):
        return []
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")]


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _percentile_rank(value: Optional[float], population: Sequence[float]) -> Optional[float]:
    if value is None:
        return None
    valid = sorted(float(x) for x in population if x is not None and math.isfinite(float(x)))
    if not valid:
        return None
    less = sum(x < value for x in valid)
    equal = sum(x == value for x in valid)
    return round(100.0 * (less + 0.5 * equal) / len(valid), 1)


def _cache_fingerprint() -> str:
    items = [f"api:{API_VERSION}", f"schema:{CACHE_SCHEMA_VERSION}"]
    for path in (Path(getattr(fst, "__file__", "")), DB_PATH):
        try:
            stat = path.stat()
            items.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            items.append(f"{path.name}:missing")
    return hashlib.sha256("|".join(items).encode("utf-8")).hexdigest()[:20]


def _cache_path(subplot_id: str) -> Path:
    safe_id = str(subplot_id).replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"subplot_{safe_id}.json"


def _read_cache(subplot_id: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(subplot_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if data.get("cache_fingerprint") == _cache_fingerprint() else None


def _write_cache(subplot_id: str, payload: Dict[str, Any]) -> None:
    path = _cache_path(subplot_id)
    data = dict(payload)
    data["cache_fingerprint"] = _cache_fingerprint()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _repository_subplots() -> Dict[str, Dict[str, Any]]:
    if not VIS_ENGINE_AVAILABLE or ForestryDataRepository is None:
        return {}
    try:
        repo = ForestryDataRepository()
        return {str(k): dict(v) for k, v in getattr(repo, "subplots", {}).items()}
    except Exception:
        return {}


def _get_all_subplot_ids() -> List[str]:
    repository = _repository_subplots()
    if repository:
        return sorted(repository.keys())
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        if not _table_exists(conn, "tree_observations"):
            return []
        rows = conn.execute(
            "SELECT DISTINCT subplot_id FROM tree_observations WHERE subplot_id IS NOT NULL ORDER BY subplot_id"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _volume_formal_available(volume_result: Dict[str, Any]) -> bool:
    candidates = [
        volume_result.get("formal_result_available"),
        volume_result.get("formal_subplot_total_available"),
    ]
    outputs = volume_result.get("volume_outputs")
    if isinstance(outputs, dict):
        candidates += [outputs.get("formal_result_available"), outputs.get("formal_subplot_total_available")]
    return any(x is True for x in candidates)


def _sanitize_volume_result(raw_result: Dict[str, Any]) -> Dict[str, Any]:
    formal = _volume_formal_available(raw_result)
    if formal:
        return {"status": "success", "formal_result_available": True, "result": raw_result, "quality_flags": []}
    return {
        "status": "pending_model_validation",
        "formal_result_available": False,
        "result": raw_result,
        "quality_flags": ["VOLUME_MODEL_NOT_FORMALLY_VALIDATED"],
        "interpretation_boundary": "当前材积仅用于模型核验和程序调试；前端不得作为正式结果展示。",
    }


def _get_tree_topography_context(tree_id: str) -> Dict[str, Any]:
    if not DB_PATH.exists():
        return {"status": "not_available"}
    _bootstrap_environment_views()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM vw_tree_topography_context WHERE tree_id=? LIMIT 1",
            (str(tree_id).strip(),),
        ).fetchone()
    if row is None:
        return {"status": "not_found"}
    return {"status": "success", "data": dict(row)}


def _get_climate_summary(station_id: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None) -> Dict[str, Any]:
    if not DB_PATH.exists():
        return {"status": "not_available"}
    _bootstrap_environment_views()
    clauses: List[str] = []
    params: List[Any] = []
    if station_id:
        clauses.append("station_id = ?")
        params.append(station_id.strip())
    if date_from:
        clauses.append("observation_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("observation_date <= ?")
        params.append(date_to)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT COUNT(*) AS record_count, MIN(observation_date) AS date_from, MAX(observation_date) AS date_to, "
        "AVG(mean_temperature_c) AS mean_temperature_c, MIN(min_temperature_c) AS min_temperature_c, "
        "MAX(max_temperature_c) AS max_temperature_c, SUM(precipitation_mm) AS total_precipitation_mm, "
        "AVG(wind_speed_m_s) AS mean_wind_speed_m_s, MAX(wind_gust_m_s) AS max_wind_gust_m_s "
        f"FROM climate_daily_normalized {where_sql}"
    )
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        if station_id:
            station = conn.execute(
                "SELECT station_id, station_name, latitude, longitude, elevation_m FROM climate_stations WHERE station_id=? LIMIT 1",
                (station_id.strip(),),
            ).fetchone()
        else:
            station = conn.execute(
                "SELECT station_id, station_name, latitude, longitude, elevation_m FROM climate_stations ORDER BY station_id LIMIT 1"
            ).fetchone()
    if row is None or int(row["record_count"] or 0) == 0:
        return {"status": "not_found"}
    data = dict(row)
    if station is not None:
        data["station"] = dict(station)
    return {"status": "success", "data": data}


def _extract_metric(source: Dict[str, Any], paths: Sequence[Tuple[str, ...]]) -> Optional[float]:
    for path in paths:
        current: Any = source
        ok = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                ok = False
                break
            current = current[key]
        if ok:
            value = _safe_float(current)
            if value is not None:
                return value
    return None


def _reference_comparison(subplot_id: str, dimensions: Dict[str, Any]) -> Dict[str, Any]:
    repository = _repository_subplots()
    if not repository or subplot_id not in repository:
        return {
            "status": "not_available",
            "reference_group": "all_available_subplots",
            "message": "样方仓库中缺少可用于比较的预计算指标。",
        }

    current = repository[subplot_id]
    specs = {
        "stand_density_per_ha": ("林分密度", ["density_per_ha", "stand_density_per_ha"], "higher_value_only"),
        "mean_dbh_cm": ("平均胸径", ["mean_dbh_cm", "arithmetic_mean_dbh_cm"], "higher_value_only"),
        "basal_area_per_ha_m2": ("单位面积断面积", ["ba_per_ha", "basal_area_per_ha_m2"], "higher_value_only"),
        "shannon_index": ("乔木层Shannon指数", ["shannon_index"], "higher_value_only"),
        "mean_hdr": ("平均高径比", ["mean_hdr"], "higher_attention_when_high"),
        "high_hdr_ratio_pct": ("高细长性关注比例", ["high_hdr_ratio_pct", "high_windthrow_risk_pct"], "higher_attention_when_high"),
    }
    values: Dict[str, Any] = {}
    for metric_id, (name_zh, keys, direction) in specs.items():
        current_value = next((_safe_float(current.get(k)) for k in keys if _safe_float(current.get(k)) is not None), None)
        population: List[float] = []
        for record in repository.values():
            candidate = next((_safe_float(record.get(k)) for k in keys if _safe_float(record.get(k)) is not None), None)
            if candidate is not None:
                population.append(candidate)
        values[metric_id] = {
            "name_zh": name_zh,
            "value": current_value,
            "percentile": _percentile_rank(current_value, population),
            "reference_count": len(population),
            "interpretation_direction": direction,
        }
    return {
        "status": "success",
        "reference_group": "all_available_subplots",
        "reference_group_description": "当前版本以所有有可用指标的样方作为全局参考组。",
        "metrics": values,
    }


def _diagnostic_signals(dimensions: Dict[str, Any], reference: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    quality_flags: List[str] = []
    metrics = reference.get("metrics", {}) if isinstance(reference, dict) else {}
    for metric_id, signal_id, label in [
        ("mean_hdr", "SIG_RELATIVE_HIGH_MEAN_HDR", "平均高径比处于参考组高位"),
        ("high_hdr_ratio_pct", "SIG_RELATIVE_HIGH_SLENDERNESS_SHARE", "高细长性关注木比例处于参考组高位"),
    ]:
        metric = metrics.get(metric_id, {})
        pct = _safe_float(metric.get("percentile"))
        if pct is not None and pct >= 90:
            signals.append({
                "signal_id": signal_id,
                "label": label,
                "level": "attention",
                "basis": {"metric_id": metric_id, "percentile": pct, "reference_group": reference.get("reference_group")},
                "status": "provisional_relative_signal",
            })

    competition = dimensions.get("competition", {})
    mean_hegyi = _extract_metric(
        {"competition": competition},
        [("competition", "mean_hegyi_ci"), ("competition", "stand_summary", "mean_hegyi_ci")],
    )
    if mean_hegyi is not None:
        signals.append({
            "signal_id": "SIG_COMPETITION_METRIC_AVAILABLE",
            "label": "已计算空间竞争指标，建议结合百分位进行相对筛查",
            "level": "information",
            "basis": {"mean_hegyi_ci": mean_hegyi},
            "status": "indicator_available",
        })

    if dimensions.get("volume", {}).get("formal_result_available") is not True:
        quality_flags.append("VOLUME_MODEL_NOT_FORMALLY_VALIDATED")
    for name, result in dimensions.items():
        if isinstance(result, dict):
            if result.get("status") in {"failed", "not_available"}:
                quality_flags.append(f"{name.upper()}_NOT_AVAILABLE")
            if isinstance(result.get("quality_flags"), list):
                quality_flags.extend(str(x) for x in result["quality_flags"])

    attention_count = sum(1 for s in signals if s.get("level") == "attention")
    priority = "high" if attention_count >= 2 else "medium" if attention_count == 1 else "low_or_unassessed"
    return signals, {
        "relative_inspection_priority": priority,
        "method": "provisional_relative_ranking",
        "attention_signal_count": attention_count,
        "interpretation_boundary": "仅用于现场核查排序，不表示死亡概率、灾害概率或病虫害确诊。",
        "quality_flags": sorted(set(quality_flags)),
    }


def _compute_subplot_payload(subplot_id: str) -> Dict[str, Any]:
    start = time.perf_counter()
    dimensions = {
        "stand_structure": _call_tool("tool_calc_stand_structure_metrics", subplot_id),
        "species_diversity": _call_tool("tool_calc_species_diversity_metrics", subplot_id),
        "competition": _call_tool("tool_calc_hegyi_competition", subplot_id, ""),
        "tree_morphology": _call_tool("tool_calc_tree_morphology_metrics", subplot_id),
        "deadwood": _call_tool("tool_calc_deadwood_metrics", subplot_id),
        "shrub_layer": _call_tool("tool_calc_shrub_metrics", subplot_id),
        "volume": _sanitize_volume_result(_call_tool("tool_calc_volume_metrics", subplot_id)),
        "environment": {"climate_summary": _get_climate_summary()},
    }
    reference = _reference_comparison(str(subplot_id), dimensions)
    signals, priority = _diagnostic_signals(dimensions, reference)
    return {
        "subplot_id": str(subplot_id),
        "survey_event_id": "EVENT_2023",
        "generated_at": _utc_now(),
        "duration_s": round(time.perf_counter() - start, 4),
        "status": "success",
        "serving_source": "realtime",
        "dimensions": dimensions,
        "reference_comparison": reference,
        "diagnostic_signals": signals,
        "inspection_priority": priority,
        "quality_flags": priority.get("quality_flags", []),
        "calculation_metadata": {
            "api_version": API_VERSION,
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "cache_fingerprint": _cache_fingerprint(),
            "subplot_area_ha": DEFAULT_SUBPLOT_AREA_HA,
        },
    }


def _precompute_one(subplot_id: str, force: bool = True) -> None:
    if not force and _read_cache(subplot_id):
        return
    payload = _compute_subplot_payload(subplot_id)
    payload["serving_source"] = "precomputed"
    _write_cache(subplot_id, payload)


def _context_to_dict(context: Optional[AgentPageContext]) -> Dict[str, Any]:
    if context is None:
        return {}
    return context.model_dump(exclude_none=True) if hasattr(context, "model_dump") else context.dict(exclude_none=True)


def _agent_scope(context: Dict[str, Any]) -> str:
    if context.get("selected_subplot_ids") or context.get("selected_tree_ids"):
        return "multi_object_comparison"
    if context.get("current_tree_id"):
        return "current_tree"
    if context.get("current_subplot_id"):
        return "current_subplot"
    return "global"


def _compose_agent_question(question: str, context: Dict[str, Any]) -> str:
    if not context:
        return question
    lines = ["[网页当前上下文]"]
    lines.extend(f"- {k}: {v}" for k, v in context.items() if v not in (None, "", [], {}))
    return "\n".join(lines + ["[用户问题]", question])


def _materialize_visualization_file(path_value: str) -> Optional[str]:
    candidate = Path(path_value)
    if not candidate.is_absolute():
        candidate = (BASE_DIR / candidate).resolve()
    if not candidate.exists() or not candidate.is_file():
        return None
    destination = VISUALIZATION_DIR / candidate.name
    try:
        if candidate.resolve() != destination.resolve():
            shutil.copy2(candidate, destination)
    except OSError:
        return None
    return destination.name


def _normalize_plot_result(result: Any, request: Request) -> Dict[str, Any]:
    data = _parse_tool_result(result)
    artifact_names: List[str] = []
    path_keys = {"image_path", "file_path", "html_path", "output_path", "chart_path", "png_path", "svg_path"}

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                if key in path_keys and isinstance(value, str):
                    name = _materialize_visualization_file(value)
                    if name:
                        artifact_names.append(name)
                        obj.pop(key, None)
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(data)

    artifacts = []
    for name in sorted(set(artifact_names)):
        rel = f"/visualizations/{name}"
        artifacts.append({
            "artifact_url": rel,
            "artifact_absolute_url": str(request.base_url).rstrip("/") + rel,
        })
    existing = data.get("image_url") or data.get("artifact_url") or data.get("html_url")
    if isinstance(existing, str) and existing.startswith("/visualizations/"):
        artifacts.append({
            "artifact_url": existing,
            "artifact_absolute_url": str(request.base_url).rstrip("/") + existing,
        })
    return {
        "status": data.get("status", "success"),
        "generated_at": _utc_now(),
        "result": data,
        "artifacts": artifacts,
        "quality_flags": data.get("quality_flags", []),
    }


def _normalize_agent_artifacts(artifacts: Any, request: Request) -> List[Dict[str, Any]]:
    """
    将 agent 返回的图表/文件统一变成前端可直接展示的 URL。
    一个工具结果可能同时包含 html_path 和 png_path，因此这里会拆成多个 artifact。
    """
    if not isinstance(artifacts, list):
        return []

    normalized: List[Dict[str, Any]] = []
    path_keys = ["path", "file_path", "image_path", "output_path", "chart_path", "html_path", "png_path", "svg_path"]

    def push_artifact(source: Dict[str, Any], rel: str, kind: Optional[str] = None) -> None:
        data = dict(source)
        data["artifact_url"] = rel
        data["artifact_absolute_url"] = str(request.base_url).rstrip("/") + rel if rel.startswith("/") else rel
        if kind:
            data["artifact_kind"] = kind
        suffix = Path(rel).suffix.lower().lstrip(".")
        if suffix and "type" not in data:
            data["type"] = suffix
        normalized.append(data)

    for item in artifacts:
        if not isinstance(item, dict):
            continue

        data = dict(item)
        emitted = False

        for key in path_keys:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                name = _materialize_visualization_file(value)
                if name:
                    push_artifact(data, f"/visualizations/{name}", key)
                    emitted = True

        rel = data.get("artifact_url") or data.get("relative_url") or data.get("url")
        if isinstance(rel, str) and rel.strip():
            if rel.startswith("/visualizations/"):
                push_artifact(data, rel, "url")
                emitted = True
            elif rel.startswith("http://") or rel.startswith("https://"):
                data["artifact_url"] = rel
                data["artifact_absolute_url"] = rel
                normalized.append(data)
                emitted = True
            else:
                name = Path(rel).name
                candidate = VISUALIZATION_DIR / name if name else None
                if candidate and candidate.exists():
                    push_artifact(data, f"/visualizations/{name}", "relative_url")
                    emitted = True

        if not emitted:
            normalized.append(data)

    seen = set()
    result: List[Dict[str, Any]] = []
    for item in normalized:
        key = item.get("artifact_absolute_url") or item.get("artifact_url") or json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _resolve_agent_context(payload: AgentChatRequest) -> Tuple[Dict[str, Any], str, str]:
    context = _context_to_dict(payload.context)
    if payload.current_page and "current_page" not in context:
        context["current_page"] = payload.current_page

    # 兼容前端传 subplot_id/tree_id
    if context.get("subplot_id") and not context.get("current_subplot_id"):
        context["current_subplot_id"] = context["subplot_id"]
    if context.get("tree_id") and not context.get("current_tree_id"):
        context["current_tree_id"] = context["tree_id"]

    scope = _agent_scope(context)
    mode = str(payload.mode or "chat").strip().lower()
    if mode not in {"chat", "report"}:
        mode = "chat"
    return context, scope, mode


def _run_agent2(payload: AgentChatRequest, context: Dict[str, Any], mode: str, event_callback=None) -> Dict[str, Any]:
    """
    统一调用新版本体驱动 agent2.py。
    保留 TypeError 兼容，避免 event_callback 或 options 参数签名不同导致崩溃。
    """
    try:
        from agent import run_agent_chat, run_agent_report
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Agent backend unavailable: {exc}") from exc

    options = {
        "max_tool_rounds": payload.max_rounds,
        "history_limit": 6,
        "include_plan": payload.include_plan,
        "include_candidates": payload.include_candidates,
        "include_debug": getattr(payload, "include_debug", False),
    }

    try:
        if mode == "report":
            return run_agent_report(
                question=payload.question,
                session_id=payload.session_id,
                client_id=payload.client_id,
                context=context,
                options=options,
            )
        return run_agent_chat(
            question=payload.question,
            session_id=payload.session_id,
            client_id=payload.client_id,
            context=context,
            options=options,
            event_callback=event_callback,
        )
    except TypeError:
        # 兼容没有 event_callback 参数的版本
        if mode == "report":
            return run_agent_report(
                question=payload.question,
                session_id=payload.session_id,
                client_id=payload.client_id,
                context=context,
                options=options,
            )
        return run_agent_chat(
            question=payload.question,
            session_id=payload.session_id,
            client_id=payload.client_id,
            context=context,
            options=options,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent execution failed: {exc}") from exc


def _maybe_write_agent_report(payload: AgentChatRequest, mode: str, details: Dict[str, Any]) -> Optional[str]:
    if not (payload.save_report or mode == "report"):
        return None
    safe_name = Path(payload.report_file or "Forest_Agent_Report.md").name
    report_path = REPORT_DIR / safe_name
    report_path.write_text(str(details.get("answer", "")), encoding="utf-8")
    return safe_name


def _require_visualization_engine() -> None:
    if not VIS_ENGINE_AVAILABLE:
        raise HTTPException(status_code=503, detail={"message": "可视化引擎不可用", "import_error": VIS_ENGINE_IMPORT_ERROR})


# =============================================================================
# 健康、总览、元数据
# =============================================================================

@app.get("/api/health", tags=["system"])
def health_check() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "ForestryAgent API",
        "version": API_VERSION,
        "time": _utc_now(),
        "database": {"exists": DB_PATH.exists(), "filename": DB_PATH.name},
        "visualization_engine_available": VIS_ENGINE_AVAILABLE,
        "cache_fingerprint": _cache_fingerprint(),
    }


@app.get("/api/overview", tags=["overview"])
def get_overview() -> Dict[str, Any]:
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail=f"数据库不存在：{DB_PATH.name}")
    result: Dict[str, Any] = {
        "plot_id": "PLOT_QL_24HA", "survey_event_id": "EVENT_2023",
        "generated_at": _utc_now(), "subplot_area_ha": DEFAULT_SUBPLOT_AREA_HA,
        "subplot_count": 0, "nominal_area_ha": 0.0, "tree_count": 0,
        "tree_taxon_count": 0, "deadwood_count": None, "shrub_taxon_count": None,
        "data_completeness": {}, "quality_flags": [],
    }
    with sqlite3.connect(DB_PATH) as conn:
        if not _table_exists(conn, "tree_observations"):
            raise HTTPException(status_code=500, detail="缺少 tree_observations 表")
        columns = _table_columns(conn, "tree_observations")
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT subplot_id), "
            "COUNT(DISTINCT CASE WHEN species IS NOT NULL AND TRIM(species)<>'' THEN species END) "
            "FROM tree_observations"
        ).fetchone()
        result["tree_count"], result["subplot_count"], result["tree_taxon_count"] = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
        result["nominal_area_ha"] = round(result["subplot_count"] * DEFAULT_SUBPLOT_AREA_HA, 3)
        total = max(result["tree_count"], 1)
        for label, column in {
            "dbh": "tree_dbh_cm", "height": "tree_height_m", "x_coordinate": "tree_x_m",
            "y_coordinate": "tree_y_m", "species": "species",
        }.items():
            if column not in columns:
                result["data_completeness"][label] = None
                result["quality_flags"].append(f"MISSING_COLUMN_{column.upper()}")
                continue
            if column == "species":
                valid = conn.execute(f"SELECT COUNT(*) FROM tree_observations WHERE {column} IS NOT NULL AND TRIM({column})<>''").fetchone()[0]
            else:
                valid = conn.execute(f"SELECT COUNT(*) FROM tree_observations WHERE {column} IS NOT NULL").fetchone()[0]
            result["data_completeness"][label] = round(100.0 * valid / total, 1)
        if _table_exists(conn, "deadwood_observations") and "total_count" in _table_columns(conn, "deadwood_observations"):
            result["deadwood_count"] = int(conn.execute("SELECT COALESCE(SUM(total_count),0) FROM deadwood_observations").fetchone()[0] or 0)
        if _table_exists(conn, "shrub_observations") and "species" in _table_columns(conn, "shrub_observations"):
            result["shrub_taxon_count"] = int(conn.execute(
                "SELECT COUNT(DISTINCT species) FROM shrub_observations WHERE species IS NOT NULL AND TRIM(species)<>''"
            ).fetchone()[0] or 0)
    return result


@app.get("/api/indicator-definitions", tags=["metadata"])
def list_indicator_definitions() -> Dict[str, Any]:
    return {"count": len(INDICATOR_DEFINITIONS), "items": list(INDICATOR_DEFINITIONS.values())}


@app.get("/api/indicators/{indicator_id}", tags=["metadata"])
def get_indicator_definition(indicator_id: str) -> Dict[str, Any]:
    item = INDICATOR_DEFINITIONS.get(indicator_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"未找到指标：{indicator_id}")
    return item


@app.get("/api/plots", tags=["metadata"])
def list_plot_tools() -> Dict[str, Any]:
    return {"count": len(PLOT_TOOL_REGISTRY), "items": PLOT_TOOL_REGISTRY}


# =============================================================================
# 样方与单木
# =============================================================================

@app.get("/api/subplots", tags=["subplots"])
def list_subplots(
    q: Optional[str] = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=1000, ge=1, le=5000),
    sort_by: str = "subplot_id",
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
) -> List[Dict[str, Any]]:
    repository = _repository_subplots()
    items = []
    if repository:
        for subplot_id, record in repository.items():
            item = dict(record)
            item.setdefault("subplot_id", subplot_id)
            items.append(item)
    elif DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT subplot_id, COUNT(*), AVG(tree_dbh_cm), AVG(tree_height_m) "
                "FROM tree_observations GROUP BY subplot_id"
            ).fetchall()
        for row in rows:
            items.append({
                "subplot_id": str(row[0]), "tree_count": int(row[1] or 0),
                "mean_dbh_cm": round(float(row[2] or 0), 2),
                "mean_height_m": round(float(row[3] or 0), 2),
                "density_per_ha": round((row[1] or 0) / DEFAULT_SUBPLOT_AREA_HA, 1),
            })
    if q:
        items = [x for x in items if q.lower() in str(x.get("subplot_id", "")).lower()]
    items.sort(key=lambda x: (x.get(sort_by) is None, x.get(sort_by)), reverse=order == "desc")
    return items[offset:offset + limit]


@app.get("/api/subplots/{subplot_id}", tags=["subplots"])
def get_subplot(subplot_id: str) -> Dict[str, Any]:
    repository = _repository_subplots()
    if subplot_id in repository:
        return {"subplot_id": subplot_id, **repository[subplot_id]}
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail="数据库不存在")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT COUNT(*), AVG(tree_dbh_cm), AVG(tree_height_m) FROM tree_observations WHERE subplot_id=?",
            (subplot_id,),
        ).fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail=f"未找到样方：{subplot_id}")
    return {
        "subplot_id": subplot_id, "tree_count": int(row[0]),
        "density_per_ha": round(row[0] / DEFAULT_SUBPLOT_AREA_HA, 1),
        "mean_dbh_cm": round(float(row[1] or 0), 2),
        "mean_height_m": round(float(row[2] or 0), 2),
    }


@app.get("/api/subplots/{subplot_id}/metrics", tags=["subplots"])
def get_subplot_metrics(subplot_id: str, force: bool = False) -> Dict[str, Any]:
    if not force:
        cached = _read_cache(subplot_id)
        if cached:
            cached = dict(cached)
            cached["serving_source"] = "cache"
            return cached
    result = _compute_subplot_payload(subplot_id)
    _write_cache(subplot_id, result)
    return result


@app.get("/api/subplots/{subplot_id}/trees", tags=["trees"])
def get_trees(
    subplot_id: str,
    species: Optional[str] = None,
    sort_by: str = "tree_id",
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=5000, ge=1, le=10000),
    include_unverified_volume: bool = False,
) -> List[Dict[str, Any]]:
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail=f"数据库不存在：{DB_PATH.name}")
    with sqlite3.connect(DB_PATH) as conn:
        columns = _table_columns(conn, "tree_observations")
        selected = [x for x in [
            "tree_id", "subplot_id", "species", "tree_dbh_cm", "tree_height_m",
            "tree_x_m", "tree_y_m", "crown_width_mean_m", "crown_base_height_m",
            "health_status", "remarks", "volume_m3",
        ] if x in columns]
        if "tree_id" not in selected:
            raise HTTPException(status_code=500, detail="tree_observations 缺少 tree_id")
        sql = f"SELECT {', '.join(selected)} FROM tree_observations WHERE subplot_id=?"
        params: List[Any] = [subplot_id]
        if species and "species" in selected:
            sql += " AND species=?"
            params.append(species)
        rows = conn.execute(sql, params).fetchall()
    items = []
    for row in rows:
        record = dict(zip(selected, row))
        raw_volume = _safe_float(record.pop("volume_m3", None))
        record["database_volume_m3"] = raw_volume if include_unverified_volume else None
        record["volume_status"] = "unverified_database_field" if raw_volume is not None else "not_available"
        record["volume_display_recommended"] = False
        items.append(record)
    items.sort(key=lambda x: (x.get(sort_by) is None, x.get(sort_by)), reverse=order == "desc")
    return items[offset:offset + limit]


def _tree_peer_percentiles(species: str, dbh: Optional[float], height: Optional[float], hdr: Optional[float]) -> Dict[str, Any]:
    if not DB_PATH.exists() or not species:
        return {"status": "not_available"}
    with sqlite3.connect(DB_PATH) as conn:
        if dbh is None:
            rows = conn.execute(
                "SELECT tree_dbh_cm, tree_height_m FROM tree_observations WHERE species=? AND tree_dbh_cm>0 AND tree_height_m>0",
                (species,),
            ).fetchall()
            group = "同树种有效单木"
        else:
            rows = conn.execute(
                "SELECT tree_dbh_cm, tree_height_m FROM tree_observations WHERE species=? AND tree_dbh_cm>0 AND tree_height_m>0 "
                "AND tree_dbh_cm BETWEEN ? AND ?",
                (species, max(0, dbh - 5), dbh + 5),
            ).fetchall()
            group = "同树种且胸径±5 cm的有效单木"
    dbhs = [float(r[0]) for r in rows]
    heights = [float(r[1]) for r in rows]
    hdrs = [100.0 * float(r[1]) / float(r[0]) for r in rows if float(r[0]) > 0]
    return {
        "status": "success", "reference_group": group, "reference_count": len(rows),
        "percentiles": {
            "dbh_cm": _percentile_rank(dbh, dbhs),
            "height_m": _percentile_rank(height, heights),
            "height_diameter_ratio_hdr": _percentile_rank(hdr, hdrs),
        },
    }


@app.get("/api/trees/{tree_id}", tags=["trees"])
def get_tree_detail(tree_id: str, radius_m: float = Query(default=6.0, gt=0, le=50)) -> Dict[str, Any]:
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail="数据库不存在")
    with sqlite3.connect(DB_PATH) as conn:
        columns = _table_columns(conn, "tree_observations")
        selected = [x for x in [
            "tree_id", "subplot_id", "species", "tree_dbh_cm", "tree_height_m",
            "tree_x_m", "tree_y_m", "crown_width_mean_m", "crown_base_height_m",
            "branch_count", "health_status", "remarks", "volume_m3",
        ] if x in columns]
        row = conn.execute(f"SELECT {', '.join(selected)} FROM tree_observations WHERE tree_id=?", (tree_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"未找到单木：{tree_id}")
    record = dict(zip(selected, row))
    subplot_id = str(record.get("subplot_id", ""))
    species = str(record.get("species") or "")
    dbh, height = _safe_float(record.get("tree_dbh_cm")), _safe_float(record.get("tree_height_m"))
    cw, cbh = _safe_float(record.get("crown_width_mean_m")), _safe_float(record.get("crown_base_height_m"))
    raw_db_volume = _safe_float(record.get("volume_m3"))
    flags: List[str] = []
    basal_area = math.pi * (dbh / 100.0) ** 2 / 4.0 if dbh and dbh > 0 else None
    hdr = 100.0 * height / dbh if dbh and height and dbh > 0 and height > 0 else None
    crown_length = live_crown_ratio = None
    if height and height > 0 and cbh is not None:
        if 0 <= cbh <= height:
            crown_length = height - cbh
            live_crown_ratio = crown_length / height
        else:
            flags.append("INVALID_CROWN_BASE_HEIGHT")
    cdr = 100.0 * cw / dbh if dbh and dbh > 0 and cw is not None and cw >= 0 else None
    competition = _call_tool("tool_calc_hegyi_competition", subplot_id, tree_id, radius_m)
    morphology = _call_tool("tool_calc_tree_morphology_metrics", subplot_id, tree_id)
    calculated_volume = None
    calc_tree_metrics = getattr(fst, "calc_tree_metrics", None)
    if calc_tree_metrics and dbh and height:
        try:
            calculated_volume = _parse_tool_result(calc_tree_metrics(species, dbh, height))
        except Exception as exc:
            calculated_volume = {"status": "failed", "error": str(exc)}
    formal_volume = bool(isinstance(calculated_volume, dict) and (
        calculated_volume.get("formal_result_available") or calculated_volume.get("formal") or calculated_volume.get("is_formal")
    ))
    if not formal_volume:
        flags.append("VOLUME_MODEL_NOT_FORMALLY_VALIDATED")
    peer = _tree_peer_percentiles(species, dbh, height, hdr)
    signals = []
    if hdr is not None and hdr > 80:
        signals.append({
            "signal_id": "SIG_HIGH_SLENDERNESS_PROVISIONAL", "label": "高细长性关注信号",
            "status": "provisional_external_threshold", "basis": {"hdr": round(hdr, 2), "threshold": 80},
            "interpretation_boundary": "不等同于已确认风折、风倒或死亡风险。",
        })
    hdr_pct = _safe_float(peer.get("percentiles", {}).get("height_diameter_ratio_hdr") if isinstance(peer.get("percentiles"), dict) else None)
    if hdr_pct is not None and hdr_pct >= 90:
        signals.append({
            "signal_id": "SIG_RELATIVE_HIGH_SLENDERNESS", "label": "同类树中高径比处于高位",
            "status": "relative_signal", "basis": {"percentile": hdr_pct, "reference_group": peer.get("reference_group")},
        })
    observations = {k: v for k, v in record.items() if k != "volume_m3"}
    observations["database_volume_m3"] = raw_db_volume
    observations["database_volume_status"] = "unverified_database_field" if raw_db_volume is not None else "not_available"
    return {
        "tree_id": tree_id, "subplot_id": subplot_id, "species": species, "survey_event_id": "EVENT_2023",
        "observations": observations,
        "computed_metrics": {
            "basal_area_m2": round(basal_area, 8) if basal_area is not None else None,
            "height_diameter_ratio_hdr": round(hdr, 3) if hdr is not None else None,
            "crown_length_m": round(crown_length, 3) if crown_length is not None else None,
            "live_crown_ratio_lcr": round(live_crown_ratio, 4) if live_crown_ratio is not None else None,
            "crown_diameter_ratio_cdr": round(cdr, 3) if cdr is not None else None,
            "volume": {"formal_result_available": formal_volume, "result": calculated_volume, "display_recommended": formal_volume},
        },
        "competition_analysis": competition,
        "morphology_tool_result": morphology,
        "topography_context": _get_tree_topography_context(tree_id),
        "climate_summary": _get_climate_summary(),
        "reference_percentiles": peer,
        "diagnostic_signals": signals,
        "quality_flags": sorted(set(flags)),
        "provenance": {
            "source_table": "tree_observations", "database_record_key": tree_id,
            "formula_ids": ["F_TREE_BASAL_AREA_V1", "F_TREE_HDR_V1", "F_TREE_LCR_V1", "F_TREE_CDR_V1"],
            "tool_ids": ["tool_calc_hegyi_competition", "tool_calc_tree_morphology_metrics"],
            "generated_at": _utc_now(),
        },
    }


# =============================================================================
# 公式、缓存、智能体
# =============================================================================

@app.post("/api/execute_formula", tags=["formula"])
def execute_formula(payload: FormulaExecuteRequest) -> Dict[str, Any]:
    try:
        from formula_execution_engine import NeuroSymbolicFormulaEngine
        result = NeuroSymbolicFormulaEngine().execute_formula(payload.knowledge_id, payload.context_json)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"未找到公式：{payload.knowledge_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"公式执行失败：{exc}") from exc
    return {"status": "success", "knowledge_id": payload.knowledge_id, "inputs": payload.context_json, "result": result, "generated_at": _utc_now()}


@app.post("/api/precompute", tags=["cache"])
def precompute_all(background_tasks: BackgroundTasks, payload: Optional[PrecomputeRequest] = Body(default=None)) -> Dict[str, Any]:
    data = payload or PrecomputeRequest()
    ids = data.subplot_ids or _get_all_subplot_ids()
    for sid in ids:
        background_tasks.add_task(_precompute_one, str(sid), data.force)
    return {"status": "started", "count": len(ids), "force": data.force, "cache_fingerprint": _cache_fingerprint()}


@app.post("/api/precompute/protected", tags=["cache"])
def precompute_protected(
    background_tasks: BackgroundTasks,
    payload: Optional[PrecomputeRequest] = Body(default=None),
    authorized: bool = Depends(verify_api_key),
) -> Dict[str, Any]:
    return precompute_all(background_tasks, payload)


@app.get("/api/precompute/status/{subplot_id}", tags=["cache"])
def precompute_status(subplot_id: str) -> Dict[str, Any]:
    path = _cache_path(subplot_id)
    if not path.exists():
        return {"subplot_id": subplot_id, "cached": False, "status": "not_cached", "current_fingerprint": _cache_fingerprint()}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"subplot_id": subplot_id, "cached": True, "status": "invalid_cache_file", "error": str(exc)}
    stale = data.get("cache_fingerprint") != _cache_fingerprint()
    return {
        "subplot_id": subplot_id, "cached": True, "status": "stale" if stale else "current", "stale": stale,
        "generated_at": data.get("generated_at"), "serving_source": data.get("serving_source"),
        "cached_fingerprint": data.get("cache_fingerprint"), "current_fingerprint": _cache_fingerprint(),
        "quality_flags": data.get("quality_flags", []),
    }


@app.delete("/api/cache", tags=["cache"])
def clear_cache(authorized: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    deleted = 0
    for path in CACHE_DIR.glob("subplot_*.json"):
        try:
            path.unlink()
            deleted += 1
        except OSError:
            pass
    return {"status": "success", "deleted_files": deleted, "generated_at": _utc_now()}



@app.get("/api/agent/health", tags=["agent"])
def agent_health() -> Dict[str, Any]:
    """
    检查本体驱动 Agent 相关模块是否能正常导入。
    """
    modules = {}
    for name in ["agent", "forestry_spatial_tools", "forestry_visualization_engine", "formula_execution_engine"]:
        try:
            __import__(name)
            modules[name] = {"available": True}
        except Exception as exc:
            modules[name] = {"available": False, "error": str(exc)}
    return {
        "status": "ok" if all(v.get("available") for v in modules.values()) else "degraded",
        "modules": modules,
        "generated_at": _utc_now(),
    }


@app.post("/api/agent/chat", tags=["agent"])
def agent_chat(payload: AgentChatRequest, request: Request, authorized: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    """
    本体驱动智能体主入口。
    保留旧 API 的 question/context/max_rounds，同时支持：
    - session_id 连续对话
    - mode=chat/report
    - semantic_plan/candidate_preview 右侧工作区展示
    - artifacts 图表/文件 URL 规范化
    """
    context, scope, mode = _resolve_agent_context(payload)
    details = _run_agent2(payload, context=context, mode=mode)
    report_file = _maybe_write_agent_report(payload, mode, details)
    artifacts = _normalize_agent_artifacts(details.get("artifacts", []), request)

    warnings: List[str] = []
    if not context and any(word in payload.question for word in ("这个样方", "该样方", "这棵树", "该单木", "这里", "它")):
        warnings.append("问题包含指代词，但没有页面上下文；可能需要补充样方或单木编号。")
    if not context:
        warnings.append("No page context supplied; the agent will answer from question text, ontology/database candidates, and tools.")

    # agent2.py 不同版本字段名可能略有不同，这里统一透传常见字段
    semantic_plan = (
        details.get("semantic_plan")
        or details.get("analysis_plan")
        or details.get("plan")
    )
    candidate_preview = (
        details.get("candidate_preview")
        or details.get("ontology_candidates")
        or details.get("candidates")
        or []
    )

    return {
        "status": "success",
        "question": payload.question,
        "scope": scope,
        "context": context,
        "session_id": details.get("session_id") or payload.session_id,
        "client_id": payload.client_id,
        "mode": mode,
        "answer_type": details.get("answer_type", "chat_answer"),
        "answer": details.get("answer", ""),
        "semantic_plan": semantic_plan,
        "candidate_preview": candidate_preview,
        "report_file": report_file,
        "artifacts": artifacts,
        "used_tools": details.get("used_tools", []),
        "followups": details.get("followups", []),
        "last_focus": details.get("last_focus", {}),
        "visible_process": details.get("visible_process", []),
        "evidence_summary": details.get("evidence_summary", []),
        "debug_trace": details.get("debug_trace", []),
        "tool_call_count": len(details.get("used_tools", [])),
        "warnings": warnings,
        "generated_at": _utc_now(),
    }


@app.post("/api/agent/chat/stream", tags=["agent"])
def agent_chat_stream(payload: AgentChatRequest, request: Request, authorized: bool = Depends(verify_api_key)) -> StreamingResponse:
    """
    NDJSON 流式接口：用于前端展示“阶段性工作过程”。
    这里用队列把 agent2.py 的 event_callback 实时转发给浏览器，而不是等整个回答结束后再一次性吐出。
    """
    import json as _json
    import time as _time
    import queue as _queue
    import threading as _threading

    context, scope, mode = _resolve_agent_context(payload)

    def _iter_stream():
        q: _queue.Queue = _queue.Queue()
        done = {"value": False}
        holder: Dict[str, Any] = {"details": None, "error": None}

        def _send(event_type: str, data: Dict[str, Any]) -> str:
            return _json.dumps({"type": event_type, "data": data}, ensure_ascii=False, default=str) + "\n"

        def _push(event_type: str, data: Dict[str, Any]) -> None:
            q.put({"type": event_type, "data": data})

        def _worker() -> None:
            try:
                holder["details"] = _run_agent2(payload, context=context, mode=mode, event_callback=_push)
            except Exception as exc:
                holder["error"] = str(exc)
            finally:
                done["value"] = True
                q.put({"type": "__done__", "data": {}})

        yield _send("start", {"question": payload.question, "scope": scope, "mode": mode})
        thread = _threading.Thread(target=_worker, daemon=True)
        thread.start()

        # 实时转发 stage/tool/final 之前的事件
        while True:
            try:
                item = q.get(timeout=0.2)
            except _queue.Empty:
                if done["value"]:
                    break
                # 心跳，防止前端误以为连接断开
                yield _send("heartbeat", {"time": _utc_now()})
                continue
            if item.get("type") == "__done__":
                break
            yield _send(str(item.get("type")), item.get("data") or {})

        if holder.get("error"):
            yield _send("error", {"message": holder["error"]})
            return

        details = holder.get("details") or {}
        report_file = _maybe_write_agent_report(payload, mode, details)
        artifacts = _normalize_agent_artifacts(details.get("artifacts", []), request)
        answer = str(details.get("answer", "") or "")

        for idx in range(0, len(answer), 80):
            yield _send("answer_chunk", {"text": answer[idx: idx + 80]})
            _time.sleep(0.003)

        final_payload = {
            "status": "success",
            "question": payload.question,
            "scope": scope,
            "context": context,
            "session_id": details.get("session_id") or payload.session_id,
            "mode": mode,
            "answer_type": details.get("answer_type", "chat_answer"),
            "answer": answer,
            "semantic_plan": details.get("semantic_plan") or details.get("analysis_plan") or details.get("plan"),
            "candidate_preview": details.get("candidate_preview") or details.get("ontology_candidates") or details.get("candidates") or [],
            "report_file": report_file,
            "artifacts": artifacts,
            "used_tools": details.get("used_tools", []),
            "followups": details.get("followups", []),
            "last_focus": details.get("last_focus", {}),
            "visible_process": details.get("visible_process", []),
            "evidence_summary": details.get("evidence_summary", []),
            "debug_trace": details.get("debug_trace", []),
            "tool_call_count": len(details.get("used_tools", [])),
            "warnings": [] if context else ["No page context supplied; the agent answers from question text, ontology/database candidates, and tools."],
            "generated_at": _utc_now(),
        }
        yield _send("final", final_payload)

    return StreamingResponse(_iter_stream(), media_type="application/x-ndjson; charset=utf-8")


@app.get("/api/agent/sessions", tags=["agent"])
def agent_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    client_id: Optional[str] = Query(default=None),
    authorized: bool = Depends(verify_api_key),
) -> Dict[str, Any]:
    """返回已保存的智能体历史会话列表。"""
    try:
        from agent import list_chat_sessions
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Agent session backend unavailable: {exc}") from exc
    return {
        "status": "success",
        "sessions": list_chat_sessions(limit=limit, client_id=client_id),
        "generated_at": _utc_now(),
    }


@app.get("/api/agent/sessions/{session_id}/messages", tags=["agent"])
def agent_session_messages(
    session_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
    authorized: bool = Depends(verify_api_key),
) -> Dict[str, Any]:
    """返回指定 session_id 的历史消息，用于前端恢复对话。"""
    try:
        from agent import load_session_messages, load_last_focus
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Agent session backend unavailable: {exc}") from exc
    return {
        "status": "success",
        "session_id": session_id,
        "messages": load_session_messages(session_id=session_id, limit=limit),
        "last_focus": load_last_focus(session_id),
        "generated_at": _utc_now(),
    }


@app.post("/api/agent/sessions/{session_id}/regenerate", tags=["agent"])
def agent_regenerate_last_answer(
    session_id: str,
    request: Request,
    max_rounds: int = Query(default=6, ge=1, le=30),
    authorized: bool = Depends(verify_api_key),
) -> Dict[str, Any]:
    """删除当前会话最后一轮问答，并基于最后一个用户问题重新生成回答。"""
    try:
        from agent import delete_messages_from_time, load_last_user_turn, run_agent_chat
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Agent session backend unavailable: {exc}") from exc

    last_turn = load_last_user_turn(session_id)
    if not last_turn or not str(last_turn.get("question") or "").strip():
        raise HTTPException(status_code=404, detail="该会话没有可重新生成的用户问题。")

    deleted_count = delete_messages_from_time(session_id, str(last_turn.get("created_at") or ""))
    details = run_agent_chat(
        question=str(last_turn["question"]),
        session_id=session_id,
        context=last_turn.get("context") or {},
        options={"max_tool_rounds": max_rounds, "history_limit": 6},
    )
    artifacts = _normalize_agent_artifacts(details.get("artifacts", []), request)
    return {
        "status": "success",
        "regenerated": True,
        "deleted_message_count": deleted_count,
        "question": last_turn["question"],
        "session_id": details.get("session_id") or session_id,
        "answer_type": details.get("answer_type", "chat_answer"),
        "answer": details.get("answer", ""),
        "artifacts": artifacts,
        "used_tools": details.get("used_tools", []),
        "followups": details.get("followups", []),
        "last_focus": details.get("last_focus", {}),
        "visible_process": details.get("visible_process", []),
        "evidence_summary": details.get("evidence_summary", []),
        "debug_trace": details.get("debug_trace", []),
        "tool_call_count": len(details.get("used_tools", [])),
        "generated_at": _utc_now(),
    }


# =============================================================================
# 现场核查候选清单
# =============================================================================

@app.post("/api/inspection/tasks/generate", tags=["inspection"])
def generate_inspection_tasks(payload: InspectionTaskRequest) -> Dict[str, Any]:
    trees = get_trees(payload.subplot_id, None, "tree_id", "asc", 0, 10000, False)
    if not trees:
        raise HTTPException(status_code=404, detail=f"样方 {payload.subplot_id} 无可用单木")
    comp = _call_tool("tool_calc_hegyi_competition", payload.subplot_id, "", payload.radius_m)
    comp_records = comp.get("all_trees") or comp.get("results") or comp.get("tree_results") or comp.get("top_suppressed_high_risk_trees") or []
    comp_map: Dict[str, float] = {}
    if isinstance(comp_records, list):
        for item in comp_records:
            if isinstance(item, dict):
                tid, ci = str(item.get("tree_id", "")), _safe_float(item.get("hegyi_ci"))
                if tid and ci is not None:
                    comp_map[tid] = ci
    rows = []
    for tree in trees:
        dbh, height = _safe_float(tree.get("tree_dbh_cm")), _safe_float(tree.get("tree_height_m"))
        hdr = 100.0 * height / dbh if dbh and height and dbh > 0 else None
        rows.append({**tree, "hdr": hdr, "hegyi_ci": comp_map.get(str(tree.get("tree_id")))})
    hdr_pop = [x["hdr"] for x in rows if x["hdr"] is not None]
    ci_pop = [x["hegyi_ci"] for x in rows if x["hegyi_ci"] is not None]
    for row in rows:
        hp, cp = _percentile_rank(row["hdr"], hdr_pop), _percentile_rank(row["hegyi_ci"], ci_pop)
        vals = [x for x in (hp, cp) if x is not None]
        row["relative_attention_score"] = round(sum(vals) / len(vals), 1) if vals else 0.0
        row["hdr_percentile_within_subplot"], row["hegyi_percentile_within_subplot"] = hp, cp
        row["attention_signals"] = (["样方内高径比处于P90以上"] if hp is not None and hp >= 90 else []) + (["样方内Hegyi竞争指数处于P90以上"] if cp is not None and cp >= 90 else [])
    ranked = sorted(rows, key=lambda x: x["relative_attention_score"], reverse=True)
    controls = sorted(rows, key=lambda x: x["relative_attention_score"])[:payload.control_count] if payload.include_controls else []

    def task_view(row: Dict[str, Any], group: str) -> Dict[str, Any]:
        return {
            "group": group, "subplot_id": payload.subplot_id, "tree_id": row.get("tree_id"),
            "species": row.get("species"), "x_m": row.get("tree_x_m"), "y_m": row.get("tree_y_m"),
            "dbh_cm": row.get("tree_dbh_cm"), "height_m": row.get("tree_height_m"),
            "hdr": round(row["hdr"], 2) if row.get("hdr") is not None else None,
            "hegyi_ci": row.get("hegyi_ci"), "relative_attention_score": row.get("relative_attention_score"),
            "attention_signals": row.get("attention_signals", []),
            "field_checks": ["存活/死亡/倒伏状态", "枯梢与冠层稀疏", "叶色异常或落叶", "断梢、断枝、倾斜或根盘松动", "蛀孔、流胶、腐朽或根腐迹象", "现场照片与备注"],
        }
    return {
        "status": "success", "subplot_id": payload.subplot_id,
        "method": "within_subplot_relative_attention_ranking",
        "priority_tasks": [task_view(x, "priority") for x in ranked[:payload.top_n]],
        "control_tasks": [task_view(x, "control") for x in controls],
        "interpretation_boundary": "用于缩小现场踏查范围；排序不是死亡概率、灾害概率或病虫害确诊结果。",
        "generated_at": _utc_now(),
    }


# =============================================================================
# 八类绘图接口
# =============================================================================

@app.get("/api/plot/grid", tags=["plots"])
def api_plot_grid(request: Request, metric: str = "stand_density_per_ha") -> Dict[str, Any]:
    _require_visualization_engine()
    return _normalize_plot_result(plot_subplot_grid_heatmap(metric), request)


@app.get("/api/plot/size_class/{subplot_id}", tags=["plots"])
def api_plot_size_class(request: Request, subplot_id: str, target_type: str = "Subplot") -> Dict[str, Any]:
    _require_visualization_engine()
    return _normalize_plot_result(plot_size_class_distribution(subplot_id, target_type), request)


@app.get("/api/plot/species/{subplot_id}", tags=["plots"])
def api_plot_species(request: Request, subplot_id: str, target_type: str = "Subplot") -> Dict[str, Any]:
    _require_visualization_engine()
    return _normalize_plot_result(plot_species_composition(subplot_id, target_type), request)


@app.get("/api/plot/scatter/{subplot_id}", tags=["plots"])
def api_plot_scatter(request: Request, subplot_id: str, x_var: str = "tree_dbh_cm", y_var: str = "tree_height_m") -> Dict[str, Any]:
    _require_visualization_engine()
    return _normalize_plot_result(plot_tree_relationship_scatter(subplot_id, x_var, y_var), request)


@app.get("/api/plot/boxplot", tags=["plots"])
def api_plot_boxplot(request: Request, variable: str = "hdr", group_by: str = "species") -> Dict[str, Any]:
    _require_visualization_engine()
    return _normalize_plot_result(plot_group_comparison_boxplot(variable, group_by), request)


@app.get("/api/plot/spatial/{subplot_id}", tags=["plots"])
def api_plot_spatial(request: Request, subplot_id: str) -> Dict[str, Any]:
    _require_visualization_engine()
    return _normalize_plot_result(plot_tree_spatial_map(subplot_id), request)


@app.get("/api/plot/percentile/{subplot_id}", tags=["plots"])
def api_plot_percentile(request: Request, subplot_id: str) -> Dict[str, Any]:
    _require_visualization_engine()
    return _normalize_plot_result(plot_subplot_percentile_profile(subplot_id), request)






def _require_pyproj_for_conversion() -> None:
    if not PYPROJ_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=(
                "坐标转换需要 pyproj 依赖，但当前环境未安装。"
                "请在后端环境安装 pyproj 后重启服务，例如：pip install pyproj。"
                f" 原始错误：{PYPROJ_IMPORT_ERROR}"
            ),
        )


def _wgs84_to_cgcs2000_gk102(lng: float, lat: float) -> Dict[str, Any]:
    """
    WGS84 经纬度转 CGCS2000 3 度带 GK 中央经线 102E 米制坐标。

    pyproj 的 tmerc 输出顺序是 easting, northing。
    当前数据库字段 tree_x_m 实际对应 northing，tree_y_m 对应 easting，因此返回时保持数据库命名：
    x_m = northing, y_m = easting。
    """
    _require_pyproj_for_conversion()
    source = CRS.from_epsg(4326)
    target = CRS.from_proj4(CGCS2000_GK102_PROJ4)
    transformer = Transformer.from_crs(source, target, always_xy=True)
    easting_m, northing_m = transformer.transform(float(lng), float(lat))
    return {
        "x_m": round(float(northing_m), 3),
        "y_m": round(float(easting_m), 3),
        "northing_m": round(float(northing_m), 3),
        "easting_m": round(float(easting_m), 3),
        "source_crs": "WGS84",
        "target_crs": "CGCS2000_3_Degree_GK_CM_102E",
        "proj4": CGCS2000_GK102_PROJ4,
        "axis_note": "数据库 tree_x_m 对应 northing，tree_y_m 对应 easting。",
    }
@app.post("/api/map/convert-position", tags=["map"])
def convert_position_to_plot_xy(payload: MapPositionConvertRequest) -> Dict[str, Any]:
    """将手机 GPS 经纬度转换为样地平面地图使用的 CGCS2000 GK 102E 米制坐标。"""
    if payload.from_crs.upper() != "WGS84":
        raise HTTPException(status_code=400, detail="第一版仅支持 from_crs=WGS84")
    converted = _wgs84_to_cgcs2000_gk102(payload.lng, payload.lat)
    return {
        "status": "success",
        "input": {"lng": payload.lng, "lat": payload.lat, "from_crs": payload.from_crs},
        "position": converted,
        "usage": "前端将 position.x_m / position.y_m 作为用户当前位置，和 /api/map/trees 返回的树木坐标绘制在同一平面地图上。",
    }


@app.get("/api/map/nearby-trees", tags=["map"])
def get_nearby_trees(
    x: Optional[float] = None,
    y: Optional[float] = None,
    lng: Optional[float] = None,
    lat: Optional[float] = None,
    radius_m: float = Query(default=20.0, gt=0, le=500),
    species: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> Dict[str, Any]:
    """
    查询当前位置附近的树木。

    推荐前端流程：手机 lng/lat -> 调本接口或 /api/map/convert-position -> 后端转为 x/y -> 按米制距离查附近树。
    也可以直接传 x/y，适合前端已经完成坐标转换或点击平面地图时使用。
    """
    if x is None or y is None:
        if lng is None or lat is None:
            raise HTTPException(status_code=400, detail="请提供 x/y 平面坐标，或提供 lng/lat 经纬度。")
        converted = _wgs84_to_cgcs2000_gk102(lng, lat)
        x_value = float(converted["x_m"])
        y_value = float(converted["y_m"])
        position_source = {"type": "lng_lat_converted", "lng": lng, "lat": lat, **converted}
    else:
        x_value = float(x)
        y_value = float(y)
        position_source = {"type": "projected_meter_coordinate", "x_m": x_value, "y_m": y_value}

    radius_value = float(getattr(radius_m, "default", radius_m))
    limit_value = int(getattr(limit, "default", limit))
    where = [
        "t.tree_x_m IS NOT NULL",
        "t.tree_y_m IS NOT NULL",
        "t.tree_x_m BETWEEN ? AND ?",
        "t.tree_y_m BETWEEN ? AND ?",
    ]
    params: List[Any] = [x_value - radius_value, x_value + radius_value, y_value - radius_value, y_value + radius_value]
    if species:
        where.append("t.species = ?")
        params.append(str(species).strip())
    where_sql = " AND ".join(where)
    sql = f"""
        SELECT
            t.tree_id,
            t.subplot_id,
            t.species,
            t.tree_x_m,
            t.tree_y_m,
            t.tree_dbh_cm,
            t.tree_height_m,
            t.health_status,
            g.elevation_m
        FROM tree_observations t
        LEFT JOIN topography_observations g ON t.tree_id = g.tree_id
        WHERE {where_sql}
    """

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()

    nearby = []
    for row in rows:
        record = dict(row)
        tree_x = _safe_float(record.get("tree_x_m"))
        tree_y = _safe_float(record.get("tree_y_m"))
        if tree_x is None or tree_y is None:
            continue
        distance = math.hypot(tree_x - x_value, tree_y - y_value)
        if distance > radius_value:
            continue
        nearby.append({
            "tree_id": record.get("tree_id"),
            "subplot_id": record.get("subplot_id"),
            "species": record.get("species"),
            "x_m": tree_x,
            "y_m": tree_y,
            "distance_m": round(distance, 2),
            "dbh_cm": _safe_float(record.get("tree_dbh_cm")),
            "height_m": _safe_float(record.get("tree_height_m")),
            "health_status": record.get("health_status"),
            "elevation_m": _safe_float(record.get("elevation_m")),
        })
    nearby.sort(key=lambda item: item["distance_m"])
    nearby = nearby[:limit_value]

    return {
        "status": "success",
        "query_position": position_source,
        "radius_m": radius_m,
        "species_filter": species,
        "count": len(nearby),
        "nearby_trees": nearby,
    }
@app.get("/api/map/trees", tags=["map"])
def get_tree_map_points(
    subplot_id: Optional[str] = None,
    species: Optional[str] = None,
    min_x: Optional[float] = None,
    max_x: Optional[float] = None,
    min_y: Optional[float] = None,
    max_y: Optional[float] = None,
    include_topography: bool = True,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50000, ge=1, le=100000),
) -> Dict[str, Any]:
    """
    返回前端地图使用的单木点位数据。

    注意：当前 tree_x_m/tree_y_m 是米制平面坐标，不是 WGS84/GCJ-02 经纬度。
    前端可以先用平面地图/canvas/SVG/Leaflet CRS.Simple 显示；如需叠加高德底图，需要补充投影信息并转换经纬度。
    """
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail=f"数据库不存在：{DB_PATH.name}")
    limit_value = int(getattr(limit, "default", limit))
    offset_value = int(getattr(offset, "default", offset))

    select_fields = [
        "t.tree_id",
        "t.subplot_id",
        "t.species",
        "t.tree_x_m",
        "t.tree_y_m",
        "t.tree_dbh_cm",
        "t.tree_height_m",
        "t.health_status",
    ]
    join_sql = ""
    if include_topography:
        select_fields.extend([
            "g.elevation_m",
            "g.slope_degree",
            "g.aspect_degree",
            "g.slope_position",
        ])
        join_sql = "LEFT JOIN topography_observations g ON t.tree_id = g.tree_id"

    where = ["t.tree_x_m IS NOT NULL", "t.tree_y_m IS NOT NULL"]
    params: List[Any] = []
    if subplot_id:
        where.append("t.subplot_id = ?")
        params.append(str(subplot_id).strip())
    if species:
        where.append("t.species = ?")
        params.append(str(species).strip())
    if min_x is not None:
        where.append("t.tree_x_m >= ?")
        params.append(float(min_x))
    if max_x is not None:
        where.append("t.tree_x_m <= ?")
        params.append(float(max_x))
    if min_y is not None:
        where.append("t.tree_y_m >= ?")
        params.append(float(min_y))
    if max_y is not None:
        where.append("t.tree_y_m <= ?")
        params.append(float(max_y))

    where_sql = " AND ".join(where)
    sql = f"""
        SELECT {', '.join(select_fields)}
        FROM tree_observations t
        {join_sql}
        WHERE {where_sql}
        ORDER BY t.subplot_id, t.tree_id
        LIMIT ? OFFSET ?
    """
    count_sql = f"""
        SELECT COUNT(*), MIN(t.tree_x_m), MAX(t.tree_x_m), MIN(t.tree_y_m), MAX(t.tree_y_m)
        FROM tree_observations t
        WHERE {where_sql}
    """

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        count_row = conn.execute(count_sql, params).fetchone()
        rows = conn.execute(sql, params + [limit_value, offset_value]).fetchall()

    total = int(count_row[0] or 0) if count_row else 0
    bounds = None
    if count_row and count_row[1] is not None:
        bounds = {
            "min_x_m": _safe_float(count_row[1]),
            "max_x_m": _safe_float(count_row[2]),
            "min_y_m": _safe_float(count_row[3]),
            "max_y_m": _safe_float(count_row[4]),
        }

    features: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        x_m = _safe_float(record.pop("tree_x_m", None))
        y_m = _safe_float(record.pop("tree_y_m", None))
        feature = {
            "type": "Feature",
            "id": record.get("tree_id"),
            "geometry": {
                "type": "Point",
                "coordinates": [x_m, y_m],
            },
            "properties": {
                "tree_id": record.get("tree_id"),
                "subplot_id": record.get("subplot_id"),
                "species": record.get("species"),
                "dbh_cm": _safe_float(record.get("tree_dbh_cm")),
                "height_m": _safe_float(record.get("tree_height_m")),
                "health_status": record.get("health_status"),
                "elevation_m": _safe_float(record.get("elevation_m")),
                "slope_degree": _safe_float(record.get("slope_degree")),
                "aspect_degree": _safe_float(record.get("aspect_degree")),
                "slope_position": record.get("slope_position"),
            },
        }
        features.append(feature)

    return {
        "status": "success",
        "data_type": "tree_point_layer",
        "coordinate_reference": {
            "type": "projected_meter_coordinate",
            "x_field": "tree_x_m",
            "y_field": "tree_y_m",
            "is_lng_lat": False,
            "note": "当前坐标为米制平面坐标，可用于样地平面图；如需高德底图，需要另行提供投影并转换为 GCJ-02 经纬度。",
        },
        "filters": {
            "subplot_id": subplot_id,
            "species": species,
            "bbox": {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y},
            "include_topography": include_topography,
        },
        "pagination": {"offset": offset_value, "limit": limit_value, "returned": len(features), "total": total},
        "bounds": bounds,
        "feature_collection": {"type": "FeatureCollection", "features": features},
    }

@app.get("/api/environment/tree-topography/{tree_id}", tags=["environment"])
def api_tree_topography(tree_id: str, _: bool = Depends(verify_api_key)) -> Dict[str, Any]:
    _bootstrap_environment_views()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM vw_tree_topography_context WHERE tree_id = ? LIMIT 1",
            (str(tree_id).strip(),),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Tree topography context not found")
    return {"status": "success", "data": dict(row)}


@app.get("/api/environment/climate-daily", tags=["environment"])
def api_climate_daily(
    station_id: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=31, ge=1, le=366),
    _: bool = Depends(verify_api_key),
) -> Dict[str, Any]:
    _bootstrap_environment_views()
    clauses: List[str] = []
    params: List[Any] = []
    if station_id:
        clauses.append("station_id = ?")
        params.append(station_id.strip())
    if date_from:
        clauses.append("observation_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("observation_date <= ?")
        params.append(date_to)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM vw_climate_daily_normalized {where_sql} ORDER BY observation_date DESC LIMIT ?"
    params.append(int(limit))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return {"status": "success", "count": len(rows), "data": [dict(row) for row in rows]}


@app.get("/api/plot/climate", tags=["plots"])
def api_plot_climate(
    request: Request,
    station_id: Optional[str] = None,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    months: str = Query(default="5,6,7,8,9", description="分析月份，逗号分隔；默认 5-9 月生长季"),
    baseline_start: int = Query(default=1991, description="距平基准期开始年份"),
    baseline_end: int = Query(default=2020, description="距平基准期结束年份"),
    chart_type: str = Query(default="dual_axis_anomaly", description="dual_axis_anomaly 或 raw_series"),
    highlight_years: str = Query(default="", description="需要标注的关注年份，逗号分隔，如 2024"),
    quality_policy: str = Query(default="mark_suspicious", description="mark_suspicious/raw 保留原值并标记异常；exclude_suspicious_precipitation 才剔除异常降水"),
    max_daily_precip_mm: float = Query(default=500.0, gt=0, description="单日降水质控阈值，超过则标记/剔除"),
    format: str = Query(default="html", description="html 或 plotly_json"),
) -> Dict[str, Any]:
    _require_visualization_engine()
    return _normalize_plot_result(
        plot_climate_time_series(
            station_id=station_id,
            start_year=start_year,
            end_year=end_year,
            months=months,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            chart_type=chart_type,
            highlight_years=highlight_years,
            quality_policy=quality_policy,
            max_daily_precip_mm=max_daily_precip_mm,
            output_format=format,
        ),
        request,
    )


# =============================================================================
# 野外调查方案管理（survey_module）
# =============================================================================

class SurveyPlanRequest(BaseModel):
    request: str = Field(..., min_length=1, description="调查需求，如'想看看干旱对青海云杉的影响'")


class ObservationRecordRequest(BaseModel):
    plan_id: int
    rec_id: Optional[int] = None
    tree_id: Optional[str] = None
    subplot_id: Optional[str] = None
    species: Optional[str] = None
    notes: str = ""
    health_status: Optional[str] = None
    pest_signs: Optional[str] = None
    phenophase: Optional[str] = None
    photo_paths: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class UpdateObservationRequest(BaseModel):
    notes: Optional[str] = None
    health_status: Optional[str] = None
    pest_signs: Optional[str] = None
    phenophase: Optional[str] = None
    photo_paths: Optional[str] = None


@app.post("/api/survey/plan", tags=["survey"])
def survey_generate_plan(payload: SurveyPlanRequest) -> Dict[str, Any]:
    """
    AI 生成调查方案
    输入自然语言需求，输出结构化的调查方案（含树级/样地级建议列表）
    """
    try:
        result = svm.generate_survey_plan(payload.request)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"方案生成失败: {exc}")


@app.get("/api/survey/plans", tags=["survey"])
def survey_list_plans(limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    """列出所有调查方案"""
    try:
        result = svm.list_plans(limit=limit)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/survey/plans/{plan_id}", tags=["survey"])
def survey_get_plan(plan_id: int) -> Dict[str, Any]:
    """获取方案详情（含所有建议）"""
    try:
        result = svm.get_plan(plan_id)
        if result["status"] == "error":
            raise HTTPException(status_code=404, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/api/survey/plans/{plan_id}/status", tags=["survey"])
def survey_update_plan_status(plan_id: int, status: str = Query(...)) -> Dict[str, Any]:
    """更新方案状态: draft / active / completed / cancelled"""
    try:
        result = svm.update_plan_status(plan_id, status)
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/survey/observations", tags=["survey"])
def survey_save_observation(payload: ObservationRecordRequest) -> Dict[str, Any]:
    """保存一条野外观察记录"""
    try:
        result = svm.save_observation(
            plan_id=payload.plan_id,
            rec_id=payload.rec_id,
            tree_id=payload.tree_id,
            subplot_id=payload.subplot_id,
            species=payload.species,
            notes=payload.notes,
            health_status=payload.health_status,
            pest_signs=payload.pest_signs,
            phenophase=payload.phenophase,
            photo_paths=payload.photo_paths,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存记录失败: {exc}")


@app.put("/api/survey/observations/{obs_id}", tags=["survey"])
def survey_update_observation(obs_id: int, payload: UpdateObservationRequest) -> Dict[str, Any]:
    """更新观察记录"""
    try:
        kwargs = {k: v for k, v in (payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()).items() if v is not None}
        result = svm.update_observation(obs_id, **kwargs)
        if result["status"] == "error":
            raise HTTPException(status_code=404, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/survey/plans/{plan_id}/observations", tags=["survey"])
def survey_plan_observations(plan_id: int) -> Dict[str, Any]:
    """获取方案的所有观察记录"""
    try:
        return svm.get_plan_observations(plan_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.put("/api/survey/recommendations/{rec_id}/status", tags=["survey"])
def survey_update_rec_status(rec_id: int, status: str = Query(...)) -> Dict[str, Any]:
    """更新单条建议状态: pending / completed / skipped"""
    try:
        result = svm.update_recommendation_status(rec_id, status)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/survey/plans/{plan_id}/report", tags=["survey"])
def survey_generate_report(plan_id: int) -> Dict[str, Any]:
    """生成方案调查报告"""
    try:
        result = svm.generate_report(plan_id)
        if result["status"] == "error":
            raise HTTPException(status_code=404, detail=result["message"])
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=int(os.getenv("FORESTRY_API_PORT", "8000")), reload=True)