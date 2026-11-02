"""Permitted source collector: NSE MCP for history, Crawl4AI for company IR content."""
import asyncio
import hashlib
import json
import logging
import re
from datetime import date
from urllib.parse import urlparse

import stock_database
import stock_intelligence_service as service
from nse_postgres_ingestor import MCP_URL, SOURCE as NSE_SOURCE, StreamableHttpMCP, _history_tool, _arguments_for

logger = logging.getLogger("app.stock_intelligence.collector")
NSE_URL = MCP_URL


def _records(value):
    """Find OHLCV-like row objects in MCP structured or text content."""
    found = []
    if isinstance(value, dict):
        keys = {re.sub(r"[^a-z]", "", str(k).lower()) for k in value}
        if keys.intersection({"date", "tradedate", "timestamp"}) and keys.intersection({"close", "closeprice", "closingprice", "lastprice"}):
            found.append(value)
        for child in value.values():
            found.extend(_records(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_records(child))
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
            if parsed is not value:
                found.extend(_records(parsed))
        except (ValueError, TypeError):
            pass
    return found


def _field(row, names):
    normalized = {re.sub(r"[^a-z]", "", str(k).lower()): v for k, v in row.items()}
    return next((normalized[n] for n in names if normalized.get(n) not in (None, "")), None)


def _normalize_prices(payload):
    result = []
    for row in _records(payload):
        try:
            day = str(_field(row, ("tradedate", "date", "timestamp")))[:10]
            result.append({
                "date": date.fromisoformat(day),
                "open": float(_field(row, ("open", "openprice", "openingprice")) or 0) or None,
                "high": float(_field(row, ("high", "highprice")) or 0) or None,
                "low": float(_field(row, ("low", "lowprice")) or 0) or None,
                "close": float(_field(row, ("close", "closeprice", "closingprice", "lastprice"))),
                "volume": int(float(_field(row, ("volume", "totaltradedvolume", "tradedvolume")) or 0)) or None,
            })
        except (ValueError, TypeError):
            continue
    unique = {r["date"]: r for r in result}
    return [unique[d] for d in sorted(unique)]


def _crawl_url(symbol: str) -> str:
    import os
    configured = os.getenv("STOCK_CRAWL_SOURCES_JSON", "")
    if configured:
        try:
            override = json.loads(configured).get(symbol)
            if override:
                return override
        except (ValueError, AttributeError):
            logger.warning("Ignoring malformed STOCK_CRAWL_SOURCES_JSON")
    return service.STOCKS[symbol]["ir_url"]


async def _crawl(symbol: str, url: str) -> list[dict]:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
    config = CrawlerRunConfig(check_robots_txt=True, word_count_threshold=30, page_timeout=30000)
    async with AsyncWebCrawler() as crawler:
        root = await crawler.arun(url=url, config=config)
        pages = []
        candidates = [url]
        root_links = getattr(root, "links", {}) or {}
        internal = root_links.get("internal", []) if isinstance(root_links, dict) else []
        keywords = ("news", "announcement", "press", "media", "result", "financial", "investor")
        host = urlparse(url).netloc
        for link in internal:
            href = link.get("href", "") if isinstance(link, dict) else ""
            parsed = urlparse(href)
            if parsed.scheme in ("http", "https") and parsed.netloc == host and any(k in parsed.path.casefold() for k in keywords):
                if href not in candidates:
                    candidates.append(href)
            if len(candidates) >= 4:
                break
        for candidate in candidates:
            page = root if candidate == url else await crawler.arun(url=candidate, config=config)
            if not getattr(page, "success", False):
                logger.info("Crawl skipped/failed for %s (%s)", candidate, getattr(page, "error_message", "robots or fetch"))
                continue
            markdown = getattr(page, "markdown", "")
            if not isinstance(markdown, str):
                markdown = getattr(markdown, "raw_markdown", "") or str(markdown)
            text = markdown.strip()[:60000]
            if text:
                pages.append({"title": (getattr(page, "metadata", {}) or {}).get("title") or service.STOCKS[symbol]["name"],
                              "url": candidate, "content": text})
        return pages


def _extract(doc: dict) -> tuple[str, dict]:
    """Use the configured LLM only for source-grounded classification/extraction."""
    try:
        import ai_llm
        result = ai_llm.chat_json([
            {"role": "system", "content": "Extract facts from this company investor-relations page. Do not infer or invent. Return JSON: document_type (fundamentals, announcement, or news), published_at (ISO date or null), facts (array of {label,value}), summary (brief string). If the page only provides navigation, return empty facts and an empty summary."},
            {"role": "user", "content": f"Source: {doc['url']}\nTitle: {doc['title']}\nContent:\n{doc['content'][:16000]}"},
        ], max_tokens=1000)
        kind = result.get("document_type", "announcement")
        if not result.get("facts") and not result.get("summary"):
            kind = "company_page"
        if kind not in {"fundamentals", "announcement", "news"}:
            kind = "company_page"
        return kind, result
    except Exception:
        logger.info("Optional source extraction unavailable; keeping crawled source content")
        return "company_page", {}


def _crawl_and_store(symbol: str) -> int:
    url = _crawl_url(symbol)
    try:
        pages = asyncio.run(_crawl(symbol, url))
    except RuntimeError:
        # BackgroundTasks normally runs in an event loop. Use a new thread-safe loop in that case.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            pages = executor.submit(lambda: asyncio.run(_crawl(symbol, url))).result()
    if not pages:
        return 0
    saved = 0
    for doc in pages:
        kind, extracted = _extract(doc)
        digest = hashlib.sha256(doc["content"].encode()).hexdigest()
        published = extracted.get("published_at") if extracted else None
        if published:
            try:
                published = date.fromisoformat(str(published)[:10])
            except ValueError:
                published = None
        with stock_database.connect_stock_db() as conn:
            conn.execute("""INSERT INTO stock_company_documents(symbol,document_type,title,source_url,published_at,content,extracted,content_hash)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,source_url,content_hash) DO NOTHING""",
              (symbol, kind, doc["title"], doc["url"], published, doc["content"], json.dumps(extracted), digest))
        try:
            from stock_intelligence_qdrant import index_document
            index_document(symbol, f"{doc['title']}\n{doc['content']}", {"title": doc["title"], "source_url": doc["url"], "document_type": kind})
        except Exception:
            logger.info("Separate Stock Intelligence Qdrant collection is unavailable; SQLite write succeeded")
        saved += 1
    return saved


def collect_stock(symbol: str) -> dict:
    symbol = symbol.upper()
    if symbol not in service.STOCKS:
        raise ValueError("Unsupported stock symbol")
    mcp = StreamableHttpMCP(MCP_URL)
    mcp.connect()
    tool = _history_tool(mcp.list_tools())
    arguments = _arguments_for(tool, symbol)
    payload = mcp.call_tool(tool["name"], arguments)
    prices = _normalize_prices(payload)
    if not prices:
        raise RuntimeError(f"NSE MCP returned no parseable OHLCV rows for {symbol}; raw data was not fabricated.")
    with stock_database.connect_stock_db() as conn:
        for row in prices:
            conn.execute("""INSERT INTO stock_price_history(symbol,trade_date,open_price,high_price,low_price,close_price,volume,source,source_url)
              VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET
              open_price=EXCLUDED.open_price,high_price=EXCLUDED.high_price,low_price=EXCLUDED.low_price,
              close_price=EXCLUDED.close_price,volume=EXCLUDED.volume,source=EXCLUDED.source,source_url=EXCLUDED.source_url,captured_at=CURRENT_TIMESTAMP""",
              (symbol,row["date"],row["open"],row["high"],row["low"],row["close"],row["volume"],NSE_SOURCE,NSE_URL))
    crawled = 0
    failures = []
    try:
        crawled = _crawl_and_store(symbol)
    except Exception as exc:
        logger.warning("Crawl content unavailable for %s (%s)", symbol, type(exc).__name__)
        failures.append(f"Company page crawl unavailable ({type(exc).__name__}).")
    if not prices:
        failures.append("No price history was returned.")
    elif not crawled and not failures:
        failures.append("No permitted company pages were collected.")
    return {"symbol": symbol, "price_rows": len(prices), "crawled_pages": crawled, "failures": failures}
