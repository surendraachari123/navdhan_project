"""
api_client.py

Thin wrapper functions around the FastAPI investor/transactions/auth
service. Each function maps to exactly one endpoint. Import these into
any other client program instead of writing requests.post/get calls
directly - if the API's base URL or error format ever changes, you only
update it here, once.

AUTH: every protected endpoint now requires a Bearer token. Call
login(username, password) once per session - it stores the token in
memory AND writes it to a small local file (SESSION_FILE) so other
scripts run afterwards (create_investors.py, get_all_investors.py, etc.)
can reuse the same login without asking again. Call logout() to clear it.
"""
import json
import os
from pathlib import Path

import requests

# Local dev server:      "http://127.0.0.1:8000"
# Deployed NavDhan API:  "https://navdhan-api.onrender.com"
BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = 200  # seconds - always set a timeout so a hung server never blocks the caller forever

# Session token is cached to a local file so every client script in this
# folder shares one login instead of each prompting separately. Never
# commit this file - it holds a live bearer token.
SESSION_FILE = Path(__file__).parent / ".navdhan_session.json"

_session = {"access_token": None, "role": None, "force_password_change": False}


class APIError(Exception):
    """Raised whenever the API returns a non-2xx response.
    Carries the HTTP status code and whatever detail message the API sent,
    so callers can decide how to react (retry, show to user, log, etc.)."""
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"[{status_code}] {detail}")


def _handle(response):
    """Shared response handling: return parsed JSON on success,
    raise APIError with the server's detail/error message on failure."""
    if response.ok:
        return response.json()
    try:
        body = response.json()
        detail = body.get("error", {}).get("message") or body.get("detail", response.text)
    except ValueError:
        detail = response.text
    raise APIError(response.status_code, detail)


def _auth_headers():
    if not _session["access_token"]:
        raise APIError(401, "Not logged in. Call login(username, password) first.")
    return {"Authorization": f"Bearer {_session['access_token']}"}


def _load_saved_session():
    if SESSION_FILE.exists():
        try:
            _session.update(json.loads(SESSION_FILE.read_text()))
        except (json.JSONDecodeError, OSError):
            pass


def _save_session():
    try:
        SESSION_FILE.write_text(json.dumps(_session))
        os.chmod(SESSION_FILE, 0o600)  # local-only readable, it holds a live token
    except OSError:
        pass  # non-fatal - session just won't persist across processes


_load_saved_session()


# ---------- Auth ----------

def login(username, password):
    """POST /login - authenticates and caches the JWT for every other
    function in this module to use. Returns the role and whether the
    caller must change their password before doing anything else."""
    response = requests.post(
        f"{BASE_URL}/login",
        data={"username": username, "password": password},
        timeout=TIMEOUT,
    )
    result = _handle(response)
    _session["access_token"] = result["access_token"]
    _session["role"] = result.get("role")
    _session["force_password_change"] = result.get("force_password_change", False)
    _save_session()
    return result


def logout():
    """Clears the cached session, in memory and on disk."""
    _session["access_token"] = None
    _session["role"] = None
    _session["force_password_change"] = False
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def current_role():
    """The role of whoever is currently logged in (or None)."""
    return _session["role"]


def must_change_password():
    return bool(_session["force_password_change"])


def register_request(username, full_name, email, mobile=None, requested_role="user", reason=None):
    """POST /register - PUBLIC. Files a registration request; does not
    log anyone in or create a usable account by itself. An admin has to
    approve it (see admin_approve_registration) before login works."""
    payload = {
        "username": username,
        "full_name": full_name,
        "email": email,
        "mobile": mobile,
        "requested_role": requested_role,
        "reason": reason,
    }
    response = requests.post(f"{BASE_URL}/register", json=payload, timeout=TIMEOUT)
    return _handle(response)


def change_password(current_password, new_password):
    """POST /change-password - authenticated. Also clears the
    "must change password" flag this module tracks locally."""
    response = requests.post(
        f"{BASE_URL}/change-password",
        json={"current_password": current_password, "new_password": new_password},
        headers=_auth_headers(),
        timeout=TIMEOUT,
    )
    result = _handle(response)
    _session["force_password_change"] = False
    _save_session()
    return result


def forgot_password(email):
    """POST /forgot-password - PUBLIC. Triggers a reset-link email if
    the address is registered (response is the same either way)."""
    response = requests.post(f"{BASE_URL}/forgot-password", json={"email": email}, timeout=TIMEOUT)
    return _handle(response)


def reset_password(token, new_password):
    """POST /reset-password - PUBLIC. Consumes the token from the
    forgot-password email."""
    response = requests.post(
        f"{BASE_URL}/reset-password",
        json={"token": token, "new_password": new_password},
        timeout=TIMEOUT,
    )
    return _handle(response)


# ---------- General ----------

def home():
    """GET / - basic liveness check."""
    return _handle(requests.get(f"{BASE_URL}/", timeout=TIMEOUT))


def health_check():
    """GET /health - confirms the API can reach the database."""
    return _handle(requests.get(f"{BASE_URL}/health", timeout=TIMEOUT))


# ---------- Transactions ----------

