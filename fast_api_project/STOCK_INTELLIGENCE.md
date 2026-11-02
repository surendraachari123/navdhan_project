# Stock Intelligence

This feature runs on a local SQLite database by default and remains separate from Market Research and the existing Oracle tables. SQLite creates its four `stock_*` tables automatically the first time the API opens the database.

## Local setup

1. Install the API dependencies from `requirements.txt`.
2. Start the FastAPI app as usual. The database is created at `server_api/stock_intelligence.sqlite3`.
3. To store the database elsewhere, set `STOCK_SQLITE_PATH` in the API environment to an absolute file path.
4. Log in as an admin or manager and open `/stock-intelligence`. Managers can view/search/generate insights; admins can refresh sources.

The first dashboard load may be empty until an admin opens a company and selects **Refresh sources**. The top of each dashboard also fetches an NSE delayed live quote from the CM Market MCP (`NSE_LIVE_MCP_URL`, default `https://mcp.nseindia.in/cmmkt/mcp`) and refreshes it while the page is open. NSE describes its intraday data as near real-time with a 1–3 minute delay; it is not tick-by-tick. The historical chart and technical indicators remain based on daily closing prices from the Bhavcopy MCP (`NSE_MCP_URL`). Company pages are crawled only when permitted by their robots policy. Source fetches can fail independently; the dashboard records available data and refresh status.

The default company pages are official investor-relations pages. To change a source, provide a JSON object in `STOCK_CRAWL_SOURCES_JSON`, for example `{"SUZLON":"https://www.suzlon.com/investors/financial-reports/"}`. Crawl4AI robots checking is always enabled.

Company-page text and extracted facts are stored in SQLite. Qdrant indexing and LLM extraction are optional. Mutual-fund holdings are not inferred from investor transactions; the exposure section stays empty until verified dated holdings are inserted into `stock_fund_exposures`.

## PostgreSQL migration

`server_api/stock_intelligence.sql` contains the matching PostgreSQL schema for a later migration. Keep a copy of `server_api/stock_intelligence.sqlite3` as the local source database; PostgreSQL support and a data-copy step can be added when you're ready to migrate.

## Supported stock symbols

`SUZLON` (default), `TCS`, `INFY`, `RELIANCE`, `HDFCBANK`, `IDEA`, and `JPL`.
