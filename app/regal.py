"""Regal-Logik: Rechte an Regalen, Ordnerrücken, Dokumente anlegen.

Rechte an einem Regal:
- Vollzugriff (Admin): alles
- sonst Eintrag in shelf_access (ansehen oder bearbeiten) oder „alle dürfen hineinsehen“ (all_read)
Zusätzlich zählt bei jedem Dokument die DSM-Freigabe: Wer die Freigabe nicht lesen darf, sieht das Dokument nicht.
"""
import re
import sqlite3
from typing import Dict, List, Optional

from fastapi import HTTPException

from . import shares
from .timeutil import now_utc_str

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_TAB = "Allgemein"


def check_color(value: Optional[str], allow_empty: bool = False) -> str:
    v = (value or "").strip()
    if allow_empty and not v:
        return ""
    if not COLOR_RE.match(v):
        raise HTTPException(400, "Farbe bitte als #RRGGBB angeben")
    return v.lower()


def shelf_right(conn: sqlite3.Connection, user, shelf_id: int) -> str:
    """'' (kein Zugriff), 'read' oder 'edit'."""
    shelf = conn.execute("SELECT all_read FROM shelves WHERE id = ?", (shelf_id,)).fetchone()
    if not shelf:
        return ""
    if user["is_admin"]:
        return "edit"
    row = conn.execute("SELECT can_edit FROM shelf_access WHERE shelf_id = ? AND user_id = ?", (shelf_id, user["id"])).fetchone()
    if row:
        return "edit" if row["can_edit"] else "read"
    return "read" if shelf["all_read"] else ""


def need_shelf(conn: sqlite3.Connection, user, shelf_id: int, edit: bool = False) -> str:
    right = shelf_right(conn, user, shelf_id)
    if not right:
        raise HTTPException(404, "Regal nicht gefunden")
    if edit and right != "edit":
        raise HTTPException(403, "Du darfst dieses Regal nur ansehen")
    return right


def shelf_of(conn: sqlite3.Connection, kind: str, obj_id: int) -> int:
    sql = {
        "board": "SELECT shelf_id FROM boards WHERE id = ?",
        "binder": "SELECT b.shelf_id FROM binders x JOIN boards b ON b.id = x.board_id WHERE x.id = ?",
        "tab": "SELECT b.shelf_id FROM tabs t JOIN binders x ON x.id = t.binder_id JOIN boards b ON b.id = x.board_id WHERE t.id = ?",
    }[kind]
    row = conn.execute(sql, (obj_id,)).fetchone()
    if not row:
        raise HTTPException(404, {"board": "Fach", "binder": "Ordner", "tab": "Register"}[kind] + " nicht gefunden")
    return row[0]


def visible_shelves(conn: sqlite3.Connection, user) -> List[dict]:
    out = []
    for s in conn.execute("SELECT * FROM shelves ORDER BY sort, id"):
        right = shelf_right(conn, user, s["id"])
        if right:
            n = conn.execute("SELECT COUNT(*) FROM binders x JOIN boards b ON b.id = x.board_id WHERE b.shelf_id = ?",
                             (s["id"],)).fetchone()[0]
            out.append({"id": s["id"], "name": s["name"], "all_read": bool(s["all_read"]), "right": right, "binders": n})
    return out


def visible_doc_filter(access: Dict[str, bool]) -> tuple:
    """SQL-Bedingung „Datei liegt in einer Freigabe, die der Benutzer lesen darf“ (für Tabelle f = files)."""
    names = [n.lower() for n in access]
    if not names:
        return "0", []
    return f"lower(f.share) IN ({','.join('?' * len(names))})", names


