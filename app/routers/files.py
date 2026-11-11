"""Freigaben durchsuchen, Dateien anzeigen/herunterladen, Freigaben verwalten."""
import sqlite3
from typing import Dict, List
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import filecheck, regal, shares
from ..db import audit, get_db
from ..security import admin_user, current_user
from ..timeutil import now_utc_str

router = APIRouter(prefix="/api/v1", tags=["Dateien & Freigaben"])

# Nur diese Arten zeigt der Browser direkt an – alles andere kommt als Download (kein HTML/SVG im Kontext der App)
INLINE = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"}


def _err(e: shares.ShareError):
    return HTTPException(e.status, str(e))


def _need_share(conn, user, share: str) -> str:
    access = shares.user_access(conn, user)
    name = next((n for n in access if n.lower() == (share or "").lower()), None)
    if not name:
        raise HTTPException(403, f"Kein Zugriff auf die Freigabe „{share}“")
    return name


def _send(path, mime: str, inline: bool):
    show = inline and mime in INLINE
    media = "text/plain; charset=utf-8" if mime == "text/plain" else mime
    name = path.name
    disp = ("inline" if show else "attachment") + f"; filename*=UTF-8''{quote(name)}"
    headers = {"Content-Disposition": disp, "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    if mime != "application/pdf":  # PDF-Anzeige der Browser verträgt keine Sandbox
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
    return FileResponse(str(path), media_type=media if show else "application/octet-stream", headers=headers)


@router.get("/shares")
def my_shares(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Freigaben, die ich im Regal benutzen darf (eingeschaltet + Rechte wie in DSM)."""
    access = shares.user_access(conn, user)
    return [{"name": n, "can_write": w} for n, w in sorted(access.items(), key=lambda x: x[0].lower())]


@router.get("/browse")
def browse(share: str = Query(..., max_length=64), path: str = Query("", max_length=2000),
           user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    name = _need_share(conn, user, share)
    try:
        out = shares.list_dir(conn, name, path)
    except shares.ShareError as e:
        raise _err(e)
    # Schon im Regal? (Dokument-ID zum Anzeigen)
    rels = [f["path"] for f in out["files"]]
    if rels:
        q = ",".join("?" * len(rels))
        known = {r["relpath"]: r["doc_id"] for r in conn.execute(
            f"SELECT f.relpath, d.id AS doc_id FROM files f JOIN documents d ON d.file_id = f.id WHERE f.share = ? AND f.relpath IN ({q})",
            [name] + rels)}
        for f in out["files"]:
            f["doc_id"] = known.get(f["path"])
    return out


@router.get("/browse/file")
def browse_file(share: str = Query(..., max_length=64), path: str = Query(..., max_length=2000), inline: bool = False,
                user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Vorschau einer Datei vor dem Ablegen."""
    name = _need_share(conn, user, share)
    try:
        p = shares.resolve(conn, name, path)
    except shares.ShareError as e:
        raise _err(e)
    if not p.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return _send(p, shares.guess_mime(p.name), inline)


@router.get("/docs/{doc_id}/file")
def doc_file(doc_id: int, inline: bool = False, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    row = regal.load_doc(conn, user, doc_id)
    try:
        p = shares.resolve(conn, row["share"], row["relpath"])
    except shares.ShareError as e:
        raise _err(e)
    if not p.is_file():
        if not row["missing_since"]:
            conn.execute("UPDATE files SET missing_since = ? WHERE id = ?", (now_utc_str(), row["file_id"]))
        raise HTTPException(404, "Die Datei ist nicht mehr da – verschoben, umbenannt oder gelöscht? "
                                 "Unter Verwaltung → Freigaben → „Jetzt prüfen“ sucht das Regal sie.")
    return _send(p, row["mime"] or shares.guess_mime(p.name), inline)


# --- Verwaltung ---------------------------------------------------------------

@router.get("/admin/shares")
def admin_shares(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    missing = conn.execute("SELECT COUNT(*) FROM files WHERE missing_since IS NOT NULL").fetchone()[0]
    return {"shares": shares.all_shares(conn), "missing_files": missing}


class ShareIn(BaseModel):
    enabled: bool


@router.patch("/admin/shares/{name}")
def toggle_share(name: str, data: ShareIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    shares.sync(conn)
    row = conn.execute("SELECT * FROM shares WHERE name = ?", (name,)).fetchone()
    if not row:
        raise HTTPException(404, "Freigabe nicht gefunden")
    from pathlib import Path
    if data.enabled and not shares.status(Path(row["path"]))["readable"]:
        raise HTTPException(400, f"Das Regal darf „{row['name']}“ noch nicht lesen. In DSM: Systemsteuerung → Freigegebener Ordner → "
                                 f"„{row['name']}“ → Bearbeiten → Berechtigungen → System-interner Benutzer → „ordnerregal“: Lesen/Schreiben.")
    conn.execute("UPDATE shares SET enabled = ? WHERE name = ?", (int(data.enabled), row["name"]))
    audit(conn, admin["id"], "share_updated", f"share:{row['name']}", ("eingeschaltet" if data.enabled else "ausgeschaltet"))
    return admin_shares(admin, conn)


@router.get("/admin/fulltext")
def fulltext_stats(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    from .. import fulltext
    return fulltext.stats(conn)


@router.post("/admin/fulltext/reindex")
def fulltext_reindex(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Alle Texte neu lesen (läuft im Hintergrund)."""
    from .. import fulltext
    fulltext.reindex_all(conn)
    fulltext.kick()
    audit(conn, admin["id"], "fulltext_reindex", "", "Volltext wird neu gelesen")
    return fulltext.stats(conn)


@router.post("/admin/filecheck")
def run_filecheck(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    r = filecheck.check(conn)
    return {**r, **admin_shares(admin, conn)}


class UserSharesIn(BaseModel):
    shares: Dict[str, bool] = Field(default_factory=dict, description="Freigabe → darf schreiben")


@router.get("/users/{user_id}/shares")
def get_user_shares(user_id: int, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    manual = {r["share"].lower(): bool(r["can_write"]) for r in
              conn.execute("SELECT * FROM share_access WHERE user_id = ? AND source = 'manual'", (user_id,))}
    dsm = {r["share"].lower(): bool(r["can_write"]) for r in
           conn.execute("SELECT * FROM share_access WHERE user_id = ? AND source = 'dsm'", (user_id,))}
    mode = "dsm" if u["dsm_synced_at"] else "admin" if u["is_admin"] else "manual"
    rows: List[dict] = []
    for r in conn.execute("SELECT name FROM shares WHERE enabled = 1 ORDER BY name COLLATE NOCASE"):
        k = r["name"].lower()
        src = dsm if mode == "dsm" else manual
        rows.append({"name": r["name"], "read": mode == "admin" or k in src, "write": mode == "admin" or src.get(k, False)})
    return {"mode": mode, "dsm_synced_at": u["dsm_synced_at"], "shares": rows}


@router.put("/users/{user_id}/shares")
def set_user_shares(user_id: int, data: UserSharesIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    """Freigaben für eigene Konten (ohne DSM). DSM-Benutzer bekommen ihre Rechte immer aus DSM."""
    u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not u:
        raise HTTPException(404, "Benutzer nicht gefunden")
    if u["dsm_synced_at"]:
        raise HTTPException(400, "Dieser Benutzer meldet sich mit DSM an – seine Rechte kommen aus DSM (Freigegebener Ordner → Berechtigungen)")
    enabled = {r["name"].lower(): r["name"] for r in conn.execute("SELECT name FROM shares WHERE enabled = 1")}
    clean = {enabled[k.lower()]: v for k, v in data.shares.items() if k.lower() in enabled}
    shares.set_manual_access(conn, user_id, clean)
    audit(conn, admin["id"], "user_updated", f"user:{user_id}",
          "Freigaben: " + (", ".join(f"{k} ({'schreiben' if v else 'lesen'})" for k, v in clean.items()) or "keine"))
    return get_user_shares(user_id, admin, conn)
