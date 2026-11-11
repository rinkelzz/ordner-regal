"""Dateien prüfen: Gibt es sie noch? Verschobene oder umbenannte Dateien über den Fingerabdruck wiederfinden.

Läuft nachts (Verwaltung → Freigaben → „Jetzt prüfen“ geht auch von Hand). Gesucht wird nur in
eingeschalteten Freigaben: zuerst nach gleicher Größe, dann wird der SHA-256 verglichen.
"""
import os
import sqlite3
from pathlib import Path
from typing import Dict, List

from . import shares
from .db import audit
from .timeutil import now_utc_str

MAX_WALK = 200000  # Obergrenze Dateien pro Durchlauf (große Freigaben)


def _walk(root: Path):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not shares.hidden(d)]
        for name in filenames:
            if shares.hidden(name):
                continue
            count += 1
            if count > MAX_WALK:
                return
            yield Path(dirpath) / name


def check(conn: sqlite3.Connection, relink: bool = True) -> Dict[str, int]:
    enabled = shares.enabled_shares(conn)
    now = now_utc_str()
    missing: List[sqlite3.Row] = []
    ok = 0
    for f in conn.execute("SELECT * FROM files").fetchall():
        root = next((p for n, p in enabled.items() if n.lower() == f["share"].lower()), None)
        if root is None:
            continue  # Freigabe ausgeschaltet – nichts ändern
        path = root / f["relpath"]
        if path.is_file():
            ok += 1
            conn.execute("UPDATE files SET missing_since = NULL, checked_at = ? WHERE id = ?", (now, f["id"]))
        else:
            if not f["missing_since"]:
                conn.execute("UPDATE files SET missing_since = ?, checked_at = ? WHERE id = ?", (now, now, f["id"]))
            missing.append(f)
    found = 0
    if relink and missing:
        found = _relink(conn, missing, enabled)
    return {"ok": ok, "missing": len(missing) - found, "relinked": found}


def _relink(conn: sqlite3.Connection, missing: List[sqlite3.Row], enabled: Dict[str, Path]) -> int:
    by_size: Dict[int, List[sqlite3.Row]] = {}
    for f in missing:
        by_size.setdefault(f["size"], []).append(f)
    known = {(r["share"].lower(), r["relpath"]) for r in conn.execute("SELECT share, relpath FROM files")}
    found = 0
    for share, root in enabled.items():
        for path in _walk(root):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            cands = by_size.get(size)
            if not cands:
                continue
            rel = path.relative_to(root).as_posix()
            if (share.lower(), rel) in known:
                continue
            digest = None
            for f in list(cands):
                if f["sha256"]:
                    digest = digest or shares.sha256_of(path)
                    match = digest == f["sha256"]
                else:  # zu groß für einen Fingerabdruck: gleicher Name und gleiche Größe
                    match = path.name == f["relpath"].rsplit("/", 1)[-1]
                if not match:
                    continue
                conn.execute("UPDATE files SET share = ?, relpath = ?, mtime = ?, missing_since = NULL, checked_at = ? WHERE id = ?",
                             (share, rel, path.stat().st_mtime, now_utc_str(), f["id"]))
                doc = conn.execute("SELECT id FROM documents WHERE file_id = ?", (f["id"],)).fetchone()
                audit(conn, None, "doc_relinked", f"doc:{doc['id']}" if doc else "", f"{f['share']}/{f['relpath']} → {share}/{rel}")
                known.add((share.lower(), rel))
                cands.remove(f)
                found += 1
                break
            if not any(by_size.values()):
                return found
    return found
