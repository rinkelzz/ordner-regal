"""0.1: Regale, Ordner, Register, Dokumente aus Freigaben, Rechte wie in DSM, Vorschau, Suche, Wiederfinden."""
import json
import os
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import dsm_auth, filecheck
from app.db import connect
from app.main import app
from app.routers import auth as auth_router

from .fresh import fresh_db

PW = os.environ["ORG_ADMIN_PASSWORD"]
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.fixture()
def nas(tmp_path, monkeypatch):
    """Nachgestellte NAS: /volume1 mit Freigaben Dokumente, Privat, Scans und Systemordnern."""
    vol = tmp_path / "volume1"
    (vol / "Dokumente" / "Versicherung").mkdir(parents=True)
    (vol / "Dokumente" / "Versicherung" / "HUK_Kfz_2026.pdf").write_bytes(PDF)
    (vol / "Dokumente" / "Versicherung" / "Rechnung.pdf").write_bytes(PDF + b"x")
    (vol / "Dokumente" / "boese.html").write_text("<script>alert(1)</script>")
    (vol / "Dokumente" / "@eaDir").mkdir()
    (vol / "Dokumente" / "#recycle").mkdir()
    (vol / "Privat").mkdir()
    (vol / "Privat" / "Gehalt.pdf").write_bytes(PDF + b"geheim")
    (vol / "Scans").mkdir()
    (vol / "homes" / "anna").mkdir(parents=True)
    (vol / "@appstore").mkdir()
    (tmp_path / "draussen.txt").write_text("nicht erlaubt")
    os.symlink(tmp_path / "draussen.txt", vol / "Dokumente" / "link.txt")
    monkeypatch.setenv("ORG_SHARE_ROOTS", str(vol))
    monkeypatch.setattr(dsm_auth, "_is_dsm_admin", lambda u: False)
    dsm_auth._admin_cache.clear()
    data = tmp_path / "data"
    data.mkdir()
    with fresh_db(data):
        yield vol


