"""Benutzerverwaltung , eigenes Profil, Passwort."""
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

from .fresh import fresh_db

PW = os.environ["ORG_ADMIN_PASSWORD"]


@pytest.fixture()
def chef(tmp_path):
    with fresh_db(tmp_path):
        with TestClient(app) as c:
            assert c.post("/api/v1/auth/login", json={"username": "admin", "password": PW}).status_code == 200
            yield c


def test_create_update_deactivate(chef):
    r = chef.post("/api/v1/users", json={"username": "anna", "display_name": "Anna", "password": "geheim123",
                                         "email": "anna@example.de"})
    assert r.status_code == 201 and r.json()["has_password"] and not r.json()["is_admin"]
    uid = r.json()["id"]
    assert chef.post("/api/v1/users", json={"username": "ANNA", "display_name": "x", "password": "geheim123"}).status_code == 409
    assert chef.post("/api/v1/users", json={"username": "böse name", "display_name": "x"}).status_code == 422
    assert chef.post("/api/v1/users", json={"username": "kurz", "display_name": "x", "password": "123"}).status_code == 422
    assert chef.post("/api/v1/users", json={"username": "e", "display_name": "x", "email": "kaputt"}).status_code == 422
    # nur DSM-Anmeldung (ohne Passwort)
    r = chef.post("/api/v1/users", json={"username": "dsmonly", "display_name": "Nur DSM"})
    assert r.status_code == 201 and r.json()["has_password"] is False

    with TestClient(app) as anna:
        assert anna.post("/api/v1/auth/login", json={"username": "anna", "password": "geheim123"}).status_code == 200
        assert anna.get("/api/v1/me").json()["display_name"] == "Anna"
        chef.patch(f"/api/v1/users/{uid}", json={"display_name": "Anna B.", "is_admin": True})
        me = anna.get("/api/v1/me").json()
        assert me["display_name"] == "Anna B." and me["is_admin"]
        # deaktivieren beendet die Sitzung sofort
        chef.patch(f"/api/v1/users/{uid}", json={"active": False})
        assert anna.get("/api/v1/me").status_code == 401
        assert anna.post("/api/v1/auth/login", json={"username": "anna", "password": "geheim123"}).status_code == 401
    users = chef.get("/api/v1/users").json()
    assert [u["username"] for u in users if not u["active"]] == ["anna"]
    log = chef.get("/api/v1/audit", params={"category": "benutzer"}).json()["entries"]
    assert any(e["action"] == "user_created" and e["subject"] == "Anna B." for e in log)


def test_last_admin_stays(chef):
    me = chef.get("/api/v1/me").json()
    r = chef.patch(f"/api/v1/users/{me['id']}", json={"is_admin": False})
    assert r.status_code == 400 and "mindestens ein" in r.json()["detail"]
    assert chef.patch(f"/api/v1/users/{me['id']}", json={"active": False}).status_code == 400
    assert chef.patch("/api/v1/users/9999", json={"display_name": "x"}).status_code == 404


def test_profile_and_password(chef):
    assert chef.patch("/api/v1/me", json={"email": "kaputt"}).status_code == 400
    me = chef.patch("/api/v1/me", json={"email": "tim@example.de", "display_name": "Tim"}).json()
    assert me["email"] == "tim@example.de" and me["display_name"] == "Tim"
    assert chef.post("/api/v1/me/password", json={"old_password": "falsch", "new_password": "neuesPasswort1"}).status_code == 400
    assert chef.post("/api/v1/me/password", json={"old_password": PW, "new_password": "kurz"}).status_code == 422
    with TestClient(app) as other:  # zweites Gerät
        other.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
        r = chef.post("/api/v1/me/password", json={"old_password": PW, "new_password": "neuesPasswort1"})
        assert r.status_code == 200 and r.json()["session_token"]
        assert other.get("/api/v1/me").status_code == 401   # andere Sitzungen beendet
        assert chef.get("/api/v1/me").status_code == 200    # dieses Gerät bleibt angemeldet
    with TestClient(app) as c:
        assert c.post("/api/v1/auth/login", json={"username": "admin", "password": "neuesPasswort1"}).status_code == 200
