# Installationsanleitung

**E-Rechnungssystem** — Version 1.0, Stand April 2026

---

## Ueberblick

Das E-Rechnungssystem laeuft als lokale Webanwendung. Es wird keine Datenbank und kein externer Server benoetigt. Alle Daten bleiben auf Ihrem Rechner.

Die Software laeuft auf **Windows** und **macOS**.

---

## Windows

### Schritt 1 — Python installieren

1. Oeffnen Sie https://www.python.org/downloads/
2. Laden Sie Python 3.10 oder hoeher herunter
3. **Wichtig:** Setzen Sie den Haken bei **"Add Python to PATH"**
4. Klicken Sie auf **Install Now**

Pruefung: Eingabeaufforderung oeffnen (Windows + R, `cmd`), eingeben:

```
python --version
```

### Schritt 2 — Software herunterladen

Download-Link:

```
https://github.com/erechnung-b2b/install/archive/refs/heads/main.zip
```

Die Datei `install-main.zip` entpacken, z.B. nach:

```
C:\E-Rechnungssystem\install-main\
```

### Schritt 3 — Installation

1. Ordner `C:\E-Rechnungssystem\install-main\` im Explorer oeffnen
2. Doppelklick auf **build_windows.bat**
3. Die Installation laeuft automatisch (1-3 Minuten):

```
[1/4] Python gefunden
[2/4] Installiere Abhaengigkeiten...        OK
[3/4] Pruefe Anwendung...                   OK
[4/4] Erstelle Standalone-Anwendung...      OK

Build erfolgreich!
```

### Schritt 4 — Programm starten

1. Doppelklick auf **starten.bat**
2. Browser oeffnet sich automatisch mit **http://localhost:5000**
3. Das E-Rechnungssystem ist betriebsbereit

Programm beenden: Konsolenfenster schliessen oder Strg+C druecken.

---

## macOS

### Schritt 1 — Python installieren

Terminal oeffnen (Programme > Dienstprogramme > Terminal) und eingeben:

```
python3 --version
```

Falls Python fehlt, installieren Sie es ueber eine der folgenden Methoden:

**Mit Homebrew (empfohlen):**

```
brew install python3
```

**Oder direkt von python.org:**

Oeffnen Sie https://www.python.org/downloads/ und laden Sie den macOS-Installer herunter.

### Schritt 2 — Software herunterladen

Im Terminal eingeben:

```
cd ~/Desktop
curl -L -o install-mac.zip https://github.com/erechnung-b2b/install-mac/archive/refs/heads/main.zip
unzip install-mac.zip
cd install-mac-main/mac-paket
```

Oder den Download-Link im Browser oeffnen und die ZIP-Datei manuell entpacken.

### Schritt 3 — Installation

Im Terminal, im entpackten Ordner:

```
chmod +x install.sh starten.sh build_mac.sh
./install.sh
```

Die Installation laeuft automatisch:

```
[1/4] Pruefe Python...          Python 3.12.4
[2/4] Installiere Abhaengigkeiten...   OK
[3/4] Pruefe Anwendung...              OK
[4/4] Erstelle Datenverzeichnisse...   OK

Installation erfolgreich!
```

### Schritt 4 — Programm starten

```
./starten.sh
```

Der Browser oeffnet sich automatisch mit **http://localhost:5000**. Programm beenden mit Strg+C im Terminal.

---

## Testphase und Lizenz

### Kostenlose Testphase (28 Tage)

Nach dem ersten Start laeuft die Software 28 Tage im vollen Funktionsumfang. Ein Hinweis in der Kopfzeile zeigt die verbleibenden Tage an.

### Nach Ablauf der Testphase

Die Software wechselt in den Lesemodus. Vorhandene Rechnungen bleiben einsehbar, aber neue Rechnungen erstellen, freigeben oder exportieren ist nicht mehr moeglich.

### Lizenz aktivieren

1. Starten Sie das E-Rechnungssystem
2. Klicken Sie auf **Einstellungen** (Zahnrad-Symbol)
3. Im Abschnitt **Lizenz** sehen Sie Ihre **Geraete-ID** (10-stellige Zahl)
4. Teilen Sie diese Geraete-ID Ihrem Anbieter mit
5. Sie erhalten einen Lizenzschluessel (beginnt mit `ERECH-`)
6. Geben Sie den Schluessel ein und klicken Sie auf **Aktivieren**

Die Lizenz ist an Ihren Computer gebunden.

---

## Erste Schritte

### Rechnung hochladen

Klicken Sie auf **Posteingang**, ziehen Sie eine XML- oder PDF-Datei in das Upload-Feld. Unterstuetzte Formate: XRechnung (XML) und ZUGFeRD (PDF mit XML).

### Rechnung erstellen

Klicken Sie auf **Rechnung erstellen**, fuellen Sie die Pflichtfelder aus, klicken Sie auf **XRechnung erzeugen**.

### Rechnung freigeben und exportieren

Oeffnen Sie eine Rechnung, klicken Sie auf **Freigeben**, danach auf **DATEV-Export**.

---

## Daten und Backup

Alle Daten liegen im Unterordner `data/`:

```
data/
  invoices.json       Alle Rechnungen
  archiv/             Archivierte Originaldateien
  export/             DATEV- und CSV-Exporte
  license.json        Lizenzstatus
  device_id.txt       Geraete-ID
```

Sichern Sie den Ordner `data/` regelmaessig.

---

## Standalone-App (optional)

### Windows

Der `build_windows.bat` erzeugt unter `dist\erechnung\` eine Version die ohne Python laeuft. Diesen Ordner auf beliebige Windows-PCs kopieren und `E-Rechnungssystem.exe` starten.

### macOS

```
./build_mac.sh
```

Erzeugt unter `dist/erechnung/` eine standalone Version. Den Ordner auf beliebige Macs kopieren und `E-Rechnungssystem` starten.

---

## Fehlerbehebung

| Problem | Windows | macOS |
|---|---|---|
| Python nicht gefunden | Mit "Add to PATH" neu installieren | `brew install python3` |
| Browser oeffnet nicht | http://localhost:5000 manuell oeffnen | http://localhost:5000 manuell oeffnen |
| Port 5000 belegt | `python webapp.py 8080` | `python3 webapp.py 8080` |
| Abhaengigkeit fehlt | `pip install -r requirements.txt` | `pip3 install -r requirements.txt` |
| Antivirus blockiert EXE | Ausnahme hinzufuegen | Systemeinstellungen > Sicherheit > Trotzdem oeffnen |
| "Permission denied" (Mac) | — | `chmod +x starten.sh install.sh` |

---

## Systemvoraussetzungen

| Komponente | Windows | macOS |
|---|---|---|
| Betriebssystem | Windows 10/11, 64-Bit | macOS 12 (Monterey) oder neuer |
| Python | 3.10 oder hoeher | 3.10 oder hoeher |
| RAM | mindestens 4 GB | mindestens 4 GB |
| Festplatte | ca. 150 MB | ca. 150 MB |
| Browser | Chrome, Edge oder Firefox | Chrome, Safari oder Firefox |

---

## Kontakt

**energieberatung rolf krause**
Dipl. Ing.
E-Mail: energieberatung@rolfkrause.com

---

*E-Rechnungssystem v1.0 — XRechnung | ZUGFeRD | EN 16931 — Stand April 2026*
