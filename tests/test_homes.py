"""0.3.1: Freigabe „homes“ – wie in DSM sieht jeder nur seinen eigenen Ordner; Eingang im eigenen Home."""
import pytest
from fastapi.testclient import TestClient

from app import dsm_auth, shares
from app.db import connect
from app.main import app

from .fresh import fresh_db
from .test_regal import PDF, login, sso


@pytest.fixture()
def nas(tmp_path, monkeypatch):
    vol = tmp_path / "volume1"
    for u in ("anna", "ben"):
        (vol / "homes" / u / "Scans").mkdir(parents=True)
        (vol / "homes" / u / "Scans" / f"scan_{u}.pdf").write_bytes(PDF + u.encode())
        (vol / "homes" / u / "Dokumente").mkdir()
    (vol / "Dokumente").mkdir()
    monkeypatch.setenv("ORG_SHARE_ROOTS", str(vol))
    monkeypatch.setattr(dsm_auth, "_is_dsm_admin", lambda u: False)
    dsm_auth._admin_cache.clear()
    data = tmp_path / "data"
    data.mkdir()
    with fresh_db(data):
        yield vol


def test_homes_own_folder_only(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as admin:
        login(admin)
        for n in ("homes", "Dokumente"):
            assert admin.patch(f"/api/v1/admin/shares/{n}", json={"enabled": True}).status_code == 200
        for u in ("anna", "ben"):
            admin.post("/api/v1/users", json={"username": u, "display_name": u.title()})
            admin.post("/api/v1/admin/inbox-sources", json={"share": "homes", "path": f"{u}/Scans", "name": f"Scanner {u}"})
        shelf = admin.post("/api/v1/shelves", json={"name": "Privat", "all_read": True}).json()
        ids = {u["username"]: u["id"] for u in admin.get("/api/v1/users").json()}
        admin.put(f"/api/v1/shelves/{shelf['id']}/access", json={"all_read": True, "users": [
            {"user_id": ids["anna"], "can_edit": True}, {"user_id": ids["ben"], "can_edit": True}]})
        b = admin.post("/api/v1/binders", json={"board_id": shelf["boards"][0]["id"], "title": "Post"}).json()
        tab = b["tabs"][0]["id"]
        # Admin (eigenes Konto, Vollzugriff) sieht alle Homes
        assert {d["name"] for d in admin.get("/api/v1/browse", params={"share": "homes"}).json()["dirs"]} == {"anna", "ben"}
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        # Anna: DSM meldet nur „home“ (ihren eigenen Ordner) + Dokumente
        h = sso("anna", [["home", True], ["Dokumente", True]])
        c.get("/api/v1/me", headers=h)
        assert {s["name"]: s["home"] for s in c.get("/api/v1/shares", headers=h).json()} == {"Dokumente": None, "homes": "anna"}
        root = c.get("/api/v1/browse", params={"share": "homes"}, headers=h).json()
        assert [d["name"] for d in root["dirs"]] == ["anna"] and root["files"] == []
        assert c.get("/api/v1/browse", params={"share": "homes", "path": "ben"}, headers=h).status_code == 403
        assert c.get("/api/v1/browse/file", params={"share": "homes", "path": "ben/Scans/scan_ben.pdf"}, headers=h).status_code == 403
        # Eingang: nur ihr eigener
        r = c.get("/api/v1/inbox", headers=h).json()
        assert [s["name"] for s in r["sources"]] == ["Scanner anna"] and [i["name"] for i in r["items"]] == ["scan_anna.pdf"]
        assert r["items"][0]["can_move"]
        # Einsortieren in ihren Home-Ordner Dokumente
        res = c.post("/api/v1/inbox/file", headers=h, json={"share": "homes", "path": "anna/Scans/scan_anna.pdf", "tab_id": tab,
                                                            "title": "Brief", "target": {"share": "homes", "path": "anna/Dokumente"}})
        assert res.status_code == 200, res.text
        assert (nas / "homes" / "anna" / "Dokumente" / "scan_anna.pdf").exists()
        # in Bens Ordner darf sie nichts verschieben oder anlegen
        assert c.post("/api/v1/browse/mkdir", headers=h, json={"share": "homes", "path": "ben", "name": "x"}).status_code == 403
        assert c.post("/api/v1/browse/mkdir", headers=h, json={"share": "homes", "path": "", "name": "x"}).status_code == 403
        assert c.post("/api/v1/browse/mkdir", headers=h, json={"share": "homes", "path": "anna", "name": "Neu"}).status_code == 200
        doc_id = res.json()["doc_id"]
        # Ben sieht Annas Dokument im (gemeinsamen) Regal nicht – nur „+1 ausgeblendet“
        hb = sso("ben", [["home", True]])
        c.get("/api/v1/me", headers=hb)
        bt = c.get(f"/api/v1/binders/{b['id']}", headers=hb).json()["tabs"][0]
        assert bt["docs"] == [] and bt["hidden"] == 1
        assert c.get(f"/api/v1/docs/{doc_id}/file", headers=hb).status_code == 404
        assert c.get("/api/v1/search", params={"q": "Brief"}, headers=hb).json()["results"] == []
        assert [s["name"] for s in c.get("/api/v1/inbox", headers=hb).json()["sources"]] == ["Scanner ben"]
        # Anna sieht es
        assert c.get(f"/api/v1/docs/{doc_id}/file", headers=h).status_code == 200
        # DSM-Admin mit ganzer Freigabe „homes“ sieht alles
        hx = sso("ben", [["homes", True]])
        c.get("/api/v1/me", headers=hx)
        assert c.get(f"/api/v1/docs/{doc_id}/file", headers=hx).status_code == 200


def test_allowed_helpers(nas):
    assert shares.in_own_home("anna/x", "Anna") and not shares.in_own_home("annabell/x", "anna") and not shares.in_own_home("", "anna")
    conn = connect()
    conn.close()
