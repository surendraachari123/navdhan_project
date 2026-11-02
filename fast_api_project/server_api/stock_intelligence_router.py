"""Stock Intelligence API routes, isolated from Market Research."""
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query

import auth
import stock_intelligence_service as service
import stock_database
import stock_live_market
from exceptions import AppError, NotFoundError

router = APIRouter(prefix="/stock-intelligence", tags=["Stock Intelligence"])
MANAGERS = ("admin", "manager")
logger = logging.getLogger("app.stock_intelligence.router")


@router.get("/search")
def stock_search(q: str = Query(default="", max_length=100), user=Depends(auth.require_role(*MANAGERS))):
    return {"results": service.search(q)}


@router.get("/refresh/{run_id}")
def refresh_status(run_id: str, user=Depends(auth.require_role(*MANAGERS))):
    try:
        run_id = str(uuid.UUID(run_id))
    except ValueError:
        raise NotFoundError("Refresh run was not found.")
    with stock_database.connect_stock_db() as conn:
        row = conn.execute("SELECT symbol,status,price_rows,crawled_pages,error_message,started_at,finished_at FROM stock_intelligence_refresh_runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise NotFoundError("Refresh run was not found.")
    return {"run_id": run_id, "symbol": row[0], "status": row[1], "price_rows": row[2], "crawled_pages": row[3], "error_message": row[4], "started_at": row[5], "finished_at": row[6]}


@router.get("/{symbol}/live-quote")
def get_live_quote(symbol: str, user=Depends(auth.require_role(*MANAGERS))):
    symbol = symbol.upper().strip()
    if symbol not in service.STOCKS:
        raise NotFoundError("Choose a supported stock symbol.")
    try:
        return stock_live_market.fetch_live_quote(symbol)
    except Exception as exc:
        logger.exception("Live quote fetch failed for %s", symbol)
        raise AppError(f"NSE live quote is unavailable for {symbol}. Check the API logs and try again.") from exc


@router.get("/{symbol}")
def get_dashboard(symbol: str, user=Depends(auth.require_role(*MANAGERS))):
    try:
        return service.dashboard(symbol)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
        logger.exception("Stock dashboard load failed for %s", symbol)
        raise AppError("Stock dashboard is unavailable. Check the local Stock Intelligence SQLite database and API logs.") from exc


@router.post("/{symbol}/insights")
def get_insights(symbol: str, user=Depends(auth.require_role(*MANAGERS))):
    try:
        return service.ai_insights(symbol)
    except Exception as exc:
        raise AppError("Stock insights could not be generated. Check LLM configuration.") from exc


def _refresh(run_id: str, symbol: str):
    from stock_intelligence_collector import collect_stock
    try:
        result = collect_stock(symbol)
        with stock_database.connect_stock_db() as conn:
            status = "SUCCESS" if not result["failures"] and result["crawled_pages"] else "PARTIAL"
            error_message = "; ".join(result["failures"])[:2000] or None
            conn.execute("UPDATE stock_intelligence_refresh_runs SET status=?,price_rows=?,crawled_pages=?,error_message=?,finished_at=CURRENT_TIMESTAMP WHERE run_id=?",
                         (status, result["price_rows"], result["crawled_pages"], error_message, run_id))
    except Exception as exc:
        logger.exception("Stock refresh %s failed", run_id)
        try:
            with stock_database.connect_stock_db() as conn:
                conn.execute("UPDATE stock_intelligence_refresh_runs SET status='FAILED',error_message=?,finished_at=CURRENT_TIMESTAMP WHERE run_id=?", (str(exc)[:2000], run_id))
        except Exception:
            logger.exception("Unable to record stock refresh failure")


@router.post("/{symbol}/refresh", status_code=202)
def refresh_stock(symbol: str, background_tasks: BackgroundTasks, user=Depends(auth.require_role("admin"))):
    symbol = symbol.upper().strip()
    if symbol not in service.STOCKS:
        raise NotFoundError("Choose a supported stock symbol.")
    run_id = str(uuid.uuid4())
    try:
        with stock_database.connect_stock_db() as conn:
            conn.execute("INSERT INTO stock_intelligence_refresh_runs(run_id,symbol) VALUES(?,?)", (run_id,symbol))
    except Exception as exc:
        raise AppError("Could not start refresh. Check the local Stock Intelligence SQLite database.") from exc
    background_tasks.add_task(_refresh, run_id, symbol)
    return {"run_id": run_id, "status": "RUNNING", "symbol": symbol}
