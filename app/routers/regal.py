"""Regale, Fächer, Ordner, Register und abgelegte Dokumente."""
import sqlite3
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import regal, shares
from ..db import audit, get_db
from ..regal import check_color, need_shelf, shelf_of
from ..security import admin_user, current_user
from ..timeutil import now_utc_str

router = APIRouter(prefix="/api/v1", tags=["Regal"])


def _err(e: shares.ShareError):
    return HTTPException(e.status, str(e))


# --- Regale -------------------------------------------------------------------

class ShelfIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=80)
    all_read: Optional[bool] = None


@router.get("/shelves")
def list_shelves(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    return {"shelves": regal.visible_shelves(conn, user), "can_create": bool(user["is_admin"]),
            "has_shares": bool(shares.user_access(conn, user))}


@router.post("/shelves")
def create_shelf(data: ShelfIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    if not data.name:
        raise HTTPException(400, "Name fehlt")
    sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM shelves").fetchone()[0]
    sid = conn.execute("INSERT INTO shelves(name, all_read, sort, created_at) VALUES (?, ?, ?, ?)",
                       (data.name.strip(), int(bool(data.all_read)), sort, now_utc_str())).lastrowid
    for i, name in enumerate(("Oberes Fach", "Unteres Fach")):
        conn.execute("INSERT INTO boards(shelf_id, name, sort) VALUES (?, ?, ?)", (sid, name, i))
    audit(conn, admin["id"], "shelf_created", f"shelf:{sid}", data.name.strip())
    return regal.shelf_detail(conn, admin, sid)


@router.get("/shelves/{shelf_id}")
def get_shelf(shelf_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    return regal.shelf_detail(conn, user, shelf_id)


@router.patch("/shelves/{shelf_id}")
def update_shelf(shelf_id: int, data: ShelfIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    need_shelf(conn, user, shelf_id, edit=True)
    ch = data.model_dump(exclude_unset=True)
    if ch.get("name"):
        conn.execute("UPDATE shelves SET name = ?, updated_at = ? WHERE id = ?", (data.name.strip(), now_utc_str(), shelf_id))
    if ch.get("all_read") is not None:
        if not user["is_admin"]:
            raise HTTPException(403, "Nur Administratoren ändern, wer das Regal sehen darf")
        conn.execute("UPDATE shelves SET all_read = ? WHERE id = ?", (int(data.all_read), shelf_id))
    if ch:
        audit(conn, user["id"], "shelf_updated", f"shelf:{shelf_id}", str(ch))
    return regal.shelf_detail(conn, user, shelf_id)


@router.delete("/shelves/{shelf_id}")
def delete_shelf(shelf_id: int, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    s = conn.execute("SELECT name FROM shelves WHERE id = ?", (shelf_id,)).fetchone()
    if not s:
        raise HTTPException(404, "Regal nicht gefunden")
    conn.execute("DELETE FROM shelves WHERE id = ?", (shelf_id,))
    regal.cleanup_orphans(conn)
    audit(conn, admin["id"], "shelf_deleted", f"shelf:{shelf_id}", s["name"] + " (Dateien auf der NAS bleiben unverändert)")
    return {"ok": True}


class AccessEntry(BaseModel):
    user_id: int
    can_edit: bool = False


class AccessIn(BaseModel):
    all_read: bool = False
    users: List[AccessEntry] = Field(default_factory=list, max_length=500)


@router.get("/shelves/{shelf_id}/access")
def get_access(shelf_id: int, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    need_shelf(conn, admin, shelf_id)
    s = conn.execute("SELECT all_read FROM shelves WHERE id = ?", (shelf_id,)).fetchone()
    rows = {r["user_id"]: bool(r["can_edit"]) for r in conn.execute("SELECT * FROM shelf_access WHERE shelf_id = ?", (shelf_id,))}
    users = [{"id": u["id"], "display_name": u["display_name"], "username": u["username"], "is_admin": bool(u["is_admin"]),
              "right": "edit" if u["is_admin"] else ("edit" if rows.get(u["id"]) else "read" if u["id"] in rows else "")}
             for u in conn.execute("SELECT * FROM users WHERE active = 1 ORDER BY display_name COLLATE NOCASE")]
    return {"all_read": bool(s["all_read"]), "users": users}


@router.put("/shelves/{shelf_id}/access")
def set_access(shelf_id: int, data: AccessIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    need_shelf(conn, admin, shelf_id)
    conn.execute("UPDATE shelves SET all_read = ? WHERE id = ?", (int(data.all_read), shelf_id))
    conn.execute("DELETE FROM shelf_access WHERE shelf_id = ?", (shelf_id,))
    for e in data.users:
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (e.user_id,)).fetchone():
            conn.execute("INSERT OR REPLACE INTO shelf_access(shelf_id, user_id, can_edit) VALUES (?, ?, ?)",
                         (shelf_id, e.user_id, int(e.can_edit)))
    names = ", ".join(f"user:{e.user_id} ({'bearbeiten' if e.can_edit else 'ansehen'})" for e in data.users) or "niemand einzeln"
    audit(conn, admin["id"], "shelf_access", f"shelf:{shelf_id}", ("alle sehen hinein; " if data.all_read else "") + names)
    return get_access(shelf_id, admin, conn)


# --- Fächer -------------------------------------------------------------------

class BoardIn(BaseModel):
    name: Optional[str] = Field(None, max_length=60)


@router.post("/shelves/{shelf_id}/boards")
def create_board(shelf_id: int, data: BoardIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    need_shelf(conn, user, shelf_id, edit=True)
    sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM boards WHERE shelf_id = ?", (shelf_id,)).fetchone()[0]
    bid = conn.execute("INSERT INTO boards(shelf_id, name, sort) VALUES (?, ?, ?)", (shelf_id, (data.name or "").strip(), sort)).lastrowid
    audit(conn, user["id"], "board_created", f"board:{bid}", data.name or "")
    return regal.shelf_detail(conn, user, shelf_id)


@router.patch("/boards/{board_id}")
def update_board(board_id: int, data: BoardIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    shelf_id = shelf_of(conn, "board", board_id)
    need_shelf(conn, user, shelf_id, edit=True)
    conn.execute("UPDATE boards SET name = ? WHERE id = ?", ((data.name or "").strip(), board_id))
    audit(conn, user["id"], "board_updated", f"board:{board_id}", data.name or "")
    return regal.shelf_detail(conn, user, shelf_id)


@router.delete("/boards/{board_id}")
def delete_board(board_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    shelf_id = shelf_of(conn, "board", board_id)
    need_shelf(conn, user, shelf_id, edit=True)
    if conn.execute("SELECT 1 FROM binders WHERE board_id = ?", (board_id,)).fetchone():
        raise HTTPException(400, "Im Fach stehen noch Ordner – bitte erst umstellen oder löschen")
    conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))
    audit(conn, user["id"], "board_deleted", f"board:{board_id}", "")
    return regal.shelf_detail(conn, user, shelf_id)


class LayoutBoard(BaseModel):
    id: int
    binders: List[int] = Field(default_factory=list, max_length=500)


class LayoutIn(BaseModel):
    boards: List[LayoutBoard] = Field(..., max_length=100)


@router.put("/shelves/{shelf_id}/layout")
def set_layout(shelf_id: int, data: LayoutIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Reihenfolge der Fächer und Ordner (Drag & Drop). Ordner können dabei das Fach wechseln – nur innerhalb des Regals."""
    need_shelf(conn, user, shelf_id, edit=True)
    own_boards = {r[0] for r in conn.execute("SELECT id FROM boards WHERE shelf_id = ?", (shelf_id,))}
    own_binders = {r[0] for r in conn.execute("SELECT x.id FROM binders x JOIN boards b ON b.id = x.board_id WHERE b.shelf_id = ?", (shelf_id,))}
    sent = [x for b in data.boards for x in b.binders]
    if {b.id for b in data.boards} - own_boards or set(sent) - own_binders or len(sent) != len(set(sent)):
        raise HTTPException(400, "Ungültige Anordnung")
    for i, b in enumerate(data.boards):
        conn.execute("UPDATE boards SET sort = ? WHERE id = ?", (i, b.id))
        for j, xid in enumerate(b.binders):
            conn.execute("UPDATE binders SET board_id = ?, sort = ? WHERE id = ?", (b.id, j, xid))
    audit(conn, user["id"], "binder_moved", f"shelf:{shelf_id}", "Ordner umgestellt")
    return regal.shelf_detail(conn, user, shelf_id)


def _target(conn, user, share: Optional[str], path: Optional[str]):
    """Zielordner prüfen – nur Freigaben, die der Benutzer lesen darf. Leer = keiner."""
    from .. import inbox
    t_share, t_path = inbox.check_target(conn, share or None, path)
    if t_share and t_share.lower() not in {n.lower() for n in shares.user_access(conn, user)}:
        raise HTTPException(403, f"Kein Zugriff auf die Freigabe „{t_share}“")
    return t_share, t_path


# --- Ordner -------------------------------------------------------------------

class BinderIn(BaseModel):
    board_id: Optional[int] = None
    title: Optional[str] = Field(None, min_length=1, max_length=60)
    color: Optional[str] = Field(None, max_length=7)
    width: Optional[int] = Field(None, ge=1, le=3)
    notes: Optional[str] = Field(None, max_length=2000)
    tabs: Optional[List[str]] = Field(None, max_length=50, description="Register beim Anlegen, leer = „Allgemein“")
    target_share: Optional[str] = Field(None, max_length=64, description="Zielordner fürs Einsortieren aus dem Eingang; leer = keiner")
    target_path: Optional[str] = Field(None, max_length=2000)


@router.post("/binders")
def create_binder(data: BinderIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    if not data.board_id or not data.title:
        raise HTTPException(400, "Fach und Titel fehlen")
    shelf_id = shelf_of(conn, "board", data.board_id)
    need_shelf(conn, user, shelf_id, edit=True)
    sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM binders WHERE board_id = ?", (data.board_id,)).fetchone()[0]
    xid = conn.execute("INSERT INTO binders(board_id, title, color, width, notes, sort, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (data.board_id, data.title.strip(), check_color(data.color or "#2b6cb0"), data.width or 2,
                        (data.notes or "").strip(), sort, now_utc_str())).lastrowid
    if data.target_share:
        t_share, t_path = _target(conn, user, data.target_share, data.target_path)
        conn.execute("UPDATE binders SET target_share = ?, target_path = ? WHERE id = ?", (t_share, t_path, xid))
    names = [t.strip()[:40] for t in (data.tabs or []) if t and t.strip()] or [regal.DEFAULT_TAB]
    for i, name in enumerate(names):
        conn.execute("INSERT INTO tabs(binder_id, name, sort) VALUES (?, ?, ?)", (xid, name, i))
    audit(conn, user["id"], "binder_created", f"binder:{xid}", data.title.strip())
    return regal.binder_detail(conn, user, xid)


@router.get("/binders/{binder_id}")
def get_binder(binder_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    return regal.binder_detail(conn, user, binder_id)


@router.patch("/binders/{binder_id}")
def update_binder(binder_id: int, data: BinderIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    shelf_id = shelf_of(conn, "binder", binder_id)
    need_shelf(conn, user, shelf_id, edit=True)
    ch = data.model_dump(exclude_unset=True)
    sets, args = [], []
    if ch.get("title"):
        sets.append("title = ?"); args.append(data.title.strip())
    if ch.get("color"):
        sets.append("color = ?"); args.append(check_color(data.color))
    if ch.get("width"):
        sets.append("width = ?"); args.append(data.width)
    if "notes" in ch:
        sets.append("notes = ?"); args.append((data.notes or "").strip())
    if "target_share" in ch:
        t_share, t_path = _target(conn, user, data.target_share, data.target_path)
        sets += ["target_share = ?", "target_path = ?"]
        args += [t_share, t_path]
    if ch.get("board_id"):
        if shelf_of(conn, "board", data.board_id) != shelf_id:
            raise HTTPException(400, "Ordner können nur innerhalb des Regals umgestellt werden")
        sets.append("board_id = ?"); args.append(data.board_id)
    if sets:
        conn.execute(f"UPDATE binders SET {', '.join(sets)}, updated_at = ? WHERE id = ?", args + [now_utc_str(), binder_id])
        audit(conn, user["id"], "binder_updated", f"binder:{binder_id}", ", ".join(k for k in ch if k != "tabs") +
              (f" (Zielordner: {ch.get('target_share') or 'keiner'}/{ch.get('target_path') or ''})" if "target_share" in ch else ""))
    return regal.binder_detail(conn, user, binder_id)


@router.delete("/binders/{binder_id}")
def delete_binder(binder_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    shelf_id = shelf_of(conn, "binder", binder_id)
    need_shelf(conn, user, shelf_id, edit=True)
    title = conn.execute("SELECT title FROM binders WHERE id = ?", (binder_id,)).fetchone()["title"]
    conn.execute("DELETE FROM binders WHERE id = ?", (binder_id,))
    regal.cleanup_orphans(conn)
    audit(conn, user["id"], "binder_deleted", f"binder:{binder_id}", title + " (Dateien auf der NAS bleiben unverändert)")
    return regal.shelf_detail(conn, user, shelf_id)


# --- Register -----------------------------------------------------------------

class TabIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40)
    color: Optional[str] = Field(None, max_length=7)
    target_share: Optional[str] = Field(None, max_length=64, description="eigener Zielordner; leer = der des Ordners")
    target_path: Optional[str] = Field(None, max_length=2000)


@router.post("/binders/{binder_id}/tabs")
def create_tab(binder_id: int, data: TabIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    need_shelf(conn, user, shelf_of(conn, "binder", binder_id), edit=True)
    if not data.name:
        raise HTTPException(400, "Name fehlt")
    sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM tabs WHERE binder_id = ?", (binder_id,)).fetchone()[0]
    tid = conn.execute("INSERT INTO tabs(binder_id, name, color, sort) VALUES (?, ?, ?, ?)",
                       (binder_id, data.name.strip(), check_color(data.color, allow_empty=True), sort)).lastrowid
    audit(conn, user["id"], "tab_created", f"tab:{tid}", data.name.strip())
    return regal.binder_detail(conn, user, binder_id)


@router.patch("/tabs/{tab_id}")
def update_tab(tab_id: int, data: TabIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    need_shelf(conn, user, shelf_of(conn, "tab", tab_id), edit=True)
    ch = data.model_dump(exclude_unset=True)
    if ch.get("name"):
        conn.execute("UPDATE tabs SET name = ? WHERE id = ?", (data.name.strip(), tab_id))
    if "color" in ch:
        conn.execute("UPDATE tabs SET color = ? WHERE id = ?", (check_color(data.color, allow_empty=True), tab_id))
    if "target_share" in ch:
        t_share, t_path = _target(conn, user, data.target_share, data.target_path)
        conn.execute("UPDATE tabs SET target_share = ?, target_path = ? WHERE id = ?", (t_share, t_path, tab_id))
    if ch:
        audit(conn, user["id"], "tab_updated", f"tab:{tab_id}", str(ch))
    binder_id = conn.execute("SELECT binder_id FROM tabs WHERE id = ?", (tab_id,)).fetchone()[0]
    return regal.binder_detail(conn, user, binder_id)


@router.delete("/tabs/{tab_id}")
def delete_tab(tab_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    need_shelf(conn, user, shelf_of(conn, "tab", tab_id), edit=True)
    t = conn.execute("SELECT binder_id, name FROM tabs WHERE id = ?", (tab_id,)).fetchone()
    if conn.execute("SELECT COUNT(*) FROM tabs WHERE binder_id = ?", (t["binder_id"],)).fetchone()[0] <= 1:
        raise HTTPException(400, "Ein Ordner braucht mindestens ein Register")
    if conn.execute("SELECT 1 FROM filings WHERE tab_id = ?", (tab_id,)).fetchone():
        raise HTTPException(400, "Im Register liegen noch Dokumente – bitte erst verschieben")
    conn.execute("DELETE FROM tabs WHERE id = ?", (tab_id,))
    audit(conn, user["id"], "tab_deleted", f"tab:{tab_id}", t["name"])
    return regal.binder_detail(conn, user, t["binder_id"])


class OrderIn(BaseModel):
    ids: List[int] = Field(..., max_length=500)


@router.put("/binders/{binder_id}/tab-order")
def order_tabs(binder_id: int, data: OrderIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    need_shelf(conn, user, shelf_of(conn, "binder", binder_id), edit=True)
    own = {r[0] for r in conn.execute("SELECT id FROM tabs WHERE binder_id = ?", (binder_id,))}
    if set(data.ids) != own or len(data.ids) != len(own):
        raise HTTPException(400, "Ungültige Reihenfolge")
    for i, tid in enumerate(data.ids):
        conn.execute("UPDATE tabs SET sort = ? WHERE id = ?", (i, tid))
    return regal.binder_detail(conn, user, binder_id)


# --- Dokumente ----------------------------------------------------------------

class FileRef(BaseModel):
    share: str = Field(..., max_length=64)
    path: str = Field(..., max_length=2000)
    title: Optional[str] = Field(None, max_length=200)


class FileIn(BaseModel):
    files: List[FileRef] = Field(..., min_length=1, max_length=200)
    tags: List[str] = Field(default_factory=list, max_length=30)
    doc_date: Optional[date] = None


@router.post("/tabs/{tab_id}/docs")
def file_docs(tab_id: int, data: FileIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Dateien aus einer Freigabe in ein Register legen. Die Dateien bleiben, wo sie sind."""
    need_shelf(conn, user, shelf_of(conn, "tab", tab_id), edit=True)
    access = {n.lower() for n in shares.user_access(conn, user)}
    added, already = 0, 0
    for ref in data.files:
        if ref.share.lower() not in access:
            raise HTTPException(403, f"Du hast in DSM keinen Zugriff auf die Freigabe „{ref.share}“")
        try:
            f = regal.get_or_create_file(conn, ref.share, ref.path)
        except shares.ShareError as e:
            raise _err(e)
        doc = conn.execute("SELECT id FROM documents WHERE file_id = ?", (f["id"],)).fetchone()
        if doc:
            doc_id = doc["id"]
        else:
            title = (ref.title or "").strip() or regal.title_from_name(f["relpath"].rsplit("/", 1)[-1])
            doc_id = conn.execute("INSERT INTO documents(file_id, title, doc_date, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                                  (f["id"], title, data.doc_date.isoformat() if data.doc_date else None, user["id"], now_utc_str())).lastrowid
            if data.tags:
                regal.set_tags(conn, doc_id, data.tags)
        if conn.execute("SELECT 1 FROM filings WHERE document_id = ? AND tab_id = ?", (doc_id, tab_id)).fetchone():
            already += 1
            continue
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM filings WHERE tab_id = ?", (tab_id,)).fetchone()[0]
        conn.execute("INSERT INTO filings(document_id, tab_id, sort, filed_at) VALUES (?, ?, ?, ?)", (doc_id, tab_id, sort, now_utc_str()))
        audit(conn, user["id"], "doc_filed", f"doc:{doc_id}", f"{f['share']}/{f['relpath']} → tab:{tab_id}")
        added += 1
    binder_id = conn.execute("SELECT binder_id FROM tabs WHERE id = ?", (tab_id,)).fetchone()[0]
    if added:
        from .. import fulltext
        fulltext.kick()
    return {"added": added, "already": already, "binder": regal.binder_detail(conn, user, binder_id)}


@router.get("/docs/{doc_id}")
def get_doc(doc_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    row = regal.load_doc(conn, user, doc_id)
    out = regal.doc_out(conn, row)
    out["places"] = regal.doc_places(conn, user, doc_id)
    out["can_edit"] = any(p["right"] == "edit" for p in out["places"])
    return out


class DocIn(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    doc_date: Optional[date] = None
    notes: Optional[str] = Field(None, max_length=5000)
    tags: Optional[List[str]] = Field(None, max_length=30)


@router.patch("/docs/{doc_id}")
def update_doc(doc_id: int, data: DocIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    regal.load_doc(conn, user, doc_id, edit=True)
    ch = data.model_dump(exclude_unset=True)
    if ch.get("title"):
        conn.execute("UPDATE documents SET title = ? WHERE id = ?", (data.title.strip(), doc_id))
    if "doc_date" in ch:
        conn.execute("UPDATE documents SET doc_date = ? WHERE id = ?", (data.doc_date.isoformat() if data.doc_date else None, doc_id))
    if "notes" in ch:
        conn.execute("UPDATE documents SET notes = ? WHERE id = ?", ((data.notes or "").strip(), doc_id))
    if data.tags is not None:
        regal.set_tags(conn, doc_id, data.tags)
        conn.execute("DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM document_tags dt WHERE dt.tag_id = tags.id)")
    if ch:
        conn.execute("UPDATE documents SET updated_at = ? WHERE id = ?", (now_utc_str(), doc_id))
        audit(conn, user["id"], "doc_updated", f"doc:{doc_id}", ", ".join(sorted(ch)))
    return get_doc(doc_id, user, conn)


class MoveIn(BaseModel):
    from_tab: int
    to_tab: int
    keep: bool = Field(False, description="True = zusätzlich ablegen statt verschieben")


@router.post("/docs/{doc_id}/move")
def move_doc(doc_id: int, data: MoveIn, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    regal.load_doc(conn, user, doc_id)
    need_shelf(conn, user, shelf_of(conn, "tab", data.to_tab), edit=True)
    if not conn.execute("SELECT 1 FROM filings WHERE document_id = ? AND tab_id = ?", (doc_id, data.from_tab)).fetchone():
        raise HTTPException(404, "Dokument liegt nicht in diesem Register")
    if not data.keep:
        need_shelf(conn, user, shelf_of(conn, "tab", data.from_tab), edit=True)
    if data.from_tab != data.to_tab and not conn.execute("SELECT 1 FROM filings WHERE document_id = ? AND tab_id = ?",
                                                         (doc_id, data.to_tab)).fetchone():
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM filings WHERE tab_id = ?", (data.to_tab,)).fetchone()[0]
        conn.execute("INSERT INTO filings(document_id, tab_id, sort, filed_at) VALUES (?, ?, ?, ?)", (doc_id, data.to_tab, sort, now_utc_str()))
    if not data.keep and data.from_tab != data.to_tab:
        conn.execute("DELETE FROM filings WHERE document_id = ? AND tab_id = ?", (doc_id, data.from_tab))
    audit(conn, user["id"], "doc_filed", f"doc:{doc_id}", f"tab:{data.from_tab} → tab:{data.to_tab}" + (" (zusätzlich)" if data.keep else ""))
    return get_doc(doc_id, user, conn)


@router.delete("/tabs/{tab_id}/docs/{doc_id}")
def remove_doc(tab_id: int, doc_id: int, user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Aus dem Register nehmen. Liegt das Dokument nirgends mehr, verschwindet es aus dem Regal – die Datei bleibt auf der NAS."""
    need_shelf(conn, user, shelf_of(conn, "tab", tab_id), edit=True)
    regal.load_doc(conn, user, doc_id)
    if not conn.execute("DELETE FROM filings WHERE document_id = ? AND tab_id = ?", (doc_id, tab_id)).rowcount:
        raise HTTPException(404, "Dokument liegt nicht in diesem Register")
    gone = regal.cleanup_document(conn, doc_id)
    audit(conn, user["id"], "doc_deleted" if gone else "doc_removed", f"doc:{doc_id}", f"aus tab:{tab_id} (Datei bleibt auf der NAS)")
    binder_id = conn.execute("SELECT binder_id FROM tabs WHERE id = ?", (tab_id,)).fetchone()[0]
    return regal.binder_detail(conn, user, binder_id)


# --- Schlagworte & Suche -------------------------------------------------------

@router.get("/tags")
def list_tags(user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    return [{"id": r["id"], "name": r["name"], "color": r["color"], "docs": r["n"]} for r in conn.execute(
        "SELECT t.*, COUNT(dt.document_id) AS n FROM tags t LEFT JOIN document_tags dt ON dt.tag_id = t.id "
        "GROUP BY t.id ORDER BY t.name COLLATE NOCASE")]


class TagIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=40)
    color: Optional[str] = Field(None, max_length=7)


@router.patch("/tags/{tag_id}")
def update_tag(tag_id: int, data: TagIn, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    ch = data.model_dump(exclude_unset=True)
    try:
        if ch.get("name"):
            conn.execute("UPDATE tags SET name = ? WHERE id = ?", (data.name.strip(), tag_id))
    except sqlite3.IntegrityError:
        raise HTTPException(400, "Dieses Schlagwort gibt es schon")
    if "color" in ch:
        conn.execute("UPDATE tags SET color = ? WHERE id = ?", (check_color(data.color, allow_empty=True), tag_id))
    audit(conn, admin["id"], "tag_updated", f"tag:{tag_id}", str(ch))
    return list_tags(admin, conn)


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, admin=Depends(admin_user), conn: sqlite3.Connection = Depends(get_db)):
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    audit(conn, admin["id"], "tag_deleted", f"tag:{tag_id}", "")
    return list_tags(admin, conn)


@router.get("/search")
def search(q: str = Query("", max_length=100), tag: Optional[int] = None, shelf_id: Optional[int] = None,
           limit: int = Query(50, ge=1, le=200), inbox_hits: bool = True,
           user=Depends(current_user), conn: sqlite3.Connection = Depends(get_db)):
    """Suche über Titel, Dateiname, Notiz, Schlagworte und den Volltext der Dokumente – nur in sichtbaren Regalen und
    erlaubten Freigaben. Treffer im Text kommen mit Ausschnitt (Suchwort zwischen \\x02 und \\x03).
    Dazu Treffer im Eingang (Dateiname oder Text), wenn der Benutzer den Eingang sehen darf."""
    from .. import fulltext, inbox
    access = shares.user_access(conn, user)
    cond, cargs = regal.visible_doc_filter(access)
    words = fulltext.words_of(q)
    if not words and not tag:
        return {"results": [], "inbox": [], "total": 0}
    tag_sql = " AND EXISTS (SELECT 1 FROM document_tags dt WHERE dt.document_id = d.id AND dt.tag_id = ?)" if tag else ""
    tag_args = [tag] if tag else []
    # 1. Titel, Dateiname, Notiz, Schlagworte
    sql, args = regal.DOC_SELECT + " WHERE " + cond + tag_sql, list(cargs) + tag_args
    for w in words:
        sql += (" AND (d.title LIKE ? OR f.relpath LIKE ? OR d.notes LIKE ? OR EXISTS (SELECT 1 FROM document_tags dt "
                "JOIN tags t ON t.id = dt.tag_id WHERE dt.document_id = d.id AND t.name LIKE ?))")
        args += [f"%{w}%"] * 4
    meta = conn.execute(sql + " ORDER BY COALESCE(d.updated_at, d.created_at) DESC LIMIT 500", args).fetchall()
    # 2. Volltext
    snippets = fulltext.search_files(conn, words) if words else {}
    text_rows = []
    if snippets:
        ids = list(snippets)[:500]
        rows = conn.execute(regal.DOC_SELECT + " WHERE " + cond + tag_sql + f" AND d.file_id IN ({','.join('?' * len(ids))})",
                            list(cargs) + tag_args + ids).fetchall()
        order = {fid: i for i, fid in enumerate(ids)}
        text_rows = sorted(rows, key=lambda r: order.get(r["file_id"], 0))
    seen, merged = set(), []
    for r in list(meta) + text_rows:
        if r["id"] not in seen:
            seen.add(r["id"])
            merged.append(r)
    tags = regal.doc_tags(conn, [r["id"] for r in merged[:limit * 3]])
    out = []
    for r in merged:
        places = regal.doc_places(conn, user, r["id"])
        if shelf_id:
            places = [p for p in places if p["shelf_id"] == shelf_id]
        if not places:
            continue
        hit = {**regal.doc_out(conn, r, tags), "places": places, "snippet": snippets.get(r["file_id"], "")}
        out.append(hit)
        if len(out) >= limit:
            break
    # 3. Eingang
    in_hits = []
    if inbox_hits and words and not tag and not shelf_id:
        text_in = fulltext.search_inbox(conn, words)
        for it in inbox.items(conn, user):
            key = (it["share"].lower(), it["path"])
            name_hit = all(w.lower() in it["name"].lower() for w in words)
            if key in text_in or name_hit:
                in_hits.append({**it, "snippet": text_in.get(key, "")})
            if len(in_hits) >= 20:
                break
    return {"results": out, "inbox": in_hits, "total": len(out)}
