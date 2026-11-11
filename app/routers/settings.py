"""Einstellungen, Port, Testmail und Änderungsprotokoll."""
import csv
import io
import re
import sqlite3
from datetime import date, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import portcfg, proxy
from ..config import THEMES, settings as app_settings
from ..csvsafe import row as safe_row
from ..db import audit, get_db, get_setting, set_setting
from ..dsm import write_ui_config
from ..mailer import MailError, send_mail, smtp_config, valid_email
from ..security import admin_user, current_user
from ..timeutil import day_start_utc, local_iso, parse_utc, to_local, to_utc_str

router = APIRouter(prefix="/api/v1", tags=["Einstellungen"])


class SettingsIn(BaseModel):
    theme_default: Optional[str] = Field(None, description="Standard-Design für alle (classic, dsm, dark, warm)")
    public_url: Optional[str] = Field(None, max_length=200, description="z. B. https://zeit.meinedomain.de – leer = automatisch")
    dsm_login: Optional[bool] = None
    dsm_auto_create: Optional[bool] = None
    dsm_admins: Optional[bool] = None
    dsm_url: Optional[str] = Field(None, max_length=200)
    dsm_sso: Optional[bool] = Field(None, description="Anmeldung im DSM-Fenster (Single Sign-on)")
    http_access: Optional[bool] = Field(None, description="Weboberfläche über unverschlüsseltes http aus dem Netz erlauben")
    smtp_host: Optional[str] = Field(None, max_length=200)
    smtp_port: Optional[int] = Field(None, ge=1, le=65535)
    smtp_security: Optional[str] = Field(None, description="starttls, ssl oder none")
    smtp_user: Optional[str] = Field(None, max_length=200)
    smtp_password: Optional[str] = Field(None, max_length=200, description="leer lassen = unverändert")
    smtp_from: Optional[str] = Field(None, max_length=200)
    smtp_from_name: Optional[str] = Field(None, max_length=100)


ADMIN_ONLY = ("smtp_", "dsm_url", "dsm_secret_mode", "public_url_env")


def _secret_mode() -> str:
    from .. import dsm_auth
    return dsm_auth.secret_mode


def _port_changed():
    """Hat das Start-Skript wegen eines belegten Ports umgestellt? (Datei port_changed: „alt neu“)"""
    try:
        old, new = (app_settings.data_dir / "port_changed").read_text().split()[:2]
        return {"old": int(old), "new": int(new)}
    except (OSError, ValueError):
        return None


def settings_out(conn) -> dict:
    return {
        "port_changed": _port_changed(),
        "company_name": get_setting(conn, "company_name"),
        "theme_default": get_setting(conn, "theme_default") or "classic",
        "themes": THEMES,
        "public_url": get_setting(conn, "public_url"),
        "public_url_env": app_settings.public_url,
        "port": app_settings.port,
        "dsm_login": get_setting(conn, "dsm_login") == "1",
        "dsm_auto_create": get_setting(conn, "dsm_auto_create") == "1",
        "dsm_admins": get_setting(conn, "dsm_admins") == "1",
        "dsm_url": get_setting(conn, "dsm_url"),
        "dsm_sso": get_setting(conn, "dsm_sso") != "0",
        "dsm_secret_mode": _secret_mode(),
        "http_access": get_setting(conn, "http_access") == "1",
        "smtp_host": get_setting(conn, "smtp_host"),
        "smtp_port": int(get_setting(conn, "smtp_port") or 587),
        "smtp_security": get_setting(conn, "smtp_security") or "starttls",
        "smtp_user": get_setting(conn, "smtp_user"),
        "smtp_password_set": bool(get_setting(conn, "smtp_password")),
        "smtp_from": get_setting(conn, "smtp_from"),
        "smtp_from_name": get_setting(conn, "smtp_from_name"),
    }


