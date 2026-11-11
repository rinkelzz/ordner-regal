"""0.2: Scanner-Eingang – Eingänge, Einsortieren mit Verschieben in den Zielordner, Rechte, Dateinamen."""
import os

import pytest
from fastapi.testclient import TestClient

from app import dsm_auth, inbox
from app.db import connect
from app.main import app

from .fresh import fresh_db
from .test_regal import PDF, login, sso


@pytest.fixture()
def nas(tmp_path, monkeypatch):
    vol = tmp_path / "volume1"
    (vol / "Scans" / "Eingang").mkdir(parents=True)
    (vol / "Scans" / "Eingang" / "scan_2026-11-10_0815.pdf").write_bytes(PDF)
    (vol / "Scans" / "Eingang" / "scan_0816.pdf").write_bytes(PDF + b"2")
    (vol / "Scans" / "Eingang" / "foto.jpg").write_bytes(b"\xff\xd8\xff")
    (vol / "Scans" / "Eingang" / "@eaDir").mkdir()
    (vol / "Scans" / "Eingang" / "Unterordner").mkdir()
    (vol / "Scans" / "Eingang" / "Unterordner" / "tief.pdf").write_bytes(PDF)
    (vol / "Dokumente" / "Versicherung" / "Kfz").mkdir(parents=True)
    (vol / "Dokumente" / "Versicherung" / "Kfz" / "2025-11-14 Versicherungsschein.pdf").write_bytes(b"alt")
    monkeypatch.setenv("ORG_SHARE_ROOTS", str(vol))
    monkeypatch.setattr(dsm_auth, "_is_dsm_admin", lambda u: False)
    dsm_auth._admin_cache.clear()
    data = tmp_path / "data"
    data.mkdir()
    with fresh_db(data):
        yield vol


def setup(c):
    for n in ("Scans", "Dokumente"):
        assert c.patch(f"/api/v1/admin/shares/{n}", json={"enabled": True}).status_code == 200
    src = c.post("/api/v1/admin/inbox-sources", json={"share": "scans", "path": "Eingang", "name": "Scanner"})
    assert src.status_code == 201 and src.json()[0]["share"] == "Scans" and src.json()[0]["ok"]
    assert c.post("/api/v1/admin/inbox-sources", json={"share": "Scans", "path": "/Eingang/"}).status_code == 400  # doppelt
    assert c.post("/api/v1/admin/inbox-sources", json={"share": "Scans", "path": "../Dokumente"}).status_code == 400
    assert c.post("/api/v1/admin/inbox-sources", json={"share": "Scans", "path": "gibtsnicht"}).status_code == 400
    shelf = c.post("/api/v1/shelves", json={"name": "Privat"}).json()
    b = c.post("/api/v1/binders", json={"board_id": shelf["boards"][0]["id"], "title": "Versicherungen", "tabs": ["Kfz", "Hausrat"],
                                        "target_share": "Dokumente", "target_path": "Versicherung"}).json()
    assert b["target"] == {"share": "Dokumente", "path": "Versicherung"}
    return shelf, b


