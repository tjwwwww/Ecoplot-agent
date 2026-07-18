# -*- coding: utf-8 -*-
"""
survey_module.py
================
野外调查模块 — AI 驱动的调查方案生成、现场记录与报告导出。

核心功能：
1. 根据自然语言需求，结合数据库已有数据，自动生成调查方案
2. 方案包含具体树木、样地、调查原因和建议行动
3. 现场逐条记录观察结果
4. 调查完成后生成对比报告
"""

import json
import math
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "qilian_forest.db"
REPORT_DIR = BASE_DIR / "reports"

# =============================================================================
# 数据库初始化
# =============================================================================

SURVEY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS field_survey_plans (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    user_request TEXT NOT NULL,
    ai_analysis TEXT,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft','active','completed','cancelled')),
    summary TEXT,
    tree_count INTEGER DEFAULT 0,
    subplot_count INTEGER DEFAULT 0,
    completed_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS survey_recommendations (
    rec_id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    tree_id TEXT,
    subplot_id TEXT,
    species TEXT,
    priority TEXT DEFAULT 'medium' CHECK(priority IN ('high','medium','low')),
    category TEXT,
    reason TEXT NOT NULL,
    suggested_actions TEXT,
    evidence_data TEXT,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending','completed','skipped')),
    completed_at TEXT,
    obs_id INTEGER,
    sort_order INTEGER DEFAULT 0,
    FOREIGN KEY (plan_id) REFERENCES field_survey_plans(plan_id)
);

CREATE TABLE IF NOT EXISTS field_observations (
    obs_id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    rec_id INTEGER,
    tree_id TEXT,
    subplot_id TEXT,
    species TEXT,
    notes TEXT,
    health_status TEXT CHECK(health_status IN ('good','fair','poor','dead',NULL)),
    pest_signs TEXT CHECK(pest_signs IN ('yes','no','suspected',NULL)),
    phenophase TEXT CHECK(phenophase IN ('budding','flowering','fruiting','leaf_changing','dormant',NULL)),
    photo_paths TEXT,
    latitude REAL,
    longitude REAL,
    recorded_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (plan_id) REFERENCES field_survey_plans(plan_id),
    FOREIGN KEY (rec_id) REFERENCES survey_recommendations(rec_id)
);

CREATE TABLE IF NOT EXISTS survey_site_briefs (
    brief_id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_key TEXT NOT NULL UNIQUE,
    analysis_text TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


def init_survey_db() -> None:
    """初始化调查相关的数据库表"""
    if not DB_PATH.exists():
        print(f"[survey] 数据库不存在: {DB_PATH}")
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SURVEY_TABLES_SQL)
        conn.commit()


def _dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    """将查询结果转换为字典"""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = _dict_factory
    return conn


# =============================================================================
# 数据采集 — 为 AI 提供上下文
# =============================================================================

def _detect_species_column(conn) -> Optional[str]:
    rows = conn.execute("PRAGMA table_info(tree_observations)").fetchall()
    columns = [row["name"] for row in rows]
    candidates = ["species", "species_name", "species_cn", "accepted_name_cn", "taxon_name", "tree_species"]
    for column in candidates:
        if column in columns:
            count = conn.execute(
                f"SELECT COUNT(*) AS n FROM tree_observations WHERE {column} IS NOT NULL AND TRIM({column}) <> ''"
            ).fetchone()["n"]
            if count:
                return column
    return None


def _gather_species_overview() -> List[Dict[str, Any]]:
    """Get species overview with schema-tolerant species column detection."""
    with _get_conn() as conn:
        species_col = _detect_species_column(conn)
        if not species_col:
            return []
        rows = conn.execute(f"""
            SELECT
                {species_col} AS species,
                COUNT(*) AS count,
                ROUND(AVG(tree_dbh_cm), 1) AS avg_dbh,
                ROUND(AVG(tree_height_m), 1) AS avg_height,
                ROUND(AVG(CASE WHEN health_status = 'good' THEN 1.0 ELSE 0 END) * 100, 1) AS health_good_pct,
                ROUND(AVG(CASE WHEN health_status = 'poor' THEN 1.0 ELSE 0 END) * 100, 1) AS health_poor_pct
            FROM tree_observations
            WHERE {species_col} IS NOT NULL AND TRIM({species_col}) <> ''
            GROUP BY {species_col}
            ORDER BY count DESC
        """).fetchall()
    return rows


def _gather_health_anomaly_trees(limit: int = 30) -> List[Dict[str, Any]]:
    """获取健康异常的树木（健康状态为差/枯死，或高径比异常）"""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT
                tree_id, subplot_id, species, tree_dbh_cm, tree_height_m,
                health_status, remarks,
                ROUND(100.0 * tree_height_m / tree_dbh_cm, 2) AS hdr
            FROM tree_observations
            WHERE (health_status IN ('poor', 'dead')
                   OR (tree_dbh_cm > 0 AND tree_height_m > 0
                       AND 100.0 * tree_height_m / tree_dbh_cm > 100))
            AND species IS NOT NULL AND TRIM(species) <> ''
            ORDER BY
                CASE health_status WHEN 'dead' THEN 0 WHEN 'poor' THEN 1 ELSE 2 END,
                hdr DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return rows


def _gather_climate_context() -> Dict[str, Any]:
    """获取气候背景"""
    context = {}
    with _get_conn() as conn:
        # 最近气候数据
        recent = conn.execute("""
            SELECT
                COUNT(*) AS days,
                ROUND(AVG(mean_temperature_c), 1) AS avg_temp,
                ROUND(SUM(precipitation_mm), 1) AS total_precip,
                ROUND(AVG(precipitation_mm), 1) AS avg_daily_precip
            FROM climate_daily_normalized
            WHERE observation_date >= date('now', '-90 days')
        """).fetchone()
        if recent and recent["days"] and recent["days"] > 0:
            context["recent_90d"] = recent

        # 去年同期对比
        last_year = conn.execute("""
            SELECT
                ROUND(SUM(precipitation_mm), 1) AS total_precip,
                ROUND(AVG(mean_temperature_c), 1) AS avg_temp
            FROM climate_daily_normalized
            WHERE observation_date BETWEEN date('now', '-1 year', '-90 days')
                                      AND date('now', '-1 year')
        """).fetchone()
        if last_year and last_year.get("total_precip"):
            context["same_period_last_year"] = last_year

        # 年降水量
        yearly = conn.execute("""
            SELECT strftime('%Y', observation_date) AS year,
                   ROUND(SUM(precipitation_mm), 1) AS annual_precip,
                   ROUND(AVG(mean_temperature_c), 1) AS annual_temp
            FROM climate_daily_normalized
            GROUP BY year
            ORDER BY year DESC
            LIMIT 3
        """).fetchall()
        if yearly:
            context["recent_years"] = yearly

    return context


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _gather_topography_context() -> List[Dict[str, Any]]:
    """Get topography overview from available table/view."""
    with _get_conn() as conn:
        source = "topography_observations" if _table_exists(conn, "topography_observations") else None
        if source is None and _table_exists(conn, "vw_tree_topography_context"):
            source = "vw_tree_topography_context"
        if source is None:
            return []
        rows = conn.execute(f"""
            SELECT
                subplot_id,
                ROUND(AVG(elevation_m), 1) AS mean_elevation,
                ROUND(AVG(slope_degree), 1) AS mean_slope,
                ROUND(AVG(aspect_degree), 1) AS mean_aspect
            FROM {source}
            WHERE subplot_id IS NOT NULL
            GROUP BY subplot_id
            LIMIT 600
        """).fetchall()
    return rows


def _gather_subplot_summary(subplot_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """获取样地汇总"""
    where = ""
    params: List[Any] = []
    if subplot_ids:
        placeholders = ",".join("?" for _ in subplot_ids)
        where = f"WHERE subplot_id IN ({placeholders})"
        params = subplot_ids

    with _get_conn() as conn:
        rows = conn.execute(f"""
            SELECT
                subplot_id,
                COUNT(*) AS tree_count,
                COUNT(DISTINCT species) AS species_count,
                ROUND(AVG(tree_dbh_cm), 1) AS avg_dbh,
                ROUND(AVG(tree_height_m), 1) AS avg_height,
                ROUND(AVG(100.0 * tree_height_m / NULLIF(tree_dbh_cm, 0)), 2) AS avg_hdr,
                SUM(CASE WHEN health_status = 'dead' THEN 1 ELSE 0 END) AS dead_count,
                SUM(CASE WHEN health_status = 'poor' THEN 1 ELSE 0 END) AS poor_count
            FROM tree_observations
            {where}
            GROUP BY subplot_id
            ORDER BY subplot_id
        """, params).fetchall()
    return rows



def _row_to_dict(row: Any) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


_SITE_BRIEF_CACHE: Optional[Dict[str, Any]] = None


def _read_site_brief_cache(brief_key: str = "default") -> Optional[Dict[str, Any]]:
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT analysis_text, payload_json FROM survey_site_briefs WHERE brief_key=?",
                (brief_key,),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["analysis_text"] = row.get("analysis_text") or payload.get("analysis_text") or ""
    return payload if payload.get("analysis_text") else None


def _write_site_brief_cache(brief: Dict[str, Any], brief_key: str = "default") -> None:
    analysis_text = str(brief.get("analysis_text") or "").strip()
    if not analysis_text:
        return
    payload_json = json.dumps(brief, ensure_ascii=False, default=str)
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO survey_site_briefs (brief_key, analysis_text, payload_json)
            VALUES (?, ?, ?)
            ON CONFLICT(brief_key) DO UPDATE SET
                analysis_text=excluded.analysis_text,
                payload_json=excluded.payload_json,
                updated_at=datetime('now','localtime')
            """,
            (brief_key, analysis_text, payload_json),
        )
        conn.commit()





def _fallback_site_analysis(site_summary: Dict[str, Any], top_species: List[Dict[str, Any]], terrain_summary: Dict[str, Any]) -> str:
    species_text = "\u3001".join(
        f"{item.get('species')}\uff08{item.get('count')}\u682a\uff09"
        for item in top_species[:4]
        if item.get("species")
    ) or "\u6811\u79cd\u7edf\u8ba1\u6682\u4e0d\u5b8c\u6574"
    if terrain_summary.get("min_elevation_m") is not None:
        terrain_text = (
            f"\u5730\u5f62\u8bb0\u5f55\u663e\u793a\uff0c\u6837\u5730\u8986\u76d6\u6d77\u62d4\u7ea6{terrain_summary.get('min_elevation_m')}-"
            f"{terrain_summary.get('max_elevation_m')}m\uff0c\u5e73\u5747\u5761\u5ea6\u7ea6{terrain_summary.get('mean_slope_degree')}\u00b0\u3002"
        )
    else:
        terrain_text = "\u5730\u5f62\u8bb0\u5f55\u6682\u4e0d\u5b8c\u6574\uff0c\u9700\u8981\u7ed3\u5408\u6837\u65b9\u4f4d\u7f6e\u548c\u73b0\u573a\u89c2\u5bdf\u8865\u5145\u5224\u65ad\u3002"
    return (
        f"\u5f53\u524d\u6837\u5730\u5df2\u8bb0\u5f55\u4e54\u6728{site_summary.get('tree_count', 0)}\u682a\u3001"
        f"\u6837\u65b9{site_summary.get('subplot_count', 0)}\u4e2a\u3001\u6811\u79cd{site_summary.get('species_count', 0)}\u79cd\u3002"
        f"\u4e3b\u8981\u6811\u79cd\u5305\u62ec{species_text}\u3002{terrain_text}"
        "\u4ece\u8c03\u67e5\u7406\u89e3\u4e0a\u770b\uff0c\u7b2c\u4e00\u8f6e\u5e94\u91cd\u70b9\u628a\u6797\u5206\u7ed3\u6784\u3001\u6811\u79cd\u7ec4\u6210\u3001\u5730\u5f62\u68af\u5ea6\u548c\u5f02\u5e38\u5355\u6728\u653e\u5728\u540c\u4e00\u5f20\u8bc1\u636e\u94fe\u91cc\u770b\uff0c"
        "\u4e0d\u8981\u53ea\u770b\u5355\u4e2a\u6307\u6807\u3002\u5efa\u8bae\u4f18\u5148\u6838\u67e5\u4ee3\u8868\u6027\u6837\u65b9\u3001\u8fb9\u7f18\u6216\u7279\u6b8a\u5730\u5f62\u6837\u65b9\u3001\u4ee5\u53ca\u5f62\u6001\u6216\u8bb0\u5f55\u5f02\u5e38\u7684\u5355\u6728\uff0c"
        "\u7528\u73b0\u573a\u89c2\u5bdf\u8865\u8db3\u51a0\u5c42\u53d7\u538b\u3001\u66f4\u65b0\u72b6\u51b5\u3001\u571f\u58e4\u6e7f\u5ea6\u3001\u75c5\u866b\u5bb3\u75d5\u8ff9\u548c\u4eba\u4e3a\u5e72\u6270\u7b49\u6570\u636e\u5e93\u65e0\u6cd5\u76f4\u63a5\u786e\u8ba4\u7684\u4fe1\u606f\u3002"
    )


def _generate_site_analysis_with_agent(snapshot: Dict[str, Any]) -> Optional[str]:
    try:
        from agent import run_agent_chat
    except Exception:
        return None
    prompt = (
        "\u4f60\u662f\u6837\u5730\u91ce\u5916\u8c03\u67e5\u4e13\u5bb6\u3002\u8bf7\u57fa\u4e8e\u4e0b\u9762\u7684\u6570\u636e\u5feb\u7167\uff0c\u5199\u4e00\u6bb5\u9762\u5411\u8c03\u67e5\u4eba\u5458\u7684\u6837\u5730\u8ba4\u77e5\u6458\u8981\u3002"
        "\u91cd\u70b9\u8bf4\u660e\u8fd9\u4e2a\u6837\u5730\u76ee\u524d\u5448\u73b0\u51fa\u7684\u6797\u5206\u3001\u6811\u79cd\u3001\u5730\u5f62\u548c\u6c14\u5019\u80cc\u666f\u7279\u70b9\uff0c\u4ee5\u53ca\u4e3a\u4e86\u66f4\u597d\u7406\u89e3\u8fd9\u4e2a\u6837\u5730\uff0c"
        "\u73b0\u573a\u8c03\u67e5\u5e94\u8be5\u91cd\u70b9\u89c2\u5bdf\u54ea\u4e9b\u73b0\u8c61\u3001\u8865\u5145\u54ea\u4e9b\u8bc1\u636e\u3002\u4e0d\u8981\u53ea\u7f57\u5217\u6570\u636e\u5e93\u6307\u6807\uff0c\u4e0d\u8981\u8bf4'\u82e5\u660e\u5929\u65f6\u95f4\u6709\u9650'\uff0c"
        "\u4e0d\u8981\u8f93\u51faJSON\u3002\u7528\u4e2d\u6587\uff0c2\u52303\u6bb5\uff0c\u5177\u4f53\u3001\u514b\u5236\u3001\u6709\u73b0\u573a\u6307\u5bfc\u610f\u4e49\u3002\n\n"
        f"\u6570\u636e\u5feb\u7167\uff1a{json.dumps(snapshot, ensure_ascii=False, default=str)}"
    )
    try:
        result = run_agent_chat(
            question=prompt,
            session_id="survey_site_brief_cache",
            client_id="survey_site_brief",
            context={"current_page": "survey_site_brief", "context_policy": "auto"},
            options={"max_tool_rounds": 4, "history_limit": 0},
        )
        answer = str(result.get("answer") or "").strip()
        return answer or None
    except Exception as exc:
        print(f"[survey] site brief agent failed: {exc}")
        return None


def get_site_survey_brief(force_refresh: bool = False) -> Dict[str, Any]:
    """Return AI-oriented site brief for field survey planning."""
    global _SITE_BRIEF_CACHE
    if _SITE_BRIEF_CACHE is not None and not force_refresh:
        return {"status": "success", "brief": _SITE_BRIEF_CACHE, "cached": True, "cache_source": "memory"}
    if not force_refresh:
        persisted = _read_site_brief_cache()
        if persisted:
            _SITE_BRIEF_CACHE = persisted
            return {"status": "success", "brief": persisted, "cached": True, "cache_source": "database"}

    context = _gather_survey_context()
    species = [dict(row) for row in context.get("species") or []]
    anomalies = [dict(row) for row in context.get("anomalies") or []]
    subplots = [dict(row) for row in context.get("subplots") or []]
    topography = [dict(row) for row in context.get("topography") or []]
    climate = context.get("climate") or {}

    with _get_conn() as conn:
        species_col = _detect_species_column(conn)
        species_count_expr = f"COUNT(DISTINCT CASE WHEN {species_col} IS NOT NULL AND TRIM({species_col}) <> '' THEN {species_col} END)" if species_col else "0"
        totals = conn.execute(f"""
            SELECT
                COUNT(*) AS tree_count,
                COUNT(DISTINCT subplot_id) AS subplot_count,
                {species_count_expr} AS species_count
            FROM tree_observations
        """).fetchone()

    site_summary = {
        "tree_count": int(totals.get("tree_count") or 0) if totals else 0,
        "species_count": int(totals.get("species_count") or 0) if totals else len(species),
        "subplot_count": int(totals.get("subplot_count") or 0) if totals else len({item.get("subplot_id") for item in subplots if item.get("subplot_id")}),
    }
    top_species = species[:5]
    high_attention = anomalies[:8]

    elevations = [float(item.get("mean_elevation")) for item in topography if item.get("mean_elevation") is not None]
    slopes = [float(item.get("mean_slope")) for item in topography if item.get("mean_slope") is not None]
    terrain_summary = {
        "subplot_samples": len(topography),
        "min_elevation_m": round(min(elevations), 1) if elevations else None,
        "max_elevation_m": round(max(elevations), 1) if elevations else None,
        "mean_slope_degree": round(sum(slopes) / len(slopes), 1) if slopes else None,
    }

    suggested_questions = [
        "\u57fa\u4e8e\u8fd9\u4e2a\u6837\u5730\u7279\u70b9\uff0c\u5e2e\u6211\u751f\u6210\u4e00\u6b21\u7efc\u5408\u8c03\u67e5\u65b9\u6848\u3002",
        "\u6211\u60f3\u91cd\u70b9\u4e86\u89e3\u4e3b\u8981\u6811\u79cd\u7684\u7a7a\u95f4\u5dee\u5f02\uff0c\u5e94\u8be5\u600e\u4e48\u8c03\u67e5\uff1f",
        "\u54ea\u4e9b\u6837\u65b9\u6216\u5355\u6728\u6700\u9002\u5408\u4f5c\u4e3a\u5f02\u5e38\u590d\u6838\u5bf9\u8c61\uff1f",
        "\u5982\u679c\u8981\u9a8c\u8bc1\u5730\u5f62\u68af\u5ea6\u4e0e\u6797\u5206\u7ed3\u6784\u5dee\u5f02\uff0c\u5e94\u8be5\u600e\u4e48\u5b89\u6392\uff1f",
    ]
    snapshot = {
        "site_summary": site_summary,
        "top_species": top_species,
        "terrain_summary": terrain_summary,
        "climate_context": climate,
        "attention_examples": high_attention,
    }
    analysis_text = _generate_site_analysis_with_agent(snapshot) or _fallback_site_analysis(site_summary, top_species, terrain_summary)

    _SITE_BRIEF_CACHE = {
        "analysis_text": analysis_text,
        "suggested_questions": suggested_questions,
        "data_snapshot": site_summary,
        "top_species": top_species,
        "terrain_summary": terrain_summary,
        "climate_context": climate,
        "attention_examples": high_attention,
    }
    _write_site_brief_cache(_SITE_BRIEF_CACHE)
    return {"status": "success", "brief": _SITE_BRIEF_CACHE, "cached": False, "cache_source": "generated"}


# =============================================================================
# AI 方案生成 — 使用智能体（ReAct Agent）分析生成
# =============================================================================

def generate_survey_plan(user_request: str) -> Dict[str, Any]:
    """
    根据用户的自然语言需求，使用智能体（ReAct Agent）生成调查方案。

    流程：
    1. 调用 agent.run_agent_chat()，智能体使用工具分析数据
    2. 智能体逐步分析：查树种分布 → 健康异常 → 气候背景 → 竞争压力
    3. 智能体结合用户需求综合判断，输出针对性方案
    4. 解析 JSON → 验证 → 保存到数据库

    与之前的硬编码方案不同，智能体可以自主决定查什么数据、怎么分析。
    """
    print(f"[survey] 🧠 使用智能体生成调查方案，需求: {user_request}")

    try:
        from agent import run_agent_chat
    except Exception as exc:
        print(f"[survey] 无法导入 agent: {exc}")
        return {"status": "error", "message": f"智能体模块不可用: {exc}"}

    # 构造智能体提示词 — 核心思路：先分析数据，再结合需求
    agent_prompt = f"""You are a senior field ecology survey planner for a large forest monitoring plot (24 ha, 600 subplots).
Your job is to create practical, data-driven survey plans that help field workers collect the most valuable information under real-world constraints.

THE KEY PRINCIPLE: ANALYZE FIRST, PLAN SECOND.
Never generate recommendations without first using your tools to examine the plot data thoroughly.

MANDATORY FIRST STEP — ANALYZE THE PLOT:
Use your available tools to investigate:
1. What species are present and in what proportions?
2. What is the size/DBH distribution — is it a young, mature, or mixed stand?
3. Are there health anomalies (dead trees, poor health, high HDR > 100)?
4. What terrain/topography context exists?
5. What is the climate background (recent drought, temperature trends)?
6. Based on all the above, what is the ESSENCE of this plot?

Then, based on your analysis, determine:
- What are the key ecological patterns or problems worth investigating?
- Where is the highest information value per unit of field effort?
- What combination of tasks gives the best picture: anomalies + representative samples + controls?

HANDLING VAGUE USER REQUESTS:
If the user's request is vague or general (e.g. "帮我看看", "常规调查"), DO NOT ask them to fill forms.
Instead, use your data analysis to determine the most valuable targets, and explain your reasoning in ai_analysis.
Your recommendation is based on what the data tells you, combined with whatever constraints the user mentioned.

USER_REQUEST_AND_CONSTRAINTS:
{user_request}

OUTPUT — STRICT JSON ONLY (no markdown, no explanation outside the fields):
{{
  "title": "short Chinese title describing the plan",
  "summary": "Chinese: brief overview of what this plan covers and why",
  "ai_analysis": "Chinese: explain what you found from data analysis — species composition, key patterns, anomalies, terrain/climate context, why certain targets were prioritized. THIS IS YOUR ANALYSIS REPORT.",
  "recommendations": [
    {{
      "tree_id": "real tree_id or null",
      "subplot_id": "real subplot_id",
      "species": "species name or null",
      "priority": "high/medium/low",
      "category": "health_check/morphology/competition/climate_stress/species_observation/control",
      "reason": "Chinese: data-supported reason for selecting this specific target",
      "suggested_actions": "Chinese: specific field instructions — how to find it, what to check, what to record"
    }}
  ]
}}

QUALITY RULES:
- Every tree_id MUST exist in the database (use only real IDs from your tool results).
- Balance three types: anomaly targets (high priority), representative samples (medium), controls (low).
- Limited time -> fewer tasks, each with high value. Abundant time -> more coverage.
- ai_analysis is where you show your thinking — it's the most important field for user trust.
- suggested_actions must be specific enough for a field worker to use directly.
- Do NOT generate more than 25 recommendations unless the user explicitly asked for a comprehensive survey.
"""

    try:
        # 调用智能体 — 使用完整的 ReAct 循环（工具调用 + 推理）
        result = run_agent_chat(
            question=agent_prompt,
            session_id=None,
            client_id="survey_plan_generator",
            context={"current_page": "survey_planning", "survey_mode": True},
            options={
                "max_tool_rounds": 10,
                "history_limit": 0,
            },
        )

        answer = result.get("answer", "")
        used_tools = result.get("used_tools", [])

        print(f"[survey] 智能体使用了 {len(used_tools)} 个工具")
        print(f"[survey] 智能体回答前200字: {answer[:200]}")

        # 解析 JSON
        plan_data = _parse_llm_json(answer)

        if not plan_data:
            print("[survey] 智能体输出不是合法 JSON，尝试用 LLM 直接重试…")
            # 兜底：把智能体的回答发给 LLM 提取 JSON
            plan_data = _retry_extract_json(answer, user_request)

        if plan_data and plan_data.get("recommendations"):
            print(f"[survey] 成功解析方案: {plan_data.get('title', '无标题')}, "
                  f"{len(plan_data['recommendations'])} 条建议")
            return _save_plan_to_db(user_request, plan_data)

        # 智能体返回了内容但没有成功解析 JSON → 用确定性逻辑作为最终兜底
        print("[survey] 智能体未能输出结构化方案，使用规则兜底")
        return _generate_deterministic_plan(user_request, _gather_survey_context())

    except Exception as exc:
        print(f"[survey] 智能体调用异常: {exc}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"智能体分析失败: {exc}"}


def _gather_survey_context() -> Dict[str, Any]:
    """收集调查上下文数据（供兜底方案使用）"""
    species = _gather_species_overview()
    anomalies = _gather_health_anomaly_trees(40)
    climate = _gather_climate_context()
    subplots = _gather_subplot_summary()
    topography = _gather_topography_context()
    return {
        "species_overview": species[:15],
        "health_anomalies": anomalies,
        "climate_context": climate,
        "subplot_summaries": subplots[:15],
        "topography_samples": topography[:10],
        "total_trees_in_db": sum(s["count"] for s in species) if species else 0,
        "total_species": len(species),
        "total_subplots": len(subplots),
    }


def _retry_extract_json(agent_answer: str, user_request: str) -> Optional[Dict[str, Any]]:
    """当智能体回答不是纯 JSON 时，用 LLM 提取 JSON"""
    try:
        from provider import get_ai_response
        prompt = f"""从下面的文本中提取调查方案的 JSON 数据。
文本是 AI 林业助手对调查需求的分析回复，其中应该包含了调查建议。

用户需求：{user_request}

AI 回复：
{agent_answer}

请提取 JSON 格式的方案，格式要求：
{{
  "title": "方案标题",
  "summary": "方案概述",
  "ai_analysis": "分析说明",
  "recommendations": [
    {{"tree_id": "编号或null", "subplot_id": "样地", "species": "树种", "priority": "high/medium/low", "category": "类别", "reason": "原因", "suggested_actions": "行动"}}
  ]
}}

只输出 JSON，不要其他文字。"""
        text = get_ai_response(content=prompt, prompt="提取 JSON", temperature=0.1)
        return _parse_llm_json(text)
    except Exception:
        return None


def _parse_llm_json(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 回复中提取 JSON"""
    if not text:
        return None

    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    import re
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试从中提取第一个 { ... } 块
    brace_start = text.find('{')
    brace_end = text.rfind('}')
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _save_plan_to_db(user_request: str, plan_data: Dict[str, Any]) -> Dict[str, Any]:
    """验证并保存方案到数据库"""
    title = str(plan_data.get("title", "野外调查方案")).strip()
    summary = str(plan_data.get("summary", "")).strip()
    ai_analysis = str(plan_data.get("ai_analysis", "")).strip()
    recommendations = plan_data.get("recommendations", plan_data.get("items", []))

    if not recommendations:
        return {"status": "error", "message": "方案中没有调查建议"}

    # 验证推荐的真实性
    valid_recs = _validate_recommendations(recommendations)

    if not valid_recs:
        return {"status": "error", "message": "验证失败：生成的建议中没有有效的树木/样地数据"}

    # 写入数据库
    with _get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO field_survey_plans (title, user_request, ai_analysis, summary, status, tree_count, subplot_count) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?)",
            (
                title or "野外调查方案",
                user_request,
                ai_analysis,
                summary,
                sum(1 for r in valid_recs if r.get("tree_id")),
                len(set(r["subplot_id"] for r in valid_recs if r.get("subplot_id"))),
            ),
        )
        plan_id = cursor.lastrowid

        for idx, rec in enumerate(valid_recs):
            conn.execute(
                "INSERT INTO survey_recommendations "
                "(plan_id, tree_id, subplot_id, species, priority, category, reason, suggested_actions, evidence_data, status, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    plan_id,
                    rec.get("tree_id"),
                    rec.get("subplot_id"),
                    rec.get("species"),
                    rec.get("priority", "medium"),
                    rec.get("category", "health_check"),
                    rec.get("reason", ""),
                    rec.get("suggested_actions", ""),
                    rec.get("evidence_data"),
                    idx + 1,
                ),
            )
        conn.commit()

    # 返回完整方案
    return _get_plan_full(plan_id)


