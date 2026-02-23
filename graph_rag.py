"""
graph_rag.py — Production-Grade Schema GraphRAG
=================================================
Builds a proper knowledge graph from any database schema and provides
graph-based retrieval for mapping natural language attributes to columns.

Graph Structure:
  ┌──────────┐     HAS_COLUMN      ┌──────────┐     HAS_TYPE      ┌──────────┐
  │  TABLE   │────────────────────→│  COLUMN  │────────────────→  │ DATATYPE │
  └──────────┘                     └──────────┘                   └──────────┘
       │                                │  │                          
       │ HAS_PK                         │  │ HAS_PATTERN     ┌──────────────┐
       ↓                                │  └───────────────→  │VALUE_PATTERN │
  ┌──────────┐     FK_TO               │                     └──────────────┘
  │  COLUMN  │←──────────────────      │  BELONGS_TO_CONCEPT
  └──────────┘                   │     ↓
                                 │  ┌──────────────┐
                                 └──│   CONCEPT    │  (email, phone, money, etc.)
                                    └──────────────┘
  Cross-table edges:
    COLUMN ──FK_TO──→ COLUMN          (foreign key relationships)
    COLUMN ──SIMILAR_NAME──→ COLUMN   (columns with similar names across tables)
    COLUMN ──SAME_CONCEPT──→ COLUMN   (columns sharing a semantic concept)
    TABLE  ──RELATED_TO──→ TABLE      (tables linked by FK chains)

Retrieval Pipeline:
  1. Parse query into tokens + detect concept + detect value pattern
  2. Graph entry points: concept nodes, pattern nodes, direct name matches
  3. Multi-hop traversal: expand via SAME_CONCEPT, FK_TO, SIMILAR_NAME edges
  4. Score each candidate column using graph distance + embedding similarity + value match
  5. Return ranked results with full graph context (FK paths, related tables, etc.)
"""

import sqlite3
import re
import json
import math
import hashlib
import os
import pickle
import logging
from dataclasses import dataclass, field
from typing import Optional, Any
from collections import defaultdict

import networkx as nx
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)

# ==========================================================================
# VALUE PATTERN DETECTION
# ==========================================================================