def test_move_into_target(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, b = setup(c)
        r = c.get("/api/v1/inbox").json()
        assert {i["name"] for i in r["items"]} == {"scan_2026-11-10_0815.pdf", "scan_0816.pdf", "foto.jpg"}  # nur oberste Ebene
        assert c.get("/api/v1/inbox/count").json() == {"count": 3}
        sug = c.get("/api/v1/inbox/suggest", params={"share": "Scans", "path": "Eingang/scan_2026-11-10_0815.pdf"}).json()
        assert sug["doc_date"] == "2026-11-10" and sug["ext"] == ".pdf"
        # Register „Kfz“ bekommt einen eigenen Zielordner
        kfz = b["tabs"][0]["id"]
        t = c.patch(f"/api/v1/tabs/{kfz}", json={"target_share": "Dokumente", "target_path": "Versicherung/Kfz"}).json()
        assert t["tabs"][0]["target"]["path"] == "Versicherung/Kfz"
        # Name gibt es am Ziel schon → „(2)“, nie überschreiben
        r = c.post("/api/v1/inbox/file", json={"share": "Scans", "path": "Eingang/scan_2026-11-10_0815.pdf", "tab_id": kfz,
                                                "title": "Versicherungsschein 2026", "doc_date": "2025-11-14", "tags": ["Kfz"],
                                                "filename": "2025-11-14 Versicherungsschein"})
        assert r.status_code == 200, r.text
        r = r.json()
        assert r["moved"] and r["path"] == "Versicherung/Kfz/2025-11-14 Versicherungsschein (2).pdf" and r["count"] == 2
        assert (nas / "Dokumente" / "Versicherung" / "Kfz" / "2025-11-14 Versicherungsschein.pdf").read_bytes() == b"alt"
        assert (nas / "Dokumente" / "Versicherung" / "Kfz" / "2025-11-14 Versicherungsschein (2).pdf").read_bytes() == PDF
        assert not (nas / "Scans" / "Eingang" / "scan_2026-11-10_0815.pdf").exists()
        doc = c.get(f"/api/v1/docs/{r['doc_id']}").json()
        assert doc["title"] == "Versicherungsschein 2026" and doc["share"] == "Dokumente" and doc["tags"][0]["name"] == "Kfz"
        # Hausrat hat keinen eigenen Zielordner → der des Ordners
        r = c.post("/api/v1/inbox/file", json={"share": "Scans", "path": "Eingang/scan_0816.pdf", "tab_id": b["tabs"][1]["id"]}).json()
        assert r["path"] == "Versicherung/scan_0816.pdf"
        # nur verknüpfen: Datei bleibt im Scan-Ordner, verschwindet aber aus dem Eingang
        r = c.post("/api/v1/inbox/file", json={"share": "Scans", "path": "Eingang/foto.jpg", "tab_id": kfz, "mode": "link"}).json()
        assert not r["moved"] and r["count"] == 0 and (nas / "Scans" / "Eingang" / "foto.jpg").exists()
        assert c.post("/api/v1/inbox/file", json={"share": "Scans", "path": "Eingang/foto.jpg", "tab_id": kfz}).status_code == 409
        log = c.get("/api/v1/audit?category=dokumente").json()["entries"]
        assert sum(e["label"] == "Aus dem Eingang einsortiert" for e in log) == 3


def test_no_target_and_remember(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, _ = setup(c)
        g = c.post("/api/v1/binders", json={"board_id": shelf["boards"][1]["id"], "title": "Garantien"}).json()
        tab = g["tabs"][0]["id"]
        r = c.post("/api/v1/inbox/file", json={"share": "Scans", "path": "Eingang/foto.jpg", "tab_id": tab})
        assert r.status_code == 409 and r.json()["detail"] == "no_target"
        # neuen Unterordner anlegen und als Ziel merken
        d = c.post("/api/v1/browse/mkdir", json={"share": "Dokumente", "path": "", "name": "Garantien"}).json()
        assert d == {"share": "Dokumente", "path": "Garantien"}
        assert c.post("/api/v1/browse/mkdir", json={"share": "Dokumente", "path": "", "name": "@eaDir"}).status_code == 400
        assert c.post("/api/v1/browse/mkdir", json={"share": "Dokumente", "path": "../Scans", "name": "x"}).status_code == 400
        r = c.post("/api/v1/inbox/file", json={"share": "Scans", "path": "Eingang/foto.jpg", "tab_id": tab, "filename": "Kassenbon",
                                                "target": {"share": "Dokumente", "path": "Garantien", "remember": True}}).json()
        assert r["path"] == "Garantien/Kassenbon.jpg"  # Endung bleibt
        assert c.get(f"/api/v1/binders/{g['id']}").json()["target"] == {"share": "Dokumente", "path": "Garantien"}


def test_paths_and_rights(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as admin:
        login(admin)
        shelf, b = setup(admin)
        kfz = b["tabs"][0]["id"]
        # nur Dateien direkt im Eingang
        for bad in ("Eingang/Unterordner/tief.pdf", "Eingang/../Eingang/foto.jpg", "Eingang/@eaDir/x", "Anderswo/foto.jpg"):
            r = admin.post("/api/v1/inbox/file", json={"share": "Scans", "path": bad, "tab_id": kfz})
            assert r.status_code in (400, 404), bad
        # Ziel außerhalb einer eingeschalteten Freigabe
        assert admin.patch(f"/api/v1/tabs/{kfz}", json={"target_share": "Scans", "target_path": "../Dokumente"}).status_code == 400
        assert admin.post("/api/v1/users", json={"username": "anna", "display_name": "Anna"}).status_code == 201
        anna = next(u["id"] for u in admin.get("/api/v1/users").json() if u["username"] == "anna")
        admin.put(f"/api/v1/shelves/{shelf['id']}/access", json={"users": [{"user_id": anna, "can_edit": True}]})
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        # Anna darf Scans nur lesen → sieht den Eingang, darf aber nicht verschieben (verknüpfen geht)
        h = sso("anna", [["Scans", False], ["Dokumente", True]])
        c.get("/api/v1/me", headers=h)
        items = c.get("/api/v1/inbox", headers=h).json()["items"]
        assert items and not any(i["can_move"] for i in items)
        r = c.post("/api/v1/inbox/file", headers=h, json={"share": "Scans", "path": "Eingang/scan_0816.pdf", "tab_id": kfz})
        assert r.status_code == 403 and "nicht schreiben" in r.json()["detail"]
        assert c.post("/api/v1/browse/mkdir", headers=h, json={"share": "Scans", "path": "", "name": "x"}).status_code == 403
        # ohne Recht auf Scans sieht sie den Eingang gar nicht
        h = sso("anna", [["Dokumente", True]])
        c.get("/api/v1/me", headers=h)
        assert c.get("/api/v1/inbox", headers=h).json() == {"sources": [], "items": []}
        assert c.post("/api/v1/inbox/file", headers=h, json={"share": "Scans", "path": "Eingang/scan_0816.pdf",
                                                             "tab_id": kfz, "mode": "link"}).status_code == 404


def test_clean_filename_and_date():
    n = inbox.clean_filename("../../etc/passwd", "a.pdf")
    assert "/" not in n and not n.startswith(".") and n.endswith(".pdf")
    assert "/" not in inbox.clean_filename("a/b\\c:d", "x.pdf") and inbox.clean_filename("a/b", "x.pdf").endswith(".pdf")
    assert inbox.clean_filename("", "scan.pdf") == "scan.pdf"
    assert inbox.clean_filename("Rechnung.PDF", "scan.pdf") == "Rechnung.PDF"
    with pytest.raises(Exception):
        inbox.clean_filename("@eaDir", "x")
    assert inbox.date_from_name("scan_20251114_0815.pdf", 0) == "2025-11-14"
    assert inbox.suggest_title("scan_2026-11-10_0815.pdf") == "" and inbox.suggest_title("IMG_2210.jpg") == ""
    assert inbox.suggest_title("Rechnung_Mediamarkt_2025-10-03.pdf") == "Rechnung Mediamarkt"
    assert inbox.date_from_name("IMG_2025-13-40.jpg", 86400 * 365)  # ungültig → Dateidatum


def test_move_across_volumes(tmp_path, monkeypatch):
    """Fallback ohne harte Links (anderes Volume): kopieren + löschen, ohne zu überschreiben."""
    src = tmp_path / "a.pdf"
    src.write_bytes(PDF)
    (tmp_path / "ziel").mkdir()
    (tmp_path / "ziel" / "a.pdf").write_bytes(b"alt")

    def nolink(a, b):
        raise OSError(18, "Invalid cross-device link")
    monkeypatch.setattr(os, "link", nolink)
    dst = inbox.move_file(src, tmp_path / "ziel", "a.pdf")
    assert dst.name == "a (2).pdf" and dst.read_bytes() == PDF and not src.exists()
    assert (tmp_path / "ziel" / "a.pdf").read_bytes() == b"alt"


def test_migration_from_01(tmp_path):
    """Eine Datenbank aus 0.1 (Schema 2) kommt ohne Datenverlust auf Schema 3."""
    import sqlite3
    from app import db as dbmod
    from app.config import settings
    old = (settings.data_dir, settings.db_path)
    settings.data_dir, settings.db_path = tmp_path, tmp_path / "alt.db"
    try:
        conn = sqlite3.connect(str(settings.db_path))
        conn.row_factory = sqlite3.Row
        for v in (1, 2):
            dbmod.MIGRATIONS[v](conn)
        conn.execute("PRAGMA user_version = 2")
        conn.execute("INSERT INTO shelves(name, created_at) VALUES ('Alt', 'x')")
        conn.commit()
        conn.close()
        dbmod.init_db()
        c = connect()
        assert c.execute("PRAGMA user_version").fetchone()[0] == dbmod.SCHEMA_VERSION == 3
        assert c.execute("SELECT name FROM shelves").fetchone()["name"] == "Alt"
        assert "target_share" in {r[1] for r in c.execute("PRAGMA table_info(binders)")}
        c.close()
    finally:
        settings.data_dir, settings.db_path = old
