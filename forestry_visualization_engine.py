# -*- coding: utf-8 -*-
from __future__ import annotations
"""
forestry_visualization_engine.py
祁连山国家公园 24 公顷乔木林监测样地全栈科学制图与可视化引擎 (V1.0)
完全响应用户“样地调查事实 -> 科学指标计算 -> 规则诊断 -> 工具可视化 -> 智能体解释”闭环：
精细定义并实现 8 大科研可视化算子，支持生成 HTML 交互图表、Plotly JSON 数据及 PNG 截图结构，
直接供前台 Web 页面、可视化大屏以及神经符号大模型智能体选择性调用！
"""

import os
import sys
import math
import json
import sqlite3
import openpyxl
from collections import defaultdict
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from forestry_spatial_tools import calc_tree_metrics, DB_PATH

# 保证中文输出正常
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

EXCEL_PATH = r"E:\Project_Participate\东盟人工智能创新大赛\data\祁连山国家公园乔木林样地数据资料汇总\祁连山国家公园森林生态系统乔木林样地调查汇总表.xlsx"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "visualizations")
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

class ForestryDataRepository:
    """
    单例轻量级全量数据仓库：从底层 Excel/SQLite 中加载全部 600 样方与 35,603 株树数据，
    避免每次绘图重复读盘，支撑秒级出图响应！
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ForestryDataRepository, cls).__new__(cls)
            cls._instance.trees = []           # list of dict
            cls._instance.subplots = {}        # subplot_id -> dict
            cls._instance._load_data()
        return cls._instance

    def _lookup_tree_volume(self, species, dbh_cm, height_m):
        """统一入口：调用 `forestry_spatial_tools.calc_tree_metrics` 以获得单株 `volume_m3`。

        这样可避免在可视化引擎中重复维持多份经验公式，所有模块共享同一份计算核心。
        若调用失败则使用一个轻量的经验回退近似。"""
        try:
            metrics = calc_tree_metrics(species or "", float(dbh_cm or 0.0), float(height_m or 0.0))
            return float(metrics.get("volume_m3", 0.0))
        except Exception:
            if dbh_cm is None or height_m is None or dbh_cm <= 0 or height_m <= 0:
                return 0.0
            # 轻量回退：通用幂律经验（仅作最后保险）
            vol = 0.0000615 * (dbh_cm ** 1.8150) * (height_m ** 0.9850)
            if dbh_cm < 10.0 and vol > 0.02:
                vol = 0.0180
            return round(float(vol), 4)

    def _load_data(self):
        print("[visualization] loading plotting data repository...")
        if os.path.exists(DB_PATH):
            self._load_data_from_sqlite(DB_PATH)
            return
        if os.path.exists(EXCEL_PATH):
            self._load_data_from_excel(EXCEL_PATH)
            return
        raise FileNotFoundError(f"plot data source not found: SQLite={DB_PATH}; Excel={EXCEL_PATH}")

    def _standardize_species(self, species):
        species = str(species).strip() if species else "未知"
        if "云杉" in species: return "青海云杉"
        if "桦" in species: return "白桦" if "白" in species else ("红桦" if "红" in species else "白桦")
        if "杨" in species: return "山杨"
        if "柏" in species: return "祁连圆柏"
        if "花楸" in species: return "花楸"
        return species

    def _append_tree_record(self, sub_stats, sub_id, tree_id, species, dbh_cm, height_m, x_m, y_m, cw_ew, health_status):
        std_sp = self._standardize_species(species)
        dbh_cm = float(dbh_cm or 0.0)
        height_m = float(height_m or 0.0)
        x_m = float(x_m) if x_m is not None else 10.0
        y_m = float(y_m) if y_m is not None else 10.0
        cw_ew = float(cw_ew) if cw_ew is not None else 3.0
        health_status = str(health_status or "健康").strip() or "健康"
        vol = self._lookup_tree_volume(std_sp, dbh_cm, height_m)
        ba = round(math.pi * ((dbh_cm / 200.0) ** 2), 4) if dbh_cm > 0 else 0.0
        hdr = round((height_m / (dbh_cm / 100.0)), 2) if dbh_cm > 0 else 0.0
        tree_id = str(tree_id or f"QSL{sub_id}{len(self.trees)+1:04d}").strip()
        if not tree_id.startswith("QSL"):
            tree_id = f"QSL{sub_id}{tree_id.zfill(4)}"
        attention_level = "高径比关注" if hdr > 80 else ("高径比偏高" if hdr > 65 else "常规")
        priority = 1 if hdr > 80 or (dbh_cm > 35 and health_status != "健康") else (2 if hdr > 65 else 3)
        self.trees.append({
            "subplot_id": sub_id, "tree_id": tree_id, "species": std_sp,
            "tree_x_m": x_m, "tree_y_m": y_m,
            "tree_dbh_cm": dbh_cm, "tree_height_m": height_m,
            "crown_width_ew_m": cw_ew,
            "basal_area_m2": ba, "volume_m3": vol,
            "hdr": hdr, "health_status": health_status,
            "attention_level": attention_level, "priority": priority,
        })
        sub_stats[sub_id]["total_vol"] += vol
        sub_stats[sub_id]["total_ba"] += ba
        sub_stats[sub_id]["tree_count"] += 1
        sub_stats[sub_id]["sp_counts"][std_sp] += 1
        if dbh_cm > 0: sub_stats[sub_id]["dbh_list"].append(dbh_cm)
        if hdr > 0: sub_stats[sub_id]["hdr_list"].append(hdr)

    def _finalize_subplot_stats(self, sub_stats):
        for sid, stats in sub_stats.items():
            if stats["tree_count"] <= 0:
                continue
            r, c = (int(sid[:2]), int(sid[2:])) if str(sid).isdigit() and len(str(sid)) == 4 else (None, None)
            stem_cnt = stats["tree_count"]
            tot_vol = round(stats["total_vol"], 3)
            tot_ba = round(stats["total_ba"], 3)
            per_ha_cnt = stem_cnt / 0.04
            per_ha_vol = round(tot_vol / 0.04, 2)
            per_ha_ba = round(tot_ba / 0.04, 2)
            shannon = 0.0
            for count in stats["sp_counts"].values():
                p = count / stem_cnt
                shannon -= p * math.log(p) if p > 0 else 0
            mean_dbh = round(float(np.mean(stats["dbh_list"])), 2) if stats["dbh_list"] else 0
            mean_hdr = round(float(np.mean(stats["hdr_list"])), 2) if stats["hdr_list"] else 0
            high_hdr_ratio = round(len([h for h in stats["hdr_list"] if h > 80]) / stem_cnt * 100, 1)
            self.subplots[sid] = {
                "subplot_id": sid, "row": r, "col": c,
                "tree_count": stem_cnt, "density_per_ha": per_ha_cnt,
                "total_volume_m3": tot_vol, "volume_per_ha": per_ha_vol,
                "total_ba_m2": tot_ba, "ba_per_ha": per_ha_ba,
                "shannon_index": shannon, "mean_dbh_cm": mean_dbh,
                "mean_hdr": mean_hdr, "high_hdr_ratio_pct": high_hdr_ratio,
            }
        print(f"[visualization] loaded {len(self.subplots)} subplots and {len(self.trees)} trees")

    def _load_data_from_sqlite(self, db_path):
        sub_stats = defaultdict(lambda: {
            "total_vol": 0.0, "total_ba": 0.0, "tree_count": 0,
            "sp_counts": defaultdict(int), "dbh_list": [], "hdr_list": []
        })
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT subplot_id, tree_id, species, tree_height_m, tree_dbh_cm, tree_x_m, tree_y_m, crown_width_ew_m, health_status
                FROM tree_observations
                WHERE subplot_id IS NOT NULL
            """).fetchall()
        for row in rows:
            sub_id = str(row["subplot_id"]).strip().zfill(4)
            self._append_tree_record(sub_stats, sub_id, row["tree_id"], row["species"], row["tree_dbh_cm"], row["tree_height_m"], row["tree_x_m"], row["tree_y_m"], row["crown_width_ew_m"], row["health_status"])
        self._finalize_subplot_stats(sub_stats)

    def _load_data_from_excel(self, excel_path):
        wb = openpyxl.load_workbook(excel_path, data_only=True)
        sheet = wb['乔木林每木调查数据']
        sub_stats = defaultdict(lambda: {
            "total_vol": 0.0, "total_ba": 0.0, "tree_count": 0,
            "sp_counts": defaultdict(int), "dbh_list": [], "hdr_list": []
        })
        for row in sheet.iter_rows(min_row=3, values_only=True):
            if not row or row[1] is None: continue
            sub_id = str(row[1]).strip()
            if not sub_id.isdigit(): continue
            sub_id = sub_id.zfill(4)
            self._append_tree_record(sub_stats, sub_id, row[2], row[5], row[7], row[6], row[3], row[4], row[8], row[13])
        self._finalize_subplot_stats(sub_stats)

