#!/usr/bin/env bash
# ================================================================
#  E-Rechnungssystem – Installation mit einem Befehl (macOS / Linux)
#
#  Im Terminal einfügen:
#    curl -fsSL https://raw.githubusercontent.com/erechnung-b2b/install-mac/main/mac-installation.sh | bash
#
#  Lädt das Programm nach ~/E-Rechnungssystem, richtet Python, Pakete,
#  Java und den KoSIT-Validator ein und legt eine Startdatei auf den
#  Schreibtisch. Keine Administratorrechte, keine Gatekeeper-Rückfrage.
#  Ist das Programm schon installiert, wird es aktualisiert – der
#  Ordner data/ (Ihre Rechnungen) bleibt unangetastet.
# ================================================================
set -euo pipefail

ZIEL="${ERECHNUNG_ZIEL:-$HOME/E-Rechnungssystem}"
if [ "$(uname -s)" = "Darwin" ]; then REPO=install-mac; else REPO=install; fi
ZIP_URL="https://github.com/erechnung-b2b/$REPO/archive/refs/heads/main.zip"

echo ""
echo "  E-Rechnungssystem wird nach $ZIEL installiert."
echo ""

command -v curl >/dev/null 2>&1 || { echo "  ✗ curl fehlt."; exit 1; }
command -v unzip >/dev/null 2>&1 || { echo "  ✗ unzip fehlt (Linux: sudo apt install unzip)."; exit 1; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "  Lade Programm..."
curl -fL --progress-bar --retry 2 -o "$TMP/programm.zip" "$ZIP_URL" \
  || { echo "  ✗ Download fehlgeschlagen – Internetverbindung prüfen."; exit 1; }
unzip -q "$TMP/programm.zip" -d "$TMP/neu"
SRC="$(dirname "$(find "$TMP/neu" -maxdepth 2 -name webapp.py | head -1)")"
[ -f "$SRC/webapp.py" ] || { echo "  ✗ Download unvollständig."; exit 1; }

mkdir -p "$ZIEL"
if [ -d "$ZIEL/data" ]; then
  STAND=$(date +%Y-%m-%d-%H%M)
  mkdir -p "$ZIEL/backup/backup-$STAND"
  cp -R "$ZIEL/data" "$ZIEL/backup/backup-$STAND/"
  echo "  ✓ vorhandene Daten gesichert: backup/backup-$STAND"
fi
# Programmdateien einspielen – Daten, Laufzeit, Umgebung, Validator bleiben
( cd "$SRC" && find . -mindepth 1 -maxdepth 1 \
    ! -name data ! -name laufzeit ! -name .venv ! -name backup ! -name tools ! -name .deps_installed \
    -exec cp -R {} "$ZIEL/" \; )
chmod +x "$ZIEL"/*.sh "$ZIEL"/*.command 2>/dev/null || true

bash "$ZIEL/erstinstallation.sh"

# Startdatei auf dem Schreibtisch (lokal erzeugt → keine Quarantäne)
if [ "$(uname -s)" = "Darwin" ] && [ -d "$HOME/Desktop" ]; then
  STARTER="$HOME/Desktop/E-Rechnung starten.command"
  printf '#!/usr/bin/env bash\ncd %q && exec bash starten.sh\n' "$ZIEL" > "$STARTER"
  chmod +x "$STARTER"
  echo "  ✓ Startdatei auf dem Schreibtisch: „E-Rechnung starten.command“"
  echo ""
fi

# stdin ist bei "curl | bash" die Pipe – Rückfrage deshalb über das Terminal
if (exec </dev/tty) 2>/dev/null; then
  read -r -p "  Jetzt starten? [J/n] " antwort < /dev/tty || antwort=n
  case "${antwort:-J}" in
    [nN]*) ;;
    *) cd "$ZIEL" && exec bash starten.sh < /dev/tty;;
  esac
fi