PATTERN_RULES = [
    ("email",          r"^[\w.+-]+@[\w-]+\.[\w.]+$"),
    ("url",            r"^https?://"),
    ("phone",          r"^\+?[\d\s\-().]{7,15}$"),
    ("date_iso",       r"^\d{4}-\d{2}-\d{2}"),
    ("date_us",        r"^\d{1,2}/\d{1,2}/\d{2,4}$"),
    ("uuid",           r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
    ("zipcode",        r"^\d{5}(-\d{4})?$"),
    ("code_alpha_num", r"^[A-Za-z]{1,5}[\-_]\d{2,}$"),
    ("boolean",        r"^(true|false|yes|no|0|1|active|inactive)$"),
    ("decimal",        r"^-?\$?[\d,]+\.\d+$"),
    ("integer",        r"^-?\d+$"),
    ("text_long",      None),  # fallback: 3+ words
    ("text_short",     None),  # fallback
]


def detect_patterns(value: str) -> list[str]:
    """Detect ALL matching patterns for a value (a value can match multiple)."""
    v = str(value).strip()
    if not v:
        return ["empty"]
    patterns = []
    v_lower = v.lower()
    for pname, regex in PATTERN_RULES:
        if regex is None:
            continue
        if re.match(regex, v_lower if pname == "boolean" else v):
            patterns.append(pname)
    if not patterns:
        patterns.append("text_long" if len(v.split()) >= 3 else "text_short")
    return patterns


def compute_value_fingerprint(values: list[str]) -> dict:
    """Compute a statistical fingerprint of a set of values."""
    if not values:
        return {"count": 0, "patterns": [], "avg_len": 0, "unique_ratio": 0}
    patterns = defaultdict(int)
    lengths = []
    for v in values:
        for p in detect_patterns(v):
            patterns[p] += 1
        lengths.append(len(str(v)))
    return {
        "count": len(values),
        "patterns": dict(patterns),
        "avg_len": sum(lengths) / len(lengths),
        "unique_ratio": len(set(values)) / len(values),
        "min_len": min(lengths),
        "max_len": max(lengths),
    }


# ==========================================================================
# SEMANTIC CONCEPT TAXONOMY
# ==========================================================================

CONCEPT_TAXONOMY = {
    # Concept name → (keywords in column names, keywords in values/context, related concepts)
    "email": {
        "name_keywords": ["email", "mail", "e_mail", "email_address"],
        "value_patterns": ["email"],
        "related": ["contact", "person"],
    },
    "phone": {
        "name_keywords": ["phone", "tel", "telephone", "mobile", "cell", "fax", "contact_number"],
        "value_patterns": ["phone"],
        "related": ["contact", "person"],
    },
    "person_name": {
        "name_keywords": ["first_name", "last_name", "name", "fname", "lname", "full_name",
                          "given_name", "surname", "middle_name", "prefix", "suffix", "title"],
        "value_patterns": ["text_short"],
        "related": ["person"],
    },
    "address": {
        "name_keywords": ["address", "addr", "street", "city", "state", "zip", "zip_code",
                          "postal", "postal_code", "country", "region", "province", "county",
                          "apartment", "suite", "unit", "building"],
        "value_patterns": ["text_short", "zipcode"],
        "related": ["location"],
    },
    "identifier": {
        "name_keywords": ["id", "pk", "key", "code", "ref", "reference", "sku", "uuid",
                          "guid", "number", "no", "num", "serial", "barcode", "external_id"],
        "value_patterns": ["integer", "uuid", "code_alpha_num"],
        "related": [],
    },
    "datetime": {
        "name_keywords": ["date", "time", "datetime", "timestamp", "created", "updated",
                          "modified", "created_at", "updated_at", "deleted_at", "expires",
                          "start_date", "end_date", "birth_date", "due_date"],
        "value_patterns": ["date_iso", "date_us"],
        "related": ["temporal"],
    },
    "money": {
        "name_keywords": ["price", "cost", "amount", "total", "fee", "salary", "wage",
                          "revenue", "balance", "payment", "charge", "discount", "tax",
                          "subtotal", "gross", "net", "unit_price", "msrp"],
        "value_patterns": ["decimal"],
        "related": ["quantity"],
    },
    "quantity": {
        "name_keywords": ["qty", "quantity", "count", "stock", "inventory", "level",
                          "units", "number", "num", "total_count", "available",
                          "stock_quantity", "on_hand", "reserved", "allocated"],
        "value_patterns": ["integer"],
        "related": ["money"],
    },
    "status": {
        "name_keywords": ["status", "state", "stage", "phase", "flag", "active",
                          "enabled", "is_active", "is_deleted", "is_enabled",
                          "workflow_status", "order_status", "payment_status"],
        "value_patterns": ["text_short", "boolean"],
        "related": [],
    },
    "description": {
        "name_keywords": ["desc", "description", "note", "notes", "comment", "comments",
                          "remarks", "bio", "summary", "detail", "details", "body",
                          "content", "text", "message"],
        "value_patterns": ["text_long"],
        "related": [],
    },
    "category": {
        "name_keywords": ["category", "type", "kind", "group", "class", "tier",
                          "segment", "classification", "department", "division", "tag"],
        "value_patterns": ["text_short"],
        "related": [],
    },
    "url": {
        "name_keywords": ["url", "link", "href", "website", "uri", "image_url",
                          "avatar", "photo_url", "redirect"],
        "value_patterns": ["url"],
        "related": [],
    },
    "percentage": {
        "name_keywords": ["percent", "pct", "rate", "ratio", "margin", "discount_rate",
                          "tax_rate", "interest_rate", "completion"],
        "value_patterns": ["decimal"],
        "related": ["money"],
    },
}


def classify_concepts(column_name: str, table_name: str, value_patterns: list[str]) -> list[str]:
    """Classify a column into semantic concepts using name + value patterns.
    
    Uses strict matching: column name keywords are weighted 3x over table name,
    and only top concepts that exceed a threshold are kept.
    """
    col_tokens = set(re.split(r"[_\-\s]+", column_name.lower()))
    table_tokens = set(re.split(r"[_\-\s]+", table_name.lower()))
    col_lower = column_name.lower()

    concepts = []
    for concept, rules in CONCEPT_TAXONOMY.items():
        score = 0
        # Column name keyword match (strong signal)
        for kw in rules["name_keywords"]:
            if kw in col_lower:
                score += 5  # exact substring in column name
            else:
                kw_tokens = set(kw.split("_"))
                col_overlap = kw_tokens & col_tokens
                if col_overlap:
                    score += len(col_overlap) * 2

        # Table name match (weaker signal — only if column also partially matches)
        for kw in rules["name_keywords"]:
            kw_tokens = set(kw.split("_"))
            if kw_tokens & table_tokens and score > 0:
                score += 1  # table context boost only if column already matched

        # Value pattern match (supporting signal only)
        for vp in rules["value_patterns"]:
            if vp in value_patterns:
                score += 1

        if score >= 3:  # threshold: must have real column name evidence
            concepts.append((concept, score))

    concepts.sort(key=lambda x: x[1], reverse=True)
    # Keep only top concepts (max 3) to avoid over-connection
    return [c[0] for c in concepts[:3]]


# ==========================================================================
# SCHEMA EXTRACTOR (supports SQLite, extensible to PostgreSQL/MySQL)
# ==========================================================================

class SchemaExtractor:
    """Extract complete schema metadata from a database."""

    def __init__(self, db_path: str, db_type: str = "sqlite", sample_limit: int = 50):
        self.db_path = db_path
        self.db_type = db_type
        self.sample_limit = sample_limit

    def extract(self) -> dict:
        """Returns structured schema with tables, columns, FKs, indexes, samples."""
        if self.db_type == "sqlite":
            return self._extract_sqlite()
        raise NotImplementedError(f"DB type '{self.db_type}' — implement extract method")

    def _extract_sqlite(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        )
        table_names = [r[0] for r in cur.fetchall()]

        schema = {"tables": {}, "foreign_keys": [], "indexes": []}

        for tname in table_names:
            # Column info
            cur.execute(f"PRAGMA table_info('{tname}');")
            columns = {}
            pks = []
            for cid, cname, ctype, notnull, default, is_pk in cur.fetchall():
                # Sample values with diversity
                samples = self._get_diverse_samples(cur, tname, cname)

                # Value fingerprint
                fingerprint = compute_value_fingerprint(samples)

                # Unique check
                unique = self._is_unique_column(cur, tname, cname)

                columns[cname] = {
                    "type": ctype or "TEXT",
                    "nullable": not bool(notnull),
                    "primary_key": bool(is_pk),
                    "unique": unique,
                    "default": str(default) if default else None,
                    "samples": samples,
                    "fingerprint": fingerprint,
                }
                if is_pk:
                    pks.append(cname)

            # Foreign keys
            cur.execute(f"PRAGMA foreign_key_list('{tname}');")
            for fk in cur.fetchall():
                schema["foreign_keys"].append({
                    "from_table": tname, "from_column": fk[3],
                    "to_table": fk[2], "to_column": fk[4],
                })

            # Row count
            cur.execute(f'SELECT COUNT(*) FROM "{tname}";')
            row_count = cur.fetchone()[0]

            # Indexes
            cur.execute(f"PRAGMA index_list('{tname}');")
            for idx in cur.fetchall():
                cur.execute(f"PRAGMA index_info('{idx[1]}');")
                idx_cols = [ic[2] for ic in cur.fetchall()]
                schema["indexes"].append({
                    "table": tname, "name": idx[1],
                    "columns": idx_cols, "unique": bool(idx[2]),
                })

            schema["tables"][tname] = {
                "columns": columns,
                "row_count": row_count,
                "primary_keys": pks,
            }

        conn.close()
        return schema

    def _get_diverse_samples(self, cur, table: str, column: str) -> list[str]:
        """Get diverse sample values — not just the first N."""
        samples = []
        try:
            # Get distinct values
            cur.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL LIMIT {self.sample_limit};'
            )
            samples = [str(r[0]) for r in cur.fetchall()]
        except Exception:
            pass
        return samples

    def _is_unique_column(self, cur, table: str, column: str) -> bool:
        cur.execute(f"PRAGMA index_list('{table}');")
        for idx in cur.fetchall():
            if idx[2]:  # unique
                cur.execute(f"PRAGMA index_info('{idx[1]}');")
                if [ic[2] for ic in cur.fetchall()] == [column]:
                    return True
        return False


