"""Feature-local stock dashboard backed by PostgreSQL and official company pages."""
import json
import logging
import math

import stock_database

logger = logging.getLogger("app.stock_intelligence")

STOCKS = {
    "SUZLON": {"name": "Suzlon Energy Limited", "sector": "Renewable energy", "ir_url": "https://www.suzlon.com/investors/financial-reports/"},
    "TCS": {"name": "Tata Consultancy Services Limited", "sector": "Information technology", "ir_url": "https://www.tcs.com/investor-relations"},
    "INFY": {"name": "Infosys Limited", "sector": "Information technology", "ir_url": "https://www.infosys.com/Investors/"},
    "RELIANCE": {"name": "Reliance Industries Limited", "sector": "Diversified", "ir_url": "https://www.ril.com/investors/shareholders-information/investor-contacts"},
    "HDFCBANK": {"name": "HDFC Bank Limited", "sector": "Banking", "ir_url": "https://www.hdfc.bank.in/about-us/investor-relations"},
    "IDEA": {"name": "Vodafone Idea Limited", "sector": "Telecommunications", "ir_url": "https://www.myvi.in/investors"},
    "JPL": {"name": "Jio Platforms Limited", "sector": "Telecommunications", "ir_url": "https://www.ril.com/investors"},
}


def search(q: str = "") -> list[dict]:
    q = q.strip().casefold()
    return [{"symbol": s, **v} for s, v in STOCKS.items()
            if not q or q in s.casefold() or q in v["name"].casefold()]


def _date(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _number(v):
    return float(v) if v is not None else None


def _indicators(rows):
    closes = [float(r["close"]) for r in rows if r["close"] is not None]
    if not closes:
        return {}
    changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(x, 0) for x in changes[-14:]]
    losses = [abs(min(x, 0)) for x in changes[-14:]]
    avg_g = sum(gains) / 14 if len(gains) == 14 else None
    avg_l = sum(losses) / 14 if len(losses) == 14 else None
    rsi = (100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)) if avg_g is not None else None
    returns = [closes[i] / closes[i-1] - 1 for i in range(max(1, len(closes)-20), len(closes)) if closes[i-1]]
    vol = math.sqrt(sum(x*x for x in returns) / len(returns)) * 100 if returns else None
    return {"sma20": sum(closes[-20:])/20 if len(closes)>=20 else None,
            "sma50": sum(closes[-50:])/50 if len(closes)>=50 else None, "rsi14": rsi,
            "volatility20_pct": vol, "high_52w": max(closes[-252:]), "low_52w": min(closes[-252:]),
            "daily_change_pct": (closes[-1]/closes[-2]-1)*100 if len(closes)>1 and closes[-2] else None}


def dashboard(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if symbol not in STOCKS:
        raise ValueError("Choose a supported stock symbol.")
    with stock_database.connect_stock_db() as conn:
        prices = conn.execute("""SELECT trade_date, open_price, high_price, low_price, close_price, volume, source, source_url, captured_at
            FROM stock_price_history WHERE symbol=? ORDER BY trade_date DESC LIMIT 500""", (symbol,)).fetchall()
        docs = conn.execute("""SELECT document_type,title,source_url,published_at,content,extracted,captured_at
            FROM stock_company_documents WHERE symbol=? ORDER BY COALESCE(published_at,captured_at) DESC LIMIT 30""", (symbol,)).fetchall()
        exposures = conn.execute("""SELECT fund_code,fund_name,exposure_pct,as_of_date,source,source_url
            FROM stock_fund_exposures WHERE symbol=? ORDER BY as_of_date DESC LIMIT 50""", (symbol,)).fetchall()
    price_rows = [{"date": _date(r[0]), "open": _number(r[1]), "high": _number(r[2]), "low": _number(r[3]),
                   "close": _number(r[4]), "volume": r[5], "source": r[6], "source_url": r[7], "captured_at": _date(r[8])} for r in reversed(prices)]
    documents = []
    for r in docs:
        extracted = r[5] if isinstance(r[5], dict) else (json.loads(r[5]) if r[5] else {})
        documents.append({"type": r[0], "title": r[1], "source_url": r[2], "published_at": _date(r[3]),
                          "content": r[4], "extracted": extracted, "captured_at": _date(r[6])})
    latest = price_rows[-1] if price_rows else None
    return {"stock": {"symbol": symbol, **STOCKS[symbol]}, "overview": latest,
            "prices": price_rows, "technicals": _indicators(price_rows),
            "fundamentals": [d for d in documents if d["type"] == "fundamentals"],
            "news": [d for d in documents if d["type"] in ("news", "announcement")],
            "documents": documents, "exposure": [{"fund_code": r[0], "fund_name": r[1], "exposure_pct": _number(r[2]),
                "as_of_date": _date(r[3]), "source": r[4], "source_url": r[5]} for r in exposures],
            "sector_context": {"sector": STOCKS[symbol]["sector"], "note": "Sector peer data is not configured."},
            "sources": list({(p["source"],p["source_url"]) for p in price_rows} | {(d["type"],d["source_url"]) for d in documents}),
            "last_updated": max([p["captured_at"] for p in price_rows if p["captured_at"]] + [d["captured_at"] for d in documents if d["captured_at"]], default=None)}


def ai_insights(symbol: str) -> dict:
    import ai_llm
    data = dashboard(symbol)
    retrieved = []
    try:
        from stock_intelligence_qdrant import search as search_stock_docs
        retrieved = search_stock_docs(symbol.upper(), "company financial results business risks announcements", limit=5)
    except Exception:
        logger.info("Stock Intelligence Qdrant is unavailable; using PostgreSQL documents")
    evidence = retrieved or data["documents"][:5]
    docs = "\n".join(f"{d.get('title', 'Company source')} ({d.get('source_url', '')}): {d.get('text', d.get('content', ''))[:1800]}" for d in evidence)
    answer = ai_llm.chat_json([
        {"role": "system", "content": "Create concise informational stock context for a fund manager. Never recommend buying or selling, never invent facts, distinguish missing data, and include citations using only supplied source URLs. Return JSON with summary (string), observations (array of strings), risks_to_monitor (array of strings), sources (array of {title,url})."},
        {"role": "user", "content": json.dumps({"stock": data["stock"], "overview": data["overview"], "technicals": data["technicals"], "company_documents": docs}, default=str)}
    ], max_tokens=1200)
    return {"symbol": symbol.upper(), **answer}