def save_and_wrap_plotly(fig, tool_id, title_cn, description, output_format: str = "png"):
    """
    Export Plotly figure. PNG is the default artifact for mobile/H5 chat.
    HTML is generated only when output_format is html/both/all/plotly_json.
    """
    html_path = os.path.join(OUTPUT_DIR, f"{tool_id}.html")
    png_path = os.path.join(OUTPUT_DIR, f"{tool_id}.png")
    output = str(output_format or "png").lower()
    result = {
        "tool_id": tool_id,
        "title": title_cn,
        "description": description,
        "status": "success",
    }
    if output in {"html", "both", "all", "plotly_json"}:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(fig.to_html(include_plotlyjs="cdn"))
        result["html_path"] = html_path
    try:
        fig.write_image(png_path, scale=2, format="png")
        result["png_path"] = png_path
    except Exception as e:
        result["status"] = "partial_success"
        result["png_error"] = str(e)
    if output == "plotly_json":
        try:
            result["plotly_json"] = fig.to_dict()
        except Exception:
            result["plotly_json"] = None
    return result


def _safe_chart_id(value: str) -> str:
    text = str(value or "chart").strip()
    cleaned = []
    for ch in text:
        cleaned.append(ch if ch.isalnum() or ch in {"_", "-"} else "_")
    return "".join(cleaned)[:80] or "chart"


def _parse_json_object(value, default):
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _load_sqlite_rows(table_name: str) -> list[dict]:
    if not os.path.exists(DB_PATH):
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
    return [dict(row) for row in rows]


def _get_chart_dataset(data_source="trees"):
    repo = ForestryDataRepository()
    source = str(data_source or "trees").lower()
    if source in {"tree", "trees", "tree_observations"}:
        return list(repo.trees)
    if source in {"subplot", "subplots", "subplot_summary"}:
        return list(repo.subplots.values())
    if source in {"climate_annual", "annual_climate"}:
        return _load_sqlite_rows("climate_annual_summary")
    if source in {"climate_monthly", "monthly_climate"}:
        return _load_sqlite_rows("climate_monthly_summary")
    raise ValueError(f"unsupported data_source: {data_source}")


def _to_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None



CHART_FONT_FAMILY = "Microsoft YaHei, SimHei, Noto Sans CJK SC, Source Han Sans SC, Arial Unicode MS, sans-serif"

CHART_FIELD_LABELS = {
    "subplot_id": "样方编号",
    "tree_id": "单木编号",
    "species": "树种",
    "tree_dbh_cm": "胸径（cm）",
    "tree_height_m": "树高（m）",
    "tree_x_m": "样方内X坐标（m）",
    "tree_y_m": "样方内Y坐标（m）",
    "crown_width_ew_m": "东西冠幅（m）",
    "basal_area_m2": "单木断面积（m²）",
    "volume_m3": "单木材积（m³）",
    "hdr": "高径比",
    "health_status": "健康状态",
    "attention_level": "形态关注等级",
    "priority": "复核优先级",
    "tree_count": "乔木株数",
    "density_per_ha": "林分密度（株/公顷）",
    "total_volume_m3": "样方总材积（m³）",
    "volume_per_ha": "每公顷材积（m³/ha）",
    "total_ba_m2": "样方总断面积（m²）",
    "ba_per_ha": "每公顷断面积（m²/ha）",
    "shannon_index": "Shannon多样性指数",
    "mean_dbh_cm": "平均胸径（cm）",
    "mean_hdr": "平均高径比",
    "high_hdr_ratio_pct": "高径比偏高个体比例（%）",
    "year": "年份",
    "month": "月份",
    "count": "数量",
    "annual_precipitation_mm": "年降水量（mm）",
    "growing_season_precipitation_mm": "生长季降水量（mm）",
    "mean_temperature_c": "平均气温（℃）",
    "annual_mean_temperature_c": "年平均气温（℃）",
    "growing_season_mean_temperature_c": "生长季平均气温（℃）",
    "temperature_anomaly_c": "气温距平（℃）",
    "precipitation_anomaly_mm": "降水距平（mm）",
}

