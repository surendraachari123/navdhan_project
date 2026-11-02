"""
Qdrant retrieval for NavDhan AI (same idea as the NavDhan AI platform project:
business glossary -> embeddings in Qdrant -> relevant context for the SQL prompt).

  ingest()  reads navdhan_glossary.yaml, turns each table / metric / relationship /
            rule / question pattern / SQL example into a document, embeds it and
            (re)creates the Qdrant collection.
  search()  embeds a question and returns the closest documents.

Qdrant holds only semantic metadata (definitions, rules, example SQL) - never
row data. Oracle stays the source of truth.

Documents are stored twice: `text` (full) and `text_restricted` (lines that
mention personal-data columns removed). Non-admin users are only ever given
the restricted text.

Embeddings use fastembed (ONNX, no PyTorch) with the same model as the
NavDhan AI project, all-MiniLM-L6-v2 (384 dimensions).
"""
import logging
import re
import threading
import uuid

import yaml

from config import (
    AI_EMBED_MODEL, AI_GLOSSARY_PATH, AI_SENSITIVE_COLUMNS,
    QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL,
)

logger = logging.getLogger("app.ai.qdrant")

_lock = threading.Lock()
_client = None
_embedder = None          # callable: list[str] -> list[list[float]]  (see set_embedder)
_model = None


# ---------------------------------------------------------------------
# Client + embedder
# ---------------------------------------------------------------------

def get_client():
    global _client
    with _lock:
        if _client is None:
            from qdrant_client import QdrantClient
            if QDRANT_URL == ":memory:":
                _client = QdrantClient(":memory:")
            else:
                _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=10)
        return _client


def set_embedder(fn) -> None:
    """Swap the embedding function (used by tests). fn(list[str]) -> list[list[float]]."""
    global _embedder
    _embedder = fn


def _embed(texts: list[str]) -> list[list[float]]:
    global _model
    if _embedder is not None:
        return _embedder(texts)
    with _lock:
        if _model is None:
            from fastembed import TextEmbedding
            logger.info("Loading embedding model %s (first use downloads it)...", AI_EMBED_MODEL)
            _model = TextEmbedding(AI_EMBED_MODEL)
    import numpy as np
    vectors = []
    for v in _model.embed(texts):
        v = np.asarray(v, dtype="float32")
        n = float(np.linalg.norm(v)) or 1.0
        vectors.append((v / n).tolist())
    return vectors


# ---------------------------------------------------------------------
# Glossary -> documents
# ---------------------------------------------------------------------

def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(f"- {_stringify(i)}" for i in value)
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{k}:\n{_stringify(v)}")
            else:
                lines.append(f"{k}: {_stringify(v)}")
        return "\n".join(lines)
    return str(value)


def _make_id(prefix: str, name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"navdhan-fastapi:{prefix}:{name}"))


def _restrict(text: str, sensitive: list[str]) -> str:
    """Drops every line that mentions a personal-data column."""
    if not sensitive:
        return text
    pattern = re.compile(r"\b(" + "|".join(re.escape(c) for c in sensitive) + r")\b", re.IGNORECASE)
    return "\n".join(line for line in text.splitlines() if not pattern.search(line))


def _doc(prefix, doc_type, name, text, sensitive, **extra):
    return {
        "id": _make_id(prefix, name), "type": doc_type, "name": name,
        "text": text, "text_restricted": _restrict(text, sensitive), **extra,
    }


def _table_text(name: str, data: dict, include_sensitive: bool, sensitive: list[str]) -> str:
    cols = []
    for c in data.get("columns", []) or []:
        is_sensitive = bool(c.get("sensitive")) or str(c.get("name", "")).lower() in sensitive
        if is_sensitive and not include_sensitive:
            continue
        cols.append(f"- {c.get('name')} ({c.get('data_type', '')}): {c.get('business_definition', '')}")
    parts = [
        f"Oracle {data.get('object_type', 'table')}: {name}",
        f"Business name: {data.get('business_name', '')}",
        f"Description: {data.get('description', '')}",
        f"Business purpose: {data.get('business_purpose', '')}",
        f"Grain: {data.get('grain', '')}",
        "Columns:", "\n".join(cols),
        "Business terms: " + ", ".join(data.get("business_terms", []) or []),
        "Keywords: " + ", ".join(data.get("keywords", []) or []),
    ]
    return "\n".join(p for p in parts if p and not p.endswith(": "))