def create_transaction(folio_id, investor_id, scheme_id, units, nav, amount, txn_type, txn_date):
    """POST /transactions - creates a new mutual fund transaction."""
    payload = {
        "folio_id": folio_id,
        "investor_id": investor_id,
        "scheme_id": scheme_id,
        "units": units,
        "nav": nav,
        "amount": amount,
        "txn_type": txn_type,
        "txn_date": txn_date,
    }
    response = requests.post(f"{BASE_URL}/transactions", json=payload, headers=_auth_headers(), timeout=TIMEOUT)
    return _handle(response)


def get_transactions_by_investor(investor_id):
    """GET /transactions/investor/{investor_id} - all transactions for one investor."""
    response = requests.get(
        f"{BASE_URL}/transactions/investor/{investor_id}", headers=_auth_headers(), timeout=TIMEOUT
    )
    return _handle(response)


# ---------- Investors ----------

def create_investor(investor_id, name, email, mobile, kyc_status, pan):
    """POST /investors - creates a new investor record."""
    payload = {
        "investor_id": investor_id,
        "name": name,
        "email": email,
        "mobile": mobile,
        "kyc_status": kyc_status,
        "pan": pan,
    }
    response = requests.post(f"{BASE_URL}/investors", json=payload, headers=_auth_headers(), timeout=TIMEOUT)
    return _handle(response)


def get_all_investors():
    """GET /investors - lists every investor."""
    response = requests.get(f"{BASE_URL}/investors", headers=_auth_headers(), timeout=TIMEOUT)
    return _handle(response)


def get_investor(investor_id):
    """GET /investors/{investor_id} - a single investor's details."""
    response = requests.get(f"{BASE_URL}/investors/{investor_id}", headers=_auth_headers(), timeout=TIMEOUT)
    return _handle(response)


def update_investor(investor_id, name, email, mobile, kyc_status, pan):
    """PUT /investors/{investor_id} - replaces the whole investor record.
    All fields are required, matching the API's PUT semantics."""
    payload = {
        "investor_id": investor_id,
        "name": name,
        "email": email,
        "mobile": mobile,
        "kyc_status": kyc_status,
        "pan": pan,
    }
    response = requests.put(
        f"{BASE_URL}/investors/{investor_id}", json=payload, headers=_auth_headers(), timeout=TIMEOUT
    )
    return _handle(response)


def patch_investor(investor_id, **fields):
    """PATCH /investors/{investor_id} - updates only the fields provided.
    Call like: patch_investor("INV001", email="new@example.com")
    Only pass the fields you actually want changed; omitted fields are
    left untouched by the API."""
    response = requests.patch(
        f"{BASE_URL}/investors/{investor_id}", json=fields, headers=_auth_headers(), timeout=TIMEOUT
    )
    return _handle(response)


def delete_investor(investor_id):
    """DELETE /investors/{investor_id} - ADMIN ONLY on the server side.
    A non-admin token gets a 403 APIError back."""
    response = requests.delete(
        f"{BASE_URL}/investors/{investor_id}", headers=_auth_headers(), timeout=TIMEOUT
    )
    return _handle(response)


def get_investor_transactions(investor_id):
    """GET /investors/{investor_id}/transactions - investor details plus
    all of their transactions in a single combined response."""
    response = requests.get(
        f"{BASE_URL}/investors/{investor_id}/transactions", headers=_auth_headers(), timeout=TIMEOUT
    )
    return _handle(response)


# ---------- Admin: registration requests ----------

def admin_list_registration_requests(status="PENDING"):
    """GET /admin/registration-requests - ADMIN ONLY. status: PENDING
    (default), APPROVED, REJECTED, or ALL."""
    response = requests.get(
        f"{BASE_URL}/admin/registration-requests",
        params={"status": status},
        headers=_auth_headers(),
        timeout=TIMEOUT,
    )
    return _handle(response)


def admin_approve_registration(request_id, role=None):
    """POST /admin/registration-requests/{id}/approve - ADMIN ONLY.
    Optionally override the role the applicant requested."""
    payload = {"role": role} if role else {}
    response = requests.post(
        f"{BASE_URL}/admin/registration-requests/{request_id}/approve",
        json=payload, headers=_auth_headers(), timeout=TIMEOUT,
    )
    return _handle(response)


def admin_reject_registration(request_id, reason=None):
    """POST /admin/registration-requests/{id}/reject - ADMIN ONLY."""
    response = requests.post(
        f"{BASE_URL}/admin/registration-requests/{request_id}/reject",
        json={"reason": reason}, headers=_auth_headers(), timeout=TIMEOUT,
    )
    return _handle(response)


# ---------- Admin: user management ----------

def admin_list_users():
    """GET /admin/users - ADMIN ONLY."""
    response = requests.get(f"{BASE_URL}/admin/users", headers=_auth_headers(), timeout=TIMEOUT)
    return _handle(response)


def admin_update_user(username, role=None, is_active=None):
    """PATCH /admin/users/{username} - ADMIN ONLY. Change role and/or
    activate/deactivate. Pass only what you want to change."""
    payload = {}
    if role is not None:
        payload["role"] = role
    if is_active is not None:
        payload["is_active"] = is_active
    response = requests.patch(
        f"{BASE_URL}/admin/users/{username}", json=payload, headers=_auth_headers(), timeout=TIMEOUT
    )
    return _handle(response)


def admin_delete_user(username):
    """DELETE /admin/users/{username} - ADMIN ONLY."""
    response = requests.delete(f"{BASE_URL}/admin/users/{username}", headers=_auth_headers(), timeout=TIMEOUT)
    return _handle(response)
