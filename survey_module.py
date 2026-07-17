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

def _gather_species_overview() -> List[Dict[str, Any]]:
    """获取树种概况"""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT
                species,
                COUNT(*) AS count,
                ROUND(AVG(tree_dbh_cm), 1) AS avg_dbh,
                ROUND(AVG(tree_height_m), 1) AS avg_height,
                ROUND(AVG(CASE WHEN health_status = 'good' THEN 1.0 ELSE 0 END) * 100, 1) AS health_good_pct,
                ROUND(AVG(CASE WHEN health_status = 'poor' THEN 1.0 ELSE 0 END) * 100, 1) AS health_poor_pct
            FROM tree_observations
            WHERE species IS NOT NULL AND TRIM(species) <> ''
            GROUP BY species
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


def _gather_topography_context() -> List[Dict[str, Any]]:
    """获取地形概况"""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT
                subplot_id,
                ROUND(AVG(elevation_m), 1) AS mean_elevation,
                ROUND(AVG(slope_degree), 1) AS mean_slope,
                ROUND(AVG(aspect_degree), 1) AS mean_aspect
            FROM topography_observations
            GROUP BY subplot_id
            LIMIT 20
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


# =============================================================================
# AI 方案生成
# =============================================================================

def _call_llm(prompt: str, system_prompt: str = "") -> str:
    """调用 LLM 分析数据"""
    try:
        from provider import get_ai_response
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        result = get_ai_response(
            content=full_prompt,
            prompt="请根据上面的数据和要求，生成调查方案。直接输出 JSON。",
            model="deepseek-ai/DeepSeek-V3.2",
        )
        return result
    except Exception as exc:
        print(f"[survey] LLM call failed: {exc}")
        return f""


