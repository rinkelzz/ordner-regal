# Sicherheit

Bitte Sicherheitslücken **nicht** als öffentliches Issue melden, sondern über
[GitHub → Security → Report a vulnerability](https://github.com/rinkelzz/ordner-regal/security/advisories/new).
Ich melde mich so schnell wie möglich.

Unterstützt wird jeweils die neueste Version unter [Releases](https://github.com/rinkelzz/ordner-regal/releases).

## Was die App selbst absichert

- **Passwörter** werden mit PBKDF2-SHA256 (200 000 Runden, eigenes Salz) gespeichert, Sitzungen nur als
  SHA-256-Hash. Beim Passwortwechsel und beim Deaktivieren eines Benutzers enden alle seine Sitzungen.
- **Login-Sperre:** 5 Fehlversuche pro Benutzername und Adresse, 10 pro Benutzername insgesamt und 30 pro Adresse
  (gleich welcher Name) – jeweils 15 Minuten. Die Tabelle ist in der Größe begrenzt (50 000 Einträge), Abgelaufenes
  wird regelmäßig entfernt, und bloßes Nachsehen legt keine Einträge an.
- **Kein http aus dem Netz (Standard):** Die Weboberfläche gibt es nur über das DSM-Fenster, über die NAS selbst
  (DSM-Reverse-Proxy) und über https (auch über einen eingetragenen eigenen Reverse Proxy). Direkte http-Zugriffe aus
  dem Netz bekommen eine Hinweisseite; nur Paketquelle und Statuscheck bleiben erreichbar.
  Einschaltbar unter Verwaltung → System → Zugriff bzw. `ORG_HTTP_ACCESS=1`. Die App lauscht weiter auf allen
  Schnittstellen (`ORG_HOST=0.0.0.0`), damit ein Reverse Proxy auf einem anderen Gerät sie erreicht; wer das nicht
  braucht, setzt `ORG_HOST=127.0.0.1` in der Paketkonfiguration.
- **CSRF:** Änderungen per Login-Cookie werden nur von der eigenen Seite angenommen (`Sec-Fetch-Site`
  bzw. `Origin`/`Referer`). Läuft die App hinter einem Proxy, der den `Host`-Header umschreibt, die
  **Öffentliche Adresse** in den Einstellungen eintragen.
- **DSM-Konten:** Per Single Sign-on kommen standardmäßig nur DSM-Administratoren und vom Admin angelegte Benutzer
  hinein. Unbekannte DSM-Konten werden nicht automatisch angelegt (einschaltbar; sie sehen dann nur Regale, die für alle freigegeben sind).
  Ob jemand DSM-Administrator ist, prüft die App selbst (Gruppe `administrators`) – ein mitgeschickter Header zählt
  nicht. Vollzugriff, der nur aus DSM kam, wird wieder entzogen, sobald die Person kein DSM-Administrator mehr ist
  oder „DSM-Administratoren bekommen Vollzugriff“ ausgeschaltet wird; von Hand vergebener Vollzugriff bleibt.
- **DSM-Passwortprüfung** nur gegen die DSM-Anmeldung der NAS selbst: `http://127.0.0.1:5000` oder
  `https://127.0.0.1:5001` (andere Ports nur per `ORG_DSM_PORTS` in der Paketkonfiguration, nicht über die Oberfläche),
  ohne Proxy-Umgebungsvariablen und ohne Weiterleitungen. DSM-Passwörter verlassen die NAS nie und gehen an keinen
  anderen Dienst. Bei https über Loopback wird das Zertifikat nicht geprüft (DSM nutzt dort meist ein nicht auf
  127.0.0.1 ausgestelltes Zertifikat); die Verbindung verlässt die NAS nicht.
- **Single Sign-on im DSM-Fenster:** Der Schlüssel (`.proxy_secret` im Paketordner) wird nur von
  Verbindungen über 127.0.0.1 ohne `X-Forwarded-For` angenommen, also nur von `app.cgi` auf der NAS selbst.
  Er wird bei jedem Start neu erzeugt. `app.cgi` läuft als Paketbenutzer (auf einer NAS mit DSM 7 geprüft), die
  Datei ist deshalb nur für ihn lesbar (Modus 0600). Lässt sich das nicht setzen, bleibt sie für alle lokalen Benutzer
  der NAS lesbar – die Verwaltung zeigt dann einen Hinweis.
  **Restrisiko:** Wer auf der NAS Programme als Paketbenutzer oder root ausführen kann, kann den Schlüssel
  lesen und sich als beliebiger DSM-Benutzer ausgeben. Wer das ausschließen will, schaltet unter
  Verwaltung → System die **Anmeldung im DSM-Fenster** aus; der Schlüssel wird dann gelöscht und nicht mehr angenommen.
- **Echte Absenderadresse** (`X-Forwarded-For`) wird nur übernommen, wenn die Anfrage vom Reverse Proxy
  auf derselben NAS (127.0.0.1) kommt oder von einem Proxy, der unter **Verwaltung → System → Eigener Reverse Proxy**
  (bzw. `ORG_TRUSTED_PROXIES`) eingetragen ist. Bei einer Kette zählt von rechts die erste Adresse, die selbst kein
  eingetragener Proxy ist; Einträge, die ein Client selbst mitschickt, bleiben wirkungslos. Netze weiter als /16
  werden nicht angenommen. Ist ein Proxy auf einem anderen Gerät **nicht** eingetragen, sieht die App nur dessen
  Adresse: die Login-Sperre trifft dann alle gemeinsam, und die Paketquelle hält Zugriffe übers Internet für
  lokal.
- **Keine Klartext-Zugangsdaten in Sicherungen:** Das SMTP-Passwort wird vor dem Sichern entfernt (inklusive
  `VACUUM`, damit es auch nicht in freien Seiten der Datei steht). Die Dokumente selbst sind nicht in der Sicherung.
- **CSV-Exporte** entschärfen Zellen, die mit `=`, `+`, `-`, `@`, Tab oder CR beginnen (Formel-Injection).
- **Paketquelle** (`/spk`) nur aus dem lokalen Netz erreichbar.

## Zugriff auf Dateien

- **Nur Verweise:** Das Regal speichert Freigabe + relativen Pfad + Fingerabdruck (SHA-256). Es lädt keine Dokumente
  hoch und ändert oder löscht keine Dateien. Ein Dokument aus dem Regal zu entfernen lässt die Datei auf der NAS
  unberührt.
- **Verschieben (nur Scanner-Eingang, ab 0.2):** Einzige Stelle, an der das Regal Dateien bewegt. Nur Dateien, die
  direkt in einem eingetragenen Eingang liegen; Ziel nur ein Ordner in einer eingeschalteten Freigabe. Verlangt
  DSM-Schreibrecht des Benutzers auf Quelle und Ziel und Schreibrecht des Paketbenutzers. Am Ziel wird nie
  überschrieben: auf demselben Volume über einen harten Link (schlägt fehl, wenn das Ziel existiert) und danach
  Löschen des Originals, sonst exklusives Anlegen + Kopieren + Löschen. Dateinamen werden bereinigt (keine Pfadteile,
  keine Sonderzeichen, Endung bleibt). Neue Unterordner nur mit Schreibrecht. Alles steht im Änderungsprotokoll.
- **Nur eingeschaltete Freigaben:** Der Admin wählt unter Verwaltung → Freigaben aus, welche freigegebenen Ordner
  das Regal benutzt. Zusätzlich muss der Paketbenutzer `ordnerregal` in DSM Zugriff darauf haben. `homes`
  (persönliche Ordner aller Benutzer) und technische Freigaben (z. B. `docker`, `web_packages`) sind ausgeschlossen.
- **Pfade:** Keine absoluten Pfade, kein `..`, keine DSM-Systemordner (`@eaDir`, `#recycle`, `#snapshot` …).
  Symbolische Links, die aus der Freigabe hinausführen, werden abgelehnt (Prüfung über den echten Pfad).
- **Rechte wie in DSM:** Für DSM-Benutzer übernimmt die App bei jeder Anmeldung, welche Freigaben das Konto lesen
  darf – im DSM-Fenster fragt `app.cgi` dafür DSM auf der NAS selbst mit der Sitzung des Benutzers
  (`SYNO.FileStation.List/list_share`) und reicht das Ergebnis zusammen mit dem Proxy-Schlüssel weiter; beim Login mit
  DSM-Passwort fragt die App mit der gerade erhaltenen DSM-Sitzung und meldet sie danach gleich wieder ab. Ein vom
  Browser mitgeschickter Header `X-DSM-Shares` zählt nie (nur zusammen mit dem Proxy-Schlüssel über 127.0.0.1).
  Dokumente aus Freigaben ohne Leserecht werden nicht angezeigt, nicht ausgeliefert und nicht gefunden.
- **Grenzen:** Geprüft wird **pro Freigabe**, nicht pro Unterordner oder Datei (erweiterte DSM-ACLs in Unterordnern
  gelten im Regal nicht). Entzieht DSM einem Konto eine Freigabe, gilt das ab der nächsten Anmeldung bzw. beim
  nächsten Öffnen des DSM-Fensters; eine laufende Sitzung außerhalb von DSM (Login am Handy) behält bis dahin den
  alten Stand (Sitzungen enden spätestens nach 7 Tagen).
- **Auslieferung:** Direkt im Browser angezeigt werden nur PDF, PNG, JPEG, GIF, WebP und reiner Text – alles andere
  (auch HTML und SVG) kommt als Download mit `application/octet-stream`. Immer mit `X-Content-Type-Options: nosniff`,
  außer bei PDF zusätzlich `Content-Security-Policy: sandbox`.
- **Rechte am Regal** werden bei jeder Schnittstelle geprüft (auch wenn die Oberfläche Knöpfe ohnehin ausblendet):
  Ansehen reicht nicht zum Ablegen, Umstellen oder Löschen; Regale anlegen und löschen nur mit Vollzugriff.

## Hinweise für den Betrieb

- Für private Dokumente ist ein **VPN** zur NAS meist besser als eine öffentliche Adresse. Wenn
  doch von außen, dann nur per **HTTPS** (Reverse Proxy von DSM mit Zertifikat), nie den Port (Standard 8800)
  direkt. Bei Zugriff per `http://` aus dem Internet zeigt die App eine Warnung.
- **Vollzugriff** nur an Personen geben, die ihn wirklich brauchen. DSM-Administratoren haben ihn standardmäßig –
  unter Verwaltung → Einstellungen abschaltbar. Eigene Konten mit Vollzugriff sehen alle eingeschalteten Freigaben.
- Dem Paketbenutzer `ordnerregal` nur die Freigaben geben, die ins Regal sollen.
- Updates kommen aus den GitHub-Releases dieses Repos. Sie sind nicht zusätzlich signiert – geschützt
  sind sie durch HTTPS und das (per Zwei-Faktor gesicherte) GitHub-Konto des Maintainers.
- Das SMTP-Passwort liegt in der Datenbank der App (`/var/packages/ordnerregal/var/`), lesbar nur für
  den Paketbenutzer. Am besten ein eigenes Mail-Konto oder App-Passwort nur für den Versand verwenden.
- Den freigegebenen Ordner für Sicherungen nicht für alle Benutzer freigeben – die Sicherungen enthalten
  das Regal (Titel, Pfade, Notizen, Schlagworte) und die Passwort-Hashes.

# Security (English)

Please report vulnerabilities privately via GitHub's *Report a vulnerability* button, not as a public issue.
The shelf only stores references (share + relative path + SHA-256) and never modifies files in 0.1. Only shared folders
enabled by the admin and readable by the package user are used; `homes` is excluded. Paths are normalised, `..`,
DSM system folders and symlinks leaving the share are rejected. Users only see documents from shared folders they can
read in DSM (share level, not sub-folder ACLs), taken over on every DSM login/SSO. Only PDF, images and plain text are
shown inline; everything else is sent as a download with `nosniff`.
