"""Anmeldung mit DSM-Benutzern.

Zwei Wege:
1. Single Sign-on im DSM-Fenster: ui/app.cgi (läuft im Webserver von DSM) ermittelt den
   angemeldeten DSM-Benutzer und schickt ihn zusammen mit einem Geheimnis (Proxy-Schlüssel)
   an die App. Nur wer den Schlüssel kennt, kann so einen Benutzer „ansagen“.
2. Login-Formular: Benutzername/Passwort werden bei DSM geprüft (SYNO.API.Auth), z. B.
   am Handy über den Reverse Proxy. Zwei-Faktor-Codes werden unterstützt.
"""
import hmac
import json
import os
import re
import secrets
import sqlite3
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import settings
from .db import audit, get_setting
from .timeutil import now_utc_str

USERNAME_MAX = 64


# --- Proxy-Schlüssel ---------------------------------------------------------

_secret: Optional[str] = None


def proxy_secret() -> str:
    global _secret
    if _secret is None:
        path = settings.data_dir / "proxy.secret"
        try:
            _secret = path.read_text(encoding="utf-8").strip()
        except OSError:
            _secret = ""
        if len(_secret) < 32:
            _secret = secrets.token_urlsafe(32)
            path.write_text(_secret, encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    return _secret


LOOPBACK = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})


# Wie die Schlüsseldatei geschützt ist: "owner" (nur Paketbenutzer, 0600), "world" (ließ sich nicht beschränken),
# "off" (Anmeldung im DSM-Fenster aus) oder "" (kein DSM-Paket, z. B. Entwicklung).
secret_mode = ""


def rotate_secret() -> None:
    """Neuer Schlüssel bei jedem Start – ein irgendwann ausgelesener Schlüssel gilt nicht ewig.
    Nur auf der NAS (dort wird er für app.cgi veröffentlicht)."""
    global _secret
    if not settings.ui_dir:
        return
    _secret = secrets.token_urlsafe(32)
    path = settings.data_dir / "proxy.secret"
    try:
        path.write_text(_secret, encoding="utf-8")
        os.chmod(path, 0o600)
    except OSError:
        pass


def _restrict(path: Path) -> str:
    """Datei nur für den Paketbenutzer lesbar machen (0600). app.cgi läuft als Paketbenutzer – auf einer NAS mit
    DSM 7 geprüft: Das DSM-Fenster funktioniert mit 0600 weiter. Andere lokale Benutzer und Programme kommen nicht
    mehr an den Schlüssel (mit ihm könnte man sich gegenüber 127.0.0.1 als beliebiger DSM-Benutzer ausgeben)."""
    try:
        os.chmod(path, 0o600)
        return "owner"
    except OSError:
        os.chmod(path, 0o644)
        return "world"


