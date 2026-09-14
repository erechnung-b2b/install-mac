#!/usr/bin/env bash
# Frühere Bezeichnung der Installation – ruft die aktuelle Erstinstallation auf.
cd "$(dirname "$0")"
exec bash erstinstallation.sh "$@"