# ==========================================================================
# GRAPH BUILDER
# ==========================================================================

class SchemaGraphBuilder:
    """Builds a NetworkX knowledge graph from extracted schema."""

    def __init__(self):
        self.graph = nx.DiGraph()

    def build(self, schema: dict) -> nx.DiGraph:
        """Construct the full graph from schema metadata."""
        self._add_table_nodes(schema)
        self._add_column_nodes(schema)
        self._add_datatype_nodes(schema)
        self._add_pattern_nodes(schema)
        self._add_concept_nodes(schema)
        self._add_fk_edges(schema)
        self._add_table_relationship_edges(schema)
        self._add_similar_name_edges()
        self._add_same_concept_edges()
        return self.graph

    def _add_table_nodes(self, schema: dict):
        for tname, tinfo in schema["tables"].items():
            self.graph.add_node(
                f"table:{tname}",
                node_type="TABLE",
                name=tname,
                row_count=tinfo["row_count"],
                primary_keys=tinfo["primary_keys"],
                column_names=list(tinfo["columns"].keys()),
            )

    def _add_column_nodes(self, schema: dict):
        for tname, tinfo in schema["tables"].items():
            for cname, cinfo in tinfo["columns"].items():
                col_id = f"column:{tname}.{cname}"

                # Detect value patterns from samples
                all_patterns = []
                for s in cinfo["samples"]:
                    all_patterns.extend(detect_patterns(s))
                unique_patterns = list(set(all_patterns))

                # Classify concepts
                concepts = classify_concepts(cname, tname, unique_patterns)

                # Build rich text for embedding — repeat name tokens for higher weight
                name_tokens = re.split(r"[_\-\s]+", cname.lower())
                table_tokens = re.split(r"[_\-\s]+", tname.lower())
                embedding_text = " ".join(
                    # Name tokens repeated 3x for emphasis
                    name_tokens * 3
                    + table_tokens * 2
                    + concepts
                    + unique_patterns
                    + [cinfo["type"].lower()]
                    + [s.lower() for s in cinfo["samples"][:10]]
                )

                self.graph.add_node(
                    col_id,
                    node_type="COLUMN",
                    table=tname,
                    column=cname,
                    data_type=cinfo["type"],
                    nullable=cinfo["nullable"],
                    primary_key=cinfo["primary_key"],
                    unique=cinfo["unique"],
                    default=cinfo["default"],
                    samples=cinfo["samples"],
                    fingerprint=cinfo["fingerprint"],
                    value_patterns=unique_patterns,
                    concepts=concepts,
                    embedding_text=embedding_text,
                )

                # HAS_COLUMN edge
                self.graph.add_edge(
                    f"table:{tname}", col_id,
                    edge_type="HAS_COLUMN",
                )

    def _add_datatype_nodes(self, schema: dict):
        """Create DataType nodes and link columns to them."""
        seen_types = set()
        for tname, tinfo in schema["tables"].items():
            for cname, cinfo in tinfo["columns"].items():
                dtype = cinfo["type"].upper() or "TEXT"
                dtype_id = f"dtype:{dtype}"
                if dtype_id not in seen_types:
                    self.graph.add_node(dtype_id, node_type="DATATYPE", name=dtype)
                    seen_types.add(dtype_id)
                self.graph.add_edge(
                    f"column:{tname}.{cname}", dtype_id,
                    edge_type="HAS_TYPE",
                )

    def _add_pattern_nodes(self, schema: dict):
        """Create ValuePattern nodes from observed sample patterns."""
        seen = set()
        for tname, tinfo in schema["tables"].items():
            for cname, cinfo in tinfo["columns"].items():
                col_id = f"column:{tname}.{cname}"
                for s in cinfo["samples"]:
                    for p in detect_patterns(s):
                        pat_id = f"pattern:{p}"
                        if pat_id not in seen:
                            self.graph.add_node(pat_id, node_type="VALUE_PATTERN", name=p)
                            seen.add(pat_id)
                        # Only add edge once per column-pattern pair
                        if not self.graph.has_edge(col_id, pat_id):
                            self.graph.add_edge(col_id, pat_id, edge_type="HAS_PATTERN")

    def _add_concept_nodes(self, schema: dict):
        """Create Concept nodes and link columns to their semantic concepts."""
        seen = set()
        for tname, tinfo in schema["tables"].items():
            for cname, cinfo in tinfo["columns"].items():
                col_id = f"column:{tname}.{cname}"
                col_data = self.graph.nodes[col_id]
                for concept in col_data.get("concepts", []):
                    concept_id = f"concept:{concept}"
                    if concept_id not in seen:
                        related = CONCEPT_TAXONOMY.get(concept, {}).get("related", [])
                        self.graph.add_node(
                            concept_id, node_type="CONCEPT",
                            name=concept, related_concepts=related,
                        )
                        seen.add(concept_id)
                        # Add edges between related concepts
                        for rc in related:
                            rc_id = f"concept:{rc}"
                            if rc_id in seen:
                                self.graph.add_edge(concept_id, rc_id, edge_type="RELATED_CONCEPT")
                                self.graph.add_edge(rc_id, concept_id, edge_type="RELATED_CONCEPT")
                    self.graph.add_edge(col_id, concept_id, edge_type="BELONGS_TO_CONCEPT")

    def _add_fk_edges(self, schema: dict):
        """Add FK_TO edges between columns."""
        for fk in schema["foreign_keys"]:
            from_id = f"column:{fk['from_table']}.{fk['from_column']}"
            to_id = f"column:{fk['to_table']}.{fk['to_column']}"
            if self.graph.has_node(from_id) and self.graph.has_node(to_id):
                self.graph.add_edge(from_id, to_id, edge_type="FK_TO")
                self.graph.add_edge(to_id, from_id, edge_type="REFERENCED_BY")

    def _add_table_relationship_edges(self, schema: dict):
        """Add RELATED_TO edges between tables connected by FKs."""
        for fk in schema["foreign_keys"]:
            from_t = f"table:{fk['from_table']}"
            to_t = f"table:{fk['to_table']}"
            if self.graph.has_node(from_t) and self.graph.has_node(to_t):
                if not self.graph.has_edge(from_t, to_t):
                    self.graph.add_edge(from_t, to_t, edge_type="RELATED_TO",
                                        via_fk=f"{fk['from_column']}→{fk['to_column']}")
                if not self.graph.has_edge(to_t, from_t):
                    self.graph.add_edge(to_t, from_t, edge_type="RELATED_TO",
                                        via_fk=f"{fk['to_column']}←{fk['from_column']}")

    def _add_similar_name_edges(self):
        """Add SIMILAR_NAME edges between columns with overlapping name tokens across tables."""
        columns = [
            (nid, data) for nid, data in self.graph.nodes(data=True)
            if data.get("node_type") == "COLUMN"
        ]
        for i, (id_a, data_a) in enumerate(columns):
            tokens_a = set(re.split(r"[_\-\s]+", data_a["column"].lower()))
            # Remove very common tokens
            tokens_a -= {"id", "the", "a", "an", "is", "at", "by", "of"}
            if not tokens_a:
                continue
            for j, (id_b, data_b) in enumerate(columns):
                if i >= j:
                    continue
                if data_a["table"] == data_b["table"]:
                    continue  # same table, skip
                tokens_b = set(re.split(r"[_\-\s]+", data_b["column"].lower()))
                tokens_b -= {"id", "the", "a", "an", "is", "at", "by", "of"}
                overlap = tokens_a & tokens_b
                if overlap and len(overlap) / max(len(tokens_a), len(tokens_b)) >= 0.3:
                    sim = len(overlap) / max(len(tokens_a), len(tokens_b))
                    self.graph.add_edge(id_a, id_b, edge_type="SIMILAR_NAME", similarity=sim)
                    self.graph.add_edge(id_b, id_a, edge_type="SIMILAR_NAME", similarity=sim)

    def _add_same_concept_edges(self):
        """Add SAME_CONCEPT edges between columns sharing concepts across tables."""
        concept_members = defaultdict(list)
        for nid, data in self.graph.nodes(data=True):
            if data.get("node_type") == "COLUMN":
                for concept in data.get("concepts", []):
                    concept_members[concept].append(nid)

        for concept, members in concept_members.items():
            for i, a in enumerate(members):
                table_a = self.graph.nodes[a]["table"]
                for b in members[i + 1:]:
                    table_b = self.graph.nodes[b]["table"]
                    if table_a != table_b and not self.graph.has_edge(a, b):
                        self.graph.add_edge(a, b, edge_type="SAME_CONCEPT", concept=concept)
                        self.graph.add_edge(b, a, edge_type="SAME_CONCEPT", concept=concept)