def login(c, user="admin", pw=PW):
    r = c.post("/api/v1/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r


def sso(user, shares=None):
    h = {"X-ORG-Proxy-Secret": dsm_auth.proxy_secret(), "X-ORG-Proxy-Base": "app.cgi", "X-DSM-User": user}
    if shares is not None:
        h["X-DSM-Shares"] = quote(json.dumps(shares))
    return h


def setup_shelf(c):
    shares = c.get("/api/v1/admin/shares").json()["shares"]
    names = [s["name"] for s in shares]
    assert names == ["Dokumente", "Privat", "Scans"]  # keine @-/#-Ordner, kein „homes“
    for n in ("Dokumente", "Privat"):
        assert c.patch(f"/api/v1/admin/shares/{n}", json={"enabled": True}).status_code == 200
    shelf = c.post("/api/v1/shelves", json={"name": "Privat"}).json()
    assert [b["name"] for b in shelf["boards"]] == ["Oberes Fach", "Unteres Fach"]
    binder = c.post("/api/v1/binders", json={"board_id": shelf["boards"][0]["id"], "title": "Versicherungen",
                                             "color": "#c53030", "tabs": ["Kfz", "Hausrat"]}).json()
    return shelf, binder


def test_file_and_view(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, binder = setup_shelf(c)
        assert [t["name"] for t in binder["tabs"]] == ["Kfz", "Hausrat"]
        kfz = binder["tabs"][0]["id"]
        br = c.get("/api/v1/browse", params={"share": "Dokumente", "path": ""}).json()
        assert [d["name"] for d in br["dirs"]] == ["Versicherung"]
        br = c.get("/api/v1/browse", params={"share": "Dokumente", "path": "Versicherung"}).json()
        assert [f["name"] for f in br["files"]] == ["HUK_Kfz_2026.pdf", "Rechnung.pdf"] and br["files"][0]["doc_id"] is None
        r = c.post(f"/api/v1/tabs/{kfz}/docs", json={"files": [{"share": "Dokumente", "path": "Versicherung/HUK_Kfz_2026.pdf"}],
                                                     "tags": ["Kfz", "HUK", "kfz"], "doc_date": "2025-11-14"}).json()
        assert r["added"] == 1
        doc = r["binder"]["tabs"][0]["docs"][0]
        assert doc["title"] == "HUK Kfz 2026" and [t["name"] for t in doc["tags"]] == ["HUK", "Kfz"] and doc["doc_date"] == "2025-11-14"
        # zweimal ablegen → nichts doppelt
        again = c.post(f"/api/v1/tabs/{kfz}/docs", json={"files": [{"share": "dokumente", "path": "/Versicherung//HUK_Kfz_2026.pdf"}]}).json()
        assert again == {**again, "added": 0, "already": 1}
        # Regalansicht zählt
        sh = c.get(f"/api/v1/shelves/{shelf['id']}").json()
        assert sh["boards"][0]["binders"][0] == {**sh["boards"][0]["binders"][0], "title": "Versicherungen", "docs": 1, "hidden": 0}
        # Vorschau: PDF inline, HTML nur als Download, ohne Sandbox-Lücke
        f = c.get(f"/api/v1/docs/{doc['id']}/file?inline=1")
        assert f.status_code == 200 and f.content == PDF and f.headers["content-type"] == "application/pdf"
        assert f.headers["content-disposition"].startswith("inline") and f.headers["x-content-type-options"] == "nosniff"
        h = c.get("/api/v1/browse/file", params={"share": "Dokumente", "path": "boese.html", "inline": 1})
        assert h.headers["content-disposition"].startswith("attachment") and h.headers["content-type"] == "application/octet-stream"
        assert "sandbox" in h.headers["content-security-policy"]
        # Dokument zusätzlich in „Hausrat“ ablegen, dann aus Kfz entfernen → bleibt im Regal
        hausrat = binder["tabs"][1]["id"]
        d = c.post(f"/api/v1/docs/{doc['id']}/move", json={"from_tab": kfz, "to_tab": hausrat, "keep": True}).json()
        assert len(d["places"]) == 2
        b = c.delete(f"/api/v1/tabs/{kfz}/docs/{doc['id']}").json()
        assert b["tabs"][0]["docs"] == [] and len(b["tabs"][1]["docs"]) == 1
        # Register mit Dokumenten lässt sich nicht löschen; letztes Register auch nicht
        assert c.delete(f"/api/v1/tabs/{hausrat}").status_code == 400
        # ganz entfernen → Dokument weg, Datei auf der NAS bleibt
        c.delete(f"/api/v1/tabs/{hausrat}/docs/{doc['id']}")
        assert c.get(f"/api/v1/docs/{doc['id']}").status_code == 404
        assert (nas / "Dokumente" / "Versicherung" / "HUK_Kfz_2026.pdf").read_bytes() == PDF
        conn = connect()
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 2  # Schlagworte bleiben bis zum Aufräumen
        conn.close()
        log = c.get("/api/v1/audit?category=dokumente").json()["entries"]
        assert {"Dokument abgelegt", "Aus Ordner entfernt", "Dokument gelöscht"} <= {e["label"] for e in log}


def test_paths_are_safe(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        setup_shelf(c)
        for bad in ("../Privat", "Versicherung/../../Privat", "@eaDir", "#recycle", "/etc/passwd"):
            r = c.get("/api/v1/browse", params={"share": "Dokumente", "path": bad})
            assert r.status_code in (400, 404), bad
        # Link, der aus der Freigabe hinausführt
        r = c.get("/api/v1/browse/file", params={"share": "Dokumente", "path": "link.txt"})
        assert r.status_code == 400
        # ausgeschaltete Freigabe
        assert c.get("/api/v1/browse", params={"share": "Scans", "path": ""}).status_code == 403
        assert c.patch("/api/v1/admin/shares/Gibtsnicht", json={"enabled": True}).status_code == 404


def test_dsm_rights(nas):
    """DSM-Benutzer sehen nur Dokumente aus Freigaben, die sie in DSM lesen dürfen."""
    with TestClient(app, client=("192.168.1.10", 1)) as admin:
        login(admin)
        shelf, binder = setup_shelf(admin)
        kfz = binder["tabs"][0]["id"]
        admin.post(f"/api/v1/tabs/{kfz}/docs", json={"files": [{"share": "Dokumente", "path": "Versicherung/HUK_Kfz_2026.pdf"},
                                                                {"share": "Privat", "path": "Gehalt.pdf", "title": "Gehalt"}]})
        assert admin.post("/api/v1/users", json={"username": "anna", "display_name": "Anna"}).status_code == 201
        anna_id = next(u["id"] for u in admin.get("/api/v1/users").json() if u["username"] == "anna")
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        # noch kein Recht am Regal
        h = sso("anna", [["Dokumente", False]])
        assert c.get("/api/v1/me", headers=h).status_code == 200
        assert c.get("/api/v1/shelves", headers=h).json()["shelves"] == []
        assert c.get(f"/api/v1/shelves/{shelf['id']}", headers=h).status_code == 404
    with TestClient(app, client=("192.168.1.10", 1)) as admin:
        login(admin)
        r = admin.put(f"/api/v1/shelves/{shelf['id']}/access", json={"all_read": False, "users": [{"user_id": anna_id, "can_edit": False}]})
        assert next(u for u in r.json()["users"] if u["username"] == "anna")["right"] == "read"
        # eigene Freigaben-Zuordnung geht bei DSM-Benutzern nicht
        assert admin.put(f"/api/v1/users/{anna_id}/shares", json={"shares": {"Privat": True}}).status_code == 400
        assert admin.get(f"/api/v1/users/{anna_id}/shares").json()["mode"] == "dsm"
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        h = sso("anna")  # ohne neue Freigabe-Liste: gespeicherter Stand gilt
        assert [s["name"] for s in c.get("/api/v1/shares", headers=h).json()] == ["Dokumente"]
        b = c.get(f"/api/v1/binders/{binder['id']}", headers=h).json()
        assert [d["title"] for d in b["tabs"][0]["docs"]] == ["HUK Kfz 2026"] and b["tabs"][0]["hidden"] == 1
        sh = c.get(f"/api/v1/shelves/{shelf['id']}", headers=h).json()
        assert sh["right"] == "read" and sh["boards"][0]["binders"][0]["hidden"] == 1
        gehalt = next(d for t in admin_binder(binder["id"]) for d in t["docs"] if d["title"] == "Gehalt")
        assert c.get(f"/api/v1/docs/{gehalt['id']}/file", headers=h).status_code == 404
        assert c.get("/api/v1/browse", params={"share": "Privat"}, headers=h).status_code == 403
        assert c.get("/api/v1/search?q=Gehalt", headers=h).json()["results"] == []
        assert len(c.get("/api/v1/search?q=kfz", headers=h).json()["results"]) == 1
        # nur ansehen → nichts ablegen, nichts umstellen
        r = c.post(f"/api/v1/tabs/{kfz}/docs", headers=h, json={"files": [{"share": "Dokumente", "path": "Versicherung/Rechnung.pdf"}]})
        assert r.status_code == 403
        assert c.post("/api/v1/binders", headers=h, json={"board_id": sh["boards"][0]["id"], "title": "X"}).status_code == 403
        # DSM nimmt Anna die Freigabe weg → beim nächsten Laden der App sieht sie nichts mehr
        h = sso("anna", [])
        c.get("/api/v1/me", headers=h)
        assert c.get(f"/api/v1/binders/{binder['id']}", headers=h).json()["tabs"][0]["docs"] == []


def admin_binder(binder_id):
    with TestClient(app, client=("192.168.1.10", 1)) as admin:
        login(admin)
        return admin.get(f"/api/v1/binders/{binder_id}").json()["tabs"]


def test_local_user_manual_shares_and_edit(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as admin:
        login(admin)
        shelf, binder = setup_shelf(admin)
        admin.post("/api/v1/users", json={"username": "praxis", "display_name": "Praxis", "password": "praxis-passwort"})
        uid = next(u["id"] for u in admin.get("/api/v1/users").json() if u["username"] == "praxis")
        assert admin.get(f"/api/v1/users/{uid}/shares").json()["mode"] == "manual"
        r = admin.put(f"/api/v1/users/{uid}/shares", json={"shares": {"dokumente": False, "Scans": True}}).json()
        assert [s["name"] for s in r["shares"] if s["read"]] == ["Dokumente"]  # Scans ist nicht eingeschaltet
        admin.put(f"/api/v1/shelves/{shelf['id']}/access", json={"users": [{"user_id": uid, "can_edit": True}]})
    with TestClient(app, client=("192.168.1.11", 1)) as c:
        login(c, "praxis", "praxis-passwort")
        kfz = binder["tabs"][0]["id"]
        assert c.post(f"/api/v1/tabs/{kfz}/docs", json={"files": [{"share": "Dokumente", "path": "Versicherung/Rechnung.pdf"}]}).json()["added"] == 1
        assert c.post(f"/api/v1/tabs/{kfz}/docs", json={"files": [{"share": "Privat", "path": "Gehalt.pdf"}]}).status_code == 403
        # Umstellen per Drag & Drop: Ordner ins untere Fach
        sh = c.get(f"/api/v1/shelves/{shelf['id']}").json()
        top, bottom = sh["boards"]
        lay = {"boards": [{"id": top["id"], "binders": []}, {"id": bottom["id"], "binders": [binder["id"]]}]}
        sh = c.put(f"/api/v1/shelves/{shelf['id']}/layout", json=lay).json()
        assert sh["boards"][1]["binders"][0]["id"] == binder["id"]
        assert c.put(f"/api/v1/shelves/{shelf['id']}/layout", json={"boards": [{"id": top["id"], "binders": [binder["id"], binder["id"]]}]}).status_code == 400
        # Regal anlegen/löschen nur Admins
        assert c.post("/api/v1/shelves", json={"name": "Meins"}).status_code == 403
        assert c.delete(f"/api/v1/shelves/{shelf['id']}").status_code == 403
        assert c.patch(f"/api/v1/binders/{binder['id']}", json={"color": "rot"}).status_code == 400
        assert c.patch(f"/api/v1/binders/{binder['id']}", json={"color": "#38A169", "width": 3}).json()["color"] == "#38a169"


def test_relink_after_move(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, binder = setup_shelf(c)
        kfz = binder["tabs"][0]["id"]
        c.post(f"/api/v1/tabs/{kfz}/docs", json={"files": [{"share": "Dokumente", "path": "Versicherung/HUK_Kfz_2026.pdf"}]})
        (nas / "Privat" / "Archiv").mkdir()
        (nas / "Dokumente" / "Versicherung" / "HUK_Kfz_2026.pdf").rename(nas / "Privat" / "Archiv" / "HUK umbenannt.pdf")
        doc = c.get(f"/api/v1/binders/{binder['id']}").json()["tabs"][0]["docs"][0]
        assert c.get(f"/api/v1/docs/{doc['id']}/file").status_code == 404
        assert c.get(f"/api/v1/binders/{binder['id']}").json()["tabs"][0]["docs"][0]["missing"]
        r = c.post("/api/v1/admin/filecheck").json()
        assert r["relinked"] == 1 and r["missing"] == 0
        d = c.get(f"/api/v1/docs/{doc['id']}").json()
        assert d["share"] == "Privat" and d["path"] == "Archiv/HUK umbenannt.pdf" and not d["missing"]
        assert c.get(f"/api/v1/docs/{doc['id']}/file").content == PDF
        # gelöscht → bleibt als „fehlt“ markiert
        (nas / "Privat" / "Archiv" / "HUK umbenannt.pdf").unlink()
        conn = connect()
        assert filecheck.check(conn) == {"ok": 0, "missing": 1, "relinked": 0}
        conn.close()


def test_search_tags_and_shelf_delete(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, binder = setup_shelf(c)
        kfz = binder["tabs"][0]["id"]
        r = c.post(f"/api/v1/tabs/{kfz}/docs", json={"files": [{"share": "Dokumente", "path": "Versicherung/Rechnung.pdf"}], "tags": ["Rechnung"]})
        doc_id = r.json()["binder"]["tabs"][0]["docs"][0]["id"]
        c.patch(f"/api/v1/docs/{doc_id}", json={"title": "Beitragsrechnung 2026", "notes": "per Lastschrift", "tags": ["Rechnung", "HUK"]})
        assert [x["title"] for x in c.get("/api/v1/search?q=lastschrift").json()["results"]] == ["Beitragsrechnung 2026"]
        assert c.get("/api/v1/search?q=huk rechnung").json()["results"][0]["places"][0]["binder"] == "Versicherungen"
        tags = c.get("/api/v1/tags").json()
        assert {t["name"] for t in tags} == {"Rechnung", "HUK"}
        huk = next(t for t in tags if t["name"] == "HUK")
        assert len(c.get(f"/api/v1/search?tag={huk['id']}").json()["results"]) == 1
        assert c.delete(f"/api/v1/shelves/{shelf['id']}").status_code == 200
        conn = connect()
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        conn.close()
        assert (nas / "Dokumente" / "Versicherung" / "Rechnung.pdf").exists()


def test_parse_share_list():
    body = {"success": True, "data": {"shares": [
        {"name": "Dokumente", "additional": {"perm": {"share_right": "RW"}}},
        {"name": "Fotos", "additional": {"perm": {"share_right": "RO"}}},
        {"name": "Gesperrt", "additional": {"perm": {"share_right": "NA"}}},
        {"name": "Alt", "additional": {"perm": {"acl": {"read": True, "write": True}}}},
        {"kaputt": 1}]}}
    assert dsm_auth.parse_share_list(body) == [("Dokumente", True), ("Fotos", False), ("Alt", True)]
    assert dsm_auth.parse_share_list({"success": False}) is None


def test_dsm_password_login_takes_shares(nas, monkeypatch):
    def fake_check(conn, username, password, otp="", shares_out=None):
        if password != "dsm-pw":
            return "wrong"
        if shares_out is not None:
            shares_out["shares"] = [("Privat", True)]
        return "ok"
    monkeypatch.setattr(auth_router, "dsm_password_check", fake_check)
    with TestClient(app, client=("192.168.1.10", 1)) as admin:
        login(admin)
        setup_shelf(admin)
        admin.post("/api/v1/users", json={"username": "ben", "display_name": "Ben"})
    with TestClient(app, client=("192.168.1.12", 1)) as c:
        login(c, "ben", "dsm-pw")
        assert c.get("/api/v1/shares").json() == [{"name": "Privat", "can_write": True}]


def test_dsm_proxy_fetches_shares(monkeypatch):
    """app.cgi fragt DSM mit der Sitzung des Benutzers nach seinen Freigaben (hier: nachgestelltes DSM)."""
    import importlib.util
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from pathlib import Path

    seen = {}

    class FakeDSM(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["path"], seen["cookie"], seen["token"] = self.path, self.headers.get("Cookie"), self.headers.get("X-SYNO-TOKEN")
            body = json.dumps({"success": True, "data": {"shares": [
                {"name": "Dokumente", "additional": {"perm": {"share_right": "RO"}}},
                {"name": "Geheim", "additional": {"perm": {"share_right": "NA"}}}]}}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), FakeDSM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    spec = importlib.util.spec_from_file_location("dsm_proxy", Path(__file__).parent.parent / "synology" / "proxy" / "dsm_proxy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DSM_ENDPOINTS", (("http", srv.server_port),))
    monkeypatch.setenv("HTTP_COOKIE", "id=abc")
    try:
        assert json.loads(mod.dsm_shares("tok")) == [["Dokumente", False]]
        assert "list_share" in seen["path"] and seen["cookie"] == "id=abc" and seen["token"] == "tok"
        monkeypatch.delenv("HTTP_COOKIE")
        assert mod.dsm_shares("tok") is None  # ohne DSM-Sitzung nichts
    finally:
        srv.shutdown()
