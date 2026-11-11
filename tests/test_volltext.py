"""0.3: Volltextsuche – Text aus PDF, Office und Textdateien, Ausschnitte, Rechte, Eingang, Rückfall ohne FTS5."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import dsm_auth, fulltext, textextract
from app.db import connect
from app.main import app

from .fresh import fresh_db
from .test_regal import login, sso


def make_pdf(lines):
    """Kleine, gültige PDF mit Textebene (Helvetica)."""
    content = "".join("BT /F1 12 Tf 60 %d Td (%s) Tj ET\n" % (780 - 18 * i, l) for i, l in enumerate(lines))
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            "<< /Length %d >>\nstream\n%sendstream" % (len(content), content), "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += ("%d 0 obj\n%s\nendobj\n" % (i, o)).encode("latin-1")
    x = len(out)
    out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode() + b"".join(("%010d 00000 n \n" % o).encode() for o in offs)
    out += ("trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)).encode()
    return out


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


DOCX = make_zip({"word/document.xml": '<w:document><w:body><w:p><w:r><w:t>Mietvertrag Wohnung Schlehenweg</w:t></w:r></w:p>'
                                      '<w:p><w:r><w:t>Kaution drei Monatsmieten &amp; Nebenkosten</w:t></w:r></w:p></w:body></w:document>'})
XLSX = make_zip({"xl/sharedStrings.xml": "<sst><si><t>Haushaltsbuch</t></si><si><t>Stromabschlag Stadtwerke</t></si></sst>",
                 "xl/worksheets/sheet1.xml": "<worksheet><sheetData><row><c><v>42</v></c></row></sheetData></worksheet>"})
ODT = make_zip({"content.xml": "<office:document-content><text:p>Garantiekarte Waschmaschine Bosch</text:p></office:document-content>"})
# „Billion laughs“: darf nichts aufblähen – Entitäten werden nicht aufgelöst
EVIL = make_zip({"word/document.xml": '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;">]>'
                                      '<w:document><w:t>&b;&b;&b; Harmloser Rest</w:t></w:document>'})
SCAN = make_pdf([])  # Seite ohne Text = Bild-Scan


@pytest.fixture()
def nas(tmp_path, monkeypatch):
    vol = tmp_path / "volume1"
    d = vol / "Dokumente"
    d.mkdir(parents=True)
    (d / "HUK.pdf").write_bytes(make_pdf(["HUK-COBURG Versicherungsschein", "Vertragsnummer 4711-0815", "Kfz Haftpflicht Vollkasko"]))
    (d / "Mietvertrag.docx").write_bytes(DOCX)
    (d / "Haushalt.xlsx").write_bytes(XLSX)
    (d / "Garantie.odt").write_bytes(ODT)
    (d / "Notiz.txt").write_text("Zählerstand Strom 12345 kWh, abgelesen im Keller", encoding="utf-8")
    (d / "Scan.pdf").write_bytes(SCAN)
    (d / "Boese.docx").write_bytes(EVIL)
    (d / "Kaputt.pdf").write_bytes(b"%PDF-1.4 kaputt")
    (vol / "Privat").mkdir()
    (vol / "Privat" / "Gehalt.pdf").write_bytes(make_pdf(["Gehaltsabrechnung Oktober Vertragsnummer 4711"]))
    (vol / "Scans" / "Eingang").mkdir(parents=True)
    (vol / "Scans" / "Eingang" / "scan_0815.pdf").write_bytes(make_pdf(["Rechnung Mediamarkt Waschmaschine Garantie"]))
    monkeypatch.setenv("ORG_SHARE_ROOTS", str(vol))
    monkeypatch.setattr(dsm_auth, "_is_dsm_admin", lambda u: False)
    dsm_auth._admin_cache.clear()
    data = tmp_path / "data"
    data.mkdir()
    with fresh_db(data):
        yield vol


def test_extract(nas):
    d = nas / "Dokumente"
    assert textextract.extract(d / "HUK.pdf")[0] == "ok" and "Vertragsnummer 4711-0815" in textextract.extract(d / "HUK.pdf")[1]
    st, t = textextract.extract(d / "Mietvertrag.docx")
    assert st == "ok" and "Kaution drei Monatsmieten & Nebenkosten" in t and "Schlehenweg\n" in t + "\n"
    assert "Stromabschlag Stadtwerke" in textextract.extract(d / "Haushalt.xlsx")[1]
    assert "Waschmaschine Bosch" in textextract.extract(d / "Garantie.odt")[1]
    assert "Zählerstand" in textextract.extract(d / "Notiz.txt")[1]
    assert textextract.extract(d / "Scan.pdf") == ("empty", "")
    st, t = textextract.extract(d / "Boese.docx")
    assert st == "ok" and "Harmloser Rest" in t and len(t) < 200 and "aaaaaaaaaa" not in t
    assert textextract.extract(d / "Kaputt.pdf")[0] in ("error", "empty")
    assert textextract.extract(nas / "Privat")[0] == "unsupported"


def file_all(c):
    for n in ("Dokumente", "Privat", "Scans"):
        c.patch(f"/api/v1/admin/shares/{n}", json={"enabled": True})
    shelf = c.post("/api/v1/shelves", json={"name": "Privat"}).json()
    b = c.post("/api/v1/binders", json={"board_id": shelf["boards"][0]["id"], "title": "Alles"}).json()
    tab = b["tabs"][0]["id"]
    files = [{"share": "Dokumente", "path": n} for n in ("HUK.pdf", "Mietvertrag.docx", "Haushalt.xlsx", "Garantie.odt", "Notiz.txt", "Scan.pdf")]
    files.append({"share": "Privat", "path": "Gehalt.pdf"})
    assert c.post(f"/api/v1/tabs/{tab}/docs", json={"files": files}).json()["added"] == 7
    c.post("/api/v1/admin/inbox-sources", json={"share": "Scans", "path": "Eingang"})
    conn = connect()
    r = fulltext.run_all(conn)
    conn.close()
    assert r == {"files": 7, "inbox": 1}
    return shelf, b


@pytest.mark.parametrize("fts", [True, False])
def test_search(nas, monkeypatch, fts):
    if not fts:
        monkeypatch.setattr(fulltext, "has_fts", lambda conn, table="file_fts": False)
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, b = file_all(c)
        r = c.get("/api/v1/search", params={"q": "4711-0815"}).json()
        assert [x["title"] for x in r["results"]] == ["HUK"]
        assert "\x024711-0815\x03" in r["results"][0]["snippet"] or "\x024711" in r["results"][0]["snippet"]
        assert [x["title"] for x in c.get("/api/v1/search", params={"q": "kaution nebenkosten"}).json()["results"]] == ["Mietvertrag"]
        assert [x["title"] for x in c.get("/api/v1/search", params={"q": "stadtwerke"}).json()["results"]] == ["Haushalt"]
        if fts:  # Wortanfang und ohne Umlaut-Unterschied nur mit FTS5
            assert [x["title"] for x in c.get("/api/v1/search", params={"q": "Waschmasch"}).json()["results"]] == ["Garantie"]
        # Treffer im Eingang (Text) – nur mit Rechten auf den Eingang
        r = c.get("/api/v1/search", params={"q": "mediamarkt"}).json()
        assert r["results"] == [] and r["inbox"][0]["name"] == "scan_0815.pdf" and "\x02" in r["inbox"][0]["snippet"]
        # Status im Dokument
        doc = next(d for d in c.get(f"/api/v1/binders/{b['id']}").json()["tabs"][0]["docs"] if d["title"] == "Scan")
        assert doc["text_status"] == "empty"
        st = c.get("/api/v1/admin/fulltext").json()
        assert st["ok"] == 6 and st["empty"] == 1 and st["inbox"] == 1
        # Filter nach Regal
        assert c.get("/api/v1/search", params={"q": "vertragsnummer", "shelf_id": shelf["id"] + 99}).json()["results"] == []
        anna = c.post("/api/v1/users", json={"username": "anna", "display_name": "Anna"})
        assert anna.status_code == 201
        uid = next(u["id"] for u in c.get("/api/v1/users").json() if u["username"] == "anna")
        c.put(f"/api/v1/shelves/{shelf['id']}/access", json={"users": [{"user_id": uid, "can_edit": False}]})
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        h = sso("anna", [["Dokumente", False]])
        c.get("/api/v1/me", headers=h)
        r = c.get("/api/v1/search", params={"q": "vertragsnummer"}, headers=h).json()
        assert [x["title"] for x in r["results"]] == ["HUK"]  # Gehalt (Privat) bleibt verborgen
        assert c.get("/api/v1/search", params={"q": "mediamarkt"}, headers=h).json()["inbox"] == []  # kein Recht auf Scans


def test_change_reindex_and_inbox_takeover(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, b = file_all(c)
        # Datei ändert sich → wird neu gelesen
        import os
        p = nas / "Dokumente" / "Notiz.txt"
        p.write_text("Neuer Inhalt Wasseruhr 999", encoding="utf-8")
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))
        c.get(f"/api/v1/binders/{b['id']}")
        conn = connect()
        assert fulltext.index_pending(conn) == 1
        conn.close()
        assert [x["title"] for x in c.get("/api/v1/search", params={"q": "wasseruhr"}).json()["results"]] == ["Notiz"]
        assert c.get("/api/v1/search", params={"q": "zählerstand"}).json()["results"] == []
        # Einsortieren aus dem Eingang übernimmt den schon gelesenen Text (sofort durchsuchbar)
        c.patch(f"/api/v1/binders/{b['id']}", json={"target_share": "Dokumente", "target_path": ""})
        r = c.post("/api/v1/inbox/file", json={"share": "Scans", "path": "Eingang/scan_0815.pdf", "tab_id": b["tabs"][0]["id"],
                                                "title": "Rechnung Waschmaschine"}).json()
        assert r["moved"]
        res = c.get("/api/v1/search", params={"q": "mediamarkt"}).json()
        assert [x["title"] for x in res["results"]] == ["Rechnung Waschmaschine"] and res["inbox"] == []
        conn = connect()
        assert conn.execute("SELECT COUNT(*) FROM inbox_text").fetchone()[0] == 0
        conn.close()
        # Neu lesen
        st = c.post("/api/v1/admin/fulltext/reindex").json()
        assert st["pending"] == 8
        # Dokument entfernen → Text weg
        doc_id = res["results"][0]["id"]
        c.delete(f"/api/v1/tabs/{b['tabs'][0]['id']}/docs/{doc_id}")
        conn = connect()
        fulltext.run_all(conn)
        assert conn.execute("SELECT COUNT(*) FROM file_text").fetchone()[0] == 6
        if fulltext.has_fts(conn):
            assert conn.execute("SELECT COUNT(*) FROM file_fts").fetchone()[0] == 6
        conn.close()


def test_words_and_snippet():
    assert fulltext.words_of('"Kfz"  OR a  4711-0815 (x)') == ["Kfz", "OR", "4711-0815"]
    s = fulltext._snippet("x" * 300 + " Die Kaution beträgt drei Monatsmieten " + "y" * 300, ["kaution"])
    assert s.startswith("…") and s.endswith("…") and "\x02Kaution\x03" in s
