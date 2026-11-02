"""
Outgoing email helper.

Two emails are sent by this app:
  1. Temporary password, when an admin approves a registration request.
  2. Password reset link, when a user hits "forgot password".

If SMTP_HOST isn't configured (e.g. local dev), messages are logged
instead of sent, so the rest of the flow can still be exercised without
a real mailbox. In production, set SMTP_HOST/SMTP_USER/SMTP_PASSWORD in
.env (see config.py) and real emails will go out instead.
"""
import logging
import smtplib
from email.message import EmailMessage

from config import (
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    SMTP_FROM_EMAIL,
    SMTP_USE_TLS,
    FRONTEND_BASE_URL,
    PASSWORD_RESET_EXPIRE_MINUTES,
)

logger = logging.getLogger("app.email")


def _send(to_email: str, subject: str, body: str) -> None:
    if not SMTP_HOST:
        # No mail server configured - log so devs/testers can still see
        # the temp password / reset link during local development.
        logger.warning(
            "SMTP not configured - logging email instead of sending.\nTo: %s\nSubject: %s\n%s",
            to_email, subject, body,
        )
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            if SMTP_USE_TLS:
                server.starttls()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Email sent to %s (%s).", to_email, subject)
    except Exception:
        # Don't let a flaky mail server take down the request that
        # triggered it (e.g. an admin approving a registration) - log
        # the full traceback and let the caller decide what to tell the user.
        logger.exception("Failed to send email to %s.", to_email)
        raise


def send_new_account_email(to_email: str, username: str, temp_password: str) -> None:
    subject = "Your NavDhan account has been approved"
    body = (
        f"Hi,\n\n"
        f"Your registration request has been approved. You can now log in with:\n\n"
        f"  Username: {username}\n"
        f"  Temporary password: {temp_password}\n\n"
        f"You'll be required to set a new password the first time you log in.\n\n"
        f"Login page: {FRONTEND_BASE_URL}/login.html\n\n"
        f"- NavDhan Mutual Fund Services"
    )
    _send(to_email, subject, body)


def send_password_reset_email(to_email: str, username: str, token: str) -> None:
    reset_link = f"{FRONTEND_BASE_URL}/reset-password.html?token={token}"
    subject = "Reset your NavDhan password"
    body = (
        f"Hi {username},\n\n"
        f"We received a request to reset your password. This link expires in "
        f"{PASSWORD_RESET_EXPIRE_MINUTES} minutes:\n\n"
        f"  {reset_link}\n\n"
        f"If you didn't request this, you can ignore this email - your password "
        f"will stay unchanged.\n\n"
        f"- NavDhan Mutual Fund Services"
    )
    _send(to_email, subject, body)


def send_registration_rejected_email(to_email: str, username: str, reason: str | None) -> None:
    subject = "Your NavDhan registration request"
    reason_line = f"\nReason: {reason}\n" if reason else ""
    body = (
        f"Hi,\n\n"
        f"Your registration request for username '{username}' was not approved.\n"
        f"{reason_line}\n"
        f"If you believe this is a mistake, please get in touch with the administrator.\n\n"
        f"- NavDhan Mutual Fund Services"
    )
    _send(to_email, subject, body)