def publish_proxy_files(enabled: bool = True) -> bool:
    """Schlüssel und Port für ui/app.cgi neben das Paket legen (Paketordner, nicht im Web-Ordner).
    Mit enabled=False (Anmeldung im DSM-Fenster aus) wird der Schlüssel entfernt."""
    global secret_mode
    if not settings.ui_dir:
        secret_mode = ""
        return False
    target = Path(settings.ui_dir).parent
    try:
        modes = []
        for name, content in ((".proxy_secret", proxy_secret() if enabled else None), (".port", str(settings.port))):
            p = target / name
            if content is None:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
                continue
            tmp = p.with_name(name + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            modes.append(_restrict(tmp) if name == ".proxy_secret" else (os.chmod(tmp, 0o644) or "port"))
            tmp.replace(p)
        secret_mode = "off" if not enabled else ("owner" if "owner" in modes else "world")
        if secret_mode == "world":
            print("[ordnerregal] Hinweis: Der Schlüssel für das DSM-Fenster ließ sich nicht auf den Paketbenutzer beschränken "
                  "und ist für alle lokalen Benutzer der NAS lesbar. Wer das nicht möchte: Verwaltung → System → DSM-Fenster.")
        return True
    except OSError as e:
        print(f"[ordnerregal] Proxy-Dateien nicht schreibbar: {e}")
        return False


def proxy_info(request) -> Optional[Dict]:
    """Prüft, ob die Anfrage über ui/app.cgi kam. Liefert {user, admin, base} oder None.
    Ob der Benutzer DSM-Administrator ist, prüft die App selbst (Gruppe administrators) – dem Header wird nicht geglaubt."""
    sent = request.headers.get("x-org-proxy-secret")
    if not sent or secret_mode == "off":
        return None
    # app.cgi läuft auf der NAS und verbindet sich immer über 127.0.0.1 – von woanders zählt der Schlüssel nicht
    peer = request.scope.get("org_peer", request.client.host if request.client else "")
    # Über den Reverse Proxy weitergereichte Anfragen kommen zwar auch von 127.0.0.1, tragen aber X-Forwarded-For
    forwarded = any(request.headers.get(h) for h in ("x-forwarded-for", "x-real-ip", "forwarded"))
    if peer not in LOOPBACK or forwarded or not hmac.compare_digest(sent, proxy_secret()):
        return None
    user = urllib.parse.unquote(request.headers.get("x-dsm-user", "")).strip()
    user = user[:USERNAME_MAX] if user else ""
    shares = None
    raw = request.headers.get("x-dsm-shares")
    if raw is not None and user:
        try:
            data = json.loads(urllib.parse.unquote(raw))
            shares = [(str(n), bool(w)) for n, w in data][:2000]
        except (ValueError, TypeError):
            shares = None
    return {
        "user": user,
        "admin": is_dsm_admin(user) if user else False,
        "base": request.headers.get("x-org-proxy-base", "app.cgi"),
        "shares": shares,
    }


# --- Benutzer zuordnen -------------------------------------------------------

def _flag(conn: sqlite3.Connection, key: str) -> bool:
    return get_setting(conn, key) == "1"


def dsm_user_row(conn: sqlite3.Connection, username: str, dsm_admin: bool) -> Tuple[Optional[sqlite3.Row], str]:
    """Findet (oder, wenn eingeschaltet, erstellt) den Benutzer zum DSM-Konto. Liefert (Zeile, Fehlertext).

    Standard: Nur DSM-Konten, für die im Ordner-Regal ein Benutzer mit gleichem Namen angelegt ist,
    kommen hinein. Neu angelegte Benutzer haben nur Lesezugriff (ohne Zugangsdaten)."""
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if row and not row["active"]:
        return None, "Dein Zugang zum Ordner-Regal ist deaktiviert."
    make_admin = dsm_admin and _flag(conn, "dsm_admins")
    if row and row["is_admin"] and row["admin_via_dsm"] and not make_admin:
        # Vollzugriff kam nur aus DSM (Administrator + „DSM-Admins bekommen Vollzugriff“) – das gilt nicht mehr
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1").fetchone()[0]
        if admins > 1:
            conn.execute("UPDATE users SET is_admin = 0, admin_via_dsm = 0 WHERE id = ?", (row["id"],))
            note = ""
            audit(conn, None, "user_admin_dsm", f"user:{row['id']}",
                  f"Vollzugriff entzogen (kein DSM-Administrator mehr oder Zuordnung ausgeschaltet){note}")
            row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    if not row:
        if not (make_admin or _flag(conn, "dsm_auto_create")):
            return None, f"Für das DSM-Konto „{username}“ gibt es noch keinen Zugang zum Ordner-Regal. Bitte beim Administrator melden."
        cur = conn.execute(
            "INSERT INTO users(username, display_name, password_hash, is_admin, admin_via_dsm, created_at) VALUES (?, ?, NULL, ?, ?, ?)",
            (username, username, int(make_admin), int(make_admin), now_utc_str()),
        )
        audit(conn, None, "user_created_dsm", f"user:{cur.lastrowid}", f"{username}{' (Vollzugriff)' if make_admin else ''}")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    elif make_admin and not row["is_admin"]:
        conn.execute("UPDATE users SET is_admin = 1, admin_via_dsm = 1 WHERE id = ?", (row["id"],))
        audit(conn, None, "user_admin_dsm", f"user:{row['id']}", "DSM-Administrator")
        row = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    return row, ""


_admin_cache: Dict[str, Tuple[float, bool]] = {}


def is_dsm_admin(username: str) -> bool:
    now = time.time()
    hit = _admin_cache.get(username)
    if hit and now - hit[0] < 60:
        return hit[1]
    res = _is_dsm_admin(username)
    if len(_admin_cache) > 1000:
        _admin_cache.clear()
    _admin_cache[username] = (now, res)
    return res


def _is_dsm_admin(username: str) -> bool:
    if not re.fullmatch(r"[^\s/:]{1,64}", username or "") or username.startswith("-"):
        return False
    try:
        res = subprocess.run(["id", "-Gn", username], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
        if "administrators" in res.stdout.decode("utf-8", "replace").split():
            return True
        res = subprocess.run(["id", "-G", username], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
        return "101" in res.stdout.decode().split()
    except (OSError, subprocess.SubprocessError):
        return False


# --- Passwortprüfung bei DSM -------------------------------------------------

DEFAULT_DSM_URL = "http://127.0.0.1:5000"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keine Weiterleitungen: DSM antwortet auf entry.cgi nie mit 3xx – alles andere gilt als „nicht erreichbar“."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Weiterleitung abgelehnt", headers, fp)


def _dsm_opener(ctx: Optional[ssl.SSLContext]) -> urllib.request.OpenerDirector:
    """HTTP-Client nur für die DSM-Passwortprüfung: ohne Proxy (http_proxy/https_proxy werden ignoriert,
    die Anfrage geht immer direkt an die NAS) und ohne Weiterleitungen."""
    handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
    if ctx is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def local_dsm_url(url: str) -> Optional[str]:
    """Bereinigte DSM-Adresse, wenn sie auf die NAS selbst zeigt (127.0.0.1, localhost, ::1) – sonst None.

    Die Passwörter der Mitarbeiter gehen an diese Adresse. Ein anderer Rechner ist deshalb nie erlaubt:
    sonst könnte ein Admim Ordner-Regal DSM-Passwörter auf einen eigenen Server umleiten, oder sie
    gingen unverschlüsselt durchs Netz. Verglichen wird der echte Hostname, nicht der Anfang der Adresse
    (https://localhost.example.org ist kein localhost)."""
    url = (url or "").strip().rstrip("/")
    if any(ord(c) <= 32 or c in "\\" for c in url):
        return None
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port  # wirft ValueError bei Unsinn
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return None
    if parts.path or parts.query or parts.fragment:
        return None
    if (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        return None
    # Nur die DSM-Anmeldeports der NAS selbst: sonst könnte ein Admin der App einen eigenen Dienst auf der NAS
    # (anderer Port) als „DSM“ eintragen und die eingegebenen DSM-Passwörter mitlesen.
    allowed = {"http": {5000}, "https": {5001}}
    extra = os.environ.get("ORG_DSM_PORTS", "")  # z. B. „5080,5443“, wenn DSM auf anderen Ports läuft (Paketkonfiguration)
    for p in extra.split(","):
        if p.strip().isdigit():
            allowed["http"].add(int(p))
            allowed["https"].add(int(p))
    if (port or (80 if parts.scheme == "http" else 443)) not in allowed[parts.scheme]:
        return None
    return url

def parse_share_list(body) -> Optional[List[Tuple[str, bool]]]:
    """Antwort von SYNO.FileStation.List/list_share (additional=perm) → [(Freigabe, darf schreiben)].
    None, wenn die Antwort nicht passt (dann bleiben die bisherigen Rechte stehen)."""
    if not isinstance(body, dict) or not body.get("success"):
        return None
    shares = (body.get("data") or {}).get("shares")
    if not isinstance(shares, list):
        return None
    out = []
    for sh in shares:
        if not isinstance(sh, dict) or not isinstance(sh.get("name"), str):
            continue
        perm = ((sh.get("additional") or {}).get("perm") or {})
        right = str(perm.get("share_right") or "").upper()
        acl = perm.get("acl") or {}
        if right == "NA":
            continue
        write = right == "RW" or (right == "" and bool(acl.get("write")))
        out.append((sh["name"], write))
    return out


LIST_SHARE_QUERY = {"api": "SYNO.FileStation.List", "version": "2", "method": "list_share",
                    "additional": '["perm"]', "limit": "1000"}


def dsm_password_check(conn: sqlite3.Connection, username: str, password: str, otp: str = "",
                       shares_out: Optional[dict] = None) -> str:
    """Fragt DSM, ob die Zugangsdaten stimmen. Mit shares_out kommen die Freigaben des Kontos dazu
    (erst wenn das Passwort stimmt, mit der gerade erhaltenen Sitzung).
    Ergebnis: ok | wrong | otp | blocked | unreachable"""
    # Nur Adressen auf der NAS selbst – auch ein alter oder von Hand eingetragener Wert wird hier nochmals geprüft.
    base = local_dsm_url(get_setting(conn, "dsm_url") or DEFAULT_DSM_URL) or DEFAULT_DSM_URL
    form = {
        "api": "SYNO.API.Auth", "version": "6", "method": "login",
        "account": username, "passwd": password, "session": "Ordner-Regal", "format": "sid",
    }
    if otp:
        form["otp_code"] = otp
    data = urllib.parse.urlencode(form).encode()
    ctx = ssl.create_default_context()
    if base.startswith("https://"):
        # Die Verbindung bleibt auf der NAS (Loopback, siehe local_dsm_url) und geht nie übers Netz.
        # DSM nutzt dort meist ein selbst signiertes Zertifikat, das nicht auf 127.0.0.1 ausgestellt ist.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    opener = _dsm_opener(ctx if base.startswith("https") else None)
    try:
        req = urllib.request.Request(base + "/webapi/entry.cgi", data=data, method="POST")
        with opener.open(req, timeout=10) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return "unreachable"
    if body.get("success"):
        sid = (body.get("data") or {}).get("sid")
        if sid and shares_out is not None:
            try:
                q = urllib.parse.urlencode({**LIST_SHARE_QUERY, "_sid": sid})
                with opener.open(base + "/webapi/entry.cgi?" + q, timeout=10) as r:
                    parsed = parse_share_list(json.loads(r.read().decode("utf-8", "replace")))
                if parsed is not None:
                    shares_out["shares"] = parsed
            except (OSError, ValueError, urllib.error.URLError):
                pass
        if sid:  # Sitzung bei DSM gleich wieder schließen
            try:
                q = urllib.parse.urlencode({"api": "SYNO.API.Auth", "version": "6", "method": "logout",
                                            "session": "Ordner-Regal", "_sid": sid})
                with opener.open(base + "/webapi/entry.cgi?" + q, timeout=5) as r:
                    r.read()
            except (OSError, urllib.error.URLError):
                pass
        return "ok"
    code = (body.get("error") or {}).get("code")
    if code in (403, 406):          # 2FA-Code nötig / 2FA erzwungen
        return "otp"
    if code == 404:                 # 2FA-Code falsch
        return "otp_wrong"
    if code in (407,):              # IP gesperrt
        return "blocked"
    return "wrong"                  # 400 falsches Passwort, 401/402 Konto deaktiviert/keine Berechtigung


# --- Schutz vor Durchprobieren ----------------------------------------------

_fails: Dict[str, list] = {}
_lock = threading.Lock()
MAX_FAILS, WINDOW_S = 5, 15 * 60
# Zusätzlich pro Benutzername, egal von welcher Adresse (gegen verteiltes Durchprobieren)
MAX_FAILS_PER_NAME = 10
# Und pro Adresse, egal welcher Name (gegen Durchprobieren vieler Namen von einer Stelle)
MAX_FAILS_PER_IP = 30


def too_many_attempts(key: str, limit: int = MAX_FAILS) -> bool:
    now = time.time()
    with _lock:
        if key not in _fails:  # Nachsehen legt nichts an – sonst füllen neue Namen den Speicher
            return False
        recent = [t for t in _fails[key] if now - t < WINDOW_S]
        if recent:
            _fails[key] = recent
        else:
            del _fails[key]
        return len(recent) >= limit


MAX_KEYS = 50000  # Obergrenze, damit viele verschiedene Namen den Speicher nicht füllen (≈ wenige MB)


def _prune(now: float) -> None:
    """Abgelaufene Einträge entfernen; ist es danach immer noch zu viel, die ältesten verwerfen."""
    for k in [k for k, v in _fails.items() if not v or now - v[-1] >= WINDOW_S]:
        del _fails[k]
    if len(_fails) > MAX_KEYS:
        for k in sorted(_fails, key=lambda k: _fails[k][-1])[: len(_fails) - MAX_KEYS]:
            del _fails[k]


_last_prune = [0.0]


def note_failure(key: str) -> None:
    now = time.time()
    with _lock:
        if len(_fails) >= MAX_KEYS or now - _last_prune[0] > 60:
            _prune(now)  # regelmäßig und bei Erreichen der Obergrenze alles Abgelaufene entfernen
            _last_prune[0] = now
        lst = _fails.setdefault(key, [])
        lst.append(now)
        if len(lst) > 100:
            del lst[:-100]


def clear_failures(key: str) -> None:
    with _lock:
        _fails.pop(key, None)
