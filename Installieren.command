#!/usr/bin/env bash
# macOS: Doppelklick installiert das E-Rechnungssystem (einmalig).
# Beim allerersten Öffnen: Rechtsklick → „Öffnen“ (Datei stammt aus dem Internet).
cd "$(dirname "$0")"
bash erstinstallation.sh
status=$?
echo ""
if [ $status -eq 0 ]; then
  read -r -p "  Jetzt starten? [J/n] " antwort
  case "${antwort:-J}" in
    [nN]*) echo "  Später starten: Doppelklick auf „E-Rechnung starten.command“";;
    *) exec bash starten.sh;;
  esac
else
  echo "  Die Installation wurde nicht abgeschlossen. Meldungen oben beachten."
  read -r -p "  Mit Enter schließen..." _
fi
