"""Scanner-Eingang: Dateien ansehen und einsortieren, Eingänge verwalten, Ordner anlegen."""
import os
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import inbox, regal, shares
from ..db import audit, get_db
from ..regal import need_shelf, shelf_of
from ..security import admin_user, current_user
from ..timeutil import now_utc_str

router = APIRouter(prefix="/api/v1", tags=["Eingang"])

NO_RIGHTS = ("Das Regal darf dort nicht schreiben. In DSM: Systemsteuerung → Freigegebener Ordner → „{s}“ → Bearbeiten → "
             "Berechtigungen → System-interner Benutzer → „ordnerregal“: Lesen/Schreiben.")


@router.get("/inbox")
def list_inbox(source_id: Optional[int] = None, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"sources": inbox.sources(conn, user), "items": inbox.items(conn, user, source_id)}


@router.get("/inbox/count")
def count_inbox(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"count": len(inbox.items(conn, user))}


@router.get("/inbox/suggest")
def suggest(share: str = Query(..., max_length=64), path: str = Query(..., max_length=2000),
            user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    src = inbox.source_of(conn, user, share, path)
    try:
        return inbox.suggest(conn, src["share"], path)
    except shares.ShareError as e:
        raise HTTPException(e.status, str(e))


class TargetIn(BaseModel):
    share: str = Field(..., max_length=64)
    path: str = Field("", max_length=2000)
    remember: bool = Field(False, description="als Zielordner des Regal-Ordners merken")


class InboxFileIn(BaseModel):
    share: str = Field(..., max_length=64)
    path: str = Field(..., max_length=2000)
    tab_id: int
    title: Optional[str] = Field(None, max_length=200)
    doc_date: Optional[date] = None
    tags: List[str] = Field(default_factory=list, max_length=30)
    filename: Optional[str] = Field(None, max_length=220, description="neuer Dateiname, leer = unverändert")
    mode: str = Field("move", description="move = in den Zielordner verschieben, link = nur verknüpfen")
    target: Optional[TargetIn] = None


@router.post("/inbox/file")
def file_from_inbox(data: InboxFileIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Datei aus dem Eingang ins Register legen – verschoben in den Zielordner oder nur verknüpft."""
    if data.mode not in ("move", "link"):
        raise HTTPException(400, "mode muss move oder link sein")
    shelf_id = shelf_of(conn, "tab", data.tab_id)
    need_shelf(conn, user, shelf_id, edit=True)
    src_info = inbox.source_of(conn, user, data.share, data.path)
    share = src_info["share"]
    try:
        src = shares.resolve(conn, share, data.path)
    except shares.ShareError as e:
        raise HTTPException(e.status, str(e))
    if not src.is_file():
        raise HTTPException(404, "Die Datei ist nicht mehr im Eingang")
    rel = shares.clean_relpath(data.path)
    if conn.execute("SELECT 1 FROM files WHERE share = ? AND relpath = ?", (share, rel)).fetchone():
        raise HTTPException(409, "Die Datei liegt schon im Regal")

    moved_from = None
    if data.mode == "move":
        target = None
        if data.target:
            t_share, t_path = inbox.check_target(conn, data.target.share, data.target.path)
            target = {"share": t_share, "path": t_path}
        else:
            target = inbox.target_for_tab(conn, data.tab_id)
        if not target:
            raise HTTPException(409, "no_target")
        if not inbox.can_write(conn, user, share):
            raise HTTPException(403, f"Du darfst in DSM in „{share}“ nicht schreiben – die Datei kann dort nicht weg. „Nur verknüpfen“ geht.")
        if not inbox.can_write(conn, user, target["share"]):
            raise HTTPException(403, f"Du darfst in DSM in „{target['share']}“ nicht schreiben")
        try:
            folder = shares.resolve(conn, target["share"], target["path"])
        except shares.ShareError as e:
            raise HTTPException(e.status, str(e))
        if not folder.is_dir():
            raise HTTPException(400, "Den Zielordner gibt es nicht mehr")
        for check, name in ((src.parent, share), (folder, target["share"])):
            if not os.access(check, os.W_OK | os.X_OK):
                raise HTTPException(403, NO_RIGHTS.format(s=name))
        new_name = inbox.clean_filename(data.filename or src.name, src.name)
        try:
            dst = inbox.move_file(src, folder, new_name)
        except PermissionError:
            raise HTTPException(403, NO_RIGHTS.format(s=target["share"]))
        except OSError as e:
            raise HTTPException(500, f"Verschieben fehlgeschlagen: {e}")
        root = shares.resolve(conn, target["share"], "")
        moved_from = f"{share}/{rel}"
        share, rel = target["share"], dst.relative_to(root).as_posix()
        if data.target and data.target.remember:
            binder_id = conn.execute("SELECT binder_id FROM tabs WHERE id = ?", (data.tab_id,)).fetchone()[0]
            conn.execute("UPDATE binders SET target_share = ?, target_path = ? WHERE id = ?", (target["share"], target["path"], binder_id))
            audit(conn, user["id"], "binder_updated", f"binder:{binder_id}", f"Zielordner: {target['share']}/{target['path']}")

    try:
        f = regal.get_or_create_file(conn, share, rel)
    except shares.ShareError as e:
        raise HTTPException(e.status, str(e))
    from .. import fulltext
    old_share, old_rel = (moved_from.split("/", 1) if moved_from else (share, rel))
    if not fulltext.take_over_inbox_text(conn, old_share, old_rel, f["size"], f["id"], f["mtime"]):
        fulltext.kick()
    title = (data.title or "").strip() or regal.title_from_name(f["relpath"].rsplit("/", 1)[-1])
    doc_id = conn.execute("INSERT INTO documents(file_id, title, doc_date, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                          (f["id"], title, data.doc_date.isoformat() if data.doc_date else None, user["id"], now_utc_str())).lastrowid
    if data.tags:
        regal.set_tags(conn, doc_id, data.tags)
    sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM filings WHERE tab_id = ?", (data.tab_id,)).fetchone()[0]
    conn.execute("INSERT INTO filings(document_id, tab_id, sort, filed_at) VALUES (?, ?, ?, ?)", (doc_id, data.tab_id, sort, now_utc_str()))
    audit(conn, user["id"], "doc_inbox", f"doc:{doc_id}",
          (f"{moved_from} → verschoben nach {share}/{rel}" if moved_from else f"{share}/{rel} (nur verknüpft)") + f" → tab:{data.tab_id}")
    binder_id = conn.execute("SELECT binder_id FROM tabs WHERE id = ?", (data.tab_id,)).fetchone()[0]
    return {"doc_id": doc_id, "binder_id": binder_id, "share": share, "path": rel, "moved": bool(moved_from),
            "count": len(inbox.items(conn, user))}


# --- Ordner anlegen (für Zielordner) --------------------------------------------

class MkdirIn(BaseModel):
    share: str = Field(..., max_length=64)
    path: str = Field("", max_length=2000)
    name: str = Field(..., min_length=1, max_length=120)


@router.post("/browse/mkdir")
def mkdir(data: MkdirIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    if not inbox.can_write(conn, user, data.share):
        raise HTTPException(403, f"Du darfst in DSM in „{data.share}“ nicht schreiben")
    name = inbox.BAD_NAME.sub("_", data.name.strip()).strip(" .")
    if not name or shares.hidden(name):
        raise HTTPException(400, "Ungültiger Ordnername")
    try:
        parent = shares.resolve(conn, data.share, data.path)
    except shares.ShareError as e:
        raise HTTPException(e.status, str(e))
    if not parent.is_dir():
        raise HTTPException(404, "Ordner nicht gefunden")
    try:
        (parent / name).mkdir()
    except FileExistsError:
        pass
    except PermissionError:
        raise HTTPException(403, NO_RIGHTS.format(s=data.share))
    rel = shares.clean_relpath(f"{data.path}/{name}")
    audit(conn, user["id"], "folder_created", f"share:{data.share}", f"{data.share}/{rel}")
    return {"share": data.share, "path": rel}


# --- Verwaltung: Eingänge --------------------------------------------------------

class SourceIn(BaseModel):
    share: Optional[str] = Field(None, max_length=64)
    path: Optional[str] = Field(None, max_length=2000)
    name: Optional[str] = Field(None, max_length=60)
    active: Optional[bool] = None


@router.get("/admin/inbox-sources")
def admin_sources(admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    out = inbox.sources(conn)
    for s in out:
        try:
            p = shares.resolve(conn, s["share"], s["path"])
            s["ok"] = p.is_dir()
            s["writable"] = s["ok"] and os.access(p, os.W_OK | os.X_OK)
        except shares.ShareError:
            s["ok"] = s["writable"] = False
    return out


@router.post("/admin/inbox-sources", status_code=201)
def add_source(data: SourceIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    if not data.share:
        raise HTTPException(400, "Freigabe fehlt")
    share, path = inbox.check_target(conn, data.share, data.path or "")
    name = (data.name or "").strip() or f"{share}/{path}".rstrip("/")
    try:
        sid = conn.execute("INSERT INTO inbox_sources(share, relpath, name, active, created_at) VALUES (?, ?, ?, 1, ?)",
                           (share, path, name, now_utc_str())).lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Diesen Eingang gibt es schon")
    audit(conn, admin["id"], "inbox_source", f"share:{share}", f"Eingang angelegt: {share}/{path}")
    return admin_sources(admin, conn)


@router.patch("/admin/inbox-sources/{source_id}")
def update_source(source_id: int, data: SourceIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    if not conn.execute("SELECT 1 FROM inbox_sources WHERE id = ?", (source_id,)).fetchone():
        raise HTTPException(404, "Eingang nicht gefunden")
    if data.name:
        conn.execute("UPDATE inbox_sources SET name = ? WHERE id = ?", (data.name.strip(), source_id))
    if data.active is not None:
        conn.execute("UPDATE inbox_sources SET active = ? WHERE id = ?", (int(data.active), source_id))
    audit(conn, admin["id"], "inbox_source", f"inbox:{source_id}", str(data.model_dump(exclude_unset=True)))
    return admin_sources(admin, conn)


@router.delete("/admin/inbox-sources/{source_id}")
def delete_source(source_id: int, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("DELETE FROM inbox_sources WHERE id = ?", (source_id,))
    audit(conn, admin["id"], "inbox_source", f"inbox:{source_id}", "Eingang entfernt (Dateien bleiben)")
    return admin_sources(admin, conn)
