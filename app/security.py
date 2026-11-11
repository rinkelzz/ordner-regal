"""Passwörter und Sitzungen – nur Standardbibliothek."""
import hashlib
import hmac
import secrets
import sqlite3
from datetime import timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request, status

from .config import settings
from .db import get_db
from .timeutil import now_utc, now_utc_str, to_utc_str

SESSION_COOKIE = "org_session"
SESSION_HEADER = "x-session-token"  # für das DSM-Fenster (iframe), wo Browser Cookies oft blocken
_PBKDF2_ITERATIONS = 200_000


# --- Passwörter -------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: Optional[str]) -> bool:
    if not stored:
        return False
    try:
        _algo, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except ValueError:
        return False


# --- Sitzungen --------------------------------------------------------------

def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = now_utc() + timedelta(days=settings.session_days)
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_utc_str(),))  # Abgelaufene aufräumen
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_sha256(token), user_id, now_utc_str(), to_utc_str(expires)),
    )
    return token


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))


def _user_from_session(conn: sqlite3.Connection, token: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
        (_sha256(token), now_utc_str()),
    ).fetchone()


# --- Dependencies -----------------------------------------------------------

def current_user(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    """Angemeldet per DSM-Fenster (Single Sign-on über app.cgi) oder per Sitzung
    (Cookie oder X-Session-Token-Header)."""
    from .dsm_auth import dsm_user_row, proxy_info  # spät importiert, vermeidet Zyklus
    proxied = proxy_info(request)
    request.state.via_dsm = False
    if proxied and proxied["user"]:
        user, error = dsm_user_row(conn, proxied["user"], proxied["admin"])
        if not user:
            raise HTTPException(status.HTTP_403_FORBIDDEN, error)
        request.state.via_dsm = True
        if proxied.get("shares") is not None:  # app.cgi hat die Freigaben des DSM-Kontos mitgeschickt
            from .shares import store_dsm_access
            store_dsm_access(conn, user["id"], proxied["shares"])
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        return user
    token = request.headers.get(SESSION_HEADER) or request.cookies.get(SESSION_COOKIE)
    user = _user_from_session(conn, token) if token else None
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Nicht angemeldet")
    return user


def admin_user(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
    if not user["is_admin"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur für Administratoren (Vollzugriff)")
    return user
