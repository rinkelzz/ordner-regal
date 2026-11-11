"""Volltext: Text speichern, im Hintergrund lesen, durchsuchen (mit Textausschnitt).

Der Index liegt in der Datenbank (file_text bzw. inbox_text). Mit FTS5 sucht SQLite selbst (schnell, mit
Wortanfängen und ohne Akzente), sonst sucht die App per LIKE im gespeicherten Text.

Gelesen wird in einem eigenen Hintergrund-Thread – nach dem Ablegen sofort, sonst alle paar Minuten. Er liest
neue und geänderte Dateien im Regal und die Dateien in den Eingängen.
"""
import re
import sqlite3
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import shares, textextract
from .config import settings

HL_START, HL_END = "\x02", "\x03"   # Markierung im Ausschnitt; die Oberfläche macht <mark> daraus
SNIPPET_CHARS = 90
STATUS_TEXT = {"ok": "Volltext gelesen", "empty": "kein Text (Bild-Scan ohne Texterkennung)", "unsupported": "Dateiart ohne Volltext",
               "toolarge": "zu groß für den Volltext", "error": "Text nicht lesbar", None: "Volltext wird gelesen …"}


def has_fts(conn: sqlite3.Connection, table: str = "file_fts") -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone())


# --- speichern ----------------------------------------------------------------

def store_file_text(conn: sqlite3.Connection, file_id: int, status: str, text: str, mtime: float) -> None:
    conn.execute("UPDATE files SET text_status = ?, text_mtime = ? WHERE id = ?", (status, mtime, file_id))
    conn.execute("DELETE FROM file_text WHERE file_id = ?", (file_id,))
    fts = has_fts(conn)
    if fts:
        try:
            conn.execute("DELETE FROM file_fts WHERE rowid = ?", (file_id,))
        except sqlite3.OperationalError:
            fts = False
    if status == "ok" and text:
        conn.execute("INSERT INTO file_text(file_id, body) VALUES (?, ?)", (file_id, text))
        if fts:
            conn.execute("INSERT INTO file_fts(rowid, body) VALUES (?, ?)", (file_id, text))


def store_inbox_text(conn: sqlite3.Connection, share: str, relpath: str, size: int, mtime: float, status: str, text: str) -> None:
    old = conn.execute("SELECT id FROM inbox_text WHERE share = ? AND relpath = ?", (share, relpath)).fetchone()
    if old:
        conn.execute("DELETE FROM inbox_text WHERE id = ?", (old["id"],))  # Trigger räumt inbox_fts auf
    rid = conn.execute("INSERT INTO inbox_text(share, relpath, size, mtime, status, body) VALUES (?, ?, ?, ?, ?, ?)",
                       (share, relpath, size, mtime, status, text if status == "ok" else "")).lastrowid
    if status == "ok" and text and has_fts(conn, "inbox_fts"):
        try:
            conn.execute("INSERT INTO inbox_fts(rowid, body) VALUES (?, ?)", (rid, text))
        except sqlite3.OperationalError:
            pass


def take_over_inbox_text(conn: sqlite3.Connection, share: str, relpath: str, size: int, file_id: int, mtime: float) -> bool:
    """Beim Einsortieren: schon gelesenen Text aus dem Eingang übernehmen (die Datei wurde nur verschoben)."""
    row = conn.execute("SELECT * FROM inbox_text WHERE share = ? AND relpath = ? AND size = ?", (share, relpath, size)).fetchone()
    if not row:
        return False
    store_file_text(conn, file_id, row["status"], row["body"], mtime)
    conn.execute("DELETE FROM inbox_text WHERE id = ?", (row["id"],))
    return True


# --- lesen ----------------------------------------------------------------------

def index_pending(conn: sqlite3.Connection, max_files: int = 200) -> int:
    """Neue und geänderte Dateien im Regal lesen. Liefert die Zahl gelesener Dateien."""
    enabled = shares.enabled_shares(conn)
    roots = {n.lower(): p for n, p in enabled.items()}
    done = 0
    rows = conn.execute("SELECT * FROM files WHERE missing_since IS NULL ORDER BY id").fetchall()
    for f in rows:
        if done >= max_files:
            break
        root = roots.get(f["share"].lower())
        if root is None:
            continue
        path = root / f["relpath"]
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if f["text_status"] is not None and f["text_mtime"] == mtime:
            continue  # unverändert
        status, text = textextract.extract(path)
        store_file_text(conn, f["id"], status, text, mtime)
        if f["mtime"] != mtime:  # Datei wurde geändert: Größe und Fingerabdruck auffrischen
            conn.execute("UPDATE files SET mtime = ?, size = ?, sha256 = ? WHERE id = ?",
                         (mtime, path.stat().st_size, shares.sha256_of(path), f["id"]))
        done += 1
    return done


def index_inbox(conn: sqlite3.Connection, max_files: int = 200) -> int:
    """Dateien in allen aktiven Eingängen lesen; Einträge für verschwundene Dateien entfernen."""
    from . import inbox
    seen = set()
    done = 0
    for src in inbox.sources(conn):
        if not src["active"]:
            continue
        try:
            listing = shares.list_dir(conn, src["share"], src["path"])
            root = shares.resolve(conn, src["share"], "")
        except shares.ShareError:
            continue
        for f in listing["files"]:
            key = (src["share"].lower(), f["path"])
            seen.add(key)
            row = conn.execute("SELECT size, mtime FROM inbox_text WHERE share = ? AND relpath = ?", (src["share"], f["path"])).fetchone()
            if row and row["size"] == f["size"] and row["mtime"] == f["mtime"]:
                continue
            if done >= max_files:
                continue
            status, text = textextract.extract(root / f["path"])
            store_inbox_text(conn, src["share"], f["path"], f["size"], f["mtime"], status, text)
            done += 1
    for r in conn.execute("SELECT id, share, relpath FROM inbox_text").fetchall():
        if (r["share"].lower(), r["relpath"]) not in seen:
            conn.execute("DELETE FROM inbox_text WHERE id = ?", (r["id"],))
    return done