def _validate_recommendations(recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """验证建议中的树木是否真实存在于数据库"""
    valid = []
    tree_ids_to_check = []
    subplot_ids_to_check = set()

    for rec in recommendations:
        tid = str(rec.get("tree_id") or "").strip() if rec.get("tree_id") else ""
        spid = str(rec.get("subplot_id") or "").strip() if rec.get("subplot_id") else ""

        if tid:
            tree_ids_to_check.append(tid)
        if spid:
            subplot_ids_to_check.add(spid)

    # 批量查询树是否存在
    existing_trees = set()
    existing_subplots = set()

    if tree_ids_to_check:
        with _get_conn() as conn:
            placeholders = ",".join("?" for _ in tree_ids_to_check)
            rows = conn.execute(
                f"SELECT DISTINCT tree_id FROM tree_observations WHERE tree_id IN ({placeholders})",
                tree_ids_to_check,
            ).fetchall()
            existing_trees = set(r["tree_id"] for r in rows)

    if subplot_ids_to_check:
        with _get_conn() as conn:
            placeholders = ",".join("?" for _ in subplot_ids_to_check)
            rows = conn.execute(
                f"SELECT DISTINCT subplot_id FROM tree_observations WHERE subplot_id IN ({placeholders})",
                list(subplot_ids_to_check),
            ).fetchall()
            existing_subplots = set(r["subplot_id"] for r in rows)
            # 也检查 topography
            rows2 = conn.execute(
                f"SELECT DISTINCT subplot_id FROM topography_observations WHERE subplot_id IN ({placeholders})",
                list(subplot_ids_to_check),
            ).fetchall()
            existing_subplots.update(r["subplot_id"] for r in rows2)

    for rec in recommendations:
        tid = str(rec.get("tree_id") or "").strip() if rec.get("tree_id") else ""
        spid = str(rec.get("subplot_id") or "").strip() if rec.get("subplot_id") else ""

        # 如果有 tree_id，必须在数据库中
        if tid and tid not in existing_trees:
            print(f"[survey] 跳过不存在的树: {tid}")
            continue

        # 如果有 subplot_id 但没有 tree_id，验证样地
        if spid and not tid and spid not in existing_subplots:
            print(f"[survey] 跳过不存在的样地: {spid}")
            continue

        # 补充物种信息
        species = rec.get("species", "")
        if not species and tid:
            with _get_conn() as conn:
                row = conn.execute(
                    "SELECT species FROM tree_observations WHERE tree_id=? LIMIT 1",
                    (tid,),
                ).fetchone()
                if row:
                    species = row["species"]

        valid.append({
            "tree_id": tid or None,
            "subplot_id": spid or None,
            "species": species or rec.get("species", ""),
            "priority": rec.get("priority", "medium"),
            "category": rec.get("category", "health_check"),
            "reason": rec.get("reason", ""),
            "suggested_actions": rec.get("suggested_actions", ""),
        })

    return valid


# =============================================================================
# 确定性方案生成（LLM 不可用时的后备）
# =============================================================================

def _generate_deterministic_plan(user_request: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """使用确定性规则生成调查方案（无需 LLM）"""
    print("[survey] 使用确定性规则生成方案（LLM 后备）")

    anomalies = context.get("health_anomalies", [])
    species_list = context.get("species_overview", [])
    subplots = context.get("subplot_summaries", [])

    recommendations = []
    seen_trees = set()

    # 1. 高优先级：健康异常的树木
    for tree in anomalies:
        if len(recommendations) >= 25:
            break
        tid = tree["tree_id"]
        if tid in seen_trees:
            continue
        seen_trees.add(tid)

        priority = "high" if tree.get("health_status") == "dead" else "high" if tree.get("health_status") == "poor" else "medium"
        category = "health_check"
        reason = f"健康状态为「{tree.get('health_status')}」"
        actions = "检查存活状态、树干是否完整、冠层状况、有无病虫害迹象"
        if tree.get("hdr") and tree["hdr"] > 100:
            reason += f"，高径比 HDR={tree['hdr']:.1f}（超过100阈值）"
            actions += "；检查树干是否倾斜、树冠是否稀疏"
            category = "morphology"

        recommendations.append({
            "tree_id": tid,
            "subplot_id": tree.get("subplot_id"),
            "species": tree.get("species"),
            "priority": priority,
            "category": category,
            "reason": reason,
            "suggested_actions": actions,
        })

    # 2. 按树种选健康对照
    species_counts = {}
    for s in species_list:
        species_counts[s["species"]] = s["count"]

    # 每个主要树种选1棵健康样木作为对照
    major_species = [s for s in species_list if s["count"] >= 10][:5]
    with _get_conn() as conn:
        for sp in major_species:
            if len(recommendations) >= 28:
                break
            # 找健康的该树种
            healthy = conn.execute("""
                SELECT tree_id, subplot_id, tree_dbh_cm, tree_height_m
                FROM tree_observations
                WHERE species=? AND health_status='good' AND tree_dbh_cm > 0
                ORDER BY RANDOM() LIMIT 1
            """, (sp["species"],)).fetchone()
            if healthy and healthy["tree_id"] not in seen_trees:
                seen_trees.add(healthy["tree_id"])
                recommendations.append({
                    "tree_id": healthy["tree_id"],
                    "subplot_id": healthy["subplot_id"],
                    "species": healthy["species"],
                    "priority": "low",
                    "category": "control",
                    "reason": f"健康对照样木（{sp['species']}）",
                    "suggested_actions": "记录正常状态，与异常个体对比",
                })

    # 3. 样地级建议
    for sp in subplots[:5]:
        if len(recommendations) >= 30:
            break
        dead_pct = round((sp["dead_count"] + sp["poor_count"]) / max(sp["tree_count"], 1) * 100, 1)
        if dead_pct > 10:
            recommendations.append({
                "tree_id": None,
                "subplot_id": sp["subplot_id"],
                "species": None,
                "priority": "medium",
                "category": "health_check",
                "reason": f"样地 {sp['subplot_id']} 中异常木比例 {dead_pct}%",
                "suggested_actions": "整体巡视样地，记录异常现象",
            })

    title = "智能生成调查方案"
    summary = f"基于数据库分析，共生成 {len(recommendations)} 条调查建议，覆盖 {len(seen_trees)} 株树木。"
    ai_analysis = f"检测到 {len(anomalies)} 株健康异常树木，{len(major_species)} 个主要树种。"

    plan_data = {
        "title": title,
        "summary": summary,
        "ai_analysis": ai_analysis,
        "recommendations": recommendations,
    }
    return _save_plan_to_db(user_request, plan_data)


# =============================================================================
# 查询接口
# =============================================================================

def _build_field_check_items(rec: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build practical field checklist items from a recommendation."""
    text = " ".join(str(rec.get(k) or "") for k in ("category", "reason", "suggested_actions")).lower()
    items = [
        {"key": "identity", "label": "\u6838\u5bf9\u5bf9\u8c61", "prompt": "\u6838\u5bf9\u6811\u53f7/\u6837\u65b9\u53f7\u662f\u5426\u4e0e\u63a8\u8350\u4efb\u52a1\u4e00\u81f4\uff0c\u5fc5\u8981\u65f6\u62cd\u6444\u6811\u724c\u6216\u4f4d\u7f6e\u7167\u7247\u3002"},
        {"key": "location", "label": "\u5b9a\u4f4d\u8bb0\u5f55", "prompt": "\u8bb0\u5f55\u5f53\u524d\u4f4d\u7f6e\u3001\u6837\u65b9\u5185\u76f8\u5bf9\u65b9\u4f4d\uff0c\u4ee5\u53ca\u662f\u5426\u5bb9\u6613\u518d\u6b21\u627e\u5230\u3002"},
    ]

    def add(key: str, label: str, prompt: str) -> None:
        if not any(item["key"] == key for item in items):
            items.append({"key": key, "label": label, "prompt": prompt})

    if any(word in text for word in ("health", "\u5065\u5eb7", "\u67af", "\u6b7b", "\u75c5", "\u866b", "\u53d7\u5bb3")):
        add("health", "\u5065\u5eb7\u72b6\u6001", "\u89c2\u5bdf\u53f6\u8272\u3001\u67af\u679d\u3001\u65ad\u68a2\u3001\u75c5\u866b\u75d5\u8ff9\u3001\u673a\u68b0\u635f\u4f24\uff0c\u5e76\u533a\u5206\u786e\u5b9a\u89c2\u6d4b\u4e0e\u5f85\u786e\u8ba4\u73b0\u8c61\u3002")
    if any(word in text for word in ("morph", "\u5f62\u6001", "hdr", "\u9ad8\u5f84", "\u80f8\u5f84", "\u6811\u9ad8", "\u51a0", "\u503e\u659c")):
        add("morphology", "\u5f62\u6001\u590d\u6838", "\u590d\u6838\u80f8\u5f84\u3001\u6811\u9ad8\u3001\u51a0\u5e45\u3001\u679d\u4e0b\u9ad8\u3001\u503e\u659c\u6216\u5f2f\u66f2\u60c5\u51b5\uff0c\u6807\u660e\u660e\u663e\u6d4b\u91cf\u5f02\u5e38\u3002")
    if any(word in text for word in ("competition", "\u7ade\u4e89", "\u53d7\u538b", "\u90bb\u6728", "\u90c1\u95ed")):
        add("competition", "\u90bb\u6728\u7ade\u4e89", "\u89c2\u5bdf\u5468\u56f4\u90bb\u6728\u5bc6\u5ea6\u3001\u51a0\u5c42\u906e\u6321\u3001\u540c\u79cd/\u5f02\u79cd\u90bb\u6728\uff0c\u4ee5\u53ca\u5bf9\u8c61\u6728\u662f\u5426\u660e\u663e\u53d7\u538b\u3002")
    if any(word in text for word in ("topography", "\u5730\u5f62", "\u6d77\u62d4", "\u5761", "\u6c9f", "\u810a", "\u5761\u5411")):
        add("topography", "\u5fae\u5730\u5f62", "\u8bb0\u5f55\u5761\u4f4d\u3001\u5761\u5411\u3001\u88f8\u5ca9\u3001\u51b2\u5237\u3001\u79ef\u6c34\u6216\u571f\u58e4\u6e7f\u5ea6\u7b49\u5fae\u5730\u5f62\u7ebf\u7d22\u3002")
    if any(word in text for word in ("climate", "\u6c14\u5019", "\u5e72\u65f1", "\u964d\u6c34", "\u4f4e\u6e29", "\u971c", "\u98ce", "\u96ea")):
        add("climate_exposure", "\u6c14\u5019\u66b4\u9732\u75d5\u8ff9", "\u89c2\u5bdf\u5e72\u65f1\u3001\u51bb\u5bb3\u3001\u98ce\u96ea\u538b\u3001\u843d\u679d\u843d\u53f6\u7b49\u66b4\u9732\u75d5\u8ff9\uff1b\u4e0d\u8981\u76f4\u63a5\u5f52\u56e0\u4e3a\u6c14\u5019\u3002")

    add("notes", "\u73b0\u573a\u5907\u6ce8", "\u8bb0\u5f55\u4e0e\u63a8\u8350\u7406\u7531\u76f8\u5173\u7684\u5173\u952e\u73b0\u8c61\u3001\u7167\u7247\u7f16\u53f7\u548c\u9700\u8981\u8865\u6d4b\u7684\u5b57\u6bb5\u3002")
    return items


def _get_plan_full(plan_id: int) -> Optional[Dict[str, Any]]:
    """Return a full survey plan with recommendations and saved observations."""
    with _get_conn() as conn:
        plan = conn.execute(
            "SELECT * FROM field_survey_plans WHERE plan_id=?",
            (plan_id,),
        ).fetchone()
        if not plan:
            return None

        recommendation_rows = conn.execute(
            "SELECT * FROM survey_recommendations WHERE plan_id=? ORDER BY sort_order",
            (plan_id,),
        ).fetchall()
        observations = conn.execute(
            "SELECT * FROM field_observations WHERE plan_id=?",
            (plan_id,),
        ).fetchall()

    obs_by_id = {obs.get("obs_id"): dict(obs) for obs in observations if obs.get("obs_id") is not None}
    obs_by_rec = {obs.get("rec_id"): dict(obs) for obs in observations if obs.get("rec_id") is not None}
    recommendations = []
    for row in recommendation_rows:
        rec = dict(row)
        observation = obs_by_id.get(rec.get("obs_id")) or obs_by_rec.get(rec.get("rec_id"))
        rec["observation"] = observation
        rec["field_check_items"] = _build_field_check_items(rec)
        recommendations.append(rec)

    result = dict(plan)
    result["recommendations"] = recommendations
    return {"status": "success", "plan": result}


def list_plans(limit: int = 20) -> Dict[str, Any]:
    """列出所有调查方案"""
    with _get_conn() as conn:
        plans = conn.execute(
            "SELECT * FROM field_survey_plans ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {"status": "success", "plans": plans, "count": len(plans)}


def get_plan(plan_id: int) -> Dict[str, Any]:
    """获取方案详情"""
    result = _get_plan_full(plan_id)
    if not result:
        return {"status": "error", "message": f"方案不存在: plan_id={plan_id}"}
    return result


def revise_survey_plan(plan_id: int, instruction: str) -> Dict[str, Any]:
    """Create a revised survey plan from an existing plan and a natural-language instruction."""
    instruction = str(instruction or "").strip()
    if not instruction:
        return {"status": "error", "message": "revision instruction is required"}

    current = get_plan(plan_id)
    if current.get("status") != "success" or not current.get("plan"):
        return current

    plan = current["plan"]
    recommendations = plan.get("recommendations") or []
    compact_recs = []
    for rec in recommendations[:80]:
        compact_recs.append({
            "tree_id": rec.get("tree_id"),
            "subplot_id": rec.get("subplot_id"),
            "species": rec.get("species"),
            "priority": rec.get("priority"),
            "category": rec.get("category"),
            "reason": rec.get("reason"),
            "suggested_actions": rec.get("suggested_actions"),
        })

    revision_request = (
        "Revise this existing field survey plan according to the user's new instruction. "
        "Keep only real tree_id/subplot_id values already present in the database. "
        "Return a practical updated plan, not an explanation.\n\n"
        f"Original plan id: {plan_id}\n"
        f"Original title: {plan.get('title') or ''}\n"
        f"Original user request: {plan.get('user_request') or ''}\n"
        f"Original summary: {plan.get('summary') or ''}\n"
        f"Original recommendations JSON: {json.dumps(compact_recs, ensure_ascii=False)}\n\n"
        f"User revision instruction: {instruction}\n"
    )
    revised = generate_survey_plan(revision_request)
    if revised.get("status") == "success" and revised.get("plan"):
        new_plan_id = revised["plan"].get("plan_id")
        if new_plan_id:
            with _get_conn() as conn:
                conn.execute(
                    "UPDATE field_survey_plans SET summary=? WHERE plan_id=?",
                    (
                        f"Revised from plan {plan_id}. Instruction: {instruction}",
                        new_plan_id,
                    ),
                )
                conn.commit()
            revised = get_plan(int(new_plan_id))
            if revised.get("status") == "success":
                revised["revised_from_plan_id"] = plan_id
                revised["revision_instruction"] = instruction
    return revised


def update_plan_status(plan_id: int, status: str) -> Dict[str, Any]:
    """更新方案状态"""
    valid_statuses = {"draft", "active", "completed", "cancelled"}
    if status not in valid_statuses:
        return {"status": "error", "message": f"无效状态: {status}，可选: {valid_statuses}"}

    with _get_conn() as conn:
        conn.execute(
            "UPDATE field_survey_plans SET status=?, updated_at=datetime('now','localtime') WHERE plan_id=?",
            (status, plan_id),
        )
        conn.commit()
    return get_plan(plan_id)


# =============================================================================
# 现场记录
# =============================================================================

def _update_plan_completed_count(plan_id: int) -> None:
    """更新方案的完成进度"""
    with _get_conn() as conn:
        stats = conn.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed
            FROM survey_recommendations WHERE plan_id=?
        """, (plan_id,)).fetchone()
        if stats:
            conn.execute(
                "UPDATE field_survey_plans SET completed_count=?, updated_at=datetime('now','localtime') WHERE plan_id=?",
                (stats["completed"] or 0, plan_id),
            )
            # 如果全部完成，自动更新状态
            if stats["total"] and stats["completed"] and stats["total"] == stats["completed"]:
                conn.execute(
                    "UPDATE field_survey_plans SET status='completed', updated_at=datetime('now','localtime') WHERE plan_id=?",
                    (plan_id,),
                )
        conn.commit()


def save_observation(
    plan_id: int,
    rec_id: Optional[int] = None,
    tree_id: Optional[str] = None,
    subplot_id: Optional[str] = None,
    species: Optional[str] = None,
    notes: str = "",
    health_status: Optional[str] = None,
    pest_signs: Optional[str] = None,
    phenophase: Optional[str] = None,
    photo_paths: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
) -> Dict[str, Any]:
    """保存一条野外观察记录，并更新对应建议的状态"""
    # 如果有关联的建议，获取树信息
    if rec_id and not tree_id:
        with _get_conn() as conn:
            rec = conn.execute(
                "SELECT tree_id, subplot_id, species FROM survey_recommendations WHERE rec_id=?",
                (rec_id,),
            ).fetchone()
            if rec:
                tree_id = rec.get("tree_id") or tree_id
                subplot_id = rec.get("subplot_id") or subplot_id
                species = rec.get("species") or species

    with _get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO field_observations "
            "(plan_id, rec_id, tree_id, subplot_id, species, notes, health_status, pest_signs, phenophase, photo_paths, latitude, longitude) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plan_id, rec_id, tree_id, subplot_id, species, notes,
                health_status, pest_signs, phenophase, photo_paths, latitude, longitude,
            ),
        )
        obs_id = cursor.lastrowid

    # 更新对应建议为已完成
    if rec_id:
        with _get_conn() as conn:
            conn.execute(
                "UPDATE survey_recommendations SET status='completed', completed_at=datetime('now','localtime'), obs_id=? WHERE rec_id=?",
                (obs_id, rec_id),
            )
            conn.commit()
        _update_plan_completed_count(plan_id)

    return get_observation(obs_id)


