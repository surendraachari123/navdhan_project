"""Manager market dashboard and admin refresh controls."""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query

import auth
from config import MARKET_QDRANT_COLLECTION, MARKET_WATCHLIST
from database import get_db
from exceptions import AppError, NotFoundError
from market_service import get_overview, index_watchlist, run_refresh_job, search_analysis

router = APIRouter(prefix="/market", tags=["Market Research"])
MANAGERS = ("admin", "manager")


@router.get("/overview")
def overview(cursor=Depends(get_db), user=Depends(auth.require_role(*MANAGERS))):
    return {**get_overview(cursor), "watchlist": MARKET_WATCHLIST,
            "qdrant_collection": MARKET_QDRANT_COLLECTION}


@router.get("/search")
def search_market(
    q: str = Query(..., min_length=2, max_length=300),
    symbol: str | None = Query(default=None, max_length=50),
    user=Depends(auth.require_role(*MANAGERS)),
):
    try:
        return {"results": search_analysis(q.strip(), symbol.strip().upper() if symbol else None)}
    except Exception as exc:
        raise AppError("Market analysis search is unavailable. Check Qdrant configuration.") from exc


@router.post("/index-watchlist")
def reindex_watchlist(user=Depends(auth.require_role("admin"))):
    """Add configured symbol names and aliases to the market Qdrant collection."""
    try:
        return index_watchlist()
    except Exception as exc:
        raise AppError("Could not index the market watchlist. Check Qdrant configuration.") from exc


@router.post("/refresh", status_code=202)
def refresh_market(
    background_tasks: BackgroundTasks,
    cursor=Depends(get_db),
    user=Depends(auth.require_role("admin")),
):
    run_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO market_refresh_runs (run_id, triggered_by, status) VALUES (:1, :2, 'RUNNING')",
        (run_id, user["username"]),
    )
    background_tasks.add_task(run_refresh_job, run_id)
    return {"run_id": run_id, "status": "RUNNING"}


@router.get("/refresh/{run_id}")
def refresh_status(run_id: str, cursor=Depends(get_db), user=Depends(auth.require_role(*MANAGERS))):
    cursor.execute(
        "SELECT run_id, triggered_by, status, result, error_message, started_at, finished_at "
        "FROM market_refresh_runs WHERE run_id=:1", (run_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise NotFoundError(f"No market refresh run found with id '{run_id}'.")
    result = row[3].read() if hasattr(row[3], "read") else row[3]
    error_message = row[4].read() if hasattr(row[4], "read") else row[4]
    return {"run_id": row[0], "triggered_by": row[1], "status": row[2],
            "result": result, "error_message": error_message,
            "started_at": row[5].isoformat() if row[5] else None,
            "finished_at": row[6].isoformat() if row[6] else None}
