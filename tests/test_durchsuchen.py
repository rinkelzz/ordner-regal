"""0.5: Freigaben durchsuchen (Zahlen pro Freigabe) und NAS-Ordner übernehmen/abgleichen."""
import pytest
from fastapi.testclient import TestClient

from app import dsm_auth, folderimport, fulltext, sharescan
from app.db import connect
from app.main import app

from .fresh import fresh_db
from .test_regal import PDF, login, sso


@pytest.fixture()
def nas(tmp_path, monkeypatch):
    vol = tmp_path / "volume1"
    d = vol / "homes" / "tim.rinkel" / "Drive" / "Arbeit"
    (d / "Rechnungen" / "2025").mkdir(parents=True)
    (d / "Verträge").mkdir()
    (d / "Leer").mkdir()
    (d / "Notiz.txt").write_text("Oberste Ebene")
    (d / "Rechnungen" / "R1.pdf").write_bytes(PDF)
    (d / "Rechnungen" / "2025" / "R2.pdf").write_bytes(PDF + b"2")
    (d / "Verträge" / "Miete.pdf").write_bytes(PDF + b"m")
    (d / "Verträge" / "@eaDir").mkdir()
    (d / "Verträge" / "@eaDir" / "thumb.jpg").write_bytes(b"x")
    (vol / "homes" / "anna" / "geheim.pdf").parent.mkdir(parents=True)
    (vol / "homes" / "anna" / "geheim.pdf").write_bytes(PDF)
    (vol / "Dokumente").mkdir()
    (vol / "Dokumente" / "a.pdf").write_bytes(PDF)
    monkeypatch.setenv("ORG_SHARE_ROOTS", str(vol))
    monkeypatch.setattr(dsm_auth, "_is_dsm_admin", lambda u: False)
    dsm_auth._admin_cache.clear()
    data = tmp_path / "data"
    data.mkdir()
    with fresh_db(data):
        yield vol


