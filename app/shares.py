"""Freigegebene Ordner der NAS: finden, sicher auflösen, Rechte pro Benutzer.

Die Dateien bleiben in den Freigaben – das Regal speichert nur „Freigabe + relativer Pfad“.

Rechte (wie in DSM):
- DSM-Benutzer (Single Sign-on im DSM-Fenster oder Login mit DSM-Passwort): Die App übernimmt bei jeder
  Anmeldung, welche Freigaben das DSM-Konto lesen bzw. schreiben darf (SYNO.FileStation.List/list_share).
- Eigene Konten der App (ohne DSM): Der Admin hakt die Freigaben pro Benutzer an.
- Eigene Konten mit Vollzugriff: alle im Regal eingeschalteten Freigaben.
Grundsätzlich zählen nur Freigaben, die der Admin unter Verwaltung → Freigaben eingeschaltet hat und auf die
der Paketbenutzer (ordnerregal) zugreifen darf.
"""
import hashlib
import mimetypes
import os
import re
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

from .timeutil import now_utc_str

# Ordner, die DSM selbst anlegt und die nie ins Regal gehören
HIDDEN_PREFIXES = ("@", "#", ".")
HIDDEN_NAMES = {"@eaDir", "#recycle", "#snapshot", "@tmp", "@sharebin", "lost+found", ".DS_Store", "Thumbs.db", "desktop.ini"}
# „homes“ enthält die persönlichen Ordner aller Benutzer. DSM regelt dort den Zugriff pro Unterordner –
# das kann das Regal (Rechte pro Freigabe) nicht abbilden, deshalb ist „homes“ ausgeschlossen.
EXCLUDED_SHARES = {"homes", "home", "web_packages", "docker", "ActiveBackupforBusiness", "NetBackup", "surveillance"}
SHARE_NAME_RE = re.compile(r"^[^/\\\x00-\x1f]{1,64}$")
HASH_LIMIT = 1024 * 1024 * 1024  # Fingerabdruck bis 1 GB – größere Dateien werden über Name + Größe wiedergefunden


class ShareError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def volume_roots() -> List[Path]:
    """/volume1, /volume2 … (für Tests über ORG_SHARE_ROOTS, mit : getrennt)."""
    env = os.environ.get("ORG_SHARE_ROOTS")
    if env is not None:
        return [Path(p) for p in env.split(":") if p.strip()]
    return sorted(p for p in Path("/").glob("volume[0-9]*") if p.is_dir())


def hidden(name: str) -> bool:
    return name in HIDDEN_NAMES or name.startswith(HIDDEN_PREFIXES)


def discover() -> Dict[str, Path]:
    """Alle freigegebenen Ordner auf den Volumes (Name → Pfad)."""
    found: Dict[str, Path] = {}
    for root in volume_roots():
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            if hidden(e.name) or not SHARE_NAME_RE.match(e.name) or e.name.lower() in {x.lower() for x in EXCLUDED_SHARES}:
                continue
            try:
                if e.is_dir(follow_symlinks=False) and e.name.lower() not in {k.lower() for k in found}:
                    found[e.name] = Path(e.path)
            except OSError:
                continue
    return found


def sync(conn: sqlite3.Connection) -> None:
    """Gefundene Freigaben in die Datenbank übernehmen (Schalter „im Regal“ bleibt erhalten)."""
    now = now_utc_str()
    for name, path in discover().items():
        conn.execute("INSERT INTO shares(name, path, enabled, seen_at, created_at) VALUES (?, ?, 0, ?, ?) "
                     "ON CONFLICT(name) DO UPDATE SET path = excluded.path, seen_at = excluded.seen_at",
                     (name, str(path), now, now))


def status(path: Path) -> Dict[str, bool]:
    """Darf der Paketbenutzer lesen/schreiben? (In DSM: Freigabe → Berechtigungen → System-interner Benutzer.)"""
    exists = path.is_dir()
    return {"exists": exists,
            "readable": exists and os.access(path, os.R_OK | os.X_OK),
            "writable": exists and os.access(path, os.W_OK | os.X_OK)}


def all_shares(conn: sqlite3.Connection) -> List[dict]:
    sync(conn)
    out = []
    for r in conn.execute("SELECT * FROM shares ORDER BY name COLLATE NOCASE"):
        st = status(Path(r["path"]))
        files = conn.execute("SELECT COUNT(*) FROM files WHERE share = ?", (r["name"],)).fetchone()[0]
        out.append({"name": r["name"], "path": r["path"], "enabled": bool(r["enabled"]), "files": files, **st})
    return out


def enabled_shares(conn: sqlite3.Connection) -> Dict[str, Path]:
    return {r["name"]: Path(r["path"]) for r in conn.execute("SELECT name, path FROM shares WHERE enabled = 1")}