def binder_counts(conn: sqlite3.Connection, binder_ids: List[int], user) -> Dict[int, dict]:
    """Pro Ordner: sichtbare Dokumente, ausgeblendete (keine DSM-Rechte), fehlende Dateien."""
    if not binder_ids:
        return {}
    cond, args = shares.doc_filter(conn, user)
    q = ",".join("?" * len(binder_ids))
    rows = conn.execute(f"""
        SELECT t.binder_id AS bid,
               COUNT(DISTINCT CASE WHEN {cond} THEN d.id END) AS visible,
               COUNT(DISTINCT CASE WHEN NOT ({cond}) THEN d.id END) AS hidden,
               COUNT(DISTINCT CASE WHEN ({cond}) AND f.missing_since IS NOT NULL THEN d.id END) AS missing
        FROM tabs t JOIN filings fi ON fi.tab_id = t.id JOIN documents d ON d.id = fi.document_id JOIN files f ON f.id = d.file_id
        WHERE t.binder_id IN ({q}) GROUP BY t.binder_id""", args + args + args + list(binder_ids)).fetchall()
    out = {bid: {"docs": 0, "hidden": 0, "missing": 0} for bid in binder_ids}
    for r in rows:
        out[r["bid"]] = {"docs": r["visible"], "hidden": r["hidden"], "missing": r["missing"]}
    return out


def shelf_detail(conn: sqlite3.Connection, user, shelf_id: int) -> dict:
    right = need_shelf(conn, user, shelf_id)
    s = conn.execute("SELECT * FROM shelves WHERE id = ?", (shelf_id,)).fetchone()
    boards = [dict(b) for b in conn.execute("SELECT id, name, sort FROM boards WHERE shelf_id = ? ORDER BY sort, id", (shelf_id,))]
    binders = conn.execute("SELECT x.* FROM binders x JOIN boards b ON b.id = x.board_id WHERE b.shelf_id = ? ORDER BY x.sort, x.id",
                           (shelf_id,)).fetchall()
    counts = binder_counts(conn, [x["id"] for x in binders], user)
    cond, args = shares.doc_filter(conn, user)
    dues = binder_deadlines(conn, [x["id"] for x in binders], cond, args)
    for b in boards:
        b["binders"] = [{"id": x["id"], "title": x["title"], "color": x["color"], "width": x["width"], "notes": x["notes"],
                         "next_due": dues.get(x["id"]), **counts.get(x["id"], {})} for x in binders if x["board_id"] == b["id"]]
    return {"id": s["id"], "name": s["name"], "all_read": bool(s["all_read"]), "right": right, "boards": boards}


def doc_out(conn: sqlite3.Connection, row, tags: Optional[Dict[int, list]] = None) -> dict:
    t = tags.get(row["id"], []) if tags is not None else doc_tags(conn, [row["id"]]).get(row["id"], [])
    name = row["relpath"].rsplit("/", 1)[-1]
    return {"id": row["id"], "title": row["title"], "doc_date": row["doc_date"], "notes": row["notes"],
            "share": row["share"], "path": row["relpath"], "filename": name, "size": row["size"], "mime": row["mime"],
            "missing": bool(row["missing_since"]), "tags": t, "updated_at": row["updated_at"] or row["created_at"],
            "text_status": row["text_status"] if "text_status" in row.keys() else None}


def doc_tags(conn: sqlite3.Connection, doc_ids: List[int]) -> Dict[int, list]:
    if not doc_ids:
        return {}
    out: Dict[int, list] = {}
    q = ",".join("?" * len(doc_ids))
    for r in conn.execute(f"SELECT dt.document_id, t.id, t.name, t.color FROM document_tags dt JOIN tags t ON t.id = dt.tag_id "
                          f"WHERE dt.document_id IN ({q}) ORDER BY t.name COLLATE NOCASE", doc_ids):
        out.setdefault(r["document_id"], []).append({"id": r["id"], "name": r["name"], "color": r["color"]})
    return out


DOC_SELECT = ("SELECT d.*, f.share, f.relpath, f.size, f.mime, f.missing_since, f.text_status FROM documents d JOIN files f ON f.id = d.file_id")


