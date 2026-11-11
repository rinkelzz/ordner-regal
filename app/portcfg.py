"""Port der App ändern – ohne Konsole.

Der Port steht in <Paketordner>/var/config.env (ORG_PORT=…). Das Start-Skript von DSM liest ihn,
ebenso die DSM-Weiterleitung (ui/app.cgi → dsm_proxy.py). Nach dem Speichern startet sich die App
selbst neu (os.execv: gleicher Prozess, gleiche PID – DSM merkt davon nichts) und lauscht dann auf
dem neuen Port.
"""
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .config import settings

# Ports, die auf einer Synology fast immer belegt oder reserviert sind
RESERVED = {
    20, 21, 22, 23, 25, 53, 80, 110, 137, 138, 139, 143, 161, 443, 445, 515, 548, 873, 993, 995,
    3260, 3261, 3262, 3263, 3306, 5000, 5001, 5005, 5006, 5353, 6690, 9000, 9900, 9901,
}
MIN_PORT = 1025


class PortError(ValueError):
    pass


def port_free(port: int, host: str = "") -> bool:
    """Lässt sich der Port gerade belegen? (kurz öffnen und sofort wieder schließen)"""
    for family, addr in ((socket.AF_INET, host or "0.0.0.0"),):
        s = socket.socket(family, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((addr, port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def check_port(port: int) -> None:
    if not MIN_PORT <= port <= 65535:
        raise PortError(f"Bitte einen Port zwischen {MIN_PORT} und 65535 wählen.")
    if port in RESERVED:
        raise PortError(f"Port {port} ist auf der Synology für andere Dienste reserviert.")
    if port != settings.port and not port_free(port):
        raise PortError(f"Port {port} ist schon belegt – bitte einen anderen wählen.")


def write_config(port: int, path: Optional[Path] = None) -> Path:
    """ORG_PORT in config.env setzen (andere Zeilen bleiben unverändert)."""
    path = path or settings.config_file
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = "# Ordner-Regal – Einstellungen. Nach Änderungen das Paket neu starten.\n"
    line = f"ORG_PORT={port}"
    if re.search(r"^ORG_PORT=.*$", text, flags=re.M):
        text = re.sub(r"^ORG_PORT=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


def _restart_later(delay: float) -> None:
    time.sleep(delay)  # Antwort an den Browser erst noch ausliefern
    print(f"[ordnerregal] Neustart auf Port {os.environ.get('ORG_PORT')}", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, "-m", "app.main"])


def apply(port: int) -> None:
    """Neuen Port übernehmen: config.env schreiben, Hilfsdateien fürs DSM-Fenster aktualisieren, neu starten."""
    check_port(port)
    write_config(port)
    os.environ["ORG_PORT"] = str(port)
    if settings.self_restart:
        threading.Thread(target=_restart_later, args=(1.5,), name="port-restart", daemon=True).start()
