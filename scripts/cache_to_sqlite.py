# -*- coding: utf-8 -*-
"""
将 data/cache 中的 subplot_{id}.json 和原始 tree_observations 计算的单木指标写入一个轻量 SQLite 数据库
用法：python scripts/cache_to_sqlite.py
"""
import os
import sys
import json
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
OUT_DB = os.path.join(BASE_DIR, "data", "cache_metrics.db")

sys.path.insert(0, BASE_DIR)
import forestry_spatial_tools as fst


def ensure_schema(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS subplot_metrics (
        subplot_id TEXT PRIMARY KEY,
        generated_at TEXT,
        total_volume_m3 REAL,
        volume_per_ha REAL,
        density_per_ha REAL,
        mean_dbh_cm REAL,
        mean_hdr REAL,
        shannon_index REAL,
        payload_json TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tree_metrics (
        tree_id TEXT PRIMARY KEY,
        subplot_id TEXT,
        species TEXT,
        dbh_cm REAL,
        height_m REAL,
        volume_m3 REAL,
        basal_area_m2 REAL,
        hdr REAL,
        risk_level TEXT,
        payload_json TEXT
    )
    """)
    conn.commit()


def load_subplot_cache_files():
    files = [f for f in os.listdir(CACHE_DIR) if f.startswith("subplot_") and f.endswith(".json")]
    for fn in files:
        path = os.path.join(CACHE_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            yield data
        except Exception as e:
            print(f"WARN: cannot read {path}: {e}")


def insert_subplot(conn: sqlite3.Connection, data: dict):
    sid = data.get("subplot_id")
    generated_at = data.get("generated_at")
    payload = data.get("payload", {})
    # try to extract common fields from payload
    total_vol = None
    vol_per_ha = None
    density = None
    mean_dbh = None
    mean_hdr = None
    shannon = None

    try:
        vol_block = payload.get("volume", {})
        vol_values = vol_block.get("volume_outputs", {}) if isinstance(vol_block, dict) else {}
        total_vol = vol_values.get("total_subplot_volume_m3") or vol_values.get("total_volume_m3")
    except Exception:
        total_vol = None

    try:
        stand_block = payload.get("stand_structure", {})
        metrics = stand_block.get("metrics", {}) if isinstance(stand_block, dict) else {}
        density = metrics.get("stand_density_per_ha")
        mean_dbh = metrics.get("arithmetic_mean_dbh_cm") or metrics.get("mean_dbh_cm")
    except Exception:
        pass

    try:
        morph = payload.get("morphology", {})
        stand_summary = morph.get("stand_summary", {}) if isinstance(morph, dict) else {}
        mean_hdr = stand_summary.get("mean_height_diameter_ratio_hdr") or stand_summary.get("mean_hdr")
    except Exception:
        pass

    try:
        div = payload.get("diversity", {})
        ind = div.get("indicator_values", {}) if isinstance(div, dict) else {}
        shannon = ind.get("tree_shannon_stem_based") or ind.get("shannon_index")
    except Exception:
        pass

    cur = conn.cursor()
    cur.execute(
        "REPLACE INTO subplot_metrics (subplot_id, generated_at, total_volume_m3, volume_per_ha, density_per_ha, mean_dbh_cm, mean_hdr, shannon_index, payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            sid,
            generated_at,
            float(total_vol) if total_vol is not None else None,
            float(vol_per_ha) if vol_per_ha is not None else None,
            float(density) if density is not None else None,
            float(mean_dbh) if mean_dbh is not None else None,
            float(mean_hdr) if mean_hdr is not None else None,
            float(shannon) if shannon is not None else None,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    conn.commit()


def insert_tree_metrics_for_subplot(conn: sqlite3.Connection, subplot_id: str):
    # read trees from original DB and compute per-tree metrics
    src_db = fst.DB_PATH
    if not os.path.exists(src_db):
        print(f"WARN: source DB not found: {src_db}")
        return
    s_conn = sqlite3.connect(src_db)
    s_cur = s_conn.cursor()
    s_cur.execute("SELECT tree_id, species, tree_dbh_cm, tree_height_m, tree_x_m, tree_y_m FROM tree_observations WHERE subplot_id = ? AND tree_dbh_cm > 0", (str(subplot_id).strip(),))
    rows = s_cur.fetchall()
    s_conn.close()

    cur = conn.cursor()
    for r in rows:
        tree_id, species, dbh, height, x, y = r[0], r[1], float(r[2] or 0), float(r[3] or 0), r[4], r[5]
        metrics = fst.calc_tree_metrics(str(species or ""), dbh, height)
        vol = float(metrics.get("volume_m3", 0.0))
        ba = float(metrics.get("basal_area_m2", 0.0))
        biomass = metrics.get("biomass_kg")
        hdr = (100.0 * height / dbh) if dbh > 0 else None
        risk = "高" if hdr and hdr > 80 else ("中" if hdr and hdr > 65 else "低")
        payload = {"computed": metrics}
        cur.execute(
            "REPLACE INTO tree_metrics (tree_id, subplot_id, species, dbh_cm, height_m, volume_m3, basal_area_m2, hdr, risk_level, payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                str(tree_id), str(subplot_id), str(species), dbh, height, vol, ba, float(hdr) if hdr is not None else None, risk, json.dumps(payload, ensure_ascii=False)
            ),
        )
    conn.commit()


def main():
    conn = sqlite3.connect(OUT_DB)
    ensure_schema(conn)
    files = list(load_subplot_cache_files())
    print(f"Found {len(files)} cached subplot files")
    for data in files:
        sid = data.get("subplot_id")
        try:
            insert_subplot(conn, data)
            insert_tree_metrics_for_subplot(conn, sid)
            print(f"Inserted metrics for subplot {sid}")
        except Exception as e:
            print(f"ERROR inserting {sid}: {e}")
    conn.close()
    print(f"Wrote cache metrics DB: {OUT_DB}")


if __name__ == "__main__":
    main()
