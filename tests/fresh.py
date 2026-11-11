"""Hilfe für Tests, die eine eigene, frische Datenbank brauchen."""
import contextlib

from app.config import settings
from app.db import init_db


@contextlib.contextmanager
def fresh_db(tmp_path, company=""):
    old = (settings.data_dir, settings.db_path, settings.initial_company)
    settings.data_dir = tmp_path
    settings.db_path = tmp_path / "ordnerregal.db"
    settings.initial_company = company
    try:
        init_db()
        yield settings.db_path
    finally:
        settings.data_dir, settings.db_path, settings.initial_company = old
