"""Fristen und Wiedervorlagen an Dokumenten – mit Wiederholung und Erinnerungs-Mails.

Mails (täglich ab der eingestellten Stunde, Rückstand wird nachgeholt):
- „lead“: n Tage vorher (Vorlauf pro Frist, 0 = keine Vorab-Mail)
- „day“: am Fälligkeitstag
- „overdue:<k>“: solange nicht erledigt, alle n Tage nach dem Fälligkeitstag (Standard 7)
- Sammelmail einmal pro Woche (Standard Montag): überfällig + nächste 30 Tage, pro Empfänger

Empfänger pro Frist: Benutzer (an ihre hinterlegte Mailadresse – nur solange sie das Dokument sehen dürfen) und
freie Adressen. Jede verschickte Erinnerung wird gespeichert, damit nichts doppelt kommt.
"""
import calendar
import sqlite3
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from .db import audit, get_setting, set_setting
from .timeutil import now_utc, now_utc_str, to_local

KINDS = {
    "kuendigung": "Kündigungsfrist",
    "ablauf": "Läuft ab",
    "wiedervorlage": "Wiedervorlage",
    "zahlung": "Zahlung fällig",
    "garantie": "Garantie endet",
    "eigene": "Eigene Frist",
}
REPEATS = {"": "einmalig", "monthly": "monatlich", "quarterly": "vierteljährlich", "halfyearly": "halbjährlich", "yearly": "jährlich"}
MONTHS = {"monthly": 1, "quarterly": 3, "halfyearly": 6, "yearly": 12}
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
DIGEST_DAYS = 30


def today() -> date:
    return to_local(now_utc()).date()


