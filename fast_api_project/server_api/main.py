"""
FastAPI application entry point: app setup, error handlers, and routes.
"""
import logging
import uuid
from pathlib import Path
from contextlib import asynccontextmanager

import oracledb
from fastapi import FastAPI, Depends, Request, BackgroundTasks, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.security import OAuth2PasswordRequestForm

from datetime import datetime, timedelta

from config import CORS_ORIGINS, LOG_LEVEL, DEFAULT_ROLE, PASSWORD_RESET_EXPIRE_MINUTES
from database import init_pool, close_pool, get_db
from exceptions import AppError, NotFoundError, DuplicateError, DatabaseUnavailableError
from models import (
    Transaction, Investor, InvestorUpdate, InvestorRequest,
    RegistrationRequestCreate, RegistrationApprove, RegistrationReject,
    ChangePasswordRequest, ForgotPasswordRequest, ResetPasswordRequest,
    UserRoleUpdate,
)
import auth
import ai_router
import market_router
import stock_intelligence_router
import email_service
from etl_runner import run_etl_job
from horse_pipeline import run_horse_pipeline_job
# Configures the root logger once, at import time, so every module's
# logger.info/warning/error calls actually get printed somewhere with a
# consistent format (timestamp | level | logger name | message).
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("app.main")

# Oracle error codes that indicate the DB/network is unreachable, as
# opposed to a bad query - these get mapped to 503 instead of 500.
# (e.g. 12154 = TNS: could not resolve connect identifier,
#        3113/3114 = end-of-file / not connected to Oracle,
#        1034 = Oracle not available)
CONNECTION_ERROR_CODES = {12154, 12541, 12545, 3113, 3114, 1034, 12170}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the app starts and once when it stops.
    Code before `yield` = startup, code after `yield` = shutdown.
    This is where the connection pool's lifecycle is tied to the app's."""
    logger.info("Starting up: initializing database connection pool.")
    init_pool()
    yield
    logger.info("Shutting down: closing database connection pool.")
    close_pool()


# `lifespan=lifespan` wires the startup/shutdown logic above into FastAPI.
app = FastAPI(title="NavDhan Mutual Fund API", lifespan=lifespan)

# NavDhan AI endpoints (/ai/ask, /ai/chat, /ai/insights, /ai/status)
app.include_router(ai_router.router)
app.include_router(market_router.router)
app.include_router(stock_intelligence_router.router)

WEB_CLIENT_DIR = Path(__file__).resolve().parent.parent / "web_client"


@app.get("/stock-intelligence-assets/{asset:path}", include_in_schema=False)
def stock_intelligence_asset(asset: str):
    # Browsers may request a favicon relative to the page's <base>. No icon
    # is configured for this feature, so answer that optional request quietly.
    asset_name = asset.rsplit("/", 1)[-1]
    if asset_name == "favicon.ico":
        return Response(status_code=204)
    asset_path = (WEB_CLIENT_DIR / asset).resolve()
    if (asset_path.parent != WEB_CLIENT_DIR.resolve()
            or asset_path.suffix.lower() not in {".html", ".css", ".js"}
            or not asset_path.is_file()):
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(asset_path)


@app.get("/stock-intelligence", include_in_schema=False)
def stock_intelligence_page():
    return FileResponse(WEB_CLIENT_DIR / "stock-intelligence.html")

# Lets browser-based frontends on other domains call this API. Without
# this middleware, a browser would block the request with a CORS error
# before it even reaches your routes.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_response(status_code: int, message: str, error_type: str) -> JSONResponse:
    """Small helper so every error handler below returns the exact same
    JSON shape: {"error": {"message": ..., "type": ...}}. Consistent
    error shape means client code only has to parse one format,
    regardless of what went wrong."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type}},
    )


# ---------------------------------------------------------------------
# Centralized exception handlers - every error path returns the same
# consistent {"error": {...}} shape instead of leaking raw tracebacks.
# FastAPI matches these by exception TYPE: whichever handler matches the
# raised exception's class (or nearest parent class) runs automatically -
# route handlers don't need their own try/except for these cases.
# ---------------------------------------------------------------------

