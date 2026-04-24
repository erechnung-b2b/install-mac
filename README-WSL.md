# E-Rechnungssystem unter WSL / Linux / macOS starten

## Kurzanleitung (WSL)

```bash
# 1. WSL öffnen (Windows Terminal → Ubuntu)

# 2. Projektordner vorbereiten
mkdir -p ~/erechnung
cd ~/erechnung

# 3. Dateien aus Windows nach WSL kopieren (einmalig)
cp -r /mnt/c/Users/Rolf/Claude/erechnung-komplett/install-main/erechnung/* .

# 4. Unix-Skripte reinkopieren (aus diesem Paket)
#    install.sh und starten.sh in ~/erechnung/ ablegen

# 5. Ausführbar machen
chmod +x install.sh starten.sh

# 6. Installieren (einmalig)
./install.sh

# 7. Starten
./starten.sh
```

Browser öffnet sich automatisch unter `http://localhost:5000`.
Falls Port 5000 belegt ist, wird automatisch 5001, 5002... gesucht.

## Was install.sh macht

1. Prüft Python 3.10+ (sucht python3.12, 3.11, 3.10, python3)
2. Erstellt `.venv/` Virtual Environment (isoliert von System-Python)
3. Installiert alle Pakete aus `requirements.txt` in die venv
4. Legt Datenverzeichnisse an (`data/archiv`, `data/export`, ...)
5. Prüft ob Java da ist (optional, für KoSIT-Validator)
6. Prüft `/etc/machine-id` (für Lizenzsystem)

## Was starten.sh macht

1. Erkennt ob install.sh schon gelaufen ist (`.deps_installed` Marker)
2. Aktiviert `.venv/` automatisch
3. Sucht freien Port (5000 → 5001 → ... falls belegt)
4. Startet `run.py` mit dem freien Port
5. Browser öffnet sich automatisch

## Voraussetzungen

### Minimal (Ubuntu/Debian/WSL)
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv
```

### Mit KoSIT-Validator (optional, für echte XRechnung-Prüfung)
```bash
sudo apt install default-jre
```

### macOS
```bash
brew install python@3.12
# Java optional: brew install openjdk
```

## Lizenz

Das System startet automatisch im **28-Tage-Testmodus**.
Alle Funktionen sind freigeschaltet. Schreibende Aktionen
(Erstellen, Freigeben, Export) werden nach Ablauf gesperrt.
Lesezugriff bleibt erhalten.

Die Geräte-ID wird aus `/etc/machine-id` + MAC-Adresse + Hostname
berechnet. In WSL ist das stabil, solange die gleiche Distribution
verwendet wird.

## Dateien die NICHT ins Repo gehören

```
.venv/              → Virtual Environment (wird von install.sh erstellt)
.deps_installed      → Marker-Datei
data/               → Alle Nutzdaten (Rechnungen, Archiv, Lieferanten...)
*.pyc / __pycache__/ → Python-Cache
```

## Tests ausführen

```bash
source .venv/bin/activate
pip install pytest
pytest test_auftragsmanagement.py -v    # 52 Tests (Auftragsmanagement)
pytest test_erechnung.py -v             # 81 Tests (Kernsystem)
```

## Fehlerbehebung

### "python3-venv nicht gefunden"
```bash
sudo apt install python3-venv
```

### "Port 5000 belegt" (wird automatisch gelöst)
Falls der automatische Port-Scan nicht funktioniert:
```bash
./starten.sh 8080    # Manuell Port 8080 verwenden
```

### "lxml Installation schlägt fehl"
```bash
sudo apt install libxml2-dev libxslt1-dev python3-dev
source .venv/bin/activate && pip install lxml
```

### "Browser öffnet sich nicht" (WSL)
In WSL kann `webbrowser.open()` nicht immer den Windows-Browser starten.
Lösung: URL manuell im Windows-Browser öffnen:
```
http://localhost:5000
```
Der Port wird in der Konsole angezeigt.

### WSL: Windows-Browser automatisch öffnen
Falls der Browser sich nicht öffnet, diese Zeile in `~/.bashrc` einfügen:
```bash
export BROWSER="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
# oder für Edge:
export BROWSER="/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
```
