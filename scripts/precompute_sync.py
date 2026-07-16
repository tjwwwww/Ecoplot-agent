# -*- coding: utf-8 -*-
"""
同步预计算脚本：遍历所有样方，调用现有工具计算指标并写入 data/cache/subplot_{id}.json
用法：python scripts/precompute_sync.py [--limit N]
"""
import os
import sys
import json
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

sys.path.insert(0, BASE_DIR)
import forestry_spatial_tools as fst
from forestry_visualization_engine import ForestryDataRepository
import sqlite3
import forestry_spatial_tools as fst


def compute_and_write(subplot_id: str):
    start = time.time()
    try:
        stand = fst.tool_calc_stand_structure_metrics(str(subplot_id))
        morph = fst.tool_calc_tree_morphology_metrics(str(subplot_id))
        vol = fst.tool_calc_volume_metrics(str(subplot_id))
        div = fst.tool_calc_species_diversity_metrics(str(subplot_id))
        dead = fst.tool_calc_deadwood_metrics(str(subplot_id))
    except Exception as e:
        payload = {"error": str(e)}
        status = "failed"
    else:
        def _parse(s):
            try:
                return json.loads(s)
            except Exception:
                return {"raw": str(s)}
        payload = {
            "stand_structure": _parse(stand),
            "morphology": _parse(morph),
            "volume": _parse(vol),
            "diversity": _parse(div),
            "deadwood": _parse(dead),
        }
        status = "success"

    duration = round(time.time() - start, 3)
    wrapper = {
        "subplot_id": str(subplot_id),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "duration_s": duration,
        "status": status,
        "payload": payload,
    }

    out_path = os.path.join(CACHE_DIR, f"subplot_{subplot_id}.json")
    # attach per-tree computed metrics into the cache for quick frontend rendering
    try:
        db = getattr(fst, 'DB_PATH', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'qilian_forest.db'))
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT tree_id, species, tree_dbh_cm, tree_height_m, tree_x_m, tree_y_m, volume_m3 FROM tree_observations WHERE subplot_id = ?", (str(subplot_id).strip(),))
        tree_rows = cur.fetchall()
        conn.close()
        tree_metrics = []
        for tr in tree_rows:
            tree_id = str(tr[0])
            species = str(tr[1] or "")
            dbh = float(tr[2] or 0)
            height = float(tr[3] or 0)
            x = float(tr[4]) if tr[4] is not None else None
            y = float(tr[5]) if tr[5] is not None else None
            legacy_vol = float(tr[6] or 0)
            try:
                metrics = fst.calc_tree_metrics(species, dbh, height)
                computed_vol = float(metrics.get("volume_m3", 0.0))
                computed_ba = float(metrics.get("basal_area_m2", 0.0))
            except Exception:
                computed_vol = legacy_vol
                computed_ba = 0.0
            hdr = round((height / (dbh / 100.0)), 2) if dbh and height else None
            quality_flag = None
            if dbh <= 0 or height <= 0:
                quality_flag = "missing_dbh_or_height"
            tree_metrics.append({
                "tree_id": tree_id,
                "species": species,
                "dbh_cm": dbh,
                "height_m": height,
                "x_m": x,
                "y_m": y,
                "computed_volume_m3": computed_vol,
                "legacy_volume_m3": legacy_vol,
                "computed_basal_area_m2": computed_ba,
                "hdr": hdr,
                "quality_flag": quality_flag
            })
        # attach hegyi competition overview if available
        try:
            hegyi_raw = fst.tool_calc_hegyi_competition(str(subplot_id))
            try:
                hegyi_obj = json.loads(hegyi_raw)
            except Exception:
                hegyi_obj = {"raw": hegyi_raw}
        except Exception:
            hegyi_obj = {"error": "hegyi computation failed"}
        wrapper["payload"]["tree_metrics"] = tree_metrics
        wrapper["payload"]["hegyi_overview"] = hegyi_obj
    except Exception:
        # if tree-level enrichment fails, continue and write the main wrapper
        pass

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(wrapper, f, ensure_ascii=False, indent=2)
    print(f"WROTE {out_path} ({status}, {duration}s)")


def main(limit=None):
    repo = ForestryDataRepository()
    sids = list(repo.subplots.keys())
    if limit:
        sids = sids[:limit]
    print(f"Precomputing {len(sids)} subplots to {CACHE_DIR} ...")
    for sid in sids:
        compute_and_write(sid)


if __name__ == "__main__":
    limit = None
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
        except Exception:
            limit = None
    main(limit=limit)
