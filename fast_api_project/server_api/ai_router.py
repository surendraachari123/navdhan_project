"""
NavDhan AI routes. Mounted in main.py with  app.include_router(ai_router.router).

  POST /ai/ask       any logged-in user - one question -> SQL -> answer + table
  POST /ai/chat      any logged-in user - multi-turn assistant (history in request)
  GET  /ai/insights  admin / manager    - auto-generated business briefing
  GET  /ai/status    any logged-in user - is the AI configured? (used by the UI)
  POST /ai/reindex   admin              - re-read navdhan_glossary.yaml into Qdrant
"""
from fastapi import APIRouter, Depends

from exceptions import AppError

import ai_llm
import ai_qdrant
import ai_service
import auth
from config import AI_ALLOWED_TABLES, AI_SENSITIVE_COLUMNS, AI_USE_QDRANT, QDRANT_COLLECTION
from database import get_db
from models import AskRequest, ChatRequest

router = APIRouter(prefix="/ai", tags=["NavDhan AI"])


@router.get("/status")
def ai_status(user=Depends(auth.get_current_user)):
    return {
        "configured": ai_llm.is_configured(),
        "tables": AI_ALLOWED_TABLES,
        "restricted_columns": [] if user["role"] == "admin" else AI_SENSITIVE_COLUMNS,
        "can_view_insights": user["role"] in ("admin", "manager"),
        "qdrant": {"enabled": AI_USE_QDRANT, "collection": QDRANT_COLLECTION,
                   **(ai_qdrant.status() if AI_USE_QDRANT else {})},
    }


@router.post("/ask")
def ask(body: AskRequest, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    """Natural language -> validated read-only Oracle SQL -> rows + plain-English answer."""
    return ai_service.ask(cursor, user, body.question)


@router.post("/chat")
def chat(body: ChatRequest, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    """Conversational assistant. Same engine as /ai/ask, but it sees the earlier
    turns and may answer without running a query."""
    history = [m.model_dump() for m in body.messages]
    return ai_service.converse(cursor, user, history)


@router.get("/insights")
def insights(
    refresh: bool = False,
    cursor=Depends(get_db),
    user=Depends(auth.require_role("admin", "manager")),
):
    """Fixed aggregate queries + an LLM-written briefing. Cached for a few
    minutes; pass ?refresh=true to recompute."""
    return ai_service.get_insights(cursor, force_refresh=refresh)


@router.post("/reindex")
def reindex(user=Depends(auth.require_role("admin"))):
    """Re-reads navdhan_glossary.yaml, embeds it and rebuilds the Qdrant
    collection. Run after editing the glossary or pointing at a new Qdrant."""
    if not AI_USE_QDRANT:
        raise AppError("Qdrant is disabled (AI_USE_QDRANT=false).")
    try:
        return ai_qdrant.ingest()
    except Exception as exc:
        # Never leak connection strings / stack traces to the browser.
        import logging
        logging.getLogger("app.ai.qdrant").exception("Reindex failed")
        raise AppError(f"Could not index the glossary into Qdrant ({type(exc).__name__}). Check QDRANT_URL and the server log.") from exc