def generate_survey_plan(user_request: str) -> Dict[str, Any]:
    """
    根据用户的自然语言需求，生成调查方案。

    流程：
    1. 收集数据库中的相关数据（树种、健康、气候等）
    2. 使用 LLM 分析数据并生成结构化的调查建议
    3. 保存到数据库并返回
    """
    print(f"[survey] 生成调查方案，用户需求: {user_request}")

    # 收集数据上下文
    species = _gather_species_overview()
    anomalies = _gather_health_anomaly_trees(40)
    climate = _gather_climate_context()
    subplots = _gather_subplot_summary()
    topography = _gather_topography_context()

    context_data = {
        "user_request": user_request,
        "species_overview": species[:15],  # 最多15个树种
        "health_anomalies": anomalies,
        "climate_context": climate,
        "subplot_summaries": subplots[:15],
        "topography_samples": topography[:10],
        "total_trees_in_db": sum(s["count"] for s in species) if species else 0,
        "total_species": len(species),
        "total_subplots": len(subplots),
    }

    system_prompt = """你是一个林业调查专家。你的任务是根据用户的需求和数据库中的现有数据，生成一份结构化的野外调查方案。

你需要注意：
1. 基于数据库中**真实存在**的树木和样地数据生成建议
2. 每条建议必须有明确的原因（为什么调查这棵树/样地）
3. 每条建议必须有具体的行动指引（去现场看什么、怎么判断）
4. 按优先级排序：high（必须查）> medium（建议查）> low（可选查）
5. 每个树种至少包括1-2棵健康对照树作为参考
6. 建议数量控制在 15-30 条，适合一次野外调查
7. 类别可以是：health_check（健康检查）、morphology（形态关注）、competition（竞争压力）、climate_stress（气候胁迫）、species_observation（物种观察）、control（对照）

请分析用户需求，结合现有数据，输出严格 JSON 格式：
{
  "title": "调查方案标题",
  "summary": "总体方案说明，包括调查目标、预期发现的说明",
  "ai_analysis": "对用户需求的AI分析，包括数据观察和初步判断",
  "recommendations": [
    {
      "tree_id": "树编号或null（如果是样地级建议）",
      "subplot_id": "样地编号",
      "species": "树种",
      "priority": "high/medium/low",
      "category": "类别",
      "reason": "为什么查这个",
      "suggested_actions": "到现场具体看什么"
    }
  ]
}"""

    # 将 context_data 转为 LLM 可读的文本
    context_lines = ["## 当前数据库数据概况"]
    context_lines.append(f"总树木数: {context_data['total_trees_in_db']}, 树种数: {context_data['total_species']}, 样地数: {context_data['total_subplots']}")

    context_lines.append("\n### 树种概况")
    context_lines.append(f"{'树种':<12} {'数量':<6} {'平均胸径':<10} {'平均树高':<10} {'健康%':<8} {'差%':<8}")
    for s in context_data["species_overview"]:
        context_lines.append(f"{s['species']:<12} {s['count']:<6} {s['avg_dbh']:<10} {s['avg_height']:<10} {s['health_good_pct']:<8} {s['health_poor_pct']:<8}")

    context_lines.append("\n### 健康异常树木 (Top 40)")
    context_lines.append(f"{'树编号':<14} {'样地':<8} {'树种':<10} {'胸径':<8} {'树高':<8} {'健康':<6} {'高径比':<8}")
    for t in context_data["health_anomalies"]:
        context_lines.append(f"{t['tree_id']:<14} {t['subplot_id']:<8} {t['species']:<10} {t['tree_dbh_cm']:<8} {t['tree_height_m']:<8} {t['health_status']:<6} {t['hdr']:<8}")

    context_lines.append("\n### 气候背景")
    if climate:
        context_lines.append(json.dumps(climate, ensure_ascii=False, indent=2))
    else:
        context_lines.append("(无气候数据)")

    context_lines.append("\n### 样地概况 (Top 15)")
    context_lines.append(f"{'样地':<8} {'株数':<6} {'树种数':<8} {'平均胸径':<10} {'平均树高':<10} {'平均HDR':<8} {'枯死':<6} {'较差':<6}")
    for s in context_data["subplot_summaries"]:
        context_lines.append(f"{s['subplot_id']:<8} {s['tree_count']:<6} {s['species_count']:<8} {s['avg_dbh']:<10} {s['avg_height']:<10} {s['avg_hdr']:<8} {s['dead_count']:<6} {s['poor_count']:<6}")

    context_lines.append(f"\n### 用户需求\n{user_request}")

    full_context = "\n".join(context_lines)

    try:
        llm_result = _call_llm(full_context, system_prompt)
        print(f"[survey] LLM返回原始结果前200字: {llm_result[:200]}")
    except Exception as exc:
        print(f"[survey] LLM调用失败: {exc}")
        return {"status": "error", "message": f"AI 分析失败: {exc}"}

    # 解析 JSON
    plan_data = _parse_llm_json(llm_result)
    if not plan_data:
        # 解析失败时使用确定性规则生成方案
        return _generate_deterministic_plan(user_request, context_data)

    # 验证并保存方案
    return _save_plan_to_db(user_request, plan_data)


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

def _get_plan_full(plan_id: int) -> Optional[Dict[str, Any]]:
    """获取完整方案（含建议）"""
    with _get_conn() as conn:
        plan = conn.execute(
            "SELECT * FROM field_survey_plans WHERE plan_id=?",
            (plan_id,),
        ).fetchone()
        if not plan:
            return None

        recommendations = conn.execute(
            "SELECT * FROM survey_recommendations WHERE plan_id=? ORDER BY sort_order",
            (plan_id,),
        ).fetchall()

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
    """更新单条建议的状态"""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE survey_recommendations SET status=?, completed_at=CASE WHEN ? THEN datetime('now','localtime') ELSE NULL END, obs_id=? WHERE rec_id=?",
            (status, status == "completed", obs_id, rec_id),
        )
        conn.commit()

    # 获取 plan_id 更新进度
    with _get_conn() as conn:
        rec = conn.execute(
            "SELECT plan_id FROM survey_recommendations WHERE rec_id=?",
            (rec_id,),
        ).fetchone()
    if rec:
        _update_plan_completed_count(rec["plan_id"])

    return {"status": "success", "rec_id": rec_id, "new_status": status}


# =============================================================================
# 报告生成
# =============================================================================

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
