#!/usr/bin/env bash
# ========================================================
#  E-Rechnungssystem - Installation (macOS / Linux)
# ========================================================
set -e

echo ""
echo "  ========================================================"
echo "   E-Rechnungssystem - Installation"
echo "  ========================================================"
echo ""

# ── 1. Python pruefen ──
echo "  [1/4] Pruefe Python..."

if ! command -v python3 &>/dev/null; then
    echo ""
    echo "  [FEHLER] Python3 nicht gefunden!"
    echo ""
    echo "  Installation:"
    echo "    macOS:   brew install python3"
    echo "             oder: https://www.python.org/downloads/"
    echo "    Ubuntu:  sudo apt install python3 python3-pip python3-venv"
    echo ""
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo $PY_VERSION | cut -d. -f1)
PY_MINOR=$(echo $PY_VERSION | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo "  [FEHLER] Python 3.10 oder hoeher erforderlich (gefunden: $PY_VERSION)"
    exit 1
fi

echo "         $(python3 --version)"
echo ""

# ── 2. Abhaengigkeiten installieren ──
echo "  [2/4] Installiere Abhaengigkeiten..."

pip3 install -r requirements.txt --quiet 2>&1
if [ $? -ne 0 ]; then
    echo "  [FEHLER] Installation der Abhaengigkeiten fehlgeschlagen"
    echo "  Versuchen Sie: pip3 install -r requirements.txt"
    exit 1
fi

echo "         OK"
echo ""

# ── 3. Module pruefen ──
echo "  [3/4] Pruefe Anwendung..."

python3 -c "from webapp import app; print('         OK: Alle Module geladen')"
if [ $? -ne 0 ]; then
    echo "  [FEHLER] Module konnten nicht geladen werden"
    exit 1
fi
echo ""

# ── 4. Datenverzeichnisse anlegen ──
echo "  [4/4] Erstelle Datenverzeichnisse..."

mkdir -p data/archiv data/export data/sent_mails data/test_mails data/logo
echo "         OK"
echo ""

# ── Startskript ausfuehrbar machen ──
chmod +x starten.sh 2>/dev/null

echo "  ========================================================"
echo "   Installation erfolgreich!"
echo "  ========================================================"
echo ""
echo "   Programm starten mit:  ./starten.sh"
echo "   Im Browser oeffnen:    http://localhost:5000"
echo ""
