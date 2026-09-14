# E-Rechnungssystem

Software für elektronische Rechnungen nach deutschem Recht.

**XRechnung | ZUGFeRD | EN 16931**

## Funktionen

- Eingangsrechnungen empfangen, validieren und freigeben (XRechnung XML, ZUGFeRD-PDF)
- Ausgangsrechnungen erstellen – als XRechnung (XML) und als ZUGFeRD-PDF/A-3
- Offizielle Prüfung mit dem KoSIT-Validator (wird automatisch eingerichtet)
- Freigabe-Workflow mit Vier-Augen-Prinzip
- DATEV-Export für die Buchhaltung
- GoBD-konforme Archivierung mit SHA-256
- GiroCode QR für Banking-Apps
- Stornierung mit Gutschrift (Typ 381)
- E-Mail-Empfang und -Versand (IMAP/SMTP)

## Installation

Sie brauchen **nichts vorab zu installieren** – kein Python, kein Java, keine
Administratorrechte. Die Installation lädt alles Nötige in den Programmordner
(geprüft per SHA-256) und dauert beim ersten Mal etwa 3–5 Minuten.

### Windows

1. [Programm herunterladen](https://github.com/erechnung-b2b/install/archive/refs/heads/main.zip)
   und entpacken (Rechtsklick → „Alle extrahieren…“), z. B. nach `C:\E-Rechnungssystem`
2. Doppelklick auf **`erstinstallation.bat`**
   – erscheint eine Sicherheitswarnung: *Ausführen* bzw. *Weitere Informationen* → *Trotzdem ausführen*
3. Danach Doppelklick auf **`starten.bat`** – der Browser öffnet http://localhost:5000

Ab dem zweiten Mal genügt `starten.bat`. Fehlt die Installation noch, holt
`starten.bat` sie selbst nach.

### macOS (Apple Silicon und Intel)

**Empfohlen – ein Befehl, vollautomatisch:** Programm *Terminal* öffnen
(Programme → Dienstprogramme), diese Zeile einfügen und Enter drücken:

```
curl -fsSL https://raw.githubusercontent.com/erechnung-b2b/install-mac/main/mac-installation.sh | bash
```

Das Programm landet in `~/E-Rechnungssystem`, auf dem Schreibtisch erscheint
**„E-Rechnung starten.command“**. Derselbe Befehl aktualisiert später auf die
neueste Version, Ihre Daten bleiben erhalten.

**Alternativ per Download:**

1. [Programm herunterladen](https://github.com/erechnung-b2b/install-mac/archive/refs/heads/main.zip) und entpacken
2. **Rechtsklick** auf **`Installieren.command`** → *Öffnen* → *Öffnen*
   – macOS 15 (Sequoia) und neuer: nach dem ersten Doppelklick *Systemeinstellungen →
   Datenschutz & Sicherheit → „Dennoch öffnen“*
3. Nach der Installation startet das Programm auf Wunsch sofort; später
   Doppelklick auf **„E-Rechnung starten.command“**

### Linux / eigener Server

Einzelplatz: `bash erstinstallation.sh`, danach `./starten.sh`.
Für den Betrieb im Netzwerk mit Anmeldung, systemd-Dienst und HTTPS siehe
[docs/Server-Installation.md](docs/Server-Installation.md).

## Update

Windows: Doppelklick auf `update.bat` · macOS/Linux: `bash update.sh`
(oder den Installationsbefehl oben erneut ausführen). Vor jedem Update wird
`data/` nach `backup/` gesichert.

## Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [Installationsanleitung](docs/Installationsanleitung.md) | Schritt für Schritt, Lizenz, Fehlerbehebung |
| [Server-Installation](docs/Server-Installation.md) | Betrieb auf einem Linux-Server |
| [KoSIT-Validator](docs/KOSIT_SETUP.md) | Hintergrund zur offiziellen Prüfung |

## Systemanforderungen

- Windows 10 (ab Version 1803) oder 11, 64-Bit
- macOS 12 (Monterey) oder neuer, Apple Silicon oder Intel
- Linux x86_64 oder ARM64 (glibc)
- Internetverbindung bei der Installation
- ca. 500 MB Festplatte (inkl. Python, Java und KoSIT-Validator)
- Browser: Chrome, Edge, Safari oder Firefox

## Was die Installation anlegt

```
laufzeit/python/   eigenes Python 3.12 (nur für dieses Programm)
laufzeit/java/     Java-Laufzeit für den KoSIT-Validator
.venv/             Programmumgebung mit allen Paketen
tools/kosit/       offizieller KoSIT-Validator mit XRechnung-Konfiguration
data/              Ihre Rechnungen und Einstellungen – regelmäßig sichern!
```

Deinstallation: Programmordner löschen (vorher `data/` sichern). Es werden
keine Einträge außerhalb des Programmordners angelegt (Ausnahme: die Startdatei
auf dem Mac-Schreibtisch beim Ein-Befehl-Weg).

## Lizenz

Proprietär. Alle Rechte vorbehalten. 28 Tage Testzeitraum, danach Lizenzcode
unter *Einstellungen → Lizenz*.