def update_observation(obs_id: int, **kwargs) -> Dict[str, Any]:
    """更新观察记录"""
    valid_fields = {"notes", "health_status", "pest_signs", "phenophase", "photo_paths"}
    updates = {k: v for k, v in kwargs.items() if k in valid_fields and v is not None}
    if not updates:
        return get_observation(obs_id)

    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values())

    with _get_conn() as conn:
        conn.execute(
            f"UPDATE field_observations SET {set_clause} WHERE obs_id=?",
            (*values, obs_id),
        )
        conn.commit()

    return get_observation(obs_id)


def get_observation(obs_id: int) -> Dict[str, Any]:
    """获取观察记录"""
    with _get_conn() as conn:
        obs = conn.execute(
            "SELECT * FROM field_observations WHERE obs_id=?",
            (obs_id,),
        ).fetchone()
    if not obs:
        return {"status": "error", "message": f"记录不存在: obs_id={obs_id}"}
    return {"status": "success", "observation": obs}


def get_plan_observations(plan_id: int) -> Dict[str, Any]:
    """获取方案的所有观察记录"""
    with _get_conn() as conn:
        observations = conn.execute(
            "SELECT * FROM field_observations WHERE plan_id=? ORDER BY recorded_at",
            (plan_id,),
        ).fetchall()
    return {"status": "success", "observations": observations, "count": len(observations)}


