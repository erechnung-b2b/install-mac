# E-Rechnungssystem

Standalone-Software fuer elektronische Rechnungen nach deutschem Recht.

**XRechnung | ZUGFeRD | EN 16931**

## Funktionen

- Eingangsrechnungen empfangen, validieren und freigeben
- Ausgangsrechnungen erstellen (XRechnung XML)
- **PDF-Import für Energieberatungs-Rechnungen** (automatische Umwandlung in XRechnung)
- **ZUGFeRD-PDF/A-3-Erzeugung** (Hybrid-PDF mit eingebettetem XML)
- **KoSIT-Validator-Hook** (optional, offizielle XRechnung-Konformitätsprüfung)
- Freigabe-Workflow mit Vier-Augen-Prinzip
- DATEV-Export fuer die Buchhaltung
- GoBD-konforme Archivierung mit SHA-256
- GiroCode QR fuer Banking-Apps
- Stornierung mit Gutschrift (Typ 381)
- E-Mail-Empfang und -Versand (IMAP/SMTP)
- Alle Daten bleiben beim Neustart erhalten

## Schnellstart

### Empfohlen: Erstinstallation mit einem Klick

Das Skript `erstinstallation.bat` (Windows) bzw. `erstinstallation.sh` (macOS)
installiert **automatisch alles**, was die Software braucht: Python, Java, alle
Python-Pakete, den offiziellen KoSIT-Validator inklusive XRechnung-Konfiguration
und legt die Datenverzeichnisse an.

**Windows:**

```
1. Repository herunterladen (gruener Button "Code" > "Download ZIP") und entpacken
2. Doppelklick auf erstinstallation.bat
3. Warten (3-5 Minuten beim ersten Mal — laedt ggf. Python, Java und KoSIT)
4. Anschliessend Doppelklick auf starten.bat
5. Browser oeffnet sich automatisch auf http://localhost:5000
```

**macOS:**

```
1. Repository herunterladen und entpacken
2. Terminal oeffnen, in den entpackten Ordner wechseln
3. Ausfuehren: ./erstinstallation.sh
4. Warten (3-5 Minuten beim ersten Mal)
5. Anschliessend ausfuehren: ./starten.sh
6. Browser oeffnet sich automatisch auf http://localhost:5000
```

Ab dem zweiten Start reicht `starten.bat` bzw. `./starten.sh` — die Erstinstallation
muss nur einmal laufen.

### Standalone-EXE bauen (optional)

```
1. Doppelklick auf build_windows.bat
2. Warten (1-3 Minuten)
3. Ergebnis in dist\erechnung\ - laeuft ohne Python
```

## Dokumentation

| Dokument | Beschreibung |
|----------|-------------|
| [Installationsanleitung](docs/Installationsanleitung.md) | Kurzanleitung zur Einrichtung |
| [Bedienungsanleitung](docs/Bedienungsanleitung.md) | Ausfuehrliche Anleitung fuer Anwender |
| [README_Windows.md](README_Windows.md) | Technische Details zu Windows-Installation und EXE-Build |

## Systemanforderungen

- Windows 10/11 oder macOS 12+ (Apple Silicon und Intel)
- Python 3.10+ (wird durch erstinstallation.bat / .sh automatisch installiert)
- Java 17+ (optional, fuer KoSIT-Validator — wird ebenfalls automatisch installiert)
- Browser: Chrome, Edge, Safari oder Firefox
- ca. 250 MB Festplatte (mit KoSIT-Validator)

## Projektstruktur

```
erechnung/
├── starten.bat              Programm starten
├── build_windows.bat        EXE bauen
├── run.py                   Windows-Launcher
├── webapp.py                Web-Server (Flask)
├── persistence.py           Datenspeicherung (JSON)
├── models.py                Datenmodell
├── xrechnung_generator.py   XRechnung XML erzeugen
├── xrechnung_parser.py      XRechnung XML lesen
├── validator.py             30+ Geschaeftsregeln
├── inbox.py                 Eingangsverarbeitung + Dubletten
├── wf_engine.py             Freigabe-Workflow
├── export.py                DATEV/CSV-Export
├── archive.py               GoBD-Archivierung
├── girocode.py              EPC QR-Code
├── static/index.html        Web-Frontend
├── test_erechnung.py        81 Unit-Tests
├── requirements.txt         Abhaengigkeiten
└── data/                    Daten (nicht im Repository)
```

## Lizenz

Proprietaer. Alle Rechte vorbehalten.
