"""Collect watchlist history from NSE's educational MCP and save it to PostgreSQL.

This uses NSE's documented Streamable HTTP MCP endpoints, not HTML scraping.
The collector discovers the server's tool schema at runtime so the MCP tool
name and argument spelling are not hard-coded.
"""
import hashlib
import json
import logging
import os
import re
import time
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

MCP_URL = os.getenv("NSE_MCP_URL", "https://mcp.nseindia.in/bhavcopy/cm/mcp")
PG_DSN = os.getenv("MARKET_PG_DSN")
WATCHLIST = [
    symbol.strip().upper().split(".", 1)[0]
    for symbol in os.getenv("NSE_MCP_WATCHLIST", "SUZLON,IDEA,JPL,HDFCBANK").split(",")
    if symbol.strip()
]
REQUEST_TIMEOUT = int(os.getenv("NSE_MCP_TIMEOUT_SECONDS", "60"))
PROTOCOL_VERSION = "2025-03-26"
SOURCE = "NSE MCP"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("nse_postgres_ingestor")


class MCPError(RuntimeError):
    pass


class StreamableHttpMCP:
    def __init__(self, url: str):
        self.url = url
        self.session_id = None
        self.protocol_version = PROTOCOL_VERSION
        self._request_id = 0
        self._http = requests.Session()
        self._http.headers.update({
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        })

    def _parse_response(self, response):
        response.raise_for_status()
        if not response.content:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "text/event-stream" not in content_type:
            body = response.json()
            if isinstance(body, list):
                return body[-1] if body else None
            return body

        messages = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw and raw != "[DONE]":
                    try:
                        messages.append(json.loads(raw))
                    except json.JSONDecodeError:
                        continue
        return messages[-1] if messages else None

    def _post(self, payload: dict):
        headers = {}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = self.protocol_version
        for attempt in range(4):
            try:
                response = self._http.post(
                    self.url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
                )
                self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
                result = self._parse_response(response)
                break
            except requests.HTTPError as exc:
                response = exc.response
                # NSE's MCP is occasionally served by a node that does not
                # recognize the session created by another node. Retry 404s;
                # other HTTP errors are not treated as transient.
                if response is not None and response.status_code == 404 and attempt < 3:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                detail = (response.text or "").strip().replace("\n", " ")[:300] if response is not None else ""
                status = response.status_code if response is not None else "unknown"
                suffix = f": {detail}" if detail else ""
                raise MCPError(f"NSE MCP returned HTTP {status}{suffix}") from None
            except requests.RequestException as exc:
                raise MCPError(f"Could not reach the configured MCP endpoint ({type(exc).__name__}).") from None
        else:  # pragma: no cover - loop always breaks or raises
            raise MCPError("NSE MCP request exhausted its retry attempts.")
        if isinstance(result, dict) and result.get("error"):
            error = result["error"]
            raise MCPError(f"MCP request failed: {error.get('message', 'unknown error')}")
        return result

    def connect(self):
        self._request_id += 1
        result = self._post({
            "jsonrpc": "2.0", "id": self._request_id, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "navdhan-market-ingestor", "version": "1.0.0"},
            },
        })
        if not isinstance(result, dict) or "result" not in result:
            raise MCPError("MCP endpoint did not return an initialize result.")
        self.protocol_version = result["result"].get("protocolVersion", PROTOCOL_VERSION)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            self._request_id += 1
            params = {"cursor": cursor} if cursor else {}
            response = self._post({
                "jsonrpc": "2.0", "id": self._request_id,
                "method": "tools/list", "params": params,
            })
            result = response.get("result", {}) if isinstance(response, dict) else {}
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict):
        self._request_id += 1
        response = self._post({
            "jsonrpc": "2.0", "id": self._request_id,
            "method": "tools/call", "params": {"name": name, "arguments": arguments},
        })
        if not isinstance(response, dict) or "result" not in response:
            raise MCPError(f"Tool '{name}' returned no result.")
        result = response["result"]
        if result.get("isError"):
            text = " ".join(str(c.get("text", "")) for c in result.get("content", []))
            raise MCPError(text or f"Tool '{name}' failed.")
        return result


def _history_tool(tools: list[dict]) -> dict:
    candidates = []
    for tool in tools:
        description = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
        if "history" in description and ("stock" in description or "security" in description or "equity" in description):
            candidates.append(tool)
    if not candidates:
        names = ", ".join(t.get("name", "?") for t in tools[:30])
        raise MCPError(f"No stock-history tool was found. Tools reported by MCP: {names}")
    candidates.sort(key=lambda t: ("stock" not in t.get("name", "").lower(), len(t.get("name", ""))))
    return candidates[0]


