"""Port ändern: Prüfung, config.env, Neustart-Auslöser, ui/app.json."""
import os
import socket

import pytest
from fastapi.testclient import TestClient

from app import portcfg
from app.config import settings
from app.main import app


@pytest.fixture()
def admin(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "config_file", tmp_path / "config.env")
    (tmp_path / "config.env").write_text('# Kommentar\nORG_PORT=8790\nORG_TIMEZONE="Europe/Berlin"\n', encoding="utf-8")
    with TestClient(app, base_url="http://nas.local:8790") as c:
        assert c.post("/api/v1/auth/login", json={"username": "admin", "password": os.environ["ORG_ADMIN_PASSWORD"]}).status_code == 200
        yield c


def test_check_port():
    for bad in (80, 443, 1024, 5000, 5001, 70000, 0):
        with pytest.raises(portcfg.PortError):
            portcfg.check_port(bad)
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen(1)
    busy = s.getsockname()[1]
    try:
        if busy >= portcfg.MIN_PORT:
            with pytest.raises(portcfg.PortError, match="belegt"):
                portcfg.check_port(busy)
    finally:
        s.close()


def test_write_config_keeps_other_lines(tmp_path):
    p = tmp_path / "config.env"
    p.write_text('# Kopf\nORG_PORT=8790\nORG_PUBLIC_URL=""\n', encoding="utf-8")
    portcfg.write_config(9123, p)
    assert p.read_text(encoding="utf-8") == '# Kopf\nORG_PORT=9123\nORG_PUBLIC_URL=""\n'
    q = tmp_path / "neu.env"
    portcfg.write_config(9124, q)
    assert "ORG_PORT=9124" in q.read_text(encoding="utf-8")


def test_change_port_via_api(admin, tmp_path, monkeypatch):
    restarts = []
    monkeypatch.setattr(settings, "self_restart", True)
    monkeypatch.setattr(portcfg, "_restart_later", lambda delay: restarts.append(delay))
    monkeypatch.setattr(portcfg.threading, "Thread", lambda target, args, **k: type("T", (), {"start": lambda self: target(*args)})())
    old_env = os.environ.get("ORG_PORT")
    try:
        assert admin.post("/api/v1/settings/port", json={"port": 5001}).status_code == 400
        assert admin.post("/api/v1/settings/port", json={"port": settings.port}).json()["restart"] is False
        r = admin.post("/api/v1/settings/port", json={"port": 9231})
        assert r.status_code == 200, r.text
        assert r.json()["restart"] is True and r.json()["direct_url"] == "http://nas.local:9231/"
        assert "ORG_PORT=9231" in (tmp_path / "config.env").read_text(encoding="utf-8")
        assert 'ORG_TIMEZONE="Europe/Berlin"' in (tmp_path / "config.env").read_text(encoding="utf-8")
        assert restarts == [1.5] and os.environ["ORG_PORT"] == "9231"
        log = admin.get("/api/v1/audit", params={"category": "einstellungen"}).json()["entries"]
        assert any(e["action"] == "port_changed" and "9231" in e["text"] for e in log)
    finally:
        if old_env is None:
            os.environ.pop("ORG_PORT", None)
        else:
            os.environ["ORG_PORT"] = old_env


def test_port_in_settings_and_proxy_reads_config(admin, tmp_path):
    assert admin.get("/api/v1/settings").json()["port"] == settings.port
    # die DSM-Weiterleitung liest den Port aus config.env
    import importlib.util
    spec = importlib.util.spec_from_file_location("dsm_proxy", "synology/proxy/dsm_proxy.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    proxy.VAR = str(tmp_path)
    (tmp_path / "config.env").write_text("ORG_PORT=9555\n", encoding="utf-8")
    assert proxy.port() == 9555