def build_documents(glossary: dict, sensitive: list[str] | None = None) -> list[dict]:
    sensitive = [c.lower() for c in (AI_SENSITIVE_COLUMNS if sensitive is None else sensitive)]
    docs = []

    rules = glossary.get("global_rules")
    if rules:
        docs.append(_doc("global", "global_rule", "global_rules",
                         "Global SQL generation rules:\n" + _stringify(rules), sensitive))

    for name, data in (glossary.get("views") or {}).items():
        if not isinstance(data, dict):
            continue
        full = _table_text(name, data, True, sensitive)
        restricted = _table_text(name, data, False, sensitive)
        d = _doc("table", "table", name, full, sensitive, view=name)
        d["text_restricted"] = _restrict(restricted, sensitive)
        docs.append(d)

    for name, data in (glossary.get("business_metrics") or {}).items():
        docs.append(_doc("metric", "metric", name, f"Business metric: {name}\n{_stringify(data)}", sensitive))

    for i, rel in enumerate(glossary.get("relationships") or []):
        name = rel.get("name", f"relationship_{i}") if isinstance(rel, dict) else f"relationship_{i}"
        docs.append(_doc("relationship", "relationship", name, f"Relationship: {name}\n{_stringify(rel)}", sensitive))

    for name, data in (glossary.get("business_rules") or {}).items():
        docs.append(_doc("rule", "business_rule", name, f"Business rule: {name}\n{_stringify(data)}", sensitive))

    for i, pat in enumerate(glossary.get("question_patterns") or []):
        name = pat.get("name", f"pattern_{i}") if isinstance(pat, dict) else f"pattern_{i}"
        docs.append(_doc("pattern", "question_pattern", name, f"Question pattern: {name}\n{_stringify(pat)}", sensitive))

    sem = glossary.get("semantic_context") or {}
    for section, doc_type, label in (
        ("business_language", "business_language", "Business language term"),
        ("question_interpretation", "question_interpretation", "Question interpretation"),
        ("sql_generation_examples", "sql_example", "SQL example"),
    ):
        for name, data in (sem.get(section) or {}).items():
            docs.append(_doc(doc_type, doc_type, name, f"{label}: {name}\n{_stringify(data)}", sensitive))

    return docs


def load_glossary(path: str = AI_GLOSSARY_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping.")
    return data


# ---------------------------------------------------------------------
# Ingest + search
# ---------------------------------------------------------------------

def ingest(path: str = AI_GLOSSARY_PATH) -> dict:
    """(Re)creates the collection from the glossary file. Safe to run repeatedly."""
    from qdrant_client.models import Distance, PointStruct, VectorParams

    docs = build_documents(load_glossary(path))
    if not docs:
        raise ValueError("The glossary produced no documents.")

    vectors = _embed([d["text"] for d in docs])
    client = get_client()

    if client.collection_exists(QDRANT_COLLECTION):
        client.delete_collection(QDRANT_COLLECTION)
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
    )
    client.upsert(
        collection_name=QDRANT_COLLECTION,
        wait=True,
        points=[
            PointStruct(
                id=d["id"], vector=v,
                payload={k: d[k] for k in ("type", "name", "text", "text_restricted") if k in d},
            )
            for d, v in zip(docs, vectors)
        ],
    )

    counts: dict[str, int] = {}
    for d in docs:
        counts[d["type"]] = counts.get(d["type"], 0) + 1
    logger.info("Indexed %d glossary documents into '%s': %s", len(docs), QDRANT_COLLECTION, counts)
    return {"collection": QDRANT_COLLECTION, "documents": len(docs), "by_type": counts}


def status() -> dict:
    """Cheap health probe for /ai/status - never raises."""
    try:
        client = get_client()
        if not client.collection_exists(QDRANT_COLLECTION):
            return {"reachable": True, "indexed": False, "documents": 0}
        return {"reachable": True, "indexed": True,
                "documents": client.count(QDRANT_COLLECTION, exact=True).count}
    except Exception as exc:  # network, auth, missing package ...
        logger.warning("Qdrant status check failed: %s", exc)
        return {"reachable": False, "indexed": False, "documents": 0}


_ensured = False


def _ensure_indexed() -> None:
    """First use against an empty Qdrant (fresh server, or the in-memory
    mode) builds the index automatically, so no manual step is required."""
    global _ensured
    if _ensured:
        return
    with _lock:
        if _ensured:
            return
    if not get_client().collection_exists(QDRANT_COLLECTION):
        logger.info("Collection '%s' not found - indexing the glossary now.", QDRANT_COLLECTION)
        ingest()
    _ensured = True


def search(question: str, limit: int, restricted: bool) -> list[dict]:
    """Returns [{type, name, score, text}], best first. Raises on any Qdrant
    problem - the caller decides how to fall back."""
    _ensure_indexed()
    client = get_client()
    vector = _embed([question.strip()])[0]
    points = client.query_points(
        collection_name=QDRANT_COLLECTION, query=vector, limit=limit, with_payload=True
    ).points
    key = "text_restricted" if restricted else "text"
    return [
        {"type": p.payload.get("type"), "name": p.payload.get("name"),
         "score": round(float(p.score), 4), "text": p.payload.get(key, "")}
        for p in points
    ]


def get_global_rules(restricted: bool) -> str:
    """The global rules document is always included, whatever the question."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    _ensure_indexed()
    client = get_client()
    points, _ = client.scroll(
        collection_name=QDRANT_COLLECTION, limit=1, with_payload=True,
        scroll_filter=Filter(must=[FieldCondition(key="name", match=MatchValue(value="global_rules"))]),
    )
    if not points:
        return ""
    return points[0].payload.get("text_restricted" if restricted else "text", "")