CHART_TYPE_LABELS = {
    "scatter": "散点图",
    "point": "散点图",
    "bar": "柱状图",
    "column": "柱状图",
    "line": "折线图",
    "box": "箱线图",
    "boxplot": "箱线图",
    "histogram": "直方图",
    "hist": "直方图",
    "spatial": "空间分布图",
    "map_points": "空间分布图",
    "point_map": "空间分布图",
}

DATA_SOURCE_LABELS = {
    "trees": "单木数据",
    "tree": "单木数据",
    "tree_observations": "单木数据",
    "subplots": "样方汇总数据",
    "subplot": "样方汇总数据",
    "subplot_summary": "样方汇总数据",
    "climate_annual": "逐年气候数据",
    "annual_climate": "逐年气候数据",
    "climate_monthly": "逐月气候数据",
    "monthly_climate": "逐月气候数据",
}


def _field_label(field):
    if not field:
        return ""
    return CHART_FIELD_LABELS.get(str(field), str(field))


def _chart_labels(rows):
    keys = rows[0].keys() if rows else []
    return {key: _field_label(key) for key in keys}


def _build_chart_title(data_source, chart, x, y, filters):
    source_label = DATA_SOURCE_LABELS.get(str(data_source or "").lower(), str(data_source or "数据"))
    chart_label = CHART_TYPE_LABELS.get(str(chart or "").lower(), str(chart or "图表"))
    scope = ""
    if isinstance(filters, dict):
        if filters.get("subplot_id"):
            scope = f"样方{filters.get('subplot_id')}"
        elif filters.get("species"):
            scope = str(filters.get("species"))
    metric = ""
    if x and y:
        metric = f"{_field_label(x)}-{_field_label(y)}"
    elif x:
        metric = _field_label(x)
    parts = [part for part in [scope, source_label, metric, chart_label] if part]
    return " ".join(parts)


def _apply_chart_layout(fig):
    fig.update_layout(
        template="plotly_white",
        width=980,
        height=620,
        margin=dict(l=70, r=35, t=90, b=70),
        font=dict(family=CHART_FONT_FAMILY, size=15),
        title=dict(font=dict(family=CHART_FONT_FAMILY, size=22)),
        legend=dict(font=dict(family=CHART_FONT_FAMILY, size=13)),
    )
    fig.update_xaxes(title_font=dict(family=CHART_FONT_FAMILY, size=16), tickfont=dict(family=CHART_FONT_FAMILY, size=13))
    fig.update_yaxes(title_font=dict(family=CHART_FONT_FAMILY, size=16), tickfont=dict(family=CHART_FONT_FAMILY, size=13))
    return fig

def _apply_chart_filters(rows: list[dict], filters: dict) -> list[dict]:
    if not filters:
        return rows
    result = rows
    for key, expected in filters.items():
        if expected is None or expected == "":
            continue
        if isinstance(expected, list):
            expected_set = {str(x) for x in expected}
            result = [r for r in result if str(r.get(key)) in expected_set]
        elif isinstance(expected, dict):
            values = expected.get("in")
            if values is not None:
                values_set = {str(x) for x in values}
                result = [r for r in result if str(r.get(key)) in values_set]
            if expected.get("min") is not None:
                min_val = float(expected["min"])
                result = [r for r in result if _to_float(r.get(key)) is not None and _to_float(r.get(key)) >= min_val]
            if expected.get("max") is not None:
                max_val = float(expected["max"])
                result = [r for r in result if _to_float(r.get(key)) is not None and _to_float(r.get(key)) <= max_val]
            if expected.get("contains"):
                contains = str(expected["contains"])
                result = [r for r in result if contains in str(r.get(key, ""))]
        else:
            result = [r for r in result if str(r.get(key)) == str(expected)]
    return result


def _aggregate_rows(rows: list[dict], group_by: str | None, y: str | None, agg: str) -> list[dict]:
    if not group_by:
        return rows
    groups = defaultdict(list)
    for row in rows:
        groups[row.get(group_by, "未分组")].append(row)
    output = []
    for group, items in groups.items():
        values = [_to_float(item.get(y)) for item in items] if y else []
        values = [v for v in values if v is not None]
        method = str(agg or "count").lower()
        if method == "count" or not y:
            value = len(items)
        elif method in {"mean", "avg"}:
            value = float(np.mean(values)) if values else None
        elif method == "sum":
            value = float(np.sum(values)) if values else None
        elif method == "min":
            value = float(np.min(values)) if values else None
        elif method == "max":
            value = float(np.max(values)) if values else None
        elif method == "median":
            value = float(np.median(values)) if values else None
        else:
            value = len(items)
        output.append({group_by: group, y or "count": value, "count": len(items)})
    return output


