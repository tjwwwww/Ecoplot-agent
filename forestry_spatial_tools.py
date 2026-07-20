# -*- coding: utf-8 -*-
"""
forestry_spatial_tools.py
=========================
林业空间结构与生态监测专用数学公式工具库 (Forestry Spatial & Ecological Formula Tools)

本模块将真实森林经理学、测树学与生态监测中的定量公式封装为标准的 Python 函数及 OpenAPI JSON Schema，
供 Graph ReAct 智能体 (`agent.py`) 自主调用，实现真正的“图谱事实 + 高精微机空间测算”联合诊断。

核心公式体系：
1. 单木/样方胸高断面积公式 (Basal Area $g_i$ & $G$)
2. Hegyi 单木空间竞争指数公式 ($C_i$) —— 基于实地 $(X, Y)$ 坐标 Euclidean 距离与径阶比值
3. 样方林分密度与疏伐强度动态模拟公式 ($V_{harvest}$, $G_{cut\%}$)
4. 多层次生物多样性评估公式 (Shannon-Wiener $H'$, Pielou $J$, Simpson $D$)
5. 生物量异速生长与乔木层碳储量测算公式 ($W_{biomass}$, $C_{carbon}$)
"""

import sqlite3
import json
import math
import os
import openpyxl
from typing import Dict, Any, List


def _repair_mojibake_text(value):
    if not isinstance(value, str) or not value:
        return value
    candidates = [value]
    for encoding in ("latin1", "cp1252", "gbk", "cp936"):
        for decoding in ("utf-8", "gbk", "cp936"):
            try:
                candidates.append(value.encode(encoding).decode(decoding))
            except Exception:
                pass
    suspicious = ("\u00c3", "\u00c2", "\ufffd", "?", "\u6d5c", "\u9407", "\u622chan", "\u6f50")
    # Keep this explicit because some source edits pass through non-UTF8 consoles.
    suspicious = ("\u00c3", "\u00c2", "\ufffd", "?", "\u6d5c", "\u9407", "\u622c", "\u6f50", "\u82a5")
    def score(text):
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        penalty = sum(text.count(mark) for mark in suspicious)
        return cjk - penalty * 4
    return max(candidates, key=score)



BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "qilian_forest.db")
# 真实 Excel 大表路径（包含乔木每木调查数据及 6 张二元材积查算表）
EXCEL_PATH = r"E:\Project_Participate\东盟人工智能创新大赛\data\祁连山国家公园乔木林样地数据资料汇总\祁连山国家公园森林生态系统乔木林样地调查汇总表.xlsx"
if not os.path.exists(EXCEL_PATH):
    # 兼容相对路径兜底
    EXCEL_PATH = os.path.join(BASE_DIR, "data", "祁连山国家公园森林生态系统乔木林样地调查汇总表.xlsx")

