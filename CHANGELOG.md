# Änderungen

Alle nennenswerten Änderungen an diesem Projekt. Versionen nach [Semantic Versioning](https://semver.org/lang/de/).

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

[0.1.0]: https://github.com/rinkelzz/ordner-regal/releases/tag/v0.1.0