def create_generic_chart(
    chart_type="scatter",
    data_source="trees",
    x=None,
    y=None,
    color_by=None,
    size_by=None,
    group_by=None,
    aggregate="none",
    filters_json="{}",
    title=None,
    output_format="png",
):
    """Create a flexible chart from registered forestry datasets. Default output is PNG."""
    filters = _parse_json_object(filters_json, {})
    rows = _apply_chart_filters(_get_chart_dataset(data_source), filters)
    if not rows:
        return {"status": "not_found", "message": "没有符合条件的数据", "filters": filters, "data_source": data_source}

    chart = str(chart_type or "scatter").lower()
    agg = str(aggregate or "none").lower()
    data = rows
    if group_by and (chart in {"bar", "line"} or agg not in {"", "none"}):
        data = _aggregate_rows(rows, group_by, y, agg if agg not in {"", "none"} else "count")
        x = group_by
        y = y or "count"

    display_title = title or _build_chart_title(data_source, chart, x, y, filters)
    hover_fields = list(rows[0].keys())[:20]
    labels = _chart_labels(rows)
    if chart in {"scatter", "point"}:
        if not x or not y:
            return {"status": "error", "message": "scatter 图需要 x 和 y 字段"}
        fig = px.scatter(data, x=x, y=y, color=color_by, size=size_by, hover_data=hover_fields, title=display_title, labels=labels)
    elif chart in {"bar", "column"}:
        if not x or not y:
            return {"status": "error", "message": "bar 图需要 x 和 y 字段；如需按组计数，请传 group_by 和 aggregate=count"}
        fig = px.bar(data, x=x, y=y, color=color_by, title=display_title, labels=labels)
    elif chart == "line":
        if not x or not y:
            return {"status": "error", "message": "line 图需要 x 和 y 字段"}
        fig = px.line(data, x=x, y=y, color=color_by, markers=True, title=display_title, labels=labels)
    elif chart in {"box", "boxplot"}:
        if not x or not y:
            return {"status": "error", "message": "box 图需要 x 分组字段和 y 数值字段"}
        fig = px.box(data, x=x, y=y, color=color_by or x, points="outliers", title=display_title, labels=labels)
    elif chart in {"histogram", "hist"}:
        if not x:
            return {"status": "error", "message": "histogram 图需要 x 字段"}
        fig = px.histogram(data, x=x, color=color_by, nbins=20, title=display_title, labels=labels)
    elif chart in {"spatial", "map_points", "point_map"}:
        x_field = x or "tree_x_m"
        y_field = y or "tree_y_m"
        fig = px.scatter(data, x=x_field, y=y_field, color=color_by or "species", size=size_by or "tree_dbh_cm", hover_data=hover_fields, title=display_title, labels=labels)
        fig.update_yaxes(scaleanchor="x", scaleratio=1)
    else:
        return {"status": "error", "message": f"不支持的 chart_type: {chart_type}"}

    _apply_chart_layout(fig)
    tool_id = "generic_" + _safe_chart_id(f"{data_source}_{chart}_{x}_{y}_{group_by}_{len(rows)}")
    result = save_and_wrap_plotly(fig, tool_id, display_title, f"通用制图：{data_source}/{chart}", output_format)
    result.update({
        "data_source": data_source,
        "chart_type": chart,
        "row_count": len(rows),
        "plotted_count": len(data),
        "fields": {"x": x, "y": y, "color_by": color_by, "size_by": size_by, "group_by": group_by, "aggregate": aggregate},
        "filters": filters,
    })
    return result
# ==============================================================================
# 8 大核心科研制图算子定义 (Eight Core Visualization Tools)
# ==============================================================================

def plot_subplot_grid_heatmap(metric="total_volume_m3"):
    """
    1. 样方格网指标热力图 (plot_subplot_grid_heatmap)
    用途：展示 600 个样方在 24 公顷大样地中（Row 1~30 x Col 1~20）的空间格局分布差异
    """
    repo = ForestryDataRepository()
    grid_matrix = [[0.0 for _ in range(20)] for _ in range(30)]
    text_matrix = [["" for _ in range(20)] for _ in range(30)]
    
    metric_label_map = {
        "total_volume_m3": ("样方实测总蓄积量 (m³)", "m³"),
        "volume_per_ha": ("折合每公顷蓄积量 (m³/ha)", "m³/ha"),
        "density_per_ha": ("每公顷乔木密度 (株/ha)", "株/ha"),
        "shannon_index": ("Shannon 群落多样性指数 (H')", "index"),
        "mean_hdr": ("样方平均高径比 (HDR)", "比值"),
        "high_hdr_ratio_pct": ("高径比关注个体比例 (%)", "%")
    }
    label_title, unit = metric_label_map.get(metric, ("未知指标", ""))
    
    for r in range(1, 31):
        for c in range(1, 21):
            sid = f"{r:02d}{c:02d}"
            st = repo.subplots.get(sid, {})
            val = st.get(metric, 0.0)
            # Row 从 1~30，在图表 Y 轴由下至上或由上至下展示，这里设 r-1
            grid_matrix[r-1][c-1] = val
            text_matrix[r-1][c-1] = f"样方: {sid}<br>行R{r} 列C{c}<br>乔木数: {st.get('tree_count',0)}<br>{label_title}: {val} {unit}"
            
    fig = go.Figure(data=go.Heatmap(
        z=grid_matrix,
        text=text_matrix,
        hoverinfo="text",
        colorscale="Viridis" if metric != "high_hdr_ratio_pct" else "YlOrRd",
        colorbar=dict(title=unit)
    ))
    
    fig.update_layout(
        title=f"祁连山 24 公顷乔木林监测大样地 - {label_title}格网空间热力图 (20×30 格网)",
        xaxis=dict(title="列坐标 Column (C01 ~ C20)", tickvals=list(range(20)), ticktext=[f"C{i:02d}" for i in range(1, 21)]),
        yaxis=dict(title="行坐标 Row (R01 ~ R30)", tickvals=list(range(30)), ticktext=[f"R{i:02d}" for i in range(1, 31)]),
        width=1000, height=1200,
        template="plotly_white"
    )
    return save_and_wrap_plotly(fig, "plot_subplot_grid_heatmap", f"格网热力图-{label_title}", f"600个样方{label_title}全景分布图")

def plot_size_class_distribution(target_id="2816", target_type="Subplot", species_filter: list | None = None, output_format: str = "png"):
    """
    2. 胸径级结构分布离散柱状图 (plot_size_class_distribution)
    用途：按林业测树学标准对单木进行离散径阶分箱（5, 10, 15, 20, >25 cm），展示群落径级连续性与分布
    """
    repo = ForestryDataRepository()
    trees = [t for t in repo.trees if t["subplot_id"] == target_id] if target_type == "Subplot" else repo.trees
    if species_filter:
        species_set = set(species_filter)
        trees = [t for t in trees if t.get("species") in species_set]
    
    # 按标准林业径阶离散分箱
    bins = ["5 cm以下 (小径木)", "5-10 cm", "10-15 cm", "15-20 cm", "20-25 cm", "25 cm以上 (大径木)"]
    counts = {b: 0 for b in bins}
    vols = {b: 0.0 for b in bins}
    
    for t in trees:
        d = t["tree_dbh_cm"]
        v = t["volume_m3"]
        if d < 5.0: b = bins[0]
        elif d < 10.0: b = bins[1]
        elif d < 15.0: b = bins[2]
        elif d < 20.0: b = bins[3]
        elif d < 25.0: b = bins[4]
        else: b = bins[5]
        counts[b] += 1
        vols[b] += v
        
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        x=bins, y=[counts[b] for b in bins],
        name="单木株数 (株)", marker_color="forestgreen",
        text=[f"{counts[b]}株" for b in bins], textposition="auto"
    ), secondary_y=False)
    
    fig.add_trace(go.Scatter(
        x=bins, y=[round(vols[b], 2) for b in bins],
        name="径阶总蓄积量 (m³)", marker_color="darkorange", mode="lines+markers+text",
        text=[f"{round(vols[b],2)}m³" for b in bins], textposition="top center", line=dict(width=3)
    ), secondary_y=True)
    
    fig.update_layout(
        title=f"祁连山监测大样地 - [{target_type}: {target_id}] 乔木胸径分布径阶结构图 (离散径阶双纵轴)",
        xaxis_title="标准离散径阶分组 (DBH Class)",
        yaxis_title="乔木株数 (株)",
        yaxis2_title="径阶蓄积量 (m³)",
        width=900, height=550, template="plotly_white"
    )
    return save_and_wrap_plotly(fig, f"plot_size_class_{target_id}", f"胸径级结构图_{target_id}", f"{target_id}样方标准测树学径阶分布", output_format)