def next_deadlines(conn: sqlite3.Connection, doc_ids: List[int]) -> Dict[int, dict]:
    """Pro Dokument die nächste offene Frist."""
    if not doc_ids:
        return {}
    from . import deadlines as dl
    out: Dict[int, dict] = {}
    q = ",".join("?" * len(doc_ids))
    for r in conn.execute(f"SELECT * FROM deadlines WHERE done_at IS NULL AND document_id IN ({q}) ORDER BY due_on", doc_ids):
        if r["document_id"] not in out:
            out[r["document_id"]] = {"id": r["id"], "due_on": r["due_on"], "title": dl.label_of(r)}
    return out


def binder_deadlines(conn: sqlite3.Connection, binder_ids: List[int], cond: str, args: list) -> Dict[int, str]:
    """Pro Ordner das früheste offene Fristdatum sichtbarer Dokumente."""
    if not binder_ids:
        return {}
    q = ",".join("?" * len(binder_ids))
    rows = conn.execute(f"""SELECT t.binder_id AS bid, MIN(dl.due_on) AS due FROM tabs t JOIN filings fi ON fi.tab_id = t.id
        JOIN documents d ON d.id = fi.document_id JOIN files f ON f.id = d.file_id JOIN deadlines dl ON dl.document_id = d.id
        WHERE dl.done_at IS NULL AND t.binder_id IN ({q}) AND {cond} GROUP BY t.binder_id""", list(binder_ids) + args).fetchall()
    return {r["bid"]: r["due"] for r in rows}


def binder_detail(conn: sqlite3.Connection, user, binder_id: int) -> dict:
    shelf_id = shelf_of(conn, "binder", binder_id)
    right = need_shelf(conn, user, shelf_id)
    x = conn.execute("SELECT x.*, b.name AS board_name FROM binders x JOIN boards b ON b.id = x.board_id WHERE x.id = ?",
                     (binder_id,)).fetchone()
    shelf = conn.execute("SELECT id, name FROM shelves WHERE id = ?", (shelf_id,)).fetchone()
    cond, args = shares.doc_filter(conn, user)
    tabs = []
    for t in conn.execute("SELECT * FROM tabs WHERE binder_id = ? ORDER BY sort, id", (binder_id,)):
        rows = conn.execute(DOC_SELECT + " JOIN filings fi ON fi.document_id = d.id WHERE fi.tab_id = ? AND " + cond +
                            " ORDER BY fi.sort, COALESCE(d.doc_date, '') DESC, d.id DESC", [t["id"]] + args).fetchall()
        hidden = conn.execute("SELECT COUNT(*) FROM filings fi JOIN documents d ON d.id = fi.document_id JOIN files f ON f.id = d.file_id "
                              "WHERE fi.tab_id = ? AND NOT (" + cond + ")", [t["id"]] + args).fetchone()[0]
        tags = doc_tags(conn, [r["id"] for r in rows])
        nd = next_deadlines(conn, [r["id"] for r in rows])
        tabs.append({"id": t["id"], "name": t["name"], "color": t["color"], "hidden": hidden,
                     "target": {"share": t["target_share"], "path": t["target_path"] or ""} if t["target_share"] else None,
                     "docs": [{**doc_out(conn, r, tags), "next_deadline": nd.get(r["id"])} for r in rows]})
    return {"id": x["id"], "title": x["title"], "color": x["color"], "width": x["width"], "notes": x["notes"],
            "board_id": x["board_id"], "board_name": x["board_name"], "shelf": {"id": shelf["id"], "name": shelf["name"]},
            "right": right, "tabs": tabs,
            "target": {"share": x["target_share"], "path": x["target_path"] or ""} if x["target_share"] else None}


