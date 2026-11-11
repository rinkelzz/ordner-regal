"""Fristen & Wiedervorlage: an Dokumenten anlegen, Übersicht, erledigen, Mail-Einstellungen."""
import sqlite3
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import deadlines as dl, regal, shares
from ..db import audit, get_db, get_setting, set_setting
from ..mailer import MailError, smtp_config, valid_email
from ..security import admin_user, current_user
from ..timeutil import now_utc_str

router = APIRouter(prefix="/api/v1", tags=["Fristen"])


class DeadlineIn(BaseModel):
    kind: Optional[str] = Field(None, description=", ".join(dl.KINDS))
    label: Optional[str] = Field(None, max_length=80)
    due_on: Optional[date] = None
    lead_days: Optional[int] = Field(None, ge=0, le=365)
    repeat: Optional[str] = Field(None, description="leer, monthly, quarterly, halfyearly, yearly")
    note: Optional[str] = Field(None, max_length=2000)
    user_ids: Optional[List[int]] = Field(None, max_length=50)
    emails: Optional[List[str]] = Field(None, max_length=20)


def _out(conn, d, user=None, with_doc: bool = True) -> dict:
    today = dl.today()
    due = date.fromisoformat(d["due_on"])
    out = {"id": d["id"], "document_id": d["document_id"], "kind": d["kind"], "kind_label": dl.KINDS.get(d["kind"], d["kind"]),
           "label": d["label"], "title": dl.label_of(d), "due_on": d["due_on"], "days": (due - today).days,
           "lead_days": d["lead_days"], "repeat": d["repeat"], "repeat_label": dl.REPEATS.get(d["repeat"], ""),
           "note": d["note"], "done_at": d["done_at"], "recipients": dl.recipients(conn, d["id"])}
    if with_doc:
        doc = conn.execute("SELECT title FROM documents WHERE id = ?", (d["document_id"],)).fetchone()
        places = regal.doc_places(conn, user, d["document_id"]) if user is not None else []
        out["doc_title"] = doc["title"] if doc else ""
        out["places"] = places
        out["can_edit"] = any(p["right"] == "edit" for p in places)
    return out


def _load(conn, user, deadline_id: int, edit: bool = False):
    d = conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone()
    if not d:
        raise HTTPException(404, "Frist nicht gefunden")
    regal.load_doc(conn, user, d["document_id"], edit=edit)  # Rechte wie beim Dokument
    return d


def _validate(conn, data: DeadlineIn, partial: bool):
    ch = data.model_dump(exclude_unset=True)
    if not partial and not data.due_on:
        raise HTTPException(400, "Datum fehlt")
    if ch.get("kind") is not None and data.kind not in dl.KINDS:
        raise HTTPException(400, "Unbekannte Art")
    if ch.get("repeat") is not None and data.repeat not in dl.REPEATS:
        raise HTTPException(400, "Unbekannte Wiederholung")
    if (data.kind == "eigene") and not (data.label or "").strip() and not partial:
        raise HTTPException(400, "Bitte einen Namen für die eigene Frist eingeben")
    for e in data.emails or []:
        if e.strip() and not valid_email(e.strip()):
            raise HTTPException(400, f"Ungültige Mailadresse: {e}")
    return ch


@router.get("/deadline-kinds")
def kinds(user=Depends(current_user)):
    return {"kinds": dl.KINDS, "repeats": dl.REPEATS}