@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    # Catches NotFoundError, DuplicateError, DatabaseUnavailableError, etc.
    # (anything defined in exceptions.py) since they all inherit from AppError.
    return _error_response(exc.status_code, exc.message, type(exc).__name__)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Raised automatically by FastAPI/Pydantic when incoming JSON doesn't
    # match a model in models.py (bad email, invalid PAN, missing field, etc).
    # exc.errors() is a list of dicts like {"loc": ["body","pan"], "msg": "..."}.
    details = "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
    )
    return _error_response(422, f"Invalid request data: {details}", "ValidationError")


@app.exception_handler(oracledb.IntegrityError)
async def integrity_error_handler(request: Request, exc: oracledb.IntegrityError):
    # Fires when Oracle rejects a write due to a constraint - e.g. a
    # duplicate primary key or a foreign key pointing to a row that
    # doesn't exist - that wasn't already caught by an explicit check
    # in the route (belt-and-suspenders against race conditions).
    logger.warning("Integrity constraint violated: %s", exc)
    return _error_response(
        409,
        "This record already exists or violates a data constraint.",
        "IntegrityError",
    )


@app.exception_handler(oracledb.DatabaseError)
async def database_error_handler(request: Request, exc: oracledb.DatabaseError):
    # Catches any other Oracle error not already handled above (e.g. a
    # dropped network connection, Oracle instance down, bad SQL syntax).
    # exc.args[0] is oracledb's error-info object, which carries the
    # numeric Oracle error code we check against CONNECTION_ERROR_CODES.
    error_obj = exc.args[0] if exc.args else None
    code = getattr(error_obj, "code", None)
    logger.error("Database error (code=%s): %s", code, exc)

    if code in CONNECTION_ERROR_CODES:
        # The DB itself is unreachable - tell the client to retry later.
        return _error_response(
            503, "Database is currently unavailable. Please try again shortly.", "DatabaseUnavailableError"
        )
    # Some other DB-side problem (e.g. bad SQL) - don't leak the raw
    # Oracle error text (which can include table/column names) to the caller.
    return _error_response(500, "A database error occurred while processing your request.", "DatabaseError")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Last-resort catch-all: anything not covered by a more specific
    # handler above lands here. Logs the FULL traceback (logger.exception
    # captures it automatically) for debugging, but only ever shows the
    # caller a generic, safe message - never internal details.
    logger.exception("Unhandled exception on %s %s", request.method, request.url)
    return _error_response(500, "An unexpected internal error occurred.", "InternalServerError")


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------

@app.get("/")
def home():
    return {"message": "FastAPI is running successfully!"}


@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), cursor=Depends(get_db)):
    """Public endpoint - exchanges a username/password for a JWT.
    form_data comes in as standard OAuth2 form fields (username, password),
    which is what makes the /docs 'Authorize' button work automatically.
    The response also tells the client the user's role (so the frontend
    can show/hide admin features) and whether they must change their
    password before doing anything else (true right after an admin
    approval or a password reset)."""
    return auth.authenticate_user(form_data.username, form_data.password, cursor)


@app.get("/health")
def health_check(cursor=Depends(get_db)):
    """Simple readiness check that confirms the DB is reachable.
    `cursor=Depends(get_db)` tells FastAPI to run the get_db generator
    from database.py before this function, and pass its yielded cursor
    in as the `cursor` argument - the pool checkout/checkin, commit,
    and cleanup all happen automatically around this function's body."""
    cursor.execute("SELECT 1 FROM dual")
    cursor.fetchone()
    return {"status": "ok", "database": "reachable"}


