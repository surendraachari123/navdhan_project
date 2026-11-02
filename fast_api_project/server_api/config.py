"""
Application configuration.

Loads and validates required environment variables at import time so the
app fails fast (at startup) with a clear message instead of failing later
with a confusing NameError/None error deep inside a request.
"""
import os
import logging
from dotenv import load_dotenv

# Reads the .env file in the current working directory and loads its
# key=value pairs into the process environment (os.environ).
load_dotenv()

logger = logging.getLogger("app.config")

# Every one of these MUST be present in .env for the app to run at all.
# Listed here once so validate_config() can loop over them.
REQUIRED_VARS = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_SERVICE", "JWT_SECRET_KEY"]

# os.getenv returns None if the variable isn't set (instead of raising),
# which is why validate_config() below checks for missing values explicitly.
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_SERVICE = os.getenv("DB_SERVICE")

# Connection pool sizing - tune via env if needed.
# min = connections kept open at all times (ready to use, no connect delay).
# max = hard ceiling so one busy app instance can't exhaust DB connection limits.
# increment = how many new connections to open at once when the pool needs to grow.
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
DB_POOL_INCREMENT = int(os.getenv("DB_POOL_INCREMENT", "1"))

# CORS - which frontend origins (domains) are allowed to call this API from
# a browser. "*" allows everyone - fine for local dev, but in real
# production you'd set this to your actual frontend's domain(s).
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# Standard Python logging level name - INFO shows normal operational
# messages, DEBUG would show far more detail, ERROR would show only problems.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# --- Auth / JWT settings -------------------------------------------------
# JWT_SECRET_KEY signs and verifies every login token - it must be a long
# random string kept only in .env (never committed, never shared). If it
# leaks, anyone can forge valid tokens for any user.
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))

# --- Roles ---------------------------------------------------------------
# Every user row has exactly one of these. Kept as a plain list (not an
# Oracle CHECK constraint enum) so adding a role later is a one-line change
# here plus a matching CHECK constraint update in create_login_table.sql.
VALID_ROLES = ["admin", "manager", "user"]
DEFAULT_ROLE = "user"

# --- Password reset --------------------------------------------------------
PASSWORD_RESET_EXPIRE_MINUTES = int(os.getenv("PASSWORD_RESET_EXPIRE_MINUTES", "30"))
# Base URL of the HTML frontend, used to build the link inside reset emails,
# e.g. "https://yourdomain.com/web_client" -> .../reset-password.html?token=...
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://127.0.0.1:5500")

# --- Outgoing email (SMTP) --------------------------------------------------
# Used for: emailing a temporary password when an admin approves a
# registration request, and emailing password-reset links. If these are
# left unset, email_service.py logs the message instead of sending it
# (handy for local dev without a real mailbox).
SMTP_HOST = os.getenv("SMTP_HOST","smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER","surendraacharisua@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD","qewublwdnyumzzzs")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER or "surendraacharisua@gmail.com")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

# --- S3 settings (horse-data pipeline) --------------------------------------
# Not in REQUIRED_VARS - the app still starts fine without these, they're
# only needed when /run-horse-etl is actually called. AWS_ACCESS_KEY_ID and
# AWS_SECRET_ACCESS_KEY must stay only in .env / Render's env var settings,
# same as JWT_SECRET_KEY - never commit them.
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_HORSE_DATA_KEY = os.getenv("S3_HORSE_DATA_KEY")  # e.g. "input_f/horsedata.xlsx"


# --- NavDhan AI (NL-to-SQL, chat assistant, auto-insights) ------------------
# Not in REQUIRED_VARS - the rest of the API runs fine without these. If
# GROQ_API_KEY is missing, /ai/ask and /ai/chat return a clear 503, and
# /ai/insights still returns the computed numbers (just without the
# written narrative).
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
AI_LLM_TIMEOUT_SECONDS = int(os.getenv("AI_LLM_TIMEOUT_SECONDS", "45"))

