"""Designs: eigenes Design und Standard der Firma."""
import os

from fastapi.testclient import TestClient

from app.main import app


def test_themes():
    with TestClient(app) as admin:
        admin.post("/api/v1/auth/login", json={"username": "admin", "password": os.environ["ORG_ADMIN_PASSWORD"]})
        s = admin.get("/api/v1/settings").json()
        assert set(s["themes"]) == {"classic", "dsm", "dark", "warm"} and s["theme_default"] == "classic"
        admin.post("/api/v1/users", json={"username": "designer", "display_name": "Designer", "password": "geheim123"})
        with TestClient(app) as u:
            u.post("/api/v1/auth/login", json={"username": "designer", "password": "geheim123"})
            me = u.get("/api/v1/me").json()
            assert me["theme"] is None and me["theme_effective"] == "classic"
            assert u.patch("/api/v1/me", json={"theme": "rosa"}).status_code == 400
            me = u.patch("/api/v1/me", json={"theme": "warm"}).json()
            assert me["theme"] == "warm" and me["theme_effective"] == "warm"
            # Firmen-Standard ändern: eigenes Design bleibt, ohne eigenes gilt der Standard
            assert admin.patch("/api/v1/settings", json={"theme_default": "dsm"}).status_code == 200
            assert u.get("/api/v1/me").json()["theme_effective"] == "warm"
            assert u.patch("/api/v1/me", json={"theme": None}).json()["theme_effective"] == "dsm"
            assert u.patch("/api/v1/settings", json={"theme_default": "dark"}).status_code == 403
        assert admin.patch("/api/v1/settings", json={"theme_default": "bunt"}).status_code == 400
        admin.patch("/api/v1/settings", json={"theme_default": "classic"})
        assert "static/theme.js" in admin.get("/").text
        assert admin.get("/static/theme.js").status_code == 200
