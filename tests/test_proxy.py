"""0.6.2: eigener Reverse Proxy im LAN (z. B. Nginx Proxy Manager) – X-Forwarded-For nur von eingetragenen Proxys."""
import os

import pytest
from fastapi.testclient import TestClient

from app import proxy
from app.dsm_auth import _fails
from app.main import app

from .fresh import fresh_db

PW = os.environ["ORG_ADMIN_PASSWORD"]
NPM = ("192.168.90.26", 40000)


@pytest.fixture(autouse=True)
def reset():
    proxy.configure("")
    _fails.clear()
    yield
    proxy.configure("")
    _fails.clear()


def test_parse():
    nets, errors = proxy.parse("192.168.90.26; 10.0.0.0/24\n192.168.90.26, fd00::1")
    assert [str(n) for n in nets] == ["192.168.90.26/32", "10.0.0.0/24", "fd00::1/128"] and not errors
    assert proxy.parse("10.0.0.0/8")[1] and proxy.parse("0.0.0.0/0")[1] and proxy.parse("nginx")[1]


def test_forwarded_only_from_trusted(tmp_path):
    with fresh_db(tmp_path):
        with TestClient(app, client=NPM) as c:
            assert c.post("/api/v1/auth/login", json={"username": "admin", "password": PW}).status_code == 200
            h = {"X-Forwarded-For": "93.184.216.34", "X-Forwarded-Proto": "https"}
            r = c.get("/api/v1/settings/proxy", headers=h).json()
            assert r["client"] == "192.168.90.26" and r["peer"] == "192.168.90.26" and r["forwarded"] == "93.184.216.34"
            # Paketquelle: über den (noch nicht eingetragenen) Proxy sieht alles nach LAN aus
            assert c.get("/spk", headers=h).status_code == 200
            assert c.put("/api/v1/settings/proxy", json={"trusted_proxies": "10.0.0.0/8"}).status_code == 400
            assert c.put("/api/v1/settings/proxy", json={"trusted_proxies": "192.168.90.26/32, 192.168.90.26"}).json() == \
                {"trusted_proxies": "192.168.90.26"}
            r = c.get("/api/v1/settings/proxy", headers=h).json()
            assert r["client"] == "93.184.216.34" and r["peer"] == "192.168.90.26"
            # Gefälschter Eintrag links in der Kette zählt nicht
            r = c.get("/api/v1/settings/proxy", headers={"X-Forwarded-For": "10.1.1.1, 93.184.216.34"}).json()
            assert r["client"] == "93.184.216.34"
            # Jetzt ist die Paketquelle von außen gesperrt, aus dem LAN weiter offen
            assert c.get("/spk", headers=h).status_code == 403
            assert c.get("/spk", headers={"X-Forwarded-For": "192.168.90.50"}).status_code == 200
            log = c.get("/api/v1/audit?category=einstellungen").json()["entries"]
            assert any("Reverse Proxy" in str(e) for e in log)


def test_untrusted_peer_cannot_spoof(tmp_path):
    proxy.configure("192.168.90.26")
    with fresh_db(tmp_path):
        with TestClient(app, client=("192.168.90.99", 1)) as c:
            c.post("/api/v1/auth/login", json={"username": "admin", "password": PW})
            r = c.get("/api/v1/settings/proxy", headers={"X-Forwarded-For": "127.0.0.1"}).json()
            assert r["client"] == "192.168.90.99"