@router.get("/docs/{doc_id}/deadlines")
def doc_deadlines(doc_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    regal.load_doc(conn, user, doc_id)
    rows = conn.execute("SELECT * FROM deadlines WHERE document_id = ? ORDER BY done_at IS NOT NULL, due_on", (doc_id,)).fetchall()
    return [_out(conn, d, with_doc=False) for d in rows]


@router.get("/docs/{doc_id}/recipient-candidates")
def candidates(doc_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Benutzer, die das Dokument sehen dürfen (nur die kommen als Empfänger in Frage)."""
    regal.load_doc(conn, user, doc_id)
    out = []
    for u in conn.execute("SELECT * FROM users WHERE active = 1 ORDER BY display_name COLLATE NOCASE").fetchall():
        if dl.user_sees_doc(conn, u, doc_id):
            out.append({"id": u["id"], "name": u["display_name"], "email": u["email"] or "", "me": u["id"] == user["id"]})
    return out


@router.post("/docs/{doc_id}/deadlines", status_code=201)
def create(doc_id: int, data: DeadlineIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    regal.load_doc(conn, user, doc_id, edit=True)
    _validate(conn, data, partial=False)
    did = conn.execute("INSERT INTO deadlines(document_id, kind, label, due_on, lead_days, repeat, note, created_by, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (doc_id, data.kind or "wiedervorlage", (data.label or "").strip(), data.due_on.isoformat(),
                        14 if data.lead_days is None else data.lead_days, data.repeat or "", (data.note or "").strip(),
                        user["id"], now_utc_str())).lastrowid
    dl.set_recipients(conn, did, data.user_ids if data.user_ids is not None else [user["id"]], data.emails or [])
    d = conn.execute("SELECT * FROM deadlines WHERE id = ?", (did,)).fetchone()
    audit(conn, user["id"], "deadline_created", f"deadline:{did}", f"{dl.label_of(d)} am {dl.fmt(d['due_on'])} (doc:{doc_id})")
    return _out(conn, d, user)


@router.patch("/deadlines/{deadline_id}")
def update(deadline_id: int, data: DeadlineIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    d = _load(conn, user, deadline_id, edit=True)
    ch = _validate(conn, data, partial=True)
    sets, args = [], []
    for k in ("kind", "repeat"):
        if ch.get(k) is not None:
            sets.append(f"{k} = ?"); args.append(ch[k])
    if "label" in ch:
        sets.append("label = ?"); args.append((data.label or "").strip())
    if "note" in ch:
        sets.append("note = ?"); args.append((data.note or "").strip())
    if ch.get("lead_days") is not None:
        sets.append("lead_days = ?"); args.append(data.lead_days)
    if ch.get("due_on"):
        sets.append("due_on = ?"); args.append(data.due_on.isoformat())
    if sets:
        conn.execute(f"UPDATE deadlines SET {', '.join(sets)}, updated_at = ? WHERE id = ?", args + [now_utc_str(), deadline_id])
    if data.user_ids is not None or data.emails is not None:
        cur = dl.recipients(conn, deadline_id)
        dl.set_recipients(conn, deadline_id, data.user_ids if data.user_ids is not None else [u["id"] for u in cur["users"]],
                          data.emails if data.emails is not None else cur["emails"])
    audit(conn, user["id"], "deadline_updated", f"deadline:{deadline_id}", ", ".join(sorted(ch)))
    return _out(conn, conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone(), user)


@router.delete("/deadlines/{deadline_id}")
def delete(deadline_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    d = _load(conn, user, deadline_id, edit=True)
    conn.execute("DELETE FROM deadlines WHERE id = ?", (deadline_id,))
    audit(conn, user["id"], "deadline_deleted", f"deadline:{deadline_id}", f"{dl.label_of(d)} am {dl.fmt(d['due_on'])}")
    return {"ok": True}


@router.post("/deadlines/{deadline_id}/done")
def done(deadline_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    d = _load(conn, user, deadline_id, edit=True)
    if d["done_at"]:
        raise HTTPException(400, "Schon erledigt")
    nxt = dl.mark_done(conn, d, user["id"])
    out = _out(conn, conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone(), user)
    out["next_due"] = nxt
    return out


@router.post("/deadlines/{deadline_id}/reopen")
def reopen(deadline_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    _load(conn, user, deadline_id, edit=True)
    conn.execute("UPDATE deadlines SET done_at = NULL, done_by = NULL, updated_at = ? WHERE id = ?", (now_utc_str(), deadline_id))
    audit(conn, user["id"], "deadline_updated", f"deadline:{deadline_id}", "wieder offen")
    return _out(conn, conn.execute("SELECT * FROM deadlines WHERE id = ?", (deadline_id,)).fetchone(), user)


@router.get("/deadlines")
def overview(status: str = Query("open", description="open | done"), days: Optional[int] = Query(None, ge=1, le=3650),
             mine: bool = False, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Alle Fristen an Dokumenten, die ich sehen darf – offen (nach Datum) oder erledigt (neueste zuerst)."""
    cond, args = shares.doc_filter(conn, user)
    sql = ("SELECT dl.* FROM deadlines dl JOIN documents d ON d.id = dl.document_id JOIN files f ON f.id = d.file_id WHERE " + cond)
    if status == "done":
        sql += " AND dl.done_at IS NOT NULL ORDER BY dl.done_at DESC LIMIT 200"
    else:
        sql += " AND dl.done_at IS NULL"
        if days:
            sql += " AND dl.due_on <= ?"
            args = args + [(dl.today() + timedelta(days=days)).isoformat()]
        sql += " ORDER BY dl.due_on LIMIT 1000"
    out = []
    for d in conn.execute(sql, args).fetchall():
        o = _out(conn, d, user)
        if not o["places"]:
            continue
        if mine and user["id"] not in {u["id"] for u in o["recipients"]["users"]}:
            continue
        out.append(o)
    return out


@router.get("/deadlines/summary")
def summary(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Für Reiter-Zähler und Seitenleiste: überfällig, nächste 7 Tage, die nächsten 5."""
    items = overview("open", 60, False, user, conn)
    return {"overdue": sum(1 for x in items if x["days"] < 0), "week": sum(1 for x in items if 0 <= x["days"] <= 7),
            "next": items[:5]}


# --- Einstellungen (Verwaltung → Mail) ------------------------------------------------

class SettingsIn(BaseModel):
    hour: Optional[int] = Field(None, ge=0, le=23)
    digest: Optional[bool] = None
    digest_weekday: Optional[int] = Field(None, ge=0, le=6)
    overdue_days: Optional[int] = Field(None, ge=0, le=90, description="0 = nicht nachfassen")


def _settings(conn) -> dict:
    return {"hour": int(get_setting(conn, "deadline_hour") or 7), "digest": get_setting(conn, "deadline_digest") == "1",
            "digest_weekday": int(get_setting(conn, "deadline_digest_weekday") or 0), "weekdays": dl.WEEKDAYS,
            "overdue_days": int(get_setting(conn, "deadline_overdue_days") or 7),
            "last_result": get_setting(conn, "deadline_last_result"), "smtp_ok": bool(smtp_config(conn)["host"]),
            "users_without_email": [u["display_name"] for u in conn.execute(
                "SELECT DISTINCT u.display_name FROM deadline_recipients r JOIN users u ON u.id = r.user_id "
                "JOIN deadlines d ON d.id = r.deadline_id WHERE d.done_at IS NULL AND (u.email IS NULL OR u.email = '')")]}


@router.get("/deadline-settings")
def get_settings(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    return _settings(conn)


@router.patch("/deadline-settings")
def set_settings(data: SettingsIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    ch = data.model_dump(exclude_unset=True)
    for k, key in (("hour", "deadline_hour"), ("digest_weekday", "deadline_digest_weekday"), ("overdue_days", "deadline_overdue_days")):
        if ch.get(k) is not None:
            set_setting(conn, key, str(ch[k]))
    if ch.get("digest") is not None:
        set_setting(conn, "deadline_digest", "1" if data.digest else "0")
    if ch:
        audit(conn, admin["id"], "settings_updated", "deadlines", f"Fristen-Mails: {ch}")
    return _settings(conn)


@router.post("/deadline-settings/run-now")
def run_now(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Fällige Erinnerungen und die Sammelmail sofort verschicken (bereits verschickte Erinnerungen kommen nicht doppelt)."""
    try:
        r = dl.run_due(conn, force=True)
    except MailError as e:
        raise HTTPException(502, str(e))
    if r and r["errors"] and not r["sent"] and not r["digest"]:
        raise HTTPException(502 if smtp_config(conn)["host"] else 400, "; ".join(r["errors"][:3]))
    return {**_settings(conn), "result": r}
