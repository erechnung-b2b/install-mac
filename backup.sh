#!/usr/bin/env bash
# E-Rechnungssystem – Datensicherung
cd "$(dirname "$0")"
DATUM=$(date +%Y-%m-%d_%H%M)
BACKUP="backup_erechnung_${DATUM}.zip"
echo ""
echo "  ========================================================"
echo "   E-Rechnungssystem - Datensicherung"
echo "  ========================================================"
echo ""
echo "  Sichere data/ Ordner..."
zip -r "$BACKUP" data/ -x "data/.DS_Store" > /dev/null 2>&1
SIZE=$(ls -lh "$BACKUP" | awk '{print $5}')
echo "  OK: $BACKUP ($SIZE)"

# Nach Windows Downloads kopieren (WSL)
WIN_DL="/mnt/c/Users/Rolf/Downloads"
if [ -d "$WIN_DL" ]; then
    cp "$BACKUP" "$WIN_DL/"
    echo "  Kopiert nach: $WIN_DL/$BACKUP"
else
    echo "  Backup liegt in: $(pwd)/$BACKUP"
fi
echo ""
