"""Daily market snapshot ingestion and analysis.

Alpha Vantage supplies daily OHLCV and news sentiment. Oracle is the source
of truth for price snapshots and indicators; Qdrant indexes the derived text
and article summaries for semantic search. This is delayed/historical market
information, not a trading signal.
"""
import json
import logging
import math
import uuid
from datetime import date, datetime

import requests

import ai_qdrant
from config import (
    MARKET_DATA_API_KEY, MARKET_DATA_BASE_URL, MARKET_DATA_LICENSE_CONFIRMED, MARKET_QDRANT_COLLECTION,
    MARKET_REQUEST_TIMEOUT_SECONDS, MARKET_WATCHLIST,
)
from database import get_connection

logger = logging.getLogger("app.market")
SOURCE_NAME = "Alpha Vantage"
PRICE_SOURCE_URL = "https://www.alphavantage.co/documentation/#daily"


class MarketDataError(RuntimeError):
    pass


def _api(params: dict) -> dict:
    if not MARKET_DATA_LICENSE_CONFIRMED:
        raise MarketDataError(
            "Market data ingestion is disabled until a provider license for manager use is confirmed."
        )
    if not MARKET_DATA_API_KEY:
        raise MarketDataError("Set MARKET_DATA_API_KEY to enable market data refresh.")
    try:
        response = requests.get(
            MARKET_DATA_BASE_URL,
            params={**params, "apikey": MARKET_DATA_API_KEY},
            timeout=MARKET_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        # Do not include the request URL; this provider puts the API key in it.
        raise MarketDataError(f"Market provider request failed ({type(exc).__name__}).") from exc
    data = response.json()
    if not isinstance(data, dict):
        raise MarketDataError("The market data provider returned an unexpected response.")
    if data.get("Error Message") or data.get("Note") or data.get("Information"):
        message = data.get("Error Message") or data.get("Note") or data.get("Information")
        raise MarketDataError(str(message)[:500])
    return data


def _prices(symbol: str) -> list[dict]:
    data = _api({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact"})
    series_key = next((k for k in data if "Time Series" in k), None)
    if not series_key:
        raise MarketDataError(f"No daily price series was returned for {symbol}.")
    rows = []
    for day, values in sorted(data[series_key].items()):
        rows.append({
            "price_date": datetime.strptime(day, "%Y-%m-%d").date(),
            "open": float(values["1. open"]), "high": float(values["2. high"]),
            "low": float(values["3. low"]), "close": float(values["4. close"]),
            "volume": int(values["5. volume"]),
        })
    return rows


def _analysis(symbol: str, rows: list[dict]) -> dict:
    closes = [r["close"] for r in rows]
    if not closes:
        raise MarketDataError(f"No prices available for {symbol}.")
    latest = closes[-1]
    previous = closes[-2] if len(closes) > 1 else None
    sma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
    sma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(x, 0) for x in changes[-14:]]
    losses = [abs(min(x, 0)) for x in changes[-14:]]
    rsi14 = None
    if len(changes) >= 14:
        avg_gain, avg_loss = sum(gains) / 14, sum(losses) / 14
        rsi14 = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    daily_change_pct = ((latest - previous) / previous * 100) if previous else None
    returns = [(closes[i] / closes[i - 1]) - 1 for i in range(max(1, len(closes) - 20), len(closes))]
    volatility20_pct = (math.sqrt(sum(r * r for r in returns) / len(returns)) * 100) if returns else None
    trend = "insufficient history"
    if sma20 is not None:
        trend = "above 20-day average" if latest >= sma20 else "below 20-day average"
        if sma50 is not None:
            trend += "; above 50-day average" if latest >= sma50 else "; below 50-day average"
    change_text = f"{daily_change_pct:+.2f}%" if daily_change_pct is not None else "unavailable"
    rsi_text = f"{rsi14:.1f}" if rsi14 is not None else "unavailable"
    volatility_text = f"{volatility20_pct:.2f}%" if volatility20_pct is not None else "unavailable"
    summary = (
        f"{symbol}: latest daily close {latest:.4f} on {rows[-1]['price_date'].isoformat()}. "
        f"Daily change {change_text}. Trend: {trend}. "
        f"RSI(14) {rsi_text}; 20-day daily volatility {volatility_text}. "
        "Indicators describe past prices and do not predict returns."
    )
    return {
        "symbol": symbol, "price_date": rows[-1]["price_date"], "close": latest,
        "daily_change_pct": daily_change_pct, "sma20": sma20, "sma50": sma50,
        "rsi14": rsi14, "volatility20_pct": volatility20_pct, "trend": trend,
        "summary": summary, "source": SOURCE_NAME,
        "source_url": PRICE_SOURCE_URL,
    }


def _news(symbols: list[str]) -> list[dict]:
    if not symbols:
        return []
    try:
        data = _api({"function": "NEWS_SENTIMENT", "tickers": ",".join(symbols), "limit": 50})
    except Exception as exc:
        # The daily market prices and technical analysis remain useful if the
        # provider's news endpoint is unavailable or outside the free tier.
        logger.warning("Market news fetch failed: %s", exc)
        return []
    return data.get("feed", []) if isinstance(data.get("feed", []), list) else []


def _save_prices(cursor, symbol: str, rows: list[dict]) -> None:
    for row in rows:
        cursor.execute(
            """MERGE INTO market_prices dst USING (SELECT :1 symbol, :2 price_date FROM dual) src
               ON (dst.symbol = src.symbol AND dst.price_date = src.price_date)
               WHEN MATCHED THEN UPDATE SET dst.open_price=:3, dst.high_price=:4,
                 dst.low_price=:5, dst.close_price=:6, dst.volume=:7,
                 dst.source=:8, dst.fetched_at=SYSTIMESTAMP
               WHEN NOT MATCHED THEN INSERT (symbol, price_date, open_price, high_price,
                 low_price, close_price, volume, source, fetched_at)
                 VALUES (:1, :2, :3, :4, :5, :6, :7, :8, SYSTIMESTAMP)""",
            (symbol, row["price_date"], row["open"], row["high"], row["low"],
             row["close"], row["volume"], SOURCE_NAME),
        )


def _save_analysis(cursor, item: dict) -> None:
    cursor.execute(
        """MERGE INTO market_insights dst USING (SELECT :1 symbol, :2 price_date FROM dual) src
           ON (dst.symbol = src.symbol AND dst.price_date = src.price_date)
           WHEN MATCHED THEN UPDATE SET dst.close_price=:3, dst.daily_change_pct=:4,
             dst.sma20=:5, dst.sma50=:6, dst.rsi14=:7, dst.volatility20_pct=:8,
             dst.trend=:9, dst.summary=:10, dst.source=:11, dst.source_url=:12,
             dst.updated_at=SYSTIMESTAMP
           WHEN NOT MATCHED THEN INSERT (symbol, price_date, close_price, daily_change_pct,
             sma20, sma50, rsi14, volatility20_pct, trend, summary, source, source_url, updated_at)
             VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10, :11, :12, SYSTIMESTAMP)""",
        (item["symbol"], item["price_date"], item["close"], item["daily_change_pct"],
         item["sma20"], item["sma50"], item["rsi14"], item["volatility20_pct"],
         item["trend"], item["summary"], item["source"], item["source_url"]),
    )


def _index_documents(documents: list[dict]) -> int:
    if not documents:
        return 0
    from qdrant_client.models import Distance, PointStruct, VectorParams

    client = ai_qdrant.get_client()
    if not client.collection_exists(MARKET_QDRANT_COLLECTION):
        vectors = ai_qdrant._embed([d["text"] for d in documents])
        client.create_collection(
            collection_name=MARKET_QDRANT_COLLECTION,
            vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
        )
    else:
        vectors = ai_qdrant._embed([d["text"] for d in documents])
    client.upsert(
        collection_name=MARKET_QDRANT_COLLECTION, wait=True,
        points=[PointStruct(id=d["id"], vector=v, payload=d["payload"])
                for d, v in zip(documents, vectors)],
    )
    return len(documents)


_INSTRUMENTS = {
    "RELIANCE": ("Reliance Industries Limited", "Reliance, oil, telecom, retail"),
    "TCS": ("Tata Consultancy Services Limited", "Tata Consultancy Services, IT services, software"),
    "INFY": ("Infosys Limited", "Infosys, IT services, software"),
    "SUZLON": ("Suzlon Energy Limited", "Suzlon Energy, wind energy, renewable power"),
    "IDEA": ("Vodafone Idea Limited", "Vodafone Idea, telecom, cellular services"),
    "JPL": ("Jio Platforms Limited", "Jio Platforms, digital services, telecom"),
    "HDFCBANK": ("HDFC Bank Limited", "HDFC Bank, banking, financial services"),
}


def index_watchlist() -> dict:
    """Index configured instrument names and aliases without requesting prices."""
    documents = []
    for symbol in MARKET_WATCHLIST:
        ticker = symbol.split(".", 1)[0]
        name, aliases = _INSTRUMENTS.get(ticker, (ticker, ticker))
        text = f"Listed instrument: {name}. Provider symbol: {symbol}. Search terms: {aliases}."
        documents.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-instrument:{symbol}")),
            "text": text,
            "payload": {"type": "instrument", "symbol": symbol, "name": name,
                        "exchange": symbol.rsplit(".", 1)[-1] if "." in symbol else "",
                        "source": "configured watchlist", "text": text},
        })
    indexed = _index_documents(documents)
    return {"collection": MARKET_QDRANT_COLLECTION, "indexed_instruments": indexed,
            "symbols": MARKET_WATCHLIST}


