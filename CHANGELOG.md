# Änderungen

Alle nennenswerten Änderungen an diesem Projekt. Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

## [0.3.1] – 2026-11-11

### Behoben
- **Ziehen & Ablegen** funktionierte nicht in allen Browsern (Firefox lässt Knöpfe nicht ziehen) und gar nicht am
  Handy/Tablet. Ordner im Regal, Dokumente auf Register und Dateien aus dem Eingang auf Ordnerrücken lassen sich jetzt
  überall ziehen – mit Maus, Stift oder Finger (am Touchscreen kurz gedrückt halten, dann ziehen).
- **Freigabe `homes`** lässt sich jetzt verwenden, z. B. für einen Scanner-Eingang in `homes/<benutzer>/Scans`.
  Wie in DSM sieht dort jeder nur seinen **eigenen Ordner**; wer in DSM die ganze Freigabe `homes` lesen darf
  (Administratoren), sieht alle. Datei-Auswahl und Zielordner starten in `homes` gleich im eigenen Ordner.

## [0.3.0] – 2026-11-11

### Neu: Volltextsuche
- **Text lesen** aus PDF (pypdf), Word/Excel/PowerPoint (docx, xlsx, pptx), LibreOffice (odt, ods, odp) und
  Textdateien – im Hintergrund, kurz nach dem Ablegen; geänderte Dateien werden bei der nächsten Runde neu gelesen.
  Auch die Dateien in den **Eingängen** werden gelesen und gefunden; beim Einsortieren wird der schon gelesene Text
  übernommen.
- **Suche** über Titel, Dateiname, Notiz, Schlagworte **und Text**, mit **Textausschnitt** und markiertem Suchwort.
  **Enter** öffnet eine Ergebnisseite mit Filtern nach Regal und Schlagwort und den Treffern im Eingang.
- Index mit **SQLite FTS5** (Wortanfänge, ohne Akzent-Unterschiede); ohne FTS5 sucht das Regal einfacher im Text.
- **„Kein Text“** bei PDFs ohne Textebene (Bild-Scans), mit Hinweis, wie man am Scanner durchsuchbare PDFs einstellt.
- **Verwaltung → Freigaben → Volltextsuche:** Stand des Index, „Alles neu lesen“.

### Verbessert
- Freigaben, die es nicht mehr gibt oder die ausgeschlossen sind (z. B. `homes` aus 0.1.0), verschwinden aus der Liste.
- Geänderte Dateien bekommen beim Lesen auch neue Größe und neuen Fingerabdruck.

## [0.2.1] – 2026-11-11

### Behoben
- **Port-Konflikt nach Update:** Wer 0.1.0 noch mit dem alten Standard-Port 8790 installiert hatte (den nutzt
  Foto-Globus), behielt ihn beim Update. Das Update stellt 8790 jetzt automatisch auf einen freien Port ab 8800 um.
- **Belegter Port beim Start:** Statt nicht zu starten, weicht das Paket auf den nächsten freien Port ab 8800 aus
  (ohne die Ports von Zeiterfassung, Zeitmanagement, IT-Dokumentation und Foto-Globus) und speichert ihn. Das
  DSM-Fenster funktioniert weiter; Admins sehen einen Hinweis, die Paketquelle im Paket-Zentrum anzupassen.

## [0.2.0] – 2026-11-11

### Neu: Scanner-Eingang
- **Eingänge:** Unter Verwaltung → Freigaben trägst du Ordner ein, in die der Scanner speichert (z. B. `Scans/Eingang`).
  Neue Dateien erscheinen im neuen Reiter **📥 Eingang** mit Zähler und als Ablagekorb unten im Regal. Jeder sieht nur
  Eingänge, deren Freigabe er in DSM lesen darf.
- **Einsortieren** per Ziehen auf einen Ordnerrücken im Mini-Regal oder über Regal/Ordner/Register. Titel, Datum (aus
  dem Dateinamen oder dem Dateidatum), Schlagworte und ein **neuer Dateiname** („Datum Titel.pdf“) sind vorbelegt
  und änderbar.
