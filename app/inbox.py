"""Scanner-Eingang: neue Dateien in überwachten Ordnern, Einsortieren mit Verschieben in den Zielordner.

- Eingänge trägt der Admin ein (Freigabe + Unterordner). Jeder sieht nur Eingänge, deren Freigabe er
  in DSM lesen darf.
- Im Eingang stehen Dateien, die noch nicht im Regal liegen (nur die oberste Ebene des Eingangsordners).
- Einsortieren verschiebt die Datei in den Zielordner (Register vor Ordner) – nur mit Schreibrecht in DSM
  auf Quelle und Ziel. Eine vorhandene Datei am Ziel wird nie überschrieben („Name (2).pdf“).
"""
import errno
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException

from . import shares
from .timeutil import now_utc_str

BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_ITEMS = 500


def sources(conn: sqlite3.Connection, user=None) -> List[dict]:
    """Eingänge – mit user nur die aktiven, deren Freigabe der Benutzer lesen darf."""
    access = {n.lower(): w for n, w in shares.user_access(conn, user).items()} if user is not None else None
    out = []
    for r in conn.execute("SELECT * FROM inbox_sources ORDER BY name COLLATE NOCASE"):
        if access is not None and (not r["active"] or r["share"].lower() not in access):
            continue
        out.append({"id": r["id"], "share": r["share"], "path": r["relpath"], "name": r["name"], "active": bool(r["active"])})
    return out


def items(conn: sqlite3.Connection, user, source_id: Optional[int] = None) -> List[dict]:
    known = {(r["share"].lower(), r["relpath"]) for r in conn.execute("SELECT share, relpath FROM files")}
    writable = {n.lower() for n, w in shares.user_access(conn, user).items() if w}
    out: List[dict] = []
    for src in sources(conn, user):
        if source_id and src["id"] != source_id:
            continue
        try:
            listing = shares.list_dir(conn, src["share"], src["path"])
        except shares.ShareError:
            continue
        for f in listing["files"]:
            if (src["share"].lower(), f["path"]) in known:
                continue
            out.append({**f, "share": src["share"], "source_id": src["id"], "source": src["name"],
                        "can_move": src["share"].lower() in writable})
    out.sort(key=lambda x: -x["mtime"])
    return out[:MAX_ITEMS]


def source_of(conn: sqlite3.Connection, user, share: str, relpath: str) -> dict:
    """Die Datei muss direkt in einem Eingang liegen, den der Benutzer sehen darf."""
    try:
        rel = shares.clean_relpath(relpath)
    except shares.ShareError as e:
        raise HTTPException(e.status, str(e))
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    for src in sources(conn, user):
        if src["share"].lower() == share.lower() and src["path"] == parent:
            return src
    raise HTTPException(404, "Diese Datei liegt in keinem deiner Eingänge")


# --- Zielordner ---------------------------------------------------------------

