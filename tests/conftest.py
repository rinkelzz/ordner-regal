"""Gemeinsame Testumgebung – wird vor allen Testdateien geladen."""
import os
import tempfile

os.environ["ORG_DATA_DIR"] = tempfile.mkdtemp()
os.environ["ORG_ADMIN_PASSWORD"] = "admin-passwort"
os.environ["ORG_PUBLIC_URL"] = "https://doku.example.de"
os.environ["ORG_SCHEDULER"] = "0"
os.environ["ORG_UPDATE_CHECK"] = "0"
os.environ["ORG_SELF_RESTART"] = "0"
os.environ["ORG_KDF_ITERATIONS"] = "1000"  # Tests: schnell statt sicher
os.environ["ORG_HTTP_ACCESS"] = "1"  # TestClient spricht http – die Sperre wird in test_security_review.py eigens geprüft
