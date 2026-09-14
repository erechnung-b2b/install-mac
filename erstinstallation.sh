#!/usr/bin/env bash
# ================================================================
#  E-Rechnungssystem – Erstinstallation für macOS und Linux
#
#  Richtet ALLES im Programmordner ein – ohne Administratorrechte,
#  ohne Homebrew, ohne vorhandenes Python:
#    1. eigenes Python 3.12 (geprüft per SHA-256)   -> laufzeit/python
#    2. Programmumgebung + Python-Pakete             -> .venv
#    3. Java für den KoSIT-Validator (optional)      -> laufzeit/java
#    4. offizieller KoSIT-Validator (optional)        -> tools/kosit
#
#  Mac: Doppelklick auf „Installieren.command“
#  Linux/Terminal: bash erstinstallation.sh   (--ohne-java: ohne KoSIT)
# ================================================================
set -euo pipefail
cd "$(dirname "$0")"
BASIS="$(pwd)"

GRUEN='\033[0;32m'; GELB='\033[1;33m'; ROT='\033[0;31m'; BLAU='\033[1;34m'; NC='\033[0m'
ok()   { echo -e "        ${GRUEN}✓ $*${NC}"; }
warn() { echo -e "        ${GELB}⚠ $*${NC}"; }
fehler() { echo -e "\n  ${ROT}✗ $*${NC}\n"; exit 1; }

OHNE_JAVA=false
[ "${1:-}" = "--ohne-java" ] && OHNE_JAVA=true

echo ""
echo -e "${BLAU}  ════════════════════════════════════════════════════════${NC}"
echo -e "${BLAU}   E-Rechnungssystem – Installation${NC}"
echo -e "${BLAU}   Alles wird im Programmordner eingerichtet.${NC}"
echo -e "${BLAU}  ════════════════════════════════════════════════════════${NC}"
echo ""

# shellcheck disable=SC1091
set -a; . installer/laufzeit.txt; set +a

# ── Plattform erkennen ──────────────────────────────────────────
SYSTEM="$(uname -s)"; ARCH="$(uname -m)"
case "$SYSTEM-$ARCH" in
  Darwin-arm64)            PY_DATEI=$PY_MAC_ARM;    PY_SHA=$PY_MAC_ARM_SHA;    JRE_DATEI=$JRE_MAC_ARM;    JRE_SHA=$JRE_MAC_ARM_SHA;    NAME="macOS (Apple Silicon)";;
  Darwin-x86_64)           PY_DATEI=$PY_MAC_X64;    PY_SHA=$PY_MAC_X64_SHA;    JRE_DATEI=$JRE_MAC_X64;    JRE_SHA=$JRE_MAC_X64_SHA;    NAME="macOS (Intel)";;
  Linux-x86_64)            PY_DATEI=$PY_LINUX_X64;  PY_SHA=$PY_LINUX_X64_SHA;  JRE_DATEI=$JRE_LINUX_X64;  JRE_SHA=$JRE_LINUX_X64_SHA;  NAME="Linux (x86_64)";;
  Linux-aarch64|Linux-arm64) PY_DATEI=$PY_LINUX_ARM; PY_SHA=$PY_LINUX_ARM_SHA; JRE_DATEI=$JRE_LINUX_ARM; JRE_SHA=$JRE_LINUX_ARM_SHA; NAME="Linux (ARM64)";;
  *) fehler "Nicht unterstützte Plattform: $SYSTEM $ARCH";;
esac
echo "  System: $NAME"
echo ""

pruefsumme() {   # Datei -> SHA-256
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

laden() {        # URL Ziel SHA
  local url="$1" ziel="$2" sha="$3" versuch
  for versuch in 1 2 3; do
    if curl -fL --progress-bar --retry 2 -o "$ziel" "$url"; then
      if [ "$(pruefsumme "$ziel")" = "$sha" ]; then return 0; fi
      warn "Prüfsumme stimmt nicht (Versuch $versuch) – lade erneut"
    else
      warn "Download fehlgeschlagen (Versuch $versuch)"
    fi
    rm -f "$ziel"; sleep 2
  done
  return 1
}

mkdir -p laufzeit
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ── 1. Python ───────────────────────────────────────────────────
echo "  [1/5] Python $PY_VERSION einrichten..."
PY="$BASIS/laufzeit/python/bin/python3"
if [ -x "$PY" ] && "$PY" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" 2>/dev/null; then
  ok "bereits vorhanden"
else
  echo "        Lade Python (ca. 25–110 MB)..."
  laden "$PY_BASE/$PY_DATEI" "$TMP/python.tar.gz" "$PY_SHA" || fehler "Python konnte nicht geladen werden. Internetverbindung prüfen."
  rm -rf laufzeit/python
  tar -xzf "$TMP/python.tar.gz" -C laufzeit
  [ -x "$PY" ] || fehler "Python-Archiv unvollständig."
  # macOS: aus dem Internet geladene Dateien ggf. freigeben (curl setzt keine Quarantäne, sicher ist sicher)
  if [ "$SYSTEM" = "Darwin" ]; then xattr -dr com.apple.quarantine laufzeit/python 2>/dev/null || true; fi
  ok "$("$PY" --version)"
fi
echo ""

# ── 2. Programmumgebung + Pakete ────────────────────────────────
echo "  [2/5] Programmumgebung und Pakete (1–3 Minuten)..."
if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix and sys.version_info[:2]==(3,12) else 1)" 2>/dev/null; then
  rm -rf .venv
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt -c installer/constraints.txt \
  --quiet --disable-pip-version-check || fehler "Paketinstallation fehlgeschlagen (siehe Meldungen oben)."
