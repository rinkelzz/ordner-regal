"""0.4: Fristen & Wiedervorlage – Anlegen, Rechte, Wiederholung, Erinnerungs-Mails (vorher, am Tag, nachfassen, Sammelmail)."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import deadlines as dl, dsm_auth, mailer
from app.db import connect, set_setting
from app.main import app

from .fresh import fresh_db
from .test_regal import PDF, login, sso

SENT = []


@pytest.fixture()
def nas(tmp_path, monkeypatch):
    vol = tmp_path / "volume1"
    (vol / "Dokumente").mkdir(parents=True)
    (vol / "Dokumente" / "Kfz.pdf").write_bytes(PDF)
    (vol / "Privat").mkdir()
    (vol / "Privat" / "Geheim.pdf").write_bytes(PDF + b"x")
    monkeypatch.setenv("ORG_SHARE_ROOTS", str(vol))
    monkeypatch.setattr(dsm_auth, "_is_dsm_admin", lambda u: False)
    dsm_auth._admin_cache.clear()
    SENT.clear()
    monkeypatch.setattr(mailer, "send_mail", lambda conn, to, subject, body, **kw: SENT.append((to, subject, body)) or "<id>")
    data = tmp_path / "data"
    data.mkdir()
    with fresh_db(data):
        conn = connect()
        set_setting(conn, "smtp_host", "smtp.example.de")
        set_setting(conn, "deadline_digest", "0")
        set_setting(conn, "public_url", "https://regal.example.de")
        conn.close()
        yield vol


def setup(c):
    for n in ("Dokumente", "Privat"):
        c.patch(f"/api/v1/admin/shares/{n}", json={"enabled": True})
    c.patch("/api/v1/me", json={"email": "tim@example.de"})
    shelf = c.post("/api/v1/shelves", json={"name": "Privat"}).json()
    b = c.post("/api/v1/binders", json={"board_id": shelf["boards"][0]["id"], "title": "Versicherungen"}).json()
    r = c.post(f"/api/v1/tabs/{b['tabs'][0]['id']}/docs", json={"files": [{"share": "Dokumente", "path": "Kfz.pdf", "title": "HUK Kfz"},
                                                                          {"share": "Privat", "path": "Geheim.pdf", "title": "Geheim"}]}).json()
    docs = {d["title"]: d["id"] for d in r["binder"]["tabs"][0]["docs"]}
    return shelf, b, docs


def run(day):
    conn = connect()
    try:
        return dl.run_due(conn, force=True, today_=day)
    finally:
        conn.close()


def test_create_and_mails(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, b, docs = setup(c)
        due = date(2026, 11, 30)
        r = c.post(f"/api/v1/docs/{docs['HUK Kfz']}/deadlines", json={"kind": "kuendigung", "due_on": due.isoformat(), "lead_days": 14,
                                                                       "repeat": "yearly", "note": "per Einschreiben", "emails": ["praxis@example.de"]})
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["title"] == "Kündigungsfrist" and d["repeat_label"] == "jährlich"
        assert [u["email"] for u in d["recipients"]["users"]] == ["tim@example.de"] and d["recipients"]["emails"] == ["praxis@example.de"]
        assert c.post(f"/api/v1/docs/{docs['HUK Kfz']}/deadlines", json={"kind": "eigene", "due_on": "2026-12-01"}).status_code == 400
        assert c.post(f"/api/v1/docs/{docs['HUK Kfz']}/deadlines", json={"due_on": "2026-12-01", "emails": ["kaputt"]}).status_code == 400
        # nichts zu früh
        assert run(due - timedelta(days=20))["sent"] == 0
        # 14 Tage vorher: an beide
        r = run(due - timedelta(days=14))
        assert r["sent"] == 2 and {t for t, _, _ in SENT} == {"tim@example.de", "praxis@example.de"}
        assert "in 14 Tagen" in SENT[0][1] and "https://regal.example.de/#regal/b" in SENT[0][2] and "per Einschreiben" in SENT[0][2]
        SENT.clear()
        assert run(due - timedelta(days=13))["sent"] == 0  # nicht doppelt
        assert run(due)["sent"] == 2 and "Heute" in SENT[0][1]
        SENT.clear()
        assert run(due + timedelta(days=3))["sent"] == 0
        assert run(due + timedelta(days=7))["sent"] == 2 and "Überfällig" in SENT[0][1]
        SENT.clear()
        assert run(due + timedelta(days=10))["sent"] == 0 and run(due + timedelta(days=14))["sent"] == 2
        # Übersicht + Zähler
        ov = c.get("/api/v1/deadlines").json()
        assert len(ov) == 1 and ov[0]["doc_title"] == "HUK Kfz" and ov[0]["places"][0]["binder"] == "Versicherungen"
        sh = c.get(f"/api/v1/shelves/{shelf['id']}").json()
        assert sh["boards"][0]["binders"][0]["next_due"] == due.isoformat()
        bd = c.get(f"/api/v1/binders/{b['id']}").json()
        assert next(x for x in bd["tabs"][0]["docs"] if x["title"] == "HUK Kfz")["next_deadline"]["due_on"] == due.isoformat()
        # erledigt bei jährlicher Frist → nächstes Jahr
        r = c.post(f"/api/v1/deadlines/{d['id']}/done").json()
        assert r["due_on"] > due.isoformat() and r["done_at"] is None and r["next_due"].endswith("-11-30")
        log = c.get("/api/v1/audit?category=fristen").json()["entries"]
        assert {"Frist angelegt", "Frist erledigt", "Erinnerung verschickt"} <= {e["label"] for e in log}


def test_once_done_and_rights(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, b, docs = setup(c)
        c.post("/api/v1/users", json={"username": "anna", "display_name": "Anna", "email": "anna@example.de"})
        anna = next(u["id"] for u in c.get("/api/v1/users").json() if u["username"] == "anna")
        c.put(f"/api/v1/shelves/{shelf['id']}/access", json={"users": [{"user_id": anna, "can_edit": False}]})
        due = date(2026, 12, 15)
        # Frist am geheimen Dokument mit Anna als Empfängerin – sie darf „Privat“ in DSM nicht lesen
        g = c.post(f"/api/v1/docs/{docs['Geheim']}/deadlines", json={"kind": "zahlung", "due_on": due.isoformat(), "lead_days": 0,
                                                                     "user_ids": [anna]}).json()
        k = c.post(f"/api/v1/docs/{docs['HUK Kfz']}/deadlines", json={"kind": "wiedervorlage", "due_on": due.isoformat(), "lead_days": 0,
                                                                      "user_ids": [anna]}).json()
        cand = c.get(f"/api/v1/docs/{docs['HUK Kfz']}/recipient-candidates").json()
        assert {x["name"] for x in cand} == {"Administrator"}  # Anna hat noch keine Freigabe
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        h = sso("anna", [["Dokumente", False]])
        c.get("/api/v1/me", headers=h)
        assert [x["doc_title"] for x in c.get("/api/v1/deadlines", headers=h).json()] == ["HUK Kfz"]
        assert c.post(f"/api/v1/deadlines/{k['id']}/done", headers=h).status_code == 403  # nur ansehen
        assert c.patch(f"/api/v1/deadlines/{g['id']}", headers=h, json={"note": "x"}).status_code == 404
        assert c.get("/api/v1/deadlines/summary", headers=h).json()["next"][0]["doc_title"] == "HUK Kfz"
    r = run(due)
    assert r["sent"] == 1 and SENT[0][0] == "anna@example.de" and "HUK Kfz" in SENT[0][1]  # Geheim nicht an Anna
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        cand = c.get(f"/api/v1/docs/{docs['HUK Kfz']}/recipient-candidates").json()
        assert {x["name"] for x in cand} == {"Administrator", "Anna"}  # nach DSM-Anmeldung mit Recht auf Dokumente
        c.post(f"/api/v1/deadlines/{k['id']}/done")
        assert c.get("/api/v1/deadlines", params={"status": "done"}).json()[0]["id"] == k["id"]
        SENT.clear()
        assert run(due + timedelta(days=7))["sent"] == 0  # erledigt: kein Nachfassen
        c.post(f"/api/v1/deadlines/{k['id']}/reopen")
        assert c.delete(f"/api/v1/deadlines/{g['id']}").json() == {"ok": True}


def test_digest(nas):
    with TestClient(app, client=("192.168.1.10", 1)) as c:
        login(c)
        shelf, b, docs = setup(c)
        today = dl.today()
        c.post(f"/api/v1/docs/{docs['HUK Kfz']}/deadlines", json={"kind": "ablauf", "due_on": (today - timedelta(days=2)).isoformat(), "lead_days": 0})
        c.post(f"/api/v1/docs/{docs['Geheim']}/deadlines", json={"kind": "garantie", "due_on": (today + timedelta(days=20)).isoformat(), "lead_days": 0})
        c.post(f"/api/v1/docs/{docs['Geheim']}/deadlines", json={"kind": "garantie", "due_on": (today + timedelta(days=90)).isoformat()})
        st = c.patch("/api/v1/deadline-settings", json={"digest": True, "hour": 6, "overdue_days": 0}).json()
        assert st["digest"] and st["hour"] == 6 and st["smtp_ok"]
        SENT.clear()
        r = c.post("/api/v1/deadline-settings/run-now").json()
        digest = [m for m in SENT if m[1].startswith("⏰ Fristen")]
        assert r["result"]["digest"] == 1 and len(digest) == 1
        body = digest[0][2]
        assert "Überfällig:" in body and "Läuft ab – HUK Kfz" in body and "Garantie endet – Geheim" in body and body.count("Garantie") == 1
        assert "https://regal.example.de/#fristen" in body


def test_add_months_and_validation():
    assert dl.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert dl.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert dl.next_due(date(2026, 1, 31), "monthly", date(2026, 3, 1)) == date(2026, 3, 31)  # Tag 31 bleibt Ziel
    assert dl.next_due(date(2024, 11, 30), "yearly", date(2026, 11, 10)) == date(2026, 11, 30)
    assert dl.due_mails({"due_on": "2026-11-30", "lead_days": 14}, date(2026, 11, 20), 7) == ["lead"]
    assert dl.due_mails({"due_on": "2026-11-30", "lead_days": 14}, date(2026, 12, 15), 7) == ["day", "overdue:2"]


def test_no_smtp(nas):
    conn = connect()
    set_setting(conn, "smtp_host", "")
    r = dl.run_due(conn, force=True)
    conn.close()
    assert r["errors"] == ["kein Mailserver eingetragen"]