def check_target(conn: sqlite3.Connection, share: Optional[str], path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Zielordner prüfen (Freigabe eingeschaltet, Ordner vorhanden). Leer = kein Zielordner."""
    if not share:
        return None, None
    row = conn.execute("SELECT name FROM shares WHERE name = ? AND enabled = 1", (share,)).fetchone()
    if not row:
        raise HTTPException(400, f"Die Freigabe „{share}“ ist im Regal nicht eingeschaltet")
    try:
        p = shares.resolve(conn, row["name"], path or "")
    except shares.ShareError as e:
        raise HTTPException(e.status, str(e))
    if not p.is_dir():
        raise HTTPException(400, "Den Zielordner gibt es nicht")
    return row["name"], shares.clean_relpath(path or "")


def target_for_tab(conn: sqlite3.Connection, tab_id: int) -> Optional[dict]:
    r = conn.execute("SELECT t.target_share AS ts, t.target_path AS tp, x.target_share AS bs, x.target_path AS bp, "
                     "t.name AS tab, x.title AS binder FROM tabs t JOIN binders x ON x.id = t.binder_id WHERE t.id = ?", (tab_id,)).fetchone()
    if not r:
        return None
    if r["ts"]:
        return {"share": r["ts"], "path": r["tp"] or "", "from": "tab", "label": f"Register „{r['tab']}“"}
    if r["bs"]:
        return {"share": r["bs"], "path": r["bp"] or "", "from": "binder", "label": f"Ordner „{r['binder']}“"}
    return None


# --- Dateinamen ---------------------------------------------------------------

DATE_IN_NAME = re.compile(r"(20\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])")


def date_from_name(name: str, mtime: float) -> str:
    m = DATE_IN_NAME.search(name)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date().isoformat()
        except ValueError:
            pass
    return datetime.fromtimestamp(mtime).date().isoformat()


def clean_filename(name: str, original: str) -> str:
    """Neuer Dateiname: keine Pfadteile oder Sonderzeichen, Endung der Originaldatei bleibt."""
    n = BAD_NAME.sub("_", (name or "").strip()).strip(" .")
    ext = os.path.splitext(original)[1]
    if not n:
        n = original
    if ext and not n.lower().endswith(ext.lower()):
        n += ext
    if shares.hidden(n) or n in (".", ".."):
        raise HTTPException(400, "Ungültiger Dateiname")
    stem, e = os.path.splitext(n)
    return stem[:180] + e[:20]


def _free_name(folder: Path, name: str) -> Path:
    stem, ext = os.path.splitext(name)
    cand = folder / name
    i = 2
    while cand.exists() or cand.is_symlink():
        cand = folder / f"{stem} ({i}){ext}"
        i += 1
        if i > 999:
            raise HTTPException(409, "Am Ziel gibt es zu viele Dateien mit diesem Namen")
    return cand


def move_file(src: Path, folder: Path, name: str) -> Path:
    """Verschieben ohne zu überschreiben. Auf demselben Volume über einen harten Link (atomar: schlägt fehl, wenn das Ziel
    inzwischen existiert), sonst kopieren + löschen."""
    for _ in range(5):
        dst = _free_name(folder, name)
        try:
            os.link(src, dst)
        except FileExistsError:
            continue
        except OSError as e:
            if e.errno not in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK, getattr(errno, "EOPNOTSUPP", 95)):
                raise
            # anderes Volume oder keine harten Links: exklusiv anlegen, kopieren, Original löschen
            try:
                fd = os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o664)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
                shutil.copystat(src, dst)
            except BaseException:
                try:
                    os.unlink(dst)
                except OSError:
                    pass
                raise
            os.unlink(src)
            return dst
        os.unlink(src)
        return dst
    raise HTTPException(409, "Zieldatei konnte nicht angelegt werden – bitte erneut versuchen")


def can_write(conn: sqlite3.Connection, user, share: str) -> bool:
    access = shares.user_access(conn, user)
    return any(n.lower() == share.lower() and w for n, w in access.items())


def suggest(conn: sqlite3.Connection, share: str, relpath: str) -> Dict[str, str]:
    from .regal import title_from_name
    p = shares.resolve(conn, share, relpath)
    name = p.name
    st = p.stat()
    return {"title": suggest_title(name), "doc_date": date_from_name(name, st.st_mtime), "ext": os.path.splitext(name)[1]}


GENERIC = re.compile(r"^(scan|scans|img|image|dsc|dscn|pxl|doc|dokument|document|bild|foto|photo|file|datei)?[\s_\-.]*\d*$", re.I)


def suggest_title(name: str) -> str:
    """Titelvorschlag ohne Datum und Zähler aus dem Dateinamen; bei reinen Scanner-Namen leer (dann tippt man ihn)."""
    from .regal import title_from_name
    stem = os.path.splitext(name)[0]
    rest = DATE_IN_NAME.sub(" ", stem)
    rest = re.sub(r"[_\s]+", " ", rest).strip(" -_.")
    if not rest or GENERIC.match(rest) or re.fullmatch(r"[\d\s_\-.]+", rest):
        return ""
    return title_from_name(rest)


def record_time() -> str:
    return now_utc_str()