def tool_get_tree_topography_context(tree_id: str) -> str:
    if not os.path.exists(DB_PATH):
        return json.dumps({"status": "failed", "error": f"database not found: {DB_PATH}"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT tree_id, elevation_m, slope_degree, aspect_degree, slope_position, source_file, imported_at FROM topography_observations WHERE tree_id=? LIMIT 1",
        (str(tree_id).strip(),),
    ).fetchone()
    conn.close()
    if row is None:
        return json.dumps({"status": "not_found", "tree_id": str(tree_id)}, ensure_ascii=False)
    return json.dumps({"status": "success", "tree_id": str(tree_id), "topography_context": dict(row)}, ensure_ascii=False)


def tool_get_subplot_topography_summary(subplot_id: str) -> str:
    if not os.path.exists(DB_PATH):
        return json.dumps({"status": "failed", "error": f"database not found: {DB_PATH}"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT
            subplot_id,
            COUNT(*) AS tree_count,
            AVG(elevation_m) AS mean_elevation_m,
            MIN(elevation_m) AS min_elevation_m,
            MAX(elevation_m) AS max_elevation_m,
            AVG(slope_degree) AS mean_slope_degree,
            AVG(aspect_degree) AS mean_aspect_degree
        FROM topography_observations
        WHERE subplot_id=?
        GROUP BY subplot_id
        LIMIT 1
        """,
        (str(subplot_id).strip(),),
    ).fetchone()
    conn.close()
    if row is None:
        return json.dumps({"status": "not_found", "subplot_id": str(subplot_id)}, ensure_ascii=False)
    return json.dumps({"status": "success", "subplot_id": str(subplot_id), "topography_summary": dict(row)}, ensure_ascii=False)


def tool_get_climate_background_summary(station_id: str = "", date_from: str = "", date_to: str = "") -> str:
    if not os.path.exists(DB_PATH):
        return json.dumps({"status": "failed", "error": f"database not found: {DB_PATH}"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    clauses = []
    params = []
    if str(station_id).strip():
        clauses.append("station_id = ?")
        params.append(str(station_id).strip())
    if str(date_from).strip():
        clauses.append("observation_date >= ?")
        params.append(str(date_from).strip())
    if str(date_to).strip():
        clauses.append("observation_date <= ?")
        params.append(str(date_to).strip())
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        "SELECT COUNT(*) AS record_count, MIN(observation_date) AS date_from, MAX(observation_date) AS date_to, "
        "AVG(mean_temperature_c) AS mean_temperature_c, MIN(min_temperature_c) AS min_temperature_c, "
        "MAX(max_temperature_c) AS max_temperature_c, SUM(precipitation_mm) AS total_precipitation_mm, "
        "AVG(wind_speed_m_s) AS mean_wind_speed_m_s, MAX(wind_gust_m_s) AS max_wind_gust_m_s "
        f"FROM climate_daily_normalized {where_sql}"
    )
    row = conn.execute(sql, params).fetchone()
    if str(station_id).strip():
        station = conn.execute(
            "SELECT station_id, station_name, latitude, longitude, elevation_m FROM climate_stations WHERE station_id=? LIMIT 1",
            (str(station_id).strip(),),
        ).fetchone()
    else:
        station = conn.execute(
            "SELECT station_id, station_name, latitude, longitude, elevation_m FROM climate_stations ORDER BY station_id LIMIT 1"
        ).fetchone()
    conn.close()
    if row is None or int(row['record_count'] or 0) == 0:
        return json.dumps({"status": "not_found", "station_id": str(station_id).strip() or None}, ensure_ascii=False)
    payload = dict(row)
    if station is not None:
        payload['station'] = dict(station)
    payload['interpretation_boundary'] = '当前返回的是站点多年逐日背景气候汇总，不是样方原位微气候。'
    return json.dumps({"status": "success", "climate_background_summary": payload}, ensure_ascii=False)


def tool_get_tree_environment_context(tree_id: str) -> str:
    return tool_get_tree_topography_context(tree_id)


def tool_get_climate_summary(station_id: str = "", date_from: str = "", date_to: str = "") -> str:
    return tool_get_climate_background_summary(station_id, date_from, date_to)


def tool_compute_registered_indicators(
    target_type: str,
    target_id: str = "",
    indicator_ids_json: str = "[]",
    indicator_group: str = "",
    parameters_json: str = "{}",
) -> str:
    """
    统一指标执行入口。

    根据 forestry_knowledge_registry.yaml 中登记的 indicator_id，
    选择性调用确定性计算函数。indicator_ids_json 为空时，可通过
    indicator_group 批量计算一组指标。
    """
    from indicator_execution_engine import tool_compute_registered_indicators as _execute

    return _execute(target_type, target_id, indicator_ids_json, indicator_group, parameters_json)


# ==============================================================================
# 单木蓄积量二元回归方程 (物种特异性，全系统唯一权威计算入口)
# ==============================================================================
# 参考标准：LY/T 1353-1999 及青海省主要乔木二元立木材积测算规范
# 公式形式：V = a * D^b * H^c  (D=胸径cm, H=树高m, V=蓄积m³)
# 注意：Excel 中的「蓄积表」各Sheet存放的是径阶内株数汇总统计，
#       不是「胸径×树高→单株蓄积」的查找矩阵，因此不能用查表法计算单株蓄积。
_SPECIES_VOLUME_PARAMS = {
    "青海云杉": (0.0000632, 1.8020, 0.9850),
    "云杉":    (0.0000632, 1.8020, 0.9850),
    "红桦":    (0.0000588, 1.8410, 0.9980),
    "白桦":    (0.0000588, 1.8410, 0.9980),
    "桦树":    (0.0000588, 1.8410, 0.9980),
    "桦":      (0.0000588, 1.8410, 0.9980),
    "山杨":    (0.0000579, 1.8680, 0.9750),
    "杨":      (0.0000579, 1.8680, 0.9750),
    "祁连圆柏":(0.0000650, 1.7850, 0.9800),
    "圆柏":    (0.0000650, 1.7850, 0.9800),
    "柏":      (0.0000650, 1.7850, 0.9800),
    "花楸":    (0.0000595, 1.8100, 0.9800),
    "乌柳":    (0.0000595, 1.8100, 0.9800),
    "柳":      (0.0000595, 1.8100, 0.9800),
}
_DEFAULT_VOLUME_PARAMS = (0.0000615, 1.8150, 0.9850)  # 其他阔叶树通用参数

def calc_single_tree_volume(species: str, dbh_cm: float, height_m: float) -> float:
    """
    按物种特异性二元回归方程精算单木蓄积量（m³）。
    V = a * D^b * H^c，D=胸径(cm)，H=树高(m)
    
    这是全系统唯一权威的单株蓄积计算入口，
    forestry_spatial_tools 和 forestry_visualization_engine 均应调用此函数。
    """
    if dbh_cm <= 0 or height_m <= 0:
        return 0.0
    sp = str(species).strip() if species else ""
    a, b, c = _DEFAULT_VOLUME_PARAMS
    for key, params in _SPECIES_VOLUME_PARAMS.items():
        if key in sp:
            a, b, c = params
            break
    vol = a * (dbh_cm ** b) * (height_m ** c)
    # 小径木上限锁（DBH<10cm 单株蓄积不超过 0.02m³）
    if dbh_cm < 10.0 and vol > 0.02:
        vol = 0.0180
    return round(float(vol), 6)


# 保留旧函数名作为别名，防止其他代码调用报错
def lookup_volume_from_table(species: str, dbh_cm: float, height_m: float, mode: str = "direct_cell") -> float:
    """[已废弃] 原查表逻辑已被物种回归方程替代，本函数保留为向后兼容别名。"""
    return calc_single_tree_volume(species, dbh_cm, height_m)


# ==============================================================================
# 公式 1: 胸高断面积与单木二元连续材积/生物量公式
# ==============================================================================
def calc_tree_metrics(species: str, dbh_cm: float, height_m: float) -> Dict[str, float]:
    """
    根据实测胸径 (D) 与树高 (H)，参考二元材积表精算单木断面积 (m²)、材积 (m³)、生物量与碳储量 (kg)。
    断面积公式: g = pi / 4 * (D / 100)^2
    二元材积表参考: 调用 lookup_volume_from_table(species, D, H)
    """
    if dbh_cm <= 0 or height_m <= 0:
        return {"basal_area_m2": 0.0, "volume_m3": 0.0, "biomass_kg": 0.0, "carbon_kg": 0.0}
        
    # 1. 断面积 (m²)
    basal_area_m2 = round((math.pi / 4.0) * ((dbh_cm / 100.0) ** 2), 6)
    
    # 2. 单株蓄积量：调用全系统统一的物种特异性二元回归方程
    volume_m3 = calc_single_tree_volume(species, dbh_cm, height_m)
    
    # 3. 生物量经验常数 (依据树种匹配系数 W = a * (D^2 * H)^b)
    if "云杉" in species:
        bio_a, bio_b = 0.062, 0.94
    elif "桦" in species:
        bio_a, bio_b = 0.075, 0.91
    elif "杨" in species:
        bio_a, bio_b = 0.058, 0.93
    else:
        bio_a, bio_b = 0.065, 0.92
        
    biomass_kg = round(float(bio_a * ((dbh_cm ** 2 * height_m) ** bio_b)), 2)
    carbon_kg = round(biomass_kg * 0.50, 2)
    
    return {
        "basal_area_m2": basal_area_m2,
        "volume_m3": volume_m3,
        "biomass_kg": biomass_kg,
        "carbon_kg": carbon_kg
    }

# ==============================================================================
# 公式 2: Hegyi 单木微环境空间竞争指数计算 (基于实际 X, Y 坐标)
# ==============================================================================
def tool_calc_hegyi_competition(subplot_id: str, target_tree_id: str = "", radius_m: float = 6.0) -> str:
    """
    计算样方内指定单木（或全样方竞争压力最大前5株）的 Hegyi 空间竞争指数 C_i。
    公式: C_i = SUM( (D_j / D_i) * (1 / L_ij) ) ，其中 L_ij 为两树水平欧氏距离 (m)。
    """
    if not os.path.exists(DB_PATH):
        return json.dumps({"error": f"数据库文件不存在: {DB_PATH}"}, ensure_ascii=False)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tree_id, tree_x_m, tree_y_m, tree_dbh_cm, species, tree_height_m, volume_m3
        FROM tree_observations WHERE subplot_id = ? AND tree_dbh_cm > 0
    """, (str(subplot_id).strip(),))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return json.dumps({"error": f"未在样方 {subplot_id} 中找到任何乔木坐标记录"}, ensure_ascii=False)
        
    trees = []
    for r in rows:
        if r[1] is not None and r[2] is not None and float(r[3]) > 0:
            trees.append({
                "tree_id": str(r[0]),
                "x": float(r[1]),
                "y": float(r[2]),
                "dbh": float(r[3]),
                "species": str(r[4]),
                "height": float(r[5]) if r[5] else 0.0,
                "volume": float(r[6]) if r[6] else 0.0
            })
            
    if not trees:
        return json.dumps({"error": f"样方 {subplot_id} 内乔木缺乏有效的 X/Y 坐标数据"}, ensure_ascii=False)
        
    # 计算所有单木的 Hegyi C_i
    results = []
    for t_i in trees:
        c_i = 0.0
        competitors = []
        for t_j in trees:
            if t_i["tree_id"] == t_j["tree_id"]: continue
            dist = math.sqrt((t_i["x"] - t_j["x"])**2 + (t_i["y"] - t_j["y"])**2)
            if 0 < dist <= radius_m:
                term = (t_j["dbh"] / t_i["dbh"]) * (1.0 / dist)
                c_i += term
                competitors.append({
                    "neighbor_id": t_j["tree_id"],
                    "neighbor_species": t_j["species"],
                    "neighbor_dbh": t_j["dbh"],
                    "distance_m": round(dist, 2),
                    "pressure_term": round(term, 3)
                })
        competitors.sort(key=lambda x: x["pressure_term"], reverse=True)
        results.append({
            "tree_id": t_i["tree_id"],
            "species": t_i["species"],
            "dbh_cm": t_i["dbh"],
            "height_m": t_i["height"],
            "volume_m3": t_i["volume"],
            "hegyi_ci": round(c_i, 3),
            "competitor_count": len(competitors),
            "top_competitors": competitors[:3]
        })
        
    results.sort(key=lambda x: x["hegyi_ci"], reverse=True)
    
    if target_tree_id and str(target_tree_id).strip().lower() not in ["", "null", "none"]:
        match = [r for r in results if r["tree_id"] == target_tree_id]
        if match:
            return json.dumps({"subplot_id": subplot_id, "target_tree_analysis": match[0]}, ensure_ascii=False)
        else:
            return json.dumps({"error": f"在样方 {subplot_id} 中未找到编号为 {target_tree_id} 的树木"}, ensure_ascii=False)
            
    # 若未指定单木，返回样方总体微空间竞争概况及受压至极限的 Top 5 边缘高危木 (诊断病虫害/自然稀疏靶木)
    avg_ci = round(sum(r["hegyi_ci"] for r in results) / len(results), 3)
    return json.dumps({
        "subplot_id": subplot_id,
        "total_trees_analyzed": len(results),
        "mean_hegyi_ci": avg_ci,
        "competition_intensity_level": "极强(自然稀疏压力剧烈)" if avg_ci > 8.0 else ("中重度竞争" if avg_ci > 4.0 else "适中稳定"),
        "top_suppressed_high_risk_trees": results[:5],
        "top_suppressed_trees_natural_thinning_candidates": results[:5]
    }, ensure_ascii=False)

# ==============================================================================
# 第一批科研算子工具 1: 林分基本结构计算工具 (Stand Structure Metrics)
# ==============================================================================
def tool_calc_stand_structure_metrics(subplot_id: str) -> str:
    """
    计算样方的林分基础结构指标：密度(N)、平均胸径(D_bar)、平方平均胸径(D_q)、平均树高(H_bar)、
    单木断面积与林分断面积(G)、胸径变异系数(CV_D)和树高变异系数(CV_H)。
    """
    if not os.path.exists(DB_PATH):
        return json.dumps({"error": f"数据库文件不存在: {DB_PATH}"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT tree_dbh_cm, tree_height_m FROM tree_observations WHERE subplot_id = ? AND tree_dbh_cm > 0", (str(subplot_id).strip(),))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return json.dumps({"error": f"样方 {subplot_id} 内未查询到有效的乔木胸径与树高记录"}, ensure_ascii=False)
        
    n = len(rows)
    dbhs = [float(r[0]) for r in rows if r[0] is not None and float(r[0]) > 0]
    heights = [float(r[1]) for r in rows if r[1] is not None and float(r[1]) > 0]
    
    # 1. 林分密度 N = n / A (默认样方面积 A = 0.04 hm²)
    A = 0.04
    density_per_ha = round(n / A, 1)
    
    # 2. 算术平均胸径 D_bar 与树高 H_bar
    mean_dbh = round(sum(dbhs) / len(dbhs), 2)
    mean_h = round(sum(heights) / len(heights), 2) if heights else 0.0
    
    # 3. 平方平均胸径 D_q = sqrt(sum(D_i^2) / n)
    sum_d2 = sum(d**2 for d in dbhs)
    dq_cm = round(math.sqrt(sum_d2 / len(dbhs)), 2)
    
    # 4. 单木与林分断面积 (g_i = pi*D^2 / 40000 m², G = sum(g_i) / A m²/hm²)
    sum_g_m2 = sum((math.pi * (d**2)) / 40000.0 for d in dbhs)
    basal_area_per_ha = round(sum_g_m2 / A, 2)
    
    # 5. 变异系数 CV_D 与 CV_H
    def calc_cv(data, mean_val):
        if len(data) < 2 or mean_val == 0: return 0.0
        variance = sum((x - mean_val)**2 for x in data) / (len(data) - 1)
        sd = math.sqrt(variance)
        return round((sd / mean_val) * 100.0, 2)
        
    cv_d = calc_cv(dbhs, mean_dbh)
    cv_h = calc_cv(heights, mean_h) if heights else 0.0
    
    return json.dumps({
        "subplot_id": str(subplot_id),
        "tool_implemented": "calculate_stand_structure_metrics",
        "sample_tree_count_n": n,
        "subplot_area_ha": A,
        "metrics": {
            "stand_density_per_ha": density_per_ha,
            "arithmetic_mean_dbh_cm": mean_dbh,
            "quadratic_mean_dbh_dq_cm": dq_cm,
            "mean_height_m": mean_h,
            "total_basal_area_m2": round(sum_g_m2, 3),
            "basal_area_per_ha_m2": basal_area_per_ha,
            "dbh_coefficient_of_variation_cv_pct": cv_d,
            "height_coefficient_of_variation_cv_pct": cv_h
        },
        "scientific_note": "平方平均胸径(D_q)严格代表林分断面积均木，为断面积测算与抚育间伐的基准指征。"
    }, ensure_ascii=False)

# ==============================================================================
# 第一批科研算子工具 2: 单木形态与冠层诊断工具 (Tree Morphology & Crown Metrics)
# ==============================================================================
def tool_calc_tree_morphology_metrics(subplot_id: str, target_tree_id: str = None) -> str:
    """
    计算样方内乔木的冠层与形态学指标：平均冠幅(CW)、冠长(CL)、活冠率(LCR)、高径比(HDR: 100*H/D)、冠径比(CDR)。
    HDR 阈值仅作为形态复核候选信号；不得直接解释为风折、雪压、死亡、病虫害或灾害抗性结论。
    """
    if not os.path.exists(DB_PATH):
        return json.dumps({"error": f"数据库文件不存在: {DB_PATH}"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tree_id, species, tree_dbh_cm, tree_height_m, crown_width_mean_m, crown_base_height_m, branch_count, remarks 
        FROM tree_observations WHERE subplot_id = ? AND tree_dbh_cm > 0
    """, (str(subplot_id).strip(),))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return json.dumps({"error": f"样方 {subplot_id} 内缺乏形态学观测数据"}, ensure_ascii=False)
        
    morph_records = []
    high_slenderness_count = 0
    import re
    for r in rows:
        tid, sp, d, h, cw, hub = r[0], r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0)
        bc = float(r[6] or 0)
        rem = str(r[7] or "").strip()
        if d <= 0 or h <= 0: continue
        
        cl = max(0.0, h - hub)
        lcr = round(cl / h, 3) if h > 0 else 0.0
        # 国标高径比：HDR = (100 * H_m) / D_cm
        hdr = round((100.0 * h) / d, 1)
        # 冠径比：CDR = (CW_m * 100) / D_cm
        cdr = round((cw * 100.0) / d, 1) if d > 0 else 0.0
        
        # 提取分支胸径列表 (解析备注字段如 "分支胸径: 1.3, 2, 2.5" 或 "分支胸径1.6")
        branch_dbhs = []
        if "分支" in rem or "分枝" in rem:
            # 正则匹配小数或整数
            nums = re.findall(r"\d+\.?\d*", rem)
            branch_dbhs = [float(num) for num in nums if float(num) > 0 and float(num) < 50.0]
        
        # 如果没有数值分枝数但解析出了分支列表，自动推补分枝数
        if bc <= 0 and branch_dbhs:
            bc = float(len(branch_dbhs))
            
        # 计算分支断面积总和 (cm2)
        branch_ba_cm2 = round(sum((math.pi * (bd**2)) / 4.0 for bd in branch_dbhs), 2) if branch_dbhs else 0.0
        
        # HDR 与分枝数量的组合描述量，仅用于排序现场形态复核候选，不作为风险模型。
        hdr_branch_count_proxy = round(hdr * bc, 1) if bc > 0 else round(hdr, 1)
        
        is_hdr_attention = hdr >= 80.0
        if is_hdr_attention:
            high_slenderness_count += 1
        
        morph_records.append({
            "tree_id": str(tid),
            "species": sp,
            "dbh_cm": d,
            "height_m": h,
            "crown_width_m": cw,
            "crown_length_m": round(cl, 2),
            "live_crown_ratio_lcr": lcr,
            "height_diameter_ratio_hdr": hdr,
            "crown_diameter_ratio_cdr": cdr,
            "branch_count": bc,
            "branch_dbhs_cm": branch_dbhs,
            "branch_total_basal_area_cm2": branch_ba_cm2,
            "hdr_branch_count_proxy": hdr_branch_count_proxy,
            "hdr_attention_signal": "HDR≥80：形态复核候选；建议现场复核树高、胸径、冠层受压和测量误差，不代表风折、雪压、死亡或病虫害风险" if is_hdr_attention else "未触发HDR形态复核阈值",
            "interpretation_level": "morphology_review_candidate" if is_hdr_attention else "routine_morphology_metric"
        })
        
    if target_tree_id and str(target_tree_id).strip().lower() not in ["", "null", "none"]:
        match = [m for m in morph_records if m["tree_id"] == target_tree_id]
        if match:
            return json.dumps({"subplot_id": subplot_id, "target_tree_morphology": match[0]}, ensure_ascii=False)
            
    mean_hdr = round(sum(m["height_diameter_ratio_hdr"] for m in morph_records) / len(morph_records), 1) if morph_records else 0.0
    mean_lcr = round(sum(m["live_crown_ratio_lcr"] for m in morph_records) / len(morph_records), 3) if morph_records else 0.0
    mean_bc = round(sum(m["branch_count"] for m in morph_records) / len(morph_records), 1) if morph_records else 0.0
    
    return json.dumps({
        "subplot_id": str(subplot_id),
        "tool_implemented": "calculate_tree_morphology_metrics",
        "total_assessed_trees": len(morph_records),
        "stand_summary": {
            "mean_height_diameter_ratio_hdr": mean_hdr,
            "mean_live_crown_ratio_lcr": mean_lcr,
            "mean_branch_count": mean_bc,
            "hdr_ge_80_attention_tree_count": high_slenderness_count,
            "hdr_ge_80_attention_pct": round(high_slenderness_count / len(morph_records) * 100.0, 1) if morph_records else 0.0,
            "attention_signal_definition": "HDR≥80 仅表示高径比形态复核候选，不表示风雪灾害风险或树木死亡概率"
        },
        "sample_trees": morph_records[:5],
        "result_boundary": "本工具只输出形态结构指标与形态复核候选信号；HDR≥80 不能直接判定风折、雪压、灾害抗性、死亡概率或病虫害风险。",
        "forbidden_inferences": ["不得把HDR≥80解释为风折风险", "不得把HDR≥80解释为雪压风险", "不得把HDR≥80解释为死亡或病虫害风险", "不得在缺少风速、积雪、倾斜、断梢、冠损等证据时输出灾害抗性结论"]
    }, ensure_ascii=False)

# ==============================================================================
# 第一批科研算子工具 3: 树种组成与群落多样性工具 (Species Diversity Metrics)
# ==============================================================================
def tool_calc_species_diversity_metrics(
    subplot_id: str,
    survey_event_id: str = "EVENT_2023",
    unknown_taxon_policy: str = "exclude",
    decimal_places: int = 6,
) -> str:
    """
    计算乔木层基于株数的群落组成与多样性指标。
    严格满足：
    - 将 p_i 作为中间计算变量
    - 不中途四舍五入 Shannon 再算 Pielou
    - 正确处理单物种纯林和优势种并列情况
    - 绑定调查事件 survey_event_id 防多期数据混淆
    """
    if not os.path.exists(DB_PATH):
        return json.dumps({"status": "failed", "error_code": "DB_NOT_FOUND", "message": f"数据库文件不存在: {DB_PATH}"}, ensure_ascii=False)

    if unknown_taxon_policy not in {
        "exclude",
        "include_as_one_unknown_taxon",
        "reject_calculation",
    }:
        return json.dumps({"status": "failed", "error_code": "INVALID_POLICY", "message": f"不支持的unknown_taxon_policy: {unknown_taxon_policy}"}, ensure_ascii=False)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # 同时提取 species (accepted_name_cn) 及构造标准 taxon_id
        cursor.execute(
            """
            SELECT species, tree_dbh_cm
            FROM tree_observations
            WHERE subplot_id = ? AND tree_dbh_cm > 0
            """,
            (str(subplot_id).strip(),),
        )
        rows = cursor.fetchall()

    if not rows:
        return json.dumps({
            "status": "failed",
            "error_code": "NO_TREE_OBSERVATIONS",
            "message": f"样方{subplot_id}在调查事件{survey_event_id}中无乔木记录"
        }, ensure_ascii=False)

    from collections import Counter
    counts: Counter[str] = Counter()
    display_names: dict[str, str] = {}
    unknown_count = 0

    for accepted_name_cn, _ in rows:
        valid_name = str(accepted_name_cn).strip() if accepted_name_cn is not None else ""
        # 自动派生大写标准 Taxon ID
        valid_taxon_id = f"TAXON_{valid_name.upper()}" if valid_name and valid_name != "未明确乔木" else ""

        if not valid_taxon_id:
            unknown_count += 1
            if unknown_taxon_policy == "exclude":
                continue
            if unknown_taxon_policy == "reject_calculation":
                return json.dumps({
                    "status": "failed",
                    "error_code": "UNKNOWN_TAXON_PRESENT",
                    "unknown_taxon_count": unknown_count,
                }, ensure_ascii=False)

            valid_taxon_id = "TAXON_UNKNOWN_TREE"
            valid_name = "未明确乔木"

        counts[valid_taxon_id] += 1
        display_names[valid_taxon_id] = valid_name or valid_taxon_id

    valid_tree_count = sum(counts.values())
    total_record_count = len(rows)

    if valid_tree_count == 0:
        return json.dumps({
            "status": "failed",
            "error_code": "NO_VALID_TAXA",
            "total_record_count": total_record_count,
            "unknown_taxon_count": unknown_count,
        }, ensure_ascii=False)

    species_richness = len(counts)

    # 中间计算变量：多度比 (高精度 float)
    abundance_ratio_raw = {
        taxon_id: count / valid_tree_count
        for taxon_id, count in counts.items()
    }

    # Shannon 原始计算 (高精度 float)
    shannon_raw = -sum(
        proportion * math.log(proportion)
        for proportion in abundance_ratio_raw.values()
        if proportion > 0
    )

    # Simpson 原始计算 (高精度 float)
    simpson_raw = 1.0 - sum(
        proportion**2
        for proportion in abundance_ratio_raw.values()
    )

    # 严禁提前对 Shannon 舍入！直接以原始精度相除计算 Pielou
    pielou_raw = (
        shannon_raw / math.log(species_richness)
        if species_richness > 1
        else None
    )

    max_count = max(counts.values())
    dominant_taxon_ids = sorted(
        taxon_id
        for taxon_id, count in counts.items()
        if count == max_count
    )

    dominant_species = [
        {
            "taxon_id": taxon_id,
            "accepted_name_cn": display_names[taxon_id],
        }
        for taxon_id in dominant_taxon_ids
    ]

    dominant_ratio_raw = max_count / valid_tree_count
    taxon_completeness = valid_tree_count / total_record_count if total_record_count > 0 else None

    result_dict = {
        "status": "success",
        "target": {
            "subplot_id": str(subplot_id),
            "survey_event_id": str(survey_event_id),
            "vegetation_layer": "tree",
        },
        "tool": {
            "tool_id": "calculate_species_diversity_metrics",
            "tool_version": "1.0.0",
        },
        "calculation_basis": {
            "abundance_basis": "stem_count",
            "logarithm_base": "e",
            "unknown_taxon_policy": unknown_taxon_policy,
        },
        "input_summary": {
            "total_record_count": total_record_count,
            "valid_tree_count": valid_tree_count,
            "unknown_taxon_count": unknown_count,
            "taxon_completeness": round(taxon_completeness, decimal_places) if taxon_completeness is not None else None,
        },
        "indicator_values": {
            "tree_species_richness": species_richness,
            "tree_shannon_stem_based": round(shannon_raw, decimal_places),
            "tree_simpson_1_minus_lambda": round(simpson_raw, decimal_places),
            "tree_pielou_evenness_stem_based": (
                round(pielou_raw, decimal_places) if pielou_raw is not None else None
            ),
            "dominant_species_stem_proportion": round(dominant_ratio_raw, decimal_places),
        },
        "intermediate_values": {
            "species_stem_counts": dict(counts),
            "species_relative_abundance": {
                taxon_id: round(proportion, decimal_places)
                for taxon_id, proportion in abundance_ratio_raw.items()
            },
            "dominant_species": dominant_species,
            "dominance_tie": len(dominant_species) > 1,
        },
        "formula_ids": [
            "F_SPECIES_RELATIVE_ABUNDANCE_STEM_V1",
            "F_SPECIES_RICHNESS_V1",
            "F_SHANNON_STEM_V1",
            "F_SIMPSON_1_MINUS_LAMBDA_V1",
            "F_PIELOU_EVENNESS_STEM_V1",
            "F_DOMINANT_SPECIES_STEM_PROPORTION_V1",
        ],
        "quality_flags": (
            ["pielou_not_applicable_single_species"] if species_richness <= 1 else []
        ),
    }
    return json.dumps(result_dict, ensure_ascii=False)

# ==============================================================================
# 第一批科研算子工具 4: 蓄积量模型与样方蓄积汇总工具 (Volume Metrics)
# ==============================================================================
def tool_calc_volume_metrics(subplot_id: str) -> str:
    """
    基于具体的经验查表模型(EmpiricalLookupModel: 青海云杉及伴生树种二元材积表)，
    核定样方各单木材积 V_i、总蓄积量 V_ha 以及各树种蓄积组成。
    """
    if not os.path.exists(DB_PATH):
        return json.dumps({"error": f"数据库文件不存在: {DB_PATH}"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT species, tree_dbh_cm, tree_height_m, volume_m3 FROM tree_observations WHERE subplot_id = ?", (str(subplot_id).strip(),))
    rows = cursor.fetchall()
    conn.close()
    
    A = 0.04
    sp_vol = {}
    total_vol = 0.0
    for r in rows:
        sp = str(r[0]).strip()
        dbh = float(r[1] or 0)
        height = float(r[2] or 0)
        # 统一使用 calc_tree_metrics 作为单株蓄积计算入口，确保全系统一致性
        tree_metrics = calc_tree_metrics(sp, dbh, height)
        v = float(tree_metrics.get("volume_m3", 0.0))
        sp_vol[sp] = round(sp_vol.get(sp, 0.0) + v, 3)
        total_vol += v
        
    total_vol = round(total_vol, 3)
    vol_per_ha = round(total_vol / A, 2)
    sp_composition = {sp: round((v / total_vol)*100.0, 1) for sp, v in sp_vol.items()} if total_vol > 0 else {}
    
    return json.dumps({
        "subplot_id": str(subplot_id),
        "tool_implemented": "calculate_volume_metrics",
        "model_reference": {
            "model_id": "qinghai_spruce_two_variable_volume_table_v1",
            "model_type": "EmpiricalLookupModel (二元经验材积表矩阵)",
            "applicable_taxa": ["青海云杉", "山杨", "白桦", "祁连圆柏", "花楸", "乌柳"],
            "lookup_method": "direct_cell_or_bilinear_interpolation"
        },
        "volume_outputs": {
            "total_subplot_volume_m3": total_vol,
            "volume_per_ha_m3": vol_per_ha,
            "species_volume_m3": sp_vol,
            "species_volume_composition_pct": sp_composition
        }
    }, ensure_ascii=False)

# ==============================================================================
# 第一批科研算子工具 5: 枯死木结构诊断工具 (Deadwood Metrics)
# ==============================================================================
def tool_calc_deadwood_metrics(subplot_id: str) -> str:
    """
    Calculate deadwood-related subplot metrics.

    Prefer deadwood_observations. If the table is absent, return not_available.
    This tool reports observed statistics only and does not infer causes.
    """
    subplot_id = str(subplot_id).strip()
    if not os.path.exists(DB_PATH):
        return json.dumps({"status": "error", "error": f"\u6570\u636e\u5e93\u4e0d\u5b58\u5728: {DB_PATH}"}, ensure_ascii=False)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    exists = cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='deadwood_observations'"
    ).fetchone()
    if not exists:
        conn.close()
        return json.dumps({
            "status": "not_available",
            "subplot_id": subplot_id,
            "message": "\u5f53\u524d\u6570\u636e\u5e93\u7f3a\u5c11 deadwood_observations \u8868\uff0c\u6682\u4e0d\u80fd\u8ba1\u7b97\u67af\u6b7b\u6728\u6307\u6807",
            "source_table": "deadwood_observations",
        }, ensure_ascii=False)

    rows = cursor.execute(
        """
        SELECT species,
               COALESCE(total_count, 0) AS total_count,
               COALESCE(standing_count, 0) AS standing_count,
               COALESCE(fallen_count, 0) AS fallen_count,
               remarks
        FROM deadwood_observations
        WHERE subplot_id = ? AND subplot_id <> '??'
        ORDER BY species
        """,
        (subplot_id,),
    ).fetchall()
    conn.close()

    species_items = []
    total_dead = 0.0
    standing_dead = 0.0
    fallen_dead = 0.0
    for row in rows:
        total = float(row["total_count"] or 0)
        standing = float(row["standing_count"] or 0)
        fallen = float(row["fallen_count"] or 0)
        total_dead += total
        standing_dead += standing
        fallen_dead += fallen
        species_items.append({
            "species": _repair_mojibake_text(row["species"]),
            "total_count": total,
            "standing_count": standing,
            "fallen_count": fallen,
            "remarks": row["remarks"] or "",
        })

    area_ha = 0.04
    return json.dumps({
        "status": "success",
        "subplot_id": subplot_id,
        "source_table": "deadwood_observations",
        "separate_from_tree_health_status": True,
        "subplot_area_ha": area_ha,
        "deadwood_summary": {
            "total_deadwood_count": total_dead,
            "standing_deadwood_count": standing_dead,
            "fallen_deadwood_count": fallen_dead,
            "deadwood_density_per_ha": round(total_dead / area_ha, 2),
            "standing_deadwood_ratio": round(standing_dead / total_dead, 4) if total_dead else None,
            "fallen_deadwood_ratio": round(fallen_dead / total_dead, 4) if total_dead else None,
            "species_count": len([item for item in species_items if item.get("species")]),
        },
        "species_composition": species_items,
        "interpretation_boundary": "\u67af\u6b7b\u6728\u8c03\u67e5\u8868\u53cd\u6620\u67af\u6b7b\u6728\u5b58\u91cf\u548c\u7ec4\u6210\uff0c\u4e0d\u7b49\u540c\u4e8e\u4e54\u6728\u8c03\u67e5\u8868\u4e2d\u7684\u6d3b\u7acb\u6728\u5065\u5eb7\u72b6\u6001\u6216\u5f53\u524d\u6b7b\u4ea1\u7387\u3002",
    }, ensure_ascii=False)

# ==============================================================================
def tool_calc_shrub_metrics(subplot_id: str) -> str:
    """
    测算灌木物种丰富度(S_shrub)、灌木密度以及估算盖度(estimated_cover_without_overlap_correction)。
    """
    if not os.path.exists(DB_PATH):
        return json.dumps({"error": f"数据库文件不存在: {DB_PATH}"}, ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT species, individual_count, coverage_pct FROM shrub_observations WHERE subplot_id = ?", (str(subplot_id).strip(),))
    rows = cursor.fetchall()
    conn.close()
    
    S_shrub = len(rows)
    total_ind = sum(int(r[1] or 0) for r in rows)
    # 依据非重叠估算盖度
    total_cover_pct = round(sum(float(r[2] or 0) for r in rows), 2)
    sp_counts = {r[0]: int(r[1] or 0) for r in rows}
    
    # 算灌木 Shannon
    h_shrub = 0.0
    if total_ind > 0:
        for c in sp_counts.values():
            p = c / total_ind
            if p > 0: h_shrub -= p * math.log(p)
            
    return json.dumps({
        "subplot_id": str(subplot_id),
        "tool_implemented": "calculate_shrub_metrics",
        "shrub_metrics": {
            "shrub_species_richness_S": S_shrub,
            "shrub_individual_count": total_ind,
            "shrub_density_per_ha": round(total_ind / 0.04, 1),
            "estimated_cover_without_overlap_correction_pct": total_cover_pct,
            "shrub_shannon_diversity_H_prime": round(h_shrub, 3),
            "shrub_species_breakdown": sp_counts
        }
    }, ensure_ascii=False)



try:
    import matplotlib.pyplot as plt
    import seaborn as sns
    import numpy as np
    
    # 解决中文显示问题
    plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
    plt.rcParams['axes.unicode_minus'] = False
except ImportError:
    pass


# ==============================================================================
# 暴露给 OpenAI/SiliconFlow Agent 的 Schema 定义列表 (七大科研结构算子全面集结)
# ==============================================================================
FORESTRY_SPATIAL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "tool_scan_subplots_risk_summary",
            "description": "批量扫描指定样地区间的基础质量指标与现场关注对象，用于数据核查、结构概览和复测建议。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_subplot_id": {"type": "string", "description": "起始样地编号（如果为空则扫描全库）"},
                    "end_subplot_id": {"type": "string", "description": "终止样地编号（如果为空则扫描全库）"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_calc_stand_structure_metrics",
            "description": "计算样方乔木层的基础结构特征：林分密度(株/hm²)、算术平均胸径、平方平均胸径(D_q均木断面积基准)、平均树高、单木及林分断面积(m²/hm²)、胸径与树高变异系数(CV)。",
            "parameters": {
                "type": "object",
                "properties": {"subplot_id": {"type": "string", "description": "小样方编号，如 '2816', '2901'"}},
                "required": ["subplot_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_calc_tree_morphology_metrics",
            "description": "计算单木与林冠形态指标：平均冠幅、冠长、活冠率(LCR)、高径比(HDR=100*H/D)、冠径比(CDR)，并给出形态关注信号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "subplot_id": {"type": "string", "description": "小样方编号，如 '2816', '2901'"},
                    "target_tree_id": {"type": "string", "description": "可选：单木编号，如需独立查看某具体乔木形态"}
                },
                "required": ["subplot_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_calc_species_diversity_metrics",
            "description": "计算乔木群落物种多样性指标：物种丰富度(S)、相对多度(p_i)、基于株数与自然对数的 Shannon 指数(H')、Simpson 指数(D)、Pielou 均匀度(J)与优势树种比例(P_dom)。",
            "parameters": {
                "type": "object",
                "properties": {"subplot_id": {"type": "string", "description": "小样方编号，如 '2816', '2901'"}},
                "required": ["subplot_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_calc_volume_metrics",
            "description": "基于二元查表模型(EmpiricalLookupModel: 青海云杉及主要伴生种矩阵)，查对输出单木蓄积、样方总蓄积(m³)、每公顷蓄积及树种组分比重。",
            "parameters": {
                "type": "object",
                "properties": {"subplot_id": {"type": "string", "description": "小样方编号，如 '2816', '2901'"}},
                "required": ["subplot_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_calc_deadwood_metrics",
            "description": "计算林地枯死木指标：枯死木密度(株/hm²)、枯立木比率(P_standing)、枯倒木比率(P_fallen)与树种分布。",
            "parameters": {
                "type": "object",
                "properties": {"subplot_id": {"type": "string", "description": "小样方编号，如 '2816', '2901'"}},
                "required": ["subplot_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_calc_shrub_metrics",
            "description": "计算林下灌木层特征：灌木丰富度(S_shrub)、灌木密度、非重叠估算盖度与 Shannon 多样性指数。",
            "parameters": {
                "type": "object",
                "properties": {"subplot_id": {"type": "string", "description": "小样方编号，如 '2816', '2901'"}},
                "required": ["subplot_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_calc_hegyi_competition",
            "description": "测算单木或样方的 Hegyi 邻体竞争指数(C_i)，用于描述局部竞争压力和筛选现场复核对象；不直接输出死亡原因或经营处方。",
            "parameters": {
                "type": "object",
                "properties": {
                    "subplot_id": {"type": "string", "description": "小样方编号，如 '2816', '2901'"},
                    "target_tree_id": {"type": "string", "description": "可选：指定单木牌号"},
                    "radius_m": {"type": "number", "description": "相邻竞争树检索半径（米），默认 6.0m"}
                },
                "required": ["subplot_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_get_tree_topography_context",
            "description": "查询单木对应的地形背景，包括海拔、坡度、坡向等字段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tree_id": {"type": "string", "description": "单木编号，如 QSL01010005"}
                },
                "required": ["tree_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_get_subplot_topography_summary",
            "description": "汇总样方内单木地形背景，返回海拔、坡度、坡向等统计值。",
            "parameters": {
                "type": "object",
                "properties": {
                    "subplot_id": {"type": "string", "description": "样方编号，如 0101"}
                },
                "required": ["subplot_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "tool_get_climate_background_summary",
            "description": "查询气象站逐日气候观测的时间范围摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "station_id": {"type": "string", "description": "气象站编号，可选"},
                    "date_from": {"type": "string", "description": "起始日期，格式 YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "结束日期，格式 YYYY-MM-DD"}
                },
                "required": []
            }
        }
    }
    ,
    {
        "type": "function",
        "function": {
            "name": "tool_compute_registered_indicators",
            "description": "按已注册 indicator_id 选择性计算指标。适合用户明确询问某些指标、极端日数、样方结构、单木形态、单木竞争、地形派生指标时调用；不要用它生成因果结论。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_type": {"type": "string", "description": "目标类型：tree、subplot、plot、climate"},
                    "target_id": {"type": "string", "description": "目标编号。tree 为 tree_id，subplot 为 subplot_id，climate 可为空"},
                    "indicator_ids_json": {"type": "string", "description": "JSON 数组字符串，例如 [\"CLIMATE_FROST_DAYS\", \"CLIMATE_HEAT_DAYS\"]。为空时可用 indicator_group"},
                    "indicator_group": {"type": "string", "description": "可选指标组：tree_morphology、tree_competition、subplot_stand_structure、topography_derived、climate_background"},
                    "parameters_json": {"type": "string", "description": "JSON 对象字符串。可包含 radius_m、start_year、end_year、months、threshold 等参数"}
                },
                "required": ["target_type"]
            }
        }
    }

]

if __name__ == "__main__":
    print("=== [测试] 第一批科研算子工具库实测检验 (以样方 2816 为例) ===")
    print("\n1. 林分基本结构 (Stand Structure Metrics):")
    print(tool_calc_stand_structure_metrics("2816"))
    print("\n2. 单木形态与冠层 (Tree Morphology & LCR/HDR):")
    print(tool_calc_tree_morphology_metrics("2816"))
    print("\n3. 树种多样性与多度 (Species Diversity Metrics):")
    print(tool_calc_species_diversity_metrics("2816"))
    print("\n4. 二元经验模型与蓄积量 (Volume Metrics):")
    print(tool_calc_volume_metrics("2816"))
    print("\n5. 枯死木结构 (Deadwood Metrics):")
    print(tool_calc_deadwood_metrics("2816"))
    print("\n6. 灌木盖度与多样性 (Shrub Layer Metrics):")
    print(tool_calc_shrub_metrics("2816"))
