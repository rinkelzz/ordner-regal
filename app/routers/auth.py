"""Anmeldung, Abmeldung und eigenes Profil."""
import ipaddress
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..config import THEMES, settings
from ..db import audit, get_db, get_setting
from ..dsm_auth import (
    MAX_FAILS_PER_IP,
    MAX_FAILS_PER_NAME,
    clear_failures,
    dsm_password_check,
    dsm_user_row,
    is_dsm_admin,
    note_failure,
    too_many_attempts,
)
from ..shares import store_dsm_access
from ..security import (
    SESSION_COOKIE,
    SESSION_HEADER,
    create_session,
    current_user,
    delete_session,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/v1", tags=["Anmeldung & Profil"])


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)
    otp_code: Optional[str] = Field(None, max_length=12, description="Zwei-Faktor-Code des DSM-Kontos")


class PasswordIn(BaseModel):
    old_password: str = Field(max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


def user_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row["email"],
        "is_admin": bool(row["is_admin"]),
        "active": bool(row["active"]),
        "has_password": bool(row["password_hash"]),
        "theme": row["theme"],
    }


def client_ip(request: Request) -> str:
    """Absenderadresse. Hinter dem DSM-Reverse-Proxy kommt sie aus X-Forwarded-For – ausgewertet wird
    das nur, wenn die Anfrage von der NAS selbst (127.0.0.1) kommt (siehe main.py), sonst nicht fälschbar."""
    return request.client.host if request.client else ""


@router.post("/auth/login")
def login(data: LoginIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    username = data.username.strip()
    # Sperre pro Name+Adresse (5 Fehlversuche) und pro Name insgesamt (10), je 15 Minuten
    key = f"{username.lower()}|{client_ip(request)}"
    name_key = f"{username.lower()}|*"
    ip_key = f"*|{client_ip(request)}"
    if too_many_attempts(key) or too_many_attempts(name_key, MAX_FAILS_PER_NAME) or too_many_attempts(ip_key, MAX_FAILS_PER_IP):
        raise HTTPException(429, "Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen")
    user = conn.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
    via = "local"
    if not (user and verify_password(data.password, user["password_hash"])):
        user = None
        # Zweiter Weg: Zugangsdaten des DSM-Kontos
        if get_setting(conn, "dsm_login") == "1" and username and data.password:
            dsm_shares: dict = {}
            result = dsm_password_check(conn, username, data.password, (data.otp_code or "").strip(), dsm_shares)
            if result == "otp":
                raise HTTPException(401, "otp_required")
            if result == "otp_wrong":
                note_failure(key)
                note_failure(name_key)
                note_failure(ip_key)
                raise HTTPException(401, "Zwei-Faktor-Code falsch")
            if result == "ok":
                user, error = dsm_user_row(conn, username, is_dsm_admin(username))
                if not user:
                    raise HTTPException(403, error)
                via = "dsm"
                if "shares" in dsm_shares:  # sonst (DSM-Abfrage fehlgeschlagen) bleiben die bisherigen Rechte
                    store_dsm_access(conn, user["id"], dsm_shares["shares"])
        if not user:
            note_failure(key)
            note_failure(name_key)
            note_failure(ip_key)
            raise HTTPException(401, "Benutzername oder Passwort falsch")
    clear_failures(key)
    token = create_session(conn, user["id"])
    https = settings.cookie_secure or request.url.scheme == "https"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        # über https darf der Cookie auch im DSM-Fenster (iframe) mitgehen
        samesite="none" if https else "lax",
        secure=https,
        max_age=settings.session_days * 86400,
        path=settings.root_path or "/",
    )
    audit(conn, user["id"], "login", f"user:{user['id']}", via)
    # Zusätzlich als Wert für den Header X-Session-Token (DSM-Fenster, Safari)
    return {**user_out(user), "session_token": token}


@router.post("/auth/logout")
def logout(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    token = request.headers.get(SESSION_HEADER) or request.cookies.get(SESSION_COOKIE)
    if token:
        delete_session(conn, token)
    response.delete_cookie(SESSION_COOKIE, path=settings.root_path or "/")
    return {"ok": True}


def theme_of(conn, user) -> str:
    own = user["theme"]
    default = get_setting(conn, "theme_default") or "classic"
    return own if own in THEMES else default if default in THEMES else "classic"


_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def insecure_http(request: Request) -> bool:
    """True, wenn jemand per unverschlüsseltem http von außerhalb des Heimnetzes zugreift."""
    if request.url.scheme != "http" or getattr(request.state, "via_dsm", False):
        return False
    try:
        ip = ipaddress.ip_address(client_ip(request))
    except ValueError:
        return False
    # 100.64.0.0/10: Carrier-NAT bzw. Tailscale/VPN – zählt als eigenes Netz
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip in _CGNAT)


@router.get("/me")
def me(request: Request, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    return {**user_out(user),
            "via_dsm": bool(getattr(request.state, "via_dsm", False)),
            "theme_effective": theme_of(conn, user),
            "insecure_http": insecure_http(request)}


class ProfileIn(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=80)
    email: Optional[str] = Field(None, max_length=200, description="für Hinweise per Mail, leer = keine Mails")
    theme: Optional[str] = Field(None, description="Design: classic, dsm, dark, warm; null = Standard der Firma")


@router.patch("/me")
def update_me(data: ProfileIn, request: Request, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Eigenen Namen, Mailadresse und Design ändern."""
    from ..mailer import valid_email
    changes = data.model_dump(exclude_unset=True)
    if changes.get("display_name"):
        conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (data.display_name.strip(), user["id"]))
    if "email" in changes:
        addr = (data.email or "").strip()
        if addr and not valid_email(addr):
            raise HTTPException(400, "Ungültige E-Mail-Adresse")
        conn.execute("UPDATE users SET email = ? WHERE id = ?", (addr or None, user["id"]))
    if "theme" in changes:
        if data.theme is not None and data.theme not in THEMES:
            raise HTTPException(400, "Unbekanntes Design")
        conn.execute("UPDATE users SET theme = ? WHERE id = ?", (data.theme, user["id"]))
    if changes:
        audit(conn, user["id"], "profile_updated", f"user:{user['id']}", ", ".join(sorted(changes)))
    return me(request, conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone(), conn)


@router.post("/me/password")
def change_password(data: PasswordIn, request: Request, response: Response, user=Depends(current_user),
                    conn: sqlite3.Connection = Depends(get_db)):
    if not user["password_hash"]:
        raise HTTPException(400, "Du meldest dich mit deinem DSM-Konto an – das Passwort änderst du in DSM.")
    if not verify_password(data.old_password, user["password_hash"]):
        raise HTTPException(400, "Altes Passwort stimmt nicht")
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(data.new_password), user["id"]))
    # alle Sitzungen beenden und für dieses Gerät eine neue ausstellen
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    audit(conn, user["id"], "password_changed", f"user:{user['id']}")
    token = create_session(conn, user["id"])
    https = settings.cookie_secure or request.url.scheme == "https"
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="none" if https else "lax", secure=https,
                        max_age=settings.session_days * 86400, path=settings.root_path or "/")
    return {"ok": True, "session_token": token}
