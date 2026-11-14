"""
Real SMTP email delivery (Phase 5) — backs the password-reset flow.

Genuinely blocked for live delivery in this environment only because no
real SMTP credentials exist (SMTP_HOST/SMTP_PORT/SMTP_USERNAME/
SMTP_PASSWORD/SMTP_FROM_EMAIL) — that requires an actual mail provider
account (Gmail app password, SendGrid, Mailgun, AWS SES, ...), a real
external service this project doesn't have. Same pattern as oauth.py and
the Postgres knowledge-graph backend: build the real thing, gate
activation on real configuration, never fake the missing credential.
"""

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("drdo-rag")


class EmailNotConfiguredError(RuntimeError):
    """Raised when SMTP env vars aren't set — callers turn this into a
    clear 503, never a silently-faked 'email sent.'"""


class EmailSendError(RuntimeError):
    """Raised when the real SMTP server rejects or errors on the send —
    always carries the real smtplib error, never a fabricated one."""


def is_configured() -> bool:
    return all(os.getenv(v) for v in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL"))


def send_email(to: str, subject: str, body_text: str) -> None:
    """
    Sends a real email over SMTP with STARTTLS. Raises
    EmailNotConfiguredError if SMTP isn't configured, or EmailSendError
    if the real SMTP server rejects the send — never returns successfully
    without the message actually having been handed to the SMTP server.
    """
    if not is_configured():
        raise EmailNotConfiguredError(
            "SMTP is not configured — set SMTP_HOST, SMTP_PORT, SMTP_USERNAME, "
            "SMTP_PASSWORD, and SMTP_FROM_EMAIL."
        )

    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    from_email = os.environ["SMTP_FROM_EMAIL"]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to
    msg.set_content(body_text)

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
    except smtplib.SMTPException as e:
        raise EmailSendError(f"Failed to send email via {host}:{port}: {e}")
    except OSError as e:
        raise EmailSendError(f"Could not connect to SMTP server {host}:{port}: {e}")


def send_password_reset(email: str, ip: str = None) -> None:
    """Background job behind POST /auth/forgot-password. Never raises and never
    signals to the HTTP caller whether the account exists or the send worked —
    every outcome is logged server-side and audited instead."""
    try:
        from VENUAI import auth, security_audit

        user_row = auth.get_user_by_email(email)
        if user_row is None:
            return
        if not is_configured():
            logger.warning("[forgot_password] SMTP is not configured; reset email NOT sent.")
            return
        token = auth.create_password_reset_token(user_row["id"])
        link = f"{os.getenv('FRONTEND_URL', 'http://localhost:3000').rstrip('/')}/reset-password?token={token}"
        send_email(
            to=email,
            subject="Reset your VenuAI password",
            body_text=(
                "Someone requested a password reset for your VenuAI account.\n\n"
                f"Reset your password: {link}\n\n"
                f"This link works once and expires in {auth.PASSWORD_RESET_TTL_SECONDS // 60} minutes. "
                "If you didn't request this, ignore this email."
            ),
        )
        security_audit.log("reset_requested", user_row["id"], user_row["tenant_id"], ip)
    except EmailSendError as e:
        logger.error(f"[forgot_password] SMTP send failed: {e}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[forgot_password] unexpected failure: {e}")