# --- Rechte -------------------------------------------------------------------

def store_dsm_access(conn: sqlite3.Connection, user_id: int, shares: Iterable[Tuple[str, bool]]) -> None:
    """Freigaben des DSM-Kontos übernehmen (ersetzt den bisherigen Stand)."""
    conn.execute("DELETE FROM share_access WHERE user_id = ? AND source = 'dsm'", (user_id,))
    for name, can_write in shares:
        if SHARE_NAME_RE.match(name or ""):
            conn.execute("INSERT OR REPLACE INTO share_access(user_id, share, can_write, source) VALUES (?, ?, ?, 'dsm')",
                         (user_id, name, int(bool(can_write))))
    conn.execute("UPDATE users SET dsm_synced_at = ? WHERE id = ?", (now_utc_str(), user_id))


def user_access(conn: sqlite3.Connection, user) -> Dict[str, bool]:
    """Freigabe → darf schreiben – nur eingeschaltete Freigaben, nach den Regeln oben."""
    enabled = {r["name"].lower(): r["name"] for r in conn.execute("SELECT name FROM shares WHERE enabled = 1")}
    if user["dsm_synced_at"]:
        rows = conn.execute("SELECT share, can_write FROM share_access WHERE user_id = ? AND source = 'dsm'", (user["id"],))
    elif user["is_admin"]:
        return {name: True for name in enabled.values()}
    else:
        rows = conn.execute("SELECT share, can_write FROM share_access WHERE user_id = ? AND source = 'manual'", (user["id"],))
    return {enabled[r["share"].lower()]: bool(r["can_write"]) for r in rows if r["share"].lower() in enabled}


def set_manual_access(conn: sqlite3.Connection, user_id: int, shares: Dict[str, bool]) -> None:
    conn.execute("DELETE FROM share_access WHERE user_id = ? AND source = 'manual'", (user_id,))
    for name, can_write in shares.items():
        conn.execute("INSERT INTO share_access(user_id, share, can_write, source) VALUES (?, ?, ?, 'manual')",
                     (user_id, name, int(bool(can_write))))


# --- Pfade --------------------------------------------------------------------

def clean_relpath(relpath: str) -> str:
    """Relativen Pfad prüfen und vereinheitlichen. Kein .., keine absoluten Pfade, keine DSM-Systemordner."""
    rel = (relpath or "").replace("\\", "/").strip("/")
    if "\x00" in rel or len(rel) > 2000:
        raise ShareError("Ungültiger Pfad")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    for p in parts:
        if p == ".." or hidden(p):
            raise ShareError("Ungültiger Pfad")
    return "/".join(parts)


def resolve(conn: sqlite3.Connection, share: str, relpath: str) -> Path:
    """Absoluter Pfad in einer eingeschalteten Freigabe. Symbolische Links, die hinausführen, werden abgelehnt."""
    row = conn.execute("SELECT name, path FROM shares WHERE name = ? AND enabled = 1", (share,)).fetchone()
    if not row:
        raise ShareError("Diese Freigabe ist im Regal nicht eingeschaltet", 404)
    root = Path(row["path"])
    rel = clean_relpath(relpath)
    target = root / PurePosixPath(rel) if rel else root
    try:
        real_root = root.resolve()
        real = target.resolve()
    except (OSError, RuntimeError):
        raise ShareError("Pfad nicht lesbar", 404)
    if real != real_root and real_root not in real.parents:
        raise ShareError("Ungültiger Pfad")
    return target


def list_dir(conn: sqlite3.Connection, share: str, relpath: str) -> dict:
    path = resolve(conn, share, relpath)
    if not path.is_dir():
        raise ShareError("Ordner nicht gefunden", 404)
    dirs, files = [], []
    try:
        entries = list(os.scandir(path))
    except PermissionError:
        raise ShareError("Kein Zugriff auf diesen Ordner – der Paketbenutzer „ordnerregal“ braucht in DSM Leserechte", 403)
    rel = clean_relpath(relpath)
    for e in entries:
        if hidden(e.name):
            continue
        try:
            if e.is_dir():
                dirs.append({"name": e.name, "path": f"{rel}/{e.name}".strip("/")})
            elif e.is_file():
                st = e.stat()
                files.append({"name": e.name, "path": f"{rel}/{e.name}".strip("/"), "size": st.st_size,
                              "mtime": st.st_mtime, "mime": guess_mime(e.name)})
        except OSError:
            continue
    key = lambda x: x["name"].lower()  # noqa: E731
    return {"share": share, "path": rel, "dirs": sorted(dirs, key=key), "files": sorted(files, key=key)}


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def sha256_of(path: Path) -> Optional[str]:
    try:
        if path.stat().st_size > HASH_LIMIT:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