def update_recommendation_status(rec_id: int, status: str, obs_id: Optional[int] = None) -> Dict[str, Any]:
    """Update recommendation status without accidentally clearing saved observations."""
    with _get_conn() as conn:
        if obs_id is None:
            conn.execute(
                "UPDATE survey_recommendations SET status=?, completed_at=CASE WHEN ? THEN datetime('now','localtime') ELSE NULL END WHERE rec_id=?",
                (status, status == "completed", rec_id),
            )
        else:
            conn.execute(
                "UPDATE survey_recommendations SET status=?, completed_at=CASE WHEN ? THEN datetime('now','localtime') ELSE NULL END, obs_id=? WHERE rec_id=?",
                (status, status == "completed", obs_id, rec_id),
            )
        conn.commit()

    with _get_conn() as conn:
        rec = conn.execute(
            "SELECT plan_id FROM survey_recommendations WHERE rec_id=?",
            (rec_id,),
        ).fetchone()
    if rec:
        _update_plan_completed_count(rec["plan_id"])

    return {"status": "success", "rec_id": rec_id, "new_status": status}


def generate_report(plan_id: int) -> Dict[str, Any]:
    """生成调查对比报告"""
    result = _get_plan_full(plan_id)
    if not result:
        return {"status": "error", "message": f"方案不存在: plan_id={plan_id}"}

    plan = result["plan"]
    recommendations = plan.pop("recommendations", [])
    observations_result = get_plan_observations(plan_id)
    observations = observations_result.get("observations", [])

    # 构建报告数据
    obs_by_rec = {o["rec_id"]: o for o in observations if o.get("rec_id")}

    # 统计
    total_recs = len(recommendations)
    completed_recs = sum(1 for r in recommendations if r["status"] == "completed")
    skipped_recs = sum(1 for r in recommendations if r["status"] == "skipped")
    pending_recs = total_recs - completed_recs - skipped_recs
    high_priority = sum(1 for r in recommendations if r["priority"] == "high")
    high_completed = sum(1 for r in recommendations if r["priority"] == "high" and r["status"] == "completed")

    # 生成 Markdown 报告
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# 野外调查报告",
        f"",
        f"**生成时间**: {now}",
        f"**调查方案**: {plan['title']}",
        f"**用户需求**: {plan['user_request']}",
        f"**方案状态**: {plan['status']}",
        f"",
        f"---",
        f"",
        f"## 1. 方案概述",
        f"",
        f"{plan.get('summary', '无摘要')}",
        f"",
        f"---",
        f"",
        f"## 2. AI 分析",
        f"",
        f"{plan.get('ai_analysis', '无 AI 分析')}",
        f"",
        f"---",
        f"",
        f"## 3. 完成统计",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总建议数 | {total_recs} |",
        f"| 已完成 | {completed_recs} |",
        f"| 已跳过 | {skipped_recs} |",
        f"| 待完成 | {pending_recs} |",
        f"| 高优先级总数 | {high_priority} |",
        f"| 高优先级已完成 | {high_completed} |",
        f"| 完成率 | {completed_recs / max(total_recs, 1) * 100:.1f}% |",
        f"",
        f"---",
        f"",
        f"## 4. 逐条调查记录",
        f"",
    ]

    for rec in recommendations:
        obs = obs_by_rec.get(rec["rec_id"])
        status_icon = {"completed": "✅", "skipped": "⏭️", "pending": "⏳"}.get(rec["status"], "❓")
        priority_tag = {"high": "🔴 高", "medium": "🟡 中", "low": "🟢 低"}.get(rec["priority"], "⚪")

        tree_info = rec.get("tree_id") or f"样地 {rec.get('subplot_id')}（样地级）"
        species_info = f"（{rec['species']}）" if rec.get("species") else ""

        lines.append(f"### {status_icon} {tree_info} {species_info}")
        lines.append(f"")
        lines.append(f"- **优先级**: {priority_tag}")
        lines.append(f"- **类别**: {rec.get('category', '未指定')}")
        lines.append(f"- **样地**: {rec.get('subplot_id', '未指定')}")
        lines.append(f"- **调查原因**: {rec.get('reason', '未说明')}")
        lines.append(f"- **建议行动**: {rec.get('suggested_actions', '未指定')}")
        lines.append(f"- **状态**: {rec['status']}")

        if obs:
            lines.append(f"")
            lines.append(f"  **📝 现场记录**:")
            lines.append(f"  - 健康状态: {obs.get('health_status', '未记录')}")
            lines.append(f"  - 病虫害迹象: {obs.get('pest_signs', '未记录')}")
            lines.append(f"  - 物候期: {obs.get('phenophase', '未记录')}")
            if obs.get("notes"):
                lines.append(f"  - 观察笔记: {obs['notes']}")
            if obs.get("photo_paths"):
                photos = json.loads(obs["photo_paths"]) if isinstance(obs["photo_paths"], str) else obs["photo_paths"]
                if isinstance(photos, list) and photos:
                    lines.append(f"  - 照片: {', '.join(str(p) for p in photos)}")
            lines.append(f"  - 记录时间: {obs.get('recorded_at', '')}")
        else:
            lines.append(f"  （尚未调查）")

        lines.append(f"")

    lines.extend([
        f"---",
        f"",
        f"## 5. 总结",
        f"",
        f"本次调查共计划 {total_recs} 项检查，实际完成 {completed_recs} 项",
        f"（完成率 {completed_recs / max(total_recs, 1) * 100:.1f}%）。",
        f"",
        f"高优先级检查 {high_priority} 项，已完成 {high_completed} 项。",
        f"",
        f"---",
        f"*报告由 ForestryAgent 野外调查模块自动生成*",
    ])

    report_text = "\n".join(lines)

    # 保存报告文件
    safe_title = plan["title"].replace("/", "_").replace("\\", "_")[:50]
    filename = f"survey_report_{plan_id}_{safe_title}.md"
    report_path = REPORT_DIR / filename
    report_path.write_text(report_text, encoding="utf-8")

    # 更新方案摘要
    with _get_conn() as conn:
        conn.execute(
            "UPDATE field_survey_plans SET summary=?, updated_at=datetime('now','localtime') WHERE plan_id=?",
            (f"报告已生成。共{total_recs}项，完成{completed_recs}项。", plan_id),
        )
        conn.commit()

    return {
        "status": "success",
        "plan_id": plan_id,
        "report": report_text,
        "report_file": filename,
        "stats": {
            "total": total_recs,
            "completed": completed_recs,
            "skipped": skipped_recs,
            "pending": pending_recs,
            "high_priority": high_priority,
            "high_completed": high_completed,
            "completion_rate": round(completed_recs / max(total_recs, 1) * 100, 1),
        },
    }


# =============================================================================
# 启动时初始化
# =============================================================================

init_survey_db()
print("[survey] 野外调查模块已加载")