def add_months(d: date, months: int, anchor_day: Optional[int] = None) -> date:
    """d + Monate; der Tag wird am Monatsende gekappt (31.01. + 1 Monat = 28./29.02.)."""
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    day = min(anchor_day or d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def next_due(due: date, repeat: str, after: date) -> date:
    """Nächster Termin der Wiederholung nach „after“ (mindestens einmal weiter)."""
    step = MONTHS[repeat]
    anchor = due.day
    n = due
    while True:
        n = add_months(n, step, anchor)
        if n > after:
            return n


def label_of(row) -> str:
    return (row["label"] or "").strip() or KINDS.get(row["kind"], row["kind"])


def fmt(d: str) -> str:
    return f"{d[8:10]}.{d[5:7]}.{d[0:4]}" if d else ""


# --- Empfänger -------------------------------------------------------------------

def recipients(conn: sqlite3.Connection, deadline_id: int) -> Dict[str, list]:
    users, emails = [], []
    for r in conn.execute("SELECT r.*, u.display_name, u.email AS uemail, u.active FROM deadline_recipients r "
                          "LEFT JOIN users u ON u.id = r.user_id WHERE r.deadline_id = ? ORDER BY r.id", (deadline_id,)):
        if r["user_id"]:
            users.append({"id": r["user_id"], "name": r["display_name"], "email": r["uemail"] or "", "active": bool(r["active"])})
        elif r["email"]:
            emails.append(r["email"])
    return {"users": users, "emails": emails}


def set_recipients(conn: sqlite3.Connection, deadline_id: int, user_ids: List[int], emails: List[str]) -> None:
    conn.execute("DELETE FROM deadline_recipients WHERE deadline_id = ?", (deadline_id,))
    seen = set()
    for uid in user_ids[:50]:
        if uid not in seen and conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone():
            seen.add(uid)
            conn.execute("INSERT INTO deadline_recipients(deadline_id, user_id) VALUES (?, ?)", (deadline_id, uid))
    for e in {x.strip().lower(): x.strip() for x in emails if x.strip()}.values():
        conn.execute("INSERT INTO deadline_recipients(deadline_id, email) VALUES (?, ?)", (deadline_id, e[:200]))


def user_sees_doc(conn: sqlite3.Connection, user, doc_id: int) -> bool:
    from fastapi import HTTPException
    from .regal import load_doc
    try:
        load_doc(conn, user, doc_id)
        return True
    except HTTPException:
        return False


def addresses(conn: sqlite3.Connection, d) -> List[Tuple[str, Optional[sqlite3.Row]]]:
    """[(Mailadresse, Benutzer oder None)] – Benutzer nur, wenn aktiv, mit Adresse und mit Recht aufs Dokument."""
    out, seen = [], set()
    for r in conn.execute("SELECT * FROM deadline_recipients WHERE deadline_id = ?", (d["id"],)).fetchall():
        if r["user_id"]:
            u = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1", (r["user_id"],)).fetchone()
            if not u or not u["email"] or not user_sees_doc(conn, u, d["document_id"]):
                continue
            addr, who = u["email"], u
        else:
            addr, who = r["email"], None
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            out.append((addr, who))
    return out


# --- fällig -----------------------------------------------------------------------

def due_mails(d, today_: date, overdue_days: int) -> List[str]:
    """Welche Erinnerungen sind heute (oder seit dem letzten Lauf) fällig? Ohne die schon verschickten."""
    due = date.fromisoformat(d["due_on"])
    out = []
    if d["lead_days"] > 0 and due - timedelta(days=d["lead_days"]) <= today_ < due:
        out.append("lead")
    if today_ >= due:
        out.append("day")
    if overdue_days > 0 and today_ >= due + timedelta(days=overdue_days):
        k = (today_ - due).days // overdue_days
        out.append(f"overdue:{k}")
    return out


def place_text(conn: sqlite3.Connection, doc_id: int) -> Tuple[str, Optional[int]]:
    r = conn.execute("""SELECT s.name AS shelf, x.title AS binder, x.id AS binder_id, t.name AS tab
                        FROM filings fi JOIN tabs t ON t.id = fi.tab_id JOIN binders x ON x.id = t.binder_id
                        JOIN boards b ON b.id = x.board_id JOIN shelves s ON s.id = b.shelf_id
                        WHERE fi.document_id = ? ORDER BY fi.filed_at LIMIT 1""", (doc_id,)).fetchone()
    return (f"{r['shelf']} › {r['binder']} › {r['tab']}", r["binder_id"]) if r else ("", None)


def app_url(conn: sqlite3.Connection) -> str:
    from .config import settings
    return (get_setting(conn, "public_url") or settings.public_url or "").rstrip("/")


def _mail_one(conn, d, what: str, today_: date) -> Tuple[str, str]:
    doc = conn.execute("SELECT title FROM documents WHERE id = ?", (d["document_id"],)).fetchone()
    title = doc["title"] if doc else "Dokument"
    due = date.fromisoformat(d["due_on"])
    days = (due - today_).days
    label = label_of(d)
    if what == "lead":
        subject = f"⏰ {label} in {days} Tag{'en' if days != 1 else ''}: {title}"
        head = f"In {days} Tag{'en' if days != 1 else ''} ist es so weit – am {fmt(d['due_on'])}."
    elif what == "day":
        subject = f"⏰ Heute: {label} – {title}"
        head = f"Heute, am {fmt(d['due_on'])}, ist die Frist fällig."
    else:
        subject = f"❗ Überfällig: {label} – {title}"
        head = f"Seit {-days} Tag{'en' if days != -1 else ''} überfällig (fällig am {fmt(d['due_on'])}) und noch nicht als erledigt markiert."
    place, binder_id = place_text(conn, d["document_id"])
    url = app_url(conn)
    lines = [head, "", f"Dokument: {title}", f"Frist:    {label}" + (f" ({REPEATS[d['repeat']]})" if d["repeat"] else "")]
    if place:
        lines.append(f"Abgelegt: {place}")
    if d["note"]:
        lines += ["", d["note"]]
    if url and binder_id:
        lines += ["", f"Im Regal öffnen: {url}/#regal/b{binder_id}"]
    lines += ["", "Erledigt? Im Ordner-Regal unter „Fristen“ abhaken – dann kommen keine weiteren Erinnerungen.",
              "", "-- ", "Ordner-Regal"]
    return subject, "\n".join(lines)


def run_due(conn: sqlite3.Connection, force: bool = False, today_: Optional[date] = None) -> Optional[dict]:
    """Einmal täglich (ab der eingestellten Stunde) – oder mit force sofort. Liefert eine Zusammenfassung."""
    from .mailer import MailError, send_mail, smtp_config
    now = to_local(now_utc())
    today_ = today_ or now.date()
    if not force:
        if now.hour < int(get_setting(conn, "deadline_hour") or 7) or get_setting(conn, "deadline_last_day") == today_.isoformat():
            return None
    if not smtp_config(conn)["host"]:
        set_setting(conn, "deadline_last_result", "kein Mailserver eingetragen")
        return {"sent": 0, "errors": ["kein Mailserver eingetragen"], "digest": 0}
    set_setting(conn, "deadline_last_day", today_.isoformat())
    overdue_days = int(get_setting(conn, "deadline_overdue_days") or 7)
    sent, errors = 0, []
    for d in conn.execute("SELECT * FROM deadlines WHERE done_at IS NULL").fetchall():
        todo = [w for w in due_mails(d, today_, overdue_days)
                if not conn.execute("SELECT 1 FROM deadline_sent WHERE deadline_id = ? AND due_on = ? AND what = ?",
                                    (d["id"], d["due_on"], w)).fetchone()]
        # beim Nachholen nur die wichtigste: überfällig vor heute vor vorab
        if not todo:
            continue
        what = next((w for w in todo if w.startswith("overdue")), None) or ("day" if "day" in todo else "lead")
        subject, body = _mail_one(conn, d, what, today_)
        ok = False
        for addr, _ in addresses(conn, d):
            try:
                send_mail(conn, addr, subject, body)
                sent += 1
                ok = True
            except MailError as e:
                errors.append(f"{addr}: {e}")
        for w in todo:  # auch die übersprungenen als erledigt merken
            conn.execute("INSERT OR IGNORE INTO deadline_sent(deadline_id, due_on, what, sent_at) VALUES (?, ?, ?, ?)",
                         (d["id"], d["due_on"], w, now_utc_str()))
        if ok:
            audit(conn, None, "deadline_mail", f"deadline:{d['id']}", f"{subject}")
    digest = 0
    if get_setting(conn, "deadline_digest") == "1" and (force or today_.weekday() == int(get_setting(conn, "deadline_digest_weekday") or 0)):
        week = f"{today_.isocalendar()[0]}-W{today_.isocalendar()[1]:02d}"
        if force or get_setting(conn, "deadline_last_digest") != week:
            set_setting(conn, "deadline_last_digest", week)
            digest, errs = send_digests(conn, today_)
            errors += errs
    result = f"{today_.isoformat()}: {sent} Erinnerung(en), {digest} Sammelmail(s)" + (f", Fehler: {'; '.join(errors[:3])}" if errors else "")
    set_setting(conn, "deadline_last_result", result)
    return {"sent": sent, "digest": digest, "errors": errors}


def send_digests(conn: sqlite3.Connection, today_: date) -> Tuple[int, List[str]]:
    """Pro Empfänger eine Mail: überfällig + nächste 30 Tage (nur Fristen, bei denen er Empfänger ist)."""
    from .mailer import MailError, send_mail
    horizon = (today_ + timedelta(days=DIGEST_DAYS)).isoformat()
    per: Dict[str, list] = {}
    for d in conn.execute("SELECT * FROM deadlines WHERE done_at IS NULL AND due_on <= ? ORDER BY due_on", (horizon,)).fetchall():
        for addr, _ in addresses(conn, d):
            per.setdefault(addr, []).append(d)
    sent, errors = 0, []
    url = app_url(conn)
    for addr, items in per.items():
        over = [d for d in items if d["due_on"] < today_.isoformat()]
        soon = [d for d in items if d["due_on"] >= today_.isoformat()]
        lines = [f"Deine Fristen für die Woche ab {today_.strftime('%d.%m.%Y')}:", ""]
        for head, group in (("Überfällig", over), (f"Nächste {DIGEST_DAYS} Tage", soon)):
            if not group:
                continue
            lines.append(head + ":")
            for d in group:
                title = conn.execute("SELECT title FROM documents WHERE id = ?", (d["document_id"],)).fetchone()["title"]
                lines.append(f"  {fmt(d['due_on'])}  {label_of(d)} – {title}")
            lines.append("")
        if url:
            lines += [f"Alle Fristen: {url}/#fristen", ""]
        lines += ["-- ", "Ordner-Regal"]
        subject = f"⏰ Fristen: {len(over)} überfällig, {len(soon)} in den nächsten {DIGEST_DAYS} Tagen" if over else \
            f"⏰ Fristen: {len(soon)} in den nächsten {DIGEST_DAYS} Tagen"
        try:
            send_mail(conn, addr, subject, "\n".join(lines))
            sent += 1
        except MailError as e:
            errors.append(f"{addr}: {e}")
    if sent:
        audit(conn, None, "deadline_digest", "", f"Sammelmail an {sent} Empfänger")
    return sent, errors


# --- erledigt / Wiederholung --------------------------------------------------------

def mark_done(conn: sqlite3.Connection, d, user_id: int) -> str:
    """Einmalige Frist: erledigt. Wiederkehrende: auf den nächsten Termin nach heute (bzw. nach der alten Frist)."""
    if d["repeat"] in MONTHS:
        due = date.fromisoformat(d["due_on"])
        nxt = next_due(due, d["repeat"], max(due, today() - timedelta(days=1)) if due < today() else due)
        conn.execute("UPDATE deadlines SET due_on = ?, updated_at = ? WHERE id = ?", (nxt.isoformat(), now_utc_str(), d["id"]))
        audit(conn, user_id, "deadline_done", f"deadline:{d['id']}", f"{label_of(d)} {fmt(d['due_on'])} erledigt – nächste: {fmt(nxt.isoformat())}")
        return nxt.isoformat()
    conn.execute("UPDATE deadlines SET done_at = ?, done_by = ?, updated_at = ? WHERE id = ?", (now_utc_str(), user_id, now_utc_str(), d["id"]))
    audit(conn, user_id, "deadline_done", f"deadline:{d['id']}", f"{label_of(d)} {fmt(d['due_on'])} erledigt")
    return ""
