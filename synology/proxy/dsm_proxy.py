"""Weiterleitung DSM-Fenster → Ordner-Regal (nur Standardbibliothek, Python 3.6+).

Wird von ui/app.cgi aufgerufen, also vom Webserver von DSM. Dadurch kommt die
Ordner-Regal aus Sicht des Browsers von DSM selbst:
- DSM blockiert den Inhalt im Fenster nicht mehr (gleicher Ursprung)
- kein http/https-Problem
- der angemeldete DSM-Benutzer wird hier über authenticate.cgi ermittelt und mit
  einem Geheimnis an die App weitergegeben → Anmeldung ohne zweiten Login.

Aufruf: app.cgi?SynoToken=…&p=/pfad?query
"""
import http.client
import os
import re
import subprocess
import sys
from urllib.parse import parse_qs, quote

PKG = "ordnerregal"
TARGET = os.environ.get("ORG_PKG_TARGET", "/var/packages/%s/target" % PKG)
VAR = os.environ.get("ORG_PKG_VAR", "/var/packages/%s/var" % PKG)
AUTH_CGI = os.environ.get("ORG_AUTH_CGI", "/usr/syno/synoman/webman/modules/authenticate.cgi")
ID_CMD = os.environ.get("ORG_ID_CMD", "id")
USER_RE = re.compile(r"^[A-Za-z0-9._@\-\\ ]{1,64}$")
PASS_HEADERS = ("CONTENT_TYPE", "HTTP_ACCEPT", "HTTP_ACCEPT_LANGUAGE", "HTTP_X_SESSION_TOKEN")
RETURN_HEADERS = ("content-type", "content-disposition", "cache-control", "etag", "last-modified",
                  "x-content-type-options", "content-security-policy")


def out(status, headers, body=b""):
    w = sys.stdout.buffer
    w.write(("Status: %s\r\n" % status).encode())
    for k, v in headers:
        w.write(("%s: %s\r\n" % (k, v)).encode("latin-1", "replace"))
    w.write(b"\r\n")
    w.write(body)
    w.flush()


def fail(status, text):
    out(status, [("Content-Type", "text/plain; charset=utf-8"), ("Cache-Control", "no-store")], text.encode("utf-8"))


def read_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def port():
    m = re.search(r"^ORG_PORT=\"?(\d+)", read_file(os.path.join(VAR, "config.env")), re.M)
    return int(m.group(1)) if m else int(read_file(os.path.join(TARGET, ".port")) or 8790)


