"""Einstiegspunkt des Ordner-Regals."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import csrf, proxy
from .config import settings
from .db import connect, get_setting, init_db
from .dsm import write_ui_config
from .dsm_auth import proxy_info, publish_proxy_files
from . import scheduler
from .routers import auth, backup, branding, files, inbox as inbox_router, regal as regal_router, settings as settings_router, updates, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    conn = connect()
    try:
        write_ui_config(conn)
        from . import dsm_auth, httpguard
        httpguard.configure(get_setting(conn, "http_access"))
        dsm_auth.rotate_secret()
        publish_proxy_files(get_setting(conn, "dsm_sso") != "0")
        proxy.configure(get_setting(conn, "trusted_proxies"))
        from . import shares
        shares.sync(conn)
    finally:
        conn.close()
    if settings.scheduler:
        scheduler.start()
    if settings.update_check:
        from . import updates
        updates.check_in_background()  # gleich beim Start, damit die Paketquelle sofort etwas liefert
    yield
    scheduler.stop()


app = FastAPI(
    title="Ordner-Regal",
    version=__version__,
    description="Virtuelles Ordner-Regal für die Synology NAS: Dokumente aus den freigegebenen Ordnern in Ordnern und Registern ablegen – die Dateien bleiben, wo sie sind.",
    root_path=settings.root_path,
    lifespan=lifespan,
)

# Echte Absenderadresse hinter dem Reverse Proxy (X-Forwarded-For/-Proto) – nur von der NAS selbst
# (DSM-Reverse-Proxy) und von Proxys, die unter Verwaltung → System eingetragen sind (siehe app/proxy.py).
# http-Sperre läuft nach der Proxy-Auswertung (innen), damit https über einen Reverse Proxy als https zählt
from . import httpguard  # noqa: E402
app.add_middleware(httpguard.HttpGuard)
app.add_middleware(proxy.ProxyHeaders)


class _RememberPeer:
    """Merkt sich die echte TCP-Gegenstelle, bevor X-Forwarded-For sie umschreibt
    (für die Prüfung, ob eine Anfrage wirklich von ui/app.cgi auf der NAS kommt)."""

    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            scope["org_peer"] = client[0] if client else ""
        await self.app(scope, receive, send)


app.add_middleware(_RememberPeer)  # zuletzt hinzugefügt = läuft zuerst

for r in (auth.router, backup.router, branding.router, settings_router.router, updates.router, users.router,
          regal_router.router, files.router, inbox_router.router):
    app.include_router(r)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    reason = csrf.blocked(request.method, request.url.path, request.headers)
    if reason:
        return JSONResponse({"detail": reason}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def no_cache_for_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index(request: Request):
    # Versionsnummer an CSS/JS hängen, damit nach einem Update nichts Altes aus dem Cache kommt.
    # Im DSM-Fenster laufen alle Adressen über app.cgi?p=… (gleicher Ursprung wie DSM).
    proxied = proxy_info(request)
    base = (proxied["base"] + "?p=/") if proxied else ""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("{{v}}", __version__)
    html = html.replace('"static/', '"' + base + "static/")
    html = html.replace("<head>", "<head>\n<script>window.ORG_PROXY=" + ("\"" + proxied["base"] + "\"" if proxied else "null") + ";</script>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/api/v1/health", tags=["System"])
def health():
    """Ohne Login – für DSM-Statusprüfung und Reverse-Proxy-Checks."""
    return {"status": "ok", "version": __version__}


def run() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=False,  # übernimmt app/proxy.py (NAS selbst + eingetragene Proxys)
    )


if __name__ == "__main__":
    run()
