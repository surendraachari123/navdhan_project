# Market Research feature

The manager dashboard is `web_client/market.html`. Managers and admins can view daily prices, derived indicators, and search indexed summaries/news. Admins can start an on-demand refresh. A separate one-shot runner supports daily scheduling.

## Storage and flow

- Alpha Vantage supplies daily OHLCV and its news sentiment feed.
- Oracle `MARKET_PRICES` stores dated prices; `MARKET_INSIGHTS` stores daily derived indicators and summaries.
- Qdrant uses a separate `MARKET_QDRANT_COLLECTION` for semantic search over generated indicator summaries and provider news. It does not replace Oracle for exact prices.
- `POST /market/refresh` is admin-only and runs in the background. `GET /market/refresh/{run_id}` reports progress. `GET /market/overview` and `GET /market/search` are available to managers and admins.
- `POST /market/index-watchlist` is admin-only and indexes configured stock names and aliases into the market Qdrant collection without fetching any market data or requiring a market-data key.

## Enable after arranging data rights

1. Obtain a market-data license that allows use by the fund manager organization and display to its managers. Do not use the Alpha Vantage free/personal key for this purpose: its terms exclude corporate and money-manager affiliations. Yahoo Finance automated scraping is also prohibited by its terms.
2. Set `MARKET_DATA_API_KEY`, configure `MARKET_WATCHLIST` with provider ticker symbols, and set `MARKET_DATA_LICENSE_CONFIRMED=true` only after the appropriate rights are in place. The Alpha Vantage adapter expects its documented response format. Its free endpoint limit is 25 requests/day, but that free tier is not suitable for this manager use case.
3. Run `server_api/market_data.sql` once against the application Oracle schema.
4. Configure Qdrant as for the AI feature and install the existing `requirements.txt` dependencies.
5. Start the API and use **Market Research → Refresh now** as an admin.

To add only the configured ticker names to Qdrant, start the Qdrant service and use **Market Research → Index watchlist** as an admin. This creates or updates the separate `navdhan_market_analysis` collection; it does not add stock tables to the NavDhan AI SQL glossary collection.

For scheduled daily updates, configure Windows Task Scheduler or cron to run `python market_data_runner.py` once after the market close, with the working directory set to `fast_api_project/server_api` and the same environment variables as the API process. The job is idempotent by symbol and price date. A manual refresh is also available in the admin dashboard.

The free Alpha Vantage key is limited to 25 API requests per day. Each refresh uses one price request per configured symbol plus a news request; keep the watchlist within the provider's quota. The service computes SMA(20), SMA(50), RSI(14), and 20-day realized volatility from daily historical closes. These are descriptive indicators, not forecasts or investment advice.