def refresh_market_data(symbols: list[str] | None = None) -> dict:
    symbols = [s.strip().upper() for s in (symbols or MARKET_WATCHLIST) if s.strip()]
    if not symbols:
        raise MarketDataError("Configure MARKET_WATCHLIST with provider symbols first.")
    if not MARKET_DATA_API_KEY:
        raise MarketDataError("Set MARKET_DATA_API_KEY to enable market data refresh.")

    insights, documents, failures = [], [], []
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            for symbol in symbols:
                try:
                    rows = _prices(symbol)
                    _save_prices(cursor, symbol, rows)
                    item = _analysis(symbol, rows)
                    _save_analysis(cursor, item)
                    insights.append(item)
                    documents.append({
                        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"market:{symbol}:{item['price_date']}")),
                        "text": item["summary"],
                        "payload": {"type": "technical_analysis", "symbol": symbol,
                                    "price_date": item["price_date"].isoformat(),
                                    "source": SOURCE_NAME, "source_url": item["source_url"],
                                    "text": item["summary"]},
                    })
                except Exception as exc:
                    logger.exception("Market price refresh failed for %s", symbol)
                    failures.append({"symbol": symbol, "error": str(exc)[:300]})

            for article in _news(symbols):
                title = str(article.get("title") or "").strip()
                url = str(article.get("url") or "").strip()
                if not title or not url:
                    continue
                text = f"{title}\n{article.get('summary', '')}\nSentiment: {article.get('overall_sentiment_label', 'unknown')}"
                documents.append({
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-news:{url}")),
                    "text": text,
                    "payload": {"type": "market_news", "symbol": ",".join(
                        x.get("ticker", "") for x in article.get("ticker_sentiment", []) if x.get("ticker")),
                        "title": title, "url": url, "source": article.get("source", SOURCE_NAME),
                        "published_at": article.get("time_published"), "text": text},
                })
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    indexed = _index_documents(documents)
    return {"status": "SUCCESS" if insights and not failures else "PARTIAL" if insights else "FAILED",
            "updated": len(insights), "failed": failures, "indexed_documents": indexed,
            "collection": MARKET_QDRANT_COLLECTION,
            "as_of": max((i["price_date"] for i in insights), default=date.today()).isoformat()}


