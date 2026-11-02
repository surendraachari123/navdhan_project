-- Optional manual setup; the collector creates this table automatically.
CREATE TABLE IF NOT EXISTS market_observations (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    UNIQUE (symbol, content_hash)
);
