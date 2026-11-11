"""Benutzerverwaltung (nur Admin). Admin = Vollzugriff (alle Regale), sonst Rechte pro Regal."""
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..db import audit, get_db
from ..security import admin_user, hash_password
from ..timeutil import now_utc_str
from .auth import user_out

router = APIRouter(prefix="/api/v1/users", tags=["Benutzer (Admin)"])

USERNAME_PATTERN = r"^[A-Za-z0-9._@\-]{1,64}$"


def _check_email(v):
    from ..mailer import valid_email
    v = (v or "").strip()
    if v and not valid_email(v):
        raise ValueError("Ungültige E-Mail-Adresse")
    return v or None


class UserCreate(BaseModel):
    username: str = Field(..., pattern=USERNAME_PATTERN, description="Gleicher Name wie in DSM = Anmeldung per DSM möglich")
    display_name: str = Field(..., min_length=1, max_length=80)
    email: Optional[str] = Field(None, max_length=200)
    password: Optional[str] = Field(None, min_length=8, max_length=256, description="leer = nur Anmeldung per DSM-Konto")
    is_admin: bool = False

    @field_validator("email")
    @classmethod
    def _em(cls, v):
        return _check_email(v)


class UserUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=80)
    email: Optional[str] = Field(None, max_length=200)
    password: Optional[str] = Field(None, min_length=8, max_length=256)
    is_admin: Optional[bool] = None
    active: Optional[bool] = None

    @field_validator("email")
    @classmethod
    def _em(cls, v):
        return _check_email(v)


def _get(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Benutzer nicht gefunden")
    return row


def _active_admins(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1").fetchone()[0]


@router.get("")
def list_users(include_inactive: bool = True, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    sql = "SELECT * FROM users" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY active DESC, display_name COLLATE NOCASE"
    return [user_out(r) for r in conn.execute(sql)]


@router.post("", status_code=201)
def create_user(data: UserCreate, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (data.username,)).fetchone():
        raise HTTPException(409, "Benutzername ist schon vergeben")
    cur = conn.execute(
        "INSERT INTO users(username, display_name, email, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (data.username, data.display_name.strip(), data.email,
         hash_password(data.password) if data.password else None, int(data.is_admin), now_utc_str()),
    )
    audit(conn, admin["id"], "user_created", f"user:{cur.lastrowid}",
          f"{data.username}{' (Admin)' if data.is_admin else ''}{'' if data.password else ', nur DSM-Anmeldung'}")
    return user_out(_get(conn, cur.lastrowid))


@router.patch("/{user_id}")
def update_user(user_id: int, data: UserUpdate, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    row = _get(conn, user_id)
    changes = data.model_dump(exclude_unset=True)
    losing_admin = (data.is_admin is False and row["is_admin"]) or (data.active is False and row["is_admin"] and row["active"])
    if losing_admin and _active_admins(conn) <= 1:
        raise HTTPException(400, "Es muss mindestens ein aktiver Admin bleiben")
    if changes.get("display_name"):
        conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (data.display_name.strip(), user_id))
    if "email" in changes:
        conn.execute("UPDATE users SET email = ? WHERE id = ?", (data.email, user_id))
    if data.password:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(data.password), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    if data.is_admin is not None:
        conn.execute("UPDATE users SET is_admin = ?, admin_via_dsm = 0 WHERE id = ?", (int(data.is_admin), user_id))
    if data.active is not None:
        conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(data.active), user_id))
        if not data.active:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    if changes:
        logged = {k: ("***" if k == "password" else v) for k, v in changes.items()}
        audit(conn, admin["id"], "user_updated", f"user:{user_id}", str(logged))
    return user_out(_get(conn, user_id))
