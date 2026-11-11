"""„Freigaben durchsuchen“: Dateien pro eingeschalteter Freigabe zählen – und wie viele davon noch nicht im Regal liegen.

Läuft im Hintergrund (große Freigaben brauchen etwas), die Oberfläche fragt den Stand ab. Gezählt wird nur, was man
auch ablegen würde (keine DSM-Systemordner, keine versteckten Dateien). Pro Freigabe höchstens MAX_FILES Dateien.
"""
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from . import shares
from .timeutil import now_utc_str

MAX_FILES = 300_000
_lock = threading.Lock()
_state: Dict[str, object] = {"running": False, "share": "", "files": 0, "started": 0.0, "error": ""}


def state() -> dict:
    with _lock:
        return dict(_state)


def _count(root: Path, known: set, share: str) -> tuple:
    total = unfiled = 0
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not shares.hidden(d)]
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        for name in filenames:
            if shares.hidden(name):
                continue
            total += 1
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if (share.lower(), rel) not in known:
                unfiled += 1
            if total % 2000 == 0:
                with _lock:
                    _state["files"] = int(_state["files"]) + 2000
            if total >= MAX_FILES:
                return total, unfiled, True
    return total, unfiled, truncated


def run(conn) -> Dict[str, dict]:
    """Alle eingeschalteten Freigaben durchsuchen (synchron) und das Ergebnis speichern."""
    known = {(r["share"].lower(), r["relpath"]) for r in conn.execute("SELECT share, relpath FROM files")}
    out = {}
    for name, root in sorted(shares.enabled_shares(conn).items(), key=lambda x: x[0].lower()):
        with _lock:
            _state["share"] = name
        try:
            total, unfiled, trunc = _count(root, known, name)
        except OSError:
            continue
        conn.execute("UPDATE shares SET scan_files = ?, scan_unfiled = ?, scan_at = ?, scan_truncated = ? WHERE name = ?",
                     (total, unfiled, now_utc_str(), int(trunc), name))
        out[name] = {"files": total, "unfiled": unfiled, "truncated": trunc}
    return out


def start() -> bool:
    """Im Hintergrund starten. False, wenn schon eine Suche läuft."""
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, share="", files=0, started=time.time(), error="")

    def work():
        from .db import connect
        conn = connect()
        try:
            run(conn)
        except Exception as e:  # noqa: BLE001
            with _lock:
                _state["error"] = str(e)
        finally:
            conn.close()
            with _lock:
                _state["running"] = False

    threading.Thread(target=work, name="ordnerregal-durchsuchen", daemon=True).start()
    return True


def wait(timeout: float = 30.0) -> None:
    """Für Tests: warten, bis die Suche fertig ist."""
    end = time.time() + timeout
    while time.time() < end and state()["running"]:
        time.sleep(0.05)


def last(conn) -> Optional[str]:
    r = conn.execute("SELECT MAX(scan_at) FROM shares").fetchone()
    return r[0] if r else None
