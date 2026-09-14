#!/usr/bin/env bash
# ================================================================
#  E-Rechnungssystem – Update (macOS / Linux)
#  Sichert data/, lädt die aktuelle Version, ersetzt die Programm-
#  dateien und aktualisiert die Pakete. Daten, Python, Java und der
#  KoSIT-Validator bleiben erhalten.
# ================================================================
set -euo pipefail
cd "$(dirname "$0")"
BASIS="$(pwd)"
[ -f webapp.py ] || { echo "  ✗ Bitte im Installationsordner ausführen."; exit 1; }

if [ "$(uname -s)" = "Darwin" ]; then REPO=install-mac; else REPO=install; fi
STAND=$(date +%Y-%m-%d-%H%M)
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo ""
echo "  [1/4] Sichere Ihre Daten..."
if [ -d data ]; then
  mkdir -p "backup/backup-$STAND"
  cp -R data "backup/backup-$STAND/"
  echo "        ✓ backup/backup-$STAND"
fi

echo "  [2/4] Lade aktuelle Version..."
curl -fL --progress-bar -o "$TMP/update.zip" "https://github.com/erechnung-b2b/$REPO/archive/refs/heads/main.zip" \
  || { echo "  ✗ Download fehlgeschlagen – Internetverbindung prüfen."; exit 1; }
PY=laufzeit/python/bin/python3; [ -x "$PY" ] || PY=python3
"$PY" -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$TMP/update.zip" "$TMP/neu"
SRC="$(dirname "$(find "$TMP/neu" -maxdepth 2 -name webapp.py | head -1)")"
[ -f "$SRC/webapp.py" ] || { echo "  ✗ Download unvollständig."; exit 1; }

echo "  [3/4] Ersetze Programmdateien..."
# alles außer Daten, Laufzeit, Umgebung, Validator und Sicherungen
( cd "$SRC" && find . -mindepth 1 -maxdepth 1 \
    ! -name data ! -name laufzeit ! -name .venv ! -name backup ! -name tools ! -name .deps_installed \
    -exec cp -R {} "$BASIS/" \; )
chmod +x ./*.sh ./*.command 2>/dev/null || true
echo "        ✓ aktualisiert"

echo "  [4/4] Aktualisiere Pakete..."
bash erstinstallation.sh
echo ""
echo "  ✓ Update abgeschlossen. Ihre Daten liegen zusätzlich unter backup/backup-$STAND"
echo ""