def plot_species_composition(target_id="2816", target_type="Subplot", species_filter: list | None = None, output_format: str = "png"):
    """
    3. 树种多维组成对比图 (plot_species_composition)
    用途：绝对不混淆或使用单层饼图，而是严格分类并列展示各树种在【株数组成比 %】 vs 【胸高断面积组成比 %】 vs 【立木蓄积贡献比 %】的显著差异！
    """
    repo = ForestryDataRepository()
    trees = [t for t in repo.trees if t["subplot_id"] == target_id] if target_type == "Subplot" else repo.trees
    if species_filter:
        species_set = set(species_filter)
        trees = [t for t in trees if t.get("species") in species_set]
    
    tot_cnt = len(trees)
    tot_ba = sum(t["basal_area_m2"] for t in trees)
    tot_vol = sum(t["volume_m3"] for t in trees)
    
    sp_stats = defaultdict(lambda: {"cnt": 0, "ba": 0.0, "vol": 0.0})
    for t in trees:
        sp = t["species"]
        sp_stats[sp]["cnt"] += 1
        sp_stats[sp]["ba"] += t["basal_area_m2"]
        sp_stats[sp]["vol"] += t["volume_m3"]
        
    sp_list = sorted(sp_stats.keys(), key=lambda s: sp_stats[s]["vol"], reverse=True)
    cnt_pcts = [round((sp_stats[s]["cnt"]/tot_cnt)*100, 2) if tot_cnt>0 else 0 for s in sp_list]
    ba_pcts = [round((sp_stats[s]["ba"]/tot_ba)*100, 2) if tot_ba>0 else 0 for s in sp_list]
    vol_pcts = [round((sp_stats[s]["vol"]/tot_vol)*100, 2) if tot_vol>0 else 0 for s in sp_list]
    
    fig = go.Figure()
    fig.add_trace(go.Bar(x=sp_list, y=cnt_pcts, name="株数多度占比 (%)", marker_color="mediumseagreen", text=[f"{p}%" for p in cnt_pcts], textposition="auto"))
    fig.add_trace(go.Bar(x=sp_list, y=ba_pcts, name="断面积优势度占比 (%)", marker_color="royalblue", text=[f"{p}%" for p in ba_pcts], textposition="auto"))
    fig.add_trace(go.Bar(x=sp_list, y=vol_pcts, name="立木蓄积量贡献占比 (%)", marker_color="goldenrod", text=[f"{p}%" for p in vol_pcts], textposition="auto"))
    
    fig.update_layout(
        title=f"祁连山大样地 - [{target_type}: {target_id}] 主要乔木树种多维生态优势度剖析图 (并列柱状比对)",
        xaxis_title="乔木分类单元 (Taxon)",
        yaxis_title="群落结构相对占比 (%)",
        barmode="group",
        width=900, height=550, template="plotly_white"
    )
    return save_and_wrap_plotly(fig, f"plot_species_comp_{target_id}", f"树种多度与断面积对比图_{target_id}", f"{target_id}样方多重结构组分对比", output_format)

def plot_tree_relationship_scatter(target_id="2816", x_var="tree_dbh_cm", y_var="tree_height_m", species_filter: list | None = None, output_format: str = "png"):
    """
    4. 单木关系散点图 (plot_tree_relationship_scatter)
    用途：展示胸径、树高、高径比等单木形态指标之间的关系。
    """
    repo = ForestryDataRepository()
    trees = [t for t in repo.trees if t["subplot_id"] == target_id] if target_id else repo.trees
    if species_filter:
        species_set = set(species_filter)
        trees = [t for t in trees if t.get("species") in species_set]
    
    fig = px.scatter(
        trees, x=x_var, y=y_var, color="species",
        hover_name="tree_id",
        hover_data=["subplot_id", "tree_dbh_cm", "tree_height_m", "hdr", "attention_level"],
        labels={"tree_dbh_cm": "单木胸径 DBH (cm)", "tree_height_m": "单木树高 H (m)", "species": "树种"},
        title=f"单木关系图 [{target_id or '全部样方'}] - {x_var} vs {y_var}"
    )
    
        
    fig.update_layout(width=900, height=600, template="plotly_white")
    return save_and_wrap_plotly(fig, f"plot_scatter_{target_id}_{x_var}_{y_var}", f"单木关系图_{target_id}", f"单木形态指标关系图", output_format)

def plot_group_comparison_boxplot(variable="hdr", group_by="species", species_filter: list | None = None, output_format: str = "png"):
    """
    5. 分组对比箱线图 (plot_group_comparison_boxplot)
    用途：对比不同物种或不同风险组的中位数、四分位间距与异常单木分布
    """
    repo = ForestryDataRepository()
    var_label = "高径比 (HDR)" if variable == "hdr" else ("胸径 (cm)" if variable == "tree_dbh_cm" else "树高 (m)")
    data = repo.trees
    if species_filter:
        species_set = set(species_filter)
        data = [t for t in data if t.get("species") in species_set]
    
    fig = px.box(
        data, x=group_by, y=variable, color=group_by,
        points="outliers", hover_data=["tree_id", "subplot_id", "hdr"],
        labels={group_by: "分组类别", variable: var_label},
        title=f"祁连山大样地乔木 {var_label} 跨物种结构与离散分布箱线对比图"
    )
    fig.update_layout(width=950, height=550, template="plotly_white")
    return save_and_wrap_plotly(fig, f"plot_boxplot_{variable}_by_{group_by}", f"分组箱线图_{variable}", f"呈现{var_label}组间中位数与异常值")

