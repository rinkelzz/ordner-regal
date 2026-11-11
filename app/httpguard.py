"""Weboberfläche nicht über unverschlüsseltes http aus dem Netz.

Über http können Passwörter und Sitzungen mitgelesen werden. Erlaubt sind deshalb nur: https (auch über einen
eingetragenen Reverse Proxy, der X-Forwarded-Proto: https setzt), Zugriffe von der NAS selbst (DSM-Fenster,
DSM-Reverse-Proxy) und die Pfade, die ohne Benutzeranmeldung auskommen müssen –
die Paketquelle und der Statuscheck. Unter Verwaltung → System lässt sich http wieder erlauben (oder per
ORG_HTTP_ACCESS=1 in der Paketkonfiguration).
"""
import os

from . import proxy

OPEN_PREFIXES = ("/spk", "/api/v1/health")
_allowed = [os.environ.get("ORG_HTTP_ACCESS") == "1"]


def configure(value: str) -> None:
    env = os.environ.get("ORG_HTTP_ACCESS")
    _allowed[0] = (env == "1") if env in ("0", "1") else value == "1"


def http_allowed() -> bool:
    return _allowed[0]


PAGE = """<!doctype html><html lang="de"><meta charset="utf-8"><title>Bitte verschlüsselt öffnen</title>
<body style="font:16px system-ui;max-width:640px;margin:60px auto;padding:0 20px;color:#1f2937">
<h1 style="font-size:22px">Bitte über https oder das DSM-Fenster öffnen</h1>
<p>Das Ordner-Regal zeigt Ihre Dokumente. Über unverschlüsseltes <b>http</b> könnten Passwörter und Anmeldungen im
Netz mitgelesen werden – deshalb ist dieser Weg gesperrt.</p>
<ul><li>Im <b>DSM</b> über das Hauptmenü → <b>Ordner-Regal</b> öffnen, oder</li>
<li>eine <b>https</b>-Adresse über einen Reverse Proxy verwenden.</li></ul>
<p style="color:#6b7280;font-size:14px">Die Paketquelle funktioniert weiter. Ein Admin kann http unter
Verwaltung → System → „Unverschlüsselter Zugriff“ wieder erlauben.</p></body></html>"""


class HttpGuard:
    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("scheme") == "http" and not _allowed[0]:
            path = scope.get("path", "")
            client = (scope.get("client") or ("", 0))[0]
            # Von der NAS selbst: DSM-Fenster (app.cgi) oder der DSM-Reverse-Proxy, der https beendet und nicht
            # immer X-Forwarded-Proto mitschickt – beides bleibt erlaubt.
            peer = scope.get("org_peer") or client
            if not path.startswith(OPEN_PREFIXES) and not proxy.is_loopback(client) and not proxy.is_loopback(peer):
                api = path.startswith("/api/")
                body = (b'{"detail":"Unverschl\\u00fcsselter Zugriff (http) ist gesperrt \\u2013 bitte \\u00fcber https oder das DSM-Fenster \\u00f6ffnen"}'
                        if api else PAGE.encode("utf-8"))
                await send({"type": "http.response.start", "status": 403, "headers": [
                    (b"content-type", b"application/json" if api else b"text/html; charset=utf-8"),
                    (b"cache-control", b"no-store"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
