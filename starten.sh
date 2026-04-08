#!/usr/bin/env bash
# ========================================================
#  E-Rechnungssystem - Programm starten (macOS / Linux)
# ========================================================

echo ""
echo "  ========================================================"
echo "   E-Rechnungssystem"
echo "   XRechnung / ZUGFeRD / EN 16931"
echo "  ========================================================"
echo ""

# Python pruefen
if ! command -v python3 &>/dev/null; then
    echo "  Python3 nicht gefunden!"
    echo "  Bitte zuerst installieren:"
    echo "    macOS:  brew install python3"
    echo "    Linux:  sudo apt install python3 python3-pip"
    echo ""
    exit 1
fi

# Ins Skript-Verzeichnis wechseln
cd "$(dirname "$0")"

# Beim ersten Start Abhaengigkeiten installieren
if [ ! -f ".deps_installed" ]; then
    echo "  Erstmalige Einrichtung..."
    pip3 install -r requirements.txt --quiet
    echo "OK" > .deps_installed
    echo "  Abhaengigkeiten installiert."
    echo ""
fi

echo "  Starte Server..."
echo "  Der Browser oeffnet sich gleich automatisch."
echo "  Zum Beenden: Strg+C"
echo ""

python3 run.py "$@"