def plot_tree_spatial_map(target_id="2816", species_filter: list | None = None, output_format: str = "png"):
    """
    6. 单木空间落图与重点踏查标杆导引 (plot_tree_spatial_map) —— 现场落地最高价值算子！
    用途：将小样方内所有活立木在 20m×20m 空间网格中精确定位。圆点大小=胸径，颜色=健康风险；
    系统自动检出触发高优先级的【前 10 棵重点现场核查树号】，以大红星标志并悬浮具体位置，直接指导现场落地踏查！
    """
    repo = ForestryDataRepository()
    trees = [t for t in repo.trees if t["subplot_id"] == target_id]
    if species_filter:
        species_set = set(species_filter)
        trees = [t for t in trees if t.get("species") in species_set]
    
    # 将真实的绝对 UTM 坐标 (如 4111089, 535630) 归一化到样方内的 0-20m 相对局部网格
    if trees:
        min_x = min(t["tree_x_m"] for t in trees)
        min_y = min(t["tree_y_m"] for t in trees)
        for t in trees:
            t["local_x"] = t["tree_x_m"] - min_x
            t["local_y"] = t["tree_y_m"] - min_y
    
    fig = go.Figure()
    
    # 1. 绘制普通单木散点
    normal_trees = [t for t in trees if t["priority"] > 1]
    if normal_trees:
        fig.add_trace(go.Scatter(
            x=[t["local_x"] for t in normal_trees],
            y=[t["local_y"] for t in normal_trees],
            mode="markers",
            name="一般乔木个体 (常规监测)",
            marker=dict(
                size=[max(6, min(25, t["tree_dbh_cm"] * 0.8)) for t in normal_trees],
                color="seagreen", opacity=0.7, line=dict(width=1, color="white")
            ),
            text=[f"编号: {t['tree_id']}<br>树种: {t['species']}<br>DBH: {t['tree_dbh_cm']} cm<br>高径比: {t['hdr']}<br>状态: {t['health_status']}" for t in normal_trees],
            hoverinfo="text"
        ))
        
    # 2. 高径比关注个体叠加显示
    high_attention_trees = sorted([t for t in trees if t["priority"] == 1 or t["hdr"] > 75], key=lambda x: x["hdr"], reverse=True)[:10]
    if high_attention_trees:
        fig.add_trace(go.Scatter(
            x=[t["local_x"] for t in high_attention_trees],
            y=[t["local_y"] for t in high_attention_trees],
            mode="markers+text",
            name="形态关注个体",
            marker=dict(
                size=[max(12, min(30, t["tree_dbh_cm"] * 1.0)) for t in high_attention_trees],
                color="crimson", symbol="star", line=dict(width=2, color="yellow")
            ),
            text=[t["tree_id"].split("QSL")[-1] for t in high_attention_trees],
            textposition="top center",
            textfont=dict(color="crimson", size=11, family="Arial Black"),
            hovertext=[f"形态关注个体<br>编号: {t['tree_id']}<br>位置X: {t['tree_x_m']}m, Y: {t['tree_y_m']}m<br>树种: {t['species']}<br>胸径: {t['tree_dbh_cm']}cm, 树高: {t['tree_height_m']}m<br>高径比 HDR: {t['hdr']}<br>说明: 仅提示现场复核，不代表风险诊断" for t in high_attention_trees],
            hoverinfo="text"
        ))
        
    fig.update_layout(
        title=f"祁连山样地 [{target_id}] - 20m×20m 乔木空间精准坐标分布与落地踏查导航图",
        xaxis=dict(title="样方内东往西 X 坐标 (0 - 20 m)", range=[-1, 21], zeroline=False),
        yaxis=dict(title="样方内南往北 Y 坐标 (0 - 20 m)", range=[-1, 21], zeroline=False),
        width=850, height=850, template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    return save_and_wrap_plotly(fig, f"plot_spatial_map_{target_id}", f"单木空间导引图_{target_id}", f"{target_id}样方20m落地精确定位与高优先级重点树标记", output_format)

def plot_subplot_percentile_profile(target_id="2816", output_format: str = "png"):
    """
    7. 样方关键科研指标百分位画像图 (plot_subplot_percentile_profile)
    用途：将目标样方放在 600 个样方的参考组背景中，精准标明该样方各指标位于群体中的具体百分位 (P1 ~ P99)！
    """
    repo = ForestryDataRepository()
    target_st = repo.subplots.get(target_id, {})
    if not target_st: return {}
    
    metrics = ["density_per_ha", "mean_dbh_cm", "volume_per_ha", "shannon_index", "high_hdr_ratio_pct"]
    metric_names = ["林分乔木密度 (株/ha)", "样方平均胸径 (cm)", "折合每公顷蓄积 (m³/ha)", "Shannon 多样性 (H')", "高细长木占比 (%)"]
    
    percentiles = []
    values = []
    for m in metrics:
        val = target_st.get(m, 0.0)
        values.append(val)
        all_vals = sorted([st.get(m, 0.0) for st in repo.subplots.values()])
        rank = sum(1 for v in all_vals if v <= val)
        pct = round((rank / len(all_vals)) * 100.0, 1) if all_vals else 50.0
        percentiles.append(pct)
        
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=percentiles, y=metric_names, orientation='h',
        marker=dict(color=percentiles, colorscale="RdYlGn_r", showscale=True, colorbar=dict(title="百分位 P")),
        text=[f"P{p} (实测: {v})" for p, v in zip(percentiles, values)],
        textposition="auto"
    ))
    
    fig.update_layout(
        title=f"祁连山样方 [{target_id}] 核心生态指标相对 600 样方群体百位排位画像 (Percentile Profile)",
        xaxis=dict(title="在全样地 600 个样方中所处百分位 (P0 - P100)", range=[0, 105]),
        yaxis=dict(title="关键健康与结构指标"),
        width=900, height=500, template="plotly_white"
    )
    return save_and_wrap_plotly(fig, f"plot_percentile_profile_{target_id}", f"样方百分位排位画像_{target_id}", f"{target_id}在全样地中的百分位水平", output_format)

