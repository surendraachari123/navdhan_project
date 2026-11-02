"""
Database connection handling.

Uses a connection pool instead of a single shared global connection/cursor.
Each request borrows a connection from the pool via the `get_cursor`
dependency and returns it automatically when the request finishes - this
is what makes the app safe under concurrent traffic.

Why this matters: the original code created ONE connection and ONE cursor
at import time and reused them for every single request. FastAPI can
handle many requests at once (e.g. two users hitting the API at the same
time), but a single shared cursor can't safely run two queries at once -
one request's results could get mixed up with another's, or one slow
query could block everyone else. A connection pool gives each request
its own connection for the duration of that request.
"""
import logging
import oracledb
from contextlib import contextmanager

from config import (
    DB_USER,
    DB_PASSWORD,
    DB_HOST,
    DB_PORT,
    DB_SERVICE,
    DB_POOL_MIN,
    DB_POOL_MAX,
    DB_POOL_INCREMENT,
)

logger = logging.getLogger("app.database")

# Builds the Oracle connection string (host:port/service_name) once,
# instead of repeating those four separate values everywhere.
dsn = oracledb.makedsn(DB_HOST, DB_PORT, service_name=DB_SERVICE)

# Holds the pool object once it's created. Starts as None so we can
# detect (and error clearly on) any attempt to use the DB before startup
# has run - see the RuntimeError in get_connection() below.
pool: oracledb.ConnectionPool | None = None


def init_pool() -> None:
    """Create the connection pool. Called once on app startup.

    This is invoked from main.py's lifespan handler when the app boots,
    not at import time - so importing this module never itself opens a
    real network connection to Oracle.
    """
    global pool
    try:
        pool = oracledb.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            dsn=dsn,
            min=DB_POOL_MIN,
            max=DB_POOL_MAX,
            increment=DB_POOL_INCREMENT,
        )
        logger.info("Database connection pool created successfully.")
    except oracledb.DatabaseError:
        # .exception() logs the full traceback automatically - useful
        # here because a pool creation failure (bad host, bad password,
        # firewall) is exactly the kind of thing you need full detail on.
        logger.exception("Failed to create database connection pool.")
        raise


def close_pool() -> None:
    """Close the connection pool. Called once on app shutdown.

    Ensures Oracle sessions are released cleanly when the app stops,
    instead of leaving orphaned connections open on the DB server.
    """
    global pool
    if pool is not None:
        pool.close(force=True)  # force=True closes even connections still in use
        logger.info("Database connection pool closed.")
        pool = None


@contextmanager
def get_connection():
    """
    Borrow a connection from the pool, guaranteeing it's returned to the
    pool afterwards (even if an error occurs) instead of leaking it.

    The try/finally is the important part: if the code using this
    connection raises an exception, the connection still gets released
    back to the pool rather than staying "checked out" forever, which
    would eventually exhaust the pool.
    """
    if pool is None:
        raise RuntimeError("Database pool is not initialized. Call init_pool() first.")

    connection = pool.acquire()  # borrow one connection from the pool
    try:
        yield connection  # hand it to whatever code is inside the "with" block
    finally:
        pool.release(connection)  # always give it back, success or failure


def get_db():
    """
    FastAPI dependency: yields a live cursor for the duration of one
    request, commits on success, rolls back on failure, and always
    returns the connection to the pool.

    FastAPI runs this as a generator: everything before `yield` happens
    before the route handler runs, everything after `yield` happens after
    the route handler returns (or raises). Used in routes like:
        def some_route(cursor=Depends(get_db)):
    """
    with get_connection() as connection:
        cursor = connection.cursor()
        try:
            yield cursor          # route handler runs here, using this cursor
            connection.commit()   # only reached if the route handler didn't raise
        except Exception:
            connection.rollback()  # undo any partial writes from this request
            raise                  # re-raise so FastAPI's exception handlers still see it
        finally:
            cursor.close()
