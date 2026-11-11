"""Hintergrund-Aufgaben: Fristen-Mails, nächtliche Dateiprüfung, Datensicherung und Update-Prüfung.

Ein Thread, der alle paar Minuten nachsieht, ob etwas fällig ist. Fehler werden protokolliert,
stoppen aber nie die App.
"""
import threading
from typing import Optional

from .config import settings

_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def run_once() -> None:
    try:
        from . import deadlines
        from .db import connect
        conn = connect()
        try:
            r = deadlines.run_due(conn)
            if r and (r["sent"] or r["digest"] or r["errors"]):
                print(f"[ordnerregal] Fristen-Mails: {r}", flush=True)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[ordnerregal] Fristen-Mails FEHLER: {e}", flush=True)
    try:
        _filecheck_due()
    except Exception as e:  # noqa: BLE001
        print(f"[ordnerregal] Dateiprüfung FEHLER: {e}", flush=True)
    try:
        from . import backup
        b = backup.run_due()
        if b:
            print(f"[ordnerregal] Sicherung {b['name']} erstellt"
                  + (f" – Ordner: {b['shared_error']}" if b["shared_error"] else ""), flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[ordnerregal] Datensicherung FEHLER: {e}", flush=True)
    if settings.update_check:
        try:
            from . import updates  # neue Version auf GitHub? (höchstens alle 30 Minuten)
            st = updates.check()
            if st["error"]:
                print(f"[ordnerregal] Update-Prüfung: {st['error']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[ordnerregal] Update-Prüfung FEHLER: {e}", flush=True)


def _filecheck_due() -> None:
    """Einmal pro Nacht (ab der eingestellten Stunde): fehlende Dateien markieren und verschobene wiederfinden."""
    from . import filecheck
    from .db import connect, get_setting, set_setting
    from .timeutil import now_utc, to_local
    conn = connect()
    try:
        now = to_local(now_utc())
        today = now.date().isoformat()
        hour = int(get_setting(conn, "filecheck_hour") or 3)
        if now.hour < hour or get_setting(conn, "filecheck_last_day") == today:
            return
        set_setting(conn, "filecheck_last_day", today)
        r = filecheck.check(conn)
        print(f"[ordnerregal] Dateiprüfung: {r}", flush=True)
        from . import folderimport, sharescan
        s = folderimport.sync_all(conn)
        if s:
            print(f"[ordnerregal] Abgleich verknüpfter Ordner: {s}", flush=True)
        sharescan.run(conn)
    finally:
        conn.close()


def _loop(interval: float) -> None:
    if _stop.wait(60):  # nach dem Start kurz warten
        return
    while not _stop.is_set():
        run_once()
        _stop.wait(interval)


def start(interval: float = 60) -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), name="ordnerregal-zeitplan", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