- **Zielordner** pro Regal-Ordner, optional abweichend pro Register. Beim Einsortieren wird die Datei dorthin
  verschoben. Ohne Zielordner fragt das Regal nach – mit „als Zielordner merken“ und neuem Unterordner – oder
  verknüpft die Datei nur.
- **Sicheres Verschieben:** nie überschreiben („Name (2).pdf“), atomar auf demselben Volume, kopieren + löschen
  zwischen Volumes. Nur mit DSM-Schreibrecht auf Quelle und Ziel; jedes Verschieben steht im Änderungsprotokoll.
- **Eigene Konten:** Freigaben pro Benutzer jetzt mit „lesen“ oder „lesen & schreiben“.

## [0.1.1] – 2026-11-11

### Verbessert
- **Belegter Port wird erkannt:** Die Installation prüft, ob der im Assistenten gewählte Port auf der NAS schon
  belegt ist, bricht dann mit einem klaren Hinweis ab und schlägt einen freien Port vor. Startet das Paket später auf
  einem belegten Port, steht der Grund im Protokoll des Pakets.
- Standard-Port ist **8800** (8790 nutzt schon Foto-Globus).

## [0.1.0] – 2026-11-11

Erste Version.

### Neu
- **Regale mit Fächern:** beliebig viele Regale (z. B. „Privat“, „Praxis“), jedes mit eigenen Fächern.
- **Ordner** mit Rückenschild, Farbe und Breite; per Ziehen umstellen, auch in ein anderes Fach.
- **Register** im Ordner mit eigener Farbe und Reihenfolge.
- **Dokumente ablegen** aus den freigegebenen Ordnern der NAS: Datei-Auswahl mit Filter und Vorschau, mehrere Dateien
  auf einmal, Datum und Schlagworte. Die Dateien bleiben, wo sie sind – das Regal speichert nur Verweise.
- **Ein Dokument an mehreren Orten**, verschieben oder zusätzlich ablegen (Dialog oder Ziehen auf ein Register).
- **Vorschau** von PDF und Bildern im Ordner, Öffnen in neuem Tab, Herunterladen.
- **Titel, Datum, Notiz und Schlagworte** pro Dokument, **Suche** in der Kopfzeile, Schlagwort-Wolke.
- **Rechte:** pro Regal (ansehen/bearbeiten, „für alle sichtbar“) und pro Freigabe wie in DSM – die App übernimmt bei
  jeder DSM-Anmeldung, welche Freigaben das Konto lesen darf. Eigene Konten bekommen Freigaben vom Admin.
- **Verwaltung → Freigaben:** freigegebene Ordner fürs Regal einschalten, Zugriff des Paketbenutzers prüfen.
- **Wiederfinden:** nächtliche Prüfung, ob die Dateien noch da sind; verschobene oder umbenannte Dateien werden über
  den SHA-256-Fingerabdruck in den eingeschalteten Freigaben wiedergefunden (auch per Knopf).
- **Plattform** (aus IT-Dokumentation übernommen): eigenes DSM-Fenster mit Single Sign-on, Login mit DSM-Konto inkl.
  Zwei-Faktor-Code, vier Designs, Name und Logo, nächtliche Datensicherung, Updates übers Paket-Zentrum,
  Änderungsprotokoll mit CSV-Export, Port änderbar, Testmail über eigenen SMTP-Server.

[0.3.1]: https://github.com/rinkelzz/ordner-regal/releases/tag/v0.3.1
[0.3.0]: https://github.com/rinkelzz/ordner-regal/releases/tag/v0.3.0
[0.2.1]: https://github.com/rinkelzz/ordner-regal/releases/tag/v0.2.1
[0.2.0]: https://github.com/rinkelzz/ordner-regal/releases/tag/v0.2.0
[0.1.1]: https://github.com/rinkelzz/ordner-regal/releases/tag/v0.1.1
[0.1.0]: https://github.com/rinkelzz/ordner-regal/releases/tag/v0.1.0
