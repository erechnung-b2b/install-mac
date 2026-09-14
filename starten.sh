#!/usr/bin/env bash
# ================================================================
#  E-Rechnungssystem – Start (macOS / Linux)
#  ./starten.sh [PORT]            Einzelplatz, Browser öffnet sich
#  ./starten.sh --server [PORT]   Serverbetrieb (siehe docs/Server-Installation.md)
# ================================================================
set -eu
cd "$(dirname "$0")"

# Erstinstallation fehlt oder unvollständig → automatisch nachholen
if [ ! -f .deps_installed ] || [ ! -x .venv/bin/python ]; then
  echo "  Erstmalige Einrichtung – das dauert einige Minuten..."
  bash erstinstallation.sh
fi

PY=.venv/bin/python
if ! "$PY" -c "import flask, pikepdf" 2>/dev/null; then
  echo "  Pakete unvollständig – richte erneut ein..."
  rm -f .deps_installed
  bash erstinstallation.sh
fi

# Java aus dem Programmordner für den KoSIT-Validator bekannt machen
for j in laufzeit/java/Contents/Home laufzeit/java; do
  if [ -x "$j/bin/java" ]; then export JAVA_HOME="$PWD/$j"; export PATH="$JAVA_HOME/bin:$PATH"; break; fi
done

mkdir -p data/logs
echo ""
echo "  E-Rechnungssystem startet – Zum Beenden: Strg+C oder Fenster schließen"
echo "  Protokoll: data/logs/erechnung.log"
echo ""
"$PY" run.py "$@" 2>&1 | tee -a data/logs/erechnung.log