def _parse_months(months) -> list[int]:
    if months is None:
        return [5, 6, 7, 8, 9]
    if isinstance(months, str):
        values = [m.strip() for m in months.split(",") if m.strip()]
    else:
        values = list(months)
    parsed = []
    for value in values:
        month = int(value)
        if month < 1 or month > 12:
            raise ValueError(f"非法月份: {value}")
        parsed.append(month)
    return sorted(set(parsed)) or [5, 6, 7, 8, 9]


def compute_climate_analysis_series(
    station_id: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    months: list[int] | str | None = None,
    baseline_start: int = 1991,
    baseline_end: int = 2020,
    quality_policy: str = "mark_suspicious",
    max_daily_precip_mm: float = 500.0,
) -> dict:
    """
    基于逐日气候表计算生长季/指定月份的气候序列。

    结果用于背景分析和异常筛查，不直接等同灾害识别或气候影响因果结论。
    """
    month_list = _parse_months(months)
    db_path = str(DB_PATH)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        station_row = None
        if station_id:
            station_row = conn.execute(
                "SELECT * FROM climate_stations WHERE station_id=? LIMIT 1",
                (station_id,),
            ).fetchone()
        if station_row is None:
            station_row = conn.execute("SELECT * FROM climate_stations ORDER BY station_id LIMIT 1").fetchone()
        station = dict(station_row) if station_row else {"station_id": station_id or "unknown"}
        resolved_station_id = str(station.get("station_id") or station_id or "")

        year_bounds = conn.execute(
            "SELECT MIN(CAST(substr(observation_date,1,4) AS INT)), MAX(CAST(substr(observation_date,1,4) AS INT)) "
            "FROM climate_daily_normalized WHERE station_id=?",
            (resolved_station_id,),
        ).fetchone()
        data_start, data_end = int(year_bounds[0] or 0), int(year_bounds[1] or 0)
        start = int(start_year or max(data_start, baseline_start))
        end = int(end_year or data_end)
        placeholders = ",".join("?" for _ in month_list)
        raw_rows = conn.execute(
            f"""
            SELECT
                CAST(substr(observation_date,1,4) AS INT) AS year,
                COUNT(*) AS record_count,
                SUM(CASE WHEN mean_temperature_c IS NOT NULL THEN 1 ELSE 0 END) AS valid_temperature_days,
                SUM(CASE WHEN precipitation_mm IS NOT NULL THEN 1 ELSE 0 END) AS valid_precipitation_days,
                AVG(mean_temperature_c) AS mean_temperature_c,
                SUM(precipitation_mm) AS raw_total_precipitation_mm,
                MAX(precipitation_mm) AS max_daily_precipitation_mm,
                SUM(CASE WHEN precipitation_mm > ? THEN 1 ELSE 0 END) AS suspicious_precipitation_days,
                SUM(CASE WHEN precipitation_mm IS NOT NULL AND precipitation_mm <= ? THEN precipitation_mm ELSE 0 END) AS screened_total_precipitation_mm
            FROM climate_daily_normalized
            WHERE station_id=?
              AND CAST(substr(observation_date,1,4) AS INT) BETWEEN ? AND ?
              AND CAST(substr(observation_date,6,2) AS INT) IN ({placeholders})
            GROUP BY year
            ORDER BY year
            """,
            [max_daily_precip_mm, max_daily_precip_mm, resolved_station_id, start, end] + month_list,
        ).fetchall()

        baseline_rows = conn.execute(
            f"""
            SELECT
                CAST(substr(observation_date,1,4) AS INT) AS year,
                AVG(mean_temperature_c) AS mean_temperature_c,
                SUM(precipitation_mm) AS raw_total_precipitation_mm,
                SUM(CASE WHEN precipitation_mm IS NOT NULL AND precipitation_mm <= ? THEN precipitation_mm ELSE 0 END) AS screened_total_precipitation_mm,
                SUM(CASE WHEN precipitation_mm > ? THEN 1 ELSE 0 END) AS suspicious_precipitation_days
            FROM climate_daily_normalized
            WHERE station_id=?
              AND CAST(substr(observation_date,1,4) AS INT) BETWEEN ? AND ?
              AND CAST(substr(observation_date,6,2) AS INT) IN ({placeholders})
            GROUP BY year
            ORDER BY year
            """,
            [max_daily_precip_mm, max_daily_precip_mm, resolved_station_id, baseline_start, baseline_end] + month_list,
        ).fetchall()
    finally:
        conn.close()

    precip_field = "screened_total_precipitation_mm" if quality_policy == "exclude_suspicious_precipitation" else "raw_total_precipitation_mm"
    baseline_temp = [float(r["mean_temperature_c"]) for r in baseline_rows if r["mean_temperature_c"] is not None]
    baseline_precip = [float(r[precip_field] or 0.0) for r in baseline_rows if r[precip_field] is not None]
    baseline_temp_mean = sum(baseline_temp) / len(baseline_temp) if baseline_temp else None
    baseline_precip_mean = sum(baseline_precip) / len(baseline_precip) if baseline_precip else None

    records = []
    for row in raw_rows:
        mean_temp = float(row["mean_temperature_c"]) if row["mean_temperature_c"] is not None else None
        precip = float(row[precip_field] or 0.0)
        temp_anomaly = mean_temp - baseline_temp_mean if mean_temp is not None and baseline_temp_mean is not None else None
        precip_anomaly = precip - baseline_precip_mean if baseline_precip_mean is not None else None
        precip_anomaly_pct = (precip_anomaly / baseline_precip_mean * 100.0) if baseline_precip_mean else None
        flags = []
        suspicious_days = int(row["suspicious_precipitation_days"] or 0)
        if suspicious_days:
            flags.append("SUSPICIOUS_DAILY_PRECIPITATION_EXCLUDED" if quality_policy == "exclude_suspicious_precipitation" else "SUSPICIOUS_DAILY_PRECIPITATION_PRESENT")
        records.append({
            "year": int(row["year"]),
            "record_count": int(row["record_count"] or 0),
            "valid_temperature_days": int(row["valid_temperature_days"] or 0),
            "valid_precipitation_days": int(row["valid_precipitation_days"] or 0),
            "mean_temperature_c": round(mean_temp, 3) if mean_temp is not None else None,
            "temperature_anomaly_c": round(temp_anomaly, 3) if temp_anomaly is not None else None,
            "total_precipitation_mm": round(precip, 3),
            "precipitation_anomaly_mm": round(precip_anomaly, 3) if precip_anomaly is not None else None,
            "precipitation_anomaly_pct": round(precip_anomaly_pct, 2) if precip_anomaly_pct is not None else None,
            "raw_total_precipitation_mm": round(float(row["raw_total_precipitation_mm"] or 0.0), 3),
            "max_daily_precipitation_mm": round(float(row["max_daily_precipitation_mm"] or 0.0), 3),
            "suspicious_precipitation_days": suspicious_days,
            "quality_flags": flags,
        })

    return {
        "status": "success",
        "station": station,
        "months": month_list,
        "period": {"start_year": start, "end_year": end},
        "baseline": {
            "start_year": baseline_start,
            "end_year": baseline_end,
            "mean_temperature_c": round(baseline_temp_mean, 3) if baseline_temp_mean is not None else None,
            "mean_total_precipitation_mm": round(baseline_precip_mean, 3) if baseline_precip_mean is not None else None,
            "available_years_temperature": len(baseline_temp),
            "available_years_precipitation": len(baseline_precip),
        },
        "quality_policy": quality_policy,
        "max_daily_precip_mm": max_daily_precip_mm,
        "records": records,
        "claim_boundary": "本结果为气候背景和异常筛查；不能单独证明灾害发生或气候对树种生长的因果影响。",
    }


