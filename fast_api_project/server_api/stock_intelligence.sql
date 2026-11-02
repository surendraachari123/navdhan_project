-- New Stock Intelligence objects only. Apply once to the existing PostgreSQL database.
CREATE TABLE IF NOT EXISTS stock_price_history (
    symbol        TEXT NOT NULL,
    trade_date    DATE NOT NULL,
    open_price    NUMERIC(20,6),
    high_price    NUMERIC(20,6),
    low_price     NUMERIC(20,6),
    close_price   NUMERIC(20,6),
    volume        BIGINT,
    source        TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS stock_company_documents (
    id             BIGSERIAL PRIMARY KEY,
    symbol         TEXT NOT NULL,
    document_type  TEXT NOT NULL,
    title          TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    published_at   TIMESTAMPTZ,
    content        TEXT NOT NULL,
    extracted      JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_hash   TEXT NOT NULL,
    captured_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, source_url, content_hash)
);

CREATE INDEX IF NOT EXISTS ix_stock_company_documents_recent
    ON stock_company_documents (symbol, captured_at DESC);

CREATE TABLE IF NOT EXISTS stock_fund_exposures (
    symbol        TEXT NOT NULL,
    fund_code     TEXT NOT NULL,
    fund_name     TEXT NOT NULL,
    exposure_pct  NUMERIC(12,6),
    as_of_date    DATE NOT NULL,
    source        TEXT NOT NULL,
    source_url    TEXT,
    captured_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, fund_code, as_of_date)
);

CREATE TABLE IF NOT EXISTS stock_intelligence_refresh_runs (
    run_id          UUID PRIMARY KEY,
    symbol          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'RUNNING',
    price_rows      INTEGER NOT NULL DEFAULT 0,
    crawled_pages   INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS stock_live_quotes (
    id                  BIGSERIAL PRIMARY KEY,
    symbol              TEXT NOT NULL,
    last_price          NUMERIC(20,6) NOT NULL,
    open_price          NUMERIC(20,6),
    high_price          NUMERIC(20,6),
    low_price           NUMERIC(20,6),
    previous_close      NUMERIC(20,6),
    change_amount       NUMERIC(20,6),
    change_percent      NUMERIC(12,6),
    volume              BIGINT,
    latest_timestamp    TEXT,
    provider_updated_at TIMESTAMPTZ,
    source_url          TEXT NOT NULL,
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_stock_live_quotes_recent
    ON stock_live_quotes (symbol, captured_at DESC);