def test_scan_counts(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        for n in ("homes", "Dokumente"):
            c.patch(f"/api/v1/admin/shares/{n}", json={"enabled": True})
        r = c.post("/api/v1/admin/shares/scan").json()
        assert "check" in r
        sharescan.wait()
        sh = {s["name"]: s for s in c.get("/api/v1/admin/shares").json()["shares"]}
        assert sh["homes"]["scan_files"] == 5 and sh["homes"]["scan_unfiled"] == 5  # ohne @eaDir
        assert sh["Dokumente"]["scan_files"] == 1 and sh["Dokumente"]["scan_at"]
        assert sh["backups"]["scan_files"] is None if "backups" in sh else True
        # nach dem Ablegen weniger „noch nicht im Regal“
        shelf = c.post("/api/v1/shelves", json={"name": "Privat"}).json()
        b = c.post("/api/v1/binders", json={"board_id": shelf["boards"][0]["id"], "title": "X"}).json()
        c.post(f"/api/v1/tabs/{b['tabs'][0]['id']}/docs", json={"files": [{"share": "Dokumente", "path": "a.pdf"}]})
        c.post("/api/v1/admin/shares/scan")
        sharescan.wait()
        sh = {s["name"]: s for s in c.get("/api/v1/admin/shares").json()["shares"]}
        assert sh["Dokumente"]["scan_unfiled"] == 0


def test_import_and_sync(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        c.patch("/api/v1/admin/shares/homes", json={"enabled": True})
        shelf = c.post("/api/v1/shelves", json={"name": "Arbeit"}).json()
        c.post("/api/v1/users", json={"username": "tim.rinkel", "display_name": "Tim"})
        tim = next(u["id"] for u in c.get("/api/v1/users").json() if u["username"] == "tim.rinkel")
        c.put(f"/api/v1/shelves/{shelf['id']}/access", json={"users": [{"user_id": tim, "can_edit": True}]})
        board = shelf["boards"][0]["id"]
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        h = sso("tim.rinkel", [["home", True]])
        c.get("/api/v1/me", headers=h)
        # fremdes Home: verboten
        assert c.post("/api/v1/binders/import", headers=h, json={"board_id": board, "share": "homes", "path": "anna"}).status_code == 403
        r = c.post("/api/v1/binders/import", headers=h, json={"board_id": board, "share": "homes", "path": "tim.rinkel/Drive/Arbeit"})
        assert r.status_code == 201, r.text
        r = r.json()
        b = r["binder"]
        assert r["added"] == 4 and b["title"] == "Arbeit" and b["source"]["path"] == "tim.rinkel/Drive/Arbeit" and b["source"]["sync"]
        tabs = {t["name"]: sorted(d["filename"] for d in t["docs"]) for t in b["tabs"]}
        assert list(tabs) == ["Allgemein", "Leer", "Rechnungen", "Verträge"]
        assert tabs == {"Allgemein": ["Notiz.txt"], "Leer": [], "Rechnungen": ["R1.pdf", "R2.pdf"], "Verträge": ["Miete.pdf"]}
        # neue Datei + neuer Unterordner → Abgleich
        d = nas / "homes" / "tim.rinkel" / "Drive" / "Arbeit"
        (d / "Steuer").mkdir()
        (d / "Steuer" / "Bescheid.pdf").write_bytes(PDF + b"s")
        (d / "Verträge" / "Strom.pdf").write_bytes(PDF + b"x")
        r = c.post(f"/api/v1/binders/{b['id']}/sync", headers=h).json()
        assert r["added"] == 2 and r["new_tabs"] == 1
        assert [t["name"] for t in r["binder"]["tabs"]] == ["Allgemein", "Leer", "Rechnungen", "Verträge", "Steuer"]  # neue hinten
        # von Hand umsortiert bleibt, wo es ist; nichts doppelt
        steuer = next(t for t in r["binder"]["tabs"] if t["name"] == "Steuer")
        allg = next(t for t in r["binder"]["tabs"] if t["name"] == "Allgemein")
        doc = steuer["docs"][0]["id"]
        c.post(f"/api/v1/docs/{doc}/move", headers=h, json={"from_tab": steuer["id"], "to_tab": allg["id"]})
        r = c.post(f"/api/v1/binders/{b['id']}/sync", headers=h).json()
        assert r["added"] == 0 and len(next(t for t in r["binder"]["tabs"] if t["name"] == "Allgemein")["docs"]) == 2
        # nachts: mit Tims Rechten
        (d / "Neu.pdf").write_bytes(PDF + b"n")
        conn = connect()
        res = folderimport.sync_all(conn)
        assert res[b["id"]]["added"] == 1
        # Fingerabdruck wird im Hintergrund nachgetragen
        assert conn.execute("SELECT COUNT(*) FROM files WHERE sha256 IS NULL").fetchone()[0] == 7
        fulltext.run_all(conn)
        assert conn.execute("SELECT COUNT(*) FROM files WHERE sha256 IS NULL").fetchone()[0] == 0
        conn.close()
        # Abgleich aus, Verknüpfung lösen
        assert not c.patch(f"/api/v1/binders/{b['id']}/source", headers=h, json={"sync": False}).json()["source"]["sync"]
        assert c.patch(f"/api/v1/binders/{b['id']}/source", headers=h, json={"unlink": True}).json()["source"] is None
        assert c.post(f"/api/v1/binders/{b['id']}/sync", headers=h).status_code == 400


def test_import_needs_edit_right(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        c.patch("/api/v1/admin/shares/homes", json={"enabled": True})
        shelf = c.post("/api/v1/shelves", json={"name": "Arbeit", "all_read": True}).json()
        c.post("/api/v1/users", json={"username": "tim.rinkel", "display_name": "Tim"})
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        h = sso("tim.rinkel", [["home", True]])
        c.get("/api/v1/me", headers=h)
        r = c.post("/api/v1/binders/import", headers=h, json={"board_id": shelf["boards"][0]["id"], "share": "homes", "path": "tim.rinkel/Drive"})
        assert r.status_code == 403


def test_read_target_folder(nas):
    """Ordner von Hand angelegt + Zielordner gesetzt → „Zielordner einlesen“ nutzt vorhandene Register und legt neue an."""
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        c.patch("/api/v1/admin/shares/homes", json={"enabled": True})
        shelf = c.post("/api/v1/shelves", json={"name": "Arbeit"}).json()
        b = c.post("/api/v1/binders", json={"board_id": shelf["boards"][0]["id"], "title": "Firma", "tabs": ["Verträge", "Sonstiges"],
                                            "target_share": "homes", "target_path": "tim.rinkel/Drive/Arbeit"}).json()
        tabs = {t["name"]: t["id"] for t in b["tabs"]}
        # Register „Sonstiges“ zeigt per eigenem Zielordner auf „Rechnungen“
        c.patch(f"/api/v1/tabs/{tabs['Sonstiges']}", json={"target_share": "homes", "target_path": "tim.rinkel/Drive/Arbeit/Rechnungen"})
        assert c.patch(f"/api/v1/binders/{b['id']}/source", json={"from_target": False}).status_code == 200
        r = c.patch(f"/api/v1/binders/{b['id']}/source", json={"from_target": True}).json()
        assert r["result"]["added"] == 4 and r["result"]["new_tabs"] == 2 and r["source"]["sync"]
        got = {t["name"]: sorted(d["filename"] for d in t["docs"]) for t in r["tabs"]}
        assert [t["name"] for t in r["tabs"]] == ["Allgemein", "Verträge", "Sonstiges", "Leer"]  # keine doppelten Register
        assert got == {"Allgemein": ["Notiz.txt"], "Verträge": ["Miete.pdf"], "Sonstiges": ["R1.pdf", "R2.pdf"], "Leer": []}
        # Ohne Zielordner geht es nicht
        b2 = c.post("/api/v1/binders", json={"board_id": shelf["boards"][0]["id"], "title": "Leer"}).json()
        assert c.patch(f"/api/v1/binders/{b2['id']}/source", json={"from_target": True}).status_code == 400