def get_overview(cursor, limit: int = 200) -> dict:
    cursor.execute(
        """SELECT symbol, price_date, close_price, daily_change_pct, sma20, sma50,
                  rsi14, volatility20_pct, trend, summary, source, source_url
           FROM (SELECT latest.* FROM
                   (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY price_date DESC) rn
                    FROM market_insights m) latest
                 WHERE latest.rn=1 ORDER BY latest.symbol)
           WHERE ROWNUM <= :1""", (limit,)
    )
    columns = [d[0].lower() for d in cursor.description]
    items = [dict(zip(columns, row)) for row in cursor.fetchall()]
    for item in items:
        if item.get("price_date"):
            item["price_date"] = item["price_date"].isoformat()
    cursor.execute("SELECT MAX(fetched_at) FROM market_prices")
    fetched = cursor.fetchone()[0]
    for item in items:
        if hasattr(item.get("summary"), "read"):
            item["summary"] = item["summary"].read()
    return {"items": items, "last_updated": fetched.isoformat() if fetched else None,
            "provider": SOURCE_NAME, "disclaimer": "Daily historical market data and technical indicators are informational only; not investment advice."}


def search_analysis(query: str, symbol: str | None = None, limit: int = 8) -> list[dict]:
    client = ai_qdrant.get_client()
    if not client.collection_exists(MARKET_QDRANT_COLLECTION):
        return []
    vector = ai_qdrant._embed([query])[0]
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    query_filter = Filter(must=[FieldCondition(key="symbol", match=MatchValue(value=symbol))]) if symbol else None
    matches = client.query_points(collection_name=MARKET_QDRANT_COLLECTION, query=vector,
                                  query_filter=query_filter, limit=limit, with_payload=True).points
    return [{**p.payload, "score": round(float(p.score), 4)} for p in matches]


def run_refresh_job(run_id: str) -> None:
    try:
        result = refresh_market_data()
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE market_refresh_runs SET status=:1, result=:2, finished_at=SYSTIMESTAMP WHERE run_id=:3",
                        (result["status"], json.dumps(result), run_id))
            conn.commit()
            cur.close()
    except Exception as exc:
        logger.exception("Market refresh job %s failed", run_id)
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE market_refresh_runs SET status='FAILED', error_message=:1, finished_at=SYSTIMESTAMP WHERE run_id=:2",
                            (str(exc)[:3000], run_id))
                conn.commit()
                cur.close()
        except Exception:
            logger.exception("Could not record market refresh failure")