def get_or_create_file(conn: sqlite3.Connection, share: str, relpath: str) -> sqlite3.Row:
    path = shares.resolve(conn, share, relpath)
    if not path.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    rel = shares.clean_relpath(relpath)
    share_name = conn.execute("SELECT name FROM shares WHERE name = ?", (share,)).fetchone()["name"]
    row = conn.execute("SELECT * FROM files WHERE share = ? AND relpath = ?", (share_name, rel)).fetchone()
    st = path.stat()
    if row:
        if row["size"] != st.st_size or row["mtime"] != st.st_mtime or row["missing_since"]:
            conn.execute("UPDATE files SET size = ?, mtime = ?, sha256 = ?, missing_since = NULL, checked_at = ? WHERE id = ?",
                         (st.st_size, st.st_mtime, shares.sha256_of(path), now_utc_str(), row["id"]))
        return conn.execute("SELECT * FROM files WHERE id = ?", (row["id"],)).fetchone()
    cur = conn.execute("INSERT INTO files(share, relpath, size, mtime, sha256, mime, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (share_name, rel, st.st_size, st.st_mtime, shares.sha256_of(path), shares.guess_mime(path.name), now_utc_str()))
    return conn.execute("SELECT * FROM files WHERE id = ?", (cur.lastrowid,)).fetchone()


def title_from_name(name: str) -> str:
    base = name.rsplit(".", 1)[0] if "." in name else name
    return re.sub(r"[_]+", " ", base).strip()[:200] or name[:200]


def set_tags(conn: sqlite3.Connection, doc_id: int, names: List[str]) -> None:
    conn.execute("DELETE FROM document_tags WHERE document_id = ?", (doc_id,))
    seen = set()
    for raw in names[:30]:
        name = (raw or "").strip()[:40]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        tag_id = row["id"] if row else conn.execute("INSERT INTO tags(name) VALUES (?)", (name,)).lastrowid
        conn.execute("INSERT OR IGNORE INTO document_tags(document_id, tag_id) VALUES (?, ?)", (doc_id, tag_id))


def cleanup_document(conn: sqlite3.Connection, doc_id: int) -> bool:
    """Dokument ohne Ablageort entfernen (die Datei auf der NAS bleibt unberührt). True = gelöscht."""
    if conn.execute("SELECT 1 FROM filings WHERE document_id = ?", (doc_id,)).fetchone():
        return False
    row = conn.execute("SELECT file_id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    if row:
        conn.execute("DELETE FROM files WHERE id = ? AND NOT EXISTS (SELECT 1 FROM documents WHERE file_id = ?)",
                     (row["file_id"], row["file_id"]))
    return True


def cleanup_orphans(conn: sqlite3.Connection) -> None:
    for r in conn.execute("SELECT id FROM documents d WHERE NOT EXISTS (SELECT 1 FROM filings fi WHERE fi.document_id = d.id)").fetchall():
        cleanup_document(conn, r["id"])
    conn.execute("DELETE FROM tags WHERE NOT EXISTS (SELECT 1 FROM document_tags dt WHERE dt.tag_id = tags.id)")


def doc_places(conn: sqlite3.Connection, user, doc_id: int) -> List[dict]:
    """Wo liegt das Dokument überall (nur in Regalen, die der Benutzer sehen darf)."""
    out = []
    for r in conn.execute("""SELECT t.id AS tab_id, t.name AS tab, x.id AS binder_id, x.title AS binder, x.color, s.id AS shelf_id, s.name AS shelf
                             FROM filings fi JOIN tabs t ON t.id = fi.tab_id JOIN binders x ON x.id = t.binder_id
                             JOIN boards b ON b.id = x.board_id JOIN shelves s ON s.id = b.shelf_id
                             WHERE fi.document_id = ? ORDER BY s.sort, x.title""", (doc_id,)):
        right = shelf_right(conn, user, r["shelf_id"])
        if right:
            out.append({**dict(r), "right": right})
    return out


def load_doc(conn: sqlite3.Connection, user, doc_id: int, edit: bool = False) -> sqlite3.Row:
    """Dokument laden, wenn der Benutzer es sehen darf (Regal + DSM-Freigabe)."""
    row = conn.execute(DOC_SELECT + " WHERE d.id = ?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Dokument nicht gefunden")
    places = doc_places(conn, user, doc_id)
    if not places or not shares.allowed(conn, user, row["share"], row["relpath"]):
        raise HTTPException(404, "Dokument nicht gefunden")
    if edit and not any(p["right"] == "edit" for p in places):
        raise HTTPException(403, "Du darfst dieses Dokument nur ansehen")
    return row
