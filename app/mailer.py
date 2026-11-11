"""Mailversand über den eigenen SMTP-Server (Testmail, später Fristen-Mails)."""
import re
import smtplib
import sqlite3
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Dict, List, Optional, Tuple

from .db import get_setting

EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


class MailError(Exception):
    pass


def valid_email(addr: str) -> bool:
    return bool(addr) and len(addr) <= 200 and bool(EMAIL_RE.match(addr))


def smtp_config(conn: sqlite3.Connection) -> Dict:
    port = get_setting(conn, "smtp_port") or "587"
    return {
        "host": get_setting(conn, "smtp_host").strip(),
        "port": int(port) if port.isdigit() else 587,
        "security": get_setting(conn, "smtp_security") or "starttls",
        "user": get_setting(conn, "smtp_user"),
        "password": get_setting(conn, "smtp_password"),
        "from": get_setting(conn, "smtp_from").strip() or get_setting(conn, "smtp_user").strip(),
        "from_name": get_setting(conn, "smtp_from_name") or "Ordner-Regal",
    }


def send_mail(conn: sqlite3.Connection, to: str, subject: str, body: str,
              attachments: Optional[List[Tuple[str, bytes, str]]] = None,
              headers: Optional[Dict[str, str]] = None) -> str:
    """Mail verschicken. Liefert die Message-ID (damit Antworten darauf später zugeordnet werden können)."""
    cfg = smtp_config(conn)
    if not cfg["host"]:
        raise MailError("Kein Mailserver eingetragen (Verwaltung → Mail)")
    if not valid_email(cfg["from"]):
        raise MailError("Absenderadresse fehlt oder ist ungültig")
    if not valid_email(to):
        raise MailError(f"Ungültige Empfängeradresse: {to}")
    msg = EmailMessage()
    msg["From"] = formataddr((cfg["from_name"], cfg["from"]))
    msg["To"] = to
    msg["Subject"] = subject.replace("\r", " ").replace("\n", " ")
    msg["Message-ID"] = make_msgid(domain=cfg["from"].split("@")[-1])
    for k, v in (headers or {}).items():
        msg[k] = v.replace("\r", " ").replace("\n", " ")
    msg.set_content(body)
    for name, data, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    try:
        ctx = ssl.create_default_context()
        if cfg["security"] == "ssl":
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=30, context=ctx)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=30)
        with server:
            server.ehlo()
            if cfg["security"] == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
            if cfg["user"]:
                server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
        return msg["Message-ID"]
    except smtplib.SMTPAuthenticationError:
        raise MailError("Anmeldung am Mailserver fehlgeschlagen – Benutzer/Passwort prüfen")
    except ssl.SSLError as e:
        raise MailError(f"Verschlüsselung fehlgeschlagen ({e.reason or e}) – Port und Verschlüsselung prüfen")
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Mailversand fehlgeschlagen: {e}")
