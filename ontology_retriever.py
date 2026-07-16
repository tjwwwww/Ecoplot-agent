# -*- coding: utf-8 -*-
"""
ontology_retriever.py
=====================
本体/知识注册表/数据库实体候选召回模块。

作用：
- 不穷举用户问法；
- 根据用户自然语言，从本体、知识注册表和 SQLite 数据库中召回相关候选；
- 交给 semantic_planner 由 LLM 结合候选自主生成 SemanticPlan。

放置位置：项目根目录，与 agent2.py、api.py 同级。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

BASE_DIR = Path(__file__).resolve().parent


def _safe_lower(text: Any) -> str:
    return str(text or "").strip().lower()


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_db_path() -> Path:
    env_path = os.getenv("FORESTRY_SQLITE_DB")
    if env_path:
        return Path(env_path)
    try:
        import forestry_spatial_tools as fst  # type: ignore
        return Path(getattr(fst, "DB_PATH", BASE_DIR / "data" / "qilian_forest.db"))
    except Exception:
        return BASE_DIR / "data" / "qilian_forest.db"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    if not _table_exists(conn, table):
        return []
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


@dataclass
class Candidate:
    type: str
    name: str
    id: Optional[str] = None
    source: str = "unknown"
    score: float = 0.0
    payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "type": self.type,
            "name": self.name,
            "id": self.id,
            "source": self.source,
            "score": round(float(self.score), 4),
        }
        if self.payload is not None:
            data["payload"] = self.payload
        return data


class OntologyRetriever:
    """轻量候选召回器。第一版采用关键词/包含匹配，后续可接 embedding/Neo4j。"""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        ontology_paths: Optional[List[Path]] = None,
        registry_paths: Optional[List[Path]] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.base_dir = Path(base_dir or BASE_DIR)
        self.db_path = Path(db_path or _get_db_path())
        self.ontology_paths = ontology_paths or self._default_ontology_paths()
        self.registry_paths = registry_paths or self._default_registry_paths()
        self._ontology_items: Optional[List[Candidate]] = None
        self._db_summary_cache: Optional[Dict[str, Any]] = None

    def _default_ontology_paths(self) -> List[Path]:
        names = [
            "ontology/qilian_ontology.yaml",
            "ontology/ontology.yaml",
            "祁连山森林样地结构质量诊断本体_v0.2.yaml",
            "祁连山森林样地结构质量诊断本体_v0.1.yaml",
            "Pasted code(2).yaml",
        ]
        return [self.base_dir / name for name in names]

    def _default_registry_paths(self) -> List[Path]:
        names = [
            "ontology/forestry_knowledge_registry.yaml",
            "ontology/knowledge_registry.yaml",
            "forestry_knowledge_registry.yaml",
            "Pasted code(3).yaml",
        ]
        return [self.base_dir / name for name in names]

    def _score(self, question: str, haystack: str, name: str = "") -> float:
        q = _safe_lower(question)
        h = _safe_lower(haystack)
        n = _safe_lower(name)
        if not q or not h:
            return 0.0
        score = 0.0
        if n and n in q:
            score += 3.0
        if q in h:
            score += 2.0
        # 中文短词/英文token粗召回
        tokens = [t for t in re.split(r"[\s,，。；;:：/\\()（）\[\]{}]+", q) if len(t) >= 2]
        for token in tokens:
            if token and token in h:
                score += 0.5
        # 对 HDR/Hegyi 等短英文保留
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_'-]*", q):
            if token.lower() in h:
                score += 0.8
        return score

    def _index_yaml_items(self) -> List[Candidate]:
        items: List[Candidate] = []

        def add_item(kind: str, item: Dict[str, Any], source: str) -> None:
            name = str(
                item.get("name_cn")
                or item.get("name_zh")
                or item.get("label")
                or item.get("class_id")
                or item.get("indicator_id")
                or item.get("knowledge_id")
                or item.get("variable_id")
                or item.get("tool_id")
                or item.get("id")
                or ""
            ).strip()
            cid = str(
                item.get("class_id")
                or item.get("indicator_id")
                or item.get("knowledge_id")
                or item.get("variable_id")
                or item.get("tool_id")
                or item.get("id")
                or ""
            ).strip() or None
            if not name and not cid:
                return
            items.append(Candidate(kind, name or str(cid), cid, source, 0.0, item))

        for path in self.ontology_paths:
            data = _load_yaml(path)
            if not data:
                continue
            for cls in data.get("classes", []) or []:
                if isinstance(cls, dict):
                    add_item("OntologyClass", cls, path.name)
            for key in [
                "canonical_tree_observation_fields",
                "relations",
                "object_properties",
                "data_properties",
            ]:
                for item in data.get(key, []) or []:
                    if isinstance(item, dict):
                        add_item(f"Ontology:{key}", item, path.name)

        for path in self.registry_paths:
            data = _load_yaml(path)
            if not data:
                continue
            group_type = {
                "variables": "VariableDefinition",
                "indicators": "IndicatorDefinition",
                "formulas": "FormulaDefinition",
                "models": "EmpiricalModelDefinition",
                "diagnostic_rules": "DiagnosticRule",
                "visualization_tools": "VisualizationTool",
            }
            for group, kind in group_type.items():
                for item in data.get(group, []) or []:
                    if isinstance(item, dict):
                        add_item(kind, item, path.name)
        return items

    @property
    def ontology_items(self) -> List[Candidate]:
        if self._ontology_items is None:
            self._ontology_items = self._index_yaml_items()
        return self._ontology_items

    def _db_candidates(self, question: str, limit: int = 30) -> Tuple[List[Candidate], Dict[str, Any]]:
        candidates: List[Candidate] = []
        summary: Dict[str, Any] = {"db_path": str(self.db_path), "exists": self.db_path.exists()}
        if not self.db_path.exists():
            return candidates, summary
        q = str(question or "")
        try:
            with sqlite3.connect(self.db_path) as conn:
                if not _table_exists(conn, "tree_observations"):
                    summary["tree_observations"] = "missing"
                    return candidates, summary
                cols = _table_columns(conn, "tree_observations")
                summary["tree_observations_columns"] = cols[:40]
                # species / taxon
                if "species" in cols:
                    rows = conn.execute(
                        "SELECT species, COUNT(*) AS n, COUNT(DISTINCT subplot_id) "
                        "FROM tree_observations WHERE species IS NOT NULL AND TRIM(species)<>'' "
                        "GROUP BY species ORDER BY n DESC LIMIT 500"
                    ).fetchall()
                    species_names = [str(r[0]) for r in rows]
                    summary["species_count"] = len(rows)
                    summary["species_examples"] = species_names[:20]
                    for sp, n, subplot_n in rows:
                        sp_name = str(sp)
                        score = self._score(q, sp_name, sp_name)
                        if score > 0 or sp_name in q:
                            candidates.append(
                                Candidate(
                                    "Taxon", sp_name, None, "database:tree_observations.species",
                                    max(score, 3.5),
                                    {"tree_count": int(n or 0), "subplot_count": int(subplot_n or 0)},
                                )
                            )
                # subplot id
                subplot_ids = set(re.findall(r"(?<!\d)(\d{4})(?!\d)", q))
                if "subplot_id" in cols and subplot_ids:
                    placeholders = ",".join("?" for _ in subplot_ids)
                    rows = conn.execute(
                        f"SELECT subplot_id, COUNT(*) FROM tree_observations WHERE subplot_id IN ({placeholders}) GROUP BY subplot_id",
                        tuple(subplot_ids),
                    ).fetchall()
                    for sid, n in rows:
                        candidates.append(
                            Candidate("Subplot", str(sid), str(sid), "database:tree_observations.subplot_id", 4.0, {"tree_count": int(n or 0)})
                        )
                # tree id rough candidate: QLS01200016 or long id containing letters/digits
                tree_like = re.findall(r"[A-Za-z]{2,}\d{4,}|\d{4}[-_]?\d{2,}", q)
                if "tree_id" in cols and tree_like:
                    for tid in tree_like[:10]:
                        row = conn.execute(
                            "SELECT tree_id, subplot_id, species FROM tree_observations WHERE tree_id=? LIMIT 1",
                            (tid,),
                        ).fetchone()
                        if row:
                            candidates.append(
                                Candidate("TreeIndividual", str(row[0]), str(row[0]), "database:tree_observations.tree_id", 4.5,
                                          {"subplot_id": str(row[1]), "species": str(row[2] or "")})
                            )
        except Exception as exc:
            summary["error"] = str(exc)
        return candidates[:limit], summary

    def retrieve(self, question: str, context: Optional[Dict[str, Any]] = None, limit: int = 20) -> Dict[str, Any]:
        question = str(question or "")
        context = dict(context or {})
        all_candidates: List[Candidate] = []
        db_candidates, db_summary = self._db_candidates(question, limit=limit)
        all_candidates.extend(db_candidates)

        for item in self.ontology_items:
            haystack = _json_dumps(item.payload or {}) + " " + item.name + " " + str(item.id or "")
            score = self._score(question, haystack, item.name)
            if score > 0:
                all_candidates.append(Candidate(item.type, item.name, item.id, item.source, score, item.payload))

        # 页面上下文作为弱候选，不强制覆盖用户问题
        if context.get("current_subplot_id"):
            sid = str(context.get("current_subplot_id"))
            all_candidates.append(Candidate("Subplot", sid, sid, "page_context", 1.2, {"role": "ambient_context"}))
        if context.get("current_tree_id"):
            tid = str(context.get("current_tree_id"))
            all_candidates.append(Candidate("TreeIndividual", tid, tid, "page_context", 1.2, {"role": "ambient_context"}))

        # 去重，保留最高分
        merged: Dict[Tuple[str, str, str], Candidate] = {}
        for c in all_candidates:
            key = (c.type, c.name, str(c.id or ""))
            if key not in merged or c.score > merged[key].score:
                merged[key] = c
        ranked = sorted(merged.values(), key=lambda c: c.score, reverse=True)

        def take(types: Iterable[str], n: int) -> List[Dict[str, Any]]:
            type_set = set(types)
            return [c.to_dict() for c in ranked if c.type in type_set][:n]

        entity_types = {"Taxon", "Subplot", "TreeIndividual", "MonitoringPlot"}
        indicator_types = {"IndicatorDefinition", "VariableDefinition"}
        formula_types = {"FormulaDefinition", "EmpiricalModelDefinition"}
        tool_types = {"VisualizationTool"}
        rule_types = {"DiagnosticRule"}
        ontology_types = {"OntologyClass", "Ontology:canonical_tree_observation_fields", "Ontology:relations", "Ontology:object_properties", "Ontology:data_properties"}

        # 根据候选和问句粗略给出能力候选；最终仍由 LLM 规划，不是硬映射
        q = question.lower()
        capability_candidates = []
        if any(w in question for w in ["图", "画", "分布", "箱线", "散点", "空间"]):
            capability_candidates.append("visualization_generation")
        if any(w in question for w in ["报告", "文档", "汇报", "导出", "任务单"]):
            capability_candidates.append("report_generation")
        if any(w in question for w in ["公式", "怎么算", "含义", "是什么", "代表", "指标", "HDR", "Hegyi"]):
            capability_candidates.append("indicator_explanation")
        if any(w in question for w in ["现场", "外业", "复测", "核查", "看什么", "候选"]):
            capability_candidates.append("field_inspection_planning")
        if any(w in question for w in ["比较", "对比", "差异", "更", "哪个"]):
            capability_candidates.append("object_comparison")
        if not capability_candidates:
            capability_candidates.append("object_status_analysis")
        capability_candidates.append("general_chat")

        return {
            "question": question,
            "context": context,
            "entity_candidates": take(entity_types, limit),
            "ontology_candidates": take(ontology_types, limit),
            "indicator_candidates": take(indicator_types, limit),
            "formula_candidates": take(formula_types, limit),
            "diagnostic_rule_candidates": take(rule_types, limit),
            "tool_candidates": take(tool_types, limit),
            "capability_candidates": capability_candidates[:10],
            "all_candidates_preview": [c.to_dict() for c in ranked[:limit]],
            "data_summary": db_summary,
        }


_DEFAULT_RETRIEVER: Optional[OntologyRetriever] = None


def get_default_retriever() -> OntologyRetriever:
    global _DEFAULT_RETRIEVER
    if _DEFAULT_RETRIEVER is None:
        _DEFAULT_RETRIEVER = OntologyRetriever()
    return _DEFAULT_RETRIEVER


def retrieve_ontology_candidates(question: str, context: Optional[Dict[str, Any]] = None, limit: int = 20) -> Dict[str, Any]:
    return get_default_retriever().retrieve(question, context=context, limit=limit)
