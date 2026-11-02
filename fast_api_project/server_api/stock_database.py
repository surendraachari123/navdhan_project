"""SQLite storage for the local Stock Intelligence feature."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().with_name("stock_intelligence.sqlite3")
DB_PATH = Path(os.getenv("STOCK_SQLITE_PATH", str(DEFAULT_DB_PATH))).expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_price_history (
    symbol TEXT NOT NULL, trade_date TEXT NOT NULL, open_price REAL, high_price REAL,
    low_price REAL, close_price REAL, volume INTEGER, source TEXT NOT NULL,
    source_url TEXT NOT NULL, captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, trade_date)
);
CREATE TABLE IF NOT EXISTS stock_company_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, document_type TEXT NOT NULL,
    title TEXT NOT NULL, source_url TEXT NOT NULL, published_at TEXT, content TEXT NOT NULL,
    extracted TEXT NOT NULL DEFAULT '{}', content_hash TEXT NOT NULL,
    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (symbol, source_url, content_hash)
);
CREATE INDEX IF NOT EXISTS ix_stock_company_documents_recent
    ON stock_company_documents (symbol, captured_at DESC);
CREATE TABLE IF NOT EXISTS stock_fund_exposures (
    symbol TEXT NOT NULL, fund_code TEXT NOT NULL, fund_name TEXT NOT NULL,
    exposure_pct REAL, as_of_date TEXT NOT NULL, source TEXT NOT NULL, source_url TEXT,
    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, fund_code, as_of_date)
);
CREATE TABLE IF NOT EXISTS stock_intelligence_refresh_runs (
    run_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'RUNNING',
    price_rows INTEGER NOT NULL DEFAULT 0, crawled_pages INTEGER NOT NULL DEFAULT 0,
    error_message TEXT, started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS stock_live_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
    last_price REAL NOT NULL, open_price REAL, high_price REAL, low_price REAL,
    previous_close REAL, change_amount REAL, change_percent REAL, volume INTEGER,
    latest_timestamp TEXT, provider_updated_at TEXT, source_url TEXT NOT NULL,
    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_stock_live_quotes_recent
    ON stock_live_quotes (symbol, captured_at DESC);
"""


@contextmanager
def connect_stock_db():
    """Open a SQLite connection and ensure the feature's tables exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
