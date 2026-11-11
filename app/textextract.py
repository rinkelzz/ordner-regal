"""Text aus Dokumenten lesen – nur reines Python (keine nativen Bibliotheken).

- PDF: pypdf. PDFs ohne Textebene (reine Bild-Scans) ergeben „empty“ – dafür bräuchte es eine Texterkennung (OCR).
- Office (docx, xlsx, pptx) und LibreOffice (odt, ods, odp): ZIP + XML. Der Text wird per regulärem Ausdruck aus
  dem XML geholt, nicht mit einem XML-Parser – so können präparierte Dateien keine Entitäten aufblähen.
- Text (txt, md, csv, …): UTF-8, sonst Latin-1.

Ergebnis: (status, text) mit status ok | empty | unsupported | toolarge | error.
"""
import html
import re
import zipfile
from pathlib import Path
from typing import Tuple

MAX_FILE = 64 * 1024 * 1024        # größere Dateien werden nicht gelesen
MAX_TEXT = 1_000_000               # höchstens so viele Zeichen pro Dokument
MAX_PAGES = 1000
MAX_XML_MEMBER = 32 * 1024 * 1024  # entpackt, pro XML-Datei im ZIP
MAX_XML_TOTAL = 96 * 1024 * 1024

TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".log", ".json", ".xml", ".ini", ".eml", ".vcf", ".ics"}
OOXML = {".docx": r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$",
         ".docm": r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$",
         ".xlsx": r"xl/(sharedStrings|worksheets/sheet\d+)\.xml$",
         ".xlsm": r"xl/(sharedStrings|worksheets/sheet\d+)\.xml$",
         ".pptx": r"ppt/(slides/slide\d+|notesSlides/notesSlide\d+)\.xml$"}
ODF = {".odt", ".ods", ".odp", ".ott"}
SUPPORTED = {".pdf"} | TEXT_EXT | set(OOXML) | ODF

_TAG = re.compile(r"<[^>]{0,2000}>")
_PARA = re.compile(r"</(w:p|a:p|text:p|text:h|row|si|c)>|<(w:br|w:tab|text:line-break|text:tab|a:br)\b[^>]*/?>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n\s*\n+")


def supported(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = _WS.sub(" ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = _NL.sub("\n", text)
    return text.strip()[:MAX_TEXT]


def _xml_text(raw: bytes) -> str:
    s = raw.decode("utf-8", "replace")
    s = _PARA.sub("\n", s)
    s = _TAG.sub(" ", s)
    return html.unescape(s)  # nur die fünf Standard-Entitäten und &#…; – keine DTD-Entitäten


def _zip_text(path: Path, pattern: str) -> str:
    rx = re.compile(pattern)
    out, total = [], 0
    with zipfile.ZipFile(path) as z:
        names = sorted((i for i in z.infolist() if rx.search(i.filename)), key=lambda i: _natural(i.filename))
        for info in names:
            if info.file_size > MAX_XML_MEMBER or total + info.file_size > MAX_XML_TOTAL:
                continue
            total += info.file_size
            with z.open(info) as f:
                out.append(_xml_text(f.read(MAX_XML_MEMBER + 1)[:MAX_XML_MEMBER]))
            if sum(len(x) for x in out) > MAX_TEXT:
                break
    return "\n".join(out)


def _natural(name: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                return ""
        except Exception:  # noqa: BLE001 – verschlüsselt mit Passwort oder nicht unterstütztem Verfahren
            return ""
    parts, size = [], 0
    for i, page in enumerate(reader.pages):
        if i >= MAX_PAGES or size > MAX_TEXT:
            break
        try:
            t = page.extract_text() or ""
        except Exception:  # noqa: BLE001 – kaputte Seite: weiter mit der nächsten
            t = ""
        parts.append(t)
        size += len(t)
    return "\n".join(parts)


def extract(path: Path) -> Tuple[str, str]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        return "unsupported", ""
    try:
        if path.stat().st_size > MAX_FILE:
            return "toolarge", ""
        if ext == ".pdf":
            text = _pdf_text(path)
        elif ext in OOXML:
            text = _zip_text(path, OOXML[ext])
        elif ext in ODF:
            text = _zip_text(path, r"^content\.xml$")
        else:
            raw = path.read_bytes()[: MAX_TEXT * 4]
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
    except Exception:  # noqa: BLE001 – kaputte oder präparierte Datei: nicht abstürzen
        return "error", ""
    text = _clean(text)
    # PDF ohne nennenswerten Text = Bild-Scan
    if len(re.sub(r"\W", "", text)) < 10:
        return "empty", ""
    return "ok", text