def dsm_user(token):
    """Angemeldeten DSM-Benutzer über Synologys authenticate.cgi ermitteln."""
    env = dict(os.environ)
    if token:
        env["QUERY_STRING"] = "SynoToken=" + quote(token)
        env["HTTP_X_SYNO_TOKEN"] = token
    try:
        res = subprocess.run([AUTH_CGI], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    name = res.stdout.decode("utf-8", "replace").strip().splitlines()
    name = name[-1].strip() if name else ""
    return name if USER_RE.match(name) else ""


# Freigaben des angemeldeten DSM-Kontos (für die Rechte im Regal). Nur beim Laden der App und bei /api/v1/me –
# nicht bei jeder Anfrage. Gefragt wird DSM auf der NAS selbst, mit der Sitzung des Benutzers (Cookie + SynoToken).
SHARES_PATHS = ("/", "/api/v1/me")
DSM_ENDPOINTS = (("http", 5000), ("https", 5001))


def dsm_shares(token):
    import json
    import ssl
    cookie = os.environ.get("HTTP_COOKIE", "")
    if not cookie:
        return None
    query = "api=SYNO.FileStation.List&version=2&method=list_share&limit=1000&additional=%5B%22perm%22%5D"
    if token:
        query += "&SynoToken=" + quote(token)
    headers = {"Cookie": cookie}
    if token:
        headers["X-SYNO-TOKEN"] = token
    for scheme, p in DSM_ENDPOINTS:
        try:
            if scheme == "https":
                ctx = ssl.create_default_context()
                ctx.check_hostname = False      # Loopback auf der NAS selbst, DSM-Zertifikat passt nicht auf 127.0.0.1
                ctx.verify_mode = ssl.CERT_NONE
                c = http.client.HTTPSConnection("127.0.0.1", p, timeout=8, context=ctx)
            else:
                c = http.client.HTTPConnection("127.0.0.1", p, timeout=8)
            c.request("GET", "/webapi/entry.cgi?" + query, headers=headers)
            r = c.getresponse()
            if r.status != 200:   # z. B. Weiterleitung http → https
                continue
            body = json.loads(r.read().decode("utf-8", "replace"))
        except (OSError, ValueError):
            continue
        if not body.get("success"):
            return None
        out = []
        for sh in (body.get("data") or {}).get("shares") or []:
            perm = ((sh.get("additional") or {}).get("perm") or {})
            right = str(perm.get("share_right") or "").upper()
            if right == "NA" or not isinstance(sh.get("name"), str):
                continue
            out.append([sh["name"], right == "RW" or (right == "" and bool((perm.get("acl") or {}).get("write")))])
        return json.dumps(out, separators=(",", ":"))
    return None


def main():
    qs = parse_qs(os.environ.get("QUERY_STRING", ""), keep_blank_values=True)
    path = (qs.get("p") or ["/"])[0] or "/"
    if not path.startswith("/") or path.startswith("//") or "\r" in path or "\n" in path:
        return fail("400 Bad Request", "Ungültiger Pfad")
    token = (qs.get("SynoToken") or [""])[0] or os.environ.get("HTTP_X_SYNO_TOKEN", "")

    secret = read_file(os.path.join(TARGET, ".proxy_secret"))
    if not secret:
        return fail("503 Service Unavailable", "Ordner-Regal ist noch nicht gestartet – oder die Anmeldung im DSM-Fenster "
                    "ist ausgeschaltet (Verwaltung → System). Bitte über https bzw. die eingerichtete Adresse öffnen.")

    user = dsm_user(token)
    headers = {"X-ORG-Proxy-Secret": secret, "X-ORG-Proxy-Base": "app.cgi"}
    if user:
        headers["X-DSM-User"] = quote(user)  # Admin-Rechte prüft die App selbst (Gruppe administrators)
        if path.split("?", 1)[0] in SHARES_PATHS:
            shares = dsm_shares(token)
            if shares is not None:
                headers["X-DSM-Shares"] = quote(shares)
    for key in PASS_HEADERS:
        if os.environ.get(key):
            name = "Content-Type" if key == "CONTENT_TYPE" else key[5:].replace("_", "-").title()
            headers[name] = os.environ[key]

    method = os.environ.get("REQUEST_METHOD", "GET").upper()
    # Der Webserver von DSM reicht nur GET/POST zuverlässig an CGIs durch.
    # PATCH/PUT/DELETE kommen deshalb als POST mit X-HTTP-Method-Override.
    override = os.environ.get("HTTP_X_HTTP_METHOD_OVERRIDE", "").upper()
    if method == "POST" and override in ("PATCH", "PUT", "DELETE"):
        method = override
    body = None
    length = int(os.environ.get("CONTENT_LENGTH") or 0)
    if length > 0:
        if length > 11 * 1024 * 1024:  # Logo u. Ä. – Dokumente selbst werden nie hochgeladen
            return fail("413 Payload Too Large", "Anfrage zu groß")
        body = sys.stdin.buffer.read(length)

    try:
        conn = http.client.HTTPConnection("127.0.0.1", port(), timeout=60)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
    except OSError as e:
        return fail("502 Bad Gateway", "Ordner-Regal nicht erreichbar – läuft das Paket? (%s)" % e)
    hdrs = [(k, v) for k, v in resp.getheaders() if k.lower() in RETURN_HEADERS and k.lower() != "cache-control"]
    if path.startswith("/api/"):  # API-Antworten nie zwischenspeichern
        hdrs += [("Cache-Control", "no-store, no-cache, must-revalidate"), ("Pragma", "no-cache"), ("Expires", "0")]
    else:
        hdrs += [(k, v) for k, v in resp.getheaders() if k.lower() == "cache-control"]
    out("%d %s" % (resp.status, resp.reason), hdrs, data)


if __name__ == "__main__":
    main()