# ==========================================================================
# GRAPH RAG RETRIEVER
# ==========================================================================

class GraphRAGRetriever:
    """
    Retrieval engine that queries the schema knowledge graph.
    
    Retrieval Pipeline:
      1. Parse query → tokens, concepts, value patterns
      2. Find entry points in graph (concept nodes, pattern nodes, name matches)
      3. Multi-hop traversal from entry points to reach COLUMN nodes
      4. Score candidates: embedding similarity + graph proximity + value match
      5. Return ranked results with graph context
    """

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph
        self._vectorizer: Optional[TfidfVectorizer] = None
        self._embedding_matrix = None
        self._column_ids: list[str] = []
        self._build_embeddings()

    def _build_embeddings(self):
        """Build TF-IDF embeddings over column nodes."""
        self._column_ids = []
        texts = []
        for nid, data in self.graph.nodes(data=True):
            if data.get("node_type") == "COLUMN":
                self._column_ids.append(nid)
                texts.append(data.get("embedding_text", ""))

        if texts:
            self._vectorizer = TfidfVectorizer(
                ngram_range=(1, 3), max_features=10000, sublinear_tf=True,
            )
            self._embedding_matrix = self._vectorizer.fit_transform(texts)

    def retrieve(
        self,
        attribute_name: str,
        attribute_value: str = "",
        attribute_context: str = "",
        top_k: int = 5,
    ) -> list[dict]:
        """
        Retrieve best matching columns using graph traversal + embeddings.
        
        Returns list of candidate columns with scores and graph context.
        """
        if not self._column_ids:
            return []

        # --- Step 1: Parse query ---
        query_tokens = set(re.split(r"[_\-\s]+", attribute_name.lower()))
        all_tokens = query_tokens | set(re.split(r"[_\-\s]+", attribute_context.lower()))

        # Detect concepts from query
        query_concepts = []
        for concept, rules in CONCEPT_TAXONOMY.items():
            kw_tokens = set()
            for kw in rules["name_keywords"]:
                kw_tokens |= set(kw.split("_"))
            if all_tokens & kw_tokens:
                query_concepts.append(concept)

        # Detect value patterns
        query_patterns = detect_patterns(attribute_value) if attribute_value else []

        # --- Step 2: Find graph entry points ---
        entry_columns = set()  # column node IDs reachable from entry points

        # Entry via concept nodes
        for concept in query_concepts:
            concept_id = f"concept:{concept}"
            if self.graph.has_node(concept_id):
                # All columns belonging to this concept
                for pred in self.graph.predecessors(concept_id):
                    if self.graph.nodes[pred].get("node_type") == "COLUMN":
                        edge = self.graph.edges[pred, concept_id]
                        if edge.get("edge_type") == "BELONGS_TO_CONCEPT":
                            entry_columns.add(pred)
                # Also check related concepts (1 hop)
                for neighbor in self.graph.neighbors(concept_id):
                    if self.graph.nodes[neighbor].get("node_type") == "CONCEPT":
                        for pred in self.graph.predecessors(neighbor):
                            if self.graph.nodes[pred].get("node_type") == "COLUMN":
                                entry_columns.add(pred)

        # Entry via pattern nodes
        for pat in query_patterns:
            pat_id = f"pattern:{pat}"
            if self.graph.has_node(pat_id):
                for pred in self.graph.predecessors(pat_id):
                    if self.graph.nodes[pred].get("node_type") == "COLUMN":
                        entry_columns.add(pred)

        # --- Step 3: Expand via graph traversal (SAME_CONCEPT, SIMILAR_NAME, FK) ---
        expanded = set(entry_columns)
        for col_id in list(entry_columns):
            for neighbor in self.graph.neighbors(col_id):
                ndata = self.graph.nodes[neighbor]
                if ndata.get("node_type") == "COLUMN":
                    edge = self.graph.edges[col_id, neighbor]
                    if edge.get("edge_type") in ("SAME_CONCEPT", "SIMILAR_NAME", "FK_TO", "REFERENCED_BY"):
                        expanded.add(neighbor)

        # --- Step 4: Score all column candidates ---
        n = len(self._column_ids)

        # 4a. Embedding similarity
        query_text = " ".join(
            list(query_tokens)
            + re.split(r"[_\-\s]+", attribute_context.lower())
            + query_concepts + query_patterns
        )
        query_vec = self._vectorizer.transform([query_text])
        embedding_scores = cosine_similarity(query_vec, self._embedding_matrix)[0]

        # 4b. Graph entry point bonus
        graph_scores = np.zeros(n)
        for i, col_id in enumerate(self._column_ids):
            if col_id in entry_columns:
                graph_scores[i] = 1.0  # direct entry point
            elif col_id in expanded:
                graph_scores[i] = 0.5  # 1-hop expansion

        # 4c. Value pattern match
        value_scores = np.zeros(n)
        if attribute_value:
            attr_fingerprint = compute_value_fingerprint([str(attribute_value)])
            attr_patterns = set(attr_fingerprint["patterns"].keys())
            attr_val_str = str(attribute_value).strip()

            for i, col_id in enumerate(self._column_ids):
                col_data = self.graph.nodes[col_id]
                col_patterns = set(col_data.get("value_patterns", []))
                col_fingerprint = col_data.get("fingerprint", {})
                col_samples = col_data.get("samples", [])

                # Pattern overlap
                pattern_overlap = attr_patterns & col_patterns
                if pattern_overlap:
                    value_scores[i] += 0.4 * len(pattern_overlap) / max(len(attr_patterns), 1)

                # Length similarity
                if col_fingerprint.get("avg_len") and attr_fingerprint.get("avg_len"):
                    len_ratio = min(attr_fingerprint["avg_len"], col_fingerprint["avg_len"]) / \
                                max(attr_fingerprint["avg_len"], col_fingerprint["avg_len"], 1)
                    value_scores[i] += 0.2 * len_ratio

                # Direct value match with samples
                for sv in col_samples:
                    # Exact match
                    if sv.lower().strip() == attr_val_str.lower():
                        value_scores[i] += 1.0
                        break
                    # Same format (e.g., both emails with same domain)
                    if detect_patterns(sv) == detect_patterns(attr_val_str):
                        value_scores[i] += 0.3
                        break

        # Normalize
        def safe_norm(arr):
            mx = arr.max()
            return arr / mx if mx > 0 else arr

        embedding_scores = safe_norm(embedding_scores)
        graph_scores = safe_norm(graph_scores)
        value_scores = safe_norm(value_scores)

        # Weighted combination
        combined = (
            embedding_scores * 0.35
            + graph_scores * 0.35
            + value_scores * 0.30
        )

        # --- Step 5: Rank and return with context ---
        top_idx = np.argsort(combined)[::-1][:top_k]

        results = []
        for idx in top_idx:
            if combined[idx] < 0.01:
                continue
            col_id = self._column_ids[idx]
            col_data = self.graph.nodes[col_id]

            # Gather graph context: FK paths, related tables
            fk_context = self._get_fk_context(col_id)
            related_columns = self._get_related_columns(col_id)

            results.append({
                "table": col_data["table"],
                "column": col_data["column"],
                "data_type": col_data["data_type"],
                "primary_key": col_data["primary_key"],
                "unique": col_data["unique"],
                "nullable": col_data["nullable"],
                "samples": col_data["samples"][:5],
                "concepts": col_data.get("concepts", []),
                "value_patterns": col_data.get("value_patterns", []),
                "score_combined": round(float(combined[idx]), 4),
                "score_embedding": round(float(embedding_scores[idx]), 4),
                "score_graph": round(float(graph_scores[idx]), 4),
                "score_value": round(float(value_scores[idx]), 4),
                "graph_context": {
                    "foreign_keys": fk_context,
                    "related_columns": related_columns,
                },
            })

        return results

    def _get_fk_context(self, col_id: str) -> list[dict]:
        """Get FK relationships for a column."""
        fks = []
        for neighbor in self.graph.neighbors(col_id):
            edge = self.graph.edges[col_id, neighbor]
            if edge.get("edge_type") == "FK_TO":
                target = self.graph.nodes[neighbor]
                fks.append({
                    "direction": "outgoing",
                    "target": f"{target['table']}.{target['column']}",
                })
            elif edge.get("edge_type") == "REFERENCED_BY":
                target = self.graph.nodes[neighbor]
                fks.append({
                    "direction": "incoming",
                    "source": f"{target['table']}.{target['column']}",
                })
        return fks

    def _get_related_columns(self, col_id: str) -> list[dict]:
        """Get columns related via SAME_CONCEPT or SIMILAR_NAME."""
        related = []
        for neighbor in self.graph.neighbors(col_id):
            ndata = self.graph.nodes.get(neighbor, {})
            if ndata.get("node_type") != "COLUMN":
                continue
            edge = self.graph.edges[col_id, neighbor]
            etype = edge.get("edge_type")
            if etype in ("SAME_CONCEPT", "SIMILAR_NAME"):
                related.append({
                    "column_id": neighbor,
                    "table": ndata["table"],
                    "column": ndata["column"],
                    "relation": etype,
                    "detail": edge.get("concept") or edge.get("similarity"),
                })
        return related