# Tables the AI is allowed to read. Anything not listed here is invisible
# to it (app_users, password_reset_tokens, etl_jobs ... are never exposed).
# Add more with a comma-separated env var, e.g. AI_ALLOWED_TABLES=dim_scheme,dim_folio
AI_ALLOWED_TABLES = [
    t.strip().lower()
    for t in os.getenv("AI_ALLOWED_TABLES", "investor_table,mfund_transactions").split(",")
    if t.strip()
]
# Columns holding personal data. Only admins can have the AI return or
# filter on these; for everyone else a query that touches them is refused.
AI_SENSITIVE_COLUMNS = [
    c.strip().lower()
    for c in os.getenv("AI_SENSITIVE_COLUMNS", "pan,mobile,email").split(",")
    if c.strip()
]
AI_MAX_ROWS = int(os.getenv("AI_MAX_ROWS", "200"))               # rows returned to the browser
AI_QUERY_TIMEOUT_MS = int(os.getenv("AI_QUERY_TIMEOUT_MS", "15000"))  # per-query DB timeout
AI_INSIGHTS_CACHE_MINUTES = int(os.getenv("AI_INSIGHTS_CACHE_MINUTES", "10"))

# --- Qdrant (semantic retrieval of the business glossary) ---------------------
# The glossary (navdhan_glossary.yaml) is embedded into Qdrant; for every
# question the most relevant entries (tables, metrics, rules, example SQL) are
# retrieved and given to the LLM. If Qdrant is unreachable the AI falls back to
# the plain table schema, so it keeps working.
#   Local Docker:  QDRANT_URL=http://localhost:6333
#   Qdrant Cloud:  QDRANT_URL=https://xxxx.cloud.qdrant.io:6333  + QDRANT_API_KEY
#   No server:     QDRANT_URL=:memory:   (index lives in the API process; re-built at startup)
AI_USE_QDRANT = os.getenv("AI_USE_QDRANT", "true").lower() == "true"
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "navdhan_fastapi_schema")
AI_EMBED_MODEL = os.getenv("AI_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")  # 384-dim, same as NavDhan AI
AI_RETRIEVAL_LIMIT = int(os.getenv("AI_RETRIEVAL_LIMIT", "6"))
AI_GLOSSARY_PATH = os.getenv(
    "AI_GLOSSARY_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "navdhan_glossary.yaml")
)

# --- Market research (Alpha Vantage -> Oracle + Qdrant) ----------------------
MARKET_DATA_API_KEY = os.getenv("MARKET_DATA_API_KEY")
MARKET_DATA_BASE_URL = os.getenv("MARKET_DATA_BASE_URL", "https://www.alphavantage.co/query")
MARKET_DATA_LICENSE_CONFIRMED = os.getenv("MARKET_DATA_LICENSE_CONFIRMED", "false").lower() == "true"
MARKET_WATCHLIST = [
    symbol.strip().upper()
    for symbol in os.getenv(
        "MARKET_WATCHLIST",
        "RELIANCE.BSE,TCS.BSE,INFY.BSE,SUZLON.BSE,IDEA.BSE,JPL.BSE,HDFCBANK.BSE",
    ).split(",")
    if symbol.strip()
]
MARKET_QDRANT_COLLECTION = os.getenv("MARKET_QDRANT_COLLECTION", "navdhan_market_analysis")
MARKET_REQUEST_TIMEOUT_SECONDS = int(os.getenv("MARKET_REQUEST_TIMEOUT_SECONDS", "20"))


def validate_config() -> None:
    """Raise a clear error immediately if required config is missing.

    Without this, a missing DB_PASSWORD (for example) would silently pass
    None into the Oracle driver, and you'd get a cryptic low-level error
    only when a request actually tries to touch the database - possibly
    minutes after the app "successfully" started.
    """
    # List comprehension: keep only the variable names whose value is
    # falsy (None or empty string) - those are the ones missing from .env.
    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            f"Check your .env file."
        )
    # DB_PORT arrives from the environment as a string ("1521"), so make
    # sure it's actually convertible to an int before the app relies on it.
    try:
        int(DB_PORT)
    except (TypeError, ValueError):
        raise RuntimeError(f"DB_PORT must be an integer, got: {DB_PORT!r}")


# Runs immediately when this module is first imported (i.e. at app
# startup) - this is what makes "fail fast" actually happen.
validate_config()
