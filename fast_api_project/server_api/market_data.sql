-- Run once in the application Oracle schema before enabling Market Research.
CREATE TABLE market_prices (
    symbol       VARCHAR2(50) NOT NULL,
    price_date   DATE NOT NULL,
    open_price   NUMBER(20,6),
    high_price   NUMBER(20,6),
    low_price    NUMBER(20,6),
    close_price  NUMBER(20,6),
    volume       NUMBER(24,0),
    source       VARCHAR2(80) NOT NULL,
    fetched_at   TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT pk_market_prices PRIMARY KEY (symbol, price_date)
);

CREATE TABLE market_insights (
    symbol            VARCHAR2(50) NOT NULL,
    price_date        DATE NOT NULL,
    close_price       NUMBER(20,6),
    daily_change_pct  NUMBER(12,6),
    sma20             NUMBER(20,6),
    sma50             NUMBER(20,6),
    rsi14             NUMBER(12,6),
    volatility20_pct  NUMBER(12,6),
    trend             VARCHAR2(300),
    summary           CLOB,
    source            VARCHAR2(80),
    source_url        VARCHAR2(1000),
    updated_at        TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    CONSTRAINT pk_market_insights PRIMARY KEY (symbol, price_date)
);

CREATE TABLE market_refresh_runs (
    run_id         VARCHAR2(36) PRIMARY KEY,
    triggered_by   VARCHAR2(100) NOT NULL,
    status         VARCHAR2(20) DEFAULT 'RUNNING' NOT NULL,
    result         CLOB,
    error_message  CLOB,
    started_at     TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    finished_at    TIMESTAMP WITH TIME ZONE
);
