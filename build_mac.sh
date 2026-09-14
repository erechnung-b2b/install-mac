#!/usr/bin/env bash
# ========================================================
#  E-Rechnungssystem - Standalone-App bauen (macOS)
#  Erzeugt eine ausfuehrbare App ohne Python-Abhaengigkeit
# ========================================================
set -e

echo ""
echo "  ========================================================"
echo "   E-Rechnungssystem - macOS Build"
echo "  ========================================================"
echo ""

cd "$(dirname "$0")"

# ── 1. Python pruefen ──
echo "  [1/4] Pruefe Python..."
if ! command -v python3 &>/dev/null; then
    echo "  [FEHLER] Python3 nicht gefunden!"
    exit 1
fi
echo "         $(python3 --version)"
echo ""

# ── 2. Abhaengigkeiten installieren ──
echo "  [2/4] Installiere Abhaengigkeiten..."
pip3 install -r requirements.txt pyinstaller --quiet
echo "         OK"
echo ""

# ── 3. Module pruefen ──
echo "  [3/4] Pruefe Anwendung..."
python3 -c "from webapp import app; print('         OK: Alle Module geladen')"
echo ""

# ── 4. PyInstaller Build ──
echo "  [4/4] Erstelle Standalone-Anwendung..."
echo "         Das kann 1-3 Minuten dauern..."
echo ""

pyinstaller erechnung.spec --noconfirm --clean

# Datenverzeichnisse im Build erstellen
mkdir -p dist/erechnung/data/archiv
mkdir -p dist/erechnung/data/export
mkdir -p dist/erechnung/data/logo

echo ""
echo "  ========================================================"
echo "   Build erfolgreich!"
echo "  ========================================================"
echo ""
echo "   Anwendung liegt in: dist/erechnung/"
echo "   Starten mit:        dist/erechnung/E-Rechnungssystem"
echo ""
echo "   Diesen Ordner koennen Sie auf beliebige Macs"
echo "   kopieren - Python ist NICHT mehr noetig."
echo ""
