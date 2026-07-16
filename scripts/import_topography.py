# -*- coding: utf-8 -*-
"""
导入地形/单株位置数据到 SQLite。
数据来源：data/祁连山国家公园乔木林样地数据资料汇总/气候数据/大样地每木调查.csv
"""
import csv
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "qilian_forest.db")
CSV_PATH = os.path.normpath(os.path.join(
    BASE_DIR,
    "..",
    "..",
    "data",
    "祁连山国家公园乔木林样地数据资料汇总",
    "气候数据",
    "大样地每木调查.csv",
))

TABLE_NAME = "topography_observations"

MISSING_VALUES = {"-9999", "-9999.0", "-9999.0000000", "\\N", "", None}


def to_float(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in MISSING_VALUES:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text not in MISSING_VALUES else None


def ensure_schema(conn: sqlite3.Connection):
    conn.execute(f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        tree_id TEXT PRIMARY KEY,
        subplot_id TEXT,
        plot_id TEXT,
        x_m REAL,
        y_m REAL,
        elevation_m REAL,
        slope_degree REAL,
        aspect_degree REAL,
        slope_position TEXT,
        source_file TEXT,
        imported_at TEXT
    )
    """)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_subplot ON {TABLE_NAME}(subplot_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_elevation ON {TABLE_NAME}(elevation_m)")
    conn.commit()


def import_topography_data():
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"目标数据库不存在: {DB_PATH}")
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"地形数据 CSV 不存在: {CSV_PATH}")

    with sqlite3.connect(DB_PATH) as conn:
        ensure_schema(conn)
        cursor = conn.cursor()

        with open(CSV_PATH, "r", encoding="utf-8", errors="ignore") as csvfile:
            reader = csv.DictReader(csvfile)
            if reader.fieldnames is None:
                raise ValueError("无法读取 CSV 表头")
            inserted = 0
            skipped = 0
            for row in reader:
                tree_id = normalize_text(row.get("树木编") or row.get("tree_id") or row.get("TREE_ID"))
                subplot_id = normalize_text(row.get("样方号") or row.get("subplot_id"))
                plot_id = normalize_text(row.get("样地号") or row.get("plot_id"))
                x_m = to_float(row.get("X坐标") or row.get("x_m") or row.get("x"))
                y_m = to_float(row.get("Y坐标") or row.get("y_m") or row.get("y"))
                elevation_m = to_float(row.get("海拔") or row.get("elevation_m") or row.get("elevation"))
                slope_degree = to_float(row.get("坡度") or row.get("slope_degree") or row.get("slope"))
                aspect_degree = to_float(row.get("坡向") or row.get("aspect_degree") or row.get("aspect"))
                slope_position = normalize_text(row.get("坡位分") or row.get("坡位") or row.get("slope_position"))
                health_status = normalize_text(row.get("健康状") or row.get("健康状况") or row.get("health_status"))

                if not tree_id:
                    skipped += 1
                    continue

                cursor.execute(
                    f"REPLACE INTO {TABLE_NAME} (tree_id, subplot_id, plot_id, x_m, y_m, elevation_m, slope_degree, aspect_degree, slope_position, source_file, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        tree_id,
                        subplot_id,
                        plot_id,
                        x_m,
                        y_m,
                        elevation_m,
                        slope_degree,
                        aspect_degree,
                        slope_position,
                        os.path.basename(CSV_PATH),
                        datetime.utcnow().isoformat() + "Z",
                    ),
                )
                inserted += 1

        conn.commit()

    print(f"导入完成: {inserted} 条记录, 跳过 {skipped} 条无 tree_id 的行")
    print(f"数据库路径: {DB_PATH}")
    print(f"表名: {TABLE_NAME}")


if __name__ == "__main__":
    import_topography_data()
