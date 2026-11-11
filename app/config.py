"""Konfiguration über Umgebungsvariablen.

Alle Werte haben sinnvolle Defaults, damit die App lokal ohne weitere
Einrichtung startet. Im Synology-Paket setzt sie das Start-Skript aus
<Paketordner>/var/config.env.
"""
import os
from pathlib import Path

DEFAULT_PORT = 8800


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "ja", "on")


def _port(value: str) -> int:
    try:
        p = int(value)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return p if 1 <= p <= 65535 else DEFAULT_PORT


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("ORG_DATA_DIR", "./data")).resolve()
        self.db_path = self.data_dir / "ordnerregal.db"
        # config.env liegt im Paket im Datenordner (var/) – dort steht u. a. der Port
        self.config_file = Path(os.environ.get("ORG_CONFIG_FILE", str(self.data_dir / "config.env")))
        # Zeitzone für Tagesgrenzen und Anzeige
        self.timezone = os.environ.get("ORG_TIMEZONE", "Europe/Berlin")
        # Öffentliche Adresse (Reverse Proxy); leer = Adresse wird aus dem Aufruf abgeleitet
        self.public_url = os.environ.get("ORG_PUBLIC_URL", "").strip().rstrip("/")
        # Unterpfad, falls die App hinter einem Proxy unter /ordnerregal läuft
        self.root_path = os.environ.get("ORG_ROOT_PATH", "").rstrip("/")
        self.host = os.environ.get("ORG_HOST", "0.0.0.0")
        self.port = _port(os.environ.get("ORG_PORT", str(DEFAULT_PORT)))
        # Session-Cookie nur über HTTPS senden (hinter Reverse Proxy mit TLS: an)
        self.cookie_secure = _bool("ORG_COOKIE_SECURE", False)
        self.session_days = int(os.environ.get("ORG_SESSION_DAYS", "7"))
        # Ordner der DSM-Oberfläche (ui/), dort liegt app.json fürs DSM-Fenster
        self.ui_dir = os.environ.get("ORG_UI_DIR", "")
        # Hintergrund-Aufgaben (Sicherung, Update-Prüfung, später Fristen-Mails); in Tests aus
        self.scheduler = _bool("ORG_SCHEDULER", True)
        # Regelmäßig bei GitHub nach neuen Versionen sehen (Paketquelle fürs Paket-Zentrum)
        self.update_check = _bool("ORG_UPDATE_CHECK", True)
        # Nach einer Port-Änderung sich selbst neu starten (in Tests aus)
        self.self_restart = _bool("ORG_SELF_RESTART", True)
        # Optional: Startwerte aus dem Installationsassistenten
        self.initial_admin_password = os.environ.get("ORG_ADMIN_PASSWORD")
        self.initial_company = os.environ.get("ORG_COMPANY", "")


settings = Settings()

# Designs der Oberfläche (Schlüssel → Name). „classic“ folgt hell/dunkel des Geräts.
THEMES = {
    "classic": "Klassisch",
    "dsm": "Synology DSM",
    "dark": "Dunkel",
    "warm": "Warm & groß",
}
