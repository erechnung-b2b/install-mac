# E-Rechnungssystem – Windows Installation

## Drei Wege zur Windows-Installation

| Variante | Zielgruppe | Python nötig? | Ergebnis |
|----------|-----------|---------------|----------|
| **A. Doppelklick-Start** | Schnelltest, Entwickler | Ja, auf dem PC | Server läuft, Browser öffnet sich |
| **B. Standalone .exe bauen** | Weitergabe an Kollegen/Kunden | Nur zum Bauen | Ein Ordner, überall lauffähig |
| **C. Inno Setup Installer** | Professionelle Verteilung | Nur zum Bauen | Setup.exe mit Installation |

---

## Voraussetzung: Python installieren (für Variante A + B)

1. **Python 3.10+** herunterladen: https://www.python.org/downloads/
2. Bei der Installation **unbedingt** anhaken: ☑ **"Add Python to PATH"**
3. Nach Installation **neues** Kommandozeilenfenster öffnen und prüfen:

```
python --version
pip --version
```

Beide Befehle müssen eine Versionsnummer zeigen.

---

## Variante A: Doppelklick-Start (einfachste Methode)

**Wann:** Sie haben Python installiert und wollen das System sofort testen.

1. ZIP entpacken (Rechtsklick → "Alle extrahieren...")
2. Doppelklick auf **`starten.bat`**
3. Beim ersten Start werden automatisch die Abhängigkeiten installiert (~30 Sekunden)
4. Der Browser öffnet sich automatisch auf `http://localhost:5000`

**Fertig.** Das Konsolenfenster muss offen bleiben (Server läuft dort).

### Alternativ per Kommandozeile:

```
cd erechnung
pip install -r requirements.txt
python run.py
```

Optionaler Port: `python run.py 8080`

---

## Variante B: Standalone .exe bauen (kein Python beim Empfänger nötig)

**Wann:** Sie wollen das Programm an Kollegen/Kunden weitergeben, die kein Python installiert haben.

### Schritt 1: Bauen (einmalig, auf Ihrem PC)

1. ZIP entpacken
2. Doppelklick auf **`build_windows.bat`**
3. Warten (1-3 Minuten)
4. Ergebnis liegt in `dist\erechnung\`

### Schritt 2: Verteilen

Den gesamten Ordner `dist\erechnung\` kopieren – per USB-Stick, Netzlaufwerk, ZIP-Datei.

### Schritt 3: Starten (auf dem Ziel-PC)

Doppelklick auf `dist\erechnung\E-Rechnungssystem.exe`

**Das ist alles.** Kein Python, kein Installer, keine Administratorrechte nötig.

### Ordnerstruktur nach dem Build:

```
dist/erechnung/
├── E-Rechnungssystem.exe     ← Hauptprogramm
├── static/
│   └── index.html            ← Web-Frontend
├── data/                     ← Wird beim Start erstellt
│   ├── archiv/               ← Archivierte Rechnungen
│   ├── export/               ← DATEV/CSV-Exporte
│   └── logo/                 ← Firmenlogo
├── _internal/                ← Python-Runtime (nicht anfassen)
└── ...
```

### Hinweise zum Build:

- **Antivirus:** Manche Virenscanner blockieren PyInstaller. In dem Fall: temporär deaktivieren oder Ausnahme hinzufügen für den `dist`-Ordner.
- **Windows Defender SmartScreen:** Beim ersten Start kann ein blauer Dialog erscheinen "Der Computer wurde durch Windows geschützt". Klicken Sie auf "Weitere Informationen" → "Trotzdem ausführen". Das passiert bei allen nicht signierten .exe-Dateien.
- **Firewall:** Windows fragt möglicherweise, ob der Server Netzwerkzugriff bekommen darf. Für lokale Nutzung "Privates Netzwerk" erlauben genügt.

---

## Variante C: Professioneller Installer mit Inno Setup

**Wann:** Sie wollen eine Setup.exe wie bei kommerzieller Software – mit Startmenü-Eintrag, Desktop-Verknüpfung und Deinstallation.

### Voraussetzungen:

1. Variante B erfolgreich durchgeführt (Ordner `dist\erechnung\` existiert)
2. **Inno Setup** installieren: https://jrsoftware.org/isdl.php (kostenlos)

### Schritt 1: Inno-Setup-Script anpassen

Die Datei `installer.iss` enthält bereits ein fertiges Script. Bei Bedarf anpassen:
- Firmenname
- Versionsnummer
- Ausgabepfad

### Schritt 2: Installer bauen

1. `installer.iss` doppelklicken (öffnet sich in Inno Setup)
2. Menü: Build → Compile
3. Ergebnis: `output\E-Rechnungssystem_Setup.exe`

### Schritt 3: Verteilen

Die eine `Setup.exe`-Datei an Endanwender weitergeben. Beim Ausführen:
- Installationsassistent mit Lizenzseite
- Startmenü-Eintrag "E-Rechnungssystem"
- Desktop-Verknüpfung (optional)
- Deinstallation über "Programme und Features"

---

## Betrieb

### Daten

Alle Daten liegen im Unterordner `data/` neben dem Programm:

| Verzeichnis | Inhalt |
|------------|--------|
| `data/archiv/` | Archivierte Rechnungen (XML + Metadaten + SHA-256) |
| `data/export/` | DATEV- und CSV-Exporte |
| `data/logo/` | Hochgeladenes Firmenlogo |
| `data/sent_mails/` | Gesendete E-Mails (Protokoll) |

### Backup

Sichern Sie regelmäßig den gesamten `data/`-Ordner. Er enthält alle Rechnungen, Archivdaten und Konfigurationen.

### Port ändern

Standard: Port 5000. Ändern per Kommandozeile:

```
E-Rechnungssystem.exe 8080
```

Oder bei der Python-Variante:

```
python run.py 8080
```

### Mehrere Benutzer (Netzwerk)

Der Server bindet standardmäßig auf `127.0.0.1` (nur lokal erreichbar). Für Netzwerkzugriff in `run.py` die Zeile ändern:

```python
app.run(host="0.0.0.0", port=port)
```

Dann kann das System von anderen PCs im Netzwerk über `http://COMPUTERNAME:5000` erreicht werden.

---

## Fehlerbehebung

| Problem | Lösung |
|---------|--------|
| "Python nicht gefunden" | Python installieren mit "Add to PATH", **neues** Konsolenfenster öffnen |
| Port 5000 belegt | Anderen Port wählen: `python run.py 8080` |
| Antivirus blockiert .exe | Ausnahme hinzufügen für den Programmordner |
| "DLL not found" nach Build | Antivirus hat Build-Dateien gelöscht – Ausnahme + neu bauen |
| Browser öffnet sich nicht | Manuell http://localhost:5000 aufrufen |
| Leere Seite im Browser | Konsolenfenster prüfen – Fehlermeldung dort sichtbar |

---

## Systemanforderungen

- **Betriebssystem:** Windows 10 (Build 1903+) oder Windows 11
- **RAM:** 256 MB frei (typisch 80-120 MB Verbrauch)
- **Festplatte:** ~150 MB für die Anwendung + Platz für Rechnungsdaten
- **Browser:** Chrome, Edge, Firefox (aktuell)
- **Netzwerk:** Nur für E-Mail-Empfang/-Versand nötig; UI läuft lokal