def run_all(conn: sqlite3.Connection) -> Dict[str, int]:
    return {"files": index_pending(conn), "inbox": index_inbox(conn)}


def reindex_all(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE files SET text_status = NULL, text_mtime = NULL")
    conn.execute("DELETE FROM inbox_text")


def stats(conn: sqlite3.Connection) -> dict:
    by = {r["s"]: r["n"] for r in conn.execute("SELECT text_status AS s, COUNT(*) AS n FROM files GROUP BY text_status")}
    return {"fts": has_fts(conn), "ok": by.get("ok", 0), "empty": by.get("empty", 0), "pending": by.get(None, 0),
            "other": sum(v for k, v in by.items() if k not in ("ok", "empty", None)),
            "inbox": conn.execute("SELECT COUNT(*) FROM inbox_text WHERE status = 'ok'").fetchone()[0]}


# --- Hintergrund ----------------------------------------------------------------

_wake = threading.Event()
_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def kick() -> None:
    """Nach dem Ablegen: gleich lesen (wenn der Hintergrund-Thread läuft)."""
    _wake.set()


def _loop() -> None:
    from .db import connect
    while not _stop.is_set():
        try:
            conn = connect()
            try:
                while not _stop.is_set():
                    r = run_all(conn)
                    if not r["files"] and not r["inbox"]:
                        break
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001
            print(f"[ordnerregal] Volltext FEHLER: {e}", flush=True)
        _wake.wait(300)
        _wake.clear()


def start() -> None:
    global _thread
    if not settings.scheduler or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ordnerregal-volltext", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()


# --- suchen ---------------------------------------------------------------------

def _fts_query(words: List[str]) -> str:
    return " ".join('"' + w.replace('"', '""') + '"*' for w in words)


def _snippet(text: str, words: List[str]) -> str:
    low = text.lower()
    pos = min((low.find(w.lower()) for w in words if low.find(w.lower()) >= 0), default=0)
    start = max(0, pos - SNIPPET_CHARS // 2)
    part = text[start:start + SNIPPET_CHARS * 2].replace("\n", " ")
    for w in sorted(words, key=len, reverse=True):
        part = re.sub("(" + re.escape(w) + ")", HL_START + r"\1" + HL_END, part, flags=re.I)
    return ("…" if start else "") + part + ("…" if start + SNIPPET_CHARS * 2 < len(text) else "")


def search_files(conn: sqlite3.Connection, words: List[str], limit: int = 500) -> Dict[int, str]:
    """file_id → Textausschnitt für alle Dateien, deren Text alle Wörter enthält."""
    if not words:
        return {}
    if has_fts(conn):
        try:
            rows = conn.execute(f"SELECT rowid AS id, snippet(file_fts, 0, '{HL_START}', '{HL_END}', '…', 14) AS snip "
                                "FROM file_fts WHERE file_fts MATCH ? ORDER BY rank LIMIT ?", (_fts_query(words), limit)).fetchall()
            return {r["id"]: r["snip"].replace("\n", " ") for r in rows}
        except sqlite3.OperationalError:
            pass
    sql = "SELECT file_id AS id, body FROM file_text WHERE " + " AND ".join(["body LIKE ?"] * len(words)) + " LIMIT ?"
    rows = conn.execute(sql, [f"%{w}%" for w in words] + [limit]).fetchall()
    return {r["id"]: _snippet(r["body"], words) for r in rows}


def search_inbox(conn: sqlite3.Connection, words: List[str], limit: int = 100) -> Dict[Tuple[str, str], str]:
    if not words:
        return {}
    if has_fts(conn, "inbox_fts"):
        try:
            rows = conn.execute(f"SELECT t.share, t.relpath, snippet(inbox_fts, 0, '{HL_START}', '{HL_END}', '…', 14) AS snip "
                                "FROM inbox_fts JOIN inbox_text t ON t.id = inbox_fts.rowid WHERE inbox_fts MATCH ? LIMIT ?",
                                (_fts_query(words), limit)).fetchall()
            return {(r["share"].lower(), r["relpath"]): r["snip"].replace("\n", " ") for r in rows}
        except sqlite3.OperationalError:
            pass
    sql = "SELECT share, relpath, body FROM inbox_text WHERE status = 'ok' AND " + " AND ".join(["body LIKE ?"] * len(words)) + " LIMIT ?"
    rows = conn.execute(sql, [f"%{w}%" for w in words] + [limit]).fetchall()
    return {(r["share"].lower(), r["relpath"]): _snippet(r["body"], words) for r in rows}


def words_of(q: str) -> List[str]:
    """Suchwörter: Satzzeichen weg, höchstens 8, mindestens 2 Zeichen."""
    return [w for w in re.findall(r"[\w@.\-]+", q or "", flags=re.UNICODE) if len(w.strip(".-")) >= 2][:8]


def path_of(conn: sqlite3.Connection, share: str, relpath: str) -> Optional[Path]:
    try:
        return shares.resolve(conn, share, relpath)
    except shares.ShareError:
        return None


def iter_ids(rows: Iterable) -> List[int]:
    return [r["id"] for r in rows]
