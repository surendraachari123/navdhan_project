"""Stock-only Qdrant collection; separate from the AI glossary and Market Research."""
import hashlib
import os
import uuid

import ai_qdrant

COLLECTION = os.getenv("STOCK_QDRANT_COLLECTION", "navdhan_stock_intelligence")


def _ensure_collection(client, vector_size: int):
    if client.collection_exists(COLLECTION):
        return
    from qdrant_client.models import Distance, VectorParams
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )


def index_document(symbol: str, text: str, payload: dict) -> None:
    if not text.strip():
        return
    from qdrant_client.models import PointStruct
    client = ai_qdrant.get_client()
    vector = ai_qdrant._embed([text])[0]
    _ensure_collection(client, len(vector))
    stable = hashlib.sha256(f"{symbol}:{payload.get('source_url')}:{text}".encode()).hexdigest()
    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"stock-intelligence:{stable}"))
    client.upsert(
        collection_name=COLLECTION, wait=True,
        points=[PointStruct(id=point_id, vector=vector,
                            payload={**payload, "symbol": symbol, "text": text})],
    )


def search(symbol: str, query: str, limit: int = 6) -> list[dict]:
    client = ai_qdrant.get_client()
    if not client.collection_exists(COLLECTION):
        return []
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    vector = ai_qdrant._embed([query])[0]
    result = client.query_points(
        collection_name=COLLECTION, query=vector, limit=limit, with_payload=True,
        query_filter=Filter(must=[FieldCondition(key="symbol", match=MatchValue(value=symbol))]),
    ).points
    return [{**point.payload, "score": round(float(point.score), 4)} for point in result]