.venv/bin/python -c "import flask, lxml, qrcode, cryptography, pdfplumber, reportlab, pikepdf, waitress" \
  || fehler "Pakete unvollständig."
ok "alle Pakete installiert"
echo ""

# ── 3. Java für KoSIT ───────────────────────────────────────────
echo "  [3/5] Java für den KoSIT-Validator (optional)..."
JAVA=""
if [ "$OHNE_JAVA" = true ]; then
  warn "übersprungen (--ohne-java)"
else
  for kandidat in laufzeit/java/Contents/Home/bin/java laufzeit/java/bin/java; do
    [ -x "$kandidat" ] && JAVA="$kandidat"
  done
  if [ -z "$JAVA" ]; then
    echo "        Lade Java-Laufzeit (ca. 45 MB)..."
    if laden "$JRE_BASE/$JRE_DATEI" "$TMP/jre.tar.gz" "$JRE_SHA"; then
      rm -rf laufzeit/java && mkdir -p "$TMP/jre" && tar -xzf "$TMP/jre.tar.gz" -C "$TMP/jre"
      mv "$TMP/jre"/* laufzeit/java
      [ "$SYSTEM" = "Darwin" ] && xattr -dr com.apple.quarantine laufzeit/java 2>/dev/null || true
      for kandidat in laufzeit/java/Contents/Home/bin/java laufzeit/java/bin/java; do
        [ -x "$kandidat" ] && JAVA="$kandidat"
      done
    fi
  fi
  if [ -n "$JAVA" ] && "$JAVA" -version >/dev/null 2>&1; then ok "$("$JAVA" -version 2>&1 | head -1)"
  else JAVA=""; warn "Java nicht verfügbar – die Software nutzt den eingebauten Prüfer"; fi
fi
echo ""

# ── 4. KoSIT-Validator ──────────────────────────────────────────
echo "  [4/5] KoSIT-Validator (offizielle XRechnung-Prüfung)..."
if [ -z "$JAVA" ]; then
  warn "ohne Java übersprungen"
elif [ -f tools/kosit/validator.jar ] && [ -f tools/kosit/scenarios.xml ]; then
  ok "bereits vorhanden"
else
  if .venv/bin/python installer/kosit_laden.py >/dev/null 2>&1; then
    ok "KoSIT-Validator einsatzbereit"
  else
    warn "KoSIT konnte nicht geladen werden – später erneut ausführen; der eingebaute Prüfer funktioniert"
  fi
fi
echo ""

# ── 5. Daten, Startdateien, Selbsttest ──────────────────────────
echo "  [5/5] Abschluss..."
mkdir -p data/archiv data/export data/sent_mails data/test_mails data/logo data/documents
chmod +x ./*.sh ./*.command 2>/dev/null || true
if .venv/bin/python -c "import cii_generator, pdfa3, zugferd_writer, xrechnung_generator"; then
  ok "Selbsttest bestanden"
else
  fehler "Selbsttest fehlgeschlagen."
fi
echo "OK $(date +%Y-%m-%d) Python $PY_VERSION" > .deps_installed

echo ""
echo -e "${GRUEN}  ════════════════════════════════════════════════════════${NC}"
echo -e "${GRUEN}   ✓ Installation abgeschlossen${NC}"
echo -e "${GRUEN}  ════════════════════════════════════════════════════════${NC}"
echo ""
if [ "$SYSTEM" = "Darwin" ]; then
  echo "   Starten: Doppelklick auf „E-Rechnung starten.command“"
else
  echo "   Starten: ./starten.sh        Serverbetrieb: siehe docs/Server-Installation.md"
fi
echo "   Der Browser öffnet sich auf http://localhost:5000"
echo "   28 Tage Testzeitraum – danach Lizenzcode unter Einstellungen."
echo ""