def _arguments_for(tool: dict, symbol: str) -> dict:
    schema = tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    args = {}
    symbol_fields = {"symbol", "ticker", "stock", "stock_symbol", "security", "security_symbol", "symbol_name"}
    today = date.today()
    date_values = {
        "from": (today - timedelta(days=90)).isoformat(),
        "start": (today - timedelta(days=90)).isoformat(),
        "start_date": (today - timedelta(days=90)).isoformat(),
        "startdate": (today - timedelta(days=90)).isoformat(),
        "from_date": (today - timedelta(days=90)).isoformat(),
        "fromdate": (today - timedelta(days=90)).isoformat(),
        "to": today.isoformat(), "end": today.isoformat(),
        "end_date": today.isoformat(), "enddate": today.isoformat(),
        "to_date": today.isoformat(), "todate": today.isoformat(), "date": today.isoformat(),
    }
    for field, definition in properties.items():
        normalized = re.sub(r"[^a-z0-9_]", "", field.lower())
        if normalized in symbol_fields:
            args[field] = symbol
        elif normalized in date_values:
            args[field] = date_values[normalized]
        elif normalized == "months" and definition.get("type") == "integer":
            # NSE's history tool currently caps a request at three months.
            args[field] = min(3, int(definition.get("maximum", 3)))
        elif normalized in {"limit", "count", "days"} and definition.get("type") == "integer":
            args[field] = min(90, int(definition.get("maximum", 90)))
        elif normalized == "period" and definition.get("type") == "integer":
            args[field] = min(90, int(definition.get("maximum", 90)))

    missing = [field for field in required if field not in args]
    if missing:
        raise MCPError(
            f"History tool '{tool.get('name')}' requires unsupported fields: {', '.join(missing)}. "
            "Inspect the MCP tool schema and add mappings in _arguments_for()."
        )
    if not any(re.sub(r"[^a-z0-9_]", "", key.lower()) in symbol_fields for key in args):
        raise MCPError(f"History tool '{tool.get('name')}' has no recognized stock-symbol argument.")
    return args


def _ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_observations (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            content_hash TEXT NOT NULL,
            payload JSONB NOT NULL,
            UNIQUE (symbol, content_hash)
        )
    """)


def collect_and_store() -> dict:
    if not PG_DSN:
        raise RuntimeError("Set MARKET_PG_DSN in the environment or .env before running the collector.")
    if not WATCHLIST:
        raise RuntimeError("Set NSE_MCP_WATCHLIST to at least one NSE symbol.")

    import psycopg
    from psycopg.types.json import Jsonb

    mcp = StreamableHttpMCP(MCP_URL)
    mcp.connect()
    tool = _history_tool(mcp.list_tools())
    inserted, duplicates, failures = [], [], []

    try:
        conn = psycopg.connect(PG_DSN)
    except psycopg.OperationalError:
        raise RuntimeError("Could not connect to PostgreSQL; check MARKET_PG_DSN and that PostgreSQL is running.") from None

    with conn:
        _ensure_table(conn)
        for symbol in WATCHLIST:
            try:
                arguments = _arguments_for(tool, symbol)
                payload = mcp.call_tool(tool["name"], arguments)
                canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
                digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                with conn.transaction():
                    cursor = conn.execute(
                        """INSERT INTO market_observations
                           (symbol, source, tool_name, content_hash, payload)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (symbol, content_hash) DO NOTHING
                           RETURNING id""",
                        (symbol, SOURCE, tool["name"], digest, Jsonb(payload)),
                    )
                    if cursor.fetchone():
                        inserted.append(symbol)
                    else:
                        duplicates.append(symbol)
                logger.info("Collected %s with MCP tool %s", symbol, tool["name"])
                time.sleep(0.3)
            except Exception as exc:
                logger.exception("Collection failed for %s", symbol)
                failures.append({"symbol": symbol, "error": str(exc)[:500]})

    conn.close()
    return {
        "source": SOURCE, "endpoint": MCP_URL, "tool": tool["name"],
        "inserted": inserted, "unchanged": duplicates, "failed": failures,
    }


if __name__ == "__main__":
    print(json.dumps(collect_and_store(), indent=2))
