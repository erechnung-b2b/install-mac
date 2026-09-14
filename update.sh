#!/usr/bin/env bash
# ========================================================
#  E-Rechnungssystem - Update (macOS / Linux)
# ========================================================

set -e

echo ""
echo "  ========================================================"
echo "   E-Rechnungssystem - Update"
echo "  ========================================================"
echo ""

# Arbeitsverzeichnis = Speicherort dieser Datei
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
INSTALL_DIR="$SCRIPT_DIR"

# Pruefen ob dies ein gueltiger Installationsordner ist
if [ ! -f "webapp.py" ]; then
    echo "  [FEHLER] Diese Datei muss im Installationsordner liegen."
    echo "           Datei nicht gefunden: webapp.py"
    echo "           Aktueller Pfad: $INSTALL_DIR"
    echo ""
    read -p "  Mit Enter schliessen..."
    exit 1
fi

echo "  Installationsordner: $INSTALL_DIR"
echo ""

TIMESTAMP=$(date +%Y-%m-%d-%H%M)
BACKUP_DIR="$INSTALL_DIR/backup/backup-$TIMESTAMP"

echo "  [1/5] Sichere Ihre Daten..."
if [ -d "data" ]; then
    mkdir -p "$BACKUP_DIR"
    cp -R data "$BACKUP_DIR/"
    echo "        Sicherung erstellt: $BACKUP_DIR"
else
    echo "        Keine Daten zu sichern (Erstinstallation)."
fi
if [ -d "lizenz_data" ]; then
    cp -R lizenz_data "$BACKUP_DIR/" 2>/dev/null || true
fi
echo ""

echo "  [2/5] Lade aktuelle Version von GitHub..."
UPDATE_ZIP="/tmp/erechnung-update.zip"
UPDATE_DIR="/tmp/erechnung-update-extract"

rm -f "$UPDATE_ZIP"
rm -rf "$UPDATE_DIR"

if ! curl -L -s -o "$UPDATE_ZIP" "https://github.com/erechnung-b2b/install-mac/archive/refs/heads/main.zip"; then
    echo "        [FEHLER] Download fehlgeschlagen. Internetverbindung pruefen."
    read -p "  Mit Enter schliessen..."
    exit 1
fi
echo "        Download abgeschlossen."
echo ""

echo "  [3/5] Entpacke Update..."
mkdir -p "$UPDATE_DIR"
if ! unzip -q "$UPDATE_ZIP" -d "$UPDATE_DIR"; then
    echo "        [FEHLER] Entpacken fehlgeschlagen."
    read -p "  Mit Enter schliessen..."
    exit 1
fi

# Quellordner finden
SRC=""
for candidate in \
    "$UPDATE_DIR/install-mac-main/mac-paket" \
    "$UPDATE_DIR/install-mac-main" \
    "$UPDATE_DIR/install-main/erechnung-komplett/erechnung" \
    "$UPDATE_DIR/install-main/erechnung"
do
    if [ -f "$candidate/webapp.py" ]; then
        SRC="$candidate"
        break
    fi
done

if [ -z "$SRC" ]; then
    echo "        [FEHLER] Quelldateien im Download nicht gefunden."
    read -p "  Mit Enter schliessen..."
    exit 1
fi
echo "        Entpackt."
echo ""

echo "  [4/5] Kopiere neue Programmdateien..."
cp "$SRC"/*.py "$INSTALL_DIR/" 2>/dev/null || true
for f in "$SRC"/*.sh; do
    name=$(basename "$f")
    if [ "$name" != "update.sh" ]; then
        cp "$f" "$INSTALL_DIR/"
    fi
done
[ -f "$SRC/requirements.txt" ] && cp "$SRC/requirements.txt" "$INSTALL_DIR/"
[ -f "$SRC/erechnung.spec" ] && cp "$SRC/erechnung.spec" "$INSTALL_DIR/"
[ -d "$SRC/static" ] && cp -R "$SRC/static"/. "$INSTALL_DIR/static/"
[ -d "$SRC/docs" ] && cp -R "$SRC/docs"/. "$INSTALL_DIR/docs/" 2>/dev/null || true

chmod +x "$INSTALL_DIR"/*.sh 2>/dev/null || true
echo "        Alle Dateien aktualisiert."
echo ""

echo "  [5/5] Raeume auf..."
rm -f "$UPDATE_ZIP"
rm -rf "$UPDATE_DIR"
echo "        Fertig."
echo ""

echo "  ========================================================"
echo "   Update erfolgreich abgeschlossen!"
echo "  ========================================================"
echo ""
echo "   Ihre Daten wurden gesichert unter:"
echo "   $BACKUP_DIR"
echo ""
echo "   Zum Starten: ./starten.sh"
echo ""
read -p "  Mit Enter schliessen..."
