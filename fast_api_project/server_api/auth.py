"""
Authentication layer: password hashing + JWT issue/verify.

This module is additive - nothing in main.py's existing route bodies
changes. Routes become private by adding one dependency parameter,
e.g.  def get_all_investors(cursor=Depends(get_db), user=Depends(get_current_user)):

Flow:
  1. Client calls POST /login with username + password.
  2. We look up the user in APP_USERS (Oracle), verify the password
     against the stored bcrypt hash, and if it matches, issue a JWT.
  3. Client sends that JWT as  Authorization: Bearer <token>  on every
     subsequent request.
  4. get_current_user() decodes/validates the token on each request and
     raises 401 if it's missing, expired, or invalid.
"""
import logging
import secrets
import string
from datetime import datetime, timedelta, timezone

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from config import JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_MINUTES, PASSWORD_RESET_EXPIRE_MINUTES
from database import get_db
from exceptions import AppError

logger = logging.getLogger("app.auth")

# bcrypt is the industry-standard choice for password hashing (salted,
# slow-by-design so brute-forcing a stolen hash is expensive).
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Tells FastAPI/Swagger where clients get a token from (shows up in the
# /docs "Authorize" button). tokenUrl is relative to the app root.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


class AuthError(AppError):
    """Raised for any login/token failure. Maps to HTTP 401."""
    status_code = 401
    message = "Could not validate credentials."


class PermissionDeniedError(AppError):
    """Raised when a valid, logged-in user's role doesn't allow the
    action they're attempting (e.g. a non-admin calling DELETE /investors
    or a user-management endpoint). Maps to HTTP 403 - distinct from 401,
    which means 'we don't know who you are' rather than 'we know who you
    are and you're not allowed to do this'."""
    status_code = 403
    message = "You don't have permission to perform this action."


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def generate_temp_password(length: int = 12) -> str:
    """Random one-time password issued when an admin approves a new
    account or a user resets their password. Uses `secrets` (not
    `random`) because this value is security-sensitive - it's mailed to
    the user and must not be guessable."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_reset_token() -> str:
    """URL-safe random token for the password-reset link. 32 bytes of
    entropy - infeasible to guess, and distinct from JWTs (this one is
    single-use and stored/checked against the database, not decoded)."""
    return secrets.token_urlsafe(32)


def create_access_token(username: str, role: str) -> str:
    """Builds a signed JWT containing the username (sub), role, and an
    expiry (exp). Anyone holding the JWT_SECRET_KEY can forge/verify
    these - so that key must stay only in .env, never committed."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def authenticate_user(username: str, password: str, cursor) -> dict:
    """Looks up the user in APP_USERS, checks the password, and returns
    a fresh JWT plus a couple of UI hints if everything checks out.
    Raises AuthError otherwise. Used only by the /login route in main.py."""
    cursor.execute(
        "SELECT username, password_hash, is_active, role, force_password_change "
        "FROM app_users WHERE username = :1",
        (username,),
    )
    row = cursor.fetchone()

    # Deliberately vague error for both "no such user" and "wrong password"
    # - telling an attacker WHICH one was wrong lets them enumerate valid
    # usernames, which is exactly what we don't want on a login endpoint.
    if row is None:
        raise AuthError("Incorrect username or password.")

    db_username, password_hash, is_active, role, force_password_change = row
    if is_active != 1:
        raise AuthError("This account is disabled.")
    if not verify_password(password, password_hash):
        raise AuthError("Incorrect username or password.")

    cursor.execute(
        "UPDATE app_users SET last_login = SYSDATE WHERE username = :1", (db_username,)
    )

    logger.info("User '%s' logged in successfully.", db_username)
    return {
        "access_token": create_access_token(db_username, role),
        "token_type": "bearer",
        "role": role,
        "force_password_change": bool(force_password_change),
    }


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """FastAPI dependency: decodes the bearer token on every protected
    request. Add `user=Depends(get_current_user)` to any route to make
    it require a valid, unexpired token - the route itself never has to
    check auth manually. Returns {"username": ..., "role": ...}."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        if username is None or role is None:
            raise AuthError()
        return {"username": username, "role": role}
    except JWTError:
        raise AuthError("Invalid or expired token.")


def require_role(*allowed_roles: str):
    """FastAPI dependency FACTORY: require_role("admin") returns a
    dependency that both authenticates the request AND checks the role.

    Usage:  def delete_investor(..., user=Depends(auth.require_role("admin"))):
    A non-admin gets a 403 before the route body ever runs; an
    unauthenticated caller still gets a 401 from get_current_user first.
    """
    def _check(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed_roles:
            raise PermissionDeniedError(
                f"This action requires one of these roles: {', '.join(allowed_roles)}."
            )
        return user

    return _check