# ==========================================================================
# MAIN: SchemaGraphRAG — Unified Interface
# ==========================================================================

class SchemaGraphRAG:
    """
    Production-grade Schema GraphRAG.
    
    Builds a full knowledge graph from a database and provides
    graph-based retrieval for the ADK agent.
    
    Usage:
        rag = SchemaGraphRAG("mydb.sqlite")
        rag.build()
        results = rag.retrieve("customer email", value="test@example.com")
    """

    def __init__(self, db_path: str, db_type: str = "sqlite", cache_dir: str = ".graph_cache"):
        self.db_path = db_path
        self.db_type = db_type
        self.cache_dir = cache_dir
        self.graph: Optional[nx.DiGraph] = None
        self.retriever: Optional[GraphRAGRetriever] = None
        self._schema: Optional[dict] = None

    def build(self, use_cache: bool = True) -> "SchemaGraphRAG":
        """Build the knowledge graph (or load from cache)."""
        cache_path = self._cache_path()

        if use_cache and cache_path and os.path.exists(cache_path):
            logger.info(f"Loading cached graph from {cache_path}")
            self._load_cache(cache_path)
        else:
            logger.info(f"Extracting schema from {self.db_path}...")
            extractor = SchemaExtractor(self.db_path, self.db_type)
            self._schema = extractor.extract()

            logger.info("Building knowledge graph...")
            builder = SchemaGraphBuilder()
            self.graph = builder.build(self._schema)

            logger.info(
                f"Graph built: {self.graph.number_of_nodes()} nodes, "
                f"{self.graph.number_of_edges()} edges"
            )

            if cache_path:
                self._save_cache(cache_path)

        self.retriever = GraphRAGRetriever(self.graph)
        return self

    def retrieve(self, attribute_name: str, value: str = "", context: str = "", top_k: int = 5) -> list[dict]:
        """Retrieve matching columns via graph traversal + embeddings."""
        return self.retriever.retrieve(attribute_name, value, context, top_k)

    def check_exists(self, table: str, column: str, value: str) -> dict:
        """Check if a value exists in the live DB."""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(f"PRAGMA table_info('{table}');")
            col_names = [c[1] for c in cur.fetchall()]
            cur.execute(f'SELECT * FROM "{table}" WHERE "{column}" = ?;', (value,))
            rows = cur.fetchall()
            conn.close()
            if rows:
                return {"exists": True, "count": len(rows),
                        "records": [dict(zip(col_names, r)) for r in rows]}
            return {"exists": False, "count": 0, "records": []}
        except Exception as e:
            return {"exists": False, "error": str(e)}

    def get_graph_stats(self) -> dict:
        """Get graph statistics."""
        node_types = defaultdict(int)
        for _, data in self.graph.nodes(data=True):
            node_types[data.get("node_type", "?")] += 1
        edge_types = defaultdict(int)
        for _, _, data in self.graph.edges(data=True):
            edge_types[data.get("edge_type", "?")] += 1
        return {
            "total_nodes": self.graph.number_of_nodes(),
            "total_edges": self.graph.number_of_edges(),
            "node_types": dict(node_types),
            "edge_types": dict(edge_types),
        }

    def get_schema_summary(self) -> dict:
        """Get schema summary from the graph."""
        tables = {}
        for nid, data in self.graph.nodes(data=True):
            if data.get("node_type") == "TABLE":
                tname = data["name"]
                cols = []
                for neighbor in self.graph.neighbors(nid):
                    edge = self.graph.edges[nid, neighbor]
                    if edge.get("edge_type") == "HAS_COLUMN":
                        cdata = self.graph.nodes[neighbor]
                        cols.append({
                            "column": cdata["column"], "type": cdata["data_type"],
                            "pk": cdata["primary_key"], "unique": cdata["unique"],
                            "nullable": cdata["nullable"],
                            "samples": cdata["samples"][:5],
                            "concepts": cdata.get("concepts", []),
                            "value_patterns": cdata.get("value_patterns", []),
                        })
                # FK info
                fks = []
                if self._schema:
                    fks = [fk for fk in self._schema.get("foreign_keys", [])
                           if fk["from_table"] == tname or fk["to_table"] == tname]
                tables[tname] = {
                    "columns": cols, "row_count": data["row_count"],
                    "primary_keys": data["primary_keys"], "foreign_keys": fks,
                }
        return tables

    def get_schema_context_text(self) -> str:
        """Generate human-readable schema context for agent prompts."""
        summary = self.get_schema_summary()
        lines = ["DATABASE SCHEMA:\n"]
        for tname, tinfo in summary.items():
            lines.append(f"Table: {tname} ({tinfo['row_count']} rows)")
            lines.append(f"  PKs: {tinfo['primary_keys']}")
            if tinfo["foreign_keys"]:
                for fk in tinfo["foreign_keys"]:
                    lines.append(f"  FK: {fk['from_table']}.{fk['from_column']} → {fk['to_table']}.{fk['to_column']}")
            for col in tinfo["columns"]:
                flags = []
                if col["pk"]: flags.append("PK")
                if col["unique"]: flags.append("UNIQUE")
                if not col["nullable"]: flags.append("NOT NULL")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                concepts_str = f" concepts={col['concepts']}" if col["concepts"] else ""
                patterns_str = f" patterns={col['value_patterns']}" if col["value_patterns"] else ""
                samples_str = f" samples={col['samples']}" if col["samples"] else ""
                lines.append(
                    f"    {col['column']:25} {col['type']:10}{flag_str}"
                    f"{concepts_str}{patterns_str}{samples_str}"
                )
            lines.append("")
        return "\n".join(lines)

    def find_fk_path(self, from_table: str, to_table: str) -> Optional[list[dict]]:
        """Find FK path between two tables via graph traversal."""
        from_id = f"table:{from_table}"
        to_id = f"table:{to_table}"
        if not (self.graph.has_node(from_id) and self.graph.has_node(to_id)):
            return None
        try:
            path = nx.shortest_path(self.graph, from_id, to_id)
            steps = []
            for i in range(len(path) - 1):
                edge = self.graph.edges.get((path[i], path[i + 1]), {})
                steps.append({
                    "from": path[i].replace("table:", ""),
                    "to": path[i + 1].replace("table:", ""),
                    "via": edge.get("via_fk", ""),
                })
            return steps
        except nx.NetworkXNoPath:
            return None

    # Cache management
    def _cache_path(self) -> Optional[str]:
        if not self.cache_dir:
            return None
        os.makedirs(self.cache_dir, exist_ok=True)
        db_hash = hashlib.md5(os.path.abspath(self.db_path).encode()).hexdigest()[:8]
        return os.path.join(self.cache_dir, f"schema_graph_{db_hash}.pkl")

    def _save_cache(self, path: str):
        with open(path, "wb") as f:
            pickle.dump({"graph": self.graph, "schema": self._schema}, f)
        logger.info(f"Graph cached to {path}")

    def _load_cache(self, path: str):
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.graph = data["graph"]
        self._schema = data["schema"]
