#!/usr/bin/env bash
# ================================================================
#  E-Rechnungssystem – Erstinstallation (Linux / macOS / WSL)
#  Einmalig ausführen: chmod +x install.sh && ./install.sh
# ================================================================
set -e

echo ""
echo "  ========================================================"
echo "   E-Rechnungssystem – Installation"
echo "   XRechnung / ZUGFeRD / EN 16931"
echo "  ========================================================"
echo ""

cd "$(dirname "$0")"
BASE="$(pwd)"

# ── 1. Python prüfen ─────────────────────────────────────────
echo "  [1/5] Python prüfen..."

PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo ""
    echo "  ✗ Python 3.10+ nicht gefunden!"
    echo ""
    echo "  Bitte installieren:"
    echo "    Ubuntu/Debian:  sudo apt update && sudo apt install python3 python3-pip python3-venv"
    echo "    Fedora/RHEL:    sudo dnf install python3 python3-pip"
    echo "    macOS:          brew install python@3.12"
    echo "    Arch:           sudo pacman -S python python-pip"
    echo ""
    exit 1
fi
echo "  ✓ $PYTHON ($($PYTHON --version 2>&1))"

# ── 2. Virtual Environment ───────────────────────────────────
echo "  [2/5] Virtual Environment einrichten..."

# python3-venv prüfen (fehlt oft auf Ubuntu/Debian)
if ! $PYTHON -m venv --help &>/dev/null; then
    echo "  ✗ python3-venv nicht installiert"
    echo "    → sudo apt install python3-venv"
    exit 1
fi

if [ ! -d ".venv" ]; then
    $PYTHON -m venv .venv
    echo "  ✓ .venv erstellt"
else
    echo "  ✓ .venv existiert bereits"
fi

source .venv/bin/activate

# ── 3. Abhängigkeiten installieren ───────────────────────────
echo "  [3/5] Python-Pakete installieren..."

pip install --upgrade pip -q 2>/dev/null
pip install -r requirements.txt -q 2>&1 | tail -3

# Schnelltest
python3 -c "
import flask, lxml, qrcode, cryptography, pdfplumber, reportlab, pikepdf
print('  ✓ Alle 7 Kernpakete installiert')
" || {
    echo "  ✗ Paket-Problem. Manuell prüfen:"
    echo "    source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
}

# ── 4. Datenverzeichnisse ────────────────────────────────────
echo "  [4/5] Datenverzeichnisse anlegen..."

for d in data/archiv data/export data/sent_mails data/test_mails data/logo data/documents; do
    mkdir -p "$BASE/$d"
done
echo "  ✓ data/ Struktur angelegt"

# ── 5. Optionale Komponenten ─────────────────────────────────
echo "  [5/5] Optionale Komponenten..."

if command -v java &>/dev/null; then
    echo "  ✓ Java: $(java -version 2>&1 | head -1)"
    if [ -f "tools/kosit/validator.jar" ]; then
        echo "  ✓ KoSIT-Validator: vorhanden"
    else
        echo "  ⚠ KoSIT-Validator JAR nicht in tools/kosit/ → Basismodus"
    fi
else
    echo "  ⚠ Java nicht gefunden → KoSIT nicht verfügbar (optional)"
    echo "    → Ubuntu: sudo apt install default-jre"
fi

# Machine-ID prüfen (für Lizenz)
if [ -f "/etc/machine-id" ]; then
    echo "  ✓ Machine-ID: vorhanden (für Lizenzsystem)"
else
    echo "  ⚠ /etc/machine-id fehlt → Geräte-ID wird aus MAC-Adresse erzeugt"
fi

echo "OK" > .deps_installed

echo ""
echo "  ========================================================"
echo "   ✓ Installation abgeschlossen!"
echo "  ========================================================"
echo ""
echo "  Starten:  ./starten.sh"
echo "  Tests:    source .venv/bin/activate && pytest test_auftragsmanagement.py -v"
echo ""
echo "  28-Tage-Testmodus startet beim ersten Aufruf."
echo "  Browser öffnet sich automatisch."
echo ""
