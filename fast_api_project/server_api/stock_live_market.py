"""NSE delayed live quote retrieval and local cache."""
import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()

import stock_database
from nse_postgres_ingestor import StreamableHttpMCP

logger = logging.getLogger("app.stock_intelligence.live_market")
MCP_URL = os.getenv("NSE_LIVE_MCP_URL", "https://mcp.nseindia.in/cmmkt/mcp")
SOURCE_URL = "https://www.nseindia.com/nse-mcp"
CACHE_SECONDS = 45


def _cached_quote(symbol):
    with stock_database.connect_stock_db() as conn:
        row = conn.execute("""SELECT symbol,last_price,open_price,high_price,low_price,previous_close,
            change_amount,change_percent,volume,latest_timestamp,provider_updated_at,captured_at,source_url
            FROM stock_live_quotes WHERE symbol=? AND captured_at >= datetime('now', ?)
            ORDER BY captured_at DESC,id DESC LIMIT 1""", (symbol, f"-{CACHE_SECONDS} seconds")).fetchone()
    return _quote_dict(row) if row else None


def _quote_dict(row):
    return {"symbol": row[0], "last_price": row[1], "open": row[2], "high": row[3],
            "low": row[4], "previous_close": row[5], "change": row[6],
            "change_percent": row[7], "volume": row[8], "latest_timestamp": row[9],
            "provider_updated_at": row[10], "captured_at": row[11], "source_url": row[12]}


def fetch_live_quote(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    cached = _cached_quote(symbol)
    if cached:
        return cached

    client = StreamableHttpMCP(MCP_URL)
    client.connect()
    tools = client.list_tools()
    tool = next((item for item in tools if item.get("name") == "cm_get_stock_quote"), None)
    if not tool:
        raise RuntimeError("NSE live-market MCP does not provide cm_get_stock_quote.")
    response = client.call_tool(tool["name"], {"symbol": symbol})
    payload = None
    for item in response.get("content", []):
        if item.get("type") == "text":
            try:
                payload = json.loads(item.get("text", ""))
                break
            except (ValueError, TypeError):
                continue
    stock = payload.get("stock") if isinstance(payload, dict) else None
    if not isinstance(stock, dict) or stock.get("lastTradedPrice") is None:
        raise RuntimeError(f"NSE returned no live quote for {symbol}.")

    values = (symbol, float(stock["lastTradedPrice"]), stock.get("openPrice"), stock.get("highPrice"),
              stock.get("lowPrice"), stock.get("preClosePrice"), stock.get("change"), stock.get("perChange"),
              stock.get("volume"), stock.get("latestTimestamp"), payload.get("updatedAt"), SOURCE_URL)
    with stock_database.connect_stock_db() as conn:
        conn.execute("""INSERT INTO stock_live_quotes(symbol,last_price,open_price,high_price,low_price,
            previous_close,change_amount,change_percent,volume,latest_timestamp,provider_updated_at,source_url)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", values)
        row = conn.execute("""SELECT symbol,last_price,open_price,high_price,low_price,previous_close,
            change_amount,change_percent,volume,latest_timestamp,provider_updated_at,captured_at,source_url
            FROM stock_live_quotes WHERE symbol=? ORDER BY captured_at DESC,id DESC LIMIT 1""", (symbol,)).fetchone()
    return _quote_dict(row)