def plot_climate_time_series(
    station_id: str | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    months: list[int] | str | None = None,
    baseline_start: int = 1991,
    baseline_end: int = 2020,
    chart_type: str = "dual_axis_anomaly",
    highlight_years: list[int] | str | None = None,
    quality_policy: str = "mark_suspicious",
    max_daily_precip_mm: float = 500.0,
    output_format: str = "png",
):
    """
    气候背景与异常筛查图。
    支持生长季/指定月份、温度距平、降水距平、关注年份标注和降水质量控制。
    """
    series = compute_climate_analysis_series(
        station_id=station_id,
        start_year=start_year,
        end_year=end_year,
        months=months,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        quality_policy=quality_policy,
        max_daily_precip_mm=max_daily_precip_mm,
    )
    records = series["records"]
    if not records:
        return {"status": "not_found", "message": "未找到符合条件的气候记录", "analysis": series}

    years = [r["year"] for r in records]
    temp_anom = [r["temperature_anomaly_c"] for r in records]
    precip_anom_pct = [r["precipitation_anomaly_pct"] for r in records]
    precip_total = [r["total_precipitation_mm"] for r in records]

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if chart_type in {"dual_axis_anomaly", "anomaly", "dual_axis"}:
        fig.add_trace(go.Bar(
            x=years,
            y=precip_anom_pct,
            name="降水距平 (%)",
            marker_color=["#2ca25f" if (v or 0) >= 0 else "#de2d26" for v in precip_anom_pct],
            opacity=0.78,
            customdata=precip_total,
            hovertemplate="年份=%{x}<br>降水距平=%{y:.1f}%<br>生长季降水=%{customdata:.1f} mm<extra></extra>",
        ), secondary_y=False)
        fig.add_trace(go.Scatter(
            x=years,
            y=temp_anom,
            name="平均气温距平 (℃)",
            mode="lines+markers",
            line=dict(color="firebrick", width=3),
            hovertemplate="年份=%{x}<br>气温距平=%{y:.2f} ℃<extra></extra>",
        ), secondary_y=True)
        left_title = "降水距平 (%)"
    else:
        fig.add_trace(go.Bar(x=years, y=precip_total, name="降水量 (mm)", marker_color="deepskyblue", opacity=0.78), secondary_y=False)
        fig.add_trace(go.Scatter(x=years, y=[r["mean_temperature_c"] for r in records], name="平均气温 (℃)", mode="lines+markers", line=dict(color="firebrick", width=3)), secondary_y=True)
        left_title = "降水量 (mm)"

    highlight_values = []
    if highlight_years:
        if isinstance(highlight_years, str):
            highlight_values = [int(x.strip()) for x in highlight_years.split(",") if x.strip()]
        else:
            highlight_values = [int(x) for x in highlight_years]
    for year in highlight_values:
        fig.add_vline(x=year, line_dash="dash", line_color="darkorange", annotation_text=f"关注年份 {year}")

    suspicious_years = [r for r in records if r["suspicious_precipitation_days"]]
    for row in suspicious_years[:10]:
        fig.add_vline(x=row["year"], line_dash="dot", line_color="gray", annotation_text=f"降水质控 {row['year']}")

    month_text = ",".join(str(m) for m in series["months"])
    baseline = series["baseline"]
    fig.update_layout(
        title=f"气候背景与异常筛查图（月: {month_text}; 基准期: {baseline['start_year']}-{baseline['end_year']}）",
        xaxis_title="年份",
        yaxis_title=left_title,
        yaxis2_title="气温距平 (℃)" if chart_type in {"dual_axis_anomaly", "anomaly", "dual_axis"} else "平均气温 (℃)",
        width=980,
        height=540,
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    result = save_and_wrap_plotly(
        fig,
        f"plot_climate_analysis_{series['period']['start_year']}_{series['period']['end_year']}",
        "气候背景与异常筛查图",
        "基于逐日气候观测计算温度/降水距平；异常为筛查信号，不等同灾害确证。",
        output_format,
    )
    result["analysis"] = series
    return result
if __name__ == "__main__":
    print("=======================================================================")
    print("     祁连山国家公园 24 公顷乔木林全量科学可视化算子生成测试")
    print("=======================================================================")
    
    res1 = plot_subplot_grid_heatmap("total_volume_m3")
    res2 = plot_size_class_distribution("2816", "Subplot")
    res3 = plot_species_composition("2816", "Subplot")
    res4 = plot_tree_relationship_scatter("2816", "tree_dbh_cm", "tree_height_m")
    res5 = plot_group_comparison_boxplot("hdr", "species")
    res6 = plot_tree_spatial_map("2816")
    res7 = plot_subplot_percentile_profile("2816")
    res8 = plot_climate_time_series()
    
    print("\n[全部 8 大科研制图引擎多轨成果导出完毕] HTML 网页交互图与 PNG 高清报告静态图已同时生成于 visualizations/ 目录：")
    for r in [res1, res2, res3, res4, res5, res6, res7, res8]:
        print(f"  √ [{r['tool_id']}]: {r['title']}")
        print(f"     - 网页交互 HTML: {r['html_path']}")
        print(f"     - 报告静态 PNG:  {r['png_path']}")