@router.get("/settings")
def get_settings(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    out = settings_out(conn)
    if not user["is_admin"]:  # Mailserver-Zugang und interne Adressen gehen nur den Admin etwas an
        for k in [k for k in out if k.startswith(ADMIN_ONLY)]:
            out.pop(k)
    return out


@router.patch("/settings")
def update_settings(data: SettingsIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    changes = data.model_dump(exclude_unset=True)
    if changes.get("theme_default") is not None:
        if data.theme_default not in THEMES:
            raise HTTPException(400, "Unbekanntes Design")
        set_setting(conn, "theme_default", data.theme_default)
    for key in ("dsm_login", "dsm_auto_create", "dsm_admins", "http_access"):
        if changes.get(key) is not None:
            set_setting(conn, key, "1" if changes[key] else "0")
    if changes.get("http_access") is not None:
        from .. import httpguard
        httpguard.configure("1" if data.http_access else "0")
    if changes.get("dsm_admins") is False:
        # Vollzugriff, der nur aus DSM kam, sofort entziehen (nicht erst bei der nächsten Anmeldung)
        for u in conn.execute("SELECT id, username FROM users WHERE is_admin = 1 AND admin_via_dsm = 1").fetchall():
            if conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1").fetchone()[0] <= 1:
                break
            conn.execute("UPDATE users SET is_admin = 0, admin_via_dsm = 0 WHERE id = ?", (u["id"],))
            audit(conn, admin["id"], "user_admin_dsm", f"user:{u['id']}", f"{u['username']}: Vollzugriff aus DSM entzogen")
    if changes.get("dsm_sso") is not None:
        from ..dsm_auth import publish_proxy_files, rotate_secret
        set_setting(conn, "dsm_sso", "1" if data.dsm_sso else "0")
        if data.dsm_sso:
            rotate_secret()
        publish_proxy_files(bool(data.dsm_sso))
    if changes.get("dsm_url"):
        from ..dsm_auth import local_dsm_url
        url = local_dsm_url(data.dsm_url)
        if not url:
            raise HTTPException(400, "Erlaubt sind nur die DSM-Anmeldeports der NAS selbst: http://127.0.0.1:5000 oder "
                                     "https://127.0.0.1:5001 – andere Rechner und Ports sind aus Sicherheitsgründen nicht erlaubt.")
        set_setting(conn, "dsm_url", url)
    for key in ("smtp_host", "smtp_user", "smtp_from_name"):
        if key in changes:
            set_setting(conn, key, (changes[key] or "").strip())
    if "smtp_from" in changes:
        addr = (data.smtp_from or "").strip()
        if addr and not valid_email(addr):
            raise HTTPException(400, "Absenderadresse ist ungültig")
        set_setting(conn, "smtp_from", addr)
    if changes.get("smtp_port") is not None:
        set_setting(conn, "smtp_port", str(data.smtp_port))
    if changes.get("smtp_security") is not None:
        if data.smtp_security not in ("starttls", "ssl", "none"):
            raise HTTPException(400, "Verschlüsselung muss starttls, ssl oder none sein")
        set_setting(conn, "smtp_security", data.smtp_security)
    if data.smtp_password:  # leer = unverändert
        set_setting(conn, "smtp_password", data.smtp_password)
    if "public_url" in changes:
        url = (data.public_url or "").strip().rstrip("/")
        if url and not re.match(r"^https?://[^\s/]+(/[^\s]*)?$", url):
            raise HTTPException(400, "Adresse muss mit http:// oder https:// beginnen, z. B. https://zeit.meinedomain.de")
        set_setting(conn, "public_url", url)
        write_ui_config(conn)
    if changes:
        logged = {k: ("***" if k == "smtp_password" else v) for k, v in changes.items()}
        audit(conn, admin["id"], "settings_updated", "settings", str(logged))
    return settings_out(conn)


class PortIn(BaseModel):
    port: int = Field(..., ge=1, le=65535)


class ProxyIn(BaseModel):
    trusted_proxies: str = Field("", max_length=500)


@router.get("/settings/proxy")
def get_proxy(request: Request, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Eingetragene Proxys und – zum Ausfüllen – woher diese Anfrage gerade kommt."""
    return {"trusted_proxies": get_setting(conn, "trusted_proxies"), "env": proxy.env_entries(),
            "peer": request.scope.get("org_peer", ""), "client": request.client.host if request.client else "",
            "forwarded": request.headers.get("x-forwarded-for", "")}


@router.put("/settings/proxy")
def set_proxy(data: ProxyIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    nets, errors = proxy.parse(data.trusted_proxies)
    if errors:
        raise HTTPException(400, errors[0])
    value = ", ".join(str(n.network_address) if n.num_addresses == 1 else str(n) for n in nets)
    old = get_setting(conn, "trusted_proxies")
    set_setting(conn, "trusted_proxies", value)
    proxy.configure(value)
    audit(conn, admin["id"], "settings_updated", "settings", f"Vertrauenswürdiger Reverse Proxy: {old or '–'} → {value or '–'}")
    return {"trusted_proxies": value}


@router.post("/settings/port-notice/dismiss")
def dismiss_port_notice(admin=Depends(admin_user)):
    try:
        (app_settings.data_dir / "port_changed").unlink()
    except OSError:
        pass
    return {"ok": True}


@router.post("/settings/port")
def change_port(data: PortIn, request: Request, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Port ändern. Die App startet sich danach selbst neu (dauert wenige Sekunden)."""
    old = app_settings.port
    if data.port == old:
        return {"ok": True, "port": old, "restart": False}
    try:
        portcfg.check_port(data.port)
    except portcfg.PortError as e:
        raise HTTPException(400, str(e))
    audit(conn, admin["id"], "port_changed", "settings", f"{old} → {data.port}")
    try:
        portcfg.apply(data.port)
    except (portcfg.PortError, OSError) as e:
        raise HTTPException(500, f"Port konnte nicht gespeichert werden: {e}")
    host = request.url.hostname or "localhost"
    return {"ok": True, "port": data.port, "restart": True,
            "direct_url": f"{request.url.scheme}://{host}:{data.port}{app_settings.root_path}/"}


class MailTestIn(BaseModel):
    to: str = Field(..., max_length=200)


@router.post("/settings/mail-test")
def mail_test(data: MailTestIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    try:
        send_mail(conn, data.to.strip(), "Ordner-Regal: Testmail",
                  "Hallo,\n\nder Mailversand des Ordner-Regals funktioniert.\n")
    except MailError as e:
        raise HTTPException(502 if smtp_config(conn)["host"] else 400, str(e))
    audit(conn, admin["id"], "mail_test", "", data.to)
    return {"ok": True, "message": f"Testmail an {data.to.strip()} verschickt"}


# --- Änderungsprotokoll -------------------------------------------------------

AUDIT_CATEGORIES = {"regal": "Regal & Ordner", "dokumente": "Dokumente", "anmeldung": "Anmeldung", "benutzer": "Benutzer",
                    "einstellungen": "Einstellungen", "sicherung": "Datensicherung"}
TARGET_KINDS = {"shelf": "Regal", "board": "Fach", "binder": "Ordner", "tab": "Register", "doc": "Dokument", "share": "Freigabe",
                "tag": "Schlagwort", "inbox": "Eingang"}
AUDIT_ACTIONS = {
    "shelf_created": ("regal", "Regal angelegt"),
    "shelf_updated": ("regal", "Regal geändert"),
    "shelf_deleted": ("regal", "Regal gelöscht"),
    "shelf_access": ("regal", "Rechte am Regal geändert"),
    "board_created": ("regal", "Fach angelegt"),
    "board_updated": ("regal", "Fach geändert"),
    "board_deleted": ("regal", "Fach gelöscht"),
    "binder_created": ("regal", "Ordner angelegt"),
    "binder_updated": ("regal", "Ordner geändert"),
    "binder_moved": ("regal", "Ordner umgestellt"),
    "binder_deleted": ("regal", "Ordner gelöscht"),
    "tab_created": ("regal", "Register angelegt"),
    "tab_updated": ("regal", "Register geändert"),
    "tab_deleted": ("regal", "Register gelöscht"),
    "doc_filed": ("dokumente", "Dokument abgelegt"),
    "doc_updated": ("dokumente", "Dokument geändert"),
    "doc_removed": ("dokumente", "Aus Ordner entfernt"),
    "doc_deleted": ("dokumente", "Dokument gelöscht"),
    "doc_relinked": ("dokumente", "Datei wiedergefunden"),
    "doc_inbox": ("dokumente", "Aus dem Eingang einsortiert"),
    "folder_created": ("dokumente", "Ordner in Freigabe angelegt"),
    "inbox_source": ("einstellungen", "Eingang geändert"),
    "fulltext_reindex": ("einstellungen", "Volltext neu gelesen"),
    "tag_updated": ("dokumente", "Schlagwort geändert"),
    "tag_deleted": ("dokumente", "Schlagwort gelöscht"),
    "share_updated": ("einstellungen", "Freigabe ein-/ausgeschaltet"),
    "login": ("anmeldung", "Anmeldung"),
    "password_changed": ("anmeldung", "Passwort geändert"),
    "profile_updated": ("benutzer", "Eigenes Profil geändert"),
    "user_created": ("benutzer", "Benutzer angelegt"),
    "user_created_dsm": ("benutzer", "Benutzer per DSM angelegt"),
    "user_admin_dsm": ("benutzer", "Vollzugriff per DSM"),
    "user_updated": ("benutzer", "Benutzer geändert"),
    "settings_updated": ("einstellungen", "Einstellungen geändert"),
    "branding_updated": ("einstellungen", "Firmenname/Logo geändert"),
    "port_changed": ("einstellungen", "Port geändert"),
    "mail_test": ("einstellungen", "Testmail"),
    "export_csv": ("einstellungen", "CSV-Export"),
    "backup_created": ("sicherung", "Sicherung erstellt"),
    "backup_restored": ("sicherung", "Sicherung wiederhergestellt"),
    "backup_deleted": ("sicherung", "Sicherungen gelöscht"),
    "backup_download": ("sicherung", "Sicherung heruntergeladen"),
}
_ISO_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?\+00:00")


def _names(conn) -> dict:
    return {r["id"]: r["display_name"] for r in conn.execute("SELECT id, display_name FROM users")}


def _text(details: str, names: dict) -> str:
    """Zeitstempel in Ortszeit, „user:5“ als Name."""
    text = _ISO_UTC.sub(lambda m: to_local(parse_utc(m.group(0))).strftime("%d.%m.%Y %H:%M"), details or "")
    text = re.sub(r"\buser:(\d+)", lambda m: names.get(int(m.group(1)), "Benutzer " + m.group(1)), text)
    if text in ("local", "dsm"):
        text = {"local": "mit Passwort", "dsm": "mit DSM-Konto"}[text]
    return text


def _query(user_id, d_from, d_to, category, q):
    sql, args = " WHERE 1 = 1", []
    if user_id:
        sql += " AND (a.actor_id = ? OR a.target = ?)"
        args += [user_id, f"user:{user_id}"]
    if d_from:
        sql += " AND a.ts >= ?"
        args.append(to_utc_str(day_start_utc(d_from)))
    if d_to:
        sql += " AND a.ts < ?"
        args.append(to_utc_str(day_start_utc(d_to + timedelta(days=1))))
    if category:
        acts = [k for k, (c, _) in AUDIT_ACTIONS.items() if c == category]
        if not acts:
            raise HTTPException(400, "Unbekannter Bereich")
        sql += f" AND a.action IN ({','.join('?' * len(acts))})"
        args += acts
    if q:
        sql += " AND (a.details LIKE ? OR a.target LIKE ? OR a.action LIKE ?)"
        args += [f"%{q.strip()}%"] * 3
    return sql, args


def _rows(conn, where, args, limit, before_id=None):
    if before_id:
        where += " AND a.id < ?"
        args = args + [before_id]
    rows = conn.execute("SELECT a.*, u.display_name AS actor FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id"
                        + where + " ORDER BY a.id DESC LIMIT ?", args + [limit]).fetchall()
    names = _names(conn)
    out = []
    for r in rows:
        cat, label = AUDIT_ACTIONS.get(r["action"], ("", r["action"]))
        target = r["target"] or ""
        subject = names.get(int(target[5:])) if target.startswith("user:") and target[5:].isdigit() else ""
        kind = target.split(":", 1)[0]
        if not subject and kind in TARGET_KINDS:
            subject = TARGET_KINDS[kind]
        out.append({"id": r["id"], "ts_local": local_iso(r["ts"]), "actor": r["actor"] or "System",
                    "subject": subject or "", "category": cat, "label": label, "action": r["action"],
                    "text": _text(r["details"], names)})
    return out


@router.get("/audit")
def audit_log(
    user_id: Optional[int] = Query(None, description="Benutzer: handelnd oder betroffen"),
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    category: Optional[str] = Query(None, description=", ".join(AUDIT_CATEGORIES)),
    q: Optional[str] = Query(None, max_length=100, description="Suchtext"),
    before_id: Optional[int] = Query(None, description="zum Nachladen: nur Einträge älter als diese ID"),
    limit: int = Query(200, ge=1, le=1000),
    admin=Depends(admin_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Protokoll aller wichtigen Aktionen – neueste zuerst. Einträge lassen sich nicht ändern oder löschen."""
    where, args = _query(user_id, from_, to, category, q)
    return {"categories": AUDIT_CATEGORIES, "entries": _rows(conn, where, args, limit, before_id)}


@router.get("/audit/export.csv")
def audit_export(
    user_id: Optional[int] = None,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    category: Optional[str] = None,
    q: Optional[str] = Query(None, max_length=100),
    admin=Depends(admin_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """Änderungsprotokoll als CSV (Semikolon, für Excel). Text wird gegen Formel-Injection entschärft."""
    where, args = _query(user_id, from_, to, category, q)
    rows = _rows(conn, where, args, 100000)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Zeitpunkt", "Wer", "Betrifft", "Bereich", "Aktion", "Details"])
    for r in rows:
        ts = r["ts_local"][:16].replace("T", " ") if r["ts_local"] else ""
        w.writerow(safe_row([ts, r["actor"], r["subject"], AUDIT_CATEGORIES.get(r["category"], ""), r["label"], r["text"]]))
    audit(conn, admin["id"], "export_csv", "audit", f"Änderungsprotokoll, {len(rows)} Einträge")
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="aenderungsprotokoll.csv"'})
