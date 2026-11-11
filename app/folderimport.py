"""NAS-Ordner als Regal-Ordner übernehmen – und verknüpft halten.

- Der gewählte NAS-Ordner wird ein Regal-Ordner (Titel = Ordnername).
- Jeder Unterordner der ersten Ebene wird ein Register; Dateien direkt im Ordner landen im Register „Allgemein“.
  Dateien in tieferen Unterordnern kommen in das Register ihres Unterordners der ersten Ebene.
- Jede Datei wird ein Dokument (die Datei bleibt, wo sie ist).
- Verknüpft: Beim Abgleich (nachts und per Knopf) kommen neue Dateien und neue Unterordner dazu. Entfernte Dateien
  behandelt die normale Dateiprüfung (fehlt / wiedergefunden). Von Hand umsortierte Dokumente bleiben, wo sie sind.
- Rechte: Abgeglichen wird mit den Rechten der Person, die den Ordner übernommen hat (Regal bearbeiten + DSM-Leserecht).
"""
import os
import sqlite3
from typing import Dict, Optional

from fastapi import HTTPException

from . import regal, shares
from .db import audit
from .timeutil import now_utc_str

MAX_PER_SYNC = 5000
ROOT_TAB = "Allgemein"


def _walk(root, start_rel: str):
    """(Register-Name oder None für die oberste Ebene, relativer Pfad) für alle Dateien."""
    base = root / start_rel if start_rel else root
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(d for d in dirnames if not shares.hidden(d))
        rel_dir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        tab = rel_dir.split("/", 1)[0] if rel_dir else None
        for name in sorted(filenames):
            if shares.hidden(name):
                continue
            inner = f"{rel_dir}/{name}" if rel_dir else name
            yield tab, (f"{start_rel}/{inner}" if start_rel else inner)


def sync(conn: sqlite3.Connection, binder_id: int, user) -> Dict[str, int]:
    x = conn.execute("SELECT * FROM binders WHERE id = ?", (binder_id,)).fetchone()
    if not x or not x["source_share"]:
        raise HTTPException(400, "Dieser Ordner ist mit keinem NAS-Ordner verknüpft")
    share = shares.allowed(conn, user, x["source_share"], x["source_path"] or "")
    if not share:
        raise HTTPException(403, f"Kein Zugriff (mehr) auf „{x['source_share']}/{x['source_path']}“")
    try:
        root = shares.resolve(conn, share, "")
        base = shares.resolve(conn, share, x["source_path"] or "")
    except shares.ShareError as e:
        raise HTTPException(e.status, str(e))
    if not base.is_dir():
        raise HTTPException(404, "Den NAS-Ordner gibt es nicht mehr")
    tabs = {(r["source_dir"] or "").lower(): r["id"] for r in conn.execute(
        "SELECT id, source_dir FROM tabs WHERE binder_id = ? AND source_dir IS NOT NULL", (binder_id,))}
    in_binder = {r["file_id"] for r in conn.execute(
        "SELECT d.file_id FROM filings fi JOIN tabs t ON t.id = fi.tab_id JOIN documents d ON d.id = fi.document_id "
        "WHERE t.binder_id = ?", (binder_id,))}
    known = {r["relpath"]: r["id"] for r in conn.execute("SELECT id, relpath FROM files WHERE share = ?", (share,))}

    def tab_for(name: Optional[str]) -> int:
        key = (name or "").lower()
        if key in tabs:
            return tabs[key]
        sort = conn.execute("SELECT COALESCE(MAX(sort), -1) + 1 FROM tabs WHERE binder_id = ?", (binder_id,)).fetchone()[0]
        tid = conn.execute("INSERT INTO tabs(binder_id, name, sort, source_dir) VALUES (?, ?, ?, ?)",
                           (binder_id, (name or ROOT_TAB)[:40], sort if name else -1, name or "")).lastrowid
        tabs[key] = tid
        return tid

    added = new_tabs = 0
    before_tabs = len(tabs)
    truncated = False
    for tab_name, rel in _walk(root, shares.clean_relpath(x["source_path"] or "")):
        fid = known.get(rel)
        if fid in in_binder:
            continue
        if added >= MAX_PER_SYNC:
            truncated = True
            break
        try:
            f = regal.get_or_create_file(conn, share, rel, hash_now=False)  # Fingerabdruck später im Hintergrund
        except (HTTPException, shares.ShareError, OSError):
            continue
        if f["id"] in in_binder:
            continue
        doc = conn.execute("SELECT id FROM documents WHERE file_id = ?", (f["id"],)).fetchone()
        doc_id = doc["id"] if doc else conn.execute(
            "INSERT INTO documents(file_id, title, created_by, created_at) VALUES (?, ?, ?, ?)",
            (f["id"], regal.title_from_name(rel.rsplit("/", 1)[-1]), user["id"], now_utc_str())).lastrowid
        tid = tab_for(tab_name)
        conn.execute("INSERT OR IGNORE INTO filings(document_id, tab_id, sort, filed_at) VALUES (?, ?, 0, ?)", (doc_id, tid, now_utc_str()))
        in_binder.add(f["id"])
        added += 1
    new_tabs = len(tabs) - before_tabs
    # Register „Allgemein“ (falls angelegt) nach vorn, dann alphabetisch
    for i, r in enumerate(conn.execute("SELECT id FROM tabs WHERE binder_id = ? ORDER BY CASE WHEN source_dir = '' THEN 0 ELSE 1 END, "
                                       "source_dir IS NULL, name COLLATE NOCASE", (binder_id,)).fetchall()):
        conn.execute("UPDATE tabs SET sort = ? WHERE id = ?", (i, r["id"]))
    conn.execute("UPDATE binders SET source_synced_at = ? WHERE id = ?", (now_utc_str(), binder_id))
    if added:
        audit(conn, user["id"], "binder_synced", f"binder:{binder_id}", f"{added} Dokument(e) aus {share}/{x['source_path']} übernommen")
        from . import fulltext
        fulltext.kick()
    return {"added": added, "new_tabs": new_tabs, "truncated": truncated}


def sync_all(conn: sqlite3.Connection) -> Dict[int, dict]:
    """Nachts: alle verknüpften Ordner mit den Rechten der Person abgleichen, die sie übernommen hat."""
    out = {}
    for x in conn.execute("SELECT id, source_user_id FROM binders WHERE source_share IS NOT NULL AND source_sync = 1").fetchall():
        u = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1", (x["source_user_id"],)).fetchone()
        if not u:
            continue
        try:
            regal.need_shelf(conn, u, regal.shelf_of(conn, "binder", x["id"]), edit=True)
            out[x["id"]] = sync(conn, x["id"], u)
        except HTTPException as e:
            out[x["id"]] = {"error": str(e.detail)}
    return out
