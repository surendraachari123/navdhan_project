# NSE to PostgreSQL collector

This is a standalone collector for the personal/educational demo. It uses NSE's published Streamable HTTP MCP endpoint for daily stock history and stores each response as JSONB in PostgreSQL. It does not scrape NSE HTML, use Oracle, or write to Qdrant.

## Setup

1. Install dependencies from `requirements.txt`.
2. Copy `.env.market.example` values into the environment or `.env`; set `MARKET_PG_DSN` to your PostgreSQL connection string. `NSE_MCP_URL` can point to an accessible Streamable HTTP endpoint exposed by your Docker MCP server.
3. Run the collector from `server_api`:

   ```powershell
   python nse_postgres_ingestor.py
   ```

The script discovers the NSE stock-history tool, calls it for each symbol in `NSE_MCP_WATCHLIST`, creates `market_observations` if needed, and inserts the returned tool output into its `payload` JSONB column. Repeated identical results are ignored using a content hash. Schedule the same command with Task Scheduler or cron for recurring collection.

The NSE MCP is intended for educational and informational use, not commercial deployment or investment decisions. For a different Docker MCP server, set its Streamable HTTP endpoint in `NSE_MCP_URL`; its tool schema must expose a stock-history tool with a recognizable symbol argument.