@app.post("/transactions", status_code=201)
def create_transaction(transaction: Transaction, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    # `transaction: Transaction` means FastAPI has already validated the
    # request body against the Transaction model in models.py before this
    # line ever runs - if validation had failed, this function wouldn't
    # even be called (validation_error_handler would have responded instead).

    # Foreign-key style check done manually here (rather than relying only
    # on Oracle to reject it) so we can return a clear 404 instead of a
    # raw Oracle constraint error.
    cursor.execute(
        "SELECT 1 FROM investor_table WHERE investor_id = :1", (transaction.investor_id,)
    )
    if cursor.fetchone() is None:
        raise NotFoundError(f"Investor '{transaction.investor_id}' does not exist.")

    sql = """
    INSERT INTO mfund_transactions
    (folio_id, investor_id, scheme_id, units, nav, amount, txn_type, txn_date)
    VALUES
    (:1, :2, :3, :4, :5, :6, :7, :8)
    """
    # :1, :2, etc. are bind parameters - oracledb sends the values
    # separately from the SQL text, which is what prevents SQL injection
    # (never build SQL strings by concatenating user input directly).
    cursor.execute(sql, (
        transaction.folio_id,
        transaction.investor_id,
        transaction.scheme_id,
        transaction.units,
        transaction.nav,
        transaction.amount,
        transaction.txn_type,
        transaction.txn_date,
    ))

    # No explicit connection.commit() here - get_db() in database.py
    # commits automatically once this function returns without raising.
    return {"message": "Transaction created successfully"}


@app.get("/transactions/investor/{investor_id}")
def get_transactions_by_investor(investor_id: str, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    cursor.execute(
        """
        SELECT transaction_id, folio_id, investor_id, scheme_id, units,
               nav, amount, txn_type, txn_date
        FROM mfund_transactions
        WHERE investor_id = :1
        """,
        (investor_id,),
    )
    rows = cursor.fetchall()

    if not rows:
        # Previously returned {"message": "No transactions found..."} with
        # a 200 OK - now correctly reports this as a 404 to the caller.
        raise NotFoundError(f"No transactions found for investor '{investor_id}'.")

    # List comprehension building one dict per row - equivalent to the
    # original's for-loop + .append(), just more compact.
    return [
        {
            "transaction_id": row[0],
            "folio_id": row[1],
            "investor_id": row[2],
            "scheme_id": row[3],
            "units": row[4],
            "nav": row[5],
            "amount": row[6],
            "txn_type": row[7],
            "txn_date": row[8],
        }
        for row in rows
    ]


@app.post("/investors", status_code=201)
def create_investor(investor: Investor, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    # Check for an existing investor_id first so we can return a clean
    # 409 Conflict with a helpful message, rather than letting Oracle's
    # unique-constraint violation bubble up as a less friendly error
    # (though integrity_error_handler would still catch it as a fallback).
    cursor.execute(
        "SELECT 1 FROM investor_table WHERE investor_id = :1", (investor.investor_id,)
    )
    if cursor.fetchone() is not None:
        raise DuplicateError(f"Investor '{investor.investor_id}' already exists.")

    sql = """
    INSERT INTO investor_table
    (investor_id, name, email, mobile, kyc_status, pan)
    VALUES
    (:1, :2, :3, :4, :5, :6)
    """
    cursor.execute(sql, (
        investor.investor_id,
        investor.name,
        investor.email,
        investor.mobile,
        investor.kyc_status,
        investor.pan,
    ))

    return {"message": "Investor created successfully"}


@app.get("/investors")
def get_all_investors(cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    # Named columns instead of SELECT * so the row order/shape below is
    # guaranteed to match the query, even if the table's columns are
    # ever reordered or new ones get added.
    cursor.execute("SELECT investor_id, name, email, mobile, kyc_status, pan FROM investor_table")
    rows = cursor.fetchall()

    return [
        {
            "investor_id": row[0],
            "name": row[1],
            "email": row[2],
            "mobile": row[3],
            "kyc_status": row[4],
            "pan": row[5],
        }
        for row in rows
    ]


@app.get("/investors/{investor_id}")
def get_investor(investor_id: str, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    cursor.execute(
        "SELECT investor_id, name, email, mobile, kyc_status, pan FROM investor_table WHERE investor_id = :1",
        (investor_id,),
    )
    row = cursor.fetchone()

    if row is None:
        raise NotFoundError(f"Investor '{investor_id}' not found.")

    return {
        "investor_id": row[0],
        "name": row[1],
        "email": row[2],
        "mobile": row[3],
        "kyc_status": row[4],
        "pan": row[5],
    }


@app.put("/investors/{investor_id}")
def update_investor(investor_id: str, investor: Investor, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    # PUT means "replace this whole record" - so every field is required
    # (via the full Investor model) and every column gets overwritten.
    cursor.execute("SELECT 1 FROM investor_table WHERE investor_id = :1", (investor_id,))
    if cursor.fetchone() is None:
        raise NotFoundError(f"Investor '{investor_id}' not found.")

    sql = """
    UPDATE investor_table
    SET name = :1, email = :2, mobile = :3, kyc_status = :4, pan = :5
    WHERE investor_id = :6
    """
    cursor.execute(sql, (
        investor.name,
        investor.email,
        investor.mobile,
        investor.kyc_status,
        investor.pan,
        investor_id,
    ))

    return {"message": "Investor updated successfully"}


@app.patch("/investors/{investor_id}")
def patch_investor(investor_id: str, investor: InvestorUpdate, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    # PATCH means "update only the fields provided" - InvestorUpdate makes
    # every field Optional, so we fetch the current row first and fall
    # back to its existing values for anything the caller didn't send.
    cursor.execute(
        "SELECT name, email, mobile, kyc_status, pan FROM investor_table WHERE investor_id = :1",
        (investor_id,),
    )
    row = cursor.fetchone()

    if row is None:
        raise NotFoundError(f"Investor '{investor_id}' not found.")

    # For each field: use the new value if the caller provided one,
    # otherwise keep whatever was already stored (row[n]).
    name = investor.name if investor.name is not None else row[0]
    email = investor.email if investor.email is not None else row[1]
    mobile = investor.mobile if investor.mobile is not None else row[2]
    kyc_status = investor.kyc_status if investor.kyc_status is not None else row[3]
    pan = investor.pan if investor.pan is not None else row[4]

    cursor.execute("""
        UPDATE investor_table
        SET name = :1, email = :2, mobile = :3, kyc_status = :4, pan = :5
        WHERE investor_id = :6
    """, (name, email, mobile, kyc_status, pan, investor_id))

    return {"message": "Investor updated successfully"}


@app.delete("/investors/{investor_id}")
def delete_investor(investor_id: str, cursor=Depends(get_db), user=Depends(auth.require_role("admin"))):
    cursor.execute("SELECT 1 FROM investor_table WHERE investor_id = :1", (investor_id,))
    if cursor.fetchone() is None:
        raise NotFoundError(f"Investor '{investor_id}' not found.")

    cursor.execute("DELETE FROM investor_table WHERE investor_id = :1", (investor_id,))

    # cursor.rowcount tells you how many rows the last statement actually
    # affected - useful for the caller to confirm the delete really happened.
    return {"message": "Investor deleted successfully", "rows_deleted": cursor.rowcount}


@app.get("/investors/{investor_id}/transactions")
def get_investor_transactions(investor_id: str, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    # Two separate queries instead of one JOIN (as the original had):
    # this lets us tell the difference between "investor doesn't exist"
    # (404) and "investor exists but has zero transactions" (empty list),
    # which a single JOIN can't distinguish - a JOIN would return zero
    # rows in both cases.
    cursor.execute(
        "SELECT investor_id, name, email, mobile, kyc_status, pan FROM investor_table WHERE investor_id = :1",
        (investor_id,),
    )
    investor_row = cursor.fetchone()
    if investor_row is None:
        raise NotFoundError(f"Investor '{investor_id}' not found.")

    cursor.execute("""
        SELECT folio_id, scheme_id, units, nav, amount, txn_type, txn_date
        FROM mfund_transactions
        WHERE investor_id = :1
    """, (investor_id,))
    txn_rows = cursor.fetchall()

    return {
        "investor_id": investor_row[0],
        "name": investor_row[1],
        "email": investor_row[2],
        "mobile": investor_row[3],
        "kyc_status": investor_row[4],
        "pan": investor_row[5],
        "transactions": [
            {
                "folio_id": r[0],
                "scheme_id": r[1],
                "units": r[2],
                "nav": r[3],
                "amount": r[4],
                "txn_type": r[5],
                "txn_date": r[6],
            }
            for r in txn_rows
        ],
    }

@app.post("/run-etl")
def run_etl(
    background_tasks: BackgroundTasks,
    cursor=Depends(get_db),
    user=Depends(auth.require_role("admin")),
):
    """Admin-only: starts the ETL pipeline in the background and returns
    immediately with a job_id - it does NOT wait for the pipeline to
    finish, since that can take a while. Check progress afterwards with
    GET /run-etl/{job_id}."""
    job_id = str(uuid.uuid4())

    cursor.execute(
        "INSERT INTO etl_jobs (job_id, triggered_by, status) VALUES (:1, :2, 'RUNNING')",
        (job_id, user["username"]),
    )

    background_tasks.add_task(run_etl_job, job_id)

    return {"job_id": job_id, "status": "RUNNING"}


@app.post("/run-horse-etl")
def run_horse_etl(
    background_tasks: BackgroundTasks,
    cursor=Depends(get_db),
    user=Depends(auth.require_role("admin")),
):
    """Admin-only: starts the horse-data pipeline (S3 -> Owner/Breeder/
    Dam/Horse/Alias) in the background and returns a job_id immediately.
    Poll progress with the same GET /run-etl/{job_id} used for the other
    pipeline - it looks up by job_id only, so it works for either."""
    job_id = str(uuid.uuid4())

    cursor.execute(
        "INSERT INTO etl_jobs (job_id, triggered_by, status) VALUES (:1, :2, 'RUNNING')",
        (job_id, user["username"]),
    )

    background_tasks.add_task(run_horse_pipeline_job, job_id, user["username"])

    return {"job_id": job_id, "status": "RUNNING"}


@app.get("/run-etl/{job_id}")
def get_etl_status(
    job_id: str,
    cursor=Depends(get_db),
    user=Depends(auth.require_role("admin")),
):
    """Admin-only: poll this after calling POST /run-etl to see whether
    the pipeline is still running, succeeded, or failed."""
    cursor.execute(
        """
        SELECT job_id, triggered_by, status, output, error_message, started_at, finished_at
        FROM etl_jobs WHERE job_id = :1
        """,
        (job_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise NotFoundError(f"No ETL job found with id '{job_id}'.")

    return {
        "job_id": row[0],
        "triggered_by": row[1],
        "status": row[2],
        "output": row[3],
        "error_message": row[4],
        "started_at": row[5],
        "finished_at": row[6],
    }


# =======================================================================
# Registration (public request -> admin approval -> emailed credentials)
# =======================================================================

@app.post("/register", status_code=201)
def submit_registration_request(request: RegistrationRequestCreate, cursor=Depends(get_db)):
    """PUBLIC endpoint - anyone can submit this, no token required.
    It does NOT create a login. It files a request that shows up for an
    admin at GET /admin/registration-requests. The applicant gets their
    actual username/password by email only after an admin approves it."""
    cursor.execute("SELECT 1 FROM app_users WHERE username = :1", (request.username,))
    if cursor.fetchone() is not None:
        raise DuplicateError(f"Username '{request.username}' is already taken.")

    cursor.execute(
        "SELECT status FROM registration_requests WHERE username = :1", (request.username,)
    )
    existing = cursor.fetchone()
    if existing is not None and existing[0] == "PENDING":
        raise DuplicateError(f"A pending registration request for '{request.username}' already exists.")

    cursor.execute(
        """
        INSERT INTO registration_requests
        (username, full_name, email, mobile, requested_role, reason)
        VALUES (:1, :2, :3, :4, :5, :6)
        """,
        (request.username, request.full_name, request.email, request.mobile,
         request.requested_role, request.reason),
    )
    return {"message": "Registration request submitted. An admin will review it shortly."}


@app.get("/admin/registration-requests")
def list_registration_requests(
    status: str = "PENDING", cursor=Depends(get_db), user=Depends(auth.require_role("admin"))
):
    """ADMIN ONLY. status defaults to PENDING; pass status=ALL to see
    everything, or APPROVED/REJECTED to see history."""
    if status == "ALL":
        cursor.execute(
            """SELECT request_id, username, full_name, email, mobile, requested_role,
                      reason, status, requested_at, reviewed_at, reviewed_by
               FROM registration_requests ORDER BY requested_at DESC"""
        )
    else:
        cursor.execute(
            """SELECT request_id, username, full_name, email, mobile, requested_role,
                      reason, status, requested_at, reviewed_at, reviewed_by
               FROM registration_requests WHERE status = :1 ORDER BY requested_at DESC""",
            (status,),
        )
    rows = cursor.fetchall()
    return [
        {
            "request_id": r[0], "username": r[1], "full_name": r[2], "email": r[3],
            "mobile": r[4], "requested_role": r[5], "reason": r[6], "status": r[7],
            "requested_at": r[8], "reviewed_at": r[9], "reviewed_by": r[10],
        }
        for r in rows
    ]


@app.post("/admin/registration-requests/{request_id}/approve")
def approve_registration_request(
    request_id: int, body: RegistrationApprove = RegistrationApprove(),
    cursor=Depends(get_db), user=Depends(auth.require_role("admin")),
):
    """ADMIN ONLY. Creates the real app_users login, generates a random
    temporary password, emails it to the applicant, and marks the
    request APPROVED. The new account has force_password_change=1, so
    the frontend should push the user straight to 'set a new password'
    after their first login."""
    cursor.execute(
        "SELECT username, full_name, email, requested_role, status FROM registration_requests WHERE request_id = :1",
        (request_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise NotFoundError(f"Registration request {request_id} not found.")

    username, full_name, email, requested_role, status = row
    if status != "PENDING":
        raise DuplicateError(f"Request {request_id} has already been {status.lower()}.")

    role = body.role or requested_role
    temp_password = auth.generate_temp_password()
    password_hash = auth.hash_password(temp_password)

    cursor.execute(
        """
        INSERT INTO app_users (username, password_hash, email, role, force_password_change)
        VALUES (:1, :2, :3, :4, 1)
        """,
        (username, password_hash, email, role),
    )
    cursor.execute(
        "UPDATE registration_requests SET status = 'APPROVED', reviewed_at = SYSDATE, reviewed_by = :1 WHERE request_id = :2",
        (user["username"], request_id),
    )

    email_service.send_new_account_email(email, username, temp_password)
    return {"message": f"User '{username}' created with role '{role}' and emailed their temporary password."}


@app.post("/admin/registration-requests/{request_id}/reject")
def reject_registration_request(
    request_id: int, body: RegistrationReject = RegistrationReject(),
    cursor=Depends(get_db), user=Depends(auth.require_role("admin")),
):
    """ADMIN ONLY."""
    cursor.execute(
        "SELECT username, email, status FROM registration_requests WHERE request_id = :1",
        (request_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise NotFoundError(f"Registration request {request_id} not found.")

    username, email, status = row
    if status != "PENDING":
        raise DuplicateError(f"Request {request_id} has already been {status.lower()}.")

    cursor.execute(
        "UPDATE registration_requests SET status = 'REJECTED', reviewed_at = SYSDATE, reviewed_by = :1 WHERE request_id = :2",
        (user["username"], request_id),
    )
    email_service.send_registration_rejected_email(email, username, body.reason)
    return {"message": f"Registration request {request_id} rejected."}


# =======================================================================
# Password self-service (change / forgot / reset)
# =======================================================================

@app.post("/change-password")
def change_password(body: ChangePasswordRequest, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    """Authenticated - logged-in user changes their own password.
    Requires the current password so a left-open session can't be used
    by someone else to lock the real owner out. Also clears
    force_password_change, since this satisfies that requirement."""
    cursor.execute(
        "SELECT password_hash FROM app_users WHERE username = :1", (user["username"],)
    )
    row = cursor.fetchone()
    if row is None or not auth.verify_password(body.current_password, row[0]):
        raise auth.AuthError("Current password is incorrect.")

    new_hash = auth.hash_password(body.new_password)
    cursor.execute(
        "UPDATE app_users SET password_hash = :1, force_password_change = 0 WHERE username = :2",
        (new_hash, user["username"]),
    )
    return {"message": "Password changed successfully."}


@app.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest, cursor=Depends(get_db)):
    """PUBLIC endpoint. Always returns the same generic message whether
    or not the email exists, to avoid leaking which emails have accounts.
    If it does exist, a one-time reset link is emailed."""
    cursor.execute("SELECT username FROM app_users WHERE email = :1", (body.email,))
    row = cursor.fetchone()

    if row is not None:
        username = row[0]
        token = auth.generate_reset_token()
        # Oracle DATE columns are naive (no timezone), so store naive UTC
        # throughout rather than mixing aware/naive datetimes.
        expires_at = datetime.utcnow() + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES)
        cursor.execute(
            "INSERT INTO password_reset_tokens (token, username, expires_at) VALUES (:1, :2, :3)",
            (token, username, expires_at),
        )
        email_service.send_password_reset_email(body.email, username, token)

    return {"message": "If that email is registered, a password reset link has been sent."}


@app.post("/reset-password")
def reset_password(body: ResetPasswordRequest, cursor=Depends(get_db)):
    """PUBLIC endpoint - consumes a token from /forgot-password's email
    and sets a new password. Tokens are single-use and time-limited."""
    cursor.execute(
        "SELECT username, expires_at, used FROM password_reset_tokens WHERE token = :1",
        (body.token,),
    )
    row = cursor.fetchone()
    if row is None:
        raise auth.AuthError("Invalid or expired reset link.")

    username, expires_at, used = row
    if used == 1:
        raise auth.AuthError("This reset link has already been used.")
    if expires_at < datetime.utcnow():
        raise auth.AuthError("This reset link has expired. Please request a new one.")

    new_hash = auth.hash_password(body.new_password)
    cursor.execute(
        "UPDATE app_users SET password_hash = :1, force_password_change = 0 WHERE username = :2",
        (new_hash, username),
    )
    cursor.execute("UPDATE password_reset_tokens SET used = 1 WHERE token = :1", (body.token,))
    return {"message": "Password reset successfully. You can now log in with your new password."}


# =======================================================================
# User management - ADMIN ONLY
# =======================================================================

@app.get("/admin/users")
def list_users(cursor=Depends(get_db), user=Depends(auth.require_role("admin"))):
    cursor.execute(
        """SELECT username, email, role, is_active, force_password_change, created_at, last_login
           FROM app_users ORDER BY created_at DESC"""
    )
    rows = cursor.fetchall()
    return [
        {
            "username": r[0], "email": r[1], "role": r[2], "is_active": bool(r[3]),
            "force_password_change": bool(r[4]), "created_at": r[5], "last_login": r[6],
        }
        for r in rows
    ]


@app.patch("/admin/users/{username}")
def update_user(
    username: str, body: UserRoleUpdate, cursor=Depends(get_db), user=Depends(auth.require_role("admin")),
):
    """ADMIN ONLY. Change a user's role and/or activate/deactivate them
    (deactivating is the reversible alternative to deleting - it blocks
    login immediately via the is_active check in authenticate_user)."""
    cursor.execute("SELECT role, is_active FROM app_users WHERE username = :1", (username,))
    row = cursor.fetchone()
    if row is None:
        raise NotFoundError(f"User '{username}' not found.")

    new_role = body.role if body.role is not None else row[0]
    new_active = int(body.is_active) if body.is_active is not None else row[1]

    cursor.execute(
        "UPDATE app_users SET role = :1, is_active = :2 WHERE username = :3",
        (new_role, new_active, username),
    )
    return {"message": f"User '{username}' updated.", "role": new_role, "is_active": bool(new_active)}


@app.delete("/admin/users/{username}")
def delete_user(username: str, cursor=Depends(get_db), user=Depends(auth.require_role("admin"))):
    """ADMIN ONLY."""
    if username == user["username"]:
        raise auth.PermissionDeniedError("You can't delete your own account while logged in as it.")

    cursor.execute("SELECT 1 FROM app_users WHERE username = :1", (username,))
    if cursor.fetchone() is None:
        raise NotFoundError(f"User '{username}' not found.")

    cursor.execute("DELETE FROM app_users WHERE username = :1", (username,))
    return {"message": f"User '{username}' deleted.", "rows_deleted": cursor.rowcount}

'''
@app.post("/investors")
def get_investor(request: InvestorRequest, cursor=Depends(get_db), user=Depends(auth.get_current_user)):
    cursor.execute(
        """
        SELECT investor_id, name, email, mobile, kyc_status, pan
        FROM investor_table
        WHERE investor_id = :1
        """,
        (request.investor_id,),
    )

    row = cursor.fetchone()

    if row is None:
        raise NotFoundError(f"Investor '{request.investor_id}' not found.")

    return {
        "investor_id": row[0],
        "name": row[1],
        "email": row[2],
        "mobile": row[3],
        "kyc_status": row[4],
        "pan": row[5],
    }
    '''
    
